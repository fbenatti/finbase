# 04 — Referência da API

Base: FastAPI (`apps/api/app/main.py`), todos os routers sob `/` (o frontend acessa via
proxy `/api`). Health: `GET /health`.

## `/connections` — conexões de origem (`routers/connections.py`)

| Método | Path | Descrição |
|---|---|---|
| GET | `/connections/` | Lista conexões |
| GET | `/connections/{id}` | Detalhe |
| POST | `/connections/` | Cria (code, connector_type, config_json, blob_prefixes) |
| PUT | `/connections/{id}` | Atualiza |
| DELETE | `/connections/{id}` | Remove |

## `/etl` — extração, blobs, reprocess, schedules (`routers/etl.py`)

| Método | Path | Descrição |
|---|---|---|
| GET | `/etl/worker-health` | Saúde do worker (heartbeat); reconcilia tasks mortas |
| POST | `/etl/connections/{id}/scan` | Escaneia blobs Azure; classifica NEW/CHANGED/UNCHANGED |
| GET | `/etl/blobs/` | Lista blobs ingeridos |
| DELETE | `/etl/blobs/{id}` · `/etl/blobs/` | Remove blob(s) ingerido(s) |
| GET/POST/DELETE | `/etl/blobs/skipped` · `/etl/blobs/skip` · `/etl/blobs/skip/{id}` | Gerencia blobs ignorados |
| POST | `/etl/run` | Enfileira extração (task QUEUED → worker) |
| POST | `/etl/cancel/{task_id}` | Cancela task |
| POST | `/etl/connections/{id}/reset-source` | Reset para reimportação |
| GET | `/etl/status/{task_id}` · `/etl/tasks/` | Status e tasks em andamento |
| POST | `/etl/connections/{id}/test` | Testa credenciais/conexão |
| POST | `/etl/reprocess` | Enfileira reprocessamento Gold: `{destination_table_id, connection_ids, billing_periods, replace_existing}` |
| GET | `/etl/reprocess-history` · `/etl/reprocess-status/{task_id}` | Histórico e status |
| POST | `/etl/reprocess/cancel/{task_id}` | Cancela reprocesso |
| POST | `/etl/connections/{id}/process-destination` | Processa destinos da conexão |
| GET | `/etl/periods` | Períodos disponíveis (por conexão/destino) |
| GET | `/etl/connections-for-dest/{destination_table_id}` | Conexões que alimentam um destino |
| GET/POST/PUT/DELETE | `/etl/schedules[/{id}]` | CRUD de agendamentos cron (registra/remove job no APScheduler) |

## `/etl/periods` — status de períodos (`routers/periods.py`)

| GET `/` | Lista períodos com status (open/closed), com ações de fechar/reabrir via `period_service` |

## Rules — mappings, business rules, classificações, organização (`routers/rules.py`, sem prefixo)

| Método | Path | Descrição |
|---|---|---|
| GET/POST/PUT/DELETE | `/mappings/[/{id}]` | CRUD `etl_mapping` (legado) |
| GET/POST/PUT/DELETE | `/business-rules/[/{id}]` | CRUD `business_rule`. Criação/atualização garante a coluna de saída no destino (`_ensure_rule_output_column`) |
| GET/POST/PUT/DELETE | `/classifications/[/{id}]` | CRUD `classification_rule` |
| GET/POST/PUT/DELETE | `/departments/[/{id}]` | CRUD árvore de departamentos |
| GET/POST/PUT/DELETE | `/projects/[/{id}]` | CRUD projetos |
| POST | `/departments/import` | Importação CSV em massa (departamentos + regras de classificação); parse de condições multi-campo (`_parse_classification_conditions`) e adaptação ao destino |

## `/destinations` — tabelas de destino (`routers/destinations.py`)

| Método | Path | Descrição |
|---|---|---|
| GET/POST | `/destinations/tables/` | Lista/cria destination_table |
| GET | `/destinations/tables/{id}` · `/tables/{id}/schema` | Detalhe e schema efetivo |
| GET/POST/DELETE | `/destinations/tables/{id}/columns/` · `/destinations/columns/{id}` | CRUD de colunas |
| GET/POST/PUT/DELETE | `/destinations/mappings/[/{id}]` | CRUD `source_destination_mapping` |

## `/clone-rules` (`routers/clone_rules.py`)

| Método | Path | Descrição |
|---|---|---|
| GET/POST | `/clone-rules` | Lista (filtro `destination_table_id`) / cria (com validação `_validate`) |
| PUT/DELETE | `/clone-rules/{id}` | Atualiza / remove |
| POST | `/clone-rules/{id}/duplicate` | Duplica |
| POST | `/clone-rules/{id}/validate` | Valida colunas/roles contra o schema do destino |
| POST | `/clone-rules/{id}/preview` | Simula aplicação (prévia de clones/estornos) |
| GET | `/clone-rules/price-pendencies` | Pendências de preço On-Demand |

## `/forecast` (`routers/forecast.py`)

| Método | Path | Descrição |
|---|---|---|
| GET | `/forecast/metadata/{destination_table_id}` | Metadata descoberto via Trino (medidas, datas, dimensões, defaults) + sugestões FOCUS |
| GET/POST | `/forecast/configs` | Lista / cria config |
| PUT/DELETE | `/forecast/configs/{id}` | Atualiza (incrementa version) / remove (bloqueado se run ativa) |
| POST | `/forecast/configs/{id}/duplicate` · `/validate` | Duplica / valida contra metadata |
| GET/POST | `/forecast/configs/{id}/business-rules` | Regras de negócio do forecast (validadas contra dimensões) |
| PUT/DELETE | `/forecast/business-rules/{id}` | Atualiza / remove regra |
| GET/POST | `/forecast/configs/{id}/adjustments` | Ajustes dimensionais |
| PUT/DELETE | `/forecast/adjustments/{id}` | Atualiza / remove ajuste |
| POST | `/forecast/runs?config_id=` | Enfileira execução (task `forecast` → worker) |
| GET | `/forecast/runs` · `/forecast/runs/{id}` | Lista / detalhe com progresso |
| POST | `/forecast/runs/{id}/cancel` | Cancela |
| GET | `/forecast/runs/{id}/results` · `/backtest` · `/export` | Resultados (pivot do snapshot), métricas de backtest, exportação |
| — | histórico | `_history_context/_history_where/_validate_history_request`: consultas sobre o `config_snapshot` congelado da run |

## `/analysis` (`routers/analysis.py`)

| Método | Path | Descrição |
|---|---|---|
| GET | `/analysis/pivot/metadata/{destination_table_id}` | Schema da Gold para pivot |
| POST | `/analysis/pivot` | Executa pivot no Trino (rows, cols, measures, filters) — validado contra schema (`_validate_pivot_request`) |
| POST | `/analysis/pivot/search` | Busca full-text na tabela lakehouse |
| POST | `/analysis/pivot/drill-down` | Linhas detalhadas de uma célula |
| GET/POST/DELETE | `/analysis/pivot/views[/{id}]` | Views salvas (`pivot_view`) |
| GET | `/analysis/tables` | Tabelas Iceberg disponíveis |
| POST | `/analysis/query` | SQL livre no Trino (fallback local `_run_local_gold_query`) |
| GET | `/analysis/summary` | Sumário agregado |

## `/schema` (`routers/schema.py`)

Introspecção de schemas (colunas de conexões/destinos) para os construtores de regras.

## `/fact` (`routers/fact.py`)

Acesso à tabela fato legada `fact_cost` (consulta/export).

## Códigos e convenções

- Tasks sempre passam pelos estados `QUEUED → RUNNING → COMPLETED|FAILED|CANCELLED`.
- Criação retorna `201`; deleção `204`.
- Schemas Pydantic em `apps/api/app/schemas/schemas.py`.
