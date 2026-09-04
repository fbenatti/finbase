"""Enforcement de RBAC via middleware ASGI.

Mapeamento (plano, Fase 2):
  GET/HEAD → read | POST → create | PUT/PATCH → update | DELETE → delete
  módulo = maior prefixo de rota registrado em PATH_MODULE (router = módulo).

Routers de administração (/users, /groups, /roles) e /auth já têm suas
próprias dependências e são ignorados aqui. Rotas públicas: /auth/login,
/auth/refresh, /auth/sso/*, /health.
"""
import json

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.core import security
from app.core.rbac import get_effective_permissions
from app.database import async_session
from sqlalchemy import select

from app.models.identity import User

# maior prefixo primeiro; (prefixo, módulo)
PATH_MODULE: list[tuple[str, str]] = [
    ("/visoes-consumo-ia/provider", "ai_provider_dashboards"),
    ("/etl/periods", "periods"),
    ("/connections", "connections"),
    ("/csv-upload", "csv_upload"),
    ("/etl", "etl"),
    ("/mappings", "rules"),
    ("/business-rules", "rules"),
    ("/classifications", "rules"),
    ("/departments", "rules"),
    ("/analysis", "analysis"),
    ("/ai-dashboards", "ai_dashboards"),
    ("/ai-copilot", "ai_copilot"),
    ("/visoes-consumo-ia", "azure_ptu"),
    ("/destinations", "destinations"),
    ("/schema", "schema"),
    ("/forecast", "forecast"),
    ("/clone-rules", "clone_rules"),
    ("/planning", "planning"),
    ("/savings", "savings"),
    ("/azure-tag-policy", "azure_tag_policy"),
    ("/dimon", "dimon"),
    ("/azure-resource-graph", "azure_resource_graph"),
    ("/reservations", "reservations"),
    ("/scheduled-shutdown", "scheduled_shutdown"),
    ("/oci-operational", "oci_operational"),
    ("/fact", "fact"),
]

PUBLIC_PATHS = ("/auth/login", "/auth/refresh", "/auth/sso", "/health")
# já protegidos por Depends(require(...)) nos próprios routers
SKIP_PREFIXES = ("/auth", "/users", "/groups", "/roles")

METHOD_ACTION = {
    "GET": "read",
    "HEAD": "read",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}


def _module_for(path: str) -> str | None:
    for prefix, module in PATH_MODULE:
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


async def _respond(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


class RBACMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path.startswith(PUBLIC_PATHS) or path.startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        module = _module_for(path)
        if module is None:
            await self.app(scope, receive, send)
            return

        action = METHOD_ACTION.get(scope.get("method", ""))
        if action is None:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")

        async with async_session() as db:
            user: User | None = None
            # Bypass de DEV
            if not settings.auth_enabled and settings.env != "production":
                result = await db.execute(select(User).where(User.username == "dev-admin"))
                user = result.scalar_one_or_none()
            elif settings.dev_master_token and settings.env != "production" and auth:
                scheme, _, token = auth.partition(" ")
                if scheme.lower() == "bearer" and security.tokens_equal(token, settings.dev_master_token):
                    result = await db.execute(select(User).where(User.username == "dev-admin"))
                    user = result.scalar_one_or_none()
            else:
                scheme, _, token = auth.partition(" ")
                if scheme.lower() == "bearer" and token:
                    try:
                        payload = security.decode_access_token(token)
                        result = await db.execute(select(User).where(User.id == int(payload["sub"])))
                        candidate = result.scalar_one_or_none()
                        if candidate is not None and candidate.is_active:
                            user = candidate
                    except Exception:
                        user = None

            if user is None:
                await _respond(send, 401, "Não autenticado")
                return

            perms = await get_effective_permissions(db, user.id)
            if action not in perms.get(module, set()):
                await _respond(send, 403, "Sem permissão para esta ação.")
                return

        await self.app(scope, receive, send)
