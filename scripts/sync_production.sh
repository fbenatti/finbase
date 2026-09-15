#!/usr/bin/env bash
# Local Git workflow for the production VM. The VM pulls read-only through
# its deploy key, rebuilds images, and keeps its database/warehouse intact.
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_host="${OPENFINOPS_PROD_HOST:-finops-brsouth}"
remote_dir="${OPENFINOPS_PROD_DIR:-/mnt/openfinops-backups/openfinops}"

cd "$project_dir"

usage() {
  echo "Uso: $0 {status|pull|push <mensagem>|deploy|publish <mensagem>}" >&2
}

require_clean() {
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Há alterações locais não commitadas. Use '$0 push \"mensagem\"'." >&2
    exit 1
  fi
}

push_changes() {
  local commit_message="${1:?Informe a mensagem do commit.}"
  git add -A
  git diff --cached --check
  if git diff --cached --quiet; then
    echo "Nenhuma alteração para publicar."
    return
  fi
  git commit -m "$commit_message"
  git push origin main
}

deploy_remote() {
  require_clean
  ssh "$remote_host" "'$remote_dir/scripts/deploy_vm.sh'"
}

case "${1:-}" in
  status)
    git status --short
    git log --oneline -1
    ssh "$remote_host" "cd '$remote_dir' && git status --short && git log --oneline -1"
    ;;
  pull)
    require_clean
    git pull --ff-only origin main
    ;;
  push)
    push_changes "${2:-}"
    ;;
  deploy)
    deploy_remote
    ;;
  publish)
    push_changes "${2:-}"
    deploy_remote
    ;;
  *)
    usage
    exit 2
    ;;
esac
