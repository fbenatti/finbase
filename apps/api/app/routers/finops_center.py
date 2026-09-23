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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require
from app.database import get_db
from app.models.identity import User
from app.services import lakehouse_layers as layers
from app.services.finops_cache import (
    cache_key,
    get_cached,
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
    fatos: list[PeriodFacts] = []
    for conexao in conexoes:
        for periodo in periods:
            fatos.append(
                PeriodFacts(
                    connection_code=conexao,
                    billing_period=periodo,
                    dataset=C1_TABLE,
                    row_count=1,
                    days_expected=0,
                    run_status="SUCCESS",
                    is_current_period=statuses.get((conexao, periodo)) == "CURRENT_OPEN",
                )
            )
    # Reclassifica localmente: o que importa é o status medido, não o recalculado.
    coberturas = []
    for fato in fatos:
        status = statuses.get((fato.connection_code, fato.billing_period))
        if status is None:
            continue
        from app.services.period_coverage import PeriodCoverage

        coberturas.append(
            PeriodCoverage(
                connection_code=fato.connection_code,
                billing_period=fato.billing_period,
                dataset=C1_TABLE,
                status=status,
                reason="medido",
                cost=_cost_of(financial, fato.billing_period),
            )
        )

    comparaveis = [c for c in coberturas if c.status in COMPARABLE_STATUSES]
    comparaveis.sort(key=lambda c: c.billing_period)
    mom = (
        month_over_month(comparaveis[-2], comparaveis[-1])
        if len(comparaveis) >= 2
        else {"comparable": False, "reason": "menos_de_duas_competencias_comparaveis"}
    )
    run_rate = run_rate_annualized(comparaveis)
    return {
        "mom": mom,
        "run_rate": run_rate,
        "months_used": run_rate.get("months_used", 0),
        "comparable_periods": [c.billing_period for c in comparaveis],
        "current_status": (
            coberturas[-1].status if coberturas else None
        ),
    }


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
       effective_cost, list_cost, rows, charge_days, rows_without_tag
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
                {"rows_without_tag", "inventory_snapshots", "charge_days"}
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
