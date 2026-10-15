from app.services.azure_tag_dashboard_service import (
    AzureTagDashboardService,
    _managed_tag_status,
    _resource_tag_support,
)
from app.services import azure_tag_dashboard_service as dashboard_module


def test_managed_tag_status_uses_case_insensitive_keys_and_nonempty_values():
    keys = ["projeto", "diretoria", "centro-de-custos"]
    assert _managed_tag_status({"Projeto": "Terra", "DIRETORIA": "TI", "centro-de-custos": "CC1"}, keys) == ("complete", [])
    assert _managed_tag_status({"projeto": "Terra", "diretoria": ""}, keys) == (
        "partial", ["diretoria", "centro-de-custos"],
    )
    assert _managed_tag_status({"owner": "ops"}, keys) == ("none", keys)
    assert _managed_tag_status({"projeto": "Terra"}, []) == ("not_assessed", [])


def test_resource_tag_support_distinguishes_supported_unsupported_and_unknown_types():
    caps = {
        "microsoft.compute/virtualmachines": True,
        "microsoft.sql/servers/databases": True,
        "microsoft.network/routefilters": False,
    }
    assert _resource_tag_support("Microsoft.Compute/virtualMachines", "vm1", caps) == (True, None)
    assert _resource_tag_support("Microsoft.Network/routeFilters", "routes1", caps)[0] is False
    assert _resource_tag_support("Microsoft.Unknown/widgets", "thing", caps)[0] is None
    master = _resource_tag_support("Microsoft.Sql/servers/databases", "sql1/master", caps)
    assert master[0] is False
    assert "master" in master[1]


def test_subscription_summary_separates_resources_groups_unknown_and_tag_coverage():
    subscription = {"subscription_id": "sub-1", "subscription_name": "Dev"}
    rows = [
        {"resource_type": "microsoft.compute/virtualmachines", "is_resource_group": False,
         "total": 10, "complete": 6, "partial": 2, "none": 2},
        {"resource_type": "microsoft.resources/subscriptions/resourcegroups", "is_resource_group": True,
         "total": 3, "complete": 1, "partial": 1, "none": 1},
        {"resource_type": "microsoft.sql/servers/databases", "is_resource_group": False,
         "is_sql_master": True, "total": 1, "complete": 0, "partial": 0, "none": 1},
        {"resource_type": "microsoft.unknown/widgets", "is_resource_group": False,
         "total": 2, "complete": 0, "partial": 0, "none": 2},
    ]
    summary = AzureTagDashboardService._subscription_summary(subscription, rows, {
        "microsoft.compute/virtualmachines": True,
        "microsoft.resources/subscriptions/resourcegroups": True,
        "microsoft.sql/servers/databases": True,
    }, ["projeto", "diretoria", "centro-de-custos"], [])
    assert summary["total_resources"] == 13
    assert summary["resource_groups"] == 3
    assert summary["taggable_resources"] == 10
    assert summary["non_taggable_resources"] == 1
    assert summary["unclassified_resources"] == 2
    assert summary["taggable_resource_groups"] == 3
    assert summary["taggable_items"] == 13
    assert summary["fully_tagged"] == 7
    assert summary["adherence_percent"] == 53.8


def test_bronze_inventory_aggregation_uses_stored_tags_and_tag_support():
    rows = [
        {"subscription_id": "sub-a", "resource_name": "rg-app",
         "resource_type": "Microsoft.Resources/subscriptions/resourceGroups",
         "tags": '{"Projeto":"Datamesh","diretoria":"Dados"}', "taggable": True},
        {"subscription_id": "sub-a", "resource_name": "vm-1",
         "resource_type": "Microsoft.Compute/virtualMachines",
         "tags": '{"projeto":"Datamesh"}', "taggable": True},
        {"subscription_id": "sub-a", "resource_name": "sql-1/master",
         "resource_type": "Microsoft.Sql/servers/databases", "tags": "{}", "taggable": False},
        {"subscription_id": "sub-a", "resource_name": "opaque",
         "resource_type": "Microsoft.Unknown/widgets", "tags": "{}", "taggable": None},
    ]
    aggregates = AzureTagDashboardService._inventory_aggregates(rows, ["projeto", "diretoria"])["sub-a"]
    by_type = {row["resource_type"]: row for row in aggregates}
    assert by_type["microsoft.resources/subscriptions/resourcegroups"]["complete"] == 1
    assert by_type["microsoft.compute/virtualmachines"]["partial"] == 1
    assert by_type["microsoft.sql/servers/databases"]["taggable"] is False
    assert by_type["microsoft.unknown/widgets"]["taggable"] is None


def test_executive_summary_reads_snapshot_and_does_not_call_resource_graph(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace

    connection = SimpleNamespace(id=2, name="Azure TI", code="azure-ti")
    service = AzureTagDashboardService(db=object())
    async def get_connection(_connection_id): return connection
    async def tag_keys(): return ["projeto", "diretoria"]
    async def inventory(_connection):
        return ([{
            "subscription_id": "sub-a", "subscription_name": "Azure Dev",
            "resource_id": "/subscriptions/sub-a/resourceGroups/rg-app",
            "resource_name": "rg-app", "resource_type": "Microsoft.Resources/subscriptions/resourceGroups",
            "tags": '{"projeto":"Datamesh","diretoria":"Dados"}', "taggable": True,
        }], SimpleNamespace(blob_path="arg/inventory/2026-10-15.csv", last_modified=datetime.now(timezone.utc)))
    async def forbidden_names(_connection_id): raise AssertionError("dashboard should use subscription names stored in Bronze")
    async def forbidden(*_args, **_kwargs): raise AssertionError("dashboard load must not query Azure Resource Graph")
    service._connection = get_connection
    service._tag_keys = tag_keys
    service._latest_inventory = inventory
    service._silver_subscription_names = forbidden_names
    monkeypatch.setattr(dashboard_module, "discover_subscriptions", forbidden, raising=False)
    monkeypatch.setattr(dashboard_module, "_post_query", forbidden, raising=False)

    result = asyncio.run(service.executive_summary(2))
    subscription = result["subscriptions"][0]
    assert result["snapshot_date"] == "2026-10-15"
    assert subscription["name"] == "Azure Dev"
    assert subscription["fully_tagged"] == 1
    assert subscription["adherence_percent"] == 100.0


def test_subscription_resources_filters_in_bronze_and_uses_snapshot_name():
    import asyncio
    from types import SimpleNamespace

    subscription_id = "a1dc3bc4-8d61-4b04-9090-65e60012b96f"
    service = AzureTagDashboardService(db=object())
    async def get_connection(_connection_id):
        return SimpleNamespace(id=2, name="Azure TI", code="azure-ti")
    async def tag_keys():
        return ["projeto", "diretoria", "centro-de-custos"]
    async def inventory(_connection, subscription_id=None):
        assert subscription_id == "a1dc3bc4-8d61-4b04-9090-65e60012b96f"
        return ([{
            "subscription_id": subscription_id,
            "subscription_name": "Terra Brasil Dev",
            "resource_id": f"/subscriptions/{subscription_id}/resourceGroups/rg-app",
            "resource_name": "rg-app",
            "resource_type": "Microsoft.Resources/subscriptions/resourceGroups",
            "tags": {"projeto": "Terra", "diretoria": "TI", "centro-de-custos": "CC1"},
            "taggable": True,
        }], SimpleNamespace(blob_path="arg/inventory/2026-10-15.csv", billing_period="202610"))
    async def forbidden_names(_connection_id):
        raise AssertionError("resource details should use the Bronze subscription name")
    service._connection = get_connection
    service._tag_keys = tag_keys
    service._latest_inventory = inventory
    service._silver_subscription_names = forbidden_names

    result = asyncio.run(service.subscription_resources(2, subscription_id))
    assert result["subscription_name"] == "Terra Brasil Dev"
    assert len(result["resources"]) == 1
    assert result["resources"][0]["tag_status"] == "complete"
