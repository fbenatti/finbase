"""Ponte entre o RBAC legado (module, action, scope) e o modelo de ACL.

Usado na migração (Fase 6): as permissões antigas viram ACLs de tela, e a
decisão nova pode ser comparada com a antiga em modo sombra.
"""
from collections.abc import Iterable

from app.core.access.policy import Grant

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


def grants_from_legacy(rows: Iterable[tuple[int, str, str, str | None]]) -> list[Grant]:
    """Converte linhas de role_permission (role_id, module, action, scope) em ACLs de tela.

    Permissões com `scope` não viram ACL de tela: o escopo textual legado não é
    um escopo de dados. Essas linhas são devolvidas por `legacy_scoped_rows` para
    serem tratadas na migração de escopos.
    """
    grants: list[Grant] = []
    for role_id, module, action, scope in rows:
        if scope is not None:
            continue
        grants.append(Grant(
            resource_type="screen",
            resource=module,
            operation=access_operation(action),
            role_id=role_id,
        ))
    return grants


def legacy_scoped_rows(rows: Iterable[tuple[int, str, str, str | None]]) -> list[tuple[int, str, str, str]]:
    """Linhas legadas com escopo textual, que precisam de revisão manual na migração."""
    return [(role_id, module, action, scope) for role_id, module, action, scope in rows if scope is not None]
