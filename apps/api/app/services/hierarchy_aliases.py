"""Aliases de nível da hierarquia: qual papel (Provedor, Cliente...) cada nível tem.

O significado de ``hierarquia_N`` muda de ramo para ramo: num organograma o
nível 4 é Cliente, noutro é Diretoria. As regras dizem "a partir deste nó, a
profundidade N é o papel X"; o resolver recebe um caminho de nomes (como os
painéis gold o conhecem) e devolve o rótulo de cada profundidade.

Precedência para a profundidade ``d``:
1. Se o caminho para antes de ``d`` e todos os filhos do último nó têm regra
   própria para ``d`` com o mesmo papel, vale esse papel.
2. Regra do escopo mais profundo entre os nós do caminho até ``d``.
3. Regra global (sem escopo).
4. "Nível N".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hierarchy import hierarchy_column
from app.models.hierarchy_alias import HierarchyLevelAlias, HierarchyRole
from app.models.rules import Department

# Profundidade mínima devolvida, para os painéis que ainda montam até o nível 6.
MIN_RESOLVED_DEPTH = 6

DEFAULT_ROLES = (
    ("segmento", "Segmento", "Segmentos"),
    ("modelo", "Modelo", "Modelos"),
    ("provedor", "Provedor", "Provedores"),
    ("cliente", "Cliente", "Clientes"),
    ("diretoria", "Diretoria", "Diretorias"),
    ("projeto", "Projeto", "Projetos"),
    ("subprojeto", "Subprojeto", "Subprojetos"),
)
# Organograma padrão: Cloud > Showback/Chargeback > Provedor > Cliente > Projeto.
DEFAULT_GLOBAL_LEVELS = ("segmento", "modelo", "provedor", "cliente", "projeto", "subprojeto")


@dataclass(frozen=True)
class RoleInfo:
    id: int
    slug: str
    label: str
    label_plural: str | None


@dataclass(frozen=True)
class NodeInfo:
    id: int
    parent_id: int | None
    name: str


def _key(name: str | None) -> str:
    return (name or "").strip().casefold()


def match_path(path: list[str], nodes: Iterable[NodeInfo]) -> list[NodeInfo]:
    """Nós da árvore que correspondem ao caminho de nomes, da raiz para baixo.

    Para no primeiro nome sem correspondência: o resto do caminho não tem nó.
    """
    children: dict[int | None, dict[str, NodeInfo]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, {}).setdefault(_key(node.name), node)
    matched: list[NodeInfo] = []
    parent_id: int | None = None
    for name in path:
        node = children.get(parent_id, {}).get(_key(name))
        if node is None:
            break
        matched.append(node)
        parent_id = node.id
    return matched


def resolve_levels(
    path: list[str],
    nodes: list[NodeInfo],
    rules: list[tuple[int | None, int, int]],
    roles: dict[int, RoleInfo],
    max_depth: int,
) -> dict:
    """Resolve o papel de cada profundidade (1..max_depth) no escopo do caminho.

    ``rules`` são tuplas ``(scope_department_id, depth, role_id)``.
    """
    matched = match_path(path, nodes)
    by_scope_depth = {(scope, depth): role_id for scope, depth, role_id in rules}
    children: dict[int | None, list[int]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node.id)

    levels = []
    for depth in range(1, max(max_depth, MIN_RESOLVED_DEPTH, len(path)) + 1):
        role_id: int | None = None
        source = "padrao"
        scope_name: str | None = None
        mixed = False

        # 1. Filhos do fim do caminho com regra própria e unânime.
        if len(matched) == depth - 1:
            parent_id = matched[-1].id if matched else None
            child_roles = [by_scope_depth.get((child, depth)) for child in children.get(parent_id, [])]
            explicit = {r for r in child_roles if r is not None}
            if explicit and None not in child_roles and len(explicit) == 1:
                role_id = explicit.pop()
                source = "escopo"
            elif explicit:
                mixed = True

        # 2. Escopo mais profundo do caminho (até a própria profundidade).
        if role_id is None:
            for node in reversed(matched[:depth]):
                candidate = by_scope_depth.get((node.id, depth))
                if candidate is not None:
                    role_id, source, scope_name = candidate, "escopo", node.name
                    break

        # 3. Regra global.
        if role_id is None and (None, depth) in by_scope_depth:
            role_id, source = by_scope_depth[(None, depth)], "global"

        role = roles.get(role_id) if role_id is not None else None
        label = role.label if role else f"Nível {depth}"
        levels.append({
            "depth": depth,
            "column": hierarchy_column(depth),
            "role": None if role is None else {"id": role.id, "slug": role.slug, "label": role.label, "label_plural": role.label_plural},
            "label": label,
            "label_plural": (role.label_plural if role and role.label_plural else label),
            "source": source,
            "scope": scope_name,
            "mixed": mixed,
        })
    return {"path": path, "matched_depth": len(matched), "levels": levels}


async def load_resolver_inputs(db: AsyncSession) -> tuple[list[NodeInfo], list[tuple[int | None, int, int]], dict[int, RoleInfo]]:
    nodes = [
        NodeInfo(id=row.id, parent_id=row.parent_id, name=row.name)
        for row in (await db.execute(select(Department.id, Department.parent_id, Department.name).where(Department.active.is_(True)))).all()
    ]
    rules = [
        (row.scope_department_id, row.depth, row.role_id)
        for row in (await db.execute(select(HierarchyLevelAlias.scope_department_id, HierarchyLevelAlias.depth, HierarchyLevelAlias.role_id))).all()
    ]
    roles = {
        row.id: RoleInfo(id=row.id, slug=row.slug, label=row.label, label_plural=row.label_plural)
        for row in (await db.execute(select(HierarchyRole))).scalars().all()
    }
    return nodes, rules, roles


def tree_depth(nodes: list[NodeInfo]) -> int:
    parents = {node.id: node.parent_id for node in nodes}
    deepest = 0
    for node_id in parents:
        depth, current, seen = 0, node_id, set()
        while current is not None and current not in seen:
            seen.add(current)
            depth += 1
            current = parents.get(current)
        deepest = max(deepest, depth)
    return deepest


async def seed_default_aliases(db: AsyncSession) -> None:
    """Cria o catálogo e as regras globais padrão só na primeira vez (catálogo vazio)."""
    if (await db.execute(select(HierarchyRole.id).limit(1))).scalar() is not None:
        return
    roles = {slug: HierarchyRole(slug=slug, label=label, label_plural=plural) for slug, label, plural in DEFAULT_ROLES}
    db.add_all(roles.values())
    await db.flush()
    db.add_all(
        HierarchyLevelAlias(scope_department_id=None, depth=depth, role_id=roles[slug].id)
        for depth, slug in enumerate(DEFAULT_GLOBAL_LEVELS, start=1)
    )
