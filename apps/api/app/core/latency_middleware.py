"""Latência medida por rota, para o aceite de performance ter número e não impressão.

Middleware ASGI puro (sem BaseHTTPMiddleware) que registra em
``finops_cache.record_request_metric`` uma amostra por request, chaveada pelo
template da rota (``POST /dados/explorador/dinamica``), não pelo caminho com
parâmetros — assim ids diferentes caem na mesma série. Requests sem rota
resolvida (404 de caminho desconhecido) não entram, para não poluir o relatório.
"""

from __future__ import annotations

import time

from app.services.finops_cache import record_request_metric


class LatencyMetricsMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = {"code": 500}

        async def send_with_status(message) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None)
            if template:
                record_request_metric(
                    f"{scope.get('method', 'GET')} {template}",
                    (time.perf_counter() - started) * 1000,
                    error=status["code"] >= 500,
                )
