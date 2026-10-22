from app.services.azure_ptu_cost import (
    _carry_forward_reservation_purchase,
    _project_filter,
)


def test_reservation_uses_last_positive_purchase_as_reference():
    rows = [
        {"period": "202605", "label": "mai/2026", "project": "Projeto A",
         "reservation_units": 100.0, "reservation_cost": 156_336.41},
        {"period": "202606", "label": "jun/2026", "project": "Projeto A",
         "reservation_units": 0.0, "reservation_cost": 0.0},
        {"period": "202609", "label": "set/2026", "project": "Projeto A",
         "reservation_units": 0.0, "reservation_cost": 0.0},
    ]

    _carry_forward_reservation_purchase(rows)

    assert rows[-1]["reservation_units"] == 100.0
    assert rows[-1]["reservation_cost"] == 156_336.41
    assert rows[-1]["reservation_referenced"] is True
    assert rows[-1]["reservation_reference_note"] == "última compra mai/2026"


def test_reservation_reference_never_uses_refund_or_zero_quantity():
    rows = [
        {"period": "202607", "label": "jul/2026", "project": "Projeto B",
         "reservation_units": 0.0, "reservation_cost": -161_311.95},
        {"period": "202608", "label": "ago/2026", "project": "Projeto B",
         "reservation_units": 0.0, "reservation_cost": 0.0},
    ]

    _carry_forward_reservation_purchase(rows)

    assert rows[-1]["reservation_units"] == 0.0
    assert rows[-1]["reservation_cost"] == 0.0
    assert "reservation_referenced" not in rows[-1]


def test_project_filter_uses_requested_projects_or_none():
    assert _project_filter(None) == ""
    assert _project_filter(["Projeto A", "Projeto A", "D'Ávila"]) == (
        "AND hierarquia_5 IN ('Projeto A', 'D''Ávila')"
    )
