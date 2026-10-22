#!/usr/bin/env bash
# Sobe (ou reinicia) o stack Finbase em Docker: api, scheduler, worker,
# supervisor, web e gateway (docker-compose.finbase.yml).
# Usa o PostgreSQL e o Trino compartilhados do projeto OpenFinops.
#
# Uso:
#   ./start-local.sh            # rebuild + reinicia tudo (build de produção)
#   ./start-local.sh dev        # modo DEV: Vite e API com recarga automática, sem rebuild
#   ./start-local.sh web        # rebuild + reinicia só o frontend
#   ./start-local.sh api        # rebuild + reinicia só o backend (api, scheduler, worker, supervisor)
#   ./start-local.sh stop       # para o stack Finbase
#   ./start-local.sh logs [svc] # acompanha logs
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.finbase.yml"
COMPOSE_DEV_FILE="$ROOT_DIR/docker-compose.finbase.dev.yml"
WEB_URL="http://localhost:5174"
API_URL="http://localhost:8011"

compose() {
  docker compose -f "$COMPOSE_FILE" --project-directory "$ROOT_DIR" "$@"
}

compose_dev() {
  docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_DEV_FILE" --project-directory "$ROOT_DIR" "$@"
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

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$ROOT_DIR/.env-databriks" ]]; then
  echo "ERRO: faltam .env e/ou .env-databriks na raiz (copie da pasta OpenFinops)." >&2
  exit 1
fi

require_shared_container openfinops-postgres-1
require_shared_container openfinops-trino-1

if [[ "$target" == "dev" ]]; then
  # Código montado da máquina: mudanças no front e na API valem sem rebuild.
  echo "Subindo o Finbase em modo DEV (Vite + API com recarga automática)..."
  compose_dev up -d --remove-orphans
else
  echo "Build + subida do Finbase (${services[*]:-todos os serviços})..."
  compose up -d --build --remove-orphans "${services[@]}"
fi
# O gateway resolve api/web por DNS; reinicia para não reaproveitar IPs antigos.
compose restart gateway >/dev/null

echo "Aguardando serviços..."
wait_http "$API_URL/saude" "API"
# No modo DEV o primeiro start instala as dependências do front (pode levar alguns minutos).
wait_http "$WEB_URL/" "Web" "$([[ "$target" == "dev" ]] && echo 300 || echo 60)"

echo
echo "Finbase no ar: Web $WEB_URL | API $API_URL"
echo "Logs: ./start-local.sh logs [api|web|worker|scheduler]"
