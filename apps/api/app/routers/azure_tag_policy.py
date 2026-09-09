"""Azure Tag Policy export and safe Azure CLI deployment script."""
from __future__ import annotations

import json
import re
import shlex
from hashlib import sha256

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.azure_tag_mapping import AzureTagMapping
from app.models.destination import DestinationColumn, DestinationTable
from app.services.azure_tag_policy_service import (
    AzureTagPolicyService, TAG_CONTRIBUTOR_ROLE, slugify, _collapse_tag_keys,
)

router = APIRouter(prefix="/azure-tag-policy", tags=["azure-tag-policy"])
FOCUS_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TagMappingBody(BaseModel):
    tag_key: str = Field(min_length=1, max_length=512)
    focus_field: str = Field(min_length=1, max_length=200)
    active: bool = True


def _mapping_dict(item: AzureTagMapping) -> dict:
    return {"id": item.id, "tag_key": item.tag_key, "focus_field": item.focus_field, "active": item.active}


async def _focus_fields(db: AsyncSession) -> list[str]:
    result = await db.execute(select(DestinationColumn.column_name).join(
        DestinationTable, DestinationColumn.destination_table_id == DestinationTable.id,
    ).where((DestinationTable.name.ilike("%FOCUS%")) | (DestinationTable.physical_name == "dest_modelo_focus"))
                         .order_by(DestinationColumn.position, DestinationColumn.column_name))
    return [row[0] for row in result.all()]


async def _validated_mapping(body: TagMappingBody, db: AsyncSession) -> tuple[str, str]:
    tag_key, focus_field = body.tag_key.strip(), body.focus_field.strip()
    if not tag_key or not focus_field:
        raise HTTPException(status_code=422, detail="Chave da tag e campo FOCUS são obrigatórios")
    if not FOCUS_FIELD_RE.fullmatch(focus_field):
        raise HTTPException(status_code=422, detail="O campo FOCUS deve ser um nome de coluna válido, por exemplo centro_custo")
    focus_fields = await _focus_fields(db)
    if focus_field not in focus_fields:
        raise HTTPException(status_code=422, detail="Selecione um campo existente no MODELO FOCUS")
    return tag_key, focus_field


@router.get("/tag-mappings")
async def list_tag_mappings(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(AzureTagMapping).order_by(AzureTagMapping.id))
    return {"mappings": [_mapping_dict(row) for row in rows.scalars().all()],
            "focus_fields": await _focus_fields(db)}


@router.post("/tag-mappings", status_code=201)
async def create_tag_mapping(body: TagMappingBody, db: AsyncSession = Depends(get_db)):
    tag_key, focus_field = await _validated_mapping(body, db)
    item = AzureTagMapping(tag_key=tag_key, focus_field=focus_field, active=body.active)
    db.add(item)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Já existe uma tag com esta chave")
    await db.refresh(item)
    return _mapping_dict(item)


@router.put("/tag-mappings/{mapping_id}")
async def update_tag_mapping(mapping_id: int, body: TagMappingBody, db: AsyncSession = Depends(get_db)):
    item = await db.get(AzureTagMapping, mapping_id)
    if not item:
        raise HTTPException(status_code=404, detail="Mapeamento de tag não encontrado")
    tag_key, focus_field = await _validated_mapping(body, db)
    item.tag_key, item.focus_field, item.active = tag_key, focus_field, body.active
    await db.commit(); await db.refresh(item)
    return _mapping_dict(item)


def _exclusion_summary(exclusions: list[dict], limit: int = 8) -> str:
    labels = [f"#{entry['rule_id']} {entry['rule_name']} → {entry['department_name']}" for entry in exclusions]
    if len(labels) > limit:
        labels = labels[:limit] + [f"e mais {len(exclusions) - limit}"]
    return "; ".join(labels) or "Nenhuma"


@router.get("/tenants")
async def list_tenants(db: AsyncSession = Depends(get_db)):
    return await AzureTagPolicyService(db).list_azure_tenants()


@router.get("/export")
async def export_policy(
    connection_id: int | None = Query(None, description="Optional: a single Azure connection/tenant"),
    department_id: int | None = Query(None, description="Optional: compile only one organizational project"),
    plan_only: bool = Query(False, description="Generate the script with PLAN_ONLY=true by default"),
    fmt: str = Query("json", description="Output format: json | script | both"),
    db: AsyncSession = Depends(get_db),
):
    if fmt not in {"json", "script", "both"}:
        raise HTTPException(status_code=422, detail="fmt deve ser json, script ou both")
    service = AzureTagPolicyService(db)
    tenants = await service.list_azure_tenants()
    if not tenants:
        raise HTTPException(status_code=404, detail="Nenhum tenant Azure configurado")
    if connection_id is not None:
        tenants = [tenant for tenant in tenants if tenant["connection_id"] == connection_id]
        if not tenants:
            raise HTTPException(status_code=404, detail="Tenant Azure não encontrado")
    policies = [doc for tenant in tenants
                if (doc := await service.generate_tenant_policy(tenant["connection_id"], department_id))]
    script = build_script(policies, plan_only=plan_only) if fmt in {"script", "both"} else ""
    if fmt == "script":
        return PlainTextResponse(script, media_type="text/x-shellscript")
    return JSONResponse({"tenants": policies, "deployment_script": script})


def _definition(
    display_name: str, description: str, condition: dict, tags: dict[str, str], mode: str,
    source_rule: str, exclusions: list[dict],
) -> dict:
    operations = [{"operation": "addOrReplace", "field": f"tags['{key}']", "value": value}
                  for key, value in _collapse_tag_keys(tags).items() if value]
    definition = {
        "mode": mode,
        "displayName": display_name,
        "description": description,
        "metadata": {
            "category": "OpenFinops", "version": "2.0",
            "openfinopsSourceRule": source_rule,
            "openfinopsExclusions": _exclusion_summary(exclusions),
        },
        "policyRule": {"if": condition, "then": {"effect": "modify", "details": {
            "conflictEffect": "audit", "roleDefinitionIds": [TAG_CONTRIBUTOR_ROLE], "operations": operations,
        }}},
    }
    # The hash intentionally excludes metadata itself, avoiding a circular and
    # unstable value while covering the effective Azure Policy definition.
    content = json.dumps({
        "mode": mode,
        "displayName": display_name,
        "description": description,
        "policyRule": definition["policyRule"],
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    definition["metadata"]["openfinopsContentHash"] = sha256(content.encode("utf-8")).hexdigest()
    return definition


def _assignment_scope(subscription_id: str, item: dict) -> str:
    if item.get("assignment_scope_kind") == "resource_group":
        resource_group = str(item.get("assignment_resource_group") or "").strip()
        if resource_group:
            return f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    return f"/subscriptions/{subscription_id}"


def build_script(policies: list[dict], plan_only: bool = False) -> str:
    """Build idempotent per-subscription assignments with their required identity."""
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by OpenFinops. Policies are mutually exclusive decision regions.",
        "set -euo pipefail",
        'LOCATION="${LOCATION:-eastus2}"',
        f'PLAN_ONLY="{"true" if plan_only else "false"}"',
        'WAIT_FOR_REMEDIATION="${WAIT_FOR_REMEDIATION:-false}"',
        'RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-5}"',
        'RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-5}"',
        'REMEDIATION_SEQ=0',
        'POLICIES_DIR="$(mktemp -d)"',
        "trap 'rm -rf \"$POLICIES_DIR\"' EXIT",
        "",
        'info() { echo "[INFO] $*"; }',
        'warn() { echo "[WARN] $*" >&2; }',
        'error() { echo "[ERROR] $*" >&2; }',
        'plan() { [[ "$PLAN_ONLY" == "true" ]]; }',
        'action() { echo "[$1] $2"; }',
        'run_mutation() { plan && { error "Mutação bloqueada em PLAN_ONLY: $*"; exit 1; }; "$@"; }',
        'require_az() { command -v az >/dev/null 2>&1 || { error "Azure CLI (az) não está instalado."; exit 1; }; az account show --only-show-errors >/dev/null || { error "Autentique-se antes com az login."; exit 1; }; }',
        'ensure_definition() {',
        '  local name="$1" mode="$2" rules="$3" metadata="$4" display="$5" description="$6" expected_hash="$7"',
        '  local category current_hash',
        '  if az policy definition show --name "$name" --only-show-errors >/dev/null 2>&1; then',
        '    category=$(az policy definition show --name "$name" --query "metadata.category" -o tsv --only-show-errors)',
        '    [[ "$category" == "OpenFinops" ]] || { action ABORT "Definition $name pertence a outra categoria ($category)."; exit 1; }',
        '    current_hash=$(az policy definition show --name "$name" --query "metadata.openfinopsContentHash" -o tsv --only-show-errors)',
        '    if [[ "$current_hash" == "$expected_hash" ]]; then action UNCHANGED "Definition $name"; return; fi',
        '    action UPDATE "Definition OpenFinops $name"',
        '    plan || run_mutation az policy definition update --name "$name" --mode "$mode" --rules "@$rules" --metadata "@$metadata" --display-name "$display" --description "$description" --only-show-errors',
        '  else',
        '    action CREATE "Definition OpenFinops $name"',
        '    plan || run_mutation az policy definition create --name "$name" --mode "$mode" --rules "@$rules" --metadata "@$metadata" --display-name "$display" --description "$description" --only-show-errors',
        '  fi',
        '}',
        'ensure_assignment() {',
        '  local name="$1" scope="$2" policy_name="$3" expected_id current_id identity_type',
        '  expected_id="$scope/providers/Microsoft.Authorization/policyDefinitions/$policy_name"',
        '  if ! plan; then expected_id=$(az policy definition show --name "$policy_name" --query id -o tsv --only-show-errors); fi',
        '  if az policy assignment show --name "$name" --scope "$scope" --only-show-errors >/dev/null 2>&1; then',
        '    current_id=$(az policy assignment show --name "$name" --scope "$scope" --query policyDefinitionId -o tsv --only-show-errors)',
        '    [[ "${current_id,,}" == "${expected_id,,}" ]] || { action ABORT "Assignment $name aponta para outra definition."; exit 1; }',
        '    identity_type=$(az policy assignment show --name "$name" --scope "$scope" --query "identity.type" -o tsv --only-show-errors)',
        '    if [[ "$identity_type" == *SystemAssigned* ]]; then action UNCHANGED "Assignment $name (managed identity preservada)"; else action UPDATE "Assignment $name (associar managed identity)"; plan || run_mutation az policy assignment identity assign --name "$name" --scope "$scope" --mi-system-assigned --only-show-errors; fi',
        '  else',
        '    action CREATE "Assignment $name com managed identity"',
        '    plan || run_mutation az policy assignment create --name "$name" --scope "$scope" --policy "$policy_name" --mi-system-assigned --location "$LOCATION" --only-show-errors',
        '  fi',
        '}',
        'resolve_assignment_principal() {',
        '  local name="$1" scope="$2" attempt=1 principal_id=""',
        '  while (( attempt <= RETRY_ATTEMPTS )); do',
        '    principal_id=$(az policy assignment show --name "$name" --scope "$scope" --query identity.principalId -o tsv --only-show-errors)',
        '    [[ -n "$principal_id" && "$principal_id" != "null" ]] && { echo "$principal_id"; return 0; }',
        '    plan && return 1',
        '    sleep "$RETRY_DELAY_SECONDS"; ((attempt++))',
        '  done',
        '  return 1',
        '}',
        'ensure_tag_contributor() {',
        '  local principal_id="$1" scope="$2" attempt=1',
        '  local rbac_id',
        '  [[ -n "$principal_id" && "$principal_id" != "null" ]] || { plan && { action CREATE "RBAC Tag Contributor em $scope (managed identity pendente)"; return; }; error "Managed identity não foi criada para o assignment."; exit 1; }',
        '  rbac_id=$(az role assignment list --assignee-object-id "$principal_id" --role "Tag Contributor" --scope "$scope" --query "[0].id" -o tsv --only-show-errors)',
        '  if [[ -n "$rbac_id" && "$rbac_id" != "null" ]]; then action UNCHANGED "RBAC Tag Contributor em $scope"; return; fi',
        '  action CREATE "RBAC Tag Contributor em $scope"',
        '  plan && return',
        '  while (( attempt <= RETRY_ATTEMPTS )); do',
        '    if az role assignment create --assignee-object-id "$principal_id" --assignee-principal-type ServicePrincipal --role "Tag Contributor" --scope "$scope" --only-show-errors; then return; fi',
        '    warn "RBAC ainda não disponível (tentativa $attempt/$RETRY_ATTEMPTS); aguardando propagação da identidade."; sleep "$RETRY_DELAY_SECONDS"; ((attempt++))',
        '  done',
        '  error "Não foi possível conceder Tag Contributor após $RETRY_ATTEMPTS tentativa(s)."; exit 1',
        '}',
        'ensure_remediation() {',
        '  local assignment="$1" scope="$2" name state subscription resource_group',
        '  subscription="${scope#/subscriptions/}"',
        '  subscription="${subscription%%/*}"',
        '  resource_group=""',
        '  if [[ "$scope" == */resourceGroups/* ]]; then',
        '    resource_group="${scope#*/resourceGroups/}"',
        '    resource_group="${resource_group%%/*}"',
        '  fi',
        '  REMEDIATION_SEQ=$((REMEDIATION_SEQ + 1))',
        '  name="of-rem-$(date -u +%Y%m%d%H%M%S)-${REMEDIATION_SEQ}"',
        '  action CREATE "Remediation $name (tarefa pontual; tasks anteriores são preservadas)"',
        '  if [[ -n "$resource_group" ]]; then',
        '    plan || run_mutation az policy remediation create --name "$name" --resource-group "$resource_group" --policy-assignment "$assignment" --resource-discovery-mode ReEvaluateCompliance --only-show-errors',
        '  else',
        '    plan || run_mutation az policy remediation create --name "$name" --subscription "$subscription" --policy-assignment "$assignment" --resource-discovery-mode ReEvaluateCompliance --only-show-errors',
        '  fi',
        '  if plan; then action CREATE "Trigger scan para $scope (não executado em PLAN)"; else run_mutation az policy state trigger-scan --no-wait --only-show-errors; fi',
        '  if [[ "$WAIT_FOR_REMEDIATION" == "true" && "$PLAN_ONLY" != "true" ]]; then',
        '    local deadline=$((SECONDS + 300))',
        '    while (( SECONDS < deadline )); do',
        '      if [[ -n "$resource_group" ]]; then state=$(az policy remediation show --name "$name" --resource-group "$resource_group" --query provisioningState -o tsv --only-show-errors);',
        '      else state=$(az policy remediation show --name "$name" --subscription "$subscription" --query provisioningState -o tsv --only-show-errors); fi',
        '      [[ "$state" != "Running" && "$state" != "Accepted" ]] && break; sleep 10;',
        '    done',
        '    info "Status final da remediation $name: $state";',
        '  fi',
        '  if [[ -n "$resource_group" ]]; then info "Acompanhe: az policy remediation show --name $name --resource-group $resource_group --query provisioningState -o tsv";',
        '  else info "Acompanhe: az policy remediation show --name $name --subscription $subscription --query provisioningState -o tsv"; fi',
        '}',
        'require_az',
        "",
    ]
    for tenant in policies:
        code = slugify(tenant.get("connection_code", "azure"))
        test_suffix = f"-test-{slugify(tenant['department_name'])}" if tenant.get("department_name") else ""
        lines += [
            f"# Billing connection {tenant.get('connection_name')} ({tenant.get('connection_code')})",
            "# Assignments are grouped by RESOURCE tenant (subscription home), not billing tenant.",
        ]
        subscription_tenants = {str(key).casefold(): str(value).strip()
                                for key, value in (tenant.get("subscription_tenants") or {}).items() if str(value).strip()}
        items = [("rg", "All", item) for item in tenant.get("layers", {}).get("resource_groups", [])]
        items += [("resource", "Indexed", item) for item in tenant.get("layers", {}).get("resources", [])]

        def _home_for(item: dict, subscription_id: str) -> str:
            return (str(item.get("scope_tenant_id") or "").strip()
                    or subscription_tenants.get(subscription_id.casefold(), ""))

        jobs: dict[str, list[dict]] = {}
        for index, (kind, mode, item) in enumerate(items, 1):
            subscriptions = item.get("scope_subscriptions") or []
            name = f"openfinops-{kind}-{code}{test_suffix}-{index}"
            if not subscriptions:
                lines += [f"# SKIPPED {name}: no observed Dimon subscription or explicit subscription GUID for this rule.", ""]
                continue
            exclusions = item.get("exclusions") or []
            assignment_scope = _assignment_scope(subscriptions[0], item) if subscriptions else ""
            if item.get("assignment_scope_kind") == "resource_group" and item.get("assignment_resource_group"):
                assignment_label = f"resource group {item['assignment_resource_group']}"
                effective_target = item["assignment_resource_group"]
            else:
                assignment_label = "subscription"
                effective_target = (item.get("observed", {}).get("resource_id")
                                    or item.get("observed", {}).get("resource_group_name")
                                    or "subscription completa")
            lines += [
                f"# Regra de origem: #{item.get('rule_id')} {item.get('rule_name')}",
                f"# Assignment: {assignment_label}; alvo efetivo: {effective_target}",
                "# Exceções de maior prioridade excluídas desta policy:",
            ]
            if exclusions:
                lines += [f"# - #{entry['rule_id']} {entry['rule_name']} → projeto {entry['department_name']}"
                          for entry in exclusions]
            else:
                lines += ["# - Nenhuma"]
            policy = _definition(
                f"OpenFinops - {item.get('rule_name', 'Tag policy')}",
                "Regra de origem #{rule_id} ({rule_name}). Exceções de maior prioridade: {exceptions}.".format(
                    rule_id=item.get("rule_id"), rule_name=item.get("rule_name"),
                    exceptions=_exclusion_summary(exclusions),
                ),
                item["condition"], item.get("tags", {}), mode,
                f"#{item.get('rule_id')} {item.get('rule_name')}", exclusions,
            )
            rules_filename = f"{name}-rules.json"
            metadata_filename = f"{name}-metadata.json"
            lines += [
                f"cat > \"$POLICIES_DIR/{rules_filename}\" <<'POLICYEOF'",
                json.dumps(policy["policyRule"], indent=2, ensure_ascii=False), "POLICYEOF",
                f"cat > \"$POLICIES_DIR/{metadata_filename}\" <<'METADATAEOF'",
                json.dumps(policy["metadata"], indent=2, ensure_ascii=False), "METADATAEOF",
            ]
            for subscription_id in subscriptions:
                home = _home_for(item, subscription_id) or "_unmapped"
                scope = _assignment_scope(subscription_id, item)
                assignment_suffix = slugify(item.get("assignment_resource_group") or "")[:16]
                jobs.setdefault(home, []).append({
                    "subscription_id": subscription_id,
                    "scope": scope,
                    "assignment_suffix": assignment_suffix,
                    "name": name,
                    "mode": mode,
                    "policy": policy,
                    "rules_filename": rules_filename,
                    "metadata_filename": metadata_filename,
                })

        q = shlex.quote
        for home_tenant in sorted(jobs, key=lambda value: (value == "_unmapped", value)):
            block = jobs[home_tenant]
            if home_tenant == "_unmapped":
                lines += [
                    "",
                    "# === Tenant não mapeado (az account set na subscription; não usar tenant de billing) ===",
                    'info "Bloco sem tenant home cadastrado: o az account set escolhe o tenant da subscription."',
                ]
            else:
                lines += [
                    "",
                    f"# === Tenant de recursos {home_tenant} ===",
                    "# O Azure CLI alterna somente a subscription; não valida nem solicita login por tenant.",
                ]
            for job in block:
                subscription_id = job["subscription_id"]
                name = job["name"]
                policy = job["policy"]
                scope = job["scope"]
                suffix = job["assignment_suffix"]
                assignment = f"{name}-{suffix}-{slugify(subscription_id)[:8]}" if suffix else f"{name}-{slugify(subscription_id)[:8]}"
                lines += [
                    f"EXPECTED_SUBSCRIPTION={q(subscription_id)}",
                    'CURRENT_SUBSCRIPTION=$(az account show --query id -o tsv --only-show-errors)',
                    'if [[ "$CURRENT_SUBSCRIPTION" != "$EXPECTED_SUBSCRIPTION" ]]; then',
                    '  if plan; then action ABORT "Subscription ativa $CURRENT_SUBSCRIPTION; esperada $EXPECTED_SUBSCRIPTION"; exit 1; fi',
                    '  az account set --subscription "$EXPECTED_SUBSCRIPTION" --only-show-errors',
                    'fi',
                    'CURRENT_SUBSCRIPTION=$(az account show --query id -o tsv --only-show-errors)',
                    '[[ "$CURRENT_SUBSCRIPTION" == "$EXPECTED_SUBSCRIPTION" ]] || { error "Subscription ativa inesperada: $CURRENT_SUBSCRIPTION (esperada: $EXPECTED_SUBSCRIPTION)"; exit 1; }',
                ]
                lines += [
                    f"ensure_definition {q(name)} {q(job['mode'])} \"$POLICIES_DIR/{job['rules_filename']}\" \"$POLICIES_DIR/{job['metadata_filename']}\" {q(policy['displayName'])} {q(policy['description'])} {q(policy['metadata']['openfinopsContentHash'])}",
                    f"ensure_assignment {q(assignment)} {q(scope)} {q(name)}",
                    f"if az policy assignment show --name {q(assignment)} --scope {q(scope)} --only-show-errors >/dev/null 2>&1 || plan; then",
                    '  PRINCIPAL_ID=""',
                    f"  if az policy assignment show --name {q(assignment)} --scope {q(scope)} --only-show-errors >/dev/null 2>&1; then",
                    f"    if PRINCIPAL_ID=$(resolve_assignment_principal {q(assignment)} {q(scope)}); then :; else PRINCIPAL_ID=\"\"; fi",
                    '  fi',
                    f"  ensure_tag_contributor \"$PRINCIPAL_ID\" {q(scope)}",
                    f"  ensure_remediation {q(assignment)} {q(scope)}",
                    'else',
                    f"  error \"Assignment {assignment} não encontrado após ensure_assignment.\"; exit 1",
                    'fi',
                    "",
                ]
    lines.append('echo "[INFO] OpenFinops tag policy deployment completed."')
    return "\n".join(lines) + "\n"
