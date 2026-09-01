from app.routers.azure_resource_graph import _build_orphan_where, _saving_generated


def test_orphan_snapshot_filter_keeps_one_daily_picture():
    where = _build_orphan_where(
        "202608", ["disk_unattached"], None, None,
        alias="o", snapshot_date="2026-08-31",
    )

    assert 'o."_billing_period" = \'202608\'' in where
    assert "o.orphan_rule IN ('disk_unattached')" in where
    assert "o.snapshot_date = '2026-08-31'" in where


def test_generated_saving_is_prorated_over_observed_days():
    days, saving = _saving_generated(3043.75, "2026-08-01", "2026-08-10")

    assert days == 10
    assert saving == 1000.0
