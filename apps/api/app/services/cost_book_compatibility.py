"""Compatibilidade entre os dois livros de custo.

Em 23/09/2026 convivem duas tabelas de custo no mesmo lakehouse:

- ``silver.resource_cost`` — o livro canônico novo (13.519.512 linhas,
  R$ 37.356.361,47), usado pelos quatro cubos MVP e escolhido como fonte de
  C1–C3;
- ``silver.dest_billing_cloud`` — o livro FOCUS legado (36.434.762 linhas), que
  ainda atende as regras Gold organizacionais (ids 3–16 em `gold_rule`).

Os dois não medem a mesma coisa. `dest_billing_cloud` é o FOCUS no grão da linha
de custo do provedor; `resource_cost` é o modelo canônico com o grão declarado em
`silver_models.py`. Contar linhas de um e comparar com o outro não prova
divergência.

A decisão de direção mantém `dest_billing_cloud` servindo os consumidores legados
até o cutover. Isso torna obrigatório **medir** a diferença em vez de supor que
ela não existe: se a tela nova mostrar um total e a antiga mostrar outro, o usuário
precisa saber qual é a diferença e por quê.

Este módulo produz a medida. Ele não decide qual livro está certo — a ordem das
fontes de verdade decide, e `resource_cost` é o canônico.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Livro canônico (novo) e livro legado.
CANONICAL_COST_TABLE = "resource_cost"
LEGACY_COST_TABLE = "dest_billing_cloud"

#: Tolerância de divergência relativa a partir da qual a diferença é material.
#: Abaixo disso a diferença é ruído de modelagem; acima, precisa ser explicada.
MATERIAL_DIVERGENCE_RATIO = 0.01


@dataclass(frozen=True)
class BookDivergence:
    """Diferença medida entre os dois livros em uma competência."""

    billing_period: str
    legacy_amount: float | None
    canonical_amount: float | None
    delta: float | None
    delta_ratio: float | None
    material: bool

    def as_row(self) -> dict[str, Any]:
        return {
            "billing_period": self.billing_period,
            "legacy_amount": self.legacy_amount,
            "canonical_amount": self.canonical_amount,
            "delta": self.delta,
            "delta_ratio": self.delta_ratio,
            "material": self.material,
        }


def compare_books(
    *, legacy_amount: float | None, canonical_amount: float | None, billing_period: str
) -> BookDivergence:
    """Compara os dois livros em uma competência.

    `delta` é ``legado − canônico``: positivo significa que o livro legado mede
    mais que o canônico. Ausência de um dos lados devolve delta ``None`` e
    `material=False` — não se declara divergência sem os dois números.
    """
    if legacy_amount is None or canonical_amount is None:
        return BookDivergence(
            billing_period=billing_period,
            legacy_amount=legacy_amount,
            canonical_amount=canonical_amount,
            delta=None,
            delta_ratio=None,
            material=False,
        )
    delta = round(float(legacy_amount) - float(canonical_amount), 2)
    base = abs(float(canonical_amount))
    if base == 0:
        # Base zero não gera percentual: qualquer diferença é material por
        # definição, e dividir por zero produziria um número inventado.
        return BookDivergence(
            billing_period=billing_period,
            legacy_amount=legacy_amount,
            canonical_amount=canonical_amount,
            delta=delta,
            delta_ratio=None,
            material=delta != 0,
        )
    razao = delta / base
    return BookDivergence(
        billing_period=billing_period,
        legacy_amount=legacy_amount,
        canonical_amount=canonical_amount,
        delta=delta,
        delta_ratio=round(razao, 6),
        material=abs(razao) > MATERIAL_DIVERGENCE_RATIO,
    )


def divergence_sql(
    *,
    layer: str = "silver",
    canonical_table: str = CANONICAL_COST_TABLE,
    legacy_table: str = LEGACY_COST_TABLE,
) -> str:
    """SQL que mede a divergência por competência entre os dois livros.

    Usa `FULL OUTER JOIN` pela competência para que uma competência presente em
    só um dos livros apareça como divergência, e não desapareça do relatório.

    O custo do legado usa `effectivecost` (o equivalente FOCUS de
    `effective_cost`) e o do canônico `effective_cost` — comparar colunas
    diferentes seria comparar conceitos diferentes.
    """
    return f"""
WITH canonico AS (
    SELECT CAST(COALESCE(_billing_period, billing_period) AS VARCHAR) AS billing_period,
           ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)), 2) AS amount,
           COUNT(*) AS rows
    FROM iceberg.{layer}.{canonical_table}
    GROUP BY 1
),
legado AS (
    SELECT CAST(_billing_period AS VARCHAR) AS billing_period,
           ROUND(SUM(TRY_CAST(effectivecost AS DOUBLE)), 2) AS amount,
           COUNT(*) AS rows
    FROM iceberg.{layer}.{legacy_table}
    GROUP BY 1
)
SELECT COALESCE(c.billing_period, l.billing_period) AS billing_period,
       c.amount AS canonical_amount,
       l.amount AS legacy_amount,
       ROUND(COALESCE(l.amount, 0) - COALESCE(c.amount, 0), 2) AS delta,
       c.rows AS canonical_rows,
       l.rows AS legacy_rows,
       CASE
         WHEN c.amount IS NULL OR l.amount IS NULL THEN NULL
         WHEN c.amount = 0 THEN NULL
         ELSE ROUND((l.amount - c.amount) / ABS(c.amount), 6)
       END AS delta_ratio
FROM canonico c
FULL OUTER JOIN legado l ON l.billing_period = c.billing_period
ORDER BY 1
""".strip()


def divergences_from_rows(rows: list[dict[str, Any]]) -> list[BookDivergence]:
    """Converte o resultado de :func:`divergence_sql` em `BookDivergence`."""

    def _num(valor: Any) -> float | None:
        if valor is None:
            return None
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None

    saida: list[BookDivergence] = []
    for row in rows or []:
        saida.append(
            compare_books(
                legacy_amount=_num(row.get("legacy_amount")),
                canonical_amount=_num(row.get("canonical_amount")),
                billing_period=str(row.get("billing_period") or ""),
            )
        )
    return saida


def material_divergences(
    divergences: list[BookDivergence],
) -> list[BookDivergence]:
    """Só as divergências acima da tolerância — o que precisa ser explicado."""
    return [d for d in divergences if d.material]


async def measure_books(
    *, layer: str = "silver", timeout_s: float = 1800.0
) -> dict[str, Any]:
    """Executa a medida de divergência e devolve o resumo para C11.

    Devolve `material_count` e a lista das competências materiais. Um total sem
    divergência material devolve lista vazia — não ausência de medição.
    """
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(divergence_sql(layer=layer), timeout_s=timeout_s)
    divergencias = divergences_from_rows(linhas or [])
    materiais = material_divergences(divergencias)
    return {
        "periods_measured": len(divergencias),
        "material_count": len(materiais),
        "material": [d.as_row() for d in materiais],
        "all": [d.as_row() for d in divergencias],
        "tolerance_ratio": MATERIAL_DIVERGENCE_RATIO,
    }
