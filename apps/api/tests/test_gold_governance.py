"""Testes da governança de materialização Gold (G2).

O aceite de G2 exige: falha preserva o cubo anterior; o manifesto prova fontes e
totais; não existe consumidor do caminho desativado; e um dependente não publica
com fonte mais antiga que o limite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import gold_governance as gov


# --- hash e dependências ------------------------------------------------------


def test_sql_fingerprint_ignores_whitespace():
    a = "SELECT a, b FROM iceberg.silver.resource_cost WHERE x = 1"
    b = "select a,\n   b\nfrom iceberg.silver.resource_cost\nwhere x = 1"
    assert gov.sql_fingerprint(a) == gov.sql_fingerprint(b)


def test_sql_fingerprint_changes_with_real_edit():
    a = "SELECT SUM(effective_cost) FROM iceberg.silver.resource_cost"
    b = "SELECT SUM(billed_cost) FROM iceberg.silver.resource_cost"
    assert gov.sql_fingerprint(a) != gov.sql_fingerprint(b)


def test_sql_fingerprint_handles_empty():
    assert gov.sql_fingerprint("") == gov.sql_fingerprint(None)


def test_extract_sources_is_deduplicated_and_ordered():
    sql = """
      SELECT * FROM iceberg.gold.dest_dm_ai_consumption_daily a
      JOIN iceberg.silver.dest_consumo_ia b ON b.x = a.x
      JOIN iceberg.gold.dest_dm_ai_consumption_daily c ON c.y = a.y
    """
    assert gov.extract_sources(sql) == [
        "gold.dest_dm_ai_consumption_daily",
        "silver.dest_consumo_ia",
    ]


def test_extract_sources_handles_no_source():
    assert gov.extract_sources("SELECT 1") == []


def test_cube_dependencies_only_counts_gold_cubes():
    """Ler Silver não entra no gate de freshness; ler cubo entra."""
    sql = """
      SELECT * FROM iceberg.silver.dest_consumo_ia s
      JOIN iceberg.gold.dest_dm_ai_consumption_daily g ON g.x = s.x
      JOIN iceberg.gold._tmp_vm_monthly t ON t.x = s.x
    """
    assert gov.cube_dependencies(sql) == ["gold.dest_dm_ai_consumption_daily"]


def test_cube_dependencies_matches_configured_namespace():
    sql = "SELECT * FROM iceberg.dimon.dest_dm_ai_license_snapshot"
    assert gov.cube_dependencies(sql, namespace="dimon") == [
        "dimon.dest_dm_ai_license_snapshot"
    ]
    assert gov.cube_dependencies(sql, namespace="gold") == []


# --- sanitização de erro ------------------------------------------------------


def test_sanitize_error_redacts_connection_url():
    exc = RuntimeError("cannot connect: postgresql://finops:s3cr3t@db:5432/finops")
    limpo = gov.sanitize_error(exc)
    assert "s3cr3t" not in limpo
    assert "[redigido]" in limpo


def test_sanitize_error_redacts_github_token():
    """O `.env` deste repositório tem um token real; erro de extração não pode vazá-lo."""
    token = "ghp_" + "A" * 24
    limpo = gov.sanitize_error(f"401 unauthorized for {token}")
    assert token not in limpo


def test_sanitize_error_redacts_key_value_secrets():
    limpo = gov.sanitize_error("account_key=AAAAAAAAAAAAAAAAAAAAAAAA== failed")
    assert "AAAAAAAAAAAAAAAAAAAAAAAA" not in limpo


def test_sanitize_error_redacts_bearer():
    limpo = gov.sanitize_error("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 failed")
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in limpo


def test_sanitize_error_truncates_and_flattens():
    limpo = gov.sanitize_error("linha1\nlinha2\n" + "x" * 2000, limit=100)
    assert "\n" not in limpo
    assert len(limpo) <= 101


# --- reconciliação financeira -------------------------------------------------


def test_financial_delta_ok_within_tolerance():
    delta, veredito = gov.compute_financial_delta(total_in=1000.0, total_out=1000.01)
    assert delta == pytest.approx(0.01)
    assert veredito == "OK"


def test_financial_delta_mismatch_above_tolerance():
    delta, veredito = gov.compute_financial_delta(total_in=1000.0, total_out=1000.02)
    assert veredito == "MISMATCH"


def test_financial_delta_not_measured_is_not_zero():
    """Cubo de dimensão não soma dinheiro: `NOT_MEASURED`, não `OK` nem zero."""
    delta, veredito = gov.compute_financial_delta(total_in=None, total_out=1000.0)
    assert delta is None
    assert veredito == "NOT_MEASURED"


def test_financial_delta_measured_zero_is_zero():
    """Total medido que vale zero é diferente de total não medido."""
    delta, veredito = gov.compute_financial_delta(total_in=0.0, total_out=0.0)
    assert delta == 0.0
    assert veredito == "OK"


# --- manifesto -----------------------------------------------------------------


def test_manifest_entry_carries_every_contract_field():
    """Seção 6.2: o manifesto precisa provar fontes, totais e rollback."""
    entrada = gov.ManifestEntry(
        cube_code="mvp_custo_recurso",
        physical_table="dest_dm_cost_mvp_custo_recurso",
        run_id=42,
        rule_id=2387,
        sql_hash=gov.sql_fingerprint("SELECT 1"),
        source_tables=["silver.resource_cost"],
        financial_total_in=37356361.47,
        currency="BRL",
        previous_snapshot_id="1234567890",
    )
    linha = entrada.as_dict()
    for campo in (
        "cube_code",
        "physical_table",
        "namespace",
        "contract_version",
        "run_id",
        "sql_hash",
        "source_tables",
        "financial_total_in",
        "financial_total_out",
        "financial_delta",
        "tolerance",
        "reconciliation_status",
        "previous_snapshot_id",
        "rollback_table",
        "error",
        "started_at",
        "finished_at",
    ):
        assert campo in linha
    assert linha["namespace"] == "gold"
    assert linha["contract_version"] == gov.MANIFEST_CONTRACT_VERSION


def test_manifest_default_status_is_running():
    entrada = gov.ManifestEntry(cube_code="x", physical_table="dest_x")
    assert entrada.status == gov.RUNNING


def test_manifest_status_vocabulary():
    assert gov.RUNNING == "RUNNING"
    assert gov.SUCCESS == "SUCCESS"
    assert gov.PARTIAL == "PARTIAL"
    assert gov.FAILED == "FAILED"


# --- freshness de dependência -------------------------------------------------


@pytest.mark.asyncio
async def test_stale_dependency_blocks_publication(monkeypatch):
    """Um dependente não publica em cima de fonte vencida (seção 6.3)."""
    velho = datetime.now(timezone.utc) - timedelta(hours=gov.DEFAULT_SOURCE_MAX_AGE_HOURS + 1)

    async def fake_freshness(cube_code):
        return velho

    monkeypatch.setattr(gov, "source_freshness", fake_freshness)
    with pytest.raises(gov.StaleDependencyError) as info:
        await gov.assert_dependencies_fresh(
            "SELECT * FROM iceberg.gold.dest_dm_ai_consumption_daily"
        )
    assert "dest_dm_ai_consumption_daily" in str(info.value)


@pytest.mark.asyncio
async def test_fresh_dependency_allows_publication(monkeypatch):
    recente = datetime.now(timezone.utc) - timedelta(hours=1)

    async def fake_freshness(cube_code):
        return recente

    monkeypatch.setattr(gov, "source_freshness", fake_freshness)
    await gov.assert_dependencies_fresh(
        "SELECT * FROM iceberg.gold.dest_dm_ai_consumption_daily"
    )


@pytest.mark.asyncio
async def test_missing_manifest_does_not_block(monkeypatch):
    """Cubos anteriores ao manifesto existem e são usados; bloquear seria pior."""

    async def fake_freshness(cube_code):
        return None

    monkeypatch.setattr(gov, "source_freshness", fake_freshness)
    await gov.assert_dependencies_fresh(
        "SELECT * FROM iceberg.gold.dest_dm_ai_consumption_daily"
    )


@pytest.mark.asyncio
async def test_only_cube_sources_are_gated(monkeypatch):
    """Silver não tem limite de idade declarado: não entra no gate."""
    chamadas = []

    async def fake_freshness(cube_code):
        chamadas.append(cube_code)
        return None

    monkeypatch.setattr(gov, "source_freshness", fake_freshness)
    await gov.assert_dependencies_fresh("SELECT * FROM iceberg.silver.resource_cost")
    assert chamadas == []


# --- publicação atômica -------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_failure_preserves_previous_table(monkeypatch):
    """Falha no staging não pode tocar a tabela publicada (aceite de G2)."""
    registros = []
    swaps = []
    limpezas = []

    async def fake_execute(sql, **kwargs):
        if sql.startswith("CREATE TABLE"):
            raise RuntimeError("Trino morreu no meio da CTAS")
        return []

    async def fake_record(entry):
        registros.append((entry.status, entry.error))

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap-anterior")
    monkeypatch.setattr(gov, "assert_dependencies_fresh", lambda *a, **k: _noop())

    def fake_swap(*a, **k):
        swaps.append(a)

    def fake_cleanup(staging, namespace=None):
        limpezas.append(staging)

    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", fake_swap)
    monkeypatch.setattr(gov.iceberg, "cleanup_staged_table", fake_cleanup)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)

    import app.services.trino_service as trino_service
    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(RuntimeError):
        await gov.publish_gold_cube(
            cube_code="teste",
            table="dest_teste",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
        )

    assert swaps == [], "falha não pode ter feito swap"
    assert len(limpezas) == 1, "staging precisa ser removido"
    assert registros[0][0] == gov.RUNNING
    assert registros[-1][0] == gov.FAILED
    assert "Trino morreu" in (registros[-1][1] or "")


async def _noop():
    return None


@pytest.mark.asyncio
async def test_publish_empty_is_partial_not_success(monkeypatch):
    registros = []
    limpezas = []

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 0}]
        return []

    async def fake_record(entry):
        registros.append(entry.status)

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: None)
    monkeypatch.setattr(gov, "assert_dependencies_fresh", lambda *a, **k: _noop())
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", lambda *a, **k: None)
    monkeypatch.setattr(gov.iceberg, "cleanup_staged_table", lambda s, namespace=None: limpezas.append(s))
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)

    import app.services.trino_service as trino_service
    monkeypatch.setattr(trino_service, "execute", fake_execute)

    saida = await gov.publish_gold_cube(
        cube_code="teste",
        table="dest_teste",
        select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
    )
    assert saida.rows == 0
    assert saida.status == gov.PARTIAL
    assert not saida.published
    assert registros[-1] == gov.PARTIAL


@pytest.mark.asyncio
async def test_publish_empty_can_be_error_when_required(monkeypatch):
    """As Gold de IA recusam materialização vazia; a semântica anterior é mantida."""
    registros = []

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 0}]
        return []

    async def fake_record(entry):
        registros.append(entry.status)

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: None)
    monkeypatch.setattr(gov, "assert_dependencies_fresh", lambda *a, **k: _noop())
    monkeypatch.setattr(gov.iceberg, "cleanup_staged_table", lambda s, namespace=None: None)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)

    import app.services.trino_service as trino_service
    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(RuntimeError):
        await gov.publish_gold_cube(
            cube_code="ai_consumption_daily",
            table="dest_dm_ai_consumption_daily",
            select_sql="SELECT 1 FROM iceberg.silver.dest_consumo_ia",
            empty_is_error=True,
        )
    assert registros[-1] == gov.FAILED


@pytest.mark.asyncio
async def test_publish_success_records_rollback_pointer_and_delta(monkeypatch):
    registros = []
    swap = []

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 1354962}]
        if "SUM(" in sql:
            return [{"total": 37356361.47}]
        return []

    async def fake_record(entry):
        registros.append(entry)

    def fake_swap(table, staging, namespace=None, known_row_count=None):
        swap.append((table, known_row_count))

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap-42")
    monkeypatch.setattr(gov, "assert_dependencies_fresh", lambda *a, **k: _noop())
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", fake_swap)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)

    import app.services.trino_service as trino_service
    monkeypatch.setattr(trino_service, "execute", fake_execute)

    saida = await gov.publish_gold_cube(
        cube_code="mvp_custo_recurso",
        table="dest_dm_cost_mvp_custo_recurso",
        select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
        financial_total_in=37356361.47,
    )
    assert saida.published is True
    assert saida.rows == 1354962
    assert swap == [("dest_dm_cost_mvp_custo_recurso", 1354962)]
    assert saida.previous_snapshot_id == "snap-42"
    assert saida.reconciliation_status == "OK"
    assert saida.financial_delta == 0.0
    assert registros[-1].status == gov.SUCCESS
    assert registros[-1].previous_snapshot_id == "snap-42"


@pytest.mark.asyncio
async def test_publish_rejects_mixed_currency(monkeypatch):
    """Somar BRL e USD na mesma medida é proibido: falha antes do swap."""
    registros = []
    swap = []

    async def fake_execute(sql, **kwargs):
        if "COUNT(*)" in sql:
            return [{"row_count": 10}]
        if "DISTINCT LOWER" in sql or "DISTINCT CAST(currency" in sql:
            return [{"currency": "brl"}, {"currency": "usd"}]
        return []

    async def fake_record(entry):
        registros.append(entry.status)

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: None)
    monkeypatch.setattr(gov, "assert_dependencies_fresh", lambda *a, **k: _noop())
    monkeypatch.setattr(gov.iceberg, "replace_staged_cube", lambda *a, **k: swap.append(a))
    monkeypatch.setattr(gov.iceberg, "cleanup_staged_table", lambda s, namespace=None: None)
    monkeypatch.setattr(gov.iceberg, "ensure_namespace", lambda *a, **k: None)

    import app.services.trino_service as trino_service
    monkeypatch.setattr(trino_service, "execute", fake_execute)

    with pytest.raises(Exception):
        await gov.publish_gold_cube(
            cube_code="custo",
            table="dest_custo",
            select_sql="SELECT 1 FROM iceberg.silver.resource_cost",
            currency="BRL",
        )
    assert swap == []
    assert registros[-1] == gov.FAILED


#: Caminhos que trocam tabela por fatia, a partir de frame já em memória, e que
#: existem exatamente para evitar reler o lakehouse inteiro. Reescrevê-los para
#: `publish_gold_cube` custaria o scan que eles evitam, então eles registram a
#: publicação no manifesto em vez de passar pelo publicador de SQL.
#:
#: `oci_operational_monitoring` não é cubo de custo: publica métrica operacional
#: da OCI por fatia. Entra na lista porque usa o mesmo swap, não a mesma camada.
_IN_MEMORY_PUBLISHERS = {
    # Frame agregado por fatia, para evitar o scan que ele existe para não fazer.
    "gold_builder.py",
    "oci_operational_monitoring.py",
    # Cobertura de competências: a classificação é produzida em Python, então as
    # linhas vêm do classificador e não de um SELECT. Dezenas de linhas.
    "period_coverage.py",
    # Inventário temporal do C2: único frame do C2, dezenas de milhares de linhas.
    # O custo (13,5 M linhas) **não** passa por frame — é agregado no Trino.
    "finops_cost_resource.py",
    # Cubos de IA: a acumulação por competência monta a tabela a partir das fatias
    # já publicadas. Cada fatia em si é publicada pelo caminho SQL, não por frame.
    "ai_usage_gold.py",
}
#: `gold_governance.py` é quem **implementa** o registro, então aparece nos dois
#: lados; não é um publicador adicional.
_IMPLEMENTS_RECORDING = "gold_governance.py"


def test_no_cube_is_published_without_a_manifest():
    """Guarda de arquitetura: quem publica cubo, registra manifesto.

    Enquanto existir mais de um lugar publicando cubo **sem** manifesto, um deles
    vai publicar sem rastro — foi o que aconteceu com `ai_usage_gold`,
    `azure_ptu_gold` e `ai_cost_materialization`.
    """
    import pathlib

    raiz = pathlib.Path(gov.__file__).parent
    sem_manifesto: list[str] = []
    for caminho in raiz.glob("*.py"):
        texto = caminho.read_text()
        if "swap_staged_table(" not in texto and "replace_staged_cube(" not in texto:
            continue
        if caminho.name in {"gold_governance.py", "iceberg_service.py", "gold_builder.py"}:
            continue
        if "record_frame_publication" not in texto and "publish_gold_cube" not in texto:
            sem_manifesto.append(caminho.name)
    assert sem_manifesto == [], (
        "publicação de cubo sem registro no manifesto: " + ", ".join(sorted(sem_manifesto))
    )


def test_in_memory_publishers_are_known_and_justified():
    """A allowlist de publicação em memória não pode crescer em silêncio."""
    import pathlib

    raiz = pathlib.Path(gov.__file__).parent
    em_memoria = sorted(
        caminho.name
        for caminho in raiz.glob("*.py")
        if (
            "swap_staged_table(" in caminho.read_text()
            or "replace_staged_cube(" in caminho.read_text()
        )
        and "record_frame_publication" in caminho.read_text()
        and caminho.name != _IMPLEMENTS_RECORDING
    )
    assert em_memoria == sorted(_IN_MEMORY_PUBLISHERS), (
        "publicador em memória novo sem justificativa: " + ", ".join(em_memoria)
    )


def test_publish_gold_cube_exists_as_the_single_sql_publisher():
    import inspect

    assert "publish_gold_cube" in inspect.getsource(gov)


def test_publication_modes_are_explicit():
    assert gov.PUBLICATION_SQL == "SQL"
    assert gov.PUBLICATION_IN_MEMORY == "IN_MEMORY"


def test_record_frame_publication_marks_partial_when_empty(monkeypatch):
    registros = []

    async def fake_record(entry):
        registros.append(entry)

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: "snap-1")
    import asyncio

    entrada = asyncio.run(
        gov.record_frame_publication(
            cube_code="dest_dm_cost_focus",
            physical_table="dest_dm_cost_focus",
            rows_written=0,
        )
    )
    assert entrada.status == gov.PARTIAL
    assert entrada.reason == "sem linhas"
    assert entrada.previous_snapshot_id == "snap-1"


def test_record_frame_publication_records_success(monkeypatch):
    registros = []

    async def fake_record(entry):
        registros.append(entry)

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: None)
    import asyncio

    entrada = asyncio.run(
        gov.record_frame_publication(
            cube_code="dest_oci_operational",
            physical_table="dest_oci_operational",
            rows_written=1234,
            source_tables=["bronze.raw_oci_prod_operational"],
        )
    )
    assert entrada.status == gov.SUCCESS
    assert entrada.rows_written == 1234
    assert entrada.source_tables == ["bronze.raw_oci_prod_operational"]


def test_record_frame_publication_blocking_registers_without_loop(monkeypatch):
    registros = []

    async def fake_record(entry):
        registros.append(entry)

    monkeypatch.setattr(gov, "record_manifest", fake_record)
    monkeypatch.setattr(gov, "current_snapshot_id", lambda ns, t: None)
    entrada = gov.record_frame_publication_blocking(
        cube_code="dest_dm_cost_focus",
        physical_table="dest_dm_cost_focus",
        rows_written=10,
    )
    assert entrada.status == gov.SUCCESS
    assert len(registros) == 1


# --- namespace legado ---------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_copy_is_not_removed_without_canonical(monkeypatch):
    """Não se troca 'duas cópias' por 'nenhuma'."""
    chamadas = []

    async def fake_execute(sql, **kwargs):
        if "iceberg.dimon." in sql:
            return [{"ok": 1}]
        raise RuntimeError("tabela ausente")

    monkeypatch.setattr("app.services.trino_service.execute", fake_execute)
    monkeypatch.setitem(
        __import__("sys").modules, "app.services.trino_service",
        __import__("app.services.trino_service", fromlist=["execute"]),
    )
    resultado = await gov.retire_legacy_namespace_copy("dest_x")
    assert resultado["legacy_exists"] is True
    assert resultado["canonical_exists"] is False
    assert resultado["removed"] is False
    assert "perderia o dado" in resultado["reason"]


@pytest.mark.asyncio
async def test_legacy_copy_dry_run_does_not_drop(monkeypatch):
    sqls = []

    async def fake_execute(sql, **kwargs):
        sqls.append(sql)
        return [{"ok": 1}]

    monkeypatch.setattr("app.services.trino_service.execute", fake_execute)
    resultado = await gov.retire_legacy_namespace_copy("dest_x", dry_run=True)
    assert resultado["removed"] is False
    assert not any(sql.startswith("DROP") for sql in sqls)


def test_legacy_namespace_is_dimension_audited():
    assert gov.LEGACY_NAMESPACE == "dimon"
    assert "_tmp_vm_monthly" in gov.LEGACY_DIMON_TABLES
    assert "dest_dm_ai_consumption_daily" in gov.LEGACY_DIMON_TABLES
