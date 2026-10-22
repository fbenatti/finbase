"""Hierarquia sem limite: árvores de 10 níveis (antes o teto era 7)."""
import io
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

from app.core.hierarchy import (
    depth_from_departments,
    hierarchy_columns,
    hierarchy_columns_in,
    legacy_to_hierarchy,
    path_to_levels,
)
from app.models.rules import Department

DEPTH = 10
NAMES = [f"Nivel {level}" for level in range(1, DEPTH + 1)]


def _chain(cls=Department):
    """Departamentos 1 → 2 → ... → 10, um filho por nível."""
    return {
        level: cls(id=level, parent_id=level - 1 or None, name=NAMES[level - 1], active=True)
        for level in range(1, DEPTH + 1)
    }


def test_helpers_order_levels_numerically_beyond_nine():
    columns = ["hierarquia_10", "hierarquia_2", "hierarquia_id", "hierarquia", "hierarquia_1", "hierarquia_9"]
    # hierarquia_10 vem depois de hierarquia_9 (ordem numérica, não alfabética)
    # e colunas como hierarquia_id/hierarquia não são níveis.
    assert hierarchy_columns_in(columns) == ["hierarquia_1", "hierarquia_2", "hierarquia_9", "hierarquia_10"]
    assert hierarchy_columns(DEPTH)[-1] == "hierarquia_10"
    assert depth_from_departments(_chain().values()) == DEPTH
    assert list(path_to_levels(NAMES).values()) == NAMES
    assert legacy_to_hierarchy("organograma_nivel_10") == "hierarquia_10"
    assert legacy_to_hierarchy("departamento_nivel_8") == "hierarquia_8"


def test_classification_fills_all_ten_levels():
    from app.services.task_runner import _apply_classifications_sync

    rule = SimpleNamespace(
        id=1, name="leaf", source_field="SubAccountId", pattern='["sub-1"]', pattern_type="exact",
        match_value="Nivel 10", target_field="organograma", conditions=None, priority=1,
        cloud_scope=None, active=True, department_id=DEPTH,
    )
    df = pd.DataFrame({"SubAccountId": ["sub-1"]})

    _apply_classifications_sync(df, [rule], dept_map=_chain())

    assert [df.loc[0, column] for column in hierarchy_columns(DEPTH)] == NAMES
    assert "hierarquia_11" not in df.columns


def test_department_path_info_has_ten_levels():
    from app.services.classification_engine import _build_department_path

    info = _build_department_path(DEPTH, _chain())

    assert info["hierarquia_10"] == "Nivel 10"
    assert info["department_path"] == " > ".join(NAMES)


def test_rateio_rows_keep_ten_levels_and_pad_short_paths():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    # Doador é irmão do ramo da folha (não pode conter a folha na subárvore).
    donor_path = [*NAMES[:3], "Rateio"]
    config = ResolvedSharedCost(
        donor_id=99, donor_path=donor_path, cloud_prefix="azure", mode="consumption",
        donor_subtree={99}, target_leaves=[DEPTH], leaf_paths={DEPTH: NAMES},
    )
    rows = build_rateio_rows(
        [config],
        [{"period": "202601", "path": donor_path, "value": 100.0},
         {"period": "202601", "path": NAMES, "value": 50.0}],
        [],
    )

    consumer = next(row for row in rows if row["hierarquia_10"] == "Nivel 10")
    donor = next(row for row in rows if row["hierarquia_10"] == "")
    assert consumer["realizado"] > 0
    assert donor["realizado"] < 0
    assert donor["hierarquia_3"] == "Nivel 3"
    # Todas as linhas com o mesmo conjunto de níveis.
    assert {tuple(hierarchy_columns_in(row)) for row in rows} == {tuple(hierarchy_columns(DEPTH))}


@pytest.mark.asyncio
async def test_planning_workbook_round_trip_with_ten_levels(monkeypatch):
    from app.routers import planning

    departments = _chain(SimpleNamespace)
    children = {None: [departments[1]], **{level: [departments[level + 1]] for level in range(1, DEPTH)}, DEPTH: []}

    async def organization(_db):
        return departments, children

    monkeypatch.setattr(planning, "_organization", organization)
    assert planning._tree_depth(departments) == DEPTH

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(planning._headers(DEPTH))
    worksheet.append([*NAMES, *([10] * 12)])
    content = io.BytesIO()
    workbook.save(content)

    rows = await planning._parse_excel(content.getvalue(), db=None)

    assert [row.department_id for row in rows] == [DEPTH]
    assert rows[0].values == [10.0] * 12


@pytest.mark.asyncio
async def test_planning_workbook_accepts_legacy_header_with_ten_levels(monkeypatch):
    from app.routers import planning

    departments = _chain(SimpleNamespace)
    children = {None: [departments[1]], **{level: [departments[level + 1]] for level in range(1, DEPTH)}, DEPTH: []}

    async def organization(_db):
        return departments, children

    monkeypatch.setattr(planning, "_organization", organization)
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append([*(f"Departamento nível {level}" for level in range(1, DEPTH + 1)), *planning.MONTHS])
    worksheet.append([*NAMES, *([1] * 12)])
    content = io.BytesIO()
    workbook.save(content)

    rows = await planning._parse_excel(content.getvalue(), db=None)

    assert [row.department_id for row in rows] == [DEPTH]


def test_default_cube_dimensions_include_all_ten_levels_in_order():
    from app.services.gold_materialization import _default_dimensions

    schema = {"gold": {column: column for column in ["_billing_period", "realizado", *reversed(hierarchy_columns(DEPTH))]}}

    dims = _default_dimensions(schema)

    assert [dim for dim in dims if dim.startswith("hierarquia_")] == hierarchy_columns(DEPTH)


def test_anomaly_contract_and_composition_with_ten_levels():
    from app.services.anomaly_composition import planning_projection
    from app.services.anomaly_schema import assessment_columns, series_dimensions

    names = [name for name, _ in assessment_columns(DEPTH)]
    assert "hierarquia_10" in names and "hierarquia_11" not in names
    dims = series_dimensions(DEPTH)
    assert dims.index("hierarquia_10") == dims.index("hierarquia_1") + 9
    assert '"hierarquia_10"' in planning_projection(depth=DEPTH)


def test_forecast_department_filter_uses_all_ten_levels():
    from app.routers.forecast import _department_scope_filter

    sql = _department_scope_filter(" > ".join(NAMES))

    assert "hierarquia_10" in sql and "'Nivel 10'" in sql
