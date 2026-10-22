"""C2: o explorador SQL só executa SELECT em tabelas liberadas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.core.sql_guard import SqlRejected, check_read_only_select, referenced_tables  # noqa: E402

ALLOWED = {"gold.billing_cloud", "silver.dest_x"}


def ok(sql: str) -> None:
    check_read_only_select(sql, ALLOWED)


def bad(sql: str, fragment: str = "") -> None:
    with pytest.raises(SqlRejected) as exc:
        check_read_only_select(sql, ALLOWED)
    assert fragment in str(exc.value)


def test_select_simples_em_tabela_liberada():
    ok("SELECT * FROM iceberg.gold.billing_cloud LIMIT 10")


def test_with_e_cte_nao_contam_como_tabela():
    ok("WITH x AS (SELECT 1 AS a) SELECT a FROM x")
    assert referenced_tables("WITH x AS (SELECT 1) SELECT * FROM x") == set()


def test_join_checa_todas_as_tabelas():
    bad("SELECT * FROM iceberg.gold.billing_cloud m JOIN iceberg.bronze.raw_azure r ON 1=1", "bronze.raw_azure")


def test_drop_e_delete_sao_recusados():
    bad("DROP TABLE iceberg.gold.billing_cloud", "Só são permitidas")
    bad("DELETE FROM iceberg.gold.billing_cloud", "Só são permitidas")


def test_escrita_disfarcada_em_select_e_recusada():
    bad("SELECT 1 FROM iceberg.gold.billing_cloud WHERE 1=1 OR (DELETE FROM x)", "DELETE")


def test_multiplas_instrucoes_sao_recusadas():
    bad("SELECT 1 FROM iceberg.gold.billing_cloud; DROP TABLE x", "uma instrução")


def test_ponto_e_virgula_dentro_de_string_nao_quebra_leitura():
    ok("SELECT 'a;b' AS x FROM iceberg.gold.billing_cloud")


def test_palavra_de_escrita_dentro_de_string_nao_bloqueia():
    ok("SELECT 'DROP TABLE' AS txt FROM iceberg.gold.billing_cloud")


def test_comentario_nao_esconde_escrita():
    bad("SELECT 1 FROM iceberg.gold.billing_cloud /* ok */ ; DROP TABLE x", "uma instrução")


def test_comentario_de_linha_com_escrita_e_ignorado_mas_tabela_e_checada():
    bad("SELECT * FROM iceberg.bronze.secreto -- DROP", "bronze.secreto")


def test_tabela_com_aspas_normaliza():
    ok('SELECT * FROM "iceberg"."gold"."billing_cloud"')


def test_consulta_vazia_e_recusada():
    bad("   ", "vazia")


def test_show_e_describe_nao_sao_select():
    bad("SHOW TABLES", "Só são permitidas")


DESTINATIONS = {"dest_billing_cloud"}


def test_destino_com_nome_simples_aceita_silver_e_gold():
    check_read_only_select("SELECT * FROM iceberg.silver.dest_billing_cloud LIMIT 10", DESTINATIONS)
    check_read_only_select("SELECT * FROM silver.dest_billing_cloud", DESTINATIONS)
    check_read_only_select("SELECT * FROM iceberg.gold.dest_billing_cloud", DESTINATIONS)
    check_read_only_select("SELECT * FROM dest_billing_cloud", DESTINATIONS)


def test_destino_com_nome_simples_nao_libera_outro_schema():
    with pytest.raises(SqlRejected):
        check_read_only_select("SELECT * FROM iceberg.bronze.dest_billing_cloud", DESTINATIONS)
    with pytest.raises(SqlRejected):
        check_read_only_select("SELECT * FROM iceberg.silver.dest_outro", DESTINATIONS)
