"""Retrospectiva de dados: reconstrução histórica de referências (E5).

Objetivo: obter uma referência para um mês passado **sem** que o realizado
daquele mês participe do cálculo, declarando sempre o que é reconstrução.

Duas origens, nesta ordem de preferência:

1. ``HISTORICAL_RUN`` — execução de forecast **real** cujo corte é anterior ao
   mês. É a melhor evidência: foi de fato produzida na época.
2. ``RECONSTRUCTED`` — reconstrução com o **motor compartilhado**
   (``run_forecast``) em modo simulado: ``simulated_as_of`` é derivado do corte
   desejado, de modo que ``effective_cutoff`` fique no último dia do mês
   anterior. Nesse modo o motor **não publica Planning**.

Garantias implementadas e testadas:

* dados posteriores ao corte **não** entram no cálculo
  (`effective_cutoff = simulated_as_of − cutoff_days`);
* a reconstrução é sempre identificada (`reference_kind = RETROSPECTIVE` e
  `limitation_codes`), e **nunca** apresentada como previsão originalmente
  disponível;
* nenhuma publicação de Planning acontece (`planning_table is None`);
* a leitura é em lotes de meses, nunca o billing inteiro por dia reconstruído.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

#: Origem da referência retrospectiva.
HISTORICAL_RUN = "HISTORICAL_RUN"
RECONSTRUCTED = "RECONSTRUCTED"
UNAVAILABLE = "UNAVAILABLE"

RECONSTRUCTION_KINDS = (HISTORICAL_RUN, RECONSTRUCTED, UNAVAILABLE)

#: Códigos de limitação declarados (plano §2.2 e §E5).
LC_FROM_HISTORICAL_RUN = "FROM_HISTORICAL_RUN"
LC_RECONSTRUCTED = "RECONSTRUCTED_FROM_CURRENT_DATA"
LC_CURRENT_RULES_USED = "CURRENT_RULES_USED"
LC_LATER_REVISIONS_POSSIBLE = "LATER_REVISIONS_POSSIBLE"
LC_NOT_ORIGINAL_FORECAST = "NOT_THE_ORIGINAL_FORECAST"
LC_CONFIG_VERSION_DIFFERS = "CONFIG_VERSION_DIFFERS_FROM_CURRENT"
LC_NO_HISTORICAL_RUN = "NO_HISTORICAL_RUN_BEFORE_MONTH"
LC_PLANNING_NOT_PUBLISHED = "PLANNING_NOT_PUBLISHED"

#: Tamanho do lote de meses reconstruídos por execução.
DEFAULT_BATCH_SIZE = 3


def month_start(month: str) -> date:
    return date(int(month[:4]), int(month[4:6]), 1)


def month_before(month: str) -> str:
    start = month_start(month) - timedelta(days=1)
    return f"{start.year:04d}{start.month:02d}"


def cutoff_before_month(month: str) -> date:
    """Último dia do mês anterior: nenhum dia do mês alvo participa."""
    return month_start(month) - timedelta(days=1)


def simulated_as_of_for(cutoff: date, cutoff_days: int) -> date:
    """``simulated_as_of`` que produz ``effective_cutoff == cutoff``.

    O motor calcula ``effective_cutoff = simulated_as_of − cutoff_days``.
    """
    return cutoff + timedelta(days=max(0, int(cutoff_days)))


@dataclass
class MonthPlan:
    """Como obter a referência de um mês."""

    month: str
    kind: str
    cutoff: date | None
    source_run_id: int | None
    simulated_as_of: date | None
    limitation_codes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "kind": self.kind,
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "source_run_id": self.source_run_id,
            "simulated_as_of": self.simulated_as_of.isoformat() if self.simulated_as_of else None,
            "limitation_codes": list(self.limitation_codes),
            "notes": list(self.notes),
        }


@dataclass
class ReconstructionPlan:
    """Plano de reconstrução: meses, lotes e limitações declaradas."""

    months: list[str]
    plans: dict[str, MonthPlan]
    batches: list[list[str]]
    batch_size: int
    limitation_codes: list[str] = field(default_factory=list)

    @property
    def reconstruct_months(self) -> list[str]:
        return [m for m in self.months if self.plans[m].kind == RECONSTRUCTED]

    @property
    def historical_months(self) -> list[str]:
        return [m for m in self.months if self.plans[m].kind == HISTORICAL_RUN]

    @property
    def unavailable_months(self) -> list[str]:
        return [m for m in self.months if self.plans[m].kind == UNAVAILABLE]

    def as_dict(self) -> dict[str, Any]:
        return {
            "months": list(self.months),
            "batches": [list(batch) for batch in self.batches],
            "batch_size": self.batch_size,
            "historical_months": self.historical_months,
            "reconstruct_months": self.reconstruct_months,
            "unavailable_months": self.unavailable_months,
            "limitation_codes": sorted(set(self.limitation_codes)),
            "plans": {month: plan.as_dict() for month, plan in self.plans.items()},
        }


def plan_reconstruction(
    months: Sequence[str],
    *,
    contracts: Sequence[Any] = (),
    batch_size: int = DEFAULT_BATCH_SIZE,
    cutoff_days: int = 2,
    current_config_version: int | None = None,
    allow_reconstruction: bool = True,
) -> ReconstructionPlan:
    """Decide, por mês, entre execução histórica e reconstrução.

    Prefere sempre uma execução **real** cujo corte seja anterior ao mês alvo —
    ela é, para efeito do mês, a previsão que existia na época. Sem essa
    execução, o mês entra em reconstrução com corte no último dia do mês
    anterior.
    """
    ordered = sorted({str(month) for month in months})
    batch_size = max(1, int(batch_size))
    plans: dict[str, MonthPlan] = {}
    global_codes: set[str] = set()

    usable = [c for c in contracts if getattr(c, "is_usable", False)]

    for month in ordered:
        start = month_start(month)
        historical = [
            c
            for c in usable
            if c.cutoff_date is not None and c.cutoff_date < start
        ]
        if historical:
            chosen = max(
                historical,
                key=lambda c: (
                    c.cutoff_date,
                    getattr(c, "generated_at", None) or date.min,
                    getattr(c, "run_id", 0),
                ),
            )
            codes = [LC_FROM_HISTORICAL_RUN, LC_PLANNING_NOT_PUBLISHED]
            notes = [
                "referência vem de execução real do forecast com corte anterior ao mês",
            ]
            if (
                current_config_version is not None
                and getattr(chosen, "config_version", None) is not None
                and int(chosen.config_version) != int(current_config_version)
            ):
                codes.append(LC_CONFIG_VERSION_DIFFERS)
                notes.append(
                    f"versão da configuração da execução ({chosen.config_version}) "
                    f"difere da atual ({current_config_version})"
                )
            plans[month] = MonthPlan(
                month=month,
                kind=HISTORICAL_RUN,
                cutoff=chosen.cutoff_date,
                source_run_id=int(getattr(chosen, "run_id", 0)) or None,
                simulated_as_of=None,
                limitation_codes=sorted(set(codes)),
                notes=notes,
            )
            global_codes.update(codes)
            continue

        if not allow_reconstruction:
            plans[month] = MonthPlan(
                month=month,
                kind=UNAVAILABLE,
                cutoff=None,
                source_run_id=None,
                simulated_as_of=None,
                limitation_codes=[LC_NO_HISTORICAL_RUN],
                notes=[
                    "não havia execução de forecast com corte anterior ao mês e a "
                    "reconstrução não foi autorizada"
                ],
            )
            global_codes.add(LC_NO_HISTORICAL_RUN)
            continue

        cutoff = cutoff_before_month(month)
        codes = [
            LC_RECONSTRUCTED,
            LC_CURRENT_RULES_USED,
            LC_LATER_REVISIONS_POSSIBLE,
            LC_NOT_ORIGINAL_FORECAST,
            LC_NO_HISTORICAL_RUN,
            LC_PLANNING_NOT_PUBLISHED,
        ]
        plans[month] = MonthPlan(
            month=month,
            kind=RECONSTRUCTED,
            cutoff=cutoff,
            source_run_id=None,
            simulated_as_of=simulated_as_of_for(cutoff, cutoff_days),
            limitation_codes=sorted(set(codes)),
            notes=[
                "reconstruída posteriormente com o motor compartilhado em modo simulado",
                "as regras aplicadas são as atuais, não as da época",
                "a fonte pode conter revisões posteriores ao corte",
                "não é a previsão originalmente disponível",
            ],
        )
        global_codes.update(codes)

    batches = [ordered[i : i + batch_size] for i in range(0, len(ordered), batch_size)]
    return ReconstructionPlan(
        months=ordered,
        plans=plans,
        batches=batches,
        batch_size=batch_size,
        limitation_codes=sorted(global_codes),
    )


def planning_publication_allowed(*, simulated_as_of: date | None) -> bool:
    """A reconstrução **nunca** publica Planning.

    Espelha a guarda do motor (`simulated_as_of is None`) e a torna explícita e
    testável: qualquer reconstrução roda como simulada.
    """
    return simulated_as_of is None


def assert_no_planning_published(result: Mapping[str, Any]) -> None:
    """Confirma que a execução não publicou Planning."""
    planning_table = result.get("planning_table")
    if planning_table:
        raise RuntimeError(
            "reconstrução publicou Planning "
            f"({planning_table}): o plano proíbe alterar a Planning corrente"
        )


def assert_cutoff_excludes_month(result: Mapping[str, Any], month: str) -> date:
    """Confirma que o corte efetivo é anterior ao mês reconstruído."""
    effective = result.get("effective_cutoff")
    if not effective:
        raise RuntimeError("execução de reconstrução não devolveu effective_cutoff")
    cutoff = date.fromisoformat(str(effective)[:10])
    if cutoff >= month_start(month):
        raise RuntimeError(
            f"reconstrução de {month} usou corte {cutoff}: dados posteriores "
            "entraram no cálculo"
        )
    return cutoff


Reconstructor = Callable[..., Awaitable[Mapping[str, Any]]]


async def run_reconstruction_batches(
    plan: ReconstructionPlan,
    *,
    config: Any,
    adjustments: Sequence[Any] = (),
    business_rules: Sequence[Any] = (),
    reconstructor: Reconstructor | None = None,
    result_table_prefix: str = "anomaly_ref",
    progress: Callable[[int, int, str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Executa as reconstruções em lotes de meses.

    Nunca publica Planning e valida, por mês, que o corte efetivo exclui o mês
    reconstruído. A tabela de resultado usa um prefixo próprio para não
    sobrescrever o snapshot de uma execução normal.
    """
    if reconstructor is None:
        from app.services.forecast_service import run_forecast as reconstructor  # type: ignore

    targets = plan.reconstruct_months
    total = len(targets)
    results: dict[str, Any] = {
        "requested": total,
        "completed": 0,
        "failed": [],
        "batches": [],
        "limitation_codes": list(plan.limitation_codes),
        "planning_published": False,
    }

    for index, month in enumerate(targets):
        if should_abort is not None and should_abort():
            results["aborted"] = True
            break
        month_plan = plan.plans[month]
        token = f"{month_plan.simulated_as_of.strftime('%Y%m%d') if month_plan.simulated_as_of else month}"
        table = f"{result_table_prefix}_{config.id}_{token}"
        if progress is not None:
            try:
                progress(index, total, f"Reconstruindo {month}")
            except Exception:  # progresso nunca derruba o processamento
                logger.debug("[anomaly] falha ao reportar progresso", exc_info=True)
        try:
            result = await reconstructor(
                config,
                list(adjustments),
                None,
                None,
                list(business_rules),
                table,
                month_plan.simulated_as_of,
            )
            assert_no_planning_published(result)
            assert_cutoff_excludes_month(result, month)
        except Exception as exc:  # um mês com falha não derruba o lote
            logger.warning("[anomaly] reconstrução de %s falhou: %s", month, exc)
            results["failed"].append({"month": month, "error": str(exc)})
            continue
        results["completed"] += 1
        results["batches"].append(
            {
                "month": month,
                "run_as_of": month_plan.simulated_as_of.isoformat() if month_plan.simulated_as_of else None,
                "effective_cutoff": result.get("effective_cutoff"),
                "table": result.get("table"),
                "rows": result.get("rows"),
                "limitation_codes": month_plan.limitation_codes,
            }
        )

    return results


def limitation_summary(plan: ReconstructionPlan) -> list[str]:
    """Resumo legível das limitações, para a tela de Qualidade (E9)."""
    messages = {
        LC_FROM_HISTORICAL_RUN: "referência de execução real do forecast",
        LC_RECONSTRUCTED: "referência reconstruída posteriormente",
        LC_CURRENT_RULES_USED: "regras atuais aplicadas na reconstrução",
        LC_LATER_REVISIONS_POSSIBLE: "a fonte pode conter revisões posteriores ao corte",
        LC_NOT_ORIGINAL_FORECAST: "não é a previsão originalmente disponível",
        LC_CONFIG_VERSION_DIFFERS: "versão de configuração divergente da atual",
        LC_NO_HISTORICAL_RUN: "não havia execução de forecast antes do mês",
        LC_PLANNING_NOT_PUBLISHED: "Planning corrente não foi alterada",
    }
    return [
        messages.get(code, code) for code in sorted(set(plan.limitation_codes))
    ]
