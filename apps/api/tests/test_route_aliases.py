"""Aliases de rota: após a migração para rotas em português não há mais aliases."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.route_aliases import ROUTE_ALIASES, RouteAliasMiddleware, rewrite_path  # noqa: E402


def test_nao_ha_aliases():
    assert ROUTE_ALIASES == ()


def test_caminho_fica_igual():
    assert rewrite_path("/operacoes/execucoes/situacao") == "/operacoes/execucoes/situacao"
    assert rewrite_path("/autenticacao/entrar") == "/autenticacao/entrar"


def test_middleware_nao_reescreve():
    seen = {}

    async def fake_app(scope, receive, send):
        seen.update(scope)

    mw = RouteAliasMiddleware(fake_app)
    asyncio.run(mw({"type": "http", "path": "/dados/fontes/x"}, None, None))
    assert seen["path"] == "/dados/fontes/x"
    assert "route_alias_from" not in seen
