#!/usr/bin/env bash
# Update the restored VM from the GitHub main branch without touching data,
# PostgreSQL volumes, Iceberg warehouse, or ignored environment files.
set -Eeuo pipefail

project_dir="${OPENFINOPS_DIR:-/mnt/openfinops-backups/openfinops}"

if [[ ! -d "$project_dir/.git" ]]; then
  echo "Repositório Git não encontrado em: $project_dir" >&2
  exit 1
fi

cd "$project_dir"

# Do not overwrite an intentional operational edit on the VM.
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Há alterações não versionadas na VM; revise-as antes do deploy." >&2
  git status --short >&2
  exit 1
fi

# Update the tracking ref explicitly. `git fetch origin main` only updates
# FETCH_HEAD, while the fast-forward below merges origin/main; without this
# refspec an offline bundle-backed deployment can silently keep the old code.
git fetch origin main:refs/remotes/origin/main
git merge --ff-only origin/main

# This file is deliberately local to the VM and ignored by Git. It prevents
# the application from ever falling back to the development JWT signing key.
if [[ ! -s .env.production ]]; then
  umask 077
  jwt_secret="$(openssl rand -hex 48)"
  printf 'ENV=production\nJWT_SECRET_KEY=%s\nSSO_COOKIE_SECURE=true\n' "$jwt_secret" > .env.production
  chmod 600 .env.production
  echo "Criado .env.production com segredo JWT exclusivo da VM."
fi

docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d --build

if [[ -f infra/nginx/openfinops.conf ]]; then
  sudo install -o root -g root -m 0644 infra/nginx/openfinops.conf /etc/nginx/conf.d/openfinops.conf
  sudo nginx -t
  sudo systemctl reload nginx
fi

echo "Deploy concluído: $(git rev-parse --short HEAD)"
