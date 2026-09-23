"""Tipos de relatório do Blob de export e o seu de-para para o catálogo.

O caminho de cada relatório é **configuração da conexão**, não código: o mesmo
export muda de nome entre ambientes (`Report4P-actual-cost`,
`ReportTI-actual-cost`) e o cliente pode publicar outros tipos (`amortized`,
`focus`, `pricelist`). Chumbar qualquer um deles obrigaria a mexer no código para
cada ambiente novo.

Formato em `SourceConnection.blob_prefixes` (JSON), tipo → caminho::

    {
      "actual_cost":   "reports/Report4P-actual-cost/",
      "amortized_cost": "reports/Report4P-amortized-cost/",
      "focus":         "reports/Report4P-focus/",
      "price_sheet":   "reports/report_-pricesheet/"
    }

A forma antiga (lista de strings) continua aceita e é lida como `actual_cost`,
para não quebrar conexão já configurada.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Tipos de relatório ────────────────────────────────────────────────────────
# Caminho relativo dentro do container. O valor é o **padrão** quando a conexão
# não declara o tipo; a conexão sempre vence.

REPORT_TYPE_ACTUAL = "actual_cost"
REPORT_TYPE_AMORTIZED = "amortized_cost"
REPORT_TYPE_FOCUS = "focus"
REPORT_TYPE_PRICE_SHEET = "price_sheet"

REPORT_TYPES: dict[str, dict[str, Any]] = {
    REPORT_TYPE_ACTUAL: {
        "display_name": "Custo realizado (actual cost)",
        "description": "Export de custo do período — a base do FOCUS.",
        "default_path": "reports/Report4P-actual-cost/",
        # Fonte do catálogo que este relatório alimenta.
        "source_key": "cost_exports",
        "is_focus_base": True,
    },
    REPORT_TYPE_AMORTIZED: {
        "display_name": "Custo amortizado",
        "description": "Export amortizado: reservas distribuídas no período de uso.",
        "default_path": "reports/Report4P-amortized-cost/",
        "source_key": None,
        "is_focus_base": False,
    },
    REPORT_TYPE_FOCUS: {
        "display_name": "FOCUS nativo",
        "description": "Export já no esquema FOCUS, quando o provedor oferece.",
        "default_path": "reports/ReportFOCUS/",
        "source_key": None,
        "is_focus_base": False,
    },
    REPORT_TYPE_PRICE_SHEET: {
        "display_name": "Price Sheet contratual (MCA)",
        "description": "Tabela de preço contratado por medidor e região.",
        "default_path": "reports/report_-pricesheet/",
        "source_key": "customer_price",
        "is_focus_base": False,
    },
}


def normalize_prefixes(blob_prefixes: Any) -> dict[str, str]:
    """Normaliza `blob_prefixes` para `{tipo: caminho}`.

    Aceita as duas formas: o mapa novo e a lista de strings antiga (que vira
    `actual_cost`, preservando conexões já existentes).
    """
    if isinstance(blob_prefixes, dict):
        return {
            str(k): str(v).strip()
            for k, v in blob_prefixes.items()
            if str(v).strip()
        }
    if isinstance(blob_prefixes, (list, tuple)):
        itens = [str(x).strip() for x in blob_prefixes if str(x).strip()]
        if not itens:
            return {}
        # A lista antiga só sabia representar um caminho: era o actual cost.
        return {REPORT_TYPE_ACTUAL: itens[0]}
    return {}


def report_path(connection, report_type: str) -> str | None:
    """Caminho configurado para um tipo de relatório.

    A conexão vence; sem ela, usa o padrão do tipo. Devolve `None` quando o tipo
    é desconhecido e não há configuração.
    """
    configurados = normalize_prefixes(getattr(connection, "blob_prefixes", None))
    if report_type in configurados:
        return configurados[report_type]
    padrao = (REPORT_TYPES.get(report_type) or {}).get("default_path")
    return padrao


def enabled_report_types(connection) -> list[str]:
    """Tipos declarados pela conexão — só estes devem ser coletados."""
    return sorted(normalize_prefixes(getattr(connection, "blob_prefixes", None)))


def assert_report_enabled(connection, report_type: str) -> str:
    """Caminho do relatório, exigindo que a conexão o declare.

    Falhar alto é proposital: coletar um tipo que a conexão não habilitou traria
    dado que ninguém pediu, e o caminho padrão pode não existir no ambiente.
    """
    caminho = report_path(connection, report_type)
    if not caminho:
        raise ValueError(
            f"A conexão não configura o relatório '{report_type}'. "
            f"Declare em `blob_prefixes` (ex.: \"{report_type}\": \"<caminho>/\"). "
            f"Tipos conhecidos: {', '.join(sorted(REPORT_TYPES))}."
        )
    return caminho


def source_key_for_report(report_type: str) -> str | None:
    """Qual fonte do catálogo o relatório alimenta."""
    return (REPORT_TYPES.get(report_type) or {}).get("source_key")
