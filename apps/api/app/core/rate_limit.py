"""Rate limiting simples em memória (sem dependência externa).

Janela fixa por chave. Usado em /auth/login e /auth/refresh.
"""
import time
import threading

_lock = threading.Lock()
_hits: dict[tuple[str, str], list[float]] = {}


def rate_limit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Retorna (permitido, tentativas na janela)."""
    now = time.time()
    bucket = f"{key}:{int(now // window_seconds)}"
    with _lock:
        window_key = (key, str(int(now // window_seconds)))
        _hits.setdefault(window_key, [])
        entries = [t for t in _hits[window_key] if now - t < window_seconds]
        if len(entries) >= limit:
            _hits[window_key] = entries
            return False, len(entries)
        entries.append(now)
        _hits[window_key] = entries
        # limpeza oportunista
        if len(_hits) > 10_000:
            cutoff = now - 3600
            _hits.clear()
        return True, len(entries)
