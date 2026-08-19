#!/usr/bin/env python3
"""
Script para escanear a conexão OCI e disparar a ingestão dos relatórios no OpenFinops.
Logs detalhados de output.
"""
import httpx
import time
from datetime import datetime

def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def main():
    log("=" * 60)
    log("INICIANDO SCAN E INGESTAO OCI NO OPENFINOPS")
    log("=" * 60)

    # 1. Obter conexão OCI
    conns = httpx.get("http://localhost:5173/api/connections/").json()
    oci_conn = next((c for c in conns if c.get("connector_type") == "oci"), None)
    if not oci_conn:
        log("[ERRO] Nenhuma conexão OCI encontrada em /api/connections/")
        return

    conn_id = oci_conn["id"]
    log(f"[INFO] Conexão OCI encontrada: ID {conn_id} - '{oci_conn.get('name')}'")

    # 2. Executar Scan
    log(f"[INFO] Executando scan de relatórios OCI...")
    scan_res = httpx.post(f"http://localhost:5173/api/etl/connections/{conn_id}/scan", timeout=120)
    scan_data = scan_res.json()
    total = scan_data.get("total", 0)
    log(f"[OK] Scan concluído! Total de relatórios disponíveis na Tenancy: {total}")

    items = scan_data.get("items", [])
    if not items:
        log("[AVISO] Nenhum item retornado no scan.")
        return

    # Amostra
    log("[INFO] Primeiros 3 relatórios encontrados:")
    for it in items[:3]:
        log(f"   * {it.get('blob_path')} ({it.get('content_length')} bytes, período: {it.get('billing_period')})")

    # 3. Disparar Ingestão (pegar os 3 primeiros para teste imediato)
    selected_blobs = [it.get("blob_path") for it in items[:3]]
    log(f"[INFO] Disparando carga de {len(selected_blobs)} relatórios OCI...")

    run_payload = {
        "connection_id": conn_id,
        "selected_blobs": selected_blobs,
        "force_periods": []
    }

    run_res = httpx.post("http://localhost:5173/api/etl/run", json=run_payload, timeout=30)
    run_data = run_res.json()
    task_id = run_data.get("task_id")
    log(f"[OK] Tarefa criada com sucesso! Task ID: {task_id}")

    if not task_id:
        log(f"[ERRO] Resposta sem task_id: {run_data}")
        return

    # 4. Acompanhar execução
    log(f"[INFO] Acompanhando processamento da tarefa {task_id}...")
    for _ in range(60):
        st = httpx.get(f"http://localhost:5173/api/etl/tasks/{task_id}", timeout=10).json()
        status = st.get("status")
        phase = st.get("phase")
        processed = st.get("processed_blobs", 0)
        total_blobs = st.get("total_blobs", 0)
        rows = st.get("rows_raw", 0)

        log(f"   [STATUS] {status} | Fase: {phase} | Blobs: {processed}/{total_blobs} | Linhas Bronze: {rows}")

        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            if st.get("errors"):
                log(f"[ERROS] {st.get('errors')}")
            break
        time.sleep(2)

    log("=" * 60)
    log("[CONCLUIDO] PROCESSO DE CARGA OCI FINALIZADO COM SUCESSO!")
    log("=" * 60)

if __name__ == "__main__":
    main()
