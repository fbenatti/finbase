"""Decisão de acesso: ACLs (tela, tabela, linha, campo), escopos e filtro de linha.

Regras de avaliação, na ordem:
1. Negação explícita (`deny`) que case com o pedido vence sempre.
2. Sem nenhuma ACL `allow` para o pedido: negado (padrão fechado).
3. ACL `allow` sem condição: acesso total ao recurso.
4. ACLs `allow` com condições: cada ACL exige todas as suas condições (AND) e o
   usuário precisa ter escopo do tipo pedido. As ACLs são somadas (OR).
"""
from dataclasses import dataclass

from app.models.access import ACL_EFFECTS, ACL_OPERATIONS, ACL_RESOURCE_TYPES, MATCH_MODES

HIERARCHY_SEPARATOR = "/"


@dataclass(frozen=True)
class Condition:
    column: str
    scope_type: str
    match_mode: str = "subtree"


@dataclass(frozen=True)
class Grant:
    """Uma ACL já carregada do banco, pronta para avaliação."""

    resource_type: str
    resource: str
    operation: str
    effect: str = "allow"
    conditions: tuple[Condition, ...] = ()
    active: bool = True
    role_id: int | None = None


@dataclass(frozen=True)
class ScopeValue:
    """Um valor de escopo que o usuário possui, já com o modo de comparação."""

    scope_type: str
    value: str
    match_mode: str = "subtree"


@dataclass(frozen=True)
class Predicate:
    """Coluna casa com `equal_to` (igualdade) ou com um nó de `under` (subárvore)."""

    column: str
    scope_type: str
    equal_to: tuple[str, ...]
    under: tuple[str, ...] = ()


@dataclass(frozen=True)
class RowFilter:
    """Forma normal disjuntiva: OR de cláusulas, cada cláusula é AND de predicados."""

    clauses: tuple[tuple[Predicate, ...], ...]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    row_filter: RowFilter | None = None  # None com allowed=True = sem filtro de linha


def _matches_resource(grant: Grant, resource_type: str, resource: str) -> bool:
    """Casa por igualdade, por "*" (qualquer recurso) ou por "tabela.*" (campos da tabela)."""
    if grant.resource_type != resource_type:
        return False
    if grant.resource in (resource, "*"):
        return True
    return grant.resource.endswith(".*") and resource.startswith(grant.resource[:-1])


def _validate_request(resource_type: str, operation: str) -> None:
    if resource_type not in ACL_RESOURCE_TYPES:
        raise ValueError(f"tipo de recurso inválido: {resource_type}")
    if operation not in ACL_OPERATIONS:
        raise ValueError(f"operação inválida: {operation}")


def _predicate_for(condition: Condition, scopes: list[ScopeValue]) -> Predicate | None:
    """Monta o predicado de uma condição a partir dos escopos do usuário.

    Um valor do usuário com `subtree` só vale como subárvore quando a condição
    da ACL também pede subárvore. Caso contrário vale só a igualdade.
    """
    matching = [s for s in scopes if s.scope_type == condition.scope_type]
    if not matching:
        return None
    equal_to = tuple(s.value for s in matching)
    under: tuple[str, ...] = ()
    if condition.match_mode == "subtree":
        under = tuple(s.value for s in matching if s.match_mode == "subtree")
    return Predicate(condition.column, condition.scope_type, equal_to, under)


def decide(
    grants: list[Grant],
    operation: str,
    resource_type: str,
    resource: str,
    scopes: list[ScopeValue] | None = None,
) -> Decision:
    """Decide se o usuário pode executar `operation` em `resource`."""
    _validate_request(resource_type, operation)
    scopes = scopes or []
    matching = [
        g for g in grants
        if g.active and g.operation == operation and _matches_resource(g, resource_type, resource)
    ]

    if any(g.effect == "deny" for g in matching):
        return Decision(False, "negado por ACL explícita")

    allows = [g for g in matching if g.effect == "allow"]
    if not allows:
        return Decision(False, "sem ACL de permissão")

    if any(not g.conditions for g in allows):
        return Decision(True, "acesso total ao recurso")

    clauses: list[tuple[Predicate, ...]] = []
    for grant in allows:
        predicates = [_predicate_for(c, scopes) for c in grant.conditions]
        if any(p is None for p in predicates):
            continue
        clauses.append(tuple(p for p in predicates if p is not None))

    if not clauses:
        return Decision(False, "sem escopo para as condições da ACL")
    return Decision(True, "acesso por escopo", RowFilter(tuple(clauses)))


def decide_field(
    grants: list[Grant],
    operation: str,
    table: str,
    field_name: str,
    scopes: list[ScopeValue] | None = None,
) -> Decision:
    """Atalho para campo: resource = "tabela.campo"."""
    return decide(grants, operation, "field", f"{table}.{field_name}", scopes)


def visible_fields(
    grants: list[Grant],
    table: str,
    fields: list[str],
    scopes: list[ScopeValue] | None = None,
) -> list[str]:
    """Campos que o usuário pode ler. Usado para filtrar a resposta."""
    return [f for f in fields if decide_field(grants, "read", table, f, scopes).allowed]


_SAFE_IDENTIFIER = "abcdefghijklmnopqrstuvwxyz0123456789_"


def _check_identifier(name: str) -> str:
    if not name or name[0].isdigit() or any(ch not in _SAFE_IDENTIFIER for ch in name):
        raise ValueError(f"identificador inseguro: {name!r}")
    return name


def compile_postgres(row_filter: RowFilter | None, prefix: str = "acl") -> tuple[str, dict[str, object]]:
    """Gera SQL parametrizado para Postgres. Nenhum valor entra na string SQL.

    Retorna ("TRUE", {}) sem filtro e ("FALSE", {}) quando não há cláusulas.
    Os nomes de coluna passam por allowlist de caracteres antes de entrar no SQL.
    """
    if row_filter is None:
        return "TRUE", {}
    if not row_filter.clauses:
        return "FALSE", {}

    params: dict[str, object] = {}
    clause_sql: list[str] = []
    counter = 0
    for clause in row_filter.clauses:
        parts: list[str] = []
        for pred in clause:
            column = _check_identifier(pred.column)
            counter += 1
            key = f"{prefix}_{counter}"
            params[key] = list(pred.equal_to)
            if pred.under:
                params[f"{key}_sub"] = [v + HIERARCHY_SEPARATOR + "%" for v in pred.under]
                parts.append(f"({column} = ANY(:{key}) OR {column} LIKE ANY(:{key}_sub))")
            else:
                parts.append(f"{column} = ANY(:{key})")
        clause_sql.append("(" + " AND ".join(parts) + ")")
    return "(" + " OR ".join(clause_sql) + ")", params


@dataclass(frozen=True)
class RuleCondition:
    field: str
    op: str  # eq|ne|in|same_actor
    value: object = None


@dataclass(frozen=True)
class BusinessRule:
    code: str
    event: str  # before_query|before_write|after_write
    resource: str
    conditions: tuple[RuleCondition, ...]
    message: str
    priority: int = 100
    active: bool = True


def _rule_condition_holds(cond: RuleCondition, record: dict, actor_id: object) -> bool:
    current = record.get(cond.field)
    if cond.op == "eq":
        return current == cond.value
    if cond.op == "ne":
        return current != cond.value
    if cond.op == "in":
        return current in (cond.value or [])
    if cond.op == "same_actor":
        return current is not None and current == actor_id
    raise ValueError(f"operador de regra inválido: {cond.op}")


def evaluate_business_rules(
    rules: list[BusinessRule],
    event: str,
    resource: str,
    record: dict,
    actor_id: object,
) -> str | None:
    """Retorna a mensagem da primeira regra que bloqueia, ou None.

    Regras são avaliadas por prioridade (menor primeiro). Uma regra bloqueia
    quando todas as condições são verdadeiras.
    """
    for rule in sorted(rules, key=lambda r: r.priority):
        if not rule.active or rule.event != event or rule.resource not in (resource, "*"):
            continue
        if all(_rule_condition_holds(c, record, actor_id) for c in rule.conditions):
            return rule.message
    return None


def expand_subtree(scopes: list[ScopeValue], children: dict[str, list[str]]) -> list[ScopeValue]:
    """Expande escopos `subtree` em valores exatos de todos os descendentes.

    Usado quando a hierarquia é uma árvore de ids (ex.: department.id/parent_id),
    antes da hierarquia virar caminho JSON. O resultado vale só para igualdade,
    então a decisão e o filtro não mudam de forma. Ciclos são ignorados.
    """
    expanded: list[ScopeValue] = []
    for scope in scopes:
        if scope.match_mode != "subtree":
            expanded.append(scope)
            continue
        seen: set[str] = set()
        stack = [scope.value]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(children.get(node, []))
        expanded.extend(ScopeValue(scope.scope_type, node, "exact") for node in sorted(seen))
    return expanded


def validate_grant_fields(effect: str, resource_type: str, operation: str) -> None:
    """Valida os valores antes de gravar uma ACL (espelha os CHECK da migração)."""
    if effect not in ACL_EFFECTS:
        raise ValueError(f"efeito inválido: {effect}")
    _validate_request(resource_type, operation)


def validate_match_mode(mode: str) -> None:
    if mode not in MATCH_MODES:
        raise ValueError(f"modo de comparação inválido: {mode}")
