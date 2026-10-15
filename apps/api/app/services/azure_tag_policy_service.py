"""Compile classification rules into non-overlapping Azure tag policies.

The billing classifier is first-match-wins (priority, id). Azure Policy has no
such ordering guarantee for overlapping ``modify`` effects, so this service
turns every lower-priority selector into an explicit decision region: selector
AND NOT(all earlier eligible selectors). This is deliberately direct tagging;
the broad RG inheritance policy cannot represent resource-level exceptions.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.destination import DestinationTable, SourceDestinationMapping
from app.models.azure_tag_mapping import AzureTagMapping
from app.models.rules import ClassificationRule, Department
from app.models.source_connection import SourceConnection
from app.services.classification_engine import normalize_rule_conditions
from app.services.cloud_scope import (
    resolve_connection_scope, rule_matches_cloud, rule_matches_connection,
    rule_matches_destination,
)
from app.services import lakehouse_layers as layers

logger = logging.getLogger("openfinops.azure_tag_policy")

SCOPE_RG = "RESOURCE_GROUP"
SCOPE_RESOURCE = "RESOURCE"
SCOPE_SUBSCRIPTION = "SUBSCRIPTION"
SCOPE_TYPE = "RESOURCE_TYPE"
SCOPE_SKU = "SKU"
SCOPE_LOCATION = "LOCATION"
SCOPE_UNSUPPORTED = "UNSUPPORTED"

# Only selectors observable by ARM are allowed into a tag policy. Cost-line
# fields are intentionally absent: Purchase/Return/reservation classifications
# have no stable Azure resource to tag.
SEMANTIC_FIELD_MAP: dict[str, tuple[str, str]] = {
    "sourceresourcegroupname": (SCOPE_RG, "name"),
    "resourcegroupname": (SCOPE_RG, "name"),
    "resourcegroup": (SCOPE_RG, "name"),
    "resourcename": (SCOPE_RESOURCE, "name"),
    "resourceid": (SCOPE_RESOURCE, "id"),
    "id": (SCOPE_RESOURCE, "id"),
    "subaccountname": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "subscriptionname": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "subaccountid": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "subscriptionid": (SCOPE_SUBSCRIPTION, "subscriptionId"),
    "resourcetype": (SCOPE_TYPE, "type"),
    "servicename": (SCOPE_TYPE, "type"),
    "sku": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "skumeter": (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name"),
    "regionname": (SCOPE_LOCATION, "location"),
    "regionid": (SCOPE_LOCATION, "location"),
    "resourcelocation": (SCOPE_LOCATION, "location"),
    "location": (SCOPE_LOCATION, "location"),
}

TAG_CONTRIBUTOR_ROLE = "/providers/microsoft.authorization/roleDefinitions/4a9ae827-6dc8-4573-8ac7-8239d42aa03f"
AZURE_POLICY_RESOURCE_GROUP_TYPE = "Microsoft.Resources/subscriptions/resourceGroups"
SAFE_FOCUS_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
UUID_ANY_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
# Keep generated definitions comfortably below Azure Policy payload limits. A
# blocked rule is safer than emitting a definition Azure will reject or truncate.
MAX_CONDITION_BYTES = 64_000


def _norm(value: str) -> str:
    return str(value or "").lower().replace("_", "").replace("-", "").strip()


def _subscription_name_key(value: str) -> str:
    """Use the same accent-insensitive comparison as the cost classifier."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]", "", normalized.encode("ascii", "ignore").decode().lower())


def _keeps_contains(terms: list[dict], scope: str) -> bool:
    scoped = [term for term in terms if term["scope"] == scope]
    return bool(scoped) and all(term.get("operator") in {"~", "contains", "like"} for term in scoped)


def _canonical_arm_spelling(spellings: dict[str, int]) -> str:
    """Pick the Silver-observed ARM spelling with the most evidence.

    Resource and resource group names are case-insensitive in ARM; Azure Policy
    ``equals``/``like``/``contains`` are also case-insensitive. Duplicate casings
    are the same object, so we keep one literal for the policy JSON.
    """
    return max(
        spellings.items(),
        key=lambda item: (item[1], sum(ch.isupper() for ch in item[0]), item[0]),
    )[0]


def _collapse_arm_values(values: list[str]) -> list[str]:
    """Drop name/id literals that differ only by case, keeping first-seen spelling."""
    chosen: dict[str, str] = {}
    for value in values:
        folded = value.casefold()
        if folded not in chosen:
            chosen[folded] = value
    return list(chosen.values())


def _collapse_tag_keys(tags: dict[str, str]) -> dict[str, str]:
    """Tag names are case-insensitive; values remain distinct if casing differs."""
    out: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in tags.items():
        folded = key.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out[key] = value
    return out


def _condition(field: str, operator: str, values: list[str]) -> dict | None:
    """Build a Policy condition with OR within a classification condition."""
    if not values:
        return None
    if operator == "=":
        if field in {"name", "id"}:
            values = _collapse_arm_values(values)
        return {"field": field, "equals": values[0]} if len(values) == 1 else {"field": field, "in": values}
    if operator == "!=":
        return {"field": field, "notEquals": values[0]} if len(values) == 1 else {"field": field, "notIn": values}
    if operator in {"~", "contains", "like"}:
        # Subscription GUIDs stay exact; contains is for RG, resource id/name,
        # type, SKU and location.
        if field == "subscriptionId":
            return {"field": field, "equals": values[0]} if len(values) == 1 else {"field": field, "in": values}
        return {"field": field, "contains": values[0]} if len(values) == 1 else {
            "anyOf": [{"field": field, "contains": value} for value in values]
        }
    return None


class AzureTagPolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_azure_tenants(self) -> list[dict]:
        rows = await self.db.execute(select(SourceConnection).where(SourceConnection.connector_type == "azure_blob"))
        return [{"connection_id": c.id, "code": c.code, "name": c.name,
                 "tenant_id": (c.config_json or {}).get("tenant_id")}
                for c in rows.scalars().all() if (c.config_json or {}).get("tenant_id")]

    async def _subscription_home_tenants(self, billing_conn: SourceConnection) -> dict[str, str]:
        """Map subscription GUID → ARM home tenant (never assume the billing collector).

        Billing TI can list cost for subscriptions that live in Azure-4P (…8fd7).
        Policy must be applied in the tenant that owns the subscription. GUIDs
        come from each connection's ``subscription_ids`` and from Resource Graph
        reachable with that connection's credentials. When TI and 4P both mention
        the same GUID, the non-billing tenant wins.
        """
        billing_tenant = str((billing_conn.config_json or {}).get("tenant_id") or "").strip().casefold()
        mapping: dict[str, str] = {}

        def remember(sid: str, tenant: str) -> None:
            sid, tenant = sid.strip(), tenant.strip()
            if not UUID_RE.match(sid) or not tenant:
                return
            key = sid.casefold()
            existing = mapping.get(key)
            if existing and existing.casefold() != tenant.casefold():
                if existing.casefold() == billing_tenant:
                    mapping[key] = tenant
                return
            mapping[key] = tenant

        rows = await self.db.execute(select(SourceConnection).where(SourceConnection.connector_type == "azure_blob"))
        connections = list(rows.scalars().all())
        for conn in connections:
            tenant = str((conn.config_json or {}).get("tenant_id") or "").strip()
            if not tenant:
                continue
            for raw in (conn.config_json or {}).get("subscription_ids") or []:
                remember(str(raw), tenant)
            try:
                from app.services.azure_resource_graph_service import discover_subscriptions
                for item in await discover_subscriptions(dict(conn.config_json or {})):
                    remember(str(item.get("subscription_id") or ""), tenant)
            except Exception as exc:
                logger.warning("Azure tag policy home-tenant discovery for %s: %s", conn.code, str(exc)[:180])
        return mapping

    async def _load_departments(self) -> dict[int, Department]:
        rows = await self.db.execute(select(Department).where(Department.active == True))
        return {row.id: row for row in rows.scalars().all()}

    async def _tag_mappings(self) -> list[dict[str, str]]:
        rows = await self.db.execute(select(AzureTagMapping).where(
            AzureTagMapping.active == True,
        ).order_by(AzureTagMapping.id))
        return [{"tag_key": row.tag_key, "focus_field": row.focus_field}
                for row in rows.scalars().all()]

    async def _connection_destinations(self, connection_id: int) -> list[tuple[int, bool]]:
        result = await self.db.execute(select(SourceDestinationMapping.destination_table_id).where(
            SourceDestinationMapping.source_connection_id == connection_id,
            SourceDestinationMapping.active == True,
        ).distinct())
        ids = [row[0] for row in result.all()]
        if not ids:
            return []
        tables = (await self.db.execute(select(DestinationTable).where(DestinationTable.id.in_(ids)))).scalars().all()
        return [(table.id, (table.name or "").strip().upper() == "MODELO FOCUS" or
                (table.physical_name or "").strip().lower() == "dest_modelo_focus") for table in tables]

    async def _load_classification_rules(self, conn: SourceConnection) -> list[ClassificationRule]:
        destinations = await self._connection_destinations(conn.id)
        rows = await self.db.execute(select(ClassificationRule).where(
            ClassificationRule.active == True,
            ClassificationRule.department_id.isnot(None),
        ).order_by(ClassificationRule.priority, ClassificationRule.id))
        out: list[ClassificationRule] = []
        for rule in rows.scalars().all():
            if not rule_matches_connection(rule.connection_id, conn.id):
                continue
            if not rule_matches_cloud(rule.cloud_scope, conn.cloud_scope, conn.cloud_name):
                continue
            if not any(rule_matches_destination(rule.destination_table_id, dest_id, is_focus_destination=is_focus)
                       for dest_id, is_focus in destinations):
                continue
            out.append(rule)
        return out

    def _hierarchy(self, dept: Department, departments: dict[int, Department]) -> dict[str, str]:
        chain, seen = [], set()
        current: Department | None = dept
        while current and current.id not in seen:
            seen.add(current.id); chain.append(current)
            current = departments.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        tags = {f"organograma_nivel_{i}": item.name for i, item in enumerate(chain[:5], 1) if item.name}
        centro = next((item.centro_custo for item in reversed(chain) if item.centro_custo), None)
        ambiente = next((item.ambiente for item in reversed(chain) if item.ambiente), None)
        if centro: tags["centro_custo"] = centro
        if ambiente: tags["ambiente"] = ambiente
        return tags

    async def _subscription_catalog(self, conn: SourceConnection) -> tuple[dict[str, list[str]], list[str], str | None]:
        """Resolve name -> subscription GUID, without making an unsafe guess."""
        configured = [str(v).strip() for v in ((conn.config_json or {}).get("subscription_ids") or []) if str(v).strip()]
        catalog: dict[str, list[str]] = defaultdict(list)
        ids = [value for value in configured if UUID_RE.match(value)]
        try:
            from app.services.azure_resource_graph_service import discover_subscriptions
            discovered = await discover_subscriptions(dict(conn.config_json or {}))
            for item in discovered:
                sid = str(item.get("subscription_id") or "").strip()
                name = str(item.get("subscription_name") or "").strip()
                if sid:
                    ids.append(sid)
                    catalog[name].append(sid)
        except Exception as exc:  # ResourceId/RG exports remain useful.
            message = f"Não foi possível consultar Azure Resource Graph: {str(exc)[:180]}"
            logger.warning("Azure tag policy subscription discovery for %s: %s", conn.code, message)
            return catalog, sorted(set(ids)), message
        return catalog, sorted(set(ids)), None

    async def list_subscriptions(self, connection_id: int) -> list[dict[str, str]]:
        conn = await self.db.get(SourceConnection, connection_id)
        if not conn or conn.connector_type != "azure_blob":
            raise ValueError("Conexão Azure não encontrada")
        catalog, ids, error = await self._subscription_catalog(conn)
        if error:
            raise RuntimeError(error)
        name_by_id = {sid.casefold(): name for name, values in catalog.items() for sid in values}
        return [{"subscription_id": sid, "name": name_by_id.get(sid.casefold(), sid)} for sid in ids]

    async def _silver_evidence(self, conn: SourceConnection, mappings: list[dict[str, str]]) -> tuple[list[dict], str | None]:
        """Return concrete Azure targets classified in the latest Silver period.

        ``business_rule.classification.id`` is written by the classifier at the
        moment a rule wins. It is the safe link between a Silver cost line and a
        classification rule: department names are neither unique nor enough to
        distinguish a broad rule from a resource exception.
        """
        safe_code = (conn.code or "").replace("'", "''")
        unsafe = [mapping["focus_field"] for mapping in mappings
                  if not SAFE_FOCUS_FIELD_RE.fullmatch(mapping["focus_field"])]
        if unsafe:
            return [], f"Campo FOCUS inválido no mapeamento de tags: {', '.join(unsafe)}"
        dynamic_fields = ",\n                    ".join(
            f'trim(CAST("{mapping["focus_field"]}" AS varchar)) AS tag_value_{index}'
            for index, mapping in enumerate(mappings)
        )
        dynamic_group = ", " + ", ".join(str(index + 6) for index in range(len(mappings))) if mappings else ""
        try:
            from app.services.trino_service import execute as trino_execute
            rows = await trino_execute(f"""
                WITH latest AS (
                    SELECT max(_billing_period) AS period
                    FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
                    WHERE cloud_origem = '{safe_code}'
                      AND CAST(source_connection_id AS bigint) = {int(conn.id)}
                )
                SELECT
                    CAST(json_extract_scalar(business_rule, '$.classification.id') AS integer) AS rule_id,
                    trim(subaccountid) AS subscription_id,
                    trim(subaccountname) AS subscription_name,
                    trim(source_resource_group_name) AS resource_group_name,
                    trim(resourceid) AS resource_id,
                    {dynamic_fields + ',' if dynamic_fields else ''}
                    count(*) AS evidence_count
                FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
                WHERE cloud_origem = '{safe_code}'
                  AND CAST(source_connection_id AS bigint) = {int(conn.id)}
                  AND _billing_period = (SELECT period FROM latest)
                  AND json_extract_scalar(business_rule, '$.classification.id') IS NOT NULL
                GROUP BY 1, 2, 3, 4, 5{dynamic_group}
            """, timeout_s=60)
        except Exception as exc:
            message = f"Não foi possível consultar evidências na Silver: {str(exc)[:180]}"
            logger.warning("Azure tag policy Silver evidence for %s: %s", conn.code, message)
            return [], message
        return rows, None

    @staticmethod
    def _selected_department_ids(departments: dict[int, Department], department_id: int | None) -> set[int] | None:
        """Return a selected organizational node plus all of its descendants."""
        if department_id is None:
            return None
        selected = {department_id}
        changed = True
        while changed:
            changed = False
            for department in departments.values():
                if department.parent_id in selected and department.id not in selected:
                    selected.add(department.id)
                    changed = True
        return selected

    @staticmethod
    def _evidence_subscription_id(row: dict) -> str:
        value = str(row.get("subscription_id") or "").strip()
        return value if UUID_RE.match(value) else ""

    @staticmethod
    def _replace_terms(terms: list[dict], scope: str, replacement: dict, *, remove_all: bool = False) -> list[dict]:
        """Replace an ARM dimension with a concrete target observed in Silver."""
        out: list[dict] = []
        replaced = False
        for term in terms:
            if term["scope"] == scope and (remove_all or not replaced):
                if not replaced:
                    out.append(replacement)
                    replaced = True
                continue
            out.append(term)
        if not replaced:
            out.append(replacement)
        return out

    def _concrete_items(self, rule: ClassificationRule, terms: list[dict], evidence: list[dict], hierarchy: dict[str, str], mappings: list[dict[str, str]] | bool, selected: bool | list[dict],
                        diagnostics: list[dict] | None = None) -> list[dict]:
        """Turn Silver evidence or current Azure inventory into scoped targets.

        Classification ``~`` (contains) is kept for RG name, resource id/name,
        type, SKU and location. Silver evidence helps select subscriptions when deciding
        whether an assignment should be generated. SubAccountName stays a GUID list with ``equals``; contains
        is never used for subscriptions. Exact ``=`` RG or Resource ID rules
        still pin the observed ARM literal.
        """
        # Compatibility with the former internal helper signature used by
        # focused unit tests: (..., tags, selected, diagnostics).
        if isinstance(mappings, bool):
            legacy_selected = mappings
            diagnostics = selected if isinstance(selected, list) else (diagnostics or [])
            mappings = [{"tag_key": key, "focus_field": key} for key in hierarchy]
            selected = legacy_selected
        diagnostics = diagnostics if diagnostics is not None else []
        scopes = {term["scope"] for term in terms}
        if not scopes.intersection({SCOPE_RESOURCE, SCOPE_RG, SCOPE_SUBSCRIPTION}):
            diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "not_deployable",
                                "reason": "Regra ARM sem escopo de subscription, Resource Group ou recurso"})
            return []
        level = (SCOPE_RESOURCE if SCOPE_RESOURCE in scopes else
                 SCOPE_RG if SCOPE_RG in scopes else SCOPE_SUBSCRIPTION)
        rg_contains = level == SCOPE_RG and _keeps_contains(terms, SCOPE_RG)
        resource_contains = level == SCOPE_RESOURCE and _keeps_contains(terms, SCOPE_RESOURCE)
        grouped: dict[tuple, dict] = {}
        for row in evidence:
            subscription_id = self._evidence_subscription_id(row)
            if not subscription_id:
                continue
            tag_values = tuple(str(row.get(f"tag_value_{i}") or "") for i in range(len(mappings)))
            count = int(row.get("evidence_count") or 0)
            if level == SCOPE_RESOURCE:
                resource_id = str(row.get("resource_id") or "").strip()
                if not resource_id or "/providers/" not in resource_id.lower():
                    continue
                key = (subscription_id, *tag_values) if resource_contains else (subscription_id, resource_id.casefold(), *tag_values)
                entry = grouped.setdefault(key, {"count": 0, "spellings": {}})
                entry["count"] += count
                entry["spellings"][resource_id] = entry["spellings"].get(resource_id, 0) + count
            elif level == SCOPE_RG:
                resource_group = str(row.get("resource_group_name") or "").strip()
                if not resource_group:
                    continue
                key = (subscription_id, *tag_values) if rg_contains else (subscription_id, resource_group.casefold(), *tag_values)
                entry = grouped.setdefault(key, {"count": 0, "spellings": {}})
                entry["count"] += count
                entry["spellings"][resource_group] = entry["spellings"].get(resource_group, 0) + count
            else:
                key = (subscription_id, *tag_values)
                entry = grouped.setdefault(key, {"count": 0, "spellings": {}})
                entry["count"] += count

        if not grouped:
            diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                "reason": "Sem alvo Azure tagueável na evidência Silver ou no inventário atual do Azure"})
            return []
        items: list[dict] = []
        for key, entry in sorted(grouped.items()):
            evidence_count = entry["count"]
            subscription_id = key[0]
            value_offset = 2 if ((level == SCOPE_RESOURCE and not resource_contains) or (level == SCOPE_RG and not rg_contains)) else 1
            tags = {mapping["tag_key"]: (hierarchy.get(mapping["focus_field"]) or key[value_offset + index])
                    for index, mapping in enumerate(mappings)}
            tags = _collapse_tag_keys({key: value for key, value in tags.items() if value})
            missing_tags = [mapping["tag_key"] for mapping in mappings
                            if mapping["tag_key"].casefold() not in {key.casefold() for key in tags}]
            if missing_tags:
                diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                    "reason": "Sem valor na hierarquia organizacional nem evidência Silver para as tags: "
                                              + ", ".join(missing_tags)})
                continue
            assignment_scope_kind = "subscription"
            assignment_resource_group = None
            if level == SCOPE_RESOURCE:
                item_terms = self._replace_terms(terms, SCOPE_SUBSCRIPTION,
                                                 {"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": [subscription_id]}, remove_all=True)
                if resource_contains:
                    patterns = [value for term in terms if term["scope"] == SCOPE_RESOURCE for value in term["values"]]
                    observed = {"resource_id": " · ".join(patterns)}
                else:
                    resource_id = _canonical_arm_spelling(entry["spellings"])
                    item_terms = self._replace_terms(item_terms, SCOPE_RESOURCE,
                                                     {"scope": SCOPE_RESOURCE, "field": "id", "operator": "=", "values": [resource_id]}, remove_all=True)
                    observed = {"resource_id": resource_id}
                targets = ("resource",)
            elif level == SCOPE_RG:
                item_terms = self._replace_terms(terms, SCOPE_SUBSCRIPTION,
                                                 {"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": [subscription_id]}, remove_all=True)
                if rg_contains:
                    patterns = [value for term in terms if term["scope"] == SCOPE_RG for value in term["values"]]
                    observed = {"resource_group_name": " · ".join(patterns)}
                else:
                    resource_group = _canonical_arm_spelling(entry["spellings"])
                    item_terms = self._replace_terms(item_terms, SCOPE_RG,
                                                     {"scope": SCOPE_RG, "field": "name", "operator": "=", "values": [resource_group]}, remove_all=True)
                    observed = {"resource_group_name": resource_group}
                    assignment_scope_kind = "resource_group"
                    assignment_resource_group = resource_group
                targets = ("resource_group", "resource")
            else:
                # SubAccountName is not an ARM field. The observed GUID list is.
                item_terms = [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": [subscription_id]}]
                targets = ("resource_group", "resource")
                observed = {}
            items.append({"rule": rule, "terms": item_terms, "tags": tags,
                          "scope_subscriptions": [subscription_id], "targets": targets,
                          "evidence_count": evidence_count, "observed": observed,
                          "assignment_scope_kind": assignment_scope_kind,
                          "assignment_resource_group": assignment_resource_group,
                          "selected": selected})
        return items

    @staticmethod
    def _explicit_scope_subscriptions(terms: list[dict]) -> list[str]:
        ids: set[str] = set()
        for term in terms:
            if term["scope"] == SCOPE_SUBSCRIPTION:
                ids.update(term["values"])
            elif term["scope"] == SCOPE_RESOURCE and term["field"] == "id":
                for value in term["values"]:
                    ids.update(UUID_ANY_RE.findall(value))
        return sorted(ids)

    def _field_scope(self, field: str) -> tuple[str, str]:
        return SEMANTIC_FIELD_MAP.get(_norm(field), (SCOPE_UNSUPPORTED, ""))

    def _translate_conditions(self, rule: ClassificationRule, catalog: dict[str, list[str]], diagnostics: list[dict], *,
                              resolve_subscriptions: bool = True) -> list[dict] | None:
        translated: list[dict] = []
        for cond in normalize_rule_conditions(rule):
            scope, arm_field = self._field_scope(str(cond.get("field") or ""))
            values = [str(value).strip() for value in cond.get("values", []) if str(value).strip()]
            operator = str(cond.get("operator") or "=")
            if scope == SCOPE_UNSUPPORTED:
                diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "not_deployable",
                                    "reason": f"Campo financeiro ou não mapeável para ARM: {cond.get('field')}"})
                return None
            if operator not in {"=", "~", "!="}:
                diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "not_deployable",
                                    "reason": f"Operador não suportado pela Azure Policy: {operator}"})
                return None
            if scope == SCOPE_SUBSCRIPTION and resolve_subscriptions:
                resolved: list[str] = []
                for value in values:
                    if UUID_RE.match(value):
                        resolved.append(value)
                    elif operator == "=":
                        matches = sorted({sid for name, ids in catalog.items()
                                          if _subscription_name_key(name) == _subscription_name_key(value)
                                          for sid in ids})
                        if len(matches) == 1:
                            resolved.append(matches[0])
                        else:
                            diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                                "reason": "Assinatura não encontrada ou ambígua", "value": value, "matches": matches})
                            return None
                    else:
                        diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                            "reason": "Assinatura por nome exige igualdade exata", "value": value})
                        return None
                values = resolved
            translated.append({"scope": scope, "field": arm_field, "operator": operator, "values": values})
        if not translated:
            diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "not_deployable",
                                "reason": "Regra sem condições ARM válidas"})
            return None
        return translated

    @staticmethod
    def _rule_matches_selected_subscription(
        rule: ClassificationRule, catalog: dict[str, list[str]], subscription_id: str,
        subscription_name: str,
    ) -> bool:
        """Check subscription conditions using the classifier's name normalization.

        This also proves that exact/contains rules for another subscription are
        disjoint without requiring that other subscription to be accessible to
        the selected connection.
        """
        selected_id = subscription_id.casefold()
        selected_name = _subscription_name_key(subscription_name)
        for condition in normalize_rule_conditions(rule):
            field_name = str(condition.get("field") or "")
            scope, _ = SEMANTIC_FIELD_MAP.get(_norm(field_name), (SCOPE_UNSUPPORTED, ""))
            if scope != SCOPE_SUBSCRIPTION:
                continue
            operator = str(condition.get("operator") or "=")
            values = [str(value).strip() for value in condition.get("values", []) if str(value).strip()]
            matches: list[bool] = []
            for value in values:
                if UUID_RE.fullmatch(value):
                    actual = selected_id
                    expected = value.casefold()
                else:
                    known_ids = {sid.casefold() for name, ids in catalog.items()
                                 if _subscription_name_key(name) == _subscription_name_key(value)
                                 for sid in ids}
                    if known_ids:
                        actual, expected = selected_id, "\0".join(sorted(known_ids))
                    else:
                        actual, expected = selected_name, _subscription_name_key(value)
                if operator == "=":
                    matches.append(actual == expected or ("\0" in expected and actual in expected.split("\0")))
                elif operator == "~":
                    matches.append(expected in actual if "\0" not in expected else actual in expected.split("\0"))
                elif operator == "!=":
                    matches.append(actual != expected and ("\0" not in expected or actual not in expected.split("\0")))
                else:
                    return False
            condition_matches = all(matches) if operator == "!=" else any(matches)
            if not matches or not condition_matches:
                return False
        return True

    @staticmethod
    def _pin_subscription(terms: list[dict], subscription_id: str) -> list[dict]:
        """Replace rule-level subscription names with the validated selected GUID."""
        return [term for term in terms if term["scope"] != SCOPE_SUBSCRIPTION] + [{
            "scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": [subscription_id],
        }]

    def _selector(self, terms: list[dict], target: str, *, omit_rg_scope: bool = False) -> dict | None:
        resource_only = {SCOPE_RESOURCE, SCOPE_TYPE, SCOPE_SKU, SCOPE_LOCATION}
        if target == "resource_group" and any(term["scope"] in resource_only for term in terms):
            return None
        clauses: list[dict] = [{"field": "type", "equals": AZURE_POLICY_RESOURCE_GROUP_TYPE}
                               if target == "resource_group" else
                               {"field": "type", "notEquals": AZURE_POLICY_RESOURCE_GROUP_TYPE}]
        if target == "resource":
            # Azure SQL's built-in master database does not support tags.
            # Keep it outside every resource modify policy to avoid futile
            # remediations and permanent NonCompliant records.
            clauses.append({"not": {"allOf": [
                {"field": "type", "equals": "Microsoft.Sql/servers/databases"},
                {"field": "name", "like": "*/master"},
            ]}})
        for term in terms:
            if term["scope"] == SCOPE_SUBSCRIPTION:
                # Assignment is already /subscriptions/{guid}. Mode All (RG) rejects
                # field subscriptionId (InvalidPolicyAliasFormat). Indexed does not
                # need it either.
                continue
            if omit_rg_scope and term["scope"] == SCOPE_RG:
                continue
            if term["scope"] == SCOPE_RG and target == "resource":
                raw = _condition("name", term["operator"], term["values"])
                if raw is None: return None
                if "anyOf" in raw:
                    # ``value`` is a condition leaf, not a container for
                    # ``anyOf``. Convert every RG-name alternative to its own
                    # value expression so the emitted policy is valid JSON.
                    part = {"anyOf": [
                        {"value": "[resourceGroup().name]", **{key: value for key, value in option.items() if key != "field"}}
                        for option in raw["anyOf"]
                    ]}
                else:
                    raw.pop("field", None)
                    part = {"value": "[resourceGroup().name]", **raw}
            else:
                part = _condition("name" if term["scope"] == SCOPE_RG else term["field"], term["operator"], term["values"])
            if part is None: return None
            clauses.append(part)
        return clauses[0] if len(clauses) == 1 else {"allOf": clauses}

    @staticmethod
    def _effective(selector: dict, previous: list[dict]) -> dict:
        if not previous:
            return selector
        exclusions = {"anyOf": previous} if len(previous) > 1 else previous[0]
        return {"allOf": [selector, {"not": exclusions}]}

    @staticmethod
    def _scopes_disjoint(scope_a: list[str], scope_b: list[str]) -> bool:
        """Retorna True se ambos os escopos são não-vazios e não se intersectam.

        Se algum escopo estiver vazio (regra sem evidência Silver / escopo desconhecido),
        não podemos provar disjunção — retorna False (conservador).
        """
        if not scope_a or not scope_b:
            return False
        return not bool(set(scope_a) & set(scope_b))

    @staticmethod
    def _is_disjoint_term_pair(left: dict, right: dict) -> bool:
        """Verifica se dois termos no mesmo campo ARM são provadamente disjuntos.

        Tabela de decisão:
          = vs =   : disjunto se valores não se intersectam
          = vs ~   : disjunto se NENHUM literal em vals_left contém qualquer fragmento em vals_right
          ~ vs =   : disjunto se NENHUM literal em vals_right contém qualquer fragmento em vals_left
          = vs !=  : disjunto se TODOS os valores exatos estão listados no notEquals
          != vs =  : simétrico ao anterior
          qualquer outro : conservador — assume sobreposição
        """
        lop = left["operator"]
        rop = right["operator"]
        lvals = [v.casefold() for v in left["values"] if v]
        rvals = [v.casefold() for v in right["values"] if v]
        if not lvals or not rvals:
            return False

        if lop == "=" and rop == "=":
            # Exato vs Exato: disjunto se conjuntos não se intersectam
            return not bool(set(lvals) & set(rvals))

        if lop == "=" and rop == "~":
            # Exato vs Contém: disjunto se nenhum literal contém o fragmento
            # Ex: 'rg-azmig-terra-production' contains 'DTMN'? → não → disjunto
            return not any(frag in val for val in lvals for frag in rvals)

        if lop == "~" and rop == "=":
            # Contém vs Exato: simétrico
            return not any(frag in val for frag in lvals for val in rvals)

        if lop == "=" and rop == "!=":
            # Exato vs NotEquals: disjunto se todos os valores exatos estão no notEquals
            return all(v in set(rvals) for v in lvals)

        if lop == "!=" and rop == "=":
            # NotEquals vs Exato: simétrico
            return all(v in set(lvals) for v in rvals)

        # ~ vs ~, != vs !=, etc. → conservador, não pode provar disjunção
        return False

    @classmethod
    def _is_disjoint_by_arm_terms(cls, current: list[dict], previous: list[dict]) -> bool:
        """Retorna True se os termos ARM garantem que as regras NUNCA classificam
        o mesmo recurso Azure.

        Estratégia: para cada dimensão ARM compartilhada (mesmo scope + field),
        verifica se os valores são provadamente disjuntos. Se qualquer dimensão
        compartilhada for disjunta, as regras inteiras são disjuntas (AND semântico).

        Se não há dimensões compartilhadas, retorna False (conservador) — as regras
        operam em dimensões diferentes e podem coexistir no mesmo recurso.
        """
        curr_by_dim: dict[tuple, list[dict]] = {}
        for t in current:
            curr_by_dim.setdefault((t["scope"], t["field"]), []).append(t)

        prev_by_dim: dict[tuple, list[dict]] = {}
        for t in previous:
            prev_by_dim.setdefault((t["scope"], t["field"]), []).append(t)

        shared_dims = set(curr_by_dim.keys()) & set(prev_by_dim.keys())
        if not shared_dims:
            # Sem dimensão compartilhada → conservador
            return False

        for dim in shared_dims:
            for ct in curr_by_dim[dim]:
                for pt in prev_by_dim[dim]:
                    if cls._is_disjoint_term_pair(ct, pt):
                        logger.debug(
                            "Disjunção ARM provada na dimensão %s: %s%s vs %s%s",
                            dim, ct["operator"], ct["values"], pt["operator"], pt["values"],
                        )
                        return True
        return False

    @staticmethod
    def _resource_group_from_id(resource_id: str) -> str:
        match = re.search(r"/resourcegroups/([^/]+)", str(resource_id or ""), re.I)
        return match.group(1).casefold() if match else ""

    @classmethod
    def _observed_targets_disjoint(cls, current: dict, previous: dict) -> bool:
        """Use concrete Silver targets before falling back to conservative rules.

        An exact ResourceId in ``rg-network`` cannot overlap a Prisma policy
        whose concrete Silver RG is ``pccagentlessscanresourcegroup`` merely
        because both occur in the same subscription.
        """
        current_id = str(current.get("resource_id") or "").casefold()
        previous_id = str(previous.get("resource_id") or "").casefold()
        current_rgs = cls._observed_rg_names(current)
        previous_rgs = cls._observed_rg_names(previous)
        if current_id and previous_id:
            return current_id != previous_id
        if current_id and previous_rgs:
            rg = cls._resource_group_from_id(current_id)
            return bool(rg and rg not in previous_rgs)
        if previous_id and current_rgs:
            rg = cls._resource_group_from_id(previous_id)
            return bool(rg and rg not in current_rgs)
        return bool(current_rgs and previous_rgs and current_rgs.isdisjoint(previous_rgs))

    @staticmethod
    def _observed_rg_names(observed: dict) -> set[str]:
        raw = str((observed or {}).get("resource_group_name") or "").strip()
        if not raw:
            return set()
        return {part.strip().casefold() for part in raw.split("·") if part.strip()}

    @staticmethod
    def _target_matches_terms(target: dict, terms: list[dict]) -> bool:
        """Match an Azure Resource Graph row to the ARM dimensions of a rule."""
        resource_type = str(target.get("resource_type") or "")
        is_resource_group = resource_type.casefold() == AZURE_POLICY_RESOURCE_GROUP_TYPE.casefold()
        values_by_field = {
            "name": str(target.get("resource_group_name") or "") if is_resource_group else str(target.get("resource_name") or ""),
            "id": str(target.get("resource_id") or ""),
            "type": str(target.get("resource_type") or ""),
            "location": str(target.get("location") or ""),
        }
        for term in terms:
            if term.get("scope") == SCOPE_SUBSCRIPTION:
                continue
            if term.get("scope") == SCOPE_RG:
                actual = str(target.get("resource_group_name") or "")
            else:
                actual = values_by_field.get(str(term.get("field") or ""), "")
            if not actual:
                return False
            operator = term.get("operator")
            candidates = [str(value) for value in term.get("values") or []]
            folded = actual.casefold()
            if operator == "=" and folded not in {value.casefold() for value in candidates}:
                return False
            if operator == "!=" and folded in {value.casefold() for value in candidates}:
                return False
            if operator == "~" and not any(value.casefold() in folded for value in candidates):
                return False
        return True

    @classmethod
    def _rg_terms_match_name(cls, terms: list[dict], assigned_rg: str) -> bool:
        """True if the previous RG-name terms could match this assigned RG."""
        assigned = assigned_rg.casefold()
        rg_terms = [term for term in terms if term.get("scope") == SCOPE_RG]
        if not rg_terms:
            return True
        for term in rg_terms:
            operator = term.get("operator")
            values = [str(value).casefold() for value in term.get("values") or [] if value]
            if not values:
                continue
            if operator == "=" and assigned not in set(values):
                return False
            if operator in {"~", "contains", "like"} and not any(fragment in assigned for fragment in values):
                return False
            if operator == "!=" and assigned in set(values):
                return False
        return True

    @classmethod
    def _exclusion_outside_assigned_rg(cls, item: dict, entry: dict) -> bool:
        """Resource groups do not nest; an RG assignment never evaluates another RG.

        Prisma ``contains PCCAGENTLESSSCANRESOURCEGROUP`` belongs on the
        subscription-scoped policy, not inside ``azure-ti-test-fundacao-...``.
        """
        if item.get("assignment_scope_kind") != "resource_group":
            return False
        assigned = str(item.get("assignment_resource_group") or "").strip()
        if not assigned:
            return False
        previous_rg = cls._resource_group_from_id(str((entry.get("observed") or {}).get("resource_id") or ""))
        if previous_rg and previous_rg != assigned.casefold():
            return True
        return not cls._rg_terms_match_name(entry.get("terms") or [], assigned)

    def _previous_overlap_reason(self, item: dict, entry: dict) -> str | None:
        if entry["tags"] == item["tags"]:
            return "same_classification"
        if self._exclusion_outside_assigned_rg(item, entry):
            return "assignment_scope_disjoint"
        if self._observed_targets_disjoint(item["observed"], entry["observed"]):
            return "gold_targets_disjoint"
        if self._is_disjoint_by_arm_terms(item["terms"], entry["terms"]):
            return "arm_terms_disjoint"
        return None

    def _prior_rule_exclusions(
        self, item: dict, rules: list[ClassificationRule], departments: dict[int, Department],
        catalog: dict[str, list[str]], mappings: list[dict[str, str]], diagnostics: list[dict],
        selected_subscription_name: str = "",
    ) -> list[dict]:
        """Build selector-only exceptions from active earlier rules, not billing rows."""
        current_rule = item["rule"]
        exclusions: list[dict] = []
        current_sid = item["scope_subscriptions"][0].casefold()
        for rule in rules:
            if (rule.priority, rule.id) >= (current_rule.priority, current_rule.id):
                break
            department = departments.get(rule.department_id)
            if not department:
                continue
            if not self._rule_matches_selected_subscription(
                rule, catalog, current_sid, selected_subscription_name,
            ):
                continue
            terms = self._translate_conditions(rule, catalog, diagnostics, resolve_subscriptions=False)
            if not terms:
                continue
            terms = self._pin_subscription(terms, current_sid)
            explicit = self._explicit_scope_subscriptions(terms)
            if explicit and current_sid not in {sid.casefold() for sid in explicit}:
                continue
            earlier_tags = _collapse_tag_keys({
                mapping["tag_key"]: self._hierarchy(department, departments).get(mapping["focus_field"], "")
                for mapping in mappings
            })
            current_tags = {key.casefold(): value for key, value in _collapse_tag_keys(item["tags"]).items()}
            if earlier_tags and all(current_tags.get(key.casefold()) == value for key, value in earlier_tags.items()):
                continue
            selector = self._selector(terms, item["target"])
            if not selector:
                continue
            entry = {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "terms": terms,
                "selector": selector,
                "tags": earlier_tags,
                "observed": {},
                "summary": {"rule_id": rule.id, "rule_name": rule.name,
                            "department_id": rule.department_id,
                            "department_name": department.name, "observed": {}},
            }
            if self._previous_overlap_reason(item, entry) is None:
                exclusions.append(entry)
        return exclusions

    async def _azure_rule_targets(self, conn: SourceConnection, subscription_id: str) -> tuple[list[dict], str | None]:
        """Read current Azure Resource Groups/resources for explicit no-cost exports."""
        try:
            from app.services.azure_identity import arm_headers
            from app.services.azure_resource_graph_service import _post_query
            import httpx

            headers = await arm_headers(dict(conn.config_json or {}))
            query = """resourcecontainers
| where type =~ 'microsoft.resources/subscriptions/resourcegroups'
| project subscription_id = subscriptionId, resource_group_name = name, resource_name = name, resource_id = id, resource_type = type, location = tostring(location)
| union (resources | project subscription_id = subscriptionId, resource_group_name = resourceGroup, resource_name = name, resource_id = id, resource_type = type, location = tostring(location))"""
            rows: list[dict] = []
            token = None
            async with httpx.AsyncClient() as client:
                while True:
                    page, token, _ = await _post_query(client, headers, query, [subscription_id], token)
                    rows.extend(page)
                    if not token:
                        break
            return rows, None
        except Exception as exc:
            return [], f"Não foi possível ler os alvos atuais do Azure: {str(exc)[:180]}"

    async def generate_tenant_policy(self, connection_id: int, department_id: int | None = None,
                                     subscription_id: str | None = None) -> dict | None:
        requested_subscription_id = subscription_id
        conn = await self.db.get(SourceConnection, connection_id)
        if not conn or resolve_connection_scope(conn.cloud_scope, conn.cloud_name) != "azure":
            return None
        departments = await self._load_departments()
        rules = await self._load_classification_rules(conn)
        # A subscription selection means a complete policy map, including
        # projects with higher-priority exceptions such as Prisma.
        selected_department_ids = None if subscription_id else self._selected_department_ids(departments, department_id)
        mappings = await self._tag_mappings()
        home_tenants = await self._subscription_home_tenants(conn)
        billing_tenant = str((conn.config_json or {}).get("tenant_id") or "").strip()
        # When a subscription is selected, current ARM inventory is the source
        # of target existence. Do not wait on the latest billing period in
        # Silver: resources without recent cost lines must still be included.
        silver_rows, silver_error = ([], None)
        if not subscription_id:
            silver_rows, silver_error = await self._silver_evidence(conn, mappings)
        diagnostics: list[dict] = []
        if silver_error:
            diagnostics.append({"status": "warning", "reason": silver_error})

        evidence_by_rule: dict[int, list[dict]] = defaultdict(list)
        catalog: dict[str, list[str]] = defaultdict(list)
        for row in silver_rows:
            try:
                evidence_by_rule[int(row.get("rule_id"))].append(row)
            except (TypeError, ValueError):
                continue
            gold_subscription_id = self._evidence_subscription_id(row)
            subscription_name = str(row.get("subscription_name") or "").strip().casefold()
            if gold_subscription_id and subscription_name:
                catalog[subscription_name].append(gold_subscription_id)
        catalog = {name: sorted(set(ids)) for name, ids in catalog.items()}

        azure_target_rows: list[dict] = []
        azure_catalog_ids: set[str] = set()
        selected_subscription_name = ""
        if subscription_id:
            azure_catalog, discovered_ids, catalog_error = await self._subscription_catalog(conn)
            azure_catalog_ids = {sid.casefold() for sid in discovered_ids}
            for name, ids in azure_catalog.items():
                catalog.setdefault(name, []).extend(ids)
            catalog = {name: sorted(set(ids)) for name, ids in catalog.items()}
            selected_subscription_name = next(
                (name for name, ids in azure_catalog.items()
                 if subscription_id.casefold() in {sid.casefold() for sid in ids}),
                "",
            )
            if catalog_error:
                diagnostics.append({"status": "blocked", "reason": catalog_error, "subscription_id": subscription_id})
                raise RuntimeError(catalog_error)
            if subscription_id.casefold() not in azure_catalog_ids:
                diagnostics.append({"status": "blocked", "reason": "Subscription não encontrada no catálogo acessível da conexão",
                                    "subscription_id": subscription_id})
                return None
            try:
                azure_target_rows, inventory_error = await self._azure_rule_targets(conn, subscription_id)
            except Exception as exc:
                inventory_error = str(exc)
            if inventory_error:
                diagnostics.append({"status": "blocked", "reason": inventory_error, "subscription_id": subscription_id})
                raise RuntimeError(inventory_error)

        compiled: list[dict] = []
        for rule in rules:
            if subscription_id and not self._rule_matches_selected_subscription(
                rule, catalog, subscription_id, selected_subscription_name,
            ):
                continue
            terms = self._translate_conditions(rule, catalog, diagnostics, resolve_subscriptions=not bool(subscription_id))
            if terms and subscription_id:
                terms = self._pin_subscription(terms, subscription_id)
            department = departments.get(rule.department_id)
            if terms and department:
                selected = selected_department_ids is None or rule.department_id in selected_department_ids
                rule_evidence = evidence_by_rule.get(rule.id, [])
                if subscription_id:
                    rule_evidence = [row for row in rule_evidence
                                     if self._evidence_subscription_id(row).casefold() == subscription_id.casefold()]
                    if not rule_evidence and not any(d.get("status") == "blocked" and d.get("subscription_id") == subscription_id
                                                     for d in diagnostics):
                        scope = next((term["scope"] for term in terms if term["scope"] in {SCOPE_RESOURCE, SCOPE_RG}), None)
                        if scope == SCOPE_RG:
                            rule_evidence = [{"subscription_id": subscription_id,
                                              "resource_group_name": str(row.get("resource_group_name") or ""),
                                              "evidence_count": 0}
                                             for row in azure_target_rows
                                             if str(row.get("subscription_id") or "").casefold() == subscription_id.casefold()
                                             and str(row.get("resource_type") or "").casefold() == AZURE_POLICY_RESOURCE_GROUP_TYPE.casefold()
                                             and self._target_matches_terms(row, terms)]
                        elif scope == SCOPE_RESOURCE:
                            rule_evidence = [{"subscription_id": subscription_id,
                                              "resource_id": row.get("resource_id"),
                                              "resource_group_name": row.get("resource_group_name"),
                                              "evidence_count": 0}
                                             for row in azure_target_rows
                                             if str(row.get("subscription_id") or "").casefold() == subscription_id.casefold()
                                             and self._target_matches_terms(row, terms)]
                        elif any(term["scope"] == SCOPE_SUBSCRIPTION for term in terms) and azure_target_rows:
                            rule_evidence = [{"subscription_id": subscription_id, "evidence_count": 0}]
                compiled.extend(self._concrete_items(
                    rule, terms, rule_evidence, self._hierarchy(department, departments), mappings, selected, diagnostics,
                ))

        layers = {"resource_groups": [], "resources": []}
        # Policies are decision regions per subscription.  A resource exception
        # in terra-connectivity must not leak into another Terra subscription.
        previous: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for item in compiled:
            rule: ClassificationRule = item["rule"]
            omit_rg_scope = item.get("assignment_scope_kind") == "resource_group"
            for target in item["targets"]:
                layer = "resource_groups" if target == "resource_group" else "resources"
                selector = self._selector(item["terms"], target, omit_rg_scope=omit_rg_scope)
                if not selector:
                    continue
                item_subscription_id = item["scope_subscriptions"][0]
                if subscription_id and item_subscription_id.casefold() != subscription_id.casefold():
                    continue
                bucket = previous[(target, item_subscription_id)]
                overlapping = []
                pruned = []
                for entry in bucket:
                    reason = self._previous_overlap_reason(item, entry)
                    if reason:
                        pruned.append({**entry["summary"], "prune_reason": reason})
                    else:
                        overlapping.append(entry)
                exclusion_item = {**item, "target": target}
                diagnostics_before_exclusions = len(diagnostics)
                rule_exclusions = self._prior_rule_exclusions(
                    exclusion_item, rules, departments, catalog, mappings, diagnostics,
                    selected_subscription_name,
                )
                unresolved_precedence = [entry for entry in diagnostics[diagnostics_before_exclusions:]
                                         if entry.get("status") == "blocked"]
                if unresolved_precedence:
                    diagnostics.append({
                        "rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                        "reason": "Policy não exportada: não foi possível resolver com segurança uma restrição de precedência",
                        "blocked_by_rule_ids": sorted({entry.get("rule_id") for entry in unresolved_precedence
                                                        if entry.get("rule_id") is not None}),
                    })
                    continue
                known_ids = {entry["rule_id"] for entry in overlapping}
                rule_exclusions = [entry for entry in rule_exclusions if entry["rule_id"] not in known_ids]
                overlapping.extend(rule_exclusions)
                effective = self._effective(selector, [entry["selector"] for entry in overlapping])
                output = {
                    "rule_id": rule.id, "rule_name": rule.name, "priority": rule.priority,
                    "department_id": rule.department_id,
                    "department_name": departments.get(rule.department_id).name if departments.get(rule.department_id) else "",
                    "layer": layer,
                    "tags": item["tags"],
                    "condition": effective, "scope_subscriptions": [item_subscription_id],
                    "base_selector": selector,
                    "evidence_count": item["evidence_count"], "observed": item["observed"],
                    "assignment_scope_kind": item.get("assignment_scope_kind", "subscription"),
                    "assignment_resource_group": item.get("assignment_resource_group"),
                    "selected": item["selected"],
                    "excludes_rule_ids": [entry["rule_id"] for entry in overlapping],
                    "exclusions": [entry["summary"] for entry in overlapping],
                    "pruned_exclusions": pruned,
                    "scope_tenant_id": home_tenants.get(item_subscription_id.casefold()) or None,
                }
                bucket.append({
                    "selector": selector, "terms": item["terms"],
                    "rule_id": rule.id, "rule_name": rule.name,
                    "department_id": rule.department_id,
                    "department_name": departments.get(rule.department_id).name if departments.get(rule.department_id) else "",
                    "tags": item["tags"],
                    "observed": item["observed"],
                    "summary": {"rule_id": rule.id, "rule_name": rule.name, "department_id": rule.department_id,
                                "department_name": departments.get(rule.department_id).name if departments.get(rule.department_id) else "",
                                "observed": item["observed"]},
                })
                condition_bytes = len(json.dumps(effective, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if condition_bytes > MAX_CONDITION_BYTES:
                    diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                        "reason": f"Condição efetiva excede {MAX_CONDITION_BYTES} bytes; reduza exceções sobrepostas",
                                        "condition_bytes": condition_bytes})
                    continue
                layers[layer].append(output)

        # The loop sees all rules to calculate the selected project's exact
        # exclusions, but exports only policies owned by that selected project.
        # A Prisma inclusion policy is exported when Prisma itself is selected.
        if department_id is not None:
            for layer, values in layers.items():
                layers[layer] = [item for item in values if item["selected"]]

        selected = departments.get(department_id) if department_id else None
        emitted_scopes = sorted({subscription for values in layers.values() for item in values
                                 for subscription in item["scope_subscriptions"]})
        if requested_subscription_id and requested_subscription_id.casefold() not in {
            value.casefold() for value in emitted_scopes
        }:
            diagnostics.append({
                "status": "warning", "subscription_id": requested_subscription_id,
                "reason": "Subscription acessível, mas nenhuma regra ARM possui alvo atual elegível para gerar policy.",
            })
        return {"tenant_id": billing_tenant, "connection_code": conn.code,
                "connection_id": conn.id,
                "connection_name": conn.name, "rule_count": len(rules), "department_id": department_id,
                "department_name": selected.name if selected else None,
                "selected_subscription_id": requested_subscription_id,
                "deployment_subscriptions": emitted_scopes,
                "subscription_tenants": {
                    subscription: tenant
                    for subscription in emitted_scopes
                    if (tenant := home_tenants.get(subscription.casefold()))
                },
                "tag_mappings": mappings,
                "subscription_catalog": [{"name": name, "ids": ids} for name, ids in sorted(catalog.items())],
                "subscriptions": [
                    {"subscription_id": subscription, "policies": [item for values in layers.values() for item in values
                                                                         if subscription in item["scope_subscriptions"]]}
                    for subscription in emitted_scopes
                ],
                "layers": layers,
                "coverage": {"eligible_rules": len(compiled),
                             "emitted_policies": sum(len(values) for values in layers.values()),
                             "diagnostics": diagnostics}}


def slugify(value: str) -> str:
    out = "".join(char if char.isalnum() else "-" for char in str(value).lower()).strip("-")
    return out or "policy"
