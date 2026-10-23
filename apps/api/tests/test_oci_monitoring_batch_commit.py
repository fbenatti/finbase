"""OCI Monitoring: várias janelas num único commit da Bronze."""
from types import SimpleNamespace

import pandas as pd

from app.services import iceberg_service as ice
from app.services import task_runner


class _Tx:
    def __init__(self, log):
        self.log = log

    def delete(self, row_filter):
        self.log.append(("delete", str(row_filter)))

    def append(self, arrow_table):
        self.log.append(("append", arrow_table.num_rows))

    def commit_transaction(self):
        self.log.append(("commit", None))


class _FakeTable:
    def __init__(self, log):
        self.log = log
        from pyiceberg.schema import Schema
        from pyiceberg.types import NestedField, StringType

        self._schema = Schema(
            NestedField(1, "valor", StringType()), NestedField(2, "_billing_period", StringType()),
            NestedField(3, "_blob_path", StringType()), NestedField(4, "_ingested_at", StringType()),
        )

    def schema(self):
        return self._schema

    def transaction(self):
        return _Tx(self.log)


def test_replace_bronze_slices_faz_um_commit_para_varias_janelas(monkeypatch):
    log: list = []
    table = _FakeTable(log)
    monkeypatch.setattr(ice, "_get_catalog", lambda: SimpleNamespace(load_table=lambda tid: table))
    monkeypatch.setattr(ice, "_schema_has_case_collisions", lambda schema: False)
    monkeypatch.setattr(ice, "_align_columns_to_existing_schema", lambda frame, schema: frame)
    import contextlib

    from app.services import write_lock

    monkeypatch.setattr(write_lock, "exclusive_write_lock", contextlib.nullcontext)
    rows = ice.replace_bronze_slices("oci-mon", [
        (pd.DataFrame({"valor": ["1", "2"]}), "202610", "oci-monitoring/1h/a"),
        (pd.DataFrame({"valor": ["3"]}), "202610", "oci-monitoring/1h/b"),
        (pd.DataFrame(), "202610", "vazia"),
    ])
    assert rows == 3
    kinds = [kind for kind, _ in log]
    assert kinds == ["delete", "append", "commit"]
    delete_filter = log[0][1]
    assert "oci-monitoring/1h/a" in delete_filter and "oci-monitoring/1h/b" in delete_filter and "vazia" not in delete_filter
    assert log[1][1] == 3


class _Session:
    def __init__(self):
        self.added = []

    def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def delete(self, _obj):
        pass

    def flush(self):
        pass

    def add(self, obj):
        self.added.append(obj)


def _window(day: int) -> str:
    """Uma janela diária por índice (o caminho tem granularidade de dia)."""
    from datetime import datetime, timedelta, timezone

    from app.services.oci_monitoring_extractor import blob_path_for

    start = datetime(2026, 10, 1 + day, tzinfo=timezone.utc)
    return blob_path_for("1h", start, start + timedelta(days=1))


def _extractor(frames: dict[str, pd.DataFrame]):
    from datetime import datetime, timezone

    def fetch_window(blob_path, should_abort=None, progress=None):
        return frames[blob_path], datetime(2026, 10, 23, tzinfo=timezone.utc), []

    return SimpleNamespace(connection=SimpleNamespace(id=7, code="oci-mon"), fetch_window=fetch_window)


def test_lote_grava_uma_vez_e_registra_proveniencia_depois_do_commit(monkeypatch):
    writes = []
    monkeypatch.setattr(task_runner.iceberg, "replace_bronze_slices", lambda code, slices: writes.append((code, [b for _d, _p, b in slices])) or 3)
    monkeypatch.setattr(task_runner, "_persist_download_progress", lambda *a, **k: None)
    monkeypatch.setattr(task_runner, "_persist_download_blob", lambda *a, **k: None)
    monkeypatch.setattr(task_runner, "_check_cancelled", lambda _task: False)
    items = [SimpleNamespace(blob_path=_window(i), blob_prefix="oci-monitoring") for i in range(3)]
    frames = {items[0].blob_path: pd.DataFrame({"v": [1, 2]}), items[1].blob_path: pd.DataFrame(), items[2].blob_path: pd.DataFrame({"v": [3]})}
    session = _Session()
    raw, _norm, _dest, errors = task_runner._process_oci_monitoring_batch_sync(session, _extractor(frames), items, task_id=1)
    assert raw == 3 and errors == []
    assert writes == [("oci-mon", [items[0].blob_path, items[2].blob_path])]
    assert sorted(blob.blob_path for blob in session.added) == sorted(item.blob_path for item in items)


def test_falha_no_commit_nao_marca_janelas_como_ingeridas(monkeypatch):
    def boom(code, slices):
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(task_runner.iceberg, "replace_bronze_slices", boom)
    monkeypatch.setattr(task_runner, "_persist_download_progress", lambda *a, **k: None)
    monkeypatch.setattr(task_runner, "_persist_download_blob", lambda *a, **k: None)
    monkeypatch.setattr(task_runner, "_check_cancelled", lambda _task: False)
    items = [SimpleNamespace(blob_path=_window(0), blob_prefix="oci-monitoring")]
    session = _Session()
    raw, _n, _d, errors = task_runner._process_oci_monitoring_batch_sync(session, _extractor({items[0].blob_path: pd.DataFrame({"v": [1]})}), items, task_id=1)
    assert raw == 1 and any("Bronze write failed" in e for e in errors)
    assert session.added == []
