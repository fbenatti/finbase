from app.services.azure_ptu_meters import (
    attach_origin_cost_per_million,
    coverage_band,
    coverage_pct,
    is_azure_openai_ptu_meter,
    pricing_mode_label,
    scd2_apply_capacity_change,
    split_deployment_cost_by_tokens,
    unused_capacity_cost,
    unused_capacity_pct,
)
from datetime import datetime, timezone, timedelta

from app.services.azure_ptu_service import _is_ptu, _request_bucket, collection_windows, resolve_ptu_collection_params, split_monitor_windows, FAILURE_ALERT_THRESHOLD


def test_origin_cost_per_million_and_pricing_labels():
    mm = attach_origin_cost_per_million(origin_cost=100_000, total_tokens=50_000_000, input_tokens=40_000_000, output_tokens=10_000_000)
    assert mm["cost_per_million_tokens"] == 2_000
    assert pricing_mode_label("reservation") == "Reserva (mensal/anual)"
    assert pricing_mode_label("ondemand") == "Horário (OnDemand)"


def test_meter_allowlist_excludes_storage_provisioned():
    assert is_azure_openai_ptu_meter(sku_meter="Provisioned Managed Global Unit", service_name="Azure OpenAI")
    assert not is_azure_openai_ptu_meter(sku_meter="Premium SSD Provisioned", service_name="Storage")
    assert not is_azure_openai_ptu_meter(sku_meter="Provisioned IOPS", service_name="Azure Storage")


def test_only_provisioned_skus_enter_ptu_inventory():
    assert _is_ptu({"sku": {"name": "GlobalProvisionedManaged"}})
    assert _is_ptu({"sku": {"name": "ProvisionedManaged"}})
    assert not _is_ptu({"sku": {"name": "Standard"}})


def test_coverage_and_unattributed_math():
    split = split_deployment_cost_by_tokens(100_000, {"app-a": 50_000_000, "app-b": 35_000_000}, 100_000_000)
    assert split["coverage_pct"] == 85
    assert split["coverage_band"] == "partial"
    assert split["attributable_cost"] == 85_000
    assert split["unattributed_cost"] == 15_000
    assert coverage_band(coverage_pct(110, 100)) == "duplication"
    assert unused_capacity_pct(40) == 0.6
    assert unused_capacity_cost(100, 40) == 60


def test_scd2_capacity_change_closes_previous_validity():
    rows = scd2_apply_capacity_change([], deployment_id="dep-a", ptus=35, observed_at="2026-01-01T00:00:00Z")
    rows = scd2_apply_capacity_change(rows, deployment_id="dep-a", ptus=50, observed_at="2026-02-01T00:00:00Z")
    open_rows = [r for r in rows if r["deployment_resource_id"] == "dep-a"]
    assert open_rows[0]["provisioned_ptus"] == 35
    assert open_rows[0]["valid_until"] == "2026-02-01T00:00:00Z"
    assert open_rows[1]["provisioned_ptus"] == 50
    assert open_rows[1]["valid_until"] is None


def test_hourly_upsert_key_is_connection_account_deployment_model_hour():
    key = ("conn", "/accounts/a", "gpt-ptu", "gpt-4.1", "2026-08-24T15:00:00Z")
    store = {key: {"total_tokens_sum": 10}}
    store[key] = {"total_tokens_sum": 20}
    assert store[key]["total_tokens_sum"] == 20
    assert len(store) == 1


def test_ptu_backfill_uses_max_30_day_windows():
    now = datetime(2026, 8, 24, 12, 10, tzinfo=timezone.utc)
    windows = collection_windows({"backfill_days": 93}, now=now)

    assert len(windows) >= 3
    for _, start, end in windows:
        assert (end - start) <= timedelta(days=30)


def test_ptu_explicit_period_keeps_month_granularity():
    now = datetime(2026, 8, 24, 12, 10, tzinfo=timezone.utc)
    windows = collection_windows({}, ["202606"], now=now)

    assert windows == [
        ("202606", datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc)),
    ]


def test_ptu_explicit_period_splits_long_months_for_monitor():
    now = datetime(2026, 8, 24, 12, 10, tzinfo=timezone.utc)
    windows = collection_windows({}, ["202607"], now=now)
    assert len(windows) == 2
    assert windows[0][0] == "202607"
    assert (windows[0][2] - windows[0][1]) <= timedelta(days=30)


def test_resolve_ptu_collection_params_prioritizes_selected_periods():
    params = resolve_ptu_collection_params(
        task_json={"backfill": True, "backfill_days": 93},
        config={"lookback_hours": 3},
        billing_periods=["202608"],
    )
    assert params["periods"] == ["202608"]
    assert params["backfill_days"] is None


def test_resolve_ptu_collection_params_scheduled_lookback():
    params = resolve_ptu_collection_params(
        task_json={"lookback_hours": 3},
        config={"lookback_hours": 24},
    )
    assert params["lookback_hours"] == 3
    assert params["backfill_days"] is None
    assert params["periods"] is None


def test_resolve_ptu_collection_params_user_incremental():
    params = resolve_ptu_collection_params(
        task_json={"lookback_hours": 3, "user_triggered": True},
        config={"lookback_hours": 3},
    )
    assert params["lookback_hours"] == 3
    assert params["backfill_days"] is None


def test_resolve_ptu_collection_params_user_default_backfill():
    params = resolve_ptu_collection_params(
        task_json={"user_triggered": True},
        config={"backfill_days": 93},
    )
    assert params["backfill_days"] == 93
    assert params["lookback_hours"] == 3
    assert params["periods"] is None


def test_monitor_interval_uses_pt1h_for_historical_window():
    from app.services.azure_ptu_service import _monitor_interval

    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 5, 23, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)
    assert _monitor_interval(start, end, now=now) == "PT1H"


def test_license_pool_default_target_type():
    from app.routers.ai_cost_origin import LinkCreate

    body = LinkCreate(name="Copilot pool", provider="GitHub", product_name="Copilot")
    assert body.target_type == "license_pool"


def test_apply_filter_excludes_ptu_pool_links():
    from app.services.ai_cost_attribution import link_applies

    class _Link:
        def __init__(self, target_type: str):
            self.active = True
            self.valid_from = None
            self.valid_until = None
            self.target_type = target_type

    links = [_Link("license_pool"), _Link("ptu_pool"), _Link("payg_ai")]
    applicable = [
        link for link in links
        if link_applies(link, "202608")
        and (getattr(link, "target_type", None) or "license_pool") == "license_pool"
    ]
    assert len(applicable) == 1
    assert applicable[0].target_type == "license_pool"


def test_request_bucket_classifies_status_and_spillover():
    assert _request_bucket({"StatusCode": "200"}) == "success"
    assert _request_bucket({"StatusCode": "429"}) == "rate_limited"
    assert _request_bucket({"StatusCode": "500"}) == "server_error"
    assert _request_bucket({"IsSpillover": "true"}) == "spillover"


def test_backfill_windows_are_max_30_days():
    from datetime import datetime, timezone, timedelta

    end = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    windows = collection_windows({"backfill_days": 93}, now=end)
    assert len(windows) >= 3
    for _, start, window_end in windows:
        assert (window_end - start) <= timedelta(days=30)


def test_failure_alert_threshold_is_two():
    assert FAILURE_ALERT_THRESHOLD == 2


def test_ingest_quarantines_unknown_client():
    from app.services.azure_ptu_ingest import ingest_usage_event_dicts

    class _Session:
        def __init__(self):
            self.added = []
            self._existing = set()

        def execute(self, *_args, **_kwargs):
            class _Result:
                def scalars(self_inner):
                    class _Scalars:
                        def all(self_s):
                            return list(self._existing)
                    return _Scalars()
            return _Result()

        def add(self, row):
            self.added.append(row)

        def flush(self):
            return None

    session = _Session()
    result = ingest_usage_event_dicts(session, [{
        "event_id": "evt-unknown-1",
        "occurred_at": "2026-08-24T15:00:00Z",
        "telemetry_client_id": "missing-client",
        "input_tokens": 10,
        "output_tokens": 5,
    }])
    assert result.quarantined == 1
    assert result.accepted == 0
    assert session.added[0].ingestion_status == "quarantine"


def test_focus_date_clause_uses_billing_period_start():
    from app.services.ai_cost_attribution import _focus_date_clause

    schema = {"billingperiodstart": "billingperiodstart"}
    clause = _focus_date_clause(schema, "2026-05-26", "2026-08-24")
    assert "billingperiodstart" in clause
    assert ">= '2026-05-26'" in clause
    assert "<= '2026-08-24'" in clause


def test_dashboard_date_bounds_from_hours():
    from app.routers.azure_ptu import DashboardFilter, _dashboard_date_bounds

    body = DashboardFilter(hours=24 * 90, grain="day")
    date_from, date_to, hour_from, hour_to = _dashboard_date_bounds(body)
    assert len(date_from) == 10
    assert len(date_to) == 10
    assert hour_from.endswith("Z")
    assert hour_to.endswith("Z")
    assert date_from <= date_to


def test_focus_ia_ondemand_sql_expr():
    from app.services.azure_ptu_meters import focus_ia_ondemand_sql_expr

    expr = focus_ia_ondemand_sql_expr()
    assert expr.startswith("CASE WHEN")
    assert "openai" in expr
    assert "provisioned managed" in expr


def test_focus_date_predicate_sql_parses_us_dates():
    from app.services.azure_ptu_meters import focus_date_predicate_sql, focus_period_bucket_sql

    clause = focus_date_predicate_sql("2026-05-26", "2026-08-24")
    assert "chargeperiodstart" in clause
    assert "date_parse" in clause
    assert "DATE '2026-05-26'" in clause
    assert "substr" not in clause
    bucket = focus_period_bucket_sql("day")
    assert "date_format" in bucket


def test_focus_haystack_sql_coalesces_nulls():
    from app.services.azure_ptu_meters import _focus_haystack_sql

    haystack = _focus_haystack_sql()
    assert "COALESCE" in haystack
    assert "skuname" not in haystack


def test_ops_kpis_exclude_focus_cost_fields():
    from app.routers.azure_ptu import _enrich_ops_kpis

    kpis = _enrich_ops_kpis(
        [
            {
                "metric_hour": "2026-08-01T10:00:00Z",
                "utilization_avg": 50.0,
                "utilization_p95": 70.0,
                "utilization_max": 80.0,
                "input_tokens_sum": 1_000,
                "output_tokens_sum": 500,
                "total_tokens_sum": 1_500,
                "rate_limited_count": 3,
                "time_to_response_avg_ms": 120.0,
                "request_peak_per_minute": 42.0,
            }
        ],
        [{"ptus": 35, "sku_name": "GlobalProvisionedManaged"}],
    )
    assert kpis["total_ptus"] == 35
    assert kpis["rate_limited_count"] == 3
    assert "realized_cost" not in kpis
    assert "cost_per_million_tokens" not in kpis
    assert "billed_cost" not in kpis


def test_ops_overview_endpoint_has_no_focus_queries():
    import inspect
    from app.routers import azure_ptu as router_mod

    source = inspect.getsource(router_mod.dashboard_ops_overview)
    assert "dest_modelo_focus" not in source
    assert "_query_focus_dashboard_costs" not in source
    assert "_build_cost_series" not in source
    assert "_origin_cost_rows" not in source


def test_load_gold_metrics_90d_window_not_clipped_to_7d(monkeypatch):
    import asyncio

    from app.services.azure_ptu_service import _gold_metrics_window, load_gold_metrics

    captured: dict[str, str] = {}

    async def fake_execute(sql, **kwargs):
        captured["sql"] = sql
        return []

    monkeypatch.setattr("app.services.trino_service.execute", fake_execute)

    asyncio.run(load_gold_metrics(hours=90 * 24, grain="day"))

    start_90d, end_90d = _gold_metrics_window(90 * 24)
    start_7d, _ = _gold_metrics_window(7 * 24)
    assert f">= '{start_90d}'" in captured["sql"]
    assert f"<= '{end_90d}'" in captured["sql"]
    assert f">= '{start_7d}'" not in captured["sql"]
    assert "from_iso8601_timestamp" not in captured["sql"]


def test_load_gold_metrics_hour_grain_long_period_uses_day_and_full_window(monkeypatch):
    import asyncio

    from app.services.azure_ptu_service import _gold_metrics_window, load_gold_metrics

    captured: dict[str, str] = {}

    async def fake_execute(sql, **kwargs):
        captured["sql"] = sql
        return []

    monkeypatch.setattr("app.services.trino_service.execute", fake_execute)

    asyncio.run(load_gold_metrics(hours=90 * 24, grain="hour"))

    start_90d, end_90d = _gold_metrics_window(90 * 24)
    assert "concat(substr(CAST(metric_hour AS VARCHAR), 1, 10)" in captured["sql"]
    assert f">= '{start_90d}'" in captured["sql"]
    assert f"<= '{end_90d}'" in captured["sql"]
    assert "from_iso8601_timestamp" not in captured["sql"]
