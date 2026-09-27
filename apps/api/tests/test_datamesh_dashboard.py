import pytest

from app.services.datamesh_dashboard import build_dashboard, classify_cost, load_datamesh_dashboard


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


@pytest.mark.asyncio
@pytest.mark.parametrize("physical_column", ["organograma_nivel_6", "departamento_nivel_6", "organograma"])
async def test_query_uses_available_level_six_column(monkeypatch, physical_column):
    statements = []

    async def execute(sql, **_kwargs):
        statements.append(sql)
        if sql.lstrip().startswith("DESCRIBE"):
            return [{"Column": name} for name in (
                "_billing_period", "tipo", "realizado", physical_column,
                "servicename", "servicesubcategory", "skumeter",
            )]
        return [row()]

    monkeypatch.setattr("app.services.datamesh_dashboard.trino_execute", execute)
    result = await load_datamesh_dashboard()

    assert result["projects"][0]["project"] == "Projeto A"
    assert f'"{physical_column}" AS organograma_nivel_6' in statements[1]
    assert "CAST(NULL AS VARCHAR) AS centro_custo" in statements[1]
    assert f"{physical_column} AS organograma_nivel_6" not in statements[1]
