#!/usr/bin/env bash
# Start the complete OpenFinops backend: API + universal ETL worker.
# The single worker consumes extraction, reprocess and forecast tasks.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_DIR="$ROOT_DIR/apps/api"
PYTHON_BIN="${OPENFINOPS_PYTHON:-$ROOT_DIR/.venv/bin/python}"
API_PORT="${OPENFINOPS_API_PORT:-8000}"
MAX_WORKER_RESTARTS="${OPENFINOPS_WORKER_RESTARTS:-3}"
TRINO_URL="${TRINO_URL:-http://localhost:8080}"
TRINO_WAIT_SECONDS="${TRINO_WAIT_SECONDS:-60}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

trino_ready() {
  local response
  response="$(curl -fsS --max-time 3 "$TRINO_URL/v1/info/state" 2>/dev/null || true)"
  [[ "$response" == '"ACTIVE"' ]]
}

ensure_analytics() {
  if ! trino_ready; then
    echo "Trino indisponível; iniciando Trino, Nessie e MinIO..."
    docker compose --project-directory "$ROOT_DIR" --profile analytics up -d trino
  fi

  echo -n "Aguardando Trino em $TRINO_URL"
  local waited=0
  until trino_ready; do
    if (( waited >= TRINO_WAIT_SECONDS )); then
      echo
      echo "Trino não ficou pronto após ${TRINO_WAIT_SECONDS}s." >&2
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
    echo -n "."
  done
  echo " OK"
}

ensure_analytics

api_pid=""
worker_pid=""
supervisor_pid=""
worker_restarts=0

stop_backend() {
  trap - INT TERM EXIT
  for process_pid in "$supervisor_pid" "$worker_pid" "$api_pid"; do
    if [[ -n "$process_pid" ]] && kill -0 "$process_pid" 2>/dev/null; then
      kill "$process_pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap stop_backend INT TERM EXIT

start_worker() {
  (cd "$API_DIR" && exec "$PYTHON_BIN" -u worker.py) &
  worker_pid=$!
}

start_supervisor() {
  (cd "$API_DIR" && exec "$PYTHON_BIN" -u supervisor.py) &
  supervisor_pid=$!
}

(cd "$API_DIR" && exec "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT") &
api_pid=$!
start_worker
start_supervisor

echo "Backend OpenFinops iniciado: API http://localhost:$API_PORT | Worker ETL PID $worker_pid"

while true; do
  sleep 2

  if ! kill -0 "$api_pid" 2>/dev/null; then
    echo "API encerrou; desligando o worker ETL." >&2
    exit 1
  fi

  if ! kill -0 "$worker_pid" 2>/dev/null; then
    worker_restarts=$((worker_restarts + 1))
    if (( worker_restarts > MAX_WORKER_RESTARTS )); then
      echo "Worker ETL encerrou após $MAX_WORKER_RESTARTS tentativas; desligando a API." >&2
      exit 1
    fi
    echo "Worker ETL encerrou; reiniciando ($worker_restarts/$MAX_WORKER_RESTARTS)." >&2
    start_worker
  fi
  if ! kill -0 "$supervisor_pid" 2>/dev/null; then
    echo "Monitor ETL encerrou; reiniciando." >&2
    start_supervisor
  fi
done
