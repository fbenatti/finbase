#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT_DIR/.venv/bin/activate"
LOG_DIR="$ROOT_DIR/.runtime-logs"
LOCK_FILE="$LOG_DIR/start-local.lock"
mkdir -p "$LOG_DIR"

api_pid=""; worker_pid=""; web_pid=""
worker_restarts=0

# ── Infra validation: Trino must answer before the worker starts. ──
# The worker queries Trino to run forecast/reprocess jobs; starting without it
# leaves that work silently failing in the queue ("All connection attempts failed").
TRINO_URL="${TRINO_URL:-http://localhost:8080}"
TRINO_WAIT_SECONDS="${TRINO_WAIT_SECONDS:-30}"
API_URL="${API_URL:-http://localhost:8001}"
BACKEND_WAIT_SECONDS="${BACKEND_WAIT_SECONDS:-60}"

is_docker_pid() {
  local pid="$1"
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  [[ "$cmd" == *com.docker* || "$cmd" == *Docker.app* || "$cmd" == *docker-proxy* || "$cmd" == *vpnkit* || "$cmd" == *qemu-system* || "$cmd" == */bin/docker* ]]
}

kill_pids() {
  local pids="$1"
  [[ -z "$pids" ]] && return 0
  local safe="" pid
  for pid in $pids; do
    [[ -z "$pid" ]] && continue
    if is_docker_pid "$pid"; then
      echo "Ignorando PID $pid (processo Docker Desktop; não será morto)" >&2
      continue
    fi
    safe="$safe $pid"
  done
  [[ -z "$safe" ]] && return 0
  # shellcheck disable=SC2086
  kill $safe 2>/dev/null || true
  sleep 1
  local still=""
  for pid in $safe; do
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

wait_for_pid_exit() {
  local pid="$1"
  local max_wait="${2:-20}"
  local waited=0
  [[ -z "$pid" ]] && return 0
  while kill -0 "$pid" 2>/dev/null; do
    if (( waited >= max_wait )); then
      return 1
    fi
    sleep 0.5
    waited=$((waited + 1))
  done
}

stop_start_local_pid() {
  local pid="$1"
  [[ -z "$pid" ]] && return 0
  kill "$pid" 2>/dev/null || true
  if ! wait_for_pid_exit "$pid" 20; then
    echo "Sessão anterior não encerrou após o cleanup; forçando PID $pid" >&2
    kill -9 "$pid" 2>/dev/null || true
    sleep 0.3
  fi
}

wait_for_port_free() {
  local port="$1"
  local max_wait="${2:-20}"
  local waited=0
  while [[ -n "$(non_docker_listeners_on_port "$port")" ]]; do
    if (( waited >= max_wait )); then
      echo "ERRO: porta $port ainda ocupada após ${max_wait}s." >&2
      return 1
    fi
    sleep 0.5
    waited=$((waited + 1))
  done
}

listeners_on_port() {
  lsof -t -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

non_docker_listeners_on_port() {
  local pid
  for pid in $(listeners_on_port "$1"); do
    [[ -z "$pid" ]] && continue
    is_docker_pid "$pid" && continue
    echo "$pid"
  done
}

cmdline_for_pid() {
  ps -p "$1" -o args= 2>/dev/null || true
}

is_openfinops_cmd() {
  local cmd="$1"
  [[ "$cmd" == *"$ROOT_DIR"* ]]
}

is_openfinops_api_cmd() {
  local cmd="$1"
  [[ "$cmd" == *uvicorn* && "$cmd" == *app.main:app* ]] && is_openfinops_cmd "$cmd"
}

is_openfinops_web_cmd() {
  local cmd="$1"
  [[ "$cmd" == *vite* ]] && is_openfinops_cmd "$cmd"
}

collect_pids_on_port() {
  local port="$1"
  shift
  local pid cmd result="" pattern
  for pid in $(listeners_on_port "$port"); do
    [[ -z "$pid" ]] && continue
    is_docker_pid "$pid" && continue
    cmd="$(cmdline_for_pid "$pid")"
    [[ -z "$cmd" ]] && continue
    if is_openfinops_cmd "$cmd"; then
      result="$result $pid"
      continue
    fi
    for pattern in "$@"; do
      if [[ "$cmd" == *"$pattern"* ]]; then
        result="$result $pid"
        break
      fi
    done
  done
  # shellcheck disable=SC2086
  echo $result
}

write_pid_file() {
  local name="$1"
  local pid="$2"
  [[ -n "$pid" ]] && echo "$pid" >"$LOG_DIR/$name.pid"
}

stop_pid_files() {
  local f pid
  shopt -s nullglob
  for f in "$LOG_DIR"/*.pid; do
    [[ "$(basename "$f")" == start-local.pid ]] && continue
    pid="$(tr -d '[:space:]' < "$f")"
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    if kill -0 "$pid" 2>/dev/null; then
      echo "Encerrando processo registrado em $(basename "$f") (PID $pid)"
      kill_pids "$pid"
    fi
    rm -f "$f"
  done
  shopt -u nullglob
}

stop_other_start_local() {
  local pid cmd
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    [[ "$pid" -eq $$ ]] && continue
    cmd="$(cmdline_for_pid "$pid")"
    [[ "$cmd" != *start-local.sh* ]] && continue
    echo "Encerrando sessão start-local.sh anterior (PID $pid)"
    stop_start_local_pid "$pid"
  done < <(pgrep -f "$ROOT_DIR/start-local.sh" 2>/dev/null || true)
}

acquire_session_lock() {
  local old_pid cmd
  if [[ -f "$LOCK_FILE" ]]; then
    old_pid="$(tr -d '[:space:]' < "$LOCK_FILE")"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
      cmd="$(cmdline_for_pid "$old_pid")"
      if [[ "$cmd" == *start-local.sh* && "$old_pid" -ne $$ ]]; then
        echo "Encerrando sessão start-local.sh anterior (PID $old_pid)"
        stop_start_local_pid "$old_pid"
      fi
    fi
    rm -f "$LOCK_FILE"
  fi
  stop_other_start_local
  echo $$ >"$LOCK_FILE"
  write_pid_file start-local "$$"
}

stop_compose_dev_conflicts() {
  if ! docker_daemon_ready; then
    return 0
  fi
  echo "Parando containers Docker api/web deste projeto (Docker Desktop permanece ativo)..."
  compose_cmd stop -t 2 api web >/dev/null 2>&1 || true
  compose_cmd kill api web >/dev/null 2>&1 || true
}

free_listeners_on_port() {
  local port="$1"
  local label="$2"
  shift 2
  local pids leftover
  pids="$(collect_pids_on_port "$port" "$@")"
  if [[ -n "$pids" ]]; then
    echo "Liberando porta $port ($label, PIDs:$(echo "$pids" | sed 's/^/ /'))"
    kill_pids "$pids"
  fi
  leftover="$(non_docker_listeners_on_port "$port" | tr '\n' ' ' | sed 's/ $//')"
  if [[ -n "$leftover" ]]; then
    echo "Porta $port ainda com listeners fora do Docker ($leftover); não forçando kill de processos do Docker Desktop."
  fi
  wait_for_port_free "$port" 20
}

kill_orphan_workers() {
  local worker_pids zombie_pids
  worker_pids="$(pgrep -f "$ROOT_DIR/.venv/bin/python -u worker.py" 2>/dev/null || true)"
  if [[ -z "$worker_pids" ]]; then
    worker_pids="$(pgrep -f "$ROOT_DIR/apps/api/worker.py" 2>/dev/null || true)"
  fi
  if [[ -n "$worker_pids" ]]; then
    echo "Encerrando worker ETL órfão (PIDs:$(echo "$worker_pids" | tr '\n' ' ' | sed 's/ $//'))"
    kill_pids "$worker_pids"
  fi

  # Limpa processos filho spawn_main/heartbeat do worker que ficam vivos após crash no macOS.
  # Esses filhos herdam o file descriptor do flock e impedem reinicialização com
  # "Another FinOps Worker already owns the worker lock".
  zombie_pids="$(pgrep -f "multiprocessing.spawn import spawn_main" 2>/dev/null || true)"
  if [[ -n "$zombie_pids" ]]; then
    echo "Encerrando processos filho zumbis do worker (spawn_main, PIDs:$(echo "$zombie_pids" | tr '\n' ' ' | sed 's/ $//'))"
    kill -9 $zombie_pids 2>/dev/null || true
    sleep 1
  fi
  rm -f /tmp/openfinops-worker.lock /tmp/openfinops-worker.pid /tmp/openfinops-worker.heartbeat
}

free_stale_processes() {
  local pids_8000

  stop_compose_dev_conflicts
  stop_pid_files

  free_listeners_on_port 8001 "API local" uvicorn app.main:app
  # 8000 is the compose API mapping. Only stop the container (above); never kill Docker's proxy.
  pids_8000="$(collect_pids_on_port 8000 uvicorn app.main:app)"
  if [[ -n "$pids_8000" ]]; then
    echo "Liberando porta 8000 (uvicorn local, PIDs: $pids_8000)"
    kill_pids "$pids_8000"
  fi
  free_listeners_on_port 5173 "frontend Vite" vite

  kill_orphan_workers


  if [[ -n "$(non_docker_listeners_on_port 8001)" ]]; then
    echo "ERRO: porta 8001 ainda ocupada após tentar liberar." >&2
    exit 1
  fi
  if [[ -n "$(non_docker_listeners_on_port 5173)" ]]; then
    echo "ERRO: porta 5173 ainda ocupada após tentar liberar." >&2
    exit 1
  fi
}

sync_api_pid_from_port() {
  local listener cmd
  listener="$(listeners_on_port 8001 | head -1 | tr -d '[:space:]')"
  [[ -z "$listener" ]] && return 1
  cmd="$(cmdline_for_pid "$listener")"
  is_openfinops_api_cmd "$cmd" || return 1
  api_pid="$listener"
  write_pid_file api "$api_pid"
  return 0
}

recover_api_pid() {
  if kill -0 "$api_pid" 2>/dev/null; then
    return 0
  fi
  sync_api_pid_from_port
}

trino_alive() {
  local payload
  payload="$(curl -fsS --max-time 3 "$TRINO_URL/v1/info" 2>/dev/null || true)"
  [[ -n "$payload" ]] || return 1
  python3 -c 'import json,sys; p=json.loads(sys.argv[1]); sys.exit(0 if p.get("state")=="ACTIVE" and not p.get("starting") else 1)' "$payload"
}

compose_cmd() {
  docker compose --project-directory "$ROOT_DIR" "$@"
}

docker_daemon_ready() {
  docker ps >/dev/null 2>&1
}

configure_docker_host() {
  if docker_daemon_ready; then
    return 0
  fi
  if [[ -S "${HOME}/.docker/run/docker.sock" ]]; then
    export DOCKER_HOST="unix://${HOME}/.docker/run/docker.sock"
  elif [[ -S "/var/run/docker.sock" ]]; then
    export DOCKER_HOST="unix:///var/run/docker.sock"
  fi
  docker_daemon_ready
}

ensure_docker_daemon() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERRO: comando docker não encontrado; Trino/MinIO/Nessie exigem Docker." >&2
    exit 1
  fi

  configure_docker_host || true

  local waited=0
  until docker_daemon_ready; do
    if (( waited == 0 )); then
      echo "Aguardando Docker daemon (somente leitura; o script não encerra o Docker Desktop)..."
    fi
    if (( waited >= 60 )); then
      echo "ERRO: Docker não respondeu em 60s." >&2
      echo "  Confirme que o Docker Desktop está em execução e rode ./start-local.sh novamente." >&2
      exit 1
    fi
    configure_docker_host || true
    sleep 2
    waited=$((waited + 2))
  done
  if (( waited > 0 )); then
    echo "Docker daemon OK"
  fi
}

start_infra() {
  ensure_docker_daemon
  echo "Subindo containers OpenFinops (trino, nessie, minio) — start, sem recriar..."
  compose_cmd start trino nessie minio >/dev/null 2>&1 || true
  if compose_cmd up -d trino nessie minio; then
    return 0
  fi
  echo "ERRO: falha ao subir containers de infraestrutura." >&2
  echo "  Veja: docker compose --project-directory \"$ROOT_DIR\" logs trino nessie minio" >&2
  return 1
}

wait_for_trino() {
  local attempt_label="${1:-Aguardando Trino em $TRINO_URL}"
  echo -n "$attempt_label"
  local waited=0
  until trino_alive; do
    if (( waited >= TRINO_WAIT_SECONDS )); then
      echo
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
    echo -n "."
  done
  echo " OK"
  return 0
}

ensure_infra() {
  ensure_docker_daemon

  if trino_alive; then
    echo "Trino já disponível em $TRINO_URL"
    return 0
  fi

  echo "Trino não está respondendo em $TRINO_URL"
  start_infra || exit 1

  if wait_for_trino "Aguardando Trino em $TRINO_URL (até ${TRINO_WAIT_SECONDS}s)"; then
    return 0
  fi

  echo "ERRO: Trino não ficou disponível após ${TRINO_WAIT_SECONDS}s." >&2
  echo "  Os containers não foram recriados. Logs: docker compose --project-directory \"$ROOT_DIR\" logs trino nessie minio" >&2
  exit 1
}

echo "Verificando instâncias anteriores do stack local OpenFinops..."
acquire_session_lock
ensure_docker_daemon
free_stale_processes

source "$VENV"

echo "Validando sintaxe do backend..."
if ! "$ROOT_DIR/.venv/bin/python" -m compileall -q "$ROOT_DIR/apps/api/app" "$ROOT_DIR/apps/api/worker.py"; then
  echo "ERRO: backend contém erro de sintaxe; inicialização cancelada antes de abrir as portas." >&2
  exit 1
fi

ensure_infra

backend_ready() {
  local api_health worker_health listener
  kill -0 "$api_pid" 2>/dev/null || return 1
  listener="$(listeners_on_port 8001 | head -1 | tr -d '[:space:]')"
  [[ -n "$listener" && "$listener" == "$api_pid" ]] || return 1
  api_health="$(curl -fsS --max-time 3 "$API_URL/health" 2>/dev/null || true)"
  worker_health="$(curl -fsS --max-time 3 "$API_URL/etl/worker-health" 2>/dev/null || true)"
  [[ "$api_health" == *'"status":"ok"'* && "$worker_health" == *'"status":"ok"'* ]]
}

wait_for_backend() {
  echo -n "Aguardando API e Worker ETL"
  local waited=0
  until backend_ready; do
    if ! kill -0 "$api_pid" 2>/dev/null; then
      echo
      echo "ERRO: processo da API encerrou durante inicialização; veja $LOG_DIR/api.log" >&2
      return 1
    fi
    if (( waited >= BACKEND_WAIT_SECONDS )); then
      echo
      echo "ERRO: API e Worker ETL não ficaram disponíveis após ${BACKEND_WAIT_SECONDS}s." >&2
      echo "  API: $LOG_DIR/api.log" >&2
      echo "  Worker: $LOG_DIR/worker.log" >&2
      if [[ -n "$(listeners_on_port 8001)" ]]; then
        echo "  Porta 8001 ouvindo em PID(s): $(listeners_on_port 8001 | tr '\n' ' ')" >&2
        echo "  PID rastreado pela sessão: ${api_pid:-desconhecido}" >&2
      fi
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
    echo -n "."
  done
  echo " OK"
}

stop_backend_processes() {
  local process_pid
  for process_pid in "$worker_pid" "$api_pid"; do
    if [[ -n "$process_pid" ]] && kill -0 "$process_pid" 2>/dev/null; then
      kill "$process_pid" 2>/dev/null || true
    fi
  done
  sleep 1
  for process_pid in "$worker_pid" "$api_pid"; do
    if [[ -n "$process_pid" ]] && kill -0 "$process_pid" 2>/dev/null; then
      kill -9 "$process_pid" 2>/dev/null || true
    fi
  done
  rm -f "$LOG_DIR/api.pid" "$LOG_DIR/worker.pid"
  api_pid=""
  worker_pid=""
}

start_api_process() {
  (cd "$ROOT_DIR/apps/api" && exec "$ROOT_DIR/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload) \
    >"$LOG_DIR/api.log" 2>&1 &
  api_pid=$!
  write_pid_file api "$api_pid"
  sleep 0.5
  if ! kill -0 "$api_pid" 2>/dev/null; then
    echo "ERRO: processo da API encerrou ao iniciar; veja $LOG_DIR/api.log" >&2
    return 1
  fi
  sync_api_pid_from_port || true
}

start_worker_process() {
  (cd "$ROOT_DIR/apps/api" && exec "$ROOT_DIR/.venv/bin/python" -u worker.py) \
    >"$LOG_DIR/worker.log" 2>&1 &
  worker_pid=$!
  write_pid_file worker "$worker_pid"
}

wait_for_backend_with_retry() {
  if wait_for_backend; then
    return 0
  fi
  echo "Reiniciando API/Worker após limpar workers órfãos e processos travados..."
  stop_backend_processes
  free_listeners_on_port 8001 "API local" uvicorn app.main:app
  kill_orphan_workers
  sleep 1
  start_api_process || return 1
  start_worker_process
  if wait_for_backend; then
    return 0
  fi
  echo "ERRO: API e Worker ETL não ficaram disponíveis após retry." >&2
  return 1
}

cleanup() {
  trap - INT TERM EXIT
  rm -f "$LOCK_FILE" "$LOG_DIR/start-local.pid" "$LOG_DIR/api.pid" "$LOG_DIR/worker.pid" "$LOG_DIR/web.pid"
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

start_api_process || exit 1
start_worker_process

(cd "$ROOT_DIR/apps/web" && exec npm run dev -- --host 0.0.0.0) \
  >"$LOG_DIR/web.log" 2>&1 &
web_pid=$!
write_pid_file web "$web_pid"

wait_for_backend_with_retry || exit 1

echo "OpenFinops completo iniciado: API $API_URL | Web http://localhost:5173 | Worker ETL ativo"
echo "Logs: $LOG_DIR"

while true; do
  sleep 2
  if ! recover_api_pid; then
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
    write_pid_file worker "$worker_pid"
  fi
done
