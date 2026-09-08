from app.models.rules import ClassificationRule, Department
from app.routers.azure_tag_policy import _definition, build_script
from app.services.azure_tag_policy_service import (
    AzureTagPolicyService,
    SCOPE_RESOURCE,
    SCOPE_RG,
    SCOPE_SUBSCRIPTION,
)


def _rule(**values):
    data = {
        "id": 1, "name": "Regra", "department_id": 10, "priority": 10,
        "cloud_scope": "azure", "source_field": "", "pattern": "", "pattern_type": "multi_condition",
        "match_value": "Projeto", "target_field": "departamento", "active": True,
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
    assert rg["allOf"][0] == {"field": "type", "equals": "Microsoft.Resources/resourceGroups"}
    assert rg["allOf"][1] == {"field": "name", "equals": "rg-terra"}
    assert resource["allOf"][1] == {"value": "[resourceGroup().name]", "equals": "rg-terra"}


def test_multiple_resource_groups_create_valid_value_alternatives():
    service = AzureTagPolicyService(db=None)
    terms = [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["rg-a", "rg-b"]}]
    resource = service._selector(terms, "resource")
    alternatives = resource["allOf"][1]["anyOf"]
    assert alternatives == [
        {"value": "[resourceGroup().name]", "like": "*rg-a*"},
        {"value": "[resourceGroup().name]", "like": "*rg-b*"},
    ]


def test_resource_id_never_tags_the_resource_group():
    service = AzureTagPolicyService(db=None)
    terms = [{"scope": SCOPE_RESOURCE, "field": "id", "operator": "~", "values": ["vhub-tcn-mx"]}]
    assert service._selector(terms, "resource_group") is None
    selector = service._selector(terms, "resource")
    assert selector["allOf"][1] == {"field": "id", "contains": "vhub-tcn-mx"}


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


def test_gold_rg_contains_becomes_exact_observed_resource_group():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    items = service._concrete_items(
        _rule(conditions=[{"field": "source_resource_group_name", "operator": "~", "values": ["trendmicro"]}]),
        [{"scope": SCOPE_RG, "field": "name", "operator": "~", "values": ["trendmicro"]}],
        [{
            "subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "resource_group_name": "rg-prd-trendmicro-eastus",
            "resource_id": None,
            "evidence_count": 14,
        }],
        {"centro_custo": "CC"}, True, diagnostics,
    )
    assert len(items) == 1
    assert items[0]["observed"] == {"resource_group_name": "rg-prd-trendmicro-eastus"}
    rg_term = next(term for term in items[0]["terms"] if term["scope"] == SCOPE_RG)
    assert rg_term == {"scope": SCOPE_RG, "field": "name", "operator": "=", "values": ["rg-prd-trendmicro-eastus"]}


def test_gold_resource_exception_is_exact_and_isolated_to_its_subscription():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    resource_id = "/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/rg-tcn/providers/Microsoft.Network/azureFirewalls/azurefirewall_vhub-tcn-mx-eastus2"
    items = service._concrete_items(
        _rule(conditions=[{"field": "ResourceId", "operator": "~", "values": ["azurefirewall_vhub-tcn-mx"]}]),
        [{"scope": SCOPE_RESOURCE, "field": "id", "operator": "contains", "values": ["azurefirewall_vhub-tcn-mx"]}],
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
    assert next(term for term in items[0]["terms"] if term["scope"] == SCOPE_RESOURCE)["values"] == [resource_id]
    selector = service._selector(items[0]["terms"], "resource")
    assert {"field": "id", "equals": resource_id} in selector["allOf"]


def test_hierarchy():
    service = AzureTagPolicyService(db=None)
    root = Department(id=1, name="TI", centro_custo="CC1", ambiente="Prod")
    project = Department(id=2, name="Terra Brasil", parent_id=1)
    tags = service._hierarchy(project, {1: root, 2: project})
    assert tags["departamento_nivel_1"] == "TI"
    assert tags["departamento_nivel_2"] == "Terra Brasil"
    assert tags["centro_custo"] == "CC1"


def test_tag_mapping_uses_global_key_and_focus_field_value():
    service = AzureTagPolicyService(db=None)
    diagnostics = []
    items = service._concrete_items(
        _rule(),
        [{"scope": SCOPE_SUBSCRIPTION, "field": "subscriptionId", "operator": "=", "values": ["ignored"]}],
        [{"subscription_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "evidence_count": 1,
          "tag_value_0": "Terra Brasil", "tag_value_1": "Financeiro"}],
        {"departamento_nivel_5": "Terra Brasil"},
        [{"tag_key": "projeto", "focus_field": "departamento_nivel_5"},
         {"tag_key": "area", "focus_field": "billing_account_name"}],
        True, diagnostics,
    )
    assert items[0]["tags"] == {"projeto": "Terra Brasil", "area": "Financeiro"}


def test_deployment_script_uses_identity_rbac_and_test_suffix():
    script = build_script([{
        "connection_code": "azure-ti", "connection_name": "Azure TI", "department_name": "Terra Brasil",
        "deployment_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "layers": {"resource_groups": [{"rule_id": 1, "rule_name": "Terra Brasil", "tags": {"centro_custo": "CC"},
                                             "condition": {"field": "name", "equals": "rg-terra"},
                                             "scope_subscriptions": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]}], "resources": []},
    }])
    assert "--mi-system-assigned" in script
    assert 'role "Tag Contributor"' in script
    assert "az policy remediation create" in script
    assert "test-terra-brasil" in script
    assert "addOrReplace" in script
    assert "az policy definition update" in script
    assert "az policy assignment update" in script
    assert "metadata.category" in script
    assert "RETRY_ATTEMPTS" in script
    assert "PLAN_ONLY" in script
    assert 'PLAN_ONLY="false"' in script
    assert "openfinopsContentHash" in script
    assert '--rules "@$rules"' in script
    assert '--metadata "@$metadata"' in script
    assert "az policy state trigger-scan --no-wait" in script
    assert "trigger-scan --resource" not in script
    assert "grep -q" not in script
    assert "|| true" not in script
    assert "continue" not in script
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
