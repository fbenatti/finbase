"""Manutenção Iceberg pós-publicação: fila, plano e execução guardada."""
import asyncio

from app.services import iceberg_maintenance as maint
from app.services import trino_service


def setup_function(_fn):
    maint._take_pending()


def test_publicacao_enfileira_a_tabela_uma_vez():
    maint.request_maintenance_after_publish("silver", "dest-billing.cloud")
    maint.request_maintenance_after_publish("silver", "dest_billing_cloud")
    assert maint.pending_tables() == [("silver", "dest_billing_cloud")]


def test_plano_tem_os_quatro_passos_com_retencao_minima_do_catalogo():
    steps = maint.plan_statements("silver", "dest_billing_cloud", retention="7d", small_file_threshold="64MB")
    labels = [label for label, _sql, _props in steps]
    assert labels == ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"]
    sqls = {label: sql for label, sql, _ in steps}
    assert "file_size_threshold => '64MB'" in sqls["optimize"]
    assert "retention_threshold => '7d'" in sqls["expire_snapshots"]
    props = {label: p for label, _, p in steps}
    assert props["expire_snapshots"] == {"iceberg.expire_snapshots_min_retention": "7d"}
    assert props["remove_orphan_files"] == {"iceberg.remove_orphan_files_min_retention": "7d"}


def test_sem_apply_so_registra_o_plano(monkeypatch):
    monkeypatch.setattr(maint, "table_state", lambda ns, t: {"snapshots": 86, "manifests": 172, "data_files": 49, "records": 372062})
    executed = []
    monkeypatch.setattr(trino_service, "execute_sync", lambda *a, **k: executed.append(a))
    report = maint.maintain_table("silver", "dest_billing_cloud", apply=False)
    assert report["applied"] is False and len(report["plan"]) == 4
    assert report["before"]["snapshots"] == 86
    assert executed == []


def test_apply_nao_roda_com_escritor_ativo_e_devolve_a_tabela_para_a_fila(monkeypatch):
    monkeypatch.setattr(maint, "table_state", lambda ns, t: {"snapshots": 1})
    monkeypatch.setattr(maint, "writers_active", lambda: True)
    monkeypatch.setattr(trino_service, "execute_sync", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria executar")))
    report = maint.maintain_table("silver", "dest_billing_cloud", apply=True)
    assert report["applied"] is False and "reprocess" in report["skipped"]
    assert maint.pending_tables() == [("silver", "dest_billing_cloud")]


def test_apply_executa_os_passos_sob_o_lock_com_propriedades_de_sessao(monkeypatch):
    import contextlib

    from app.services import write_lock

    monkeypatch.setattr(maint, "table_state", lambda ns, t: {"snapshots": 2})
    monkeypatch.setattr(maint, "writers_active", lambda: False)
    monkeypatch.setattr(write_lock, "exclusive_write_lock", contextlib.nullcontext)
    calls = []
    monkeypatch.setattr(trino_service, "execute_sync", lambda sql, **kw: calls.append((sql, kw.get("session_properties"))))
    report = maint.maintain_table("silver", "dest_billing_cloud", apply=True)
    assert report["applied"] is True and all(step["ok"] for step in report["steps"])
    assert len(calls) == 4
    assert calls[2][1] == {"iceberg.expire_snapshots_min_retention": maint.RETENTION}
    assert calls[0][1] is None


def test_execute_passa_propriedades_de_sessao_no_cabecalho(monkeypatch):
    seen = {}

    async def protocol(client, sql, headers, timeout_s, cancel_event):
        seen.update(headers)
        return "id", []

    monkeypatch.setattr(trino_service, "_execute_protocol", protocol)
    monkeypatch.setattr(trino_service, "TRINO_QUERY_PHASES", False)
    asyncio.run(trino_service._execute_once("SELECT 1", session_properties={"a": "1", "b": "x"}))
    assert seen["X-Trino-Session"] == "a=1,b=x"
