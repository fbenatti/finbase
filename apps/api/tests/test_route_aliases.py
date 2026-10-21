"""Fase 3A do RBAC: aliases de rota do front para os prefixos da API."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.route_aliases import ROUTE_ALIASES, RouteAliasMiddleware, rewrite_path  # noqa: E402


def test_alias_reescreve_prefixo_e_preserva_resto():
    assert rewrite_path("/dados/fontes/12/tabelas") == "/connections/12/tabelas"


def test_alias_exato_sem_barra_final():
    assert rewrite_path("/custos") == "/gold"


def test_prefixo_parcial_nao_casa():
    assert rewrite_path("/custosextra/x") == "/custosextra/x"


def test_caminho_sem_alias_fica_igual():
    assert rewrite_path("/connections/1") == "/connections/1"
    assert rewrite_path("/auth/login") == "/auth/login"


def test_maior_prefixo_vence():
    # /dados/fluxos e /dados/mapeamentos não se sobrepõem, mas garantimos a busca
    assert rewrite_path("/dados/mapeamentos/x") == "/mappings/x"


def test_aliases_nao_apontam_para_si_mesmos():
    for front, api in ROUTE_ALIASES:
        assert front != api


def test_aliases_sem_duplicatas_de_front():
    fronts = [f for f, _ in ROUTE_ALIASES]
    assert len(fronts) == len(set(fronts))


def test_middleware_reescreve_scope_http_e_guarda_original():
    seen = {}

    async def app(scope, receive, send):
        seen.update(scope)

    mw = RouteAliasMiddleware(app)
    asyncio.run(mw({"type": "http", "path": "/planejamento/orcamento"}, None, None))
    assert seen["path"] == "/planning/orcamento"
    assert seen["route_alias_from"] == "/planejamento/orcamento"


def test_middleware_ignora_websocket_e_caminho_sem_alias():
    seen = {}

    async def app(scope, receive, send):
        seen.update(scope)

    mw = RouteAliasMiddleware(app)
    asyncio.run(mw({"type": "http", "path": "/health"}, None, None))
    assert seen["path"] == "/health" and "route_alias_from" not in seen
    asyncio.run(mw({"type": "websocket", "path": "/custos"}, None, None))
    assert seen["path"] == "/custos"
