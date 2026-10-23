# FORECAST engine internals — read-only reconnaissance report

Repo: `<raiz-do-repositorio>`
Sources read: `FORECAST.md`, `apps/api/app/services/forecast_service.py`, `apps/api/app/models/forecast.py`,
`apps/api/app/routers/forecast.py`, `apps/api/worker.py`, `apps/api/app/services/iceberg_service.py`,
`apps/api/app/services/lakehouse_layers.py`, `apps/api/app/main.py`, plus live Postgres (host `localhost:5433`)
and live Trino (`localhost:8080`) metadata for corroboration. **No file was modified.**

Live environment used for corroboration: Postgres `localhost:5433/finops` (the catalog the Trino
iceberg.properties points at), Iceberg warehouse `apps/data/iceberg` via Trino. (Note: the Compose container
`openfinops-postgres-1` on port 5434 has an empty `forecast_run`; all row counts below are from 5433.)

---

## 0. Executive summary (what matters for an anomaly layer)

1. The result of a run is an **immutable monthly snapshot** in Iceberg namespace `forecast`, table
   `result_run_{run.id}`, written by `iceberg_service.write_forecast_result` (drop+create+append).
2. `forecast_run.config_snapshot` freezes the **configuration + the active business rules**, not the data.
   It does **not** contain the observed/realized series, and no `realizado` series is preserved anywhere by
   the engine. **Only projected rows are written** (`tipo` is hard-coded `'forecast'` at
   `forecast_service.py:1694` and `:1709`).
3. `realizado_cloud` exists **only as a projected output measure** (`forecast_service.py:142`), never as a
   preserved realized input.
4. The only existing cross-run reference id is `forecast_run.id`, surfaced by `result_table`
   (`result_run_{id}`). `forecast_run.source_snapshot_id` exists as a column but **is never written by any
   code path** — live DB: `count(*) FILTER (WHERE source_snapshot_id IS NOT NULL) = 0` out of 386 runs.
5. Anomaly detection should therefore anchor on the **Gold realized series** (the same source the engine
   reads) plus a `forecast_run.id`/`result_table` reference, **not** on a second recurrence engine, and must
   reuse `config_snapshot` for dimensions/cutoff semantics.
6. Existing "deviation" machinery already lives in the accuracy layer (`_accuracy_metrics`, `type='forecast'`
   vs Gold realized, variance/wape/accuracy/bias, `forecast_ack` reasons). That is the natural seam.

---

## 1. `forecast_run` model — every column, immutability, snapshots

### 1.1 Model definition — `apps/api/app/models/forecast.py:76-108` (class `ForecastRun`, table `forecast_run`)

| # | Column | Type / FK | Line | Notes |
|---|---|---|---|---|
| 1 | `id` | Integer PK autoincrement | 78 | the run identity; also the snapshot id (`result_run_{id}`) |
| 2 | `forecast_config_id` | Integer, FK `forecast_config.id` **ON DELETE SET NULL**, nullable, indexed | 81 | comment at 79-80: runs are audit records, never cascade-deleted |
| 3 | `config_version` | Integer NOT NULL default 1 | 82 | `config.version` at queue time |
| 4 | `trigger_type` | String(30) NOT NULL default `"MANUAL"` | 83 | observed values: `MANUAL`, `SCHEDULED_REPROCESS`, `POST_INGESTION`, `ORGANOGRAM_REFRESH` |
| 5 | `source_etl_task_id` | Integer FK `etl_task.id`, nullable | 84 | set by scheduled paths |
| 6 | `cutoff_date` | String(10), nullable | 89 | data-driven cutoff, persisted **after** execution; NULL until then |
| 7 | `status` | String(30) NOT NULL default `"QUEUED"` | 90 | `QUEUED`/`RUNNING`/`COMPLETED`/`FAILED`/`CANCELLED` |
| 8 | `phase` | String(200), nullable | 91 | human phase text |
| 9 | `total_series` | Integer NOT NULL default 0 | 92 | set post-run |
| 10 | `processed_series` | Integer NOT NULL default 0 | 93 | set post-run |
| 11 | `rows_written` | Integer NOT NULL default 0 | 94 | set post-run |
| 12 | `selected_models` | JSON NOT NULL default `{}` | 95 | `{method: count}` from the engine (`worker.py:643`) |
| 13 | `error_metrics` | JSON NOT NULL default `{}` | 96 | used as the **applied-business-rules audit** (`worker.py:649-651`) |
| 14 | `result_table` | String(200), nullable | 97 | short table name inside namespace `forecast` |
| 15 | `config_snapshot` | JSON NOT NULL default `{}` | 100 | frozen contract (see 1.3) |
| 16 | `destination_table_id_snapshot` | Integer, nullable, indexed | 101 | frozen destination id |
| 17 | `destination_physical_name` | String(200), nullable | 102 | frozen Gold physical table |
| 18 | `source_snapshot_id` | String(100), nullable | 103 | **never written anywhere** (see 1.4) |
| 19 | `classification_version_id` | Integer, nullable, indexed | 104 | always set to `None` at creation (`routers/forecast.py:800`, `worker.py:818`) |
| 20 | `error` | String(2000), nullable | 105 | failure message |
| 21 | `started_at` | DateTime(tz), server_default `now()` | 106 | |
| 22 | `finished_at` | DateTime(tz), nullable | 107 | |

Index: `ix_forecast_run_config_status (forecast_config_id, status)` — line 108.
Live DB extra indexes (from startup migrations): `ix_forecast_run_destination_table_id_snapshot`,
`ix_forecast_run_forecast_config_id`; FK `forecast_run_forecast_config_id_fkey ... ON DELETE SET NULL`
(created in `apps/api/app/main.py:836-847`).

Startup migrations that add/groom these columns: `apps/api/app/main.py:267-272`
(`config_snapshot`, `destination_table_id_snapshot`, `destination_physical_name`, `source_snapshot_id`,
`classification_version_id`, `forecast_config_id DROP NOT NULL`) and `apps/api/app/main.py:891`
(`cutoff_date DROP NOT NULL`).

### 1.2 Are runs immutable snapshots?

**Yes at the data level.** The Iceberg result table for a run is created fresh and is never rewritten:

- `iceberg_service.write_forecast_result` — `apps/api/app/services/iceberg_service.py:2486-2505`:
  `cat.drop_table(tid)` then `cat.create_table(tid, schema=...)` then `table.append(arrow_table)`
  (lines 2492-2502). Table name = `table_name.replace("-", "_").replace(".", "_")`.
- The run row itself is `INSERT`-then-`UPDATE` on progress/status only; nothing recomputes a past result.
- Table name is pinned to the run id by the worker: `result_table=f"result_run_{run.id}"`
  (`apps/api/worker.py:630` for normal runs, `apps/api/worker.py:827` for simulated runs). The engine's own
  fallback (only used when `result_table` is not passed) is
  `f"result_{config.id}_{anchor:%Y%m%d}"` — `forecast_service.py:1760-1762`.
- `run_forecast` docstring/comment at `forecast_service.py:1756-1759`: "A forecast run is an immutable audit
  snapshot. Callers pass the run ID so two executions on the same day can never overwrite each other."
- Simulated runs never touch the current planning table: `forecast_service.py:1764-1768`
  (`planning_table = None` when `simulated_as_of is not None`).

Corroboration: Trino `iceberg.forecast` currently holds `result_run_389` and `result_run_390`, both
exclusively `tipo='forecast'` (140,184 rows each).

### 1.3 What is stored in `config_snapshot`

Built at queue time by `_config_snapshot(config, destination)` — `apps/api/app/routers/forecast.py:192-204`:

```
config_id, config_version, destination_table_id, destination_physical_name,
measure_column, date_column, period_column, type_column, dimensions (list),
currency_column, cutoff_days, horizon_months, period_allocation,
charge_end_column, connection_allocation_rules (list)
```

Then `create_run` augments it — `routers/forecast.py:779-790`:

- `destination_physical_name`, `source_namespace` (781)
- `business_rules` = full JSON-safe dump of every **active** rule (782-788), via
  `_business_rule_snapshot` (`routers/forecast.py:184-190`: all columns except `created_at`/`updated_at`)
- `simulated_as_of` = ISO date, when the caller passed `simulated_as_of` (789-790)

A **second, richer builder** exists inside the accuracy worker — `apps/api/worker.py:782-803`. It writes the
same keys **plus** `source_namespace` and `null_dimension_label`, and embeds `business_rules` (795-802).
Live corroboration (Postgres 5433):

- 5 runs have `business_rules` and no `simulated_as_of` (normal runs through the worker)
- 6 runs have `business_rules` **and** `simulated_as_of` (recent accuracy runs, e.g. run 400/2026-10-02)
- 209 runs have `simulated_as_of` but **no** `business_rules` (older accuracy runs)
- 166 runs have neither (legacy runs backfilled by `main.py:850-866`, or created by
  `scheduler_service._queue_forecast_run` at `apps/api/app/services/scheduler_service.py:458-479`, which sets
  **no** `config_snapshot` at all)

Live example (`forecast_run.id = 400`, accuracy run), keys: `config_id, config_version,
destination_table_id, destination_physical_name, source_namespace, measure_column, date_column,
period_column, type_column, dimensions, currency_column, cutoff_days, horizon_months, period_allocation,
charge_end_column, connection_allocation_rules, null_dimension_label, business_rules, simulated_as_of` (19 keys).

**Not present in `config_snapshot`:** `growth_rate`, `model_mode`, `enabled_windows`, `recent_weight`,
`minimum_daily_points`, `minimum_monthly_points`, `config name` — even though the engine uses all of them
(`forecast_service.py:1594, 1617, 1626, 1634, 1707`). So a snapshot alone cannot reconstruct the math, and
neither `config_snapshot` nor any other place stores the input series.

### 1.4 Does a run preserve the input (observed) series? Is `realizado_cloud` preserved?

**No, and no.**

- The engine's `output` DataFrame contains only two kinds of rows, both with `tipo='forecast'`:
  `forecast_service.py:1694` (anchor/current month) and `forecast_service.py:1709` (future months).
  There is no `results.append` with `tipo='realizado'`.
- `forecast_service.py:1695-1699` is an explicit comment: the realized of the anchor month is **NOT** written
  to planning (writing both would double count in the Gold).
- `realizado_cloud` appears only as an **output** measure: `_apply_dual_financial_measures`
  (`forecast_service.py:128-205`) sets `output["realizado_cloud"] = forecast_value` (line 142) and, for
  `financial_effect='BUDGET_COMMITMENT'` rows, `output["realizado"]` = allocated budget (lines 143, 203-205).
  These are **projections dual-written**, not observed history.
- Live schema of `iceberg.forecast.result_run_390` / `result_run_392` (Trino `DESCRIBE`): dimension columns +
  `forecast_month, tipo, forecast_value, selected_method, fallback_level, history_points, backtest_wape,
  realizado_cloud, realizado`. All rows `tipo='forecast'` (`SELECT tipo, count(*)` → `forecast | 140184` for
  390; `forecast | 135009` for 392). So `realizado_cloud` **is** present as a column in dual-measure
  configurations (config 9), but it mirrors the projection, not the observation.
- The observed series can therefore only be **recovered by re-querying Gold** (the accuracy endpoints do
  exactly that: `_accuracy_batch_totals` at `routers/forecast.py:1174-1222`, `_accuracy_actual_context`
  at `routers/forecast.py:1047-1076`).

### 1.5 `source_snapshot_id` and other snapshot-ish refs

- `forecast_run.source_snapshot_id` (model `models/forecast.py:103`; migration `main.py:270`): **no writer
  exists.** `grep -rIn source_snapshot_id apps/` returns only the model line and the *read* sites
  `worker.py:662` and `worker.py:668` (which copy it, always `None`, into `EtlTask.result_json` and the
  ProcessingLog details). Live DB: 0 of 386 runs populated.
- Snapshot-ish things that *are* real:
  - `result_table` / `result_run_{id}` — the immutable Iceberg snapshot of the projection (`worker.py:630`).
  - `config_snapshot` — frozen config + rules.
  - `destination_table_id_snapshot` + `destination_physical_name` — frozen Gold identity.
  - `cutoff_date` — frozen data-driven cutoff.
  - `selected_models` — `{method: series_count}`.
  - `error_metrics["business_rules"]` — applied-rule audit (section 7).
  - `ForecastAck.run_id` (`models/forecast.py:117`) — the **only** foreign key-like reference from another
    table into a computed forecast (nullable integer, no FK constraint), pointing at `forecast_run.id`
    together with `target_month`.

---

## 2. `forecast_config` model — every column

`apps/api/app/models/forecast.py:4-31` (class `ForecastConfig`, table `forecast_config`):

| # | Column | Type / default | Line |
|---|---|---|---|
| 1 | `id` | Integer PK autoincrement | 6 |
| 2 | `name` | String(200) NOT NULL | 7 |
| 3 | `description` | String(1000) nullable | 8 |
| 4 | `destination_table_id` | Integer FK `destination_table.id` ON DELETE CASCADE, NOT NULL, indexed | 9 |
| 5 | `measure_column` | String(200) NOT NULL | 10 |
| 6 | `currency_column` | String(200) nullable | 11 |
| 7 | `date_column` | String(200) NOT NULL | 12 |
| 8 | `period_column` | String(200) nullable | 13 |
| 9 | `type_column` | String(200) nullable, default `"tipo"` | 14 |
| 10 | `dimensions` | JSON NOT NULL default `list` | 15 |
| 11 | `cutoff_days` | Integer NOT NULL default `2` | 16 |
| 12 | `horizon_months` | Integer NOT NULL default `12` | 17 |
| 13 | `growth_rate` | Float NOT NULL default `0.0083` | 18 |
| 14 | `model_mode` | String(30) NOT NULL default `"AUTO_BACKTEST"` | 19 |
| 15 | `enabled_windows` | JSON NOT NULL default `[7, 14, 28]` | 20 |
| 16 | `recent_weight` | Float NOT NULL default `0.7` | 21 |
| 17 | `minimum_daily_points` | Integer NOT NULL default `7` | 22 |
| 18 | `minimum_monthly_points` | Integer NOT NULL default `3` | 23 |
| 19 | `null_dimension_label` | String(100) NOT NULL default `"Não classificado"` | 24 |
| 20 | `period_allocation` | String(30) NOT NULL default `"none"` | 25 |
| 21 | `charge_end_column` | String(200) nullable | 26 |
| 22 | `connection_allocation_rules` | JSON NOT NULL default `list` | 27 |
| 23 | `active` | Boolean NOT NULL default `True` | 28 |
| 24 | `version` | Integer NOT NULL default `1` | 29 |
| 25 | `created_at` | DateTime(tz) server_default `now()` | 30 |
| 26 | `updated_at` | DateTime(tz) server_default `now()` onupdate `now()` | 31 |

Notes that matter for an anomaly layer:

- `version` is bumped on **config** update (`routers/forecast.py:538`) and on **rule** update
  (`routers/forecast.py:703`), and copied into every run as `config_version` (`routers/forecast.py:792`).
- `null_dimension_label` is **never read by the engine**. It is read only by accuracy/target-month helpers
  (`routers/forecast.py:1386, 1411, 1487`). NULL dimension values in the engine become SQL `NULL` in pandas
  and are kept as `None` in `key_values` (`forecast_service.py:37-47, 1566`), i.e. Iceberg `NULL`, not the label.
- Validation of these fields lives in `validate_config` — `forecast_service.py:1290-1318`:
  measure must be in metadata measures (1293-1294); date/period must exist (1295-1296); dimensions must exist
  (1297-1299); `horizon_months` 1..36 (1300-1301); `enabled_windows ⊆ {7,14,28}` (1302-1303);
  `model_mode ∈ {AUTO_BACKTEST, WEIGHTED, MEAN_7, MEAN_14, MEAN_28}` (1304-1305);
  `period_allocation ∈ {none, charge_interval}` (1306-1308); per-connection rules (1309-1311);
  legacy `charge_end_column` fallback (1312-1317).
- Related model: `ForecastAdjustment` (`models/forecast.py:33-47`) columns: `id, forecast_config_id,
  name, filters (JSON), adjustment_type, percentage, fixed_value, start_period, end_period, priority, active,
  created_at, updated_at`. Allowed `adjustment_type` values (`routers/forecast.py:719`):
  `PERCENTAGE, FIXED_VALUE, ADD_VALUE, SUBTRACT_VALUE, ZERO, CUSTOM_GROWTH`.
  **`CUSTOM_GROWTH` is accepted by the API but has no branch in the engine** (`forecast_service.py:1728-1732`
  handles only the first five), so it is silently a no-op.

---

## 3. `forecast_business_rule` model — every column + allowed values

`apps/api/app/models/forecast.py:49-74` (class `ForecastBusinessRule`, table `forecast_business_rule`):

| # | Column | Type / default | Line |
|---|---|---|---|
| 1 | `id` | Integer PK autoincrement | 52 |
| 2 | `forecast_config_id` | Integer FK `forecast_config.id` ON DELETE CASCADE, NOT NULL, indexed | 53 |
| 3 | `name` | String(200) NOT NULL | 54 |
| 4 | `identification_column` | String(200) NOT NULL default `""` | 55 |
| 5 | `identification_values` | JSON NOT NULL default `list` | 56 |
| 6 | `identification_clauses` | JSON NOT NULL default `list` | 57 |
| 7 | `charge_type` | String(40) NOT NULL default `"Usage"` | 58 |
| 8 | `behavior` | String(40) NOT NULL default `"RECURRING_MONTHLY"` | 59 |
| 9 | `behavior_options` | JSON NOT NULL default `dict` | 60 |
| 10 | `reference_column` | String(200) nullable | 61 |
| 11 | `reference_values` | JSON NOT NULL default `list` | 62 |
| 12 | `value_column` | String(200) nullable | 63 |
| 13 | `start_period` | String(6) NOT NULL | 64 |
| 14 | `end_period` | String(6) nullable | 65 |
| 15 | `growth_rate` | Float NOT NULL default `0.0` | 66 |
| 16 | `replacement_value` | Float nullable | 67 |
| 17 | `cancel_on_refund` | Boolean NOT NULL default `False` | 68 |
| 18 | `scope` | JSON NOT NULL default `dict` | 69 |
| 19 | `priority` | Integer NOT NULL default `0` | 70 |
| 20 | `active` | Boolean NOT NULL default `True` | 71 |
| 21 | `version` | Integer NOT NULL default `1` | 72 |
| 22 | `created_at` | DateTime(tz) server_default `now()` | 73 |
| 23 | `updated_at` | DateTime(tz) server_default `now()` onupdate `now()` | 74 |

Allowed values:

- **`behavior`** — enforced in `_validate_rule_fields`, `routers/forecast.py:677`:
  `REPEAT_NEXT_MONTH`, `RECURRING_MONTHLY`, `RECURRING_UNTIL_END`, `CANCEL_ON_REFUND`, `REPLACE_VALUE`,
  `CHARGE_INTERVAL`, `SOURCE_EXCLUDE_PREFIX`, `SOURCE_MEAN_30D_PREFIX`, `SOURCE_VALIDITY`.
- **`charge_type`** — **no server-side enum.** It is a free String(40). The API only string-matches
  `charge_type.lower() == "refund"` combined with `cancel_on_refund` (`forecast_service.py:418-419`).
  The UI offers `["Usage", "Return", "Purchase", "Refund", "Credit"]`
  (`apps/web/src/pages/Forecast/ForecastRuleDialog.tsx:355`); seeded templates use `Purchase`, `Return`,
  `Refund` (`routers/forecast.py:247, 257, 267, 280, 292, 301`). `FORECAST.md:288` also mentions
  `Recurring commitment`, which has no code support.
- **`behavior_options`** keys recognized by code:
  - `current_month_policy` ∈ `{AUTO, ZERO_WHEN_REALIZED, DAILY_REMAINDER}`, validated at
    `routers/forecast.py:679-681`, consumed at `forecast_service.py:298-308`.
  - `max_age_days` ∈ 1..365, required for `SOURCE_VALIDITY`, validated at `routers/forecast.py:682-685`,
    consumed at `forecast_service.py:477-479`.
- Structural validation: `CHARGE_INTERVAL` requires `reference_column` and identity/scope
  (`routers/forecast.py:670-674`); every other behavior requires identity or scope (675-676);
  `start_period`/`end_period` must be 6 digits (686-687).
- Seeded provider packs: `PROVIDER_RULE_PACKS` — `routers/forecast.py:241-307`
  (`azure_gcp`, `oci`, `marketplace`), installed by `ensure_provider_forecast_rules`
  (`routers/forecast.py:310-367`).

---

## 4. Where results are WRITTEN, the result schema, and how `tipo` is set

### 4.1 The write/publication function

`iceberg_service.write_forecast_result(table_name, df)` — `apps/api/app/services/iceberg_service.py:2486-2505`:

- namespace hard-coded `"forecast"` (line 2490), `_ensure_namespace` (2491)
- `safe_name = table_name.replace("-", "_").replace(".", "_")` (2492)
- `_df_to_arrow(df, column_types={"forecast_value": "DOUBLE"})` (2494) → schema auto-derived from the pandas
  columns, so the table schema is data-driven per configuration
- `cat.drop_table(tid)` then `cat.create_table(...)` then `table.append(...)` (2496-2502) — **replace, not
  append across runs**
- returns `len(df)`

Called once per run: `forecast_service.py:1763` → `iceberg.write_forecast_result(result_table, output)`.

Second output: the "planning" table (current-planning surface, distinct from the immutable run snapshot):

- `write_forecast_planning(physical_name, df, value_column=measure)` — `iceberg_service.py:2537-2562`; called
  at `forecast_service.py:1768` **only when `simulated_as_of is None`** (guard at 1767).
- It writes to `layers.detail_layer()` = `silver` (`lakehouse_layers.py:31, 46-53`), name
  `planning_table_name(physical_name)` = `physical_name` sanitized + `"_planning"`
  (`iceberg_service.py:2508-2509`).
- It drops `selected_method, fallback_level, history_points, backtest_wape` (2546), renames
  `forecast_month → period` (2552); for dual-measure configs it drops `forecast_value` and publishes
  `realizado` + `realizado_cloud` (2553-2560), else renames `forecast_value → measure_name` (2561).
- Live: `iceberg.silver.dest_modelo_focus_planning` columns = 22 dimension columns + `period, tipo,
  realizado_cloud, realizado, rateio_medida`; `tipo` counts: `forecast 141625`, `rateio 4270`,
  `shared_cost 105`. So planning is **also** only forecast rows from the engine; `rateio`/`shared_cost` are
  added by `merge_planning_rateio` (`iceberg_service.py:2565-2655`), which preserves `realizado`/`forecast`.
- Export path for the raw snapshot: `GET /forecast/runs/{id}/export` → `get_catalog().load_table(("forecast",
  run.result_table))` (`routers/forecast.py:2345-2355`).

### 4.2 Result table schema (verbatim, live `iceberg.forecast.result_run_390`)

Dimensions come from `config.dimensions` plus engine-injected context columns. Live columns, in order:

```
cloud_origem, marketplaceproductname, marketplacebillingmodel, servicename, subaccountname,
centro_custo, organograma_nivel_1..organograma_nivel_7,
chargecategory, serviceprovidername, servicesubcategory, pricingcategory, commitmentdiscounttype,
source_connection_id (bigint), financial_effect (varchar), financial_contract_id (double), id_orcamento (varchar),
forecast_month (varchar), tipo (varchar), forecast_value (double),
selected_method (varchar), fallback_level (bigint), history_points (bigint), backtest_wape (varchar),
realizado_cloud (double), realizado (double)
```

Column provenance in `run_forecast`:

- dimension columns = `config.dimensions` + `cloud_origem` + `source_connection_id` if present
  (`forecast_service.py:1439-1442`), + `financial_effect`, `financial_contract_id`, `id_orcamento` when the
  dual-measure contract path is active (1443-1451). `source_connection_id` is only in the key when
  per-connection allocation is used (1554-1558).
- the 6 literal result columns are created in the two dict literals at `forecast_service.py:1694` and `:1709`:
  `forecast_month, tipo, forecast_value, selected_method, fallback_level=0, history_points, backtest_wape=None`.
- `backtest_wape` is **always NULL** — nothing ever assigns it (grep: only these two literals).
  Backtest WAPE is computed internally only to *choose* the method (1653-1659) and is discarded.
- `__forecast_component` (line 1541/1547) is an execution-only key and is dropped before writing (1755).
- `realizado` / `realizado_cloud` are added only by `_apply_dual_financial_measures`
  (`forecast_service.py:142-143, 205`), i.e. only when `measure == "realizado"` AND Gold has both
  `realizado_cloud` and `financial_effect` (1433-1437).

### 4.3 How `tipo` is set

Hard-coded literal `"forecast"` in both row constructions (lines 1694 and 1709). There is **no engine code
path that emits `tipo='realizado'`** into the run table or the planning table. The accuracy/history readers
therefore filter `"tipo" = 'forecast'` (`routers/forecast.py:989, 1006, 1895`) or rely on the column existing
(`_history_fields`, line 131-132). The Gold side carries the realized facts with `tipo='realizado'` or NULL
(engine type filter at `forecast_service.py:1333-1336`).

---

## 5. Daily series aggregation in Trino + where the cutoff is computed

### 5.1 SQL builders (all in `forecast_service.py`)

| Function | Lines | Role |
|---|---|---|
| `gold_date_expr(date_col, period_col=None, *, table_alias=None)` | 638-678 | builds the Trino date expression: ISO `TRY_CAST`/`date_parse`/`from_iso8601_timestamp`, then `MM/DD/YYYY`, then `DD/MM/YYYY`, disambiguated by `_billing_period` when present (672-677). Also handles `date_col == period_col` (649-650). |
| `_billing_period_end_expr(period_col)` | 528-529 | `last_day_of_month(date_parse(period_col || '01', '%Y%m%d'))` |
| `_charge_interval_end_expr(date_expr, charge_end_col, period)` | 562-570 | `COALESCE(charge_end, period_end, date)` |
| `_expand_charge_interval(frame, dims)` | 532-559 | pandas-side even spread of a charge across `[charge_start, charge_end]` |
| `_fetch_period_records(...)` | 1116-1179 | **the aggregation SQL builder.** Daily mode: `SELECT dims, date_expr AS forecast_day, SUM(TRY_CAST(measure AS DOUBLE)) AS value FROM source WHERE type_filter AND date_expr BETWEEN history_start AND effective_cutoff [AND connection_filter] GROUP BY dims, date_expr` (1144-1146, 1171). Interval mode: same but `charge_start`/`charge_end` and, when a period column exists, **no date predicate** (1138-1142, 1153). |
| `_fetch_period_records_by_cloud(...)` | 1182-1287 | shards the aggregation by `cloud_origem`, then by `subaccountname` in batches of `_SHARD_BATCH_SIZE = 64` (line 23, 1256-1259); re-anchors each cloud's history window to that cloud's own cutoff (1209-1222); queries with `DISTINCT` shards are scoped to the same window (1246-1252). |
| `_source_exclusion_filter(...)` | 581-630 | predicates for `SOURCE_EXCLUDE_PREFIX` / `SOURCE_MEAN_30D_PREFIX` (like-prefix OR groups + scope), applied *before* aggregation |
| `_billing_period_values(...)` | 883-900 | partition values (`YYYYMM`) to scan, widened by `minimum_monthly_points` |
| type filter | 1333-1336 | `(tipo = 'realizado' OR tipo IS NULL)` when the Gold has the type column |
| source | 1337 | `f"iceberg.{namespace}.{table}"` where `namespace` is `config.source_namespace` resolved by the worker (gold or silver) |

Engine entry sequence: `run_forecast` at `forecast_service.py:1321`;
fetch calls at 1461-1542 (global interval / per-connection split / plain daily / exclusion / mean-30d);
`pd.concat` + `dropna` at 1545-1553; `groupby(model_key_cols)` loop at 1564.

### 5.2 Where the cutoff is computed

- Core rule: `_realized_cutoff(collected_through, *, as_of, excluded_days)` — `forecast_service.py:26-34`:
  `min(collected_through, as_of - excluded_days)`.
- Per-cloud discovery: `_fetch_cloud_cutoffs` — 1022-1099 (max date per `cloud_origem` + connection, then
  `_realized_cutoff(..., as_of=date.today(), ...)` at 1085-1087, then connection/cloud max propagation at
  1090-1098). Invoice-style clouds: `_fetch_invoice_cloud_cutoffs` — 910-957 (anchors on charge-end and the
  latest period, also `_realized_cutoff` at 954-956); applied by `_apply_interval_cloud_cutoff_overrides`
  — 960-1019.
- Global cutoff in `run_forecast` — `forecast_service.py:1353-1402`:
  - simulated: `collected_through = simulated_as_of`; `effective_cutoff = simulated_as_of - excluded_days`
    (1354-1359) — no query at all;
  - real, cloud cutoffs available: `collected_through = max(cutoff + excluded_days)` and
    `effective_cutoff = max(cloud_cutoffs.values())` (1382-1386);
  - fallback (no `cloud_origem`): `SELECT MAX(date_expr) FROM source WHERE type_filter [AND latest period]`
    (1389-1394) then `_realized_cutoff(..., as_of=date.today(), ...)` (1400-1402); raises
    `"Não foi possível identificar a última data válida na Gold"` if empty (1398).
- Per-series cutoff: `series_cutoff = min(cloud_effective_cutoff, series_last_date)`
  (`forecast_service.py:1575-1577`), with `_cloud_effective_cutoff` at 1102-1113 and `history` filtered to
  `<= series_cutoff` (1577). Series whose last date precedes the cloud's current month start are dropped
  (1583-1584) or end as `ENDED_SERIES` (1673-1676).
- Persisted: `run.cutoff_date = result["effective_cutoff"]` (`worker.py:642`, accuracy variant 837);
  returned in the result dict at `forecast_service.py:1777` together with `collected_through` and
  `cloud_cutoffs` (1776-1778).

---

## 6. Month computation: current month, future months, growth rate, daily vs monthly

### 6.1 History preparation (index = day)

- `daily = history.resample("D").sum()` then reindexed to a contiguous day range ending at `series_cutoff`
  with `fill_value=0.0` (`forecast_service.py:1585-1589`).
- `_close_missing_months(daily, series_cutoff)` (251-266) materializes closed months with no rows as zero.
- Per-series context `_build_series_context` (208-248): `monthly_realized` map (keys `YYYYMM`),
  `last_closed_month_total` (last nonzero closed month, with a lump fallback at 227-242),
  `current_month_realized`, `current_key`. `last_observed_date` is added at 1593, `lumpy` at 1613.

### 6.2 Baseline / method selection (per series)

`forecast_service.py:1594-1676`:

- candidates `MEAN_{7,14,28}D` for enabled windows with enough points (1614-1620;
  `minimum_daily_points` gate at 1617); fallback `MEAN_AVAILABLE` (1619-1620).
- `WEIGHTED_{short}D_{long}D = recent_mean*recent_weight + extended_mean*(1-recent_weight)` (1621-1627).
- component override `MEAN_30D_RULE` (1628-1629, from `SOURCE_MEAN_30D_PREFIX`).
- `MONTHLY_LUMP` when `nonzero_days <= 3`: baseline = last charged month total ÷ days in that month
  (1600-1609, 1630-1633).
- `AUTO_BACKTEST`: last 7 days as validation, rest as train, WAPE per candidate, picks min WAPE
  (1634-1659). **The chosen WAPE is discarded** (only `min()` result used).
- `WEIGHTED` / `MEAN_*` modes (1660-1672); `ENDED_SERIES` forced to baseline 0 when the series ended before
  the current month (1673-1676).

### 6.3 Current month = realizado acumulado + forecast restante

- `series_anchor = series_cutoff` (1680); `realized_value = SUM(history in anchor month)` (1683);
  `remaining_days = last_day - series_anchor.day` (1684).
- `current_forecast_days = remaining_days`, except invoice-interval series where it is `0` if any realized
  exists else `last_day` (1688-1691).
- anchor row: `forecast_value = baseline * current_forecast_days`,
  `forecast_month = f"{anchor.year}{anchor.month:02d}"` (1692-1694).
- **The realized accumulated value is deliberately NOT emitted** (comment 1695-1699). So "realizado
  acumulado + forecast restante" is materialized only in *consumers* (dashboard / accuracy), not in the
  immutable run table. `FORECAST.md:179-186` documents the decomposition; the code only publishes the
  remainder.
- Anomaly-layer implication: `realizado_acumulado` for the anchor month must be recomputed from Gold, or read
  from `realizado` only in the dual-measure contract path (which is a budget allocation, not the observed sum).

### 6.4 Future months + where the growth rate is applied

`forecast_service.py:1700-1709`:

```python
for month_offset in range(1, int(config.horizon_months) + 1):
    ...
    growth_rate = 0.0 if component == "MEAN_30D_SOURCE" else float(config.growth_rate)   # 1707
    value = baseline * ((1.0 + growth_rate) ** month_offset) * calendar.monthrange(year, month)[1]  # 1708
```

i.e. growth is compounded **per month offset** and the days-in-month multiplies the daily baseline back to a
monthly value (`FORECAST.md:228-234`). Business rules apply a *separate* growth: `rule.growth_rate` in
`_replicated_month_value` (`forecast_service.py:315, 327`) and in the `REPLACE_VALUE` branch (428-430).

### 6.5 Daily vs monthly emission

**The engine emits only monthly rows.** `forecast_month` (`YYYYMM` string) is the only time grain in the
result table; the daily grid is entirely internal (input aggregation + windows). There is **no** daily
projection column. The only daily "projection" in the product is derived on read:
`GET /forecast/accuracy/delta/series` (`routers/forecast.py:1830-1942`) spreads the *monthly* snapshot total
as `forecast_rate = forecast_total / days_in_month` (1905) and compares it with Gold's realized per day.
A daily or intra-month anomaly layer needs its own day-grain projection (or should reuse this uniform-rate
convention explicitly, as the endpoint docstring at 1838-1843 states).

---

## 7. Business rules: application and audit trail of what matched

### 7.1 Application order (inside `run_forecast`)

1. Source-level filters are built **before** aggregation: `_source_exclusion_filter` for
   `SOURCE_EXCLUDE_PREFIX` + `SOURCE_MEAN_30D_PREFIX` (`forecast_service.py:1340-1347`), emitted as
   negative/positive SQL contributions at 1515-1542; measured via separate queries with `exclusion_sign`
   (1132, 1141, 1145).
2. `_allocation_rules_from_business_rules` (737-782) converts `CHARGE_INTERVAL` rules into per-connection
   fetch rules; `_merge_allocation_rules` (717-734) merges with config rules.
3. Math (baseline + current/future months), 1594-1709.
4. `_apply_forecast_business_rules` (372-459) — `REPEAT_NEXT_MONTH`, `RECURRING_MONTHLY`,
   `RECURRING_UNTIL_END`, `CANCEL_ON_REFUND`, `REPLACE_VALUE`; skips source-level behaviors (385-388).
5. `adjustments` loop (1722-1732) — ordered by `priority`.
6. `_apply_dual_financial_measures` (1733-1743).
7. `_apply_source_validity_rules` (1744-1751) — `SOURCE_VALIDITY`, evaluated at
   `freshness_reference = simulated_as_of or date.today()` (1744).

### 7.2 What the audit trail records (and what it does not)

Returned as `result["business_rules"]` (a list of strings, `forecast_service.py:1775`) and persisted by the
worker into `forecast_run.error_metrics["business_rules"]` (`worker.py:644-651`).

Per-rule strings emitted:

- `"{name}"` — rule applied (CANCEL_ON_REFUND 422; REPLACE_VALUE 431; REPEAT/RECURRING 454)
- `"{name} (não aplicada: campo fora das dimensões)"` — 392
- `"{name} (não aplicada: sem identificação/escopo)"` — 404
- `"{name} (sem séries correspondentes)"` — 412
- `"{name} (não aplicada: comportamento desconhecido)"` — 457
- source-exclusion rules contribute bare `name` (629), interval rules contribute
  `rule["_from_business_rule"]` (1452-1456)
- validity: `"{name} (suspensas N linhas; validade D dias; idade avaliada A dias em YYYY-MM-DD)"` (517-520),
  `"{name} (válida; idade máxima A dias)"` (522), `"{name} (sem séries correspondentes)"` (524)

Live corroboration (`forecast_run.error_metrics`, Postgres 5433): run 399/397/395/... carry an 11-entry list
including `"Azure-Producao - suspender após 30 dias sem dados (suspensas 26 linhas; validade 30 dias; idade
avaliada 104 dias em 2026-10-14)"` and `"Marketplace ONESHOT zera forecast (sem séries correspondentes)"`.
Run 387 shows the legacy `"(não aplicada: comportamento desconhecido)"` string.

**Gaps an anomaly layer must know about:**

- The audit is a **flat list of names/strings**, not structured match data. There is **no per-series,
  per-row, or per-month record of which rule touched which value** — no rule id, no before/after values, no
  match counts except inside the free-text parentheses.
- The `error_metrics` audit is written **only** on the normal `task_type == "forecast"` path
  (`worker.py:644-651`). The accuracy path sets `run.error_metrics = {}` (`worker.py:839`), so
  **accuracy snapshots have no rule audit**.
- The audit is **not** surfaced by any API beyond `GET /forecast/runs/{id}/results` (`routers/forecast.py:
  2333-2337`, as `"metrics"`) and `GET /forecast/runs/{id}/backtest` (2339-2343); the frontend does not read
  `error_metrics` at all (`grep` over `apps/web/src` finds no `error_metrics`/`business_rules` usage).
- Rules depend on the column being **among the series dimensions**; otherwise the model
  `identification_column` lookup (`_rule_identity_mask`, 331-369, using `output_by_lower`) fails and the rule
  is recorded as "campo fora das dimensões". This is also documented at `FORECAST.md:378-382`.

---

## 8. Cancellation / refund handling and source validity

### 8.1 Refund / cancellation

Inside `_apply_forecast_business_rules` (`forecast_service.py:418-423`):

```python
if behavior == "CANCEL_ON_REFUND" or (
    str(getattr(rule, "charge_type", "") or "").lower() == "refund" and getattr(rule, "cancel_on_refund", False)
):
    output.loc[mask, "forecast_value"] = 0.0
```

Details:

- The refund is handled by **zeroing the projected rows that match the rule's identity/scope** — it does
  **not** net a refund amount out of history, and it does **not** read the refund's own value.
- `reference_column` / `reference_values` / `value_column` are **never used** by the engine for matching.
  `reference_column` is used only by `CHARGE_INTERVAL` (as the charge-end column, 752) and validated at
  `routers/forecast.py:667-668, 578`. So `FORECAST.md:293-296`'s "Refunds devem ser associados por
  referência ou escopo" is implemented **by scope/identity only** — reference linkage is a UI/validation
  concept, not an engine join.
- Priority ordering (382, `sorted(..., key=lambda item: item.priority)`) means a `REPLACE_VALUE` with lower
  priority runs first and a later refund can zero it.
- Cancel also happens for ended series and stale sources: `ENDED_SERIES` (1673-1676), invoice-interval
  current-month logic (1688-1691), and `MONTHLY_LUMP` zeroing in `_replicated_month_value` (316-327).

### 8.2 "Validade da fonte" (`SOURCE_VALIDITY`)

`_apply_source_validity_rules` — `forecast_service.py:462-525`:

- only active rules with `behavior == "SOURCE_VALIDITY"` (474); `max_age_days` must be int 1..365 or it
  raises (477-479).
- identity/clause + `scope` + `start_period`/`end_period` build the mask (481-498); a rule without identity or
  scope raises (493-494), and a column outside the dimensions raises (483-484, 489-490).
- age = `(evaluated_at - last_observed_date).days` from `series_context` (506-510), where
  `last_observed_date` is the series' own max observed date set at 1593.
- if `age_days > max_age_days`, `forecast_value`, `realizado`, and `realizado_cloud` are all zeroed for that
  row (511-515) — note this is the only place the engine writes `realizado`/`realizado_cloud` to 0 for
  non-contract configs (guarded by `if value_column in output.columns`, 512-514).
- `evaluated_at` is `freshness_reference = simulated_as_of or date.today()` (1744), so simulations use the
  simulated clock — covered by `apps/api/tests/test_forecast_source_validity.py`
  (`test_source_validity_uses_simulated_date_not_wall_clock`).
- Live config 9 rule: "Azure-Producao - suspender após 30 dias sem dados" (`max_age_days = 30`,
  `scope = {"cloud_origem": "azure-producao"}`, priority 1) — suspended 26 rows in run 399.

---

## 9. Task/worker integration: task types and progress reporting

- **`task_type` strings:** `"forecast"` for a normal run (`routers/forecast.py:803`, `worker.py:296`,
  `scheduler_service.py:472`) and `"forecast_accuracy"` for the batch of simulated month snapshots
  (`routers/forecast.py:2288`, `worker.py:317`). Both are recognized in claim/cleanup lists:
  `apps/api/app/services/etl_task_claim.py:26, 38`; `apps/api/app/services/iceberg_service.py:2223, 2340`.
- **Queue → run:**
  - `POST /forecast/runs?config_id=&trigger_type=&simulated_as_of=` — `routers/forecast.py:741-810`.
    Guards: 404 unknown config; 409 if a run of that config is `QUEUED`/`RUNNING` (751-752);
    503 if no healthy worker (753-756); 404 if destination missing/inactive (757-759); 503 if neither
    `gold` nor `silver` resolves the Gold through Trino (766-778). It builds `config_snapshot` (779-790),
    inserts `ForecastRun` (791-802) and `EtlTask(task_type="forecast", result_json={"forecast_run_id": id})`
    (803-805), then syncs processing logs (806-808).
  - Worker handler: `apps/api/worker.py:563-734`. Phases set: `"Validando metadados"` (603),
    `"Agregando histórico no Trino"` (615-617), then final `"Forecast publicado"` (634) with
    `rows_written/total_series/processed_series/result_table/cutoff_date/selected_models/error_metrics`
    (636-651). Cancellation re-check at 588-598. Failure path 709-734 sets `FAILED` unless already
    `CANCELLED`.
  - Accuracy batch: `apps/api/worker.py:736-892`; per month it creates its own `ForecastRun`
    (`simulated_as_of = day 2`, 811-820), runs the engine with `simulated_as_of=as_of` (824-829), and
    reports progress through `EtlTask.processed_blobs/total_blobs/current_blob/phase`
    (`f"Simulado {month} concluído (run {run.id})"`, 842-847); final phase
    `f"Acurácia reprocessada: {processed}/{total} meses"` (864-867).
- **Progress surface for the UI:** `EtlTask.phase`, `processed_blobs`, `total_blobs`, `current_blob`,
  plus `ForecastRun.status/phase/rows_written/total_series/processed_series` (banner polling every 3s per
  `FORECAST.md:337`). `_task_notification_metadata` (`task_runner.py:3408-3420`) enriches forecast tasks with
  `rows_destination/forecast_total_series/forecast_processed_series/phase` read from the run.
- Related endpoints: cancel (`routers/forecast.py:2307-2331`, sets both run and `EtlTask` to `CANCELLED`),
  results (2333-2337), backtest (2339-2343), export (2345-2355), grid (820-921).

---

## 10. Existing notion of a "snapshot of a computed forecast" referenceable by id

What exists today:

1. **`forecast_run.id` → `result_table = "result_run_{id}"`** in namespace `forecast`. This is the de-facto
   snapshot id. Pinned by the worker (`worker.py:630`, `worker.py:827`); documented by `FORECAST.md:374-376`
   ("O resultado é gravado como snapshot imutável no Iceberg (`forecast.result_run_{id}`)").
2. **`config_snapshot`** (JSON) — the frozen execution contract, including `simulated_as_of` for simulations.
   Note the two inconsistent builders (`routers/forecast.py:192-204` + 779-790 vs `worker.py:782-803`),
   and the fact that `scheduler_service.py:458-479` creates runs with an empty snapshot.
3. **`source_snapshot_id`** — a column that exists and is **always NULL** (0/386 runs); the only readers are
   `worker.py:662` and `worker.py:668`.
4. **`forecast_ack.run_id`** (`models/forecast.py:111-135`) — the only other table that *references* a
   computed forecast by id (nullable, no FK), scoped by `target_month` + `department_name`, with frozen
   metrics `forecast_value/actual_value/variance/accuracy`, plus `category/reason/severity/ordem` and an edit
   history table `forecast_ack_history` (138-147). This is the closest existing precedent for "annotate a
   snapshot by id".
5. **Accuracy run selection convention** (`routers/forecast.py:1225-1237`, `_latest_simulated_runs`): the
   dashboard picks the most recent `COMPLETED` run per `target_month = _month_after(simulated_as_of)`; the
   target month is derived, not stored. `_history_context` (108-129) refuses runs that are not `COMPLETED`,
   have no `result_table`, or whose Iceberg schema lacks `forecast_month`/`forecast_value`
   (`routers/forecast.py:112-113, 127-128`).
6. Deterministic id-free tables (the non-snapshot publication path): `iceberg.silver.<gold>_planning`
   (rewritten on every real run; `iceberg_service.py:2537-2562`) — **not** referenceable per run.

---

## 11. Practical implications for a cloud-cost anomaly layer (factual constraints, not a design)

- Reuse the **Gold realized series** as the observed input; the engine does not persist it. The aggregation
  contract to mirror is `_fetch_period_records` (`forecast_service.py:1116-1179`) with
  `gold_date_expr` (638-678) and the type filter (1333-1336).
- Reference a computed projection by **`forecast_run.id`/`result_table`**, and read the frozen contract from
  `config_snapshot` (dimensions, measure, date/period/type columns, cutoff_days, allocation rules) rather than
  the live `forecast_config`.
- The comparison arithmetic already implemented (and reusable) is `_accuracy_metrics`
  (`routers/forecast.py:1134-1164`): `variance = realizado - projetado`, `variance_pct = variance/projetado`,
  `wape = |variance|/realizado`, `accuracy = 1 - wape`, `bias = (projetado - realizado)/realizado`.
- The engine's own "expected" baseline at day grain is only available as `forecast_month` totals; any daily
  expected rate must be derived (the existing precedent is `forecast_total / days_in_month`,
  `routers/forecast.py:1905`).
- Rule attribution per series/month is **not** persisted; only free-text rule names/notes in
  `forecast_run.error_metrics["business_rules"]`, and only for non-accuracy runs.
- `source_snapshot_id` is a reserved-but-unused hook: if the anomaly layer needs to bind a projection to an
  input-data snapshot, that column is currently unpopulated and any use would be new behavior.
