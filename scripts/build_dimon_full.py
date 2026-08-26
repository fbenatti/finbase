"""Gera a Dimon FULL agregada a partir da Gold (uma única tabela com tudo).

Uso: python scripts/build_dimon_full.py [nome_da_tabela]
Gera dest_dm_cost_focus (ou o nome informado) com as dimensões enxutas
(menos granulares) e medidas SUM(quantity), SUM(realizado), SUM(billedcost),
reconstruindo a tabela inteira a partir de iceberg.gold.dest_modelo_focus.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

from app.services import dimon_builder

if __name__ == "__main__":
    table = sys.argv[1] if len(sys.argv) > 1 else dimon_builder.DIMON_TABLE
    rows = dimon_builder.rebuild_dimons_from_gold(table=table)
    print(f"DIMON FULL gerada: {table} com {rows:,} linhas")
