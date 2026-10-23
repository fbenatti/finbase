"""Listagem de destinos: planning e rateio saem de uma única agregação."""
from app.routers.analysis import _planning_breakdown


def test_planning_breakdown_separa_total_e_rateio_por_periodo():
    rows = [
        {"period": "202609", "shared": False, "cnt": 10},
        {"period": "202609", "shared": True, "cnt": 3},
        {"period": "202610", "shared": False, "cnt": 7},
    ]
    result = _planning_breakdown(rows)
    assert result["rows"] == 20
    assert result["periods"] == [{"period": "202610", "count": 7}, {"period": "202609", "count": 13}]
    assert result["shared_rows"] == 3
    assert result["shared_periods"] == [{"period": "202609", "count": 3}]


def test_planning_breakdown_vazio_nao_expoe_pseudo_destinos():
    result = _planning_breakdown([])
    assert result == {"rows": 0, "periods": [], "shared_rows": 0, "shared_periods": []}
