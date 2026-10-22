"""
Script de limpeza de pastas órfãs de staging no repositório Iceberg local.

Identifica e remove com segurança todos os diretórios temporários (*_stg_*)
que não correspondem a nenhuma tabela ativa no catálogo SQL do PyIceberg.
Preserva 100% das tabelas ativas, snapshots da Gold, Bronze, Silver, Forecast e Gold.

Uso:
    PYTHONPATH=apps/api ./.venv/bin/python apps/api/scripts/cleanup_orphan_staging.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

# Ajustar sys.path para importar módulos do app
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

# Carregar variáveis de ambiente do .env do app se não estiverem no ambiente
from dotenv import load_dotenv
env_file = API_DIR / ".env"
if env_file.exists():
    load_dotenv(str(env_file))

from app.services.iceberg_service import _get_catalog, _LOCAL_WAREHOUSE


def format_bytes(b: int) -> str:
    """Formata bytes para exibição legível (B, KB, MB, GB)."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024**2:
        return f"{b / 1024:.2f} KB"
    elif b < 1024**3:
        return f"{b / (1024**2):.2f} MB"
    return f"{b / (1024**3):.2f} GB"


def get_dir_size(path: Path) -> int:
    """Calcula o tamanho total em bytes de um diretório recursivamente."""
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


def run_cleanup(dry_run: bool = False) -> None:
    print("=" * 70)
    print("INICIANDO LIMPEZA DE PASTAS ÓRFÃS DE STAGING NO ICEBERG")
    print(f"Modo: {'SIMULAÇÃO (DRY RUN)' if dry_run else 'EXECUÇÃO REAL'}")
    print(f"Horário de início: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    warehouse_path = Path(_LOCAL_WAREHOUSE).resolve()
    print(f"\n[1/4] Diretório Warehouse: {warehouse_path}")
    if not warehouse_path.exists():
        print("ERRO: Diretório warehouse não encontrado!")
        return

    # 1. Carregar todas as tabelas ativas em todos os namespaces
    print("\n[2/4] Conectando ao catálogo PyIceberg e mapeando tabelas ativas...")
    cat = _get_catalog()
    active_locations: set[str] = set()
    active_table_count = 0

    known_namespaces = ["gold", "silver", "bronze", "financial_base", "forecast", "gold"]
    for ns in known_namespaces:
        try:
            tables = cat.list_tables(ns)
            for t in tables:
                table_name = t[1] if isinstance(t, tuple) else t
                tid = (ns, table_name)
                try:
                    tbl = cat.load_table(tid)
                    loc = tbl.location().replace("file://", "")
                    real_loc = os.path.realpath(loc)
                    active_locations.add(real_loc)
                    active_table_count += 1
                except Exception as exc:
                    print(f"   [AVISO] Não foi possível carregar tabela {tid}: {exc}")
        except Exception as exc:
            print(f"   [AVISO] Erro ao listar namespace {ns}: {exc}")

    print(f"   Total de tabelas ativas identificadas no catálogo: {active_table_count}")
    print(f"   Total de caminhos físicos ativos protegidos: {len(active_locations)}")

    # 2. Varrer o warehouse identificando diretórios de staging órfãos
    print("\n[3/4] Varrendo diretórios para detectar pastas órfãs com padrão '*_stg_*'...")
    orphan_dirs: list[tuple[Path, int]] = []
    total_scanned = 0

    for db_dir in warehouse_path.glob("*"):
        if not db_dir.is_dir():
            continue
        for tbl_dir in db_dir.glob("*"):
            if not tbl_dir.is_dir():
                continue
            total_scanned += 1
            dir_name = tbl_dir.name
            real_path = os.path.realpath(str(tbl_dir))

            # Se for uma tabela ativa no catálogo, NUNCA toca
            if real_path in active_locations:
                continue

            # Se for pasta temporária de staging
            if "_stg_" in dir_name:
                size = get_dir_size(tbl_dir)
                orphan_dirs.append((tbl_dir, size))

    total_orphan_bytes = sum(size for _, size in orphan_dirs)
    print(f"   Pastas inspecionadas: {total_scanned}")
    print(f"   Pastas órfãs de staging encontradas: {len(orphan_dirs)}")
    print(f"   Espaço total ocupado por pastas órfãs: {format_bytes(total_orphan_bytes)}")

    if not orphan_dirs:
        print("\nNenhuma pasta órfã encontrada. O repositório já está limpo!")
        return

    # 3. Execução da remoção
    print(f"\n[4/4] {'Simulando' if dry_run else 'Executando'} exclusão das pastas órfãs...")
    deleted_count = 0
    deleted_bytes = 0
    start_time = time.time()

    for idx, (path, size) in enumerate(orphan_dirs, start=1):
        if not dry_run:
            try:
                shutil.rmtree(path, ignore_errors=True)
                deleted_count += 1
                deleted_bytes += size
            except Exception as e:
                print(f"   [FALHA] Erro ao remover {path}: {e}")
        else:
            deleted_count += 1
            deleted_bytes += size

        # Log a cada 200 itens para não poluir demais a saída
        if idx % 200 == 0 or idx == len(orphan_dirs):
            elapsed = time.time() - start_time
            print(f"   Progresso: {idx}/{len(orphan_dirs)} pastas removidas "
                  f"({format_bytes(deleted_bytes)} liberados em {elapsed:.1f}s)")

    # 4. Relatório final e validação de integridade
    print("\n" + "=" * 70)
    print("RELATÓRIO FINAL DA LIMPEZA")
    print("=" * 70)
    print(f"Pastas removidas com sucesso: {deleted_count}")
    print(f"Espaço total liberado no disco: {format_bytes(deleted_bytes)}")
    print(f"Tempo de execução: {time.time() - start_time:.2f} segundos")

    # Validação de integridade pós-limpeza da tabela principal
    print("\nVerificando integridade das tabelas ativas...")
    try:
        tbl_focus = cat.load_table(("gold", "dest_billing_cloud"))
        print(f"   [OK] Tabela gold.dest_billing_cloud acessível!")
        print(f"   [OK] Localização: {tbl_focus.location()}")
        print(f"   [OK] Quantidade de Snapshots mantidos: {len(tbl_focus.snapshots())}")
        current_snap = tbl_focus.current_snapshot()
        print(f"   [OK] Snapshot Atual ID: {current_snap.snapshot_id if current_snap else 'Nenhum'}")
    except Exception as exc:
        print(f"   [ERRO CRÍTICO] Falha ao verificar gold.dest_billing_cloud: {exc}")

    print("=" * 70)


if __name__ == "__main__":
    is_dry_run = "--dry-run" in sys.argv
    run_cleanup(dry_run=is_dry_run)
