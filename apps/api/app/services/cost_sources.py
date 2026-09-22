"""Classificação explícita de conexões como fonte de custo.

O destino FOCUS (`dest_modelo_focus`) é o livro financeiro. Aceitar mapping de
qualquer conexão permitiu que Azure Automation e Azure Resource Graph — fontes
operacionais, sem valor financeiro — escrevessem 22.747 linhas com `realizado`
nulo dentro dele (ver `docs/ai/azure-finops-execution-log.md`, achado D9/P1B).

Identificar "fonte de custo" por prefixo textual é errado: `azure-automation-*`
e `azure-resource-graph-*` começam com `azure-` e **não** são custo. A
classificação aqui é por metadata explícita, gravada em
`source_connection.config_json`:

- `cost_source: true`  → a conexão produz linhas financeiras no destino de custo;
- `cost_source: false` → fonte operacional; nunca pode alimentar o livro financeiro;
- ausente              → legado ainda não classificado (não bloqueia, mas não é
  considerado custo por `is_cost_source`).

Os marcadores são aplicados por `apps/api/scripts/mark_cost_sources.py`.
"""

from __future__ import annotations

from typing import Any

#: `source_key` das conexões que entregam export de custo.
COST_EXPORTS_KEY = "cost_exports"

#: `destination_table.id` do FOCUS — o único destino com valor financeiro hoje.
FOCUS_DESTINATION_ID = 1


def is_cost_source(connection: Any) -> bool:
    """``True`` quando a conexão está marcada como fonte de custo.

    Aceita tanto `cost_source` booleano quanto `source_key == 'cost_exports'`,
    porque o marcador foi introduzido em duas etapas. Ausência de metadata
    devolve ``False``: o default seguro é não tratar como custo.
    """
    config = getattr(connection, "config_json", None) or {}
    if not isinstance(config, dict):
        return False
    if config.get("source_key") == COST_EXPORTS_KEY:
        return True
    value = config.get("cost_source")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "sim"}
    return False


def is_explicitly_non_cost(connection: Any) -> bool:
    """``True`` quando a conexão foi marcada explicitamente como não-custo."""
    config = getattr(connection, "config_json", None) or {}
    if not isinstance(config, dict):
        return False
    value = config.get("cost_source")
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "nao", "não"}
    return False


def destination_requires_cost_source(destination_id: int | None) -> bool:
    """``True`` quando o destino é o livro financeiro."""
    return destination_id == FOCUS_DESTINATION_ID


def mapping_rejection_reason(connection: Any, destination_id: int | None) -> str | None:
    """Motivo pelo qual a conexão não pode mapear para o destino, ou ``None``.

    O bloqueio vale para fontes **explicitamente** operacionais. Conexões legadas
    sem metadata continuam permitidas para não quebrar configuração existente —
    a marcação é feita por script idempotente, não por adivinhação em runtime.
    """
    if not destination_requires_cost_source(destination_id):
        return None
    if is_cost_source(connection):
        return None
    if is_explicitly_non_cost(connection):
        return (
            f"A conexão '{getattr(connection, 'code', '?')}' está marcada como fonte "
            "operacional (cost_source=false) e não pode alimentar o destino FOCUS. "
            "Use o destino próprio da fonte (inventário, órfãos, automação)."
        )
    return None
