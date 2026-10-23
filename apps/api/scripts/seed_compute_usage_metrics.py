#!/usr/bin/env python
"""Cria a Silver genérica de métricas de uso e liga a fonte OCI Monitoring a ela.

Idempotente. Projeta no catálogo de dados, no mesmo modelo do billing:

- fonte ``oci-monitoring`` (``connector_type = oci_monitoring``), que reaproveita
  as credenciais da conexão OCI de custo por referência;
- destino ``compute_usage_metrics`` ("Métricas de uso de computação"), com
  colunas neutras que qualquer cloud preenche;
- mapeamentos Bronze → Silver da OCI, editáveis em "Relações entre tabelas".

Os nomes canônicos de métrica/unidade vêm do catálogo declarativo
(``infrastructure_metrics.catalog``), sem segunda fonte de verdade.

Uso (a partir de ``apps/api``):

    PYTHONPATH=. ../../.venv/bin/python scripts/seed_compute_usage_metrics.py --credentials oci
    PYTHONPATH=. ../../.venv/bin/python scripts/seed_compute_usage_metrics.py --credentials oci --apply
"""
from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: E402

for _module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{_module.name}")

from app.database import SyncSession  # noqa: E402
from app.models.destination import DestinationColumn, DestinationTable, SourceDestinationMapping  # noqa: E402
from app.models.source_connection import SourceConnection  # noqa: E402
from app.services.infrastructure_metrics.catalog import OCI_METRICS  # noqa: E402
from app.services.oci_monitoring_extractor import AGGREGATIONS, RESOLUTIONS  # noqa: E402

SILVER_NAME = "Métricas de uso de computação"
SILVER_PHYSICAL = "compute_usage_metrics"
SILVER_DESCRIPTION = (
    "Série de uso de recursos de computação de qualquer cloud, um valor por "
    "recurso, métrica canônica, estatística e bucket. Alimenta o motor de rightsizing."
)
# (coluna, tipo, obrigatória) — nomes neutros: nenhuma cloud aparece aqui.
SILVER_COLUMNS = (
    ("cloud_provider", "TEXT", True),
    ("account_id", "TEXT", False),
    ("scope_id", "TEXT", False),
    ("region", "TEXT", False),
    ("resource_id", "TEXT", True),
    ("resource_name", "TEXT", False),
    ("resource_type", "TEXT", False),
    ("metric_namespace", "TEXT", False),
    ("source_metric_name", "TEXT", True),
    ("metric", "TEXT", True),
    ("metric_unit", "TEXT", False),
    ("statistic", "TEXT", True),
    ("resolution_seconds", "NUMERIC", True),
    ("bucket_start_utc", "TIMESTAMPTZ", True),
    ("value", "NUMERIC", False),
    ("dimensions", "JSONB", False),
)

SOURCE_CODE = "oci-monitoring"
SOURCE_NAME = "OCI Monitoring – métricas de uso"


def oci_mappings(tenancy: str | None) -> list[dict]:
    """Bronze (campos da API OCI) → colunas neutras da Silver."""
    statistic_of = {aggregation: name for name, aggregation in AGGREGATIONS.items()}
    return [
        {"source_column": "namespace", "destination_column": "cloud_provider", "transform_type": "CONST", "transform_config": {"value": "oci"}},
        {"source_column": "namespace", "destination_column": "account_id", "transform_type": "CONST", "transform_config": {"value": tenancy}},
        {"source_column": "compartment_id", "destination_column": "scope_id", "transform_type": "RENAME"},
        {"source_column": "region", "destination_column": "region", "transform_type": "RENAME"},
        {"source_column": "dimensions.resourceId", "destination_column": "resource_id", "transform_type": "RENAME"},
        {"source_column": "dimensions.resourceDisplayName", "destination_column": "resource_name", "transform_type": "RENAME"},
        {"source_column": "namespace", "destination_column": "resource_type", "transform_type": "VALUE_MAP",
         "transform_config": {"map": {"oci_computeagent": "compute.instance"}, "default": None}},
        {"source_column": "namespace", "destination_column": "metric_namespace", "transform_type": "RENAME"},
        {"source_column": "name", "destination_column": "source_metric_name", "transform_type": "RENAME"},
        {"source_column": "name", "destination_column": "metric", "transform_type": "VALUE_MAP",
         "transform_config": {"map": {m.name: m.canonical_metric for m in OCI_METRICS}, "default": None}},
        {"source_column": "name", "destination_column": "metric_unit", "transform_type": "VALUE_MAP",
         "transform_config": {"map": {m.name: m.canonical_unit for m in OCI_METRICS}, "default": None}},
        {"source_column": "aggregation", "destination_column": "statistic", "transform_type": "VALUE_MAP",
         "transform_config": {"map": statistic_of, "default": None}},
        {"source_column": "resolution", "destination_column": "resolution_seconds", "transform_type": "VALUE_MAP",
         "transform_config": {"map": {k: str(v) for k, v in RESOLUTIONS.items()}, "default": None}},
        {"source_column": "timestamp", "destination_column": "bucket_start_utc", "transform_type": "RENAME"},
        {"source_column": "value", "destination_column": "value", "transform_type": "RENAME"},
        {"source_column": "dimensions", "destination_column": "dimensions", "transform_type": "RENAME"},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True, help="code da conexão OCI de custo cujas credenciais serão reutilizadas")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    session = SyncSession()
    try:
        credentials = session.execute(
            select(SourceConnection).where(SourceConnection.code == args.credentials)
        ).scalar_one_or_none()
        if credentials is None or credentials.connector_type != "oci":
            sys.exit(f"Conexão OCI '{args.credentials}' não encontrada")
        cred_cfg = credentials.config_json or {}
        pilot = cred_cfg.get("infrastructure_metrics") or {}
        tenancy = cred_cfg.get("tenancy_ocid") or cred_cfg.get("tenancy")

        source = session.execute(select(SourceConnection).where(SourceConnection.code == SOURCE_CODE)).scalar_one_or_none()
        if source is None:
            source = SourceConnection(
                code=SOURCE_CODE, name=SOURCE_NAME, connector_type="oci_monitoring",
                cloud_name="Oracle Cloud", cloud_scope="oci", blob_prefixes=[],
                config_json={
                    "credentials_connection_code": credentials.code,
                    "region": pilot.get("region") or cred_cfg.get("region"),
                    "compartment_id": pilot.get("compartment_id"),
                    "resource_ids": list(pilot.get("resource_ids") or []),
                    "namespace": "oci_computeagent",
                    "resolution": "5m",
                    "history_days": 30,
                    "statistics": ["mean", "max", "p95", "p99", "count"],
                },
            )
            session.add(source)
            print(f"+ fonte {SOURCE_CODE} (credenciais de {credentials.code})")
        else:
            print(f"= fonte {SOURCE_CODE} já existe (id {source.id})")

        silver = session.execute(
            select(DestinationTable).where(DestinationTable.physical_name == SILVER_PHYSICAL)
        ).scalar_one_or_none()
        if silver is None:
            silver = DestinationTable(
                name=SILVER_NAME, description=SILVER_DESCRIPTION, physical_name=SILVER_PHYSICAL,
                partition_spec_json={"grain": "billing_period"}, active=True,
            )
            session.add(silver)
            print(f"+ Silver {SILVER_PHYSICAL}")
        session.flush()
        existing_columns = {
            column.column_name for column in session.execute(
                select(DestinationColumn).where(DestinationColumn.destination_table_id == silver.id)
            ).scalars()
        }
        for position, (name, data_type, required) in enumerate(SILVER_COLUMNS, start=1):
            if name not in existing_columns:
                session.add(DestinationColumn(
                    destination_table_id=silver.id, column_name=name, data_type=data_type,
                    is_required=required, position=position,
                ))
                print(f"  + coluna {name} {data_type}")

        existing_mappings = {
            (m.source_column, m.destination_column) for m in session.execute(
                select(SourceDestinationMapping).where(
                    SourceDestinationMapping.source_connection_id == source.id,
                    SourceDestinationMapping.destination_table_id == silver.id,
                )
            ).scalars()
        }
        for priority, mapping in enumerate(oci_mappings(tenancy)):
            if (mapping["source_column"], mapping["destination_column"]) in existing_mappings:
                continue
            session.add(SourceDestinationMapping(
                source_connection_id=source.id, destination_table_id=silver.id,
                name=f"{mapping['source_column']} → {mapping['destination_column']}",
                source_column=mapping["source_column"], destination_column=mapping["destination_column"],
                transform_type=mapping["transform_type"], transform_config=mapping.get("transform_config"),
                priority=priority, active=True,
            ))
            print(f"  + mapeamento {mapping['source_column']} → {mapping['destination_column']} ({mapping['transform_type']})")

        if args.apply:
            session.commit()
            print("aplicado")
        else:
            session.rollback()
            print("simulação (use --apply para gravar)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
