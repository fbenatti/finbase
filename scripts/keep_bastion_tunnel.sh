#!/usr/bin/env bash
set -Eeuo pipefail

# Keeps an Azure Bastion native tunnel and the SSH forwards alive. Azure CLI
# uses its normal cached login/token; no access token is written by this file.
# Run in a terminal (Ctrl+C stops both processes):
#   ./scripts/keep_bastion_tunnel.sh
#
# The default HTTPS port is 8443 so the script does not require sudo. When
# LOCAL_HTTPS_PORT is a privileged port (e.g. 443), only the SSH forward is
# elevated; Azure CLI/Bastion still run as the logged-in user.

SUBSCRIPTION_ID="${BASTION_SUBSCRIPTION_ID:-71cc41f4-8e37-4c60-abbb-b9003b91c554}"
BASTION_RG="${BASTION_RESOURCE_GROUP:-rg-network-production}"
BASTION_NAME="${BASTION_NAME:-vnet-prod-brsouth-01-bastion}"
VM_ID="${BASTION_VM_ID:-/subscriptions/6510c657-2706-4e3c-b9d2-f309b2ab639c/resourceGroups/RG-FINOPS-EASTUS-SHARED/providers/Microsoft.Compute/virtualMachines/vm-finops-brsouth-01}"
BASTION_PORT="${BASTION_PORT:-2022}"
SSH_USER="${BASTION_SSH_USER:-finops}"
LOCAL_HTTPS_PORT="${LOCAL_HTTPS_PORT:-8443}"
LOCAL_TRINO_PORT="${LOCAL_TRINO_PORT:-18080}"
# O frontend local do OpenFinops usa a porta 5173. Para manter o túnel ativo
# simultaneamente, exponha o frontend remoto em outra porta local.
LOCAL_REMOTE_FRONTEND_PORT="${LOCAL_REMOTE_FRONTEND_PORT:-5175}"
LOG_DIR="${BASTION_LOG_DIR:-$PWD/.runtime-logs}"
LOCK_DIR="$LOG_DIR/.bastion-tunnel.lock"

mkdir -p "$LOG_DIR"

# Não permita dois processos brigando pelo túnel do Bastion e pelos forwards.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$LOCK_DIR/pid" ]] && kill -0 "$(<"$LOCK_DIR/pid")" 2>/dev/null; then
    echo "Já existe um keep_bastion_tunnel.sh em execução (PID $(<"$LOCK_DIR/pid"))." >&2
    exit 1
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi
printf '%s\n' "$$" >"$LOCK_DIR/pid"

SSH_PREFIX=()
if [[ "$LOCAL_HTTPS_PORT" =~ ^[0-9]+$ ]] && (( LOCAL_HTTPS_PORT < 1024 )); then
  echo "A porta HTTPS $LOCAL_HTTPS_PORT requer privilégio administrativo local."
  sudo -v
  SSH_PREFIX=(sudo)
fi

if ! az account show --subscription "$SUBSCRIPTION_ID" >/dev/null 2>&1; then
  echo "Azure CLI não está autenticado na subscription $SUBSCRIPTION_ID." >&2
  echo "Execute: az login --use-device-code --tenant telefonicati.onmicrosoft.com" >&2
  exit 1
fi
az account set --subscription "$SUBSCRIPTION_ID" >/dev/null

cleanup() {
  trap - EXIT INT TERM
  [[ -n "${SSH_PID:-}" ]] && kill "$SSH_PID" 2>/dev/null || true
  [[ -n "${BASTION_PID:-}" ]] && kill "$BASTION_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

SSH_FORWARDS=()
add_forward_if_available() {
  local local_port="$1" remote_port="$2" label="$3"
  if nc -z 127.0.0.1 "$local_port" >/dev/null 2>&1; then
    echo "Porta local $local_port ($label) já está em uso; forward ignorado." >&2
    return
  fi
  SSH_FORWARDS+=(-L "127.0.0.1:$local_port:127.0.0.1:$remote_port")
}

# A máquina de desenvolvimento pode já estar usando 5173/8000. Isso não deve
# impedir o HTTPS do ambiente remoto nem o acesso ao Trino pelo túnel.
add_forward_if_available "$LOCAL_REMOTE_FRONTEND_PORT" 5173 "frontend remoto"
add_forward_if_available 8000 8000 "API"
add_forward_if_available "$LOCAL_TRINO_PORT" 8080 "Trino"
add_forward_if_available "$LOCAL_HTTPS_PORT" 443 "HTTPS"

while :; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Abrindo túnel Bastion..."
  az network bastion tunnel \
    --subscription "$SUBSCRIPTION_ID" \
    --name "$BASTION_NAME" \
    --resource-group "$BASTION_RG" \
    --target-resource-id "$VM_ID" \
    --resource-port 22 \
    --port "$BASTION_PORT" \
    >>"$LOG_DIR/bastion-tunnel.log" 2>&1 &
  BASTION_PID=$!

  for _ in $(seq 1 60); do
    if ! kill -0 "$BASTION_PID" 2>/dev/null; then break; fi
    if nc -z 127.0.0.1 "$BASTION_PORT" >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if ! nc -z 127.0.0.1 "$BASTION_PORT" >/dev/null 2>&1; then
    echo "Bastion não abriu a porta $BASTION_PORT; veja $LOG_DIR/bastion-tunnel.log" >&2
    kill "$BASTION_PID" 2>/dev/null || true
    wait "$BASTION_PID" 2>/dev/null || true
    sleep 5
    continue
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bastion ativo; mantendo forwards SSH."
  "${SSH_PREFIX[@]}" ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ConnectTimeout=15 \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$HOME/.ssh/known_hosts" \
    -o IdentitiesOnly=yes \
    -i "$HOME/.ssh/id_ed25519" \
    -p "$BASTION_PORT" \
    "${SSH_FORWARDS[@]}" \
    "$SSH_USER@127.0.0.1" \
    >>"$LOG_DIR/ssh-forwards.log" 2>&1 &
  SSH_PID=$!

  while kill -0 "$BASTION_PID" 2>/dev/null && kill -0 "$SSH_PID" 2>/dev/null; do
    sleep 5
  done
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Conexão caiu; reconectando em 5s..."
  kill "$SSH_PID" "$BASTION_PID" 2>/dev/null || true
  wait "$SSH_PID" "$BASTION_PID" 2>/dev/null || true
  unset SSH_PID BASTION_PID
  sleep 5
done
