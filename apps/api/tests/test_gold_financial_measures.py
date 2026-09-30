from pathlib import Path

from app.routers.gold import _GOLD_MEASURE_COLUMNS


def test_gold_api_exposes_contractual_financial_measures():
    assert {
        "realizado",
        "realizado_cloud",
        "consumo_realizado",
        "ajuste_contratual",
    }.issubset(_GOLD_MEASURE_COLUMNS)


def test_startup_keeps_executive_cube_financial_measures_configured():
    main_source = (
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).read_text(encoding="utf-8")

    assert "WHERE code = 'visao-completa'" in main_source
    assert "\\\"realizado_cloud\\\"" in main_source
    assert "\\\"consumo_realizado\\\"" in main_source
    assert "\\\"ajuste_contratual\\\"" in main_source
