"""Synchronize active Azure resource groups from the project directory.

The synchronizer deliberately changes metadata only.  Gold is queried to
identify active consumption, while departments and classification rules are
created only when ``apply=True``.  Reprocessing Gold remains an explicit
operation after the audit has been reviewed.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.destination import DestinationTable
from app.models.rules import ClassificationRule, Department
from app.models.source_connection import SourceConnection
from app.services.classification_engine import normalize_rule_conditions
from app.services.trino_service import execute as trino_execute


AZURE_CLOUDS = frozenset({"Azure-4P", "Azure-TI"})
FALLBACK_PARENT_ID = 55
FALLBACK_NAME = "DIRETORIAS NAO MAPEADAS"


def canon(value: object) -> str:
    """Use the same comparison semantics as the classification engine."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", ascii_value)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=";")]


@dataclass(frozen=True)
class Candidate:
    cloud: str
    subscription_id: str
    project: str
    resource_group: str


@dataclass
class SyncReport:
    latest_periods: list[str] = field(default_factory=list)
    csv_pairs: int = 0
    active_pairs: int = 0
    active_resource_groups: int = 0
    inactive_or_zero_pairs: int = 0
    rules_created: int = 0
    rules_existing: int = 0
    departments_created: int = 0
    fallback_departments_created: int = 0
    divergences: list[dict[str, Any]] = field(default_factory=list)
    unmapped_projects: list[str] = field(default_factory=list)
    vivo_mais: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "latest_periods": self.latest_periods,
            "csv_pairs": self.csv_pairs,
            "active_pairs": self.active_pairs,
            "active_resource_groups": self.active_resource_groups,
            "inactive_or_zero_pairs": self.inactive_or_zero_pairs,
            "rules_created": self.rules_created,
            "rules_existing": self.rules_existing,
            "departments_created": self.departments_created,
            "fallback_departments_created": self.fallback_departments_created,
            "divergences": self.divergences,
            "unmapped_projects": self.unmapped_projects,
            "vivo_mais": self.vivo_mais,
        }


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _candidates(project_rows: list[dict[str, str]]) -> list[Candidate]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[Candidate] = []
    for row in project_rows:
        cloud = (row.get("Cloud") or "").strip()
        subscription_id = (row.get("RowKey") or "").strip()
        project = (row.get("Projeto") or "").strip()
        resource_group = (row.get("SubscriptionRG") or "").strip()
        key = (cloud, subscription_id, canon(project), canon(resource_group))
        if cloud not in AZURE_CLOUDS or not subscription_id or not project or not resource_group or key in seen:
            continue
        seen.add(key)
        result.append(Candidate(cloud, subscription_id, project, resource_group))
    return result


async def _active_pairs() -> tuple[list[str], list[dict[str, Any]]]:
    periods = await trino_execute(
        "SELECT DISTINCT _billing_period FROM iceberg.gold.dest_modelo_focus "
        "WHERE _billing_period IS NOT NULL ORDER BY _billing_period DESC LIMIT 3",
        timeout_s=120,
    )
    latest_periods = [str(row["_billing_period"]) for row in periods]
    if not latest_periods:
        return [], []
    period_list = ", ".join(_sql_literal(value) for value in latest_periods)
    # ``RowKey`` in projetos_download.csv is not reliably the subscription
    # identifier exposed by FOCUS.  Read the real subscription from Gold and
    # use the CSV only as the RG → project directory.
    rows = await trino_execute(
        f'''SELECT cloud_origem,
                   "SubAccountId" AS subscription_id,
                   source_resource_group_name,
                   SUM(COALESCE("BilledCost", 0)) AS billed_cost
            FROM iceberg.gold.dest_modelo_focus
            WHERE _billing_period IN ({period_list})
              AND lower(cloud_origem) IN ('azure-4p', 'azure-ti')
              AND source_resource_group_name IS NOT NULL
            GROUP BY 1, 2, 3
            HAVING SUM(COALESCE("BilledCost", 0)) <> 0''',
        timeout_s=300,
    )
    return latest_periods, rows


def _rule_resource_groups(rule: ClassificationRule) -> set[str]:
    groups: set[str] = set()
    for condition in normalize_rule_conditions(rule):
        if condition.get("field") == "source_resource_group_name" and condition.get("operator") == "=":
            groups.update(canon(value) for value in condition.get("values", []))
    return groups


async def sync_active_azure_classifications(
    db: AsyncSession,
    *,
    apply: bool = False,
    projects_path: Path | None = None,
    directories_path: Path | None = None,
) -> SyncReport:
    """Return an audit report, optionally persisting its departments and rules."""
    root = Path(__file__).resolve().parents[4]
    projects_path = projects_path or root / "projetos_download.csv"
    directories_path = directories_path or root / "diretorias_download.csv"
    candidates = _candidates(_csv_rows(projects_path))
    directory_rows = _csv_rows(directories_path)
    report = SyncReport(csv_pairs=len(candidates))

    report.latest_periods, gold_active_rows = await _active_pairs()
    candidates_by_cloud_rg: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_cloud_rg[(candidate.cloud, canon(candidate.resource_group))].append(candidate)
    active_candidates: list[Candidate] = []
    for row in gold_active_rows:
        raw_cloud = str(row.get("cloud_origem") or "").lower().strip()
        cloud = "Azure-4P" if raw_cloud == "azure-4p" else "Azure-TI" if raw_cloud == "azure-ti" else ""
        resource_group = str(row.get("source_resource_group_name") or "").strip()
        subscription_id = str(row.get("subscription_id") or "").strip()
        matching = candidates_by_cloud_rg.get((cloud, canon(resource_group)), [])
        projects = {canon(candidate.project) for candidate in matching}
        if not matching or not subscription_id:
            continue
        if len(projects) != 1:
            report.divergences.append({
                "cloud": cloud,
                "subscription_id": subscription_id,
                "resource_group": resource_group,
                "csv_projects": sorted({candidate.project for candidate in matching}),
                "reason": "resource_group_ambiguous_in_csv",
            })
            continue
        source = matching[0]
        active_candidates.append(Candidate(cloud, subscription_id, source.project, resource_group))
    report.active_pairs = len(active_candidates)
    report.active_resource_groups = len({canon(candidate.resource_group) for candidate in active_candidates})
    report.inactive_or_zero_pairs = report.csv_pairs - report.active_pairs

    all_departments = (await db.execute(select(Department))).scalars().all()
    departments_by_id = {department.id: department for department in all_departments}
    departments_by_parent_name: dict[tuple[int | None, str], Department] = {
        (department.parent_id, canon(department.name)): department for department in all_departments
    }
    departments_by_name: dict[str, list[Department]] = defaultdict(list)
    for department in all_departments:
        departments_by_name[canon(department.name)].append(department)

    fallback_parent = departments_by_id.get(FALLBACK_PARENT_ID)
    if fallback_parent is None:
        raise ValueError("Diretoria Azure fallback (id=55) não foi encontrada")
    azure_root_id = fallback_parent.parent_id
    if azure_root_id is None:
        raise ValueError("A diretoria Azure fallback não possui raiz organizacional")

    directory_by_cloud_project: dict[tuple[str, str], dict[str, str]] = {}
    for row in directory_rows:
        cloud, project = (row.get("Cloud") or "").strip(), (row.get("Projeto") or "").strip()
        if cloud in AZURE_CLOUDS and project:
            directory_by_cloud_project.setdefault((cloud, canon(project)), row)

    connections = (await db.execute(select(SourceConnection).where(SourceConnection.code.in_(["azure-4p", "azure-ti"])))).scalars().all()
    connection_by_cloud = {"Azure-4P": next((item for item in connections if item.code == "azure-4p"), None), "Azure-TI": next((item for item in connections if item.code == "azure-ti"), None)}
    if not all(connection_by_cloud.values()):
        raise ValueError("As conexões azure-4p e azure-ti são obrigatórias para a sincronização")
    focus_destination_id = (await db.execute(
        select(DestinationTable.id).where(DestinationTable.physical_name == "dest_modelo_focus").limit(1)
    )).scalar_one_or_none()
    if focus_destination_id is None:
        raise ValueError("O destino MODELO FOCUS não foi encontrado")

    existing_rules = (await db.execute(select(ClassificationRule).where(ClassificationRule.cloud_scope == "azure", ClassificationRule.active == True))).scalars().all()
    rules_by_resource_group: dict[str, list[ClassificationRule]] = defaultdict(list)
    for rule in existing_rules:
        groups = _rule_resource_groups(rule)
        for group in groups:
            rules_by_resource_group[group].append(rule)

    fallback_node = departments_by_parent_name.get((FALLBACK_PARENT_ID, canon(FALLBACK_NAME)))
    grouped: dict[tuple[str, int], list[Candidate]] = defaultdict(list)
    unmapped: set[str] = set()
    virtual_department_id = -1

    async def persist_or_virtual(department: Department) -> Department:
        """Assign stable identities in preview mode without persisting metadata."""
        nonlocal virtual_department_id
        if apply:
            db.add(department)
            await db.flush()
        else:
            department.id = virtual_department_id
            virtual_department_id -= 1
        return department

    async def ensure_department(candidate: Candidate) -> Department:
        nonlocal fallback_node
        directory = directory_by_cloud_project.get((candidate.cloud, canon(candidate.project)))
        parent: Department
        if directory:
            directory_name = (directory.get("Diretoria") or "").strip()
            parent = departments_by_parent_name.get((azure_root_id, canon(directory_name)))
            if parent is None:
                parent = Department(parent_id=azure_root_id, name=directory_name, cloud_filter="Azure")
                await persist_or_virtual(parent)
                departments_by_parent_name[(azure_root_id, canon(directory_name))] = parent
                departments_by_name[canon(parent.name)].append(parent)
                report.departments_created += 1
            department = departments_by_parent_name.get((parent.id, canon(candidate.project)))
            if department is None:
                department = Department(
                    parent_id=parent.id,
                    name=candidate.project,
                    centro_custo=(directory.get("CentrodeCusto") or "").strip() or None,
                    ambiente=(directory.get("Origem") or "").strip() or None,
                    cloud_filter=candidate.cloud,
                    custom_fields={"source": "diretorias_download.csv"},
                )
                await persist_or_virtual(department)
                departments_by_parent_name[(parent.id, canon(candidate.project))] = department
                departments_by_name[canon(department.name)].append(department)
                report.departments_created += 1
            return department

        unmapped.add(candidate.project)
        if fallback_node is None:
            fallback_node = Department(
                parent_id=FALLBACK_PARENT_ID,
                name=FALLBACK_NAME,
                cloud_filter="Azure",
                custom_fields={"tipo": "organization_5", "source": "azure_active_classification_sync"},
            )
            await persist_or_virtual(fallback_node)
            departments_by_parent_name[(FALLBACK_PARENT_ID, canon(FALLBACK_NAME))] = fallback_node
            report.departments_created += 1
        department = departments_by_parent_name.get((fallback_node.id, canon(candidate.project)))
        if department is None:
            department = Department(
                parent_id=fallback_node.id,
                name=candidate.project,
                cloud_filter=candidate.cloud,
                custom_fields={"source": "projetos_download.csv", "association": "fallback_directory"},
            )
            await persist_or_virtual(department)
            departments_by_parent_name[(fallback_node.id, canon(candidate.project))] = department
            departments_by_name[canon(department.name)].append(department)
            report.departments_created += 1
            report.fallback_departments_created += 1
        return department

    for candidate in active_candidates:
        group_key = canon(candidate.resource_group)
        # Keep the organizational inventory complete even when a legacy rule
        # is divergent.  The rule itself remains untouched in that case.
        department = await ensure_department(candidate)
        current = rules_by_resource_group.get(group_key, [])
        conflicting = [rule for rule in current if rule.department_id is not None and canon(rule.match_value) != canon(candidate.project)]
        if conflicting:
            report.divergences.append({
                "cloud": candidate.cloud,
                "subscription_id": candidate.subscription_id,
                "resource_group": candidate.resource_group,
                "csv_project": candidate.project,
                "rule_ids": [rule.id for rule in conflicting],
                "rule_projects": sorted({rule.match_value for rule in conflicting}),
            })
            continue
        grouped[(candidate.cloud, department.id)].append(candidate)

    for (cloud, department_id), group_candidates in grouped.items():
        group_values = sorted({candidate.resource_group for candidate in group_candidates}, key=canon)
        uncovered_values = [
            value for value in group_values
            if not any(rule.department_id == department_id for rule in rules_by_resource_group.get(canon(value), []))
        ]
        if not uncovered_values:
            report.rules_existing += 1
            continue
        department = next(department for department in departments_by_name[canon(group_candidates[0].project)] if department.id == department_id)
        connection = connection_by_cloud[cloud]
        rule = ClassificationRule(
            connection_id=connection.id,
            destination_table_id=focus_destination_id,
            department_id=department.id,
            name=f"Azure ativa | {cloud} | {department.name}",
            source_field="source_resource_group_name",
            pattern_type="multi_condition",
            pattern=("source_resource_group_name=" + "|".join(uncovered_values))[:500],
            match_value=department.name[:200],
            target_field="departamento",
            conditions=[
                {"field": "source_resource_group_name", "operator": "=", "values": uncovered_values, "logic": "AND"},
            ],
            priority=100,
            cloud_scope="azure",
            active=True,
        )
        if apply:
            db.add(rule)
        report.rules_created += 1

    report.unmapped_projects = sorted(unmapped, key=canon)
    vivo_active = [candidate for candidate in active_candidates if canon(candidate.project) == canon("VIVO+")]
    report.vivo_mais = {
        "active_pairs": len(vivo_active),
        "active_resource_groups": len({canon(candidate.resource_group) for candidate in vivo_active}),
        "department_id": 69,
    }
    if apply:
        await db.commit()
    else:
        await db.rollback()
    return report
