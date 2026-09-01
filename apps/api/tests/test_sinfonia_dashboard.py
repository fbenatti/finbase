from app.services.sinfonia_dashboard import build_dashboard, ia_family_for, normalize_row


def row(**values):
    base = {"period": "202608", "tipo": "realizado", "cloud_origem": "azure-ti",
            "departamento_nivel_5": " Fábrica de Agente", "departamento_nivel_6": "Fábrica de Agente - Comercial",
            "servicename": "Azure OpenAI", "servicesubcategory": "Azure OpenAI", "skumeter": "Tokens", "realized": 100}
    base.update(values)
    return base


def test_normalizes_portfolio_and_excludes_legacy_sinfonia_ia():
    assert normalize_row(row())["project"] == "Comercial"
    assert normalize_row(row(departamento_nivel_5="Sinfonia - Watson ", departamento_nivel_6=None))["cloud"] == "IBM"
    assert normalize_row(row(departamento_nivel_5="Sinfonia IA", departamento_nivel_6=None)) is None


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
