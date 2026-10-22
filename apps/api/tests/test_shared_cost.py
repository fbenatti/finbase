from app.services.shared_cost import allocate_amount, driver_name_for


def test_allocation_uses_source_cloud_even_when_organogram_filter_is_stale():
    from types import SimpleNamespace

    from app.models.rules import Department
    from app.services.shared_cost import build_allocation_rows, resolve_shared_costs

    departments = {
        1: Department(id=1, name="Consumo Cloud", active=True),
        2: Department(id=2, parent_id=1, name="Showback", active=True),
        3: Department(id=3, parent_id=2, name="GCP", active=True),
        4: Department(id=4, parent_id=3, name="DIR INFRA", active=True),
        5: Department(id=5, parent_id=4, name="Networking", active=True),
        6: Department(id=6, parent_id=3, name="DIR DADOS", active=True),
        7: Department(id=7, parent_id=6, name="Projeto", active=True),
    }
    config = SimpleNamespace(
        enabled=True, department_id=5, mode="custom", allocation_type="rateio",
        targets=[SimpleNamespace(target_department_id=7, percent=100.0)],
    )
    resolved = resolve_shared_costs([config], departments)
    assert len(resolved) == 1
    assert resolved[0].cloud_prefix == "gcp"
    actual = [
        {"period": "202609", "path": resolved[0].donor_path,
         "cloud": "gcp-ttech", "value": 100.0, "cloud_value": 20.0},
        {"period": "202609", "path": resolved[0].donor_path,
         "cloud": "gcp-ti", "value": 30.0, "cloud_value": 6.0},
    ]
    rows = build_allocation_rows(resolved, actual, [])
    for cloud, billed, consumption in [("gcp-ttech", 100.0, 20.0), ("gcp-ti", 30.0, 6.0)]:
        source_rows = [row for row in rows if row["cloud_origem"] == cloud]
        assert len(source_rows) == 2
        receiver = next(row for row in source_rows if row["organograma_nivel_5"] == "Projeto")
        assert receiver["realizado"] == billed
        assert receiver["realizado_cloud"] == consumption
        assert sum(row["realizado"] for row in source_rows) == 0
        assert sum(row["realizado_cloud"] for row in source_rows) == 0


def test_expected_view_uses_provider_scope_when_organogram_filter_is_stale():
    from app.models.rules import Department
    from app.routers.analysis import _expected_view_cloud_filter

    selected = Department(name="Networking")
    scope = ["Consumo Cloud", "Showback", "GCP", "DIR INFRA", "Networking"]
    clause = _expected_view_cloud_filter(scope, selected, {"cloud_origem": "cloud_origem"})
    assert "gcp%" in clause
    assert "gcp-ti%" not in clause


def test_normal_allocation_conserves_billed_and_cloud_measures_independently():
    from app.services.shared_cost import ResolvedSharedCost, build_allocation_rows

    config = ResolvedSharedCost(
        donor_id=1, donor_path=["IBM", "Origem"], cloud_prefix="ibm",
        mode="custom", donor_subtree={1}, target_leaves=[2],
        leaf_paths={2: ["IBM", "Destino"]}, custom_targets=[([2], 100.0)],
        allocation_type="rateio",
    )
    for billed, cloud in [(100.0, 20.0), (0.0, 20.0), (100.0, 0.0)]:
        actual = [{"period": "202609", "path": config.donor_path,
                   "value": billed, "cloud_value": cloud}]
        rows = build_allocation_rows([config], actual, [])
        receiver = next(row for row in rows if row["organograma_nivel_2"] == "Destino")
        assert receiver["realizado"] == billed
        assert receiver["realizado_cloud"] == cloud
        assert sum(row["realizado"] for row in rows) == 0
        assert sum(row["realizado_cloud"] for row in rows) == 0
        assert {row["tipo"] for row in rows} == {"shared_cost"}

    config.allocation_type = "shared_cost"
    condominium = build_allocation_rows([config], actual, [])
    assert {row["tipo"] for row in condominium} == {"rateio"}
    assert all("realizado_cloud" not in row for row in condominium)


def test_normal_rateio_moves_forecast_in_realized_month_but_condominium_skips_it():
    from app.services.shared_cost import ResolvedSharedCost, build_allocation_rows

    config = ResolvedSharedCost(
        donor_id=1, donor_path=["IBM", "Origem"], cloud_prefix="ibm",
        mode="custom", donor_subtree={1}, target_leaves=[2],
        leaf_paths={2: ["IBM", "Destino"]}, custom_targets=[([2], 80.0)],
        allocation_type="rateio",
    )
    actual = [{"period": "202609", "path": config.donor_path, "value": 100, "cloud_value": 20}]
    forecast = [{"period": "202609", "path": config.donor_path, "value": 50, "cloud_value": 10}]
    rows = build_allocation_rows([config], actual, forecast)
    projected = [row for row in rows if row["rateio_medida"] == "forecast"]
    recipient = next(row for row in projected if row["organograma_nivel_2"] == "Destino")
    assert recipient["realizado"] == 40
    assert recipient["realizado_cloud"] == 8
    assert sum(row["realizado"] for row in projected) == 0
    assert sum(row["realizado_cloud"] for row in projected) == 0
    assert 50 + sum(row["realizado"] for row in projected if row["organograma_nivel_2"] == "Origem") == 10
    config.allocation_type = "shared_cost"
    assert all(row["rateio_medida"] == "realizado" for row in build_allocation_rows([config], actual, forecast))


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
    donor = next(row for row in rows if row["organograma_nivel_4"] == "Rateio Azure")
    consumer = next(row for row in rows if row["organograma_nivel_5"] == "Proj X")
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


def test_realized_rateio_marks_period_for_recipient_without_own_cost():
    from app.services.shared_cost import apply_rateio_facts

    by_period: dict[str, dict[str, Any]] = {}
    actual_by_driver: dict[str, float] = {}
    apply_rateio_facts(
        [{"period": "202601", "driver": "CONDOMINIO TESTE", "value": 25.0}],
        by_period,
        actual_by_driver,
        "shared_actual_value",
    )

    assert by_period["202601"]["actual_value"] == 0.0
    assert by_period["202601"]["shared_actual_value"] == 25.0
    assert by_period["202601"]["shared_received"] == 25.0


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
    donor = next(row for row in rows if row["organograma_nivel_4"] == "Rateio Azure")
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
    by_proj = {row["organograma_nivel_5"]: row["realizado"] for row in sep}
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
        1: Department(id=1, parent_id=None, name="Marketplace", active=True),
        2: Department(id=2, parent_id=1, name="Azure - MongoDB", active=True),
        3: Department(id=3, parent_id=2, name="MongoDB - Condominio", active=True),
        4: Department(id=4, parent_id=2, name="MongoDB - ATMA", active=True),
        5: Department(id=5, parent_id=2, name="MongoDB - Invoice", active=True),
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
    consumers = {(row["organograma_nivel_5"] or "").strip(): row["realizado"] for row in rows if row["realizado"] > 0}
    assert consumers["Sinfonia - Watson"] == 50
    assert consumers["Sinfonia - VivoCog"] == 50
    assert "env" not in consumers


def test_cloud_prefix_for_atlas_mongodb():
    from app.models.rules import Department
    from app.services.shared_cost import cloud_prefix_for

    departments = {
        1: Department(id=1, parent_id=None, name="Marketplace", active=True),
        2: Department(id=2, parent_id=1, name="Azure - MongoDB", active=True),
        3: Department(id=3, parent_id=2, name="MongoDB - Invoice", active=True),
    }
    assert cloud_prefix_for(departments[3], ["Marketplace", "Azure - MongoDB", "MongoDB - Invoice"], departments) == "mongodb"
    assert cloud_prefix_for(departments[2], ["Marketplace", "Azure - MongoDB"], departments) == "mongodb"
