import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.anomaly_v2 import case_series as series
from app.services.anomaly_v2.baseline import baseline_reference, expected_for_day, weekday_factors


def test_one_weekend_observation_does_not_create_weekly_seasonality():
    start = date(2026, 8, 31)
    rows = []
    for index in range(47):
        day = start + timedelta(days=index)
        if day.weekday() < 5:
            cost = 0.02 if day < date(2026, 10, 9) else 1000
            rows.append({"day": day.isoformat(), "cost_cloud": cost})
        elif day in (date(2026, 10, 10), date(2026, 10, 11)):
            rows.append({"day": day.isoformat(), "cost_cloud": 700})
    factors = weekday_factors(rows, as_of=date(2026, 10, 16))
    assert factors[5] == factors[6] == 1
    expected, *_ = expected_for_day(rows, target=date(2026, 10, 17))
    assert expected <= 1000


def test_repeated_weekly_pattern_remains_supported():
    start = date(2026, 8, 31)
    rows = [{"day": (start + timedelta(days=index)).isoformat(),
             "cost_cloud": 200 if (start + timedelta(days=index)).weekday() == 5 else 100}
            for index in range(42)]
    factors = weekday_factors(rows, as_of=date(2026, 10, 11))
    assert factors[5] == 2
    assert factors[0] == 1


def test_chart_uses_same_frozen_model_as_detector_without_absorbing_new_spend():
    start = date(2026, 9, 1)
    event = date(2026, 10, 9)
    end = date(2026, 10, 18)
    rows = [{"day": (start + timedelta(days=index)).isoformat(), "cost_cloud": 10}
            for index in range((event - start).days)]
    reference = baseline_reference(rows, reference_day=event)
    rows += [{"day": (event + timedelta(days=index)).isoformat(), "cost_cloud": 2000}
             for index in range((end - event).days + 1)]
    points = series._chart_items({row["day"]: row["cost_cloud"] for row in rows}, rows,
                                start=start, end=end, as_of=end, frozen_reference=reference)
    after = [point for point in points if point["event_date"] >= event.isoformat()]
    assert all(point["expected_cloud"] == 10 for point in after)
    assert all(point["observed_cloud"] == 2000 for point in after)


def test_raw_date_filter_is_small_conservative_and_uses_native_column():
    sql = series.raw_date_range_predicate("chargeperiodstart", date(2026, 8, 29), date(2026, 10, 18))
    assert sql.count(" OR ") == 2
    assert "CAST(" not in sql and "starts_with" not in sql
    assert len(sql) < 500
    native = series.raw_date_range_predicate("day", date(2026, 10, 1), date(2026, 10, 18), "date")
    assert native == '"day" BETWEEN DATE \'2026-10-01\' AND DATE \'2026-10-18\''


def test_selective_scope_comparison_precedes_normalization_and_escapes_values():
    sql = series._scope_sql_filter({"resource_group": "rg-test", "servicename": "service"},
                                  {"resource_group": ["Rg-Test", " RG-TEST '"]})
    assert sql.startswith('(\"source_resource_group_name\" IN')
    assert "CASE WHEN" in sql
    assert 'lower(trim(CAST("source_resource_group_name"' not in sql
    assert "RG-TEST ''" in sql
    assert "FALSE" in series._scope_sql_filter({"resource_group": "missing"}, {"resource_group": []})


def test_legacy_case_keeps_recorded_reference_and_all_queries_share_snapshot(monkeypatch):
    async def scenario():
        case = SimpleNamespace(id=123, series_id="scope", event_date="2026-10-13", episode_start="2026-10-09",
            detector="SPIKE", expected_cloud=.72, lower_cloud=0, upper_cloud=3.39,
            series_dimensions={"resource_group": "rg-test", "cloud_origem": "cloud", "servicename": "service"})
        config = SimpleNamespace(source_namespace="silver", destination_physical_name="focus",
            period_column="_billing_period", type_column="tipo", date_column="chargeperiodstart", measure_column="realizado_cloud")
        types = {name: "string" for name in ["chargeperiodstart", "_billing_period", "source_resource_group_name", "servicename"]}
        types["source_connection_id"] = "long"
        monkeypatch.setattr(series, "source_read_metadata", AsyncMock(return_value=(types, 12345)))
        monkeypatch.setattr(series, "connection_ids_for_cloud", AsyncMock(return_value=[2]))

        async def execute(sql, **kwargs):
            assert "FOR VERSION AS OF 12345" in sql
            assert kwargs["max_retries"] == 1
            if "AS scope_value" in sql:
                return [{"scope_value": "Rg-Test"}]
            if 'AS "resource_group"' in sql:
                return [{"day_raw": "10/13/2026", "period": "202610", "resource_group": "Rg-Test", "cost_cloud": 1884.73}]
            return [{"day_raw": "10/08/2026", "period": "202610", "cost_cloud": .02},
                    {"day_raw": "10/13/2026", "period": "202610", "cost_cloud": 1884.73},
                    {"day_raw": "10/17/2026", "period": "202610", "cost_cloud": 228.26}]

        query = AsyncMock(side_effect=execute)
        monkeypatch.setattr(series, "trino_execute", query)
        result = await series._read_v2_case_series(case, config, as_of=date(2026, 10, 18))
        event = next(point for point in result["items"] if point["event_date"] == case.event_date)
        saturday = next(point for point in result["items"] if point["event_date"] == "2026-10-17")
        assert event["observed_cloud"] == 1884.73
        assert event["expected_cloud"] == saturday["expected_cloud"] == .72
        assert saturday["upper_cloud"] == 3.39
        assert result["reference"]["data_cutoff"] == "2026-10-08"
        assert query.await_count == 3

    asyncio.run(scenario())
