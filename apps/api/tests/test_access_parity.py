"""Paridade do modo sombra (Fase 6): ACL de tela equivale ao has_permission legado.

Compara todas as combinações pequenas de permissões, pedidos e escopos.
"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.access.legacy import grants_from_legacy, legacy_screen_allowed  # noqa: E402
from app.core.rbac import has_permission  # noqa: E402

MODULES = ["gold", "planning"]
ACTIONS = ["read", "create", "update", "delete"]
SCOPES = [None, "visao-contabil", "defesa-publicar"]


def _legacy_perms(rows):
    perms: dict[str, dict[str, set]] = {}
    for _, module, action, scope in rows:
        perms.setdefault(module, {}).setdefault(action, set()).add(scope)
    return perms


def _all_rows():
    options = [None] + [(m, a, s) for m in MODULES for a in ACTIONS for s in SCOPES]
    for size in range(0, 4):
        for combo in itertools.combinations([o for o in options if o is not None], size):
            yield [(1, m, a, s) for m, a, s in combo]


def test_acl_de_tela_tem_paridade_com_has_permission_legado():
    checked = 0
    for rows in _all_rows():
        legacy = _legacy_perms(rows)
        grants = grants_from_legacy(rows)
        for module, action, scope in itertools.product(MODULES, ACTIONS, SCOPES):
            expected = has_permission(legacy, module, action, scope)
            got = legacy_screen_allowed(grants, module, action, scope).allowed
            assert got == expected, (rows, module, action, scope)
            checked += 1
    assert checked > 1000
