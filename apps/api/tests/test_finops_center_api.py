"""Testes de contrato da API do Centro de Decisão (G8, frente de custo).

A API é exercitada pelo cliente HTTP do FastAPI, não por chamada direta: chamar o
handler direto deixa os defaults de `Query` sem resolver e testaria um caminho que
não é o de produção.
"""

from __future__ import annotations

import pytest

from app.routers import finops_center as fc


# --- allowlist de filtros -----------------------------------------------------


def test_where_accepts_allowlisted_filters():
    where = fc._where(filters=fc.C1_FILTERS, values={"connection": "azure-4p"})
    assert where == "connection_code = 'azure-4p'"


def test_where_escapes_single_quote():
    where = fc._where(filters=fc.C1_FILTERS, values={"service": "o'brien"})
    assert "'o''brien'" in where


def test_where_rejects_unknown_filter():
    """Filtro ignorado em silêncio devolveria número que não é o pedido."""
    with pytest.raises(fc.HTTPException) as info:
        fc._where(filters=fc.C1_FILTERS, values={"pod": "x"})
    assert info.value.status_code == 400
    assert "não suportado" in info.value.detail


def test_where_accepts_list_of_values():
    where = fc._where(
        filters=fc.C1_FILTERS, values={"service": ["microsoft.compute", "microsoft.storage"]}
    )
    assert "service_key IN ('microsoft.compute', 'microsoft.storage')" in where


def test_where_ignores_empty_values():
    assert fc._where(filters=fc.C1_FILTERS, values={"service": None, "region": ""}) == "1 = 1"


def test_where_never_interpolates_the_column_from_input():
    """O nome da coluna vem da allowlist, nunca do cliente."""
    where = fc._where(filters=fc.C1_FILTERS, values={"service": "service_key; DROP TABLE x"})
    assert "DROP TABLE" not in where.split("=")[0]
    assert where.startswith("service_key =")


def test_query_default_is_treated_as_absent():
    """Fora do ciclo de requisição, o default de `Query` não é `None`."""
    from fastapi import Query

    assert fc._resolved(Query(None)) is None
    assert fc._resolved("azure-4p") == "azure-4p"
    assert fc._resolved(5) == 5


# --- medidas não aditivas -----------------------------------------------------


def test_non_additive_measures_absent_from_additive_set():
    from app.services.finops_cost_monthly import ADDITIVE_MEASURES, NON_ADDITIVE_MEASURES

    assert not (ADDITIVE_MEASURES & NON_ADDITIVE_MEASURES)
    assert "resources" in NON_ADDITIVE_MEASURES


def test_unavailable_measures_are_declared_with_reason():
    """Indisponível é declarado com motivo, e não vira zero.

    Duas naturezas diferentes convivem aqui: `billed_cost`/`contracted_cost` são
    nulos **na origem**; os níveis organizacionais são indisponíveis por **falta de
    chave reconciliável**. A UI precisa distinguir as duas.
    """
    from app.services.finops_cost_monthly import unavailable_measures

    motivos = unavailable_measures()
    for campo in ("billed_cost", "contracted_cost"):
        assert "13.519.512" in motivos[campo]
    assert "UNMAPPED" in motivos["organograma_nivel_1..7"]


# --- ordenação e paginação ----------------------------------------------------


def test_sort_allowlist_is_closed():
    assert "effective_cost" in fc._SORTABLE
    assert "resource_name" in fc._SORTABLE
    assert "senha" not in fc._SORTABLE


def test_envelope_has_the_common_contract():
    envelope = fc._envelope(scope={"a": 1}, data={"x": 2})
    for campo in ("scope", "as_of", "currency", "data_status", "quality", "data", "page"):
        assert campo in envelope


def test_envelope_reports_page_when_paginated():
    envelope = fc._envelope(
        scope={}, data={}, page={"total": 10, "limit": 5, "offset": 0}
    )
    assert envelope["page"]["total"] == 10


def test_envelope_quality_default_has_warnings():
    envelope = fc._envelope(scope={}, data={})
    assert envelope["quality"]["warnings"] == []


# --- driver columns -----------------------------------------------------------


def test_driver_filter_uses_declared_types():
    from app.services.finops_cost_driver import DRIVER_TYPES

    assert set(fc.DRIVER_TYPES) == set(DRIVER_TYPES)
