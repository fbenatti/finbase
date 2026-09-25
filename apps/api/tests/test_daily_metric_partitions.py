from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import asyncio
import pytest


class _Session:
    def get(self, _model, _id):
        return SimpleNamespace(id=_id, code="azure-test", config_json={})

    def commit(self):
        pass


def test_monitor_bronze_uses_utc_day_partitions(monkeypatch):
    from app.services import azure_monitor_vm_service as monitor

    written = []
    result = {
        "frame": pd.DataFrame([
            {"subscription_id": "sub", "location": "eastus", "bucket_utc": "2026-09-23T23:45:00Z"},
            {"subscription_id": "sub", "location": "eastus", "bucket_utc": "2026-09-24T00:15:00Z"},
        ]),
        "gaps": pd.DataFrame(), "failures": [], "vms": 1, "disks": 0,
        "metrics": [], "disk_metrics": [], "series_rows": 2, "gap_rows": 0,
        "duration_seconds": 1, "requests": 1, "rate_limited": 0,
        "api_version": "test", "since": "2026-09-23T00:00:00+00:00",
        "until": "2026-09-25T00:00:00+00:00",
    }
    monkeypatch.setattr(monitor.asyncio, "run", lambda _coro: result)
    monkeypatch.setattr(monitor, "collect_metrics", lambda *_a, **_kw: None)
    monkeypatch.setattr(monitor, "_write_bronze_and_register",
                        lambda _s, _c, frame, period, path, **_kw:
                        written.append((period, path, len(frame))) or len(frame))

    output = monitor.run_monitor_vm_collection(
        _Session(), 1, window_days=2,
        until=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    assert output["periods"] == ["20260923", "20260924"]
    assert written == [
        ("20260923", "monitor/sub/eastus/20260923/metrics.csv", 1),
        ("20260924", "monitor/sub/eastus/20260924/metrics.csv", 1),
    ]


def test_container_metrics_keep_each_day_in_its_own_partition(monkeypatch):
    from app.services import azure_container_insights_service as container
    from app.services import azure_automation_service as automation

    written = []
    frame = pd.DataFrame([
        {"record_kind": "pod_inventory", "snapshot_date": "2026-09-23"},
        {"record_kind": "performance", "snapshot_date": "2026-09-24 00:00:00"},
    ])
    monkeypatch.setattr(automation, "asyncio_run", lambda _coro: (frame, []))
    monkeypatch.setattr(container, "collect_container_insights", lambda *_a, **_kw: None)
    monkeypatch.setattr(container, "_write_bronze",
                        lambda _s, _c, chunk, period, path, **_kw:
                        written.append((period, path, len(chunk))) or len(chunk))

    output = container.run_container_insights_collection(
        _Session(), 1, window_days=2,
        until=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    assert output["periods"] == ["20260923", "20260924"]
    assert written == [
        ("20260923", "azure_container_insights/20260923/dados", 1),
        ("20260924", "azure_container_insights/20260924/dados", 1),
    ]


def test_container_performance_queries_one_day_at_a_time(monkeypatch):
    import asyncio
    from app.services import azure_container_insights_service as container
    from app.services import azure_resource_graph_service as graph

    calls = []

    async def discover(_config):
        return pd.DataFrame([{
            "cluster_id": "/subscriptions/sub/clusters/example",
            "cluster_name": "example", "subscription_id": "sub",
            "location": "brazilsouth", "habilitado": "true", "ws": "/workspace",
        }])

    async def headers(_config):
        return {}

    async def query(_client, _headers, _ws, kql, *, window_days, until):
        if kql == container.QUERY_PERFORMANCE:
            calls.append((window_days, until.date().isoformat()))
        return []

    monkeypatch.setattr(container, "discover_workspaces", discover)
    monkeypatch.setattr(graph, "arm_headers", headers)
    monkeypatch.setattr(container, "_consultar_workspace", query)

    asyncio.run(container.collect_container_insights(
        {}, window_days=7, until=datetime(2026, 9, 25, tzinfo=timezone.utc),
    ))

    assert calls == [(1, f"2026-09-{day:02d}") for day in range(19, 26)]


def test_analysis_partition_endpoint_validates_catalog_and_classifies_period(monkeypatch):
    from app.routers import analysis

    monkeypatch.setattr(analysis, "list_tables", lambda layer: ["raw_azure_4p_monitor_vm"] if layer == "bronze" else [])
    async def fake_execute(sql, **kwargs):
        assert '"raw_azure_4p_monitor_vm$partitions"' in sql
        assert kwargs == {"timeout_s": 12, "max_retries": 1}
        return [{"partition": {"_billing_period": "20260924"}, "record_count": 123, "file_count": 2}]
    monkeypatch.setattr(analysis, "trino_execute", fake_execute)
    monkeypatch.setattr(analysis, "get_catalog", lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable in test")))
    result = asyncio.run(analysis.get_table_partitions("bronze", "raw_azure_4p_monitor_vm"))
    assert result["partitions"] == [{"period": "20260924", "period_kind": "dia UTC de métrica", "record_count": 123, "file_count": 2}]

    with pytest.raises(Exception):
        asyncio.run(analysis.get_table_partitions("bronze", 'x"; DROP TABLE foo --'))


def test_analysis_catalog_hides_only_generated_staging_tables(monkeypatch):
    from app.routers import analysis

    monkeypatch.setattr(analysis, "list_tables", lambda layer: [
        "dest_consumo_ia", "dest_consumo_ia_stg_1790928100765000", "silver_stg_notes",
    ])
    result = asyncio.run(analysis.get_tables())
    assert result["silver"] == ["dest_consumo_ia", "silver_stg_notes"]


def test_force_delete_staging_preserves_a_location_referenced_by_canonical_table(monkeypatch, tmp_path):
    from app import database
    from app.services import iceberg_service

    shared = tmp_path / "silver.db" / "dest_consumo_ia"
    (shared / "metadata").mkdir(parents=True)
    (shared / "metadata" / "keep.json").write_text("canonical metadata")

    class Table:
        def location(self):
            return str(shared)

    class Catalog:
        def load_table(self, _table):
            return Table()
        def drop_table(self, _table):
            pass

    class Rows:
        def all(self):
            return [("silver", "dest_consumo_ia", f"{shared.as_uri()}/metadata/keep.json")]

    class Session:
        def execute(self, _query):
            return Rows()
        def close(self):
            pass

    monkeypatch.setattr(iceberg_service, "_get_catalog", lambda: Catalog())
    monkeypatch.setattr(database, "SyncSession", Session)
    monkeypatch.setattr(iceberg_service, "_LOCAL_WAREHOUSE", tmp_path / "warehouse")

    iceberg_service._force_delete_table(
        ("silver", "dest_consumo_ia_stg_1790352835748844000"),
        "dest_consumo_ia_stg_1790352835748844000",
    )

    assert (shared / "metadata" / "keep.json").exists()


def test_unpartitioned_slice_returns_table_counts_and_period_stays_in_name(monkeypatch):
    from app.routers import analysis

    class Inspection:
        def to_pandas(self):
            return pd.DataFrame([{"record_count": 43, "file_count": 1}])

    class Table:
        def __init__(self):
            self.inspect = SimpleNamespace(partitions=lambda: Inspection())
        def spec(self):
            return SimpleNamespace(fields=())

    class Catalog:
        def load_table(self, _table):
            return Table()

    monkeypatch.setattr(analysis, "list_tables", lambda _layer: ["finops_cost_driver_slice_azure_4p_202512_service_888001"])
    async def trino_down(*_args, **_kwargs):
        raise RuntimeError("unpartitioned Iceberg table")
    monkeypatch.setattr(analysis, "trino_execute", trino_down)
    monkeypatch.setattr(analysis, "get_catalog", lambda: Catalog())
    result = asyncio.run(analysis.get_table_partitions("silver", "finops_cost_driver_slice_azure_4p_202512_service_888001"))
    assert result["partitioned"] is False
    assert result["record_count"] == 43
    assert result["file_count"] == 1


def test_metric_retention_cutoff_is_90_complete_utc_days():
    from datetime import date
    from app.services.metric_retention import metric_retention_cutoff

    assert metric_retention_cutoff(date(2026, 9, 25)) == "20260627"


def test_metric_worker_failure_distinguishes_unavailable_source():
    from app.services.metric_daily_state import metric_failure_status

    assert metric_failure_status("workspace not found") == "SOURCE_UNAVAILABLE"
    assert metric_failure_status("HTTP 500 from Azure") == "FAILED"
