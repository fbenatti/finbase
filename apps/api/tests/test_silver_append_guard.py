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
