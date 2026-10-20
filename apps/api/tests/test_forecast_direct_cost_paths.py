from app.routers.forecast import _desvio_cost_paths, _leaf_totals


def test_ranking_includes_usage_on_parent_and_return_on_child_without_double_counting():
    parent = ["Cloud", "Showback", "Azure", "Dir", "SAPTDF"]
    child = [*parent, "Reserva - Retorno"]
    purchase = [*parent, "Reserva"]
    key = lambda path: tuple(path + [None] * (7 - len(path)))
    forecasts = {key(parent): 300, key(child): -300, key(purchase): 100}
    actuals = {key(parent): 320, key(child): -320, key(purchase): 101}
    paths = _desvio_cost_paths([child, purchase, child], forecasts, actuals, parent)
    assert len(paths) == 3
    assert parent in paths
    totals = [_leaf_totals(path, forecasts, actuals) for path in paths]
    assert sum(f for f, _ in totals) == 100
    assert sum(a for _, a in totals) == 101


def test_direct_cost_paths_stay_inside_selected_scope():
    actuals = {("Cloud", "A", None): 10, ("Cloud", "B", None): 20}
    assert _desvio_cost_paths([], {}, actuals, ["Cloud", "A"]) == [["Cloud", "A"]]
