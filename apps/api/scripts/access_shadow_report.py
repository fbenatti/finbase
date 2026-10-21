"""Relatório do modo sombra sobre um banco real. Somente leitura.

Uso (a partir de apps/api):
    DATABASE_URL=postgresql+asyncpg://usuario:senha@host:5432/banco \
      ../../.venv/bin/python scripts/access_shadow_report.py

Lista cada divergência entre o RBAC legado e as ACLs de tela. A saída é CSV em
stdout. Nenhuma escrita é feita. Rode antes de trocar ACCESS_ENGINE para acl.
"""
import asyncio
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.access.shadow_review import compare_all, divergences  # noqa: E402
from app.core.modules import ALL_MODULES  # noqa: E402
from app.models.identity import RolePermission  # noqa: E402


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Defina DATABASE_URL (somente leitura).", file=sys.stderr)
        return 2
    engine = create_async_engine(url)
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            scopes: dict[str, list[str | None]] = {m: [None] for m in ALL_MODULES}
            for module, scope in (await session.execute(
                select(RolePermission.module, RolePermission.scope).where(RolePermission.scope.is_not(None)).distinct()
            )).all():
                scopes.setdefault(module, [None]).append(scope)
            rows = await compare_all(session, sorted(scopes), scopes)
    finally:
        await engine.dispose()

    diffs = divergences(rows)
    writer = csv.writer(sys.stdout)
    writer.writerow(["usuario", "modulo", "acao", "escopo", "legado", "acl", "tipo"])
    for r in diffs:
        writer.writerow([r.username, r.module, r.action, r.scope or "", r.legacy, r.acl, r.kind])
    print(f"# comparações: {len(rows)} · divergências: {len(diffs)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
