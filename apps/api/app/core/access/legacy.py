"""Ponte entre o RBAC legado (module, action, scope) e o modelo de ACL.

Permissões com escopo textual viram ACL de tela no recurso `modulo.escopo`.
Permissão sem escopo vira ACL no recurso `modulo`. A regra de equivalência
fica em `legacy_screen_allowed` e é testada contra o RBAC antigo.
"""
from collections.abc import Iterable

from app.core.access.policy import Decision, Grant, decide

LEGACY_TO_ACCESS_OPERATION = {
    "read": "read",
    "create": "create",
    "update": "write",
    "delete": "delete",
}


def access_operation(legacy_action: str) -> str:
    try:
        return LEGACY_TO_ACCESS_OPERATION[legacy_action]
    except KeyError as exc:
        raise ValueError(f"ação legada desconhecida: {legacy_action}") from exc


def screen_resource(module: str, scope: str | None) -> str:
    return module if scope is None else f"{module}.{scope}"


def grants_from_legacy(rows: Iterable[tuple[int, str, str, str | None]]) -> list[Grant]:
    """Converte linhas de role_permission (role_id, module, action, scope) em ACLs de tela."""
    return [
        Grant(
            resource_type="screen",
            resource=screen_resource(module, scope),
            operation=access_operation(action),
            role_id=role_id,
        )
        for role_id, module, action, scope in rows
    ]


def legacy_screen_allowed(grants: list[Grant], module: str, legacy_action: str, scope: str | None) -> Decision:
    """Equivalente do has_permission legado, usando ACLs de tela.

    Sem escopo, pede a permissão do módulo inteiro. Com escopo, aceita o módulo
    inteiro ou o escopo, como o has_permission antigo faz.
    """
    operation = access_operation(legacy_action)
    whole = decide(grants, operation, "screen", module)
    if whole.allowed or scope is None:
        return whole
    return decide(grants, operation, "screen", screen_resource(module, scope))
