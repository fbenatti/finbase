"""Testes da decomposição executiva de custo Aura Brain / Vivo Mais (Gold)."""

from datetime import date

import pytest

from app.services import aura_brain_vivo_mais as dashboard_service

from app.services.aura_brain_vivo_mais import (
    classify_dimon_row,
    ia_family_for,
    _empty_classes,
    _infra_by_subcategory,
    _last_closed_period,
    _mom_pct,
    _monthly_series,
    _ondemand_ptu_estimate,
    _period_label,
)


def _row(**kw):
    base = {"period": "202608", "project": "Aura Brain", "tipo": "realizado",
            "chargecategory": "Usage", "cost_class": "infra",
            "servicesubcategory": "Virtual Machines FSv2 Series", "realized": 100.0,
            "is_ondemand_ptu": 0, "ia_family": None}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_vivo_mais_queries_its_own_dimon_and_normalizes_project(monkeypatch):
    captured = []

    async def fake_execute(sql, **kwargs):
        captured.append(sql)
        return []

    monkeypatch.setattr(dashboard_service, "trino_execute", fake_execute)

    await dashboard_service._query_cost_rows("vivo-mais", None)
    await dashboard_service._query_subaccounts("vivo-mais", "202607", None)

    assert len(captured) == 2
    for sql in captured:
        assert "iceberg.gold_mart.dest_dm_cost_custo_vivo_mais" in sql
        assert "lower(CAST(departamento_nivel_5 AS VARCHAR)) = 'vivo+'" in sql
    assert "'Vivo Mais' AS project" in captured[0]


@pytest.mark.asyncio
async def test_aura_brain_keeps_combined_gold(monkeypatch):
    captured = []

    async def fake_execute(sql, **kwargs):
        captured.append(sql)
        return []

    monkeypatch.setattr(dashboard_service, "trino_execute", fake_execute)
    await dashboard_service._query_cost_rows("aura-brain", None)

    assert "iceberg.gold.dest_dm_cost_custo_aura_brain_vivo_mais" in captured[0]
    assert "lower(CAST(departamento_nivel_5 AS VARCHAR)) = 'aura brain'" in captured[0]


# ── classificação ──────────────────────────────────────────────────────────

def test_classify_on_demand_monthly_purchase_is_ptu():
    row = {"skumeter": "Provisioned Throughput Units", "chargecategory": "Usage",
           "servicesubcategory": "Azure OpenAI", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "ptu"


def test_classify_reservation_purchase_is_ptu():
    row = {"skumeter": "Provisioned Managed Global Unit", "chargecategory": "Purchase",
           "servicesubcategory": "Azure AI Foundry Provisioned Throughput Reservation",
           "servicename": "Microsoft.Capacity"}
    assert classify_dimon_row(row) == "ptu"


def test_classify_reservation_refund_is_ptu():
    row = {"skumeter": None, "chargecategory": "Refund",
           "servicesubcategory": "Azure AI Foundry Provisioned Throughput Reservation",
           "servicename": "Microsoft.Capacity"}
    assert classify_dimon_row(row) == "ptu"


def test_classify_daily_consumption_is_excluded():
    # Consumo diário/horário (1/Hour) — já está na compra mensal; não retorna.
    row = {"skumeter": "Provisioned Managed Global Unit", "chargecategory": "Usage",
           "servicesubcategory": "Azure OpenAI", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "__excluded__"


def test_classify_daily_consumption_return_is_excluded():
    row = {"skumeter": "Provisioned Managed Global Unit", "chargecategory": "Return",
           "servicesubcategory": "Azure OpenAI", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "__excluded__"


def test_classify_payg_token_meters_are_ondemand():
    for meter in ("gpt 4o 0513 Input global Tokens", "embedding-ada-regional Tokens",
                  "Claude in Microsoft Foundry (Anthropic hosted) - claude-consumption-units"):
        row = {"skumeter": meter, "chargecategory": "Usage",
               "servicesubcategory": "Azure OpenAI", "servicename": "Microsoft.CognitiveServices"}
        assert classify_dimon_row(row) == "tokens_ondemand", meter


def test_classify_vm_and_storage_are_infra():
    row = {"skumeter": "F16s v2", "chargecategory": "Usage",
           "servicesubcategory": "Virtual Machines FSv2 Series", "servicename": "Microsoft.Compute"}
    assert classify_dimon_row(row) == "infra"
    row2 = {"skumeter": "P10 LRS Disk", "chargecategory": "Usage",
            "servicesubcategory": "Premium SSD Managed Disks", "servicename": "Microsoft.Compute"}
    assert classify_dimon_row(row2) == "infra"


def test_classify_vm_return_is_infra():
    row = {"skumeter": None, "chargecategory": "Return",
           "servicesubcategory": "Virtual Machines Dsv5 Series", "servicename": "Microsoft.Compute"}
    assert classify_dimon_row(row) == "infra"


# ── serviços de IA (card próprio, fora de infra/tokens) ────────────────────

def test_classify_document_intelligence_is_ia_service():
    row = {"skumeter": "S0 Pre-built Pages", "chargecategory": "Usage",
           "servicesubcategory": "Azure Document Intelligence", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "ia_service"
    assert ia_family_for(row) == "Azure Document Intelligence"


def test_classify_claude_saas_is_ia_service():
    row = {"skumeter": "claude sonnet 4.6 - anthropic-claude-sonnet-4-6-plan-new - paygo",
           "chargecategory": "Usage", "servicesubcategory": "Claude Sonnet 4.6", "servicename": "Microsoft.SaaS"}
    assert classify_dimon_row(row) == "ia_service"
    assert ia_family_for(row) == "Claude (Anthropic)"


def test_classify_claude_foundry_ccu_is_ia_service_not_tokens():
    row = {"skumeter": "Claude in Microsoft Foundry (Anthropic hosted) - claude-ccu-anthropic-hosted-plan - claude-consumption-units",
           "chargecategory": "Usage", "servicesubcategory": "Claude in Microsoft Foundry (Anthropic hosted)", "servicename": "Microsoft.SaaS"}
    assert classify_dimon_row(row) == "ia_service"
    assert ia_family_for(row) == "Claude (Anthropic)"


def test_classify_gpt5_tokens_are_ia_service_not_ondemand():
    row = {"skumeter": "5.4 opt gl 1m tokens", "chargecategory": "Usage",
           "servicesubcategory": "Azure OpenAI GPT5", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "ia_service"
    assert ia_family_for(row) == "Azure OpenAI GPT-5"


def test_classify_speech_is_ia_service():
    row = {"skumeter": "S1 Neural Text to Speech Characters", "chargecategory": "Usage",
           "servicesubcategory": "Azure Speech", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "ia_service"
    assert ia_family_for(row) == "Azure Speech"


def test_classify_azure_openai_generic_tokens_still_ondemand():
    row = {"skumeter": "gpt 4o 0513 Input global Tokens", "chargecategory": "Usage",
           "servicesubcategory": "Azure OpenAI", "servicename": "Microsoft.CognitiveServices"}
    assert classify_dimon_row(row) == "tokens_ondemand"
    assert ia_family_for(row) is None


# ── estimativa da compra on-demand de PTU ──────────────────────────────────

def test_ondemand_estimate_uses_last_realized_purchase():
    # A compra on-demand (Provisioned Throughput Units, 1/Month) é estável;
    # quando o mês ainda não a recebeu, estima pelo último realizado.
    rows = [
        _row(period="202606", project="Aura Brain", cost_class="ptu", is_ondemand_ptu=1, realized=133_984.0),
        _row(period="202607", project="Aura Brain", cost_class="ptu", is_ondemand_ptu=1, realized=136_827.0),
        _row(period="202607", project="Aura Brain", cost_class="infra", is_ondemand_ptu=0, realized=150_000.0),
        _row(period="202608", project="Vivo Mais", cost_class="ptu", is_ondemand_ptu=1, realized=121_257.0),
    ]
    value, note = _ondemand_ptu_estimate(rows, "202608", "Aura Brain")
    assert value == 136_827.0
    assert note == "último realizado (jul/2026)"
    # Vivo Mais usa reserva (não on-demand): sem histórico -> 0.
    value2, note2 = _ondemand_ptu_estimate(rows, "202608", "Vivo Mais")
    assert value2 == 0.0
    assert note2 is None


# ── série mensal ───────────────────────────────────────────────────────────

def test_monthly_series_filters_project_and_splits_realized_forecast():
    rows = [
        _row(period="202607", project="Aura Brain", cost_class="ptu", realized=200_000.0),
        _row(period="202607", project="Aura Brain", cost_class="infra", realized=50_000.0),
        _row(period="202607", project="Aura Brain", cost_class="ia_service", ia_family="Azure Document Intelligence", realized=4_000.0),
        _row(period="202607", project="Aura Brain", cost_class="ia_service", ia_family="Claude (Anthropic)", realized=600.0),
        _row(period="202607", project="Aura Brain", tipo="forecast", realized=300_000.0),
        _row(period="202607", project="Vivo Mais", cost_class="ptu", realized=121_000.0),
        _row(period="202607", project="Vivo Mais", cost_class="tokens_ondemand", realized=8_000.0),
    ]
    series = _monthly_series(rows, ["202607"], "Aura Brain")
    assert len(series) == 1
    item = series[0]
    assert item["realizado"]["total"] == 254_600.0
    assert item["realizado"]["ptu"] == 200_000.0
    assert item["realizado"]["infra"] == 50_000.0
    assert item["realizado"]["ia_service"] == 4_600.0
    assert item["realizado"]["ia_services"] == {
        "Azure Document Intelligence": 4_000.0,
        "Claude (Anthropic)": 600.0,
    }
    assert item["forecast"] == 300_000.0


def test_monthly_series_month_without_realized_is_none():
    rows = [_row(period="202607", project="Aura Brain", tipo="forecast", realized=300_000.0)]
    series = _monthly_series(rows, ["202607", "202608"], "Aura Brain")
    assert series[0]["realizado"] is None
    assert series[0]["forecast"] == 300_000.0
    assert series[1]["realizado"] is None
    assert series[1]["forecast"] is None


def test_monthly_series_infra_is_only_infra_class():
    """Infraestrutura is the infra class alone, not total and not total+infra."""
    rows = [
        _row(period="202607", project="Vivo Mais", cost_class="ptu", realized=100_000.0),
        _row(period="202607", project="Vivo Mais", cost_class="tokens_ondemand", realized=20_000.0),
        _row(period="202607", project="Vivo Mais", cost_class="ia_service",
             ia_family="Claude (Anthropic)", realized=5_000.0),
        _row(period="202607", project="Vivo Mais", cost_class="infra", realized=8_000.0),
        _row(period="202607", project="Vivo Mais", cost_class="__excluded__", realized=99_000.0),
    ]
    series = _monthly_series(rows, ["202607"], "Vivo Mais")
    realized = series[0]["realizado"]
    assert realized["ptu"] == 100_000.0
    assert realized["tokens_ondemand"] == 20_000.0
    assert realized["ia_service"] == 5_000.0
    assert realized["ia_services"] == {"Claude (Anthropic)": 5_000.0}
    assert realized["infra"] == 8_000.0
    assert realized["total"] == 133_000.0
    assert realized["infra"] != realized["total"]
    assert realized["infra"] != realized["total"] + 8_000.0


def test_monthly_series_does_not_treat_rateio_as_realized():
    rows = [
        _row(period="202608", project="Vivo Mais", tipo="realizado", realized=650_000.0),
        _row(period="202609", project="Vivo Mais", tipo="rateio", realized=22_000.0),
    ]
    series = _monthly_series(rows, ["202608", "202609"], "Vivo Mais")
    assert series[0]["realizado"]["total"] == 650_000.0
    assert series[1]["realizado"] is None


def test_monthly_series_forecast_excludes_return_rows():
    # O espelho de planning espelha o consumo horário (excluído do realizado);
    # as Returns não são abatidas — senão os meses futuros ficam próximos de zero.
    rows = [
        _row(period="202608", project="Aura Brain", tipo="forecast",
             chargecategory="Usage", servicesubcategory="Azure OpenAI", realized=368_208.0),
        _row(period="202608", project="Aura Brain", tipo="forecast",
             chargecategory="Return", servicesubcategory="Azure OpenAI", realized=-202_389.0),
        _row(period="202608", project="Aura Brain", tipo="forecast",
             chargecategory="Usage", servicesubcategory="Virtual Machines FSv2 Series", realized=25_000.0),
    ]
    series = _monthly_series(rows, ["202608"], "Aura Brain")
    assert series[0]["forecast"] == 368_208.0 + 25_000.0


# ── infra por servicename ──────────────────────────────────────────────────

def test_infra_by_servicename_ranking():
    rows = [
        _row(period="202608", project="Aura Brain", cost_class="infra", servicesubcategory="Virtual Machines FSv2 Series", realized=30_000.0),
        _row(period="202608", project="Aura Brain", cost_class="infra", servicesubcategory="Azure Cosmos DB", realized=10_000.0),
        _row(period="202608", project="Aura Brain", cost_class="ptu", servicesubcategory="Azure OpenAI", realized=200_000.0),
        _row(period="202608", project="Vivo Mais", cost_class="infra", servicesubcategory="Virtual Machines FSv2 Series", realized=5_000.0),
        _row(period="202607", project="Aura Brain", cost_class="infra", servicesubcategory="Virtual Machines FSv2 Series", realized=9_999.0),
    ]
    ranking = _infra_by_subcategory(rows, "202608", "Aura Brain")
    assert [r["servicesubcategory"] for r in ranking] == ["Virtual Machines FSv2 Series", "Azure Cosmos DB"]
    assert ranking[0]["realized"] == 30_000.0
    assert round(ranking[0]["pct"], 1) == 75.0


def test_infra_by_subcategory_empty_for_vivo_mais():
    assert _infra_by_subcategory([], "202608", "Vivo Mais") == []


# ── helpers ────────────────────────────────────────────────────────────────

def test_mom_pct():
    assert _mom_pct(300_000.0, 250_000.0) == 20.0
    assert _mom_pct(None, 250_000.0) is None
    assert _mom_pct(300_000.0, None) is None


def test_empty_classes_and_period_label():
    assert _empty_classes() == {"ptu": 0.0, "tokens_ondemand": 0.0, "ia_service": 0.0, "infra": 0.0}
    assert _period_label("202608") == "ago/2026"
    assert _period_label("") == "—"
    assert _last_closed_period(date(2026, 9, 4)) == "202608"
    assert _last_closed_period(date(2026, 1, 4)) == "202512"
