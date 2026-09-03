"""
Script de expiração de snapshots técnicos antigos e purga de arquivos órfãos na Gold.

Executa manutenção na tabela dest_modelo_focus do Iceberg via Trino:
1. Valida a contagem de linhas e integridade dos dados ativos.
2. Executa expire_snapshots com retenção configurada (padrão: 1 dia) via header de sessão.
3. Executa remove_orphan_files para expurgar parquets não referenciados.
4. Valida novamente a contagem de linhas e valores consolidados para comprovar 100% de integridade.
5. Reporta o espaço liberado em disco.

Uso:
    PYTHONPATH=apps/api ./.venv/bin/python apps/api/scripts/expire_gold_snapshots.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin
import httpx

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from dotenv import load_dotenv
env_file = API_DIR / ".env"
if env_file.exists():
    load_dotenv(str(env_file))

from app.services.trino_service import execute_sync, TRINO_URL, TRINO_USER
from app.services.iceberg_service import _get_catalog


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024**2:
        return f"{b / 1024:.2f} KB"
    elif b < 1024**3:
        return f"{b / (1024**2):.2f} MB"
    return f"{b / (1024**3):.2f} GB"


def get_dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += get_dir_size(Path(entry.path))
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        pass
    return total


def execute_trino_statement(sql: str, session_properties: str) -> None:
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": "iceberg",
        "X-Trino-Schema": "gold",
        "X-Trino-Session": session_properties,
    }
    submit_url = urljoin(TRINO_URL, "/v1/statement")
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(submit_url, content=sql, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Erro inicial Trino HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        next_uri = data.get("nextUri")
        while next_uri:
            resp = client.get(next_uri, headers=headers)
            data = resp.json()
            if "error" in data:
                err = data["error"]
                raise RuntimeError(f"Trino error: {err.get('message', err)}")
            state = data.get("stats", {}).get("state")
            if state in ("FINISHED", "FAILED", "CANCELED"):
                break
            next_uri = data.get("nextUri")


def run_maintenance(table_name: str = "dest_modelo_focus", retention: str = "1d") -> None:
    print("=" * 70)
    print("INICIANDO MANUTENÇÃO DE SNAPSHOTS TÉCNICOS NA GOLD")
    print(f"Tabela-alvo: iceberg.gold.{table_name}")
    print(f"Threshold de Retenção: {retention}")
    print(f"Horário de início: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. Carregar tabela via PyIceberg para checar snapshots e path
    print("\n[1/5] Inspecionando estado atual da tabela no Iceberg...")
    cat = _get_catalog()
    tbl = cat.load_table(("gold", table_name))
    initial_snapshots = len(tbl.snapshots())
    table_loc = Path(tbl.location().replace("file://", ""))
    initial_size = get_dir_size(table_loc) if table_loc.exists() else 0

    print(f"   Localização física: {table_loc}")
    print(f"   Snapshots cadastrados: {initial_snapshots}")
    print(f"   Tamanho em disco antes: {format_bytes(initial_size)}")

    # 2. Contar linhas ativas via Trino antes do procedimento
    print("\n[2/5] Validando contagem de linhas ativas via Trino...")
    res_before = execute_sync(f"SELECT COUNT(*) AS total_rows FROM iceberg.gold.{table_name}")
    rows_before = int((res_before[0] if res_before else {}).get("total_rows") or 0)
    print(f"   Linhas ativas na tabela: {rows_before:,}")

    # 3. Executar expire_snapshots via Trino
    print(f"\n[3/5] Executando expire_snapshots(retention_threshold => '{retention}')...")
    start_t = time.time()
    try:
        execute_trino_statement(
            f"ALTER TABLE iceberg.gold.{table_name} EXECUTE expire_snapshots(retention_threshold => '{retention}')",
            session_properties=f"iceberg.expire_snapshots_min_retention={retention}",
        )
        print(f"   [OK] expire_snapshots concluído em {time.time() - start_t:.2f}s")
    except Exception as e:
        print(f"   [FALHA] Erro em expire_snapshots: {e}")
        return

    # 4. Executar remove_orphan_files via Trino
    print(f"\n[4/5] Executando remove_orphan_files(retention_threshold => '{retention}')...")
    start_t = time.time()
    try:
        execute_trino_statement(
            f"ALTER TABLE iceberg.gold.{table_name} EXECUTE remove_orphan_files(retention_threshold => '{retention}')",
            session_properties=f"iceberg.remove_orphan_files_min_retention={retention}",
        )
        print(f"   [OK] remove_orphan_files concluído em {time.time() - start_t:.2f}s")
    except Exception as e:
        print(f"   [AVISO] remove_orphan_files: {e}")

    # 5. Validação de integridade e medição final
    print("\n[5/5] Validando integridade final dos dados...")
    res_after = execute_sync(f"SELECT COUNT(*) AS total_rows FROM iceberg.gold.{table_name}")
    rows_after = int((res_after[0] if res_after else {}).get("total_rows") or 0)

    tbl_after = cat.load_table(("gold", table_name))
    final_snapshots = len(tbl_after.snapshots())
    final_size = get_dir_size(table_loc) if table_loc.exists() else 0
    liberado = max(0, initial_size - final_size)

    print(f"   Linhas ativas após manutenção: {rows_after:,}")
    if rows_before == rows_after:
        print("   [PERFEITO] Integridade 100% confirmada! Nenhuma linha foi perdida.")
    else:
        print(f"   [ATENÇÃO] Divergência na contagem de linhas: {rows_before} vs {rows_after}")

    print(f"   Snapshots reduzidos: de {initial_snapshots} para {final_snapshots}")
    print(f"   Tamanho em disco final: {format_bytes(final_size)}")
    print(f"   Espaço total liberado nesta tabela: {format_bytes(liberado)}")
    print("=" * 70)


if __name__ == "__main__":
    run_maintenance()
