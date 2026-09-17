#!/usr/bin/env bash
set -Eeuo pipefail

# Keeps an Azure Bastion native tunnel and the SSH forwards alive. Azure CLI
# uses its normal cached login/token; no access token is written by this file.
# Run in a terminal (Ctrl+C stops both processes):
#   ./scripts/keep_bastion_tunnel.sh
#
# The default HTTPS port is 8443 so the script does not require sudo. Use
# LOCAL_HTTPS_PORT=443 only when a privileged SSH forward is already allowed.

SUBSCRIPTION_ID="${BASTION_SUBSCRIPTION_ID:-71cc41f4-8e37-4c60-abbb-b9003b91c554}"
BASTION_RG="${BASTION_RESOURCE_GROUP:-rg-network-production}"
BASTION_NAME="${BASTION_NAME:-vnet-prod-brsouth-01-bastion}"
VM_ID="${BASTION_VM_ID:-/subscriptions/6510c657-2706-4e3c-b9d2-f309b2ab639c/resourceGroups/RG-FINOPS-EASTUS-SHARED/providers/Microsoft.Compute/virtualMachines/vm-finops-brsouth-01}"
BASTION_PORT="${BASTION_PORT:-2022}"
SSH_USER="${BASTION_SSH_USER:-finops}"
LOCAL_HTTPS_PORT="${LOCAL_HTTPS_PORT:-8443}"
LOCAL_TRINO_PORT="${LOCAL_TRINO_PORT:-18080}"
LOG_DIR="${BASTION_LOG_DIR:-$PWD/.runtime-logs}"

mkdir -p "$LOG_DIR"

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
}
trap cleanup EXIT INT TERM

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
  ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ConnectTimeout=15 \
    -o StrictHostKeyChecking=yes \
    -o IdentitiesOnly=yes \
    -i "$HOME/.ssh/id_ed25519" \
    -p "$BASTION_PORT" \
    -L "127.0.0.1:5173:127.0.0.1:5173" \
    -L "127.0.0.1:8000:127.0.0.1:8000" \
    -L "127.0.0.1:$LOCAL_TRINO_PORT:127.0.0.1:8080" \
    -L "127.0.0.1:$LOCAL_HTTPS_PORT:127.0.0.1:443" \
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
