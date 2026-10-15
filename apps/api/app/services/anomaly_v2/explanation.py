"""Explicações em linguagem de negócio."""
from __future__ import annotations

from typing import Any, Mapping

REASON_CODE_TEXT: dict[str, dict[str, str]] = {
    "BELOW_MATERIALITY": {
        "title": "Abaixo da materialidade",
        "description": "O desvio financeiro ficou abaixo do limiar mínimo configurado.",
        "impact": "Reduz confiança; pode ir para Em observação.",
    },
    "BELOW_MIN_PCT": {
        "title": "Desvio percentual baixo",
        "description": "O aumento percentual ficou abaixo do mínimo configurado.",
        "impact": "Reduz confiança.",
    },
    "PARTIAL_LOAD": {
        "title": "Carga incompleta",
        "description": "O total do dia na nuvem ficou abaixo de 70% do esperado.",
        "impact": "Não confirma alerta neste dia.",
    },
    "LOW_CONFIDENCE": {
        "title": "Histórico insuficiente",
        "description": "A série não tem histórico estável suficiente para alta confiança.",
        "impact": "Vai para Em observação.",
    },
    "NEEDS_PERSISTENCE": {
        "title": "Persistência insuficiente",
        "description": "O excesso não se repetiu pelo número mínimo de dias.",
        "impact": "Vai para Em observação.",
    },
    "ON_CALENDAR": {
        "title": "Cobrança no calendário",
        "description": "Compromisso recorrente dentro da data e valor esperados.",
        "impact": "Evento programado, sem caso.",
    },
}


def explain_signal(signal: Mapping[str, Any], *, materiality: float, min_pct: float) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    delta = float(signal.get("delta") or 0)
    pct = float(signal.get("pct_delta") or 0)
    checks.append(
        {
            "criterio": "Materialidade",
            "limite": materiality,
            "valor_observado": delta,
            "passou": delta >= materiality,
            "explicacao": f"Desvio de R$ {delta:,.2f} {'≥' if delta >= materiality else '<'} limite R$ {materiality:,.2f}",
        }
    )
    checks.append(
        {
            "criterio": "Desvio percentual",
            "limite": min_pct,
            "valor_observado": pct,
            "passou": pct >= min_pct or signal.get("detector") == "NEW_SPEND",
            "explicacao": f"Variação de {pct * 100:.1f}%",
        }
    )
    for code in signal.get("reason_codes") or []:
        meta = REASON_CODE_TEXT.get(str(code), {})
        checks.append(
            {
                "criterio": meta.get("title") or code,
                "limite": None,
                "valor_observado": None,
                "passou": False,
                "explicacao": meta.get("description") or str(code),
            }
        )
    return checks
