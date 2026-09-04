"""Helper de gravação de eventos de auditoria.

Falha de auditoria NÃO derruba a operação de negócio: exceção é capturada,
logada e a execução segue.
"""
import logging
import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_sanitize import compute_diff, sanitize_dict, snapshot_from_dict
from app.models.audit_log import AuditLog
from app.models.identity import User

logger = logging.getLogger(__name__)


def request_id_of(request: Request | None) -> str | None:
    if request is None:
        return None
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = str(uuid.uuid4())
        request.state.request_id = rid
    return rid


async def audit(
    db: AsyncSession,
    request: Request | None,
    user: User | None,
    *,
    module: str,
    method: str,
    action_label: str,
    entity_type: str | None = None,
    entity_id=None,
    entity_label: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    status_code: int | None = None,
) -> None:
    try:
        before_s = snapshot_from_dict(before)
        after_s = snapshot_from_dict(after)
        changes = compute_diff(before_s, after_s) if action_label in ("update",) else None
        ip = None
        user_agent = None
        path = None
        if request is not None:
            path = request.url.path
            user_agent = (request.headers.get("user-agent") or "")[:500] or None
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                ip = fwd.split(",")[0].strip()
            elif request.client:
                ip = request.client.host
        db.add(AuditLog(
            user_id=user.id if user else None,
            username=user.username if user else None,
            auth_provider=user.auth_provider if user else None,
            module=module,
            method=method,
            path=path,
            status_code=status_code,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            entity_label=(entity_label or "")[:300] or None,
            action_label=action_label,
            before_json=before_s,
            after_json=after_s,
            changes_json=changes,
            ip=ip,
            user_agent=user_agent,
            request_id=request_id_of(request),
        ))
        await db.flush()
        if request is not None:
            request.state.audit_done = True
    except Exception:
        logger.exception("Falha ao gravar auditoria (module=%s action=%s)", module, action_label)
