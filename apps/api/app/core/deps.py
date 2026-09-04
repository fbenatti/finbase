"""FastAPI dependencies: current user resolution with DEV bypass."""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import security
from app.database import get_db
from app.models.identity import User

bearer_scheme = HTTPBearer(auto_error=False)


def _dev_bypass_active() -> bool:
    return not settings.auth_enabled and settings.env != "production"


def _master_token_active() -> bool:
    return bool(settings.dev_master_token) and settings.env != "production"


async def _get_dev_admin(db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.username == "dev-admin"))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=500, detail="Usuário dev-admin não existe (seed não executado)")
    return user


async def resolve_user_from_request(request: Request, db: AsyncSession) -> User:
    """Resolve o usuário a partir do header Authorization (sem DEV bypass implícito aqui)."""
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado")
    try:
        payload = security.decode_access_token(token)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário inválido ou inativo")
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Bypass de DEV: auth desligada fora de produção → dev-admin
    if _dev_bypass_active():
        return await _get_dev_admin(db)

    # Token master DEV (Bearer estático fora de produção)
    if _master_token_active() and credentials is not None:
        if security.tokens_equal(credentials.credentials, settings.dev_master_token):
            return await _get_dev_admin(db)

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = security.decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário inválido ou inativo")
    return user


def require(module: str, action: str, scope: str | None = None):
    """Dependency factory: exige permissão (module, action[, scope]) no RBAC."""
    from app.core.rbac import get_effective_permissions, has_permission

    async def checker(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        perms = await get_effective_permissions(db, user.id)
        if not has_permission(perms, module, action, scope):
            raise HTTPException(status_code=403, detail="Sem permissão para esta ação.")
        return user

    return checker
