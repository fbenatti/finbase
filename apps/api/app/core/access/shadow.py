"""Fase 6: modo sombra. Compara a decisão antiga (RBAC legado) com a nova (ACL).

Não altera a resposta. Só devolve o registro de divergência para ser logado,
e o corte acontece quando o log estiver sem divergência não explicada.
"""
from dataclasses import dataclass

from app.core.access.policy import Decision


@dataclass(frozen=True)
class Divergence:
    module: str
    operation: str
    legacy_allowed: bool
    new_allowed: bool
    new_reason: str


def compare(module: str, operation: str, legacy_allowed: bool, decision: Decision) -> Divergence | None:
    if legacy_allowed == decision.allowed:
        return None
    return Divergence(module, operation, legacy_allowed, decision.allowed, decision.reason)
