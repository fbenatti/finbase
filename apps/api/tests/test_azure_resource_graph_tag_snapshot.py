import asyncio

import pandas as pd
from types import SimpleNamespace

from app.services.azure_resource_graph_service import INVENTORY_QUERY, _annotate_tag_support


def test_resource_graph_collection_discovers_subscriptions_before_inventory(monkeypatch):
    from app.services import azure_resource_graph_service as service

    calls = {}

    async def fake_discover(_config):
        return [
            {"subscription_id": "sub-a", "subscription_name": "Subscription A"},
            {"subscription_id": "sub-b", "subscription_name": "Subscription B"},
        ]

    async def fake_collect(_config, subscription_ids=None):
        calls["subscription_ids"] = subscription_ids
        return {"inventory": pd.DataFrame([{
            "resource_id": "/subscriptions/sub-a/resourceGroups/rg",
            "subscription_id": "sub-a",
        }])}

    class FakeSession:
        def get(self, *_args):
            return SimpleNamespace(code="azure-dev", config_json={})

        def commit(self):
            pass

    monkeypatch.setattr(service, "discover_subscriptions", fake_discover)
    monkeypatch.setattr(service, "collect_snapshot", fake_collect)
    written_frames = []

    def fake_write(_s, _c, frame, *_a, **_k):
        written_frames.append(frame.copy())
        return len(frame)

    monkeypatch.setattr(service, "_write_bronze_and_register", fake_write)

    result = service.run_resource_graph_collection(FakeSession(), 2, snapshot_date="2026-10-15")

    assert calls["subscription_ids"] == ["sub-a", "sub-b"]
    assert written_frames[0].iloc[0]["subscription_name"] == "Subscription A"
    assert result["inventory_rows"] == 1
    assert result["bronze_rows"] == 1


def test_inventory_snapshot_includes_resource_groups_and_their_tags():
    assert "| union (" in INVENTORY_QUERY
    assert "resourcecontainers" in INVENTORY_QUERY
    assert 'type =~ "microsoft.resources/subscriptions/resourcegroups"' in INVENTORY_QUERY
    assert "tags = tags" in INVENTORY_QUERY


def test_tag_support_is_saved_in_inventory_snapshot_at_collection_time():
    class FakeResponse:
        status_code = 200

        def __init__(self, namespace): self.namespace = namespace

        def json(self):
            if self.namespace == "Microsoft.Compute":
                return {"resourceTypes": [{"resourceType": "virtualMachines", "capabilities": "SupportsTags,SupportsLocation"}]}
            if self.namespace == "Microsoft.Network":
                return {"resourceTypes": [{"resourceType": "routeFilters", "capabilities": "SupportsLocation"}]}
            if self.namespace == "Microsoft.Sql":
                return {"resourceTypes": [{"resourceType": "servers/databases", "capabilities": "SupportsTags"}]}
            return {"resourceTypes": []}

    class FakeClient:
        async def get(self, url, **_kwargs):
            namespace = url.split("/providers/")[1].split("?")[0]
            return FakeResponse(namespace)

    inventory = pd.DataFrame([
        {"subscription_id": "sub-1", "resource_type": "Microsoft.Compute/virtualMachines", "resource_name": "vm"},
        {"subscription_id": "sub-1", "resource_type": "Microsoft.Resources/subscriptions/resourceGroups", "resource_name": "rg"},
        {"subscription_id": "sub-1", "resource_type": "Microsoft.Network/routeFilters", "resource_name": "routes"},
        {"subscription_id": "sub-1", "resource_type": "Microsoft.Sql/servers/databases", "resource_name": "server/master"},
        {"subscription_id": "sub-1", "resource_type": "Microsoft.Unknown/widgets", "resource_name": "unknown"},
    ])

    asyncio.run(_annotate_tag_support(FakeClient(), {"Authorization": "Bearer test"}, inventory))
    assert inventory["taggable"].tolist() == [True, True, False, False, None]
    assert "master" in inventory.iloc[3]["tag_support_reason"].casefold()
