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
from app.core.access.engine import resolve_screen_access
from app.database import async_session
from sqlalchemy import select

from app.models.identity import User

# maior prefixo primeiro; (prefixo, módulo)
PATH_MODULE: list[tuple[str, str]] = [
    ("/administracao/modulos", "modules"),
    ("/dados/integracoes", "connections"),
    ("/operacoes/periodos", "periods"),
    ("/dados/fontes", "connections"),
    ("/dados/upload-csv", "csv_upload"),
    ("/operacoes", "etl"),
    ("/dados/mapeamentos", "rules"),
    ("/dados/regras-negocio", "rules"),
    ("/dados/classificacoes", "rules"),
    ("/governanca/hierarquia", "rules"),
    ("/dados/explorador", "analysis"),
    ("/dados/catalogo", "destinations"),
    ("/dados/esquema", "schema"),
    ("/projecoes", "forecast"),
    ("/dados/regras-clonagem", "clone_rules"),
    ("/planejamento", "planning"),
    ("/governanca/politica-tags-azure", "azure_tag_policy"),
    ("/governanca/grafo-recursos-azure", "azure_resource_graph"),
    ("/custos/reservas", "reservations"),
    ("/custos/compromisso-mca", "reservations"),
    ("/otimizacao", "savings"),
    ("/custos/oci-operacional", "oci_operational"),
    ("/custos/metricas-infra", "infrastructure_metrics"),
    ("/custos", "gold"),
    ("/dados/fato", "fact"),
    ("/administracao/usuarios", "users"),
    ("/administracao/grupos", "groups"),
    ("/administracao/perfis", "roles"),
    ("/iniciativas", "ideas"),
    ("/alertas", "anomalies"),
]

PUBLIC_PATHS = ("/autenticacao/entrar", "/autenticacao/atualizar", "/autenticacao/sso", "/saude", "/operacoes/execucoes/saude-executor")
# documentação da API: sem dados, sem permissão de módulo
DOC_PATHS = ("/docs", "/openapi.json", "/redoc")
# já protegidos por Depends(require(...)) nos próprios routers
SKIP_PREFIXES = ("/administracao/modulos", "/navegacao", "/autenticacao", "/administracao/usuarios", "/administracao/grupos", "/administracao/perfis", "/planejamento/defesa-orcamentaria")

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
    ("/custos/consulta", "gold"),
    ("/custos/informacoes", "gold"),
    ("/custos/metricas-infra/planejar", "infrastructure_metrics"),
    ("/dados/explorador/consulta", "analysis"),
    ("/dados/explorador/dinamica", "analysis"),
    ("/dados/explorador/visao-esperada", "analysis"),
]
READ_POST_WRITE_MARKERS = ("/reprocessar", "/executar", "/cancelar")
# POST que altera estado (não é create)
UPDATE_POST_MARKERS = ("/transicao",)
# DELETE que não exige delete no módulo (ex.: remover voto)
CREATE_DELETE_MARKERS = ("/votar",)

# Áreas de "aba" escopadas dentro de um módulo (kind prefix|contains).
# kind=prefix casa path.startswith(needle); kind=contains casa needle em path.
# Regras de consulta (query POST) redefinem a ação para "read" — apenas nessas
# áreas exatas (não afetam cancel/execuções sob o mesmo prefixo).
# escopo_vale_para_leitura=False mantém a leitura no escopo do módulo: quem pode
# solicitar ou publicar uma defesa precisa ler a grade, e a leitura não deve ser
# restringida à área da permissão de escrita.
SCOPED_PATH_RULES: list[tuple[str, str, str, str, bool, bool]] = [
    # (kind, needle, module, scope, post_é_leitura, escopo_vale_para_leitura)
    ("prefix", "/projecoes/aderencia/meses", "forecast", "visao-contabil", True, True),
    ("contains", "/historico/", "forecast", "visao-contabil", True, True),
    ("prefix", "/custos/consulta", "gold", "visao-contabil", True, True),
    ("prefix", "/custos/informacoes", "gold", "visao-contabil", True, True),
    # Publicar a defesa no orçamento é a permissão de merge/aprovação.
    ("contains", "/publicar-orcamento", "planning", "defesa-publicar", False, False),
    # Salvar/solicitar a defesa exige a permissão de solicitação.
    ("prefix", "/planejamento/defesa-orcamentaria", "planning", "defesa-solicitar", False, False),
]


def _module_for(path: str) -> str | None:
    for prefix, module in PATH_MODULE:
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


# Consultas de tarefas que o painel de avisos faz. Pertencem ao módulo etl, mas
# também valem para quem só tem leitura dos avisos do sistema.
NOTIFICATION_READ_PREFIXES = ("/operacoes/execucoes/tarefas", "/operacoes/execucoes/situacao")


# Routers que a autorização precisa cobrir, mas que a auditoria ainda nomeia pelo
# primeiro segmento (ver audit_module_for). Por isso ficam fora do PATH_MODULE.
AUTHZ_EXTRA_MODULES: list[tuple[str, str]] = [
    ("/governanca/contratos-financeiros", "rules"),
    ("/dados/fontes-azure", "connections"),
    ("/operacoes/logs-processamento", "etl"),
    ("/dados/fontes/visibilidade", "connections"),
    ("/administracao/auditoria", "audit"),
    ("/administracao/identidade/provedores", "auth"),
    ("/administracao/acessos", "users"),
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
        if module is None and path.startswith(DOC_PATHS):
            await self.app(scope, receive, send)
            return
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
            allowed = not unmapped and has_permission(perms, module, action, rbac_scope)
            if not unmapped:
                allowed = await resolve_screen_access(db, user.id, module, action, rbac_scope, legacy_allowed=allowed)
            if not allowed and action == "read" and path.startswith(NOTIFICATION_READ_PREFIXES):
                allowed = has_permission(perms, "notifications", "read")
            if not allowed:
                await _audit_denied(scope, 403, user)
                await _respond(send, 403, "Sem permissão para esta ação.")
                return

        await self.app(scope, receive, send)
