# Azure FinOps Intelligence — log de execução (P0 → …)

Documento de registro por pacote do plano `planDataCollector.md` (raiz do repositório).
Contém o artefato **P0** e, a partir daqui, a entrada/saída/aceite de cada pacote seguinte.
Não copia o plano inteiro; aponta para ele.

Contexto desta auditoria:

- Repositório: `/Users/fernandohenriquebenatti/projetos/OpenFinops`
- Branch: **`novo_dev`** (remoto `origin` = `git@github.com:fbenatti/finops-intelligence.git`)
- Commit do P0: **`e4e78b76`** — `docs(azure-finops): add execution plan and P0 audit artifact`
  (push `43b4d802..e4e78b76` em `novo_dev`), contendo apenas `planDataCollector.md` e este log.
- Árvore de trabalho no momento do P0: **19 arquivos modificados + 4 não rastreados**.
  Alterações do usuário foram preservadas; **nenhum código funcional foi alterado no P0**.
- Ambiente vivo: PostgreSQL `localhost:5433/finops`, API `:8001`, Web `:5173`,
  Trino `:8080` (1 coordinator + 2 workers), Iceberg SQL catalog no mesmo PostgreSQL,
  warehouse em `apps/data/iceberg/`.
- Data de referência da coleta: **2026-09-22**.

### Classificação dos 19 arquivos modificados (decidida no início de P1)

| Grupo | Arquivos | Destino |
|---|---|---|
| **Refatoração P1** (namespace Gold→Silver, Dimon→Gold) | `apps/api/app/services/iceberg_service.py`, `dimon_materialization.py`, `dimon_builder.py`, `forecast_service.py`, `shared_cost.py`, `ai_usage_dimon.py`, `apps/api/app/routers/etl.py`, `analysis.py`, `forecast.py`, `dimon.py`, `apps/api/worker.py`, `apps/web/src/pages/Analysis/Analysis.tsx`, `Reprocess/Reprocess.tsx`, `Reprocess/ReprocessDimonTab.tsx`, `Reprocess/ReprocessForecastTab.tsx` (15 arquivos) | **Preservar e concluir** em P1B–P1D |
| **Alteração do usuário, não relacionada** (serviço `supervisor` + túnel do bastion) | `docker-compose.yml`, `infra/trino/catalog/iceberg.properties`, `scripts/keep_bastion_tunnel.sh`, `start-local.sh` (4 arquivos) | **Preservar sem tocar**; não entra em commit de P1 |
| **Não rastreado, do usuário** | `apps/api/supervisor.py` (recovery de fila), `.jev-finops` (contém chave de API — permanece fora do git) | **Preservar sem tocar** |
| **Não rastreado, utilitário de migração** | `apps/api/scripts/reset_derived_layers.py` (dropa Silver/Gold/Dimon/forecast, preserva Bronze) | Usar **somente** como rollback de P1D, depois do cutover validado |

> Nota: `infra/trino/catalog/iceberg.properties` já está aplicado no container em execução
> (`host.docker.internal:5433`), então o Trino lê o catálogo correto. Não é preciso reiniciar
> o Trino por causa desse arquivo.

> **Nota de versão do plano.** O plan em `planDataCollector.md` (14:50) é mais recente que a
> cópia recebida na instrução. O plan em disco (a) move a camada detalhada de Gold para
> **Silver**, (b) determina **remover a Dimon**, (c) faz cubos finais na **Gold**, (d) inicia a
> telemetria em **15 dias** crescendo até 90. Esta auditoria usa o plan em disco como fonte de
> verdade, porque é ele que casa com o runtime medido. Os números de pacote P1–P12 também
> diferem do texto da instrução; ver §7.

---

## 1. Mapa real do OpenFinops e divergências

### 1.1 O que o runtime realmente é

| Componente | Realidade verificada | Evidência |
|---|---|---|
| Camadas Iceberg | `bronze`, `silver`, `dimon`, `forecast` com tabelas. `gold` **existe como namespace e está vazio**; `classified` e `financial_base` existem e nunca são usados | Trino `SHOW TABLES FROM iceberg.gold` → vazio; `iceberg_tables` = 44 linhas, nenhuma em `gold` (`catalog_name='sql'`) |
| Tabela FOCUS realizada | Vive em **`iceberg.silver.dest_modelo_focus`** (4.723.025 linhas em 202608) | PyIceberg `load_table(("silver","dest_modelo_focus"))` OK; `("gold","dest_modelo_focus")` → `NoSuchTableError` |
| Namespace das tabelas de destino | Todo o encanamento de destino já foi trocado para `silver` na árvore de trabalho | `iceberg_service.py:668,970,1002,1043,1078,1105,1181,1252,1307,1430` default `namespace="silver"`; `etl.py:1772,1777,1984-1990,2218-2224` força `"silver"` |
| Warehouse físico | `apps/data/iceberg/{bronze,silver,gold,dimon,forecast}/` | `ls apps/data/iceberg/` |
| Catalog | SQL/PostgreSQL, `catalog_name='sql'`, warehouse lógico `file:///data/iceberg` | `iceberg_catalog_migration.py:19`; `iceberg_namespace_properties` lista os 7 namespaces |
| Fila / worker | `etl_task` + `worker.py` + `supervisor.py` (não rastreado) | `etl_task` 5.9k linhas; tipos `extraction`, `reprocess`, `forecast`, `dimon_reprocess`, `bronze_audit`, `shared_cost`, … |
| Scheduler | `scheduled_task` com 4 crons ativos | §3.4 |

### 1.2 Divergências entre plano, documentação e runtime

| # | Divergência | Evidência | Impacto |
|---|---|---|---|
| D1 | O plano recebido diz que `gold.dest_modelo_focus` é o livro financeiro; o runtime não tem `gold` algum e a árvore de trabalho já migra para `silver` | Trino + PyIceberg (§1.1) | **Alto.** Toda leitura de FOCUS, órfãos, IA, OCI, planning e savings que aponte para `iceberg.gold` está quebrada hoje |
| D2 | `dimon_materialization.DIMON_NAMESPACE` foi de `"dimon"` para `"gold"`, mas `dimon.py` continua **lendo** `iceberg.dimon` | `dimon_materialization.py:24` vs `dimon.py:460,568,590,599` | **Alto.** Marts escritos em gold, lidos de dimon → dashboards vazios/velhos |
| D3 | `merge_planning_rateio` lê de `("gold", …)` mas o planning é publicado em `("silver", …)` | `iceberg_service.py:1831` vs `:1793/1797`; chamador `shared_cost.py:699` | **Alto / perda de dado.** O rateio republica a tabela de planning só com `tipo='rateio'`, descartando realizado+forecast |
| D4 | Staging criado em um namespace, swap/cleanup no outro | `dimon_builder.py:249,257` (gold) + `:260-265` sem namespace → silver; `ai_cost_materialization.py:108-143` idem | **Alto.** Erro "table not found" ou staging órfão |
| D5 | ~140 referências a `iceberg.gold` sobrevivem em ~31 arquivos, incluindo caminho inteiro de pivô/análise, planning, savings, órfãos, IA, OCI, tag policy e templates SQL persistidos em `DimonRule.sql_template` | §1.3 e anexo do subagente de namespace | **Alto.** Correção de código não corrige linhas já persistidas |
| D6 | Docs `docs/ai/*` não foram atualizados: 8 arquivos ainda declaram `gold`/`dimon` como camada final | `01-architecture.md:40,83`; `02-data-model.md:69-72`; `03-business-rules.md:202`; `10-finos-intelligence.md:62-74,122`; `ai-usage-dimons.md:4`; `11-azure-resource-graph…:201-302`; `desligue-programado.md:169,181`; `PLAN-wisper-oci-dashboard.md:41-498` | Médio (orienta mal o próximo agente) |
| D7 | Testes falham no baseline: 3 por causa da migração, 2 pré-existentes | `pytest` → 5 failed / 329 passed. Migração: `test_dimon_materialization.py:68`, `test_forecast_daily_drilldown.py:13`, +1. Pré-existentes: `test_auth_unit.py::test_dev_defaults_safe` (`auth_enabled=False` no ambiente), `test_azure_ptu_finops.py::test_license_pool_default_target_type` (`app.routers.ai_cost_origin` não existe) | Médio |
| D8 | Export Cost Management **4P está vazio desde abr/2026** e ninguém percebeu | §3.2 | **Alto.** O 4P fica sem custo detalhado e o pipeline reporta `COMPLETED` |
| D9 | Fontes **não-custo** têm mapping para o destino `MODELO FOCUS` (id 1) e aparecem no FOCUS com `cloud_origem` próprio | 11 mappings cada para `azure-automation-4p/ti` e `azure-resource-graph-4p/ti`; linhas em `202607/202608` com `realizado = NULL` | **Alto.** O FOCUS deixa de ser livro financeiro limpo; qualquer soma por prefixo `azure-` inclui lixo |
| D10 | Escritas concorrentes na mesma Bronze falham com "branch main has changed" | 7 tasks `COMPLETED_WITH_ERRORS`; ex.: task 5355 (`azure-ti`, 2,4M linhas) | Médio, mas é exatamente o modo de falha que os coletores novos vão repetir |
| D11 | Trino não lê 3 tabelas Bronze Azure | `bronze.raw_azure_ti`, `raw_azure_automation_4p/ti` → `Error processing metadata for table`; PyIceberg lê as 3 sem erro | Médio. O caminho de leitura dos dashboards (Trino) está quebrado para essas tabelas |
| D12 | `credential_profile` é gravado mas **ignorado**: `resolve_config()` só devolve o próprio `config_json` | `azure_identity.py:18-20`; docs `11-…:49-51` descrevem um fallback para `Settings`/`.env` que não existe no runtime | **Alto para P1.** Antes de criar fontes-filhas, a resolução de credencial precisa existir de verdade |
| D13 | `_credential` está **duplicado** em 4 serviços; `azure_identity.py` só é importado por um | `grep azure_identity` → apenas `azure_resource_graph_service.py:27`; cópias em `azure_ptu_service.py:74-85`, `azure_automation_service.py:32-49`, `azure_reservation_service.py:50-64`, `azure_extractor.py:84-88` | Médio. Consolidar antes de somar 8 fontes novas |
| D14 | `azure_automation_jobs` e `azure_resource_graph` passam o allowlist do `task_runner` mas **não têm `elif`**: caem no `else` → `GcpBigQueryExtractor` | `task_runner.py:2684` (allowlist) vs `:2749-2753` (`else`); os branches corretos existem só no worker (`worker.py:1199-1246`) | **Alto (latente).** Qualquer chamador não-worker de `_run_etl_sync` processa ARG/Automation com o extrator GCP errado, sem erro |
| D15 | `Settings` já tem as credenciais `AZURE_4P_*`/`AZURE_TI_*`, mas elas são consumidas **só pelo seeder** | `config.py:153-164` → `seed.py:14-53`; nada em runtime lê `.env-4p`/`.env-ti` | Baixo. É exatamente o insumo que falta para D12 |

### 1.3 Pontos de código que importam para o plano

| Capacidade | Arquivo real | Situação |
|---|---|---|
| Conexões | `apps/api/app/models/source_connection.py`, `routers/connections.py` | Existe; `code` único (`connections.py:210-214`); `create_connection` já tem hooks de setup por `connector_type` (`:226-237`) |
| Tipos de conector | `routers/connections.py:55-101` (`TOOL_CONNECTOR_TYPES`, `GRID_FILTER_VALUES`) | Lista branca explícita; conector novo "aparece" adicionando aqui |
| Fila/worker | `worker.py:1199-1290` (branches PTU/Automation/ARG), `services/task_runner.py` | Existe; dispatch por `connector_type`. **Atenção D14**: ARG/Automation não têm `elif` em `task_runner.py` |
| Escrita Bronze | `iceberg_service.write_bronze` (`:403`), `_bronze_table_id` (`:221-223`) | `raw_{code}` com `-`/`.` → `_`; o diff não rastreado adiciona `delete_bronze_slice` antes do write (idempotência por fatia) |
| Staging/swap | `write_destination_staged`, `append_to_staged`, `publish_staged_partition`, `swap_staged_table`, `cleanup_staged_table` | Existem, todos com default `silver` hoje |
| Reprocess | `routers/etl.py` (`_reprocess_destination_sync`) | Ordem mapping → clone → regras → classificação → Silver preservada |
| Cubos | `services/dimon_materialization.py` | "Dimon" virou mero nome de motor de cubo; escreve em `DIMON_NAMESPACE = "gold"` (`:24`) |
| ARG | `services/azure_resource_graph_service.py`, `routers/azure_resource_graph.py` | Conector completo (inventário + órfãos + Dimon de custo) |
| Automation | `services/azure_automation_service.py`, `routers/scheduled_shutdown.py` | Conector completo (jobs + schedules + governança) |
| PTU | `services/azure_ptu*.py` (11 arquivos), `routers/azure_ptu.py` | Pipeline próprio, ilha gold coerente consigo mesma |
| Identidade Azure | `services/azure_identity.py` | **Só `azure_resource_graph_service.py:27` importa** (D13); outras 4 cópias de `_credential` |
| Monitor (transporte) | `services/azure_ptu_service.py:586,624-632` | **Já existe** chamada a `microsoft.insights/metrics` (`2018-01-01`), mas `METRIC_SPECS` (`:34-48`) cobre só `AzureOpenAI*` |
| Advisor / reservas / benefícios | `services/azure_reservation_service.py` | Já coleta Advisor (`2022-09-01`), reservas EA/MCA (`2024-04-01`), savingsPlans, billingProfiles |
| UI Connections | `apps/web/src/pages/Connections/Connections.tsx` | Wizard por `connector_type`; pattern visual validado |

### 1.4 APIs Azure já chamadas (inventário para não reimplementar)

| API | Endpoint | api-version | Onde |
|---|---|---|---|
| ARM subscriptions | `GET {ARM}/subscriptions` | `2020-01-01` / `2022-12-01` | `azure_automation_service.py:24,81`; `azure_ptu_service.py:28,115`; `azure_reservation_service.py:285` |
| Automation accounts / jobs / schedules / runbooks | `.../Microsoft.Automation/automationAccounts/...` | `2023-11-01` | `azure_automation_service.py:25-26,88-90,135-139,178,536,549,564` |
| Azure Resource Graph | `POST {ARM}/providers/Microsoft.ResourceGraph/resources` | `2022-10-01` | `azure_resource_graph_service.py:31-32,171-172` |
| Azure Monitor metrics | `GET {accountId}/providers/microsoft.insights/metrics` | `2018-01-01` | `azure_ptu_service.py:31,624-632` |
| Cognitive Services deployments | `GET {accountId}/deployments` | `2024-10-01` | `azure_ptu_service.py:30,132` |
| Event Hub (probe) | `.../eventhubs/{name}`, `/partitions`, `/authorizationRules` | `2021-11-01` | `azure_ptu_service.py:974-985` |
| Billing accounts / profiles / reservations / savingsPlans | `{ARM}/providers/Microsoft.Billing/...` | `2020-05-01`, `2024-04-01` | `azure_reservation_service.py:40-42,100-165` |
| Azure Advisor | `GET /subscriptions/{sub}/providers/Microsoft.Advisor/recommendations` | `2022-09-01` | `azure_reservation_service.py:279,299-303` |
| Benefit recommendations | `.../Microsoft.CostManagement/benefitRecommendations` | `2023-11-01` | `azure_reservation_service.py:280,315-319` |
| Blob Storage (data plane) | `https://{storage_account}.blob.core.windows.net` | SDK | `azure_extractor.py:89-94` |

**Nunca chamadas hoje** (tudo a criar no plano): Cost Management query/usageDetails
(`COST_API = "2023-11-01"` existe em `azure_reservation_service.py:42` e **não é usado**),
Price Sheet, retail prices, `Microsoft.Compute/skus`, quota/usages, métrica de VM.
Azure Policy `modify` é **gerado** mas nunca executado pelo backend
(`azure_tag_policy.py:100-123,165+`).

### 1.5 Pontos fixos que todo `connector_type` novo precisa tocar

Adicionar uma fonte hoje exige editar **4 cadeias `if/elif` manuais** — não há registry:

1. `routers/connections.py:55-65` (`TOOL_CONNECTOR_TYPES`) e `:81-100` (`GRID_FILTER_VALUES`);
2. `routers/etl.py` — scan/test (`:85-253`, `:692-875`) e allowlist de `/etl/run` (`:512-521`);
3. `worker.py:1199-1336` — branch de extração por tipo;
4. `services/task_runner.py:2684` (allowlist) e `:2749-2753` (`else`/extrator padrão — ver D14).

Detalhe que surpreende: `azure_blob` **acumula dois papéis** — fonte de billing FOCUS e
"dono genérico" da credencial Azure (a descoberta de tenant do tag policy filtra
`connector_type == "azure_blob"`, `azure_tag_policy_service.py:143,172`).
Separar isso é parte de P1.

### 1.6 Inventário de referências Dimon (insumo do P1)

- **101 arquivos** contêm a palavra `dimon` em código: 21 routers, 37 services, 2 models,
  24 páginas web, 1 service web.
- 10 documentos citam Dimon, incluindo `docs/ai/10-finos-intelligence.md` (308 linhas),
  `docs/ai/ai-usage-dimons.md`, `docs/ai/AGENTS.md`.
- Scripts/testes: `apps/api/reprocess_dimon_centro_custo.py`,
  `apps/api/tests/test_dimon_materialization.py`, `test_azure_ptu_dimon.py`, `test_forecast_daily_drilldown.py`.
- Namespace Iceberg com 6 tabelas (`iceberg_tables`: `_tmp_vm_monthly`,
  `dest_dm_ai_consumption_daily`, `dest_dm_ai_license_snapshot`, `dest_dm_copilot_license_snapshot`,
  `dest_dm_copilot_usage_daily`, `dest_dm_copilot_user_period`) + 17 `dimon_rule` no PostgreSQL.

---

## 2. Matriz de fontes `reusar / estender / criar`

Legenda de destino: **B** = Bronze, **S** = Silver detalhada, **G** = cubo Gold.

| `source_key` | Existe? | `code` real (DB) | Bronze real | Silver | Gold | Ação |
|---|---|---|---|---|---|---|
| `cost_exports` | **Sim** | `azure-4p` (id 1), `azure-ti` (id 2) | `bronze.raw_azure_4p`, `bronze.raw_azure_ti` | `silver.dest_modelo_focus` | cubo a criar | Reusar; **corrigir export 4P** (D8) |
| `cost_exports` (marketplace) | Sim | `azure-marketplace` (id 10) | `bronze.raw_azure_marketplace` | `silver.dest_modelo_focus` | — | Reusar |
| `automation_jobs` | **Sim** | `azure-automation-4p` (27), `azure-automation-ti` (30) | `raw_azure_automation_4p/_ti` | `silver.dest_modelo_automation(_schedule)` | cubo desligue a criar | Reusar; parar de escrever no destino FOCUS (D9) |
| `resource_graph` | **Sim** | `azure-resource-graph-4p` (34), `-ti` (35) | `raw_azure_resource_graph_4p/_ti` | `silver.dest_cloud_resource_inventory`, `silver.dest_cloud_orphan_resources` | cubo órfãos | Reusar; **estender** para perfil VM/relações |
| `openai_ptu` | **Sim** | `azure-4p-ptu` (23) | `raw_azure_4p_ptu`, `raw_azure_ptu_metrics` | — | `gold.dest_azure_ptu_*` | Preservar (ilha gold coerente) |
| `advisor` | Parcial | — | `bronze.azure_advisor_recommendations` | — | — | Existe coleta; fora do MVP |
| `benefit_recommendations` / reservas | Parcial | — | `bronze.azure_benefit_recommendations`, `bronze.azure_reservations_catalog` | — | — | Existe coleta; útil em P9/P11 (compromisso) |
| `aks_inventory` | **Não** | proposto `azure-4p-aks-inventory` | a criar | entidades/arestas K8s | cubo consumo | **Criar** (P3) |
| `aks_cost_allocation` | **Não** | proposto `azure-4p-aks-cost-allocation` | a criar | alocação diária | ponte → cubo | **Criar**, condicionado a add-on/oferta (P3) |
| `container_insights` | **Não** | proposto `azure-4p-container-insights` | a criar | métricas node/pod/container | — | **Criar** se necessário (P3) |
| `monitor_vm_metrics` | **Não** | proposto `azure-4p-monitor-vm-metrics` | a criar | série temporal | perfil | **Criar** (P5) |
| `compute_skus` | **Não** | proposto `azure-4p-compute-skus` | a criar | SKU por região | — | **Criar** (P7) |
| `compute_quota` | **Não** | proposto `azure-4p-compute-quota` | a criar | quota por sub/região | — | **Criar** (P7) |
| `retail_price` | **Não** | proposto `azure-4p-retail-price` | a criar | preço público | — | **Criar** (P7) |
| `customer_price` | **Não** | proposto `azure-4p-customer-price` | a criar | preço contratado | — | **Criar** se permitido (P7) |
| `region_policy` | **Não** | proposto `azure-4p-region-policy` | a criar | restrições | — | **Criar** (P11) |

Não existe `source_key`, `environment_slug` nem capability/status por fonte em lugar nenhum do
schema atual: `source_connection` tem `code`, `connector_type`, `cloud_name`, `cloud_scope`,
`config_json`, `blob_prefixes`, `last_test_status/message/at` e nada mais
(`models/source_connection.py:1-24`). Isso é material novo de P1.

---

## 3. Baseline reproduzível (2026-09-22)

Todas as queries abaixo são read-only e podem ser repetidas. Medida de custo usada:
**`realizado`** da tabela FOCUS (BRL, com taxas/markup já aplicados pelas regras de negócio),
somada por período. `billedcost`/`effectivecost` ficam como colunas de referência.

### 3.1 Custo FOCUS por período e origem

`iceberg.silver.dest_modelo_focus`, agrupado por `_billing_period` + `source_connection_id`:

| Período | Total linhas | Total `realizado` (BRL) | Linhas de fonte não-custo (`realizado` NULL) |
|---|---|---|---|
| 202606 | 3.795.015 | 21.377.762,68 | 0 |
| 202607 | 4.023.771 | 18.298.301,05 | 228 |
| 202608 | 4.723.025 | 18.674.809,88 | 11.094 |

Conferência cruzada: 4.723.025 é exatamente a contagem de 202608 lida via PyIceberg
(§1.1), independente do Trino.

Por origem em 202608 (o mês fechado mais recente):

| `source_connection_id` | code | linhas | `realizado` (BRL) |
|---|---|---|---|
| 2 | `azure-ti` | 3.453.100 | **12.031.488,93** |
| 10 | `azure-marketplace` | 317 | 1.473.204,58 |
| 9 | `oci-prod` | 1.166.393 | 3.726.267,35 |
| 8 | `gcp-ttech` | 20.472 | 897.074,90 |
| 4 | `gcp-ti` | 101 | 91.896,38 |
| 5 | `mongodb-atlas-pro` | 13.810 | 94.866,33 |
| 3 | `gcp-4p` | 57.738 | 360.011,41 |
| **1** | **`azure-4p`** | **0** | **—** (D8) |
| 27 / 30 | automation 4P/TI | 944 / 679 | NULL |
| 34 / 35 | resource graph 4P/TI | 9.208 / 263 | NULL |

### 3.2 Anomalia `azure-4p` (D8) — evidência

- Bronze por período (`raw_azure_4p`): 202512=823.720; 202601=790.406; 202602=693.389;
  **202603=683.474; 202604=51; 202605=1; 202606=1; 202607=3; 202608=0**.
- `ingested_blob` de `azure-4p`: o blob de 202605 é `part_0_0001.csv` com **1 linha e 2.228 bytes**;
  os de 202605/202606 têm 1 linha; 202607 tem 3 linhas; 202608/202609 têm **0 linhas e 964 bytes**.
- Tasks de extração `azure-4p` completam com `rows_raw = 0` todos os dias desde então
  (tasks 5354, 4466, 4424, …) e mesmo assim terminam `COMPLETED`.
- As 1–3 "linhas" de 202605–202607 não são extrato analítico: carregam ~R$ 156–158 mil cada
  (linhas de resumo/ajuste), o que **não** substitui o custo detalhado perdido.

Consequência: a série 4P em Silver termina em 202603. Sem correção do export/lado Azure, o 4P
não tem base para rightsizing. **Não é bug do OpenFinops** — é o export Cost Management que
deixou de entregar dados. Ação P2: validar no portal/ARM o estado do export 4P e, se o export
mudou de escopo, recoletar o histórico antes de qualquer análise.

### 3.3 Baseline de VM (o alvo do MVP)

202608, `azure-ti`, pelo `ServiceSubcategory` do FOCUS:

| Recorte | Linhas | `realizado` (BRL) |
|---|---|---|
| `servicesubcategory` ~ `Virtual Machines` | 280.899 | **2.700.285,31** |
| `servicesubcategory` ~ `Managed Disks` | 761.785 | 848.044,08 |
| `resourceid` contém `/virtualmachines/` (case-insensitive) | 785.275 | 869.346,06 |

- IDs distintos com `/virtualmachines/` em 202608: **124.810** em minúsculas / **240.618** em
  caixa exata. A diferença de ~116 mil é **variante de caixa do mesmo Resource ID no export**.
  Regra para P2: canonicalizar ID (trim + lowercase + barras consistentes) **antes** de contar.
- VMs ≈ 22% do gasto Azure (`azure-ti`), discos ≈ 7%.

### 3.4 Estado de conexões, mappings e jobs

- 22 `source_connection`; 8 com `cloud_scope='azure'` (§2).
- 974 mappings ativos + 1 inativo; `MODELO FOCUS` (id 1) tem mappings de **8 conexões**,
  incluindo 4 não-custo (D9).
- 7 `destination_table` ativas: FOCUS(1), LICENCAS IA(34), CONSUMO IA(35),
  AUTOMATION(36), AUTOMATION SCHEDULE(38), INVENTÁRIO CLOUD(40), RECURSOS ÓRFÃOS(41).
- 17 `dimon_rule`, das quais **2 estão `EMPTY`**: `saving_desligue_vm` (id 11) e
  `consumo_diario_vm` (id 16). Ambas são regras de **VM** com SQL custom.
  `consumo_diario_vm` já aponta para `iceberg.silver.dest_modelo_focus` e é um protótipo útil
  do que P2/P4 precisam — mas nunca materializou.
- 4 `scheduled_task`: `Coleta Diária Cloud` (03:00, connection 1, destino 1),
  `Coleta Diária Ferramentas` (04:00, connection 13, destinos 34/35),
  `Backup Diário Full`, `Reprocessamento` (domingo 02:00).
- `etl_task`: 2.332 `reprocess` COMPLETED contra 1.014 FAILED e 640 CANCELLED.
- `.venv/bin/python -m pytest tests/` → **5 failed, 329 passed** (D7).

### 3.5 Infraestrutura em observação

- Trino: coordinator + 2 workers; `trino-worker-1` com 3 restarts, `trino-worker-2-1` com 2
  restarts nas últimas horas; consultas pesadas devolvem `503 Server is still initializing`
  durante reinício de worker. Qualquer verificação de baseline precisa de retry.
- Warehouse Iceberg mistura caminhos `bronze.db/<tabela>/` (PyIceberg lê, Trino falha em 3)
  e `bronze/<tabela>/` (Trino lê). Resolver isso é pré-requisito de confiança em leitura (D11).

---

## 4. Metadata escolhida para o grupo Azure, credencial e compatibilidade de codes

### 4.1 Agrupamento e associação de fonte

Proposta para P1, mínima e aditiva (não quebra `code`, Bronze nem histórico):

1. **Novo campo de metadata por conexão** — `config_json.source_key` (string) e
   `config_json.environment_slug` (string estável). Aditivo; conexões legadas ganham por seed
   idempotente. `code` **não muda nunca** quando o nome visível muda.
2. **Tabela de catálogo de fontes** (PostgreSQL, metadata pura) — `azure_source_catalog`:
   `source_key` (PK lógica), versão de schema, escopo Azure, `credential_profile`,
   permissões exigidas, API+versão, paginação, política de 429/5xx, janela, checkpoint,
   chave natural, frequência, forma (snapshot/upsert), `code` proposto, tabela Bronze,
   transformação Silver, consumidores. O catálogo é **declarativo**, não código espalhado.
3. **Status por fonte** — derivar de dados que já existem (`ingested_blob`,
   `etl_task`, `last_test_status`) + teste de capacidade por API. Estados do plano:
   `NOT_CONFIGURED`, `AVAILABLE`, `PARTIAL`, `UNAVAILABLE`, `ERROR`, com último
   sucesso/falha, linhas, janela e permissão faltante. Erro de uma fonte não pode impedir
   as independentes — o worker já é por task, então isso é natural.
4. **UI**: agrupar por ambiente dentro de `Connections → Ferramentas Cloud → Azure`,
   reusando `Connections.tsx` e o padrão visual de `08-ui-design-reference.md`.

### 4.2 Credencial compartilhada (bloqueador de P1 — D12)

Hoje `azure_identity.resolve_config()` devolve apenas `config_json`. Medido no banco:

| Conexões | `client_secret` inline | `credential_profile` |
|---|---|---|
| `azure-4p`, `azure-ti`, `azure-marketplace` | sim | **não têm** |
| `azure-4p-ptu`, `azure-automation-4p`, `azure-automation-ti`, `azure-resource-graph-4p`, `azure-resource-graph-ti` | sim | sim (`azure-4p` / `azure-ti`), mas **ignorado em runtime** |

O `credential_profile` só é usado hoje como rótulo de tenant
(`azure_automation_service.py:618`) ou como booleano de UI (`azure_ptu.py:113`,
`reservations.py:133`). `Settings` já expõe `AZURE_4P_*`/`AZURE_TI_*` (`config.py:153-164`),
mas **apenas o seeder** as consome (`seed.py:14-53`); nada lê `.env-4p`/`.env-ti` em runtime,
embora a UI afirme que sim (`Connections.tsx:1015-1017`). Antes de criar conexão-filha:

- Implementar resolução real: `credential_profile` → `Settings`/`.env-{profile}` → fallback
  para `config_json`. Sem isso, cada fonte nova duplicaria `client_secret` (exatamente o que
  o plano proíbe).
- Consolidar as 4 cópias de `_credential` (D13) em `azure_identity.py`.
- Manter compatibilidade: as 3 conexões Cost Management têm `client_secret` inline e
  **não** têm profile; elas continuam funcionando pelo fallback.
- Não logar nem devolver secrets em endpoint de teste. O teste de capacidade só devolve
  booleanos por escopo (clusters visíveis? cost analysis ligado? workspace presente?).
- **Achado de segurança (fora do escopo, registrar):** `.env` na raiz contém um
  `token_git` em texto claro. Não foi exposto aqui, mas deve ser rotacionado/removido.

### 4.3 Compatibilidade e unicidade de `code`

- Convenção nova: `azure-{environment_slug}-{dataset_slug}`, ASCII minúsculo, ≤ 50 chars
  (limite real da coluna `code varchar(50)`).
- Unicidade **lógica** já existe (`code` é UNIQUE; `connections.py:210-214` devolve 409).
- Unicidade **física** não existe: `_bronze_table_id` faz `-`/`.` → `_`
  (`iceberg_service.py:221-223`). `azure-4p-monitor` e `azure_4p.monitor` colidiriam em
  `raw_azure_4p_monitor`. P1 deve adicionar validador que compara o nome físico sanitizado
  contra todas as conexões existentes **e** contra `iceberg_tables`.
- Códigos legados de Automation/ARG **vinculam, não renomeiam** (`azure-automation-4p`,
  `azure-resource-graph-ti`). O padrão novo só vale para fontes novas.
- Custo não se identifica por prefixo textual. O critério correto é lista explícita de
  conexões de custo (hoje: 1, 2, 10) ou metadata `source_key='cost_exports'` — nunca
  `code LIKE 'azure-%'` (D9 mostra o porquê).

---

## 5. Dependências, permissões/APIs a validar e riscos de dados

### 5.1 Grafo de dependências dos pacotes

```
P0 (este artefato)
 └─► P1  Bronze/Silver/Gold + remover Dimon  ◄── BLOQUEIA TODO O RESTO
      └─► P2  identidade de VM e custo atual  (precisa de P1 e do export 4P corrigido)
           ├─► P3  AKS spike + topologia (independente de P2, mas precisa de P1)
           ├─► P5  Azure Monitor 15 dias ─► P6 perfil ─┐
           └─► P7  catálogos/preço/quota ──────────────┴─► P8 rightsizing
                                                             └─► P9 histórico/revisão
                                                                  └─► P10 cubos + UI
                                                                       ├─► P11 estratégias avançadas
                                                                       └─► P12 operação/resultado
```

Ponto de ordem: o plan em disco coloca a **refatoração de camadas e a remoção da Dimon em P1**,
antes de qualquer coleta nova. Isso é correto e obrigatório — sem isso, todo coletor novo
escolheria um namespace diferente. **P3 (AKS) pode andar em paralelo** porque é um spike com
Bronze própria.

### 5.2 APIs e permissões a validar antes de codificar cada fonte

| Fonte | O que validar no tenant 4P/TI | Estado |
|---|---|---|
| Cost Management Exports | se o export 4P ainda existe, escopo e motivo do vazio desde abr/2026 (D8) | **Pendente, bloqueia P2** |
| ARG | `Reader` nas subscriptions; paginação `$skipToken` (1.000/página); `collect_inventory=false` nas duas conexões hoje | Já funciona |
| Automation | `Reader` no Automation Account; jobs/schedules por tag (não trazem `ResourceId` da VM) | Já funciona, com limitação conhecida |
| Azure Monitor | retenção de métricas de plataforma (93 dias), batch ≤ 50 Resource IDs mesma região/sub, suporte por métrica, guest metrics exigem AMA/DCR | **A validar em P5** |
| Resource SKUs | `Microsoft.Compute/sku` + `nextLink`; catálogo global ≠ disponibilidade na subscription | A validar em P7 |
| Quota | `Microsoft.Compute/locations/{loc}/usages`; quota pode exigir aprovação | A validar em P7 |
| Price Sheet | EA vs MCA têm endpoint/escopo distintos; resultado assíncrono; versões `Microsoft.Consumption` antigas em aposentadoria | A validar em P7 |
| Retail Prices | endpoint público, sem auth; útil como fallback indicativo | A validar em P7 |
| AKS | tier do cluster, add-on Cost Analysis, Container Insights/workspace, acesso ao K8s API | **Spike em P3** |

Nenhuma versão de API deve ser fixada por suposição: validar documentação vigente + payload
sanitizado do tenant na etapa da fonte.

### 5.3 Riscos de dados (ordenados por probabilidade × impacto)

| Risco | Evidência | Mitigação |
|---|---|---|
| Migração de camada parcial corromper leitura silenciosamente | D1–D5 | P1 termina só quando busca no código **executável** não acha `iceberg.gold` exceto ilhas declaradas |
| Perda de realizado+forecast no planning | D3 | Corrigir `merge_planning_rateio` e comparar totais de planning antes/depois |
| 4P sem base histórica | D8 | Recoletar antes de P2; até lá, 4P entra como `REQUIRES_VALIDATION`, nunca zero |
| FOCUS contaminado por fontes não-custo | D9 | Lista explícita de conexões de custo; medir e remover as linhas de Automation/ARG do destino FOCUS |
| Dupla contagem por variante de caixa do Resource ID | §3.3 (~116 mil IDs em 202608) | `canonical_resource_id` na Silver antes de qualquer contagem/join |
| Concorrência de escrita na Bronze | D10 | Serializar por tabela Bronze ou particionar a Bronze por fonte; retry com nova leitura de metadata |
| Extrator errado escolhido silenciosamente | D14 (`task_runner.py:2749-2753`) | Adicionar `elif` (ou falha explícita) para todo `connector_type` novo antes de registrar a conexão |
| Credencial duplicada em 8 fontes novas | D12/D13/D15 | Consolidar em `azure_identity.py` antes de P1 criar conexões-filhas |
| Trino não lê parte da Bronze | D11 | Alinhar caminhos `.db` vs não-`.db`; usar PyIceberg como caminho de auditoria |
| Métrica ausente virar zero | plano §Contrato de métricas | `NULL` + `quality_status`; ausência com motivo (`NOT_AVAILABLE`, sem agente, sem permissão, sem retenção, VM desligada) |
| Join cruzar subscriptions/tenants | nome de VM repetido entre subscriptions | Chave é `canonical_resource_id`, nunca `resource_name` |

---

## 6. Critérios de teste e rollback do primeiro pacote funcional

### 6.1 Antes de P1 (baseline congelado — já feito aqui)

1. Guardar as queries de §3 e os totais por período/origem antes de mexer em namespace.
2. Confirmar `pytest` baseline: **5 failed / 329 passed**, com a lista nominal de D7.
3. `pg_dump` do schema `public` (metadata) antes de qualquer ALTER; Bronze/Silver no warehouse
   já é preservado por cópia do diretório.

### 6.2 Aceite de P1 (refatoração de camadas + remoção da Dimon)

- Busca no código executável (`apps/api/app/**`, `apps/api/*.py`, `apps/web/src/**`) não
  encontra dependência ativa de `dimon` nem de `iceberg.gold` — exceto as ilhas declaradas
  (PTU) e o próprio log de migração.
- Totais de FOCUS por período/origem **idênticos** ao baseline de §3.1 (tolerância zero em
  linhas; igualdade exata em `realizado`).
- Planning preserva realizado+forecast+rateio depois do `shared_cost` (teste de regressão do
  bug D3: rodar `shared_cost` duas vezes e comparar contagem por `tipo`).
- Cada leitor migrado tem teste que **falha** no estado antigo: apontar para `gold` deve dar
  erro explícito, não resultado vazio silencioso.
- Testes de migração atualizados (`test_dimon_materialization`, `test_forecast_daily_drilldown`)
  e os 2 pré-existentes resolvidos ou explicitamente marcados como dívida.
- `docs/ai/*` atualizado para Bronze → Silver → Gold; `10-finos-intelligence.md` e
  `ai-usage-dimons.md` removidos/reescritos.
- Rollback: metadados em PostgreSQL restaurados por `pg_dump`; como a migração de namespace
  **não apaga dados** (staging + swap), o rollback é trocar o default de volta e reexecutar o
  reprocess — nenhum payload Bronze é perdido. O `reset_derived_layers.py` só pode ser usado
  como rollback depois de o novo baseline estar validado, nunca antes.

### 6.3 Testes que precisam usar caso de falha, não caso feliz

Para P8 (rightsizing) e adiante, o plano exige explicitamente testes onde a regra **falha**:
CPU baixa com RAM insuficiente deve vetar; preço de destino ausente deve dar
`REQUIRES_VALIDATION`; região proibida deve dar `INVALID`; memória sem agente deve dar
`NOT_AVAILABLE`, não estimativa.

### 6.4 Operacional

- Worker **não recarrega código** entre tasks (`docs/ai/10-…:276`): reiniciar worker após
  mudar módulo importado por ele — vale para `iceberg_service`, `dimon_materialization` e
  para todo coletor novo.
- Trino reinicia workers sozinho (5.3): qualquer validação de baseline precisa de retry.

---

## 7. Pendências e próxima ação

### 7.1 Decisões confirmadas pelo usuário

- **Fonte de verdade:** `planDataCollector.md` no workspace, complementado por
  este log. Arquitetura alvo: **Bronze → Silver detalhada → Gold de cubos**. A
  camada Dimon será removida. Telemetria começa com **15 dias**, configurável e
  expansível até 90. Instruções anteriores que contradigam o arquivo são
  ignoradas.
- **Ordem:** P1 antes de P2 e de qualquer coletor novo, em checkpoints
  P1A → P1B → P1C → P1D e gate final de P1. P2 só começa após o fechamento de P1.

### 7.2 Impacto dos achados do P0 sobre P2+

| Achado | Impacto | Quando tratar |
|---|---|---|
| Export Cost Management 4P vazio desde abr/2026 (D8) | **Não bloqueia P1 nem P2.** Bloqueia **recomendação 4P a partir de P3** | P2 marca a fonte como `DEGRADED`/`STALE` quando coletas sucessivas retornam zero e impede recomendação com dado vencido |
| Ambiente TI saudável | Pode ser usado para continuar a validação enquanto o 4P está degradado | P2 em diante |
| Resolução de `credential_profile` (D12/D13/D15) | Fica em **P2**; deve **centralizar** todas as credenciais em `azure_identity` | Nenhuma fonte nova pode duplicar `client_secret` |
| Variante de caixa do Resource ID (§3.3, ~116 mil IDs em 202608) | **Canonicalização case-insensitive é obrigatória em P3**, antes de qualquer join | P3 |

### 7.3 Ações herdadas por checkpoint

- **P1B** — remover Automation e Resource Graph dos mappings do destino FOCUS;
  identificar fonte de custo por metadata/`source_key` (nunca prefixo `azure-`);
  impedir nova associação operacional ao FOCUS; reprocessar os períodos
  afetados; confirmar realizado inalterado e redução de linhas igual às
  não-custo removidas.
- **P1C** — tratar `silver.dest_modelo_focus` como baseline; corrigir todas as
  leituras/escritas que ainda esperam detalhe em `iceberg.gold`; migrar os
  **templates SQL persistidos** (`dimon_rule.sql_template`, ids 11/12/15/16) de
  forma idempotente guardando o SQL anterior; centralizar a resolução de
  namespace. **Recuperar a tabela de planejamento órfã** (§8.5 item 1).
- **P1D** — materializar cubos finais na Gold lendo o detalhe da Silver (custo,
  planning, Automation, órfãos, PTU, IA); atualizar worker/APIs/frontend;
  validar contra os resultados legados; **só depois** remover namespace, models,
  services, routers, endpoints `/dimon`, componentes de frontend e atualizar
  README + `docs/ai`.

### 7.4 Fila de trabalho imediata

1. Aguardar o término do forecast em execução e reiniciar API + worker para
   carregar o código de P1A (§8.7).
2. Reconstruir o planejamento na camada de detalhe — item de P1C, mas
   necessário antes de qualquer `shared_cost` rodar.
3. P1B.

---

## 8. P1A — Conter os bugs ativos

**Entrada:** decisões confirmadas do usuário; P0 fechado em `e4e78b76`.
**Commit do P1A:** **`19a1c287`** — `fix(p1a): preserve realizado/forecast in planning rateio and read detail from Silver`
(push `e4e78b76..19a1c287` em `novo_dev`).
**Escopo:** corrigir a perda de dado do rateio e o caminho de leitura de pivô/análise.
Explicitamente **fora** desta etapa: excluir tabelas, namespace legado ou Dimon.

| # | Item pedido | Situação | Evidência |
|---|---|---|---|
| 1 | Snapshot/backup antes de qualquer escrita | **Feito** | §8.2 |
| 2 | `merge_planning_rateio` lê e escreve na Silver | **Feito** | `iceberg_service.py:1827+` usa `layers.detail_layer()` |
| 3 | Publicação preserva realizado + forecast + rateio | **Feito** | guarda `_type_counts` + 8 testes |
| 4 | Pivô/análise lê detalhe da Silver | **Feito** | `analysis.py` sem `iceberg.gold` no caminho de detalhe |
| 5 | Teste de regressão que falha se um tipo desaparecer | **Feito** | `tests/test_planning_rateio_preservation.py` |
| 6 | Não excluir tabela/namespace legado | **Respeitado** | nenhuma exclusão executada |

### 8.1 Causa-raiz

`_publish_planning_dataframe` gravava em `silver`
(`iceberg_service.py:1793/1795`) enquanto `merge_planning_rateio` **lia** de
`("gold", safe_name)` (`:1831`). Como nada registrado existe em `gold`, a leitura
voltava vazia, `keep` ficava vazio e cada execução de rateio republicava a tabela
de planejamento **somente com `tipo='rateio'`** — apagando realizado e forecast.
O `shared_cost` chama esse merge em toda execução (`shared_cost.py:699`).

Agrava: **não existe tabela de planejamento registrada no catálogo** hoje. O
objeto físico sobrevive em `apps/data/iceberg/gold.db/dest_modelo_focus_planning`
(146 parquets, 79,4 MB, 291 arquivos de metadata), mas `iceberg_tables` não tem
nenhuma linha de planning. Ou seja, o merge também não encontrava nada porque a
tabela havia sido dropada do catálogo em uma execução anterior e a recriação
apontou para outro diretório (`silver/…`). Ver §9 (P1C).

### 8.2 Snapshot feito antes de qualquer escrita

Diretório `backups/azure-finops-p1a/` (artefatos de auditoria, não versionados):

- `metadata_p1a_20260922.sql` — `pg_dump` de `source_connection`,
  `source_destination_mapping`, `destination_table`, `dimon_rule`,
  `forecast_config`, `forecast_run` (500 KB);
- `dimon_rule_sql_templates_20260922.json` — os 4 templates SQL persistidos
  (ids 11, 12, 15, 16) em JSON, para rollback de P1C item 3;
- `planning_orphan_manifest_20260922.json` — inventário dos 146 parquets com
  `sha256` do manifesto (integridade da tabela órfã).

Nenhum arquivo do warehouse foi alterado ou removido nesta etapa.

### 8.3 Mudanças de código

**Novo: `apps/api/app/services/lakehouse_layers.py`** — resolvedor único de
camada. Define `BRONZE/SILVER/GOLD/FORECAST`, marca `dimon` como legado,
normaliza `silver.db` → `silver`, e expõe `detail_layer()` / `cube_layer()`.
É o ponto único de virada da arquitetura: `merge_planning_rateio` e
`_publish_planning_dataframe` passam a resolver a mesma camada por construção,
tornando a divergência impossível sem alterar um único arquivo.

**`app/services/iceberg_service.py`**

- importa `lakehouse_layers as layers`;
- `_publish_planning_dataframe` resolve a camada por `layers.detail_layer()`;
- `merge_planning_rateio` lê da mesma camada, e ganhou duas proteções:
  - **aborta** (em vez de publicar subconjunto) quando a leitura da tabela
    existente falha por motivo que não seja "tabela ausente";
  - **guarda de perda**: se um `tipo` preservado existia antes e some do
    resultado, levanta `RuntimeError` antes de publicar;
  - não republica quando não há rateio novo nem rateio anterior.

**`app/routers/analysis.py`**

- `_describe_gold_schema` → `_describe_layer_schema` e
  `_gold_from` → `_layer_from`, resolvendo `layers.detail_layer()`;
- as 5 chamadas de `_inject_latest_period_filter("gold", …)` passam a usar a
  camada de detalhe (é o `MAX(período)` sobre a tabela do destino);
- staging tables (`list/describe/drop`) e `/analysis/summary` passam a apontar
  para a camada de detalhe;
- `_run_local_gold_query` reconhece o SQL que o próprio endpoint agora monta
  (antes o fallback local nunca casava, porque procurava `iceberg.gold`);
- union de pivot: o lado forecast usa a mesma camada do lado realizado.

`analysis.py` ficou **sem nenhuma referência a `iceberg.gold`** no caminho de
detalhe. Permanecem apenas: a heurística de rótulo `{"realizado","actual","gold"}`
(aceita o nome da camada como sinônimo de "valor realizado" no gráfico) e a lista
de namespaces do endpoint **Dimon** (`:2306`), que só sai em P1D.

### 8.4 Testes

Novo arquivo `apps/api/tests/test_planning_rateio_preservation.py` — 8 testes:

| Teste | O que trava |
|---|---|
| `test_merge_reads_from_the_detail_layer` | a leitura tem de ocorrer na camada em que se publica |
| `test_merge_preserves_realizado_forecast_and_rateio` | os três tipos sobrevivem; rateio antigo é substituído, não somado |
| `test_merge_aborts_when_it_cannot_read_existing_rows` | falha de leitura não vira publicação parcial |
| `test_merge_guard_blocks_dropping_a_preserved_type` | guarda explícita dispara antes de publicar |
| `test_merge_creates_table_when_absent` | tabela ausente com rateio novo cria do zero |
| `test_nothing_to_merge_returns_zero` | sem rateio não republica |
| `test_empty_rateio_clears_previous_rateio_rows` | rateio vazio remove rateio anterior |
| `test_layer_resolver_declares_silver_as_detail` | trava a arquitetura Silver= detalhe, Gold= cubo |

**Os testes foram validados contra o bug**: reintroduzindo `namespace = "gold"` e
removendo a guarda em `merge_planning_rateio`, **5 dos 8 falham** — a suíte
realmente detecta a regressão em vez de passar por acidente.

**Prova de integração no Iceberg real** (tabela de teste `p1a_selftest_planning`,
criada e dropada no mesmo script): publicar base com realizado+forecast+rateio,
rodar o merge com rateio novo → resultado `{realizado: 1, forecast: 1, rateio: 1}`
com o rateio novo no valor correto. Antes da correção esse caminho produzia
`{rateio: 1}`.

**Suíte completa:** `5 failed, 337 passed` (era `5 failed, 329 passed`).
O **conjunto de falhas é idêntico** ao baseline — nenhuma falha nova:

| Falha | Natureza | Tratamento |
|---|---|---|
| `test_dimon_materialization.py::test_merged_dimensions_uses_analytic_default` | migração P1 | corrigir em P1D |
| `test_dimon_materialization.py::test_preview_source_queries_gold` | migração P1 | corrigir em P1D (o SQL agora é `iceberg.silver.*`) |
| `test_forecast_daily_drilldown.py::test_daily_drilldown_uses_original_gold_date_not_monthly_dimon` | migração P1 | corrigir em P1D |
| `test_auth_unit.py::test_dev_defaults_safe` | **pré-existente** | documentada; `auth_enabled=False` no ambiente |
| `test_azure_ptu_finops.py::test_license_pool_default_target_type` | **pré-existente** | documentada; importa `app.routers.ai_cost_origin`, que não existe |

### 8.5 Pendências que P1A expôs (para P1C)

1. **Recuperar a tabela de planejamento órfã.** O objeto físico existe
   (`gold.db/dest_modelo_focus_planning`, 146 parquets) mas não está registrado.
   Enquanto isso, o primeiro merge ou o próximo forecast a recria do zero,
   começando sem o par realizado+forecast. P1C deve registrar/republicar essa
   tabela na camada de detalhe antes de qualquer rateio rodar — usando o
   snapshot imutável de `forecast.result_run_*` como fonte, e conferindo contra o
   manifesto de integridade de §8.2.
2. **Rateio não deve rodar antes disso.** `shared_cost` chama o merge direto;
   com a guarda nova ele não destrói nada que consiga ler, mas se a tabela não
   existe ele a cria só com rateio. O `admin` deve evitar rodar `shared_cost`
   até o item 1 ser concluído.
3. **Worker e API em memória.** Os processos em execução (API :8001, 2 workers)
   carregaram os módulos antigos. Não havia task RUNNING/QUEUED no momento da
   mudança, mas o código novo só passa a valer após reiniciar API e worker —
   reinício a fazer no fechamento deste checkpoint, quando não houver fila.

### 8.6 Rollback de P1A

- Código: `git revert` do commit deste checkpoint (mudanças isoladas em
  `lakehouse_layers.py`, `iceberg_service.py`, `analysis.py` e o teste novo).
- Dados: **nenhuma escrita em dado real foi feita em P1A**. A tabela de teste da
  prova de integração foi criada e dropada no mesmo script (o diretório residual
  `silver.db/p1a_selftest_planning` foi removido manualmente depois); a tabela
  órfã de planejamento não foi tocada (manifesto com `sha256` em §8.2).
- Configuração: `dimon_rule` e `forecast_*` não foram alterados; o `pg_dump`
  está em `backups/azure-finops-p1a/`.

### 8.7 Estado operacional no fechamento do P1A

- Havia um `forecast` (task 5702 / run 259, `trigger_type=MANUAL`) **RUNNING** na
  fase "Agregando histórico no Trino" durante o P1A. Ele roda no processo do
  worker, que mantém os módulos antigos em memória: **não foi reiniciado** para
  não matar a tarefa. Nenhum `shared_cost` estava enfileirado.
- Para a correção passar a valer em produção é preciso reiniciar API e worker
  (o worker não recarrega código entre tasks). Fazer quando a fila estiver
  vazia; o próximo agendamento é 2026-09-23 03:00.
- Consequência a observar: como a tabela de planejamento não está registrada, se
  um `shared_cost` rodar **antes** de reiniciar o worker, ele usará o código
  antigo e criará a tabela apenas com `tipo='rateio'`. Depois do reinício, a
  ordem correta é: rodar o forecast (publica planejamento com
  realizado+forecast) e só então o `shared_cost` (adiciona rateio preservando os
  outros dois tipos).

### 8.8 O bug se reproduziu ao vivo durante o P1A (evidência definitiva)

O forecast que estava em execução terminou **com o código antigo** e o cenário
descrito em §8.7 aconteceu de fato, documentado pela própria linha `etl_task`:

| Evidência | Valor |
|---|---|
| `etl_task.id` | 5702 (`forecast`, COMPLETED) |
| `rows_destination` | **129.631** (o que o forecast publicou no planejamento) |
| `phase` final | `Forecast publicado · 3751 linhas de rateio · Dimon enfileirada (task #5703)` |
| Conteúdo real de `silver.dest_modelo_focus_planning` | **3.751 linhas, todas `tipo='rateio'`** |

O caminho é direto no worker: ao terminar o forecast ele chama o rateio **no
mesmo processo** (`worker.py:505`, `run_shared_cost_job_sync`), sem passar por uma
task própria. Com o código antigo, esse merge leu `gold` (vazio), não encontrou
nada para preservar e republicou o planejamento somente com rateio — descartando
os 129.631 registros de realizado+forecast que o próprio forecast tinha acabado
de publicar em `silver`.

Isso confirma que o defeito não era teórico: ele dispara em **toda** execução de
forecast, e é exatamente o que a correção de P1A impede (a guarda `_type_counts`
levanta erro antes de publicar um subconjunto).

**Estado herdado por P1C:** o planejamento atual está reduzido a rateio. Precisa
ser reconstruído rodando o forecast com o código corrigido (o merge passará a
preservar realizado+forecast) e conferindo contra `forecast.result_run_*`, que é
snapshot imutável e não foi afetado.





```sql
-- Baseline FOCUS por período e origem (Trino, schema iceberg.silver)
SELECT _billing_period, source_connection_id, count(*) AS n,
       round(sum(try_cast(realizado AS double)), 2) AS realizado
FROM iceberg.silver.dest_modelo_focus
WHERE _billing_period IN ('202605','202606','202607','202608')
GROUP BY 1, 2 ORDER BY 1, 2;

-- Baseline de VM e disco (202608)
SELECT servicesubcategory, count(*) AS n,
       round(sum(try_cast(realizado AS double)), 2) AS realizado
FROM iceberg.silver.dest_modelo_focus
WHERE _billing_period = '202608'
  AND (lower(CAST(servicesubcategory AS VARCHAR)) LIKE '%virtual machines%'
    OR lower(CAST(servicesubcategory AS VARCHAR)) LIKE '%managed disks%')
GROUP BY 1 ORDER BY 3 DESC;

-- Contaminação do FOCUS por fontes não-custo
SELECT _billing_period, source_connection_id, count(*) AS n,
       count(realizado) AS com_realizado
FROM iceberg.silver.dest_modelo_focus
WHERE _billing_period IN ('202607','202608') AND source_connection_id IN (27,30,34,35)
GROUP BY 1, 2 ORDER BY 1, 2;
```

```sql
-- Metadata (PostgreSQL 5433/finops)
SELECT id, code, connector_type, cloud_scope, last_test_status,
       config_json->>'credential_profile' AS profile
FROM source_connection WHERE cloud_scope = 'azure' ORDER BY id;

SELECT id, code, active, materialization_table, materialization_status,
       (sql_template IS NOT NULL) AS custom_sql
FROM dimon_rule ORDER BY id;

SELECT catalog_name, table_namespace, count(*) FROM iceberg_tables GROUP BY 1, 2;
```

```bash
# Baseline de testes
cd apps/api && ../../.venv/bin/python -m pytest tests/ -q --no-header
# → 5 failed, 329 passed (P0)
# → 10 failed, 351 passed após P1B (conjunto de falhas inalterado)
```

---

## 9. P1B — Limpar a contaminação do FOCUS

**Commit:** **`f73639e3`** — `fix(p1b): keep operational sources out of the FOCUS ledger`
(push `726f7a68..f73639e3` em `novo_dev`). O commit `23ceaf6b` do usuário veio
antes e permanece como commit local dele; não o empurrei.

### 9.1 Medição antes

`silver.dest_modelo_focus`: **36.457.509 linhas**, `realizado` total
**R$ 161.075.910,97**, das quais **22.747 linhas** com `realizado` nulo e vindas
de fontes operacionais:

| id | code | linhas | com `realizado` | períodos |
|---|---|---|---|---|
| 27 | `azure-automation-4p` | 4.718 | 0 | 202607–202609 |
| 30 | `azure-automation-ti` | 5.614 | 0 | 202607–202609 |
| 34 | `azure-resource-graph-4p` | 11.760 | 0 | 202608–202609 |
| 35 | `azure-resource-graph-ti` | 655 | 0 | 202608–202609 |
| | **total** | **22.747** | **0** | |

Havia ainda um quinto caso sem linhas publicadas: `azure-4p-ptu` (id 23) com
**11 mappings ativos** para o destino FOCUS.

### 9.2 O que foi feito

| Item pedido | Como |
|---|---|
| Remover Automation e Resource Graph dos mappings do FOCUS | `DELETE` de **44 mappings** (11 de cada). Os mappings legítimos para os destinos 36/38/40/41 foram preservados; as 11 do PTU também saíram |
| Identificação explícita de fonte de custo, sem prefixo `azure-` | `services/cost_sources.py` + marcador `cost_source`/`source_key` em `config_json` |
| Impedir nova associação operacional ao FOCUS | Guarda dupla: `POST /destinations/mappings/` devolve 422; o reprocess ignora a fonte mesmo com mapping legado |
| Reprocessar os períodos afetados | 202607–202609 |
| Confirmar os critérios | §9.3 |

`scripts/mark_cost_sources.py` (idempotente; rodar duas vezes não altera nada)
marcou **10 conexões de custo** com `source_key="cost_exports"` e
`cost_source=true` (4 Azure de custo, 3 GCP, 2 Atlas, IBM, OCI) e **5
operacionais** com `cost_source=false` (`azure-automation-4p/ti`,
`azure-resource-graph-4p/ti`, `azure-4p-ptu`). Nenhum `code`, Bronze, Silver,
Gold ou histórico foi renomeado. `pg_dump` das duas tabelas de metadata em
`backups/azure-finops-p1a/metadata_p1b_pre_20260922.sql`.

### 9.3 Aceite verificado

`scripts/measure_focus_contamination.py --verify` contra o baseline:

| Critério | Resultado |
|---|---|
| Soma de `realizado` inalterada | **OK** — R$ 161.075.910,97 (idêntico) |
| Redução de linhas = linhas não-custo do baseline | **OK** — removidas exatamente 22.747 |
| Nenhuma linha Automation/ARG no FOCUS | **OK** — zero linhas de origem não-custo |

Teste da guarda em produção: `POST /etl/reprocess` com as 4 fontes operacionais
para o destino FOCUS devolveu **422** ("Nenhum blob Bronze encontrado…") — o
reprocess não aceitou nenhuma delas.

### 9.4 Reprocessamento de 202609

Feito **somente com as fontes de custo**, para provar que o mês fecha sem as
operacionais:

| Momento | Linhas | `realizado` |
|---|---|---|
| 202609 já sem as linhas não-custo | 2.338.021 | R$ 9.002.518,01 |
| 202609 antes de qualquer remoção (referência) | 2.349.446 | R$ 9.002.518,01 |

A diferença de 11.425 linhas é exatamente a soma das linhas não-custo de setembro
(3.609 + 4.872 + 2.552 + 392) e o `realizado` não mudou.

> A cadeia `5743..5752` (10 tasks) ainda estava processando no fechamento deste
> registro, sem erro. O resultado financeiro do mês já está correto conforme a
> tabela acima.

### 9.5 Achado colateral: as falhas de teste vieram do commit `23ceaf6b`

Investigado com worktrees isolados, porque a contagem de falhas subiu de 5 para 10
sem que P1A/P1B tocassem nesses módulos:

| Revisão | Resultado |
|---|---|
| `726f7a68` (P1A, antes do commit do usuário) | 1 failed / 341 passed |
| `23ceaf6b` (commit do usuário) | 9 failed / 333 passed |
| após P1B | 10 failed / 351 passed |

**Oito falhas novas entraram em `23ceaf6b`** — `test_aura_brain_vivo_mais` (2),
`test_datamesh_dashboard` (1), `test_sinfonia_dashboard` (2),
`test_dimon_materialization` (2), `test_forecast_daily_drilldown` (1). As
restantes são ambientais: `test_dev_defaults_safe` depende de `.env` não
versionado e `test_license_pool_default_target_type` importa
`app.routers.ai_cost_origin`, que não existe.
**P1A e P1B não introduziram falha nova**: o conjunto de falhas é idêntico ao de
antes de cada checkpoint. As três asserções que ainda esperam `iceberg.gold` são
corrigidas em P1C.

### 9.6 Itens que P1B deixa para P1C

1. Corrigir as asserções de teste que ainda esperam `iceberg.gold`
   (`test_dimon_materialization`, `test_forecast_daily_drilldown`).
2. Investigar as 8 falhas herdadas de `23ceaf6b` (dashboards Sinfonia/DataMesh/
   Aura) — não são desta iniciativa, mas bloqueiam o gate "nenhuma falha nova"
   do P1 como um todo.
3. Migrar os templates SQL persistidos (`dimon_rule.sql_template` ids 11/12/15/16)
   e concluir o cutover das leituras restantes.


---

## 10. P1C — Cutover de leitura e namespaces

**Commit:** `34bbda92` — `fix(p1c): point remaining reads to the correct layer and centralize namespaces`
(push `29a56d9f..4f3d627f`).

### 10.1 Bug de leitura/escrita encontrado

`dimon_materialization.DIMON_NAMESPACE = "gold"` (o materializador **escreve** os
marts na Gold) enquanto `dimon.py` **lia** de `iceberg.dimon`. Os marts iam para
um namespace e ninguém os lia — dashboards vazios. `dimon.py` passa a resolver
por `DIMON_NAMESPACE`, e o mesmo foi feito nas constantes de IA e nos dashboards
de projeto (sinfonia, datamesh, wisper, copilot).

### 10.2 O que foi corrigido

| Grupo | Tratamento |
|---|---|
| Detalhe (FOCUS, órfãos, inventário, planejamento, savings, IA de origem, métricas OCI) | resolvido por `lakehouse_layers.detail_layer()` em 12 arquivos |
| Marts Dimon (consumo/licenças IA, copilot, sinfonia, datamesh, wisper) | resolvido por `DIMON_NAMESPACE` |
| Ilha PTU (`dest_azure_ptu_*`) | **preservada de propósito**: escreve e lê na Gold de forma consistente |
| Templates SQL persistidos | **auditados, não reescritos** (§10.3) |
| Asserções de teste que fixavam `iceberg.gold` | passam a usar o resolvedor |

### 10.3 Templates SQL persistidos: auditoria em vez de substituição cega

`scripts/migrate_persisted_sql_templates.py` verifica cada
`iceberg.<namespace>.<tabela>` dos templates contra o catálogo Iceberg, aponta
referências divergentes e salva o SQL atual para rollback (`--dump`).

Resultado: **0 divergências**.

| Template | Referências | Situação |
|---|---|---|
| 11 `saving_desligue_vm` | `iceberg.gold._tmp_vm_monthly` | **correto** — tabela auxiliar criada pelo materializador no namespace dele |
| 12 `snapshot_tags_automacao` | `iceberg.silver.dest_modelo_focus` | correto |
| 15 `custo_recursos_orfaos` | `iceberg.silver.dest_cloud_orphan_resources`, `iceberg.silver.dest_modelo_focus` | correto |
| 16 `consumo_diario_vm` | `iceberg.silver.dest_modelo_focus` | correto |

Não havia reescrita a fazer. Isto é o oposto de substituição textual: uma troca
cega de `gold`→`silver` teria quebrado o template 11, cuja tabela realmente vive
na Gold.

### 10.4 Resultado

Suíte: **8 failed, 353 passed** (antes do P1C: 10 failed, 351). As duas falhas de
migração que existiam (`test_dimon_materialization::test_preview_source_queries_gold`
e `test_forecast_daily_drilldown`) foram corrigidas; as 8 restantes são as
herdadas do commit `23ceaf6b`, não desta iniciativa.

---

## 11. P5 (antecipado) — Fonte `monitor_vm_metrics` e carga de 15 dias

**Commit:** `4f3d627f` — `feat(p5): add the Azure Monitor VM metrics source with a 15-day pilot`.

O P5 é a etapa do plano que cria a carga de 15 dias. Foi antecipado a pedido do
usuário, depois de P1A/P1B/P1C, porque é uma fonte **nova com Bronze própria** e
não depende do cutover de leitura.

### 11.1 Capacidade verificada no tenant

| Verificação | Resultado |
|---|---|
| VMs visíveis (4P) | **61** em 2 regiões (`brazilsouth`, `eastus`) e 12 subscriptions |
| API de métricas (batch) | `HTTP 200` |
| Clusters AKS visíveis | **46** (tiers Standard e Free) — insumo para P4 |
| Memória guest | indisponível: exige Azure Monitor Agent/DCR; a API batch não atende guest metrics |

### 11.2 Contrato da fonte

`source_key = monitor_vm_metrics`; `code = azure-4p-monitor-vm` (id 37); Bronze
exclusiva `bronze.raw_azure_4p_monitor_vm`; sem tabela compartilhada com custo,
inventário ou AKS. Detalhes de API, batch, limites, checkpoint e idempotência no
docstring de `app/services/azure_monitor_vm_service.py`.

### 11.3 Carga real de 15 dias

| Métrica | Valor |
|---|---|
| VMs | 61 |
| Métricas de plataforma | 7 (CPU, rede in/out, IOPS e bytes de disco r/w) |
| Linhas na Bronze | **615.002** (614.880 série + 122 ausência) |
| Cobertura | **1.440 buckets por métrica** = 15 dias × 96 intervalos de 15 min |
| Janela observada | `2026-09-07T00:00Z` → `2026-09-21T23:45Z` |
| Requisições | 195 (batch de até 50 recursos) |
| Duração | 164,8 s |
| 429 / falhas | 0 / 0 |
| Qualidade | `SERIES/OK` 614.880 · `GAP/NOT_AVAILABLE` 122 (memória guest) |

### 11.4 Idempotência (critério de aceite do P5)

Reexecutar a mesma janela **não duplica**. Duas execuções após a correção da
âncora: mesmo total (615.002), **13 fatias** (não 26), caminho estável
`monitor/<sub>/<região>/20260922T0000/15d/metrics.csv`.

### 11.5 Dois defeitos encontrados e corrigidos durante a carga

1. **Deadlock em `etl_task`.** O callback de progresso chamava `session.commit()`
   a cada fatia, disputando lock com o próprio worker. Progresso agora é
   atualizado em memória e persistido pelo worker ao final, como Automation e ARG.
2. **Duplicação por âncora instável.** A chave da fatia incluía `now()`, então
   duas execuções da mesma janela geravam `_blob_path` diferente e a coleta
   **acumulava** em vez de substituir (a primeira carga rendeu 1.230.004 linhas
   para 615.002 reportadas). Corrigido com `anchor_until` (dia cheio por padrão,
   hora cheia opcional para coleta incremental).

### 11.6 Estado e pendências

- A Bronze está coletada e idempotente; a **Silver temporal** da fonte ainda não
  existe (transformação Bronze → Silver é o próximo passo do P5).
- A conexão está marcada `cost_source = false`: métrica não é custo e não pode
  alimentar o FOCUS.
- `worker.py` ganhou branch explícito para o tipo novo. Sem ele o
  `azure_monitor_vm_metrics` cairia no `else` do `task_runner` (extrator GCP) —
  o risco D14 do §1.2, agora fechado para este tipo.
