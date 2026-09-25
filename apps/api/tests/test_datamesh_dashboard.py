from app.services.datamesh_dashboard import build_dashboard, classify_cost


def row(**values):
    base = {"period": "202608", "tipo": "realizado", "organograma_nivel_6": "Projeto A", "servicename": "Azure Databricks", "servicesubcategory": "Analytics", "skumeter": "DBU", "realized": 100}
    base.update(values)
    return base


def test_cost_classification():
    assert classify_cost(row()) == "Databricks"
    assert classify_cost(row(servicename="Storage Accounts")) == "Dados associados"
    assert classify_cost(row(servicename="Virtual Machines", servicesubcategory="Compute", skumeter="vCPU Hours")) == "Infraestrutura"


def test_forecast_precedence_rateio_and_project_fallback():
    result = build_dashboard([row(period="202607", realized=100), row(period="202607", tipo="rateio", realized=20), row(tipo="realizado", realized=50), row(tipo="forecast", realized=200), row(servicename="Storage Accounts", organograma_nivel_6=None, tipo="forecast", realized=30)], open_period="202608")
    assert result["monthly"][0]["total"] == 120
    # Com a correção de Bug #2: mês aberto exibe realizado_parcial + forecast_restante.
    # Databricks: realizado=50 + forecast=200 = 250 (antes exibia apenas 200).
    assert result["kpis"]["databricks"] == 250
    assert any(project["project"] == "Não informado" for project in result["projects"])
