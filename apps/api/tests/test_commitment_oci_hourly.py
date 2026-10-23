from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import pandas as pd

from app.services.commitments.contracts import INSTRUMENTS
from app.services.commitments.engine import BALANCED, COMMIT, DAY, day_grid
from app.services.commitments.providers.oci import compute_hourly_rows, read_usage

AS_OF = datetime(2026, 10, 1, tzinfo=timezone.utc)
START = AS_OF - timedelta(days=30)
DAYS = day_grid(START, AS_OF)
CONNECTION = SimpleNamespace(id=7, code="oci-prod", cloud_scope="oci", connector_type="oci",
                             config_json={"tenancy_ocid": "ocid1.tenancy.oc1..x"})


def test_compute_lines_are_kept_per_hour_and_resource():
    raw = pd.DataFrame([
        # Two report lines of the same instance and hour are summed.
        {"lineItem/intervalUsageStart": "2026-09-01T03:00Z", "product/service": "COMPUTE",
         "product/resourceId": "ocid1.instance.a", "product/region": "sa-saopaulo-1", "cost/productSku": "B93113",
         "cost/myCost": "0.30", "usage/billedQuantity": "1", "cost/currencyCode": "BRL"},
        {"lineItem/intervalUsageStart": "2026-09-01T03:00Z", "product/service": "COMPUTE",
         "product/resourceId": "ocid1.instance.a", "product/region": "sa-saopaulo-1", "cost/productSku": "B93113",
         "cost/myCost": "0.20", "usage/billedQuantity": "1", "cost/currencyCode": "BRL"},
        {"lineItem/intervalUsageStart": "2026-09-01T04:00Z", "product/service": "COMPUTE",
         "product/resourceId": "ocid1.instance.a", "product/region": "sa-saopaulo-1", "cost/productSku": "B93113",
         "cost/myCost": "0.50", "usage/billedQuantity": "1", "cost/currencyCode": "BRL"},
        {"lineItem/intervalUsageStart": "2026-09-01T03:00Z", "product/service": "BLOCK_STORAGE",
         "product/resourceId": "ocid1.volume.b", "product/region": "sa-saopaulo-1", "cost/productSku": "B91961",
         "cost/myCost": "9.99", "usage/billedQuantity": "1", "cost/currencyCode": "BRL"},
    ])
    rows = compute_hourly_rows(raw, connection_id=7, billing_period="202609")
    assert sorted(rows["usage_hour"]) == ["2026-09-01T03", "2026-09-01T04"]
    assert rows.set_index("usage_hour").loc["2026-09-01T03", "billed_cost"] == 0.5
    assert set(rows["resource_id"]) == {"ocid1.instance.a"}
    assert set(rows["source_connection_id"]) == {7} and set(rows["_billing_period"]) == {"202609"}


def test_report_without_cost_columns_yields_nothing():
    assert compute_hourly_rows(pd.DataFrame([{"foo": 1}]), connection_id=7, billing_period="202609").empty


def _silver(ocpus=2.0, part_time_ocpus=4.0, price=1.0):
    """Daily billing: an always-on 2-OCPU instance and a 4-OCPU one running 12 h per day."""
    rows = []
    for day in DAYS:
        rows.append({"usage_day": day, "region": "sa-saopaulo-1", "service": "Compute", "sku": "B93113",
                     "sku_meter": "Compute - Standard - E4 - OCPU", "unit": "OCPU Hours", "currency": "BRL",
                     "quantity": (ocpus * 24 + part_time_ocpus * 12), "cost": (ocpus * 24 + part_time_ocpus * 12) * price})
        # Flat fee billed without quantity keeps its cost.
        rows.append({"usage_day": day, "region": None, "service": "Support", "sku": "FEE", "sku_meter": "fee",
                     "unit": None, "currency": "BRL", "quantity": 0.0, "cost": 2.4})
    return rows


def test_read_usage_reads_daily_quantity_without_hourly_data():
    queries = []

    def execute(sql, timeout_s):
        queries.append(sql)
        return _silver()

    snapshot = read_usage(CONNECTION, START, AS_OF, execute=execute)
    assert "dest_billing_cloud" in queries[0] and "oci_compute_hourly" not in queries[0]
    assert "source_connection_id = 7" in queries[0]
    assert "_billing_period IN ('202609')" in queries[0]
    assert "chargeperiodstart >= '2026-09-01'" in queries[0] and "chargeperiodstart < '2026-10-01'" in queries[0]
    assert snapshot.grain == DAY and snapshot.observed_hours == set(DAYS)
    assert snapshot.scope == "ocid1.tenancy.oc1..x" and snapshot.currency == "BRL"
    # 96 OCPU hours per day = 4 OCPUs running on average at 1.00 per OCPU hour → 4.00/h; the fee adds 0.10/h.
    per_day = {}
    for line in snapshot.lines:
        per_day[line.hour_utc] = per_day.get(line.hour_utc, 0.0) + line.ondemand_cost
    assert set(round(value, 6) for value in per_day.values()) == {4.1}
    top = snapshot.basis["top_skus"][0]
    assert top["units_in_use"] == 4.0 and top["unit_price"] == 1.0 and top["quantity_per_day"] == 96.0


def test_each_unit_of_measure_becomes_units_in_use():
    from app.services.commitments.providers.oci import COUNTED_UNIT, MONTHLY_UNIT, unit_kind, units_in_use

    # As billed per day by OCI: a 4-OCPU instance all day, 14 000 GB of block volume in October.
    assert units_in_use(96.0, unit_kind("OCPU Hours"), "2026-10-10") == 4.0
    assert units_in_use(384.0, unit_kind("Gigabyte Per Hour"), "2026-10-10") == 16.0
    assert unit_kind("GB Months") == unit_kind("Desktops per Month") == MONTHLY_UNIT
    assert round(units_in_use(14000 / 31, MONTHLY_UNIT, "2026-10-10"), 6) == 14000
    assert round(units_in_use(14000 / 30, MONTHLY_UNIT, "2026-09-10"), 6) == 14000
    assert unit_kind("10K Requests") == unit_kind("Million Datapoints") == COUNTED_UNIT
    assert units_in_use(5.0, COUNTED_UNIT, "2026-10-10") is None


def test_unit_price_is_the_effective_price_of_the_window():
    rows = _silver()
    # Same quantity both days, billed at 1.00 then 3.00: each day is priced at the 2.00 average.
    rows = [dict(row, usage_day=DAYS[0]) for row in rows[:2]] + [
        dict(rows[2], usage_day=DAYS[1], cost=rows[2]["quantity"] * 3.0), dict(rows[3], usage_day=DAYS[1])]
    snapshot = read_usage(CONNECTION, START, AS_OF, execute=lambda sql, timeout_s: rows)
    compute = [line.ondemand_cost for line in snapshot.lines if line.family == "B93113"]
    assert compute == [96 * 2.0 / 24] * 2


def test_window_without_billing_explains_what_is_missing():
    import pytest

    with pytest.raises(LookupError, match="quantidade diária"):
        read_usage(CONNECTION, START, AS_OF, execute=lambda sql, timeout_s: [])


def test_oci_run_sizes_universal_credits_from_projected_spend(tmp_path):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.database import Base
    import app.models.financial_contract  # noqa: F401  (FK target)
    import app.models.source_connection  # noqa: F401  (FK target)
    from app.models.commitment import CommitmentOffer, CommitmentRecommendation
    from app.services.commitments.runner import (
        CommitmentProvider, commitment_provider_for, run_commitment_engine, supports_connection,
    )

    assert commitment_provider_for(CONNECTION).provider == "oci"
    monitoring = SimpleNamespace(id=8, code="oci-metrics", cloud_scope="oci", connector_type="oci_monitoring")
    assert not supports_connection(monitoring)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in (
        "commitment_offer", "commitment_reco_run", "commitment_recommendation")])
    registration = CommitmentProvider(
        provider="oci", read_usage=partial(read_usage, execute=lambda sql, timeout_s: _silver(part_time_ocpus=3.0)),
    )
    with Session(engine, expire_on_commit=False) as db:
        run = run_commitment_engine(db, CONNECTION, registration=registration, as_of=AS_OF, adjustments=[])
        assert run.status == "COMPLETED", run.error_message
        missing = db.execute(select(CommitmentRecommendation).where(
            CommitmentRecommendation.run_id == run.id)).scalar_one()
        # Without a negotiated discount the baseline is shown but no saving is invented.
        assert missing.readiness_status == "MISSING_OFFER" and missing.action_type == "NO_ACTION"
        assert "31536.00/ano" in missing.rationale[0]

        db.add(CommitmentOffer(provider="oci", instrument="oci_universal_credits", term_months=12,
                               payment_option="CONTRACT", source="NEGOTIATED", pricing_mode="DISCOUNT",
                               discount_pct=0.20))
        db.commit()
        run = run_commitment_engine(db, CONNECTION, registration=registration, as_of=AS_OF, adjustments=[])
        assert run.status == "COMPLETED", run.error_message
        balanced = db.execute(select(CommitmentRecommendation).where(
            CommitmentRecommendation.run_id == run.id, CommitmentRecommendation.profile == BALANCED,
        )).scalar_one()
    assert balanced.action_type == COMMIT
    assert balanced.purchase_path == INSTRUMENTS["oci_universal_credits"].purchase_path == "negotiated"
    # A spend pool is drawn over the year: idle hours of the 12 h/day instance do not matter.
    # 84 OCPU hours/day at 1.00 + 2.40 fee = 86.40/day = 3.6/h → 31 536/year; balanced commits 90% at 20% off.
    assert balanced.commit_cost_hour == round(31536 * 0.8 * 0.9 / 8760, 6)
    assert balanced.monthly_saving == round(31536 * 0.20 / 12, 2)
    assert balanced.evidence["commit_amount_annual"] == round(31536 * 0.8 * 0.9, 2)
    assert balanced.evidence["hourly"]["grain"] == DAY
    assert balanced.evidence["usage_basis"]["top_skus"][0]["units_in_use"] == 3.5
    assert any("quantidade faturada por dia" in line for line in balanced.rationale)
    assert balanced.currency == "BRL"


def test_hourly_write_failure_never_fails_the_cost_load(monkeypatch):
    from app.services import task_runner

    def boom(*args, **kwargs):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(task_runner.iceberg, "replace_oci_compute_hourly", boom)
    monkeypatch.setattr(task_runner, "_persist_download_progress", lambda *args, **kwargs: None)
    part = pd.DataFrame([{"usage_hour": "2026-09-01T03", "resource_id": "a", "region": "r", "sku": "s",
                          "sku_description": None, "currency": "BRL", "billed_cost": 1.0, "billed_quantity": 1.0,
                          "source_connection_id": 7, "_billing_period": "202609"}])
    task_runner._write_oci_compute_hourly(None, 7, "202609", [part, part])


def _usage(hour, service="Compute", cost=1.0, resource="ocid1.instance.a"):
    return SimpleNamespace(time_usage_started=datetime.fromisoformat(f"{hour}:00:00+00:00"), service=service,
                           resource_id=resource, region="sa-saopaulo-1", sku_part_number="B93113",
                           currency="BRL", computed_amount=cost, computed_quantity=1.0)


def test_usage_api_hourly_keeps_only_compute_and_queries_one_day_at_a_time(monkeypatch):
    from app.services import oci_extractor
    from app.services.oci_extractor import OciExtractor

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 3, 15, tzinfo=timezone.utc)

    monkeypatch.setattr(oci_extractor, "datetime", FrozenDatetime)
    calls = []

    def summarized(self, start, end, granularity, group_by):
        calls.append((start, end, granularity))
        day = start.strftime("%Y-%m-%d")
        return [_usage(f"{day}T03", cost=0.5), _usage(f"{day}T03", cost=0.25),
                _usage(f"{day}T03", service="Block Storage", cost=9.0)]

    monkeypatch.setattr(OciExtractor, "_summarized_usages", summarized)
    frame, first_hour, end_hour = OciExtractor.fetch_compute_hourly_month(object.__new__(OciExtractor), "202609", 4)
    assert (first_hour, end_hour) == ("2026-09-01T00", "2026-09-04T00")
    # 1 Sep → 4 Sep (today + 1 day), one query per day, all HOURLY.
    assert [(start.day, (end - start).days, granularity) for start, end, granularity in calls] == [
        (1, 1, "HOURLY"), (2, 1, "HOURLY"), (3, 1, "HOURLY")]
    assert sorted(frame["usage_hour"]) == ["2026-09-01T03", "2026-09-02T03", "2026-09-03T03"]
    assert set(frame["billed_cost"]) == {0.75}
    assert set(frame["source_connection_id"]) == {4} and set(frame["_billing_period"]) == {"202609"}
    # The Usage API serves HOURLY only for the last 31 days: August starts on the 4th (today − 30 days).
    calls.clear()
    _, first_hour, end_hour = OciExtractor.fetch_compute_hourly_month(object.__new__(OciExtractor), "202608", 4)
    assert (first_hour, end_hour) == ("2026-08-04T00", "2026-09-01T00") and calls[0][0].day == 4
    # A month entirely before the window keeps whatever series is stored.
    assert OciExtractor.fetch_compute_hourly_month(object.__new__(OciExtractor), "202607", 4) is None


def test_failed_usage_api_hourly_fetch_keeps_the_stored_series(monkeypatch):
    from app.services import task_runner

    class Broken:
        def fetch_compute_hourly_month(self, billing_period, connection_id):
            raise RuntimeError("429 TooManyRequests")

    monkeypatch.setattr(task_runner, "_persist_download_progress", lambda *args, **kwargs: None)
    assert task_runner._fetch_oci_usage_api_hourly(None, Broken(), 4, "202609") is None


def test_usage_api_write_replaces_only_the_fetched_hours(monkeypatch):
    from app.services import task_runner

    captured = {}
    monkeypatch.setattr(task_runner.iceberg, "replace_oci_compute_hourly",
                        lambda frame, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(task_runner, "_persist_download_progress", lambda *args, **kwargs: None)

    class Fetcher:
        def fetch_compute_hourly_month(self, billing_period, connection_id):
            return pd.DataFrame(), "2026-09-23T00", "2026-10-01T00"

    parts, hour_range = task_runner._fetch_oci_usage_api_hourly(None, Fetcher(), 4, "202609")
    task_runner._write_oci_compute_hourly(None, 4, "202609", parts, hour_range)
    assert captured == {"connection_id": 4, "billing_period": "202609", "hour_range": ("2026-09-23T00", "2026-10-01T00")}
