"""Shared-cost allocation as precomputed monthly planning rows (tipo=rateio)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.rules import Department, DepartmentSharedCost

logger = logging.getLogger(__name__)
RATEIO_TIPO = "rateio"
RATEIO_MEDIDA_ACTUAL = "realizado"
RATEIO_MEDIDA_FORECAST = "forecast"

SHARED_COST_MODES = frozenset({"consumption", "equal", "custom"})
CLOUD_BRANCHES = frozenset({"Chargeback", "Showback"})
CLOUD_PROVIDERS = frozenset({"GCP", "Azure", "AWS", "OCI", "IBM"})
PROVIDER_CLOUD_PREFIX = {
    "GCP": "gcp",
    "Azure": "azure",
    "AWS": "aws",
    "OCI": "oci",
    "IBM": "ibm",
}


def department_path(department_id: int, departments: dict[int, Department]) -> list[str]:
    path: list[str] = []
    current = departments.get(department_id)
    seen: set[int] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        path.append((current.name or "").strip())
        current = departments.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def children_map(departments: dict[int, Department]) -> dict[int | None, list[int]]:
    mapping: dict[int | None, list[int]] = {}
    for department in departments.values():
        mapping.setdefault(department.parent_id, []).append(department.id)
    return mapping


def subtree_ids(root_id: int, child_ids: dict[int | None, list[int]]) -> set[int]:
    ids: set[int] = set()
    pending = [root_id]
    while pending:
        current = pending.pop()
        if current in ids:
            continue
        ids.add(current)
        pending.extend(child_ids.get(current, []))
    return ids


def leaf_ids(root_id: int, child_ids: dict[int | None, list[int]]) -> list[int]:
    leaves: list[int] = []
    pending = [root_id]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        kids = child_ids.get(current, [])
        if not kids:
            leaves.append(current)
        else:
            pending.extend(kids)
    return leaves


def cloud_prefix_for(department: Department | None, path: list[str], departments: dict[int, Department]) -> str:
    current = department
    seen: set[int] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        prefix = (current.cloud_filter or "").strip().lower()
        if prefix:
            return prefix
        current = departments.get(current.parent_id) if current.parent_id else None
    if len(path) >= 3 and path[1] in CLOUD_BRANCHES and path[2] in CLOUD_PROVIDERS:
        return PROVIDER_CLOUD_PREFIX.get(path[2], path[2].lower())
    return ""


def driver_name_for(path: list[str], scope_path: list[str]) -> str | None:
    if len(path) <= len(scope_path) or path[: len(scope_path)] != scope_path:
        return None
    return path[len(scope_path)]


def _split_by_weight(amount: float, weights: dict[int, float]) -> tuple[dict[int, float], float]:
    active = {leaf_id: weight for leaf_id, weight in weights.items() if weight > 0}
    if not active:
        return {}, amount
    total = sum(active.values())
    if total <= 0:
        return {}, amount
    return {leaf_id: amount * weight / total for leaf_id, weight in active.items()}, 0.0


def allocate_amount(
    donor: float,
    mode: str,
    target_leaves: list[int],
    weights: dict[int, float],
    custom_targets: list[tuple[list[int], float]] | None = None,
) -> tuple[dict[int, float], float]:
    """Split a donor amount across project leaves. Residual stays with the donor."""
    if abs(donor) < 1e-12:
        return {}, 0.0
    mode = (mode or "consumption").lower()
    unique_leaves = list(dict.fromkeys(target_leaves))
    if mode == "custom":
        shares: dict[int, float] = {}
        residual = donor
        for leaves, percent in custom_targets or []:
            bucket = donor * (float(percent or 0) / 100.0)
            residual -= bucket
            part, rest = _split_by_weight(bucket, {leaf_id: weights.get(leaf_id, 0.0) for leaf_id in leaves})
            for leaf_id, value in part.items():
                shares[leaf_id] = shares.get(leaf_id, 0.0) + value
            residual += rest
        return shares, residual
    active = [leaf_id for leaf_id in unique_leaves if weights.get(leaf_id, 0.0) > 0]
    if not active:
        return {}, donor
    if mode == "equal":
        each = donor / len(active)
        return {leaf_id: each for leaf_id in active}, 0.0
    return _split_by_weight(donor, {leaf_id: weights.get(leaf_id, 0.0) for leaf_id in active})


@dataclass
class ResolvedSharedCost:
    donor_id: int
    donor_path: list[str]
    cloud_prefix: str
    mode: str
    donor_subtree: set[int]
    target_leaves: list[int]
    leaf_paths: dict[int, list[str]]
    custom_targets: list[tuple[list[int], float]] = field(default_factory=list)


def same_cloud_leaf_ids(
    donor_prefix: str,
    departments: dict[int, Department],
    child_ids: dict[int | None, list[int]],
    donor_tree: set[int],
) -> list[int]:
    """Same-cloud departments that can receive rateio, excluding the donor subtree.

    Project folders (non-leaves) are included. Cost often sits on
    ``Sinfonia - Watson`` while a sibling leaf such as VivoCog is the only
    org-chart leaf — counting only leaves would give VivoCog 100%.
    Cloud roots (Consumo Cloud / Showback / provider) are skipped.
    """
    targets: list[int] = []
    for department in departments.values():
        if not department.active or department.id in donor_tree:
            continue
        path = department_path(department.id, departments)
        if len(path) < 4:
            continue
        prefix = cloud_prefix_for(department, path, departments)
        if prefix and (prefix.startswith(donor_prefix) or donor_prefix.startswith(prefix)):
            targets.append(department.id)
    return targets


def matching_target_id(path: list[str], config: ResolvedSharedCost) -> int | None:
    """Longest hierarchy prefix wins so a child is not billed to a parent folder."""
    best_id = None
    best_len = -1
    for leaf_id in config.target_leaves:
        prefix = config.leaf_paths.get(leaf_id) or []
        if path_matches(path, prefix) and len(prefix) > best_len:
            best_id = leaf_id
            best_len = len(prefix)
    return best_id


def resolve_shared_costs(
    configs: list[DepartmentSharedCost],
    departments: dict[int, Department],
) -> list[ResolvedSharedCost]:
    child_ids = children_map(departments)
    resolved: list[ResolvedSharedCost] = []
    for config in configs:
        if not config.enabled:
            continue
        donor = departments.get(config.department_id)
        if not donor or not donor.active:
            continue
        path = department_path(donor.id, departments)
        prefix = cloud_prefix_for(donor, path, departments)
        if not prefix:
            continue
        donor_tree = subtree_ids(donor.id, child_ids)
        mode = (config.mode or "consumption").lower()
        target_leaves: list[int] = []
        custom_targets: list[tuple[list[int], float]] = []
        leaf_paths: dict[int, list[str]] = {}
        if mode == "consumption":
            target_leaves = same_cloud_leaf_ids(prefix, departments, child_ids, donor_tree)
            for leaf_id in target_leaves:
                leaf_paths[leaf_id] = department_path(leaf_id, departments)
        else:
            for target in config.targets or []:
                node = departments.get(target.target_department_id)
                if not node or not node.active:
                    continue
                if node.id in donor_tree:
                    continue
                leaves = [leaf_id for leaf_id in leaf_ids(node.id, child_ids) if leaf_id not in donor_tree]
                active_leaves = [leaf_id for leaf_id in leaves if departments.get(leaf_id) and departments[leaf_id].active]
                if not active_leaves:
                    continue
                target_leaves.extend(active_leaves)
                if mode == "custom":
                    custom_targets.append((active_leaves, float(target.percent or 0)))
                for leaf_id in active_leaves:
                    leaf_paths[leaf_id] = department_path(leaf_id, departments)
        unique_leaves = list(dict.fromkeys(target_leaves))
        if not unique_leaves:
            continue
        resolved.append(ResolvedSharedCost(
            donor_id=donor.id,
            donor_path=path,
            cloud_prefix=prefix,
            mode=mode,
            donor_subtree=donor_tree,
            target_leaves=unique_leaves,
            leaf_paths=leaf_paths,
            custom_targets=custom_targets,
        ))
    return resolved


def snapshot_enabled_shared_costs(
    configs: list[DepartmentSharedCost],
    departments: dict[int, Department],
) -> dict[str, Any]:
    resolved = resolve_shared_costs(configs, departments)
    return {
        "shared_nodes": [
            {
                "department_id": item.donor_id,
                "path": " > ".join(item.donor_path),
                "mode": item.mode,
                "cloud_prefix": item.cloud_prefix,
                "projects": len(item.target_leaves),
            }
            for item in resolved
        ],
        "total_projects": sum(len(item.target_leaves) for item in resolved),
    }


def configs_for_scope(resolved: list[ResolvedSharedCost], scope_prefix: str) -> list[ResolvedSharedCost]:
    if not scope_prefix:
        return resolved
    return [item for item in resolved if item.cloud_prefix and (
        scope_prefix.startswith(item.cloud_prefix) or item.cloud_prefix.startswith(scope_prefix)
    )]


def path_matches(row_path: list[str], prefix: list[str]) -> bool:
    row = [(part or "").strip() for part in row_path]
    row = [part for part in row if part]
    target = [(part or "").strip() for part in prefix]
    target = [part for part in target if part]
    return bool(target) and row[: len(target)] == target


def accumulate_series(
    rows: list[dict[str, Any]],
    configs: list[ResolvedSharedCost],
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, float]]]:
    """Map Gold rows onto donor and target-leaf series by hierarchy prefix."""
    donor_values: dict[int, dict[str, float]] = {}
    leaf_values: dict[int, dict[str, float]] = {}
    for row in rows:
        period = str(row.get("period") or "")
        path = list(row.get("path") or [])
        value = float(row.get("value") or 0)
        if not period:
            continue
        for config in configs:
            if path_matches(path, config.donor_path):
                bucket = donor_values.setdefault(config.donor_id, {})
                bucket[period] = bucket.get(period, 0.0) + value
                continue
            leaf_id = matching_target_id(path, config)
            if leaf_id is not None:
                bucket = leaf_values.setdefault(leaf_id, {})
                bucket[period] = bucket.get(period, 0.0) + value
    return donor_values, leaf_values


def hierarchy_fields(path: list[str]) -> dict[str, str]:
    return {f"departamento_nivel_{level}": path[level - 1] if level <= len(path) else "" for level in range(1, 8)}


def build_rateio_rows(
    configs: list[ResolvedSharedCost],
    actual_rows: list[dict[str, Any]],
    forecast_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build monthly +consumer / −donor rows at forecast grain."""
    rows: list[dict[str, Any]] = []

    def emit(
        medida: str,
        mapped: list[dict[str, Any]],
        skip_donor_periods: dict[int, set[str]] | None = None,
        fallback_leaves: dict[int, dict[str, float]] | None = None,
    ) -> None:
        donor_values, leaf_values = accumulate_series(mapped, configs)
        for config in configs:
            skipped = skip_donor_periods.get(config.donor_id, set()) if skip_donor_periods else set()
            ytd = {
                leaf_id: sum(fallback_leaves.get(leaf_id, {}).values())
                for leaf_id in config.target_leaves
            } if fallback_leaves else {}
            ytd_total = sum(ytd.values())
            for period, donor in donor_values.get(config.donor_id, {}).items():
                if period in skipped:
                    continue
                weights = {leaf_id: leaf_values.get(leaf_id, {}).get(period, 0.0) for leaf_id in config.target_leaves}
                if (
                    medida == RATEIO_MEDIDA_FORECAST
                    and config.mode == "consumption"
                    and ytd_total > 1e-12
                ):
                    # Future months often have no planning consumption yet.
                    # Reuse YTD Gold mix so the same consumers receive the
                    # condomínio forecast they already receive on realizado.
                    weights = ytd
                shares, residual = allocate_amount(
                    donor, config.mode, config.target_leaves, weights, config.custom_targets,
                )
                allocated = donor - residual
                if abs(allocated) < 1e-12:
                    continue
                rows.append({
                    **hierarchy_fields(config.donor_path),
                    "period": period,
                    "tipo": RATEIO_TIPO,
                    "realizado": -allocated,
                    "rateio_medida": medida,
                    "cloud_origem": config.cloud_prefix,
                })
                for leaf_id, amount in shares.items():
                    if abs(amount) < 1e-12:
                        continue
                    rows.append({
                        **hierarchy_fields(config.leaf_paths[leaf_id]),
                        "period": period,
                        "tipo": RATEIO_TIPO,
                        "realizado": amount,
                        "rateio_medida": medida,
                        "cloud_origem": config.cloud_prefix,
                    })

    # Closed for a donor = that donor already has Gold. Another project's
    # billed month must not skip the condomínio's remaining forecast.
    actual_donor, actual_leaves = accumulate_series(actual_rows, configs)
    skip_forecast = {donor_id: set(periods) for donor_id, periods in actual_donor.items()}
    emit(RATEIO_MEDIDA_ACTUAL, actual_rows)
    emit(RATEIO_MEDIDA_FORECAST, forecast_rows, skip_forecast, actual_leaves)
    if not rows:
        return []
    collapsed: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (
            row["period"],
            row["rateio_medida"],
            tuple(row[f"departamento_nivel_{level}"] for level in range(1, 8)),
            row.get("cloud_origem") or "",
        )
        current = collapsed.get(key)
        if current is None:
            collapsed[key] = dict(row)
        else:
            current["realizado"] = float(current["realizado"]) + float(row["realizado"])
    return [row for row in collapsed.values() if abs(float(row["realizado"])) >= 1e-12]


def apply_rateio_facts(
    rows: list[dict[str, Any]],
    by_period_facts: dict[str, dict[str, Any]],
    by_driver: dict[str, float],
    measure_key: str,
) -> None:
    """Add precomputed rateio facts (already scoped/grouped) onto expected-view series."""
    for row in rows:
        period = str(row.get("period") or "")
        driver = str(row.get("driver") or "Sem departamento filho")
        value = float(row.get("value") or 0)
        if not period or abs(value) < 1e-12:
            continue
        current = by_period_facts.setdefault(period, {"period": period, "actual_value": None, "forecast_value": None})
        current[measure_key] = float(current.get(measure_key) or 0) + value
        if measure_key == "shared_value":
            if value > 0:
                current["shared_received"] = float(current.get("shared_received") or 0) + value
            else:
                current["shared_given"] = float(current.get("shared_given") or 0) + value
        by_driver[driver] = by_driver.get(driver, 0.0) + value


def apply_shared_cost_overlay(
    *,
    by_period_facts: dict[str, dict[str, Any]],
    actual_by_driver: dict[str, float],
    forecast_by_driver: dict[str, float],
    scope_path: list[str],
    selected_ids: set[int],
    configs: list[ResolvedSharedCost],
    donor_actual: dict[int, dict[str, float]],
    donor_forecast: dict[int, dict[str, float]],
    leaf_actual: dict[int, dict[str, float]],
    leaf_forecast: dict[int, dict[str, float]],
) -> None:
    def ensure_row(period: str) -> dict[str, Any]:
        return by_period_facts.setdefault(period, {"period": period, "actual_value": None, "forecast_value": None})

    def add_measure(row: dict[str, Any], key: str, delta: float) -> None:
        if abs(delta) < 1e-12:
            return
        row[key] = float(row.get(key) or 0) + delta

    def add_driver(store: dict[str, float], name: str | None, delta: float) -> None:
        if not name or abs(delta) < 1e-12:
            return
        store[name] = store.get(name, 0.0) + delta

    periods: set[str] = set(by_period_facts)
    for config in configs:
        periods.update(donor_actual.get(config.donor_id, {}))
        periods.update(donor_forecast.get(config.donor_id, {}))

    for config in configs:
        donor_inside = config.donor_id in selected_ids
        for period in periods:
            actual_donor = donor_actual.get(config.donor_id, {}).get(period)
            forecast_donor = donor_forecast.get(config.donor_id, {}).get(period)
            actual_weights = {leaf_id: leaf_actual.get(leaf_id, {}).get(period, 0.0) for leaf_id in config.target_leaves}
            forecast_weights = {leaf_id: leaf_forecast.get(leaf_id, {}).get(period, 0.0) for leaf_id in config.target_leaves}
            actual_shares: dict[int, float] = {}
            forecast_shares: dict[int, float] = {}
            actual_residual = 0.0
            forecast_residual = 0.0
            if actual_donor is not None:
                actual_shares, actual_residual = allocate_amount(
                    actual_donor, config.mode, config.target_leaves, actual_weights, config.custom_targets,
                )
            if forecast_donor is not None:
                forecast_shares, forecast_residual = allocate_amount(
                    forecast_donor, config.mode, config.target_leaves, forecast_weights, config.custom_targets,
                )

            actual_delta = 0.0
            forecast_delta = 0.0
            if donor_inside and actual_donor is not None:
                allocated = actual_donor - actual_residual
                actual_delta -= allocated
                add_driver(actual_by_driver, driver_name_for(config.donor_path, scope_path), -allocated)
            if donor_inside and forecast_donor is not None:
                allocated = forecast_donor - forecast_residual
                forecast_delta -= allocated
                add_driver(forecast_by_driver, driver_name_for(config.donor_path, scope_path), -allocated)
            for leaf_id, amount in actual_shares.items():
                if leaf_id in selected_ids:
                    actual_delta += amount
                    add_driver(actual_by_driver, driver_name_for(config.leaf_paths[leaf_id], scope_path), amount)
            for leaf_id, amount in forecast_shares.items():
                if leaf_id in selected_ids:
                    forecast_delta += amount
                    add_driver(forecast_by_driver, driver_name_for(config.leaf_paths[leaf_id], scope_path), amount)
            if abs(actual_delta) < 1e-12 and abs(forecast_delta) < 1e-12:
                continue
            row = ensure_row(period)
            add_measure(row, "actual_value", actual_delta)
            add_measure(row, "forecast_value", forecast_delta)


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


def _hierarchy_select_sql(columns: dict[str, str]) -> str:
    parts: list[str] = []
    for level in range(1, 8):
        key = f"departamento_nivel_{level}"
        if key in columns:
            parts.append(
                f"COALESCE(NULLIF(TRIM(CAST({_quote_ident(columns[key])} AS VARCHAR)), ''), '') AS nivel_{level}"
            )
        else:
            parts.append(f"'' AS nivel_{level}")
    return ", ".join(parts)


def _row_hierarchy_path(row: dict[str, Any]) -> list[str]:
    path: list[str] = []
    for level in range(1, 8):
        name = str(row.get(f"nivel_{level}") or "").strip()
        if not name:
            break
        path.append(name)
    return path


def _tipo_realizado_sql(columns: dict[str, str]) -> str:
    if "tipo" not in columns:
        return ""
    col = _quote_ident(columns["tipo"])
    return f" AND ({col} IS NULL OR lower(CAST({col} AS VARCHAR)) IN ('realizado', ''))"


def _cloud_sql(prefixes: list[str], columns: dict[str, str]) -> str:
    if "cloud_origem" not in columns or not prefixes:
        return ""
    cloud_column = _quote_ident(columns["cloud_origem"])
    likes = [
        f"lower(CAST({cloud_column} AS VARCHAR)) LIKE {_quote_literal(prefix + '%')}"
        for prefix in dict.fromkeys(item.strip().lower() for item in prefixes if item)
    ]
    return f" AND ({' OR '.join(likes)})" if likes else ""


def _mapped_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"period": row.get("period"), "path": _row_hierarchy_path(row), "value": row.get("value")}
        for row in records
    ]


async def load_weight_aggregates(
    gold_table: str,
    planning_table: str,
    gold_columns: dict[str, str],
    planning_columns: dict[str, str],
    prefixes: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Closed months from Gold; open/future months from planning tipo=forecast."""
    from app.services.trino_service import execute as trino_execute

    gold_period = _quote_ident(gold_columns["_billing_period"] if "_billing_period" in gold_columns else gold_columns["period"])
    gold_value = _quote_ident(gold_columns["realizado"])
    actual_sql = f"""
      SELECT {gold_period} AS period,
             {_hierarchy_select_sql(gold_columns)},
             SUM(COALESCE(TRY_CAST({gold_value} AS DOUBLE), 0)) AS value
      FROM iceberg.gold.{gold_table}
      WHERE 1=1
        {_tipo_realizado_sql(gold_columns)}
        {_cloud_sql(prefixes, gold_columns)}
      GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    """
    forecast_period = _quote_ident(planning_columns["period"])
    forecast_value = _quote_ident(planning_columns["realizado"])
    forecast_type = _quote_ident(planning_columns["tipo"])
    forecast_sql = f"""
      SELECT {forecast_period} AS period,
             {_hierarchy_select_sql(planning_columns)},
             SUM(COALESCE(TRY_CAST({forecast_value} AS DOUBLE), 0)) AS value
      FROM iceberg.gold.{planning_table}
      WHERE lower(CAST({forecast_type} AS VARCHAR)) = 'forecast'
        {_cloud_sql(prefixes, planning_columns)}
      GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    """
    actual_records = await trino_execute(actual_sql, timeout_s=300)
    try:
        forecast_records = await trino_execute(forecast_sql, timeout_s=120)
    except Exception as exc:
        logger.warning("Planning forecast aggregate skipped: %s", exc)
        forecast_records = []
    return _mapped_rows(actual_records), _mapped_rows(forecast_records)


async def run_shared_cost_job(session: Any, *, allow_empty: bool = False) -> dict[str, Any]:
    """Resolve configs, aggregate weights once, write tipo=rateio into planning."""
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    from app.models.destination import DestinationTable
    from app.services import iceberg_service as iceberg
    from app.services.trino_service import execute as trino_execute

    departments = {row.id: row for row in session.query(Department).all()}
    configs = session.execute(
        select(DepartmentSharedCost).options(selectinload(DepartmentSharedCost.targets)).where(
            DepartmentSharedCost.enabled == True
        )
    ).scalars().all()
    snapshot = snapshot_enabled_shared_costs(list(configs), departments)
    resolved = resolve_shared_costs(list(configs), departments)
    dest = session.get(DestinationTable, 1)
    if dest is None or not dest.active:
        dest = session.query(DestinationTable).filter(DestinationTable.active == True).order_by(DestinationTable.id).first()
    gold_table = str(dest.physical_name).replace("-", "_").replace(".", "_") if dest else ""
    planning_table = iceberg.planning_table_name(dest.physical_name) if dest else ""
    if not resolved:
        if allow_empty:
            return {"snapshot": snapshot, "rows": 0, "planning_table": planning_table, "gold_table": gold_table}
        raise RuntimeError("Nenhum custo compartilhado habilitado com cloud de origem e projetos para ratear")
    if dest is None:
        raise RuntimeError("Nenhuma tabela destino ativa para gravar o rateio")
    gold_schema = _schema_map(await trino_execute(f"DESCRIBE iceberg.gold.{gold_table}", timeout_s=30))
    if "realizado" not in gold_schema or ("_billing_period" not in gold_schema and "period" not in gold_schema):
        raise RuntimeError("A tabela Gold não possui as colunas necessárias para o rateio")
    try:
        planning_schema = _schema_map(await trino_execute(f"DESCRIBE iceberg.gold.{planning_table}", timeout_s=30))
    except Exception:
        planning_schema = {
            "period": "period",
            "realizado": "realizado",
            "tipo": "tipo",
            **{f"departamento_nivel_{level}": f"departamento_nivel_{level}" for level in range(1, 8)},
        }
    if "period" not in planning_schema or "realizado" not in planning_schema or "tipo" not in planning_schema:
        raise RuntimeError("A tabela de planejamento não possui as colunas necessárias para o rateio")

    prefixes = [item.cloud_prefix for item in resolved]
    actual_rows, forecast_rows = await load_weight_aggregates(
        gold_table, planning_table, gold_schema, planning_schema, prefixes,
    )
    rateio_rows = build_rateio_rows(resolved, actual_rows, forecast_rows)
    import pandas as pd
    rateio_df = pd.DataFrame(rateio_rows)
    written = iceberg.merge_planning_rateio(planning_table, rateio_df, value_column="realizado")
    logger.info("Wrote %s rateio rows to gold.%s", written, planning_table)
    return {
        "snapshot": snapshot,
        "rows": written,
        "planning_table": planning_table,
        "gold_table": gold_table,
    }


def run_shared_cost_job_sync(session: Any, *, allow_empty: bool = False) -> dict[str, Any]:
    import asyncio
    return asyncio.run(run_shared_cost_job(session, allow_empty=allow_empty))

