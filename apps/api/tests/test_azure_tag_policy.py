import base64
import json
import os
import subprocess

from app.models.rules import ClassificationRule, Department
from app.routers.azure_tag_policy import _definition, _tag_drift_condition, build_script
from app.services.azure_tag_policy_service import (
    AzureTagPolicyService,
    SCOPE_RESOURCE,
    SCOPE_RG,
    SCOPE_SKU,
    SCOPE_SUBSCRIPTION,
)


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
    assert resource["allOf"][1] == {"value": "[resourceGroup().name]", "equals": "rg-terra"}


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
    alternatives = resource["allOf"][1]["anyOf"]
    assert alternatives == [
        {"value": "[resourceGroup().name]", "contains": "rg-a"},
        {"value": "[resourceGroup().name]", "contains": "rg-b"},
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
    assert 'az policy remediation show --name "$name" --subscription "$subscription"' in script


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
    assert '--metadata "@$metadata"' in script
    assert "az policy state trigger-scan --no-wait" in script
    assert "trigger-scan --resource" not in script
    assert "grep -q" not in script
    assert "|| true" not in script
    assert "Há $matches assignments OpenFinops candidatos" in script
    assert 'az rest --method get' in script
    assert "contains(properties.policyDefinitionId" in script
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
    assert resource == {"field": "type", "notEquals": "Microsoft.Resources/subscriptions/resourceGroups"}


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
