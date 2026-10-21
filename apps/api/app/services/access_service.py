"""Fase 3: carrega ACLs e escopos do banco e aplica a decisão do motor puro.

Também aplica o filtro de linha em consultas SQLAlchemy, para Postgres e SQLite.
"""
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.access.policy import (
    Condition,
    Decision,
    Grant,
    RowFilter,
    ScopeValue,
    decide,
)
from app.core.rbac import _expand_with_trace, _load_user_context
from app.models.access import AccessAcl, AccessScope, GroupScope, UserScope
from app.models.identity import user_group


async def load_grants(db: AsyncSession, user_id: int) -> list[Grant]:
    """ACLs ativas de todos os perfis do usuário, inclusive os herdados."""
    role_ids, _ = await _load_user_context(db, user_id)
    expanded, _ = await _expand_with_trace(db, role_ids)
    if not expanded:
        return []
    acls = (await db.execute(
        select(AccessAcl)
        .options(selectinload(AccessAcl.conditions))
        .where(AccessAcl.role_id.in_(expanded), AccessAcl.active.is_(True))
    )).scalars().all()
    return [
        Grant(
            resource_type=acl.resource_type,
            resource=acl.resource,
            operation=acl.operation,
            effect=acl.effect,
            conditions=tuple(Condition(c.column_name, c.scope_type, c.match_mode) for c in acl.conditions),
            active=acl.active,
            role_id=acl.role_id,
        )
        for acl in acls
    ]


async def load_scopes(db: AsyncSession, user_id: int, now: datetime | None = None) -> list[ScopeValue]:
    """Escopos do usuário (diretos e por grupo). Escopos vencidos não entram."""
    now = now or datetime.now(timezone.utc)
    direct = (await db.execute(
        select(AccessScope.scope_type, AccessScope.value, UserScope.match_mode, UserScope.expires_at)
        .join(UserScope, UserScope.scope_id == AccessScope.id)
        .where(UserScope.user_id == user_id)
    )).all()
    via_group = (await db.execute(
        select(AccessScope.scope_type, AccessScope.value, GroupScope.match_mode)
        .join(GroupScope, GroupScope.scope_id == AccessScope.id)
        .where(GroupScope.group_id.in_(select(user_group.c.group_id).where(user_group.c.user_id == user_id)))
    )).all()
    scopes = [
        ScopeValue(scope_type, value, mode)
        for scope_type, value, mode, expires_at in direct
        if expires_at is None or expires_at > now
    ]
    scopes.extend(ScopeValue(scope_type, value, mode) for scope_type, value, mode in via_group)
    return scopes


async def load_children(db: AsyncSession) -> dict[str, list[str]]:
    """Mapa pai → filhos da árvore de departamentos (ids como texto)."""
    # Import tardio: app.models.rules puxa tabelas de outro domínio para o metadata.
    from app.models.rules import Department

    children: dict[str, list[str]] = {}
    for dept_id, parent_id in (await db.execute(select(Department.id, Department.parent_id))).all():
        if parent_id is not None:
            children.setdefault(str(parent_id), []).append(str(dept_id))
    return children


async def decide_for_user(
    db: AsyncSession,
    user_id: int,
    operation: str,
    resource_type: str,
    resource: str,
    now: datetime | None = None,
    use_cache: bool = False,
) -> Decision:
    """Decisão para uma pessoa. Com use_cache, consulta o cache versionado (access_cache)."""
    from app.services import access_cache

    version = None
    if use_cache and now is None:
        version = await access_cache.current_version(db)
        cached = access_cache.get_cached(version, user_id, operation, resource_type, resource)
        if cached is not None:
            return cached
    grants = await load_grants(db, user_id)
    scopes = await load_scopes(db, user_id, now)
    children = await load_children(db)
    decision = decide(grants, operation, resource_type, resource, scopes, children=children)
    if version is not None:
        access_cache.put_cached(version, user_id, operation, resource_type, resource, decision)
    return decision


def _coerce(column, value):
    """Valores da ACL são texto. Colunas inteiras (ids) recebem int, para não quebrar o tipo."""
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value
    if python_type is int:
        return int(value)
    return value


def apply_row_filter(stmt, columns: dict, row_filter: RowFilter | None):
    """Aplica o filtro de linha a um select do SQLAlchemy.

    `columns` mapeia o nome lógico da coluna (o mesmo de Condition.column) para
    a coluna ORM. Coluna sem mapeamento não filtra: levanta KeyError.
    Sem filtro (None) devolve o select intacto. Filtro sem cláusulas vira
    condição falsa.
    """
    if row_filter is None:
        return stmt
    if not row_filter.clauses:
        return stmt.where(False)
    clause_exprs = []
    for clause in row_filter.clauses:
        parts = []
        for pred in clause:
            column = columns[pred.column]
            values = [_coerce(column, v) for v in pred.equal_to]
            if pred.under:
                parts.append(or_(column.in_(values), *[column.like(v + "/%") for v in pred.under]))
            else:
                parts.append(column.in_(values))
        clause_exprs.append(and_(*parts))
    return stmt.where(or_(*clause_exprs))
