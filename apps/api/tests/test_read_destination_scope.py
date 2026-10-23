"""``read_destination`` não lê mais a tabela inteira sem pedido explícito."""
import pandas as pd
import pytest

from app.services import iceberg_service as ice


class _Scan:
    def __init__(self, frame, calls, kwargs):
        self._frame = frame
        calls.append(kwargs)

    def to_pandas(self):
        return self._frame.copy()


class _FakeTable:
    def __init__(self, frame):
        self._frame = frame
        self.calls: list[dict] = []

    def scan(self, **kwargs):
        return _Scan(self._frame, self.calls, kwargs)


class _FakeCatalog:
    def __init__(self, table):
        self.table = table

    def load_table(self, tid):
        if self.table is None:
            raise RuntimeError("NoSuchTableError")
        return self.table


def test_full_scan_requires_explicit_consent(monkeypatch):
    table = _FakeTable(pd.DataFrame({"a": [1]}))
    monkeypatch.setattr(ice, "_get_catalog", lambda: _FakeCatalog(table))
    with pytest.raises(ValueError):
        ice.read_destination("dest_billing_cloud")
    assert table.calls == []
    frame = ice.read_destination("dest_billing_cloud", allow_full_scan=True)
    assert len(frame) == 1 and table.calls == [{}]


def test_periods_and_columns_become_partition_filter_and_projection(monkeypatch):
    table = _FakeTable(pd.DataFrame({"a": [1]}))
    monkeypatch.setattr(ice, "_get_catalog", lambda: _FakeCatalog(table))
    ice.read_destination("dest_billing_cloud", billing_periods=["202609", "202610"], columns=["a", "b"])
    (call,) = table.calls
    assert call["selected_fields"] == ("a", "b")
    assert "_billing_period" in str(call["row_filter"]) and "202610" in str(call["row_filter"])


def test_missing_table_returns_empty_frame(monkeypatch):
    monkeypatch.setattr(ice, "_get_catalog", lambda: _FakeCatalog(None))
    assert ice.read_destination("nada", billing_periods=["202610"]).empty
