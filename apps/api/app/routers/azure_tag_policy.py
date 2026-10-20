"""Azure Tag Policy export and safe Azure CLI deployment script."""
from __future__ import annotations

import json
import re
import shlex
import base64
from hashlib import sha256
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.azure_connection_source import AzureConnectionSource
from app.models.etl_task import EtlTask
from app.models.source_connection import SourceConnection

from app.database import get_db
from app.models.azure_tag_mapping import AzureTagMapping
from app.models.destination import DestinationColumn, DestinationTable
from app.services.azure_tag_policy_service import (
    AzureTagPolicyService, TAG_CONTRIBUTOR_ROLE, slugify, _collapse_tag_keys,
)
from app.services.azure_tag_dashboard_service import AzureTagDashboardService
from app.services.azure_tag_timeline_service import AzureTagTimelineService

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


@router.get("/subscriptions")
async def list_subscriptions(connection_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    try:
        return {"subscriptions": await AzureTagPolicyService(db).list_subscriptions(connection_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/executive-dashboard")
async def executive_tag_dashboard(connection_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    try:
        return await AzureTagDashboardService(db).executive_summary(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/timeline")
async def azure_tag_policy_timeline(
    connection_id: int = Query(...),
    since: date = Query(default=date(2026, 10, 12)),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await AzureTagTimelineService(db).timeline(connection_id, since)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/timeline/backfill")
async def backfill_azure_tag_policy_timeline(
    connection_id: int = Query(...),
    since: date = Query(default=date(2026, 10, 12)),
    db: AsyncSession = Depends(get_db),
):
    if since > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=422, detail="A data inicial não pode estar no futuro")
    try:
        return await AzureTagTimelineService(db).backfill(connection_id, since)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/executive-dashboard/refresh", status_code=202)
async def refresh_executive_tag_dashboard(
    connection_id: int = Query(...), db: AsyncSession = Depends(get_db),
):
    """Queue the existing Azure Resource Graph Bronze collector for one tenant."""
    connection = await db.get(SourceConnection, connection_id)
    if not connection or connection.connector_type != "azure_blob" or connection.cloud_scope != "azure":
        raise HTTPException(status_code=404, detail="Conexão Azure não encontrada")
    selection = (await db.execute(select(AzureConnectionSource).where(
        AzureConnectionSource.connection_id == connection_id,
        AzureConnectionSource.source_key == "resource_graph",
        AzureConnectionSource.enabled.is_(True),
    ))).scalar_one_or_none()
    if selection is None:
        raise HTTPException(
            status_code=422,
            detail="A fonte Resource Graph está desativada para esta conexão. Ative-a nas fontes Azure antes de atualizar o dashboard.",
        )

    from app.services.worker_runtime import any_worker_healthy
    if not any_worker_healthy():
        raise HTTPException(status_code=503, detail="Worker ETL indisponível; a coleta não foi enfileirada")

    active_tasks = (await db.execute(select(EtlTask).where(
        EtlTask.connection_id == connection_id,
        EtlTask.task_type == "extraction",
        EtlTask.status.in_(["QUEUED", "RUNNING"]),
    ).order_by(EtlTask.id.desc()).limit(30))).scalars().all()
    for task in active_tasks:
        if (task.result_json or {}).get("source_key") == "resource_graph":
            return {"task_id": task.id, "status": task.status, "message": "A coleta do Resource Graph já está em andamento."}

    raw_subscriptions = [value.strip() for value in (selection.subscription_ids or "").split(",") if value.strip()]
    invalid = [value for value in raw_subscriptions if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", value,
    )]
    if invalid:
        raise HTTPException(status_code=422, detail="A configuração da fonte Resource Graph contém subscription_id inválido")
    from app.services.azure_source_registry import source_code
    task = EtlTask(
        task_type="extraction", status="QUEUED", connection_id=connection.id,
        connection_ids=[connection.id], billing_periods=[], selected_blobs=[],
        total_blobs=0, processed_blobs=0, rows_raw=0, rows_normalized=0,
        rows_destination=0, errors=[],
        phase="Na fila: atualizar inventário de tags Azure na Bronze",
        result_json={
            "source_key": "resource_graph",
            "source_code": source_code(connection.code, "resource_graph"),
            "subscription_ids": raw_subscriptions or None,
            "user_triggered": True,
            "requested_from": "azure_tag_executive_dashboard",
        },
    )
    db.add(task)
    await db.flush()
    from app.services.task_runner import _inmemory_tasks
    _inmemory_tasks[task.id] = {
        "id": task.id, "connection_id": connection.id, "status": "QUEUED",
        "task_type": "extraction", "total_blobs": 0, "processed_blobs": 0,
        "rows_raw": 0, "rows_normalized": 0, "rows_destination": 0,
        "current_blob": None, "current_blob_size": 0, "download_bytes": 0,
        "downloaded_bytes_total": 0, "total_download_bytes": 0,
        "active_downloads": 0, "errors": [],
    }
    await db.commit()
    return {"task_id": task.id, "status": "QUEUED", "message": "Coleta Resource Graph enfileirada para atualizar a Bronze."}


@router.get("/executive-dashboard/resources")
async def executive_tag_dashboard_resources(
    connection_id: int = Query(...),
    subscription_id: str = Query(...),
    page_token: str | None = Query(None, max_length=8192),
    db: AsyncSession = Depends(get_db),
):
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", subscription_id):
        raise HTTPException(status_code=422, detail="subscription_id deve ser um GUID válido")
    try:
        return await AzureTagDashboardService(db).subscription_resources(
            connection_id, subscription_id, page_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/export")
async def export_policy(
    connection_id: int | None = Query(None, description="Optional: a single Azure connection/tenant"),
    department_id: int | None = Query(None, description="Optional: compile only one organizational project"),
    subscription_id: str | None = Query(None, description="Optional: restrict export to one accessible subscription"),
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
    if subscription_id and not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", subscription_id):
        raise HTTPException(status_code=422, detail="subscription_id deve ser um GUID válido")
    policies = []
    try:
        for tenant in tenants:
            doc = await service.generate_tenant_policy(tenant["connection_id"], department_id, subscription_id)
            if doc:
                policies.append(doc)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if subscription_id and not any(
        str(doc.get("selected_subscription_id") or "").casefold() == subscription_id.casefold()
        for doc in policies
    ):
        raise HTTPException(
            status_code=400,
            detail="A subscription selecionada não está acessível por nenhuma conexão Azure escolhida. Confira a conexão e o catálogo de subscriptions.",
        )
    has_policies = any(
        doc.get("layers", {}).get("resource_groups") or doc.get("layers", {}).get("resources")
        for doc in policies
    )
    script = build_script(policies, plan_only=plan_only) if has_policies and fmt in {"script", "both"} else ""
    if fmt == "script":
        return PlainTextResponse(script, media_type="text/x-shellscript")
    return JSONResponse({"tenants": policies, "deployment_script": script})


def _definition(
    display_name: str, description: str, condition: dict, tags: dict[str, str], mode: str,
    source_rule: str, exclusions: list[dict], *, rule_id: int | None = None,
    layer: str | None = None, identity_key: str | None = None,
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
            **({"openfinopsRuleId": str(rule_id)} if rule_id is not None else {}),
            **({"openfinopsLayer": layer} if layer else {}),
            **({"openfinopsIdentityKey": identity_key} if identity_key else {}),
        },
        "policyRule": {"if": _tag_drift_condition(condition, tags), "then": {"effect": "modify", "details": {
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


def _tag_drift_condition(region: dict, tags: dict[str, str]) -> dict:
    """Only select resources missing at least one exact, case-sensitive tag value."""
    drift: list[dict] = []
    for key, value in _collapse_tag_keys(tags).items():
        if not value:
            continue
        encoded = base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        escaped_key = str(key).replace("'", "''")
        observed = f"[base64(string(field('tags[''{escaped_key}'']')))]"
        drift.append({"anyOf": [
            {"field": f"tags['{key}']", "exists": "false"},
            {"value": observed, "notMatch": encoded},
        ]})
    if not drift:
        return region
    return {"allOf": [region, {"anyOf": drift}]}


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
        'SKIP_REMEDIATION="${SKIP_REMEDIATION:-false}"',
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
        'resolve_existing_target() {',
        '  local scope="$1" source_rule="$2" mode="$3" rows assignment definition definition_name category existing_rule existing_mode matches=0',
        '  RESOLVED_ASSIGNMENT_NAME=""; RESOLVED_POLICY_NAME=""',
        '  rows=$(az rest --method get --url "https://management.azure.com${scope}/providers/Microsoft.Authorization/policyAssignments?api-version=2022-06-01" --query "value[?contains(properties.policyDefinitionId, \`openfinops\`) || contains(properties.policyDefinitionId, \`of-def-\`)].[name,properties.policyDefinitionId]" -o tsv --only-show-errors) || { error "Falha ARM REST ao listar assignments em $scope"; exit 1; }',
        '  while IFS=$\'\\t\' read -r assignment definition; do',
        '    [[ -n "$assignment" && -n "$definition" ]] || continue',
        '    definition_name="${definition##*/}"',
        '    category=$(az policy definition show --name "$definition_name" --query "metadata.category" -o tsv --only-show-errors) || { error "Falha ao consultar definition $definition; interrompendo para evitar falso resultado de inexistência."; exit 1; }',
        '    [[ "$category" == "OpenFinops" ]] || continue',
        '    existing_rule=$(az policy definition show --name "$definition_name" --query "metadata.openfinopsSourceRule" -o tsv --only-show-errors) || { error "Falha ao consultar origem da definition $definition"; exit 1; }',
        '    existing_mode=$(az policy definition show --name "$definition_name" --query "mode" -o tsv --only-show-errors) || { error "Falha ao consultar mode da definition $definition"; exit 1; }',
        '    [[ "$existing_rule" == "$source_rule" && "$existing_mode" == "$mode" ]] || continue',
        '    matches=$((matches + 1)); RESOLVED_ASSIGNMENT_NAME="$assignment"; RESOLVED_POLICY_NAME="${definition##*/}"',
        '  done <<< "$rows"',
        '  if (( matches > 1 )); then error "Há $matches assignments OpenFinops candidatos para $source_rule em $scope; resolva a duplicidade manualmente."; exit 1; fi',
        '  if (( matches == 1 )); then info "Reutilizando assignment $RESOLVED_ASSIGNMENT_NAME e definition $RESOLVED_POLICY_NAME"; fi',
        '}',
        'ensure_definition() {',
        '  local name="$1" mode="$2" rules="$3" metadata_file="$4" display="$5" description="$6" expected_hash="$7"',
        '  local category current_hash lookup_output lookup_status definition_exists=0',
        '  shift 7; local -a metadata_args=("$@")',
        '  if lookup_output=$(az policy definition show --name "$name" --only-show-errors 2>&1); then definition_exists=1; else lookup_status=$?; [[ "$lookup_output" =~ (ResourceNotFound|NotFound|not\\ found|does\\ not\\ exist) ]] || { error "Falha consultando definition $name (az exit $lookup_status): $lookup_output"; exit 1; }; fi',
        '  if (( definition_exists )); then',
        '    category=$(az policy definition show --name "$name" --query "metadata.category" -o tsv --only-show-errors)',
        '    [[ "$category" == "OpenFinops" ]] || { action ABORT "Definition $name pertence a outra categoria ($category)."; exit 1; }',
        '    current_hash=$(az policy definition show --name "$name" --query "metadata.openfinopsContentHash" -o tsv --only-show-errors)',
        '    if [[ "$current_hash" == "$expected_hash" ]]; then action UNCHANGED "Definition $name"; return; fi',
        '    action UPDATE "Definition OpenFinops $name"',
        '    plan || run_mutation az policy definition update --name "$name" --mode "$mode" --rules "@$rules" --metadata "${metadata_args[@]}" --display-name "$display" --description "$description" --only-show-errors',
        '  else',
        '    action CREATE "Definition OpenFinops $name"',
        '    plan || run_mutation az policy definition create --name "$name" --mode "$mode" --rules "@$rules" --metadata "${metadata_args[@]}" --display-name "$display" --description "$description" --only-show-errors',
        '  fi',
        '}',
        'ensure_assignment() {',
        '  local name="$1" scope="$2" policy_name="$3" expected_id current_id identity_type lookup_output lookup_status',
        '  expected_id="$scope/providers/Microsoft.Authorization/policyDefinitions/$policy_name"',
        '  if ! plan; then expected_id=$(az policy definition show --name "$policy_name" --query id -o tsv --only-show-errors); fi',
        '  local existing_assignment=0',
        '  if lookup_output=$(az policy assignment show --name "$name" --scope "$scope" --only-show-errors 2>&1); then existing_assignment=1; else lookup_status=$?; [[ "$lookup_output" =~ (ResourceNotFound|NotFound|not\ found|does\ not\ exist) ]] || { error "Falha consultando assignment $name (az exit $lookup_status): $lookup_output"; exit 1; }; fi',
        '  if (( existing_assignment )); then',
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
        '  local rbac_id create_succeeded=0',
        '  [[ -n "$principal_id" && "$principal_id" != "null" ]] || { plan && { action CREATE "RBAC Tag Contributor em $scope (managed identity pendente)"; return; }; error "Managed identity não foi criada para o assignment."; exit 1; }',
        '  rbac_id=$(az role assignment list --assignee-object-id "$principal_id" --role "Tag Contributor" --scope "$scope" --query "[0].id" -o tsv --only-show-errors) || { error "Falha ao consultar RBAC Tag Contributor em $scope"; exit 1; }',
        '  if [[ -n "$rbac_id" && "$rbac_id" != "null" ]]; then action UNCHANGED "RBAC Tag Contributor em $scope"; return; fi',
        '  action CREATE "RBAC Tag Contributor em $scope"',
        '  plan && return',
        '  while (( attempt <= RETRY_ATTEMPTS )); do',
        '    if az role assignment create --assignee-object-id "$principal_id" --assignee-principal-type ServicePrincipal --role "Tag Contributor" --scope "$scope" --only-show-errors; then create_succeeded=1; break; fi',
        '    warn "RBAC ainda não disponível (tentativa $attempt/$RETRY_ATTEMPTS); aguardando propagação da identidade."; sleep "$RETRY_DELAY_SECONDS"; ((attempt++))',
        '  done',
        '  (( create_succeeded )) || { error "Não foi possível conceder Tag Contributor após $RETRY_ATTEMPTS tentativa(s)."; exit 1; }',
        '  attempt=1',
        '  while (( attempt <= RETRY_ATTEMPTS )); do',
        '    rbac_id=$(az role assignment list --assignee-object-id "$principal_id" --role "Tag Contributor" --scope "$scope" --query "[0].id" -o tsv --only-show-errors) || { error "Falha ao verificar propagação do RBAC em $scope"; exit 1; }',
        '    [[ -n "$rbac_id" && "$rbac_id" != "null" ]] && { info "RBAC Tag Contributor confirmado em $scope"; return; }',
        '    warn "RBAC criado, aguardando propagação (tentativa $attempt/$RETRY_ATTEMPTS)."; sleep "$RETRY_DELAY_SECONDS"; ((attempt++))',
        '  done',
        '  error "Tag Contributor não ficou visível após $RETRY_ATTEMPTS tentativa(s)."; exit 1',
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
        '    plan || run_mutation az policy remediation create --name "$name" --resource-group "$resource_group" --policy-assignment "$assignment" --resource-discovery-mode ReEvaluateCompliance --resource-count 50000 --only-show-errors',
        '  else',
        '    plan || run_mutation az policy remediation create --name "$name" --subscription "$subscription" --policy-assignment "$assignment" --resource-discovery-mode ReEvaluateCompliance --resource-count 50000 --only-show-errors',
        '  fi',
        '  if plan; then action CREATE "Trigger scan para $scope (não executado em PLAN)"; else run_mutation az policy state trigger-scan --no-wait --only-show-errors; fi',
        '  if [[ "$WAIT_FOR_REMEDIATION" == "true" && "$PLAN_ONLY" != "true" ]]; then',
        '    local deadline=$((SECONDS + 300))',
        '    while (( SECONDS < deadline )); do',
        '      if [[ -n "$resource_group" ]]; then state=$(az policy remediation show --name "$name" --resource-group "$resource_group" --query provisioningState -o tsv --only-show-errors);',
        '      else state=$(az policy remediation show --name "$name" --subscription "$subscription" --query provisioningState -o tsv --only-show-errors); fi',
        '      [[ "$state" != "Running" && "$state" != "Accepted" && "$state" != "InProgress" && "$state" != "Evaluating" ]] && break; sleep 10;',
        '    done',
        '    if [[ "$state" == "Running" || "$state" == "Accepted" || "$state" == "InProgress" || "$state" == "Evaluating" ]]; then error "Timeout aguardando remediation $name (estado $state)."; exit 2; fi',
        '    [[ "$state" == "Succeeded" ]] || { error "Remediation $name terminou com estado $state."; exit 1; }',
        '    local successful total',
        '    if [[ -n "$resource_group" ]]; then successful=$(az policy remediation show --name "$name" --resource-group "$resource_group" --query deploymentStatus.successfulDeployments -o tsv --only-show-errors); total=$(az policy remediation show --name "$name" --resource-group "$resource_group" --query deploymentStatus.totalDeployments -o tsv --only-show-errors);',
        '    else successful=$(az policy remediation show --name "$name" --subscription "$subscription" --query deploymentStatus.successfulDeployments -o tsv --only-show-errors); total=$(az policy remediation show --name "$name" --subscription "$subscription" --query deploymentStatus.totalDeployments -o tsv --only-show-errors); fi',
        '    if [[ "${total:-0}" == "0" ]]; then warn "Remediation concluída sem deployments; confirme compliance e seletor no Policy Insights."; else info "Remediation concluída: $successful/$total deployments bem-sucedidos."; fi',
        '    run_mutation az policy state trigger-scan --no-wait --only-show-errors',
        '    local compliance_deadline=$((SECONDS + 300)) noncompliant="" evaluated=""',
        '    while (( SECONDS < compliance_deadline )); do',
        '      evaluated=$(az policy state list --subscription "$subscription" --query "[?policyAssignmentName==\`$assignment\`].id | length(@)" -o tsv --only-show-errors) || { error "Falha consultando estados de conformidade após remediation."; exit 1; }',
        '      noncompliant=$(az policy state list --subscription "$subscription" --query "[?complianceState==\`NonCompliant\` && policyAssignmentName==\`$assignment\`].id | length(@)" -o tsv --only-show-errors) || { error "Falha consultando conformidade após remediation."; exit 1; }',
        '      [[ "${evaluated:-0}" != "0" && "${noncompliant:-0}" == "0" ]] && break',
        '      sleep 10',
        '    done',
        '    if [[ "${evaluated:-0}" == "0" ]]; then warn "Avaliação ainda não retornou estados para este assignment; tags não foram confirmadas."; elif [[ "${noncompliant:-0}" != "0" ]]; then warn "Tags verificadas com $noncompliant estado(s) ainda NonCompliant; confira recursos não elegíveis e Policy Insights."; else info "Conformidade verificada: $evaluated estado(s), nenhum NonCompliant para este assignment."; fi',
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
        subscription_names = {
            str(subscription_id).casefold(): str(name).strip()
            for subscription_id, name in (tenant.get("subscription_name_by_id") or {}).items()
            if str(name).strip()
        }
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
            identity_key = ""
            if subscriptions:
                scope_for_identity = _assignment_scope(subscriptions[0], item)
                identity_payload = json.dumps({
                    "connection_id": tenant.get("connection_id"), "rule_id": item.get("rule_id"),
                    "kind": kind, "scope": scope_for_identity,
                    "selector": item.get("base_selector", item.get("condition")),
                }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                identity_key = sha256(identity_payload.encode("utf-8")).hexdigest()[:12]
            connection_identity = str(tenant.get("connection_id") or "0")
            rule_identity = str(item.get("rule_id", index))
            name = f"of-def-{connection_identity}-{rule_identity}-{kind}-{identity_key or index}"
            if not subscriptions:
                lines += [f"# SKIPPED {name}: no observed Silver subscription or explicit subscription GUID for this rule.", ""]
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
                rule_id=item.get("rule_id"), layer=kind, identity_key=identity_key,
            )
            rules_filename = f"{name}-rules.json"
            metadata_filename = f"{name}-metadata.json"
            metadata_cli_args = " ".join(shlex.quote(f"{key}={value}") for key, value in policy["metadata"].items())
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
                    "kind": kind,
                    "assignment_suffix": assignment_suffix,
                    "identity_key": identity_key,
                    "source_rule": f"#{item.get('rule_id')} {item.get('rule_name')}",
                    "name": name,
                    "mode": mode,
                    "policy": policy,
                    "metadata_args": " ".join(
                        shlex.quote(f"{key}={value}") for key, value in policy["metadata"].items()
                    ),
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
                metadata_cli_args = job["metadata_args"]
                assignment = f"of-asg-{tenant.get('connection_id') or 0}-{job['source_rule'].split(' ', 1)[0].lstrip('#')}-{job['kind']}-{job['identity_key']}"
                lines += [
                    f"# Subscription alvo: {subscription_names.get(subscription_id.casefold(), 'nome não informado')} ({subscription_id})",
                    f"EXPECTED_SUBSCRIPTION={q(subscription_id)}",
                    'CURRENT_SUBSCRIPTION=$(az account show --query id -o tsv --only-show-errors)',
                    'if [[ "$CURRENT_SUBSCRIPTION" != "$EXPECTED_SUBSCRIPTION" ]]; then',
                    '  if plan; then action ABORT "Subscription ativa $CURRENT_SUBSCRIPTION; esperada $EXPECTED_SUBSCRIPTION"; exit 1; fi',
                    '  az account set --subscription "$EXPECTED_SUBSCRIPTION" --only-show-errors',
                    'fi',
                    'CURRENT_SUBSCRIPTION=$(az account show --query id -o tsv --only-show-errors)',
                    '[[ "$CURRENT_SUBSCRIPTION" == "$EXPECTED_SUBSCRIPTION" ]] || { error "Subscription ativa inesperada: $CURRENT_SUBSCRIPTION (esperada: $EXPECTED_SUBSCRIPTION)"; exit 1; }',
                    f"resolve_existing_target {q(scope)} {q(job['source_rule'])} {q(job['mode'])}",
                    f"ACTIVE_ASSIGNMENT_NAME=\"${{RESOLVED_ASSIGNMENT_NAME:-{assignment}}}\"",
                    f"ACTIVE_POLICY_NAME=\"${{RESOLVED_POLICY_NAME:-{name}}}\"",
                ]
                lines += [
                    f"ensure_definition \"$ACTIVE_POLICY_NAME\" {q(job['mode'])} \"$POLICIES_DIR/{job['rules_filename']}\" \"$POLICIES_DIR/{job['metadata_filename']}\" {q(policy['displayName'])} {q(policy['description'])} {q(policy['metadata']['openfinopsContentHash'])} {metadata_cli_args}",
                    f"ensure_assignment \"$ACTIVE_ASSIGNMENT_NAME\" {q(scope)} \"$ACTIVE_POLICY_NAME\"",
                    f"if az policy assignment show --name \"$ACTIVE_ASSIGNMENT_NAME\" --scope {q(scope)} --only-show-errors >/dev/null 2>&1 || plan; then",
                    '  PRINCIPAL_ID=""',
                    f"  if az policy assignment show --name \"$ACTIVE_ASSIGNMENT_NAME\" --scope {q(scope)} --only-show-errors >/dev/null 2>&1; then",
                    f"    if PRINCIPAL_ID=$(resolve_assignment_principal \"$ACTIVE_ASSIGNMENT_NAME\" {q(scope)}); then :; else PRINCIPAL_ID=\"\"; fi",
                    '  fi',
                    f"  ensure_tag_contributor \"$PRINCIPAL_ID\" {q(scope)}",
                    '  if [[ "$SKIP_REMEDIATION" == "true" ]]; then',
                    '    if plan; then action CREATE "Trigger scan para $scope (sem nova remediation em SKIP_REMEDIATION)"; else run_mutation az policy state trigger-scan --no-wait --only-show-errors; fi',
                    '  else',
                    f"    ensure_remediation \"$ACTIVE_ASSIGNMENT_NAME\" {q(scope)}",
                    '  fi',
                    'else',
                    '  error "Assignment $ACTIVE_ASSIGNMENT_NAME não encontrado após ensure_assignment."; exit 1',
                    'fi',
                    "",
                ]
    lines += [
        'if plan; then echo "[INFO] PLAN_ONLY concluído; nenhuma alteração Azure foi submetida."',
        'elif [[ "$SKIP_REMEDIATION" == "true" ]]; then echo "[INFO] Definitions/assignments processados; nenhuma remediation nova foi criada e a avaliação foi submetida."',
        'else echo "[INFO] OpenFinops tag policy deployment submitted; confira o estado de cada remediation."; fi',
    ]
    return "\n".join(lines) + "\n"
