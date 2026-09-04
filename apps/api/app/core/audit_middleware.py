"""Middleware de captura genérica de auditoria.

Para todo POST/PUT/PATCH/DELETE em rota de módulo mapeado, grava um evento
AuditLog genérico (payload sanitizado como after_json, sem before). Endpoints
instrumentados com app.core.audit.audit() marcam request.state.audit_done e o
middleware pula (o evento rico já foi gravado na mesma transação).

Order de middleware (main.py): AuditCaptureMiddleware é adicionado ANTES de
RBACMiddleware/CORSMiddleware para executar por último (dentro deles).
"""
import json
import logging
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.core import security
from app.core.audit_sanitize import sanitize_dict, snapshot_from_dict
from app.core.authz_middleware import METHOD_ACTION, _module_for
from app.database import async_session
from app.models.audit_log import AuditLog
from app.models.identity import User
from sqlalchemy import select

logger = logging.getLogger(__name__)

MAX_BODY = 200_000


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


class AuditCaptureMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path: str = scope.get("path", "")
        if method not in ("POST", "PUT", "PATCH", "DELETE") or path.startswith("/auth"):
            await self.app(scope, receive, send)
            return
        module = _module_for(path)
        if module is None or path.startswith("/audit"):
            await self.app(scope, receive, send)
            return

        # request_id no state (compartilhado com endpoints via request.state)
        state = scope.setdefault("state", {})
        state.setdefault("request_id", str(uuid.uuid4()))

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
            await send(message)

        await self.app(scope, wrapped_receive, wrapped_send)

        status = status_holder["status"]
        if status is None or not (200 <= status < 300):
            return
        if scope.get("state", {}).get("audit_done"):
            return

        after = None
        ctype = dict(scope.get("headers") or {}).get(b"content-type", b"").decode("latin-1")
        if "application/json" in ctype and body:
            try:
                parsed = json.loads(body.decode("utf-8"))
                after = snapshot_from_dict(parsed) if isinstance(parsed, dict) else sanitize_dict(parsed)
            except Exception:
                after = {"_unparsed_body": body[:500].decode("utf-8", "replace")}

        headers = dict(scope.get("headers") or [])
        entity_id = None
        segments = [s for s in path.split("/") if s]
        if segments and segments[-1].isdigit():
            entity_id = segments[-1]

        try:
            user = await _resolve_user(headers)
            async with async_session() as db:
                db.add(AuditLog(
                    user_id=user.id if user else None,
                    username=user.username if user else None,
                    auth_provider=user.auth_provider if user else None,
                    module=module,
                    method=method,
                    path=path,
                    status_code=status,
                    entity_type=segments[0] if segments else None,
                    entity_id=entity_id,
                    entity_label=after.get("name") or after.get("code") if isinstance(after, dict) else None,
                    action_label=METHOD_ACTION.get(method, "execute"),
                    before_json=None,
                    after_json=after,
                    changes_json=None,
                    ip=_client_ip(headers, scope),
                    user_agent=(headers.get(b"user-agent", b"").decode("latin-1") or None)[:500] if headers.get(b"user-agent") else None,
                    request_id=state.get("request_id"),
                ))
                await db.commit()
        except Exception:
            logger.exception("Falha ao gravar auditoria genérica (path=%s)", path)
