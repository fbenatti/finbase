"""Colunas de hierarquia: ``hierarquia_1`` (raiz) > ``hierarquia_2`` > ...

Não há limite de níveis: a quantidade acompanha a profundidade da árvore de
``department``. Ao gravar, usa-se a profundidade atual da árvore; ao ler uma
tabela, usam-se os níveis ``hierarquia_N`` que existem no schema dela.
"""
from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

HIERARCHY_PREFIX = "hierarquia_"
# Nomes antigos, aceitos só para migrar tabelas e configurações existentes.
LEGACY_HIERARCHY_PREFIXES = ("organograma_nivel_", "departamento_nivel_")

_LEVEL_RE = re.compile(r"^hierarquia_(\d+)$")

# Profundidade mínima: tabelas e consultas sempre têm ao menos este nível.
MIN_LEVELS = 1

_DEPTH_SQL = """
    WITH RECURSIVE tree AS (
      SELECT id, 1 AS depth FROM department WHERE parent_id IS NULL
      UNION ALL
      SELECT d.id, t.depth + 1 FROM department d JOIN tree t ON d.parent_id = t.id
    )
    SELECT COALESCE(MAX(depth), 0) FROM tree
"""


def hierarchy_column(level: int) -> str:
    return f"{HIERARCHY_PREFIX}{level}"


def hierarchy_level(column: str) -> int | None:
    """Número do nível de ``hierarquia_N`` (``None`` para outras colunas, ex. hierarquia_id)."""
    match = _LEVEL_RE.match(str(column or "").strip().lower())
    return int(match.group(1)) if match else None


def is_hierarchy_column(column: str) -> bool:
    return hierarchy_level(column) is not None


def hierarchy_columns(depth: int) -> list[str]:
    return [hierarchy_column(level) for level in range(1, max(depth, MIN_LEVELS) + 1)]


def hierarchy_columns_in(columns: Iterable[str]) -> list[str]:
    """Colunas ``hierarquia_N`` presentes num schema, em ordem de nível."""
    found = {hierarchy_level(column): column for column in columns if is_hierarchy_column(column)}
    return [found[level] for level in sorted(found)]


def hierarchy_depth_of(columns: Iterable[str]) -> int:
    levels = [hierarchy_level(column) for column in columns]
    return max([level for level in levels if level] or [0])


def path_to_levels(path: Iterable[str], depth: int | None = None) -> dict[str, str]:
    """Caminho raiz→folha em ``{hierarquia_1: ..., hierarquia_N: ...}``; níveis além do caminho ficam vazios."""
    names = [str(name or "") for name in path]
    total = max(depth or 0, len(names), MIN_LEVELS)
    return {hierarchy_column(level): names[level - 1] if level <= len(names) else "" for level in range(1, total + 1)}


def legacy_to_hierarchy(name: str) -> str:
    """Converte ``organograma_nivel_N``/``departamento_nivel_N`` para ``hierarquia_N``."""
    for prefix in LEGACY_HIERARCHY_PREFIXES:
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return hierarchy_column(int(name[len(prefix):]))
    return name


def depth_from_departments(departments: Iterable) -> int:
    """Profundidade da árvore a partir de objetos com ``id`` e ``parent_id`` já carregados."""
    by_id = {node.id: node for node in departments}
    depth = 0
    for node in by_id.values():
        level, current, seen = 0, node, set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            level += 1
            current = by_id.get(current.parent_id) if current.parent_id else None
        depth = max(depth, level)
    return max(depth, MIN_LEVELS)


async def hierarchy_depth(db: AsyncSession) -> int:
    """Profundidade atual da árvore de hierarquia (mínimo ``MIN_LEVELS``)."""
    depth = (await db.execute(text(_DEPTH_SQL))).scalar() or 0
    return max(int(depth), MIN_LEVELS)


def hierarchy_depth_sync(conn: Connection) -> int:
    depth = conn.execute(text(_DEPTH_SQL)).scalar() or 0
    return max(int(depth), MIN_LEVELS)
