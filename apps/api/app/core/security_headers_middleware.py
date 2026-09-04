"""Adiciona headers de segurança básicos a toda resposta HTTP."""
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(h for h in _HEADERS if not any(h[0] == k for k, _ in headers))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, wrapped_send)
