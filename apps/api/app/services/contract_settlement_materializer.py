"""Build auditable contract settlement runs from the published Silver layer.

The Bronze-to-Silver worker owns provider transformation. This module owns the
contract ledger: it reads the completed Silver slices, resolves the current
monthly budget, applies pool credits chronologically, and publishes one run
atomically. It never accepts financial amounts from a caller.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.financial_contract import (
    ContractSettlementMonth,
    ContractSettlementRun,
    FinancialContract,
    FinancialContractConnection,
)
from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department
from app.services.contract_settlement import (
    money,
    normalize_cloud_scope,
    organizational_budget_commitments,
    settle_pool,
)
from app.services.trino_service import execute


def _month_range(start: str, end: str) -> list[str]:
    year, month = int(start[:4]), int(start[4:])
    end_year, end_month = int(end[:4]), int(end[4:])
    periods: list[str] = []
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return periods


async def _silver_monthly_inputs(
    contract_id: int,
    period_from: str,
    period_until: str,
) -> dict[tuple[str, str, str], dict[str, Decimal]]:
    sql = f"""
        SELECT _billing_period AS period,
               lower(trim(cloud_origem)) AS cloud_scope,
               trim(id_orcamento) AS budget_id,
               count(*) AS row_count,
               sum(coalesce(BilledCost, 0)) AS billed_cost,
               sum(coalesce(consumo_realizado, realizado_cloud, 0)) AS consumption,
               sum(coalesce(realizado, 0)) AS realized,
               sum(coalesce(ajuste_contratual, 0)) AS adjustment
          FROM iceberg.silver.dest_modelo_focus
         WHERE financial_contract_id = {int(contract_id)}
           AND upper(coalesce(financial_effect, '')) = 'BUDGET_COMMITMENT'
           AND _billing_period BETWEEN '{period_from}' AND '{period_until}'
         GROUP BY 1, 2, 3
    """
    rows = await execute(sql, timeout_s=300)
    result: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    for row in rows:
        key = (
            str(row.get("period") or ""),
            normalize_cloud_scope(row.get("cloud_scope")),
            str(row.get("budget_id") or "").strip(),
        )
        result[key] = {
            "row_count": Decimal(str(row.get("row_count") or 0)),
            "billed_cost": money(row.get("billed_cost")),
            "consumption": money(row.get("consumption")),
            "realized": money(row.get("realized")),
            "adjustment": money(row.get("adjustment")),
        }
    return result


async def _budgets_by_period(
    db: AsyncSession,
    contract: FinancialContract,
    periods: list[str],
) -> tuple[dict[tuple[str, str, str], Decimal], list[int]]:
    budget_type = await db.scalar(
        select(FinancialType).where(FinancialType.code == "orcamento")
    )
    if not budget_type or not periods:
        return {}, []
    years = sorted({int(period[:4]) for period in periods})
    versions = list((await db.execute(
        select(PlanningVersion).where(
            PlanningVersion.financial_type_id == budget_type.id,
            PlanningVersion.year.in_(years),
            PlanningVersion.is_current.is_(True),
        ).order_by(PlanningVersion.year, PlanningVersion.version)
    )).scalars().all())
    version_ids = [int(version.id) for version in versions]
    if not version_ids:
        return {}, []
    departments = {
        int(item.id): item
        for item in (await db.execute(
            select(Department).where(Department.active.is_(True))
        )).scalars().all()
    }
    months_by_year: dict[int, set[int]] = {}
    for period in periods:
        months_by_year.setdefault(int(period[:4]), set()).add(int(period[4:]))

    pool_keys = [
        (str(contract.id), normalize_cloud_scope(scope.cloud_scope), scope.budget_id.strip())
        for scope in contract.budget_scopes or []
    ]
    commitments: dict[tuple[str, str, str], Decimal] = {}
    for year in years:
        year_periods = [period for period in periods if int(period[:4]) == year]
        if not year_periods:
            continue
        ids = [int(version.id) for version in versions if int(version.year) == year]
        entries = (await db.execute(
            select(PlanningEntry.department_id, PlanningEntry.month, PlanningEntry.value)
            .where(
                PlanningEntry.planning_version_id.in_(ids),
                PlanningEntry.entity_type == "department",
                PlanningEntry.month.in_(months_by_year.get(year, set())),
            )
        )).all()
        for period in year_periods:
            month = int(period[4:])
            monthly_entries = [
                (department_id, value)
                for _department_id, entry_month, value in entries
                if int(entry_month) == month
                for department_id in [_department_id]
            ]
            resolved = organizational_budget_commitments(
                pool_keys, departments, monthly_entries
            )
            for key, value in resolved.items():
                commitments[(period, key[1], key[2])] = value
    return commitments, version_ids


async def _recalculate_contract(
    db: AsyncSession,
    contract: FinancialContract,
    *,
    source: str,
) -> dict[str, Any]:
    connections = list((await db.execute(
        select(FinancialContractConnection.connection_id).where(
            FinancialContractConnection.contract_id == contract.id
        )
    )).scalars().all())
    if not connections:
        raise ValueError(f"contract {contract.code} has no source connections")

    from app.models.ingested_blob import IngestedBlob

    available_periods = list((await db.execute(
        select(IngestedBlob.billing_period).where(
            IngestedBlob.connection_id.in_(connections),
            IngestedBlob.row_count > 0,
            IngestedBlob.billing_period >= contract.valid_from,
            IngestedBlob.billing_period <= contract.valid_until,
        ).distinct().order_by(IngestedBlob.billing_period)
    )).scalars().all())
    if not available_periods:
        raise ValueError(f"no Bronze periods available for contract {contract.code}")

    # Recompute from the contract's first available month so earlier credits
    # are replayed before later periods, even when the request began mid-year.
    period_from = max(str(available_periods[0]), contract.valid_from)
    period_until = min(str(available_periods[-1]), contract.valid_until)
    periods = _month_range(period_from, period_until)
    inputs = await _silver_monthly_inputs(contract.id, period_from, period_until)
    commitments, version_ids = await _budgets_by_period(db, contract, periods)

    scopes = [
        (normalize_cloud_scope(scope.cloud_scope), scope.budget_id.strip())
        for scope in contract.budget_scopes or []
    ]
    fingerprint_payload: list[dict[str, Any]] = []
    for period in periods:
        for cloud, budget_id in scopes:
            values = inputs.get((period, cloud, budget_id), {})
            fingerprint_payload.append({
                "period": period,
                "cloud": cloud,
                "budget_id": budget_id,
                "budget": str(commitments.get((period, cloud, budget_id), Decimal("0.00"))),
                "billed_cost": str(values.get("billed_cost", Decimal("0.00"))),
                "consumption": str(values.get("consumption", Decimal("0.00"))),
                "realized": str(values.get("realized", Decimal("0.00"))),
                "adjustment": str(values.get("adjustment", Decimal("0.00"))),
            })
    fingerprint = hashlib.sha256(json.dumps(
        {
            "settlement_logic_version": 2,
            "contract": contract.id,
            "versions": version_ids,
            "pools": fingerprint_payload,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()

    # Serialize publishers for a contract. This prevents concurrent manual
    # recalculations from making two different runs appear current.
    await db.execute(text("SELECT pg_advisory_xact_lock(33784, :contract_id)"), {
        "contract_id": int(contract.id),
    })
    existing = (await db.execute(
        select(ContractSettlementRun).where(
            ContractSettlementRun.contract_id == contract.id,
            ContractSettlementRun.status == "SUCCESS",
            ContractSettlementRun.input_fingerprint == fingerprint,
        ).order_by(ContractSettlementRun.id.desc()).limit(1)
    )).scalar_one_or_none()
    if existing:
        await db.commit()
        return {
            "contract_id": contract.id, "run_id": existing.id,
            "status": "SUCCESS", "idempotent_reuse": True,
            "period_from": existing.period_from, "period_until": existing.period_until,
        }

    run = ContractSettlementRun(
        contract_id=contract.id,
        period_from=period_from,
        period_until=period_until,
        source=source,
        input_fingerprint=fingerprint,
        budget_version_ids=version_ids,
        status="RUNNING",
    )
    db.add(run)
    await db.flush()
    opening_credit: dict[tuple[str, str], Decimal] = {
        scope: Decimal("0.00") for scope in scopes
    }
    opening_overage: dict[tuple[str, str], Decimal] = {
        scope: Decimal("0.00") for scope in scopes
    }
    for period in periods:
        for cloud, budget_id in scopes:
            pool = (cloud, budget_id)
            key = (period, cloud, budget_id)
            values = inputs.get(key, {})
            commitment = commitments.get(key, Decimal("0.00"))
            consumption = values.get("consumption", Decimal("0.00"))
            is_contract_end = period == contract.valid_until
            result = settle_pool(
                commitment,
                consumption,
                opening_credit[pool],
                opening_overage[pool],
                contract_end=is_contract_end,
            )
            adjustment = money(result.credit_generated - result.gross_overage)

            # The ledger must agree with the row-level Silver publication.
            # Refuse to publish a successful run over a stale or partially
            # transformed slice; the previous SUCCESS run remains visible.
            if values.get("row_count", Decimal(0)) > 0:
                if money(values.get("realized")) != result.realized:
                    raise ValueError(
                        f"Silver realized does not close for {contract.code}/{cloud}/{budget_id}/{period}: "
                        f"{money(values.get('realized'))} != {result.realized}"
                    )
                silver_adjustment = money(values.get("adjustment"))
                # Existing Silver partitions used the opposite sign for this
                # display-only measure. Accept either representation while
                # rebuilding the ledger; new ETL publications use the signed
                # convention (credit positive, overage negative).
                if silver_adjustment not in {adjustment, -adjustment}:
                    raise ValueError(
                        f"Silver adjustment does not close for {contract.code}/{cloud}/{budget_id}/{period}: "
                        f"{silver_adjustment} != ±{adjustment}"
                    )

            expired = Decimal("0.00")
            closing = result.closing_credit
            if period == contract.valid_until and contract.credit_expiry_policy == "CONTRACT_END":
                expired = closing
                closing = Decimal("0.00")
            db.add(ContractSettlementMonth(
                run_id=run.id,
                contract_id=contract.id,
                cloud_scope=cloud,
                budget_id=budget_id,
                period=period,
                commitment=result.commitment,
                billed_cost=values.get("billed_cost", Decimal("0.00")),
                consumption=result.consumption,
                opening_credit=result.opening_credit,
                credit_generated=result.credit_generated,
                gross_overage=result.gross_overage,
                credit_used=result.credit_used,
                uncovered_overage=result.uncovered_overage,
                opening_overage=-result.opening_overage,
                closing_overage=-result.closing_overage,
                settled_overage=-result.settled_overage,
                credit_overage_delta=adjustment,
                expired_credit=expired,
                closing_credit=closing,
                realized=result.realized,
                contractual_adjustment=adjustment,
                allocation_factor=(result.realized / result.consumption) if result.consumption > 0 else Decimal("0"),
                status="SUCCESS",
            ))
            opening_credit[pool] = closing
            opening_overage[pool] = result.closing_overage

    run.status = "SUCCESS"
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "contract_id": contract.id, "run_id": run.id,
        "status": "SUCCESS", "idempotent_reuse": False,
        "period_from": period_from, "period_until": period_until,
    }


async def recalculate_contracts_for_connections(
    connection_ids: list[int],
    *,
    contract_ids: list[int] | None = None,
    source: str = "REPROCESS_GROUP",
) -> list[dict[str, Any]]:
    """Rebuild latest contract runs for contracts touched by a completed group."""
    async with async_session() as db:
        contracts = list((await db.execute(
            select(FinancialContract)
            .options(selectinload(FinancialContract.budget_scopes))
            .join(FinancialContractConnection)
            .where(
                FinancialContractConnection.connection_id.in_(connection_ids),
                FinancialContract.active.is_(True),
                *([FinancialContract.id.in_(contract_ids)] if contract_ids else []),
            )
            .distinct()
            .order_by(FinancialContract.id)
        )).scalars().all())
        results = []
        for contract in contracts:
            try:
                results.append(await _recalculate_contract(db, contract, source=source))
            except Exception:
                await db.rollback()
                raise
        return results
