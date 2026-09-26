from fastapi import HTTPException
import pytest

from app.routers import gold


@pytest.mark.asyncio
async def test_daily_consumption_reads_silver_with_gold_scope(monkeypatch):
    calls = []

    async def fake_execute(sql, timeout_s=0):
        calls.append(sql)
        if sql.startswith("DESCRIBE"):
            return [{"Column": name} for name in (
                "_billing_period", "ChargePeriodStart", "realizado", "tipo",
                "servicecategory", "organograma_nivel_4",
            )]
        return [{"day": "2026-08-03", "servicecategory": "Compute", "realizado": 123.0}]

    monkeypatch.setattr(gold, "execute", fake_execute)
    request = gold.GoldQueryRequest(
        table="dest_dm_cost_visao_completa",
        filters=[
            gold.GoldQueryFilter(column="_billing_period", values=["202608"]),
            gold.GoldQueryFilter(column="servicecategory", values=["Compute", "Storage"]),
        ],
        filter_groups=[[
            gold.GoldQueryFilter(column="organograma_nivel_4", values=["Cloud"]),
        ]],
        group_by="servicecategory",
    )

    result = await gold.query_gold_daily_consumption(request)

    assert result["available"] is True
    assert result["source"] == "silver.dest_modelo_focus"
    assert result["day_column"] == "ChargePeriodStart"
    assert result["rows"][0]["realizado"] == 123.0
    query = calls[-1]
    assert "FROM iceberg.silver.dest_modelo_focus" in query
    assert "ChargePeriodStart" in query
    assert "servicecategory" in query
    assert "organograma_nivel_4" in query
    assert "_billing_period" in query


@pytest.mark.asyncio
async def test_daily_consumption_rejects_unmapped_gold_cube():
    request = gold.GoldQueryRequest(table="dest_dm_cost_unmapped")
    with pytest.raises(HTTPException) as error:
        await gold.query_gold_daily_consumption(request)
    assert error.value.status_code == 422
