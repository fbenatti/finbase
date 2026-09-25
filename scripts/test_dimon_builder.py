"""Teste isolado do dimon_builder contra dados reais de uma fatia da Gold.

Uso: python scripts/test_dimon_builder.py
Publica em dest_dm_cost_focus_test e compara com SUM SQL direto na Gold.
"""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

from app.services import dimon_builder
from app.services.trino_service import execute_sync
from app.services import iceberg_service as iceberg

TEST_TABLE = "dest_dm_cost_focus_test"
SLICE_CID = int(os.getenv("SLICE_CID", "10"))  # azure-marketplace (poucas linhas)
SLICE_PERIOD = os.getenv("SLICE_PERIOD", "202601")

def rows_to_df(rows, columns=None):
    if not rows:
        return pd.DataFrame()
    cols = list(rows[0].keys()) if columns is None else columns
    return pd.DataFrame([{c: r.get(c) for c in cols} for r in rows], columns=cols)

def main():
    # 0) Remove tabela de teste antiga
    try:
        iceberg._force_delete_table(iceberg._table_id("gold", TEST_TABLE), TEST_TABLE)
        print("tabela de teste antiga removida")
    except Exception:
        pass

    # 1) Puxa a fatia real da Gold (todas as colunas, como um frame do reprocess)
    gold_cols = [
        "_billing_period", "source_connection_id", "cloud_origem", "tipo",
        "organograma_nivel_1", "organograma_nivel_2", "organograma_nivel_3",
        "organograma_nivel_4", "organograma_nivel_5", "organograma",
        "centro_custo", "ambiente", "ServiceCategory", "ServiceName",
        "ServiceSubcategory", "SkuMeter", "SubAccountName", "RegionName",
        "ChargeCategory", "ConsumedQuantity", "realizado", "BilledCost",
    ]
    select = ", ".join(f'"{c}"' for c in gold_cols)
    sql = (
        f'SELECT {select} FROM iceberg.gold.dest_modelo_focus '
        f'WHERE "_billing_period" = \'{SLICE_PERIOD}\' AND "source_connection_id" = {SLICE_CID}'
    )
    rows = execute_sync(sql, timeout_s=300)
    frame = rows_to_df(rows)
    print(f"fatia lida: {len(frame)} linhas da Gold ({SLICE_PERIOD}/conn {SLICE_CID})")
    if frame.empty:
        print("FATIA VAZIA — nada a testar")
        return 1

    # 2) Agrega em chunks (simula _stage_frame do reprocess)
    chunks = []
    for start in range(0, len(frame), 20000):
        chunk = frame.iloc[start:start + 20000]
        agg = dimon_builder.aggregate_frame(chunk)
        if agg is not None and not agg.empty:
            chunks.append(agg)
    print(f"chunks agregados: {len(chunks)}")

    # 3) Publica a Dimon de teste (caminho de primeira publicação = swap)
    published = dimon_builder.publish_dimons(chunks, table=TEST_TABLE)
    print(f"publicado: {published} linhas")

    # 4) Confere via SQL na Dimon de teste
    check = execute_sync(
        f'SELECT "_billing_period", COUNT(*) AS n, '
        f'SUM(quantity) AS q, SUM(realizado) AS r, SUM(billedcost) AS b '
        f'FROM iceberg.gold.{TEST_TABLE} GROUP BY 1 ORDER BY 1',
        timeout_s=120,
    )
    print("Dimon de teste:", check)

    # 5) Compara com SUM direto na Gold da mesma fatia
    gold_check = execute_sync(
        f'SELECT "_billing_period", '
        f'SUM(COALESCE(TRY_CAST("ConsumedQuantity" AS DOUBLE),0)) AS q, '
        f'SUM(COALESCE(TRY_CAST("realizado" AS DOUBLE),0)) AS r, '
        f'SUM(COALESCE(TRY_CAST("BilledCost" AS DOUBLE),0)) AS b '
        f'FROM iceberg.gold.dest_modelo_focus '
        f'WHERE "_billing_period" = \'{SLICE_PERIOD}\' AND "source_connection_id" = {SLICE_CID} '
        f'GROUP BY 1 ORDER BY 1',
        timeout_s=300,
    )
    print("Gold direto:", gold_check)
    if check and gold_check:
        a, b = check[0], gold_check[0]
        ok = (
            abs(float(a.get("q") or 0) - float(b.get("q") or 0)) < 0.01
            and abs(float(a.get("r") or 0) - float(b.get("r") or 0)) < 0.01
            and abs(float(a.get("b") or 0) - float(b.get("b") or 0)) < 0.01
        )
        print("COMPARAÇÃO:", "OK ✅" if ok else "DIVERGENTE ❌")
        # 6) Testa caminho de substituição por fatia (2ª publicação)
        published2 = dimon_builder.publish_dimons(chunks, table=TEST_TABLE)
        check2 = execute_sync(
            f'SELECT COUNT(*) AS n FROM iceberg.gold.{TEST_TABLE}', timeout_s=60
        )
        print(f"2ª publicação (slice replace): {published2} linhas, total agora {check2}")
        return 0 if ok else 2
    return 1

if __name__ == "__main__":
    sys.exit(main())
