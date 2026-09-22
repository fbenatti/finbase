from types import SimpleNamespace

import pytest

from app.services.ai_cost_materialization import (
    _build_routed_staging, _link_clause, link_gold_table_name,
    materialize_ai_cost_relationships,
)


def _link(link_id: int, value: str):
    source = SimpleNamespace(
        id=link_id * 10,
        connection_id=None,
        filter_json={"conditions": [{"column": "ServiceName", "operator": "=", "value": value}]},
    )
    return SimpleNamespace(
        id=link_id, name=f"Regra {link_id}", active=True,
        valid_from=None, valid_until=None, sources=[source],
    )


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
        assert statement.count("iceberg.silver.dest_modelo_focus") == 1
        assert "CAST(1 AS BIGINT)" in statement
        assert "CAST(5 AS BIGINT)" in statement


def test_link_clause_raises_on_unknown_focus_field():
    link = _link(1, "GitHub")
    link.sources[0].filter_json = {
        "conditions": [{"column": "ReservationName", "operator": "NOT_EMPTY", "values": []}]
    }
    schema = {"_billing_period": "_billing_period", "servicename": "ServiceName", "source_reservation_name": "source_reservation_name"}
    # Alias reservationname → source_reservation_name resolves when present.
    assert _link_clause(link, schema) is not None

    missing_schema = {"_billing_period": "_billing_period", "servicename": "ServiceName"}
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="Campos FOCUS inexistentes"):
        _link_clause(link, missing_schema)


@pytest.mark.asyncio
async def test_materialization_isolates_invalid_rules(monkeypatch):
    """One rule with unknown FOCUS fields must not block the others."""
    session = SimpleNamespace(
        query=lambda model: SimpleNamespace(
            options=lambda *args: SimpleNamespace(
                filter=lambda *conds: SimpleNamespace(all=lambda: [
                    _link(1, "GitHub"),
                    SimpleNamespace(
                        id=5,
                        active=True,
                        valid_from=None,
                        valid_until=None,
                        name="Azure 4P — OpenAI PTU",
                        sources=[
                            SimpleNamespace(
                                id=50,
                                connection_id=None,
                                filter_json={"conditions": [
                                    {"column": "skumeter", "operator": "IN", "values": ["Provisioned Unit"]},
                                    {"column": "ReservationName", "operator": "NOT_EMPTY", "values": []},
                                ]},
                            )
                        ],
                    ),
                ])
            )
        ),
        commit=lambda: None,
        add=lambda *args: None,
        flush=lambda *args: None,
    )

    async def fake_load_focus_schema():
        return {
            "_billing_period": "_billing_period",
            "servicename": "ServiceName",
            "skumeter": "skumeter",
            "source_connection_id": "source_connection_id",
            # No source_reservation_name → ReservationName is unknown here.
        }

    async def fake_focus_periods(schema):
        return ["202601", "202602"]

    import app.services.ai_cost_materialization as mod

    monkeypatch.setattr(mod, "load_focus_schema", fake_load_focus_schema)
    monkeypatch.setattr(mod, "_focus_periods", fake_focus_periods)

    statements = []
    async def fake_execute(sql, **_kwargs):
        statements.append(sql)
        if sql.startswith("SELECT COUNT(*)") or sql.startswith("SELECT DISTINCT"):
            return []
        return []

    monkeypatch.setattr("app.services.trino_service.execute", fake_execute)

    import app.services.iceberg_service as iceberg_mod
    monkeypatch.setattr(iceberg_mod, "cleanup_staged_table", lambda *a, **k: None)

    result = await materialize_ai_cost_relationships(session, run_id=999)

    assert result["links"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["link_id"] == 5
    assert "ReservationName" in result["failed"][0]["error"]
    # Only the valid rule is routed — no statement may reference link 5.
    for statement in statements:
        assert "CAST(5 AS BIGINT)" not in statement
