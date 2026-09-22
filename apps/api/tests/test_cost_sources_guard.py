"""Testes do bloqueio de fontes operacionais no destino FOCUS (P1B).

Contexto: Azure Automation e Azure Resource Graph escreveram 22.747 linhas com
`realizado` nulo dentro do livro financeiro porque o destino aceitava mapping de
qualquer conexão. A classificação por prefixo textual (`azure-`) não resolve —
essas duas fontes começam com `azure-`. O bloqueio é por metadata explícita.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.cost_sources import (
    FOCUS_DESTINATION_ID,
    is_cost_source,
    is_explicitly_non_cost,
    mapping_rejection_reason,
)


def conn(code: str, **config):
    return SimpleNamespace(code=code, config_json=config)


# --- classificação ---------------------------------------------------------

def test_source_key_marks_cost_source():
    assert is_cost_source(conn("azure-ti", source_key="cost_exports")) is True


def test_explicit_true_marks_cost_source():
    assert is_cost_source(conn("gcp-4p", cost_source=True)) is True
    assert is_cost_source(conn("gcp-4p", cost_source="true")) is True
    assert is_cost_source(conn("gcp-4p", cost_source="True")) is True


def test_explicit_false_is_non_cost():
    assert is_cost_source(conn("azure-automation-4p", cost_source=False)) is False
    assert is_explicitly_non_cost(conn("azure-automation-4p", cost_source=False)) is True


def test_unmarked_connection_is_not_a_cost_source_by_default():
    """Ausência de metadata não pode virar custo por omissão."""
    unmarked = conn("alguma-conexao")
    assert is_cost_source(unmarked) is False
    assert is_explicitly_non_cost(unmarked) is False


def test_azure_prefix_alone_does_not_make_it_cost():
    """O bug original: `azure-automation-*` casa com o prefixo e não é custo."""
    assert is_cost_source(conn("azure-automation-ti")) is False
    assert is_cost_source(conn("azure-resource-graph-4p")) is False


# --- bloqueio de mapping ---------------------------------------------------

def test_operational_source_cannot_map_to_focus():
    reason = mapping_rejection_reason(conn("azure-automation-4p", cost_source=False), FOCUS_DESTINATION_ID)
    assert reason is not None
    assert "operacional" in reason
    assert "azure-automation-4p" in reason


@pytest.mark.parametrize("code,config", [
    ("azure-automation-4p", {"cost_source": False}),
    ("azure-automation-ti", {"cost_source": False}),
    ("azure-resource-graph-4p", {"cost_source": False}),
    ("azure-resource-graph-ti", {"cost_source": False}),
    ("azure-4p-ptu", {"cost_source": False}),
])
def test_every_operational_source_is_blocked_for_focus(code, config):
    assert mapping_rejection_reason(conn(code, **config), FOCUS_DESTINATION_ID) is not None


def test_cost_source_is_allowed_for_focus():
    assert mapping_rejection_reason(conn("azure-ti", source_key="cost_exports"), FOCUS_DESTINATION_ID) is None


def test_unmarked_legacy_connection_is_not_blocked():
    """Marcação é feita por script; runtime não bloqueia o que não classificou."""
    assert mapping_rejection_reason(conn("conexao-legada"), FOCUS_DESTINATION_ID) is None


@pytest.mark.parametrize("destination_id", [36, 38, 40, 41])
def test_operational_source_is_allowed_for_its_own_destination(destination_id):
    """O bloqueio vale para o FOCUS; inventário/órfãos/automação continuam válidos."""
    assert mapping_rejection_reason(conn("azure-automation-4p", cost_source=False), destination_id) is None
    assert mapping_rejection_reason(conn("azure-resource-graph-4p", cost_source=False), destination_id) is None


def test_missing_connection_does_not_block_unrelated_destination():
    assert mapping_rejection_reason(None, 40) is None


def test_missing_connection_without_metadata_does_not_block_focus():
    """Conexão não resolvida não é prova de má configuração.

    O bloqueio exige marcação explícita (`cost_source=false`). Uma conexão
    ausente não carrega essa marca, então não bloqueia — quem impede a
    reincidência de fato é o script de marcação somado ao bloqueio aqui.
    """
    assert mapping_rejection_reason(None, FOCUS_DESTINATION_ID) is None
