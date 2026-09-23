"""Testes da normalização semântica (G1).

Cada caso foi medido no ambiente de referência em 23/09/2026. Os números nos
comentários são contagens reais de `iceberg.silver.resource_cost`, para que uma
regressão que reintroduza a divisão por caixa seja visível.

Os testes cobrem os aceites de G1:

- serviços com diferença de caixa são agrupados;
- cobranças comprovadamente globais usam `GLOBAL`, ausências desconhecidas usam
  `UNKNOWN`;
- `resource_id` sozinho nunca é chave de ligação;
- nenhum total financeiro muda por normalização.
"""

from __future__ import annotations

import pytest

from app.services.semantic_normalization import (
    GLOBAL,
    UNKNOWN,
    CurrencyMixedError,
    assert_single_currency,
    billing_period_key,
    canonical_resource_id,
    connection_key,
    cost_join_key,
    currency_code,
    is_allocatable_region,
    is_global_region,
    provider_key,
    region_key,
    resource_type_key,
    service_display_name,
    service_key,
    sql_canonical_resource_id,
    sql_cost_join_key,
    sql_region_key,
    sql_service_key,
)


# --- serviço: caixa não divide o mesmo serviço ---------------------------------


def test_service_casing_collapses():
    """`Microsoft.Compute` (2.249.339 linhas) e `microsoft.compute` (2.228.455)."""
    assert service_key("Microsoft.Compute") == service_key("microsoft.compute")
    assert service_key("Microsoft.Compute") == "microsoft.compute"


def test_service_key_strips_and_squeezes_spaces():
    assert service_key("  Microsoft.  Compute  ") == "microsoft.compute"
    assert service_key("Microsoft.Compute") == service_key("Microsoft.Compute ")


@pytest.mark.parametrize(
    "a,b",
    [
        ("Microsoft.Storage", "microsoft.storage"),
        ("microsoft.network", "Microsoft.Network"),
        ("Microsoft.Databricks", "microsoft.databricks"),
        ("Microsoft.App", "MICROSOFT.APP"),
    ],
)
def test_every_measured_collision_collapses(a, b):
    """As 19 colisões reais medidas em 23/09/2026 devem colapsar."""
    assert service_key(a) == service_key(b)


def test_service_key_absent_is_unknown_not_empty():
    """R$ 5.857,19/mês de `Purchase` sem serviço não pode virar categoria vazia."""
    assert service_key(None) == UNKNOWN
    assert service_key("") == UNKNOWN
    assert service_key("   ") == UNKNOWN
    assert service_key("null") == UNKNOWN
    assert service_key("None") == UNKNOWN


def test_service_display_prefers_capitalized_brand():
    vencedor = service_display_name(["microsoft.compute", "Microsoft.Compute"])
    assert vencedor == "Microsoft.Compute"


def test_service_display_is_deterministic_regardless_of_order():
    entrada = ["microsoft.network", "Microsoft.Network", "microsoft.NETWORK"]
    assert service_display_name(entrada) == service_display_name(reversed(entrada))


def test_service_display_breaks_tie_lexically():
    """Empate não pode depender da ordem de leitura do Trino."""
    assert service_display_name(["B.Compute", "A.Compute"]) == "A.Compute"


def test_service_display_absent_is_unknown():
    assert service_display_name([]) == UNKNOWN
    assert service_display_name([None, ""]) == UNKNOWN


# --- região: GLOBAL é diferente de UNKNOWN ------------------------------------


def test_region_present_is_normalized():
    """`BRAZILSOUTH` (384.258 linhas) convivia com `brazilsouth` (6.857.678)."""
    assert region_key("brazilsouth") == "brazilsouth"
    assert region_key("BRAZILSOUTH") == "brazilsouth"
    assert region_key(" BrazilSouth ") == "brazilsouth"


def test_region_already_global_stays_global():
    """`global` aparece 558.813 vezes na origem — não é ausência."""
    assert region_key("global") == GLOBAL
    assert region_key("GLOBAL") == GLOBAL


def test_region_absent_with_purchase_is_global():
    """R$ 6.482.044,66 de compra de reserva: global por natureza, não desconhecido."""
    assert region_key(None, charge_category="Purchase") == GLOBAL
    assert region_key(None, charge_category="Refund") == GLOBAL
    assert region_key(None, service_name="Microsoft.Capacity") == GLOBAL


def test_region_absent_with_usage_is_unknown():
    """Ausência em consumo é limitação de dado — nunca vira global."""
    assert region_key(None, charge_category="Usage") == UNKNOWN
    assert region_key(None, charge_category="Usage", service_name="Microsoft.Storage") == UNKNOWN


def test_region_absent_without_context_is_unknown():
    assert region_key(None) == UNKNOWN
    assert region_key("") == UNKNOWN


def test_region_global_and_unknown_are_distinguishable():
    assert region_key(None, charge_category="Purchase") != region_key(None, charge_category="Usage")


@pytest.mark.parametrize("valor", ["brazilsouth", "eastus", "global", "GLOBAL"])
def test_allocatable_region_excludes_markers(valor):
    if valor.upper() == GLOBAL:
        assert is_allocatable_region(valor) is False
    else:
        assert is_allocatable_region(valor) is True


@pytest.mark.parametrize("valor", [UNKNOWN, "", None, "unknown"])
def test_allocatable_region_rejects_absence(valor):
    assert is_allocatable_region(valor) is False


def test_is_global_region_only_accepts_marker():
    assert is_global_region("global") is True
    assert is_global_region("GLOBAL") is True
    assert is_global_region("brazilsouth") is False
    assert is_global_region(UNKNOWN) is False
    assert is_global_region(None) is False


# --- ausência nunca vira valor -------------------------------------------------


def test_absent_sentinels_are_absence():
    for sentinela in (None, "", "  ", "null", "NULL", "None", "undefined", "nan"):
        assert service_key(sentinela) == UNKNOWN
        assert resource_type_key(sentinela) == UNKNOWN
        assert canonical_resource_id(sentinela) is None
        assert currency_code(sentinela) is None


def test_absent_is_never_zero_like():
    """Zero observado e ausência são estados diferentes (seção 4.4)."""
    assert canonical_resource_id(None) is None
    assert currency_code(None) is None
    assert billing_period_key(None) is None


# --- ID canônico ---------------------------------------------------------------


def test_canonical_resource_id_normalizes_case_and_space():
    bruto = "  /Subscriptions/ABC/ResourceGroups/RG/Providers/Microsoft.Compute/virtualMachines/VM1 "
    assert canonical_resource_id(bruto) == (
        "/subscriptions/abc/resourcegroups/rg/providers/microsoft.compute/virtualmachines/vm1"
    )


def test_canonical_resource_id_preserves_absence():
    assert canonical_resource_id(None) is None
    assert canonical_resource_id("") is None


# --- chave de ligação ----------------------------------------------------------


def test_cost_join_key_requires_all_components():
    """`resource_id` sozinho nunca liga entre tenants ou providers."""
    completa = cost_join_key(
        provider="azure",
        sub_account_id="4816bdc1",
        resource_id="/subscriptions/x/virtualMachines/vm1",
    )
    assert completa == ("azure", "4816bdc1", "/subscriptions/x/virtualmachines/vm1")

    assert cost_join_key(provider=None, sub_account_id="4816bdc1", resource_id="/x") is None
    assert cost_join_key(provider="azure", sub_account_id=None, resource_id="/x") is None
    assert cost_join_key(provider="azure", sub_account_id="4816bdc1", resource_id=None) is None


def test_cost_join_key_same_id_different_tenant_is_different_key():
    """O mesmo ID em tenants distintos não pode colidir."""
    a = cost_join_key(provider="azure", sub_account_id="tenant-a", resource_id="/x/vm1")
    b = cost_join_key(provider="azure", sub_account_id="tenant-b", resource_id="/x/vm1")
    assert a != b


def test_cost_join_key_provider_casing_collapses():
    a = cost_join_key(provider="Azure", sub_account_id="T1", resource_id="/x")
    b = cost_join_key(provider="azure", sub_account_id="t1", resource_id="/x")
    assert a == b


def test_provider_key_is_controlled_vocabulary():
    assert provider_key("Azure") == "azure"
    assert provider_key("GCP") == "gcp"
    assert provider_key(None) == UNKNOWN


# --- competência ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("202504", "202504"),
        ("2025-04", "202504"),
        ("2025/04", "202504"),
        ("20250401", "202504"),
        ("202504-01", "202504"),
    ],
)
def test_billing_period_accepts_known_formats(entrada, esperado):
    assert billing_period_key(entrada) == esperado


@pytest.mark.parametrize("invalido", ["", None, "abc", "2025", "202513", "000013", "189912"])
def test_billing_period_rejects_invalid(invalido):
    """Competência inventada é pior que competência ausente."""
    assert billing_period_key(invalido) is None


# --- moeda --------------------------------------------------------------------


def test_currency_is_uppercase_and_required():
    assert currency_code("brl") == "BRL"
    assert currency_code(" usd ") == "USD"


def test_single_currency_ok():
    assert assert_single_currency([{"currency": "BRL"}, {"currency": "brl"}]) == "BRL"


def test_mixed_currency_fails_loudly():
    """Somar BRL e USD na mesma medida é proibido — falha, não converte."""
    with pytest.raises(CurrencyMixedError):
        assert_single_currency([{"currency": "BRL"}, {"currency": "USD"}])


def test_missing_currency_is_not_assumed():
    """Assumir a moeda da maioria seria o erro que a regra proíbe."""
    with pytest.raises(CurrencyMixedError):
        assert_single_currency([{"currency": None}])


# --- conexão ------------------------------------------------------------------


def test_connection_key_prefers_stable_code():
    """O `name` na Silver é a tabela Bronze (`raw_azure_4p`), não nome de negócio."""
    assert connection_key("azure-4p", "raw_azure_4p") == "azure-4p"
    assert connection_key(None, "raw_azure_4p") == "raw_azure_4p"
    assert connection_key(None, None) == UNKNOWN


# --- expressões SQL equivalentes ----------------------------------------------


def test_sql_service_key_mentions_normalization():
    sql = sql_service_key("service_name")
    assert "LOWER" in sql and "REGEXP_REPLACE" in sql
    assert UNKNOWN in sql


def test_sql_region_key_has_both_branches():
    """A expressão precisa poder produzir GLOBAL e UNKNOWN — é o ponto da regra."""
    sql = sql_region_key(
        "region_id", charge_category_expr="charge_category", service_expr="service_name"
    )
    assert GLOBAL in sql
    assert UNKNOWN in sql
    assert "purchase" in sql.lower()


def test_sql_region_key_without_context_defaults_to_unknown():
    """Sem contexto não há como provar globalidade: o default seguro é UNKNOWN."""
    sql = sql_region_key("region_id")
    assert UNKNOWN in sql
    assert GLOBAL in sql  # ainda reconhece o marcador explícito da origem


def test_sql_canonical_resource_id_preserves_null():
    sql = sql_canonical_resource_id("resource_id")
    assert "NULL" in sql and "LOWER" in sql


def test_sql_cost_join_key_returns_null_when_incomplete():
    sql = sql_cost_join_key(
        provider_expr="cloud_provider",
        sub_account_expr="sub_account_id",
        resource_id_expr="resource_id",
    )
    assert "NULL" in sql
    assert "CONCAT" in sql
