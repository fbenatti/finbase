"""Provider-neutral commitment (RI / Savings Plan / CUD / spend commitment) contracts.

The engine speaks one unit only: on-demand-equivalent cost per hour. A
commitment of ``c`` covers up to ``c`` of that cost each hour and costs
``c × (1 − discount)`` whether used or not. Each cloud plugs in by reading its
hourly eligible usage into ``UsageLine`` rows and its active commitments into
``ExistingCommitment``; the instrument decides which fields form the key.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

SELF_SERVICE = "self_service"
NEGOTIATED = "negotiated"

KEY_FIELDS = ("region", "family", "platform")


@dataclass(frozen=True)
class Instrument:
    code: str
    provider: str
    label: str
    # Fields of ``UsageLine`` that the commitment is bound to; the rest is flexible.
    key_fields: tuple[str, ...]
    purchase_path: str
    terms_months: tuple[int, ...]
    payment_options: tuple[str, ...] = ("NO_UPFRONT",)
    process_note: str = ""


_SELF_SERVICE_NOTE = "Compra self-service no portal ou via API da cloud."
_NEGOTIATED_NOTE = (
    "Não é uma compra no portal: é um compromisso de gasto negociado ou renovado com o provider. "
    "O desconto esperado vem do catálogo negociado."
)
_PAYMENTS = ("NO_UPFRONT", "PARTIAL_UPFRONT", "ALL_UPFRONT")

INSTRUMENTS: dict[str, Instrument] = {item.code: item for item in (
    Instrument("aws_compute_savings_plan", "aws", "AWS Compute Savings Plan", (), SELF_SERVICE,
               (12, 36), _PAYMENTS, _SELF_SERVICE_NOTE),
    Instrument("aws_ec2_instance_savings_plan", "aws", "AWS EC2 Instance Savings Plan", ("region", "family"),
               SELF_SERVICE, (12, 36), _PAYMENTS, _SELF_SERVICE_NOTE),
    Instrument("aws_reserved_instance", "aws", "AWS Reserved Instance", ("region", "family", "platform"),
               SELF_SERVICE, (12, 36), _PAYMENTS, _SELF_SERVICE_NOTE),
    Instrument("aws_edp", "aws", "AWS EDP (compromisso de gasto)", (), NEGOTIATED, (12, 36, 60), (),
               _NEGOTIATED_NOTE),
    Instrument("azure_savings_plan", "azure", "Azure Savings Plan for Compute", (), SELF_SERVICE,
               (12, 36), ("NO_UPFRONT", "ALL_UPFRONT"), _SELF_SERVICE_NOTE),
    Instrument("azure_reservation", "azure", "Azure Reservation", ("region", "family", "platform"),
               SELF_SERVICE, (12, 36), ("NO_UPFRONT", "ALL_UPFRONT"), _SELF_SERVICE_NOTE),
    Instrument("azure_macc", "azure", "Azure MACC (compromisso de gasto)", (), NEGOTIATED, (12, 36, 60), (),
               _NEGOTIATED_NOTE),
    Instrument("gcp_cud_flexible", "gcp", "GCP CUD flexível (por gasto)", (), SELF_SERVICE, (12, 36),
               ("NO_UPFRONT",), _SELF_SERVICE_NOTE),
    Instrument("gcp_cud_resource", "gcp", "GCP CUD por recurso", ("region", "family"), SELF_SERVICE,
               (12, 36), ("NO_UPFRONT",), _SELF_SERVICE_NOTE),
    Instrument("oci_universal_credits", "oci", "OCI Universal Credits (compromisso anual)", (), NEGOTIATED,
               (12, 36), (), "A OCI não tem reserva de compute. " + _NEGOTIATED_NOTE),
)}


def instruments_for(provider: str) -> list[Instrument]:
    return [item for item in INSTRUMENTS.values() if item.provider == provider]


@dataclass(frozen=True)
class UsageLine:
    """Eligible usage of one hour, in on-demand-equivalent cost (covered or not)."""
    hour_utc: str
    ondemand_cost: float
    region: str | None = None
    family: str | None = None
    platform: str | None = None


@dataclass(frozen=True)
class CommitmentKey:
    provider: str
    instrument: str
    scope: str
    region: str | None = None
    family: str | None = None
    platform: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    def label(self) -> str:
        parts = [self.instrument] + [value for value in (self.region, self.family, self.platform) if value]
        return " · ".join(parts)


def key_for(provider: str, instrument: Instrument, scope: str, line: UsageLine | Mapping[str, Any]) -> CommitmentKey:
    get = line.get if isinstance(line, Mapping) else (lambda name: getattr(line, name))
    values = {name: get(name) for name in instrument.key_fields}
    return CommitmentKey(provider=provider, instrument=instrument.code, scope=scope, **values)


@dataclass(frozen=True)
class ExistingCommitment:
    commitment_id: str
    instrument: str
    # On-demand-equivalent cost per hour it covers.
    ondemand_hour: float
    expires_at: datetime | None
    region: str | None = None
    family: str | None = None
    platform: str | None = None

    def matches(self, key: CommitmentKey) -> bool:
        if self.instrument != key.instrument:
            return False
        return all(getattr(self, name) in (None, getattr(key, name)) for name in KEY_FIELDS)


@dataclass(frozen=True)
class Adjustment:
    """Usage that will not be there during the term (rightsizing, decommission)."""
    kind: str
    ondemand_hour: float
    reference: str
    region: str | None = None
    family: str | None = None
    platform: str | None = None

    def matches(self, key: CommitmentKey) -> bool:
        return all(getattr(self, name) in (None, getattr(key, name)) for name in KEY_FIELDS)


@dataclass(frozen=True)
class Offer:
    """Resolved price of an instrument × term × payment for one key."""
    instrument: str
    term_months: int
    payment_option: str
    discount: float
    source: str
    offer_id: int | None = None
    upfront_fraction: float = 0.0
    burn_down: bool = False
    basis: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {**asdict(self), "basis": dict(self.basis)}


@dataclass(frozen=True)
class OfferRecord:
    """A catalog row (market or negotiated) before resolution for a key."""
    offer_id: int | None
    provider: str
    instrument: str
    term_months: int
    payment_option: str
    source: str
    pricing_mode: str
    discount_pct: float
    region: str | None = None
    family: str | None = None
    platform: str | None = None
    scope: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    priority: int = 0
    burn_down: bool = False
