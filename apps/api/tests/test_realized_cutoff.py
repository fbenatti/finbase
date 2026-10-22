from app.services.realized_cutoff import RealizedCutoffPolicy, realized_cutoff_source
from app.services import lakehouse_layers as layers


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
    """O detalhe vive na camada de detalhe (Silver), não na Gold.

    A asserção aponta para o resolvedor central: se a camada de detalhe mudar,
    o teste acompanha em vez de fixar um namespace que já mudou uma vez.
    """
    sql = realized_cutoff_source("dest_billing_cloud", _schema(), _policy())
    assert sql == f"iceberg.{layers.detail_layer()}.dest_billing_cloud"
    assert "LEFT JOIN" not in sql
    assert "date_add" not in sql


def test_planning_table_reads_from_the_detail_layer():
    assert realized_cutoff_source(
        "dest_billing_cloud_planning", _schema(), _policy(),
    ) == f"iceberg.{layers.detail_layer()}.dest_billing_cloud_planning"


def test_missing_policy_keeps_plain_scan():
    assert realized_cutoff_source(
        "dest_billing_cloud", _schema(), None,
    ) == f"iceberg.{layers.detail_layer()}.dest_billing_cloud"
