"""Materializa um cubo Gold pré-agregado (dest_dm_cost_<code>) a partir da Silver.

Uma `gold_rule` seleciona uma ou mais tabelas **Silver** já normalizadas
(`resource_cost` para os cubos financeiros; `dest_billing_cloud` e o espelho
`dest_billing_cloud_planning` para os cubos organizacionais legados), aplica os
filtros declarados e pré-agrega nas dimensões analíticas para que a API e o
dashboard leiam uma tabela fina.

O docstring anterior chamava essas fontes de "Gold tables". A camada errada no
texto levou a supor que já existia um cruzamento que nunca foi feito; as fontes
são Silver detalhada, e o cruzamento é o que este materializador produz.

Toda publicação passa por `gold_governance.publish_gold_cube`, que registra o
manifesto (fontes, hash do SQL, linhas, totais, rollback) e troca atomicamente.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.core.hierarchy import hierarchy_columns_in, is_hierarchy_column
from app.models.destination import DestinationTable
from app.models.source_connection import SourceConnection
from app.models.gold_mart_rule import GoldMartRule, GoldMartRuleSource
from app.services import iceberg_service as iceberg
from app.services.trino_service import execute
from app.services import lakehouse_layers as layers

logger = logging.getLogger(__name__)

# Os gold cubes vivem no namespace canônico GOLD (hoje `gold`), separado
# das cubos Gold (iceberg.gold.*). O materializador cria/swap aqui; os dashboards e
# a API consultam {GOLD}.dest_dm_cost_*.
GOLD = layers.GOLD

_AI_AUXILIARY_GOLD_CUBES = {
    "ai_consumption_daily": "dest_dm_ai_consumption_daily",
    "ai_license_snapshot": "dest_dm_ai_license_snapshot",
    "copilot_usage_daily": "dest_dm_copilot_usage_daily",
    "copilot_license_snapshot": "dest_dm_copilot_license_snapshot",
    "copilot_user_period": "dest_dm_copilot_user_period",
}


async def materialize_dollar_evolution_cube(session: Any, *, run_id: int) -> int:
    """Build monthly Azure FX rates from observed FOCUS exchange rates only."""
    source = "dest_billing_cloud"
    schema = await load_gold_schema(source)
    period = schema.get("_billing_period") or schema.get("billingperiodstart")
    if not period:
        raise RuntimeError("Silver FOCUS sem período de faturamento para o cubo de câmbio")
    provider = schema.get("serviceprovidername")
    exchange_rate = schema.get("source_exchange_rate")
    if not exchange_rate:
        # Fontes sem conversão cambial (como uma base apenas OCI em BRL) não
        # precisam do cubo auxiliar de câmbio para publicar a visão de custos.
        logger.info("Cubo de câmbio ignorado: Silver FOCUS sem source_exchange_rate")
        return 0
    period_text = f"CAST({_quote_ident(period)} AS VARCHAR)"
    month_expr = (
        f"CASE WHEN regexp_like({period_text}, '^[0-9]{{6}}') "
        f"THEN substr({period_text}, 1, 6) "
        f"ELSE date_format(try_cast({period_text} AS TIMESTAMP), '%Y%m') END"
    )
    # Linhas Azure pelo provedor FOCUS, não pelo código das conexões.
    azure_filter = (
        f" AND lower(CAST({_quote_ident(provider)} AS VARCHAR)) LIKE 'microsoft%'" if provider else ""
    )
    sql = f"""
      SELECT {month_expr} AS billing_month,
             AVG(DISTINCT TRY_CAST({_quote_ident(exchange_rate)} AS DOUBLE)) AS taxa_brl_usd
      FROM iceberg.silver.{source}
      WHERE TRY_CAST({_quote_ident(exchange_rate)} AS DOUBLE) > 1{azure_filter}
      GROUP BY 1 ORDER BY 1
    """
    rule = session.query(GoldMartRule).filter(GoldMartRule.code == "evolucao-dolar").first()
    if rule is None:
        rule = GoldMartRule(code="evolucao-dolar", name="Evolução do Dólar", active=True)
        session.add(rule)
    rule.name = "Evolução do Dólar Azure"
    rule.description = "Cubo mensal Azure com média das taxas source_exchange_rate observadas, sem medidas de fatura ou realizado."
    rule.active = True
    rule.sql_template = sql.strip()
    rule.dimensions = ["billing_month"]
    rule.measure_columns = ["taxa_brl_usd"]
    rule.materialization_table = "dest_dm_cost_evolucao_dolar"
    session.flush()
    return await _materialize_rule(rule, run_id=run_id)

_FOCUS_FIELD_ALIASES = {
    "projectname": "x_Project",
    "project": "x_Project",
    "reservationname": "source_reservation_name",
    # A planning (espelho de forecast) não expõe PricingCategory; o filtro de
    # classificação aplica-se pelo equivalente mais próximo (servicename).
    "pricingcategory": "servicename",
}


def _alias_for_missing(schema: dict[str, str], requested: str) -> str | None:
    # Resolve a filter field that a table lacks onto an equivalent column.
    key = str(requested or "").strip().lower()
    if is_hierarchy_column(key):
        candidate = schema.get(key)
        if candidate:
            return candidate
    return None


def rule_target_table(code: str) -> str:
    """dest_dm_cost_<gold_code> - the original code is preserved in the rule;
    the physical table name uses an underscore-safe variant (hyphens would
    break unquoted Iceberg identifiers in Trino SQL)."""
    normalized = normalize_code(code)
    if normalized in _AI_AUXILIARY_GOLD_CUBES:
        return _AI_AUXILIARY_GOLD_CUBES[normalized]
    safe = normalized.replace("-", "_")
    return f"dest_dm_cost_{safe}"


def normalize_code(raw: str) -> str:
    """Lowercase, strip, and collapse unsafe characters for the table name."""
    value = str(raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9_\-]+", "_", value).strip("_")
    return value or "gold"


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _schema_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        if name:
            mapping[name.lower()] = name
    return mapping


def _resolve_column(schema: dict[str, str], name: str) -> str | None:
    key = str(name or "").lower()
    return schema.get(key)


def _normalized_conditions(schema: dict[str, str], raw_conditions: Any) -> tuple[list[dict[str, Any]], list[str]]:
    conditions = raw_conditions or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    normalized, missing = [], []
    for condition in conditions:
        item = dict(condition)
        requested = str(item.get("column") or item.get("field") or "")
        alias = _FOCUS_FIELD_ALIASES.get(requested.lower())
        if requested.lower() not in schema and alias and alias.lower() in schema:
            item["column"] = alias
            item.pop("field", None)
        if str(item.get("column") or item.get("field") or "").lower() not in schema:
            fallback = _alias_for_missing(schema, requested)
            if fallback:
                item["column"] = fallback
                item.pop("field", None)
            else:
                missing.append(requested)
        normalized.append(item)
    return normalized, missing


def _filter_clause(schema: dict[str, str], conditions: list[dict[str, Any]], table_alias: str | None = None) -> str:
    clauses: list[str] = []
    for item in conditions or []:
        column = _resolve_column(schema, item.get("column") or item.get("field") or "")
        if not column:
            continue
        operator = str(item.get("operator") or item.get("op") or "IN").upper()
        values = item.get("values")
        if values is None and item.get("value") is not None:
            values = [item.get("value")]
        values = [str(v) for v in (values or []) if v is not None and str(v).strip() != ""]
        col_ref = f"{table_alias}.{_quote_ident(column)}" if table_alias else _quote_ident(column)
        col_sql = f"lower(CAST({col_ref} AS VARCHAR))"
        logic = str(item.get("logic") or "AND").upper()
        connector = logic if logic in {"AND", "OR"} else "AND"
        prefix = connector if clauses else ""
        if operator in {"EMPTY", "IS_EMPTY"}:
            clauses.append(f"{prefix} ({col_ref} IS NULL OR TRIM(CAST({col_ref} AS VARCHAR)) = '')")
        elif operator in {"NOT_EMPTY", "IS_NOT_EMPTY"}:
            clauses.append(f"{prefix} ({col_ref} IS NOT NULL AND TRIM(CAST({col_ref} AS VARCHAR)) <> '')")
        elif operator in {"CONTAINS", "~"} and values:
            likes = " OR ".join(f"{col_sql} LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"{prefix} ({likes})")
        elif operator in {"NOT_CONTAINS"} and values:
            likes = " AND ".join(f"{col_sql} NOT LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"{prefix} ({likes})")
        elif operator in {"NOT_IN", "!="} and values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"{prefix} ({col_sql} NOT IN ({literals}) OR {col_ref} IS NULL)")
        elif operator in {"=", "=="} and values:
            clauses.append(f"{prefix} {col_sql} = {_quote_literal(values[0].lower())}")
        elif values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"{prefix} {col_sql} IN ({literals})")
    return " ".join(clauses) if clauses else "1=1"


def _source_clause(schema: dict[str, str], source: GoldMartRuleSource, gold_table: str | None = None, table_alias: str | None = None) -> str:
    raw_filter = source.filter_json or {}
    raw_conditions = raw_filter if isinstance(raw_filter, list) else raw_filter.get("conditions", [])
    conditions, missing = _normalized_conditions(schema, raw_conditions)
    if missing:
        gold = gold_table or source.gold_table or "?"
        # Planning is a deliberately narrow Silver mirror (period, measure,
        # type, cloud and hierarchy).  A rule may also union it with FOCUS;
        # dimensions that only exist on FOCUS cannot be evaluated on planning.
        # Treat those predicates as unconstrained for the planning leg instead
        # of aborting the entire Gold cube.
        if str(gold).lower().endswith("_planning"):
            return "1=1"
        # Sugere colunas equivalentes que a Gold realmente possui, para o usuário
        # ajustar o filtro ou a regra (ex.: planning tem subaccountname, não subaccountid).
        existing = sorted({m.lower(): schema.get(m.lower()) for m in missing if m.lower() in schema})
        hints = [f"'{m}' existe como '{schema.get(m.lower())}'" for m in missing if m.lower() in schema]
        others = [f"'{m}' (a Gold tem: {', '.join(sorted(schema)[:6])}...)" for m in missing if m.lower() not in schema]
        detail = "; ".join(hints + others)
        raise RuntimeError(
            f"Filtro da regra não aplicável à Gold '{gold}' (fonte #{source.id}): "
            f"campo(s) {', '.join(missing)} não existe(m) nessa tabela. "
            f"O filtro vale para TODAS as cubos Gold unidas na fonte — se a coluna só existe na "
            f"FOCUS (ex.: subaccountid), a planning/espelho precisa dela ou o filtro deve "
            f"usar uma coluna comum (ex.: subaccountname). Detalhe: {detail}."
        )
    return _filter_clause(schema, conditions, table_alias=table_alias)


def _period_column(schema: dict[str, str]) -> str | None:
    return _resolve_column(schema, "_billing_period") or _resolve_column(schema, "period")


def _numeric_columns(schema: dict[str, str]) -> set[str]:
    """Column names (lowercased) whose Trino type is numeric - excluded from GROUP BY."""
    return {
        name.lower() for name, typ in schema.items()
        if any(token in str(typ).lower() for token in ("int", "double", "real", "decimal", "float", "bigint"))
    }


async def load_gold_schema(gold_table: str) -> dict[str, str]:
    rows = await execute(f"DESCRIBE iceberg.silver.{gold_table}", timeout_s=30)
    return _schema_map(rows)


async def load_gold_schema_typed(gold_table: str) -> dict[str, dict[str, str]]:
    """DESCRIBE a Gold and keep the REAL column type (double, varchar, ...).

    load_gold_schema discards types (only name resolution), which made
    /gold/gold-schema report type = column name and broke the
    numeric-measure filter in the pivot editor. This variant preserves it.
    """
    rows = await execute(f"DESCRIBE iceberg.silver.{gold_table}", timeout_s=30)
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        typ = str(row.get("Type") or row.get("type") or row.get("data_type") or "")
        if name:
            out[name.lower()] = {"name": name, "type": typ}
    return out


def _value_expr(schema: dict[str, str], value_column: str) -> str:
    col = _resolve_column(schema, value_column)
    if not col:
        # Measure absent in this source (e.g. planning has only 'realizado'):
        # contribute 0 instead of failing with an unresolvable column.
        return "CAST(0 AS DOUBLE)"
    return f"COALESCE(TRY_CAST({_quote_ident(col)} AS DOUBLE), 0)"


async def preview_source(*, gold_table: str, filter_json: dict[str, Any] | None, value_column: str = "realizado") -> dict[str, Any]:
    schema = await load_gold_schema(gold_table)
    value_col = value_column if value_column in {"realizado", "BilledCost", "EffectiveCost"} else "realizado"
    conditions = (filter_json or {}).get("conditions") or filter_json or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    where = _filter_clause(schema, list(conditions))
    sql = f"""
      SELECT COUNT(*) AS row_count,
             SUM({_value_expr(schema, value_col)}) AS total_value
      FROM iceberg.silver.{gold_table}
      WHERE {where}
    """
    rows = await execute(sql, timeout_s=120)
    row = rows[0] if rows else {}
    return {
        "gold_table": gold_table,
        "row_count": int(row.get("row_count") or 0),
        "total_value": float(row.get("total_value") or 0),
        "value_column": value_col,
    }


# Marca na lista abaixo: entra cada hierarquia_N existente na fonte, em ordem.
_ALL_HIERARCHY_LEVELS = "hierarquia_*"

# Analytic dimensions used by default when a rule does not declare its own.
# Grouping by high-cardinality columns (ResourceId, tags, custom_fields...)
# neither shrinks the table nor helps dashboards and explodes Trino memory on
# multi-million-row cubos Gold, so the default keeps the drill-down dimensions that
# dashboards actually use.
_ANALYTIC_DIMENSIONS = (
    "_billing_period",
    "period",
    "tipo",
    "cloud_origem",
    _ALL_HIERARCHY_LEVELS,
    "servicecategory",
    "servicename",
    "servicesubcategory",
    "skumeter",
    "subaccountname",
    "centro_custo",
    "regionname",
    "chargecategory",
    # FOCUS marketplace/provider dimensions are part of the detailed Silver
    # schema and must remain available in the executive Gold cube as well.
    "marketplaceproductname",
    "marketplacebillingmodel",
    "serviceprovidername",
    "source_connection_id",
)


def _merged_lookup(schema_by_gold: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """(lowercase name, physical name) union across sources, first source wins."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for schema in schema_by_gold.values():
        for physical in schema.values():
            key = physical.lower()
            if key not in seen:
                seen.add(key)
                out.append((key, physical))
    return out


def _default_dimensions(schema_by_gold: dict[str, dict[str, str]]) -> list[str]:
    """Analytic dimensions present in at least one source (period first)."""
    lookup = {key: physical for key, physical in _merged_lookup(schema_by_gold)}
    dims: list[str] = []
    for logical in _ANALYTIC_DIMENSIONS:
        names = hierarchy_columns_in(lookup) if logical == _ALL_HIERARCHY_LEVELS else [logical.lower()]
        for name in names:
            physical = lookup.get(name)
            if physical and physical not in dims:
                dims.append(physical)
    return dims


def _merged_dimensions(schema_by_gold: dict[str, dict[str, str]], configured: list[str] | None) -> list[str]:
    """Resolve the GROUP BY dimensions for a rule.

    ``configured`` (rule.dimensions) is honored when non-empty; otherwise the
    analytic default is used. Every dimension must resolve in at least one
    source; the period column is always placed first for cheap partitioning.
    """
    lookup = {key: physical for key, physical in _merged_lookup(schema_by_gold)}
    if configured:
        dims: list[str] = []
        for logical in configured:
            physical = lookup.get(str(logical).lower())
            if physical and physical not in dims:
                dims.append(physical)
    else:
        dims = _default_dimensions(schema_by_gold)
    period_name = None
    for schema in schema_by_gold.values():
        p = _period_column(schema)
        if p:
            period_name = p
            break
    if period_name:
        if period_name in dims:
            dims.remove(period_name)
        dims.insert(0, period_name)
    return dims


def _side_select(gold: str, schema: dict[str, str], dims: list[str], measure_aliases: list[tuple[str, str]], percent: float, period_physical: dict[str, str], centro_map: dict[str, str] | None = None, table_alias: str | None = None) -> str:
    # Build one side of the UNION ALL. measure_aliases is (measure, alias)
    # with each measure scaled by percent and projected as _m0, _m1...
    exprs: list[str] = []
    for dim in dims:
        key = dim.lower()
        resolved = _resolve_column(schema, dim)
        if resolved:
            ref = f"{table_alias}.{_quote_ident(resolved)}" if table_alias else _quote_ident(resolved)
            if key == "tipo" and "rateio_medida" in schema:
                measure_ref = f"{table_alias}.{_quote_ident(schema['rateio_medida'])}" if table_alias else _quote_ident(schema["rateio_medida"])
                exprs.append(
                    f"CASE WHEN lower(CAST({ref} AS VARCHAR)) = 'shared_cost' "
                    f"THEN CASE lower(CAST({measure_ref} AS VARCHAR)) "
                    f"WHEN 'realizado' THEN 'realizado' WHEN 'forecast' THEN 'forecast' "
                    f"ELSE 'rateio' END ELSE CAST({ref} AS VARCHAR) END AS {_quote_ident(dim)}"
                )
            else:
                exprs.append(f"CAST({ref} AS VARCHAR) AS {_quote_ident(dim)}")
        elif key in {"period", "_billing_period"} and period_physical.get(gold) and period_physical[gold] != dim:
            ref = f"{table_alias}.{_quote_ident(period_physical[gold])}" if table_alias else _quote_ident(period_physical[gold])
            exprs.append(f"CAST({ref} AS VARCHAR) AS {_quote_ident(dim)}")
        elif key == "centro_custo" and centro_map and "subaccountname" in schema:
            # Fonte sem centro_custo (ex.: espelho de planning): herda o
            # centro de custo da subconta através do mapa derivado do Gold.
            exprs.append(f"CAST(cm.{_quote_ident(centro_map['cc'])} AS VARCHAR) AS {_quote_ident(dim)}")
        else:
            exprs.append(f"CAST(NULL AS VARCHAR) AS {_quote_ident(dim)}")
    for measure, alias in measure_aliases:
        physical = _resolve_column(schema, measure) or measure
        measure_expr = _value_expr(schema, measure).replace(_quote_ident(physical), f"{table_alias}.{_quote_ident(physical)}" if table_alias else _quote_ident(physical), 1)
        scaled = f"({measure_expr}) * {percent}" if percent != 1.0 else measure_expr
        exprs.append(f"{scaled} AS {alias}")
    return ", ".join(exprs)


async def _materialize_rule(
    rule: GoldMartRule,
    *,
    run_id: int,
    progress: Any | None = None,
    billing_periods: list[str] | None = None,
) -> int:
    """Build the pre-aggregated Gold table for one rule and swap it atomically."""
    table = rule_target_table(rule.code)

    # SQL custom: o usuário fornece a query inteira. Ela deve gerar as
    # dimensões e medidas desejadas; nós apenas criamos a tabela no namespace
    # gold e fazemos o swap atômico.
    if getattr(rule, "sql_template", None) and str(rule.sql_template).strip():
        if billing_periods:
            raise ValueError("Materialização por período não é suportada para regras SQL customizadas")
        snapshot_mode = bool(getattr(rule, "snapshot_mode", False))
        snapshot_col = str(getattr(rule, "snapshot_col", "_snapshot_date") or "_snapshot_date")
        staging = f"{table}_stg_{time.time_ns()}"
        iceberg.ensure_namespace(GOLD)
        # Em snapshot mode, cada execução adiciona uma cópia datada (append),
        # preservando o histórico para comparar dias (ex.: delta de tags).
        if snapshot_mode:
            create_sql = (
                f"CREATE TABLE iceberg.{GOLD}.{staging} AS "
                f"SELECT t.*, CAST(CURRENT_DATE AS VARCHAR) AS {_quote_ident(snapshot_col)} "
                f"FROM ({rule.sql_template.strip()}) t"
            )
        else:
            create_sql = f"CREATE TABLE iceberg.{GOLD}.{staging} AS {rule.sql_template.strip()}"
        try:
            if progress:
                progress(0, 1, f"Materializando {rule.code} via SQL custom (snapshot={snapshot_mode})")
            await execute(create_sql, timeout_s=1800)
            result = await execute(f"SELECT COUNT(*) AS row_count FROM iceberg.{GOLD}.{staging}", timeout_s=180)
            rows = int((result[0] if result else {}).get("row_count") or 0)
            if snapshot_mode:
                if rows:
                    snapshot_val = iceberg.staging_column_single_value(
                        staging, snapshot_col, namespace=GOLD
                    )
                    if iceberg.table_exists(table, namespace=GOLD):
                        iceberg.replace_staged_cube_snapshot_day(
                            table,
                            staging,
                            snapshot_col=snapshot_col,
                            snapshot_value=snapshot_val,
                            known_row_count=rows,
                        )
                    else:
                        iceberg.replace_staged_cube(
                            table, staging, namespace=GOLD, known_row_count=rows, purge_snapshots=False
                        )
                else:
                    iceberg.cleanup_staged_table(staging, namespace=GOLD)
                await _record_rule_manifest(rule, rows=rows, sql=rule.sql_template, run_id=run_id)
                return rows
            if rows:
                iceberg.replace_staged_cube(table, staging, namespace=GOLD, known_row_count=rows)
            else:
                iceberg.cleanup_staged_table(staging, namespace=GOLD)
            await _record_rule_manifest(rule, rows=rows, sql=rule.sql_template, run_id=run_id)
            return rows
        except Exception:
            try:
                iceberg.cleanup_staged_table(staging, namespace=GOLD)
            except Exception:
                pass
            raise

    schema_by_gold: dict[str, dict[str, str]] = {}
    for source in rule.sources or []:
        golds = list(getattr(source, "gold_tables", None) or [])
        if not golds and getattr(source, "gold_table", None):
            golds = [source.gold_table]
        for gold in golds:
            schema_by_gold[gold] = await load_gold_schema(gold)

    dims = _merged_dimensions(schema_by_gold, list(getattr(rule, "dimensions", None) or []))
    period_physical = {gold: (_period_column(schema) or "_billing_period") for gold, schema in schema_by_gold.items()}

    # Lookup de centro de custo por subconta: quando a Gold inclui a dimensão
    # centro_custo mas alguma fonte não a possui (ex.: espelho de planning),
    # o valor é herdado do Gold que tem a coluna, casando por subaccountname.
    centro_map: dict[str, str] | None = None
    if "centro_custo" in dims:
        for gold, schema in schema_by_gold.items():
            if "centro_custo" in schema and "subaccountname" in schema:
                centro_map = {"gold": gold, "cc": schema["centro_custo"], "sa": schema["subaccountname"]}
                break

    # Pivot-style measures: rule.measure_columns wins; else the first
    # source value_column; else realized.
    measures: list[str] = list(getattr(rule, "measure_columns", None) or [])
    if not measures:
        value_col = (rule.sources or [])[0].value_column
        if value_col in {"realizado", "BilledCost", "EffectiveCost"}:
            measures = [value_col]
        else:
            measures = ["realizado"]
    measure_aliases = [(m, f"_m{i}") for i, m in enumerate(measures)]

    parts: list[str] = []
    uses_centro_map = False
    for source in rule.sources or []:
        # A source may bind one or more Gold tables with the same structure;
        # the same filters/value/percent apply to each table and the results
        # are unioned into one clean dataset before aggregation.
        golds = list(getattr(source, "gold_tables", None) or [])
        if not golds and getattr(source, "gold_table", None):
            golds = [source.gold_table]
        for gold in golds:
            schema = schema_by_gold.get(gold)
            if schema is None:
                schema = await load_gold_schema(gold)
                schema_by_gold[gold] = schema
            percent = float(source.percent or 100) / 100.0
            use_map = (
                centro_map is not None
                and "centro_custo" not in schema
                and "subaccountname" in schema
                and centro_map["gold"] != gold
            )
            table_alias = "src" if use_map else None
            where = _source_clause(schema, source, gold_table=gold, table_alias=table_alias)
            if billing_periods:
                source_period = period_physical.get(gold)
                if not source_period:
                    raise ValueError(f"Fonte Silver sem coluna de período: {gold}")
                period_ref = f"{table_alias}." if table_alias else ""
                period_values = ", ".join(_quote_literal(str(value)) for value in billing_periods)
                period_filter = (
                    f"CAST({period_ref}{_quote_ident(source_period)} AS VARCHAR) "
                    f"IN ({period_values})"
                )
                where = f"({where}) AND ({period_filter})"
            select_exprs = _side_select(
                gold, schema, dims, measure_aliases, percent, period_physical,
                centro_map=centro_map if use_map else None,
                table_alias=table_alias,
            )
            if use_map:
                uses_centro_map = True
                sa = schema["subaccountname"]
                join = (
                    f"LEFT JOIN _gold_centro_map cm ON "
                    f"TRIM(lower(CAST(src.{_quote_ident(sa)} AS VARCHAR))) = "
                    f"TRIM(lower(CAST(cm.cc_subaccount AS VARCHAR)))"
                )
                parts.append(f"SELECT {select_exprs} FROM iceberg.silver.{gold} AS src {join} WHERE {where}")
            else:
                parts.append(f"SELECT {select_exprs} FROM iceberg.silver.{gold} WHERE {where}")

    if not parts:
        return 0
    union_sql = " UNION ALL ".join(f"({part})" for part in parts)
    group_by = ", ".join(_quote_ident(dim) for dim in dims)
    measure_sums = ", ".join(f"SUM({alias}) AS {_quote_ident(measure)}" for measure, alias in measure_aliases)
    if uses_centro_map:
        map_sql = (
            f"SELECT {_quote_ident(centro_map['sa'])} AS cc_subaccount, "
            f"max({_quote_ident(centro_map['cc'])}) AS {_quote_ident(centro_map['cc'])} "
            f"FROM iceberg.silver.{centro_map['gold']} "
            f"WHERE {_quote_ident(centro_map['cc'])} IS NOT NULL "
            f"AND TRIM(CAST({_quote_ident(centro_map['cc'])} AS VARCHAR)) <> '' "
            f"GROUP BY {_quote_ident(centro_map['sa'])}"
        )
        full_sql = (
            f"WITH _gold_centro_map AS ({map_sql}) "
            f"SELECT {group_by}, {measure_sums} FROM ({union_sql}) AS gold_src GROUP BY {group_by}"
        )
    else:
        full_sql = f"SELECT {group_by}, {measure_sums} FROM ({union_sql}) AS gold_src GROUP BY {group_by}"

    staging = f"{table}_stg_{time.time_ns()}"
    period = period_physical.get((rule.sources or [])[0].gold_table) if rule.sources else None
    # Garante que o namespace dos gold cubes exista antes do CREATE.
    iceberg.ensure_namespace(GOLD)
    create_sql = f"CREATE TABLE iceberg.{GOLD}.{staging} AS {full_sql}"
    if period:
        create_sql = f"CREATE TABLE iceberg.{GOLD}.{staging} WITH (partitioning = ARRAY['{period}']) AS {full_sql}"
    try:
        if progress:
            progress(0, 1, f"Agregando {rule.code} a partir de {len(rule.sources or [])} fonte(s) Silver")
        await execute(create_sql, timeout_s=1800)
        result = await execute(f"SELECT COUNT(*) AS row_count FROM iceberg.{GOLD}.{staging}", timeout_s=180)
        rows = int((result[0] if result else {}).get("row_count") or 0)
        if rows or billing_periods:
            delete_filter = None
            if billing_periods:
                from pyiceberg.expressions import In

                delete_filter = In("_billing_period", tuple(str(value) for value in billing_periods))
            iceberg.replace_staged_cube(
                table,
                staging,
                namespace=GOLD,
                known_row_count=rows,
                delete_filter=delete_filter,
            )
        else:
            iceberg.cleanup_staged_table(staging, namespace=GOLD)
        await _record_rule_manifest(rule, rows=rows, sql=full_sql, run_id=run_id)
        return rows
    except Exception:
        try:
            iceberg.cleanup_staged_table(staging, namespace=GOLD)
        except Exception:
            pass
        raise


async def _record_rule_manifest(
    rule: GoldMartRule, *, rows: int, sql: str, run_id: int | None
) -> None:
    """Registra no manifesto a materialização governada de uma regra.

    Um único ponto para os três caminhos de `_materialize_rule` (SQL custom,
    snapshot mode e pivô), para que nenhum deles publique sem rastro.
    """
    from app.services.gold_governance import ManifestEntry, record_manifest, sql_fingerprint

    entry = ManifestEntry(
        cube_code=normalize_code(rule.code),
        physical_table=rule_target_table(rule.code),
        run_id=run_id,
        rule_id=getattr(rule, "id", None),
        rows_written=rows,
        sql_hash=sql_fingerprint(sql),
        status="SUCCESS" if rows else "PARTIAL",
        reason=None if rows else "sem linhas",
    )
    await record_manifest(entry)


async def materialize_gold_cubes(
    session: Any,
    *,
    run_id: int,
    progress: Any | None = None,
    rule_ids: list[int] | None = None,
    billing_periods: list[str] | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone
    from sqlalchemy.orm import selectinload

    query = session.query(GoldMartRule).options(selectinload(GoldMartRule.sources)).filter(GoldMartRule.active == True)  # noqa: E712
    if rule_ids:
        query = query.filter(GoldMartRule.id.in_(rule_ids))
    rules = query.all()
    if billing_periods and any(
        getattr(rule, "materializer_key", None)
        or getattr(rule, "snapshot_mode", False)
        or (getattr(rule, "sql_template", None) and str(rule.sql_template).strip())
        for rule in rules
    ):
        raise ValueError("Materialização por período exige regras pivot sem SQL customizado")

    # ``copilot_user_period`` is derived from the already aggregated Copilot
    # usage Gold.  Rebuilding only ``copilot_usage_daily`` left the dashboard
    # drill-down with a stale user/credit snapshot (for example, August after
    # September usage was loaded).  Model that dependency in the materializer
    # itself so every execution path — API, scheduler and worker chaining —
    # keeps both tables consistent.
    if rule_ids and any(normalize_code(rule.code) == "copilot_usage_daily" for rule in rules):
        user_period = (
            session.query(GoldMartRule)
            .options(selectinload(GoldMartRule.sources))
            .filter(
                GoldMartRule.active == True,  # noqa: E712
                GoldMartRule.code == "copilot_user_period",
            )
            .first()
        )
        if user_period and all(existing.id != user_period.id for existing in rules):
            rules.append(user_period)

    # R3: RUNNING órfão não pode ficar aberto para sempre. Um worker morto no meio
    # de um build deixa um RUNNING sem término; expirar antes de começar evita que
    # a leitura de "o que está materializando agora" minta.
    from app.services.gold_governance import expire_stale_running_manifests_sync

    try:
        expirados = expire_stale_running_manifests_sync()
        if expirados:
            logger.info("Manifestos RUNNING órfãos marcados ABANDONED: %d", expirados)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao expirar RUNNING órfãos: %s", exc)

    details: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    written = 0

    # Regras com `materializer_key` têm construção multi-estágio e são resolvidas
    # pela allowlist `gold_materializers`. Elas saem do laço de pivô abaixo: passar
    # um cubo `finops_*` pelo caminho genérico publicaria uma tabela diferente da
    # que o nome promete.
    keyed = [r for r in rules if getattr(r, "materializer_key", None)]
    rules = [r for r in rules if not getattr(r, "materializer_key", None)]

    if keyed:
        from app.services.gold_materializers import (
            execution_order,
            is_registered,
            runner_for,
            spec_for,
        )

        chaves = [str(r.materializer_key) for r in keyed]
        invalidas = [c for c in chaves if not is_registered(c)]
        if invalidas:
            raise KeyError(
                "materializer_key fora da allowlist: " + ", ".join(sorted(set(invalidas)))
            )
        try:
            if progress:
                progress(0, len(chaves), "Materializando cubos multi-estágio")
            # Executa chave a chave (e não em lote) para que a falha de um cubo não
            # apague o resultado dos que já publicaram. A ordem continua sendo a de
            # dependência; o que muda é o registro.
            from app.services.gold_governance import cost_baseline

            financeiros = [
                c for c in chaves
                if is_registered(c) and spec_for(c).financial
            ]
            # Baseline medido **uma vez por run**: se cada materializador medisse a
            # origem na hora própria, uma carga intermediária mudaria o número entre
            # C1 e C2 e a comparação deixaria de provar a preservação.
            baseline = await cost_baseline() if financeiros else None
            estado: dict[str, dict[str, Any]] = {}
            erro: Exception | None = None
            for chave in execution_order(chaves):
                try:
                    estado[chave] = await runner_for(chave)(
                        run_id=run_id,
                        expected_total=(
                            baseline.total
                            if baseline is not None
                            else None
                        ),
                        baseline=baseline,
                        progress=progress,
                    )
                except Exception as exc:  # noqa: BLE001 — vira status da regra
                    erro = exc
                    break

            from app.services.gold_materializers import is_registered, spec_for

            def _afetada(regra: Any) -> bool:
                """Regra afetada pela falha: a que falhou ou a que depende dela.

                Marcar as **anteriores** como ERROR seria falso — elas publicaram. E
                marcar as posteriores independentes também seria: cobertura e C1 não
                dependem de C2.
                """
                if erro is None:
                    return False
                if str(regra.materializer_key) in estado:
                    return False
                return True

            for regra in keyed:
                chave = str(regra.materializer_key)
                resultado = estado.get(chave)
                regra.materialization_run_id = run_id
                regra.materialized_at = datetime.now(timezone.utc)
                # `rule_target_table` deriva `dest_dm_cost_<code>` e não vale para os
                # cubos `finops_*`, cuja tabela física é declarada na spec. Usar o
                # nome derivado registraria uma tabela que não existe.
                regra.materialization_table = spec_for(chave).physical_table
                if resultado is not None:
                    linhas = int(
                        resultado.get("rows")
                        or resultado.get("grain_rows")
                        or resultado.get("inventory_rows")
                        or 0
                    )
                    regra.materialization_rows = linhas
                    regra.materialization_status = "UPDATED" if linhas else "EMPTY"
                    regra.materialization_error = None
                    details.append(
                        {
                            "rule_id": regra.id,
                            "code": regra.code,
                            "table": regra.materialization_table,
                            "rows": linhas,
                            "materializer_key": chave,
                        }
                    )
                    written += linhas
                elif _afetada(regra):
                    regra.materialization_status = "ERROR"
                    regra.materialization_error = str(erro)[:2000]
                    failed.append(
                        {"rule_id": regra.id, "code": regra.code, "error": str(erro)[:500]}
                    )
            if erro is not None:
                session.commit()
                raise erro
        except Exception as exc:
            session.commit()
            raise

    for index, rule in enumerate(rules, start=1):
        if progress:
            progress(index - 1, len(rules), f"Materializando {rule.code}")
        try:
            rows = await _materialize_rule(
                rule,
                run_id=run_id,
                progress=progress,
                billing_periods=billing_periods,
            )
            rule.materialization_table = rule_target_table(rule.code)
            rule.materialization_rows = rows
            rule.materialization_status = "UPDATED" if rows else "EMPTY"
            rule.materialization_error = None
            rule.materialization_run_id = run_id
            rule.materialized_at = datetime.now(timezone.utc)
            details.append({"rule_id": rule.id, "code": rule.code, "table": rule_target_table(rule.code), "rows": rows})
            written += rows
        except Exception as exc:
            failed.append({"rule_id": rule.id, "code": rule.code, "error": str(exc)})
            rule.materialization_status = "ERROR"
            rule.materialization_error = str(exc)
            rule.materialization_run_id = run_id
            rule.materialized_at = datetime.now(timezone.utc)
            details.append({"rule_id": rule.id, "code": rule.code, "rows": 0, "error": str(exc)})
        if progress:
            progress(index, len(rules), f"{rule.code} - {'publicada' if rule.materialization_status != 'ERROR' else 'com erro'}")
    dollar_rule = next((rule for rule in rules if normalize_code(rule.code) == "evolucao-dolar"), None)
    if dollar_rule:
        rules = [rule for rule in rules if normalize_code(rule.code) != "evolucao-dolar"]
    should_refresh_dollar_cube = not rule_ids or any(normalize_code(rule.code) == "visao-completa" for rule in rules) or bool(rule_ids and dollar_rule and dollar_rule.id in rule_ids)
    if should_refresh_dollar_cube:
        try:
            cube_rows = await materialize_dollar_evolution_cube(session, run_id=run_id)
            if dollar_rule:
                dollar_rule.materialization_table = "dest_dm_cost_evolucao_dolar"
                dollar_rule.materialization_rows = cube_rows
                dollar_rule.materialization_status = "UPDATED" if cube_rows else "EMPTY"
                dollar_rule.materialization_error = None
                dollar_rule.materialization_run_id = run_id
                dollar_rule.materialized_at = datetime.now(timezone.utc)
            details.append({"code": "evolucao_dolar", "table": "dest_dm_cost_evolucao_dolar", "rows": cube_rows})
        except Exception as exc:  # the core Gold materialization must remain reportable
            if dollar_rule:
                dollar_rule.materialization_status = "ERROR"
                dollar_rule.materialization_error = str(exc)[:2000]
                dollar_rule.materialization_run_id = run_id
                dollar_rule.materialized_at = datetime.now(timezone.utc)
            failed.append({"code": "evolucao_dolar", "error": str(exc)[:500]})
    session.commit()
    return {
        "rules": len(rules) + len(keyed),
        "rows": written,
        "details": details,
        "failed": failed,
        "materializer_keys": [str(r.materializer_key) for r in keyed],
    }


def materialize_golds_sync(session: Any, *, run_id: int, progress: Any | None = None, rule_ids: list[int] | None = None) -> dict[str, Any]:
    from app.database import run_async_isolated
    return run_async_isolated(materialize_gold_cubes(session, run_id=run_id, progress=progress, rule_ids=rule_ids))


def enqueue_rule_materialization(
    session: Any,
    *,
    rule_ids: list[int] | None = None,
    source_tables: list[str] | None = None,
    source: str = "worker",
) -> Any | None:
    """Create a QUEUED ``gold_reprocess`` EtlTask for active rules (all or a subset).

    Used by the worker to chain the Gold materialization (visão completa =
    realizado + forecast + rateio) right after a forecast/reprocess completes.
    Skips silently when no active rule matches or a Gold task is already
    queued/running (the manual /gold/reprocess would return 409 there).

    ``session`` must be a sync SQLAlchemy session (worker SyncSession).
    The caller owns the commit.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.etl_task import EtlTask
    from app.models.gold_mart_rule import GoldMartRule

    from sqlalchemy.orm import selectinload

    query = session.query(GoldMartRule).options(selectinload(GoldMartRule.sources)).filter(GoldMartRule.active == True)  # noqa: E712
    if rule_ids:
        query = query.filter(GoldMartRule.id.in_(rule_ids))
    rules = query.all()
    if source_tables:
        wanted = {str(table).replace("-", "_").replace(".", "_").lower() for table in source_tables}
        rules = [
            rule for rule in rules
            if any(
                str(table).replace("-", "_").replace(".", "_").lower() in wanted
                for item in (rule.sources or [])
                for table in (list(item.gold_tables or []) or [item.gold_table])
            )
        ]
    if not rules:
        return None
    running = session.execute(
        select(EtlTask.id).where(
            EtlTask.task_type == "gold_reprocess",
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        )
    ).scalars().first()
    if running:
        return None
    now = datetime.now(timezone.utc)
    task = EtlTask(task_type="gold_reprocess", status="QUEUED", errors=[])
    task.phase = f"Na fila: materializar {len(rules)} cubo(s) Gold (após {source})"
    task.started_at = now
    task.total_blobs = len(rules)
    task.processed_blobs = 0
    task.rows_destination = 0
    task.result_json = {
        "event_log": [{"at": now.isoformat(), "level": "INFO", "message": task.phase}],
        "rule_ids": [rule.id for rule in rules],
        "chained_from": source,
    }
    session.add(task)
    session.flush()  # popula task.id para o phase do chamador
    return task


async def _run_keyed_rules(chaves: list[str], *, run_id: int | None = None, progress: Any | None = None) -> dict[str, Any]:
    """Executa as regras com materializer_key. Exposta para teste da fila.

    É o mesmo laço de `materialize_gold_cubes`, isolado para que o teste prove que
    o baseline é medido uma vez por run e passado a todos os financeiros.
    """
    from app.services.gold_governance import cost_baseline
    from app.services.gold_materializers import (
        execution_order,
        is_registered,
        runner_for,
        spec_for,
    )

    financeiros = [c for c in chaves if is_registered(c) and spec_for(c).financial]
    baseline = await cost_baseline() if financeiros else None
    resultados: dict[str, Any] = {}
    for chave in execution_order(chaves):
        resultados[chave] = await runner_for(chave)(
            run_id=run_id,
            expected_total=baseline.total if baseline is not None else None,
            baseline=baseline,
            progress=progress,
        )
    return resultados
