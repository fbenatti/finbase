#!/usr/bin/env python3
import json
import time
import urllib.request
import urllib.error
import sys

API_BASE = "http://localhost:8001"

FAILED_SOURCES = [
    {"conn_id": 9, "name": "Oracle Cloud (OCI) - Produção (oci-prod)", "blob": 202, "period": "202608"},
    {"conn_id": 5, "name": "MongoDB Atlas Pro (mongodb-atlas-pro)", "blob": 630, "period": "202608"},
    {"conn_id": 11, "name": "MongoDB Atlas Enterprise (mongodb-atlas-enterprise)", "blob": 638, "period": "202608"},
    {"conn_id": 8, "name": "GCP TTech Billing Export (gcp-ttech)", "blob": 186, "period": "202608"},
    {"conn_id": 4, "name": "GCP TI Billing Export (gcp-ti)", "blob": 132, "period": "202608"},
    {"conn_id": 3, "name": "GCP 4P Billing Export (gcp-4p)", "blob": 140, "period": "202608"},
]

def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def main():
    log("Iniciando reprocessamento das cargas que falharam para a tabela de destino MODELO FOCUS (ID: 1)...")
    
    # Check worker health
    try:
        req = urllib.request.Request(f"{API_BASE}/etl/worker-health")
        with urllib.request.urlopen(req) as resp:
            health = json.loads(resp.read().decode())
            log(f"Worker Health Status: {health.get('status')} | Heartbeat Age: {health.get('heartbeat_age_seconds')}s | PID: {health.get('worker_pid')}")
            if health.get("status") != "ok":
                log("ATENÇÃO: Worker ETL não está saudável ou está offline!")
    except Exception as e:
        log(f"Erro ao consultar worker health: {e}")

    # Trigger reprocess for all failed sources in Destination 1
    dest_id = 1
    conn_ids = [s["conn_id"] for s in FAILED_SOURCES]
    periods = ["202608"]

    payload = {
        "destination_table_id": dest_id,
        "connection_ids": conn_ids,
        "billing_periods": periods,
        "replace_existing": False
    }

    log("Enviando requisição de reprocessamento:")
    log(f"  - Tabela Destino: {dest_id} (MODELO FOCUS)")
    log(f"  - Conexões ({len(conn_ids)}): {conn_ids}")
    for s in FAILED_SOURCES:
        log(f"      * Conexão {s['conn_id']}: {s['name']} (Período: {s['period']}, Blob anterior com erro: {s['blob']})")
    log(f"  - Períodos: {periods}")

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE}/etl/reprocess",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            res_data = json.loads(resp.read().decode())
            log("Reprocessamento enfileirado com sucesso!")
            log(f"  - Task ID principal: {res_data.get('task_id')}")
            log(f"  - Task IDs geradas: {res_data.get('task_ids')}")
            log(f"  - Queue Group: {res_data.get('queue_group')}")
            log(f"  - Mensagem: {res_data.get('message')}")
            
            task_ids = res_data.get("task_ids") or [res_data.get("task_id")]
            return task_ids
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode()
        log(f"ERRO HTTP {e.code}: {err_msg}")
        sys.exit(1)
    except Exception as e:
        log(f"ERRO ao disparar reprocessamento: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
