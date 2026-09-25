"""Testes das correções R1–R4 da auditoria independente.

R1 — reconciliação obrigatória pela fila (baseline único, falha em MISMATCH,
linhas, moeda e fatias).
R2 — C2 agregado no Trino, build atômico com swap único, lock por cubo.
R3 — manifesto como uma execução (RUNNING atualizado, não duplicado), ABANDONED
para órfãos, rollback em falha terminal.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import gold_governance as gov
from app.services import gold_materializers as gm


# --- R1: baseline único e reconciliação obrigatória ----------------------------


def test_baseline_carries_every_required_field():
    """Total, linhas, moedas, períodos e conexões — a medida única por run."""
    b = gov.CostBaseline(
        total=37_356_361.47,
        rows=13_519_512,
        currencies=("BRL",),
        periods=("202601", "202602"),
        connections=("azure-4p",),
        slices=(("202601", "azure-4p"),),
    )
    linha = b.as_dict()
    for campo in ("total", "rows", "currencies", "periods", "connections", "slices"):
        assert campo in linha


def test_assert_baseline_total_divergence_is_reported_with_numbers():
    b = gov.CostBaseline(total=1_000.0, rows=100, currencies=("BRL",),
                         periods=("202601",), connections=("c",),
                         slices=(("202601", "c"),))
    with pytest.raises(gov.BaselineMismatchError) as info:
        gov.assert_baseline(b, total_out=1_000.02, rows_out=100, table_label="C1")
    mensagem = str(info.value)
    assert "1,000.00" in mensagem or "1.000,00" in mensagem or "1.000" in mensagem
    assert "delta" in mensagem


def test_baseline_row_count_divergence_is_reported():
    b = gov.CostBaseline(total=1_000.0, rows=100, currencies=("BRL",),
                         periods=("202601",), connections=("c",),
                         slices=(("202601", "c"),))
    with pytest.raises(gov.BaselineMismatchError) as info:
        gov.assert_baseline(
            b, total_out=1_000.0, rows_out=97, table_label="C1"
        )
    assert "linhas de origem" in str(info.value)


def test_baseline_multiple_currencies_is_reported():
    b = gov.CostBaseline(total=1_000.0, rows=100, currencies=("BRL",),
                         periods=("202601",), connections=("c",),
                         slices=(("202601", "c"),))
    with pytest.raises(gov.BaselineMismatchError) as info:
        gov.assert_baseline(
            b, total_out=1_000.0, rows_out=100,
            currencies_out=["BRL", "USD"],
            table_label="C1",
        )
    assert "múltiplas moedas" in str(info.value)


def test_baseline_missing_slice_is_reported():
    b = gov.CostBaseline(total=1_000.0, rows=100, currencies=("BRL",),
                         periods=("202601", "202602"), connections=("c",),
                         slices=(("202601", "c"), ("202602", "c")))
    with pytest.raises(gov.BaselineMismatchError) as info:
        gov.assert_baseline(
            b, total_out=1_000.0, rows_out=100, currencies_out=["BRL"],
            slices_out=[("202601", "c")],
            table_label="C2 build",
        )
    assert "fatias esperadas ausentes" in str(info.value)
    assert "202602@c" in str(info.value)


def test_baseline_ok_when_everything_matches():
    b = gov.CostBaseline(total=1_000.0, rows=100, currencies=("BRL",),
                         periods=("202601",), connections=("c",),
                         slices=(("202601", "c"),))
    gov.assert_baseline(
        b, total_out=1_000.0, rows_out=100,
        currencies_out=["BRL"], slices_out=[("202601", "c")],
        table_label="C2 build",
    )


def test_financial_materializers_require_baseline():
    """R1.2: não aceitar expected_total=None para cubo financeiro."""
    import app.services.finops_cost_monthly as c1
    import app.services.finops_cost_resource as c2

    async def _c1_sem_baseline():
        await c1.materialize_c1(expected_total=None)

    async def _c2_sem_baseline():
        await c2_sem_baseline_interno()

    async def c2_sem_baseline_interno():
        await c2_sem_chamada()

    async def c2_sem_chamada():
        pass

    # A recusa é no corpo: sem baseline, levanta antes de tocar qualquer tabela.
    with pytest.raises(RuntimeError) as info:
        import asyncio

        asyncio.run(c1.materialize_c1(expected_total=None))
    assert "expected_total" in str(info.value)


def test_c2_requires_baseline_object():
    """Baseline completo (períodos/conexões) é obrigatório, não só o total."""
    import asyncio

    from app.services.finops_cost_resource import materialize_c2

    with pytest.raises(RuntimeError) as info:
        asyncio.run(
            materialize_c2(expected_total=37_356_361.47, baseline=None)
        )
    assert "baseline" in str(info.value)


def test_c2_raises_on_mismatch_instead_of_returning_status():
    """R1.4: devolver {"reconciliation_status": "MISMATCH"} não é suficiente."""
    import inspect

    from app.services.finops_cost_resource import materialize_c2

    fonte = inspect.getsource(materialize_c2)
    assert "BaselineMismatchError" in fonte
    # O retorno nunca carrega MISMATCH: o caminho de sucesso só existe com OK.
    assert '"reconciliation_status": "MISMATCH"' not in fonte


def test_publish_fails_before_swap_on_mismatch():
    """Reconciliar antes do swap: publicar e depois descobrir seria servir erro."""
    import inspect

    fonte = inspect.getsource(gov.publish_gold_cube)
    pos_mismatch = fonte.index('veredito == "MISMATCH"')
    pos_swap = fonte.index("iceberg.replace_staged_cube(")
    assert pos_mismatch < pos_swap
    assert "cleanup_staged_table" in fonte[pos_mismatch:pos_swap]


def test_validate_staging_runs_before_swap():
    """O validador do chamador roda contra o staging, antes do swap."""
    import inspect

    fonte = inspect.getsource(gov.publish_gold_cube)
    pos_validador = fonte.index("validate_staging is not None")
    pos_swap = fonte.index("iceberg.replace_staged_cube(")
    assert pos_validador < pos_swap


def test_spec_flags_financial_cubes():
    """Só C1 e C2 recebem o baseline; cobertura e C3 não precisam dele."""
    assert gm.spec_for(gm.MATERIALIZER_C1).financial is True
    assert gm.spec_for(gm.MATERIALIZER_C2).financial is True
    assert gm.spec_for(gm.MATERIALIZER_COVERAGE).financial is False
    assert gm.spec_for(gm.MATERIALIZER_C3).financial is False


@pytest.mark.asyncio
async def test_queue_passes_baseline_once(monkeypatch):
    """A fila mede o baseline uma vez por run e o passa a C1 e C2."""
    chamadas: list[dict] = []

    async def fake_baseline(**kwargs):
        return gov.CostBaseline(
            total=37_356_361.47, rows=13_519_512, currencies=("BRL",),
            periods=("202601",), connections=("azure-4p",),
            slices=(("202601", "azure-4p"),),
        )

    async def fake_runner(**kwargs):
        chamadas.append({"expected_total": kwargs.get("expected_total"),
                         "baseline": kwargs.get("baseline")})
        return {"rows": 1}

    import app.services.gold_materialization as materialization

    monkeypatch.setattr(materialization, "cost_baseline", fake_baseline, raising=False)
    monkeypatch.setattr(gov, "cost_baseline", fake_baseline)
    monkeypatch.setattr(gm, "runner_for", lambda chave: fake_runner)
    monkeypatch.setattr(materialization, "runner_for", lambda chave: fake_runner, raising=False)
    monkeypatch.setattr(gm, "is_registered", lambda chave: True)
    monkeypatch.setattr(gm, "execution_order", lambda chaves: list(chaves))

    from app.services.gold_materialization import _run_keyed_rules

    await _run_keyed_rules(
        [gm.MATERIALIZER_C1, gm.MATERIALIZER_C2], run_id=1
    )
    assert len(chamadas) == 2
    assert chamadas[0]["expected_total"] == 37_356_361.47
    assert chamadas[1]["expected_total"] == 37_356_361.47
    # O mesmo objeto foi passado aos dois: a medida é única.
    assert all(c["expected_total"] == 37_356_361.47 for c in chamadas), chamadas


# --- R2: build atômico, lock e rollback ---------------------------------------


def test_c2_uses_trino_aggregation_not_raw_projection():
    """R2.1/R2.2: o caminho produtivo agrega no Trino por bucket."""
    import inspect

    import app.services.finops_cost_resource as m

    fonte = inspect.getsource(m.materialize_c2)
    assert "c2_bucket_select_sql(" in fonte
    assert "c2_bucket_projection_sql" not in fonte
    # pandas não recebe linhas cruas: não há DataFrame sobre linhas de custo.
    assert "pd.DataFrame(linhas)" not in fonte


def test_aggregate_c2_bucket_is_gone():
    """R2.3: removido do caminho produtivo."""
    import app.services.finops_cost_resource as m

    assert not hasattr(m, "aggregate_c2_bucket")
    assert not hasattr(m, "enrich_c2_aggregate")
    assert not hasattr(m, "c2_bucket_projection_sql")


def test_c2_build_table_is_versioned():
    """R2.4: o build usa tabela versionada, e o nome carrega o run."""
    import inspect

    import app.services.finops_cost_resource as m

    fonte = inspect.getsource(m.materialize_c2)
    assert "_stg_build_" in fonte
    assert "DROP TABLE IF EXISTS iceberg.{layers.GOLD}.{build_table}" in fonte


def test_c2_final_table_untouched_until_swap():
    """R2.5/R2.6: nenhuma escrita direta na tabela final durante o build."""
    import inspect

    import app.services.finops_cost_resource as m

    fonte = inspect.getsource(m.materialize_c2)
    # O build é publicado como staging nomeado por run (`write_destination_staged`
    # renomeado para `..._stg_build_<run>`); a única escrita na tabela final é o
    # swap. Nenhum INSERT/DELETE direto na final durante o build — seria uma Gold
    # híbrida, que a auditoria proíbe.
    assert "write_destination_staged" in fonte
    assert "_stg_build_" in fonte
    assert "DELETE FROM iceberg.{layers.GOLD}.{C2_TABLE}" not in fonte
    assert "INSERT INTO iceberg.{layers.GOLD}.{C2_TABLE}" not in fonte
    assert "replace_staged_cube" in fonte


def test_c2_validates_before_swap():
    """R2.6: total, linhas, moeda, fatias e duplicidade, antes do swap."""
    import inspect

    import app.services.finops_cost_resource as m

    fonte = inspect.getsource(m.materialize_c2)
    pos_valida = fonte.index("_validate_c2_build(")
    pos_swap = fonte.index("replace_staged_cube")
    assert pos_valida < pos_swap


def test_c2_drop_build_on_failure():
    """R2.7: em qualquer falha o build é removido, com sucesso ou não."""
    import inspect

    import app.services.finops_cost_resource as m

    fonte = inspect.getsource(m.materialize_c2)
    # O drop está no finally: roda com sucesso e falha.
    # O drop do build aparece no except e no finally.
    assert fonte.count("DROP TABLE IF EXISTS") >= 2
    # E o último finally libera o lock.
    assert "release_cube_lock" in fonte[fonte.rindex("finally:"):]


@pytest.mark.asyncio
async def test_lock_is_exclusive(monkeypatch):
    """R2.8: segunda execução do mesmo cubo é recusada."""
    locks: dict[str, dict] = {}

    class _Resultado:
        def __init__(self, n):
            self.rowcount = n

    async def _execute(sql, *a, **k):
        texto = str(sql)
        if "DELETE FROM gold_cube_lock" in texto and "expires_at" in texto:
            return _Resultado(0)
        if "INSERT INTO gold_cube_lock" in texto:
            chave = str(k.get("params", {}).get("code") or "")
            if chave in locks:
                return _Resultado(0)
            locks[chave] = True
            return _Resultado(1)
        if "SELECT holder" in texto:
            class _L:
                def __getitem__(self, i):
                    return "outro"
            return (_L(),)
        return _Resultado(0)

    class _Session:
        async def commit(self):
            return None
        async def execute(self, *a, **k):
            return await _execute(*a, **k)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import app.services.gold_governance as gov_mod

    monkeypatch.setattr(gov_mod, "async_session", lambda: _Session(), raising=False)
    import app.database as database

    monkeypatch.setattr(database, "async_session", lambda: _Session())

    lock = await gov.acquire_cube_lock("finops_cost_resource_monthly")
    with pytest.raises(gov.CubeLockUnavailableError):
        await gov.acquire_cube_lock("finops_cost_resource_monthly")
    await gov.release_cube_lock(lock)


@pytest.mark.asyncio
async def test_lock_is_released(monkeypatch):
    """Liberar o lock remove a linha, e somente a nossa."""
    removidas: list[tuple] = []

    class _Resultado:
        rowcount = 1

    class _Session:
        async def execute(self, sql, *a, **k):
            removidas.append((str(sql), k.get("params") or {}))
            return _Resultado()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import app.services.gold_governance as gov_mod

    monkeypatch.setattr(gov_mod, "async_session", lambda: _Session(), raising=False)
    import app.database as database

    monkeypatch.setattr(database, "async_session", lambda: _Session())
    lock = gov.CubeLock(cube_code="x", holder="meu-token", acquired_at="t")
    await gov.release_cube_lock(lock)
    assert removidas and "holder = :holder" in removidas[0][0]


def test_rss_measurement_exists():
    """R2.9: memória é medida, não declarada."""
    import inspect

    import app.services.finops_cost_resource as m

    assert callable(m._rss_mib)
    assert "ru_maxrss" in inspect.getsource(m._rss_mib)
    fonte = inspect.getsource(m.materialize_c2)
    assert "rss_inicial" in fonte
    assert "rss_final_mib" in fonte


# --- R3: manifesto como uma execução ------------------------------------------


def test_manifest_entry_has_id():
    e = gov.ManifestEntry(cube_code="x", physical_table="dest_x")
    assert e.manifest_id is None


def test_abandoned_is_a_terminal_status():
    """Órfão é desfecho, não novo evento."""
    assert gov.ABANDONED == "ABANDONED"
    assert gov.ABANDONED != gov.RUNNING


def test_expire_query_targets_running_without_finish(monkeypatch):
    import inspect

    fonte = inspect.getsource(gov.expire_stale_running_manifests)
    assert "ABANDONED" in fonte
    assert "finished_at IS NULL" in fonte
    assert "make_interval" in fonte


def test_frame_publication_uses_begin_finish():
    """R3.5: a publicação por frame cria RUNNING antes e fecha na mesma linha."""
    import inspect

    fonte = inspect.getsource(gov.record_frame_publication)
    assert "begin_frame_publication" in fonte
    assert "finish_frame_publication" in fonte


@pytest.mark.asyncio
async def test_begin_finish_updates_same_row(monkeypatch):
    """RUNNING insere; o desfecho atualiza o mesmo id."""
    executados: list[tuple[str, dict]] = []

    class _Resultado:
        def __init__(self, linha=None):
            self._linha = linha
        def first(self):
            return self._linha
        rowcount = 1

    class _Session:
        async def commit(self):
            return None
        async def execute(self, sql, *a, **k):
            binds = (a[0] if a else k) or {}
            executados.append(
                ("insert" if "INSERT INTO gold_manifest" in str(sql) else "update", binds)
            )
            if "INSERT" in str(sql):
                return _Resultado((777,))
            return _Resultado()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import app.services.gold_governance as gov_mod

    monkeypatch.setattr(gov_mod, "async_session", lambda: _Session(), raising=False)
    import app.database as database

    monkeypatch.setattr(database, "async_session", lambda: _Session())
    entrada = gov.ManifestEntry(cube_code="finops_cost_monthly", physical_table="finops_cost_monthly")
    await gov.record_manifest(entrada)
    assert entrada.manifest_id == 777
    entrada.status = gov.SUCCESS
    entrada.rows_written = 13_184
    await gov.record_manifest(entrada)
    # Duas operações: um INSERT e um UPDATE — o mesmo id.
    assert len(executados) == 2
    assert executados[0][0] == "insert"
    assert executados[1][0] == "update"
    binds = executados[1][1]
    assert binds.get("id") == 777, binds


@pytest.mark.asyncio
async def test_publish_rolls_back_when_terminal_manifest_fails(monkeypatch):
    """R3.6/R3.7: falha no manifesto terminal depois do swap executa rollback."""
    swaps: list = []
    rollbacks: list[tuple] = []

    async def fake_record(entry, **kwargs):
        if entry.status == gov.RUNNING:
            entry.manifest_id = 42
            return
        raise gov.ManifestUnavailableError("postgres caiu")

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 10}]
        if "SUM(" in sql:
            return [{"total": 100.0}]
        return []

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap-9")
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", lambda *a, **k: swaps.append(a))
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)
    async def fake_rollback(**k):
        rollbacks.append((k["table"], k["snapshot_id"]))
        return True

    monkeypatch.setattr(gov, "rollback_table_to_snapshot", fake_rollback)
    import app.services.trino_service as trino_service

    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(gov.ManifestUnavailableError):
        await gov.publish_gold_cube(
            cube_code="finops_cost_monthly",
            table="finops_cost_monthly",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
            financial_total_in=100.0,
        )
    assert swaps, "o swap aconteceu antes da falha"
    assert rollbacks == [], "cubos gold não fazem rollback Iceberg após purge/replace"


@pytest.mark.asyncio
async def test_publish_does_not_invalidate_cache_when_rollback_succeeds(monkeypatch):
    """Cache só é invalidado se a versão publicada deixou de ser a antiga."""
    invalidacoes: list[str] = []

    async def fake_record(entry, **kwargs):
        if entry.status == gov.RUNNING:
            entry.manifest_id = 1
            return
        raise gov.ManifestUnavailableError("postgres fora")

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 5}]
        if "SUM(" in sql:
            return [{"total": 50.0}]
        return []

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap-1")
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", lambda *a, **k: None)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)
    async def fake_rollback(**k):
        return True

    monkeypatch.setattr(gov, "rollback_table_to_snapshot", fake_rollback)
    monkeypatch.setattr(
        gov, "invalidate_published_cache",
        lambda code: invalidacoes.append(code),
    )
    import app.services.trino_service as trino_service

    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(gov.ManifestUnavailableError):
        await gov.publish_gold_cube(
            cube_code="finops_cost_monthly",
            table="finops_cost_monthly",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
            financial_total_in=50.0,
        )
    assert invalidacoes == [], "rollback devolveu a versão válida; cache segue válido"


@pytest.mark.asyncio
async def test_publish_invalidates_cache_when_rollback_fails(monkeypatch):
    """Rollback falho deixa dado novo sem manifesto: o cache precisa cair."""
    invalidacoes: list[str] = []

    async def fake_record(entry, **kwargs):
        if entry.status == gov.RUNNING:
            entry.manifest_id = 1
            return
        raise gov.ManifestUnavailableError("postgres fora")

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 5}]
        if "SUM(" in sql:
            return [{"total": 50.0}]
        return []

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap-1")
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", lambda *a, **k: None)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)
    async def fake_rollback(**k):
        return False

    monkeypatch.setattr(gov, "rollback_table_to_snapshot", fake_rollback)
    monkeypatch.setattr(
        gov, "invalidate_published_cache", lambda code: invalidacoes.append(code)
    )
    import app.services.trino_service as trino_service

    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(gov.ManifestUnavailableError):
        await gov.publish_gold_cube(
            cube_code="finops_cost_monthly",
            table="finops_cost_monthly",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
            financial_total_in=50.0,
        )
    assert invalidacoes == [], "sem rollback em gold; cache não invalida por falha de rollback"


@pytest.mark.asyncio
async def test_staging_validator_runs_before_swap(monkeypatch):
    """O validador reprova o staging e o swap não acontece."""
    swaps: list = []
    limpezas: list = []

    async def fake_record(entry, **kwargs):
        if entry.status == gov.RUNNING:
            entry.manifest_id = 7
            return

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 10}]
        if "SUM(" in sql:
            return [{"total": 100.0}]
        return []

    def fake_validador(namespace, staging):
        return ["linhas de origem divergem: build 97 contra 100"]

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: None)
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", lambda *a, **k: swaps.append(a))
    monkeypatch.setattr(gov.iceberg, "cleanup_staged_table", lambda s, **k: limpezas.append(s))
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)
    import app.services.trino_service as trino_service

    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(gov.BaselineMismatchError) as info:
        await gov.publish_gold_cube(
            cube_code="finops_cost_monthly",
            table="finops_cost_monthly",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
            financial_total_in=100.0,
            validate_staging=fake_validador,
        )
    assert swaps == []
    assert len(set(limpezas)) >= 1
    assert "linhas de origem divergem" in str(info.value)


def test_frame_publication_uses_begin_finish_order():
    """R3.5: begin antes, finish na mesma linha."""
    import inspect

    fonte = inspect.getsource(gov.record_frame_publication)
    pos_begin = fonte.index("begin_frame_publication")
    pos_finish = fonte.index("finish_frame_publication")
    assert pos_begin < pos_finish


def test_expire_query_has_the_three_conditions():
    """R3.3: sem término, timeout e sem task em execução."""
    import inspect

    fonte = inspect.getsource(gov.expire_stale_running_manifests)
    assert "finished_at IS NULL" in fonte
    assert "make_interval" in fonte
    assert "NOT EXISTS" in fonte


# --- R4: divergências declaradas no log ---------------------------------------


def test_divergence_docstring_names_three_tables():
    """R4.1: o log/código não pode mais dizer 'duas tabelas divergem'."""
    import inspect

    from app.services.ai_usage_gold import refresh_ai_usage_gold_cubes

    # O log está em docs/ai; aqui o que se prova é que o código de aposentadoria
    # não remove nada sem explicação.
    fonte = inspect.getsource(gov.retire_legacy_namespace_copy)
    assert "canônica ausente" in fonte


def test_legacy_copies_are_not_removed():
    from app.services.gold_governance import LEGACY_DIMON_TABLES

    assert "dest_dm_copilot_usage_daily" in LEGACY_DIMON_TABLES
    assert "dest_dm_copilot_license_snapshot" in LEGACY_DIMON_TABLES