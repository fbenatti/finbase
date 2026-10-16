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


def compact_region(node):
    """Factor Boolean predicates without broadening their decision regions.

    (A AND B) OR (A AND C) becomes A AND (B OR C). Exact scalar
    alternatives become ``in``; contains/like patterns are never inferred.
    """
    if set(node) == {'not'}:
        return {'not': compact_region(node['not'])}
    op = next((key for key in ('allOf', 'anyOf') if set(node) == {key}), None)
    if not op:
        return node
    children = []
    for child in node[op]:
        child = compact_region(child)
        children.extend(child[op] if set(child) == {op} else [child])
    unique = {canonical(child): child for child in children}
    children = [unique[key] for key in sorted(unique)]
    if op == 'anyOf':
        scalar_fields = {'id', 'name', 'type', 'location', 'subscriptionId'}
        alternatives, rest = defaultdict(list), []
        for child in children:
            comparator = 'equals' if 'equals' in child else 'in'
            if (len(child) == 2 and child.get('field') in scalar_fields
                    and comparator in child):
                alternatives[child['field']].extend(
                    child[comparator] if comparator == 'in' else [child[comparator]])
            else:
                rest.append(child)
        for field, values in sorted(alternatives.items()):
            values = {canonical(value): value for value in values}
            values = [values[key] for key in sorted(values)]
            rest.append({'field': field, **({'equals': values[0]} if len(values) == 1 else {'in': values})})
        children = sorted(rest, key=canonical)
        # Factor shared terms, also when only a subset of branches shares them.
        rows = [{canonical(term): term for term in (child['allOf'] if set(child) == {'allOf'} else [child])}
                for child in children]
        frequencies = defaultdict(int)
        for row in rows:
            for key in row:
                frequencies[key] += 1
        candidates = [key for key, count in frequencies.items() if count > 1]
        if candidates:
            key = max(candidates, key=lambda k: (frequencies[k] * len(k), k))
            selected = [row for row in rows if key in row]
            common = set.intersection(*(set(row) for row in selected))
            shared = [selected[0][key] for key in sorted(common)]
            remainders = [[row[key] for key in sorted(set(row) - common)] for row in selected]
            if all(remainders):
                shared.append(compact_region({'anyOf': [compact_region({'allOf': row}) for row in remainders]}))
            combined = compact_region({'allOf': shared})
            others = [child for child, row in zip(children, rows) if key not in row]
            return compact_region({'anyOf': [combined, *others]}) if others else combined
    return children[0] if len(children) == 1 else {op: children}


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
    outputs = defaultdict(set)
    for connection, subscription, rule_id, layer, tags in groups:
        outputs[(connection, subscription, rule_id, layer)].add(tags)
    bundles = {}
    for (connection, subscription, rule_id, layer, _), items in sorted(groups.items()):
        bundle = bundles.setdefault((connection, subscription), {
            'subscription_id': subscription, 'subscription_name': names.get(subscription, subscription),
            'connection_id': connection, 'definitions': [], 'original_assignments': 0,
        })
        regions = {canonical(bound_region(i, layer, subscription)): bound_region(i, layer, subscription) for i in items}
        base = sorted({canonical(bound_region(i, layer, subscription, base=True)) for i in items})
        # Inventory changes must update an existing definition rather than
        # allocate a fresh one. Use the source predicate, not observed RGs.
        # Evidence-dependent tag variants retain separate scope identities.
        if (len(outputs[(connection, subscription, rule_id, layer)]) == 1
                and all(i.get('identity_selector') for i in items)):
            base = sorted({canonical(i['identity_selector']) for i in items})
        identity = digest([connection, subscription, rule_id, layer, base])
        name = f'of-def-{connection}-{rule_id}-{layer}-{identity}'
        conditions = [regions[k] for k in sorted(regions)]
        region = compact_region(conditions[0] if len(conditions) == 1 else {'anyOf': conditions})
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
        # Keep a conservative expression budget after factoring predicates.
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
check_object_capacity() {
  local label="$1" maximum="$2" existing="$3" planned="$4" current=0 additional=0 name
  while IFS= read -r name; do [[ -z "$name" ]] || current=$((current + 1)); done <<< "$existing"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    [[ $'\n'"$existing"$'\n' == *$'\n'"$name"$'\n'* ]] || additional=$((additional + 1))
  done <<< "$planned"
  info "$label: $current existentes + $additional novos / limite $maximum"
  if (( current + additional > maximum )); then
    error "Capacidade insuficiente de $label; nenhuma policy foi alterada. Revise as regras ou migre definições antigas."
    exit 3
  fi
}
check_policy_capacity() {
  local subscription="$1" definitions_planned="$2" initiative="$3" assignment="$4"
  local scope="/subscriptions/$1" definitions initiatives assignments
  definitions=$(az policy definition list --subscription "$subscription" --query "[?policyType=='Custom' && starts_with(id, '$scope/providers/Microsoft.Authorization/policyDefinitions/')].name" -o tsv --only-show-errors) || { error "Falha ao consultar capacidade de definitions"; exit 1; }
  initiatives=$(az policy set-definition list --subscription "$subscription" --query "[?policyType=='Custom' && starts_with(id, '$scope/providers/Microsoft.Authorization/policySetDefinitions/')].name" -o tsv --only-show-errors) || { error "Falha ao consultar capacidade de initiatives"; exit 1; }
  assignments=$(az policy assignment list --scope "$scope" --query "[?scope=='$scope'].name" -o tsv --only-show-errors) || { error "Falha ao consultar capacidade de assignments"; exit 1; }
  check_object_capacity definitions 500 "$definitions" "$definitions_planned"
  check_object_capacity initiatives 200 "$initiatives" "$initiative"
  check_object_capacity assignments 200 "$assignments" "$assignment"
}
ensure_definition_json() {
  local name="$1" expected_hash="$2" file="$3" output exists=0 category current_hash
  if output=$(az policy definition show --name "$name" --only-show-errors 2>&1); then exists=1
  else [[ "$output" =~ (ResourceNotFound|NotFound|not\ found|does\ not\ exist) ]] || { error "Falha consultando definition $name: $output"; exit 1; }; fi
  if (( exists )); then
    category=$(az policy definition show --name "$name" --query metadata.category -o tsv --only-show-errors) || exit 1
    [[ "$category" == "OpenFinops" ]] || { error "Definition $name pertence a outra categoria ($category)"; exit 1; }
    current_hash=$(az policy definition show --name "$name" --query metadata.openfinopsContentHash -o tsv --only-show-errors) || exit 1
    if [[ "$current_hash" == "$expected_hash" ]]; then action UNCHANGED "Definition $name"; return; fi
    action UPDATE "Definition OpenFinops $name"
  else action CREATE "Definition OpenFinops $name"; fi
  plan || run_mutation az rest --method put --url "https://management.azure.com/subscriptions/$EXPECTED_SUBSCRIPTION/providers/Microsoft.Authorization/policyDefinitions/$name?api-version=2023-04-01" --body "@$file" --only-show-errors -o none
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
        records = ['\t'.join([d['name'], d['definition']['metadata']['openfinopsContentHash'],
                             canonical({'properties': d['definition']})]) for d in bundle['definitions']]
        lines += [f"# Subscription alvo: {bundle['subscription_name']} ({sub})",
                  f'EXPECTED_SUBSCRIPTION={q(sub)}',
                  'CURRENT_SUBSCRIPTION=$(az account show --query id -o tsv --only-show-errors)',
                  'if [[ "$CURRENT_SUBSCRIPTION" != "$EXPECTED_SUBSCRIPTION" ]]; then',
                  '  if plan; then error "Selecione a subscription alvo antes do PLAN"; exit 1; fi',
                  '  az account set --subscription "$EXPECTED_SUBSCRIPTION" --only-show-errors', 'fi',
                  'cat > "$POLICIES_DIR/definitions.tsv" <<\'DEFINITIONSEOF\'', *records, 'DEFINITIONSEOF',
                  'DEFINITION_NAMES=$(cut -f1 "$POLICIES_DIR/definitions.tsv")',
                  f'check_policy_capacity {q(sub)} "$DEFINITION_NAMES" {q(bundle["name"])} {q(bundle["assignment_name"])}',
                  f'check_shared_identity {q(sub)}',
                  f"info '{bundle['original_assignments']} assignments originais -> {len(bundle['definitions'])} definitions / 1 iniciativa / 1 assignment / 1 identidade'",
                  '# PLAN verifica a carga; autorização e quota RBAC da concessão são confirmadas pela API no APPLY.',
                  '# Assignments legados não são removidos por este script. Migrar após equivalência validada.',
                  'while IFS=$\'\t\' read -r POLICY_NAME CONTENT_HASH DEFINITION_JSON; do',
                  '  printf \'%s\' "$DEFINITION_JSON" > "$POLICIES_DIR/definition.json"',
                  '  ensure_definition_json "$POLICY_NAME" "$CONTENT_HASH" "$POLICIES_DIR/definition.json"',
                  'done < "$POLICIES_DIR/definitions.tsv"']
        lines += ['cat > "$POLICIES_DIR/initiative.json" <<\'INITIATIVEEOF\'', canonical(bundle['members']), 'INITIATIVEEOF',
                  f'ensure_initiative {q(bundle["name"])} "$POLICIES_DIR/initiative.json" {q(bundle["content_hash"])}',
                  f'ensure_initiative_assignment {q(bundle["assignment_name"])} {q(scope)} {q(bundle["name"])}',
                  'PRINCIPAL_ID=""',
                  f'if [[ "$ASSIGNMENT_ALREADY_EXISTS" == "1" ]] || ! plan; then PRINCIPAL_ID=$(resolve_assignment_principal {q(bundle["assignment_name"])} {q(scope)}) || exit 1; fi',
                  f'ensure_tag_contributor "$PRINCIPAL_ID" {q(scope)}',
                  'if [[ "$SKIP_REMEDIATION" != "true" ]]; then']
        assignment_id=scope+'/providers/Microsoft.Authorization/policyAssignments/'+bundle['assignment_name']
        lines += ['  while IFS=$\'\t\' read -r REFERENCE_ID _; do',
                  f'    remediate_reference {q(assignment_id)} "$REFERENCE_ID" {q(sub)}',
                  '  done < "$POLICIES_DIR/definitions.tsv"',
                  'fi', 'if ! plan; then run_mutation az policy state trigger-scan --no-wait --only-show-errors; fi']
    lines += ['if plan; then info "PLAN de carga concluído sem mutações. RBAC da remediação será confirmado no APPLY.";',
              'else info "Deploy submetido. Conclusão depende das remediações e da verificação direta das tags."; fi']
    return '\n'.join(lines)+'\n'
