"""Camadas do lakehouse e resolução única de namespace Iceberg.

Fonte de verdade da arquitetura Bronze → Silver → Gold:

- **Bronze**  — payload como coletado, imutável, `bronze.raw_{code_sanitizado}`.
- **Silver**  — detalhe tipado, normalizado e enriquecido: FOCUS detalhado,
  inventário, séries, topologia, preços, perfis, candidatos, decisões,
  planejamento/forecast detalhado e a ponte de consumo (namespace/workload).
- **Gold**    — somente cubos e materializações finais prontos para API,
  dashboard e exportação. Nunca recebe payload bruto nem série de 15 minutos.
- **Forecast** — snapshots imutáveis de execução do motor de forecast
  (`forecast.result_run_{id}`), que não são cubo de consumo.

Antes deste módulo cada chamada repetia a string do namespace (``"silver"``,
``"gold"``, ``"dimon"``). Foi exatamente assim que ``merge_planning_rateio``
passou a **ler** de ``gold`` enquanto a publicação **escrevia** em ``silver``,
apagando realizado e forecast a cada execução de rateio. Toda resolução de
camada passa por aqui para que uma divergência dessas seja impossível de
reintroduzir sem alterar um único ponto.

O namespace legado ``dimon`` é reconhecido apenas para **detecção e
migração**; nenhum caminho novo deve escrever nele.
"""

from __future__ import annotations

# --- Camadas canônicas -----------------------------------------------------

BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"
FORECAST = "forecast"

#: Namespace legado em remoção (P1D). Não usar em código novo.
LEGACY_DIMON = "dimon"

#: Namespaces que o runtime atual pode criar/ler.
ACTIVE_LAYERS: tuple[str, ...] = (BRONZE, SILVER, GOLD, FORECAST)

#: Todos os namespaces conhecidos, incluindo o legado — usado por utilitários de
#: auditoria, limpeza de staging e scripts de migração.
KNOWN_LAYERS: tuple[str, ...] = ACTIVE_LAYERS + (LEGACY_DIMON,)

#: Camadas que guardam dado derivado (podem ser reconstruídas a partir da Bronze).
DERIVED_LAYERS: tuple[str, ...] = (SILVER, GOLD, FORECAST, LEGACY_DIMON)


def detail_layer() -> str:
    """Camada do detalhe tipado.

    Toda leitura/escrita de FOCUS detalhado, inventário, série, perfil,
    candidato, decisão e planejamento usa esta camada. Existe como função (e não
    como constante mágica espalhada) para que a migração Gold → Silver tenha um
    único ponto de virada.
    """
    return SILVER


def cube_layer() -> str:
    """Camada dos cubos/materializações finais consumidos por API e dashboard."""
    return GOLD


def bronze_layer() -> str:
    """Camada de payload bruto."""
    return BRONZE


def forecast_layer() -> str:
    """Camada dos snapshots imutáveis de execução de forecast."""
    return FORECAST


def is_known(namespace: str | None) -> bool:
    """``True`` quando o namespace é um dos namespaces conhecidos de Iceberg."""
    return bool(namespace) and str(namespace).lower() in KNOWN_LAYERS


def is_legacy(namespace: str | None) -> bool:
    """``True`` quando o namespace pertence à arquitetura legada em remoção."""
    return bool(namespace) and str(namespace).lower() == LEGACY_DIMON


def normalize(namespace: str | None, *, fallback: str | None = None) -> str:
    """Normaliza um namespace para a camada canônica correspondente.

    Aceita variações de caixa e o sufixo ``.db`` usado por parte do warehouse
    físico (``silver.db``), mas sempre devolve o nome lógico da camada.
    """
    if not namespace:
        if fallback is None:
            raise ValueError("namespace vazio e sem fallback definido")
        return fallback
    value = str(namespace).strip().lower()
    if value.endswith(".db"):
        value = value[: -len(".db")]
    if value in KNOWN_LAYERS:
        return value
    if fallback is None:
        raise ValueError(f"namespace desconhecido: {namespace!r}")
    return fallback


def detail_sources() -> tuple[str, ...]:
    """Camadas que podem conter detalhe — Silver primeiro.

    Usado pela detecção de consultas divergentes: uma leitura de detalhe deve
    resolver para Silver. A Gold aparece depois apenas enquanto a migração de
    P1C/P1D não termina, e ``dimon`` só para auditoria.
    """
    return (SILVER, GOLD, LEGACY_DIMON)
