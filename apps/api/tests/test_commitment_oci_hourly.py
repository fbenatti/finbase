from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import pandas as pd

from app.services.commitments.contracts import INSTRUMENTS
from app.services.commitments.engine import BALANCED, COMMIT, hour_grid
from app.services.commitments.providers.oci import compute_hourly_rows, read_usage

AS_OF = datetime(2026, 10, 1, tzinfo=timezone.utc)
START = AS_OF - timedelta(days=30)
HOURS = hour_grid(START, AS_OF)
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


def _silver(always_on_cost=2.0, part_time_cost=3.0):
    rows = []
    for hour in HOURS:
        rows.append({"usage_hour": hour, "resource_id": "ocid1.instance.a", "region": "sa-saopaulo-1",
                     "sku": "B93113", "currency": "BRL", "cost": always_on_cost})
        if int(hour[11:13]) < 12:
            rows.append({"usage_hour": hour, "resource_id": "ocid1.instance.b", "region": "sa-saopaulo-1",
                         "sku": "B93113", "currency": "BRL", "cost": part_time_cost})
    return rows


def test_read_usage_filters_the_window_and_builds_lines():
    queries = []

    def execute(sql, timeout_s):
        queries.append(sql)
        return _silver()

    snapshot = read_usage(CONNECTION, START, AS_OF, execute=execute)
    assert "source_connection_id = 7" in queries[0]
    assert "usage_hour >= '2026-09-01T00'" in queries[0] and "usage_hour < '2026-10-01T00'" in queries[0]
    assert snapshot.scope == "ocid1.tenancy.oc1..x" and snapshot.currency == "BRL"
    assert snapshot.observed_hours == set(HOURS)
    assert {line.resource_id for line in snapshot.lines} == {"ocid1.instance.a", "ocid1.instance.b"}


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
        provider="oci", read_usage=partial(read_usage, execute=lambda sql, timeout_s: _silver()),
    )
    with Session(engine, expire_on_commit=False) as db:
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
    # Average 3.5/h → 30 660/year; balanced commits 90% of it at the 20% discount.
    assert balanced.commit_cost_hour == round(30660 * 0.8 * 0.9 / 8760, 6)
    assert balanced.monthly_saving == round(30660 * 0.20 / 12, 2)
    assert balanced.evidence["commit_amount_annual"] == round(30660 * 0.8 * 0.9, 2)
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


def test_missing_hourly_table_explains_the_reprocess():
    import pytest

    def execute(sql, timeout_s):
        raise RuntimeError("line 1:102: Table 'iceberg_finbase.silver.oci_compute_hourly' does not exist")

    with pytest.raises(LookupError, match="reprocesse"):
        read_usage(CONNECTION, START, AS_OF, execute=execute)


def test_usage_api_connection_is_told_it_has_no_hourly_billing():
    import pytest

    connection = SimpleNamespace(id=4, code="oci", cloud_scope="oci", connector_type="oci",
                                 config_json={"extraction_mode": "usage_api"})
    with pytest.raises(LookupError, match="Usage API"):
        read_usage(connection, START, AS_OF, execute=lambda sql, timeout_s: _silver())
