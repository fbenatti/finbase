"""Aliases de rota (Fase 3A): caminhos do front viram os prefixos atuais da API.

A chamada do front e a da API seguem a mesma árvore de rotas, sem renomear os
routers. Um middleware ASGI reescreve `/dados/fontes/...` para `/connections/...`
antes da autorização e da auditoria, então o alias recebe a mesma ACL e o mesmo
registro de auditoria que o caminho canônico.

Só entram aliases com correspondência única. Áreas que cobrem vários routers
(ex.: /governanca, /administracao/acessos) continuam sem alias até a Fase 5.
"""
from starlette.types import ASGIApp, Receive, Scope, Send

# (prefixo do front, prefixo atual da API). Ordem não importa: a busca usa o maior prefixo.
ROUTE_ALIASES: tuple[tuple[str, str], ...] = ()


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def rewrite_path(path: str) -> str:
    """Devolve o caminho canônico da API, ou o próprio caminho se não houver alias."""
    best_front = ""
    best_api = ""
    for front, api in ROUTE_ALIASES:
        if _matches(path, front) and len(front) > len(best_front):
            best_front, best_api = front, api
    if not best_front:
        return path
    return best_api + path[len(best_front):]


class RouteAliasMiddleware:
    """Reescreve o caminho HTTP antes do restante da pilha. Preserva o original em scope['route_alias_from']."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            original = scope["path"]
            canonical = rewrite_path(original)
            if canonical != original:
                scope = {**scope, "path": canonical, "route_alias_from": original}
        await self.app(scope, receive, send)
