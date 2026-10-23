"""Middleware de captura genérica de auditoria.

Para todo POST/PUT/PATCH/DELETE em rota da API, grava um evento AuditLog
genérico. Endpoints instrumentados com app.core.audit.audit() marcam
request.state.audit_done e o middleware pula (o evento rico já foi gravado
na mesma transação).

Antes/depois: em PUT/PATCH/DELETE com id numérico no fim da rota e modelo
registrado em AUDIT_MODELS, o estado anterior é lido ANTES da rota executar
e o posterior é relido DEPOIS. O diff sai do banco, não só do body.

Falhas (status >= 400) também são gravadas, com action_label="failed".
Negações do RBAC (401/403) chegam por record_denied_attempt(), porque o
RBACMiddleware responde antes de este middleware ser alcançado.

Order de middleware (main.py): AuditCaptureMiddleware é adicionado ANTES de
RBACMiddleware/CORSMiddleware para executar por último (dentro deles).
"""
import json
import logging
import uuid

from sqlalchemy import select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.core import security
from app.core.audit_sanitize import compute_diff, sanitize_dict, snapshot_from_dict, to_snapshot
from app.core.authz_middleware import METHOD_ACTION, _module_for
from app.database import async_session
from app.models.anomaly import AnomalyAction, AnomalyConfig
from app.models.audit_log import AuditLog
from app.models.azure_tag_mapping import AzureTagMapping
from app.models.clone_rule import CloneRule
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.destination import DestinationColumn, DestinationTable, SourceDestinationMapping
from app.models.financial_contract import FinancialContract
from app.models.forecast import ForecastAdjustment, ForecastBusinessRule, ForecastConfig
from app.models.gold_mart_rule import GoldMartRule
from app.models.identity import Group, Role, SsoProvider, User
from app.models.hierarchy_alias import HierarchyLevelAlias, HierarchyRole
from app.models.idea import Idea
from app.models.pivot_view import PivotView
from app.models.rules import BusinessRule, ClassificationRule, Department, EtlMapping
from app.models.saving import SavingAction
from app.models.shutdown_exception_rule import ShutdownExceptionRule
from app.models.source_connection import SourceConnection

logger = logging.getLogger(__name__)

MAX_BODY = 200_000
MUTATING = ("POST", "PUT", "PATCH", "DELETE")

# Chave: "primeiro_segmento/pai" (rotas aninhadas) ou só o pai (recurso de topo).
# O pai é o segmento logo antes do id. Ex.: /forecast/configs/5 → "forecast/configs".
AUDIT_MODELS: dict[str, type] = {
    "administracao/usuarios": User,
    "administracao/perfis": Role,
    "administracao/grupos": Group,
    "fontes": SourceConnection,
    "regras-negocio": BusinessRule,
    "iniciativas": Idea,
    "contratos-financeiros": FinancialContract,
    "regras-clonagem": CloneRule,
    "hierarquia": Department,
    "papeis": HierarchyRole,
    "aliases-niveis": HierarchyLevelAlias,
    "mapeamentos": EtlMapping,
    "classificacoes": ClassificationRule,
    "administracao/provedores": SsoProvider,
    "projecoes/configuracoes": ForecastConfig,
    "projecoes/ajustes": ForecastAdjustment,
    "projecoes/regras-negocio": ForecastBusinessRule,
    "alertas/configuracoes": AnomalyConfig,
    "alertas/acoes": AnomalyAction,
    "otimizacao/acoes": SavingAction,
    "otimizacao/excecoes": ShutdownExceptionRule,
    "mapeamentos-tags": AzureTagMapping,
    "tabelas": DestinationTable,
    "colunas": DestinationColumn,
    "vinculos": SourceDestinationMapping,
    "custos/regras": GoldMartRule,
    "visoes": PivotView,
    "paineis": Dashboard,
    "componentes": DashboardWidget,
}


AREA_ROOTS = frozenset({"dados", "custos", "governanca", "operacoes", "alertas", "projecoes", "planejamento", "administracao", "autenticacao", "otimizacao", "iniciativas"})


def audit_module_for(path: str) -> str | None:
    """Módulo de auditoria da rota. Prefixos fora do PATH_MODULE usam o 1º segmento.

    /audit é excluído para não auditar a própria consulta de auditoria.
    """
    if path == "/administracao/auditoria" or path.startswith("/administracao/auditoria/"):
        return None
    module = _module_for(path)
    if module is not None:
        return module
    segments = [s for s in path.split("/") if s]
    if len(segments) > 1 and segments[0] in AREA_ROOTS:
        return segments[1]
    return segments[0] if segments else None


def _model_for(segments: list[str]) -> type | None:
    if len(segments) < 2:
        return None
    first, parent = segments[0], segments[-2]
    return AUDIT_MODELS.get(f"{first}/{parent}") or AUDIT_MODELS.get(parent)


def _client_ip(headers: dict[bytes, bytes], scope: Scope) -> str | None:
    fwd = headers.get(b"x-forwarded-for")
    if fwd:
        return fwd.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else None


async def _resolve_user(headers: dict[bytes, bytes]) -> User | None:
    auth = headers.get(b"authorization", b"").decode("latin-1")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    async with async_session() as db:
        try:
            payload = security.decode_access_token(token)
            result = await db.execute(select(User).where(User.id == int(payload["sub"])))
            user = result.scalar_one_or_none()
            return user if user is not None and user.is_active else None
        except Exception:
            return None


async def _load_snapshot(segments: list[str], entity_id: str | None) -> dict | None:
    """Estado atual da entidade no banco (sanitizado), ou None se não há modelo/registro."""
    model = _model_for(segments)
    if model is None or entity_id is None:
        return None
    try:
        async with async_session() as db:
            instance = await db.get(model, int(entity_id))
            if instance is None:
                return None
            return to_snapshot(instance)
    except Exception:
        logger.exception("Falha ao ler snapshot de auditoria (path=%s)", "/".join(segments))
        return None


def _body_snapshot(headers: dict[bytes, bytes], body: bytes) -> dict | None:
    ctype = headers.get(b"content-type", b"").decode("latin-1")
    if "application/json" not in ctype or not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
        return snapshot_from_dict(parsed) if isinstance(parsed, dict) else sanitize_dict(parsed)
    except Exception:
        return {"_unparsed_body": body[:500].decode("utf-8", "replace")}


def _label_of(snapshot: dict | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    label = snapshot.get("name") or snapshot.get("code")
    return str(label)[:300] if label else None


async def _write_row(
    scope: Scope,
    *,
    user: User | None,
    module: str,
    status: int,
    action_label: str,
    entity_type: str | None,
    entity_id: str | None,
    before: dict | None,
    after: dict | None,
    changes: list | None,
    request_id: str,
) -> None:
    headers = dict(scope.get("headers") or [])
    try:
        async with async_session() as db:
            db.add(AuditLog(
                user_id=user.id if user else None,
                username=user.username if user else None,
                auth_provider=user.auth_provider if user else None,
                module=module,
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                status_code=status,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_label=_label_of(after) or _label_of(before),
                action_label=action_label,
                before_json=before,
                after_json=after,
                changes_json=changes,
                ip=_client_ip(headers, scope),
                user_agent=(headers.get(b"user-agent", b"").decode("latin-1") or None)[:500] if headers.get(b"user-agent") else None,
                request_id=request_id,
            ))
            await db.commit()
    except Exception:
        logger.exception("Falha ao gravar auditoria (path=%s status=%s)", scope.get("path"), status)


async def record_denied_attempt(scope: Scope, status: int, user: User | None = None) -> None:
    """Tentativa de mutação negada pelo RBAC (401/403): grava como failed, sem executar a rota."""
    if scope.get("method", "") not in MUTATING:
        return
    path: str = scope.get("path", "")
    module = audit_module_for(path)
    if module is None:
        return
    segments = [s for s in path.split("/") if s]
    entity_id = segments[-1] if segments and segments[-1].isdigit() else None
    await _write_row(
        scope,
        user=user,
        module=module,
        status=status,
        action_label="failed",
        entity_type=segments[0] if segments else None,
        entity_id=entity_id,
        before=None,
        after=None,
        changes=None,
        request_id=str(uuid.uuid4()),
    )


class AuditCaptureMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path: str = scope.get("path", "")
        if method not in MUTATING:
            await self.app(scope, receive, send)
            return
        module = audit_module_for(path)
        if module is None:
            await self.app(scope, receive, send)
            return

        # request_id no state (compartilhado com endpoints via request.state)
        state = scope.setdefault("state", {})
        state.setdefault("request_id", str(uuid.uuid4()))

        segments = [s for s in path.split("/") if s]
        entity_id = segments[-1] if segments and segments[-1].isdigit() else None

        # estado anterior: lido antes de a rota alterar o registro
        before = None
        if method in ("PUT", "PATCH", "DELETE"):
            before = await _load_snapshot(segments, entity_id)

        # captura do body com replay
        body = b""
        received = False

        async def wrapped_receive() -> Message:
            nonlocal body, received
            message = await receive()
            if message["type"] == "http.request" and not received:
                chunk = message.get("body", b"")
                if len(body) < MAX_BODY:
                    body += chunk
                if not message.get("more_body"):
                    received = True
            return message

        status_holder = {"status": None}

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append((b"content-security-policy", b"frame-ancestors 'none'"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, wrapped_receive, wrapped_send)

        if scope.get("state", {}).get("audit_done"):
            return
        status = status_holder["status"]
        if status is None:
            return
        ok = 200 <= status < 300
        headers = dict(scope.get("headers") or [])

        # estado posterior: relido do banco; sem modelo cai no body enviado
        after = None
        if ok and method != "DELETE":
            after = await _load_snapshot(segments, entity_id) if entity_id else None
            if after is None:
                after = _body_snapshot(headers, body)

        changes = None
        if ok and before is not None and after is not None and method in ("PUT", "PATCH"):
            changes = compute_diff(before, after)

        user = await _resolve_user(headers)
        await _write_row(
            scope,
            user=user,
            module=module,
            status=status,
            action_label="failed" if not ok else METHOD_ACTION.get(method, "execute"),
            entity_type=segments[0] if segments else None,
            entity_id=entity_id,
            before=before,
            after=after,
            changes=changes,
            request_id=state.get("request_id"),
        )
