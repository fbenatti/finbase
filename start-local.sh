#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT_DIR/.venv/bin/activate"
LOG_DIR="$ROOT_DIR/.runtime-logs"
mkdir -p "$LOG_DIR"
source "$VENV"

api_pid=""; worker_pid=""; web_pid=""
worker_restarts=0

# ── Infra validation: Trino must answer before the worker starts. ──
# The worker queries Trino to run forecast/reprocess jobs; starting without it
# leaves that work silently failing in the queue ("All connection attempts failed").
TRINO_URL="${TRINO_URL:-http://localhost:8080}"
TRINO_WAIT_SECONDS="${TRINO_WAIT_SECONDS:-60}"
API_URL="${API_URL:-http://localhost:8001}"
BACKEND_WAIT_SECONDS="${BACKEND_WAIT_SECONDS:-60}"

trino_alive() {
  [[ "$(curl -fsS --max-time 3 "$TRINO_URL/v1/info/state" 2>/dev/null || true)" == '"ACTIVE"' ]]
}

ensure_infra() {
  # Bring up the analytics containers only if they are not already running.
  if ! trino_alive; then
    echo "Trino não está respondendo; tentando subir os containers de infraestrutura..."
    docker compose --profile analytics up -d trino nessie minio >/dev/null 2>&1 || true
  fi

  echo -n "Aguardando Trino em $TRINO_URL"
  local waited=0
  until trino_alive; do
    if (( waited >= TRINO_WAIT_SECONDS )); then
      echo
      echo "ERRO: Trino não ficou disponível após ${TRINO_WAIT_SECONDS}s." >&2
      echo "  Verifique com: docker compose --profile analytics up -d trino nessie minio" >&2
      echo "  E os logs: docker logs openfinops-trino-1" >&2
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
    echo -n "."
  done
  echo " OK"
}

ensure_infra

# Kill leftover uvicorn/worker from a previous session so bind/lock don't fail.
# The previous process can still answer /health, which makes wait_for_backend
# succeed — then this script's child exits and the watchdog treats it as a crash.
kill_pids() {
  local pids="$1"
  [[ -z "$pids" ]] && return 0
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
  local still=""
  local pid
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then
      still="$still $pid"
    fi
  done
  if [[ -n "$still" ]]; then
    # shellcheck disable=SC2086
    kill -9 $still 2>/dev/null || true
    sleep 0.3
  fi
}

listeners_on_port() {
  lsof -t -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

free_stale_backend() {
  local api_pids worker_pids
  api_pids="$(listeners_on_port 8001)"
  if [[ -n "$api_pids" ]]; then
    echo "Liberando porta 8001 (PIDs: $(echo "$api_pids" | tr '\n' ' '))"
    kill_pids "$api_pids"
  fi
  worker_pids="$(pgrep -f "$ROOT_DIR/.venv/bin/python -u worker.py" 2>/dev/null || true)"
  if [[ -z "$worker_pids" ]]; then
    worker_pids="$(pgrep -f "$ROOT_DIR/apps/api/worker.py" 2>/dev/null || true)"
  fi
  if [[ -n "$worker_pids" ]]; then
    echo "Encerrando worker legado (PIDs: $(echo "$worker_pids" | tr '\n' ' '))"
    kill_pids "$worker_pids"
  fi
  if [[ -n "$(listeners_on_port 8001)" ]]; then
    echo "ERRO: porta 8001 ainda ocupada após tentar liberar." >&2
    exit 1
  fi
}

free_stale_backend

backend_ready() {
  local api_health worker_health
  api_health="$(curl -fsS --max-time 3 "$API_URL/health" 2>/dev/null || true)"
  worker_health="$(curl -fsS --max-time 3 "$API_URL/etl/worker-health" 2>/dev/null || true)"
  [[ "$api_health" == *'"status":"ok"'* && "$worker_health" == *'"status":"ok"'* ]]
}

wait_for_backend() {
  echo -n "Aguardando API e Worker ETL"
  local waited=0
  until backend_ready; do
    if (( waited >= BACKEND_WAIT_SECONDS )); then
      echo
      echo "ERRO: API e Worker ETL não ficaram disponíveis após ${BACKEND_WAIT_SECONDS}s." >&2
      echo "  API: $LOG_DIR/api.log" >&2
      echo "  Worker: $LOG_DIR/worker.log" >&2
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
    echo -n "."
  done
  echo " OK"
}

cleanup() {
  trap - INT TERM EXIT
  for process_pid in "$web_pid" "$worker_pid" "$api_pid"; do
    if [[ -n "$process_pid" ]] && kill -0 "$process_pid" 2>/dev/null; then kill "$process_pid" 2>/dev/null || true; fi
  done
  for _ in {1..10}; do
    alive=0
    for process_pid in "$web_pid" "$worker_pid" "$api_pid"; do
      if [[ -n "$process_pid" ]] && kill -0 "$process_pid" 2>/dev/null; then alive=1; fi
    done
    (( alive == 0 )) && break
    sleep 0.5
  done
  for process_pid in "$web_pid" "$worker_pid" "$api_pid"; do
    if [[ -n "$process_pid" ]] && kill -0 "$process_pid" 2>/dev/null; then kill -9 "$process_pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(cd "$ROOT_DIR/apps/api" && exec "$ROOT_DIR/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8001) \
  >"$LOG_DIR/api.log" 2>&1 &
api_pid=$!

(cd "$ROOT_DIR/apps/api" && exec "$ROOT_DIR/.venv/bin/python" -u worker.py) \
  >"$LOG_DIR/worker.log" 2>&1 &
worker_pid=$!

(cd "$ROOT_DIR/apps/web" && exec npm run dev -- --host 0.0.0.0) \
  >"$LOG_DIR/web.log" 2>&1 &
web_pid=$!

wait_for_backend

echo "OpenFinops completo iniciado: API $API_URL | Web http://localhost:5173 | Worker ETL ativo"
echo "Logs: $LOG_DIR"

while true; do
  sleep 2
  if ! kill -0 "$api_pid" 2>/dev/null; then
    echo "API encerrou; veja $LOG_DIR/api.log" >&2
    exit 1
  fi
  if ! kill -0 "$web_pid" 2>/dev/null; then
    echo "Frontend encerrou; veja $LOG_DIR/web.log" >&2
    exit 1
  fi
  if ! kill -0 "$worker_pid" 2>/dev/null; then
    worker_restarts=$((worker_restarts + 1))
    if (( worker_restarts > 3 )); then
      echo "Worker encerrou após 3 tentativas; veja $LOG_DIR/worker.log" >&2
      exit 1
    fi
    echo "Worker encerrou; reiniciando ($worker_restarts/3)"
    (cd "$ROOT_DIR/apps/api" && exec "$ROOT_DIR/.venv/bin/python" -u worker.py) \
      >>"$LOG_DIR/worker.log" 2>&1 &
    worker_pid=$!
  fi
done
