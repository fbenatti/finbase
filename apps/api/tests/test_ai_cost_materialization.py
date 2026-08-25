from types import SimpleNamespace

import pytest

from app.services.ai_cost_materialization import _build_routed_staging, link_gold_table_name


def _link(link_id: int, value: str):
    source = SimpleNamespace(
        id=link_id * 10,
        connection_id=None,
        filter_json={"conditions": [{"column": "ServiceName", "operator": "=", "value": value}]},
    )
    return SimpleNamespace(id=link_id, active=True, valid_from=None, valid_until=None, sources=[source])


def test_link_gold_name_is_stable_by_id():
    assert link_gold_table_name(27) == "dest_ai_cost_link_27"


@pytest.mark.asyncio
async def test_router_scans_focus_once_per_period_for_all_links(monkeypatch):
    statements = []

    async def fake_execute(sql, **_kwargs):
        statements.append(sql)
        return []

    monkeypatch.setattr("app.services.trino_service.execute", fake_execute)
    links = [_link(1, "GitHub"), _link(5, "Azure OpenAI")]
    schema = {"_billing_period": "_billing_period", "servicename": "ServiceName"}

    created = await _build_routed_staging(
        links, schema, ["202601", "202602"], "route_staging", progress=None,
    )

    assert created is True
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE")
    assert statements[1].startswith("INSERT INTO")
    for statement in statements:
        assert statement.count("iceberg.gold.dest_modelo_focus") == 1
        assert "CAST(1 AS BIGINT)" in statement
        assert "CAST(5 AS BIGINT)" in statement
