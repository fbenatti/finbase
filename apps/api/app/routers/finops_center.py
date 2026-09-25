"""Centro de Decisão FinOps — endpoints de leitura sobre os cubos Gold.

Os cubos financeiros (`finops_cost_monthly`, `finops_cost_resource_monthly`,
`finops_cost_change_driver`) são materializados pelo caminho governado de G2, com
manifesto e reconciliação. Esta é a camada de consulta: resumo para o card, detalhe
paginado para a tabela, e nenhum SQL livre vindo do cliente.

Regras que estes endpoints respeitam (seção 8 do plano):

- **filtros por allowlist.** Nenhum nome de coluna vem do cliente; a lista de
  valores aceitos por dimensão é declarada aqui;
- **resumo separado de detalhe paginado.** O resumo nunca devolve recurso; o
  detalhe nunca varre o cubo inteiro;
- **contrato comum de resposta** com `scope`, `as_of`, `currency`, `data_status`,
  `quality` e `data`;
- **medidas não aditivas não são somadas.** `resources`,
  `cost_missing_dimension` e `cost_without_tag` são publicadas linha a linha; o
  total de recursos distintos vem de uma contagem própria, não da soma da coluna.
  Somar `resources` pelas dimensões devolveu 21.229.892 contra 968.339 reais;
- **moeda explícita.** Somar BRL com USD é recusado, não convertido.
"""

from __future__ import annotations

import time
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require
from app.database import get_db
from app.models.identity import User
from app.models.source_connection import SystemIntegration
from app.services import lakehouse_layers as layers
from app.services.finops_cache import (
    cache_key,
    get_cached,
    record_cache_hit,
    record_request_metric,
    set_cached,
)
from app.services.finops_cost_driver import C3_TABLE, DRIVER_TYPES, driver_key_column
from app.services.finops_cost_monthly import (
    C1_TABLE,
    NON_ADDITIVE_MEASURES,
    unavailable_measures,
)
from app.services.finops_cost_resource import C2_TABLE
from app.services.semantic_normalization import CurrencyMixedError, assert_single_currency
from app.services.trino_service import execute as trino_execute
from app.services.metric_window import MetricDayWindow, resolve_metric_day_window

router = APIRouter(prefix="/finops", tags=["finops"])

#: Dependência de autorização. Todos os endpoints do Centro de Decisão leem custo
#: agregado, então exigem `finops:read` no mesmo padrão dos demais routers
#: protegidos (`require(module, action)`).
_READ = Depends(require("finops", "read"))

#: Dimensões filtráveis do C1. A allowlist é o contrato: o cliente manda o nome
#: lógico, nunca a coluna.
C1_FILTERS: dict[str, str] = {
    "provider": "provider",
    "connection": "connection_code",
    "account": "sub_account_id",
    "service": "service_key",
    "service_category": "service_category",
    "region": "region_key",
    "charge_category": "charge_category",
    "currency": "currency",
    "period": "billing_period",
}

#: Dimensões filtráveis do C2, além das do C1.
C2_FILTERS: dict[str, str] = {
    **C1_FILTERS,
    "resource_type": "resource_type_key",
    "resource_group": "resource_group_id",
    "inventory_status": "inventory_status",
}

#: Medidas que podem ser ordenadas no detalhe. Ordenar por coluna arbitrária é
#: caminho para scan completo.
_SORTABLE = {
    "effective_cost",
    "list_cost",
    "rows",
    "billing_period",
    "resource_name",
}

#: Timeout das consultas de resumo e detalhe.
_SUMMARY_TIMEOUT = 60.0
_DETAIL_TIMEOUT = 90.0

# As séries diárias deste painel correspondem apenas à fonte 4P validada. O ID
# é a identidade da conexão de origem no lakehouse; `resource_id` continua sendo
# a chave de relacionamento do recurso.
_METRICS_4P_CONNECTION_ID = 1
_METRICS_4P_VM_OWNER = "monitor_vm_metrics"
_METRICS_4P_CONTAINER_OWNER = "container_insights"


def _metric_partition_filter(alias: str, window: MetricDayWindow, *, owner: str) -> str:
    """Predicate obrigatório para leitura histórica das séries diárias 4P."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("alias SQL inválido")
    return (
        f"{alias}._source_connection_id = {_METRICS_4P_CONNECTION_ID} "
        f"AND {alias}._silver_owner = {_quote_literal(owner)} "
        f"AND {alias}._billing_period BETWEEN {_quote_literal(window.from_day)} "
        f"AND {_quote_literal(window.to_day)}"
    )


def _observed_partition_days(rows: list[dict[str, Any]], field: str = "utc_day") -> int:
    return len({str(row.get(field) or "")[:10].replace("-", "") for row in rows if row.get(field)})


def _as_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _quote_literal(valor: Any) -> str:
    """Literal SQL a partir de valor do cliente.

    Escapa aspas simples e descarta qualquer coisa que não seja escalar. É o único
    ponto de interpolação — os nomes de coluna vêm da allowlist, nunca do cliente.
    """
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float)):
        return str(valor)
    texto = str(valor).replace("'", "''")
    return f"'{texto}'"


def _resolved(valor: Any) -> Any:
    """Trata default não resolvido de `Query` como ausente.

    FORA do ciclo de requisição do FastAPI — em teste direto ou chamada interna — o
    default de `param: str = Query(None)` ainda é o objeto `Query`, não `None`. Sem
    este tratamento, a comparação `valor in (None, "")` é falsa e o filtro tenta
    virar `coluna = Query(...)`. Em requisição real o FastAPI já resolve, então o
    valor chega escalar.
    """
    if valor is None:
        return None
    if type(valor).__module__.startswith("fastapi"):
        return getattr(valor, "default", None)
    return valor


def _where(
    *,
    filters: dict[str, str],
    values: dict[str, Any],
    extra: list[str] | None = None,
) -> str:
    """`WHERE` a partir da allowlist e dos valores recebidos.

    Valor desconhecido na allowlist levanta: um filtro ignorado em silêncio
    devolveria um número que não corresponde ao que o usuário pediu.
    """
    condicoes: list[str] = []
    for chave, bruto in values.items():
        valor = _resolved(bruto)
        if valor in (None, ""):
            continue
        if chave not in filters:
            raise HTTPException(
                status_code=400,
                detail=f"filtro não suportado: {chave!r}. Aceitos: {', '.join(sorted(filters))}",
            )
        coluna = filters[chave]
        if isinstance(valor, (list, tuple, set)):
            lista = ", ".join(_quote_literal(v) for v in valor)
            condicoes.append(f"{coluna} IN ({lista})")
        else:
            condicoes.append(f"{coluna} = {_quote_literal(valor)}")
    condicoes.extend(extra or [])
    return " AND ".join(condicoes) if condicoes else "1 = 1"


def _separate_currency(rows: list[dict[str, Any]]) -> str | None:
    """Moeda única do resultado, ou erro explícito se houver mais de uma.

    Não converte e não escolhe a da maioria: somar moedas diferentes é inválido.
    """
    if not rows:
        return None
    try:
        return assert_single_currency(rows)
    except CurrencyMixedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _envelope(
    *,
    scope: dict[str, Any],
    data: dict[str, Any],
    currency: str | None = None,
    data_status: str = "COMPLETE",
    quality: dict[str, Any] | None = None,
    page: dict[str, Any] | None = None,
    elapsed_ms: float | None = None,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contrato comum de resposta (seção 8)."""
    return {
        "scope": scope,
        "as_of": _now_iso(),
        "currency": currency,
        "data_status": data_status,
        "quality": quality or {"coverage_pct": None, "warnings": []},
        "data": data,
        "page": page,
        "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
        "cache": cache,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def _run(sql: str, *, timeout_s: float) -> tuple[list[dict[str, Any]], float]:
    """Executa a consulta e devolve as linhas com o tempo decorrido."""
    inicio = time.monotonic()
    linhas = await trino_execute(sql, timeout_s=timeout_s)
    return list(linhas or []), (time.monotonic() - inicio) * 1000.0


async def _run_cached(
    *,
    endpoint: str,
    cube: str,
    filters: dict[str, Any],
    sql: str,
    timeout_s: float,
) -> tuple[list[dict[str, Any]], float, bool]:
    """Executa com cache por cubo + snapshot publicado + filtros.

    Devolve as linhas, o tempo decorrido e se veio do cache. A versão que entra na
    chave é o snapshot atual do cubo, obtido do manifesto: uma publicação nova muda
    a versão e a entrada antiga deixa de ser encontrada, mesmo antes da invalidação
    explícita.

    Toda chamada registra amostra de latência — inclusive a que veio do cache, que é
    a latência que o usuário sente.
    """
    inicio = time.monotonic()
    versao = await _cubo_version(cube)
    chave = cache_key(cube, filters, version=versao)
    em_cache = get_cached(chave)
    if em_cache is not None:
        record_cache_hit(endpoint, True)
        decorrido = (time.monotonic() - inicio) * 1000.0
        record_request_metric(endpoint, decorrido)
        return list(em_cache), decorrido, True

    record_cache_hit(endpoint, False)
    try:
        linhas, _ = await _run(sql, timeout_s=timeout_s)
    except Exception:
        record_request_metric(endpoint, (time.monotonic() - inicio) * 1000.0, error=True)
        raise
    set_cached(chave, linhas, version=versao)
    decorrido = (time.monotonic() - inicio) * 1000.0
    record_request_metric(endpoint, decorrido)
    return linhas, decorrido, False


async def _cubo_version(cube: str) -> str:
    """Versão publicada do cubo, pelo manifesto. ``unversioned`` quando ausente."""
    from app.services.gold_governance import current_snapshot_id

    try:
        return current_snapshot_id(layers.GOLD, cube) or "unversioned"
    except Exception:  # noqa: BLE001 — versão ausente não impede responder
        return "unversioned"


# ── Resumo ───────────────────────────────────────────────────────────────────


def summary_sql(where: str) -> str:
    """Série de custo por competência e moeda do escopo.

    O `GROUP BY` inclui a moeda para que uma mistura apareça como duas linhas e seja
    recusada por :func:`_separate_currency`, em vez de somada.
    """
    return f"""
SELECT billing_period, currency,
       ROUND(SUM(effective_cost), 2) AS effective_cost,
       ROUND(SUM(list_cost), 2) AS list_cost,
       SUM(rows) AS source_rows,
       COUNT(*) AS cube_rows
FROM iceberg.{layers.GOLD}.{C1_TABLE}
WHERE {where} AND billing_period IS NOT NULL
GROUP BY 1, 2
ORDER BY 1
"""


@router.get("/summary")
async def summary(
    period: str | None = Query(None, description="Competência YYYYMM"),
    provider: str | None = None,
    connection: str | None = None,
    account: str | None = None,
    service: str | None = None,
    region: str | None = None,
    currency: str | None = None,
    user: User = _READ,
) -> dict[str, Any]:
    """Custo do escopo, com escopo, qualidade e indisponibilidades declaradas.

    O card precisa responder `valor + comparação + escopo + qualidade + ação`. Um
    valor isolado não é suficiente, então a resposta carrega:
    `portable` (custos aditivos), `non_additive` (medidas que não podem ser
    somadas), `unavailable` (o que a origem não permite medir) e `periods` (a série
    com o status de cada competência).
    """
    where = _where(
        filters=C1_FILTERS,
        values={
            "period": period,
            "provider": provider,
            "connection": connection,
            "account": account,
            "service": service,
            "region": region,
            "currency": currency,
        },
    )
    linhas, ms, do_cache = await _run_cached(
        endpoint="summary",
        cube=C1_TABLE,
        filters={"period": period, "provider": provider, "connection": connection,
                 "account": account, "service": service, "region": region,
                 "currency": currency},
        sql=summary_sql(where),
        timeout_s=_SUMMARY_TIMEOUT,
    )
    moeda = _separate_currency(linhas)

    custo_mensal = sum(float(r.get("effective_cost") or 0) for r in linhas)
    periodo_atual = linhas[-1]["billing_period"] if linhas else None
    custo_atual = (
        float(linhas[-1].get("effective_cost") or 0) if linhas else None
    )

    # MoM e run rate vêm do classificador de cobertura: um par com período parcial
    # devolve o motivo, não um percentual enganoso.
    comparacao = await _mom_and_run_rate(
        where=where, periods=[r["billing_period"] for r in linhas], financial=linhas
    )

    qualidade = await _quality(where=where)

    return _envelope(
        scope={
            "period": period,
            "provider": provider,
            "connection": connection,
            "account": account,
            "service": service,
            "region": region,
            "currency": moeda,
            "periods_available": len(linhas),
        },
        currency=moeda,
        data_status=(
            "EMPTY"
            if not linhas
            else str(comparacao.get("current_status") or "COMPLETE")
        ),
        quality=qualidade,
        cache={"hit": do_cache, "version_source": "gold_manifest"},
        data={
            "cube": C1_TABLE,
            "latest_period": periodo_atual,
            "latest_period_cost": custo_atual,
            "total_cost": round(custo_mensal, 2),
            "comparison": comparacao,
            "months_used": comparacao.get("months_used", 0),
            "non_additive": sorted(NON_ADDITIVE_MEASURES),
            "unavailable": unavailable_measures(),
            "periods": [
                {
                    "billing_period": r["billing_period"],
                    "effective_cost": float(r.get("effective_cost") or 0),
                    "source_rows": int(r.get("source_rows") or 0),
                }
                for r in linhas
            ],
        },
        elapsed_ms=ms,
    )


async def _mom_and_run_rate(
    *, where: str, periods: list[str], financial: list[dict[str, Any]]
) -> dict[str, Any]:
    """MoM e run rate usando **apenas** competências comparáveis.

    O status vem de `gold.finops_period_coverage`, a tabela publicada que C3 também
    lê. Não existe segunda definição de "período comparável" no projeto, e nenhum
    request reclassifica período.
    """
    from app.services.period_coverage import (
        COMPARABLE_STATUSES,
        PeriodCoverage,
        load_coverage_map,
        month_over_month,
        run_rate_annualized,
    )

    # Comparabilidade vem da tabela publicada, não de reclassificação no request.
    statuses = await load_coverage_map()
    conexoes = await _connections_in_scope(where)

    # Monta a cobertura a partir do **status publicado**, sem classificar nada aqui.
    # `PeriodFacts` e o classificador não aparecem: a API pergunta, não decide.
    coberturas = []
    for conexao in conexoes:
        for periodo in periods:
            status = statuses.get((conexao, periodo))
            if status is None:
                # Período sem cobertura publicada não entra na comparação.
                continue
            coberturas.append(
                PeriodCoverage(
                    connection_code=conexao,
                    billing_period=periodo,
                    dataset=C1_TABLE,
                    status=status,
                    reason="cobertura publicada",
                    cost=_cost_of(financial, periodo),
                )
            )

    comparaveis = [c for c in coberturas if c.status in COMPARABLE_STATUSES]
    comparaveis.sort(key=lambda c: c.billing_period)
    recentes = _periodos_consecutivos_finais(comparaveis)
    mom = (
        month_over_month(recentes[-2], recentes[-1])
        if len(recentes) >= 2
        else {"comparable": False, "reason": "menos_de_duas_competencias_comparaveis"}
    )
    run_rate = run_rate_annualized(recentes)
    return {
        "mom": mom,
        "run_rate": run_rate,
        "months_used": run_rate.get("months_used", 0),
        "comparable_periods": [c.billing_period for c in comparaveis],
        "run_rate_periods": [c.billing_period for c in recentes],
        "current_status": (
            coberturas[-1].status if coberturas else None
        ),
    }


def _periodos_consecutivos_finais(periodos: list[Any]) -> list[Any]:
    """Recorta meses comparáveis ao trecho final, sem atravessar lacunas."""
    periodo_de = lambda item: getattr(item, "billing_period", item)
    ordenados = sorted(periodos, key=periodo_de)
    if not ordenados:
        return []
    finais = [ordenados[-1]]
    for item in reversed(ordenados[:-1]):
        atual = periodo_de(finais[0])
        ano, mes = int(atual[:4]), int(atual[4:6])
        anterior = f"{ano - 1:04d}12" if mes == 1 else f"{ano:04d}{mes - 1:02d}"
        if periodo_de(item) != anterior:
            break
        finais.insert(0, item)
    return finais


def _cost_of(financial: list[dict[str, Any]], periodo: str) -> float | None:
    for linha in financial:
        if linha.get("billing_period") == periodo:
            return float(linha.get("effective_cost") or 0)
    return None


async def _connections_in_scope(where: str) -> list[str]:
    sql = (
        f"SELECT DISTINCT connection_code FROM iceberg.{layers.GOLD}.{C1_TABLE} "
        f"WHERE {where}"
    )
    linhas, _ = await _run(sql, timeout_s=_SUMMARY_TIMEOUT)
    return [str(r["connection_code"]) for r in linhas if r.get("connection_code")]


async def _quality(*, where: str) -> dict[str, Any]:
    """Qualidade do escopo, com valor financeiro afetado.

    O score não pode mascarar materialidade: junto com o percentual vai o custo.
    """
    sql = f"""
SELECT ROUND(SUM(effective_cost), 2) AS total_cost,
       ROUND(SUM(cost_missing_dimension), 2) AS cost_missing_dimension,
       ROUND(SUM(cost_without_tag), 2) AS cost_without_tag,
       SUM(rows_without_tag) AS rows_without_tag,
       COUNT(*) FILTER (WHERE org_classification_status = 'UNMAPPED') AS rows_unmapped_org,
       COUNT(*) AS cube_rows
FROM iceberg.{layers.GOLD}.{C1_TABLE}
WHERE {where}
"""
    linhas, _ = await _run(sql, timeout_s=_SUMMARY_TIMEOUT)
    linha = linhas[0] if linhas else {}
    total = float(linha.get("total_cost") or 0)
    sem_dim = float(linha.get("cost_missing_dimension") or 0)
    sem_tag = float(linha.get("cost_without_tag") or 0)
    avisos: list[str] = []
    if total and sem_dim / total > 0.05:
        avisos.append(f"custo sem dimensão crítica acima de 5 %: R$ {sem_dim:,.2f}")
    if linha.get("rows_unmapped_org"):
        avisos.append(
            f"{int(linha['rows_unmapped_org'])} linhas do cubo sem classificação organizacional"
        )
    return {
        "coverage_pct": (
            round(100.0 * (1 - sem_dim / total), 4) if total else None
        ),
        "total_cost": total,
        "cost_missing_dimension": sem_dim,
        "cost_without_tag": sem_tag,
        "rows_without_tag": int(linha.get("rows_without_tag") or 0),
        "rows_unmapped_org": int(linha.get("rows_unmapped_org") or 0),
        "cube_rows": int(linha.get("cube_rows") or 0),
        "warnings": avisos,
        # Medidas não aditivas ficam fora de qualquer soma do cliente.
        "non_additive": sorted(NON_ADDITIVE_MEASURES),
    }


# ── Série mensal ─────────────────────────────────────────────────────────────


@router.get("/cost/trend")
async def cost_trend(
    period: str | None = None,
    provider: str | None = None,
    connection: str | None = None,
    account: str | None = None,
    service: str | None = None,
    region: str | None = None,
    currency: str | None = None,
    group_by: str = Query("period", description="period | service | region | sub_account"),
    limit: int = Query(25, ge=1, le=200),
    user: User = _READ,
) -> dict[str, Any]:
    """Série mensal e decomposição, com o status de cada competência.

    A série devolve **todas** as competências do escopo, cada uma com o status
    medido. Período parcial é exibido e rotulado — nunca usado como base de
    tendência em silêncio.
    """
    if group_by not in {"period", "service", "region", "sub_account"}:
        raise HTTPException(
            status_code=400,
            detail="group_by aceita: period, service, region, sub_account",
        )
    where = _where(
        filters=C1_FILTERS,
        values={
            "period": period,
            "provider": provider,
            "connection": connection,
            "account": account,
            "service": service,
            "region": region,
            "currency": currency,
        },
    )
    if group_by == "period":
        sql = f"""
SELECT billing_period AS grp, currency,
       ROUND(SUM(effective_cost), 2) AS effective_cost,
       ROUND(SUM(list_cost), 2) AS list_cost,
       SUM(rows) AS source_rows
FROM iceberg.{layers.GOLD}.{C1_TABLE}
WHERE {where} AND billing_period IS NOT NULL
GROUP BY 1, 2 ORDER BY 1
"""
    else:
        coluna = {
            "service": "service_key",
            "region": "region_key",
            "sub_account": "sub_account_id",
        }[group_by]
        sql = f"""
SELECT CAST({coluna} AS VARCHAR) AS grp, currency,
       ROUND(SUM(effective_cost), 2) AS effective_cost,
       ROUND(SUM(list_cost), 2) AS list_cost,
       SUM(rows) AS source_rows
FROM iceberg.{layers.GOLD}.{C1_TABLE}
WHERE {where} AND billing_period IS NOT NULL
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {int(_resolved(limit) or 25)}
"""
    linhas, ms, do_cache = await _run_cached(
        endpoint="cost/trend",
        cube=C1_TABLE,
        filters={"period": period, "provider": provider, "connection": connection,
                 "account": account, "service": service, "region": region,
                 "currency": currency, "group_by": group_by, "limit": limit},
        sql=sql,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    moeda = _separate_currency(linhas)
    statuses = await _statuses_for(linhas if group_by == "period" else [])
    return _envelope(
        scope={"group_by": group_by, "filters": _scope_filters(locals())},
        currency=moeda,
        quality={"coverage_pct": None, "warnings": []},
        cache={"hit": do_cache, "version_source": "gold_manifest"},
        data={
            "cube": C1_TABLE,
            "group_by": group_by,
            "series": [
                {
                    "key": r.get("grp"),
                    "effective_cost": float(r.get("effective_cost") or 0),
                    "list_cost": float(r.get("list_cost") or 0),
                    "source_rows": int(r.get("source_rows") or 0),
                    "data_status": statuses.get(str(r.get("grp"))) if group_by == "period" else None,
                }
                for r in linhas
            ],
        },
        elapsed_ms=ms,
    )


def _scope_filters(valores: dict[str, Any]) -> dict[str, Any]:
    return {
        chave: valores.get(chave)
        for chave in ("period", "provider", "connection", "account", "service", "region", "currency")
        if valores.get(chave)
    }


async def _statuses_for(linhas: list[dict[str, Any]]) -> dict[str, str]:
    """Status de cobertura por competência, lido da tabela publicada.

    A API **não** reclassifica em memória: isso custaria segundos por request e
    criaria uma segunda definição de comparabilidade. Período ausente da tabela
    publicada é rotulado `NOT_COMPARABLE`.
    """
    from app.services.period_coverage import (
        DEFAULT_UNKNOWN_STATUS,
        load_coverage_map,
    )

    if not linhas:
        return {}
    statuses = await load_coverage_map()
    saida: dict[str, str] = {}
    for linha in linhas:
        periodo = str(linha.get("grp"))
        estados = {s for (_, p), s in statuses.items() if p == periodo}
        # Uma competência é comparável só se **todas** as conexões que a produzem
        # forem comparáveis.
        if not estados:
            saida[periodo] = DEFAULT_UNKNOWN_STATUS
        elif estados == {"COMPLETE"}:
            saida[periodo] = "COMPLETE"
        else:
            saida[periodo] = sorted(estados)[0]
    return saida


# ── Drivers ──────────────────────────────────────────────────────────────────


@router.get("/cost/drivers")
async def cost_drivers(
    period_current: str = Query(..., description="Competência atual YYYYMM"),
    period_previous: str = Query(..., description="Competência anterior YYYYMM"),
    driver_type: str = Query("service", description=" | ".join(DRIVER_TYPES)),
    limit: int = Query(25, ge=1, le=200),
    user: User = _READ,
) -> dict[str, Any]:
    """O que explica a mudança entre duas competências comparáveis.

    Um par não comparável **não devolve número**: devolve o motivo. Comparar um mês
    de ajuste com um mês operacional produz uma variação sem significado.
    """
    if driver_type not in DRIVER_TYPES:
        raise HTTPException(
            status_code=400, detail=f"driver_type aceita: {', '.join(DRIVER_TYPES)}"
        )
    sql = f"""
SELECT driver_type, driver_key, cost_current, cost_previous, delta, delta_pct,
       contribution_pct, flag, total_delta
FROM iceberg.{layers.GOLD}.{C3_TABLE}
WHERE period_current = {_quote_literal(period_current)}
  AND period_previous = {_quote_literal(period_previous)}
  AND driver_type = {_quote_literal(driver_type)}
ORDER BY ABS(delta) DESC
LIMIT {int(_resolved(limit) or 25)}
"""
    linhas, ms, do_cache = await _run_cached(
        endpoint="cost/drivers",
        cube=C3_TABLE,
        filters={"period_current": period_current, "period_previous": period_previous,
                 "driver_type": driver_type, "limit": limit},
        sql=sql,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    if not linhas:
        return _envelope(
            scope={
                "period_current": period_current,
                "period_previous": period_previous,
                "driver_type": driver_type,
            },
            data_status="EMPTY",
            quality={
                "coverage_pct": None,
                "warnings": [
                    "par sem driver materializado: um dos períodos pode não ser comparável"
                ],
            },
            cache={"hit": do_cache, "version_source": "gold_manifest"},
            data={"cube": C3_TABLE, "drivers": [], "reason": "sem_par_comparavel"},
            elapsed_ms=ms,
        )
    total_declarado = float(linhas[0].get("total_delta") or 0)
    return _envelope(
        scope={
            "period_current": period_current,
            "period_previous": period_previous,
            "driver_type": driver_type,
        },
        quality={"coverage_pct": None, "warnings": []},
        cache={"hit": do_cache, "version_source": "gold_manifest"},
        data={
            "cube": C3_TABLE,
            "driver_type": driver_type,
            "total_delta": total_declarado,
            "drivers": [
                {
                    "key": r.get("driver_key"),
                    "flag": r.get("flag"),
                    "cost_current": float(r.get("cost_current") or 0),
                    "cost_previous": float(r.get("cost_previous") or 0),
                    "delta": float(r.get("delta") or 0),
                    "delta_pct": (
                        None if r.get("delta_pct") is None else float(r["delta_pct"])
                    ),
                    "contribution_pct": (
                        None
                        if r.get("contribution_pct") is None
                        else float(r["contribution_pct"])
                    ),
                }
                for r in linhas
            ],
        },
        elapsed_ms=ms,
    )


# ── Recursos (detalhe paginado) ──────────────────────────────────────────────


@router.get("/cost/resources")
async def cost_resources(
    period: str | None = None,
    provider: str | None = None,
    connection: str | None = None,
    account: str | None = None,
    service: str | None = None,
    region: str | None = None,
    resource_type: str | None = None,
    currency: str | None = None,
    order_by: str = Query("effective_cost"),
    order: str = Query("desc", description="asc | desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = _READ,
) -> dict[str, Any]:
    """Detalhe por recurso, **paginado**, a partir de C2.

    Nunca devolve o cubo inteiro: C2 tem 1.023.977 linhas de cubo. `page` diz
    quantas existem no escopo, para que a UI saiba que há mais.
    """
    order_by = _resolved(order_by) or "effective_cost"
    if order_by not in _SORTABLE:
        raise HTTPException(
            status_code=400, detail=f"order_by aceita: {', '.join(sorted(_SORTABLE))}"
        )
    direcao = "DESC" if str(_resolved(order) or "desc").lower() != "asc" else "ASC"
    where = _where(
        filters=C2_FILTERS,
        values={
            "period": period,
            "provider": provider,
            "connection": connection,
            "account": account,
            "service": service,
            "region": region,
            "resource_type": resource_type,
            "currency": currency,
        },
    )
    total_sql = (
        f"SELECT COUNT(*) AS n FROM iceberg.{layers.GOLD}.{C2_TABLE} WHERE {where}"
    )
    total_linhas, _ = await _run(total_sql, timeout_s=_SUMMARY_TIMEOUT)
    total = int((total_linhas[0] if total_linhas else {}).get("n") or 0)

    sql = f"""
SELECT billing_period, provider, connection_code, sub_account_id,
       canonical_resource_id, resource_name, resource_type_key, resource_sku,
       resource_group_id, resource_state, operating_system, region_key, service_key,
       charge_category, currency, inventory_status, inventory_snapshots,
       inventory_snapshot_period, resource_id_status,
       effective_cost, list_cost, rows, rows_without_tag
FROM iceberg.{layers.GOLD}.{C2_TABLE}
WHERE {where}
ORDER BY {_resolved(order_by) or "effective_cost"} {direcao} NULLS LAST
OFFSET {int(_resolved(offset) or 0)} ROWS
FETCH NEXT {int(_resolved(limit) or 50)} ROWS ONLY
"""
    linhas, ms, do_cache = await _run_cached(
        endpoint="cost/resources",
        cube=C2_TABLE,
        filters={"period": period, "provider": provider, "connection": connection,
                 "account": account, "service": service, "region": region,
                 "resource_type": resource_type, "currency": currency,
                 "order_by": order_by, "order": direcao,
                 "limit": limit, "offset": offset},
        sql=sql,
        timeout_s=_DETAIL_TIMEOUT,
    )
    moeda = _separate_currency(linhas)
    return _envelope(
        scope=_scope_filters(locals()),
        currency=moeda,
        quality={
            "coverage_pct": None,
            "warnings": [
                "resources e rows_without_tag não são aditivos entre linhas",
            ],
        },
        cache={"hit": do_cache, "version_source": "gold_manifest"},
        data={
            "cube": C2_TABLE,
            "resources": [
                {
                    **{
                        k: (float(v) if k in {"effective_cost", "list_cost"} and v is not None else v)
                        for k, v in r.items()
                    },
                }
                for r in linhas
            ],
            "non_additive": sorted(
                {"rows_without_tag", "inventory_snapshots"}
            ),
        },
        page={
            "total": total,
            "limit": int(_resolved(limit) or 50),
            "offset": int(_resolved(offset) or 0),
        },
        elapsed_ms=ms,
    )


@router.get("/cost/meta")
async def cost_meta(user: User = _READ) -> dict[str, Any]:
    """Contrato do cubo para a UI: filtros aceitos, medidas e indisponibilidades.

    Existe para que a tela não precise conhecer nomes de coluna nem inventar
    rótulo: ela pergunta o que pode filtrar e o que não é mensurável.
    """
    return _envelope(
        scope={},
        data={
            "cubes": {
                "monthly": C1_TABLE,
                "resource": C2_TABLE,
                "driver": C3_TABLE,
            },
            "filters": {
                "monthly": sorted(C1_FILTERS),
                "resource": sorted(C2_FILTERS),
            },
            "driver_types": list(DRIVER_TYPES),
            "driver_key_columns": {d: driver_key_column(d) for d in DRIVER_TYPES},
            "non_additive_measures": sorted(NON_ADDITIVE_MEASURES),
            "unavailable_measures": unavailable_measures(),
        },
    )


@router.get("/metrics")
async def finops_metrics(
    user: User = Depends(require("finops", "read")),
) -> dict[str, Any]:
    """Latência medida e estatística de cache dos endpoints do Centro.

    A auditoria pediu p50 e p95 **medidos**, não declarados. Este endpoint devolve o
    que foi observado no processo: amostras, p50, p95, erros, acertos e invalidações
    de cache por endpoint.

    As amostras são de janela curta e por processo — não substituem observabilidade
    histórica; servem para sustentar o aceite de performance no ambiente de
    referência.
    """
    from app.services.finops_cache import metrics_report

    return _envelope(
        scope={"scope": "processo"},
        data={"metrics": metrics_report()},
    )


# ── Visão simplificada (modelo CloudZero/Turbonomic) ─────────────────────────
#
# O Centro completo tem 7 abas e 23 KPIs — profundidade que atende FinOps, mas
# que a direção não abre. Esta seção expõe os mesmos dados pré-digeridos em
# três perguntas, na ordem que um gestor faz:
#
#   1. Quanto custou e como mudou?      -> /finops/overview
#   2. O que devo fazer e quanto vale?  -> /finops/actions
#   3. Onde está o custo?               -> /finops/navigate?dim=service
#
# Cada resposta é pré-mastigada: número, comparação, escopo e ação — sem que o
# frontend precise montar KPI ou somar nada.

_ACOES_SQL = """
SELECT c.resource_name AS recurso,
       CAST(c.resource_id AS VARCHAR) AS resource_id,
       c.sku_from, c.sku_to,
       TRY_CAST(c.vcpus_from AS BIGINT) AS vcpus_de,
       TRY_CAST(c.vcpus_to AS BIGINT) AS vcpus_para,
       TRY_CAST(c.memory_gb_from AS BIGINT) AS memoria_de,
       TRY_CAST(c.memory_gb_to AS BIGINT) AS memoria_para,
       ROUND(TRY_CAST(c.cost_from_current AS DOUBLE), 2) AS custo_atual,
       ROUND(TRY_CAST(c.cost_to_estimated AS DOUBLE), 2) AS custo_projetado,
       ROUND(TRY_CAST(c.saving_monthly AS DOUBLE), 2) AS economia_mensal,
       TRY_CAST(c.saving_pct AS DOUBLE) AS economia_pct,
       c.cpu_p95, c.observed_days, c.verdict, c.veto_reason,
       COALESCE(c.resource_type, 'UNKNOWN') AS tipo,
       c.requested_days, c.profile_built_at
FROM iceberg.gold.finops_rightsizing_actions c
WHERE c.verdict IN ('VALID', 'REQUIRES_VALIDATION')
"""


def _vm_vendor(sku: str) -> str | None:
    """Classifica apenas nomes Azure D/E conhecidos pelo sufixo de processador."""
    body = str(sku or "").removeprefix("Standard_").removeprefix("standard_")
    match = re.match(r"^[DEde](?:C)?\d+(?:-\d+)?([a-z]*)_v\d+", body, re.IGNORECASE)
    if not match:
        return None
    variant = match.group(1).lower()
    if "a" in variant:
        return "AMD"
    # Sufixos p/ps indicam ARM; não os rotulamos como Intel.
    if variant in {"s", "ds", "ls", "d", "lds"}:
        return "Intel"
    return None


def _vm_vendor_for_detail(sku: str) -> str | None:
    """Classificação local do detalhe, incluindo F clássico/Fsv2/Fas."""
    vendor = _vm_vendor(sku)
    if vendor:
        return vendor
    body = str(sku or "").removeprefix("Standard_").removeprefix("standard_")
    if re.match(r"^F\d+", body, re.IGNORECASE):
        return "AMD" if re.match(r"^F\d+a", body, re.IGNORECASE) else "Intel"
    return None


def _sku_key(sku: str) -> str:
    text = str(sku or "").lower().replace("standard_", "").replace("standard ", "")
    return "".join(ch for ch in text if ch.isalnum())


def _price_unit_hours(unit: str | None) -> float | None:
    """Convert a price-sheet unit such as `100 Hours` to hours per unit."""
    if not unit or "hour" not in unit.lower():
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", unit)
    if not match:
        return None
    hours = float(match.group(1))
    return hours if hours > 0 else None


async def _action_detail_data(
    resource_id: str,
    window: MetricDayWindow | None = None,
) -> dict[str, Any]:
    window = window or resolve_metric_day_window()
    safe_id = _quote_literal(resource_id)
    rows = await trino_execute(
        f"""
        SELECT resource_id, resource_name, region_id, sku_from, sku_to,
               vcpus_from, vcpus_to, memory_gb_from, memory_gb_to,
               cost_from_current, cost_to_estimated, saving_monthly, saving_pct,
               cpu_p95, mem_p95, observed_days, verdict, veto_reason,
               price_source_from, price_source_to, price_comparable, confidence, created_at
        FROM iceberg.silver.rightsizing_candidate
        WHERE resource_id = {safe_id}
        ORDER BY created_at DESC NULLS LAST, run_id DESC
        LIMIT 1
        """,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Ação não encontrada para este recurso.")
    candidate = rows[0]
    candidate["status_revisao"] = "PENDING"
    try:
        reviews = await trino_execute(
            f"""
            SELECT review_status FROM iceberg.silver.rightsizing_decision
            WHERE resource_id = {safe_id}
            ORDER BY created_at DESC LIMIT 1
            """,
            timeout_s=_SUMMARY_TIMEOUT,
        )
        if reviews:
            candidate["status_revisao"] = reviews[0].get("review_status") or "PENDING"
    except Exception:  # noqa: BLE001 - primeira materialização de decisões
        pass
    region = str(candidate.get("region_id") or "")
    sku_from = str(candidate.get("sku_from") or "")
    current_vendor = _vm_vendor_for_detail(sku_from)
    target_vendor = _vm_vendor_for_detail(str(candidate.get("sku_to") or ""))
    candidate["vendor_from"] = current_vendor
    candidate["vendor_to"] = target_vendor
    candidate["family_change"] = bool(current_vendor and target_vendor and current_vendor != target_vendor)
    # Quando não há tarifa de destino, deriva reduções com a mesma proporção
    # de vCPU e RAM dentro da mesma série Azure, usando o custo observado.
    if candidate.get("cost_to_estimated") is None:
        sku_to = str(candidate.get("sku_to") or "")
        cpu_from, cpu_to = float(candidate.get("vcpus_from") or 0), float(candidate.get("vcpus_to") or 0)
        mem_from, mem_to = float(candidate.get("memory_gb_from") or 0), float(candidate.get("memory_gb_to") or 0)
        cpu_ratio = cpu_to / cpu_from if cpu_from > 0 and cpu_to > 0 else None
        mem_ratio = mem_to / mem_from if mem_from > 0 and mem_to > 0 else None
        from_family = re.match(r"^Standard_([A-Za-z])", sku_from, re.IGNORECASE)
        to_family = re.match(r"^Standard_([A-Za-z])", sku_to, re.IGNORECASE)
        same_sku_family = bool(from_family and to_family and from_family.group(1).lower() == to_family.group(1).lower())
        if same_sku_family and cpu_ratio is not None and mem_ratio is not None and math.isclose(cpu_ratio, mem_ratio, rel_tol=0.03):
            observed_cost = float(candidate.get("cost_from_current") or 0)
            candidate["cost_to_estimated"] = round(observed_cost * cpu_ratio, 2)
            candidate["saving_monthly"] = round(observed_cost - float(candidate["cost_to_estimated"]), 2)
            candidate["cost_projection_method"] = "OBSERVED_COST_PROPORTIONAL_CAPACITY"

    metric_filter = _metric_partition_filter("m", window, owner=_METRICS_4P_VM_OWNER)
    profiles = await trino_execute(
        f"""
        SELECT m.metric, max(m.metric_unit) AS metric_unit,
               min(try_cast(m.value AS double)) AS value_min,
               approx_percentile(try_cast(m.value AS double), 0.50) AS p50,
               approx_percentile(try_cast(m.value AS double), 0.95) AS p95,
               approx_percentile(try_cast(m.value AS double), 0.99) AS p99,
               max(try_cast(m.value AS double)) AS value_max,
               avg(try_cast(m.value AS double)) AS value_avg,
               count(*) AS samples,
               count(DISTINCT m._billing_period) AS observed_days,
               CAST(count(*) AS double) / NULLIF({window.requested_days} * 96, 0) AS coverage,
               CASE WHEN count(*) >= {window.requested_days} * 96 * 0.9 THEN 'ALTA'
                    WHEN count(*) >= {window.requested_days} * 96 * 0.6 THEN 'MEDIA'
                    ELSE 'BAIXA' END AS confidence
        FROM iceberg.silver.resource_metric_series m
        WHERE {metric_filter}
          AND m.resource_id = {safe_id}
          AND m.quality_status = 'OK' AND m.value IS NOT NULL
          AND (m.metric = 'Percentage CPU' OR m.metric LIKE '%Memory%Percentage%' OR m.metric = 'Available Memory Bytes')
        GROUP BY m.metric
        ORDER BY m.metric
        """,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    series = await trino_execute(
        f"""
        SELECT date_trunc('hour', m.bucket_start_utc) AS bucket_start_utc,
               m.metric, avg(try_cast(m.value AS double)) AS value,
               min(try_cast(m.value AS double)) AS value_min,
               max(try_cast(m.value AS double)) AS value_max,
               max(m.metric_unit) AS metric_unit, count(*) AS observed_samples
        FROM iceberg.silver.resource_metric_series m
        WHERE {metric_filter}
          AND m.resource_id = {safe_id}
          AND m.quality_status = 'OK' AND m.value IS NOT NULL
          AND (m.metric = 'Percentage CPU' OR m.metric LIKE '%Memory%Percentage%')
        GROUP BY 1, 2
        ORDER BY bucket_start_utc, metric
        """,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    candidate_built_at = _as_utc_datetime(candidate.get("created_at"))
    latest_metric_at = max((parsed for row in series if (parsed := _as_utc_datetime(row.get("bucket_start_utc")))), default=None)
    recommendation_profile_status = (
        "STALE" if candidate_built_at and latest_metric_at and latest_metric_at > candidate_built_at
        else "CURRENT" if candidate_built_at and latest_metric_at
        else "UNKNOWN"
    )

    aks_topology: dict[str, Any] | None = None
    is_aks_vmss = "/virtualmachinescalesets/" in resource_id.lower() or str(candidate.get("resource_name") or "").lower().endswith("-vmss")
    if is_aks_vmss:
        # The VMSS is the AKS node pool's resource_id. Its Azure tag carries the
        # pool key; resource_allocation links that pool to the managed-cluster
        # resource_id (no source connection id participates in this join).
        vmss = await trino_execute(
            f"""
            SELECT tags_json, resource_group_id, region_id, resource_sku, valid_from
            FROM iceberg.silver.resource_inventory
            WHERE resource_id = {safe_id}
            ORDER BY valid_from DESC
            LIMIT 1
            """,
            timeout_s=_SUMMARY_TIMEOUT,
        )
        tags = str(vmss[0].get("tags_json") or "") if vmss else ""
        pool_match = re.search(r"aks-managed-poolName['\"]?\s*:\s*['\"]([^'\"]+)", tags, re.IGNORECASE)
        pool_name = pool_match.group(1).strip() if pool_match else ""
        orchestrator_match = re.search(r"aks-managed-orchestrator['\"]?\s*:\s*['\"]([^'\"]+)", tags, re.IGNORECASE)
        orchestrator = orchestrator_match.group(1).strip() if orchestrator_match else None
        version_match = re.search(r"(?:Kubernetes[: ]+)([0-9]+(?:\.[0-9]+){1,2})", orchestrator or "", re.IGNORECASE)
        os_match = re.search(r"compute\.aks\.billing['\"]?\s*:\s*['\"]([^'\"]+)", tags, re.IGNORECASE)
        subscription_match = re.match(r"/subscriptions/([^/]+)/", resource_id, re.IGNORECASE)
        subscription_id = subscription_match.group(1) if subscription_match else ""
        if pool_name and subscription_id and region and sku_from:
            topology_rows = await trino_execute(
                f"""
                WITH clusters AS (
                    SELECT resource_id, max(resource_name) AS cluster_name,
                           lower(max(region_id)) AS region_id, max(sub_account_id) AS sub_account_id
                    FROM iceberg.silver.resource_inventory
                    WHERE lower(cloud_provider) = 'azure'
                      AND lower(resource_type) LIKE '%managedclusters%'
                    GROUP BY resource_id
                ), allocation_latest AS (
                    SELECT parent_resource_id, allocation_key, allocation_weight, node_count,
                           node_sku, pool_mode, allocation_status, status_reason, snapshot_date,
                           row_number() OVER (
                               PARTITION BY parent_resource_id, allocation_key
                               ORDER BY snapshot_date DESC, _silver_ingested_at DESC
                           ) AS rn
                    FROM iceberg.silver.resource_allocation
                    WHERE lower(cloud_provider) = 'azure' AND allocation_level = 'node_pool'
                ), matched_clusters AS (
                    SELECT DISTINCT a.parent_resource_id
                    FROM allocation_latest a
                    JOIN clusters c ON c.resource_id = a.parent_resource_id
                    WHERE a.rn = 1
                      AND lower(c.sub_account_id) = lower({_quote_literal(subscription_id)})
                      AND c.region_id = lower({_quote_literal(region)})
                      AND lower(a.allocation_key) = lower({_quote_literal(pool_name)})
                      AND lower(a.node_sku) = lower({_quote_literal(sku_from)})
                )
                SELECT c.resource_id AS cluster_resource_id, c.cluster_name, c.region_id,
                       a.allocation_key AS pool_name, a.allocation_weight,
                       a.node_count AS workers, a.node_sku AS sku, a.pool_mode,
                       a.allocation_status, a.status_reason, a.snapshot_date
                FROM matched_clusters m
                JOIN clusters c ON c.resource_id = m.parent_resource_id
                JOIN allocation_latest a ON a.parent_resource_id = m.parent_resource_id AND a.rn = 1
                ORDER BY a.allocation_key
                """,
                timeout_s=_SUMMARY_TIMEOUT,
            )
            target_rows = [r for r in topology_rows if str(r.get("pool_name") or "").lower() == pool_name.lower()]
            matched_ids = {str(r.get("cluster_resource_id") or "") for r in target_rows}
            if len(matched_ids) == 1 and target_rows:
                cluster_id = next(iter(matched_ids))
                target_pool = next((r for r in target_rows if str(r.get("sku") or "").lower() == sku_from.lower()), target_rows[0])
                container_filter = _metric_partition_filter("m", window, owner=_METRICS_4P_CONTAINER_OWNER)
                metric_rows = await trino_execute(
                    f"""
                    WITH ranked AS (
                        SELECT m.record_kind, m.metric_name, m.metric_average, m.samples,
                               m.nodes, m.pods, m.containers, m.kubernetes_version,
                               m.coverage_status, m.coverage_reason, m.namespace, m.computer,
                               m.snapshot_date,
                               row_number() OVER (
                                   PARTITION BY m.cluster_resource_id, m.record_kind,
                                                COALESCE(m.metric_name, ''), COALESCE(m.namespace, ''),
                                                COALESCE(m.computer, '')
                                   ORDER BY m.snapshot_date DESC, m._silver_ingested_at DESC
                               ) AS rn
                        FROM iceberg.silver.container_metric_series m
                        WHERE {container_filter}
                          AND m.cluster_resource_id = {_quote_literal(cluster_id)}
                    )
                    SELECT record_kind, metric_name, metric_average, samples, nodes, pods, containers,
                           kubernetes_version, coverage_status, coverage_reason, namespace, computer,
                           snapshot_date
                    FROM ranked WHERE rn = 1
                    ORDER BY record_kind, metric_name, namespace, computer
                    """,
                    timeout_s=_SUMMARY_TIMEOUT,
                )
                namespace_allocations = await trino_execute(
                    f"""
                    WITH ranked AS (
                        SELECT allocation_key, namespace, allocation_weight, weight_basis,
                               allocation_status, status_reason, snapshot_date,
                               row_number() OVER (
                                   PARTITION BY parent_resource_id, allocation_key
                                   ORDER BY snapshot_date DESC NULLS LAST, _silver_ingested_at DESC
                               ) AS rn
                        FROM iceberg.silver.resource_allocation
                        WHERE lower(cloud_provider) = 'azure'
                          AND lower(allocation_level) = 'namespace'
                          AND lower(parent_resource_id) = lower({_quote_literal(cluster_id)})
                    )
                    SELECT allocation_key, COALESCE(namespace, allocation_key) AS namespace,
                           allocation_weight, weight_basis, allocation_status,
                           status_reason, snapshot_date
                    FROM ranked WHERE rn = 1
                    ORDER BY allocation_weight DESC NULLS LAST, namespace
                    """,
                    timeout_s=_SUMMARY_TIMEOUT,
                )
                pools = [{
                    "name": str(r.get("pool_name") or ""), "workers": int(r.get("workers") or 0),
                    "sku": r.get("sku"), "mode": r.get("pool_mode"),
                    "allocation_weight": float(r.get("allocation_weight") or 0),
                    "allocation_status": r.get("allocation_status"), "snapshot_date": r.get("snapshot_date"),
                } for r in topology_rows]
                coverage = next((r for r in metric_rows if r.get("coverage_status")), None)
                aks_topology = {
                    "status": "AVAILABLE", "cluster_resource_id": cluster_id,
                    "cluster_name": target_rows[0].get("cluster_name"), "region": target_rows[0].get("region_id"),
                    "pool_name": pool_name, "workers": int(target_pool.get("workers") or 0),
                    "pool_mode": target_pool.get("pool_mode"), "pools": pools,
                    "total_workers": sum(p["workers"] for p in pools),
                    "snapshot_date": target_pool.get("snapshot_date"),
                    "kubernetes_version": next((r.get("kubernetes_version") for r in metric_rows if r.get("kubernetes_version")), None) or (version_match.group(1) if version_match else None),
                    "orchestrator": orchestrator, "os_type": os_match.group(1).upper() if os_match else None,
                    "vmss_resource_group": vmss[0].get("resource_group_id"),
                    "vmss_inventory_snapshot": vmss[0].get("valid_from"),
                    "container_insights_status": coverage.get("coverage_status") if coverage else "UNAVAILABLE",
                    "container_insights_reason": coverage.get("coverage_reason") if coverage else "Sem registro de cobertura de Container Insights no Silver.",
                    "container_metrics": [r for r in metric_rows if r.get("record_kind") == "performance"],
                    "container_inventory": [r for r in metric_rows if r.get("record_kind") in ("pod_inventory", "node_inventory")],
                    "container_coverage": [r for r in metric_rows if r.get("record_kind") == "coverage"],
                    "namespace_allocations": namespace_allocations,
                    "cost_per_worker": round(float(candidate.get("cost_from_current") or 0) / int(target_pool["workers"]), 2) if int(target_pool.get("workers") or 0) > 0 else None,
                    "saving_per_worker": round(float(candidate.get("saving_monthly") or 0) / int(target_pool["workers"]), 2) if int(target_pool.get("workers") or 0) > 0 else None,
                }
            elif target_rows:
                aks_topology = {"status": "AMBIGUOUS", "pool_name": pool_name,
                    "reason": "Mais de um cluster na assinatura/região possui este node pool e SKU; a ligação foi omitida para evitar atribuição incorreta."}
            else:
                aks_topology = {"status": "UNAVAILABLE", "pool_name": pool_name,
                    "reason": "Não há alocação de node pool correspondente a este resource_id, SKU e região no Silver."}
        elif is_aks_vmss:
            aks_topology = {"status": "UNAVAILABLE", "reason": "O resource_id não tem tag de node pool ou dados de assinatura/região suficientes para ligar ao cluster no Silver."}

    # Alternativas Intel/AMD: só D/E x86 com SKU disponível, CPU/memória
    # suficientes para a demanda p95 + 25% de folga e preço comparável.
    family = re.match(r"^Standard_([DEF])", sku_from, re.IGNORECASE)
    detail_current_vendor = current_vendor
    family_options: list[dict[str, Any]] = []
    family_note = "As alternativas Intel/AMD usam catálogo regional D/E/F; quando não houver tarifa comparável, só é exibida uma projeção proporcional dentro da mesma série."
    if target_vendor and target_vendor != current_vendor:
        family_note = f"A ação determinística já sugere mudança de {current_vendor} para {target_vendor}. Confirme compatibilidade da aplicação e benchmark antes de migrar."
    if family and detail_current_vendor:
        catalog = await trino_execute(
            f"""
            SELECT sku_name, max(try_cast(vcpus AS DOUBLE)) AS vcpus,
                   max(try_cast(memory_gb AS DOUBLE)) AS memory_gb,
                   max(is_available) AS is_available,
                   max(restriction_reason) AS restriction_reason
            FROM iceberg.silver.compute_sku_catalog
            WHERE lower(region_id) = lower({ _quote_literal(region) })
              AND upper(sku_name) LIKE 'STANDARD_{family.group(1).upper()}%'
              AND upper(is_available) = 'SIM'
            GROUP BY sku_name
            ORDER BY sku_name
            LIMIT 500
            """,
            timeout_s=_SUMMARY_TIMEOUT,
        )
        vcpus_now = float(candidate.get("vcpus_from") or 0)
        cpu_p95 = float(candidate.get("cpu_p95") or 0)
        memory_now = float(candidate.get("memory_gb_from") or 0)
        memory_profile = next((p for p in profiles if p.get("metric") == "Available Memory Bytes"), None)
        memory_pct_profile = next((p for p in profiles if p.get("metric") == "Available Memory Percentage"), None)
        # Rightsizing uses observed minimum free memory (worst observed headroom),
        # never treats a byte count in candidate.mem_p95 as a percentage.
        memory_available_min_gb = (
            float(memory_profile.get("value_min")) / 1024**3
            if memory_profile and memory_profile.get("value_min") is not None
            else memory_now * float(memory_pct_profile.get("value_min")) / 100
            if memory_pct_profile and memory_pct_profile.get("value_min") is not None else None
        )
        cpu_needed = math.ceil(vcpus_now * cpu_p95 / 100 * 1.25)
        memory_needed = max(0.0, memory_now - memory_available_min_gb) * 1.25 if memory_available_min_gb is not None else None
        peers = []
        for row in catalog:
            vendor = _vm_vendor_for_detail(str(row.get("sku_name") or ""))
            vcpus = float(row.get("vcpus") or 0)
            memory = float(row.get("memory_gb") or 0)
            allowed_vendor = vendor in {"Intel", "AMD"} if detail_current_vendor == "Intel" else vendor == "AMD"
            if allowed_vendor and _sku_key(str(row.get("sku_name") or "")) != _sku_key(sku_from) and cpu_needed <= vcpus <= vcpus_now and (
                memory_needed is None or memory >= memory_needed
            ):
                peers.append({**row, "vendor": vendor})

        if peers:
            from app.services.rightsizing_service import REGIAO_EXIBICAO_PARA_ARM

            region_key = "".join(ch for ch in region.lower() if ch.isalnum())
            region_aliases = [region_key] + [
                alias for alias, arm in REGIAO_EXIBICAO_PARA_ARM.items()
                if arm == region_key
            ]
            sku_keys = sorted({_sku_key(str(row["sku_name"])) for row in peers})
            values = ",".join(_quote_literal(key) for key in sku_keys)
            regions = ",".join(_quote_literal(alias) for alias in region_aliases)
            prices = await trino_execute(
                f"""
                SELECT regexp_replace(regexp_replace(lower(meter_name), '^standard[ _]?', ''), '[^a-z0-9]', '') AS sku_key,
                       price_source, currency, unit_of_measure, AVG(unit_price) AS unit_price
                FROM iceberg.silver.price_catalog
                WHERE service_name = 'Virtual Machines'
                  AND unit_price > 0 AND lower(price_type) = 'consumption'
                  AND regexp_replace(lower(region_id), '[^a-z0-9]', '') IN ({regions})
                  AND regexp_replace(regexp_replace(lower(meter_name), '^standard[ _]?', ''), '[^a-z0-9]', '') IN ({values})
                GROUP BY 1, 2, 3, 4
                """,
                timeout_s=_SUMMARY_TIMEOUT,
            )
            price_by_sku: dict[str, list[dict[str, Any]]] = {}
            for price in prices:
                price_by_sku.setdefault(str(price.get("sku_key") or ""), []).append(price)

            source_from = str(candidate.get("price_source_from") or "")
            source_prices = await trino_execute(
                f"""
                SELECT regexp_replace(regexp_replace(lower(meter_name), '^standard[ _]?', ''), '[^a-z0-9]', '') AS sku_key,
                       price_source, currency, unit_of_measure, AVG(unit_price) AS unit_price
                FROM iceberg.silver.price_catalog
                WHERE service_name = 'Virtual Machines' AND unit_price > 0
                  AND lower(price_type) = 'consumption'
                  AND price_source = {_quote_literal(source_from)}
                  AND regexp_replace(lower(region_id), '[^a-z0-9]', '') IN ({regions})
                  AND regexp_replace(regexp_replace(lower(meter_name), '^standard[ _]?', ''), '[^a-z0-9]', '') IN ({_quote_literal(_sku_key(sku_from))})
                GROUP BY 1, 2, 3, 4
                """,
                timeout_s=_SUMMARY_TIMEOUT,
            ) if source_from else []
            for vendor in ("Intel", "AMD"):
                if detail_current_vendor == "AMD" and vendor != "AMD":
                    continue
                eligible = []
                for peer in peers:
                    if peer["vendor"] != vendor:
                        continue
                    key = _sku_key(str(peer["sku_name"]))
                    matched_rates = [
                        (current_rate, alt_rate)
                        for current_rate in source_prices
                        for alt_rate in price_by_sku.get(key, [])
                        if current_rate.get("price_source") == source_from
                        and alt_rate.get("price_source") == source_from
                        and alt_rate.get("currency") == current_rate.get("currency")
                        and alt_rate.get("unit_of_measure") == current_rate.get("unit_of_measure")
                    ]
                    current_rate, price = matched_rates[0] if matched_rates else (None, None)
                    vcpus = float(peer.get("vcpus") or 0)
                    memory = float(peer.get("memory_gb") or 0)
                    price_comparable = bool(price and current_rate and float(current_rate.get("unit_price") or 0) > 0)
                    if price_comparable:
                        cost = round(float(candidate.get("cost_from_current") or 0) * float(price["unit_price"]) / float(current_rate["unit_price"]), 2)
                    else:
                        peer_family = re.match(r"^Standard_([A-Za-z])", str(peer["sku_name"]), re.IGNORECASE)
                        same_family = bool(family and peer_family and family.group(1).lower() == peer_family.group(1).lower())
                        cpu_ratio = vcpus / vcpus_now if vcpus_now > 0 else None
                        memory_ratio = memory / memory_now if memory_now > 0 else None
                        if not same_family or cpu_ratio is None or memory_ratio is None or not math.isclose(cpu_ratio, memory_ratio, rel_tol=0.03):
                            continue
                        cost = round(float(candidate.get("cost_from_current") or 0) * cpu_ratio, 2)
                    observed_cost = float(candidate.get("cost_from_current") or 0)
                    if vcpus >= vcpus_now and cost >= observed_cost:
                        continue
                    saving = round(observed_cost - cost, 2)
                    eligible.append({
                        "vendor": vendor, "sku": peer["sku_name"], "vcpus": vcpus,
                        "memory_gb": memory, "cost_projected": cost, "saving": saving,
                        "price_source": source_from if price_comparable else "Custo observado proporcional à capacidade",
                        "price_comparable": price_comparable,
                    })
                eligible.sort(key=lambda option: (
                    option["vcpus"], option["memory_gb"],
                    option["cost_projected"] is None,
                    option["cost_projected"] if option["cost_projected"] is not None else 0,
                    option["sku"],
                ))
                # Offer up to three distinct SKUs per vendor, including
                # alternative families/generations at the same capacity.
                seen_skus: set[str] = set()
                for option in eligible:
                    if option["sku"] in seen_skus:
                        continue
                    family_options.append(option)
                    seen_skus.add(option["sku"])
                    if len(seen_skus) == 3:
                        break
            if family_options:
                family_note = "Tarifas usam a mesma fonte e região do SKU atual. Quando não há tarifa, a estimativa proporcional só é oferecida dentro da mesma série e quando vCPU e RAM mudam na mesma proporção, usando o custo observado. Valide quota, policy, compatibilidade e benchmark antes de migrar."
            elif not (target_vendor and target_vendor != current_vendor):
                family_note = "Não há alternativas com preço ou projeção proporcional disponíveis para a capacidade observada nesta região."

    return {
        "action": candidate,
        "profiles": profiles or [],
        "series": series or [],
        "metric_window": window.as_dict(_observed_partition_days(series or [], "bucket_start_utc")),
        "recommendation_profile_status": recommendation_profile_status,
        "recommendation_built_at": candidate.get("created_at"),
        "metrics_observed_through_utc": max((row.get("bucket_start_utc") for row in series), default=None),
        "family_options": family_options,
        "aks_topology": aks_topology,
        "family_note": family_note,
        "projection_note": (
            "Custo de destino proporcional ao custo observado e à redução equivalente de vCPU e RAM dentro da mesma família/geração; é estimativa, não tarifa observada do SKU de destino. CPU projetada é capacidade estimada, não benchmark nem garantia de desempenho."
            if candidate.get("cost_projection_method") == "OBSERVED_COST_PROPORTIONAL_CAPACITY"
            else "CPU projetada mantém a demanda observada e escala pelo número de vCPU. É uma estimativa de capacidade, não benchmark nem garantia de desempenho."
        ),
    }


@router.get("/actions/detail")
async def finops_action_detail(
    resource_id: str = Query(..., min_length=1, max_length=2048),
    from_day: str | None = Query(None, description="Início inclusivo UTC YYYYMMDD; informe junto com to_day."),
    to_day: str | None = Query(None, description="Fim inclusivo UTC YYYYMMDD; deve ser dia completo."),
    user: User = _READ,
) -> dict[str, Any]:
    window = resolve_metric_day_window(from_day, to_day)
    result = await _action_detail_data(resource_id, window)
    result["metric_window"] = window.as_dict(_observed_partition_days(result.get("series") or [], "bucket_start_utc"))
    return result


@router.get("/actions/namespace-detail")
async def finops_action_namespace_detail(
    resource_id: str = Query(..., min_length=1, max_length=2048),
    namespace: str = Query(..., min_length=1, max_length=255),
    from_day: str | None = Query(None, description="Início inclusivo UTC YYYYMMDD; informe junto com to_day."),
    to_day: str | None = Query(None, description="Fim inclusivo UTC YYYYMMDD; deve ser dia completo."),
    user: User = _READ,
) -> dict[str, Any]:
    """Histórico observado de um namespace do cluster associado ao VMSS."""
    window = resolve_metric_day_window(from_day, to_day)
    detail = await _action_detail_data(resource_id, window)
    topology = detail.get("aks_topology") or {}
    if topology.get("status") != "AVAILABLE":
        raise HTTPException(status_code=409, detail="A topologia AKS deste recurso não está disponível.")
    cluster_id = str(topology.get("cluster_resource_id") or "")
    metric_filter = _metric_partition_filter("m", window, owner=_METRICS_4P_CONTAINER_OWNER)
    rows = await trino_execute(
        f"""
        SELECT CASE WHEN m.record_kind = 'performance' THEN date_trunc('hour', m.snapshot_date)
                    ELSE date_trunc('day', m.snapshot_date) END AS snapshot_date,
               m.record_kind, m.metric_name,
               avg(try_cast(m.metric_average AS DOUBLE)) AS metric_average,
               sum(try_cast(m.samples AS DOUBLE)) AS samples,
               max(m.nodes) AS nodes, max(m.pods) AS pods, max(m.containers) AS containers,
               max(m.kubernetes_version) AS kubernetes_version,
               m.namespace, m.computer,
               max(m.coverage_status) AS coverage_status,
               max(m.coverage_reason) AS coverage_reason
        FROM iceberg.silver.container_metric_series m
        WHERE {metric_filter}
          AND m.cluster_resource_id = {_quote_literal(cluster_id)}
          AND (m.namespace = {_quote_literal(namespace)} OR m.record_kind = 'coverage')
        GROUP BY 1, 2, 3, m.namespace, m.computer
        ORDER BY snapshot_date, record_kind, metric_name, computer
        """,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    allocations = await trino_execute(
        f"""
        SELECT namespace, allocation_weight, weight_basis, allocation_status,
               status_reason, snapshot_date
        FROM iceberg.silver.resource_allocation
        WHERE lower(cloud_provider) = 'azure'
          AND lower(allocation_level) = 'namespace'
          AND lower(parent_resource_id) = lower({_quote_literal(cluster_id)})
          AND lower(COALESCE(namespace, allocation_key)) = lower({_quote_literal(namespace)})
          AND CAST(snapshot_date AS date) BETWEEN DATE '{window.from_date.isoformat()}' AND DATE '{window.to_date.isoformat()}'
        ORDER BY snapshot_date
        """,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    if not rows and not allocations:
        raise HTTPException(status_code=404, detail="Não há métricas históricas para este namespace no Silver.")
    return {
        "cluster_resource_id": cluster_id,
        "cluster_name": topology.get("cluster_name"),
        "region": topology.get("region"),
        "kubernetes_version": topology.get("kubernetes_version"),
        "namespace": namespace,
        "series": rows,
        "allocations": allocations,
        "metric_window": window.as_dict(_observed_partition_days(rows, "snapshot_date")),
    }


@router.get("/actions/worker-detail")
async def finops_action_worker_detail(
    resource_id: str = Query(..., min_length=1, max_length=2048),
    worker: str = Query(..., min_length=1, max_length=512),
    from_day: str | None = Query(None, description="Início inclusivo UTC YYYYMMDD; informe junto com to_day."),
    to_day: str | None = Query(None, description="Fim inclusivo UTC YYYYMMDD; deve ser dia completo."),
    user: User = _READ,
) -> dict[str, Any]:
    """Séries de consumo e workloads observados em um nó AKS específico."""
    window = resolve_metric_day_window(from_day, to_day)
    detail = await _action_detail_data(resource_id, window)
    topology = detail.get("aks_topology") or {}
    if topology.get("status") != "AVAILABLE":
        raise HTTPException(status_code=409, detail="A topologia AKS deste recurso não está disponível.")
    cluster_id = str(topology.get("cluster_resource_id") or "")
    metric_filter = _metric_partition_filter("m", window, owner=_METRICS_4P_CONTAINER_OWNER)
    rows = await trino_execute(
        f"""
        SELECT CASE WHEN m.record_kind = 'performance' THEN date_trunc('hour', m.snapshot_date)
                    ELSE date_trunc('day', m.snapshot_date) END AS snapshot_date,
               m.record_kind, m.metric_name,
               avg(try_cast(m.metric_average AS DOUBLE)) AS metric_average,
               sum(try_cast(m.samples AS DOUBLE)) AS samples,
               max(m.nodes) AS nodes, max(m.pods) AS pods, max(m.containers) AS containers,
               max(m.kubernetes_version) AS kubernetes_version,
               m.namespace, m.computer,
               max(m.coverage_status) AS coverage_status,
               max(m.coverage_reason) AS coverage_reason
        FROM iceberg.silver.container_metric_series m
        WHERE {metric_filter}
          AND m.cluster_resource_id = {_quote_literal(cluster_id)}
          AND lower(m.computer) = lower({_quote_literal(worker)})
        GROUP BY 1, 2, 3, m.namespace, m.computer
        ORDER BY snapshot_date, namespace, record_kind, metric_name
        """,
        timeout_s=_SUMMARY_TIMEOUT,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Não há métricas históricas deste worker no Silver.")
    return {
        "cluster_resource_id": cluster_id,
        "cluster_name": topology.get("cluster_name"),
        "region": topology.get("region"),
        "kubernetes_version": topology.get("kubernetes_version"),
        "worker": worker,
        "series": rows,
        "metric_window": window.as_dict(_observed_partition_days(rows, "snapshot_date")),
    }


class JevActionReviewIn(BaseModel):
    resource_id: str = Field(..., min_length=1, max_length=2048)


@router.post("/actions/jev-review")
async def finops_jev_action_review(
    payload: JevActionReviewIn,
    db: AsyncSession = Depends(get_db),
    user: User = _READ,
) -> dict[str, Any]:
    detail = await _action_detail_data(payload.resource_id)
    action = detail["action"]
    alternatives = detail["family_options"]
    integration = (await db.execute(
        select(SystemIntegration).where(SystemIntegration.code == "jev")
    )).scalars().first()
    if not integration or not integration.enabled:
        return {"status": "UNAVAILABLE", "reason": "A integração Jev não está habilitada.", "deterministic_verdict": action.get("verdict"), "applied": False}

    choices = {
        "keep_current": f"Manter o SKU atual {action.get('sku_from')}.",
    }
    if action.get("sku_to"):
        movement = f"mudança de {action.get('vendor_from')} para {action.get('vendor_to')}" if action.get("family_change") else "mesma família de processador"
        choices["deterministic_target"] = f"Avaliar a redução determinística para {action.get('sku_to')} ({movement})."
    choices.update({
        f"family_{index}": (
            f"Avaliar {option['vendor']} {option['sku']} ({option['vcpus']} vCPU, {option['memory_gb']} GB, "
            + (f"economia estimada {option['saving']} na mesma fonte de preço)." if option.get("price_comparable") else "sem preço comparável).")
        )
        for index, option in enumerate(alternatives)
        if not option.get("is_deterministic_target")
    })
    state = json.dumps({
        "resource_type": "Azure virtual machine",
        "region": action.get("region_id"),
        "current": {"sku": action.get("sku_from"), "vcpus": action.get("vcpus_from"), "memory_gb": action.get("memory_gb_from")},
        "observed": {"cpu_p95_pct": action.get("cpu_p95"), "memory_available_p95_pct": action.get("mem_p95"), "observed_days": action.get("observed_days")},
        "deterministic": {"sku": action.get("sku_to"), "verdict": action.get("verdict"), "saving_observed_window": action.get("saving_monthly"), "veto_reason": action.get("veto_reason")},
        "family_options": alternatives,
            "constraints": ["Do not recommend an unavailable SKU", "Do not claim performance improvement without benchmark data", "A family change requires application compatibility validation", "Do not assume subscription quota or policy is validated for catalog alternatives"],
    }, ensure_ascii=False, default=str)
    questions = {"recommendation": {
        "type": "choice",
        "instructions": "Choose the best option for human review. This is advisory only; do not claim a VM change was executed.",
        "criteria": choices,
    }}
    from app.services.jev_service import ask

    # /v1/systemone aceita model/state/questions. `context` não faz parte do
    # contrato do endpoint e fazia a API responder 400 Invalid request.
    result = await ask(integration, state, questions)
    answers = result.get("answers")
    await db.execute(sql_text("""
        INSERT INTO assisted_decision_audit
            (integration_code, input_hash, status, model, model_version, answers,
             reason, deterministic_verdict, applied)
        VALUES (:integration_code, :input_hash, :status, :model, :model_version,
                CAST(:answers AS JSON), :reason, :deterministic_verdict, FALSE)
    """), {
        "integration_code": "jev",
        "input_hash": result.get("input_hash") or "",
        "status": result.get("status") or "UNAVAILABLE",
        "model": result.get("model"),
        "model_version": (answers or {}).get("model_version") if isinstance(answers, dict) else None,
        "answers": json.dumps(answers, ensure_ascii=False, default=str) if answers is not None else None,
        "reason": result.get("reason"),
        "deterministic_verdict": action.get("verdict"),
    })
    await db.commit()
    return {**result, "deterministic_verdict": action.get("verdict"), "applied": False, "choices": choices}


@router.get("/vendor-comparison")
async def finops_vendor_comparison(user: User = _READ) -> dict[str, Any]:
    """Comparação de vendor por capacidade equivalente, independente de uso/p95."""
    inventory = await trino_execute("""
        WITH latest AS (
            SELECT resource_id, resource_sku, region_id, cloud_provider,
                   row_number() OVER (PARTITION BY cloud_provider, resource_id ORDER BY valid_from DESC) AS rn
            FROM iceberg.silver.resource_inventory
            WHERE lower(resource_type) LIKE '%virtualmachine%'
              AND lower(cloud_provider) = 'azure'
              AND resource_sku IS NOT NULL AND resource_id IS NOT NULL
        )
        SELECT cloud_provider, resource_sku AS sku, region_id, count(*) AS servers
        FROM latest WHERE rn = 1 GROUP BY 1, 2, 3
    """, timeout_s=_SUMMARY_TIMEOUT)
    sku_counts: dict[tuple[str, str], int] = {}
    sku_regions: set[tuple[str, str]] = set()
    counts = {"Intel": 0, "AMD": 0, "other": 0}
    for row in inventory:
        sku = str(row.get("sku") or "")
        vendor = _vm_vendor(sku)
        n = int(row.get("servers") or 0)
        counts[vendor or "other"] += n
        if vendor:
            key = (_sku_key(sku), str(row.get("region_id") or "").lower())
            sku_counts[key] = sku_counts.get(key, 0) + n
            sku_regions.add((sku, str(row.get("region_id") or "")))

    catalog = await trino_execute("""
        SELECT sku_name AS sku, lower(region_id) AS region_id,
               max(try_cast(vcpus AS double)) AS vcpus,
               max(try_cast(memory_gb AS double)) AS memory_gb
        FROM iceberg.silver.compute_sku_catalog
        WHERE upper(is_available) = 'SIM'
          AND (upper(sku_name) LIKE 'STANDARD_D%' OR upper(sku_name) LIKE 'STANDARD_E%')
        GROUP BY 1, 2
    """, timeout_s=_SUMMARY_TIMEOUT)
    capacities = {
        (_sku_key(str(row.get("sku") or "")), str(row.get("region_id") or "").lower()): row
        for row in catalog
    }
    rates = await trino_execute("""
        SELECT regexp_replace(regexp_replace(lower(meter_name), '^standard[ _]?', ''), '[^a-z0-9]', '') AS sku_key,
               lower(region_id) AS region_id, price_source, currency, unit_of_measure,
               avg(try_cast(unit_price AS double)) AS unit_price
        FROM iceberg.silver.price_catalog
        WHERE service_name = 'Virtual Machines' AND unit_price > 0
          AND lower(price_type) = 'consumption'
        GROUP BY 1, 2, 3, 4, 5
    """, timeout_s=_SUMMARY_TIMEOUT)
    # The price catalog is the cost reference for this vendor-only scenario.
    # It lets us compare even when realized spend is not mapped to every VM.
    from app.services.rightsizing_service import REGIAO_EXIBICAO_PARA_ARM
    price_region_to_arm = {
        "".join(ch for ch in alias.lower() if ch.isalnum()): arm
        for alias, arm in REGIAO_EXIBICAO_PARA_ARM.items()
    }
    rate_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rate in rates:
        price_region = str(rate.get("region_id") or "").lower()
        price_region_key = "".join(ch for ch in price_region if ch.isalnum())
        normalized_region = price_region_to_arm.get(price_region_key, price_region)
        rate_map.setdefault((str(rate.get("sku_key") or ""), normalized_region), []).append(rate)
    savings_by_currency: dict[str, float] = {}
    coverage = 0
    currency = None
    pairs: list[dict[str, Any]] = []
    for (sku_key, region), servers in sku_counts.items():
        current = capacities.get((sku_key, region))
        sku = next((s for s, r in sku_regions if _sku_key(s) == sku_key and r.lower() == region), "")
        vendor = _vm_vendor(sku)
        # Esta aba representa apenas a oportunidade Intel -> AMD solicitada.
        if not current or vendor != "Intel":
            continue
        vcpu, memory = float(current.get("vcpus") or 0), float(current.get("memory_gb") or 0)
        current_rates = rate_map.get((sku_key, region), [])
        best = None
        for (other_key, other_region), alt in capacities.items():
            alt_vendor = _vm_vendor(str(alt.get("sku") or ""))
            if other_region != region or alt_vendor != "AMD":
                continue
            if float(alt.get("vcpus") or 0) != vcpu or float(alt.get("memory_gb") or 0) < memory:
                continue
            for cur_rate in current_rates:
                alt_rate = next((r for r in rate_map.get((other_key, region), [])
                    if r.get("price_source") == cur_rate.get("price_source")
                    and r.get("currency") == cur_rate.get("currency")
                    and _price_unit_hours(r.get("unit_of_measure")) is not None), None)
                current_hours = _price_unit_hours(cur_rate.get("unit_of_measure"))
                alt_hours = _price_unit_hours(alt_rate.get("unit_of_measure")) if alt_rate else None
                if alt_rate and current_hours and alt_hours and float(cur_rate.get("unit_price") or 0) > 0:
                    # Normalize the workbook/Silver price-sheet amount to one
                    # compute hour, then price 730 hours per server per month.
                    monthly_current_per_server = float(cur_rate["unit_price"]) / current_hours * 730
                    monthly_alt_per_server = float(alt_rate["unit_price"]) / alt_hours * 730
                    saving_per_server = monthly_current_per_server - monthly_alt_per_server
                    candidate_pair = {"sku_from": sku, "sku_to": alt["sku"], "vendor_from": vendor,
                        "vendor_to": _vm_vendor(str(alt["sku"])), "servers": servers,
                        "saving_monthly": round(saving_per_server * servers, 2),
                        "cost_current_monthly": round(monthly_current_per_server * servers, 2),
                        "cost_target_monthly": round(monthly_alt_per_server * servers, 2),
                        "price_source": cur_rate.get("price_source"), "currency": cur_rate.get("currency"),
                        "price_currency": cur_rate.get("currency"), "price_unit": cur_rate.get("unit_of_measure"),
                        "cost_basis": "PRICE_CATALOG_730H", "price_comparable": True,
                        "cost_available": True, "comparison_status": "COMPARABLE"}
                    if best is None or candidate_pair["saving_monthly"] > best["saving_monthly"]:
                        best = candidate_pair
        if best:
            pairs.append(best)
            if best["saving_monthly"] is not None:
                ccy = str(best["currency"] or "")
                savings_by_currency[ccy] = savings_by_currency.get(ccy, 0.0) + best["saving_monthly"]
                coverage += servers
    total = sum(counts.values())
    currency = next(iter(savings_by_currency)) if len(savings_by_currency) == 1 else None
    saving_total = next(iter(savings_by_currency.values())) if len(savings_by_currency) == 1 else None
    return {"counts": counts, "total_servers": total,
        "shares_pct": {k: round(v * 100 / total, 1) if total else 0 for k, v in counts.items()},
        "estimated_monthly_saving": round(saving_total, 2) if saving_total is not None else None, "currency": currency,
        "savings_by_currency": {k: round(v, 2) for k, v in savings_by_currency.items()},
        "servers_with_comparable_saving": coverage, "pairs": pairs[:50],
        "cost_basis": {"source": "price_catalog", "hours_per_month": 730, "region_must_match": True},
        "method": "Saving potencial calculado pela tabela de preços Consumption carregada no Silver: preço unitário normalizado para hora × 730 horas × servidores do inventário. SKU de origem e destino precisam ter tarifas da mesma fonte, moeda e região. Esse cenário usa preço de referência, não custo realizado nem consumo/p95."}


DISK_TYPES = {
    "Standard_LRS": "HDD padrão",
    "StandardSSD_LRS": "SSD padrão",
    "StandardSSD_ZRS": "SSD padrão ZRS",
    "Premium_LRS": "SSD Premium",
    "Premium_ZRS": "SSD Premium ZRS",
    "PremiumV2_LRS": "SSD Premium v2",
}

DISK_PERFORMANCE = {
    "HDD padrão": [("S4", 32, 500, 60), ("S6", 64, 500, 60), ("S10", 128, 500, 60), ("S15", 256, 500, 60), ("S20", 512, 500, 60), ("S30", 1024, 500, 60), ("S40", 2048, 500, 60), ("S50", 4096, 500, 60), ("S60", 8192, 1300, 300), ("S70", 16384, 2000, 500), ("S80", 32767, 2000, 500)],
    "SSD padrão": [("E1", 4, 500, 100), ("E2", 8, 500, 100), ("E3", 16, 500, 100), ("E4", 32, 500, 100), ("E6", 64, 500, 100), ("E10", 128, 500, 100), ("E15", 256, 500, 100), ("E20", 512, 500, 100), ("E30", 1024, 500, 100), ("E40", 2048, 500, 100), ("E50", 4096, 500, 100), ("E60", 8192, 500, 100), ("E70", 16384, 4000, 600), ("E80", 32767, 6000, 750)],
    "SSD Premium": [("P1", 4, 120, 25), ("P2", 8, 120, 25), ("P3", 16, 120, 25), ("P4", 32, 120, 25), ("P6", 64, 240, 50), ("P10", 128, 500, 100), ("P15", 256, 1100, 125), ("P20", 512, 2300, 150), ("P30", 1024, 5000, 200), ("P40", 2048, 7500, 250), ("P50", 4096, 7500, 250), ("P60", 8192, 16000, 500), ("P70", 16384, 18000, 750), ("P80", 32767, 20000, 900)],
}


@router.get("/disk-actions")
async def finops_disk_actions(
    from_day: str | None = Query(None, description="Início inclusivo UTC YYYYMMDD; informe junto com to_day."),
    to_day: str | None = Query(None, description="Fim inclusivo UTC YYYYMMDD; deve ser dia completo."),
    user: User = _READ,
) -> dict[str, Any]:
    """Inventário e oportunidades de discos usando métricas por resource_id."""
    window = resolve_metric_day_window(from_day, to_day)
    inventory = await trino_execute("""
        WITH latest AS (
            SELECT resource_id, resource_name, resource_sku, sku_capacity, region_id,
                   row_number() OVER (PARTITION BY cloud_provider, resource_id ORDER BY valid_from DESC) AS rn
            FROM iceberg.silver.resource_inventory
            WHERE lower(cloud_provider) = 'azure' AND lower(resource_type) = 'microsoft.compute/disks'
        )
        SELECT resource_id, resource_name, resource_sku, sku_capacity, region_id
        FROM latest WHERE rn = 1
    """, timeout_s=_SUMMARY_TIMEOUT)
    metric_filter = _metric_partition_filter("m", window, owner=_METRICS_4P_VM_OWNER)
    profiles = await trino_execute(f"""
        SELECT m.resource_id, m.metric, approx_percentile(try_cast(m.value AS double), 0.95) AS p95,
               max(m.bucket_start_utc) AS last_seen, count(*) AS samples, max(m.metric_unit) AS metric_unit,
               count(DISTINCT m._billing_period) AS observed_days
        FROM iceberg.silver.resource_metric_series m
        WHERE {metric_filter}
          AND lower(m.resource_type) = 'microsoft.compute/disks'
          AND m.quality_status = 'OK' AND m.value IS NOT NULL
          AND m.metric IN ('Composite Disk Read Operations/sec','Composite Disk Write Operations/sec',
                         'Composite Disk Read Bytes/sec','Composite Disk Write Bytes/sec')
        GROUP BY 1, 2
    """, timeout_s=_SUMMARY_TIMEOUT)
    period_rows = await trino_execute("""
        SELECT max(billing_period) AS billing_period FROM iceberg.silver.resource_cost
    """, timeout_s=_SUMMARY_TIMEOUT)
    billing_period = (period_rows[0].get("billing_period") if period_rows else None)
    costs = await trino_execute(f"""
        SELECT resource_id, max(resource_name) AS resource_name,
               max(charge_description) AS charge_description,
               max(pricing_currency) AS currency,
               round(sum(try_cast(effective_cost AS double)), 2) AS effective_cost
        FROM iceberg.silver.resource_cost
        WHERE lower(cloud_provider) = 'azure' AND billing_period = {_quote_literal(billing_period or '')}
        GROUP BY resource_id
    """, timeout_s=_SUMMARY_TIMEOUT) if billing_period else []
    cost_by_id = {str(row.get("resource_id")): row for row in costs}
    profile_by_id: dict[str, dict[str, Any]] = {}
    for row in profiles:
        profile_by_id.setdefault(str(row.get("resource_id")), {})[str(row.get("metric"))] = row

    counts: dict[str, int] = {}
    actions: list[dict[str, Any]] = []
    for disk in inventory:
        rid = str(disk.get("resource_id") or "")
        sku = str(disk.get("resource_sku") or "")
        tier = DISK_TYPES.get(sku, "Outro / não classificado")
        counts[tier] = counts.get(tier, 0) + 1
        metrics = profile_by_id.get(rid, {})
        read_iops = metrics.get("Composite Disk Read Operations/sec")
        write_iops = metrics.get("Composite Disk Write Operations/sec")
        read_bps = metrics.get("Composite Disk Read Bytes/sec")
        write_bps = metrics.get("Composite Disk Write Bytes/sec")
        iops = (float(read_iops.get("p95") or 0) if read_iops else 0) + (float(write_iops.get("p95") or 0) if write_iops else 0)
        throughput_mbps = ((float(read_bps.get("p95") or 0) if read_bps else 0) + (float(write_bps.get("p95") or 0) if write_bps else 0)) / 1_000_000
        sample_count = sum(int(m.get("samples") or 0) for m in metrics.values())
        cost = cost_by_id.get(rid, {})
        action_state = "READY_FOR_COMPARISON" if metrics and disk.get("sku_capacity") else (
            "WAITING_FOR_DISK_METRICS" if not metrics else "WAITING_FOR_DISK_CAPACITY"
        )
        target_tiers = []
        size_gib = float(disk.get("sku_capacity") or 0)
        redundancy = "ZRS" if sku.upper().endswith("_ZRS") else "LRS"
        if action_state == "READY_FOR_COMPARISON":
            for family, tiers in DISK_PERFORMANCE.items():
                for tier_name, capacity, max_iops, max_mbps in tiers:
                    if capacity >= size_gib and max_iops >= iops * 1.25 and max_mbps >= throughput_mbps * 1.25:
                        target_tiers.append({"family": family, "tier": tier_name,
                            "redundancy": redundancy,
                            "max_iops": max_iops, "max_throughput_mbps": max_mbps})
                        break
            if size_gib > 6 and redundancy == "LRS":
                v2_iops = max(3000, math.ceil(iops * 1.25))
                v2_mbps = max(125, math.ceil(throughput_mbps * 1.25))
                if v2_iops <= min(80000, 500 * size_gib) and v2_mbps <= min(2000, 0.25 * v2_iops):
                    target_tiers.append({"family": "SSD Premium v2", "tier": "Premium SSD v2",
                        "provisioned_iops": v2_iops, "provisioned_throughput_mbps": v2_mbps})
            if not target_tiers:
                action_state = "NO_SAFE_TARGET"
        actions.append({
            "resource_id": rid, "resource_name": disk.get("resource_name"),
            "sku": sku, "tier": tier, "size_gib": disk.get("sku_capacity"),
            "region": disk.get("region_id"), "billing_period": billing_period,
            "effective_cost": cost.get("effective_cost"), "currency": cost.get("currency"),
            "charge_description": cost.get("charge_description"),
            "read_iops_p95": round(float(read_iops.get("p95") or 0), 2) if read_iops else None,
            "write_iops_p95": round(float(write_iops.get("p95") or 0), 2) if write_iops else None,
            "total_iops_p95_conservative": round(iops, 2) if metrics else None,
            "throughput_mbps_p95_conservative": round(throughput_mbps, 3) if metrics else None,
            "samples": sample_count,
            "last_metric_at": max((m.get("last_seen") for m in metrics.values() if m.get("last_seen")), default=None),
            "action_state": action_state, "compatible_target_tiers": target_tiers,
        })
    actions.sort(key=lambda row: (row["action_state"] != "READY_FOR_COMPARISON", -(row["effective_cost"] or 0)))
    observed = sum(1 for row in actions if row["action_state"] == "READY_FOR_COMPARISON")
    observed_days = max((int(row.get("observed_days") or 0) for row in profiles), default=0)
    return {
        "total_disks": len(inventory), "tier_counts": counts,
        "disks_with_direct_metrics": len(profile_by_id), "disks_ready_for_comparison": observed,
        "billing_period": billing_period, "metric_window_days": window.requested_days,
        "metric_window": window.as_dict(observed_days), "actions": actions[:500],
        "notes": [
            "A métrica agregada de uma VM soma vários discos e não é atribuída a um disco individual. Só haverá comparação por disco quando Azure Monitor tiver série cujo resource_id seja o próprio managed disk.",
            "IOPS e throughput exibidos usam p95 de leitura + p95 de gravação como limite conservador; os dados não são uma recomendação de alteração automática.",
            "Premium SSD v2 cobra capacidade, IOPS e throughput provisionados separadamente; qualquer saving depende dos preços e configurações efetivas do disco.",
        ],
    }


@router.get("/disk-actions/detail")
async def finops_disk_action_detail(
    resource_id: str = Query(..., min_length=1, max_length=2048),
    from_day: str | None = Query(None, description="Início inclusivo UTC YYYYMMDD; informe junto com to_day."),
    to_day: str | None = Query(None, description="Fim inclusivo UTC YYYYMMDD; deve ser dia completo."),
    user: User = _READ,
) -> dict[str, Any]:
    window = resolve_metric_day_window(from_day, to_day)
    safe_id = _quote_literal(resource_id)
    rows = await trino_execute(f"""
        WITH i AS (
            SELECT resource_id, resource_name, resource_sku, sku_capacity, region_id,
                   row_number() OVER (PARTITION BY cloud_provider, resource_id ORDER BY valid_from DESC) rn
            FROM iceberg.silver.resource_inventory
            WHERE lower(cloud_provider) = 'azure' AND lower(resource_type) = 'microsoft.compute/disks'
              AND resource_id = {safe_id}
        )
        SELECT resource_id, resource_name, resource_sku, sku_capacity, region_id
        FROM i WHERE rn = 1
    """, timeout_s=_SUMMARY_TIMEOUT)
    if not rows:
        raise HTTPException(status_code=404, detail="Disco não encontrado no inventário atual.")
    metric_filter = _metric_partition_filter("m", window, owner=_METRICS_4P_VM_OWNER)
    series = await trino_execute(f"""
        SELECT date_trunc('hour', m.bucket_start_utc) AS bucket_start_utc, m.metric,
               avg(try_cast(m.value AS double)) AS value,
               min(try_cast(m.value AS double)) AS value_min,
               max(try_cast(m.value AS double)) AS value_max,
               max(m.metric_unit) AS metric_unit, count(*) AS observed_samples
        FROM iceberg.silver.resource_metric_series m
        WHERE {metric_filter} AND lower(m.resource_type) = 'microsoft.compute/disks'
          AND m.resource_id = {safe_id}
          AND m.quality_status = 'OK' AND m.value IS NOT NULL
          AND m.metric IN ('Composite Disk Read Operations/sec','Composite Disk Write Operations/sec',
                         'Composite Disk Read Bytes/sec','Composite Disk Write Bytes/sec')
        GROUP BY 1, 2 ORDER BY bucket_start_utc, metric
    """, timeout_s=_SUMMARY_TIMEOUT)
    return {"disk": rows[0], "series": series,
            "metric_window": window.as_dict(_observed_partition_days(series, "bucket_start_utc")),
            "metric_window_days": window.requested_days}


@router.get("/overview")
async def finops_overview(
    from_day: str | None = Query(None, description="Início inclusivo UTC YYYYMMDD; informe junto com to_day."),
    to_day: str | None = Query(None, description="Fim inclusivo UTC YYYYMMDD; deve ser dia completo."),
    user: User = Depends(require("finops", "read")),
) -> dict[str, Any]:
    """Visão de uma tela: cards, principais mudanças e ações priorizadas.

    Responde as duas perguntas de entrada de um gestor — "como estamos" e
    "o que fazer agora" — sem exigir que ele navegue sete abas. Cada card
    carrega valor, comparação e escopo; nada aqui pede cálculo no navegador.
    """
    cards_sql = summary_sql("1 = 1")
    linhas, ms = await _run(cards_sql, timeout_s=_SUMMARY_TIMEOUT)
    moeda = _separate_currency(linhas)

    from app.services.finops_cost_driver import period_statuses
    from app.services.period_coverage import (
        COMPARABLE_STATUSES,
        DEFAULT_UNKNOWN_STATUS,
        PeriodCoverage,
        load_coverage_map,
    )

    cobertura = await load_coverage_map()
    status_por_periodo: dict[str, str] = {}
    for (conexao, periodo), estado in cobertura.items():
        atual = status_por_periodo.get(periodo)
        if atual == "PARTIAL" or (atual is None and estado != "COMPLETE"):
            status_por_periodo[periodo] = estado if estado != "COMPLETE" else atual or estado
        elif estado == "COMPLETE" and atual != "PARTIAL":
            status_por_periodo.setdefault(periodo, estado)

    por_periodo: list[dict[str, Any]] = []
    for r in linhas:
        estados = {
            e for (_, p), e in cobertura.items() if p == str(r["billing_period"])
        }
        por_periodo.append(
            {
                "periodo": str(r["billing_period"]),
                "custo": float(r.get("effective_cost") or 0),
                "status": next((s for s in ("PARTIAL", "CURRENT_OPEN", "COMPLETE") if s in estados), DEFAULT_UNKNOWN_STATUS),
            }
        )
    comparaveis = sorted(
        p["periodo"] for p in por_periodo if p["status"] in COMPARABLE_STATUSES
    )
    comparaveis_recentes = _periodos_consecutivos_finais(comparaveis)
    ultimo_periodo_disponivel = max((p["periodo"] for p in por_periodo), default=None)
    status_ultimo_disponivel = next((p["status"] for p in por_periodo if p["periodo"] == ultimo_periodo_disponivel), None)

    # Cards: último mês completo, variação MoM, acumulado, run rate.
    custo_mes, periodo_mes = None, None
    for p in reversed(por_periodo):
        if p["status"] in COMPARABLE_STATUSES:
            custo_mes, periodo_mes = p["custo"], p["periodo"]
            break
    mom = {"comparavel": False, "reason": "sem par comparável"}
    if len(comparaveis_recentes) >= 2:
        custo_atual = next(
            p["custo"] for p in por_periodo if p["periodo"] == comparaveis_recentes[-1]
        )
        custo_ant = next(
            p["custo"] for p in por_periodo if p["periodo"] == comparaveis_recentes[-2]
        )
        delta = round(custo_mes - custo_ant, 2)
        mom = {
            "comparavel": True,
            "periodo_anterior": comparaveis_recentes[-2],
            "periodo_atual": comparaveis_recentes[-1],
            "delta": delta,
            "delta_pct": round(100.0 * delta / custo_ant, 2) if custo_ant else None,
        }

    total_historico = sum(p["custo"] for p in por_periodo)
    run_rate = {
        "mensal": round(sum(next(p["custo"] for p in por_periodo if p["periodo"] == bp) for bp in comparaveis_recentes) / len(comparaveis_recentes), 2)
        if comparaveis_recentes
        else None,
        "meses": len(comparaveis_recentes),
        "periodos": comparaveis_recentes,
    }
    if run_rate["mensal"]:
        run_rate["anual"] = round(run_rate["mensal"] * 12, 2)

    # Economia identificada: candidatos VALID (o que a análise determinística
    # reconhece), e o estado de revisão real vem do PostgreSQL.
    acoes = await _acoes_detalhadas()
    economia_identificada = round(
        sum(float(a.get("economia_mensal") or 0.0) for a in acoes
            if a["verdict"] == "VALID" and a["status_revisao"] != "REJECTED"),
        2,
    )
    economia_aprovada = round(
        sum(float(a.get("economia_mensal") or 0.0) for a in acoes
            if a["verdict"] == "VALID" and a["status_revisao"] == "APPROVED"),
        2,
    )

    movers = (await cost_drivers(
        period_current=comparaveis_recentes[-1],
        period_previous=comparaveis_recentes[-2],
        driver_type="service",
        limit=5,
        user=None,
    ))["data"]["drivers"] if len(comparaveis_recentes) >= 2 else []

    periodos_por_status = {p["periodo"]: p["status"] for p in por_periodo}
    hoje = date.today()
    ultima_completa = comparaveis[-1] if comparaveis else None
    mes = int(ultima_completa[4:6]) + 1 if ultima_completa else 1
    ano = int(ultima_completa[:4]) if ultima_completa else hoje.year
    pendentes: list[str] = []
    while ultima_completa and (ano, mes) < (hoje.year, hoje.month):
        competencia = f"{ano:04d}{mes:02d}"
        if periodos_por_status.get(competencia) != "COMPLETE":
            pendentes.append(competencia)
        mes += 1
        if mes > 12:
            ano += 1
            mes = 1
    metric_window = resolve_metric_day_window(from_day, to_day)
    freshness_filter = _metric_partition_filter("m", metric_window, owner=_METRICS_4P_VM_OWNER)
    metric_freshness_rows = await trino_execute(f"""
        SELECT max(m.bucket_start_utc) AS last_observation_utc,
               min(m.bucket_start_utc) AS first_observation_utc,
               count(DISTINCT m.resource_id) AS resources,
               count(DISTINCT m._billing_period) AS observed_days,
               count(DISTINCT m.resource_id) FILTER (WHERE lower(m.resource_type) = 'microsoft.compute/disks') AS disks_with_metrics
        FROM iceberg.silver.resource_metric_series m
        WHERE {freshness_filter}
          AND m.quality_status = 'OK' AND m.value IS NOT NULL
    """, timeout_s=_SUMMARY_TIMEOUT)
    metric_freshness = metric_freshness_rows[0] if metric_freshness_rows else {}
    qualidade_warnings = []
    stale_recommendations = sum(1 for action in acoes if action.get("profile_status") == "STALE")
    if stale_recommendations:
        qualidade_warnings.append(
            f"{stale_recommendations} recomendações ainda usam perfil anterior à amostra 4P mais recente; "
            "elas permanecem visíveis como snapshot, sem recálculo nesta consulta."
        )
    if pendentes:
        qualidade_warnings.append(
            f"Faturamento: {ultima_completa} é a competência fechada mais recente; "
            f"sem fechamento completo em {', '.join(pendentes)}. A competência financeira "
            f"mais recente no cubo é {ultimo_periodo_disponivel} ({status_ultimo_disponivel}). "
            "Isso não representa a data da coleta de métricas dos recursos."
        )

    return _envelope(
        scope={"periodo_comparavel": comparaveis[-1] if comparaveis else None},
        currency=moeda,
        quality={"coverage_pct": None, "warnings": qualidade_warnings},
        data={
            "cards": {
                "custo_mes_completo": {
                    "periodo": periodo_mes,
                    "valor": custo_mes,
                },
                "freshness": {
                    "latest_available_period": ultimo_periodo_disponivel,
                    "latest_available_status": status_ultimo_disponivel,
                    "latest_complete_period": ultima_completa,
                    "periods_without_complete_coverage": pendentes,
                    "metric_observations_through_utc": metric_freshness.get("last_observation_utc"),
                    "metric_observations_from_utc": metric_freshness.get("first_observation_utc"),
                    "metric_resources": int(metric_freshness.get("resources") or 0),
                    "disk_resources_with_metrics": int(metric_freshness.get("disks_with_metrics") or 0),
                    "metric_window_days": metric_window.requested_days,
                    "metric_observed_days": int(metric_freshness.get("observed_days") or 0),
                    "metric_from_day": metric_window.from_day,
                    "metric_to_day": metric_window.to_day,
                },
                "variacao_mom": mom,
                "custo_total_historico": {
                    "valor": round(sum(p["custo"] for p in por_periodo), 2),
                    "competencias": len(por_periodo),
                    "nota": "acumula competências com escopos diferentes; não é um mês",
                },
                "run_rate": run_rate,
                "economia": {
                    "identificada": economia_identificada,
                    "aprovada": economia_aprovada,
                    "acoes": len(acoes),
                    "nota": "estimativa mensal; aprovada não é realizada",
                },
            },
            "principais_mudancas": movers,
            "acoes_top": [
                {
                    "recurso": a["recurso"],
                    "acao": a["tipo_acao"],
                    "economia_mensal": a["economia_mensal"],
                    "confianca": a["confianca"],
                }
                for a in sorted(
                    (a for a in acoes if a["verdict"] == "VALID" and a.get("economia_mensal") is not None),
                    key=lambda x: -float(x.get("economia_mensal") or 0.0),
                )[:5]
            ],
            "periodos": por_periodo,
        },
        elapsed_ms=ms,
    )


async def _acoes_detalhadas() -> list[dict[str, Any]]:
    """Ações propostas com o estado de revisão real (PostgreSQL sobrepõe Silver)."""
    candidatos, _ = await _run(_ACOES_SQL, timeout_s=_SUMMARY_TIMEOUT)
    from app.services.trino_service import execute as trino_execute

    metric_window = resolve_metric_day_window()
    resource_ids = sorted({str(row.get("resource_id") or "") for row in candidatos if row.get("resource_id")})
    freshness_by_resource: dict[str, tuple[datetime, int]] = {}
    if resource_ids:
        metric_filter = _metric_partition_filter("m", metric_window, owner=_METRICS_4P_VM_OWNER)
        metric_rows = await trino_execute(
            f"""
            SELECT m.resource_id, max(m.bucket_start_utc) AS metrics_observed_through_utc,
                   count(DISTINCT m._billing_period) AS metric_observed_days
            FROM iceberg.silver.resource_metric_series m
            WHERE {metric_filter}
              AND m.resource_id IN ({', '.join(_quote_literal(rid) for rid in resource_ids)})
              AND m.quality_status = 'OK' AND m.value IS NOT NULL
            GROUP BY m.resource_id
            """,
            timeout_s=_SUMMARY_TIMEOUT,
        )
        freshness_by_resource = {
            str(row.get("resource_id")): (parsed, int(row.get("metric_observed_days") or 0))
            for row in metric_rows
            if (parsed := _as_utc_datetime(row.get("metrics_observed_through_utc")))
        }

    revisoes = {
        str(r["evidence_hash"]): str(r["review_status"])
        for r in (
            await trino_execute(
                "SELECT evidence_hash, review_status FROM iceberg.silver.rightsizing_decision",
                timeout_s=300,
            )
            or []
        )
    }
    por_recurso: dict[str, str] = {}
    for r in (
        await trino_execute(
            "SELECT d.evidence_hash, d.resource_id, d.review_status "
            "FROM iceberg.silver.rightsizing_decision d",
            timeout_s=300,
        )
        or []
    ):
        por_recurso[str(r["resource_id"])] = str(r["review_status"])

    acoes: list[dict[str, Any]] = []
    for r in candidatos:
        rid = str(r.get("resource_id") or "")
        profile_built_at = _as_utc_datetime(r.get("profile_built_at"))
        latest_metric_at, current_metric_days = freshness_by_resource.get(rid, (None, 0))
        profile_status = (
            "STALE" if profile_built_at and latest_metric_at and latest_metric_at > profile_built_at
            else "CURRENT" if profile_built_at and latest_metric_at
            else "UNKNOWN"
        )
        estado = revisoes.get(rid) or por_recurso.get(rid) or "PENDING"
        verdict = str(r.get("verdict") or "")
        acoes.append(
            {
                "recurso": r.get("recurso"),
                "resource_id": rid,
                "tipo_acao": "Redimensionar",
                "de": {
                    "sku": r.get("sku_from"),
                    "vcpus": r.get("vcpus_de"),
                    "memoria_gb": r.get("memoria_de"),
                    "custo_mensal": r.get("custo_atual"),
                },
                "para": {
                    "sku": r.get("sku_to"),
                    "vcpus": r.get("vcpus_para"),
                    "memoria_gb": r.get("memoria_para"),
                    "custo_mensal": r.get("custo_projetado"),
                },
                "economia_mensal": r.get("economia_mensal"),
                "economia_pct": r.get("economia_pct"),
                "economia_anual": round(float(r["economia_mensal"]) * 12, 2) if r.get("economia_mensal") is not None else None,
                "cpu_p95": r.get("cpu_p95"),
                "dias_observados": int(r.get("observed_days") or 0),
                "janela_solicitada_dias": int(r.get("requested_days") or 0),
                "perfil_atualizado_em": r.get("profile_built_at"),
                "profile_status": profile_status,
                "dias_serie_atual": current_metric_days,
                "metrics_observed_through_utc": latest_metric_at.isoformat() if latest_metric_at else None,
                "confianca": (
                    "alta" if verdict == "VALID"
                    and int(r.get("observed_days") or 0) >= max(14, math.ceil(int(r.get("requested_days") or 30) * 0.8))
                    else "media" if verdict == "VALID" and int(r.get("observed_days") or 0) >= 14
                    else "baixa"
                ),
                "verdict": verdict,
                "status_revisao": estado,
                "vetor": r.get("veto_reason"),
                "vendor_from": _vm_vendor(str(r.get("sku_from") or "")),
                "vendor_to": _vm_vendor(str(r.get("sku_to") or "")),
                "family_change": bool(
                    _vm_vendor(str(r.get("sku_from") or ""))
                    and _vm_vendor(str(r.get("sku_to") or ""))
                    and _vm_vendor(str(r.get("sku_from") or "")) != _vm_vendor(str(r.get("sku_to") or ""))
                ),
            }
        )
    return acoes


@router.get("/actions")
async def finops_actions(
    user: User = Depends(require("finops", "read")),
    incluir_sem_preco: bool = Query(False, description="Legado: recomendações REQUIRES_VALIDATION já são exibidas por padrão."),
) -> dict[str, Any]:
    """Ações propostas, no formato de uma lista de decisão (Turbonomic).

    Uma linha = uma ação com o que fazer, para quê, quanto vale e com quanta
    confiança. Sem lógica no navegador: o que chega é a lista ordenada.
    """
    acoes = await _acoes_detalhadas()
    # Exibir as duas classes evita que a contagem do Centro esconda
    # oportunidades que Rightsizing apresenta como pendentes de validação.
    # Somente VALID entra no total de economia identificada.
    visiveis = [
        a for a in acoes
        if a["verdict"] == "VALID"
        or a["verdict"] == "REQUIRES_VALIDATION"
    ]
    visiveis.sort(key=lambda x: (-float(x.get("economia_mensal") or 0.0), x["recurso"]))
    resumo_estados: dict[str, int] = {}
    economia_por_estado: dict[str, float] = {}
    perfis_antigos = sum(1 for a in visiveis if a.get("profile_status") == "STALE")
    for a in visiveis:
        estado = "aprovada" if a["status_revisao"] == "APPROVED" else (
            "rejeitada" if a["status_revisao"] == "REJECTED"
            else "pendente de revisão" if a["status_revisao"] == "PENDING"
            else a["verdict"]
        )
        resumo_estados[estado] = resumo_estados.get(estado, 0) + 1
        economia_por_estado[estado] = round(
            economia_por_estado.get(estado, 0.0) + float(a.get("economia_mensal") or 0.0), 2
        )
    return _envelope(
        scope={"cubo": "gold.finops_rightsizing_actions", "revisoes": "rightsizing_decision"},
        quality={
            "coverage_pct": None,
            "warnings": [
                "economia é estimativa mensal; aprovada não é realizada",
                (
                    f"Perfil solicitado: {max((int(a.get('janela_solicitada_dias') or 0) for a in acoes), default=0)} dias; "
                    f"dias da série atual observados: {max((int(a.get('dias_serie_atual') or 0) for a in acoes), default=0)}."
                ),
                (f"{perfis_antigos} recomendações foram geradas antes da amostra mais recente da série 4P; valide-as como snapshot anterior até recalcular o perfil." if perfis_antigos else "Perfis das recomendações acompanham a amostra mais recente da série 4P."),
                "recomendações REQUIRES_VALIDATION são exibidas, mas não entram na economia identificada",
            ],
        },
        data={
            "resumo": {
                "por_estado": resumo_estados,
                "quantidade_validas": sum(1 for a in visiveis if a["verdict"] == "VALID"),
                "quantidade_requer_validacao": sum(1 for a in visiveis if a["verdict"] == "REQUIRES_VALIDATION"),
                "economia_identificada_mensal": round(
                    sum(float(a.get("economia_mensal") or 0.0) for a in visiveis if a["verdict"] == "VALID" and a["status_revisao"] != "REJECTED"), 2
                ),
                "quantidade": len(visiveis),
                "perfis_antigos": perfis_antigos,
                "janela_solicitada_dias": max((int(a.get("janela_solicitada_dias") or 0) for a in visiveis), default=0),
                "dias_observados": max((int(a.get("dias_serie_atual") or 0) for a in visiveis), default=0),
                "perfil_atualizado_em": max((str(a.get("perfil_atualizado_em") or "") for a in visiveis), default="") or None,
            },
            "acoes": visiveis,
        },
    )


_NAV_DIMENSIONS: dict[str, str] = {
    "service": "service_key",
    "region": "region_key",
    "account": "sub_account_id",
    "resource_type": "resource_type_key",
}


@router.get("/navigate")
async def finops_navigate(
    dim: str = Query("service"),
    period: str | None = None,
    service: str | None = None,
    region: str | None = None,
    resource_type: str | None = None,
    account: str | None = None,
    user: User = Depends(require("finops", "read")),
) -> dict[str, Any]:
    """Onde está o custo, com drill-down em um clique.

    `dim=service` lista serviços; `dim=region&service=<serviço>` lista regiões
    daquele serviço; `dim=resource_type&service=<s>&region=<r>` lista tipos de
    recurso. O caminho dos ancestrais vem pelos parâmetros nomeados, e cada nível
    traz custo, % do total e contagem de recursos — pareto pronto, sem cálculo no
    navegador.
    """
    if dim not in _NAV_DIMENSIONS:
        raise HTTPException(
            status_code=400, detail=f"dim aceita: {', '.join(sorted(_NAV_DIMENSIONS))}"
        )
    coluna = _NAV_DIMENSIONS[dim]
    valores = {"service": service, "region": region,
               "account": account, "resource_type": resource_type}
    condicoes = [
        f"{_NAV_DIMENSIONS[d]} = {_quote_literal(v)}"
        for d, v in valores.items()
        if d != dim and v not in (None, "")
    ]
    base = _where(
        filters=C2_FILTERS,
        values={"period": period},
    )
    cond = " AND ".join([base] + condicoes) if condicoes else base

    total_linhas, _ = await _run(
        f"SELECT ROUND(SUM(effective_cost), 2) AS total "
        f"FROM iceberg.{layers.GOLD}.{C2_TABLE} WHERE {cond}",
        timeout_s=_SUMMARY_TIMEOUT,
    )
    total = float((total_linhas[0] if total_linhas else {}).get("total") or 0)
    sql = f"""
SELECT CAST({coluna} AS VARCHAR) AS chave,
       ROUND(SUM(effective_cost), 2) AS custo,
       SUM(rows) AS linhas_origem,
       COUNT(DISTINCT CASE WHEN resource_id_status = 'VALIDO'
              THEN canonical_resource_id END) AS recursos
FROM iceberg.{layers.GOLD}.{C2_TABLE}
WHERE {cond}
GROUP BY 1
ORDER BY 2 DESC
LIMIT 25
"""
    linhas, ms = await _run(sql, timeout_s=_SUMMARY_TIMEOUT)
    moeda = "BRL"
    proximo = {"service": "region", "region": "resource_type",
               "resource_type": "account", "account": "service"}.get(dim)
    return _envelope(
        scope={"dim": dim, "period": period,
               "moeda": moeda,
               "filtros": {k: v for k, v in valores.items() if v and k != dim}},
        currency=moeda,
        quality={"coverage_pct": None, "warnings": []},
        data={
            "nivel": dim,
            "proximo_nivel": proximo,
            "total": total,
            "itens": [
                {
                    "chave": r.get("chave") or "UNKNOWN",
                    "custo": float(r.get("custo") or 0),
                    "pct": (
                        round(100.0 * float(r.get("custo") or 0) / total, 2)
                        if total else None
                    ),
                    "linhas_origem": int(r.get("linhas_origem") or 0),
                    "recursos": int(r.get("recursos") or 0),
                }
                for r in linhas
            ],
        },
        elapsed_ms=ms,
    )
