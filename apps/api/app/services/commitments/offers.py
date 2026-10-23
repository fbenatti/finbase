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
    """One effective offer per (term, payment) for the key, sorted by term and payment."""
    groups: dict[tuple[int, str], dict[str, list[OfferRecord]]] = {}
    for record in records:
        if not _applies(record, key, as_of):
            continue
        layer = groups.setdefault((record.term_months, record.payment_option), {MARKET: [], NEGOTIATED: []})
        layer.setdefault(record.source, []).append(record)
    offers: list[Offer] = []
    for (term, payment), layer in sorted(groups.items()):
        market = _best(layer.get(MARKET, []))
        negotiated = _best(layer.get(NEGOTIATED, []))
        chosen, discount, basis = None, None, {}
        if negotiated is not None:
            if negotiated.pricing_mode == EXTRA_OVER_MARKET:
                if market is not None:
                    discount = 1 - (1 - market.discount_pct) * (1 - negotiated.discount_pct)
                    chosen = negotiated
                    basis = {"market_offer_id": market.offer_id, "market_discount": market.discount_pct,
                             "extra_discount": negotiated.discount_pct}
            else:
                chosen, discount = negotiated, negotiated.discount_pct
        if chosen is None and market is not None:
            chosen, discount = market, market.discount_pct
        if chosen is None or discount is None or not 0 < discount < 1:
            continue
        offers.append(Offer(
            instrument=key.instrument, term_months=term, payment_option=payment, discount=round(discount, 6),
            source=chosen.source, offer_id=chosen.offer_id,
            upfront_fraction=UPFRONT_FRACTION.get(payment, 0.0), burn_down=chosen.burn_down,
            basis={"pricing_mode": chosen.pricing_mode, **basis},
        ))
    return offers
