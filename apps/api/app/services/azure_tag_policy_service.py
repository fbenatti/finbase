"""
Azure Tag Policy Service — generate Azure Policy definitions (and deployment
scripts) from the current classification business rules.

The classification rules reference FOCUS (Gold) columns such as `ResourceId`,
`resourceGroupName`, `subscriptionName`, `ResourceName`, `ResourceType` and
`RegionName`. An Azure Policy, however, evaluates ARM aliases/fields such as
`name`, `location` and `Microsoft.Compute/virtualMachines/sku.name`.

This module performs the *reverse mapping* (FOCUS -> Azure alias) using the
`SourceDestinationMapping` relationships plus a static alias table fallback, then
emits compact policies grouped in three layers so the result stays well under
Azure Policy limits:

  1. Resource-group inheritance (built-in "Inherit a tag from the resource group").
  2. Name / SKU / region wildcard patterns (like/contains/equals).
  3. Explicit resource-group lists grouped by unique tag combination.

Policies are grouped per Azure tenant (each `azure-*` source connection).
"""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rules import ClassificationRule, Department, Project
from app.models.destination import SourceDestinationMapping
from app.models.source_connection import SourceConnection
from app.services.cloud_scope import connection_cloud_scope, resolve_connection_scope

# Static FOCUS -> Azure ARM alias fallback, used when no SourceDestinationMapping
# exists for a given field. Keys are normalized lowercase (no separators).
FOCUS_TO_ALIAS: dict[str, str] = {
    "resourcename": "name",
    "resourcegroupname": "name",
    "subscriptionname": "subscriptionId",
    "resourcetype": "type",
    "regionname": "location",
    "resourcelocation": "location",
    "regionid": "location",
    "resourceid": "id",
}

# SKU-family reverse mapping for common VM size families.
SKU_FAMILY_PREFIXES = ("standard_", "basic_")

TAG_CONTRIBUTOR_ROLE = "4a9ae827-6dc8-4573-8ac7-8239d42aa03f"
CONTRIBUTOR_ROLE = "b24988ac-6180-42a0-ab88-20f7382dd24c"
INHERIT_TAG_POLICY_DEFINITION_ID = "/providers/Microsoft.Authorization/policyDefinitions/cd3aa116-8754-49c9-a813-ad46512ece54"

# Tags we materialize from the department tree.
TAG_KEYS = ["departamento_nivel_1", "departamento_nivel_2", "departamento_nivel_3",
            "departamento_nivel_4", "departamento_nivel_5", "centro_custo", "ambiente"]


class AzureTagPolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Loading ──────────────────────────────────────────────────────────

    async def list_azure_tenants(self) -> list[dict]:
        """Return the Azure source connections that carry a tenant_id."""
        result = await self.db.execute(
            select(SourceConnection).where(SourceConnection.connector_type == "azure_blob")
        )
        tenants: list[dict] = []
        for conn in result.scalars().all():
            cfg = conn.config_json or {}
            tenant_id = cfg.get("tenant_id")
            if tenant_id:
                tenants.append({
                    "connection_id": conn.id,
                    "code": conn.code,
                    "name": conn.name,
                    "tenant_id": tenant_id,
                })
        return tenants

    async def _load_departments(self) -> dict[int, Department]:
        result = await self.db.execute(select(Department).where(Department.active == True))
        return {d.id: d for d in result.scalars().all()}

    async def _load_projects(self) -> dict[int, Project]:
        result = await self.db.execute(select(Project).where(Project.active == True))
        return {p.id: p for p in result.scalars().all()}

    async def _load_reverse_mapping(self, connection_id: int) -> dict[str, str]:
        """Build destination_column -> source_column for a connection (reverse)."""
        result = await self.db.execute(
            select(SourceDestinationMapping).where(
                SourceDestinationMapping.source_connection_id == connection_id,
                SourceDestinationMapping.active == True,
            )
        )
        mapping: dict[str, str] = {}
        for m in result.scalars().all():
            # First mapping wins for a destination column (same rule as importers).
            mapping.setdefault(m.destination_column.lower(), m.source_column)
        return mapping

    async def _load_classification_rules(self, connection_id: int) -> list[ClassificationRule]:
        result = await self.db.execute(
            select(ClassificationRule)
            .where(
                ClassificationRule.active == True,
                ClassificationRule.department_id.isnot(None),
            )
            .order_by(ClassificationRule.priority)
        )
        rules = list(result.scalars().all())
        # Keep Azure rules: those explicitly scoped to azure, or global rules
        # that belong to this Azure connection.
        filtered = []
        for rule in rules:
            scope = rule.cloud_scope
            if scope and scope != "azure":
                continue
            filtered.append(rule)
        return filtered

    # ── Hierarchy resolution ─────────────────────────────────────────────

    def _hierarchy(self, dept: Department, dept_map: dict[int, Department]) -> dict:
        """Walk root->leaf and materialize departamento_nivel_* + centro_custo."""
        chain: list[Department] = []
        cur: Department | None = dept
        seen: set[int] = set()
        while cur and cur.id not in seen:
            seen.add(cur.id)
            chain.append(cur)
            cur = dept_map.get(cur.parent_id) if cur.parent_id else None
        chain.reverse()  # root -> leaf

        tags: dict[str, str] = {}
        for i, d in enumerate(chain[:5], start=1):
            tags[f"departamento_nivel_{i}"] = d.name or ""

        centro = next((d.centro_custo for d in reversed(chain) if d.centro_custo), None)
        ambiente = next((d.ambiente for d in reversed(chain) if d.ambiente), None)
        if centro:
            tags["centro_custo"] = centro
        if ambiente:
            tags["ambiente"] = ambiente
        return tags

    def _resolve_tags(self, rule: ClassificationRule, dept_map: dict[int, Department]) -> dict:
        dept = dept_map.get(rule.department_id)
        if not dept:
            return {}
        return self._hierarchy(dept, dept_map)

    # ── Reverse mapping (FOCUS field -> Azure alias) ─────────────────────

    def _reverse_field(self, field: str, reverse_map: dict[str, str]) -> str:
        """Translate a FOCUS field name to the Azure ARM alias."""
        key = field.lower().strip()
        if key in reverse_map:
            return reverse_map[key]
        if key in FOCUS_TO_ALIAS:
            return FOCUS_TO_ALIAS[key]
        # Already looks like an ARM field/alias: keep it.
        return field

    # ── Policy emission ──────────────────────────────────────────────────

    def _build_policy(
        self,
        display_name: str,
        description: str,
        if_condition: dict,
        operations: list[dict],
    ) -> dict:
        return {
            "mode": "Indexed",
            "displayName": display_name,
            "description": description,
            "policyRule": {
                "if": if_condition,
                "then": {
                    "effect": "modify",
                    "details": {
                        "roleDefinitionIds": [CONTRIBUTOR_ROLE, TAG_CONTRIBUTOR_ROLE],
                        "operations": operations,
                    },
                },
            },
        }

    @staticmethod
    def _tag_operations(tags: dict[str, str]) -> list[dict]:
        return [
            {"operation": "addOrReplace", "field": f"tags['{k}']", "value": v}
            for k, v in tags.items() if v
        ]

    def _build_policies(self, rules: list[ClassificationRule], dept_map: dict[int, Department],
                        reverse_map: dict[str, str]) -> dict:
        """Emit the three layers of policies."""
        inherit = self._inheritance_policies()
        rg_groups: dict[str, dict] = {}
        pattern_groups: dict[str, dict] = {}

        for rule in rules:
            tags = self._resolve_tags(rule, dept_map)
            if not tags:
                continue

            # Layer 3: explicit resource groups (grouped by unique tag set)
            parent = self._grouping_condition(rule, reverse_map)

            tag_sig = json.dumps(tags, sort_keys=True, ensure_ascii=False)
            if parent is not None:
                bucket = rg_groups.setdefault(tag_sig, {
                    "tags": tags, "names": [], "rule_names": [],
                })
                if parent not in bucket["names"]:
                    bucket["names"].append(parent)
                if rule.name not in bucket["rule_names"]:
                    bucket["rule_names"].append(rule.name)
            else:
                cond = self._condition_for_rule(rule, reverse_map)
                if cond is not None:
                    cond_sig = json.dumps(cond, sort_keys=True, ensure_ascii=False)
                    bucket = pattern_groups.setdefault(tag_sig + "|" + cond_sig, {
                        "tags": tags, "condition": cond, "rule_names": [],
                    })
                    if rule.name not in bucket["rule_names"]:
                        bucket["rule_names"].append(rule.name)

        return {
            "inheritance": inherit,
            "resource_groups": list(rg_groups.values()),
            "patterns": list(pattern_groups.values()),
        }

    @staticmethod
    def _inheritance_policies() -> list[dict]:
        policies = []
        for key in TAG_KEYS:
            policies.append({
                "name": f"Inherit {key} from resource group",
                "policyDefinitionId": INHERIT_TAG_POLICY_DEFINITION_ID,
                "parameters": {"tagName": {"value": key}},
                "note": "Uses the built-in 'Inherit a tag from the resource group' policy.",
            })
        return policies

    def _grouping_condition(self, rule: ClassificationRule, reverse_map: dict[str, str]) -> str | None:
        """Return a resource-group name when the rule matches resourceGroupName/SubscriptionName."""
        conditions = rule.conditions or []
        for cond in conditions:
            field = self._reverse_field(cond.get("field", ""), reverse_map)
            # resourceGroupName -> Azure 'name' grouping (and subscription-level
            # rules likewise map to the RG name for grouping purposes).
            if field in ("name", "resourceGroupName") and cond.get("operator") in ("=", "~"):
                values = cond.get("values") or []
                if values:
                    return values[0]
        return None

    def _condition_for_rule(self, rule: ClassificationRule, reverse_map: dict[str, str]) -> dict | None:
        """Translate a rule's conditions into an Azure Policy if-condition."""
        conditions = rule.conditions or []
        all_of: list[dict] = []
        for cond in conditions:
            field = self._reverse_field(cond.get("field", ""), reverse_map)
            operator = cond.get("operator", "=")
            values = cond.get("values") or []
            if not values:
                continue
            if field in ("name", "fullName"):
                # Name patterns: contains -> contains, exact -> equals, wildcard -> like
                if operator == "~":
                    for v in values:
                        all_of.append({"field": "name", "contains": v})
                else:
                    if len(values) == 1:
                        all_of.append({"field": "name", "equals": values[0]})
                    else:
                        all_of.append({"field": "name", "in": values})
            elif field in ("location",):
                if len(values) == 1:
                    all_of.append({"field": "location", "equals": values[0]})
                else:
                    all_of.append({"field": "location", "in": values})
            elif field in ("type",):
                if len(values) == 1:
                    all_of.append({"field": "type", "equals": values[0]})
                else:
                    all_of.append({"field": "type", "in": values})
            elif field.startswith("Microsoft.") or "." in field:
                # Alias (e.g. Microsoft.Compute/virtualMachines/sku.name): like for family
                if operator == "~" or any(v.endswith("*") for v in values):
                    for v in values:
                        all_of.append({"field": field, "like": v if v.endswith("*") else f"{v}*"})
                else:
                    if len(values) == 1:
                        all_of.append({"field": field, "equals": values[0]})
                    else:
                        all_of.append({"field": field, "in": values})
        if not all_of:
            return None
        return {"allOf": all_of} if len(all_of) > 1 else all_of[0]

    # ── Public API ───────────────────────────────────────────────────────

    async def generate_tenant_policy(self, connection_id: int) -> dict | None:
        """Generate policy documents for a single Azure tenant (connection)."""
        conn = await self.db.get(SourceConnection, connection_id)
        if not conn or resolve_connection_scope(conn.cloud_scope, conn.cloud_name) != "azure":
            return None

        dept_map = await self._load_departments()
        reverse_map = await self._load_reverse_mapping(connection_id)
        rules = await self._load_classification_rules(connection_id)

        policies = self._build_policies(rules, dept_map, reverse_map)

        return {
            "tenant_id": (conn.config_json or {}).get("tenant_id"),
            "connection_code": conn.code,
            "connection_name": conn.name,
            "rule_count": len(rules),
            "layers": policies,
        }


def slugify(value: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")
    return out or "policy"
