"""OCI implementation of the rightsizing contracts (capacity, Flex sizes, price)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ..sizing import CapacitySnapshot, FlexRule, FlexSizingCatalog, RightsizingProvider, SizeOption

# Flex limits per shape (OCI documentation, "oci_flex_rules_v1"). 1 OCPU = 2
# vCPU on x86 shapes and 1 vCPU on Ampere A1.
OCI_FLEX_RULES = {
    "VM.Standard.E4.Flex": FlexRule(1, 64, 2, 1, 64, 1024, "OCPU"),
    "VM.Standard.E5.Flex": FlexRule(1, 94, 2, 1, 64, 1049, "OCPU"),
    "VM.Standard3.Flex": FlexRule(1, 32, 2, 1, 64, 512, "OCPU"),
    "VM.Optimized3.Flex": FlexRule(1, 18, 2, 1, 64, 256, "OCPU"),
    "VM.Standard.A1.Flex": FlexRule(1, 80, 1, 1, 64, 512, "OCPU"),
}


def _snapshot(instance: Any) -> CapacitySnapshot:
    shape_config = getattr(instance, "shape_config", None)
    ocpus = float(shape_config.ocpus) if shape_config and shape_config.ocpus is not None else None
    vcpus = shape_config.vcpus if shape_config and shape_config.vcpus is not None else None
    rule = OCI_FLEX_RULES.get(instance.shape)
    if vcpus is None and ocpus is not None and rule:
        vcpus = ocpus * rule.vcpus_per_unit
    return CapacitySnapshot(
        provider="oci", resource_id=instance.id, resource_name=instance.display_name,
        sku=instance.shape, family=instance.shape,
        vcpus=float(vcpus) if vcpus is not None else 0.0,
        memory_gb=float(shape_config.memory_in_gbs) if shape_config and shape_config.memory_in_gbs is not None else 0.0,
        lifecycle_state=instance.lifecycle_state,
        observed_at_utc=datetime.now(timezone.utc).isoformat(),
        source="oci.core.ComputeClient.get_instance",
        attributes={"ocpu": ocpus},
        details={
            "processor_description": getattr(shape_config, "processor_description", None) if shape_config else None,
            "baseline_ocpu_utilization": getattr(shape_config, "baseline_ocpu_utilization", None) if shape_config else None,
        },
    )


def read_oci_capacity(
    connection: Any, config: Any, *, client_factory: Callable[[dict], Any] | None = None,
) -> dict[str, CapacitySnapshot | dict]:
    """One read-only capacity snapshot per allowlisted instance.

    Failures stay per resource so the engine reports MISSING_CAPACITY
    instead of inventing a size.
    """
    if not config.region:
        raise ValueError("OCI region is required to read capacity")
    if hasattr(config, "oci_config"):
        oci_config = {**config.oci_config(), "region": config.region}
    else:
        from app.services.oci_extractor import OciExtractor

        oci_config = {**OciExtractor(connection)._get_oci_config(), "region": config.region}
    if client_factory is None:
        import oci

        client_factory = lambda values: oci.core.ComputeClient(
            values, retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(5, 30),
        )
    client = client_factory(oci_config)
    snapshots: dict[str, CapacitySnapshot | dict] = {}
    for resource_id in config.resource_ids:
        try:
            snapshots[resource_id] = _snapshot(client.get_instance(resource_id).data)
        except Exception as exc:  # noqa: BLE001 - recorded as missing capacity
            snapshots[resource_id] = {"resource_id": resource_id, "error": type(exc).__name__}
    return snapshots


@dataclass(frozen=True)
class OciFlexPriceBook:
    """Flex price = OCPU-hours × OCPU price + GB-hours × memory price."""
    ocpu_hour: float
    memory_gb_hour: float
    currency: str | None
    basis: Mapping[str, Any] = field(default_factory=dict)

    def hourly(self, option: SizeOption) -> float | None:
        ocpus = option.attributes.get("ocpu")
        if ocpus is None:
            return None
        return float(ocpus) * self.ocpu_hour + option.memory_gb * self.memory_gb_hour


def effective_unit_prices(billing_rows: Iterable[Mapping]) -> dict[str, OciFlexPriceBook]:
    """Effective price per OCPU-hour and GB-hour of each resource's own meters.

    Uses the most recent billing period that has both meters for the resource.
    """
    grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    currencies: dict[str, str] = {}
    meters: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for row in billing_rows:
        quantity = float(row.get("quantity") or 0)
        if quantity <= 0:
            continue
        unit = str(row.get("unit") or "").lower()
        meter = str(row.get("skumeter") or "")
        kind = "memory_gb_hour" if ("gigabyte" in unit or unit.startswith("gb") or "memory" in meter.lower()) else (
            "ocpu_hour" if "ocpu" in unit else None)
        if kind is None:
            continue
        key = (str(row["resource_id"]), str(row["billing_period"]))
        grouped[key][kind][0] += float(row.get("cost") or 0)
        grouped[key][kind][1] += quantity
        meters[key][kind] = meter
        currencies[str(row["resource_id"])] = str(row.get("currency") or "")
    books: dict[str, OciFlexPriceBook] = {}
    for (resource_id, period), kinds in sorted(grouped.items(), key=lambda item: item[0][1]):
        if "ocpu_hour" not in kinds or "memory_gb_hour" not in kinds:
            continue
        ocpu_hour = kinds["ocpu_hour"][0] / kinds["ocpu_hour"][1]
        memory_gb_hour = kinds["memory_gb_hour"][0] / kinds["memory_gb_hour"][1]
        books[resource_id] = OciFlexPriceBook(
            ocpu_hour=ocpu_hour, memory_gb_hour=memory_gb_hour, currency=currencies.get(resource_id),
            basis={
                "billing_period": period, "meters": meters[(resource_id, period)],
                "unit_prices": {"ocpu_hour": ocpu_hour, "memory_gb_hour": memory_gb_hour},
                "formula": "OCPU × preço/OCPU-h + GB × preço/GB-h",
                "source": "effectivecost / consumedquantity (Silver dest_billing_cloud)",
            },
        )
    return books


def read_oci_price_books(resource_ids: tuple[str, ...]) -> dict[str, OciFlexPriceBook]:
    from app.services import lakehouse_layers as layers
    from app.services.trino_service import execute_sync

    if not resource_ids:
        return {}
    in_list = ", ".join("'" + rid.replace("'", "''") + "'" for rid in resource_ids)
    rows = execute_sync(
        f"SELECT resourceid AS resource_id, _billing_period AS billing_period, skumeter, "
        f"consumedunit AS unit, billingcurrency AS currency, "
        f"SUM(consumedquantity) AS quantity, SUM(effectivecost) AS cost "
        f"FROM {layers.detail_layer()}.dest_billing_cloud "
        f"WHERE resourceid IN ({in_list}) GROUP BY 1, 2, 3, 4, 5",
        timeout_s=180,
    )
    return effective_unit_prices(rows)


OCI_RIGHTSIZING = RightsizingProvider(
    provider="oci", read_capacity=read_oci_capacity,
    sizing_catalog=FlexSizingCatalog(OCI_FLEX_RULES), price_books=read_oci_price_books,
)
