#!/usr/bin/env bash
# Sobe (ou reinicia) o stack Finbase em Docker: api, scheduler, worker,
# supervisor, web e gateway (docker-compose.finbase.yml).
# Usa o PostgreSQL e o Trino compartilhados do projeto OpenFinops.
#
# Uso:
#   ./start-local.sh            # rebuild + reinicia tudo (build de produção)
#   ./start-local.sh dev        # modo DEV local: API, worker e Vite na máquina, com recarga automática
#   ./start-local.sh web        # rebuild + reinicia só o frontend
#   ./start-local.sh api        # rebuild + reinicia só o backend (api, scheduler, worker, supervisor)
#   ./start-local.sh stop       # para o stack Finbase
#   ./start-local.sh logs [svc] # acompanha logs
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.finbase.yml"
LOG_DIR="$ROOT_DIR/.runtime-logs"
WEB_URL="http://localhost:5174"
API_URL="http://localhost:8011"

compose() {
  docker compose -f "$COMPOSE_FILE" --project-directory "$ROOT_DIR" "$@"
}

# ── Modo DEV local ──────────────────────────────────────────────────────────
# API (uvicorn --reload), worker, supervisor e Vite rodam direto na máquina,
# nas mesmas portas do Docker (8011 e 5174). Do Docker ficam só o PostgreSQL
# (porta 5433 do host) e o Trino compartilhados. Ctrl+C encerra tudo.
run_local_dev() {
  local venv_python="$ROOT_DIR/.venv/bin/python"
  if [[ ! -x "$venv_python" ]]; then
    echo "ERRO: falta o .venv. Crie com:" >&2
    echo "  python3.12 -m venv .venv && .venv/bin/pip install -r apps/api/requirements.txt" >&2
    exit 1
  fi
  local postgres_password
  postgres_password="$(grep -E '^POSTGRES_PASSWORD=' "$ROOT_DIR/.env" | head -1 | cut -d= -f2-)"
  [[ -n "$postgres_password" ]] || { echo "ERRO: POSTGRES_PASSWORD não está no .env" >&2; exit 1; }

  # Mesmas configurações do docker-compose.finbase.yml, com endereços do host.
  export ENV=development AUTH_ENABLED=false BOOTSTRAP_ENABLED=true SCHEDULER_ENABLED=true
  export DATABASE_URL="postgresql+asyncpg://finops:${postgres_password}@localhost:5433/finbase"
  export DATABASE_URL_SYNC="postgresql://finops:${postgres_password}@localhost:5433/finbase"
  export TRINO_URL="http://localhost:8080" TRINO_CATALOG="iceberg_finbase"
  export ICEBERG_LOCAL_WAREHOUSE="$ROOT_DIR/apps/data/iceberg"
  export ICEBERG_CANONICAL_WAREHOUSE_PATH="/data/finbase-iceberg"
  export OCI_CONFIG_DIR="$ROOT_DIR/env"
  export OPENFINOPS_WORKER_RUNTIME_DIR="${TMPDIR:-/tmp}/finbase-worker"
  export CORS_ORIGINS="$WEB_URL"
  mkdir -p "$LOG_DIR" "$OPENFINOPS_WORKER_RUNTIME_DIR"

  echo "Parando os containers da aplicação Finbase (as portas 5174/8011 passam para a máquina)..."
  compose stop api web gateway worker scheduler supervisor >/dev/null 2>&1 || true

  if [[ ! -x "$ROOT_DIR/apps/web/node_modules/.bin/vite" ]]; then
    echo "Instalando dependências do front (só na primeira vez)..."
    (cd "$ROOT_DIR/apps/web" && yarn install --frozen-lockfile) || exit 1
  fi

  local pids=()
  stop_local() {
    trap - INT TERM EXIT
    echo
    echo "Encerrando API, worker, supervisor e Vite..."
    kill "${pids[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
  }
  trap stop_local INT TERM EXIT

  (cd "$ROOT_DIR/apps/api" && exec "$venv_python" -m uvicorn app.main:app --host 0.0.0.0 --port 8011 --reload --reload-dir app) \
    >"$LOG_DIR/api.log" 2>&1 &
  pids+=($!)
  echo "Aguardando a API (migrações na primeira subida)..."
  wait_http "$API_URL/saude" "API" 120 || { tail -20 "$LOG_DIR/api.log" >&2; exit 1; }

  # O worker só começa depois das migrações da API.
  (cd "$ROOT_DIR/apps/api" && exec "$venv_python" -u worker.py) >"$LOG_DIR/worker.log" 2>&1 &
  pids+=($!)
  (cd "$ROOT_DIR/apps/api" && exec "$venv_python" -u supervisor.py) >"$LOG_DIR/supervisor.log" 2>&1 &
  pids+=($!)
  (cd "$ROOT_DIR/apps/web" && VITE_API_PROXY_TARGET="$API_URL" exec yarn dev --port 5174 --strictPort) \
    >"$LOG_DIR/web.log" 2>&1 &
  pids+=($!)
  wait_http "$WEB_URL/" "Web" 60 || { tail -20 "$LOG_DIR/web.log" >&2; exit 1; }

  echo
  echo "Finbase local (DEV): Web $WEB_URL | API $API_URL"
  echo "Front e API recarregam sozinhos ao salvar. Logs: $LOG_DIR (api, worker, supervisor, web)"
  echo "Ctrl+C encerra tudo."
  # Encerra tudo se qualquer processo cair.
  wait -n "${pids[@]}"
  echo "Um dos processos encerrou; veja os logs em $LOG_DIR" >&2
}

require_shared_container() {
  local name="$1"
  if ! docker ps --format '{{.Names}}' | grep -qx "$name"; then
    echo "ERRO: container compartilhado '$name' não está rodando." >&2
    echo "      Suba-o na pasta OpenFinops (veja infra/FINBASE.md)." >&2
    exit 1
  fi
}

wait_http() {
  local url="$1" label="$2" tries="${3:-60}"
  for _ in $(seq 1 "$tries"); do
    if curl -fs -o /dev/null --max-time 3 "$url"; then
      echo "  $label OK ($url)"
      return 0
    fi
    sleep 2
  done
  echo "ERRO: $label não respondeu em $url" >&2
  return 1
}

if ! docker info >/dev/null 2>&1; then
  echo "ERRO: Docker daemon indisponível. Abra o Docker Desktop." >&2
  exit 1
fi

target="${1:-all}"
case "$target" in
  stop)
    compose down
    exit 0
    ;;
  logs)
    shift
    compose logs -f --tail 100 "$@"
    exit 0
    ;;
  web)
    services=(web gateway)
    ;;
  api)
    services=(scheduler api worker supervisor gateway)
    ;;
  all)
    services=()
    ;;
  dev)
    services=()
    ;;
  *)
    echo "Uso: $0 [all|dev|web|api|stop|logs [serviço]]" >&2
    exit 1
    ;;
esac

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "ERRO: falta o .env na raiz (copie da pasta OpenFinops)." >&2
  exit 1
fi

require_shared_container openfinops-postgres-1
require_shared_container openfinops-trino-1

if [[ "$target" == "dev" ]]; then
  run_local_dev
  exit 0
fi

echo "Build + subida do Finbase (${services[*]:-todos os serviços})..."
compose up -d --build --remove-orphans "${services[@]}"
# O gateway resolve api/web por DNS; reinicia para não reaproveitar IPs antigos.
compose restart gateway >/dev/null

echo "Aguardando serviços..."
wait_http "$API_URL/saude" "API"
wait_http "$WEB_URL/" "Web"

echo
echo "Finbase no ar: Web $WEB_URL | API $API_URL"
echo "Logs: ./start-local.sh logs [api|web|worker|scheduler]"
