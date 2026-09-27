import pandas as pd
import pytest

from app.services import iceberg_service as iceberg


def test_write_destination_blocks_silver_dest_append():
    frame = pd.DataFrame({
        "_billing_period": ["202501"],
        "source_connection_id": [1],
        "cost": [1.0],
    })
    with pytest.raises(RuntimeError, match="desabilitado"):
        iceberg.write_destination("dest_modelo_focus", frame)


def test_publish_appends_staged_batches_in_one_snapshot():
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField
    import pyarrow as pa

    class Target:
        def schema(self):
            return Schema(NestedField(1, "id", LongType(), required=False))

    class Tx:
        def __init__(self):
            self.appended = []

        def append(self, table):
            self.appended.append(table.num_rows)

    batches = [pa.record_batch({"id": pa.array([i, i + 1], pa.int64())}) for i in range(0, 200, 2)]
    batches.append(pa.record_batch({"id": pa.array([], pa.int64())}))

    tx = Tx()
    assert iceberg._append_staged_batches(tx, batches, Target()) == 1
    assert tx.appended == [200]

    tx = Tx()
    assert iceberg._append_staged_batches(tx, batches, Target(), max_rows=50) == 4
    assert sum(tx.appended) == 200
