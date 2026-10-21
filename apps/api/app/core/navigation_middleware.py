"""Customer feature enforcement, in addition to the existing API RBAC policy."""
from fastapi import HTTPException
from starlette.requests import Request
from app.core.deps import resolve_user_from_request
from app.core.authz_middleware import _respond
from app.database import async_session
from app.services.navigation import CATALOG, require_module_access


class NavigationPolicyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith(("/auth", "/navigation", "/modules", "/health")):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        codes = {headers.get(b"x-finbase-module", b"").decode("latin-1")}
        # Dedicated resources cannot be accessed by omitting or forging the UI header.
        codes.update(item["code"] for item in CATALOG for prefix in item["api_prefixes"] if path == prefix or path.startswith(prefix + "/"))
        codes.discard("")
        if codes:
            try:
                async with async_session() as db:
                    user = await resolve_user_from_request(Request(scope), db)
                    for code in codes:
                        await require_module_access(db, user.id, code)
            except HTTPException as exc:
                return await _respond(send, exc.status_code, str(exc.detail))
        await self.app(scope, receive, send)
