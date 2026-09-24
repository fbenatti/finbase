"""Correções da auditoria: manifesto obrigatório, cobertura publicada, temporalidade.

Cada teste corresponde a um item do prompt corretivo. O que se prova aqui:

- A/E — `finops_period_coverage` publicada dirige a comparabilidade, e o default sem
  cobertura é `NOT_COMPARABLE`;
- B — nenhuma conta com múltiplas hierarquias recebe hierarquia dominante;
- C — custo anterior a setembro não recebe atributos do snapshot de setembro;
- D — `materializer_key` é allowlist fechada, sem nome de função;
- E — publicação governada não ocorre sem manifesto;
- F — o C2 bucketa pela chave canônica e não carrega linhas Silver no processo.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import gold_governance as gov
from app.services import gold_materializers as gm
from app.services import period_coverage as pc
from app.services.finops_cost_monthly import (
    ORG_UNMAPPED,
    c1_stage2_sql,
    measure_org_hierarchies,
    org_hierarchy_audit_sql,
)
from app.services.finops_cost_resource import (
    DEFAULT_BUCKETS,
    INVENTORY_STATUSES,
    _C2_OUTPUT_TYPES,
    bucket_count,
    bucket_predicate,
    c2_bucket_projection_sql,
    c2_projection_sql,
    inventory_temporal_sql,
)
from app.services.finops_cache import (
    cache_key,
    get_cached,
    invalidate_after_publish,
    metrics_report,
    record_request_metric,
    reset_state,
    set_cached,
)


# --- E: manifesto obrigatório -------------------------------------------------


def test_manifest_is_required_by_default():
    assert gov.manifest_mode_for("finops_cost_monthly") == gov.MANIFEST_REQUIRED
    assert gov.manifest_mode_for("finops_period_coverage") == gov.MANIFEST_REQUIRED
    assert (
        gov.manifest_mode_for("finops_cost_resource_monthly:202512:azure_4p:3")
        == gov.MANIFEST_REQUIRED
    )


def test_only_declared_legacy_cubes_use_best_effort():
    """Best effort existe só para legado identificado, e a lista é fechada."""
    assert gov.manifest_mode_for("dest_dm_cost_focus") == gov.MANIFEST_BEST_EFFORT
    assert "finops_cost_monthly" not in gov._BEST_EFFORT_CUBES
    assert "finops_cost_resource_monthly" not in gov._BEST_EFFORT_CUBES
    assert "finops_cost_change_driver" not in gov._BEST_EFFORT_CUBES
    assert "finops_period_coverage" not in gov._BEST_EFFORT_CUBES


@pytest.mark.asyncio
async def test_manifest_failure_aborts_before_staging(monkeypatch):
    """PostgreSQL indisponível: nenhum swap, nenhum staging criado."""
    def _sem_postgres():
        raise RuntimeError("connection refused")

    async def _record_falha(entry, **kwargs):
        raise gov.ManifestUnavailableError("postgres indisponível")

    swaps: list = []
    creates: list = []

    async def fake_execute(sql, **kwargs):
        creates.append(sql)
        return [{"row_count": 1}]

    monkeypatch.setattr(gov, "record_manifest", _record_falha)
    monkeypatch.setattr(gov.iceberg, "swap_staged_table", lambda *a, **k: swaps.append(a))
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)
    import app.services.trino_service as trino_service

    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(gov.ManifestUnavailableError):
        await gov.publish_gold_cube(
            cube_code="finops_cost_monthly",
            table="finops_cost_monthly",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
        )

    assert swaps == [], "publicação não pode ter acontecido sem manifesto"
    assert creates == [], "nem a CTAS pode ter rodado"


@pytest.mark.asyncio
async def test_final_manifest_failure_marks_failure(monkeypatch):
    """Falha no manifesto final reprova a publicação governada."""
    chamadas = {"n": 0}

    async def _record(entry, **kwargs):
        chamadas["n"] += 1
        if chamadas["n"] >= 2:
            raise gov.ManifestUnavailableError("postgres caiu no fim")

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 10}]
        if "SUM(" in sql:
            return [{"total": 100.0}]
        return []

    monkeypatch.setattr(gov, "record_manifest", _record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap")
    monkeypatch.setattr(gov.iceberg, "swap_staged_table", lambda *a, **k: None)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)
    import app.services.trino_service as trino_service

    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(gov.ManifestUnavailableError):
        await gov.publish_gold_cube(
            cube_code="finops_cost_monthly",
            table="finops_cost_monthly",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
        )


def test_publish_gold_cube_records_manifest_before_create():
    """A ordem importa: o registro RUNNING precede a criação do staging."""
    import inspect

    fonte = inspect.getsource(gov.publish_gold_cube)
    pos_registro = fonte.index("await record_manifest(entrada)")
    pos_create = fonte.index("await trino_execute(create_sql")
    assert pos_registro < pos_create


def test_blocking_manifest_refuses_inside_event_loop():
    """Caminho síncrono dentro de loop assíncrono é defeito, não motivo de skip."""
    import asyncio

    entrada = gov.ManifestEntry(
        cube_code="finops_cost_monthly", physical_table="finops_cost_monthly"
    )

    async def _tenta():
        with pytest.raises(gov.ManifestUnavailableError):
            gov.record_manifest_blocking(entrada)

    asyncio.run(_tenta())


# --- A: cobertura publicada dirige C3 e API -----------------------------------


def test_default_without_published_coverage_is_not_comparable():
    """Sem cobertura publicada, o default é não comparável — nunca COMPLETE."""
    assert pc.DEFAULT_UNKNOWN_STATUS == "NOT_COMPARABLE"
    assert pc.DEFAULT_UNKNOWN_STATUS not in pc.COMPARABLE_STATUSES


def test_coverage_status_uses_default_for_absent_pair():
    coverage = {("azure-4p", "202601"): "COMPLETE"}
    assert pc.coverage_status(coverage, "azure-4p", "202601") == "COMPLETE"
    assert pc.coverage_status(coverage, "azure-4p", "202602") == "NOT_COMPARABLE"
    assert pc.coverage_status(coverage, "desconhecida", "202601") == "NOT_COMPARABLE"


def test_is_comparable_only_for_complete():
    coverage = {
        ("a", "202601"): "COMPLETE",
        ("a", "202602"): "PARTIAL",
        ("a", "202603"): "CURRENT_OPEN",
    }
    assert pc.is_comparable(coverage, "a", "202601") is True
    assert pc.is_comparable(coverage, "a", "202602") is False
    assert pc.is_comparable(coverage, "a", "202603") is False


def test_coverage_row_has_every_required_field():
    """O cubo de cobertura carrega conexão, competência, dataset, status, motivo,
    comparabilidade, cobertura, regra e freshness."""
    linha = pc._coverage_row(
        pc.PeriodCoverage(
            connection_code="azure-4p",
            billing_period="202601",
            dataset="resource_cost",
            status="COMPLETE",
            reason="OK",
        ),
        dataset="resource_cost",
    )
    for campo in (
        "connection_code",
        "billing_period",
        "dataset",
        "status",
        "reason",
        "comparable",
        "day_coverage",
        "missing_dimension_ratio",
        "rule_version",
    ):
        assert campo in linha
    assert pc.COVERAGE_COLUMNS[-1] == "freshness_at"


def test_coverage_table_name():
    assert pc.COVERAGE_TABLE == "finops_period_coverage"


def test_c3_reads_published_coverage_not_the_classifier():
    """C3 não reclassifica: a leitura é da tabela publicada."""
    import inspect

    from app.services.finops_cost_driver import period_statuses

    fonte = inspect.getsource(period_statuses)
    assert "load_coverage_map" in fonte
    assert "collect_coverage" not in fonte


def test_api_does_not_reclassify_periods():
    """Nenhum request pode reexecutar o classificador."""
    import inspect

    from app.routers import finops_center

    fonte = inspect.getsource(finops_center)
    assert "collect_coverage" not in fonte
    assert "classify_all" not in fonte
    assert "load_coverage_map" in fonte


# --- B: sem hierarquia dominante ----------------------------------------------


def test_c1_stage2_publishes_null_org_levels_and_unmapped():
    sql = c1_stage2_sql(grain_table="g", resource_table="r")
    assert f"'{ORG_UNMAPPED}' AS org_classification_status" in sql
    # Nenhum nível organizacional é preenchido.
    assert "departamento_nivel_1 AS departamento_nivel_1" not in sql
    assert sql.count("CAST(NULL AS VARCHAR) AS departamento_nivel_") == 7


def test_c1_stage2_has_no_organization_join():
    """O join organizacional foi removido: não há chave reconciliável."""
    sql = c1_stage2_sql(grain_table="g", resource_table="r")
    assert "JOIN" in sql  # o join de recursos continua
    assert "account_key" not in sql
    assert "ROW_NUMBER" not in sql


def test_org_mapping_function_is_gone():
    """Não pode sobrar função que escolha uma organização entre várias."""
    import app.services.finops_cost_monthly as m

    assert not hasattr(m, "org_mapping_sql")


def test_org_audit_sql_measures_ambiguity():
    sql = org_hierarchy_audit_sql()
    assert "MULTIPLAS_HIERARQUIAS" in sql
    assert "hierarquias" in sql
    assert "ambigua" in sql


def test_org_audit_helper_exists():
    assert callable(measure_org_hierarchies)


def test_org_unavailable_reason_is_declared():
    from app.services.finops_cost_monthly import unavailable_measures

    motivos = unavailable_measures()
    assert any("departamento_nivel_1" in k for k in motivos)
    assert "org_classification_status" in motivos


# --- C: temporalidade do inventário -------------------------------------------


def test_inventory_temporal_accepts_only_valid_from_before_period_end():
    """Snapshot de setembro não pode descrever maio."""
    sql = inventory_temporal_sql()
    assert "snapshot_valid_from <= " in sql
    assert "NO_TEMPORAL_SNAPSHOT" in sql or "CURRENT_SNAPSHOT" in sql


def test_inventory_temporal_picks_closest_valid_snapshot():
    sql = inventory_temporal_sql()
    assert "ORDER BY b.snapshot_valid_from DESC" in sql
    assert "rn = 1" in sql


def test_inventory_temporal_partitions_by_period():
    """Cada competência resolve o próprio snapshot."""
    sql = inventory_temporal_sql()
    assert "c.billing_period" in sql
    assert "PARTITION BY" in sql


def test_inventory_status_vocabulary_is_closed():
    assert "NO_TEMPORAL_SNAPSHOT" in INVENTORY_STATUSES
    assert "CURRENT_SNAPSHOT" in INVENTORY_STATUSES
    assert "HISTORICAL_SNAPSHOT" in INVENTORY_STATUSES


def test_c2_enrich_does_not_retroproject_attributes():
    """Sem snapshot vigente, SKU/estado/SO ficam NULL e o tipo cai em UNKNOWN.

    A verificação é sobre o helper que aplica a regra: o `merge` com o inventário
    temporal pode não trazer linha alguma, e nesse caso os atributos precisam
    permanecer nulos — nunca herdados do snapshot de outro período.
    """
    import inspect

    from app.services.finops_cost_resource import enrich_c2_aggregate

    fonte = inspect.getsource(enrich_c2_aggregate)
    assert '"resource_sku"' in fonte
    assert '"UNKNOWN"' in fonte
    assert "NO_TEMPORAL_SNAPSHOT" in fonte


def test_c2_enrich_joins_inventory_by_period():
    """O merge do inventário inclui a competência: vigência por período."""
    import inspect

    from app.services.finops_cost_resource import enrich_c2_aggregate

    fonte = inspect.getsource(enrich_c2_aggregate)
    assert '"billing_period"' in fonte
    assert "canonical_resource_id" in fonte
    assert "sub_account_id" in fonte


def test_period_end_is_computed_by_date_arithmetic():
    from app.services.finops_cost_resource import _period_end_expr

    expr = _period_end_expr("bp")
    assert "DATE_ADD" in expr
    assert "'month', 1" in expr
    assert "'day', -1" in expr


# --- F: buckets determinísticos e sem pandas de milhões -----------------------


def test_bucket_is_deterministic_by_canonical_key():
    """Mesmo recurso, mesmo bucket; e a chave é a canônica."""
    pred = bucket_predicate("c.canonical_resource_id", 5)
    assert "CRC32" in pred
    assert "canonical_resource_id" in pred
    assert pred.endswith("= 5")


def test_bucket_coalesces_null_so_no_row_is_lost():
    """`CRC32(NULL)` é NULL: sem coalesce, 412.665 linhas de ajuste sumiriam."""
    pred = bucket_predicate("c.canonical_resource_id", 0)
    assert "COALESCE(c.canonical_resource_id, '')" in pred


def test_bucket_validates_range_and_count():
    with pytest.raises(ValueError):
        bucket_predicate("x", DEFAULT_BUCKETS)
    with pytest.raises(ValueError):
        bucket_count(buckets=0)
    assert bucket_count(buckets=8) == 8


def test_bucket_count_is_enough_for_the_heaviest_slice():
    """A fatia mais pesada (3,76 M linhas) precisa caber por bucket."""
    assert DEFAULT_BUCKETS >= 32


def test_bucket_sql_filters_one_period_and_connection():
    from app.services.finops_cost_resource import c2_bucket_projection_sql

    sql = c2_bucket_projection_sql(
        billing_period="202512", bucket=3, connection_code="azure-4p"
    )
    assert "'202512'" in sql
    assert "'azure-4p'" in sql


def test_bucket_sql_escapes_injected_period():
    """A competência vem da Silver, mas ainda assim vira literal escapado.

    O teste não procura "DROP TABLE" no texto — o valor escapado contém a string.
    O que se prova é que a aspa simples foi dobrada, então o valor permanece um
    literal e não fecha a string para virar SQL.
    """
    from app.services.finops_cost_resource import c2_bucket_projection_sql

    sql = c2_bucket_projection_sql(billing_period="202512'; DROP TABLE x --", bucket=0)
    assert "202512''; DROP TABLE x --" in sql
    # Nenhuma aspa simples solta antes do predicado de bucket.
    predicado = sql.index("CRC32")
    trecho = sql[:predicado]
    assert trecho.count("'") % 2 == 0, "aspa ímpar significaria literal aberto"


def test_c2_does_not_hold_the_whole_slice_raw():
    """A fatia é processada bucket a bucket, e as linhas cruas são descartadas.

    O processo segura o **agregado** acumulado, não as linhas Silver da fatia
    inteira. Sem o `del frame` a cada bucket, uma fatia de 3,76 M linhas ficaria
    toda em memória — que é exatamente o que a auditoria mandou evitar.
    """
    import inspect

    import app.services.finops_cost_resource as m

    fonte = inspect.getsource(m.materialize_c2_slice)
    # Itera bucket por bucket e descarta as linhas cruas.
    assert "for bucket in range(total_buckets)" in fonte
    assert "del frame" in fonte
    # Acumula apenas os agregados.
    assert "agregados.append(aggregate_c2_bucket(frame))" in fonte


def test_c2_frames_are_the_inventory_and_the_aggregate():
    """Os frames do C2 são inventário e agregado — nunca a linha Silver inteira."""
    import inspect

    import app.services.finops_cost_resource as m

    assert "pd.DataFrame" in inspect.getsource(m.materialize_c2_inventory)
    fonte_slice = inspect.getsource(m.materialize_c2_slice)
    # O frame por bucket existe, mas é do bucket e é descartado em seguida.
    assert "pd.DataFrame(linhas)" in fonte_slice
    assert "del frame" in fonte_slice


def test_projection_declares_explicit_types():
    """Tipo explícito por coluna: fatias de naturezas diferentes não divergem."""
    sql = c2_projection_sql("SELECT 1 AS billing_period")
    assert "CAST(billing_period AS varchar) AS billing_period" in sql
    assert "CAST(effective_cost AS double) AS effective_cost" in sql
    assert "CAST(first_charge_day AS date) AS first_charge_day" in sql


def test_output_columns_are_contract_not_internal():
    nomes = [nome for nome, _ in _C2_OUTPUT_TYPES]
    assert "build_bucket" not in nomes
    assert "billing_period" in nomes


# --- D: materializer_key -------------------------------------------------------


def test_materializer_keys_are_a_closed_allowlist():
    assert gm.is_registered(gm.MATERIALIZER_COVERAGE)
    assert gm.is_registered(gm.MATERIALIZER_C1)
    assert gm.is_registered(gm.MATERIALIZER_C2)
    assert gm.is_registered(gm.MATERIALIZER_C3)
    assert gm.is_registered("import os; os.system('rm -rf /')") is False


def test_unknown_materializer_key_raises():
    with pytest.raises(KeyError):
        gm.runner_for("nao_existe")
    with pytest.raises(KeyError):
        gm.spec_for("nao_existe")


def test_materializers_store_functions_not_names():
    """A coluna guarda chave; o código guarda a função. Nunca o contrário."""
    for chave, (spec, runner) in gm.MATERIALIZERS.items():
        assert callable(runner)
        assert spec.key == chave
        assert spec.physical_table
        assert spec.namespace == "gold"


def test_c3_depends_on_coverage():
    """C3 recusa par não comparável: a cobertura precisa vir antes."""
    assert gm.MATERIALIZER_COVERAGE in gm.spec_for(gm.MATERIALIZER_C3).depends_on


def test_execution_order_puts_dependencies_first():
    ordem = gm.execution_order(
        [gm.MATERIALIZER_C3, gm.MATERIALIZER_C1, gm.MATERIALIZER_COVERAGE]
    )
    assert ordem.index(gm.MATERIALIZER_COVERAGE) < ordem.index(gm.MATERIALIZER_C1)
    assert ordem.index(gm.MATERIALIZER_C1) < ordem.index(gm.MATERIALIZER_C3)


def test_execution_order_breaks_cycles_loudly(monkeypatch):
    spec_ciclo = gm.MaterializerSpec(
        key="a", physical_table="dest_a", namespace="gold", depends_on=("b",)
    )
    spec_b = gm.MaterializerSpec(
        key="b", physical_table="dest_b", namespace="gold", depends_on=("a",)
    )
    monkeypatch.setitem(gm.MATERIALIZERS, "a", (spec_ciclo, gm._run_c1))
    monkeypatch.setitem(gm.MATERIALIZERS, "b", (spec_b, gm._run_c1))
    with pytest.raises(RuntimeError):
        gm.execution_order(["a", "b"])


def test_seed_payload_declares_sources_and_materializer():
    from app.services.finops_gold_rules import _rule_payload

    for chave in (
        gm.MATERIALIZER_COVERAGE,
        gm.MATERIALIZER_C1,
        gm.MATERIALIZER_C2,
        gm.MATERIALIZER_C3,
    ):
        payload = _rule_payload(chave)
        assert payload["materializer_key"] == chave
        assert payload["gold_tables"], "a regra precisa declarar as fontes"
        assert "Contrato v" in payload["description"]


# --- cache e métricas ---------------------------------------------------------


def test_cache_key_includes_version_and_filters():
    a = cache_key("finops_cost_monthly", {"period": "202601"}, version="snap-1")
    b = cache_key("finops_cost_monthly", {"period": "202601"}, version="snap-2")
    c = cache_key("finops_cost_monthly", {"period": "202602"}, version="snap-1")
    assert a != b, "publicação nova precisa mudar a chave"
    assert a != c


def test_cache_key_ignores_filter_order():
    a = cache_key("c", {"a": 1, "b": 2})
    b = cache_key("c", {"b": 2, "a": 1})
    assert a == b


def test_cache_key_ignores_empty_filters():
    assert cache_key("c", {"a": None, "b": ""}) == cache_key("c", {})


def test_cache_roundtrip_and_invalidation():
    reset_state()
    set_cached("k", [1, 2, 3], version="snap-1")
    assert get_cached("k") == [1, 2, 3]
    invalidate_after_publish("finops_cost_monthly")
    assert get_cached("k") == [1, 2, 3], "invalidação é por cubo, não global"
    invalidate_after_publish()
    assert get_cached("k") is None


def test_metrics_report_has_p50_and_p95():
    reset_state()
    for valor in (10.0, 20.0, 30.0, 40.0, 100.0):
        record_request_metric("summary", valor)
    relatorio = metrics_report()
    assert relatorio["endpoints"]["summary"]["samples"] == 5
    assert relatorio["endpoints"]["summary"]["p50_ms"] is not None
    assert relatorio["endpoints"]["summary"]["p95_ms"] >= relatorio["endpoints"]["summary"]["p50_ms"]


def test_metrics_without_samples_reports_none_not_zero():
    """Sem amostra não há latência: `None`, não `0 ms`."""
    reset_state()
    assert metrics_report()["endpoints"] == {}


# --- C: regra de vigência, testável sem depender do dado existente -------------


def test_september_snapshot_does_not_describe_may():
    """A regra central da correção: valid_from <= fim da competência."""
    from app.services.finops_cost_resource import snapshot_is_valid_for_period

    assert (
        snapshot_is_valid_for_period(
            valid_from="2026-09-22", snapshot_period="202609", billing_period="202505"
        )
        is False
    )
    assert (
        snapshot_is_valid_for_period(
            valid_from="2026-09-22", snapshot_period="202609", billing_period="202609"
        )
        is True
    )


def test_snapshot_from_the_same_month_is_valid():
    from app.services.finops_cost_resource import snapshot_is_valid_for_period

    assert (
        snapshot_is_valid_for_period(
            valid_from="2026-09-30", snapshot_period="202609", billing_period="202609"
        )
        is True
    )


def test_snapshot_after_period_end_is_invalid():
    from app.services.finops_cost_resource import snapshot_is_valid_for_period

    # 22/09 não descreve agosto, que termina em 31/08.
    assert (
        snapshot_is_valid_for_period(
            valid_from="2026-09-22", snapshot_period="202609", billing_period="202608"
        )
        is False
    )


def test_snapshot_status_classification():
    from app.services.finops_cost_resource import inventory_status_for

    assert (
        inventory_status_for(billing_period="202609", snapshot_period="202609")
        == "CURRENT_SNAPSHOT"
    )
    assert (
        inventory_status_for(billing_period="202610", snapshot_period="202609")
        == "HISTORICAL_SNAPSHOT"
    )
    assert (
        inventory_status_for(billing_period="202505", snapshot_period=None)
        == "NO_TEMPORAL_SNAPSHOT"
    )
    assert (
        inventory_status_for(billing_period=None, snapshot_period="202609")
        == "NO_TEMPORAL_SNAPSHOT"
    )


def test_period_end_date_handles_leap_and_december():
    from app.services.finops_cost_resource import _period_end_date
    from datetime import date

    assert _period_end_date("202602") == date(2026, 2, 28)
    assert _period_end_date("202402") == date(2024, 2, 29)
    assert _period_end_date("202612") == date(2026, 12, 31)
    assert _period_end_date("202513") is None
    assert _period_end_date("") is None


def test_historical_period_gets_null_attributes():
    """Período sem snapshot vigente não recebe tipo, SKU, estado nem SO."""
    from app.services.finops_cost_resource import inventory_status_for

    status = inventory_status_for(billing_period="202505", snapshot_period=None)
    assert status == "NO_TEMPORAL_SNAPSHOT"
    # Sem snapshot, o contrato obriga NULL/UNKNOWN — está no helper de enriquecimento.
    import inspect

    from app.services.finops_cost_resource import enrich_c2_aggregate

    fonte = inspect.getsource(enrich_c2_aggregate)
    assert '"resource_sku"' in fonte
    assert '"UNKNOWN"' in fonte
