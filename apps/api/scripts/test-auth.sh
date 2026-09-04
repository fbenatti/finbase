#!/usr/bin/env bash
# Suite mínima de auth/RBAC/auditoria (unidades puras, sem banco).
# Para E2E com banco/API, ver docs/auth-runbook.md (cenários curl).
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH=apps/api python -m pytest apps/api/tests/test_auth_unit.py apps/api/tests/test_audit_unit.py -q "$@"
