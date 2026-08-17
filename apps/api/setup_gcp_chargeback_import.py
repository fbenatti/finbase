"""
Bootstrap cloud org and import GCP departments under Chargeback > GCP.

Usage: cd apps/api && python setup_gcp_chargeback_import.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select

from app.database import async_session
from app.models.source_connection import SourceConnection  # noqa: F401
from app.models.rules import Department
from app.routers.rules import bootstrap_cloud_organization, import_csv_data
from app.schemas.schemas import CsvImportRequest


async def _find_chargeback_gcp_id(db) -> int | None:
    consumption = (await db.execute(
        select(Department).where(Department.parent_id.is_(None), Department.name == "Consumo Cloud")
    )).scalars().first()
    if not consumption:
        return None
    chargeback = (await db.execute(
        select(Department).where(Department.parent_id == consumption.id, Department.name == "Chargeback")
    )).scalars().first()
    if not chargeback:
        return None
    gcp = (await db.execute(
        select(Department).where(Department.parent_id == chargeback.id, Department.name == "GCP")
    )).scalars().first()
    return gcp.id if gcp else None


async def main() -> None:
    async with async_session() as db:
        await bootstrap_cloud_organization(db)
        gcp_id = await _find_chargeback_gcp_id(db)
        if not gcp_id:
            raise RuntimeError("Nó Chargeback > GCP não encontrado após bootstrap")
        result = await import_csv_data(
            CsvImportRequest(cloud_root_map={"GCP-4P": gcp_id, "GCP-TI": gcp_id}),
            db,
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
