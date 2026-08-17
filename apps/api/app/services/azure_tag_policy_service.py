"""
Azure Tag Policy Service — generate Azure Policy definitions (and deployment
scripts) from the current classification business rules.

The classification rules reference FOCUS (Gold) columns such as `ResourceId`,
`resourceGroupName`, `source_resource_group_name`, `subscriptionName`,
`SubAccountName`, `ResourceName`, `ResourceType`, `SkuMeter`, and `RegionName`.
An Azure Policy evaluates ARM aliases/fields such as `name`, `type`, `location`
and `Microsoft.Compute/virtualMachines/sku.name`.

This module performs reverse mapping and semantic classification (FOCUS -> Azure ARM),
emitting compact policies grouped into:
  1. Resource-group inheritance (built-in "Inherit a tag from the resource group").
  2. Resource Group tag policies (exact lists grouped by identical tag sets + wildcard patterns).
  3. Individual resource patterns (name / SKU / VM size family / region / subscription).

Policies are grouped per Azure tenant (each `azure-*` source connection).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rules import ClassificationRule, Department, Project
from app.models.destination import SourceDestinationMapping
from app.models.source_connection import SourceConnection
from app.services.cloud_scope import resolve_connection_scope

logger = logging.getLogger("openfinops.azure_tag_policy")

# Semantic field scopes
SCOPE_RG = "RESOURCE_GROUP"
SCOPE_RESOURCE = "RESOURCE_NAME"
SCOPE_SUBSCRIPTION = "SUBSCRIPTION"
SCOPE_TYPE = "RESOURCE_TYPE"
SCOPE_SKU = "SKU"
SCOPE_LOCATION = "LOCATION"
SCOPE_UNKNOWN = "UNKNOWN"

# Map normalized field names to their semantic scope and default ARM field/alias
SEMANTIC_FIELD_MAP: dict[str, tuple[str, str]] = {
    # Resource Group
    "sourceresourcegroupname": (SCOPE_RG, "name"),
    "resourcegroupname": (SCOPE_RG, "name"),
    "resourcegroup": (SCOPE_RG, "name"),
    "resource_group_name": (SCOPE_RG, "name"),
    "resource_group": (SCOPE_RG, "name"),

    # Individual Resource
    "resourcename": (SCOPE_RESOURCE, "name"),
    "resource_name": (SCOPE_RESOURCE, "name"),
    "resourceid": (SCOPE_RESOURCE, "name"),
    "id": (SCOPE_RESOURCE, "name"),

    # Subscription
    "subaccountname": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "subscriptionname": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "subaccountid": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "subscriptionid": (SCOPE_SUBSCRIPTION, "subscriptionId"),

    # Resource Type
    "resourcetype": (SCOPE_TYPE, "type"),
    "resource_type": (SCOPE_TYPE, "type"),
    "servicename": (SCOPE_TYPE, "type"),
    "servicecategory": (SCOPE_TYPE, "type"),

    # SKU / Pricing / VM Family
    "sku": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "skumeter": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "skuid": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "servicesubcategory": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "pricingcategory": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "chargecategory": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),

    # Location
    "regionname": (SCOPE_LOCATION, "location"),
    "regionid": (SCOPE_LOCATION, "location"),
    "resourcelocation": (SCOPE_LOCATION, "location"),
    "location": (SCOPE_LOCATION, "location"),
}

TAG_CONTRIBUTOR_ROLE = "/providers/microsoft.authorization/roleDefinitions/4a9ae827-6dc8-4573-8ac7-8239d42aa03f"
CONTRIBUTOR_ROLE = "/providers/microsoft.authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"
INHERIT_TAG_POLICY_DEFINITION_ID = "/providers/Microsoft.Authorization/policyDefinitions/cd3aa116-8754-49c9-a813-ad46512ece54"

# Standard department hierarchy and organizational tags
TAG_KEYS = [
    "departamento_nivel_1",
    "departamento_nivel_2",
    "departamento_nivel_3",
    "departamento_nivel_4",
    "departamento_nivel_5",
    "centro_custo",
    "ambiente",
]


def format_wildcard(v: str) -> str:
    """Ensure a search term is properly formatted for Azure Policy 'like' operator."""
    s = v.strip()
    if not s:
        return "*"
    if "*" in s or "?" in s:
        return s
    return f"*{s}*"


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
            mapping.setdefault(m.destination_column.lower().replace("_", ""), m.source_column)
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
        filtered = []
        for rule in rules:
            scope = rule.cloud_scope
            if scope and scope.lower() != "azure":
                continue
            filtered.append(rule)
        return filtered

    # ── Hierarchy resolution ─────────────────────────────────────────────

    def _hierarchy(self, dept: Department, dept_map: dict[int, Department]) -> dict[str, str]:
        """Walk root->leaf and materialize departamento_nivel_* + centro_custo + ambiente."""
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
            if d.name:
                tags[f"departamento_nivel_{i}"] = d.name

        centro = next((d.centro_custo for d in reversed(chain) if d.centro_custo), None)
        ambiente = next((d.ambiente for d in reversed(chain) if d.ambiente), None)
        if centro:
            tags["centro_custo"] = centro
        if ambiente:
            tags["ambiente"] = ambiente
        return tags

    def _resolve_tags(self, rule: ClassificationRule, dept_map: dict[int, Department]) -> dict[str, str]:
        dept = dept_map.get(rule.department_id)
        if not dept:
            return {}
        return self._hierarchy(dept, dept_map)

    # ── Semantic field resolution ────────────────────────────────────────

    def _classify_field(self, raw_field: str, reverse_map: dict[str, str]) -> tuple[str, str]:
        """
        Identify the semantic scope and ARM alias for a classification condition field.
        Returns: (scope, arm_field)
        """
        norm = raw_field.lower().replace("_", "").replace("-", "").strip()
        # Check reverse mapping if available
        if norm in reverse_map:
            mapped = reverse_map[norm]
            mapped_norm = mapped.lower().replace("_", "").replace("-", "").strip()
            if mapped_norm in SEMANTIC_FIELD_MAP:
                return SEMANTIC_FIELD_MAP[mapped_norm]

        if norm in SEMANTIC_FIELD_MAP:
            return SEMANTIC_FIELD_MAP[norm]

        # Fallback heuristics
        if "group" in norm or "rg" in norm:
            return (SCOPE_RG, "name")
        if "sub" in norm or "account" in norm:
            return (SCOPE_SUBSCRIPTION, "subscriptionId")
        if "location" in norm or "region" in norm:
            return (SCOPE_LOCATION, "location")
        if "sku" in norm or "meter" in norm:
            return (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name")
        if "type" in norm:
            return (SCOPE_TYPE, "type")

        return (SCOPE_RESOURCE, "name")

    # ── Policy emission ──────────────────────────────────────────────────

    def _inheritance_policies(self) -> list[dict]:
        """Return the built-in inheritance policy assignments for all 7 organizational tags."""
        policies = []
        for key in TAG_KEYS:
            policies.append({
                "name": f"Inherit {key} from resource group",
                "tag_name": key,
                "policyDefinitionId": INHERIT_TAG_POLICY_DEFINITION_ID,
                "parameters": {"tagName": {"value": key}},
                "note": "Aplica a tag automaticamente em todos os recursos filhos dentro do Resource Group.",
            })
        return policies

    def _build_rg_condition(self, operator: str, values: list[str]) -> dict | None:
        """Build ARM if-condition for Resource Groups."""
        if not values:
            return None

        rg_type_cond = {"field": "type", "equals": "Microsoft.Resources/resourceGroups"}

        if operator == "~":
            if len(values) == 1:
                name_cond = {"field": "name", "like": format_wildcard(values[0])}
            else:
                name_cond = {"anyOf": [{"field": "name", "like": format_wildcard(v)} for v in values]}
        else:
            # Exact match (=)
            if len(values) == 1:
                name_cond = {"field": "name", "equals": values[0]}
            else:
                name_cond = {"field": "name", "in": values}

        return {"allOf": [rg_type_cond, name_cond]}

    def _build_resource_condition(
        self,
        conditions: list[dict],
        reverse_map: dict[str, str]
    ) -> dict | None:
        """Build ARM if-condition for individual resources, types, SKUs or locations."""
        all_of: list[dict] = []

        has_resource_scope = False

        for cond in conditions:
            field_name = cond.get("field", "")
            operator = cond.get("operator", "=")
            values = cond.get("values") or []
            if not values:
                continue

            scope, arm_field = self._classify_field(field_name, reverse_map)

            if scope == SCOPE_RESOURCE:
                has_resource_scope = True
                if operator == "~":
                    if len(values) == 1:
                        all_of.append({"field": arm_field, "like": format_wildcard(values[0])})
                    else:
                        all_of.append({"anyOf": [{"field": arm_field, "like": format_wildcard(v)} for v in values]})
                else:
                    if len(values) == 1:
                        all_of.append({"field": arm_field, "equals": values[0]})
                    else:
                        all_of.append({"field": arm_field, "in": values})

            elif scope == SCOPE_SKU:
                if operator == "~" or any(not v.startswith("Microsoft.") for v in values):
                    if len(values) == 1:
                        all_of.append({"field": arm_field, "like": format_wildcard(values[0])})
                    else:
                        all_of.append({"anyOf": [{"field": arm_field, "like": format_wildcard(v)} for v in values]})
                else:
                    if len(values) == 1:
                        all_of.append({"field": arm_field, "equals": values[0]})
                    else:
                        all_of.append({"field": arm_field, "in": values})

            elif scope == SCOPE_TYPE:
                if len(values) == 1:
                    all_of.append({"field": "type", "equals": values[0]})
                else:
                    all_of.append({"field": "type", "in": values})

            elif scope == SCOPE_LOCATION:
                if len(values) == 1:
                    all_of.append({"field": "location", "equals": values[0]})
                else:
                    all_of.append({"field": "location", "in": values})

            elif scope == SCOPE_SUBSCRIPTION:
                # Subscription-level filtering
                if operator == "~":
                    if len(values) == 1:
                        all_of.append({"field": "subscriptionId", "like": format_wildcard(values[0])})
                    else:
                        all_of.append({"anyOf": [{"field": "subscriptionId", "like": format_wildcard(v)} for v in values]})
                else:
                    if len(values) == 1:
                        all_of.append({"field": "subscriptionId", "equals": values[0]})
                    else:
                        all_of.append({"field": "subscriptionId", "in": values})

        if not all_of:
            return None

        # If matching by resource name, ensure we don't accidentally match resource groups
        if has_resource_scope:
            all_of.insert(0, {"field": "type", "notEquals": "Microsoft.Resources/resourceGroups"})

        return {"allOf": all_of} if len(all_of) > 1 else all_of[0]

    def _build_policies(
        self,
        rules: list[ClassificationRule],
        dept_map: dict[int, Department],
        reverse_map: dict[str, str],
    ) -> dict:
        """Emit the layers of Azure Tag policies."""
        inherit = self._inheritance_policies()

        # Group exact RG rules by unique tag set: tag_sig -> {tags, names: set(), rule_names: set()}
        rg_exact_groups: dict[str, dict] = {}

        # RG wildcard pattern policies
        rg_pattern_groups: dict[str, dict] = {}

        # Resource / SKU / Location pattern policies
        resource_pattern_groups: dict[str, dict] = {}

        for rule in rules:
            tags = self._resolve_tags(rule, dept_map)
            if not tags:
                continue

            tag_sig = json.dumps(tags, sort_keys=True, ensure_ascii=False)
            conditions = rule.conditions or []

            # Check if this rule applies to Resource Groups
            is_rg_rule = any(
                self._classify_field(c.get("field", ""), reverse_map)[0] == SCOPE_RG
                for c in conditions
            )

            if is_rg_rule:
                for c in conditions:
                    scope, _ = self._classify_field(c.get("field", ""), reverse_map)
                    if scope != SCOPE_RG:
                        continue
                    operator = c.get("operator", "=")
                    values = [str(v).strip() for v in (c.get("values") or []) if str(v).strip()]
                    if not values:
                        continue

                    if operator == "=":
                        # Group exact RGs by tag signature
                        bucket = rg_exact_groups.setdefault(tag_sig, {
                            "tags": tags,
                            "names": [],
                            "rule_names": [],
                        })
                        for val in values:
                            if val not in bucket["names"]:
                                bucket["names"].append(val)
                        if rule.name not in bucket["rule_names"]:
                            bucket["rule_names"].append(rule.name)
                    else:
                        # Wildcard / nomenclature RG rule
                        cond = self._build_rg_condition(operator, values)
                        if cond:
                            cond_sig = json.dumps(cond, sort_keys=True, ensure_ascii=False)
                            key = f"{tag_sig}|{cond_sig}"
                            bucket = rg_pattern_groups.setdefault(key, {
                                "tags": tags,
                                "condition": cond,
                                "patterns": values,
                                "rule_names": [],
                            })
                            if rule.name not in bucket["rule_names"]:
                                bucket["rule_names"].append(rule.name)
            else:
                # Individual resource / SKU / location / subscription rule
                cond = self._build_resource_condition(conditions, reverse_map)
                if cond:
                    cond_sig = json.dumps(cond, sort_keys=True, ensure_ascii=False)
                    key = f"{tag_sig}|{cond_sig}"
                    bucket = resource_pattern_groups.setdefault(key, {
                        "tags": tags,
                        "condition": cond,
                        "rule_names": [],
                    })
                    if rule.name not in bucket["rule_names"]:
                        bucket["rule_names"].append(rule.name)

        # Convert exact RG groups into formatted policies
        rg_exact_list = []
        for grp in rg_exact_groups.values():
            rg_names = grp["names"]
            if not rg_names:
                continue
            cond = {
                "allOf": [
                    {"field": "type", "equals": "Microsoft.Resources/resourceGroups"},
                    {"field": "name", "in": rg_names} if len(rg_names) > 1 else {"field": "name", "equals": rg_names[0]},
                ]
            }
            rg_exact_list.append({
                "tags": grp["tags"],
                "names": rg_names,
                "rule_names": grp["rule_names"],
                "condition": cond,
            })

        return {
            "inheritance": inherit,
            "resource_groups": rg_exact_list,
            "rg_patterns": list(rg_pattern_groups.values()),
            "patterns": list(resource_pattern_groups.values()),
        }

    # ── Public API ───────────────────────────────────────────────────────

    async def generate_tenant_policy(self, connection_id: int) -> dict | None:
        """Generate complete Azure Tag Policy documents for a single Azure tenant."""
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
    out = "".join(c if c.isalnum() else "-" for c in str(value).lower()).strip("-")
    return out or "policy"
