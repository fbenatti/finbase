from app.routers.azure_resource_graph import (
    ORPHAN_COST_SQL_TEMPLATE,
    _build_orphan_where,
    _latest_snapshot_per_origin_where,
    _orphan_table,
    _saving_generated,
)


def test_orphan_cost_gold_uses_resolved_unified_silver_table():
    assert _orphan_table() == "iceberg.silver.dest_cloud_orphan_resources"
    assert "{layers.detail_layer()}" not in ORPHAN_COST_SQL_TEMPLATE
    assert "iceberg.silver.dest_cloud_orphan_resources" in ORPHAN_COST_SQL_TEMPLATE
    # O cruzamento com o custo é por recurso e mês, sem códigos de conexão fixos.
    assert "THEN 'azure-" not in ORPHAN_COST_SQL_TEMPLATE
    assert 'GROUP BY cloud_origem' in ORPHAN_COST_SQL_TEMPLATE
    assert 'p.cloud_origem = o.cloud_origem' in ORPHAN_COST_SQL_TEMPLATE
    assert 'SELECT MAX("_billing_period") FROM iceberg.silver.dest_cloud_orphan_resources' not in ORPHAN_COST_SQL_TEMPLATE


def test_orphan_snapshot_filter_keeps_one_daily_picture():
    where = _build_orphan_where(
        "202608", ["disk_unattached"], None, None,
        alias="o", snapshot_date="2026-08-31",
    )

    assert 'o."_billing_period" = \'202608\'' in where
    assert "o.orphan_rule IN ('disk_unattached')" in where
    assert "o.snapshot_date = '2026-08-31'" in where


def test_latest_snapshot_per_origin_correlates_to_outer_origin():
    where = _latest_snapshot_per_origin_where("", "o")

    assert "i.cloud_origem = o.cloud_origem" in where


def test_generated_saving_is_prorated_over_observed_days():
    days, saving = _saving_generated(3043.75, "2026-08-01", "2026-08-10")

    assert days == 10
    assert saving == 1000.0
