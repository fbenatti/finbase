import base64
import asyncio
import json
import os
import subprocess
from types import SimpleNamespace

from app.models.rules import ClassificationRule, Department
from app.routers import azure_tag_policy as azure_tag_policy_router
from app.routers.azure_tag_policy import _definition, _tag_drift_condition, build_script
from app.services.azure_tag_policy_service import (
    AzureTagPolicyService,
    SCOPE_RESOURCE,
    SCOPE_RG,
    SCOPE_SKU,
    SCOPE_SUBSCRIPTION,
)
from app.services import azure_resource_graph_service


def test_subscription_discovery_uses_lightweight_arm_list(monkeypatch):
    calls = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def fake_headers(_config):
        return {"Authorization": "Bearer test"}

    async def fake_get_json(_client, url, headers, params=None):
        calls.update(url=url, headers=headers, params=params)
        return [
            {"subscriptionId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "displayName": "Fundação", "state": "Enabled"},
            {"subscriptionId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "displayName": "Desativada", "state": "Disabled"},
        ]

    monkeypatch.setattr(azure_resource_graph_service, "arm_headers", fake_headers)
    monkeypatch.setattr(azure_resource_graph_service, "get_json", fake_get_json)
    result = asyncio.run(azure_resource_graph_service.discover_subscriptions({"tenant_id": "tenant"}))

    assert calls["url"] == "https://management.azure.com/subscriptions"
    assert calls["params"] == {"api-version": "2022-12-01"}
    assert result == [{
        "subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "subscription_name": "Fundação",
    }]


def test_tag_policy_subscription_selector_reads_azure_subscriptions_from_silver(monkeypatch):
    connection = SimpleNamespace(
        id=2, code="azure-ti", connector_type="azure_blob",
    )
    calls = []

    class FakeDB:
        async def get(self, *_args):
            return connection

    async def fake_execute(query, timeout_s=0):
        calls.append((query, timeout_s))
        return [
            {"subscription_id": "63dbc6fc-93d8-4ac1-9745-253f6b502240", "subscription_name": "Fundação Telefônica"},
            {"subscription_id": "63DBC6FC-93D8-4AC1-9745-253F6B502240", "subscription_name": "Fundação Telefônica"},
            {"subscription_id": "not-a-subscription", "subscription_name": "Linha inválida"},
            {"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "subscription_name": "Terra Dev"},
        ]

    from app.services import trino_service
    monkeypatch.setattr(trino_service, "execute", fake_execute)
    result = asyncio.run(AzureTagPolicyService(FakeDB()).list_subscriptions(2))

    assert result == [
        {"subscription_id": "63dbc6fc-93d8-4ac1-9745-253f6b502240", "name": "Fundação Telefônica"},
        {"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "name": "Terra Dev"},
    ]
    query, timeout_s = calls[0]
    assert "cloud_provider" not in query
    assert "cloud_origem" in query and "'azure-ti'" in query
    assert "source_connection_id" in query and "= 2" in query
    assert "max_by" in query
    assert timeout_s == 60


def test_dashboard_refresh_queues_resource_graph_collection_for_selected_connection(monkeypatch):
    import app.services.worker_runtime as worker_runtime
    import app.services.task_runner as task_runner
    connection = SimpleNamespace(id=2, connector_type="azure_blob", cloud_scope="azure", code="azure-ti")
    selection = SimpleNamespace(subscription_ids="63dbc6fc-93d8-4ac1-9745-253f6b502240")

    class FakeResult:
        def scalar_one_or_none(self): return selection
        def scalars(self): return self
        def all(self): return []

    class FakeDB:
        def __init__(self): self.task = None
        async def get(self, *_args): return connection
        async def execute(self, *_args): return FakeResult()
        def add(self, task): self.task = task
        async def flush(self): self.task.id = 987
        async def commit(self): pass

    db = FakeDB()
    monkeypatch.setattr(worker_runtime, "any_worker_healthy", lambda: True)
    monkeypatch.setattr(task_runner, "_inmemory_tasks", {})
    result = asyncio.run(azure_tag_policy_router.refresh_executive_tag_dashboard(2, db))

    assert result["task_id"] == 987
    assert result["status"] == "QUEUED"
    assert db.task.result_json["source_key"] == "resource_graph"
    assert db.task.result_json["subscription_ids"] == ["63dbc6fc-93d8-4ac1-9745-253f6b502240"]


def test_export_returns_explanation_instead_of_400_when_subscription_has_no_policy_targets(monkeypatch):
    subscription = "63dbc6fc-93d8-4ac1-9745-253f6b502240"

    class FakeService:
        def __init__(self, _db):
            pass

        async def list_azure_tenants(self):
            return [{"connection_id": 2, "name": "Azure TI", "code": "azure-ti"}]

        async def generate_tenant_policy(self, *_args):
            return {
                "connection_id": 2, "connection_code": "azure-ti", "connection_name": "Azure TI",
                "selected_subscription_id": subscription, "deployment_subscriptions": [],
                "layers": {"resource_groups": [], "resources": []},
                "coverage": {"diagnostics": [{"status": "warning", "reason": "Sem alvos ARM atuais"}]},
            }

    monkeypatch.setattr(azure_tag_policy_router, "AzureTagPolicyService", FakeService)
    response = asyncio.run(azure_tag_policy_router.export_policy(
        connection_id=2, department_id=None, subscription_id=subscription,
        plan_only=False, fmt="both", db=object(),
    ))

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["tenants"][0]["selected_subscription_id"] == subscription
    assert payload["deployment_script"] == ""
    assert "Sem alvos ARM atuais" in payload["tenants"][0]["coverage"]["diagnostics"][0]["reason"]


def test_explicit_subscription_expands_historical_silver_rg_to_rg_scoped_policy(monkeypatch):
    selected = "63dbc6fc-93d8-4ac1-9745-253f6b502240"
    other = "71cc41f4-8e37-4c60-abbb-b9003b91c554"
    connection = SimpleNamespace(
        id=2, code="azure-ti", name="Azure TI", cloud_scope="azure", cloud_name="Azure TI",
        config_json={"tenant_id": "78879b00-f0e9-486c-836f-56f314e47e7f"},
    )

    class FakeDB:
        async def get(self, *_args):
            return connection

    service = AzureTagPolicyService(db=FakeDB())
    rule = _rule(id=52, priority=90, department_id=10,
                 conditions=[{"field": "SubAccountName", "operator": "=", "values": ["Fundação"]}])
    department = Department(id=10, name="Fundação Telefônica", parent_id=None, centro_custo="29TN161700")

    async def load_departments():
        return {10: department}

    async def load_rules(_conn):
        return [rule]

    async def tag_mappings():
        return [{"tag_key": "projeto", "focus_field": "organograma_nivel_1"}]

    async def home_tenants(_conn):
        return {selected: connection.config_json["tenant_id"], other: connection.config_json["tenant_id"]}

    async def silver_evidence_must_not_run(_conn, _mappings):
        raise AssertionError("selected subscription must use all-period RG evidence, not latest-period evidence")

    async def billed_rg_evidence(_conn, _subscription, _mappings):
        return ([{"rule_id": 52, "subscription_id": selected,
                  "resource_group_name": name, "evidence_count": 3}
                 for name in ("rg-selected-a", "rg-selected-b")], None)

    async def subscription_catalog(_conn):
        return ({"fundacao": [selected], "production": [other]}, [selected, other], None)

    async def azure_targets(_conn, subscription):
        return ([{
            "subscription_id": subscription,
            "resource_group_name": name,
            "resource_name": name,
            "resource_type": "Microsoft.Resources/subscriptions/resourceGroups",
            "resource_id": f"/subscriptions/{subscription}/resourceGroups/{name}",
        } for name in ("rg-selected-a", "rg-selected-b")], None)

    monkeypatch.setattr(service, "_load_departments", load_departments)
    monkeypatch.setattr(service, "_load_classification_rules", load_rules)
    monkeypatch.setattr(service, "_tag_mappings", tag_mappings)
    monkeypatch.setattr(service, "_subscription_home_tenants", home_tenants)
    monkeypatch.setattr(service, "_silver_evidence", silver_evidence_must_not_run)
    monkeypatch.setattr(service, "_silver_billed_rg_evidence", billed_rg_evidence)
    monkeypatch.setattr(service, "_subscription_catalog", subscription_catalog)
    monkeypatch.setattr(service, "_azure_rule_targets", azure_targets)
    monkeypatch.setattr(service, "_translate_conditions", lambda *_args, **_kwargs: [
        {"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["rg-"]},
    ])

    result = asyncio.run(service.generate_tenant_policy(2, subscription_id=selected))

    assert result["selected_subscription_id"] == selected
    assert result["deployment_subscriptions"] == [selected]
    script = build_script([result])
    assert f'ensure_assignment "$ACTIVE_ASSIGNMENT_NAME" /subscriptions/{selected} "$ACTIVE_POLICY_NAME"' in script
    assert f'ensure_assignment "$ACTIVE_ASSIGNMENT_NAME" /subscriptions/{selected}/resourceGroups/' not in script
    assert all(
        item["scope_subscriptions"] == [selected]
        and item["assignment_scope_kind"] == "subscription"
        and item["assignment_resource_group"] is None
        and item["observed"]["resource_group_name"] == "rg-"
        and len(item["condition"]["allOf"]) > 1
        for layer in result["layers"].values() for item in layer
    )
    assert all(len(items) == 1 for items in result["layers"].values())


def test_selected_subscription_groups_contains_rules_but_keeps_exact_rules_at_resource_group(monkeypatch):
    selected = "63dbc6fc-93d8-4ac1-9745-253f6b502240"
    connection = SimpleNamespace(
        id=2, code="azure-ti", name="Azure TI", cloud_scope="azure", cloud_name="Azure TI",
        config_json={"tenant_id": "tenant"},
    )

    class FakeDB:
        async def get(self, *_args):
            return connection

    service = AzureTagPolicyService(db=FakeDB())
    prisma = _rule(id=78, priority=9, department_id=234, name="Classifica → Prisma",
                   conditions=[{"field": "source_resource_group_name", "operator": "~",
                                "values": ["PCCAGENTLESSSCANRESOURCEGROUP", "dig-security-rg"]}])
    foundation = _rule(id=52, priority=90, department_id=73, name="Classifica → Fundação Telefônica",
                       conditions=[{"field": "SubAccountName", "operator": "=",
                                    "values": ["Fundação Telefonica"]}])
    other_subscription = _rule(
        id=5460, priority=0, department_id=216, name="Framework Brasil - outras subscriptions",
        conditions=[
            {"field": "source_resource_group_name", "operator": "=", "values": ["PCCAGENTLESSSCANRESOURCEGROUP"]},
            {"field": "SubAccountName", "operator": "=", "values": [
                "Microsoft Azure 28 - EA - FRAMEWORK BRASIL - PRO",
                "Microsoft Azure 38 - EA - FRAMEWORK BRASIL - PRE",
            ]},
        ],
    )
    departments = {
        1: Department(id=1, name="DIR FUNDACAO TELEFONICA", parent_id=None),
        73: Department(id=73, name="fundacao telefonica", parent_id=1, centro_custo="29TN161700"),
        234: Department(id=234, name="Prisma", parent_id=1, centro_custo="29TN161700"),
        216: Department(id=216, name="Framework Brasil", parent_id=1, centro_custo="29TN161700"),
    }
    targets = [
        {"subscription_id": selected, "resource_group_name": name, "resource_name": name,
         "resource_type": "Microsoft.Resources/subscriptions/resourceGroups",
         "resource_id": f"/subscriptions/{selected}/resourceGroups/{name}"}
        for name in ["PCCAgentlessScanResourceGroup", "dig-security-rg", "rg-common"]
    ]

    async def load_departments(): return departments
    async def load_rules(_conn): return [other_subscription, prisma, foundation]
    async def tag_mappings():
        return [
            {"tag_key": "projeto", "focus_field": "organograma_nivel_2"},
            {"tag_key": "diretoria", "focus_field": "organograma_nivel_1"},
            {"tag_key": "centro-de-custos", "focus_field": "centro_custo"},
        ]
    async def home_tenants(_conn): return {selected: "tenant"}
    async def subscription_catalog(_conn):
        return ({"Fundação Telefônica": [selected]}, [selected], None)
    async def azure_targets(_conn, _subscription): return (targets, None)
    async def billed_rg_evidence(_conn, _subscription, _mappings):
        return ([
            {"rule_id": 52, "subscription_id": selected, "resource_group_name": "rg-common", "evidence_count": 4},
            {"rule_id": 78, "subscription_id": selected,
             "resource_group_name": "PCCAgentlessScanResourceGroup", "evidence_count": 2},
            {"rule_id": 78, "subscription_id": selected,
             "resource_group_name": "dig-security-rg", "evidence_count": 1},
        ], None)

    monkeypatch.setattr(service, "_load_departments", load_departments)
    monkeypatch.setattr(service, "_load_classification_rules", load_rules)
    monkeypatch.setattr(service, "_tag_mappings", tag_mappings)
    monkeypatch.setattr(service, "_subscription_home_tenants", home_tenants)
    monkeypatch.setattr(service, "_subscription_catalog", subscription_catalog)
    monkeypatch.setattr(service, "_azure_rule_targets", azure_targets)
    monkeypatch.setattr(service, "_silver_billed_rg_evidence", billed_rg_evidence)

    result = asyncio.run(service.generate_tenant_policy(2, subscription_id=selected))

    for layer in ("resource_groups", "resources"):
        policies = result["layers"][layer]
        assert {policy["rule_id"] for policy in policies} == {52, 78}
        foundation_policy = next(policy for policy in policies if policy["rule_id"] == 52)
        prisma_policy = next(policy for policy in policies if policy["rule_id"] == 78)
        assert foundation_policy["assignment_scope_kind"] == "subscription"
        assert foundation_policy["assignment_resource_group"] is None
        assert foundation_policy["observed"] == {}
        assert foundation_policy["excludes_rule_ids"] == [78]
        assert prisma_policy["assignment_scope_kind"] == "subscription"
        assert prisma_policy["assignment_resource_group"] is None
        assert prisma_policy["observed"]["resource_group_name"] == "PCCAGENTLESSSCANRESOURCEGROUP · dig-security-rg"
        assert len(policies) == 2
        assert foundation_policy["tags"] == {
            "projeto": "fundacao telefonica",
            "diretoria": "DIR FUNDACAO TELEFONICA",
            "centro-de-custos": "29TN161700",
        }
        assert "contains" in json.dumps(prisma_policy["base_selector"], ensure_ascii=False)
        assert "not" in json.dumps(foundation_policy["condition"], ensure_ascii=False)
    assert result["deployment_subscriptions"] == [selected]
    script = build_script([result])
    assert script.count(f'ensure_assignment "$ACTIVE_ASSIGNMENT_NAME" /subscriptions/{selected} "$ACTIVE_POLICY_NAME"') == 4
    assert f'ensure_assignment "$ACTIVE_ASSIGNMENT_NAME" /subscriptions/{selected}/resourceGroups/' not in script


def test_subscription_only_policy_requires_billed_current_resource_group():
    service = AzureTagPolicyService(db=None)
    rule = _rule(id=52, priority=90, department_id=10)
    diagnostics = []
    items = service._concrete_items(
        rule,
        [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=",
          "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}],
        [{"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "evidence_count": 1}],
        {"organograma": "Fundação"},
        [{"tag_key": "projeto", "focus_field": "organograma"}],
        True,
        diagnostics,
        force_resource_group_scope=True,
    )
    assert items == []
    assert diagnostics[0]["status"] == "blocked"


def _rule(**values):
    data = {
        "id": 1, "name": "Regra", "department_id": 10, "priority": 10,
        "cloud_scope": "azure", "source_field": "", "pattern": "", "pattern_type": "multi_condition",
        "match_value": "Projeto", "target_field": "organograma", "active": True,
        "conditions": [{"field": "SubAccountId", "operator": "=", "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}],
    }
    data.update(values)
    return ClassificationRule(**data)


def test_arm_field_classification_and_resource_id():
    service = AzureTagPolicyService(db=None)
    assert service._field_scope("source_resource_group_name")[0] == SCOPE_RG
    assert service._field_scope("ResourceId") == (SCOPE_RESOURCE, "id")
    assert service._field_scope("SubAccountName")[0] == SCOPE_SUBSCRIPTION


def test_resource_group_selector_applies_to_rg_and_children():
    service = AzureTagPolicyService(db=None)
    terms = [{"scope": SCOPE_RG, "field": "name", "operator": "=", "values": ["rg-terra"]}]
    rg = service._selector(terms, "resource_group")
    resource = service._selector(terms, "resource")
    assert rg["allOf"][0] == {"field": "type", "equals": "Microsoft.Resources/subscriptions/resourceGroups"}
    assert rg["allOf"][1] == {"field": "name", "equals": "rg-terra"}
    assert resource["allOf"][1]["not"]["allOf"] == [
        {"field": "type", "equals": "Microsoft.Sql/servers/databases"},
        {"field": "name", "like": "*/master"},
    ]
    assert resource["allOf"][2] == {"value": "[resourceGroup().name]", "equals": "rg-terra"}


def test_rg_selector_omits_subscription_id_field():
    service = AzureTagPolicyService(db=None)
    terms = [
        {"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["354cc2f6-cbad-45aa-adea-045fb4602f79"]},
        {"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["dig-security-rg"]},
    ]
    rg = service._selector(terms, "resource_group")
    resource = service._selector(terms, "resource")
    dumped = json.dumps(rg) + json.dumps(resource)
    assert "subscriptionId" not in dumped
    assert {"field": "name", "contains": "dig-security-rg"} in rg["allOf"]


def test_multiple_resource_groups_create_valid_value_alternatives():
    service = AzureTagPolicyService(db=None)
    terms = [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["rg-a", "rg-b"]}]
    resource = service._selector(terms, "resource")
    alternatives = resource["allOf"][2]["anyOf"]
    assert alternatives == [
        {"value": "[resourceGroup().name]", "contains": "rg-a"},
        {"value": "[resourceGroup().name]", "contains": "rg-b"},
    ]


def test_resource_id_never_tags_the_resource_group():
    service = AzureTagPolicyService(db=None)
    terms = [{"scope": SCOPE_RESOURCE, "field": "id", "operator": "~", "values": ["vhub-tcn-mx"]}]
    assert service._selector(terms, "resource_group") is None
    selector = service._selector(terms, "resource")
    assert selector["allOf"][2] == {"field": "id", "contains": "vhub-tcn-mx"}


def test_lower_priority_rule_contains_explicit_exception():
    service = AzureTagPolicyService(db=None)
    mexico = {"field": "id", "contains": "mexico"}
    brasil = {"field": "subscriptionId", "equals": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
    effective = service._effective(brasil, [mexico])
    assert effective == {"allOf": [brasil, {"not": mexico}]}


def test_rules_in_different_observed_subscriptions_are_not_exceptions():
    service = AzureTagPolicyService(db=None)
    assert service._scopes_disjoint(["sub-terra-brasil"], ["sub-datamesh"])
    assert not service._scopes_disjoint(["sub-shared"], ["sub-shared", "sub-mexico"])


def test_disjoint_rg_terms_are_not_exceptions():
    service = AzureTagPolicyService(db=None)
    terra = [{"scope": SCOPE_RG, "field": "name", "operator": "=", "values": ["rg-terra-brasil"]}]
    datamesh = [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["DTMN"]}]
    assert service._is_disjoint_by_arm_terms(terra, datamesh)


def test_different_arm_dimensions_remain_conservative_exception_candidates():
    service = AzureTagPolicyService(db=None)
    subscription = [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["sub-shared"]}]
    resource = [{"scope": SCOPE_RESOURCE, "field": "id", "operator": "~", "values": ["terra-mexico"]}]
    assert not service._is_disjoint_by_arm_terms(subscription, resource)


def test_gold_resource_id_in_another_rg_is_not_a_prisma_exception():
    service = AzureTagPolicyService(db=None)
    mexico = {"resource_id": "/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/rg-network-tcn/providers/Microsoft.Network/azureFirewalls/fw-mexico"}
    prisma = {"resource_group_name": "pccagentlessscanresourcegroup"}
    assert service._observed_targets_disjoint(mexico, prisma)


def test_financial_rules_are_reported_not_emitted():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    result = service._translate_conditions(
        _rule(conditions=[{"field": "ChargeCategory", "operator": "=", "values": ["Purchase"]}]), {}, diagnostics,
    )
    assert result is None
    assert diagnostics[0]["status"] == "not_deployable"
    assert "ChargeCategory" in diagnostics[0]["reason"]


def test_subscription_name_requires_unambiguous_catalog_match():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    result = service._translate_conditions(
        _rule(conditions=[{"field": "SubAccountName", "operator": "=", "values": ["Terra Brasil"]}]),
        {"terra brasil": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}, diagnostics,
    )
    assert result[0]["values"] == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]


def test_resource_id_limits_assignment_to_its_subscription():
    service = AzureTagPolicyService(db=None)
    scopes = service._explicit_scope_subscriptions([{
        "scope": SCOPE_RESOURCE, "field": "id", "operator": "=",
        "values": ["/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm"],
    }])
    assert scopes == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]


def test_gold_rg_contains_keeps_classification_pattern():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    items = service._concrete_items(
        _rule(conditions=[{"field": "source_resource_group_name", "operator": "~", "values": ["dig-security-rg"]}]),
        [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["dig-security-rg"]}],
        [
            {
                "subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource_group_name": "dig-security-rg-3u2ql5dvf4su4-1162383220130775040-use1",
                "evidence_count": 4,
            },
            {
                "subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource_group_name": "dig-security-rg-other-suffix-use2",
                "evidence_count": 3,
            },
        ],
        {"centro_custo": "CC"}, True, diagnostics,
    )
    assert len(items) == 1
    assert items[0]["observed"] == {"resource_group_name": "dig-security-rg"}
    assert items[0]["evidence_count"] == 7
    rg_term = next(term for term in items[0]["terms"] if term["scope"] == SCOPE_RG)
    assert rg_term == {"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["dig-security-rg"]}
    selector = service._selector(items[0]["terms"], "resource_group")
    assert {"field": "name", "contains": "dig-security-rg"} in selector["allOf"]
    indexed = service._selector(items[0]["terms"], "resource")
    assert {"value": "[resourceGroup().name]", "contains": "dig-security-rg"} in indexed["allOf"]


def test_gold_resource_id_contains_keeps_pattern_on_its_subscription():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    resource_id = "/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/rg-tcn/providers/Microsoft.Network/azureFirewalls/azurefirewall_vhub-tcn-mx-eastus2"
    items = service._concrete_items(
        _rule(conditions=[{"field": "ResourceId", "operator": "~", "values": ["terra-mexico"]}]),
        [{"scope": SCOPE_RESOURCE, "field": "id", "operator": "~", "values": ["terra-mexico"]}],
        [{
            "subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "resource_group_name": "rg-tcn",
            "resource_id": resource_id,
            "evidence_count": 2,
        }],
        {"centro_custo": "MEX"}, True, diagnostics,
    )
    assert items[0]["scope_subscriptions"] == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
    assert items[0]["targets"] == ("resource",)
    resource_term = next(term for term in items[0]["terms"] if term["scope"] == SCOPE_RESOURCE)
    assert resource_term["operator"] == "~"
    assert resource_term["values"] == ["terra-mexico"]
    selector = service._selector(items[0]["terms"], "resource")
    assert {"field": "id", "contains": "terra-mexico"} in selector["allOf"]
    assert service._selector(items[0]["terms"], "resource_group") is None


def test_sku_contains_is_emitted_as_azure_policy_contains():
    service = AzureTagPolicyService(db=None)
    terms = [
        {"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
        {"scope": SCOPE_SKU, "field": "Microsoft.Compute/virtualMachines/sku.name", "operator": "~", "values": ["Dsv5"]},
    ]
    selector = service._selector(terms, "resource")
    assert {"field": "Microsoft.Compute/virtualMachines/sku.name", "contains": "Dsv5"} in selector["allOf"]


def test_gold_rg_contains_collapses_case_and_suffix_variants():
    service = AzureTagPolicyService(db=None)
    mixed = "MC_RG-AKS-KCLI-BRSOUTH-PROD_AKS-KCLI-BRSOUTH-PROD_BRAZILSOUTH"
    lower = "mc_rg-aks-kcli-brsouth-prod_aks-kcli-brsouth-prod_brazilsouth"
    items = service._concrete_items(
        _rule(id=5220, name="kcli", conditions=[{"field": "source_resource_group_name", "operator": "~", "values": ["kcli"]}]),
        [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["kcli"]}],
        [
            {"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "resource_group_name": lower, "evidence_count": 3},
            {"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "resource_group_name": mixed, "evidence_count": 3},
        ],
        {"centro_custo": "CC"}, True, [],
    )
    assert len(items) == 1
    assert items[0]["observed"]["resource_group_name"] == "kcli"
    assert items[0]["evidence_count"] == 6
    rg_term = next(term for term in items[0]["terms"] if term["scope"] == SCOPE_RG)
    assert rg_term["operator"] == "~"
    assert rg_term["values"] == ["kcli"]
    selector = service._selector(items[0]["terms"], "resource")
    assert {"value": "[resourceGroup().name]", "contains": "kcli"} in selector["allOf"]


def test_subscription_name_contains_is_not_used():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    result = service._translate_conditions(
        _rule(conditions=[{"field": "SubAccountName", "operator": "~", "values": ["Terra"]}]),
        {"terra brasil": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}, diagnostics,
    )
    assert result is None
    assert diagnostics[0]["reason"] == "Assinatura por nome exige igualdade exata"


def test_gold_resource_id_case_variants_collapse_to_one_equals():
    service = AzureTagPolicyService(db=None)
    lower = "/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/rg-tcn/providers/Microsoft.Network/azureFirewalls/fw"
    mixed = "/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/RG-TCN/providers/Microsoft.Network/azureFirewalls/fw"
    items = service._concrete_items(
        _rule(conditions=[{"field": "ResourceId", "operator": "=", "values": [lower]}]),
        [{"scope": SCOPE_RESOURCE, "field": "id", "operator": "=", "values": [lower]}],
        [
            {"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "resource_id": lower, "evidence_count": 1},
            {"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "resource_id": mixed, "evidence_count": 2},
        ],
        {"centro_custo": "CC"}, True, [],
    )
    assert len(items) == 1
    assert items[0]["observed"]["resource_id"] == mixed


def test_add_or_replace_collapses_tag_key_case_not_values():
    from app.services.azure_tag_policy_service import _collapse_tag_keys
    tags = _collapse_tag_keys({"Ambiente": "Prod", "ambiente": "prod", "projeto": "Terra"})
    assert list(tags) == ["Ambiente", "projeto"]
    assert tags["Ambiente"] == "Prod"


def test_hierarchy():
    service = AzureTagPolicyService(db=None)
    root = Department(id=1, name="TI", centro_custo="CC1", ambiente="Prod")
    project = Department(id=2, name="Terra Brasil", parent_id=1)
    tags = service._hierarchy(project, {1: root, 2: project})
    assert tags["organograma_nivel_1"] == "TI"
    assert tags["organograma_nivel_2"] == "Terra Brasil"
    assert tags["centro_custo"] == "CC1"


def test_tag_mapping_uses_global_key_and_focus_field_value():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    items = service._concrete_items(
        _rule(),
        [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["ignored"]}],
        [{"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "evidence_count": 1,
          "tag_value_0": "Terra Brasil", "tag_value_1": "Financeiro"}],
        {"organograma_nivel_5": "Terra Brasil"},
        [{"tag_key": "projeto", "focus_field": "organograma_nivel_5"},
         {"tag_key": "area", "focus_field": "billing_account_name"}],
        True, diagnostics,
    )
    assert items[0]["tags"] == {"projeto": "Terra Brasil", "area": "Financeiro"}


def test_deployment_script_uses_identity_rbac_and_stable_names():
    script = build_script([{
        "tenant_id": "11111111-1111-1111-1111-111111111111", "connection_code": "azure-ti", "connection_name": "Azure TI", "department_name": "Terra Brasil",
        "subscription_name_by_id": {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": "Terra-Mexico-Test"},
        "deployment_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "layers": {"resource_groups": [{"rule_id": 1, "rule_name": "Terra Brasil", "tags": {"centro_custo": "CC"},
                                             "condition": {"field": "name", "equals": "rg-terra"},
                                             "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
                                             "scope_tenant_id": "11111111-1111-1111-1111-111111111111"}], "resources": []},
    }])
    assert "--mi-system-assigned" in script
    assert 'role "Tag Contributor"' in script
    assert "az policy remediation create" in script
    assert "test-terra-brasil" not in script
    assert "resolve_existing_target" in script
    assert "openfinopsContentHash" in script
    assert "ensure_tenant" not in script
    assert "--query tenantId" not in script
    assert 'az policy remediation create --name "$name" --subscription "$subscription"' in script
    assert script.count("--resource-count 50000") == 2
    assert 'az policy remediation show --name "$name" --subscription "$subscription"' in script
    assert "# Subscription alvo: Terra-Mexico-Test (aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa)" in script


def test_deployment_script_uses_resource_home_tenant_not_billing_tenant():
    billing = "78879b00-f0e9-486c-836f-56f314e47e7f"
    home = "0fce1554-ffcc-4e69-aa8d-9334eb2d8fd7"
    finops = "354cc2f6-cbad-45aa-adea-045fb4602f79"
    script = build_script([{
        "tenant_id": billing, "connection_code": "azure-ti", "connection_name": "Azure TI",
        "layers": {"resource_groups": [{"rule_id": 3028, "rule_name": "FinOps", "tags": {"projeto": "FinOps"},
                                             "condition": {"field": "name", "equals": "rg"},
                                             "scope_subscriptions": [finops],
                                             "scope_tenant_id": home}], "resources": []},
    }])
    assert f"# === Tenant de recursos {home} ===" in script
    assert "não valida nem solicita login por tenant" in script
    assert "az login --tenant" not in script


def test_deployment_script_groups_4p_and_ti_tenant_blocks():
    billing = "78879b00-f0e9-486c-836f-56f314e47e7f"
    four_p = "0fce1554-ffcc-4e69-aa8d-9334eb2d8fd7"
    finops = "354cc2f6-cbad-45aa-adea-045fb4602f79"
    terra = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    script = build_script([{
        "tenant_id": billing, "connection_code": "azure-ti", "connection_name": "Azure TI",
        "subscription_tenants": {finops: four_p, terra: billing},
        "layers": {"resource_groups": [
            {"rule_id": 3028, "rule_name": "FinOps", "tags": {"projeto": "FinOps"},
             "condition": {"field": "name", "contains": "rg"},
             "scope_subscriptions": [finops], "scope_tenant_id": four_p},
            {"rule_id": 21, "rule_name": "Terra Brasil", "tags": {"projeto": "Terra"},
             "condition": {"field": "name", "equals": "rg-terra"},
             "scope_subscriptions": [terra], "scope_tenant_id": billing},
        ], "resources": []},
    }])
    assert script.index(f"# === Tenant de recursos {four_p} ===") < script.index(f"# === Tenant de recursos {billing} ===")
    assert "az login --tenant" not in script
    assert "EXPECTED_SUBSCRIPTION=" + finops in script or f"EXPECTED_SUBSCRIPTION={finops}" in script
    assert "addOrReplace" in script
    assert "az policy definition update" in script
    assert "az policy assignment identity assign" in script
    assert "metadata.category" in script
    assert "RETRY_ATTEMPTS" in script
    assert "PLAN_ONLY" in script
    assert 'PLAN_ONLY="false"' in script
    assert "openfinopsContentHash" in script
    assert '--rules "@$rules"' in script
    assert '--metadata "${metadata_args[@]}"' in script
    assert "'openfinopsSourceRule=#3028 FinOps'" in script
    assert "az policy state trigger-scan --no-wait" in script
    assert "trigger-scan --resource" not in script
    assert "grep -q" not in script
    assert "|| true" not in script
    assert "Há $matches assignments OpenFinops candidatos" in script
    assert 'az rest --method get' in script
    assert "contains(properties.policyDefinitionId" in script
    assert "contains(properties.policyDefinitionId, \\`of-def-\\`)" in script
    assert "az policy definition delete" not in script
    assert "az policy assignment delete" not in script
    assert "az policy remediation delete" not in script
    assert "az role assignment delete" not in script
    assert "az policy remediation create" in script
    assert 'name="of-rem-$(date -u +%Y%m%d%H%M%S)-${REMEDIATION_SEQ}"' in script
    assert 'action UNCHANGED "Remediation' not in script
    assert 'action CREATE "Remediation $name' in script


def test_deployment_script_can_default_to_plan_only():
    script = build_script([{
        "connection_code": "azure", "connection_name": "Azure",
        "layers": {"resource_groups": [{"rule_id": 1, "rule_name": "Regra", "tags": {"projeto": "Terra"},
                                             "condition": {"field": "name", "equals": "rg"},
                                             "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}], "resources": []},
    }], plan_only=True)
    assert 'PLAN_ONLY="true"' in script
    assert 'WAIT_FOR_REMEDIATION="${WAIT_FOR_REMEDIATION:-false}"' in script
    assert 'SKIP_REMEDIATION="${SKIP_REMEDIATION:-false}"' in script
    assert 'if [[ "$SKIP_REMEDIATION" == "true" ]]; then' in script
    assert 'action ABORT "Subscription ativa' in script
    assert 'action UNCHANGED "Definition $name"' in script
    assert 'action UPDATE "Definition OpenFinops $name"' in script
    assert 'action CREATE "Definition OpenFinops $name"' in script
    assert 'plan || run_mutation az policy definition update' in script
    assert 'action CREATE "Trigger scan para $scope (não executado em PLAN)"' in script
    assert 'action CREATE "Remediation $name' in script
    assert 'action UNCHANGED "Remediation' not in script
    assert 'name="of-rem-$(date -u +%Y%m%d%H%M%S)-${REMEDIATION_SEQ}"' in script
    assert 'resolve_assignment_principal' in script
    assert 'RBAC Tag Contributor em $scope (managed identity pendente)' in script


def test_deployment_script_emits_both_layers_with_content_hash():
    script = build_script([{
        "connection_code": "azure", "connection_name": "Azure",
        "layers": {
            "resource_groups": [{
                "rule_id": 1, "rule_name": "Regra", "tags": {"projeto": "Terra"},
                "condition": {"field": "name", "equals": "rg"},
                "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            }],
            "resources": [{
                "rule_id": 1, "rule_name": "Regra", "tags": {"projeto": "Terra"},
                "condition": {"field": "name", "equals": "rg"},
                "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            }],
        },
    }])
    assert script.count("openfinopsContentHash") >= 2
    assert '"mode": "All"' in script or "All" in script
    assert "Indexed" in script
    assert "addOrReplace" in script


def test_policy_definition_has_stable_content_hash():
    args = ("OpenFinops", "Descrição", {"field": "name", "equals": "rg"}, {"projeto": "Terra"}, "All", "#1 Regra", [])
    first = _definition(*args)
    second = _definition(*args)
    assert first["metadata"]["openfinopsContentHash"] == second["metadata"]["openfinopsContentHash"]
    assert len(first["metadata"]["openfinopsContentHash"]) == 64


def test_tag_drift_condition_only_selects_missing_or_exact_value_mismatch():
    region = {"field": "type", "equals": "Microsoft.Compute/virtualMachines"}
    condition = _tag_drift_condition(region, {
        "projeto": "Fundação Telefonica",
        "diretoria": "DIR FUNDACAO TELEFONICA",
    })
    assert condition["allOf"][0] == region
    drift = condition["allOf"][1]["anyOf"]
    assert drift[0]["anyOf"][0] == {"field": "tags['projeto']", "exists": "false"}
    assert drift[0]["anyOf"][1]["notMatch"] == base64.b64encode("Fundação Telefonica".encode()).decode()
    assert drift[0]["anyOf"][1]["value"] == "[base64(string(field('tags[''projeto'']')))]"
    assert drift[1]["anyOf"][0]["field"] == "tags['diretoria']"


def test_tag_drift_condition_escapes_apostrophes_in_dynamic_field():
    condition = _tag_drift_condition({"field": "type", "equals": "T"}, {"owner's team": "Vivo's"})
    encoded_field = condition["allOf"][1]["anyOf"][0]["anyOf"][1]["value"]
    assert encoded_field == "[base64(string(field('tags[''owner''s team'']')))]"


def test_definition_uses_drift_condition_and_keeps_modify_operations():
    policy = _definition("OpenFinops", "Regra", {"field": "name", "equals": "rg"},
                         {"projeto": "Terra"}, "All", "#1 Regra", [])
    policy_rule = policy["policyRule"]
    assert policy_rule["if"]["allOf"][0] == {"field": "name", "equals": "rg"}
    assert policy_rule["if"]["allOf"][1]["anyOf"][0]["anyOf"][0]["exists"] == "false"
    assert policy_rule["then"]["effect"] == "modify"
    assert policy_rule["then"]["details"]["operations"] == [
        {"operation": "addOrReplace", "field": "tags['projeto']", "value": "Terra"},
    ]


def test_rg_assignment_does_not_embed_other_rg_exclusions():
    service = AzureTagPolicyService(db=None)
    assigned = {
        "assignment_scope_kind": "resource_group",
        "assignment_resource_group": "azure-ti-test-fundacao-telefonica-1",
        "tags": {"projeto": "fundacao telefonica"},
        "observed": {"resource_group_name": "azure-ti-test-fundacao-telefonica-1"},
        "terms": [
            {"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
            {"scope": SCOPE_RG, "field": "name", "operator": "=", "values": ["azure-ti-test-fundacao-telefonica-1"]},
        ],
    }
    prisma = {
        "tags": {"projeto": "Prisma"},
        "observed": {"resource_group_name": "PCCAGENTLESSSCANRESOURCEGROUP · dig-security-rg"},
        "terms": [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["PCCAGENTLESSSCANRESOURCEGROUP", "dig-security-rg"]}],
        "summary": {"rule_id": 78, "rule_name": "Prisma"},
    }
    assert service._previous_overlap_reason(assigned, prisma) == "assignment_scope_disjoint"
    selector = service._selector(assigned["terms"], "resource_group", omit_rg_scope=True)
    effective = service._effective(selector, [])
    assert effective == {"field": "type", "equals": "Microsoft.Resources/subscriptions/resourceGroups"}
    assert "PCCAGENTLESSSCANRESOURCEGROUP" not in json.dumps(effective)


def test_subscription_policy_still_subtracts_prisma_contains():
    service = AzureTagPolicyService(db=None)
    subscription_rule = {
        "assignment_scope_kind": "subscription",
        "assignment_resource_group": None,
        "tags": {"projeto": "fundacao telefonica"},
        "observed": {},
        "terms": [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}],
    }
    prisma = {
        "tags": {"projeto": "Prisma"},
        "observed": {"resource_group_name": "PCCAGENTLESSSCANRESOURCEGROUP"},
        "terms": [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["PCCAGENTLESSSCANRESOURCEGROUP"]}],
        "summary": {"rule_id": 78},
    }
    assert service._previous_overlap_reason(subscription_rule, prisma) is None


def test_prior_rule_exclusion_uses_active_rule_without_recent_billing_evidence():
    service = AzureTagPolicyService(db=None)
    foundation = _rule(id=52, priority=90, department_id=73)
    prisma = _rule(id=78, priority=9, department_id=234, name="Classifica → Prisma",
                   conditions=[{"field": "source_resource_group_name", "operator": "~",
                                "values": ["PCCAGENTLESSSCANRESOURCEGROUP", "dig-security-rg"]}])
    departments = {
        73: Department(id=73, name="fundacao telefonica", parent_id=None, centro_custo="29TN161700"),
        234: Department(id=234, name="Prisma", parent_id=None, centro_custo="29TN161700"),
    }
    item = {
        "rule": foundation, "target": "resource_group",
        "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "terms": [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=",
                   "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}],
        "tags": {"projeto": "fundacao telefonica"}, "observed": {},
    }
    exclusions = service._prior_rule_exclusions(item, [prisma, foundation], departments, {},
                                                 [{"tag_key": "projeto", "focus_field": "organograma_nivel_5"}], [])
    assert [entry["rule_id"] for entry in exclusions] == [78]
    assert exclusions[0]["selector"]["allOf"][1]["anyOf"][0] == {
        "field": "name", "contains": "PCCAGENTLESSSCANRESOURCEGROUP",
    }


def test_billed_higher_priority_rule_without_department_still_excludes_broad_policy():
    service = AzureTagPolicyService(db=None)
    terra = _rule(id=45, priority=90, department_id=73, name="Classifica → Terra Brasil",
                  conditions=[{"field": "SubAccountName", "operator": "=", "values": ["Terra-Brasil-Dev"]}])
    security = _rule(id=3605, priority=10, department_id=157, name="Segurança_class",
                     conditions=[{"field": "source_resource_group_name", "operator": "~",
                                  "values": ["trendmicro"]}])
    departments = {73: Department(id=73, name="Terra Brasil", parent_id=None)}
    item = {
        "rule": terra, "target": "resource_group",
        "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "terms": [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=",
                   "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}],
        "tags": {"projeto": "Terra Brasil"}, "observed": {},
    }

    exclusions = service._prior_rule_exclusions(
        item, [security, terra], departments,
        {"Terra-Brasil-Dev": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
        [{"tag_key": "projeto", "focus_field": "organograma_nivel_5"}], [],
        "Terra-Brasil-Dev", {3605},
    )

    assert [entry["rule_id"] for entry in exclusions] == [3605]
    assert {"field": "name", "contains": "trendmicro"} in exclusions[0]["selector"]["allOf"]


def test_prior_rule_exclusion_drops_proven_disjoint_subscription():
    service = AzureTagPolicyService(db=None)
    foundation = _rule(id=52, priority=90, department_id=73)
    other = _rule(id=78, priority=9, department_id=234,
                  conditions=[{"field": "SubAccountId", "operator": "=",
                               "values": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]}])
    departments = {
        73: Department(id=73, name="Fundacao", parent_id=None),
        234: Department(id=234, name="Prisma", parent_id=None),
    }
    item = {"rule": foundation, "target": "resource", "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            "terms": [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=",
                       "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}],
            "tags": {"projeto": "Fundacao"}, "observed": {}}
    assert service._prior_rule_exclusions(item, [other, foundation], departments, {},
                                          [{"tag_key": "projeto", "focus_field": "organograma_nivel_5"}], []) == []


def test_current_arg_target_matches_contains_case_insensitively():
    target = {"resource_group_name": "PCCAgentlessScanResourceGroup", "resource_id": "/subscriptions/a/resourceGroups/PCCAgentlessScanResourceGroup/providers/Microsoft.Web/sites/test", "resource_type": "Microsoft.Web/sites"}
    terms = [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["pccagentlessscanresourcegroup"]}]
    assert AzureTagPolicyService._target_matches_terms(target, terms)


def test_current_arg_matches_resource_name_separately_from_resource_group_name():
    service = AzureTagPolicyService(db=None)
    target = {"resource_group_name": "rg-voice", "resource_name": "voice-api",
              "resource_id": "/subscriptions/a/resourceGroups/rg-voice/providers/Microsoft.Web/sites/voice-api",
              "resource_type": "Microsoft.Web/sites"}
    assert service._target_matches_terms(target, [
        {"scope": SCOPE_RESOURCE, "field": "name", "operator": "=", "values": ["voice-api"]},
        {"scope": SCOPE_RG, "field": "name", "operator": "=", "values": ["rg-voice"]},
    ])
    assert not service._target_matches_terms(target, [
        {"scope": SCOPE_RESOURCE, "field": "name", "operator": "=", "values": ["rg-voice"]},
    ])


def test_plan_only_shell_calls_no_azure_mutations(tmp_path):
    subscription = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    script = build_script([{
        "connection_id": 2, "connection_code": "azure-ti", "connection_name": "Azure TI",
        "layers": {"resource_groups": [{
            "rule_id": 52, "rule_name": "Fundacao", "tags": {"projeto": "Fundacao"},
            "condition": {"field": "name", "equals": "rg-fundacao"},
            "base_selector": {"field": "name", "equals": "rg-fundacao"},
            "scope_subscriptions": [subscription],
        }], "resources": []},
    }], plan_only=True)
    fake_az = tmp_path / "az"
    fake_az.write_text("""#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$AZ_LOG"
if [[ "$1 $2" == "account show" ]]; then
  [[ "$*" == *"--query id"* ]] && printf '%s\\n' "$EXPECTED_SUBSCRIPTION"
  exit 0
fi
if [[ "$1" == "rest" ]]; then exit 0; fi
if [[ "$1 $2 $3" == "policy definition show" || "$1 $2 $3" == "policy assignment show" ]]; then
  echo 'ResourceNotFound' >&2
  exit 1
fi
echo "unexpected Azure CLI call: $*" >&2
exit 91
""")
    fake_az.chmod(0o755)
    log = tmp_path / "az.log"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "AZ_LOG": str(log), "EXPECTED_SUBSCRIPTION": subscription}
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, env=env, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    calls = log.read_text().splitlines()
    assert calls
    forbidden = ("create", "update", "assign", "trigger-scan", "remediation")
    assert not [call for call in calls if any(token in call.split() for token in forbidden)]


def test_rg_assignment_scope_omits_rg_name_from_selector():
    service = AzureTagPolicyService(db=None)
    terms = [
        {"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
        {"scope": SCOPE_RG, "field": "name", "operator": "=", "values": ["rg-terra"]},
    ]
    rg = service._selector(terms, "resource_group", omit_rg_scope=True)
    resource = service._selector(terms, "resource", omit_rg_scope=True)
    assert rg == {"field": "type", "equals": "Microsoft.Resources/subscriptions/resourceGroups"}
    assert resource["allOf"][0] == {"field": "type", "notEquals": "Microsoft.Resources/subscriptions/resourceGroups"}
    assert resource["allOf"][1]["not"]["allOf"][1] == {"field": "name", "like": "*/master"}


def test_deployment_script_uses_resource_group_assignment_scope():
    subscription = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    script = build_script([{
        "connection_code": "azure-ti", "connection_name": "Azure TI",
        "layers": {"resource_groups": [{
            "rule_id": 21, "rule_name": "Terra Brasil", "tags": {"projeto": "Terra"},
            "condition": {"field": "type", "equals": "Microsoft.Resources/subscriptions/resourceGroups"},
            "scope_subscriptions": [subscription],
            "assignment_scope_kind": "resource_group",
            "assignment_resource_group": "rg-terra",
            "observed": {"resource_group_name": "rg-terra"},
        }], "resources": []},
    }])
    assert f"/subscriptions/{subscription}/resourceGroups/rg-terra" in script
    assert "# Assignment: resource group rg-terra" in script
    assert 'az policy remediation create --name "$name" --resource-group "$resource_group"' in script


def test_deployment_identity_is_stable_across_item_order_and_short_enough():
    subscription = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    items = [{"rule_id": 52, "rule_name": "Fundacao", "tags": {"projeto": "Fundacao"},
              "condition": {"field": "name", "contains": "fundacao"}, "base_selector": {"field": "name", "contains": "fundacao"},
              "scope_subscriptions": [subscription]}]
    tenant = {"connection_id": 2, "connection_code": "azure-ti", "connection_name": "Azure TI",
              "layers": {"resource_groups": items, "resources": []}}
    build_script([tenant])
    second = build_script([{**tenant, "layers": {"resource_groups": [{"rule_id": 999, "rule_name": "Other",
                 "tags": {"x": "y"}, "condition": {}, "scope_subscriptions": [subscription]}, *items], "resources": []}}])
    expected = build_script([{**tenant, "layers": {"resource_groups": [items[0]], "resources": []}}])
    identity = next(line for line in expected.splitlines() if line.startswith("ensure_definition \"$ACTIVE_POLICY_NAME\""))
    name_line = next(line for line in expected.splitlines() if line.startswith('ACTIVE_POLICY_NAME='))
    assignment_line = next(line for line in expected.splitlines() if line.startswith("ACTIVE_ASSIGNMENT_NAME="))
    name = name_line.split(":-", 1)[1].removesuffix('}"')
    assignment = assignment_line.split(":-", 1)[1].removesuffix('}"')
    assert len(name) <= 64
    assert len(assignment) <= 64
    assert identity in second
    assert name_line in second
    assert assignment_line in second
    assert name.startswith("of-def-2-52-rg-")
    assert assignment.startswith("of-asg-2-52-rg-")
