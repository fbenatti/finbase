"""Authentication endpoints: login, refresh, logout, change-password, me."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import security
from app.core.deps import get_current_user
from app.core.perms import compute_effective_perms, perms_to_json
from app.core.audit import audit
from app.database import get_db
from app.models.identity import RefreshToken, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
_COOKIE_PATH = "/"  # "/" para funcionar também atrás do proxy /api do Vite em dev


class LoginBody(BaseModel):
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        path=_COOKIE_PATH,
        secure=settings.env == "production",
    )


async def _issue_tokens(response: Response, db: AsyncSession, user: User, request: Request) -> str:
    perms = await compute_effective_perms(db, user)
    access_token = security.create_access_token(user.id, user.username, perms_to_json(perms))

    refresh_token = security.generate_refresh_token()
    now = datetime.now(timezone.utc)
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=security.hash_refresh_token(refresh_token),
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        ip=request.client.host if request.client else None,
    ))
    await db.commit()
    _set_refresh_cookie(response, refresh_token)
    return access_token


def _validate_password_policy(password: str) -> None:
    if not security.validate_password_policy(password):
        raise HTTPException(status_code=422, detail=security.PASSWORD_POLICY_ERROR)


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username.lower().strip()))
    user = result.scalar_one_or_none()
    if user is None or not security.verify_password(body.password, user.password_hash):
        await audit(db, request, None, module="auth", method="LOGIN_FAILED", action_label="failed", status_code=401)
        await db.commit()
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    if not user.is_active:
        await audit(db, request, None, module="auth", method="LOGIN_FAILED", action_label="failed", status_code=401)
        await db.commit()
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    access_token = await _issue_tokens(response, db, user, request)
    await audit(db, request, user, module="auth", method="LOGIN", action_label="login",
                after={"username": user.username}, status_code=200)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "must_change_password": user.must_change_password,
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Refresh token ausente")
    token_hash = security.hash_refresh_token(token)
    now = datetime.now(timezone.utc)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=401, detail="Refresh token inválido")
    if row.revoked_at is not None:
        # Reutilização de refresh revogado → revogar toda a família do usuário
        logger.warning("Refresh revogado reutilizado — revogando família do usuário %s", row.user_id)
        result2 = await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == row.user_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        for sibling in result2.scalars().all():
            sibling.revoked_at = now
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token revogado")
    if row.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(status_code=401, detail="Refresh token expirado")

    user_result = await db.execute(select(User).where(User.id == row.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuário inválido ou inativo")

    # Rotação: revoga o antigo e emite novo
    new_refresh = security.generate_refresh_token()
    new_row = RefreshToken(
        user_id=user.id,
        token_hash=security.hash_refresh_token(new_refresh),
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        ip=request.client.host if request.client else None,
    )
    db.add(new_row)
    await db.flush()
    row.revoked_at = now
    row.replaced_by_id = new_row.id
    await db.commit()

    perms = await compute_effective_perms(db, user)
    access_token = security.create_access_token(user.id, user.username, perms_to_json(perms))
    _set_refresh_cookie(response, new_refresh)
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    user = None
    if token:
        token_hash = security.hash_refresh_token(token)
        result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        row = result.scalar_one_or_none()
        if row is not None:
            if row.revoked_at is None:
                row.revoked_at = datetime.now(timezone.utc)
            ures = await db.execute(select(User).where(User.id == row.user_id))
            user = ures.scalar_one_or_none()
            await audit(db, request, user, module="auth", method="LOGOUT", action_label="logout",
                        after={"username": user.username if user else None}, status_code=200)
            await db.commit()
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=_COOKIE_PATH)
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not security.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Senha atual incorreta")
    _validate_password_policy(body.new_password)
    user.password_hash = security.hash_password(body.new_password)
    user.must_change_password = False
    # Encerra sessões: revoga todos os refresh tokens do usuário
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    now = datetime.now(timezone.utc)
    for row in result.scalars().all():
        row.revoked_at = now
    await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    perms = await compute_effective_perms(db, user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "auth_provider": user.auth_provider,
        "must_change_password": user.must_change_password,
        "permissions": perms_to_json(perms),
    }
