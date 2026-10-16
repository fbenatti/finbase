import fnmatch
import os
import subprocess
import pytest

from app.routers.azure_tag_policy import _definition, build_script
from app.services.azure_tag_initiative import compile_initiatives, bound_region, RG_TYPE

SUB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def item(rule, region, tags, rg=None):
    return {'rule_id': rule, 'rule_name': str(rule), 'condition': region, 'base_selector': region,
            'tags': tags, 'scope_subscriptions': [SUB], **({'assignment_scope_kind': 'resource_group',
            'assignment_resource_group': rg} if rg else {})}


def evaluate(region, obj):
    if 'allOf' in region: return all(evaluate(v, obj) for v in region['allOf'])
    if 'anyOf' in region: return any(evaluate(v, obj) for v in region['anyOf'])
    if 'not' in region: return not evaluate(region['not'], obj)
    value = obj.get(region.get('field'), '')
    if 'equals' in region: return value.casefold() == region['equals'].casefold()
    if 'contains' in region: return region['contains'].casefold() in value.casefold()
    if 'like' in region: return fnmatch.fnmatchcase(value.casefold(), region['like'].casefold())
    raise AssertionError(region)


def test_scoped_rg_regions_are_merged_without_leaking_to_other_rgs():
    tags = {'projeto': 'Vivo+'}
    originals = [item(5217, {'field': 'type', 'equals': RG_TYPE}, tags, rg) for rg in ['rg-a', 'rg-b']]
    tenant = {'connection_id': 2, 'layers': {'resource_groups': originals, 'resources': []}}
    bundles = compile_initiatives([tenant], _definition)
    assert len(bundles) == 1
    assert bundles[0]['original_assignments'] == 2
    assert len(bundles[0]['definitions']) == 1
    compiled_region = bundles[0]['definitions'][0]['definition']['policyRule']['if']['allOf'][0]
    for rg in ['rg-a', 'rg-b', 'rg-a-child', 'PCCAgentlessScanResourceGroup', 'other']:
        obj = {'type': RG_TYPE, 'name': rg, 'id': f'/subscriptions/{SUB}/resourceGroups/{rg}'}
        expected = any(evaluate(bound_region(i, 'rg', SUB), obj) for i in originals)
        assert evaluate(compiled_region, obj) == expected
    assert not evaluate(compiled_region, {'type': RG_TYPE, 'id': '/subscriptions/other/resourceGroups/rg-a'})


def test_resource_scope_requires_the_exact_parent_rg():
    source = item(1, {'field': 'type', 'equals': 'Microsoft.Compute/virtualMachines'}, {'projeto': 'A'}, 'rg-a')
    region = bound_region(source, 'res', SUB)
    for rg, expected in [('rg-a', True), ('RG-A', True), ('rg-a-child', False), ('rg-b', False)]:
        obj = {'type': 'Microsoft.Compute/virtualMachines', 'id': f'/subscriptions/{SUB}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/vm'}
        assert evaluate(region, obj) == expected


def test_foundation_subscription_and_prisma_exceptions_survive_initiative():
    prisma = {'anyOf': [{'field': 'name', 'contains': 'PCCAgentlessScanResourceGroup'},
                        {'field': 'name', 'contains': 'dig-security-rg'}]}
    foundation = {'allOf': [{'field': 'type', 'equals': RG_TYPE}, {'not': prisma}]}
    original = [item(52, foundation, {'projeto': 'fundacao telefonica'}),
                item(78, {'allOf': [{'field': 'type', 'equals': RG_TYPE}, prisma]}, {'projeto': 'Prisma'})]
    tenant = {'connection_id': 2, 'layers': {'resource_groups': original, 'resources': []}}
    bundle = compile_initiatives([tenant], _definition)[0]
    for name, expected in [('rg-common', ['fundacao telefonica']), ('PCCAgentlessScanResourceGroup', ['Prisma']),
                           ('dig-security-rg-123', ['Prisma'])]:
        obj = {'name': name, 'type': RG_TYPE}
        matches = [d['definition']['policyRule']['then']['details']['operations'][0]['value']
                   for d in bundle['definitions'] if evaluate(d['definition']['policyRule']['if']['allOf'][0], obj)]
        assert matches == expected
    assert len(bundle['members']) == 2


def test_initiative_names_stable_across_output_order():
    a = item(1, {'field': 'name', 'equals': 'rg-a'}, {'projeto': 'A'})
    b = item(2, {'field': 'name', 'equals': 'rg-b'}, {'projeto': 'B'})
    def compile(rows): return compile_initiatives([{'layers': {'resource_groups': rows}}], _definition)
    assert compile([a,b]) == compile([b,a])


def test_missing_rg_or_oversized_expression_budget_blocks_export():
    source = item(1, {'field': 'name', 'equals': 'x'}, {'projeto': 'A'}, 'rg')
    source['assignment_resource_group'] = ''
    with pytest.raises(ValueError, match='sem nome'):
        compile_initiatives([{'layers': {'resource_groups': [source]}}], _definition)
    source = item(1, {'anyOf': [{'field': 'name', 'equals': str(i)} for i in range(513)]}, {'projeto': 'A'})
    with pytest.raises(ValueError, match='512'):
        compile_initiatives([{'layers': {'resource_groups': [source]}}], _definition)


def test_initiative_plan_executes_no_mutations_and_has_one_assignment(tmp_path):
    tenant = {'connection_id': 2, 'subscription_name_by_id': {SUB: 'Test'}, 'layers': {'resource_groups': [
        item(1, {'field': 'type', 'equals': RG_TYPE}, {'projeto': 'A'}, 'rg-a'),
        item(1, {'field': 'type', 'equals': RG_TYPE}, {'projeto': 'A'}, 'rg-b')], 'resources': []}}
    script = build_script([tenant], plan_only=True)
    az = tmp_path/'az'; log = tmp_path/'calls'
    az.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$AZ_LOG"
if [[ "$1 $2" == "account show" ]]; then echo "$SUB"; exit 0; fi
if [[ "$1 $2 $3" == "role assignment list" ]]; then exit 0; fi
if [[ "$1 $2 $3" == "policy definition show" || "$1 $2 $3" == "policy set-definition show" || "$1 $2 $3" == "policy assignment show" ]]; then echo ResourceNotFound >&2; exit 1; fi
exit 99
'''); az.chmod(0o755)
    result = subprocess.run(['bash','-c',script],env={**os.environ,'IDENTITY_MODE':'system','AZ_LOG':str(log),'SUB':SUB,'PATH':f'{tmp_path}:'+os.environ['PATH']},text=True,capture_output=True)
    assert result.returncode == 0, result.stderr+result.stdout
    assert result.stdout.count('Assignment único') == 1
    assert result.stdout.count('Remediation ') == 1
    assert not any(word in log.read_text().split() for word in ['create','update','assign','delete','put','trigger-scan'])
    assert '"policyDefinitionReferenceId"' in script
    assert '--resource-count' not in script
    assert '/resourceGroups/rg-a' in script and '/resourceGroups/rg-b' in script


def test_export_reports_compacted_assignment_counts(monkeypatch):
    import asyncio
    import json
    from app.routers import azure_tag_policy as router
    tenant = {'connection_id': 2, 'selected_subscription_id': SUB, 'layers': {'resource_groups': [
        item(1, {'field':'type','equals':RG_TYPE}, {'projeto':'A'}, 'rg-a'),
        item(1, {'field':'type','equals':RG_TYPE}, {'projeto':'A'}, 'rg-b')], 'resources': []}}
    class Service:
        def __init__(self, db): pass
        async def list_azure_tenants(self): return [{'connection_id':2}]
        async def generate_tenant_policy(self, *_): return tenant
    monkeypatch.setattr(router,'AzureTagPolicyService',Service)
    response=asyncio.run(router.export_policy(connection_id=2, department_id=None, subscription_id=SUB,
                                               plan_only=True, fmt='both', db=object()))
    payload=json.loads(response.body)
    assert payload['deployment_model']=='initiative'
    summary=payload['initiatives'][0]
    assert summary['assignment_count']==1
    assert summary['definition_count']==1
    assert summary['original_assignment_count']==2
