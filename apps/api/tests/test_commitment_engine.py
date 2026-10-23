from datetime import date, datetime, timedelta, timezone

from app.services.commitments.contracts import (
    INSTRUMENTS, Adjustment, CommitmentKey, ExistingCommitment, OfferRecord, UsageLine,
)
from app.services.commitments.engine import (
    AGGRESSIVE, BALANCED, COMMIT, CONSERVATIVE, INSUFFICIENT_HOURLY_DATA, MISSING_OFFER, NO_ACTION,
    NO_UNCOVERED_USAGE, READY, analyze_key, group_usage, hour_grid,
)
from app.services.commitments.offers import resolve_offers

AS_OF = datetime(2026, 10, 1, tzinfo=timezone.utc)
START = AS_OF - timedelta(days=30)
HOURS = hour_grid(START, AS_OF)
SP = INSTRUMENTS["aws_compute_savings_plan"]
RI = INSTRUMENTS["aws_reserved_instance"]
OCI = INSTRUMENTS["oci_universal_credits"]
KEY = CommitmentKey(provider="aws", instrument=SP.code, scope="acct-1")


def _market(discount, term=12, payment="NO_UPFRONT", offer_id=1, instrument=SP.code, provider="aws", **match):
    return OfferRecord(offer_id=offer_id, provider=provider, instrument=instrument, term_months=term,
                       payment_option=payment, source="MARKET", pricing_mode="DISCOUNT",
                       discount_pct=discount, **match)


def _analyze(series, offers, key=KEY, instrument=SP, observed=None, **kwargs):
    return analyze_key(
        key=key, instrument=instrument, series=series, window_hours=HOURS,
        observed_hours=set(HOURS) if observed is None else observed,
        offers=resolve_offers(offers, key, AS_OF.date()), as_of=AS_OF, currency="USD", **kwargs,
    )


def _by_profile(results):
    return {item["profile"]: item for item in results}


def test_daily_average_would_be_a_false_positive_but_hourly_recommends_nothing():
    # 10/h during 12 h, idle the other 12 h: the daily average is 5/h, but a 5/h
    # commitment runs at 50% utilization, under the 60% break-even of a 40% discount.
    series = {hour: (10.0 if int(hour[11:13]) < 12 else 0.0) for hour in HOURS}
    results = _by_profile(_analyze(series, [_market(0.40)]))
    assert all(item["action_type"] == NO_ACTION for item in results.values())
    assert results[BALANCED]["readiness_status"] == READY


def test_steady_base_load_is_committed_at_the_optimal_quantile():
    # Base 4/h always, plus 6/h during 6 h of the day.
    series = {hour: 4.0 + (6.0 if int(hour[11:13]) < 6 else 0.0) for hour in HOURS}
    results = _by_profile(_analyze(series, [_market(0.30)]))
    balanced = results[BALANCED]
    assert balanced["action_type"] == COMMIT
    # Peak hours are 25% of the time, below the 70% needed to pay the extra: commit the base.
    assert balanced["commit_ondemand_hour"] == 4.0
    assert balanced["expected_utilization"] == 1.0
    assert balanced["monthly_saving"] == round(4.0 * 0.30 * 730, 2)
    assert balanced["purchase_path"] == "self_service"
    assert results[CONSERVATIVE]["commit_ondemand_hour"] <= balanced["commit_ondemand_hour"]
    assert results[AGGRESSIVE]["commit_ondemand_hour"] >= balanced["commit_ondemand_hour"]


def test_missing_hours_block_sizing():
    series = {hour: 5.0 for hour in HOURS}
    observed = set(HOURS[: len(HOURS) // 2])
    (result,) = _analyze(series, [_market(0.30)], observed=observed)
    assert result["readiness_status"] == INSUFFICIENT_HOURLY_DATA
    assert result["action_type"] == NO_ACTION


def test_existing_commitment_covers_usage_unless_it_expires_soon():
    series = {hour: 5.0 for hour in HOURS}
    lasting = ExistingCommitment("sp-1", SP.code, 5.0, AS_OF + timedelta(days=400))
    (result,) = _analyze(series, [_market(0.30)], existing=[lasting])
    assert result["readiness_status"] == NO_UNCOVERED_USAGE

    expiring = ExistingCommitment("sp-2", SP.code, 5.0, AS_OF + timedelta(days=30))
    balanced = _by_profile(_analyze(series, [_market(0.30)], existing=[expiring]))[BALANCED]
    assert balanced["action_type"] == COMMIT and balanced["commit_ondemand_hour"] == 5.0
    assert any("sp-2" in risk for risk in balanced["risks"])


def test_rightsizing_adjustment_reduces_the_commitment():
    series = {hour: 5.0 for hour in HOURS}
    cut = Adjustment(kind="RIGHTSIZING", ondemand_hour=2.0, reference="opp-7")
    balanced = _by_profile(_analyze(series, [_market(0.30)], adjustments=[cut]))[BALANCED]
    assert balanced["commit_ondemand_hour"] == 3.0


def test_without_offer_the_key_is_not_priced():
    (result,) = _analyze({hour: 5.0 for hour in HOURS}, [])
    assert result["readiness_status"] == MISSING_OFFER


def test_best_npv_offer_wins_between_terms():
    series = {hour: 5.0 for hour in HOURS}
    offers = [_market(0.25, term=12, offer_id=1), _market(0.50, term=36, offer_id=2)]
    balanced = _by_profile(_analyze(series, offers))[BALANCED]
    assert balanced["term_months"] == 36 and balanced["offer_id"] == 2
    assert len(balanced["evidence"]["options"]) == 6


def test_upfront_payment_has_payback_and_npv_below_nominal_saving():
    series = {hour: 5.0 for hour in HOURS}
    balanced = _by_profile(_analyze(series, [_market(0.40, payment="ALL_UPFRONT")]))[BALANCED]
    assert balanced["upfront_cost"] == round(5.0 * 0.60 * 730 * 12, 2)
    assert balanced["payback_months"] > 0
    assert balanced["npv"] < balanced["term_saving"]


def test_negotiated_offer_overrides_market_and_can_stack():
    key = CommitmentKey(provider="aws", instrument=RI.code, scope="acct-1", region="us-east-1", family="m5")
    market = _market(0.30, instrument=RI.code)
    flat = OfferRecord(offer_id=9, provider="aws", instrument=RI.code, term_months=12, payment_option="NO_UPFRONT",
                       source="NEGOTIATED", pricing_mode="DISCOUNT", discount_pct=0.45, region="us-east-1")
    (offer,) = resolve_offers([market, flat], key, AS_OF.date())
    assert offer.source == "NEGOTIATED" and offer.discount == 0.45

    stacked = OfferRecord(offer_id=10, provider="aws", instrument=RI.code, term_months=12,
                          payment_option="NO_UPFRONT", source="NEGOTIATED", pricing_mode="EXTRA_OVER_MARKET",
                          discount_pct=0.10, family="m5", region="us-east-1")
    (offer,) = resolve_offers([market, flat, stacked], key, AS_OF.date())
    assert offer.offer_id == 10 and offer.discount == round(1 - 0.70 * 0.90, 6)

    expired = OfferRecord(offer_id=11, provider="aws", instrument=RI.code, term_months=12,
                          payment_option="NO_UPFRONT", source="NEGOTIATED", pricing_mode="DISCOUNT",
                          discount_pct=0.60, valid_until=date(2026, 1, 1))
    other_family = _market(0.50, instrument=RI.code, offer_id=12, family="c5")
    (offer,) = resolve_offers([market, expired, other_family], key, AS_OF.date())
    assert offer.offer_id == 1


def test_oci_spend_commitment_is_flagged_as_negotiated_process():
    key = CommitmentKey(provider="oci", instrument=OCI.code, scope="tenancy-1")
    offer = OfferRecord(offer_id=5, provider="oci", instrument=OCI.code, term_months=12, payment_option="CONTRACT",
                        source="NEGOTIATED", pricing_mode="DISCOUNT", discount_pct=0.20)
    balanced = _by_profile(_analyze({hour: 8.0 for hour in HOURS}, [offer], key=key, instrument=OCI))[BALANCED]
    assert balanced["action_type"] == COMMIT
    assert balanced["purchase_path"] == "negotiated"
    assert any("não tem reserva" in risk for risk in balanced["risks"])


def test_usage_is_grouped_per_instrument_key_fields():
    lines = [
        UsageLine("2026-09-01T00", 1.0, region="us-east-1", family="m5", platform="linux"),
        UsageLine("2026-09-01T00", 2.0, region="us-east-1", family="c5", platform="linux"),
    ]
    series = group_usage(lines, provider="aws", scope="acct-1", instruments=[SP, RI])
    assert series[KEY] == {"2026-09-01T00": 3.0}
    ri_m5 = CommitmentKey("aws", RI.code, "acct-1", "us-east-1", "m5", "linux")
    assert series[ri_m5] == {"2026-09-01T00": 1.0}


def test_inputs_hash_is_stable():
    series = {hour: 5.0 for hour in HOURS}
    first = _analyze(series, [_market(0.30)])[0]["inputs_hash"]
    assert _analyze(dict(reversed(list(series.items()))), [_market(0.30)])[0]["inputs_hash"] == first


def test_runner_persists_recommendations_and_keeps_state(tmp_path):
    from types import SimpleNamespace

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.database import Base
    import app.models.financial_contract  # noqa: F401  (FK target)
    import app.models.source_connection  # noqa: F401  (FK target)
    from app.models.commitment import CommitmentOffer, CommitmentRecoRun, CommitmentRecommendation
    from app.services.commitments.runner import CommitmentProvider, UsageSnapshot, run_commitment_engine

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in (
        "commitment_offer", "commitment_reco_run", "commitment_recommendation")])
    lines = [UsageLine(hour, 5.0, region="us-east-1", family="m5", platform="linux") for hour in HOURS]
    registration = CommitmentProvider(
        provider="aws",
        read_usage=lambda connection, start, end: UsageSnapshot("acct-1", "USD", set(HOURS), lines),
    )
    connection = SimpleNamespace(id=1, cloud_scope="aws")
    with Session(engine, expire_on_commit=False) as db:
        db.add(CommitmentOffer(provider="aws", instrument=SP.code, term_months=12, payment_option="NO_UPFRONT",
                               source="MARKET", pricing_mode="DISCOUNT", discount_pct=0.30))
        db.commit()
        run = run_commitment_engine(db, connection, registration=registration, as_of=AS_OF, adjustments=[])
        assert run.status == "COMPLETED", run.error_message
        assert run.recommendations_count == 1
        assert run.total_monthly_saving == round(5.0 * 0.30 * 730, 2)
        assert run.readiness_counts == {"READY": 3, "MISSING_OFFER": 3}
        chosen = db.execute(select(CommitmentRecommendation).where(
            CommitmentRecommendation.instrument == SP.code, CommitmentRecommendation.profile == BALANCED,
        )).scalar_one()
        chosen.state = "APPROVED"
        db.commit()

        rerun = run_commitment_engine(db, connection, registration=registration, as_of=AS_OF, adjustments=[])
        again = db.execute(select(CommitmentRecommendation).where(
            CommitmentRecommendation.run_id == rerun.id, CommitmentRecommendation.instrument == SP.code,
            CommitmentRecommendation.profile == BALANCED,
        )).scalar_one()
        assert again.state == "APPROVED"
        assert db.get(CommitmentRecoRun, rerun.id).status == "COMPLETED"
