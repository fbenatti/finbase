#!/usr/bin/env bash
# Publish committed main and deploy it to the bundle-backed production checkout.
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_host="${OPENFINOPS_PROD_HOST:-finops-brsouth}"
remote_dir="${OPENFINOPS_PROD_DIR:-/mnt/openfinops-backups/openfinops}"
bundle_dir="${OPENFINOPS_PROD_BUNDLE_DIR:-/mnt/openfinops-backups}"
branch="main"

cd "$project_dir"
current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "$branch" ]]; then
  echo "Deploy requer a branch '$branch'; atual: '$current_branch'." >&2
  exit 1
fi

git fetch origin "$branch:refs/remotes/origin/$branch"
if ! git merge-base --is-ancestor "origin/$branch" "$branch"; then
  echo "A branch local '$branch' está atrás/divergente de origin/$branch. Atualize e revise antes do deploy." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Aviso: alterações locais sem commit serão excluídas do release." >&2
fi

git push origin "$branch"
target="$(git rev-parse "$branch")"
short_target="$(git rev-parse --short=8 "$target")"
remote_bundle="$bundle_dir/openfinops-prod-release-$short_target.bundle"
local_bundle="${TMPDIR:-/tmp}/openfinops-prod-release-$short_target-$$.bundle"
trap 'rm -f "$local_bundle"' EXIT

remote_status="$(ssh "$remote_host" "git -C '$remote_dir' status --porcelain --untracked-files=normal")"
if [[ -n "$remote_status" ]]; then
  echo "Checkout remoto tem alterações operacionais; abortando sem sobrescrevê-las:" >&2
  printf '%s\n' "$remote_status" >&2
  exit 1
fi

base="$(ssh "$remote_host" "git -C '$remote_dir' rev-parse HEAD")"
if [[ "$base" == "$target" ]]; then
  echo "Produção já está no commit $short_target."
  exit 0
fi
if ! git merge-base --is-ancestor "$base" "$target"; then
  echo "O commit de produção $base não é ancestral de $target; deploy interrompido." >&2
  exit 1
fi

git bundle create "$local_bundle" "$branch" "^$base"
git bundle verify "$local_bundle"
scp "$local_bundle" "$remote_host:$remote_bundle"

ssh "$remote_host" \
  "git -C '$remote_dir' bundle verify '$remote_bundle' >/dev/null && git -C '$remote_dir' remote set-url origin '$remote_bundle' && git -C '$remote_dir' fetch origin main:refs/remotes/origin/main && git -C '$remote_dir' merge --ff-only origin/main && cd '$remote_dir' && scripts/deploy_vm.sh && test \"\$(git rev-parse HEAD)\" = '$target'"

ssh "$remote_host" \
  "cd '$remote_dir' && docker compose -f docker-compose.yml -f docker-compose.vm.yml ps && docker exec openfinops-api-1 python -c 'import requests; h=requests.get(\"http://127.0.0.1:8000/etl/worker-health\", timeout=10).json(); assert h.get(\"status\")==\"ok\", h; print(h)' && docker exec openfinops-api-1 python -c 'import requests; x=requests.get(\"http://trino:8080/v1/info\", timeout=10).json(); assert not x.get(\"starting\", True), x; print(\"Trino pronto\")' && docker exec openfinops-web-1 sh -c 'wget -qO- http://127.0.0.1/ | grep -qi \"<html\"'"

echo "Produção validada no commit $short_target."
