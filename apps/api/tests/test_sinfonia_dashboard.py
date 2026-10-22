import pytest

from app.services.sinfonia_dashboard import build_dashboard, ia_family_for, load_sinfonia_dashboard, normalize_row


def row(**values):
    base = {"period": "202608", "tipo": "realizado", "cloud_origem": "azure-ti",
            "hierarquia_5": " Fábrica de Agente", "hierarquia_6": "Fábrica de Agente - Comercial",
            "servicename": "Azure OpenAI", "servicesubcategory": "Azure OpenAI", "skumeter": "Tokens", "realized": 100}
    base.update(values)
    return base


def test_normalizes_portfolio_and_excludes_legacy_sinfonia_ia():
    assert normalize_row(row())["project"] == "Comercial"
    assert normalize_row(row(hierarquia_5="Sinfonia - Watson ", hierarquia_6=None))["cloud"] == "IBM"
    assert normalize_row(row(hierarquia_5="Sinfonia IA", hierarquia_6=None)) is None


def test_current_month_prefers_forecast_and_includes_rateio_in_closed_month():
    result = build_dashboard([
        row(period="202607", tipo="realizado", realized=100),
        row(period="202607", tipo="rateio", realized=20),
        row(tipo="realizado", realized=50),
        row(tipo="forecast", realized=200),
    ], open_period="202608")
    assert result["monthly"][0]["factory"] == 120
    assert result["monthly"][1]["factory"] == 200
    assert result["kpis"]["factory"] == 200


def test_identifies_ibm_and_azure_ai_families():
    assert ia_family_for(row(servicename="watsonx Orchestrate"), "IBM") == "watsonx Orchestrate"
    assert ia_family_for(row(servicesubcategory="Azure Document Intelligence"), "Azure") == "Azure Document Intelligence"


@pytest.mark.asyncio
@pytest.mark.parametrize("level5,level6", [
    ("hierarquia_5", "hierarquia_6"),
    ("departamento_nivel_5", "departamento_nivel_6"),
])
async def test_factory_subprojects_use_gold_hierarchy(monkeypatch, level5, level6):
    statements = []

    async def execute(sql, **_kwargs):
        statements.append(sql)
        if sql.lstrip().startswith("DESCRIBE"):
            return [{"Column": name} for name in (
                "_billing_period", "tipo", "realizado", level5, level6,
                "servicename", "servicesubcategory", "skumeter",
            )]
        return [row(hierarquia_5="Fábrica de Agentes",
                    hierarquia_6="Fábrica de Agentes - Comercial")]

    monkeypatch.setattr("app.services.sinfonia_dashboard.trino_execute", execute)
    result = await load_sinfonia_dashboard()

    assert result["kpis"]["factory"] == 100
    assert '"' + level5 + '" AS hierarquia_5' in statements[1]
    assert '"' + level6 + '" AS hierarquia_6' in statements[1]


@pytest.mark.asyncio
async def test_factory_hierarchy_uses_populated_department_when_organogram_is_blank(monkeypatch):
    statements = []

    async def execute(sql, **_kwargs):
        statements.append(sql)
        if sql.lstrip().startswith("DESCRIBE"):
            return [{"Column": name} for name in (
                "_billing_period", "tipo", "realizado", "hierarquia_5",
                "departamento_nivel_5", "hierarquia_6", "departamento_nivel_6",
            )]
        return []

    monkeypatch.setattr("app.services.sinfonia_dashboard.trino_execute", execute)
    await load_sinfonia_dashboard()

    assert "NULLIF(TRIM(CAST(\"departamento_nivel_5\" AS VARCHAR)), '')" in statements[1]
    assert "NULLIF(TRIM(CAST(\"departamento_nivel_6\" AS VARCHAR)), '')" in statements[1]
