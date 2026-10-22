"""Aplica o rateio de Marketplace depois do reprocessamento padrão da Silver.

O rateio é uma regra de negócio (``financial_effect = MARKETPLACE_ALLOCATION``,
``config_json.allocation_method = MARKETPLACE_USD_SHARE``) da conexão de
Marketplace. A configuração da regra diz de onde vem o pool em BRL na nuvem:

* ``cloud_connection_code`` — conexão de nuvem que fatura a oferta;
* ``cloud_sku`` — ``SkuId`` da oferta (opcional);
* ``cloud_product_name`` — ``ChargeDescription`` da oferta (opcional);
* ``cloud_resource_group`` — resource group no ``ResourceId`` (opcional).

O pool é distribuído nas linhas do Marketplace pela participação em USD.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.services import iceberg_service as iceberg
from app.services.marketplace_allocation import allocate_marketplace_pool
from app.services.trino_service import execute as trino_execute

ALLOCATION_METHOD = "MARKETPLACE_USD_SHARE"


def _literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def marketplace_allocation_rules(session, connection) -> list:
    """Regras de rateio ativas cuja conexão de Marketplace é ``connection``."""
    from app.models.rules import BusinessRule

    rules = session.execute(select(BusinessRule).where(
        BusinessRule.active == True,  # noqa: E712
        BusinessRule.financial_effect == "MARKETPLACE_ALLOCATION",
    )).scalars().all()
    code = str(connection.code or "").lower()
    return [
        rule for rule in rules
        if str((rule.config_json or {}).get("allocation_method", "")).upper() == ALLOCATION_METHOD
        and str((rule.config_json or {}).get("marketplace_connection_code", "")).lower() == code
    ]


def _pool_sql(physical_name: str, period: str, config: dict[str, Any]) -> str:
    filters = [
        f"_billing_period = {_literal(period)}",
        f"lower(cloud_origem) = {_literal(str(config['cloud_connection_code']).lower())}",
    ]
    if config.get("cloud_sku"):
        filters.append(f"skuid = {_literal(config['cloud_sku'])}")
    if config.get("cloud_product_name"):
        filters.append(f"chargedescription = {_literal(config['cloud_product_name'])}")
    if config.get("cloud_resource_group"):
        rg = str(config["cloud_resource_group"]).lower()
        filters.append(f"lower(resourceid) LIKE {_literal('%/resourcegroups/' + rg + '/%')}")
    return f"SELECT sum(realizado) AS pool_brl FROM iceberg.silver.{physical_name} WHERE " + " AND ".join(filters)


def _append_rule(value: object, rule) -> str:
    try:
        payload = json.loads(value) if isinstance(value, str) and value else {}
    except json.JSONDecodeError:
        payload = {}
    financial = [item for item in payload.get("financial", []) if item.get("id") != rule.id]
    financial.append({
        "id": rule.id, "name": rule.name,
        "financial_effect": "MARKETPLACE_ALLOCATION", "config": rule.config_json or {},
    })
    payload["financial"] = financial
    return json.dumps(payload, ensure_ascii=False)


def apply_marketplace_allocation(physical_name: str, period: str, connection, rule) -> dict:
    """Rateia o pool de nuvem da regra nas linhas do Marketplace do período."""
    config = rule.config_json or {}
    if not config.get("cloud_connection_code"):
        raise ValueError(f"Regra '{rule.name}' sem conexão de nuvem (cloud_connection_code)")
    frames = list(iceberg.read_destination_slice_batches(
        physical_name, billing_period=period,
        connection_id=connection.id, batch_size=100_000, namespace="silver",
    ))
    marketplace = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if marketplace.empty:
        return {"period": period, "rows": 0, "allocated_brl": 0.0}

    amount_column = next(
        (column for column in marketplace.columns if str(column).lower() == "billedcost"),
        None,
    )
    if amount_column is None:
        raise RuntimeError(f"Coluna BilledCost não encontrada para {period}")

    rows = asyncio.run(trino_execute(_pool_sql(physical_name, period, config), timeout_s=120))
    value = rows[0].get("pool_brl") if rows else None
    if value is None:
        raise RuntimeError(f"Pool de nuvem não encontrado para {period} (regra '{rule.name}')")
    pool = Decimal(str(value))

    usd = pd.to_numeric(marketplace[amount_column], errors="coerce").fillna(0)
    allocations = allocate_marketplace_pool(usd.tolist(), pool)
    marketplace["realizado_cloud"] = [float(item) for item in allocations]
    marketplace["realizado"] = marketplace["realizado_cloud"]
    current_rules = marketplace.get("business_rule", pd.Series([None] * len(marketplace)))
    marketplace["business_rule"] = current_rules.map(lambda item: _append_rule(item, rule))

    staged = iceberg.write_destination_staged(
        physical_name, marketplace, namespace="silver", partition_spec_json=None,
    )
    iceberg.publish_staged_partition(
        physical_name, staged, billing_period=period,
        connection_id=connection.id, namespace="silver",
        expected_row_count=len(marketplace),
    )
    return {
        "period": period,
        "rows": len(marketplace),
        "marketplace_usd": float(usd.sum()),
        "cloud_pool_brl": float(pool),
        "allocated_brl": float(marketplace["realizado"].sum()),
    }
