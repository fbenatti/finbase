"""Cache e métricas dos endpoints do Centro de Decisão FinOps.

O cache é por **cubo, snapshot do manifesto e conjunto de filtros**. Por que o
snapshot entra na chave: o cubo é materializado por swap, e o snapshot identifica a
versão publicada. Cachear só por filtros serviria dado antigo depois de uma
publicação nova — que é exatamente o caso em que o usuário mais precisa ver o número
novo.

A invalidação é por publicação: `gold_governance` chama `invalidate_after_publish`
ao terminar uma publicação bem-sucedida, e todas as entradas daquele cubo caem. Não
existe invalidação por tempo como mecanismo principal — o TTL é rede de segurança
para o caso de uma publicação não notificar.

Também guarda as amostras de latência que a auditoria pediu (p50 e p95 **medidos**,
não declarados). As amostras são em processo e de janela curta: o objetivo é dar
base ao aceite de performance no ambiente de referência, não ser observabilidade
histórica.
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: TTL do cache. Rede de segurança: o caminho normal de invalidação é a publicação.
DEFAULT_TTL_SECONDS = 300.0

#: Amostras de latência mantidas por endpoint. Janela curta e limitada: memória
#: constante, e a leitura é a distribuição recente.
MAX_SAMPLES_PER_ENDPOINT = 200

_lock = threading.Lock()
_cache: dict[str, tuple[float, str, Any]] = {}
_metrics: dict[str, list[float]] = {}


@dataclass(frozen=True)
class CacheInfo:
    """Estatística de acerto/erro por endpoint."""

    hits: int = 0
    misses: int = 0
    entries: int = 0
    invalidations: int = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return (self.hits / total) if total else None


@dataclass
class _Stats:
    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    samples_ms: list[float] = field(default_factory=list)
    errors: int = 0


_stats: dict[str, _Stats] = {}


def _stats_for(endpoint: str) -> _Stats:
    return _stats.setdefault(endpoint, _Stats())


def snapshot_key(cube: str, filters: dict[str, Any], *, version: str | None = None) -> str:
    """Chave de cache: cubo + versão publicada + filtros normalizados.

    Os filtros são ordenados e serializados de forma estável, então dois pedidos com
    os mesmos pares em ordem diferente compartilham a entrada.
    """
    import json

    pares = sorted((str(k), str(v)) for k, v in (filters or {}).items() if v not in (None, ""))
    return json.dumps(
        {"cube": cube, "version": version or "unversioned", "filters": pares},
        sort_keys=True,
        ensure_ascii=False,
    )


def cache_key(cube: str, filters: dict[str, Any], *, version: str | None = None) -> str:
    """Alias público de :func:`snapshot_key`."""
    return snapshot_key(cube, filters, version=version)


def get_cached(key: str, *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> Any | None:
    """Valor em cache, ou ``None``.

    Expirado pela TTL é tratado como ausente e removido — nunca devolvido.
    """
    agora = time.monotonic()
    with _lock:
        entrada = _cache.get(key)
        if entrada is None:
            return None
        gravado_em, _, valor = entrada
        if (agora - gravado_em) > ttl_seconds:
            _cache.pop(key, None)
            return None
        return valor


def set_cached(key: str, value: Any, *, version: str | None = None) -> None:
    """Guarda um valor no cache, com a versão do cubo que o produziu."""
    with _lock:
        _cache[key] = (time.monotonic(), version or "unversioned", value)


def invalidate_after_publish(cube_code: str | None = None) -> int:
    """Invalida o cache de um cubo (ou tudo) após uma publicação bem-sucedida.

    A entrada guarda a versão do cubo; a invalidação por `cube_code` remove as
    entradas cujo valor foi produzido por ele. Sem `cube_code`, limpa tudo — usado
    quando a publicação não informa o cubo.
    """
    with _lock:
        if cube_code is None:
            removidas = len(_cache)
            _cache.clear()
        else:
            alvo = str(cube_code)
            chaves = [k for k in _cache if f'"{alvo}"' in k]
            for chave in chaves:
                _cache.pop(chave, None)
            removidas = len(chaves)
        for stats in _stats.values():
            stats.invalidations += removidas
    return removidas


def record_request_metric(endpoint: str, elapsed_ms: float, *, error: bool = False) -> None:
    """Registra uma amostra de latência do endpoint."""
    with _lock:
        stats = _stats_for(endpoint)
        stats.samples_ms.append(float(elapsed_ms))
        if len(stats.samples_ms) > MAX_SAMPLES_PER_ENDPOINT:
            del stats.samples_ms[: len(stats.samples_ms) - MAX_SAMPLES_PER_ENDPOINT]
        if error:
            stats.errors += 1


def record_cache_hit(endpoint: str, hit: bool) -> None:
    """Contabiliza acerto ou erro de cache."""
    with _lock:
        stats = _stats_for(endpoint)
        if hit:
            stats.hits += 1
        else:
            stats.misses += 1


def _percentile(samples: list[float], fraction: float) -> float | None:
    """Percentil por interpolação linear sobre a amostra ordenada."""
    if not samples:
        return None
    ordenadas = sorted(samples)
    if len(ordenadas) == 1:
        return ordenadas[0]
    posicao = fraction * (len(ordenadas) - 1)
    base = int(posicao)
    resto = posicao - base
    if base + 1 >= len(ordenadas):
        return ordenadas[-1]
    return ordenadas[base] + (ordenadas[base + 1] - ordenadas[base]) * resto


def metrics_report() -> dict[str, Any]:
    """Latência (p50/p95), contagem e erros por endpoint."""
    with _lock:
        relatorio: dict[str, Any] = {}
        for endpoint, stats in sorted(_stats.items()):
            amostras = list(stats.samples_ms)
            relatorio[endpoint] = {
                "samples": len(amostras),
                "p50_ms": (
                    round(_percentile(amostras, 0.50), 1) if amostras else None
                ),
                "p95_ms": (
                    round(_percentile(amostras, 0.95), 1) if amostras else None
                ),
                "min_ms": round(min(amostras), 1) if amostras else None,
                "max_ms": round(max(amostras), 1) if amostras else None,
                "mean_ms": (
                    round(statistics.fmean(amostras), 1) if amostras else None
                ),
                "errors": stats.errors,
                "cache_hits": stats.hits,
                "cache_misses": stats.misses,
                "invalidations": stats.invalidations,
            }
        return {
            "endpoints": relatorio,
            "cache_entries": len(_cache),
            "ttl_seconds": DEFAULT_TTL_SECONDS,
        }


def reset_state() -> None:
    """Limpa cache e métricas. Usado por teste e por operação de diagnóstico."""
    with _lock:
        _cache.clear()
        _stats.clear()
