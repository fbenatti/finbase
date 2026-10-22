"""Testes da reconstrução histórica (E5)."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.anomaly_retrospective import (
    DEFAULT_BATCH_SIZE,
    HISTORICAL_RUN,
    LC_CONFIG_VERSION_DIFFERS,
    LC_CURRENT_RULES_USED,
    LC_FROM_HISTORICAL_RUN,
    LC_LATER_REVISIONS_POSSIBLE,
    LC_NO_HISTORICAL_RUN,
    LC_NOT_ORIGINAL_FORECAST,
    LC_PLANNING_NOT_PUBLISHED,
    LC_RECONSTRUCTED,
    RECONSTRUCTED,
    UNAVAILABLE,
    assert_cutoff_excludes_month,
    assert_no_planning_published,
    cutoff_before_month,
    limitation_summary,
    month_before,
    month_start,
    plan_reconstruction,
    planning_publication_allowed,
    run_reconstruction_batches,
    simulated_as_of_for,
)


def contract(*, run_id: int, cutoff: str | None, version: int | None = 21, usable: bool = True):
    return SimpleNamespace(
        run_id=run_id,
        cutoff_date=date.fromisoformat(cutoff) if cutoff else None,
        config_version=version,
        generated_at=None,
        is_usable=usable,
    )


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------
def test_corte_antes_do_mes_e_o_ultimo_dia_do_mes_anterior():
    assert cutoff_before_month("202610") == date(2026, 9, 30)
    assert cutoff_before_month("202601") == date(2025, 12, 31)
    assert cutoff_before_month("202603") == date(2026, 2, 28)


def test_simulated_as_of_reproduz_o_corte_desejado():
    # O motor calcula effective_cutoff = simulated_as_of − cutoff_days.
    assert simulated_as_of_for(date(2026, 9, 30), 2) == date(2026, 10, 2)
    assert simulated_as_of_for(date(2026, 9, 30), 2) - timedelta(days=2) == date(2026, 9, 30)


def test_month_before():
    assert month_before("202610") == "202609"
    assert month_before("202601") == "202512"


def test_month_start():
    assert month_start("202610") == date(2026, 10, 1)


# ---------------------------------------------------------------------------
# Planejamento
# ---------------------------------------------------------------------------
def test_prefere_execucao_historica_com_corte_anterior_ao_mes():
    plan = plan_reconstruction(
        ["202609", "202610"],
        contracts=[contract(run_id=395, cutoff="2026-09-10"), contract(run_id=399, cutoff="2026-10-12")],
    )
    # 202609: corte 2026-09-10 é posterior ao início do mês → não serve.
    assert plan.months == ["202609", "202610"]
    assert plan.plans["202609"].kind == RECONSTRUCTED
    # 202610: corte 2026-09-10 é anterior a 2026-10-01 → execução histórica.
    assert plan.plans["202610"].kind == HISTORICAL_RUN
    assert plan.plans["202610"].source_run_id == 395
    assert plan.plans["202610"].cutoff == date(2026, 9, 10)
    assert LC_FROM_HISTORICAL_RUN in plan.plans["202610"].limitation_codes


def test_escolhe_a_execucao_historica_mais_recente():
    plan = plan_reconstruction(
        ["202610"],
        contracts=[
            contract(run_id=380, cutoff="2026-08-31"),
            contract(run_id=390, cutoff="2026-09-30"),
            contract(run_id=399, cutoff="2026-10-12"),
        ],
    )
    assert plan.plans["202610"].source_run_id == 390


def test_reconstrucao_fica_identificada_com_todas_as_limitacoes():
    plan = plan_reconstruction(["202608"], contracts=[contract(run_id=399, cutoff="2026-10-12")])
    month_plan = plan.plans["202608"]
    assert month_plan.kind == RECONSTRUCTED
    assert month_plan.cutoff == date(2026, 7, 31)
    assert month_plan.simulated_as_of == date(2026, 8, 2)
    for code in (
        LC_RECONSTRUCTED,
        LC_CURRENT_RULES_USED,
        LC_LATER_REVISIONS_POSSIBLE,
        LC_NOT_ORIGINAL_FORECAST,
        LC_PLANNING_NOT_PUBLISHED,
    ):
        assert code in month_plan.limitation_codes
    assert "não é a previsão originalmente disponível" in month_plan.notes


def test_reconstrucao_nunca_e_apresentada_como_previsao_original():
    plan = plan_reconstruction(["202608"], contracts=[])
    assert LC_NOT_ORIGINAL_FORECAST in plan.plans["202608"].limitation_codes
    assert plan.plans["202608"].source_run_id is None


def test_versao_de_configuracao_divergente_e_declarada():
    plan = plan_reconstruction(
        ["202610"],
        contracts=[contract(run_id=390, cutoff="2026-09-30", version=18)],
        current_config_version=21,
    )
    assert LC_CONFIG_VERSION_DIFFERS in plan.plans["202610"].limitation_codes


def test_sem_reconstrucao_autorizada_o_mes_fica_indisponivel():
    plan = plan_reconstruction(["202608"], contracts=[], allow_reconstruction=False)
    # Nenhum contrato e nenhuma lista: não há como reconstruir sem o motor.
    assert plan.plans["202608"].kind == UNAVAILABLE
    assert plan.unavailable_months == ["202608"]
    assert LC_NO_HISTORICAL_RUN in plan.limitation_codes


def test_contratos_presentes_mas_nenhum_utilizavel_permite_reconstruir():
    plan = plan_reconstruction(
        ["202608"], contracts=[contract(run_id=1, cutoff=None, usable=False)]
    )
    assert plan.plans["202608"].kind == RECONSTRUCTED


def test_lotes_de_meses():
    plan = plan_reconstruction(
        [f"2026{m:02d}" for m in range(1, 8)], contracts=[], batch_size=3
    )
    assert plan.batches == [["202601", "202602", "202603"], ["202604", "202605", "202606"], ["202607"]]
    assert plan.batch_size == 3
    assert DEFAULT_BATCH_SIZE == 3


def test_plano_serializa_para_auditoria():
    plan = plan_reconstruction(["202608"], contracts=[])
    payload = plan.as_dict()
    assert payload["months"] == ["202608"]
    assert payload["reconstruct_months"] == ["202608"]
    assert payload["plans"]["202608"]["limitation_codes"]
    assert payload["batches"] == [["202608"]]


def test_resumo_legivel_das_limitacoes():
    plan = plan_reconstruction(["202608"], contracts=[])
    summary = limitation_summary(plan)
    assert any("reconstruída" in line for line in summary)
    assert any("Planning" in line for line in summary)


# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------
def test_reconstrucao_nunca_publica_planning():
    assert planning_publication_allowed(simulated_as_of=date(2026, 8, 2)) is False
    assert planning_publication_allowed(simulated_as_of=None) is True

    with pytest.raises(RuntimeError, match="publicou Planning"):
        assert_no_planning_published({"planning_table": "dest_billing_cloud_planning"})
    assert_no_planning_published({"planning_table": None})


def test_corte_efetivo_nao_pode_alcancar_o_mes_reconstruido():
    with pytest.raises(RuntimeError, match="dados posteriores entraram no cálculo"):
        assert_cutoff_excludes_month({"effective_cutoff": "2026-08-15"}, "202608")
    assert assert_cutoff_excludes_month({"effective_cutoff": "2026-07-31"}, "202608") == date(2026, 7, 31)
    with pytest.raises(RuntimeError, match="effective_cutoff"):
        assert_cutoff_excludes_month({}, "202608")


# ---------------------------------------------------------------------------
# Execução em lotes
# ---------------------------------------------------------------------------
class FakeConfig:
    id = 9


def make_reconstructor(*, planning_table=None, override_cutoff=None, fail_for=()):
    calls: list[dict] = []

    async def reconstructor(config, adjustments, cutoff, progress, business_rules, result_table, simulated_as_of):
        calls.append(
            {
                "config_id": config.id,
                "result_table": result_table,
                "simulated_as_of": simulated_as_of,
                "cutoff": cutoff,
            }
        )
        month = f"{simulated_as_of.year:04d}{simulated_as_of.month:02d}"
        if month in fail_for:
            raise RuntimeError(f"falha simulada em {month}")
        effective = override_cutoff or (simulated_as_of - timedelta(days=2))
        return {
            "table": result_table,
            "planning_table": planning_table,
            "effective_cutoff": effective.isoformat(),
            "rows": 42,
        }

    reconstructor.calls = calls  # type: ignore[attr-defined]
    return reconstructor


@pytest.mark.asyncio
async def test_reconstrucao_em_lotes_usa_modo_simulado_e_nao_publica_planning():
    plan = plan_reconstruction(["202606", "202607"], contracts=[])
    reconstructor = make_reconstructor()
    result = await run_reconstruction_batches(
        plan, config=FakeConfig(), reconstructor=reconstructor
    )
    assert result["completed"] == 2
    assert result["planning_published"] is False
    assert result["failed"] == []
    for call in reconstructor.calls:
        assert call["simulated_as_of"] is not None  # modo simulado: sem Planning
        assert call["cutoff"] is None
        assert call["result_table"].startswith("anomaly_ref_9_")
    for batch in result["batches"]:
        assert date.fromisoformat(batch["effective_cutoff"]) < month_start(batch["month"])


@pytest.mark.asyncio
async def test_um_mes_com_falha_nao_derruba_o_lote():
    plan = plan_reconstruction(["202606", "202607", "202608"], contracts=[])
    reconstructor = make_reconstructor(fail_for=("202607",))
    result = await run_reconstruction_batches(
        plan, config=FakeConfig(), reconstructor=reconstructor
    )
    assert result["completed"] == 2
    assert result["failed"][0]["month"] == "202607"


@pytest.mark.asyncio
async def test_reconstrucao_que_publicasse_planning_falha_alto():
    plan = plan_reconstruction(["202606"], contracts=[])
    reconstructor = make_reconstructor(planning_table="dest_billing_cloud_planning")
    result = await run_reconstruction_batches(
        plan, config=FakeConfig(), reconstructor=reconstructor
    )
    assert result["completed"] == 0
    assert "publicou Planning" in result["failed"][0]["error"]


@pytest.mark.asyncio
async def test_reconstrucao_com_corte_invalido_falha_alto():
    plan = plan_reconstruction(["202606"], contracts=[])
    reconstructor = make_reconstructor(override_cutoff=date(2026, 6, 15))
    result = await run_reconstruction_batches(
        plan, config=FakeConfig(), reconstructor=reconstructor
    )
    assert result["completed"] == 0
    assert "dados posteriores" in result["failed"][0]["error"]


@pytest.mark.asyncio
async def test_progresso_e_cancelamento():
    plan = plan_reconstruction([f"2026{m:02d}" for m in range(1, 6)], contracts=[])
    reconstructor = make_reconstructor()
    seen: list[tuple[int, int, str]] = []
    result = await run_reconstruction_batches(
        plan,
        config=FakeConfig(),
        reconstructor=reconstructor,
        progress=lambda done, total, name: seen.append((done, total, name)),
    )
    assert result["completed"] == 5
    assert len(seen) == 5
    assert seen[0][1] == 5

    aborted = await run_reconstruction_batches(
        plan,
        config=FakeConfig(),
        reconstructor=make_reconstructor(),
        should_abort=lambda: True,
    )
    assert aborted.get("aborted") is True
    assert aborted["completed"] == 0


@pytest.mark.asyncio
async def test_cada_mes_usa_sua_propria_tabela_de_resultado():
    plan = plan_reconstruction(["202606", "202607"], contracts=[])
    reconstructor = make_reconstructor()
    await run_reconstruction_batches(plan, config=FakeConfig(), reconstructor=reconstructor)
    tables = [call["result_table"] for call in reconstructor.calls]
    assert len(set(tables)) == 2
