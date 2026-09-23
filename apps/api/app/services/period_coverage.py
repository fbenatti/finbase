"""Classificação de competência: quando um período pode ser base de comparação.

O dashboard atual soma 17 competências e chama isso de "custo total", mesmo com
escopos e completudes diferentes. Medido em 23/09/2026:

- `azure-4p` / `202504`: 3.763.016 linhas, R$ 209.564,67, **1 dia** de consumo —
  é carga de ajuste, não competência;
- `azure-4p` / `202505`: 1.575.747 linhas **sem região** (R$ 557.990,51);
- `azure-4p` / `202604`–`202607`: 51, 1, 1 e 3 linhas, só `Microsoft.Capacity`;
- `azure-ti` / `202609`: mês em andamento, todas as linhas em 01/09.

O último texto `YYYYMM` **não** é automaticamente o último mês comparável. Este
módulo decide, por `connection_code + billing_period + dataset`, se o período é
`COMPLETE`, `PARTIAL`, `EMPTY`, `MISSING`, `CURRENT_OPEN` ou `NOT_APPLICABLE`, e
— o que importa para o KPI — se ele **pode alimentar comparação** (MoM, run
rate, anomalia, forecast).

O limite não depende só da quantidade de linhas (defeito do plano original):
soma status da execução, janela de dias, dimensões críticas, variação contra o
histórico da mesma conexão e reconciliação (seção 4.2).

Duas distinções que a implementação precisou fazer e que não estavam no plano:

- **presença não é validade.** As linhas de ajuste do 4P têm `resource_id`
  preenchido com ``'320core'``; um teste de "é nulo?" não as encontra;
- **anomalia não é parcialidade.** Um pico legítimo vira aviso e continua
  comparável; excluí-lo do MoM esconderia a variação que se quer investigar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

from app.services.semantic_normalization import connection_key

# --- Estados de cobertura ---------------------------------------------------

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
EMPTY = "EMPTY"
MISSING = "MISSING"
CURRENT_OPEN = "CURRENT_OPEN"
NOT_APPLICABLE = "NOT_APPLICABLE"

CoverageStatus = Literal[
    "COMPLETE", "PARTIAL", "EMPTY", "MISSING", "CURRENT_OPEN", "NOT_APPLICABLE"
]

#: Estados que podem alimentar MoM, run rate, anomalia e forecast.
#:
#: `CURRENT_OPEN` fica **fora**: o mês em andamento pode ser exibido, mas nunca
#: usado silenciosamente como base de tendência (seção 4.2).
COMPARABLE_STATUSES: frozenset[str] = frozenset({COMPLETE})

#: Estados que podem ser exibidos na UI, sempre rotulados.
DISPLAYABLE_STATUSES: frozenset[str] = frozenset(
    {COMPLETE, PARTIAL, CURRENT_OPEN}
)

# --- Motivos de parcialidade (versionados) ----------------------------------

REASON_NO_RUN = "NO_RUN"
REASON_RUN_FAILED = "RUN_FAILED"
REASON_NO_ROWS = "NO_ROWS"
REASON_CURRENT_PERIOD = "CURRENT_PERIOD"
REASON_SHORT_WINDOW = "SHORT_WINDOW"
REASON_PURCHASE_ONLY = "PURCHASE_ONLY"
REASON_MISSING_DIMENSIONS = "MISSING_DIMENSIONS"
REASON_RECONCILIATION = "RECONCILIATION"
REASON_NO_EXPECTED_LOAD = "NO_EXPECTED_LOAD"
REASON_COMPLETE = "OK"

#: Limiares — versionados aqui, nunca espalhados pelo código.
#:
#: `MIN_DAY_COVERAGE` 0,8: um mês com 80 % dos dias cobertos é comparável com
#: ressalva; abaixo disso a janela é curta demais e o valor não representa o mês.
MIN_DAY_COVERAGE = 0.80
#: Tolerância de dimensão crítica: acima disso o período não é COMPLETE.
MAX_MISSING_DIMENSION_RATIO = 0.05
#: Desvio robusto (MAD) a partir do qual o período é outlier contra o histórico.
OUTLIER_MAD_MULTIPLIER = 3.0
#: Divergência Bronze -> Silver acima disso torna o período não confiável.
MAX_RECONCILIATION_RATIO = 0.005
#: Janela em que o mês corrente é considerado "em andamento" e não "atrasado".
CURRENT_OPEN_GRACE_DAYS = 3

#: Versão da regra. Muda quando um limiar muda — o consumidor precisa saber.
COVERAGE_RULE_VERSION = "1.0.0"


@dataclass(frozen=True)
class PeriodFacts:
    """Sinais coletados de um período. Nenhum campo é inferido por omissão.

    A janela é medida por `first_charge_day` / `last_charge_day`, **não** por
    contagem de dias distintos: em 23/09/2026 `charge_period_start` tinha só 10 a
    15 valores distintos por mês (é o dia da fatura, não o dia de consumo), então
    contar dias distintos reprovaria todo mês do 4P. `distinct_charge_days` fica
    como sinal de granularidade esparsa, não como medida de cobertura.
    """

    connection_code: str
    billing_period: str
    dataset: str = "resource_cost"
    has_expected_load: bool = True
    run_status: str | None = None
    row_count: int = 0
    first_charge_day: date | None = None
    last_charge_day: date | None = None
    distinct_charge_days: int = 0
    days_expected: int = 0
    missing_dimension_ratio: float = 0.0
    reconciliation_ratio: float = 0.0
    cost: float | None = None
    cost_missing_dimension: float | None = None
    #: Custo sem tag de alocação. Medida própria (custo não alocado), **não**
    #: motivo de parcialidade — a maioria dos recursos de plataforma não tem tag.
    tag_missing_ratio: float = 0.0
    cost_without_tag: float | None = None
    #: Histórico dos **desvios** de cada mês contra a mediana das competências
    #: comparáveis da mesma conexão. É um resíduo, não um custo: o custo tem
    #: sazonalidade real e o resíduo não.
    residual_history_median: float | None = None
    residual_history_mad: float | None = None
    residual_history_n: int = 0
    age_days: int | None = None
    freshness_at: datetime | None = None
    is_current_period: bool = False
    #: `True` quando **todas** as linhas são de compra/ajuste
    #: (`Microsoft.Capacity` ou `charge_category='Purchase'`), isto é, o período
    #: não tem operação — é uma ordem de reserva, não um mês.
    purchase_only: bool = False

    @property
    def days_covered(self) -> int:
        """Dias cobertos pela janela observada, limitados ao mês.

        A extensão é `último dia − primeiro dia + 1`. `202505` cobre 01/05 a
        28/05 = 28 dias; `202504` cobre 01/04 a 01/04 = 1 dia.
        """
        if self.first_charge_day is None or self.last_charge_day is None:
            return 0
        extensao = (self.last_charge_day - self.first_charge_day).days + 1
        return max(0, extensao)

    @property
    def day_coverage(self) -> float:
        """Fração de dias do mês dentro da janela observada. Sem expectativa, 0."""
        if self.days_expected <= 0:
            return 0.0
        return min(1.0, self.days_covered / self.days_expected)


@dataclass(frozen=True)
class PeriodCoverage:
    """Resultado da classificação de um período."""

    connection_code: str
    billing_period: str
    dataset: str
    status: str
    reason: str
    day_coverage: float = 0.0
    missing_dimension_ratio: float = 0.0
    row_count: int = 0
    cost: float | None = None
    tag_missing_ratio: float = 0.0
    rule_version: str = COVERAGE_RULE_VERSION
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def comparable(self) -> bool:
        """``True`` só quando o período pode alimentar comparação temporal.

        É a única pergunta que o consumidor deveria fazer. Usar
        ``status == COMPLETE`` direto em cada tela é como o defeito volta.
        """
        return self.status in COMPARABLE_STATUSES

    @property
    def displayable(self) -> bool:
        """``True`` quando a UI pode mostrar o período — com rótulo de estado."""
        return self.status in DISPLAYABLE_STATUSES

    def as_row(self) -> dict[str, Any]:
        """Linha pronta para a Gold (`finops_period_coverage`)."""
        return {
            "connection_code": self.connection_code,
            "billing_period": self.billing_period,
            "dataset": self.dataset,
            "status": self.status,
            "reason": self.reason,
            "day_coverage": round(self.day_coverage, 6),
            "missing_dimension_ratio": round(self.missing_dimension_ratio, 6),
            "tag_missing_ratio": round(self.tag_missing_ratio, 6),
            "row_count": self.row_count,
            "cost": self.cost,
            "comparable": self.comparable,
            "displayable": self.displayable,
            "rule_version": self.rule_version,
            "warnings": list(self.warnings),
        }


def days_in_period(billing_period: str) -> int:
    """Dias do mês de uma competência `YYYYMM`. Zero quando inválida."""
    try:
        ano, mes = int(billing_period[:4]), int(billing_period[4:6])
    except (TypeError, ValueError):
        return 0
    if not (1 <= mes <= 12):
        return 0
    if mes == 12:
        return 31
    return (date(ano, mes + 1, 1) - date(ano, mes, 1)).days


def _is_outlier(facts: PeriodFacts) -> bool:
    """Variação anômala contra o histórico da **mesma conexão**.

    A referência é a **mediana das competências comparáveis** da conexão, e a
    escala é a MAD dos resíduos de cada mês contra essa mediana — não a MAD do
    custo absoluto. Medido em 23/09/2026, o 4P tem mínimo de R$ 2,06 M (202509) e
    máximo de R$ 3,83 M (202601). Usar a MAD do custo absoluto reprovava 202509
    como outlier mesmo sendo mês operacional completo. O resíduo é estável nos
    meses normais e extremo nos meses de ajuste — que é o que se quer detectar.

    Exige pelo menos 3 resíduos no histórico. Sem histórico suficiente não há
    outlier: não se inventa referência.
    """
    if facts.cost is None or facts.residual_history_median is None:
        return False
    if facts.residual_history_n < 3 or not facts.residual_history_mad:
        return False
    residuo = abs(facts.cost - facts.residual_history_median)
    return residuo > OUTLIER_MAD_MULTIPLIER * facts.residual_history_mad


def classify_period(facts: PeriodFacts) -> PeriodCoverage:
    """Classifica um período. Regra determinística, na ordem documentada.

    A ordem importa: um período sem carga esperada nunca é `MISSING`; um mês em
    andamento nunca é `COMPLETE`; falha de execução tem precedência sobre
    qualquer análise de conteúdo, porque sem execução confiável o conteúdo não
    é evidência.

    **Anomalia não é parcialidade.** Um valor atípico contra o histórico vira
    `warnings` e o período continua comparável: é sinal de negócio a explicar, não
    prova de carga ruim. Já janela curta, dimensão ausente e falha de execução são
    defeitos determinísticos de medição — esses tornam o período `PARTIAL`, porque
    o número não representa o mês.
    """
    warnings: list[str] = []
    base = dict(
        connection_code=facts.connection_code,
        billing_period=facts.billing_period,
        dataset=facts.dataset,
        day_coverage=facts.day_coverage,
        missing_dimension_ratio=facts.missing_dimension_ratio,
        tag_missing_ratio=facts.tag_missing_ratio,
        row_count=facts.row_count,
        cost=facts.cost,
    )

    def result(status: str, reason: str, **extra: Any) -> PeriodCoverage:
        return PeriodCoverage(status=status, reason=reason, warnings=tuple(warnings), **base, **extra)

    # 1. A fonte não deveria produzir este período.
    if not facts.has_expected_load:
        return result(NOT_APPLICABLE, REASON_NO_EXPECTED_LOAD)

    if facts.row_count <= 0:
        # 2. Sem execução registrada e sem linhas: não sabemos se faltou rodar.
        if facts.run_status is None:
            return result(MISSING, REASON_NO_RUN)
        # 3. Execução válida com zero linhas é um estado legítimo, não um erro.
        if str(facts.run_status).upper() in {"SUCCESS", "UPDATED", "OK", "COMPLETE"}:
            return result(EMPTY, REASON_NO_ROWS)
        return result(MISSING, REASON_NO_RUN)

    # 4. Falha de execução tem precedência: o conteúdo não é evidência.
    if facts.run_status is not None and str(facts.run_status).upper() in {
        "FAILED",
        "ERROR",
        "PARTIAL",
        "CANCELLED",
    }:
        return result(PARTIAL, REASON_RUN_FAILED)

    # 5. Mês em andamento só é `CURRENT_OPEN` dentro da janela de graça.
    if facts.is_current_period:
        if facts.age_days is None or facts.age_days <= CURRENT_OPEN_GRACE_DAYS:
            return result(CURRENT_OPEN, REASON_CURRENT_PERIOD)
        warnings.append(
            f"mês corrente sem atualização há {facts.age_days} dias"
        )

    # 6. Janela curta: menos de 80 % dos dias do mês estão na janela observada.
    if facts.day_coverage < MIN_DAY_COVERAGE:
        return result(PARTIAL, REASON_SHORT_WINDOW)

    # 7. Dimensão crítica ausente acima da tolerância.
    if facts.missing_dimension_ratio > MAX_MISSING_DIMENSION_RATIO:
        return result(PARTIAL, REASON_MISSING_DIMENSIONS)

    # 8. Reconciliação Bronze -> Silver divergente: a camada não fecha.
    if facts.reconciliation_ratio > MAX_RECONCILIATION_RATIO:
        return result(PARTIAL, REASON_RECONCILIATION)

    # 9. Só linhas de compra/ajuste: o período é uma ordem de reserva, não um mês
    #    operacional. Motivo próprio porque a leitura é diferente de janela curta:
    #    o mês pode estar 100 % "coberto" e ainda assim não ser comparável.
    if facts.purchase_only:
        return result(PARTIAL, REASON_PURCHASE_ONLY)

    # 10. Sinais que **não** invalidam o período, mas precisam ser vistos.
    if _is_outlier(facts):
        warnings.append(
            "variacao atipica contra o historico da conexao: explicar antes de usar como tendencia"
        )
    if facts.tag_missing_ratio > 0.20:
        warnings.append(f"{facts.tag_missing_ratio:.1%} das linhas sem tag de alocacao")
    if facts.reconciliation_ratio > 0:
        warnings.append(f"delta de reconciliacao: {facts.reconciliation_ratio:.4%}")

    return result(COMPLETE, REASON_COMPLETE)


def classify_all(facts_list: list[PeriodFacts]) -> list[PeriodCoverage]:
    """Classifica vários períodos, calculando o histórico por conexão.

    A referência de normalidade de cada conexão é a mediana das competências
    **operacionais** dela (com linhas e sem ser mês de compra/ajuste), e a escala
    é a MAD dos resíduos. É o que impede 202504 e 202505 — cargas de ajuste — de
    definirem o que é normal para o 4P.
    """
    por_conexao: dict[str, list[PeriodFacts]] = {}
    for facts in facts_list:
        por_conexao.setdefault(facts.connection_code, []).append(facts)

    # Referência por conexão: mediana dos custos operacionais.
    referencia: dict[str, tuple[float, float, int]] = {}
    for conexao, irmaos in por_conexao.items():
        custos = sorted(
            f.cost
            for f in irmaos
            if f.cost is not None and f.row_count > 0 and not f.purchase_only
        )
        if len(custos) < 4:
            continue
        mediana = _median(custos)
        residuos = sorted(abs(c - mediana) for c in custos)
        referencia[conexao] = (mediana, _median(residuos), len(residuos))

    resultado: list[PeriodCoverage] = []
    for facts in facts_list:
        enriquecido = facts
        if (
            facts.residual_history_median is None
            and facts.connection_code in referencia
        ):
            mediana, mad, n = referencia[facts.connection_code]
            enriquecido = _replace(
                facts,
                residual_history_median=mediana,
                residual_history_mad=mad,
                residual_history_n=n,
            )
        resultado.append(classify_period(enriquecido))
    return resultado


def _median(sorted_values: list[float]) -> float:
    """Mediana de uma lista já ordenada e não vazia."""
    n = len(sorted_values)
    meio = n // 2
    if n % 2:
        return float(sorted_values[meio])
    return (float(sorted_values[meio - 1]) + float(sorted_values[meio])) / 2.0


def _replace(facts: PeriodFacts, **changes: Any) -> PeriodFacts:
    """Cópia de `PeriodFacts` com campos trocados (frozen dataclass)."""
    from dataclasses import replace

    return replace(facts, **changes)


def comparable_pairs(
    coverage: list[PeriodCoverage],
) -> list[tuple[PeriodCoverage, PeriodCoverage]]:
    """Pares consecutivos **da mesma conexão e dataset** que podem ir a MoM.

    Só devolve pares em que ambos os lados são comparáveis. É o que impede
    `202504 → 202505` e `202603 → 202604` de virarem tendência: comparar um mês
    de ajuste de 1 dia com um mês operacional produz uma variação sem significado.
    """
    agrupado: dict[tuple[str, str], list[PeriodCoverage]] = {}
    for item in coverage:
        if not item.comparable:
            continue
        agrupado.setdefault((item.connection_code, item.dataset), []).append(item)

    pares: list[tuple[PeriodCoverage, PeriodCoverage]] = []
    for itens in agrupado.values():
        ordenado = sorted(itens, key=lambda c: c.billing_period)
        pares.extend(zip(ordenado, ordenado[1:]))
    return pares


def month_over_month(previous: PeriodCoverage, current: PeriodCoverage) -> dict[str, Any]:
    """Variação percentual entre dois períodos comparáveis.

    Recusa a comparação quando qualquer um dos lados não é comparável — devolve
    ``comparable: False`` com o motivo, em vez de um número enganoso.
    """
    if not previous.comparable or not current.comparable:
        return {
            "comparable": False,
            "reason": "periodo_nao_comparavel",
            "previous_status": previous.status,
            "current_status": current.status,
        }
    if previous.connection_code != current.connection_code:
        return {
            "comparable": False,
            "reason": "conexoes_diferentes",
            "previous_connection": previous.connection_code,
            "current_connection": current.connection_code,
        }
    if previous.cost is None or current.cost is None:
        return {"comparable": False, "reason": "custo_ausente"}
    if previous.cost == 0:
        return {
            "comparable": False,
            "reason": "base_zero",
            "current_cost": current.cost,
        }
    delta = current.cost - previous.cost
    return {
        "comparable": True,
        "previous_period": previous.billing_period,
        "current_period": current.billing_period,
        "previous_cost": previous.cost,
        "current_cost": current.cost,
        "delta": round(delta, 2),
        "delta_pct": round(100.0 * delta / previous.cost, 4),
    }


def run_rate_annualized(coverage: list[PeriodCoverage]) -> dict[str, Any]:
    """Run rate anualizado, sempre informando **quantos meses** o sustentam.

    Um run rate calculado sobre 1 mês não é comparável a um calculado sobre 12.
    O número de meses é parte do resultado, não um detalhe de rodapé.
    """
    comparaveis = [c for c in coverage if c.comparable and c.cost is not None]
    if not comparaveis:
        return {"available": False, "reason": "sem_periodo_comparavel", "months_used": 0}
    conexoes = {c.connection_code for c in comparaveis}
    if len(conexoes) > 1:
        return {
            "available": False,
            "reason": "multiplas_conexoes",
            "connections": sorted(conexoes),
            "months_used": 0,
        }
    total = sum(c.cost for c in comparaveis)  # type: ignore[misc]
    meses = len(comparaveis)
    media = total / meses
    return {
        "available": True,
        "connection_code": comparaveis[0].connection_code,
        "months_used": meses,
        "average_monthly": round(media, 2),
        "annualized": round(media * 12, 2),
        "periods": sorted(c.billing_period for c in comparaveis),
    }


def freshness_of(rows: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Freshness a partir de timestamps de processamento (`_processed_at`).

    Distingue "sem timestamp" de "timestamp antigo": ausência de evidência de
    atualização não é o mesmo que atraso comprovado.
    """
    agora = now or datetime.now(timezone.utc)
    instantes: list[datetime] = []
    sem_timestamp = 0
    for row in rows:
        bruto = row.get("last_processed_at") or row.get("freshness_at")
        if not bruto:
            sem_timestamp += 1
            continue
        if isinstance(bruto, datetime):
            instante = bruto
        else:
            try:
                instante = datetime.fromisoformat(str(bruto).replace("Z", "+00:00"))
            except ValueError:
                sem_timestamp += 1
                continue
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)
        instantes.append(instante)
    if not instantes:
        return {
            "available": False,
            "reason": "sem_timestamp_de_processamento",
            "rows_without_timestamp": sem_timestamp,
        }
    ultimo = max(instantes)
    return {
        "available": True,
        "freshness_at": ultimo.isoformat(),
        "age_days": (agora - ultimo).days,
        "rows_without_timestamp": sem_timestamp,
    }


# ── Coleta dos sinais a partir da Silver ─────────────────────────────────────
#
# A competência usa `COALESCE(_billing_period, billing_period)`: em 23/09/2026 as
# duas colunas existem e concordam em 13.519.512/13.519.512 linhas, mas
# `_billing_period` é a coluna de controle da Silver e `billing_period` a do
# FOCUS. Preferir a de controle mantém a leitura estável se uma origem nova
# preencher só uma delas.

_PERIOD_EXPR = "COALESCE(_billing_period, billing_period)"

#: Dimensões críticas para C1/C2/C11, com a condição que prova a **validade** do
#: valor — não apenas a presença. Presença não é validade: as linhas de ajuste do
#: `azure-4p` têm `resource_id` preenchido com ``'320core'``.
def _dimension_absent_sql() -> str:
    """Condição SQL que é verdadeira quando a linha não tem dimensão utilizável.

    Cada dimensão tem sua própria definição de "utilizável":

    - `service_name`: textual presente;
    - `region_id`: textual presente **ou** cobrança global comprovada — reserva
      sem região não é limitação de dado;
    - `sub_account_id`: precisa ter forma de identificador de assinatura (GUID ou
      equivalente), não um rótulo curto de carga;
    - `resource_id`: precisa ser caminho de recurso de provedor (``/…/providers/…``),
      não um identificador sintético como ``'96core'``;
    - `tags_json`: presente e não vazio. `'{}'` é ausência de tag, não tag.
    """
    condicoes = [
        "NULLIF(TRIM(CAST(service_name AS VARCHAR)), '') IS NULL",
        (
            "NULLIF(TRIM(CAST(region_id AS VARCHAR)), '') IS NULL "
            "AND REGEXP_REPLACE(LOWER(TRIM(COALESCE(CAST(service_name AS VARCHAR), ''))), "
            "'\\s+', '') NOT IN ('microsoft.capacity') "
            "AND LOWER(TRIM(COALESCE(CAST(charge_category AS VARCHAR), ''))) "
            "NOT IN ('purchase', 'refund', 'tax', 'credit', 'roundingadjustment')"
        ),
        (
            "NULLIF(TRIM(CAST(sub_account_id AS VARCHAR)), '') IS NULL "
            "OR LENGTH(TRIM(CAST(sub_account_id AS VARCHAR))) < 8"
        ),
        (
            "NULLIF(TRIM(CAST(resource_id AS VARCHAR)), '') IS NULL "
            "OR NOT (LOWER(TRIM(CAST(resource_id AS VARCHAR))) LIKE '/%' "
            "AND LOWER(TRIM(CAST(resource_id AS VARCHAR))) LIKE '%/providers/%')"
        ),
    ]
    return " OR ".join(f"({c})" for c in condicoes)


#: Condição SQL de ausência de tag de alocação.
#:
#: **Não** entra em `rows_missing_dimension`. Medido em 23/09/2026: 5.677.656 de
#: 13.519.512 linhas não têm tag, porque a maioria dos recursos de plataforma
#: (storage, rede, licença) legitimamente não tem tag de alocação. Tratar isso
#: como dimensão crítica reprovaria 12 dos 16 meses do 4P e esconderia o defeito
#: real (as linhas de ajuste sem serviço/região/conta). Tag ausente é medida
#: própria de "custo não alocado" (seção 4.3), não de completude de competência.
_ALLOCATION_TAG_ABSENT_SQL = (
    "NULLIF(TRIM(CAST(tags_json AS VARCHAR)), '') IS NULL "
    "OR TRIM(CAST(tags_json AS VARCHAR)) IN ('{}', '[]')"
)


#: Condição SQL reutilizada nas duas medidas de dimensão ausente.
_DIM_ABSENT = _dimension_absent_sql()


def coverage_sql(
    *,
    dataset: str = "resource_cost",
    layer: str = "silver",
    connection_filter: list[str] | None = None,
) -> str:
    """SQL que coleta os sinais de cobertura por `conexão + competência`.

    Sinais por par `conexão + competência`:

    - `row_count` — volume da carga;
    - `first_charge_day` / `last_charge_day` — extensão real da janela. Não é
      `COUNT(DISTINCT data)`: medido em 23/09/2026, `charge_period_start` tem só
      10 a 15 valores distintos por mês (é o dia da fatura, não o dia de
      consumo), então contar dias distintos reprovaria todo mês;
    - `distinct_charge_days` — quantos dias distintos existem, para sinalizar
      granularidade esparsa sem confundir isso com janela curta;
    - `rows_missing_dimension` / `cost_missing_dimension` — custo que não se
      explica, pela definição de validade de cada dimensão;
    - `purchase_rows` — linhas de compra/ajuste, para separar mês operacional de
      mês de ordem de reserva;
    - `last_processed_at` — freshness.
    """
    conn = "COALESCE(_source_connection_code, _source_connection_name)"
    filtro = ""
    if connection_filter:
        lista = ", ".join("'" + str(c).replace("'", "''") + "'" for c in connection_filter)
        filtro = f"WHERE {conn} IN ({lista})"
    return f"""
SELECT
    {conn} AS connection_code,
    CAST({_PERIOD_EXPR} AS VARCHAR) AS billing_period,
    COUNT(*) AS row_count,
    MIN(DATE(charge_period_start)) AS first_charge_day,
    MAX(DATE(charge_period_start)) AS last_charge_day,
    COUNT(DISTINCT DATE(charge_period_start)) AS distinct_charge_days,
    ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)), 2) AS cost,
    ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)) FILTER (WHERE {_DIM_ABSENT}), 2)
        AS cost_missing_dimension,
    COUNT(*) FILTER (WHERE {_DIM_ABSENT}) AS rows_missing_dimension,
    COUNT(*) FILTER (WHERE {_ALLOCATION_TAG_ABSENT_SQL}) AS rows_without_tag,
    ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)) FILTER (WHERE {_ALLOCATION_TAG_ABSENT_SQL}), 2)
        AS cost_without_tag,
    COUNT(*) FILTER (
        WHERE LOWER(TRIM(CAST(service_name AS VARCHAR))) = 'microsoft.capacity'
           OR LOWER(TRIM(CAST(charge_category AS VARCHAR))) = 'purchase'
    ) AS purchase_rows,
    MAX(CAST(_processed_at AS VARCHAR)) AS last_processed_at
FROM iceberg.{layer}.{dataset}
{filtro}
GROUP BY 1, 2
ORDER BY 1, 2
""".strip()


def _to_float(value: Any) -> float | None:
    """Converte o que o Trino devolve (decimal chega como string) para float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def facts_from_row(
    row: dict[str, Any],
    *,
    dataset: str = "resource_cost",
    run_status: str | None = None,
    reconciliation_ratio: float = 0.0,
    runs_by_connection_period: dict[tuple[str, str], str] | None = None,
    today: date | None = None,
) -> PeriodFacts:
    """Converte uma linha de :func:`coverage_sql` em `PeriodFacts`.

    Tudo o que não vier da Silver entra explicitamente como parâmetro —
    `run_status`, reconciliação e histórico. Nada é inferido por omissão.
    """
    conexao = connection_key(row.get("connection_code"), row.get("connection_name"))
    periodo = str(row.get("billing_period") or "")
    linhas = int(row.get("row_count") or 0)
    dimensao_ausente = int(row.get("rows_missing_dimension") or 0)
    compras = int(row.get("purchase_rows") or 0)

    hoje = today or datetime.now(timezone.utc).date()
    esperado = days_in_period(periodo)
    mes_corrente = periodo == hoje.strftime("%Y%m") if esperado else False
    atraso = _age_days(row.get("last_processed_at"), hoje=hoje)

    status_run = run_status
    if status_run is None and runs_by_connection_period:
        status_run = runs_by_connection_period.get((conexao, periodo))

    return PeriodFacts(
        connection_code=conexao,
        billing_period=periodo,
        dataset=dataset,
        has_expected_load=True,
        run_status=status_run,
        row_count=linhas,
        first_charge_day=_as_date(row.get("first_charge_day")),
        last_charge_day=_as_date(row.get("last_charge_day")),
        distinct_charge_days=int(row.get("distinct_charge_days") or 0),
        days_expected=esperado,
        missing_dimension_ratio=(dimensao_ausente / linhas) if linhas else 0.0,
        reconciliation_ratio=reconciliation_ratio,
        cost=_to_float(row.get("cost")),
        cost_missing_dimension=_to_float(row.get("cost_missing_dimension")),
        tag_missing_ratio=((int(row.get("rows_without_tag") or 0)) / linhas) if linhas else 0.0,
        cost_without_tag=_to_float(row.get("cost_without_tag")),
        age_days=atraso,
        is_current_period=mes_corrente,
        # `purchase_only` exige **todas** as linhas de compra/ajuste. Um mês
        # operacional com 27 linhas de compra entre 656.168 linhas não é um mês
        # de compra — é um mês com compra dentro dele.
        purchase_only=bool(linhas) and compras == linhas,
    )


def _as_date(value: Any) -> date | None:
    """Converte o que o Trino devolve em `date`, sem inventar valor.

    O Trino devolve `date` como `datetime.date`, mas o transporte REST pode
    entregar `'2025-05-01'`. Qualquer outra forma devolve ``None`` — janela
    desconhecida, nunca janela total.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _age_days(value: Any, *, hoje: date) -> int | None:
    """Dias desde o último processamento. ``None`` quando não há timestamp.

    O `_processed_at` é `varchar` na Silver e pode chegar como ISO com sufixo
    `Z`/offset ou como `YYYY-MM-DD HH:MM:SS`. Formatos não reconhecidos devolvem
    ``None`` — atraso desconhecido, nunca zero.
    """
    if not value:
        return None
    texto = str(value).strip()
    for tentativa in (texto.replace("Z", "+00:00"), texto.split(".")[0], texto[:10]):
        try:
            instante = datetime.fromisoformat(tentativa)
        except ValueError:
            continue
        if instante.tzinfo is not None:
            instante = instante.astimezone(timezone.utc).replace(tzinfo=None)
        return max(0, (hoje - instante.date()).days)
    return None


async def collect_coverage(
    *,
    dataset: str = "resource_cost",
    layer: str = "silver",
    connection_filter: list[str] | None = None,
    runs_by_connection_period: dict[tuple[str, str], str] | None = None,
    reconciliation: dict[tuple[str, str], float] | None = None,
    timeout_s: float = 900.0,
    today: date | None = None,
) -> list[PeriodCoverage]:
    """Coleta e classifica a cobertura de todos os períodos do dataset.

    É o produtor de `finops_period_coverage` (C11). Devolve a classificação; a
    materialização na Gold acontece no materializador governado (G2).
    """
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(
        coverage_sql(dataset=dataset, layer=layer, connection_filter=connection_filter),
        timeout_s=timeout_s,
    )
    fatos = [
        facts_from_row(
            row,
            dataset=dataset,
            runs_by_connection_period=runs_by_connection_period,
            reconciliation_ratio=(reconciliation or {}).get(
                (str(row.get("connection_code")), str(row.get("billing_period"))), 0.0
            ),
            today=today,
        )
        for row in linhas or []
    ]
    return classify_all(fatos)


# ── Guarda de reconciliação: normalizar não muda total ───────────────────────


def reconciliation_sql(
    *,
    dataset: str = "resource_cost",
    layer: str = "silver",
    dimensions: dict[str, str] | None = None,
) -> str:
    """SQL que prova que agrupar por chave normalizada não altera o total.

    Devolve, para cada competência: total bruto, total agrupado pela chave
    normalizada, número de linhas de cada lado e quantas linhas ficaram de fora do
    agrupamento. Um `grouped_total` diferente de `raw_total`, ou
    `rows_grouped` diferente de `rows_raw`, significa que a normalização perdeu ou
    multiplicou linha — e o cubo não pode ser publicado.

    `dimensions` mapeia nome → expressão SQL já normalizada. Sem dimensões, agrupa
    só pela competência, o que ainda prova a preservação do total global.
    """
    if dimensions:
        chaves = ", ".join(f"{expr} AS {nome}" for nome, expr in dimensions.items())
        agrupamento = f"SELECT {_PERIOD_EXPR} AS bp, {chaves}, TRY_CAST(effective_cost AS DOUBLE) AS eff\nFROM iceberg.{layer}.{dataset}"
        group_by = ", ".join(["bp"] + list(dimensions))
        agrupado = f"SELECT bp, SUM(eff) AS grouped_total, COUNT(*) AS rows_grouped FROM ({agrupamento}) GROUP BY {group_by}"
    else:
        agrupado = (
            f"SELECT {_PERIOD_EXPR} AS bp, SUM(TRY_CAST(effective_cost AS DOUBLE)) AS grouped_total, "
            f"COUNT(*) AS rows_grouped FROM iceberg.{layer}.{dataset} GROUP BY 1"
        )
    return f"""
WITH bruto AS (
    SELECT CAST({_PERIOD_EXPR} AS VARCHAR) AS bp,
           ROUND(SUM(TRY_CAST(effective_cost AS DOUBLE)), 2) AS raw_total,
           COUNT(*) AS rows_raw
    FROM iceberg.{layer}.{dataset}
    GROUP BY 1
),
agrupado AS (
    SELECT CAST(bp AS VARCHAR) AS bp,
           ROUND(SUM(grouped_total), 2) AS grouped_total,
           SUM(rows_grouped) AS rows_grouped
    FROM ({agrupado})
    GROUP BY 1
)
SELECT b.bp,
       b.raw_total,
       a.grouped_total,
       b.rows_raw,
       a.rows_grouped,
       ROUND(COALESCE(a.grouped_total, 0) - b.raw_total, 2) AS total_delta,
       COALESCE(a.rows_grouped, 0) - b.rows_raw AS rows_delta
FROM bruto b
LEFT JOIN agrupado a ON a.bp = b.bp
ORDER BY b.bp
""".strip()


#: Tolerância de reconciliação por competência, em reais (seção 5, C1).
RECONCILIATION_TOLERANCE_BRL = 0.01


def reconciliation_ok(rows: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Avalia o resultado de :func:`reconciliation_sql`.

    Devolve `(ok, problemas)`. `ok` só é `True` quando **toda** competência fecha
    dentro de `RECONCILIATION_TOLERANCE_BRL` e a contagem de linhas é idêntica.

    Delta de linhas diferente de zero é sempre falha, mesmo com total igual:
    significaria que o agrupamento perdeu ou duplicou linha e o total fechou por
    coincidência.
    """
    problemas: list[str] = []
    for row in rows or []:
        bp = row.get("bp")
        total_delta = _to_float(row.get("total_delta")) or 0.0
        rows_delta = int(row.get("rows_delta") or 0)
        if abs(total_delta) > RECONCILIATION_TOLERANCE_BRL:
            problemas.append(f"{bp}: delta de custo R$ {total_delta:.2f}")
        if rows_delta != 0:
            problemas.append(f"{bp}: delta de linhas {rows_delta}")
    return (not problemas, problemas)


async def verify_reconciliation(
    *,
    dataset: str = "resource_cost",
    layer: str = "silver",
    dimensions: dict[str, str] | None = None,
    timeout_s: float = 900.0,
) -> dict[str, Any]:
    """Executa a guarda de reconciliação e devolve o veredito.

    É o teste que o aceite de G1 exige — "nenhum total financeiro muda por
    normalização" — expresso como verificação executável, não como afirmação.
    """
    from app.services.trino_service import execute as trino_execute

    linhas = await trino_execute(
        reconciliation_sql(dataset=dataset, layer=layer, dimensions=dimensions),
        timeout_s=timeout_s,
    )
    ok, problemas = reconciliation_ok(linhas or [])
    return {
        "ok": ok,
        "problems": problemas,
        "periods": len(linhas or []),
        "dimensions": sorted(dimensions) if dimensions else [],
        "rows": linhas or [],
    }
