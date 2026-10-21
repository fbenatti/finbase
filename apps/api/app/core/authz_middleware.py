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
    ("/modules", "modules"),
    ("/integrations", "connections"),
    ("/etl/periods", "periods"),
    ("/connections", "connections"),
    ("/csv-upload", "csv_upload"),
    ("/etl", "etl"),
    ("/mappings", "rules"),
    ("/business-rules", "rules"),
    ("/classifications", "rules"),
    ("/departments", "rules"),
    ("/analysis", "analysis"),
    ("/visoes-consumo-ia", "azure_ptu"),
    ("/destinations", "destinations"),
    ("/schema", "schema"),
    ("/forecast", "forecast"),
    ("/clone-rules", "clone_rules"),
    ("/planning", "planning"),
    ("/savings", "savings"),
    ("/azure-tag-policy", "azure_tag_policy"),
    ("/gold", "gold"),
    ("/azure-resource-graph", "azure_resource_graph"),
    ("/reservations", "reservations"),
    ("/scheduled-shutdown", "scheduled_shutdown"),
    ("/oci-operational", "oci_operational"),
    ("/fact", "fact"),
    ("/users", "users"),
    ("/groups", "groups"),
    ("/roles", "roles"),
    ("/ideas", "ideas"),
    ("/anomalies", "anomalies"),
]

PUBLIC_PATHS = ("/auth/login", "/auth/refresh", "/auth/sso", "/health", "/etl/worker-health")
# já protegidos por Depends(require(...)) nos próprios routers
SKIP_PREFIXES = ("/modules", "/navigation", "/auth", "/users", "/groups", "/roles", "/planning/budget-defense")

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
    ("/gold/query", "gold"),
    ("/gold/info", "gold"),
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
# escopo_vale_para_leitura=False mantém a leitura no escopo do módulo: quem pode
# solicitar ou publicar uma defesa precisa ler a grade, e a leitura não deve ser
# restringida à área da permissão de escrita.
SCOPED_PATH_RULES: list[tuple[str, str, str, str, bool, bool]] = [
    # (kind, needle, module, scope, post_é_leitura, escopo_vale_para_leitura)
    ("prefix", "/forecast/accuracy/months", "forecast", "visao-contabil", True, True),
    ("contains", "/history/", "forecast", "visao-contabil", True, True),
    ("prefix", "/gold/query", "gold", "visao-contabil", True, True),
    ("prefix", "/gold/info", "gold", "visao-contabil", True, True),
    # Publicar a defesa no orçamento é a permissão de merge/aprovação.
    ("contains", "/publish-budget", "planning", "defesa-publicar", False, False),
    # Salvar/solicitar a defesa exige a permissão de solicitação.
    ("prefix", "/planning/defesa-orcamentaria", "planning", "defesa-solicitar", False, False),
]


def _module_for(path: str) -> str | None:
    for prefix, module in PATH_MODULE:
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


# Routers que a autorização precisa cobrir, mas que a auditoria ainda nomeia pelo
# primeiro segmento (ver audit_module_for). Por isso ficam fora do PATH_MODULE.
AUTHZ_EXTRA_MODULES: list[tuple[str, str]] = [
    ("/financial-contracts", "rules"),
    ("/azure-sources", "connections"),
    ("/mca-commitment", "reservations"),
    ("/processing-logs", "etl"),
    ("/connector-visibility", "connections"),
    ("/audit", "audit"),
    ("/sso/providers", "auth"),
    ("/access", "users"),
]


def _authz_module_for(path: str) -> str | None:
    """Módulo de autorização: PATH_MODULE primeiro, depois AUTHZ_EXTRA_MODULES."""
    module = _module_for(path)
    if module is not None:
        return module
    for prefix, extra_module in AUTHZ_EXTRA_MODULES:
        if path == prefix or path.startswith(prefix + "/"):
            return extra_module
    return None


async def _audit_denied(scope: Scope, status: int, user) -> None:
    # Import tardio: audit_middleware importa deste módulo (evita ciclo).
    from app.core.audit_middleware import record_denied_attempt
    await record_denied_attempt(scope, status, user)


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

        module = _authz_module_for(path)
        # Rota sem módulo RBAC: fecha por padrão. Exige token e depois nega.
        unmapped = module is None

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
        for kind, needle, rule_module, rule_scope, post_is_read, scope_on_read in SCOPED_PATH_RULES:
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
            if action == "read" and not scope_on_read:
                rbac_scope = None
            break

        headers = dict(scope.get("headers") or [])
        # The navigation policy validates this code against the authenticated
        # customer; only a matching module can narrow an otherwise unscoped rule.
        menu_code = headers.get(b"x-finbase-module", b"").decode("latin-1")
        if menu_code and rbac_scope is None:
            from app.services.navigation import CATALOG
            menu = next((entry for entry in CATALOG if entry["code"] == menu_code), None)
            if menu and menu["permission_module"] == module:
                rbac_scope = menu_code
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
                await _audit_denied(scope, 401, None)
                await _respond(send, 401, "Não autenticado")
                return

            perms = await get_effective_permissions(db, user.id)
            if unmapped or not has_permission(perms, module, action, rbac_scope):
                await _audit_denied(scope, 403, user)
                await _respond(send, 403, "Sem permissão para esta ação.")
                return

        await self.app(scope, receive, send)
