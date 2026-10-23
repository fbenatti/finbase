"""Manutenção de tabelas Iceberg depois de publicar: manifests, snapshots, órfãos e arquivos pequenos.

Por que existe: cada publicação deixa um snapshot e manifests novos, e
``silver.dest_billing_cloud`` chegou a 86 snapshots e 172 manifests para 49
parquet (metade com menos de 256 KB). O Trino paga isso no planejamento de toda
query: ``DESCRIBE`` em 6 s, ``COUNT(*)`` em 2,5 s para 372 k linhas.

Compactar reescreve dados e expirar snapshots é irreversível, então nada roda
sozinho. A publicação só **pede** manutenção (``request_maintenance_after_publish``),
o scheduler drena a fila a cada ``ICEBERG_MAINTENANCE_INTERVAL_MIN`` minutos e,
por padrão, registra no log o estado da tabela e os statements que executaria.
Com ``ICEBERG_MAINTENANCE_APPLY=true`` ele executa de fato, sob o lock exclusivo
de escrita e nunca enquanto houver reprocess ou publicação analítica ativa.

A retenção padrão é ``7d`` porque o catálogo compartilhado impõe esse mínimo
(``iceberg.expire-snapshots.min-retention`` / ``iceberg.remove-orphan-files.min-retention``):
pedir menos falha no coordenador, como o log já mostrou com ``retention 1s``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

APPLY = os.getenv("ICEBERG_MAINTENANCE_APPLY", "false").lower() == "true"
RETENTION = os.getenv("ICEBERG_MAINTENANCE_RETENTION", "7d")
SMALL_FILE_THRESHOLD = os.getenv("ICEBERG_MAINTENANCE_SMALL_FILE", "64MB")
INTERVAL_MINUTES = max(1, int(os.getenv("ICEBERG_MAINTENANCE_INTERVAL_MIN", "15")))

#: Tipos de tarefa que escrevem nas tabelas analíticas. Manutenção não roda com
#: nenhuma delas em fila ou execução.
WRITER_TASK_TYPES = (
    "reprocess", "reclassification", "gold_reprocess", "shared_cost", "forecast",
    "forecast_accuracy", "ai_cost_relationship_reprocess",
)

_lock = threading.Lock()
_pending: dict[tuple[str, str], float] = {}


def request_maintenance_after_publish(namespace: str, table: str) -> None:
    """Marca a tabela para manutenção na próxima rodada do scheduler. Barato e idempotente."""
    key = (str(namespace), str(table).replace("-", "_").replace(".", "_"))
    with _lock:
        _pending[key] = time.time()
    logger.info("Manutenção Iceberg pedida para %s.%s", *key)


def pending_tables() -> list[tuple[str, str]]:
    with _lock:
        return sorted(_pending)


def _take_pending() -> list[tuple[str, str]]:
    with _lock:
        keys = sorted(_pending)
        _pending.clear()
    return keys


def table_state(namespace: str, table: str) -> dict[str, Any]:
    """Snapshots, manifests e arquivos da tabela, lidos do catálogo (sem Trino)."""
    from app.services import iceberg_service as iceberg

    tbl = iceberg.get_catalog().load_table(iceberg._table_id(namespace, table))
    snapshot = tbl.current_snapshot()
    summary = dict(snapshot.summary.additional_properties) if snapshot and snapshot.summary else {}
    manifests = len(snapshot.manifests(tbl.io)) if snapshot else 0
    return {
        "snapshots": len(tbl.metadata.snapshots),
        "manifests": manifests,
        "data_files": int(summary.get("total-data-files") or 0),
        "records": int(summary.get("total-records") or 0),
        "size_bytes": int(summary.get("total-files-size") or 0),
        "properties": dict(tbl.properties),
    }


def plan_statements(
    namespace: str,
    table: str,
    *,
    retention: str = RETENTION,
    small_file_threshold: str = SMALL_FILE_THRESHOLD,
) -> list[tuple[str, str, dict[str, str]]]:
    """``(rótulo, SQL, propriedades de sessão)`` na ordem em que devem rodar.

    1. ``optimize`` junta arquivos menores que o limiar (reescreve dados, não apaga história);
    2. ``optimize_manifests`` reescreve manifests pequenos num só;
    3. ``expire_snapshots`` apaga snapshots fora da retenção (irreversível);
    4. ``remove_orphan_files`` apaga arquivos que nenhum snapshot referencia (irreversível).
    """
    fqtn = f"iceberg.{namespace}.{table}"
    return [
        ("optimize", f"ALTER TABLE {fqtn} EXECUTE optimize(file_size_threshold => '{small_file_threshold}')", {}),
        ("optimize_manifests", f"ALTER TABLE {fqtn} EXECUTE optimize_manifests", {}),
        (
            "expire_snapshots",
            f"ALTER TABLE {fqtn} EXECUTE expire_snapshots(retention_threshold => '{retention}')",
            {"iceberg.expire_snapshots_min_retention": retention},
        ),
        (
            "remove_orphan_files",
            f"ALTER TABLE {fqtn} EXECUTE remove_orphan_files(retention_threshold => '{retention}')",
            {"iceberg.remove_orphan_files_min_retention": retention},
        ),
    ]


def writers_active() -> bool:
    """Há reprocess/publicação analítica em fila ou execução? Em dúvida, sim."""
    try:
        from sqlalchemy import text

        from app.database import SyncSession

        session = SyncSession()
        try:
            types = ", ".join(f"'{name}'" for name in WRITER_TASK_TYPES)
            return bool(session.execute(text(
                "SELECT EXISTS (SELECT 1 FROM etl_task WHERE status IN ('QUEUED', 'RUNNING') "
                f"AND task_type IN ({types}))"
            )).scalar())
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 — sem resposta do banco, não mexer na tabela
        logger.warning("Estado dos escritores desconhecido (%s); manutenção adiada", exc)
        return True


def maintain_table(namespace: str, table: str, *, apply: bool | None = None) -> dict[str, Any]:
    """Planeja e, se ``apply``, executa a manutenção de uma tabela. Devolve o relatório."""
    from app.services.trino_service import execute_sync

    apply = APPLY if apply is None else apply
    report: dict[str, Any] = {"table": f"{namespace}.{table}", "applied": False, "steps": []}
    try:
        report["before"] = table_state(namespace, table)
    except Exception as exc:  # noqa: BLE001
        report["skipped"] = f"metadados indisponíveis: {exc}"
        return report
    statements = plan_statements(namespace, table)
    report["plan"] = [sql for _label, sql, _props in statements]
    if not apply:
        report["skipped"] = "ICEBERG_MAINTENANCE_APPLY não está ligado; só o plano foi registrado"
        return report
    if writers_active():
        report["skipped"] = "há reprocess ou publicação analítica em fila/execução"
        # Volta para a fila: a próxima rodada tenta de novo.
        request_maintenance_after_publish(namespace, table)
        return report

    from app.services.write_lock import exclusive_write_lock

    with exclusive_write_lock():
        for label, sql, props in statements:
            started = time.monotonic()
            try:
                execute_sync(sql, timeout_s=900, session_properties=props or None)
                report["steps"].append({"step": label, "ok": True, "seconds": round(time.monotonic() - started, 1)})
            except Exception as exc:  # noqa: BLE001 — um passo falhar não impede o relatório
                report["steps"].append({"step": label, "ok": False, "error": str(exc)[:300]})
                logger.warning("Manutenção %s em %s.%s falhou: %s", label, namespace, table, exc)
    report["applied"] = True
    try:
        report["after"] = table_state(namespace, table)
    except Exception as exc:  # noqa: BLE001
        report["after_error"] = str(exc)
    return report


def run_pending_maintenance_sync() -> list[dict[str, Any]]:
    """Drena a fila pedida pelas publicações. Chamado pelo scheduler."""
    reports = []
    for namespace, table in _take_pending():
        report = maintain_table(namespace, table)
        reports.append(report)
        before = report.get("before") or {}
        logger.info(
            "Manutenção Iceberg %s.%s: %s | snapshots=%s manifests=%s arquivos=%s linhas=%s%s",
            namespace, table,
            "aplicada" if report.get("applied") else "só plano",
            before.get("snapshots"), before.get("manifests"), before.get("data_files"), before.get("records"),
            f" | {report['skipped']}" if report.get("skipped") else "",
        )
        for sql in report.get("plan", []):
            logger.info("  plano: %s", sql)
    return reports
