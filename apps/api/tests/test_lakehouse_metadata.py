"""Metadados de tabela chaveados pelo snapshot do catálogo, sem Trino."""
import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
from pyiceberg.types import (
    DecimalType, DoubleType, ListType, LongType, MapType, NestedField, StringType, StructType,
    TimestamptzType,
)

from app.services import lakehouse_metadata as meta


class FakeTable:
    def __init__(self, snapshot_id, fields, *, partition_names=(), partitions=()):
        self.metadata = SimpleNamespace(current_snapshot_id=snapshot_id, current_schema_id=0)
        self._fields = fields
        self._partition_names = partition_names
        self._partitions = list(partitions)
        self.inspect = SimpleNamespace(partitions=self._partitions_table)

    def schema(self):
        return SimpleNamespace(fields=self._fields)

    def spec(self):
        return SimpleNamespace(fields=[SimpleNamespace(name=name) for name in self._partition_names])

    def _partitions_table(self):
        frame = pd.DataFrame({"partition": self._partitions}) if self._partitions else pd.DataFrame()
        return SimpleNamespace(to_pandas=lambda: frame)


def field(name, field_type, field_id=1):
    return NestedField(field_id=field_id, name=name, field_type=field_type, required=False)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    meta.invalidate()
    # Sem memória da versão: cada chamada relê a "tabela", como requests separados.
    monkeypatch.setattr(meta, "VERSION_MEMO_SECONDS", -1.0)
    yield
    meta.invalidate()


def test_tipos_saem_como_o_describe_do_trino():
    assert meta.trino_type_name(StringType()) == "varchar"
    assert meta.trino_type_name(LongType()) == "bigint"
    assert meta.trino_type_name(DoubleType()) == "double"
    assert meta.trino_type_name(DecimalType(18, 2)) == "decimal(18, 2)"
    assert meta.trino_type_name(TimestamptzType()) == "timestamp(6) with time zone"
    assert meta.trino_type_name(ListType(element_id=2, element_type=StringType())) == "array(varchar)"
    assert meta.trino_type_name(
        MapType(key_id=3, key_type=StringType(), value_id=4, value_type=LongType())
    ) == "map(varchar, bigint)"
    assert meta.trino_type_name(StructType(field("A", LongType(), 5))) == "row(a bigint)"


def test_schema_vem_minusculo_e_fica_em_cache_enquanto_o_snapshot_nao_muda(monkeypatch):
    tables = {"v1": FakeTable(1, [field("BilledCost", DoubleType()), field("_billing_period", StringType(), 2)])}
    loads = {"n": 0}

    def load(_ns, _table):
        loads["n"] += 1
        return tables["v1"]

    monkeypatch.setattr(meta, "_load_table_sync", load)
    first = asyncio.run(meta.table_schema("silver", "dest_billing_cloud"))
    assert first == {"billedcost": "double", "_billing_period": "varchar"}
    # Mesmo snapshot: o schema cacheado é servido mesmo se a leitura mudasse.
    tables["v1"]._fields = [field("Outra", StringType())]
    assert asyncio.run(meta.table_schema("silver", "dest_billing_cloud")) == first
    assert loads["n"] == 2  # a versão é relida; o schema não é recalculado
    # Snapshot novo: recalcula.
    tables["v1"] = FakeTable(2, [field("Outra", StringType())])
    assert asyncio.run(meta.table_schema("silver", "dest_billing_cloud")) == {"outra": "varchar"}


def test_ultimo_periodo_sai_das_particoes_sem_fallback(monkeypatch):
    table = FakeTable(
        7, [field("_billing_period", StringType())], partition_names=("_billing_period", "source_connection_id"),
        partitions=[{"_billing_period": "202609", "source_connection_id": 4}, {"_billing_period": "202610", "source_connection_id": 4}],
    )
    monkeypatch.setattr(meta, "_load_table_sync", lambda *_: table)

    async def fallback():
        raise AssertionError("não deveria consultar o Trino")

    assert asyncio.run(meta.latest_period("silver", "dest_billing_cloud", "_billing_period", fallback=fallback)) == "202610"


def test_ultimo_periodo_usa_fallback_uma_vez_por_snapshot(monkeypatch):
    holder = {"table": FakeTable(1, [field("period", StringType())])}
    monkeypatch.setattr(meta, "_load_table_sync", lambda *_: holder["table"])
    calls = {"n": 0}

    async def fallback():
        calls["n"] += 1
        return "202610"

    for _ in range(3):
        assert asyncio.run(meta.latest_period("silver", "planning", "period", fallback=fallback)) == "202610"
    assert calls["n"] == 1
    holder["table"] = FakeTable(2, [field("period", StringType())])
    asyncio.run(meta.latest_period("silver", "planning", "period", fallback=fallback))
    assert calls["n"] == 2


def test_invalidate_esquece_a_tabela(monkeypatch):
    table = FakeTable(1, [field("a", StringType())])
    monkeypatch.setattr(meta, "_load_table_sync", lambda *_: table)
    asyncio.run(meta.table_schema("gold", "cubo"))
    assert meta.invalidate("gold", "cubo") >= 1
    assert meta.invalidate("gold", "cubo") == 0
