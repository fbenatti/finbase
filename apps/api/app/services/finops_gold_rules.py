"""Registro dos cubos `finops_*` na governança declarativa (`gold_rule`).

Os cubos C1–C3 e a cobertura usam o publicador governado e registram manifesto, mas
não tinham linha em `gold_rule`: ficavam fora da fila, fora do catálogo e fora do
manifesto de regras. Isso descumpre o contrato da seção 6.1 — um único caminho de
governança — porque um cubo podia existir sem que a governança soubesse dele.

O registro é **idempotente**: roda no start da API e pode rodar de novo sem duplicar
nem sobrescrever ajustes feitos no banco. Regra que já existe é atualizada apenas nos
campos que descrevem o contrato (nome, tabela, materializador, versão), nunca no
`active`, que é decisão do operador.

A fonte declarada é a **Silver** detalhada, não a Gold: uma regra que diz ler Gold
quando lê Silver foi exatamente o defeito de docstring corrigido em G2.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.gold_materializers import (
    MATERIALIZER_C1,
    MATERIALIZER_C2,
    MATERIALIZER_C3,
    MATERIALIZER_COVERAGE,
    MATERIALIZERS,
)

logger = logging.getLogger(__name__)

#: Versão do contrato de cada cubo. Muda quando o grão ou as medidas mudam.
#: Registrada em `gold_rule.description` para que a fila saiba o que vai publicar.
CUBE_CONTRACT_VERSION = "1.0.0"


def _rule_payload(key: str) -> dict[str, Any]:
    spec = MATERIALIZERS[key][0]
    return {
        "code": key,
        "name": {
            MATERIALIZER_COVERAGE: "FinOps — Cobertura de competências",
            MATERIALIZER_C1: "FinOps — Custo mensal (C1)",
            MATERIALIZER_C2: "FinOps — Custo por recurso (C2)",
            MATERIALIZER_C3: "FinOps — Drivers de variação (C3)",
        }[key],
        "description": (
            f"{spec.description} Contrato v{CONTRACT_VERSION}. "
            f"Construção multi-estágio: materializer_key={key}. "
            f"Fontes: {', '.join(spec.sources)}. "
            f"Depende de: {', '.join(spec.depends_on) or 'nada'}."
        ),
        "materialization_table": spec.physical_table,
        "materializer_key": key,
        "gold_tables": list(spec.sources),
    }


#: Versão do contrato dos cubos financeiros. Mantida aqui para não repetir a string
#: em quatro descrições.
CONTRACT_VERSION = CUBE_CONTRACT_VERSION


def seed_finops_gold_rules(session: Any) -> dict[str, Any]:
    """Insere/atualiza as quatro regras. Devolve o que foi criado e atualizado.

    `session` é sessão **síncrona** (worker/startup). O chamador faz o commit.
    """
    from app.models.gold_mart_rule import GoldMartRule, GoldMartRuleSource

    criadas: list[str] = []
    atualizadas: list[str] = []

    for key in (
        MATERIALIZER_COVERAGE,
        MATERIALIZER_C1,
        MATERIALIZER_C2,
        MATERIALIZER_C3,
    ):
        payload = _rule_payload(key)
        regra = (
            session.query(GoldMartRule).filter(GoldMartRule.code == payload["code"]).first()
        )
        if regra is None:
            regra = GoldMartRule(
                code=payload["code"],
                name=payload["name"],
                description=payload["description"],
                active=True,
                materialization_table=payload["materialization_table"],
                materializer_key=payload["materializer_key"],
                dimensions=[],
                measure_columns=[],
            )
            session.add(regra)
            session.flush()
            criadas.append(payload["code"])
        else:
            regra.name = payload["name"]
            regra.description = payload["description"]
            regra.materialization_table = payload["materialization_table"]
            regra.materializer_key = payload["materializer_key"]
            atualizadas.append(payload["code"])

        # Uma fonte por tabela declarada. O materializador destes cubos não usa
        # `gold_rule_source` para montar SQL — a construção é em código — mas a
        # fonte fica registrada para o catálogo e para a auditoria de dependência.
        existentes = {
            str(getattr(s, "gold_table", "")) for s in (regra.sources or [])
        }
        for tabela in payload["gold_tables"]:
            if tabela in existentes:
                continue
            session.add(
                GoldMartRuleSource(
                    rule_id=regra.id,
                    gold_table=tabela,
                    gold_tables=[tabela],
                    filter_json={"conditions": []},
                    value_column="effective_cost",
                    percent=100.0,
                )
            )

    return {"created": criadas, "updated": atualizadas}
