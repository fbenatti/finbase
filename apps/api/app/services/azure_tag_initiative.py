"""Compile scope-bound tag regions into one initiative assignment per subscription."""
from __future__ import annotations

import json
import shlex
from collections import defaultdict
from hashlib import sha256

ARM_REQUEST_LIMIT = 1_048_576
RG_TYPE = 'Microsoft.Resources/subscriptions/resourceGroups'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()[:12]


def bound_region(item, layer, subscription, *, base=False):
    region = item.get('base_selector', item['condition']) if base else item['condition']
    if item.get('assignment_scope_kind') == 'resource_group':
        rg = str(item.get('assignment_resource_group') or '').strip()
        if not rg:
            raise ValueError('Assignment de RG sem nome: iniciativa bloqueada para evitar ampliar escopo')
        scope = f'/subscriptions/{subscription}/resourceGroups/{rg}'
        guard = {'field': 'id', 'equals': scope} if layer == 'rg' else {'field': 'id', 'like': scope + '/*'}
        return {'allOf': [guard, region]}
    return region


def compile_initiatives(policies, definition_factory):
    groups = defaultdict(list)
    names = {}
    for tenant in policies:
        connection = str(tenant.get('connection_id') or 0)
        names.update({k.casefold(): v for k, v in tenant.get('subscription_name_by_id', {}).items()})
        for layer, key in [('rg', 'resource_groups'), ('res', 'resources')]:
            for item in tenant.get('layers', {}).get(key, []):
                for subscription in item.get('scope_subscriptions') or []:
                    # Distinct tag outputs stay distinct, even for the same source rule.
                    groups[(connection, subscription.casefold(), str(item['rule_id']), layer,
                            canonical(item.get('tags', {})))].append(item)
    bundles = {}
    for (connection, subscription, rule_id, layer, _), items in sorted(groups.items()):
        bundle = bundles.setdefault((connection, subscription), {
            'subscription_id': subscription, 'subscription_name': names.get(subscription, subscription),
            'connection_id': connection, 'definitions': [], 'original_assignments': 0,
        })
        regions = {canonical(bound_region(i, layer, subscription)): bound_region(i, layer, subscription) for i in items}
        base = sorted({canonical(bound_region(i, layer, subscription, base=True)) for i in items})
        identity = digest([connection, subscription, rule_id, layer, base])
        name = f'of-def-{connection}-{rule_id}-{layer}-{identity}'
        conditions = [regions[k] for k in sorted(regions)]
        region = conditions[0] if len(conditions) == 1 else {'anyOf': conditions}
        first = items[0]
        exclusions = {canonical(e): e for i in items for e in i.get('exclusions', [])}
        definition = definition_factory(
            f"OpenFinops - {first.get('rule_name', rule_id)}",
            f'Regra #{rule_id}; seletores com os escopos originais preservados.',
            region, first.get('tags', {}), 'All' if layer == 'rg' else 'Indexed',
            f"#{rule_id} {first.get('rule_name', rule_id)}", list(exclusions.values()),
            rule_id=int(rule_id), layer='rg' if layer == 'rg' else 'resource', identity_key=identity,
        )
        if len(canonical(definition).encode()) >= ARM_REQUEST_LIMIT:
            raise ValueError(f'Regra #{rule_id} excede 1 MiB; exportação bloqueada antes de alterações Azure')
        # Azure Policy allows 512 condition expressions per definition.
        def expression_count(node):
            if isinstance(node, dict):
                return int('field' in node or 'value' in node) + sum(expression_count(v) for v in node.values())
            if isinstance(node, list):
                return sum(expression_count(v) for v in node)
            return 0
        if expression_count(definition['policyRule']) > 512:
            raise ValueError(f'Regra #{rule_id} excede orçamento de 512 expressões; dividir esta regra preservando as regiões')
        if any(d['name'] == name for d in bundle['definitions']):
            raise ValueError(f'Regra #{rule_id} tem classificações incompatíveis na mesma região')
        bundle['definitions'].append({'name': name, 'reference_id': name, 'definition': definition,
                                      'original_regions': conditions})
        bundle['original_assignments'] += len(items)
    for bundle in bundles.values():
        key = digest([bundle['connection_id'], bundle['subscription_id']])
        bundle['name'] = f"of-set-{bundle['connection_id']}-{key}"
        bundle['assignment_name'] = f"of-asg-set-{bundle['connection_id']}-{key}"
        scope = '/subscriptions/' + bundle['subscription_id']
        bundle['members'] = [{'policyDefinitionId': scope + '/providers/Microsoft.Authorization/policyDefinitions/' + d['name'],
                              'policyDefinitionReferenceId': d['reference_id']} for d in bundle['definitions']]
        if len(bundle['members']) > 1000 or len(canonical(bundle['members']).encode()) > ARM_REQUEST_LIMIT - 4096:
            raise ValueError('Iniciativa excede limite ARM; exportação bloqueada')
        bundle['content_hash'] = digest(bundle['members'])
    return list(bundles.values())


def render_initiative_script(bundles, preamble, plan_only=False):
    q = shlex.quote
    # An initiative has only one identity/grant. Shared UAMI remains optional.
    lines = [preamble.replace('IDENTITY_MODE="${IDENTITY_MODE:-shared}"', 'IDENTITY_MODE="${IDENTITY_MODE:-system}"'), r'''
QUOTA_BLOCKED=false
check_initiative_quota() {
  local subscription="$1" assignment="$2" scope="/subscriptions/$1" output principal="" role="" scopes entry count=0
  [[ "$IDENTITY_MODE" != "shared" ]] || return 0
  if output=$(az policy assignment show --name "$assignment" --scope "$scope" --only-show-errors 2>&1); then
    principal=$(resolve_assignment_principal "$assignment" "$scope") || exit 1
    role=$(az role assignment list --assignee-object-id "$principal" --role "Tag Contributor" --scope "$scope" --subscription "$subscription" --query "[0].id" -o tsv --only-show-errors) || exit 1
    [[ -z "$role" || "$role" == "null" ]] || return 0
  else [[ "$output" =~ (ResourceNotFound|NotFound|not\ found|does\ not\ exist) ]] || { error "$output"; exit 1; }; fi
  scopes=$(az role assignment list --all --subscription "$subscription" --query "[].scope" -o tsv --only-show-errors) || { error "Falha na leitura da quota RBAC"; exit 1; }
  while IFS= read -r entry; do
    if [[ "${entry,,}" == "${scope,,}" || "${entry,,}" == "${scope,,}/"* ]]; then count=$((count + 1)); fi
  done <<< "$scopes"
  info "RBAC na subscription: $count; nova concessão necessária: 1"
  if (( count >= 5000 )); then
    error "Quota RBAC esgotada em $subscription; compactação não libera permissões já existentes"
    QUOTA_BLOCKED=true
    plan || exit 3
  fi
}
ensure_initiative() {
  local name="$1" file="$2" content_hash="$3" output exists=0
  if output=$(az policy set-definition show --name "$name" --only-show-errors 2>&1); then exists=1
  else [[ "$output" =~ (ResourceNotFound|NotFound|not\ found|does\ not\ exist) ]] || { error "$output"; exit 1; }; fi
  if (( exists )); then
    local category
    category=$(az policy set-definition show --name "$name" --query metadata.category -o tsv --only-show-errors)
    [[ "$category" == "OpenFinops" ]] || { error "Iniciativa pertence a outra categoria"; exit 1; }
    local current_hash
    current_hash=$(az policy set-definition show --name "$name" --query metadata.openfinopsContentHash -o tsv --only-show-errors)
    if [[ "$current_hash" == "$content_hash" ]]; then action UNCHANGED "Initiative $name"; return; fi
    action UPDATE "Initiative $name"
    plan || run_mutation az policy set-definition update --name "$name" --definitions "@$file" --metadata category=OpenFinops "openfinopsContentHash=$content_hash" --only-show-errors -o none
  else
    action CREATE "Initiative $name"
    plan || run_mutation az policy set-definition create --name "$name" --definitions "@$file" --metadata category=OpenFinops "openfinopsContentHash=$content_hash" --display-name "OpenFinops Tags - $EXPECTED_SUBSCRIPTION" --only-show-errors -o none
  fi
}
ensure_initiative_assignment() {
  local name="$1" scope="$2" initiative="$3" output exists=0 actual identity_type
  local expected="$scope/providers/Microsoft.Authorization/policySetDefinitions/$initiative"
  local -a identity_args=(--mi-system-assigned)
  [[ "$IDENTITY_MODE" != "shared" ]] || identity_args=(--mi-user-assigned "$SHARED_IDENTITY_ID")
  if output=$(az policy assignment show --name "$name" --scope "$scope" --only-show-errors 2>&1); then exists=1
  else [[ "$output" =~ (ResourceNotFound|NotFound|not\ found|does\ not\ exist) ]] || { error "$output"; exit 1; }; fi
  ASSIGNMENT_ALREADY_EXISTS="$exists"
  if (( exists )); then
    actual=$(az policy assignment show --name "$name" --scope "$scope" --query policyDefinitionId -o tsv --only-show-errors)
    [[ "${actual,,}" == "${expected,,}" ]] || { error "Assignment aponta para outra iniciativa"; exit 1; }
    identity_type=$(az policy assignment show --name "$name" --scope "$scope" --query identity.type -o tsv --only-show-errors)
    if [[ "$identity_type" == "SystemAssigned" || "$identity_type" == "UserAssigned" ]]; then action UNCHANGED "Assignment $name (identidade preservada)"
    else plan || run_mutation az policy assignment identity assign --name "$name" --scope "$scope" "${identity_args[@]}" --only-show-errors -o none; fi
  else
    action CREATE "Assignment único $name"
    plan || run_mutation az policy assignment create --name "$name" --scope "$scope" --policy-set-definition "$expected" "${identity_args[@]}" --location "$LOCATION" --only-show-errors -o none
  fi
}
remediate_reference() {
  local assignment_id="$1" reference="$2" subscription="$3" name payload state
  REMEDIATION_SEQ=$((REMEDIATION_SEQ + 1))
  name="of-rem-${RUN_ID}-${REMEDIATION_SEQ}"
  payload="$POLICIES_DIR/remediation-${REMEDIATION_SEQ}.json"
  printf '{"properties":{"policyAssignmentId":"%s","policyDefinitionReferenceId":"%s","resourceDiscoveryMode":"ReEvaluateCompliance","resourceCount":50000}}' "$assignment_id" "$reference" > "$payload"
  action CREATE "Remediation $name; referência $reference"
  plan || run_mutation az rest --method put --url "https://management.azure.com/subscriptions/$subscription/providers/Microsoft.PolicyInsights/remediations/$name?api-version=2021-10-01" --body "@$payload" --only-show-errors -o none
  if [[ "$WAIT_FOR_REMEDIATION" == "true" ]] && ! plan; then
    local deadline=$((SECONDS + 300))
    while (( SECONDS < deadline )); do
      state=$(az policy remediation show --name "$name" --subscription "$subscription" --query provisioningState -o tsv --only-show-errors) || exit 1
      case "$state" in
        Succeeded)
          local total
          total=$(az policy remediation show --name "$name" --subscription "$subscription" --query deploymentStatus.totalDeployments -o tsv --only-show-errors) || exit 1
          if [[ "${total:-0}" == "0" ]]; then warn "Remediação $name sem deployments; reconciliar seletor e tags atuais"; else info "Remediação $name concluída com $total deployments; verificar tags e conformidade"; fi
          return;;
        Failed|Canceled|Cancelled) error "Remediação $name: $state"; exit 1;;
        Accepted|Running|Evaluating|InProgress) sleep 10;;
        *) error "Estado inesperado $state"; exit 1;;
      esac
    done
    error "Timeout: $name continua pendente ($state)"; exit 2
  fi
}
require_az
RUN_ID="$(date -u +%Y%m%d%H%M%S)-${RANDOM}"
''']
    for bundle in bundles:
        sub = bundle['subscription_id']; scope = '/subscriptions/' + sub
        lines += [f"# Subscription alvo: {bundle['subscription_name']} ({sub})",
                  f'EXPECTED_SUBSCRIPTION={q(sub)}',
                  'CURRENT_SUBSCRIPTION=$(az account show --query id -o tsv --only-show-errors)',
                  'if [[ "$CURRENT_SUBSCRIPTION" != "$EXPECTED_SUBSCRIPTION" ]]; then',
                  '  if plan; then error "Selecione a subscription alvo antes do PLAN"; exit 1; fi',
                  '  az account set --subscription "$EXPECTED_SUBSCRIPTION" --only-show-errors', 'fi',
                  f'check_shared_identity {q(sub)}',
                  f'check_initiative_quota {q(sub)} {q(bundle["assignment_name"])}',
                  f"info '{bundle['original_assignments']} assignments originais -> {len(bundle['definitions'])} definitions / 1 iniciativa / 1 assignment / 1 identidade'",
                  '# PLAN não comprova quota livre para a nova permissão RBAC.',
                  '# Assignments legados não são removidos por este script. Migrar após equivalência validada.']
        for d in bundle['definitions']:
            name = d['name']; definition = d['definition']
            lines += [f'cat > "$POLICIES_DIR/{name}.json" <<\'POLICYEOF\'',
                      json.dumps(definition['policyRule'], ensure_ascii=False, indent=2), 'POLICYEOF',
                      f"# {definition['metadata']['openfinopsSourceRule']}; exceções: {definition['metadata']['openfinopsExclusions']}"]
            metadata = ' '.join(q(f'{k}={v}') for k,v in definition['metadata'].items())
            lines += [f'ensure_definition {q(name)} {q(definition["mode"])} "$POLICIES_DIR/{name}.json" unused {q(definition["displayName"])} {q(definition["description"])} {q(definition["metadata"]["openfinopsContentHash"])} {metadata}']
        lines += ['cat > "$POLICIES_DIR/initiative.json" <<\'INITIATIVEEOF\'', json.dumps(bundle['members'], indent=2), 'INITIATIVEEOF',
                  f'ensure_initiative {q(bundle["name"])} "$POLICIES_DIR/initiative.json" {q(bundle["content_hash"])}',
                  f'ensure_initiative_assignment {q(bundle["assignment_name"])} {q(scope)} {q(bundle["name"])}',
                  'PRINCIPAL_ID=""',
                  f'if [[ "$ASSIGNMENT_ALREADY_EXISTS" == "1" ]] || ! plan; then PRINCIPAL_ID=$(resolve_assignment_principal {q(bundle["assignment_name"])} {q(scope)}) || exit 1; fi',
                  f'ensure_tag_contributor "$PRINCIPAL_ID" {q(scope)}',
                  'if [[ "$SKIP_REMEDIATION" != "true" ]]; then']
        for d in bundle['definitions']:
            assignment_id=scope+'/providers/Microsoft.Authorization/policyAssignments/'+bundle['assignment_name']
            lines += [f'  remediate_reference {q(assignment_id)} {q(d["reference_id"])} {q(sub)}']
        lines += ['fi', 'if ! plan; then run_mutation az policy state trigger-scan --no-wait --only-show-errors; fi']
    lines += ['if [[ "$QUOTA_BLOCKED" == "true" ]]; then error "PLAN BLOQUEADO pela quota RBAC; nenhuma mutação foi executada"; exit 3; fi',
              'if plan; then info "PLAN concluído sem mutações. Verifique migração dos legados antes do APPLY.";',
              'else info "Deploy submetido. Conclusão depende das remediações e da verificação direta das tags."; fi']
    return '\n'.join(lines)+'\n'
