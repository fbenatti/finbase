from app.services.realized_cutoff import RealizedCutoffPolicy, realized_cutoff_source


def _schema():
    return {
        "ChargePeriodStart": "varchar",
        "_billing_period": "varchar",
        "cloud_origem": "varchar",
        "source_connection_id": "bigint",
        "tipo": "varchar",
        "realizado": "double",
    }


def _policy(**kwargs):
    defaults = dict(
        cutoff_days=2,
        date_column="ChargePeriodStart",
        period_column="_billing_period",
        type_column="tipo",
        interval_rules=[],
        global_interval=False,
    )
    defaults.update(kwargs)
    return RealizedCutoffPolicy(**defaults)


def test_cutoff_wrap_is_disabled():
    sql = realized_cutoff_source("dest_modelo_focus", _schema(), _policy())
    assert sql == "iceberg.gold.dest_modelo_focus"
    assert "LEFT JOIN" not in sql
    assert "date_add" not in sql


def test_planning_table_is_plain_gold():
    assert realized_cutoff_source(
        "dest_modelo_focus_planning", _schema(), _policy(),
    ) == "iceberg.gold.dest_modelo_focus_planning"


def test_missing_policy_keeps_plain_gold_scan():
    assert realized_cutoff_source("dest_modelo_focus", _schema(), None) == "iceberg.gold.dest_modelo_focus"
