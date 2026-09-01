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
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.destination import DestinationTable, SourceDestinationMapping
from app.models.rules import ClassificationRule, Department
from app.models.source_connection import SourceConnection
from app.services.classification_engine import normalize_rule_conditions
from app.services.cloud_scope import (
    resolve_connection_scope, rule_matches_cloud, rule_matches_connection,
    rule_matches_destination,
)

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
TAG_KEYS = [
    "departamento_nivel_1", "departamento_nivel_2", "departamento_nivel_3",
    "departamento_nivel_4", "departamento_nivel_5", "centro_custo", "ambiente",
]
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
UUID_ANY_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
# Keep generated definitions comfortably below Azure Policy payload limits. A
# blocked rule is safer than emitting a definition Azure will reject or truncate.
MAX_CONDITION_BYTES = 64_000


def _norm(value: str) -> str:
    return str(value or "").lower().replace("_", "").replace("-", "").strip()


def _wildcard(value: str) -> str:
    value = value.strip()
    return value if "*" in value else f"*{value}*"


def _condition(field: str, operator: str, values: list[str]) -> dict | None:
    """Build a Policy condition with OR within a classification condition."""
    if not values:
        return None
    if operator == "=":
        return {"field": field, "equals": values[0]} if len(values) == 1 else {"field": field, "in": values}
    if operator == "!=":
        return {"field": field, "notEquals": values[0]} if len(values) == 1 else {"field": field, "notIn": values}
    if operator == "~":
        # Azure Policy like permits one wildcard. contains is safer for IDs.
        op = "contains" if field == "id" else "like"
        formatted = values if op == "contains" else [_wildcard(value) for value in values]
        return {"field": field, op: formatted[0]} if len(formatted) == 1 else {
            "anyOf": [{"field": field, op: value} for value in formatted]
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

    async def _load_departments(self) -> dict[int, Department]:
        rows = await self.db.execute(select(Department).where(Department.active == True))
        return {row.id: row for row in rows.scalars().all()}

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
        tags = {f"departamento_nivel_{i}": item.name for i, item in enumerate(chain[:5], 1) if item.name}
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
            from app.services.azure_resource_graph_service import discover_scope
            discovered = await discover_scope(dict(conn.config_json or {}))
            for item in discovered:
                sid = str(item.get("subscription_id") or "").strip()
                name = str(item.get("subscription_name") or "").strip()
                if sid:
                    ids.append(sid)
                    catalog[name.casefold()].append(sid)
        except Exception as exc:  # ResourceId/RG exports remain useful.
            message = f"Não foi possível consultar Azure Resource Graph: {str(exc)[:180]}"
            logger.warning("Azure tag policy subscription discovery for %s: %s", conn.code, message)
            return catalog, sorted(set(ids)), message
        return catalog, sorted(set(ids)), None

    async def _observed_project_scopes(self, conn: SourceConnection, catalog: dict[str, list[str]]) -> tuple[dict[str, list[str]], str | None]:
        """Resolve the subscriptions actually classified in the Dimon cost view.

        Azure credentials can see subscriptions with no OpenFinops business rule.
        They are never a safe default policy scope, so this query is the scope
        source for RG/name/resource rules that do not contain an explicit GUID.
        """
        safe_code = (conn.code or "").replace("'", "''")
        try:
            from app.services.trino_service import execute as trino_execute
            rows = await trino_execute(f"""
                SELECT lower(trim(departamento)) AS department_name,
                       lower(trim(subaccountname)) AS subscription_name
                FROM iceberg.dimon.dest_dm_cost_visao_completa
                WHERE cloud_origem = '{safe_code}'
                  AND CAST(source_connection_id AS bigint) = {int(conn.id)}
                  AND departamento IS NOT NULL AND trim(departamento) <> ''
                  AND subaccountname IS NOT NULL AND trim(subaccountname) <> ''
                GROUP BY 1, 2
            """, timeout_s=60)
        except Exception as exc:
            message = f"Não foi possível consultar as subscriptions classificadas no Dimon: {str(exc)[:180]}"
            logger.warning("Azure tag policy Dimon scope for %s: %s", conn.code, message)
            return {}, message
        scopes: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            department_name = str(row.get("department_name") or "").strip().casefold()
            subscription_name = str(row.get("subscription_name") or "").strip().casefold()
            if department_name:
                scopes[department_name].update(catalog.get(subscription_name, []))
        return {name: sorted(ids) for name, ids in scopes.items()}, None

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

    def _translate_conditions(self, rule: ClassificationRule, catalog: dict[str, list[str]], diagnostics: list[dict]) -> list[dict] | None:
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
            if scope == SCOPE_SUBSCRIPTION:
                resolved: list[str] = []
                for value in values:
                    if UUID_RE.match(value):
                        resolved.append(value)
                    elif operator == "=":
                        matches = catalog.get(value.casefold(), [])
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

    def _selector(self, terms: list[dict], target: str) -> dict | None:
        resource_only = {SCOPE_RESOURCE, SCOPE_TYPE, SCOPE_SKU, SCOPE_LOCATION}
        if target == "resource_group" and any(term["scope"] in resource_only for term in terms):
            return None
        clauses: list[dict] = [{"field": "type", "equals": "Microsoft.Resources/resourceGroups"}
                               if target == "resource_group" else
                               {"field": "type", "notEquals": "Microsoft.Resources/resourceGroups"}]
        for term in terms:
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

        Se algum escopo estiver vazio (regra sem dados Dimon / escopo desconhecido),
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

    async def generate_tenant_policy(self, connection_id: int, department_id: int | None = None) -> dict | None:
        conn = await self.db.get(SourceConnection, connection_id)
        if not conn or resolve_connection_scope(conn.cloud_scope, conn.cloud_name) != "azure":
            return None
        departments = await self._load_departments()
        rules = await self._load_classification_rules(conn)
        catalog, deployment_subscriptions, catalog_error = await self._subscription_catalog(conn)
        observed_scopes, dimon_scope_error = await self._observed_project_scopes(conn, catalog)
        diagnostics: list[dict] = []
        if catalog_error:
            diagnostics.append({"status": "warning", "reason": catalog_error})
        if dimon_scope_error:
            diagnostics.append({"status": "warning", "reason": dimon_scope_error})
        compiled: list[dict] = []
        for rule in rules:
            terms = self._translate_conditions(rule, catalog, diagnostics)
            department = departments.get(rule.department_id)
            if terms and department:
                explicit_scopes = self._explicit_scope_subscriptions(terms)
                scopes = explicit_scopes or observed_scopes.get(department.name.casefold(), [])
                if not scopes:
                    diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                        "reason": "Sem subscription observada no Dimon ou GUID explícito na regra"})
                compiled.append({"rule": rule, "tags": self._hierarchy(department, departments), "terms": terms,
                                 "scope_subscriptions": scopes})

        layers = {"resource_groups": [], "resources": []}
        previous: dict[str, list[dict]] = {"resource_group": [], "resource": []}
        for item in compiled:
            rule: ClassificationRule = item["rule"]
            for target, layer in (("resource_group", "resource_groups"), ("resource", "resources")):
                selector = self._selector(item["terms"], target)
                # Uma regra sem subscription comprovada é somente diagnóstico.
                # Ela não pode virar exceção e abrir um buraco na policy
                # principal se sua própria policy não pode ser atribuída.
                if not selector or not item["scope_subscriptions"]:
                    continue

                # Calcular candidatos a exceção e registrar os descartados para diagnóstico.
                # Ordem de verificação (curto-circuito mais barato primeiro):
                #   1. Disjunção por scope de assinaturas (GUIDs) — O(1)
                #   2. Disjunção sintática avançada dos termos ARM — O(n²) mas conjunto pequeno
                overlapping: list[dict] = []
                pruned: list[dict] = []
                for entry in previous[target]:
                    if entry["tags"] == item["tags"]:
                        pruned.append({
                            "rule_id": entry["rule_id"],
                            "rule_name": entry["rule_name"],
                            "department_name": entry["department_name"],
                            "prune_reason": "same_classification",
                        })
                    elif self._scopes_disjoint(item["scope_subscriptions"], entry["scope_subscriptions"]):
                        pruned.append({
                            "rule_id": entry["rule_id"],
                            "rule_name": entry["rule_name"],
                            "department_name": entry["department_name"],
                            "prune_reason": "scope_disjoint",
                        })
                        logger.debug(
                            "Excluindo exceção dispensável (scope disjunto): regra #%s vs #%s",
                            rule.id, entry["rule_id"],
                        )
                    elif self._is_disjoint_by_arm_terms(item["terms"], entry["terms"]):
                        pruned.append({
                            "rule_id": entry["rule_id"],
                            "rule_name": entry["rule_name"],
                            "department_name": entry["department_name"],
                            "prune_reason": "arm_terms_disjoint",
                        })
                        logger.debug(
                            "Excluindo exceção dispensável (termos ARM disjuntos): regra #%s vs #%s",
                            rule.id, entry["rule_id"],
                        )
                    else:
                        overlapping.append(entry)

                effective = self._effective(selector, [entry["selector"] for entry in overlapping])
                previous[target].append({
                    "selector": selector, "terms": item["terms"],
                    "rule_id": rule.id, "rule_name": rule.name,
                    "department_id": rule.department_id,
                    "department_name": departments.get(rule.department_id).name if departments.get(rule.department_id) else "",
                    "scope_subscriptions": item["scope_subscriptions"],
                    "tags": item["tags"],
                })
                if department_id is not None and rule.department_id != department_id:
                    continue
                condition_bytes = len(json.dumps(effective, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if condition_bytes > MAX_CONDITION_BYTES:
                    diagnostics.append({"rule_id": rule.id, "rule_name": rule.name, "status": "blocked",
                                        "reason": f"Condição efetiva excede {MAX_CONDITION_BYTES} bytes; reduza exceções sobrepostas",
                                        "condition_bytes": condition_bytes})
                    continue
                if pruned:
                    logger.info(
                        "Regra #%s (%s): %d exceção(ões) dispensável(is) removida(s): %s",
                        rule.id, rule.name, len(pruned),
                        [(p["rule_id"], p["prune_reason"]) for p in pruned],
                    )
                layers[layer].append({
                    "rule_id": rule.id, "rule_name": rule.name,
                    "department_id": rule.department_id, "tags": item["tags"],
                    "condition": effective,
                    "scope_subscriptions": item["scope_subscriptions"],
                    "excludes_rule_ids": [entry["rule_id"] for entry in overlapping],
                    "exclusions": [
                        {
                            "rule_id": entry["rule_id"],
                            "rule_name": entry["rule_name"],
                            "department_id": entry["department_id"],
                            "department_name": entry["department_name"],
                        }
                        for entry in overlapping
                    ],
                    "pruned_exclusions": pruned,
                })

        selected = departments.get(department_id) if department_id else None
        emitted_scopes = sorted({subscription for values in layers.values() for item in values
                                 for subscription in item["scope_subscriptions"]})
        return {"tenant_id": (conn.config_json or {}).get("tenant_id"), "connection_code": conn.code,
                "connection_name": conn.name, "rule_count": len(rules), "department_id": department_id,
                "department_name": selected.name if selected else None,
                "deployment_subscriptions": emitted_scopes,
                "subscription_catalog": [{"name": name, "ids": ids} for name, ids in sorted(catalog.items())],
                "layers": layers,
                "coverage": {"eligible_rules": len(compiled),
                             "emitted_policies": sum(len(values) for values in layers.values()),
                             "diagnostics": diagnostics}}


def slugify(value: str) -> str:
    out = "".join(char if char.isalnum() else "-" for char in str(value).lower()).strip("-")
    return out or "policy"
