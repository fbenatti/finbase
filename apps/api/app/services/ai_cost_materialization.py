"""Materialize one pure FOCUS Gold per AI-cost rule with one source scan per period."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.models.ai_cost import AiCostLink
from app.services import lakehouse_layers as layers
from app.services.ai_cost_attribution import (
    FOCUS_TABLE, _connection_clause, _filter_clause, _quote_ident,
    load_focus_schema, link_applies,
)

logger = logging.getLogger(__name__)
_FOCUS_FIELD_ALIASES = {"projectname": "x_Project", "project": "x_Project", "reservationname": "source_reservation_name"}


def link_gold_table_name(link_id: int) -> str:
    return f"dest_ai_cost_link_{int(link_id)}"


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
            missing.append(requested)
        normalized.append(item)
    return normalized, missing


async def validate_active_relationship_rules(session: Any) -> list[dict[str, Any]]:
    from sqlalchemy.orm import selectinload
    links = session.query(AiCostLink).options(selectinload(AiCostLink.sources)).filter(AiCostLink.active == True).all()  # noqa: E712
    schema = await load_focus_schema()
    errors = []
    for link in links:
        for source in link.sources or []:
            _, missing = _normalized_conditions(schema, (source.filter_json or {}).get("conditions") or [])
            if missing:
                errors.append({"link_id": link.id, "link_name": link.name, "source_id": source.id, "missing_fields": missing})
    return errors


def drop_link_gold_table(link_id: int) -> None:
    from app.services import iceberg_service as iceberg
    table = link_gold_table_name(link_id)
    try:
        iceberg._force_delete_table(iceberg._table_id(layers.detail_layer(), table), table)
    except Exception as exc:
        logger.warning("Could not remove relationship Gold %s: %s", table, exc)


async def _focus_periods(schema: dict[str, str]) -> list[str]:
    from app.services.trino_service import execute
    period = schema.get("_billing_period") or schema.get("period")
    if not period:
        raise RuntimeError("A Gold FOCUS não possui _billing_period/period")
    qperiod = _quote_ident(period)
    rows = await execute(
        f"SELECT DISTINCT CAST({qperiod} AS VARCHAR) AS billing_period FROM iceberg.{layers.detail_layer()}.{FOCUS_TABLE} "
        f"WHERE {qperiod} IS NOT NULL ORDER BY 1", timeout_s=120,
    )
    return [str(row["billing_period"]) for row in rows if row.get("billing_period")]


def _link_clause(link: AiCostLink, schema: dict[str, str]) -> str | None:
    sources = []
    for source in link.sources or []:
        conditions, missing = _normalized_conditions(schema, (source.filter_json or {}).get("conditions") or [])
        if missing:
            raise RuntimeError(f"Campos FOCUS inexistentes na fonte {source.id}: {', '.join(missing)}")
        sources.append(" AND ".join((_connection_clause(schema, source.connection_id), _filter_clause(schema, conditions))))
    return " OR ".join(f"({clause})" for clause in sources) if sources else None


async def _build_routed_staging(
    links: list[AiCostLink], schema: dict[str, str], periods: list[str], staging: str,
    progress: Any | None,
) -> bool:
    """Read each FOCUS partition once and tag matches only in a temporary table."""
    from app.services.trino_service import execute
    period_col = schema.get("_billing_period") or schema.get("period")
    clauses = {int(link.id): _link_clause(link, schema) for link in links}
    applicable = [p for p in periods if any(clauses[int(link.id)] and link_applies(link, p) for link in links)]
    created = False
    for index, period in enumerate(applicable, start=1):
        eligible = [link for link in links if clauses[int(link.id)] and link_applies(link, period)]
        routes = ", ".join(
            f"IF(({clauses[int(link.id)]}), CAST({int(link.id)} AS BIGINT), CAST(NULL AS BIGINT))"
            for link in eligible
        )
        escaped = period.replace("'", "''")
        select_sql = (
            f"SELECT f.*, routed._ai_cost_link_id FROM iceberg.{layers.detail_layer()}.{FOCUS_TABLE} f "
            f"CROSS JOIN UNNEST(filter(ARRAY[{routes}], x -> x IS NOT NULL)) AS routed (_ai_cost_link_id) "
            f"WHERE CAST({_quote_ident(period_col)} AS VARCHAR) = '{escaped}'"
        )
        sql = (f"CREATE TABLE iceberg.gold.{staging} WITH (partitioning = ARRAY['_ai_cost_link_id']) AS {select_sql}"
               if not created else f"INSERT INTO iceberg.gold.{staging} {select_sql}")
        if progress:
            progress(0, len(links), f"Leitura única FOCUS {index}/{len(applicable)} — período {period}")
        await execute(sql, timeout_s=1800)
        created = True
    return created


async def _publish_link(link: AiCostLink, schema: dict[str, str], routed: str) -> int:
    """Publica o clone final a partir da partição roteada, nunca da FOCUS inteira.

    A publicação passa por `gold_governance.publish_gold_cube`: antes fazia
    staging + swap direto, o que criava um caminho de publicação de cubo sem
    manifesto. O vínculo (`ai_cost_link`) mantém o próprio controle de
    materialização, e o manifesto registra fontes, hash e rollback.
    """
    from app.services.gold_governance import publish_gold_cube

    table = link_gold_table_name(link.id)
    columns = ", ".join(_quote_ident(name) for name in schema.values())
    period = schema.get("_billing_period") or schema.get("period")
    select_sql = (
        f"SELECT {columns} FROM iceberg.gold.{routed} "
        f"WHERE _ai_cost_link_id = {int(link.id)}"
    )
    resultado = await publish_gold_cube(
        cube_code=f"ai_cost_link_{int(link.id)}",
        table=table,
        select_sql=select_sql,
        partition_column=period,
    )
    link.materialization_table = table
    link.materialization_rows = resultado.rows
    link.materialization_status = "UPDATED" if resultado.rows else "EMPTY"
    return resultado.rows


async def materialize_ai_cost_relationships(session: Any, *, run_id: int, progress: Any | None = None) -> dict[str, Any]:
    from datetime import datetime, timezone
    from sqlalchemy.orm import selectinload
    from app.services import iceberg_service as iceberg

    links = session.query(AiCostLink).options(selectinload(AiCostLink.sources)).filter(AiCostLink.active == True).all()  # noqa: E712
    schema = await load_focus_schema()
    periods = await _focus_periods(schema)

    # Per-rule fault isolation: a relationship whose FOCUS filters reference
    # fields absent from the current Gold is marked ERROR and skipped, while
    # the remaining relationships still publish their individual cubos Gold.
    valid_links: list[AiCostLink] = []
    failed: list[dict[str, Any]] = []
    for link in links:
        try:
            _link_clause(link, schema)
            valid_links.append(link)
        except Exception as exc:
            failed.append({"link_id": link.id, "name": link.name, "error": str(exc)})
            link.materialization_table = link_gold_table_name(link.id)
            link.materialization_status = "ERROR"
            link.materialization_error = str(exc)
            link.materialization_run_id = run_id
            link.materialized_at = datetime.now(timezone.utc)

    routed = f"dest_ai_cost_route_stg_{run_id}_{time.time_ns()}"
    created = False
    written, details = 0, []
    try:
        created = await _build_routed_staging(valid_links, schema, periods, routed, progress)
        now = datetime.now(timezone.utc)
        for index, link in enumerate(valid_links, start=1):
            if progress:
                progress(index - 1, len(links), f"Publicando {link.name} do staging classificado")
            try:
                rows = await _publish_link(link, schema, routed) if created else 0
                link.materialization_status = link.materialization_status if created else "EMPTY"
                link.materialization_error = None
                details.append({"link_id": link.id, "name": link.name, "table": link_gold_table_name(link.id), "rows_read": rows})
                written += rows
            except Exception as exc:
                failed.append({"link_id": link.id, "name": link.name, "error": str(exc)})
                link.materialization_status = "ERROR"
                link.materialization_error = str(exc)
                details.append({"link_id": link.id, "name": link.name, "table": link_gold_table_name(link.id), "rows_read": 0, "error": str(exc)})
            link.materialization_run_id = run_id
            link.materialized_at = now
            if progress:
                progress(index, len(links), f"{link.name} — {'publicada' if link.materialization_status != 'ERROR' else 'com erro'}")
        session.commit()
    finally:
        if created:
            iceberg.cleanup_staged_table(routed)
    return {
        "links": len(links),
        "periods": periods,
        "rows": written,
        "details": details,
        "failed": failed,
        "conflicts": [],
    }


def materialize_ai_cost_relationships_sync(session: Any, *, run_id: int, progress: Any | None = None) -> dict[str, Any]:
    return asyncio.run(materialize_ai_cost_relationships(session, run_id=run_id, progress=progress))
