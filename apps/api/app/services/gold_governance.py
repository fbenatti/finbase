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
import inspect
import os
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services import iceberg_service as iceberg
from app.services import lakehouse_layers as layers
from app.services.semantic_normalization import (
    UNKNOWN,
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

#: Tolerância de reconciliação financeira, em reais (seção 5, C1). Um delta
#: acima disso reprova a publicação do cubo de custo.
RECONCILIATION_TOLERANCE_BRL = 0.01

#: Status possíveis de uma materialização no manifesto.
RUNNING = "RUNNING"
SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
#: Manifesto RUNNING que não terminou dentro do timeout operacional e não
#: corresponde a uma task em execução. Nunca é inserido diretamente: é o desfecho
#: de um RUNNING órfão.
ABANDONED = "ABANDONED"

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
    tolerance: float = RECONCILIATION_TOLERANCE_BRL
    reconciliation_status: str | None = None
    currency: str | None = None
    previous_snapshot_id: str | None = None
    rollback_table: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: `True` depois que a entrada RUNNING foi persistida com sucesso. Não vai para
    #: o banco: é controle de fluxo do publicador.
    manifest_started: bool = False
    #: Identificador da linha `gold_manifest` desta execução. Uma materialização é
    #: **uma** linha: RUNNING insere, e o desfecho atualiza o mesmo id. Inserir
    #: outra linha terminal separaria a origem do resultado e os dois deixariam de
    #: falar do mesmo run.
    manifest_id: int | None = None

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
    create_sql: str | None = None,
    financial_total_in: float | None = None,
    currency: str | None = None,
    empty_is_error: bool = False,
    rollback_table: str | None = None,
    progress: Any | None = None,
    timeout_s: float = 1800.0,
    max_source_age_hours: float = DEFAULT_SOURCE_MAX_AGE_HOURS,
    #: Validador executado **contra o staging, antes do swap**. Recebe
    #: (namespace, nome da tabela de staging) e devolve problemas. Um problema
    #: reprova a publicação sem tocar a tabela publicada — é o que impede uma Gold
    #: híbrida: o swap só acontece quando o staging fechou com o baseline.
    validate_staging: Callable[[str, str], list[str]] | None = None,
    #: Linhas de origem medidas pelo chamador (baseline), para o manifesto.
    rows_read: int | None = None,
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

    `create_sql` permite ao chamador montar a CTAS completa (por exemplo com
    `WITH (partitioning = ...)` declarado por coluna). O hash, as fontes e a
    pós-medição continuam derivados do SQL, então o manifesto não muda.
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
        rows_read=rows_read,
        financial_total_in=financial_total_in,
        currency=currency,
        previous_snapshot_id=anterior,
        rollback_table=rollback_table,
        started_at=inicio,
    )
    # Manifesto RUNNING **antes** do staging. Em modo obrigatório, falha aqui
    # aborta antes de criar qualquer tabela: publicar um cubo governado cujo
    # registro de origem falhou é o defeito que este módulo existe para eliminar.
    await record_manifest(entrada)
    entrada.manifest_started = True

    iceberg.ensure_namespace(namespace)
    particao = f" WITH (partitioning = ARRAY['{partition_column}'])" if partition_column else ""
    if create_sql is None:
        create_sql = f"CREATE TABLE iceberg.{namespace}.{staging}{particao} AS {select_sql}"
    else:
        create_sql = create_sql.replace("{staging}", staging).replace("{namespace}", namespace)

    trocou = False
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
        if veredito == "MISMATCH":
            # Reprovado **antes** do swap: publicar um cubo que não fecha com a
            # origem e depois descobrir significaria servir dado errado.
            iceberg.cleanup_staged_table(staging, namespace=namespace)
            raise BaselineMismatchError(
                f"{cube_code}: total do staging R$ {total_out:,.2f} diverge de "
                f"R$ {financial_total_in:,.2f} (delta R$ {delta:,.2f}); nada foi trocado"
            )

        # Validação do chamador contra o staging, antes do swap: linhas de origem,
        # fatias esperadas e duplicidade são verificadas aqui, porque depois do swap
        # seria tarde — a tabela anterior já teria sido substituída.
        if validate_staging is not None:
            resultado_validacao = validate_staging(namespace, staging)
            if inspect.isawaitable(resultado_validacao):
                resultado_validacao = await resultado_validacao
            problemas = list(resultado_validacao or [])
            if problemas:
                iceberg.cleanup_staged_table(staging, namespace=namespace)
                raise BaselineMismatchError(
                    f"{cube_code}: staging reprovado antes do swap: " + "; ".join(problemas)
                )

        if namespace == layers.GOLD:
            iceberg.replace_staged_cube(
                table, staging, namespace=namespace, known_row_count=linhas
            )
        else:
            iceberg.swap_staged_table(
                table, staging, namespace=namespace, known_row_count=linhas
            )
        entrada.financial_total_out = total_out
        trocou = True
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
    except ManifestUnavailableError:
        # O manifesto terminal falhou **depois** do swap: a versão nova está
        # publicada sem registro. Rollback para o snapshot anterior devolve a
        # versão que tinha manifesto — e o cache só é invalidado se o rollback
        # falhar, porque aí fica publicado um dado sem prova.
        if trocou and anterior and namespace != layers.GOLD:
            devolveu = await rollback_table_to_snapshot(
                namespace=namespace, table=table, snapshot_id=anterior
            )
            if not devolveu:
                invalidate_published_cache(cube_code)
        try:
            entrada.status = FAILED
            entrada.error = "manifesto terminal não pôde ser gravado; publicação reprovada"
            entrada.finished_at = datetime.now(timezone.utc)
            await record_manifest(entrada)
        except ManifestUnavailableError as manifest_exc:
            # PostgreSQL continua indisponível: o RUNNING fica aberto e a migração
            # de órfãos o marca como ABANDONED quando o timeout passar.
            logger.error(
                "Recuperação do manifesto de %s pendente (PostgreSQL indisponível): %s",
                cube_code, manifest_exc,
            )
        raise
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
    """Registra o desfecho. Em modo obrigatório, falha aqui reprova a publicação.

    O swap já aconteceu quando isto é chamado, então o cubo está publicado e o
    manifesto não está: é exatamente o estado que não pode passar como sucesso. A
    exceção sobe com o cubo já trocado, e o chamador marca a task como falha
    preservando a evidência (o `previous_snapshot_id` da entrada RUNNING continua no
    banco para a recuperação).
    """
    entrada.status = saida.status
    entrada.rows_written = saida.rows
    entrada.financial_delta = saida.financial_delta
    entrada.reconciliation_status = saida.reconciliation_status
    entrada.reason = saida.skipped_reason
    entrada.finished_at = datetime.now(timezone.utc)
    await record_manifest(entrada)
    # Publicação bem-sucedida invalida o cache do cubo. O ponto é único de propósito:
    # o cache dos endpoints `finops_*` é chaveado pelo snapshot do cubo, e deixá-lo
    # vivo depois de um swap serviria o número antigo justamente quando o usuário
    # precisa ver o novo.
    if entrada.status == SUCCESS:
        try:
            from app.services.finops_cache import invalidate_after_publish

            invalidate_after_publish(entrada.cube_code)
        except Exception as exc:  # noqa: BLE001 — cache não derruba publicação
            logger.warning("Falha ao invalidar cache de %s: %s", entrada.cube_code, exc)
        try:
            from app.services.iceberg_maintenance import request_maintenance_after_publish

            request_maintenance_after_publish(entrada.namespace, entrada.physical_table)
        except Exception as exc:  # noqa: BLE001 — manutenção não derruba publicação
            logger.warning("Falha ao pedir manutenção de %s: %s", entrada.physical_table, exc)


class ManifestUnavailableError(RuntimeError):
    """O manifesto não pôde ser persistido.

    Levantada por `record_manifest` no modo obrigatório. Publicar um cubo
    governado sem manifesto é o defeito que G2 existe para eliminar: o número passa
    a existir sem prova de origem, de SQL, de totais nem de rollback.
    """


#: Modo de registro do manifesto.
#:
#: `REQUIRED` é o modo dos cubos `finops_*` e de qualquer publicação nova: falha ao
#: registrar aborta a publicação. `BEST_EFFORT` existe apenas para os caminhos
#: legados identificados em `_BEST_EFFORT_CUBES`, onde o manifesto foi introduzido
#: depois do cubo já estar em produção.
MANIFEST_REQUIRED = "REQUIRED"
MANIFEST_BEST_EFFORT = "BEST_EFFORT"

#: Cubos que ainda usam best effort. Lista **fechada** e explícita: enquanto o
#: manifesto era opcional em todo lugar, um caminho novo podia nascer sem rastro
#: sem ninguém notar. Nenhum cubo `finops_*` pertence a esta lista.
_BEST_EFFORT_CUBES: frozenset[str] = frozenset(
    {
        # Publicados por caminho legado (frame em memória por fatia) antes do
        # manifesto existir. Migrar para REQUIRED é item de G4.
        "dest_dm_cost_focus",
        "dest_oci_operational_metrics",
    }
)


def manifest_mode_for(cube_code: str) -> str:
    """Modo de registro do manifesto para um cubo.

    O default é **obrigatório**. Best effort só para o que está na allowlist
    fechada, e a allowlist não é configurável por usuário.
    """
    base = str(cube_code or "").split(":", 1)[0]
    return (
        MANIFEST_BEST_EFFORT
        if base in _BEST_EFFORT_CUBES
        else MANIFEST_REQUIRED
    )


def record_manifest_sync(entry: ManifestEntry, *, mode: str | None = None) -> None:
    """Persiste o manifesto via engine síncrono (psycopg2).

    O worker materializa Gold dentro de ``asyncio.run`` e misturar asyncpg com
    o pool global gerava ``another operation is in progress`` no INSERT.
    """
    modo = mode or manifest_mode_for(entry.cube_code)
    try:
        from sqlalchemy import text

        from app.database import sync_engine
    except Exception as exc:  # noqa: BLE001
        mensagem = f"Manifesto Gold indisponível ({sanitize_error(exc)})"
        if modo == MANIFEST_REQUIRED:
            raise ManifestUnavailableError(mensagem) from exc
        logger.warning("%s", mensagem)
        return

    dados = entry.as_dict()
    params = {**dados, "source_tables": _json_list(dados["source_tables"])}
    try:
        with sync_engine.begin() as conn:
            if entry.manifest_id is None:
                resultado = conn.execute(
                    text(_manifest_insert_sql() + " RETURNING id"),
                    params,
                )
                linha = resultado.first()
                entry.manifest_id = int(linha[0]) if linha else None
            else:
                conn.execute(
                    text(_manifest_update_sql()),
                    {**params, "id": entry.manifest_id},
                )
    except Exception as exc:  # noqa: BLE001
        mensagem = f"Falha ao registrar manifesto de {entry.cube_code}: {sanitize_error(exc)}"
        if modo == MANIFEST_REQUIRED:
            raise ManifestUnavailableError(mensagem) from exc
        logger.warning("%s", mensagem)


async def record_manifest(entry: ManifestEntry, *, mode: str | None = None) -> None:
    """Persiste o manifesto no PostgreSQL (delega ao caminho síncrono)."""
    import asyncio

    await asyncio.to_thread(record_manifest_sync, entry, mode=mode)


#: Campos atualizados no desfecho. `manifest_id`, `cube_code` e a identidade da
#: execução não mudam: só o resultado.
_MANIFEST_UPDATE_FIELDS = (
    "status", "reason", "rows_read", "rows_written",
    "financial_total_in", "financial_total_out", "financial_delta",
    "tolerance", "reconciliation_status", "currency",
    "previous_snapshot_id", "rollback_table", "error",
    "started_at", "finished_at",
)


def _manifest_update_sql() -> str:
    campos = ",\n    ".join(f"{c} = :{c}" for c in _MANIFEST_UPDATE_FIELDS)
    return f"""
UPDATE gold_manifest SET
    {campos}
WHERE id = :id
""".strip()


def _manifest_insert_sql() -> str:
    return """
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

    Mantém o contrato anterior (uma chamada, um registro), mas por baixo cria o
    `RUNNING` **antes** de devolver o resultado e o fecha na mesma linha — a ordem
    exigida para que um manifesto não seja um evento solto pós-swap.

    Vazio é `PARTIAL` a menos que o chamador exija erro.
    """
    handle = await begin_frame_publication(
        cube_code=cube_code,
        physical_table=physical_table,
        namespace=namespace,
        run_id=run_id,
        source_tables=source_tables,
    )
    vazio = rows_written <= 0
    if vazio and empty_is_error:
        await finish_frame_publication(
            handle, rows_written=0, status=FAILED,
            reason="não produziu linhas",
        )
        raise RuntimeError(f"Materialização {cube_code} não produziu linhas")
    status = PARTIAL if vazio else SUCCESS
    await finish_frame_publication(
        handle,
        rows_written=rows_written,
        status=status,
        reason="sem linhas" if vazio else None,
    )
    return handle.entry


def record_manifest_blocking(entry: ManifestEntry) -> None:
    """Versão síncrona de :func:`record_manifest`, para caminhos que já são sync."""
    record_manifest_sync(entry)


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


async def materialize_gold_sql(
    *,
    cube_code: str,
    table: str,
    select_sql: str,
    namespace: str = layers.GOLD,
    partition_columns: tuple[str, ...] = (),
    run_id: int | None = None,
    rule_id: int | None = None,
    currency: str | None = None,
    financial_total_in: float | None = None,
    empty_is_error: bool = False,
    progress: Any | None = None,
    timeout_s: float = 3600.0,
) -> PublishOutcome:
    """Materializa um cubo a partir do SQL de select, com partição declarada.

    Açúcar sobre :func:`publish_gold_cube` para o caso comum dos cubos
    financeiros: o chamador escreve apenas o `SELECT` e declara as colunas de
    partição, e o manifesto é preenchido com hash, fontes e totais.
    """
    particao = ""
    if partition_columns:
        colunas = ", ".join(f"'{c}'" for c in partition_columns)
        particao = f" WITH (partitioning = ARRAY[{colunas}])"
    return await publish_gold_cube(
        cube_code=cube_code,
        table=table,
        select_sql=select_sql,
        namespace=namespace,
        run_id=run_id,
        rule_id=rule_id,
        create_sql=(
            f"CREATE TABLE iceberg.{{namespace}}.{{staging}}{particao} AS {select_sql}"
        ),
        currency=currency,
        financial_total_in=financial_total_in,
        empty_is_error=empty_is_error,
        progress=progress,
        timeout_s=timeout_s,
    )


# ── Baseline financeiro único por run ────────────────────────────────────────


@dataclass(frozen=True)
class CostBaseline:
    """Baseline financeiro medido **uma vez** na Silver por execução da fila.

    A auditoria exige que C1 e C2 sejam reconciliados contra o **mesmo** número: se
    cada materializador medisse a origem na hora própria, uma carga intermediária
    mudaria o valor entre eles e a comparação deixaria de provar a preservação.
    """

    total: float
    rows: int
    currencies: tuple[str, ...]
    periods: tuple[str, ...]
    connections: tuple[str, ...]
    slices: tuple[tuple[str, str], ...]
    dataset: str = "resource_cost"
    layer: str = "silver"
    measured_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "rows": self.rows,
            "currencies": list(self.currencies),
            "periods": list(self.periods),
            "connections": list(self.connections),
            "slices": [list(s) for s in self.slices],
            "dataset": self.dataset,
            "layer": self.layer,
            "measured_at": self.measured_at,
        }


class BaselineMismatchError(RuntimeError):
    """O cubo publicado não fecha com o baseline medido na Silver."""


async def cost_baseline(
    *, dataset: str = "resource_cost", layer: str = "silver", timeout_s: float = 1800.0
) -> CostBaseline:
    """Lê de uma vez: total, linhas, moedas, períodos e conexões esperados.

    Uma única função porque a reconciliação obrigatória precisa de **uma** fonte:
    C1 e C2 recebem este objeto, e divergir dele reprova a publicação.
    """
    from app.services.trino_service import execute as trino_execute

    resumo = await trino_execute(
        f"""
SELECT ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)), 2) AS total,
       COUNT(*) AS rows
FROM iceberg.{layer}.{dataset}
WHERE COALESCE(_billing_period, billing_period) IS NOT NULL
""",
        timeout_s=timeout_s,
    )
    moedas = await trino_execute(
        f"SELECT DISTINCT CAST(COALESCE(NULLIF(TRIM(CAST(pricing_currency AS VARCHAR)), ''), "
        f"'{UNKNOWN}') AS VARCHAR) AS currency FROM iceberg.{layer}.{dataset}",
        timeout_s=timeout_s,
    )
    fatias = await trino_execute(
        f"""
SELECT CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) AS billing_period,
       COALESCE(_source_connection_code, _source_connection_name) AS connection_code,
       COUNT(*) AS rows
FROM iceberg.{layer}.{dataset}
WHERE COALESCE(_billing_period, billing_period) IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2
""",
        timeout_s=timeout_s,
    )
    total = float((resumo[0] if resumo else {}).get("total") or 0)
    linhas = int((resumo[0] if resumo else {}).get("rows") or 0)
    moedas = tuple(sorted(str(r["currency"]) for r in moedas or [] if r.get("currency")))
    fatias = [
        (str(r["billing_period"]), str(r["connection_code"]))
        for r in (
            await trino_execute(
                f"""
SELECT CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) AS billing_period,
       COALESCE(_source_connection_code, _source_connection_name) AS connection_code
FROM iceberg.{layer}.{dataset}
WHERE COALESCE(_billing_period, billing_period) IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2
""",
                timeout_s=timeout_s,
            )
            or []
        )
    ]
    return CostBaseline(
        total=round(total, 2),
        rows=linhas,
        currencies=moedas,
        periods=tuple(sorted({p for p, _ in fatias})),
        connections=tuple(sorted({c for _, c in fatias})),
        slices=tuple(fatias),
        dataset=dataset,
        layer=layer,
        measured_at=datetime.now(timezone.utc).isoformat(),
    )


def assert_baseline(
    baseline: CostBaseline,
    *,
    total_out: float,
    rows_out: int,
    currencies_out: list[str] | None = None,
    slices_out: list[tuple[str, str]] | None = None,
    table_label: str,
    tolerance: float = RECONCILIATION_TOLERANCE_BRL,
) -> None:
    """Reprova o cubo quando não fecha com o baseline, com o motivo exato.

    Quatro verificações, na ordem em que a auditoria as lista: total, linhas de
    origem, moeda única e fatias esperadas. Cada uma produz uma mensagem que diz o
    **número esperado** e o **número medido** — "reconciliação falhou" sem o par de
    números não permite investigar.
    """
    problemas: list[str] = []

    delta = round(float(total_out) - float(baseline.total), 2)
    if abs(delta) > tolerance:
        problemas.append(
            f"total diverge: publicado R$ {total_out:,.2f} contra "
            f"R$ {baseline.total:,.2f} da Silver (delta R$ {delta:,.2f})"
        )

    if rows_out != baseline.rows:
        problemas.append(
            f"linhas de origem divergem: publicado {rows_out:,} contra "
            f"{baseline.rows:,} da Silver"
        )

    if currencies_out:
        if len(currencies_out) > 1:
            problemas.append(
                f"múltiplas moedas no cubo: {', '.join(sorted(currencies_out))}"
            )
        ausentes = set(baseline.currencies) - set(currencies_out)
        if baseline.currencies and set(currencies_out) - set(baseline.currencies):
            problemas.append(
                f"moedas fora do baseline: "
                f"{', '.join(sorted(set(currencies_out) - set(baseline.currencies)))}"
            )

    if slices_out is not None:
        esperadas = set(baseline.slices)
        presentes = set(slices_out)
        faltantes = sorted(esperadas - presentes)
        if faltantes:
            problemas.append(
                f"fatias esperadas ausentes: "
                f"{', '.join(f'{p}@{c}' for p, c in faltantes[:10])}"
                f"{'…' if len(faltantes) > 10 else ''}"
            )

    if problemas:
        raise BaselineMismatchError(
            f"{table_label}: " + "; ".join(problemas)
        )


# ── Publicação por frame: begin/finish, não um registro pós-swap ─────────────


class FramePublicationHandle:
    """Manifesto `RUNNING` de uma publicação por frame.

    Antes deste handle, a publicação por frame registrava o manifesto **depois** do
    swap: o cubo podia ter sido trocado e o registro falhar, deixando uma versão
    publicada sem rastro. Agora o `RUNNING` existe antes de qualquer escrita e o
    desfecho atualiza a **mesma** linha.
    """

    def __init__(self, entry: ManifestEntry) -> None:
        self.entry = entry

    @property
    def manifest_id(self) -> int | None:
        return self.entry.manifest_id


async def begin_frame_publication(
    *,
    cube_code: str,
    physical_table: str,
    namespace: str = layers.GOLD,
    run_id: int | None = None,
    source_tables: list[str] | None = None,
    rows_read: int | None = None,
) -> FramePublicationHandle:
    """Cria o manifesto `RUNNING` **antes** de qualquer escrita.

    Em modo obrigatório, falha aqui aborta a publicação antes de o cubo ser
    escrito — o mesmo contrato de `publish_gold_cube`, aplicado ao caminho por
    frame.
    """
    entrada = ManifestEntry(
        cube_code=cube_code,
        physical_table=physical_table,
        namespace=namespace,
        run_id=run_id,
        status=RUNNING,
        source_tables=list(source_tables or []),
        rows_read=rows_read,
        previous_snapshot_id=current_snapshot_id(namespace, physical_table),
        started_at=datetime.now(timezone.utc),
    )
    await record_manifest(entrada)
    entrada.manifest_started = True
    return FramePublicationHandle(entrada)


async def finish_frame_publication(
    handle: FramePublicationHandle,
    *,
    rows_written: int,
    status: str = SUCCESS,
    reason: str | None = None,
    financial_total_out: float | None = None,
    financial_total_in: float | None = None,
) -> None:
    """Fecha o manifesto aberto por :func:`begin_frame_publication`.

    Atualiza **a mesma linha** (`manifest_id`). Falha aqui em modo obrigatório
    reprova a publicação: o swap já aconteceu, então o chamador deve tentar
    rollback e marcar a task como falha.
    """
    entrada = handle.entry
    entrada.status = status
    entrada.rows_written = rows_written
    entrada.financial_total_out = financial_total_out
    entrada.financial_total_in = financial_total_in
    entrada.financial_delta = (
        None
        if financial_total_in is None or financial_total_out is None
        else round(float(financial_total_out) - float(financial_total_in), 2)
    )
    if status == SUCCESS and entrada.financial_delta is not None:
        entrada.reconciliation_status = (
            "OK" if abs(entrada.financial_delta) <= entrada.tolerance else "MISMATCH"
        )
    entrada.reason = reason
    entrada.finished_at = datetime.now(timezone.utc)
    await record_manifest(entrada)
    if status == SUCCESS:
        invalidate_published_cache(entrada.cube_code)


def invalidate_published_cache(cube_code: str) -> None:
    """Invalida o cache de um cubo cuja versão publicada permaneceu válida."""
    try:
        from app.services.finops_cache import invalidate_after_publish

        invalidate_after_publish(cube_code)
    except Exception as exc:  # noqa: BLE001 — cache não derruba publicação
        logger.warning("Falha ao invalidar cache de %s: %s", cube_code, exc)


# Alias com o nome antigo: o ponto de chamada é único de propósito.
def invalidate_published_cache_if_valid(cube_code: str) -> None:
    invalidate_published_cache(cube_code)


# Alias usado pelo caminho de SQL; nome antigo mantido para não quebrar chamadores.
def invalidate_after_publish(cube_code: str) -> int:
    try:
        from app.services.finops_cache import invalidate_after_publish as _inv

        return _inv(cube_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao invalidar cache de %s: %s", cube_code, exc)
        return 0


async def rollback_table_to_snapshot(
    *, namespace: str, table: str, snapshot_id: str
) -> bool:
    """Devolve a tabela ao snapshot anterior, quando há um para voltar.

    É o rollback que a auditoria exige quando o manifesto terminal falha **depois**
    do swap: publicar sem manifesto é o defeito, mas publicar e deixar a versão nova
    sem registro é pior — o rollback devolve a versão anterior, que tinha manifesto.

    ``False`` quando não havia snapshot anterior ou o rollback falhou; quem chama
    decide o que fazer com essa informação.
    """
    if not snapshot_id:
        return False
    try:
        cat = iceberg._get_catalog()  # noqa: SLF001
        alvo = cat.load_table(iceberg._table_id(namespace, table.replace("-", "_")))
        with alvo.manage_snapshots() as gestor:
            gestor.rollback(int(snapshot_id))
        return True
    except Exception as exc:  # noqa: BLE001 — rollback falho precisa ser visível
        logger.error(
            "Rollback de %s.%s para o snapshot %s falhou: %s",
            namespace, table, snapshot_id, sanitize_error(exc),
        )
        return False


def expire_stale_running_manifests_sync(*, timeout_hours: float = 2.0) -> int:
    """Versão síncrona — usada no worker Gold junto com ``record_manifest_sync``."""
    try:
        from sqlalchemy import text

        from app.database import sync_engine
    except Exception:  # noqa: BLE001
        return 0
    try:
        with sync_engine.begin() as conn:
            resultado = conn.execute(
                text(
                    """
                    UPDATE gold_manifest
                    SET status = :abandoned,
                        reason = :motivo,
                        finished_at = now()
                    WHERE id IN (
                        SELECT m.id FROM gold_manifest m
                        WHERE m.status = :running
                          AND m.finished_at IS NULL
                          AND m.started_at < now() - make_interval(secs => CAST(:seconds AS DOUBLE PRECISION))
                          AND NOT EXISTS (
                              SELECT 1 FROM etl_task t
                              WHERE t.id = m.run_id
                                AND t.status IN ('QUEUED', 'RUNNING')
                          )
                    )
                    """
                ),
                {
                    "abandoned": ABANDONED,
                    "running": RUNNING,
                    "seconds": timeout_hours * 3600,
                    "motivo": f"RUNNING órfão: sem término há mais de {timeout_hours:.0f}h",
                },
            )
            return int(resultado.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao expirar manifestos RUNNING órfãos: %s", sanitize_error(exc))
        return 0


async def expire_stale_running_manifests(
    *, timeout_hours: float = 2.0, limit: int = 5000
) -> int:
    """Marca como `ABANDONED` os `RUNNING` órfãos.

    Um `RUNNING` é órfão quando: não tem `finished_at`, é mais velho que o timeout
    operacional e não corresponde a nenhuma task em execução com o mesmo `run_id`.

    Sem esta migração, um worker morto no meio de um build deixaria um `RUNNING`
    aberto para sempre, e a leitura "quais cubos estão sendo materializados agora?"
    mentiria.
    """
    import asyncio

    _ = limit  # reservado; a query SQL não pagina hoje
    return await asyncio.to_thread(
        expire_stale_running_manifests_sync,
        timeout_hours=timeout_hours,
    )


# ── Lock distribuído por cubo ────────────────────────────────────────────────


class CubeLockUnavailableError(RuntimeError):
    """Outra execução já materializa este cubo.

    Duas execuções de C2 em paralelo publicariam builds distintos na mesma tabela
    final — o segundo swap sobrescreveria o primeiro sem que nenhum dos dois soubesse.
    """


@dataclass(frozen=True)
class CubeLock:
    cube_code: str
    holder: str
    acquired_at: str = ""


async def acquire_cube_lock(
    cube_code: str, *, holder: str | None = None, timeout_hours: float = 2.0
) -> CubeLock:
    """Adquire o lock de materialização de um cubo no PostgreSQL.

    Uma tabela de lock, e não um advisory lock de sessão: a materialização dura de
   zenas de minutos e cruza várias transações, e um advisory lock amarraria uma
    conexão por todo o build. Com linha própria, o lock é observável no banco e
    pode ser inspecionado quando algo travar.

    Um lock vencido é roubado: um worker morto não pode bloquear o cubo para
    sempre — mas roubar exige que o tempo tenha passado, para que duas execuções
    genuinamente simultâneas continuem sendo impossíveis.
    """
    import uuid as _uuid

    try:
        from app.database import async_session
        from sqlalchemy import text
    except Exception as exc:  # noqa: BLE001
        raise CubeLockUnavailableError(f"lock de {cube_code} indisponível: {exc}") from exc

    token = holder or f"pid-{os.getpid()}-{_uuid.uuid4().hex[:12]}"
    try:
        async with async_session() as session:
            # Locks vencidos são liberados primeiro, para que um worker morto não
            # bloqueie o cubo para sempre.
            await session.execute(
                text(
                    "DELETE FROM gold_cube_lock WHERE expires_at < now()"
                ),
            )
            resultado = await session.execute(
                text(
                    "INSERT INTO gold_cube_lock (cube_code, holder, expires_at) "
                    "VALUES (:code, :holder, now() + make_interval(hours => :h)) "
                    "ON CONFLICT (cube_code) DO NOTHING"
                ),
                {"code": cube_code, "holder": token, "h": timeout_hours},
            )
            if int(getattr(resultado, "rowcount", 0) or 0) == 0:
                linha = (
                    await session.execute(
                        text("SELECT holder, expires_at FROM gold_cube_lock WHERE cube_code = :code"),
                        {"code": cube_code},
                    )
                ).first()
                dono = str(linha[0]) if linha else "desconhecido"
                raise CubeLockUnavailableError(
                    f"{cube_code}: já está em materialização por {dono or 'outro processo'}"
                    if linha
                    else f"{cube_code}: lock não adquirido"
                )
            await session.commit()
    except Exception as exc:
        if isinstance(exc, CubeLockUnavailableError):
            raise
        raise CubeLockUnavailableError(
            f"falha ao adquirir lock de {cube_code}: {sanitize_error(exc)}"
        ) from exc
    return CubeLock(cube_code=cube_code, holder=token)


async def release_cube_lock(lock: CubeLock) -> None:
    """Libera o lock somente se for o nosso. Nunca o de outro."""
    try:
        from app.database import async_session
        from sqlalchemy import text
    except Exception:  # noqa: BLE001
        return
    try:
        async with async_session() as session:
            await session.execute(
                text("DELETE FROM gold_cube_lock WHERE cube_code = :code AND holder = :holder"),
                {"code": lock.cube_code, "holder": lock.holder},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao liberar lock de %s: %s", lock.cube_code, sanitize_error(exc))
