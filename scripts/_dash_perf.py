"""Check bronze partitioning + per-query timings (dev helper)."""
import asyncio
import sys
import time

sys.path.insert(0, "apps/api")

from app.services.trino_service import execute  # noqa: E402


async def main() -> None:
    queries = [
        ("SHOW CREATE TABLE iceberg.bronze.raw_github_token", "token create"),
        ("SELECT COUNT(*) AS rows FROM iceberg.bronze.raw_github_license", "license rows"),
        # Queries internas dos endpoints mais lentos
        ("""
         WITH credit_daily AS (
             SELECT day, lower(assignee_login) AS login, model, language, editor,
                    MAX_BY(COALESCE(ai_credits, total_chat_turns), _ingested_at) AS v
             FROM iceberg.bronze.raw_github_token
             WHERE _billing_period = '202608' AND (source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit')
               AND assignee_login IS NOT NULL AND assignee_login <> ''
             GROUP BY 1,2,3,4,5
         )
         SELECT SUM(COALESCE(TRY_CAST(v AS DOUBLE),0)) AS credits FROM credit_daily
         """, "engagement credit_daily (núcleo)"),
        ("""
         SELECT model, lower(assignee_login) AS login, source_kind, billing_currency,
                SUM(CASE WHEN (source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit') THEN COALESCE(TRY_CAST(ai_credits AS DOUBLE), TRY_CAST(total_chat_turns AS DOUBLE), 0) ELSE 0 END) AS credits,
                SUM(COALESCE(TRY_CAST(input_tokens AS DOUBLE),0)+COALESCE(TRY_CAST(output_tokens AS DOUBLE),0)+COALESCE(TRY_CAST(cache_read_tokens AS DOUBLE),0)+COALESCE(TRY_CAST(cache_write_tokens AS DOUBLE),0)) AS tokens
         FROM iceberg.bronze.raw_github_token
         WHERE _billing_period = '202608' AND assignee_login IS NOT NULL AND assignee_login <> ''
         GROUP BY 1,2,3,4 ORDER BY credits DESC
         """, "financial github_sql"),
        ("""
         SELECT _billing_period, SUM(COALESCE(TRY_CAST(realizado AS DOUBLE),0)) AS r
         FROM iceberg.dimon.dest_dm_cost_copilot_custo
         WHERE tipo = 'realizado' GROUP BY 1 ORDER BY 1 DESC LIMIT 10
         """, "dimon custo (rápido?)"),
    ]
    for sql, label in queries:
        t0 = time.monotonic()
        try:
            rows = await execute(sql, timeout_s=180)
            dt = time.monotonic() - t0
            print(f"===== {label} ({dt:.2f}s) =====")
            for r in rows[:3]:
                print(r)
        except Exception as exc:
            print(f"===== {label} ERROR: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
