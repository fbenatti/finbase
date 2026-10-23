"""Resolve the effective price of each instrument × term × payment for a key.

Two layers: MARKET (list commitment prices) and NEGOTIATED (customer deals).
For each (instrument, term, payment) the most specific valid NEGOTIATED offer
wins; otherwise the most specific valid MARKET offer. A negotiated offer in
EXTRA_OVER_MARKET mode stacks on top of the market discount and needs one.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from .contracts import KEY_FIELDS, CommitmentKey, Offer, OfferRecord

MARKET = "MARKET"
NEGOTIATED = "NEGOTIATED"
DISCOUNT = "DISCOUNT"
EXTRA_OVER_MARKET = "EXTRA_OVER_MARKET"

UPFRONT_FRACTION = {"NO_UPFRONT": 0.0, "PARTIAL_UPFRONT": 0.5, "ALL_UPFRONT": 1.0, "CONTRACT": 0.0}


def _applies(record: OfferRecord, key: CommitmentKey, as_of: date) -> bool:
    if record.provider != key.provider or record.instrument != key.instrument:
        return False
    if record.valid_from and as_of < record.valid_from:
        return False
    if record.valid_until and as_of > record.valid_until:
        return False
    if record.scope and record.scope != key.scope:
        return False
    return all(getattr(record, name) in (None, getattr(key, name)) for name in KEY_FIELDS)


def _specificity(record: OfferRecord) -> tuple[int, int, int]:
    matched = sum(1 for name in KEY_FIELDS if getattr(record, name) is not None) + (1 if record.scope else 0)
    # Ties: higher priority, then the newest row.
    return matched, record.priority, record.offer_id or 0


def _best(records: list[OfferRecord]) -> OfferRecord | None:
    return max(records, key=_specificity) if records else None


def resolve_offers(records: Iterable[OfferRecord], key: CommitmentKey, as_of: date) -> list[Offer]:
    """Effective offers for the key: one per (term, payment) plus one per spend tier.

    A negotiated offer without a tier overrides the market one of the same
    (term, payment); tiered negotiated offers (``min_commit_annual``) are extra
    options the engine weighs against the projected spend.
    """
    market: dict[tuple[int, str], list[OfferRecord]] = {}
    negotiated: dict[tuple[int, str, float], list[OfferRecord]] = {}
    for record in records:
        if not _applies(record, key, as_of):
            continue
        if record.source == MARKET:
            market.setdefault((record.term_months, record.payment_option), []).append(record)
        else:
            tier = float(record.min_commit_annual or 0.0)
            negotiated.setdefault((record.term_months, record.payment_option, tier), []).append(record)
    chosen: list[tuple[OfferRecord, float, dict]] = []
    for (term, payment, tier), group in negotiated.items():
        deal = _best(group)
        base_market = _best(market.get((term, payment), []))
        if deal.pricing_mode == EXTRA_OVER_MARKET:
            if base_market is None:
                continue
            discount = 1 - (1 - base_market.discount_pct) * (1 - deal.discount_pct)
            chosen.append((deal, discount, {"market_offer_id": base_market.offer_id,
                                            "market_discount": base_market.discount_pct,
                                            "extra_discount": deal.discount_pct}))
        else:
            chosen.append((deal, deal.discount_pct, {}))
    overridden = {(term, payment) for (term, payment, tier), group in negotiated.items()
                  if tier == 0 and any(deal is _best(group) for deal, _, _ in chosen)}
    for (term, payment), group in market.items():
        if (term, payment) not in overridden:
            best = _best(group)
            chosen.append((best, best.discount_pct, {}))
    offers = [
        Offer(
            instrument=key.instrument, term_months=record.term_months, payment_option=record.payment_option,
            discount=round(discount, 6), source=record.source, offer_id=record.offer_id,
            upfront_fraction=UPFRONT_FRACTION.get(record.payment_option, 0.0), burn_down=record.burn_down,
            min_commit_annual=record.min_commit_annual if record.source != MARKET else None,
            basis={"pricing_mode": record.pricing_mode, **basis},
        )
        for record, discount, basis in chosen if 0 < discount < 1
    ]
    return sorted(offers, key=lambda offer: (offer.term_months, offer.payment_option, offer.min_commit_annual or 0.0))
