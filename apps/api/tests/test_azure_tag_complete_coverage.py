"""Billing membership and original selectors survive inventory growth/failure."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models.rules import ClassificationRule, Department
from app.services.azure_tag_policy_service import AzureTagPolicyService, SCOPE_RG
from app.services.azure_tag_initiative import compile_initiatives
from app.routers.azure_tag_policy import _definition, build_script

SUB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def configured_service(monkeypatch, count=48):
    connection = SimpleNamespace(id=2, code='azure-ti', name='TI', cloud_scope='azure',
                                 cloud_name='Azure', config_json={'tenant_id': 'tenant'})

    class DB:
        async def get(self, *_):
            return connection

    service = AzureTagPolicyService(DB())
    rules = [ClassificationRule(id=1000+i, name=f'Project {i}', priority=i, department_id=2000+i,
                                conditions=[{'field': 'source_resource_group_name', 'operator': '=',
                                             'values': [f'rg-{i}', f'rg-future-{i}']}]) for i in range(count)]
    departments = {2000+i: Department(id=2000+i, name=f'Project {i}', parent_id=None,
                                      centro_custo='CC') for i in range(count)}
    evidence = [{'rule_id': 1000+i, 'subscription_id': SUB, 'resource_group_name': f'rg-{i}',
                 'evidence_count': 1} for i in range(count)]

    def returning(value):
        async def call(*_):
            return value
        return call

    for name, value in {
        '_load_classification_rules': rules, '_load_departments': departments,
        '_tag_mappings': [{'tag_key': 'projeto', 'focus_field': 'organograma_nivel_1'},
                          {'tag_key': 'centro-de-custos', 'focus_field': 'centro_custo'}],
        '_subscription_home_tenants': {SUB: 'tenant'},
        '_subscription_catalog': ({'Test': [SUB]}, [SUB], None),
        '_silver_billed_rg_evidence': (evidence, None),
    }.items():
        monkeypatch.setattr(service, name, returning(value))

    async def inventory_must_not_filter_billed_rules(*_):
        raise AssertionError('A current inventory cannot erase a historically billed business predicate')

    monkeypatch.setattr(service, '_azure_rule_targets', inventory_must_not_filter_billed_rules)
    return service, rules, departments, evidence


def test_all_48_billed_rules_survive_without_inventory_and_keep_future_exact_names(monkeypatch):
    service, rules, _, _ = configured_service(monkeypatch)
    doc = asyncio.run(service.generate_tenant_policy(2, subscription_id=SUB))
    assert doc['coverage']['complete']
    assert len(doc['coverage']['reconciliation']) == 48
    assert all(row['status'] == 'emitted' for row in doc['coverage']['reconciliation'])
    for layer in ('resources', 'resource_groups'):
        assert {item['rule_id'] for item in doc['layers'][layer]} == {r.id for r in rules}
        for row in doc['layers'][layer]:
            assert row['assignment_scope_kind'] == 'subscription'
            assert 'rg-future-' in json.dumps(row['base_selector'])
    bundle, = compile_initiatives([doc], _definition)
    assert len(bundle['definitions']) == 96
    assert len(bundle['members']) == 96


def test_cost_centre_inherits_nearest_ancestor_and_omits_only_unavailable_key():
    service = AzureTagPolicyService(None)
    departments = {
        1: Department(id=1, name='Root', parent_id=None, centro_custo='Grandparent'),
        2: Department(id=2, name='Parent', parent_id=1, centro_custo='ParentCC'),
        3: Department(id=3, name='Child', parent_id=2),
    }
    assert service._hierarchy(departments[3], departments)['centro_custo'] == 'ParentCC'
    departments[2].centro_custo = None
    assert service._hierarchy(departments[3], departments)['centro_custo'] == 'Grandparent'
    departments[1].centro_custo = None
    assert 'centro_custo' not in service._hierarchy(departments[3], departments)


def test_missing_cost_centre_keeps_classification_and_does_not_write_or_remove_key(monkeypatch):
    service, _, departments, _ = configured_service(monkeypatch, count=1)
    departments[2000].centro_custo = None
    doc = asyncio.run(service.generate_tenant_policy(2, subscription_id=SUB))
    assert doc['coverage']['complete']
    assert doc['coverage']['diagnostics'][0]['omitted_tags'] == ['centro-de-custos']
    bundle, = compile_initiatives([doc], _definition)
    for definition in bundle['definitions']:
        policy = definition['definition']['policyRule']
        assert 'centro-de-custos' not in json.dumps(policy)
        assert policy['then']['details']['operations'] == [
            {'operation': 'addOrReplace', 'field': "tags['projeto']", 'value': 'Project 0'}]


def test_unaccounted_billing_rule_blocks_entire_script_instead_of_partial_success(monkeypatch):
    service, _, _, evidence = configured_service(monkeypatch, count=1)
    evidence.append({**evidence[0], 'rule_id': 9999})
    doc = asyncio.run(service.generate_tenant_policy(2, subscription_id=SUB))
    assert not doc['coverage']['complete']
    assert doc['coverage']['blocked_rule_ids'] == [9999]
    with pytest.raises(ValueError, match='#9999'):
        build_script([doc])


@pytest.mark.parametrize('partial', [False, True])
def test_export_endpoint_refuses_incomplete_scripts_but_returns_json_diagnostics(monkeypatch, partial):
    from fastapi import HTTPException
    from app.routers import azure_tag_policy as router
    doc = {'connection_id': 2, 'selected_subscription_id': SUB,
           'coverage': {'complete': False, 'blocked_rule_ids': [9999], 'reconciliation': [
               {'rule_id': 9999, 'status': 'blocked', 'diagnostics': [{'reason': 'missing rule'}]}]},
           'layers': {'resource_groups': [], 'resources': []}}
    if partial:
        doc['layers']['resource_groups'] = [{'rule_id': 1, 'tags': {'projeto': 'A'},
            'scope_subscriptions': [SUB], 'condition': {'field': 'name', 'equals': 'rg-a'}}]

    class Service:
        def __init__(self, _db): pass
        async def list_azure_tenants(self): return [{'connection_id': 2}]
        async def generate_tenant_policy(self, *_): return doc

    monkeypatch.setattr(router, 'AzureTagPolicyService', Service)
    for fmt in ['both', 'script']:
        with pytest.raises(HTTPException) as error:
            asyncio.run(router.export_policy(connection_id=2, department_id=None, subscription_id=SUB,
                                              plan_only=True, fmt=fmt, db=None))
        assert error.value.status_code == 422 and '#9999' in error.value.detail
    response = asyncio.run(router.export_policy(connection_id=2, department_id=None, subscription_id=SUB,
                                               plan_only=True, fmt='json', db=None))
    body = json.loads(response.body)
    assert body['export_ready'] is False and body['deployment_script'] == ''


def test_evidence_dependent_tags_cannot_replace_exact_scope_with_an_unrelated_old_rg():
    service = AzureTagPolicyService(None)
    rule = ClassificationRule(id=1, name='Exact')
    result = service._concrete_items(
        rule, [{'scope': SCOPE_RG, 'field': 'name', 'operator': '=', 'values': ['rg-current']}],
        [{'subscription_id': SUB, 'resource_group_name': 'rg-old', 'evidence_count': 1}],
        {'organograma_nivel_1': 'A'}, [{'tag_key': 'projeto', 'focus_field': 'organograma_nivel_1'}],
        True, [], force_resource_group_scope=True)
    assert not result


def test_empty_billed_rg_does_not_erase_native_rule_for_future_resources():
    service = AzureTagPolicyService(None)
    rule = ClassificationRule(id=78, name='Prisma')
    diagnostics = []
    result = service._concrete_items(
        rule, [{'scope': SCOPE_RG, 'field': 'name', 'operator': '~', 'values': ['prisma']}],
        [{'subscription_id': SUB, 'resource_group_name': '', 'evidence_count': 1}],
        {'organograma_nivel_1': 'Prisma'}, [{'tag_key': 'projeto', 'focus_field': 'organograma_nivel_1'}],
        True, diagnostics, force_resource_group_scope=True, preserve_source_selectors=True)
    assert len(result) == 1
    assert result[0]['assignment_scope_kind'] == 'subscription'
    assert result[0]['terms'][0]['operator'] == '~'
    assert not diagnostics


@pytest.mark.parametrize('duplicate', [False, True])
def test_inventory_audit_orders_by_unique_id_and_rejects_duplicate_pages(monkeypatch, duplicate):
    import httpx
    from app.services import azure_identity, azure_resource_graph_service

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None

    async def headers(*_): return {}

    async def page(_client, _headers, query, subscriptions, token):
        assert query.endswith('| order by resource_id asc')
        assert subscriptions == [SUB]
        return ([{'resource_id': 'a' if duplicate or not token else 'b'}],
                'next' if not token else None, 1)

    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    monkeypatch.setattr(azure_identity, 'arm_headers', headers)
    monkeypatch.setattr(azure_resource_graph_service, '_post_query', page)
    rows, error = asyncio.run(AzureTagPolicyService(None)._azure_rule_targets(
        SimpleNamespace(config_json={}), SUB))
    if duplicate:
        assert not rows and 'inconsistente' in error
    else:
        assert len(rows) == 2 and error is None
