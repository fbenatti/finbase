"""Instrumentação de latência: por query Trino e por rota da API."""
import asyncio

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.latency_middleware import LatencyMetricsMiddleware
from app.services import finops_cache
from app.services import trino_service


def test_sql_kind_agrupa_pela_primeira_palavra():
    assert trino_service.sql_kind("  DESCRIBE iceberg.gold.t") == "DESCRIBE"
    assert trino_service.sql_kind("\n  with parsed as (select 1) select * from parsed") == "WITH"
    assert trino_service.sql_kind("(SELECT 1)") == "SELECT"
    assert trino_service.sql_kind("   ") == "VAZIO"


def test_sql_for_log_achata_e_trunca():
    sql = "SELECT\n   a,\n   b\nFROM t WHERE " + "x" * 500
    flat = trino_service.sql_for_log(sql, limit=60)
    assert "\n" not in flat
    assert len(flat) == 60 and flat.endswith("…")
    assert trino_service.sql_for_log("SELECT 1") == "SELECT 1"


def test_execute_once_registra_amostra_mesmo_com_erro(monkeypatch):
    finops_cache.reset_state()

    async def fail(*_args, **_kwargs):
        raise trino_service.TrinoError("Trino error 500: boom")

    monkeypatch.setattr(trino_service, "_execute_protocol", fail)
    monkeypatch.setattr(trino_service, "TRINO_QUERY_PHASES", False)
    try:
        asyncio.run(trino_service._execute_once("DESCRIBE iceberg.gold.t"))
    except trino_service.TrinoError:
        pass
    report = finops_cache.metrics_report()["endpoints"]
    assert report["trino"]["samples"] == 1 and report["trino"]["errors"] == 1
    assert report["trino:DESCRIBE"]["samples"] == 1


def test_execute_once_devolve_linhas_e_registra_por_tipo(monkeypatch):
    finops_cache.reset_state()

    async def ok(*_args, **_kwargs):
        return "20261023_000000_00001_abcd", [{"n": 1}]

    monkeypatch.setattr(trino_service, "_execute_protocol", ok)
    monkeypatch.setattr(trino_service, "TRINO_QUERY_PHASES", False)
    rows = asyncio.run(trino_service._execute_once("select 1"))
    assert rows == [{"n": 1}]
    report = finops_cache.metrics_report()["endpoints"]
    assert report["trino:SELECT"]["samples"] == 1 and report["trino:SELECT"]["errors"] == 0


def test_middleware_registra_pelo_template_da_rota():
    finops_cache.reset_state()

    async def item(request):
        return JSONResponse({"id": request.path_params["item_id"]})

    app = Starlette(routes=[Route("/itens/{item_id}", item)])
    app.add_middleware(LatencyMetricsMiddleware)
    client = TestClient(app)
    assert client.get("/itens/1").status_code == 200
    assert client.get("/itens/2").status_code == 200
    assert client.get("/nao-existe").status_code == 404
    report = finops_cache.metrics_report()["endpoints"]
    assert report["GET /itens/{item_id}"]["samples"] == 2
    assert not any(key.endswith("/nao-existe") for key in report)
