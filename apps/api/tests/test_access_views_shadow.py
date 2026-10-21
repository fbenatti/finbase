"""Fase 4 (views Trino) e Fase 6 (modo sombra): geração e comparação, sem Trino."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.core.access.policy import Decision  # noqa: E402
from app.core.access.shadow import compare  # noqa: E402
from app.services.access_views import build_scope_view_sql, view_name_for  # noqa: E402


def test_view_filtra_pelos_ids_do_escopo():
    sql = build_scope_view_sql("iceberg", "gold", "fact_cost", "fact_cost__hierarchy__10", "department_id", ["10", "11"])
    assert sql == (
        'CREATE OR REPLACE VIEW "iceberg"."gold"."fact_cost__hierarchy__10" AS '
        'SELECT * FROM "iceberg"."gold"."fact_cost" WHERE "department_id" IN (\'10\', \'11\')'
    )


def test_view_sem_valores_nao_devolve_linhas():
    sql = build_scope_view_sql("iceberg", "gold", "fact_cost", "v", "department_id", [])
    assert sql.endswith("WHERE FALSE")


def test_valor_com_aspas_e_escapado():
    sql = build_scope_view_sql("iceberg", "gold", "t", "v", "c", ["x') OR (1=1"])
    assert "'x'') OR (1=1'" in sql


def test_identificador_inseguro_e_rejeitado():
    with pytest.raises(ValueError):
        build_scope_view_sql("iceberg", "gold", "t; DROP", "v", "c", ["1"])
    with pytest.raises(ValueError):
        build_scope_view_sql("iceberg", "gold", "t", "v", 'c" OR 1=1 --', ["1"])
    with pytest.raises(ValueError):
        build_scope_view_sql("iceberg", "gold", "Tabela", "v", "c", ["1"])


def test_nome_de_view_estavel_e_seguro():
    assert view_name_for("fact_cost", "hierarchy", "10") == "fact_cost__hierarchy__10"
    assert view_name_for("fact_cost", "hierarchy", "TI/Infra") == "fact_cost__hierarchy__ti_infra"


def test_sombra_sem_divergencia_quando_concordam():
    assert compare("gold", "read", True, Decision(True, "ok")) is None
    assert compare("gold", "read", False, Decision(False, "negado")) is None


def test_sombra_registra_divergencia():
    d = compare("gold", "read", True, Decision(False, "sem ACL de permissão"))
    assert d is not None
    assert d.legacy_allowed is True and d.new_allowed is False
    assert d.new_reason == "sem ACL de permissão"
