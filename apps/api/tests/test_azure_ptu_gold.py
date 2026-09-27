import pytest

from app.services import azure_ptu_gold as gold_mart


@pytest.mark.asyncio
async def test_project_tokens_are_read_from_ptu_gold(monkeypatch):
    captured = []

    async def fake_execute(sql, **kwargs):
        captured.append(sql)
        return [{"period": "202608", "input_tokens": 800, "output_tokens": 200, "total_tokens": 1000}]

    monkeypatch.setattr(gold_mart, "trino_execute", fake_execute)
    result = await gold_mart.load_project_token_totals("vivo-mais", ["202608"], None)

    assert result["202608"]["total_tokens"] == 1000
    assert gold_mart.GOLD_SQL in captured[0]
    assert "lower(CAST(project_hint AS VARCHAR)) = 'vivo-mais'" in captured[0]


@pytest.mark.asyncio
async def test_project_tokens_can_load_all_available_periods(monkeypatch):
    captured = []

    async def fake_execute(sql, **kwargs):
        captured.append(sql)
        return []

    monkeypatch.setattr(gold_mart, "trino_execute", fake_execute)
    await gold_mart.load_project_token_totals("aura-brain", None, None)

    assert "replace(_billing_period, '-', '') IN" not in captured[0]
    assert "lower(CAST(project_hint AS VARCHAR)) = 'aura-brain'" in captured[0]


@pytest.mark.asyncio
async def test_dashboard_metrics_do_not_fallback_to_gold(monkeypatch):
    captured = []
    gold_mart._GOLD_COLUMNS = {
        "metric_day", "connection_id", "account_resource_id", "deployment_name",
        "model_name", "model_version", "region", "sku_name", "project_hint",
        "provisioned_ptus", "input_tokens_sum", "output_tokens_sum", "total_tokens_sum",
        "request_count", "utilization_avg", "sample_count",
    }

    async def fake_execute(sql, **kwargs):
        captured.append(sql)
        return []

    monkeypatch.setattr(gold_mart, "trino_execute", fake_execute)
    await gold_mart.load_gold_metrics(hours=720, project_hints=["aura-brain"], grain="day")

    assert len(captured) == 1
    assert f"FROM iceberg.{gold_mart.GOLD}.dest_dm_azure_ptu_daily" in captured[0]
    assert "iceberg.gold.dest_azure_ptu_metrics" not in captured[0]


def test_daily_rollup_keeps_token_and_risk_measures():
    sql = gold_mart._daily_select({
        "input_tokens_sum", "output_tokens_sum", "total_tokens_sum", "request_count",
        "provisioned_ptus", "utilization_avg", "utilization_max",
    })
    assert "SUM(TRY_CAST(total_tokens_sum AS DOUBLE))" in sql
    assert "hours_at_or_above_80" in sql
    assert "hours_at_or_above_95" in sql
    assert "AS sample_count" in sql


@pytest.mark.asyncio
async def test_project_tokens_last_30d_uses_rolling_window(monkeypatch):
    captured = []

    async def fake_execute(sql, **kwargs):
        captured.append(sql)
        return [{"input_tokens": 800, "output_tokens": 200, "total_tokens": 1000,
                 "date_from": "2026-08-02", "date_to": "2026-09-01"}]

    monkeypatch.setattr(gold_mart, "trino_execute", fake_execute)
    result = await gold_mart.load_project_tokens_last_30d("aura-brain")

    assert result["total_tokens"] == 1000
    assert "current_date - INTERVAL '30' DAY" in captured[0]
    assert "project_hint AS VARCHAR)) = 'aura-brain'" in captured[0]


@pytest.mark.asyncio
async def test_daily_refresh_reads_hourly_source(monkeypatch):
    described = []
    published = []

    async def describe(table):
        described.append(table)
        return {"metric_hour", "project_hint", "input_tokens_sum", "output_tokens_sum", "total_tokens_sum"}

    async def publish(**kwargs):
        published.append(kwargs)
        return type("Outcome", (), {"rows": 2})()

    monkeypatch.setattr(gold_mart, "_columns", describe)
    monkeypatch.setattr(gold_mart, "publish_gold_cube", publish)
    result = await gold_mart.refresh_azure_ptu_gold(days=7)

    assert result["rows"] == 2
    assert described == [gold_mart.PTU_HOURLY_TABLE]
    assert f"FROM {gold_mart.PTU_HOURLY_TABLE}" in published[0]["select_sql"]
    assert f"FROM {gold_mart.GOLD_SQL}" not in published[0]["select_sql"]
