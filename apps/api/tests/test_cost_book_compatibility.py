"""Testes da compatibilidade entre os dois livros de custo (G2).

Contexto: `silver.resource_cost` (canônico, 13.519.512 linhas) e
`silver.dest_modelo_focus` (legado, 36.434.762 linhas) convivem. A decisão de
direção mantém o legado servindo os consumidores atuais até o cutover, o que
torna obrigatório **medir** a diferença em vez de supor que ela não existe.
"""

from __future__ import annotations

import pytest

from app.services.cost_book_compatibility import (
    CANONICAL_COST_TABLE,
    LEGACY_COST_TABLE,
    MATERIAL_DIVERGENCE_RATIO,
    compare_books,
    divergence_sql,
    divergences_from_rows,
    material_divergences,
)


def test_comparison_reports_signed_delta():
    """`delta` é legado − canônico: positivo significa legado medindo mais."""
    d = compare_books(legacy_amount=7696515.12, canonical_amount=3718891.12, billing_period="202512")
    assert d.delta == pytest.approx(3977624.00)
    assert d.delta_ratio == pytest.approx(1.069573, abs=1e-6)
    assert d.material is True


def test_small_divergence_is_not_material():
    d = compare_books(legacy_amount=1_010.0, canonical_amount=1_000.0, billing_period="202601")
    assert d.material is False
    assert d.delta_ratio == pytest.approx(0.01)


def test_divergence_at_tolerance_is_not_material():
    """A tolerância é o limite: exatamente 1% ainda não é material."""
    d = compare_books(legacy_amount=1_010.0, canonical_amount=1_000.0, billing_period="202601")
    assert abs(d.delta_ratio) == pytest.approx(MATERIAL_DIVERGENCE_RATIO)
    assert d.material is False


def test_negative_delta_is_material_too():
    """Legado medindo menos também é divergência a explicar."""
    d = compare_books(legacy_amount=500.0, canonical_amount=1_000.0, billing_period="202601")
    assert d.delta == -500.0
    assert d.material is True


def test_missing_side_does_not_declare_divergence():
    """Sem os dois números não se declara divergência — declara-se ausência."""
    so_canonico = compare_books(legacy_amount=None, canonical_amount=209564.67, billing_period="202504")
    assert so_canonico.delta is None
    assert so_canonico.material is False

    so_legado = compare_books(legacy_amount=11188333.54, canonical_amount=None, billing_period="202608")
    assert so_legado.delta is None
    assert so_legado.material is False


def test_zero_base_does_not_produce_ratio():
    """Base zero não gera percentual: dividir por zero inventaria número."""
    d = compare_books(legacy_amount=10_970_689.60, canonical_amount=0.0, billing_period="202607")
    assert d.delta_ratio is None
    assert d.material is True


def test_zero_on_both_sides_is_not_material():
    d = compare_books(legacy_amount=0.0, canonical_amount=0.0, billing_period="202607")
    assert d.delta == 0.0
    assert d.material is False


def test_divergence_sql_reads_both_books_and_uses_full_outer_join():
    """Competência presente em só um livro precisa aparecer, não desaparecer."""
    sql = divergence_sql()
    assert f"iceberg.silver.{CANONICAL_COST_TABLE}" in sql
    assert f"iceberg.silver.{LEGACY_COST_TABLE}" in sql
    assert "FULL OUTER JOIN" in sql
    assert "effective_cost" in sql
    assert "effectivecost" in sql


def test_divergence_sql_only_aggregates():
    """A medida não pode varrer linha a linha para o consumidor."""
    sql = divergence_sql()
    assert "GROUP BY 1" in sql
    assert sql.count("GROUP BY") == 2


def test_rows_are_converted_with_types_from_trino():
    """O Trino devolve decimal como string; a conversão não pode quebrar."""
    linhas = [
        {"billing_period": "202512", "canonical_amount": "3718891.12", "legacy_amount": "7696515.12"},
        {"billing_period": "202504", "canonical_amount": "209564.67", "legacy_amount": None},
    ]
    divergencias = divergences_from_rows(linhas)
    assert divergencias[0].delta == pytest.approx(3977624.0)
    assert divergencias[1].material is False


def test_rows_tolerate_unparseable_amount():
    divergencias = divergences_from_rows(
        [{"billing_period": "202601", "canonical_amount": "n/a", "legacy_amount": "10"}]
    )
    assert divergencias[0].delta is None


def test_material_filter_keeps_only_what_needs_explaining():
    divergencias = divergences_from_rows(
        [
            {"billing_period": "202504", "canonical_amount": "209564.67", "legacy_amount": None},
            {"billing_period": "202512", "canonical_amount": "3718891.12", "legacy_amount": "7696515.12"},
            {"billing_period": "202601", "canonical_amount": "1000", "legacy_amount": "1005"},
        ]
    )
    materiais = material_divergences(divergencias)
    assert [d.billing_period for d in materiais] == ["202512"]


def test_divergence_row_shape_is_api_ready():
    d = compare_books(legacy_amount=100.0, canonical_amount=50.0, billing_period="202601")
    linha = d.as_row()
    for campo in ("billing_period", "legacy_amount", "canonical_amount", "delta", "delta_ratio", "material"):
        assert campo in linha
    assert isinstance(linha["material"], bool)


def test_legacy_book_is_not_the_canonical_one():
    """A decisão de direção: `resource_cost` é o canônico, não o legado."""
    assert CANONICAL_COST_TABLE == "resource_cost"
    assert LEGACY_COST_TABLE == "dest_modelo_focus"
    assert CANONICAL_COST_TABLE != LEGACY_COST_TABLE
