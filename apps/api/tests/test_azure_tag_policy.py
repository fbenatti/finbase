import pytest
from app.services.azure_tag_policy_service import (
    AzureTagPolicyService,
    format_wildcard,
    SCOPE_RG,
    SCOPE_RESOURCE,
    SCOPE_SKU,
    SCOPE_SUBSCRIPTION,
    SCOPE_LOCATION,
)
from app.models.rules import ClassificationRule, Department
from app.routers.azure_tag_policy import build_script


def test_format_wildcard():
    assert format_wildcard("DTMN") == "*DTMN*"
    assert format_wildcard("core*") == "core*"
    assert format_wildcard("*prod*") == "*prod*"
    assert format_wildcard("?") == "?"
    assert format_wildcard("") == "*"


def test_classify_field():
    service = AzureTagPolicyService(db=None)
    rev_map = {"sourceresourcegroupname": "source_resource_group_name"}

    assert service._classify_field("source_resource_group_name", rev_map) == (SCOPE_RG, "name")
    assert service._classify_field("resourceGroupName", {}) == (SCOPE_RG, "name")
    assert service._classify_field("ResourceName", {}) == (SCOPE_RESOURCE, "name")
    assert service._classify_field("SubAccountName", {}) == (SCOPE_SUBSCRIPTION, "subscriptionId")
    assert service._classify_field("SkuMeter", {}) == (SCOPE_SKU, "Microsoft.Compute/virtualMachines/sku.name")
    assert service._classify_field("RegionName", {}) == (SCOPE_LOCATION, "location")


def test_hierarchy():
    service = AzureTagPolicyService(db=None)
    d1 = Department(id=1, name="DIRETORIA TI", parent_id=None, centro_custo="CC01", ambiente="Prod")
    d2 = Department(id=2, name="GERENCIA INFRA", parent_id=1, centro_custo="CC02", ambiente="TI")
    d3 = Department(id=3, name="CLOUD TEAM", parent_id=2, centro_custo=None, ambiente=None)

    dept_map = {1: d1, 2: d2, 3: d3}

    tags = service._hierarchy(d3, dept_map)
    assert tags["departamento_nivel_1"] == "DIRETORIA TI"
    assert tags["departamento_nivel_2"] == "GERENCIA INFRA"
    assert tags["departamento_nivel_3"] == "CLOUD TEAM"
    assert tags["centro_custo"] == "CC02"
    assert tags["ambiente"] == "TI"


def test_build_policies_rg_patterns_and_exact():
    service = AzureTagPolicyService(db=None)
    dept = Department(id=10, name="DATA TEAM", parent_id=None, centro_custo="CC10", ambiente="Data")
    dept_map = {10: dept}

    # Rule 1: RG with nomenclature (operator ~)
    rule_pat = ClassificationRule(
        id=1,
        name="Rule RG Pattern",
        department_id=10,
        cloud_scope="azure",
        conditions=[{"field": "source_resource_group_name", "operator": "~", "values": ["DTMN"]}],
    )

    # Rule 2: RG exact (operator =)
    rule_exact1 = ClassificationRule(
        id=2,
        name="Rule RG Exact 1",
        department_id=10,
        cloud_scope="azure",
        conditions=[{"field": "resourceGroupName", "operator": "=", "values": ["rg-app-prod"]}],
    )
    rule_exact2 = ClassificationRule(
        id=3,
        name="Rule RG Exact 2",
        department_id=10,
        cloud_scope="azure",
        conditions=[{"field": "resourceGroupName", "operator": "=", "values": ["rg-db-prod"]}],
    )

    # Rule 3: Individual Resource Name
    rule_res = ClassificationRule(
        id=4,
        name="Rule Resource Mexico",
        department_id=10,
        cloud_scope="azure",
        conditions=[{"field": "ResourceName", "operator": "~", "values": ["mexico"]}],
    )

    policies = service._build_policies([rule_pat, rule_exact1, rule_exact2, rule_res], dept_map, {})

    # Check inheritance
    assert len(policies["inheritance"]) == 7

    # Check exact RGs grouped
    assert len(policies["resource_groups"]) == 1
    rg_exact = policies["resource_groups"][0]
    assert set(rg_exact["names"]) == {"rg-app-prod", "rg-db-prod"}
    assert rg_exact["condition"]["allOf"][0] == {"field": "type", "equals": "Microsoft.Resources/resourceGroups"}
    assert rg_exact["condition"]["allOf"][1] == {"field": "name", "in": ["rg-app-prod", "rg-db-prod"]}

    # Check RG pattern
    assert len(policies["rg_patterns"]) == 1
    rg_pat = policies["rg_patterns"][0]
    assert rg_pat["condition"]["allOf"][0] == {"field": "type", "equals": "Microsoft.Resources/resourceGroups"}
    assert rg_pat["condition"]["allOf"][1] == {"field": "name", "like": "*DTMN*"}

    # Check Resource pattern
    assert len(policies["patterns"]) == 1
    res_pat = policies["patterns"][0]
    assert res_pat["condition"]["allOf"][0] == {"field": "type", "notEquals": "Microsoft.Resources/resourceGroups"}
    assert res_pat["condition"]["allOf"][1] == {"field": "name", "like": "*mexico*"}


def test_build_script():
    policies = [{
        "connection_code": "azure_4p",
        "connection_name": "Azure 4P",
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "layers": {
            "inheritance": [{
                "parameters": {"tagName": {"value": "departamento_nivel_1"}},
                "policyDefinitionId": "/providers/Microsoft.Authorization/policyDefinitions/cd3aa116-8754-49c9-a813-ad46512ece54"
            }],
            "resource_groups": [{
                "names": ["rg-prod-1"],
                "condition": {"field": "name", "equals": "rg-prod-1"},
                "tags": {"departamento_nivel_1": "TI"}
            }],
            "rg_patterns": [{
                "patterns": ["DTMN"],
                "condition": {"field": "name", "like": "*DTMN*"},
                "tags": {"departamento_nivel_1": "TI"}
            }],
            "patterns": []
        }
    }]
    script = build_script(policies)
    assert "#!/usr/bin/env bash" in script
    assert "[INFO]" in script
    assert "openfinops-inherit-departamento-nivel-1" in script
    assert "openfinops-rg-azure-4p-1" in script
    assert "openfinops-rgpat-azure-4p-1" in script
    assert "az policy remediation create" in script
