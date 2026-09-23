"""Registra os cubos do MVP como REGRAS de governança.

É o mesmo processo da aba cost-relationship: dimensões e medidas declaradas na
regra, fonte na Silver, materialização pelo motor existente — nada de cubo solto.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")

from sqlalchemy import text  # noqa: E402

from app.database import SyncSession  # noqa: E402

REGRAS = [
    ("mvp_custo_recurso", "MVP — Custo por recurso", "resource_cost",
     ["resource_id", "resource_name", "resource_sku", "region_id",
      "_source_connection_name", "_billing_period"],
     ["effective_cost", "billed_cost", "consumed_quantity"],
     "Custo por recurso e período, com proveniência de conexão."),
    ("mvp_oportunidade", "MVP — Oportunidade de saving", "rightsizing_decision",
     ["deterministic_verdict", "_source_connection_name", "region_id", "sku_from", "sku_to"],
     ["saving_monthly"],
     "Oportunidade agregada por veredito; os vereditos não somam entre si."),
    ("mvp_decisao", "MVP — Estado das decisões", "rightsizing_decision",
     ["deterministic_verdict", "review_status", "_source_connection_name"],
     ["saving_monthly"],
     "Estado do fluxo de revisão humana."),
    ("mvp_aks_consumo", "MVP — Consumo AKS", "resource_allocation",
     ["allocation_level", "parent_resource_id", "allocation_key", "namespace",
      "_source_connection_name", "_billing_period"],
     ["allocation_weight"],
     "Alocação de AKS nos dois níveis; os níveis não somam entre si."),
]


def main() -> None:
    s = SyncSession()
    for code, nome, fonte, dims, medidas, desc in REGRAS:
        destino = f"dest_dm_cost_{code}"
        s.execute(text('''
            INSERT INTO gold_rule (code, name, description, active, materialization_table,
                                   dimensions, measure_columns)
            SELECT :c, :n, :d, true, :t, CAST(:dims AS jsonb), CAST(:m AS jsonb)
            WHERE NOT EXISTS (SELECT 1 FROM gold_rule WHERE code = :c)
        '''), {'c': code, 'n': nome, 'd': desc, 't': destino,
               'dims': json.dumps(dims), 'm': json.dumps(medidas)})
        s.execute(text('''
            INSERT INTO gold_rule_source (rule_id, gold_table, filter_json, value_column, percent)
            SELECT r.id, :f, CAST('{}' AS json), :v, 100.0
            FROM gold_rule r
            WHERE r.code = :c AND NOT EXISTS (
              SELECT 1 FROM gold_rule_source s WHERE s.rule_id = r.id AND s.gold_table = :f
            )
        '''), {'f': fonte, 'v': medidas[0], 'c': code})
    s.commit()
    print("regras registradas")
    for row in s.execute(text(
        "SELECT code, materialization_table FROM gold_rule WHERE code LIKE 'mvp_%'"
    )).all():
        print("  ", row)


if __name__ == "__main__":
    main()
