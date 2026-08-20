from app.services.shared_cost import allocate_amount, driver_name_for


def test_allocate_consumption_proportional():
    shares, residual = allocate_amount(
        100,
        "consumption",
        [1, 2],
        {1: 10.0, 2: 90.0},
    )
    assert shares[1] == 10
    assert shares[2] == 90
    assert residual == 0


def test_allocate_consumption_zero_does_not_pay():
    shares, residual = allocate_amount(
        100,
        "consumption",
        [1, 2, 3],
        {1: 10.0, 2: 90.0, 3: 0.0},
    )
    assert 3 not in shares
    assert shares[1] == 10
    assert shares[2] == 90
    assert residual == 0


def test_allocate_consumption_all_zero_stays_with_donor():
    shares, residual = allocate_amount(100, "consumption", [1, 2], {1: 0.0, 2: 0.0})
    assert shares == {}
    assert residual == 100


def test_allocate_equal_among_consumers():
    shares, residual = allocate_amount(
        100,
        "equal",
        [1, 2, 3],
        {1: 1.0, 2: 99.0, 3: 0.0},
    )
    assert shares[1] == 50
    assert shares[2] == 50
    assert 3 not in shares
    assert residual == 0


def test_allocate_custom_keeps_unassigned_residual():
    shares, residual = allocate_amount(
        100,
        "custom",
        [1, 2],
        {1: 10.0, 2: 90.0},
        custom_targets=[([1], 10.0), ([2], 80.0)],
    )
    assert shares[1] == 10
    assert shares[2] == 80
    assert residual == 10


def test_allocate_custom_zero_consumption_returns_bucket_to_residual():
    shares, residual = allocate_amount(
        100,
        "custom",
        [1, 2],
        {1: 0.0, 2: 50.0},
        custom_targets=[([1], 10.0), ([2], 80.0)],
    )
    assert 1 not in shares
    assert shares[2] == 80
    assert residual == 20


def test_driver_name_uses_child_of_scope():
    assert driver_name_for(
        ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"],
        ["Consumo Cloud", "Showback", "Azure"],
    ) == "DIR A"
    assert driver_name_for(
        ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"],
        ["Consumo Cloud", "Showback", "GCP"],
    ) is None


def test_path_matches_ignores_trailing_whitespace():
    from app.services.shared_cost import path_matches
    assert path_matches(["Sinfonia - Watson"], ["Sinfonia - Watson "])
    assert path_matches(["Sinfonia - Watson "], ["Sinfonia - Watson"])


def test_rateio_rows_are_positive_on_consumer_negative_on_donor():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=["Consumo Cloud", "Showback", "Azure", "Rateio Azure"],
        cloud_prefix="azure",
        mode="consumption",
        donor_subtree={9},
        target_leaves=[10],
        leaf_paths={10: ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"]},
    )
    rows = build_rateio_rows(
        [config],
        [{"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 100.0},
         {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 50.0}],
        [],
    )
    donor = next(row for row in rows if row["departamento_nivel_4"] == "Rateio Azure")
    consumer = next(row for row in rows if row["departamento_nivel_5"] == "Proj X")
    assert donor["tipo"] == "rateio"
    assert donor["rateio_medida"] == "realizado"
    assert donor["realizado"] == -100
    assert consumer["realizado"] == 100
    assert sum(row["realizado"] for row in rows) == 0


def test_expected_view_overlay_is_sum_of_rateio_rows():
    from app.services.shared_cost import apply_rateio_facts

    by_period = {"202601": {"period": "202601", "actual_value": 50.0, "forecast_value": None}}
    actual_by_driver = {"Proj X": 50.0}
    apply_rateio_facts(
        [{"period": "202601", "driver": "Proj X", "value": 100.0}],
        by_period,
        actual_by_driver,
        "shared_value",
    )
    assert by_period["202601"]["actual_value"] == 50
    assert by_period["202601"]["shared_value"] == 100
    assert by_period["202601"]["shared_received"] == 100
    assert actual_by_driver["Proj X"] == 150


def test_rateio_facts_split_received_and_given_in_the_same_period():
    from app.services.shared_cost import apply_rateio_facts

    by_period = {"202601": {"period": "202601", "actual_value": 200.0, "forecast_value": None}}
    by_driver: dict[str, float] = {}
    apply_rateio_facts(
        [
            {"period": "202601", "driver": "DIR IA", "value": 80.0},
            {"period": "202601", "driver": "DIR INFRA", "value": -80.0},
        ],
        by_period,
        by_driver,
        "shared_value",
    )
    assert by_period["202601"]["shared_value"] == 0
    assert by_period["202601"]["shared_received"] == 80
    assert by_period["202601"]["shared_given"] == -80


def test_rateio_rows_forecast_measure_separate():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=["Consumo Cloud", "Showback", "Azure", "Rateio Azure"],
        cloud_prefix="azure",
        mode="equal",
        donor_subtree={9},
        target_leaves=[10, 11],
        leaf_paths={
            10: ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"],
            11: ["Consumo Cloud", "Showback", "Azure", "DIR B", "Proj Y"],
        },
    )
    rows = build_rateio_rows(
        [config],
        [],
        [{"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 80.0},
         {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 1.0},
         {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "DIR B", "Proj Y"], "value": 1.0}],
    )
    assert all(row["rateio_medida"] == "forecast" for row in rows)
    assert all(row["tipo"] == "rateio" for row in rows)
    consumers = [row for row in rows if row["realizado"] > 0]
    assert sorted(row["realizado"] for row in consumers) == [40.0, 40.0]


def test_rateio_does_not_double_closed_months_that_also_have_forecast():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=["Consumo Cloud", "Showback", "Azure", "Rateio Azure"],
        cloud_prefix="azure",
        mode="consumption",
        donor_subtree={9},
        target_leaves=[10],
        leaf_paths={10: ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"]},
    )
    actual = [
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 100.0},
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 50.0},
    ]
    forecast = [
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 100.0},
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 50.0},
        {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 80.0},
        {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 40.0},
    ]
    rows = build_rateio_rows([config], actual, forecast)
    jan = [row for row in rows if row["period"] == "202601"]
    sep = [row for row in rows if row["period"] == "202609"]
    assert {row["rateio_medida"] for row in jan} == {"realizado"}
    assert sum(row["realizado"] for row in jan if row["realizado"] > 0) == 100
    assert {row["rateio_medida"] for row in sep} == {"forecast"}
    assert sum(row["realizado"] for row in sep if row["realizado"] > 0) == 80


def test_forecast_rateio_when_only_other_projects_have_gold_in_the_month():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=["Consumo Cloud", "Showback", "Azure", "Rateio Azure"],
        cloud_prefix="azure",
        mode="consumption",
        donor_subtree={9},
        target_leaves=[10],
        leaf_paths={10: ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"]},
    )
    actual = [
        {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 50.0},
    ]
    forecast = [
        {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 80.0},
        {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 40.0},
    ]
    rows = build_rateio_rows([config], actual, forecast)
    assert {row["rateio_medida"] for row in rows} == {"forecast"}
    donor = next(row for row in rows if row["departamento_nivel_4"] == "Rateio Azure")
    assert donor["realizado"] == -80


def test_forecast_rateio_follows_actual_consumption_mix():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=["Consumo Cloud", "Showback", "Azure", "Rateio Azure"],
        cloud_prefix="azure",
        mode="consumption",
        donor_subtree={9},
        target_leaves=[10, 11],
        leaf_paths={
            10: ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"],
            11: ["Consumo Cloud", "Showback", "Azure", "DIR B", "Proj Y"],
        },
    )
    actual = [
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 100.0},
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 75.0},
        {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "DIR B", "Proj Y"], "value": 25.0},
    ]
    forecast = [
        {"period": "202609", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 80.0},
    ]
    rows = build_rateio_rows([config], actual, forecast)
    sep = [row for row in rows if row["period"] == "202609" and row["realizado"] > 0]
    by_proj = {row["departamento_nivel_5"]: row["realizado"] for row in sep}
    assert by_proj["Proj X"] == 60
    assert by_proj["Proj Y"] == 20


def test_rateio_zero_consumption_writes_nothing():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=["Consumo Cloud", "Showback", "Azure", "Rateio Azure"],
        cloud_prefix="azure",
        mode="consumption",
        donor_subtree={9},
        target_leaves=[10],
        leaf_paths={10: ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"]},
    )
    rows = build_rateio_rows(
        [config],
        [{"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "Rateio Azure"], "value": 100.0},
         {"period": "202601", "path": ["Consumo Cloud", "Showback", "Azure", "DIR A", "Proj X"], "value": 0.0}],
        [],
    )
    assert rows == []


def test_same_cloud_leaves_exclude_donor_and_other_clouds():
    from app.models.rules import Department
    from app.services.shared_cost import children_map, same_cloud_leaf_ids, subtree_ids

    departments = {
        1: Department(id=1, parent_id=None, name="Consumo Cloud", active=True),
        2: Department(id=2, parent_id=1, name="Showback", active=True),
        3: Department(id=3, parent_id=2, name="Azure", active=True),
        4: Department(id=4, parent_id=2, name="GCP", active=True),
        5: Department(id=5, parent_id=3, name="Rateio Azure", active=True),
        6: Department(id=6, parent_id=3, name="Proj A", active=True),
        7: Department(id=7, parent_id=4, name="Proj GCP", active=True),
    }
    child_ids = children_map(departments)
    leaves = same_cloud_leaf_ids("azure", departments, child_ids, subtree_ids(5, child_ids))
    assert leaves == [6]


def test_same_cloud_leaves_shallow_marketplace_structure():
    """Marketplace > Azure - MongoDB > MongoDB - X: doador no nível 3.

    O piso fixo de profundidade 4 excluía todos os projetos da branch e o
    condomínio era silenciosamente descartado do rateio. Projetos com
    ``cloud_filter`` obsoleto (``Atlas-MongoDB``) continuam elegíveis porque
    o rateio segue a cloud de origem (subárvore do doador), não filtros por nó.
    """
    from app.models.rules import Department
    from app.services.shared_cost import (
        children_map, cloud_root_path_for, same_cloud_leaf_ids, subtree_ids,
    )

    departments = {
        1: Department(id=1, parent_id=None, name="Marketplace", active=True, cloud_filter="mongodb"),
        2: Department(id=2, parent_id=1, name="Azure - MongoDB", active=True, cloud_filter="mongodb"),
        3: Department(id=3, parent_id=2, name="MongoDB - Condominio", active=True),
        4: Department(id=4, parent_id=2, name="MongoDB - ATMA", active=True),
        5: Department(id=5, parent_id=2, name="MongoDB - Invoice", active=True, cloud_filter="Atlas-MongoDB"),
    }
    child_ids = children_map(departments)
    root = cloud_root_path_for(departments[3], ["Marketplace", "Azure - MongoDB", "MongoDB - Condominio"], departments)
    assert root == ["Marketplace", "Azure - MongoDB"]
    leaves = same_cloud_leaf_ids(
        "mongodb", departments, child_ids, subtree_ids(3, child_ids), donor_depth=3, donor_root=root,
    )
    assert sorted(leaves) == [4, 5]


def test_consumption_splits_parent_project_and_sibling_leaf():
    from app.services.shared_cost import ResolvedSharedCost, build_rateio_rows

    watson = ["Consumo Cloud", "Showback", "IBM", "DIR IA", "Sinfonia - Watson "]
    vivo = ["Consumo Cloud", "Showback", "IBM", "DIR IA", "Sinfonia - VivoCog"]
    donor = ["Consumo Cloud", "Showback", "IBM", "Condominio"]
    config = ResolvedSharedCost(
        donor_id=9,
        donor_path=donor,
        cloud_prefix="ibm",
        mode="consumption",
        donor_subtree={9},
        target_leaves=[10, 11, 12],
        leaf_paths={
            10: watson,
            11: [*watson, "env"],
            12: vivo,
        },
    )
    rows = build_rateio_rows(
        [config],
        [
            {"period": "202601", "path": donor, "value": 100.0},
            {"period": "202601", "path": ["Consumo Cloud", "Showback", "IBM", "DIR IA", "Sinfonia - Watson"], "value": 50.0},
            {"period": "202601", "path": vivo, "value": 50.0},
        ],
        [],
    )
    consumers = {(row["departamento_nivel_5"] or "").strip(): row["realizado"] for row in rows if row["realizado"] > 0}
    assert consumers["Sinfonia - Watson"] == 50
    assert consumers["Sinfonia - VivoCog"] == 50
    assert "env" not in consumers
