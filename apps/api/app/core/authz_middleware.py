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
from app.core.rbac import get_effective_permissions, has_permission
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
    ("/users", "users"),
    ("/groups", "groups"),
    ("/roles", "roles"),
    ("/ideas", "ideas"),
]

PUBLIC_PATHS = ("/auth/login", "/auth/refresh", "/auth/sso", "/health", "/etl/worker-health")
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


# Endpoints de consulta de dados que usam POST mas são LEITURA (exceção
# documentada ao mapeamento HTTP→ação: POST→create). Valem para leitores.
READ_POST_PREFIXES: list[tuple[str, str]] = [
    ("/visoes-consumo-ia/dashboard/", "azure_ptu"),
    ("/visoes-consumo-ia/provider/", "ai_provider_dashboards"),
    ("/ai-copilot/", "ai_copilot"),
    ("/ai-dashboards/", "ai_dashboards"),
    ("/dimon/query", "dimon"),
    ("/dimon/info", "dimon"),
    ("/analysis/query", "analysis"),
    ("/analysis/pivot", "analysis"),
    ("/analysis/expected-view", "analysis"),
]
READ_POST_WRITE_MARKERS = ("/reprocess", "/exec", "/cancel", "/enqueue", "/run")
# POST que altera estado (não é create)
UPDATE_POST_MARKERS = ("/transition",)
# DELETE que não exige delete no módulo (ex.: remover voto)
CREATE_DELETE_MARKERS = ("/vote",)

# Áreas de "aba" escopadas dentro de um módulo (kind prefix|contains).
# kind=prefix casa path.startswith(needle); kind=contains casa needle em path.
# Regras de consulta (query POST) redefinem a ação para "read" — apenas nessas
# áreas exatas (não afetam cancel/execuções sob o mesmo prefixo).
SCOPED_PATH_RULES: list[tuple[str, str, str, str, bool]] = [
    # (kind, needle, module, scope, post_é_leitura)
    ("prefix", "/forecast/accuracy/months", "forecast", "visao-contabil", True),
    ("contains", "/history/", "forecast", "visao-contabil", True),
    ("prefix", "/dimon/query", "dimon", "visao-contabil", True),
    ("prefix", "/dimon/info", "dimon", "visao-contabil", True),
]


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

        if scope.get("method") == "POST" and any(marker in path for marker in UPDATE_POST_MARKERS):
            action = "update"
        elif scope.get("method") == "DELETE" and any(marker in path for marker in CREATE_DELETE_MARKERS):
            action = "create"

        # Consultas por POST são leitura (documentado)
        if scope.get("method") in ("POST", "PUT", "PATCH"):
            for qprefix, qmodule in READ_POST_PREFIXES:
                if path.startswith(qprefix) and module == qmodule:
                    if not any(marker in path for marker in READ_POST_WRITE_MARKERS):
                        action = "read"
                    break

        # Regra escopada (aba) tem precedência e pode redefinir a ação
        rbac_scope: str | None = None
        method_action = METHOD_ACTION.get(scope.get("method", ""))
        for kind, needle, rule_module, rule_scope, post_is_read in SCOPED_PATH_RULES:
            matched = path.startswith(needle) if kind == "prefix" else needle in path
            if not matched:
                continue
            module = rule_module
            rbac_scope = rule_scope
            if post_is_read and scope.get("method") in ("POST", "PUT", "PATCH"):
                action = "read"
            elif scope.get("method") == "GET":
                action = "read"
            else:
                action = method_action or "read"
            break

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
            if not has_permission(perms, module, action, rbac_scope):
                await _respond(send, 403, "Sem permissão para esta ação.")
                return

        await self.app(scope, receive, send)
