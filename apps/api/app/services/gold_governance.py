"""Governança da materialização Gold: manifesto, dependências, publicação atômica.

Sétimo caminho. Antes deste módulo, sete lugares diferentes emitiam
``CREATE TABLE iceberg…`` e publicavam cubos sem deixar rastro: o materializador
governado (`gold_materialization`), o agregador do reprocess (`gold_builder`), o
conjunto morto (`gold_cubes_service`), o enriquecimento morto
(`focus_enrichment`, removido em G1), a Gold de PTU (`azure_ptu_gold`), as Gold
de IA (`ai_usage_gold`) e a materialização de custo de IA
(`ai_cost_materialization`). Nenhum registrava de onde veio o número.

O que este módulo garante:

- **um único ponto de publicação** (`publish_gold_cube`), usado por todos os
  caminhos. Não existe segundo lugar que faça staging + swap;
- **manifesto** por materialização: código e versão do contrato, run, fontes
  lidas, hash do SQL, linhas e totais financeiros antes e depois, delta,
  status, erro sanitizado e snapshot anterior para rollback (seção 6.2);
- **publicação atômica**: o swap é uma transação Iceberg. Falha preserva a versão
  publicada anterior, e o manifesto registra `FAILED` com o motivo;
- **dependência fresca**: um cubo que lê outro cubo não publica se a fonte estiver
  mais velha que o limite (seção 6.3);
- **moeda protegida**: publicar um cubo de custo com mais de uma moeda é falha,
  não conversão.

O manifesto vive no PostgreSQL porque é metadata de execução, não dado
financeiro: a regra do projeto é que linhas financeiras ficam no lakehouse e
PostgreSQL guarda configuração, workflow e metadata de execução.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services import iceberg_service as iceberg
from app.services import lakehouse_layers as layers
from app.services.semantic_normalization import (
    CurrencyMixedError,
    assert_single_currency,
)

logger = logging.getLogger(__name__)

#: Versão do contrato de manifesto. Muda quando um campo muda de significado.
MANIFEST_CONTRACT_VERSION = "1.0.0"

#: Limite de idade, em horas, que uma fonte pode ter para um dependente publicar.
#: Um cubo lido por outro não pode ser reconstruído a partir de dado mais velho
#: que isto — senão o dependente publica "novo" em cima de "velho".
DEFAULT_SOURCE_MAX_AGE_HOURS = 48.0

#: Status possíveis de uma materialização no manifesto.
RUNNING = "RUNNING"
SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILED = "FAILED"

#: Tabelas que são cubos Gold consumíveis por outros cubos. Um dependente só
#: espera freshness de tabela que é cubo; ler Silver não entra no gate.
_CUBE_PREFIX = "dest_"

_DEPENDENCY_RE = re.compile(
    r"iceberg\.([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)", re.IGNORECASE
)

#: Padrões que nunca podem chegar ao manifesto nem ao log de erro.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|account[_-]?key)\b\s*[=:]\s*\S+"),
    re.compile(r"(?i)://[^:@/\s]+:[^@/\s]+@"),
    # Prefixos de token conhecidos. O separador pode ser `-` **ou** `_`: tokens
    # do GitHub usam `ghp_`, do Slack usam `xoxb-`, da OpenAI usam `sk-`. Exigir
    # só `-` deixaria os tokens do GitHub passarem — e é justamente esse o token
    # que existe no `.env` deste repositório.
    re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]|sk|xox[baprs])[-_][A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}"),
)


def sanitize_error(exc: BaseException | str, *, limit: int = 800) -> str:
    """Erro sem segredo, truncado.

    O manifesto é lido por humanos e por API. Uma mensagem de erro de driver pode
    carregar a URL de conexão com senha, e o token do GitHub aparece em mensagens
    de erro de extração. Nada disso pode ser persistido.
    """
    texto = str(exc) if not isinstance(exc, str) else exc
    texto = texto.replace("\n", " ").replace("\r", " ").strip()
    for padrao in _SECRET_PATTERNS:
        texto = padrao.sub("[redigido]", texto)
    if len(texto) > limit:
        texto = texto[:limit] + "…"
    return texto


def sql_fingerprint(sql: str) -> str:
    """Hash estável do SQL, insensível a espaço em branco.

    Duas execuções da mesma regra produzem o mesmo hash mesmo com indentação
    diferente; uma mudança real de SQL produz hash diferente. É o que permite
    responder "o número mudou porque o dado mudou ou porque a query mudou".
    """
    normalizado = " ".join(str(sql or "").split()).lower()
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()[:32]


def extract_sources(sql: str) -> list[str]:
    """Tabelas `iceberg.<namespace>.<tabela>` lidas por um SQL, sem repetição.

    Extrai do próprio SQL em vez de exigir declaração paralela: uma lista de
    dependências mantida à mão diverge da query na primeira edição, e o manifesto
    passaria a mentir.
    """
    vistos: list[str] = []
    for namespace, tabela in _DEPENDENCY_RE.findall(str(sql or "")):
        chave = f"{namespace.lower()}.{tabela.lower()}"
        if chave not in vistos:
            vistos.append(chave)
    return vistos


def cube_dependencies(sql: str, *, namespace: str | None = None) -> list[str]:
    """Fontes de :func:`extract_sources` que são **cubos Gold**, não Silver.

    Só cubo entra no gate de freshness: ler `silver.resource_cost` não tem limite
    de idade declarado, mas ler `gold.dest_dm_ai_consumption_daily` tem.
    """
    alvo = (namespace or layers.GOLD).lower()
    return [
        fonte
        for fonte in extract_sources(sql)
        if fonte.split(".", 1)[0] == alvo and fonte.split(".", 1)[1].startswith(_CUBE_PREFIX)
    ]


# ── Manifesto ────────────────────────────────────────────────────────────────


@dataclass
class ManifestEntry:
    """Uma materialização registrada. Espelha a tabela `gold_manifest`."""

    cube_code: str
    physical_table: str
    namespace: str = layers.GOLD
    contract_version: str = MANIFEST_CONTRACT_VERSION
    run_id: int | None = None
    rule_id: int | None = None
    status: str = RUNNING
    reason: str | None = None
    sql_hash: str | None = None
    source_tables: list[str] = field(default_factory=list)
    source_freshness_at: datetime | None = None
    rows_read: int | None = None
    rows_written: int | None = None
    financial_total_in: float | None = None
    financial_total_out: float | None = None
    financial_delta: float | None = None
    tolerance: float = 0.01
    reconciliation_status: str | None = None
    currency: str | None = None
    previous_snapshot_id: str | None = None
    rollback_table: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cube_code": self.cube_code,
            "physical_table": self.physical_table,
            "namespace": self.namespace,
            "contract_version": self.contract_version,
            "run_id": self.run_id,
            "rule_id": self.rule_id,
            "status": self.status,
            "reason": self.reason,
            "sql_hash": self.sql_hash,
            "source_tables": list(self.source_tables),
            "source_freshness_at": self.source_freshness_at,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "financial_total_in": self.financial_total_in,
            "financial_total_out": self.financial_total_out,
            "financial_delta": self.financial_delta,
            "tolerance": self.tolerance,
            "reconciliation_status": self.reconciliation_status,
            "currency": self.currency,
            "previous_snapshot_id": self.previous_snapshot_id,
            "rollback_table": self.rollback_table,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def compute_financial_delta(
    *, total_in: float | None, total_out: float | None, tolerance: float = 0.01
) -> tuple[float | None, str]:
    """Delta e veredito de reconciliação entre entrada e saída.

    `None` de um dos lados não é zero: significa que aquele lado não foi medido, e
    o veredito é `NOT_MEASURED` em vez de `OK`. Um cubo de dimensão (que não soma
    dinheiro) também cai aqui — e não pode ser reprovado por não ter total.
    """
    if total_in is None or total_out is None:
        return None, "NOT_MEASURED"
    delta = round(float(total_out) - float(total_in), 2)
    return delta, ("OK" if abs(delta) <= tolerance else "MISMATCH")


def current_snapshot_id(namespace: str, table: str) -> str | None:
    """Snapshot Iceberg atual da tabela, ou ``None`` se ela não existe.

    Guardado no manifesto **antes** do swap: é o ponteiro de rollback. Sem ele,
    "voltar a versão anterior" seria uma intenção, não um procedimento.
    """
    try:
        cat = iceberg._get_catalog()  # noqa: SLF001 — helper privado do serviço de Iceberg
        safe = table.replace("-", "_").replace(".", "_")
        alvo = cat.load_table(iceberg._table_id(namespace, safe))  # noqa: SLF001
        snap = alvo.current_snapshot()
        return str(snap.snapshot_id) if snap is not None else None
    except Exception:  # noqa: BLE001 — tabela ausente é estado, não erro
        return None


async def source_freshness(cube_code: str) -> datetime | None:
    """Última publicação bem-sucedida de um cubo, pelo manifesto.

    Consulta o PostgreSQL (metadata de execução). Ausência de registro devolve
    ``None``: freshness desconhecida, nunca "agora".
    """
    try:
        from app.database import async_session
        from sqlalchemy import text
    except Exception:  # noqa: BLE001
        return None
    try:
        async with async_session() as session:
            resultado = await session.execute(
                text(
                    "SELECT max(finished_at) AS finished_at FROM gold_manifest "
                    "WHERE cube_code = :code AND status = :status"
                ),
                {"code": cube_code, "status": SUCCESS},
            )
            linha = resultado.first()
            return linha[0] if linha else None
    except Exception:  # noqa: BLE001 — tabela ainda não criada é estado inicial
        return None


class StaleDependencyError(RuntimeError):
    """Uma fonte do cubo está mais velha que o limite permitido."""


async def assert_dependencies_fresh(
    sql: str,
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_SOURCE_MAX_AGE_HOURS,
) -> None:
    """Falha quando um cubo-fonte é mais velho que `max_age_hours`.

    Um dependente não pode publicar em cima de fonte vencida (seção 6.3): o
    resultado pareceria novo e seria reconstruído a partir de dado velho.
    """
    agora = now or datetime.now(timezone.utc)
    vencidas: list[str] = []
    for fonte in cube_dependencies(sql):
        tabela = fonte.split(".", 1)[1]
        publicado = await source_freshness(tabela)
        if publicado is None:
            # Sem manifesto não há como provar freshness. Bloquear seria pior que
            # publicar: os cubos anteriores ao manifesto existem e são usados.
            logger.info(
                "Cubo dependente %s sem manifesto de freshness; gate não aplicável", tabela
            )
            continue
        if publicado.tzinfo is None:
            publicado = publicado.replace(tzinfo=timezone.utc)
        idade_horas = (agora - publicado).total_seconds() / 3600.0
        if idade_horas > max_age_hours:
            vencidas.append(f"{tabela} ({idade_horas:.1f}h)")
    if vencidas:
        raise StaleDependencyError(
            "fonte vencida para publicar dependente: " + ", ".join(vencidas)
        )


# ── Publicação ───────────────────────────────────────────────────────────────


@dataclass
class PublishOutcome:
    """Resultado de :func:`publish_gold_cube`."""

    table: str
    rows: int
    status: str
    sql_hash: str
    source_tables: list[str] = field(default_factory=list)
    previous_snapshot_id: str | None = None
    financial_delta: float | None = None
    reconciliation_status: str | None = None
    skipped_reason: str | None = None

    @property
    def published(self) -> bool:
        return self.status == SUCCESS


async def publish_gold_cube(
    *,
    cube_code: str,
    table: str,
    select_sql: str,
    namespace: str = layers.GOLD,
    run_id: int | None = None,
    rule_id: int | None = None,
    partition_column: str | None = None,
    financial_total_in: float | None = None,
    currency: str | None = None,
    empty_is_error: bool = False,
    rollback_table: str | None = None,
    progress: Any | None = None,
    timeout_s: float = 1800.0,
    max_source_age_hours: float = DEFAULT_SOURCE_MAX_AGE_HOURS,
) -> PublishOutcome:
    """Publica um cubo Gold: valida, materializa em staging e troca atomicamente.

    Ordem (seção 6.3):

    1. dependência fresca;
    2. snapshot anterior capturado para rollback;
    3. manifesto em `RUNNING`;
    4. staging;
    5. contagem e, se houver moeda, verificação de moeda única;
    6. swap atômico;
    7. manifesto em `SUCCESS`/`PARTIAL`, ou `FAILED` com erro sanitizado.

    Falha em qualquer passo remove o staging e **preserva a versão publicada
    anterior**. O manifesto registra o motivo — falha silenciosa é o que o
    plano proíbe.
    """
    from app.services.trino_service import execute as trino_execute

    sql_hash = sql_fingerprint(select_sql)
    fontes = extract_sources(select_sql)
    inicio = datetime.now(timezone.utc)

    await assert_dependencies_fresh(select_sql, max_age_hours=max_source_age_hours)

    anterior = current_snapshot_id(namespace, table)
    staging = f"{table}_stg_{time.time_ns()}"

    entrada = ManifestEntry(
        cube_code=cube_code,
        physical_table=table,
        namespace=namespace,
        run_id=run_id,
        rule_id=rule_id,
        status=RUNNING,
        sql_hash=sql_hash,
        source_tables=fontes,
        financial_total_in=financial_total_in,
        currency=currency,
        previous_snapshot_id=anterior,
        rollback_table=rollback_table,
        started_at=inicio,
    )
    await record_manifest(entrada)

    iceberg.ensure_namespace(namespace)
    particao = f" WITH (partitioning = ARRAY['{partition_column}'])" if partition_column else ""
    create_sql = (
        f"CREATE TABLE iceberg.{namespace}.{staging}{particao} AS {select_sql}"
    )

    try:
        if progress:
            progress(0, 1, f"Materializando {cube_code}")
        await trino_execute(create_sql, timeout_s=timeout_s)
        contagem = await trino_execute(
            f"SELECT COUNT(*) AS row_count FROM iceberg.{namespace}.{staging}",
            timeout_s=180,
        )
        linhas = int((contagem[0] if contagem else {}).get("row_count") or 0)

        if linhas <= 0:
            iceberg.cleanup_staged_table(staging, namespace=namespace)
            if empty_is_error:
                raise RuntimeError(f"Materialização {cube_code} não produziu linhas")
            saida = PublishOutcome(
                table=table,
                rows=0,
                status=PARTIAL,
                sql_hash=sql_hash,
                source_tables=fontes,
                previous_snapshot_id=anterior,
                skipped_reason="sem linhas",
            )
            await _finish_manifest(entrada, saida)
            return saida

        if currency is not None:
            await _assert_staging_single_currency(
                namespace=namespace, staging=staging, expected=currency
            )

        # Total financeiro do staging, medido ANTES do swap: comparar depois não
        # teria como distinguir entrada de saída.
        total_out = await _measure_financial_total(
            namespace=namespace, staging=staging, measure=_FINANCIAL_MEASURE
        )
        delta, veredito = compute_financial_delta(
            total_in=financial_total_in, total_out=total_out, tolerance=entrada.tolerance
        )

        iceberg.swap_staged_table(
            table, staging, namespace=namespace, known_row_count=linhas
        )
        entrada.financial_total_out = total_out
        saida = PublishOutcome(
            table=table,
            rows=linhas,
            status=SUCCESS,
            sql_hash=sql_hash,
            source_tables=fontes,
            previous_snapshot_id=anterior,
            financial_delta=delta,
            reconciliation_status=veredito,
        )
        await _finish_manifest(entrada, saida)
        return saida
    except Exception as exc:  # noqa: BLE001 — falha precisa virar manifesto
        try:
            iceberg.cleanup_staged_table(staging, namespace=namespace)
        except Exception:  # noqa: BLE001
            pass
        entrada.status = FAILED
        entrada.error = sanitize_error(exc)
        entrada.finished_at = datetime.now(timezone.utc)
        await record_manifest(entrada)
        raise


#: Medida financeira usada para reconciliar entrada e saída. `effective_cost` é o
#: valor canônico do projeto (FOCUS EffectiveCost). Cubo que não tem a coluna cai
#: em `NOT_MEASURED`, e não em erro.
_FINANCIAL_MEASURE = "effective_cost"


async def _measure_financial_total(
    *, namespace: str, staging: str, measure: str
) -> float | None:
    """Soma a medida financeira do staging. ``None`` quando a coluna não existe.

    `None` e `0.0` são estados diferentes: zero é um total medido que vale zero;
    `None` é "não há o que medir". O manifesto precisa distinguir os dois.
    """
    from app.services.trino_service import execute as trino_execute

    try:
        linhas = await trino_execute(
            f"SELECT ROUND(SUM(TRY_CAST({measure} AS DOUBLE)), 2) AS total "
            f"FROM iceberg.{namespace}.{staging}",
            timeout_s=300,
        )
    except Exception:  # noqa: BLE001 — coluna ausente é cubo de dimensão
        return None
    if not linhas:
        return None
    valor = linhas[0].get("total")
    return None if valor is None else float(valor)


async def _assert_staging_single_currency(
    *, namespace: str, staging: str, expected: str
) -> None:
    """Confirma que o staging tem uma única moeda, igual à esperada.

    Um cubo de custo com duas moedas somaria BRL com USD. A checagem é feita no
    staging, antes do swap: o que está publicado nunca fica inválido.
    """
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(
        f"SELECT DISTINCT LOWER(CAST(currency AS VARCHAR)) AS currency "
        f"FROM iceberg.{namespace}.{staging}",
        timeout_s=180,
    )
    moedas = [str(r.get("currency")) for r in linhas or []]
    try:
        unica = assert_single_currency([{"currency": m} for m in moedas])
    except CurrencyMixedError as exc:
        raise CurrencyMixedError(f"{staging}: {exc}") from exc
    if unica != expected.upper():
        raise CurrencyMixedError(
            f"{staging}: moeda {unica} difere da declarada {expected.upper()}"
        )


async def _finish_manifest(entrada: ManifestEntry, saida: PublishOutcome) -> None:
    entrada.status = saida.status
    entrada.rows_written = saida.rows
    entrada.financial_delta = saida.financial_delta
    entrada.reconciliation_status = saida.reconciliation_status
    entrada.reason = saida.skipped_reason
    entrada.finished_at = datetime.now(timezone.utc)
    await record_manifest(entrada)


async def record_manifest(entry: ManifestEntry) -> None:
    """Persiste o manifesto no PostgreSQL.

    Nunca levanta: o manifesto é observabilidade. Se o registro falhar, a
    materialização já aconteceu de fato e derrubar o pipeline por causa do
    registro seria pior que perder a linha de log — mas a falha é registrada.
    """
    try:
        from app.database import async_session
        from sqlalchemy import text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Manifesto Gold indisponível (%s)", sanitize_error(exc))
        return
    dados = entry.as_dict()
    try:
        async with async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO gold_manifest (
                        cube_code, physical_table, namespace, contract_version,
                        run_id, rule_id, status, reason, sql_hash, source_tables,
                        rows_read, rows_written, financial_total_in, financial_total_out,
                        financial_delta, tolerance, reconciliation_status, currency,
                        previous_snapshot_id, rollback_table, error, started_at, finished_at
                    ) VALUES (
                        :cube_code, :physical_table, :namespace, :contract_version,
                        :run_id, :rule_id, :status, :reason, :sql_hash,
                        CAST(:source_tables AS JSONB),
                        :rows_read, :rows_written, :financial_total_in, :financial_total_out,
                        :financial_delta, :tolerance, :reconciliation_status, :currency,
                        :previous_snapshot_id, :rollback_table, :error, :started_at, :finished_at
                    )
                    """
                ),
                {
                    **dados,
                    "source_tables": _json_list(dados["source_tables"]),
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao registrar manifesto de %s: %s", entry.cube_code, sanitize_error(exc))


def _json_list(valores: list[str]) -> str:
    import json

    return json.dumps(list(valores or []))


# ── Registro de publicação local (frames em memória) ─────────────────────────
#
# Dois caminhos publicam a partir de dados que já estão em memória e cujo swap é
# feito por fatia, não por tabela inteira: o agregador do reprocess
# (`gold_builder`) e a Gold operacional da OCI. Reescrevê-los para reler do Trino
# custaria o scan que eles existem para evitar.
#
# Eles não publicam sem rastro: registram o fato no mesmo manifesto. A diferença
# é `publication_mode = IN_MEMORY`, que diz ao leitor que a origem é um frame já
# agregado, não uma query — e que o `sql_hash` é `None` porque não houve SQL.


#: Publicação a partir de SQL contra o lakehouse.
PUBLICATION_SQL = "SQL"
#: Publicação a partir de frame já agregado em memória.
PUBLICATION_IN_MEMORY = "IN_MEMORY"


async def record_frame_publication(
    *,
    cube_code: str,
    physical_table: str,
    rows_written: int,
    namespace: str = layers.GOLD,
    run_id: int | None = None,
    source_tables: list[str] | None = None,
    publication_mode: str = PUBLICATION_IN_MEMORY,
    empty_is_error: bool = False,
) -> ManifestEntry:
    """Registra no manifesto uma publicação feita de frame em memória.

    Não levanta por materialização vazia a menos que o chamador exija; devolve a
    entrada registrada para que o chamador saiba o status.
    """
    vazio = rows_written <= 0
    if vazio and empty_is_error:
        raise RuntimeError(f"Materialização {cube_code} não produziu linhas")
    entrada = ManifestEntry(
        cube_code=cube_code,
        physical_table=physical_table,
        namespace=namespace,
        run_id=run_id,
        rows_written=rows_written,
        source_tables=list(source_tables or []),
        status=PARTIAL if vazio else SUCCESS,
        reason="sem linhas" if vazio else None,
        previous_snapshot_id=current_snapshot_id(namespace, physical_table),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    await record_manifest(entrada)
    return entrada


def record_manifest_blocking(entry: ManifestEntry) -> None:
    """Versão síncrona de :func:`record_manifest`, para caminhos que já são sync.

    O agregador do reprocess roda dentro de função síncrona (pandas + Iceberg
    local). Criar um loop novo quando não há loop rodando é seguro; quando há, o
    registro é ignorado em vez de derrubar a publicação — o dado já foi
    publicado, e perder a linha de manifesto é menos grave que falhar o pipeline.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(record_manifest(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Manifesto não registrado: %s", sanitize_error(exc))
        return
    logger.warning(
        "Manifesto de %s não registrado: há loop assíncrono ativo no caminho síncrono",
        entry.cube_code,
    )


def record_frame_publication_blocking(
    *,
    cube_code: str,
    physical_table: str,
    rows_written: int,
    namespace: str = layers.GOLD,
    source_tables: list[str] | None = None,
) -> ManifestEntry:
    """Versão síncrona de :func:`record_frame_publication`."""
    vazio = rows_written <= 0
    entrada = ManifestEntry(
        cube_code=cube_code,
        physical_table=physical_table,
        namespace=namespace,
        rows_written=rows_written,
        source_tables=list(source_tables or []),
        status=PARTIAL if vazio else SUCCESS,
        reason="sem linhas" if vazio else None,
        previous_snapshot_id=current_snapshot_id(namespace, physical_table),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    record_manifest_blocking(entrada)
    return entrada


# ── Namespace legado `dimon` ─────────────────────────────────────────────────
#
# Cinco tabelas `dest_dm_ai_*` e `_tmp_vm_monthly` existem em `iceberg.dimon`
# além de (ou em vez de) `iceberg.gold`. O namespace não é escrito por nenhum
# código atual — é sobra da arquitetura anterior. Enquanto a cópia existir, um
# consumidor que a encontre lê um livro abandonado, e é impossível saber qual das
# duas respostas está certa.
#
# A remoção é conservadora: a tabela legada só cai depois que a versão canônica
# existe, para não trocar "duas cópias" por "nenhuma".

LEGACY_NAMESPACE = "dimon"


async def retire_legacy_namespace_copy(
    table: str, *, canonical_namespace: str = layers.GOLD, dry_run: bool = False
) -> dict[str, Any]:
    """Remove a cópia de uma tabela no namespace legado, se a canônica existir.

    Devolve o que foi verificado e o que foi removido. Nunca levanta por
    ausência: a tabela não existir é o estado desejado.
    """
    from app.services.trino_service import execute as trino_execute

    async def _existe(namespace: str) -> bool:
        try:
            await trino_execute(
                f"SELECT 1 AS ok FROM iceberg.{namespace}.{table} LIMIT 1", timeout_s=120
            )
            return True
        except Exception:  # noqa: BLE001 — ausência é o estado desejado
            return False

    canonica = await _existe(canonical_namespace)
    legada = await _existe(LEGACY_NAMESPACE)
    resultado: dict[str, Any] = {
        "table": table,
        "canonical_namespace": canonical_namespace,
        "canonical_exists": canonica,
        "legacy_exists": legada,
        "removed": False,
        "reason": None,
    }
    if not legada:
        resultado["reason"] = "sem cópia legada"
        return resultado
    if not canonica:
        resultado["reason"] = "cópia canônica ausente: remover perderia o dado"
        return resultado
    if dry_run:
        resultado["reason"] = "dry run"
        return resultado
    await trino_execute(f"DROP TABLE IF EXISTS iceberg.{LEGACY_NAMESPACE}.{table}", timeout_s=300)
    resultado["removed"] = True
    resultado["reason"] = "canônica existe; cópia legada removida"
    return resultado


#: Tabelas conhecidas em `iceberg.dimon` em 23/09/2026.
LEGACY_DIMON_TABLES: tuple[str, ...] = (
    "_tmp_vm_monthly",
    "dest_dm_ai_consumption_daily",
    "dest_dm_ai_license_snapshot",
    "dest_dm_copilot_license_snapshot",
    "dest_dm_copilot_usage_daily",
    "dest_dm_copilot_user_period",
)


async def retire_legacy_namespace(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Verifica (e opcionalmente remove) todas as cópias conhecidas em `dimon`."""
    return [
        await retire_legacy_namespace_copy(tabela, dry_run=dry_run)
        for tabela in LEGACY_DIMON_TABLES
    ]
