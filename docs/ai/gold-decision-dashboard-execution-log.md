# Log de execução — Gold orientada a decisão e novos dashboards FinOps

Plano de referência: `planGoldDecisionDashboards.md` (raiz do repositório).
Fonte de verdade, em ordem: (1) tabelas Iceberg e registros PostgreSQL do ambiente
em execução; (2) contratos em `apps/api/app/services/silver_models.py`;
(3) serviços que leem/escrevem esses dados; (4) testes ativos; (5) o plano;
(6) documentação anterior.

Este arquivo é cumulativo: cada pacote (G0…G10) acrescenta uma seção.

---

# G0 — Auditoria reproduzível e dicionário de métricas

**Status:** concluído. **Nenhuma alteração funcional foi feita neste pacote.**
Nenhum arquivo de código, SQL, migração ou dado local foi modificado.

---

## G0.0 Regra de execução do pacote

```text
entrada     : planGoldDecisionDashboards.md (seções 3, 4, 5, 6), ambiente local em execução
premissas   : Trino local (http://localhost:8080), PostgreSQL local (localhost:5433/finops),
              API uvicorn (porta 8000), warehouse Iceberg em apps/data/iceberg
arquivos    : docs/ai/gold-decision-dashboard-execution-log.md (este arquivo, novo)
saida       : baseline reproduzido, inventário de Gold, dicionário de métricas,
              matriz C1–C12, desenho da classificação de competência,
              mapa widget -> endpoint -> cubo, arquivos previstos para G1
testes      : consultas de contagem/reconciliação somente leitura, reproduzidas abaixo
risco       : nenhum (G0 é somente leitura). Risco de migração está em G0.7.
rollback    : remover este arquivo. Nada mais foi tocado.
```

### Comandos de reprodução (sanitizados)

Nenhum comando abaixo contém segredo, token ou credencial. As senhas locais de
desenvolvimento foram omitidas.

```bash
# 1. Catálogos Trino
.venv/bin/python scripts/trino_query.py "SHOW SCHEMAS FROM iceberg"
.venv/bin/python scripts/trino_query.py "SHOW TABLES FROM iceberg.silver"
.venv/bin/python scripts/trino_query.py "SHOW TABLES FROM iceberg.gold"

# 2. Contagens (COUNT(*) real, 0,1–0,2 s cada)
.venv/bin/python scripts/trino_query.py "SELECT COUNT(*) AS c FROM iceberg.silver.resource_cost"

# 3. Registros de governança Gold (PostgreSQL)
psql -h localhost -p 5433 -U finops -d finops -c \
  "SELECT id, code, name, active, materialization_table, materialization_status, materialization_rows, materialized_at, snapshot_mode FROM gold_rule ORDER BY id;"
psql -h localhost -p 5433 -U finops -d finops -c \
  "SELECT r.id, r.code, s.gold_table, s.gold_tables, s.value_column, s.percent, s.filter_json FROM gold_rule r JOIN gold_rule_source s ON s.rule_id=r.id ORDER BY r.id, s.id;"

# 4. Baseline financeiro por conexão e competência
#    (ver consulta em G0.1.2)
```

Coleta feita em **23/09/2026**, entre 17h e 18h (horário local), no ambiente
local de referência. Serviços ativos: `openfinops-trino-1` + 2 workers (healthy),
PostgreSQL local em 5433, API uvicorn em 8000, Vite web em 3080.

---

## G0.1 Baseline reproduzido

### G0.1.1 Contagens Silver — confirmadas

`COUNT(*)` real no Trino, não estatística de catálogo.

| Modelo Silver | Linhas medidas | Linhas no plano | Situação |
|---|---:|---:|---|
| `resource_cost` | 13.519.512 | 13.519.512 | confere |
| `resource_metric_series` | 3.080.297 | 3.080.297 | confere |
| `resource_inventory` | 39.343 | 39.343 | confere |
| `price_catalog` | 3.946.787 | 3.946.787 | confere |
| `compute_sku_catalog` | 1.523.324 | 1.523.324 | confere |
| `resource_quota` | 23.200 | 23.200 | confere |
| `region_policy` | 5.527 | 5.527 | confere |
| `resource_allocation` | 242 | 242 | confere |
| `container_metric_series` | 9.177 | 9.177 | confere |
| `ptu_capacity_series` | 6.660 | 6.660 | confere |
| `resource_profile_window` | 1.746 | 1.746 | confere |
| `rightsizing_candidate` | 193 | 193 | confere |
| `rightsizing_decision` | 127 | 127 | confere |

Recursos distintos em `resource_cost`: **968.339** (`COUNT(DISTINCT resource_id)`
= `COUNT(DISTINCT lower(trim(resource_id)))` = 968.339, medido em 7,6 s).
Recursos distintos em `resource_inventory`: **20.160** (39.343 linhas em 2
snapshots: 19.546 em 22/09 e 19.797 em 23/09).

### G0.1.2 Custo por conexão e competência — confirmado, com correção de leitura

| conexão | linhas | `SUM(effective_cost)` | competências | recursos distintos |
|---|---:|---:|---:|---:|
| `azure-4p` | 12.970.906 | 35.368.613,01 | 16 (202504–202607) | 825.512 |
| `azure-ti` | 548.606 | 1.987.748,46 | 1 (202609) | 146.271 |
| **total** | **13.519.512** | **37.356.361,47** | 17 competências | 968.339 |

Detalhe mensal do `azure-4p` (linhas medidas):

| competência | linhas | custo efetivo | dias distintos | regiões | serviços |
|---|---:|---:|---:|---:|---:|
| 202504 | 3.763.016 | 209.564,67 | 1 | 10 | 42 |
| 202505 | 2.261.106 | 3.177.030,65 | 10 | 10 | 45 |
| 202506 | 656.168 | 2.881.673,31 | — | 15 | 47 |
| 202507 | 717.112 | 2.986.495,25 | — | 15 | 48 |
| 202508 | 684.053 | 2.912.511,00 | 11 | 15 | 47 |
| 202509 | 434.954 | 2.055.609,04 | — | 15 | 47 |
| 202510 | 678.106 | 2.882.348,17 | — | 15 | 48 |
| 202511 | 785.346 | 3.507.991,37 | — | 15 | 47 |
| 202512 | 823.720 | 3.718.891,12 | — | 15 | 48 |
| 202601 | 790.406 | 3.827.009,90 | — | 15 | 48 |
| 202602 | 693.389 | 3.455.091,64 | 12 | 11 | 48 |
| 202603 | 683.474 | 3.265.244,23 | 15 | 10 | 47 |
| 202604 | **51** | 194.941,66 | — | **0** | **1** |
| 202605 | **1** | 146.346,00 | — | **0** | **1** |
| 202606 | **1** | 147.865,00 | — | **0** | **1** |
| 202607 | **3** | 0,00 | — | **0** | **1** |

`azure-ti`: 202609, 548.606 linhas, R$ 1.987.748,46, 12 regiões, 78 serviços,
todas as linhas com `charge_period_start` em 2026-09-01.

### G0.1.3 `focus_enriched` — confirmado inexistente

`iceberg.gold.focus_enriched` não existe. O modelo está **declarado** em
`apps/api/app/services/silver_models.py:340-401` e **não tem nenhum consumidor**:

```bash
grep -rn "focus_enriched" apps/api apps/web --include=*.py --include=*.ts --include=*.tsx
# silver_models.py:341,343  (declaração)
# focus_enrichment.py:35,142 (construtor isolado, sem chamador)
```

`focus_enrichment.py` não é importado por nenhum router, worker ou serviço
(`grep "focus_enrichment"` fora do próprio arquivo: zero resultados). É código
morto com declaração pública. **Não dependemos dele. Remover a declaração em G1.**

### G0.1.4 AKS e PTU — confirmados, com ressalvas de vocabulário

- `container_metric_series`: 9.177 linhas. **46 clusters avaliados** = 41
  `UNAVAILABLE` (1 linha cada, marcador de indisponibilidade) + 5 clusters com
  linha `AVAILABLE` (1 linha de resumo cada). As linhas de *namespace* têm
  `coverage_status = NULL` em 4 clusters: 1.972 + 1.932 + 234 + 15 = **4.153
  linhas de namespace em 4 clusters**. 41 clusters sem Container Insights.
  Ressalva: `dev-azure-proxy` aparece com `coverage_status = AVAILABLE` e
  `namespace = 0` — é um cluster disponível mas sem namespace materializado.
- `ptu_capacity_series`: **2 recursos, 4 deployments**, 1.665 linhas cada
  (1.663 horas distintas), janela **2026-05-23 20:00 → 2026-08-24 19:00**.
  Completude média 99,87%. Utilização média **1,80 / 0,24 / 7,62 / 5,11 %**
  por deployment (média simples dos 4 = **3,69 %**, igual ao plano);
  p95 médio **5,49 / 0,60 / 19,75 / 7,92 %** (média = **8,44 %**, igual ao plano).
  **Divergência de leitura:** `utilization_max` chega a **1213 %** e **226 %**
  em alguns deployments. Ou a métrica não é percentual no pico, ou há
  estouro de capacidade real. Não tratar `max` como percentual antes de G6.
- `resource_allocation`: 242 linhas, `allocation_level = 'node_pool'`,
  `allocation_status = 'PARTIAL'`, `weight_validated = 'SIM'`, 46 pais
  distintos, 99 chaves distintas. **`namespace` é NULL em 242/242 linhas.**
  A coluna existe no contrato e está sempre vazia.
  Soma dos pesos = 46,0 — ou seja **1,0 por pai**, não 1,0 global.

### G0.1.5 Saving — confirmado

| veredito | linhas | saving mensal | resources distintos | `price_comparable` |
|---|---:|---:|---:|---:|
| `VALID` | 23 | 6.050,24 | 23 | 0 (todos NULL) |
| `REQUIRES_VALIDATION` | 104 | 0,00 | 104 | 0 |
| `INVALID` | 66 | −299,37 | 66 | 0 |

- `rightsizing_candidate`: 23 VALID / 104 REQUIRES_VALIDATION / 66 INVALID.
- `rightsizing_decision`: 127 linhas, **todas com `review_status = 'PENDING'`**,
  `evidence_hash` distinto por linha (23 + 104), uma linha por recurso.
  R$ 6.050,24 está inteiramente em `VALID`/`PENDING`.
- **Divergência material:** o PostgreSQL `rightsizing_review` tem 5 registros
  (**2 `APPROVED`, 3 `REJECTED`**) e a Silver **não reflete nenhum deles**
  (`review_status` é `PENDING` em 127/127). Ou seja, o estado real de revisão
  vive no PostgreSQL e a Silver publica um estado defasado. Isso muda o
  contrato de C5 (ver G0.3 e G0.5), não apenas o baseline.
- `price_comparable` é NULL em 127/127, apesar de `silver_models.py` declará-lo
  como coluna do grão. O veredito já embute a comparabilidade; a coluna não.
- `saving_action` (workflow de medição): **0 linhas**. `saving_measurement`: 0.
  Portanto saving aprovado e realizado são **ausentes**, não zero.

### G0.1.6 Divergências encontradas em relação ao plano

Nenhuma muda o grão dos cubos C1–C12. Todas mudam a *leitura do baseline* ou
o *contrato de campos* de um cubo.

| # | Divergência | Impacto | Muda contrato? |
|---|---|---|---|
| D1 | `billed_cost` é **NULL em 13.519.512/13.519.512 linhas**; `contracted_cost` também. `list_cost` == `effective_cost` linha a linha | `custo faturado` e `desconto contratado` (seção 4.3) são **não mensuráveis** hoje. C1 deve publicá-los como `NULL` + motivo, nunca como 0 | Sim, campo de C1/C10 |
| D2 | `resource_cost` tem `billing_period` **e** `_billing_period`; 0 divergências entre eles | Confirmar qual é canônico antes de indexar/particionar C1 | Não (só baseline) |
| D3 | 202504 do 4P: quase todo o custo é ~0, com **1 único dia** (2025-04-01) e 3,76 M linhas sem serviço/região | É carga de ajuste, não competência. Nunca usar como base MoM | Não |
| D4 | 202505 do 4P: 1,58 M linhas sem região, R$ 557.990,51 de custo sem região | Custo sem região é relevante: 17,6 % do mês | Não |
| D5 | As competências 202604–202607 do 4P são **linhas de compra/ajuste** (`Microsoft.Capacity`, 1–51 linhas, R$ 0 a 195 mil), com `region_id` NULL | Não são meses operacionais. `PARTIAL` obrigatório | Não |
| D6 | Custo sem serviço é **constante R$ 5.857,19/mês** de 202505 a 202603, em ~11 linhas de `Purchase` (SaaS/marketplace: Zenrows, SendGrid) | Não é erro de agregação; é **custo recorrente sem classificação de serviço**. Vira regra de qualidade C11 | Não |
| D7 | Custo sem tag: **R$ 500–644 mil/mês** no 4P (≈17–22 % do mês) e R$ 175.567,13 no TI | Bucket material de custo não alocado. C1/C11 devem expor | Não |
| D8 | Região NULL concentra `Microsoft.Capacity` (R$ 443–602 mil/mês) | Reserva é global por natureza. `region_key` precisa distinguir `NULL` de `global` e o cubo precisa de um valor `GLOBAL` explícito | Sim, valor de dimensão |
| D9 | `gold_rule.materialization_status` = `ERROR` em 5 regras, mas 5 delas têm tabela **materializada** em `iceberg.dimon`, não em `iceberg.gold` | O registro de governança aponta para `materialization_table` que não existe no namespace declarado | Sim, G2 |
| D10 | `_tmp_vm_monthly` existe em **dois** namespaces: `gold` (66.570 linhas) e `dimon` (0 linhas) | Contrato público duplicado; `dimon` é camada legada | Sim, G2 |
| D11 | `rightsizing_review` (PostgreSQL) tem 2 `APPROVED` + 3 `REJECTED`; Silver publica 127 `PENDING` | C5 precisa sobrepor o estado do PostgreSQL; senão o funil mente | Sim, C5/C6 |
| D12 | `utilization_max` de PTU chega a 1213 % | Limiares de C9 não podem usar `max` como percentual sem validação | Sim, C9 |
| D13 | Fallback de leitura: plano cita o "último `YYYYMM`". O último do 4P é 202607 (R$ 0,00, 3 linhas); do TI é 202609 (mês em andamento) | Reforça a seção 4.2: o último `YYYYMM` não é o último mês comparável | Não |
| D14 | `dest_modelo_focus` existe como tabela Silver legada com **36.434.762 linhas** (vs 13,5 M de `resource_cost`) e é a fonte dos cubos Gold legados | Dois livros de custo convivem. C1–C3 devem usar `resource_cost`; a divergência precisa ser medida em C11 | Sim, C11 (regra nova) |

---

## G0.2 Inventário de governança Gold, tabelas e consumidores

### G0.2.1 `gold_rule` — 21 registros

Todos os 21 registros estão `active = true`. A coluna `sql_template` existe; as
regras abaixo foram materializadas pelo caminho do materializador governado,
exceto onde indicado.

| id | code | tabela materializada | status | linhas | última execução |
|---:|---|---|---|---:|---|
| 3 | `copilot-custo` | `dest_dm_cost_copilot_custo` | UPDATED | 72 | 2026-09-22 17:54 |
| 4 | `visao-completa` | `dest_dm_cost_visao_completa` | UPDATED | 303.748 | 2026-09-22 17:53 |
| 7 | `custo_aura_brain_vivo_mais` | `dest_dm_cost_custo_aura_brain_vivo_mais` | UPDATED | 5.426 | 2026-09-22 17:52 |
| 8 | `custo_notebooklm` | `dest_dm_cost_custo_notebooklm` | UPDATED | 20 | 2026-09-22 17:53 |
| 9 | `projeto_sinfonia` | `dest_dm_cost_projeto_sinfonia` | UPDATED | 2.487 | 2026-09-22 17:53 |
| 10 | `projeto_datamesh` | `dest_dm_cost_projeto_datamesh` | UPDATED | 9.319 | 2026-09-22 17:54 |
| 11 | `saving_desligue_vm` | `dest_dm_cost_saving_desligue_vm` | UPDATED | 57.405 | 2026-09-22 17:54 |
| 12 | `snapshot_tags_automacao` | `dest_dm_cost_snapshot_tags_automacao` | UPDATED (inativo) | 164.670 | 2026-08-28 08:38 |
| 13 | `custo_wisper` | `dest_dm_cost_custo_wisper` | UPDATED | 869 | 2026-09-22 17:54 |
| 14 | `custo_vivo_mais` | `dest_dm_cost_custo_vivo_mais` | UPDATED | 3.884 | 2026-09-22 17:54 |
| 15 | `custo_recursos_orfaos` | `dest_dm_cost_custo_recursos_orfaos` | **ERROR** | 2.944 | 2026-09-22 17:54 |
| 16 | `consumo_diario_vm` | `dest_dm_cost_consumo_diario_vm` | UPDATED | 146.618 | 2026-09-22 17:54 |
| 17 | `ai_consumption_daily` | `dest_dm_ai_consumption_daily` | **ERROR** (tabela vive em `dimon`) | 851.768 | 2026-09-23 05:10 |
| 18 | `ai_license_snapshot` | `dest_dm_ai_license_snapshot` | **ERROR** (tabela vive em `dimon`) | 5.767 | 2026-09-23 05:10 |
| 19 | `copilot_usage_daily` | `dest_dm_copilot_usage_daily` | **ERROR** (tabela vive em `dimon`) | 845.233 | 2026-09-23 05:10 |
| 20 | `copilot_license_snapshot` | `dest_dm_copilot_license_snapshot` | **ERROR** (tabela vive em `dimon`) | 15.881 | 2026-09-23 05:10 |
| 21 | `copilot_user_period` | `dest_dm_copilot_user_period` | **ERROR** (tabela vive em `dimon`) | 16.133 | 2026-09-23 05:10 |
| 2387 | `mvp_custo_recurso` | `dest_dm_cost_mvp_custo_recurso` | UPDATED | 1.354.962 | 2026-09-23 15:04 |
| 2388 | `mvp_oportunidade` | `dest_dm_cost_mvp_oportunidade` | UPDATED | 20 | 2026-09-23 15:04 |
| 2389 | `mvp_decisao` | `dest_dm_cost_mvp_decisao` | UPDATED | 3 | 2026-09-23 15:04 |
| 2390 | `mvp_aks_consumo` | `dest_dm_cost_mvp_aks_consumo` | UPDATED | 242 | 2026-09-23 15:04 |

Erros registrados:

- id 15 `custo_recursos_orfaos` e id 21 `copilot_user_period`:
  `line 14:18 / 16:20: mismatched input '{'. Expecting: <identifier>` — SQL
  template com interpolação inválida. **Falha de template, não de dado.**
- ids 17–20 (`ai_consumption_daily`, `ai_license_snapshot`, `copilot_usage_daily`,
  `copilot_license_snapshot`): `PSQLException: remaining connection slots are
  reserved for roles with the SUPERUSER attribute` — **exaustão de conexões do
  PostgreSQL do catálogo Iceberg**, não erro de SQL.

`materialization_rows` não é confiável como evidência de sucesso: as regras 17–20
têm `materialization_status = ERROR` e linhas contadas, e as tabelas existem em
`iceberg.dimon`, não em `iceberg.gold`. **O manifesto de G2 precisa registrar o
namespace físico, não só o nome da tabela.**

### G0.2.2 Fontes de `gold_rule_source` — 13 registros

| regra | `gold_table` / `gold_tables` | `value_column` | filtro |
|---|---|---|---|
| `copilot-custo` | `dest_modelo_focus` + `_planning` | `realizado` | `PricingCategory = GitHub` |
| `visao-completa` | `dest_modelo_focus` + `_planning` | `realizado` | — |
| `custo_aura_brain_vivo_mais` | `dest_modelo_focus` + `_planning` | `realizado` | `subaccountname CONTAINS Aura Brain` |
| `custo_notebooklm` | `dest_modelo_focus` + `_planning` | `realizado` | `subaccountname = telefonica-notebook-lm` |
| `projeto_sinfonia` | `dest_modelo_focus` + `_planning` | `realizado` | 4 condições em `departamento_nivel_5/6` |
| `projeto_datamesh` | `dest_modelo_focus` + `_planning` | `realizado` | `departamento_nivel_5 = Datamesh` |
| `saving_desligue_vm` | `dest_modelo_focus` | `realizado` | — |
| `custo_wisper` | `dest_modelo_focus` + `_planning` | `realizado` | `departamento_nivel_5 CONTAINS Wisper` |
| `custo_vivo_mais` | `dest_modelo_focus` + `_planning` | `realizado` | `departamento_nivel_5 CONTAINS Vivo+` |
| `mvp_custo_recurso` | `resource_cost` | `effective_cost` | — |
| `mvp_oportunidade` | `rightsizing_decision` | `saving_monthly` | — |
| `mvp_decisao` | `rightsizing_decision` | `saving_monthly` | — |
| `mvp_aks_consumo` | `resource_allocation` | `allocation_weight` | — |

**Fato central:** as regras organizacionais (ids 3–16) leem
`silver.dest_modelo_focus` (36,4 M linhas). As quatro regras MVP (2387–2390)
leem os canônicos `resource_cost`, `rightsizing_decision`, `resource_allocation`.
Há dois livros de custo em produção simultânea — ver D14.

### G0.2.3 Tabelas Gold físicas — 24 no total, em 3 namespaces

`iceberg.gold` (18): `_tmp_vm_monthly`, `dest_azure_ptu_capacity`,
`dest_azure_ptu_metrics`, `dest_dm_cost_consumo_diario_vm`,
`dest_dm_cost_copilot_custo`, `dest_dm_cost_custo_aura_brain_vivo_mais`,
`dest_dm_cost_custo_notebooklm`, `dest_dm_cost_custo_recursos_orfaos`,
`dest_dm_cost_custo_vivo_mais`, `dest_dm_cost_custo_wisper`,
`dest_dm_cost_mvp_aks_consumo`, `dest_dm_cost_mvp_custo_recurso`,
`dest_dm_cost_mvp_decisao`, `dest_dm_cost_mvp_oportunidade`,
`dest_dm_cost_projeto_datamesh`, `dest_dm_cost_projeto_sinfonia`,
`dest_dm_cost_saving_desligue_vm`, `dest_dm_cost_visao_completa`.

`iceberg.dimon` (6): `_tmp_vm_monthly` (0 linhas),
`dest_dm_ai_consumption_daily` (851.768), `dest_dm_ai_license_snapshot` (5.767),
`dest_dm_copilot_license_snapshot` (15.881), `dest_dm_copilot_usage_daily`
(845.233), `dest_dm_copilot_user_period` (16.133).

`iceberg.silver` com prefixo `dest_` (mistura de legado e staging): 11 tabelas,
incluindo `dest_modelo_focus` (36.434.762), `dest_modelo_focus_planning`
(133.382), `dest_consumo_ia` (17.335.973), `dest_licencas_ia` (37.985),
`dest_oci_operational_metrics` (145.571) e **5 tabelas de staging órfãs**
`dest_oci_operational_metrics_stg_<epoch>`.

Tabelas Gold referenciadas pela governança que **não existem fisicamente**:
`dest_dm_cost_snapshot_tags_automacao` (regra 12, inativa) e
`dest_dm_ai_consumption_daily` / `dest_dm_ai_license_snapshot` /
`dest_dm_copilot_usage_daily` / `dest_dm_copilot_license_snapshot` /
`dest_dm_copilot_user_period` **em `gold`** (existem em `dimon`).

### G0.2.4 Caminhos de escrita direta (CTAS) — o que G2 precisa unificar

O plano exige um único caminho governado. Hoje há **sete** módulos que emitem
`CREATE TABLE iceberg…` diretamente:

| Módulo | Usa governança `gold_rule`? | Consumidores ativos |
|---|---|---|
| `app/services/gold_materialization.py` | **Sim** — é o materializador governado | `worker.py:567,747,1031`; 10 routers/serviços via constante `GOLD` |
| `app/services/gold_builder.py` | Não — CTAS dentro do reprocess | `routers/etl.py:1412` |
| `app/services/gold_cubes_service.py` | Não | **nenhum** (código morto) |
| `app/services/focus_enrichment.py` | Não | **nenhum** (código morto) |
| `app/services/azure_ptu_gold.py` | Não | `routers/azure_ptu.py:55`, `aura_brain_vivo_mais.py:39`, `scheduler_service.py:251` |
| `app/services/ai_usage_gold.py` | Não | `routers/ai_copilot.py:19`, `ai_provider_dashboards.py:25`, `gold_materialization.py:385` |
| `app/services/ai_cost_materialization.py` | Não | `routers/etl.py:2658`, testes |

Observação: `gold_materialization.py` **já chama** `ai_usage_gold.refresh_ai_usage_gold_cubes()`
(linha 385), ou seja o caminho governado delega para um CTAS paralelo. E
`gold_materialization.py` também cria `_tmp_vm_monthly` e `_tmp_vm_daily` por
CTAS próprio (linhas 666, 765) e `dest_dm_cost_<saving>` (linha 1088).

### G0.2.5 Consumidores reais — frontend

| Rota | Página | Endpoint | Fonte efetiva |
|---|---|---|---|
| `/gold-dashboard` | `GoldDashboard.tsx` (2.227 linhas) | `POST /gold/query`, `GET /gold/info` | `dest_dm_cost_visao_completa` (Gold legada) |
| `/gold-dashboard` (abas) | `HierarchicalDecompTree.tsx`, `VisaoContabil.tsx` | `POST /gold/query` (3 + 2 chamadas) | idem |
| `/saving-actions` | `SavingActions.tsx` (210 linhas) | `GET /rightsizing/overview` | **Silver direta**: `resource_cost` + `rightsizing_candidate` |
| `/rightsizing` | `Rightsizing.tsx` (251 linhas) | `GET /rightsizing/dashboard`, `/rightsizing/decisions`, `/rightsizing/decisions/{hash}` | cubos MVP + Silver (workflow) |
| `/savings` | `Savings.tsx` | `GET /savings/*` | PostgreSQL (`saving_action`) |
| `/reservations` | redireciona | `GET /reservations/*` | `price_catalog` |
| `/visoes-consumo-ia` | `AzurePtuOps.tsx` e 8 sub-páginas | `POST /visoes-consumo-ia/dashboard/overview`, `/ops-overview`, `/ops-heatmap` | Gold PTU (`dest_azure_ptu_*`) |
| `/dashboards-ia` | `DashboardsIA` | `/ai-dashboards/*` | `dest_dm_ai_*` (namespace `dimon`) |

Confirmações de diagnóstico (seção 3.6 do plano):

- `SavingActions.tsx:59` rotula literalmente **"Custo total (17 períodos)"** e o
  valor vem de `rightsizing.py:210`, que faz
  `SUM(effective_cost)` sobre **toda** a `resource_cost`, misturando `azure-4p`
  (16 competências, várias não comparáveis) com `azure-ti` (1 competência).
  É a violação mais direta da regra 4.2.
- `rightsizing.py:195-265` (`/overview`) dispara **6 agregações sobre a Silver
  inteira** a cada carregamento, com `timeout_s=300` e `except: return []`.
  Sem paginação, sem cache, sem filtro obrigatório.
- `rightsizing.py:268-325` (`/dashboard`) já lê os cubos MVP — a tela usa os
  dois contratos ao mesmo tempo.
- `GoldDashboard` calcula KPIs no navegador (`aggregate`/`reduce`/`useMemo`
  em ~70 pontos) sobre o resultado de `POST /gold/query`.
- `queryDimonTable` / `getDimonInfo` (`apps/web/src/services/api.ts:4633,4649`)
  são os clientes do cubo legado; o nome `dimon` corresponde ao namespace
  Iceberg `dimon`, não a uma arquitetura vigente.

---

## G0.3 Dicionário inicial de métricas

Formato: nome · fórmula · grão · moeda · filtros obrigatórios · owner ·
limitações conhecidas.

| KPI | Fórmula | Grão | Moeda | Filtros obrigatórios | Owner | Limitações |
|---|---|---|---|---|---|---|
| `kpi_custo_efetivo` | `SUM(effective_cost)` | conexão × competência × moeda | única por consulta | `data_status IN (COMPLETE, CURRENT_OPEN)` explícito | FinOps | 202504 é ajuste de 1 dia; 202604–202607 são linhas de compra |
| `kpi_custo_faturado` | `SUM(billed_cost)` | idem | idem | idem | FinOps | **não mensurável**: `billed_cost` NULL em 100 % das linhas |
| `kpi_desconto_contratado` | `SUM(list_cost − contracted_cost)` | conexão × serviço × competência | idem | ambos não nulos | FinOps | **não mensurável**: `contracted_cost` NULL; `list_cost = effective_cost` |
| `kpi_custo_lista` | `SUM(list_cost)` | idem custo efetivo | idem | idem | FinOps | idêntico a efetivo hoje; não usar como "preço de tabela" |
| `kpi_variacao_mom` | `(C(n) − C(n−1)) / C(n−1)` | conexão × moeda | idem | **par de competências `COMPLETE` da MESMA conexão** | FinOps | proibido com 202504 e com 202604–202607 |
| `kpi_run_rate_anualizado` | `média(C das competências COMPLETE do escopo) × 12` | conexão × moeda | idem | informar nº de meses usados | FinOps | 4P tem 12 competências usáveis; TI tem 0 fechadas |
| `kpi_custo_parcial` | `SUM(effective_cost)` com `data_status = PARTIAL/CURRENT_OPEN` | conexão × competência | idem | — | FinOps | nunca somar ao efetivo comparável |
| `kpi_custo_sem_classificacao` | `SUM(effective_cost)` onde `service_key`/org/tag ausentes | conexão × competência × dimensão | idem | dimensão explícita | Eng. Dados | ≈R$ 5.857,19/mês sem serviço; R$ 500–644 mil/mês sem tag no 4P |
| `kpi_custo_nao_alocado` | custo elegível sem consumidor confiável | conexão × competência × nível | idem | definição de "elegível" | FinOps | hoje só existe no nível `node_pool` para AKS |
| `kpi_economia_identificada` | melhor decisão `VALID` vigente por recurso | recurso × versão de decisão | BRL | uma alternativa por recurso | FinOps | baseline: 23 recursos, R$ 6.050,24/mês |
| `kpi_economia_pendente` | identificada com revisão `PENDING` | idem | BRL | — | FinOps | não é compromisso; 23/23 no baseline |
| `kpi_economia_aprovada` | identificada com revisão `APPROVED` | idem | BRL | estado do PostgreSQL, não da Silver | FinOps | **divergência D11**: PG tem 2 APPROVED, Silver diz 127 PENDING |
| `kpi_economia_realizada` | baseline ajustado − custo posterior ajustado | `action_id × competência` | BRL | `executed_at` + janela completa | FinOps | **não mensurável**: `saving_action` tem 0 linhas |
| `kpi_cobertura_alocacao_aks` | peso alocado / peso total por cluster | cluster × dia | — | nível suportado | FinOps | baseline: só `node_pool`, `PARTIAL`, `namespace` sempre NULL |
| `kpi_cobertura_telemetria` | recursos com perfil / recursos elegíveis | métrica × tipo de recurso | — | janela explícita | Eng. Dados | 194 VMs com janela de 15 dias; só `virtualmachines` |
| `kpi_cobertura_join_custo_inventario` | recursos em `resource_cost` presentes em `resource_inventory` | tipo × competência | — | chave canônica provider+conta+id | Eng. Dados | 968.339 recursos de custo vs 20.160 inventariados |
| `kpi_ptu_utilizacao` | média e p95 de `utilization_avg`/`utilization_p95` | recurso × deployment × hora | — | completude ≥ limiar | Plataforma IA | `max` > 100 %: não tratar como percentual |
| `kpi_ptu_oportunidade_financeira` | *(nula)* | recurso × deployment × competência | BRL | custo **e** vigência comparáveis | FinOps | **não mensurável** hoje; proibido derivar de utilização |
| `kpi_exposicao_compromisso` | compra − cobertura associada | conexão × serviço × compromisso × competência | idem | `charge_category` + `reservationorders` | FinOps | dados existem (`Microsoft.Capacity`, ~R$ 500 mil/mês); cobertura de reserva **não** inferível por preço |
| `kpi_reconciliacao_silver_gold` | `SUM(Silver) − SUM(Gold)` por período/conexão/moeda | cubo × competência | idem | tolerância R$ 0,01 | Eng. Dados | precisa excluir o livro legado `dest_modelo_focus` (36,4 M linhas) |
| `kpi_data_quality_score` | composição de regras versionadas | conexão × dataset × regra × severidade | — | sempre com valor financeiro afetado | Eng. Dados | `period_status` está **vazio**: a base da classificação não existe ainda |

---

## G0.4 Matriz dos cubos C1–C12

| Cubo | Decisão | Situação | Fonte Silver | Observação de G0 |
|---|---|---|---|---|
| **C1** `finops_cost_monthly` | **criar** | não existe | `resource_cost` | 13,5 M linhas; particionar por `billing_period`; normalizar caixa de `service_name` (D8, D1) |
| **C2** `finops_cost_resource_monthly` | **substituir/alterar** | `dest_dm_cost_mvp_custo_recurso` existe com 1.354.962 linhas, mas sem serviço/categoria/tipo/moeda/completude; `resource_name` e `region_id` nulos amostrados | `resource_cost` + snapshot de `resource_inventory` | exige join 1:1 provado (2 snapshots × 20.160 recursos) |
| **C3** `finops_cost_change_driver` | **criar** | não existe | C1 + C2 | depende de C1 com `data_status` |
| **C4** `finops_efficiency_resource` | **alterar** | `resource_profile_window` (1.746) é a base, mas traz só `virtualmachines`, janela de 15 dias e `billed_cost`/`consumed_quantity` como `varchar` | `resource_profile_window` + C2 | perfis de VMSS não existem como tipo distinto |
| **C5** `finops_optimization_portfolio` | **substituir** | `dest_dm_cost_mvp_oportunidade` (20) e `dest_dm_cost_mvp_decisao` (3) — agregados demais | `rightsizing_decision` + PG | **deve sobrepor `rightsizing_review`** (D11); uma linha por `evidence_hash` |
| **C6** `finops_optimization_funnel_daily` | **criar** | não existe | C5 + histórico | precisa de snapshot diário a partir da 1ª execução |
| **C7** `finops_optimization_projection_daily` | **aguardar/criar** | não existe | `resource_metric_series` + candidato | série existe (3,08 M, 15 dias); projeção depende de simulação |
| **C8** `finops_aks_allocation_daily` | **substituir** | `dest_dm_cost_mvp_aks_consumo` replica 242 pesos de `node_pool` | `resource_cost` + `resource_allocation` + `container_metric_series` | `namespace` NULL em 242/242: **a ponte não existe**; parar em `node_pool` |
| **C9** `finops_ptu_efficiency_hourly` | **alterar** | `dest_azure_ptu_metrics` (17.904) e `dest_azure_ptu_capacity` (28) já materializados fora da governança | `ptu_capacity_series` | 2 recursos/4 deployments; custo atribuído ainda não reconciliado (D12) |
| **C10** `finops_commitment_exposure` | **criar** | não existe | `resource_cost` + `price_catalog` | **condição satisfeita para exposição**: `charge_category='Purchase'`, `resource_id=/providers/microsoft.capacity/reservationorders/…`; cobertura/utilização **não** inferível |
| **C11** `finops_data_quality_daily` | **criar** | não existe; `period_status` vazio | metadata de carga + Silver + Gold | incluir regra de divergência `dest_modelo_focus` × `resource_cost` (D14) |
| **C12** `finops_savings_realized_monthly` | **aguardar dado** | contrato preparado | `saving_action` + C2 | `saving_action` = 0 linhas; gate não satisfeito |

Legenda: **criar** = não existe; **alterar** = existe e serve de base, precisa de
grão/colunas; **substituir** = existe, mas o grão ou a semântica não sustentam a
decisão; **aguardar dado** = contrato pronto, dado insuficiente.

---

## G0.5 Desenho concreto da classificação de competência completa

O plano define os estados em 4.2. O que falta é a **fonte dos sinais**. No
baseline, `period_status` (PostgreSQL) está **vazio** — nenhuma competência está
classificada hoje. Proposta para G1, usando apenas o que existe:

**Chave:** `connection_code + billing_period + dataset`.

**Sinais coletados (todos já medidos/medíveis em G0):**

1. `status` da execução da tarefa ETL que carregou a fatia (`etl_task`);
2. `first_charge_day` e `last_charge_day` = `MIN/MAX(charge_period_start)`;
3. `days_covered` = `COUNT(DISTINCT date(charge_period_start))` vs dias do mês;
4. `row_delta_pct` e `cost_delta_pct` vs mediana das competências da **mesma
   conexão**;
5. `service_dims_missing_pct` e `region_dims_missing_pct` (dimensões críticas);
6. `age_days` = hoje − `MAX(_processed_at)` da fatia;
7. reconciliação Bronze → Silver por contagem de linhas da fatia;
8. `charge_category` predominante (separa mês operacional de mês de ajuste).

**Regra de decisão (determinística):**

```text
se não há carga esperada para (conexão, período, dataset)      -> NOT_APPLICABLE
senão se não há nenhuma execução registrada                     -> MISSING
senão se execução ok e 0 linhas                                -> EMPTY
senão se período é o mês corrente e age_days <= 3               -> CURRENT_OPEN
senão se execução falhou                                        -> PARTIAL (motivo=run_failed)
senão se dias_cobertos < 80% dos dias esperados                 -> PARTIAL (motivo=short_window)
senão se custo sem dimensão crítica > 5% do período             -> PARTIAL (motivo=missing_dims)
senão se |cost_delta_pct| vs mediana > 3× MAD da conexão        -> PARTIAL (motivo=outlier)
senão se reconciliação Bronze->Silver diverge > 0,5%            -> PARTIAL (motivo=reconciliation)
senão                                                           -> COMPLETE
```

**Aplicação ao baseline (resultado esperado de G1, para conferência):**

| conexão | período | dias cobertos | status esperado | motivo |
|---|---|---:|---|---|
| azure-4p | 202504 | 1 | `PARTIAL` | short_window; custo ~0; ajuste |
| azure-4p | 202505 | 10 + 1,58 M linhas sem região | `PARTIAL` | short_window + missing_dims |
| azure-4p | 202506–202512, 202601–202603 | ≥15 | `COMPLETE` (a confirmar por outlier) | — |
| azure-4p | 202602, 202603 | 12 e 15; 11 e 10 regiões | `COMPLETE` ou `PARTIAL` | avaliar outlier de região |
| azure-4p | 202604–202606 | 1 dia; 1–51 linhas; só `Purchase` | `PARTIAL` | short_window; linhas de compra |
| azure-4p | 202607 | 1 dia; 3 linhas; custo 0 | `PARTIAL` | short_window |
| azure-ti | 202609 | 1 (todas as linhas em 01/09) | `CURRENT_OPEN` | mês corrente no baseline |
| demais conexões | — | — | `NOT_APPLICABLE` | não produzem `resource_cost` |

**Implementação prevista:** tabela `iceberg.gold.finops_period_coverage`
(derivável de `resource_cost` + `etl_task`) materializada antes de C1; a
classificação **não** pode depender só de contagem de linhas (era o defeito
apontado no plano). `period_status` no PostgreSQL continua sendo o override
manual de fechamento contábil, com precedência sobre o cálculo — mas hoje vazio.

**Consequência para KPI temporal:** o último mês `COMPLETE` do `azure-4p` é
202603 (a confirmar). O `azure-ti` não tem nenhuma competência comparável. Todo
MoM, run rate e forecast usam apenas `COMPLETE` da mesma conexão.

---

## G0.6 Mapa widget → endpoint → cubo (sete abas)

Endpoints `/finops/*` são **novos** (G8). A coluna "hoje" mostra o que a tela
usa se for construída antes de G8.

### Aba 1 — Resumo executivo

| Widget | Endpoint (novo) | Cubo | Hoje (equivalente) |
|---|---|---|---|
| Custo efetivo da última competência comparável | `GET /finops/summary` | C1 | `rightsizing.py:210` (soma 17 competências — errado) |
| Variação MoM (valor e %) | `GET /finops/summary` | C1 + C3 | não existe |
| Run rate anualizado + nº de meses | `GET /finops/summary` | C1 | não existe |
| Custo em período parcial (separado) | `GET /finops/summary` | C1 | não existe |
| Cards identificado / aprovado / realizado | `GET /finops/optimization/funnel` | C5 + C6 | `dest_dm_cost_mvp_oportunidade` (20 linhas) |
| Custo com classificação incompleta | `GET /finops/summary` | C1 + C11 | não existe |
| Cobertura de telemetria e de alocação AKS | `GET /finops/data-quality` | C11 | não existe |
| Waterfall de variação | `GET /finops/cost/drivers` | C3 | não existe |
| Top 5 ações por saving/confiança/idade | `GET /finops/optimization/portfolio` | C5 | `rightsizing.py:223` (Silver direta) |
| Painel "Decisões requeridas" | `GET /finops/optimization/portfolio` | C5 | `GET /rightsizing/decisions` |

### Aba 2 — Custo e drivers

| Widget | Endpoint | Cubo | Hoje |
|---|---|---|---|
| Série mensal comparável | `GET /finops/cost/trend` | C1 | `rightsizing.py:211` (todas as competências) |
| Decomposição por org/conta/serviço/categoria/região | `GET /finops/cost/trend` | C1 | `GoldDashboard`/`VisaoContabil` via `POST /gold/query` |
| Pareto e concentração | `GET /finops/cost/trend` | C1 | `GoldDashboard` (navegador) |
| Tabela de drivers | `GET /finops/cost/drivers` | C3 | não existe |
| Recursos novos/removidos/maior aumento | `GET /finops/cost/drivers` | C3 + C2 | não existe |
| Drilldown até recurso | `GET /finops/cost/resources` | C2 | `top_recursos` (`rightsizing.py:219`, LIMIT 20) |
| Exportação filtrada | idem + `page` | C2 | export XLSX no navegador (`GoldDashboard`) |

### Aba 3 — Eficiência

| Widget | Endpoint | Cubo | Hoje |
|---|---|---|---|
| Matriz custo × utilização | `GET /finops/efficiency/resources` | C4 | não existe |
| Distribuição por quadrante/confiança/cobertura | `GET /finops/efficiency/resources` | C4 | não existe |
| Top alto custo + baixo uso | `GET /finops/efficiency/resources` | C4 | `rightsizing_candidate` (Silver) |
| Tooltip CPU/memória/disco/rede | `GET /finops/efficiency/resources` | C4 | não existe |
| Detalhe com série + recomendação | `GET /finops/efficiency/resources/{id}` | C4 + C7 | `GET /rightsizing/decisions/{hash}` |

### Aba 4 — Oportunidades e workflow

| Widget | Endpoint | Cubo | Hoje |
|---|---|---|---|
| Funil detectadas→…→medidas | `GET /finops/optimization/funnel` | C6 | não existe |
| Valores mensal e anual por estado | `GET /finops/optimization/funnel` | C6 | não existe |
| Aging e SLA de revisão | `GET /finops/optimization/funnel` | C6 | não existe |
| Carteira por estratégia/tipo/confiança/owner | `GET /finops/optimization/portfolio` | C5 | `dest_dm_cost_mvp_oportunidade` |
| Tabela acionável com DE/PARA | `GET /finops/optimization/portfolio` | C5 | `rightsizing.py:223` |
| Comparação entre alternativas (sem somar) | `GET /finops/optimization/{hash}/comparison` | C5 | `GET /rightsizing/decisions/{hash}` |
| Revisão humana | `PUT /rightsizing/decisions/{hash}/review` | PG + C5 | já existe |
| Modal observado × projetado | `GET /finops/optimization/{hash}/comparison` | C7 | não existe |

### Aba 5 — AKS economics

| Widget | Endpoint | Cubo | Hoje |
|---|---|---|---|
| Custo por cluster + cobertura | `GET /finops/aks/tree` | C8 | `dest_dm_cost_mvp_aks_consumo` (242) |
| cluster → node_pool → namespace → workload | `GET /finops/aks/tree` | C8 | **para em `node_pool`**: `namespace` NULL |
| workload/idle/system/shared/unallocated | `GET /finops/aks/tree` | C8 | `allocation_status='PARTIAL'` |
| Eficiência requests/usage | `GET /finops/aks/tree` | C8 | `container_metric_series` (4.153 ns) |
| Clusters sem Container Insights | `GET /finops/aks/tree` | C8 + C11 | 41 clusters `UNAVAILABLE` |
| Seletor de snapshot/data | `GET /finops/aks/tree` | C8 | `snapshot_date` em `resource_allocation` |
| Breadcrumb + reconciliação por nível | `GET /finops/aks/tree` | C8 | não existe |

### Aba 6 — AI/PTU

| Widget | Endpoint | Cubo | Hoje |
|---|---|---|---|
| Capacidade provisionada + média/p95/max | `GET /finops/ptu/efficiency` | C9 | `POST /visoes-consumo-ia/dashboard/overview` |
| Tokens e requests por deployment/modelo | `GET /finops/ptu/efficiency` | C9 | `dest_azure_ptu_metrics` |
| Erro/throttling/spillover/latência | `GET /finops/ptu/efficiency` | C9 | idem |
| Heatmap hora × dia | `GET /finops/ptu/efficiency` | C9 | `POST /visoes-consumo-ia/dashboard/ops-heatmap` |
| Períodos ociosos/saturados | `GET /finops/ptu/efficiency` | C9 | não existe |
| Completude da telemetria | `GET /finops/ptu/efficiency` | C9 | `completeness_pct` (99,87 %) |
| Custo e oportunidade | `GET /finops/ptu/efficiency` | C9 + C2 | **nulo** até o join reconciliar |

### Aba 7 — Qualidade e cobertura

| Widget | Endpoint | Cubo | Hoje |
|---|---|---|---|
| Score + componentes | `GET /finops/data-quality` | C11 | não existe |
| Freshness por fonte/cubo | `GET /finops/data-quality` | C11 | `gold_rule.materialized_at` |
| Períodos completos/parciais/ausentes | `GET /finops/data-quality` | C11 (+cobertura) | não existe; `period_status` vazio |
| Custo sem serviço/região/tag/org | `GET /finops/data-quality` | C1 + C11 | não existe |
| Cobertura custo↔inventário, custo↔telemetria, custo↔alocação | `GET /finops/data-quality` | C11 | não existe |
| Delta Silver × Gold | `GET /finops/data-quality` | C11 | não existe |
| Incidentes de pipeline | `GET /finops/data-quality` | C11 | `gold_rule.materialization_error` |

---

## G0.7 Arquivos previstos para G1 e riscos de migração

### Arquivos previstos (G1 — fundação semântica e qualidade de período)

| Arquivo | Ação | Conteúdo |
|---|---|---|
| `apps/api/app/services/semantic_normalization.py` | **criar** | normalizadores: `provider_key`, `service_key` + `service_name_display` determinístico, `resource_type_key`, `region_key` (com `GLOBAL` ≠ NULL), `canonical_resource_id`, `currency`, `billing_period` |
| `apps/api/app/services/period_coverage.py` | **criar** | coleta dos 8 sinais e a regra de decisão de G0.5; escreve `finops_period_coverage` |
| `apps/api/app/services/gold_materialization.py` | alterar | registrar `finops_period_coverage` como dependência de C1; corrigir docstring de `GoldMartRule` que descreve fontes como tabelas Gold |
| `apps/api/app/models/gold_mart_rule.py` | alterar | docstrings (`GoldMartRule` linha 1-7 e `GoldMartRuleSource`); nenhuma mudança de coluna |
| `apps/api/app/services/silver_models.py` | alterar — **feito em G1** | bloco `FOCUS_ENRICHED` removido (linhas 329-396) — sem consumidor |
| `apps/api/app/services/focus_enrichment.py` | **remover ou marcar** — **feito em G1** | código morto; zero consumidores confirmado |
| `apps/api/tests/test_semantic_normalization.py` | criar | casos negativos: caixa de serviço, nulo vs global, ID canônico |
| `apps/api/tests/test_period_coverage.py` | criar | 202504, 202505, 202604 e azure-ti/202609 produzem o status esperado |
| `scripts/` (novo utilitário) | criar | consulta de conferência do baseline para regressão |

### Riscos de migração

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Normalizar `service_name` quebra cubos legados que agrupam por valor bruto | alta | alto | normalizar **apenas na Gold**; Silver preserva o original (regra 4.1). Regras legadas (ids 3–16) leem `dest_modelo_focus`, não `resource_cost`: não são afetadas |
| Join `resource_cost` × `resource_inventory` multiplica linhas (2 snapshots × 20.160 recursos) | alta | alto | provar 1:1 por `provider+conta+id canônico+snapshot mais recente ≤ período`; medir antes de materializar C2 |
| Interpretar `region_id` NULL como erro quando é reserva global | alta | médio | introduzir valor explícito `GLOBAL` e regra C11 separada para "região ausente" vs "região global" |
| `list_cost = effective_cost` e `billed_cost` NULL levarem a publicar "desconto = 0" | média | alto | campos não mensuráveis saem `NULL` + `quality.warnings`, jamais `0` |
| Sobrepor `rightsizing_review` no C5 e perder a trilha determinística | média | alto | C5 mantém `deterministic_verdict` e `review_status` em colunas distintas, como já faz `routers/rightsizing.py:190` |
| Sobrecarga de conexões do PostgreSQL do catálogo Iceberg (erro real observado, D9) | alta | alto | G2 deve limitar concorrência do materializador; o manifesto registra a falha em vez de a mascarar |
| Remover `focus_enriched` quebrar algo não mapeado | baixa | médio | grep já confirma zero consumidores; remover só em G1, com teste de importação |
| Mexer em `_tmp_vm_monthly` interromper o saving de desligue (regra 11/16) | média | médio | renomear só depois de migrar o consumidor; manter alias temporário |
| `dest_modelo_focus` (36,4 M) e `resource_cost` (13,5 M) divergirem e a nova tela contradizer a antiga | alta | alto | medir a divergência em C11 antes do cutover (G10) e documentar a diferença de metodologia |
| `.env` da raiz contém token do GitHub | — | **alto** | **não incluir em commit**; `.env` está no `.gitignore`. Registrado aqui apenas como alerta de segurança |

---

## G0.8 `git status` inicial — mudanças do usuário preservadas

Branch atual: **`novo_dev`**.

```text
## novo_dev
?? planGoldDecisionDashboards.md
?? pnpm-lock.yaml
```

Últimos commits:

```text
53a85545 docs: dashboard completo custo e antes/depois
a5214e29 feat(P11): dashboard completo de custo e antes/depois (VM+VMSS)
e57f4d91 fix(P11): coletor de metricas inclui VMSS e resource_type vem da fonte
c4f3b13a docs: menu novo Saving Actions
56c6969d feat(P10): menu novo "Saving Actions" com o dashboard dedicado
```

- `planGoldDecisionDashboards.md` — arquivo do plano, **untracked**, do usuário.
  **Preservar.** Só incluir em commit se o usuário pedir.
- `pnpm-lock.yaml` — **untracked**, gerado. Preservar.
- Nenhum arquivo rastreado estava modificado no início de G0.
- Nenhum `.env`, dump, secret ou dado local foi lido para produzir este log,
  exceto a constatação de que `.env` contém um token (registrada em G0.7).

O único arquivo criado por este pacote é
`docs/ai/gold-decision-dashboard-execution-log.md`.

---

## G0.9 Aceite do pacote

| Critério do plano | Situação |
|---|---|
| Números do plano confirmados ou divergências explicadas | **Atendido.** 13 contagens conferem exatamente. 14 divergências registradas (D1–D14), nenhuma silenciosa. |
| Nenhuma mudança funcional | **Atendido.** G0 é somente leitura. |
| Nenhum secret no log | **Atendido.** Nenhum valor de credencial transcrito. |
| Inventário `reusar/alterar/remover` | **Atendido.** G0.2 e G0.4. |
| Dicionário versionado | **Atendido.** G0.3 (23 KPIs, com limitações). |
| Matriz tela → KPI → cubo | **Atendido.** G0.6 (sete abas). |
| Desenho da classificação de competência | **Atendido.** G0.5, com regra determinística e resultado esperado. |

**Divergências que mudam contrato** (exigem decisão antes de G3+): D1, D8, D9,
D10, D11, D12, D14.
**Divergências que mudam apenas o baseline:** D2, D3, D4, D5, D6, D7, D13.

Nenhuma ADR é necessária para as divergências de baseline. Para D1 (moeda e
custo faturado não mensuráveis) e D11 (estado de revisão no PostgreSQL) o
contrato de C1 e C5, respectivamente, já está explicitado neste log e não exige
alteração de grão.

## G0.10 Próximo passo

**G1 não foi iniciado nesta execução**, conforme a seção 12 do plano
("Não codificar G1 na mesma execução de G0").

Ordem proposta de abertura de G1, já sustentada por este log:

1. `semantic_normalization.py` (elimina a divisão por caixa de `Microsoft.Compute`;
   introduz `GLOBAL` para reserva sem região);
2. `period_coverage.py` (faz 202504/202505/202604–202607 e `azure-ti/202609`
   deixarem de ser base silenciosa de MoM);
3. remoção da declaração `FOCUS_ENRICHED` e do módulo `focus_enrichment.py`;
4. testes negativos da seção 10.2 que já são reproduzíveis hoje.

---

# G1 — Fundação semântica e qualidade de período

**Status:** concluído. Commit e push registrados em G1.9.

## G1.0 Regra de execução do pacote

```text
entrada     : G0 (baseline e divergencias D1-D14) + decisoes de direcao do usuario
premissas   : resource_cost e a fonte canonica de C1-C3; dest_modelo_focus atende
              consumidores legados ate o cutover; billed_cost/contracted_cost
              publicados como indisponiveis; workflow vem de rightsizing_review;
              AKS limitado a node_pool; PTU nao usa utilization_max
arquivos    : +app/services/semantic_normalization.py
              +app/services/period_coverage.py
              -app/services/focus_enrichment.py (removido)
              ~app/services/silver_models.py (declaracao FOCUS_ENRICHED removida)
              +tests/test_semantic_normalization.py
              +tests/test_period_coverage.py
saida       : normalizadores + classificacao de competencia + guarda de reconciliacao
testes      : 116 testes novos; suite completa sem regressao nova
risco       : ver G0.7. Nenhum risco materializado.
rollback    : git revert do commit de G1. Os dois arquivos novos sao aditivos; a
              remocao de focus_enriched nao tem consumidor.
```

## G1.1 Normalizadores semânticos

Arquivo novo: `apps/api/app/services/semantic_normalization.py`.

Cada normalizador tem **duas formas equivalentes**: uma em Python (`*_key`) usada
por API, serviço e teste; outra como expressão Trino (`sql_*`) usada pelo
materializador. Não existem duas definições de "serviço normalizado".

| Normalizador | Regra | Efeito medido em 23/09/2026 |
|---|---|---|
| `service_key` | lowercase, remove espaços | 83 serviços → **62** chaves; 21 colisões de caixa |
| `service_display_name` | marca capitalizada, mais frequente, empate lexical | determinístico, independe da ordem de leitura |
| `region_key` | lowercase; `GLOBAL` ≠ `UNKNOWN` | 18 chaves (era 19 com `BRAZILSOUTH` duplicado) |
| `resource_type_key` | lowercase | `UNKNOWN` em 100 % (coluna nula no custo) |
| `canonical_resource_id` | `trim(lower())`, preserva `NULL` | 968.339 ids distintos, sem colisão |
| `billing_period_key` | exatamente `YYYYMM` | aceita `2025-04`, descarta `202513` |
| `currency_code` | ISO maiúsculo, ausente = `None` | nunca assume `BRL` |
| `provider_key` | vocabulário controlado | `azure` |
| `cost_join_key` | `(provider, conta, id canônico)` | `resource_id` sozinho não liga |
| `assert_single_currency` | falha se houver 2 moedas | impede somar BRL + USD |

### G1.1.1 Custo sem dimensão: presença não é validade

Achado novo durante a implementação, não previsto no plano: as 5.294.906 linhas
de ajuste do `azure-4p` (202504/202505) têm `resource_id` **preenchido** com
valores como `'320core'`, `'100core'` e `'96core'`. Não são nulos — são
identificadores sintéticos de carga.

Consequência: um teste de "`resource_id` é nulo?" devolve zero linhas
problemáticas e o dado sujo passa. A primeira versão da classificação de
cobertura fez exatamente isso e reprovou 202505 como `COMPLETE`.

`is_valid_resource_id` resolve: um ID de nuvem é caminho de provedor (começa com
`/` e contém `/providers/`). `is_valid_sub_account_id` faz o mesmo para a conta.
Esses valores **não** podem virar dimensão de C2.

### G1.1.2 Custo sem tag é medida própria, não defeito de competência

`tags_json` está ausente ou vazio em 5.677.656 de 13.519.512 linhas. A maioria é
recurso de plataforma (storage, rede, licença) que legitimamente não tem tag de
alocação. Tratar isso como dimensão crítica reprovava 12 dos 16 meses do 4P e
escondia o defeito real.

Decisão: `rows_without_tag` e `cost_without_tag` são medidas próprias de **custo
não alocado** (seção 4.3) e geram aviso acima de 20 % — nunca parcialidade de
competência.

## G1.2 Classificação de competência

Arquivo novo: `apps/api/app/services/period_coverage.py`.

Estados: `COMPLETE`, `PARTIAL`, `EMPTY`, `MISSING`, `CURRENT_OPEN`,
`NOT_APPLICABLE`. `comparable` é a única propriedade que o consumidor deve
consultar para MoM, run rate, anomalia e forecast.

Regra determinística, na ordem: sem carga esperada → `NOT_APPLICABLE`; sem
execução e sem linhas → `MISSING`; execução ok com zero linhas → `EMPTY`; execução
falhou → `PARTIAL`; mês corrente → `CURRENT_OPEN`; janela < 80 % → `PARTIAL`;
dimensão crítica > 5 % → `PARTIAL`; reconciliação > 0,5 % → `PARTIAL`; só
compra/ajuste → `PARTIAL`; caso contrário `COMPLETE`.

**Anomalia não é parcialidade.** Um valor atípico contra o histórico vira
`warnings` e o período continua comparável. Excluir um pico real do MoM esconderia
exatamente a variação que o usuário precisa investigar.

### G1.2.1 Janela medida pelo intervalo, não por dias distintos

Medido: `charge_period_start` tem apenas 10 a 15 valores distintos por mês — é o
dia da fatura, não o dia de consumo. Contar dias distintos reprovava **todos** os
16 meses do 4P. A janela passou a ser `último dia − primeiro dia + 1`:
202505 cobre 01/05 a 28/05 = 28 dias (90 %), 202504 cobre 1 dia (3 %).

### G1.2.2 Outlier por resíduo contra a mediana da conexão

A referência é a mediana das competências operacionais da conexão e a escala é a
MAD dos resíduos — não a MAD do custo absoluto. O 4P varia de R$ 2,06 M (202509) a
R$ 3,83 M (202601); com MAD do custo absoluto, 202509 era reprovado sendo mês
operacional completo. Meses de ajuste e mês de compra ficam fora da referência.

### G1.2.3 Resultado real no baseline

Executado contra `iceberg.silver.resource_cost` em 23/09/2026:

| competência | conexão | status | motivo | janela | dim. ausente | custo |
|---|---|---|---|---:|---:|---:|
| 202504 | azure-4p | `PARTIAL` | `SHORT_WINDOW` | 0,03 | 98,83 % | 209.564,67 |
| 202505 | azure-4p | `PARTIAL` | `MISSING_DIMENSIONS` | 0,90 | 69,69 % | 3.177.030,65 |
| 202506 | azure-4p | `COMPLETE` | `OK` | 0,93 | 0 % | 2.881.673,31 |
| 202507 | azure-4p | `COMPLETE` | `OK` | 0,90 | 0 % | 2.986.495,25 |
| 202508 | azure-4p | `COMPLETE` | `OK` | 0,90 | 0 % | 2.912.511,00 |
| 202509 | azure-4p | `COMPLETE` | `OK` | 0,93 | 0 % | 2.055.609,04 |
| 202510 | azure-4p | `COMPLETE` | `OK` | 0,90 | 0 % | 2.882.348,17 |
| 202511 | azure-4p | `COMPLETE` | `OK` | 0,93 | 0 % | 3.507.991,37 |
| 202512 | azure-4p | `COMPLETE` | `OK` | 1,00 | 0 % | 3.718.891,12 |
| 202601 | azure-4p | `COMPLETE` | `OK` | 0,97 | 0 % | 3.827.009,90 |
| 202602 | azure-4p | `COMPLETE` | `OK` | 1,00 | 0 % | 3.455.091,64 |
| 202603 | azure-4p | `COMPLETE` | `OK` | 0,97 | 0 % | 3.265.244,23 |
| 202604 | azure-4p | `PARTIAL` | `PURCHASE_ONLY` | 1,00 | 0 % | 194.941,66 |
| 202605 | azure-4p | `PARTIAL` | `SHORT_WINDOW` | 0,03 | 0 % | 146.346,00 |
| 202606 | azure-4p | `PARTIAL` | `SHORT_WINDOW` | 0,03 | 0 % | 147.865,00 |
| 202607 | azure-4p | `PARTIAL` | `SHORT_WINDOW` | 1,00 | 0 % | 0,00 |
| 202609 | azure-ti | `CURRENT_OPEN` | `CURRENT_PERIOD` | 0,03 | 0 % | 1.987.748,46 |

Consequências para os KPIs:

- **10 competências comparáveis** do `azure-4p`: 202506–202603 (exclui 202504,
  202505 e 202604–202607);
- **9 pares MoM permitidos**, todos consecutivos, todos do `azure-4p`;
- `azure-ti` tem **zero** competências comparáveis: run rate e MoM indisponíveis,
  com motivo explícito;
- run rate do `azure-4p`: R$ 3.149.286,50/mês sobre **10 meses** → R$ 37.791.438,04
  anualizado. O número de meses é parte do resultado.

Nenhuma comparação atravessa conexões nem usa período parcial em silêncio.

## G1.3 `focus_enriched` removido

- `apps/api/app/services/silver_models.py`: bloco `FOCUS_ENRICHED` (declaração de
  62 linhas) removido, incluindo a entrada em `MODELS`. O registro passou de 14
  para 13 modelos e **nenhuma tabela `gold.*` resta no registry**.
- `apps/api/app/services/focus_enrichment.py`: removido (195 linhas, zero
  consumidores).
- Prova de que não quebrou nada:
  - `python -c "import app.main"` → importa;
  - `model('resource_cost')` resolve; `'focus_enriched' not in MODELS`;
  - busca por `focus_enriched`/`FOCUS_ENRICHED` no código: zero ocorrências;
  - suíte completa sem regressão nova (G1.5).

## G1.4 Guarda de reconciliação

`reconciliation_sql` + `verify_reconciliation` provam, por competência, que
agrupar pelas chaves normalizadas **não altera** o total nem a contagem de linhas.
Tolerância: R$ 0,01 (seção 5, C1). Delta de linhas diferente de zero é sempre
falha, mesmo com total igual — fecharia por coincidência.

Executado no baseline:

```text
reconciliacao global por competencia      : OK (17 competencias, nenhum problema)
reconciliacao por service_key + region_key: OK (17 competencias, nenhum problema)
```

Exemplo medido: 202505 tinha e continua com R$ 3.177.030,65 em 2.261.106 linhas.

## G1.5 Testes

| Arquivo | Testes | Cobre |
|---|---:|---|
| `tests/test_semantic_normalization.py` | 55 | caixa, ausência, `GLOBAL`/`UNKNOWN`, chave de ligação, moeda, competência, equivalência das expressões SQL |
| `tests/test_period_coverage.py` | 61 | os 17 períodos reais, ordem de precedência, limiares, outlier, run rate, MoM, guarda de reconciliação |
| **total novo** | **116** | |

Suíte completa: **510 passam, 9 falham**. As 9 falhas são **pré-existentes**:
rodadas em `git stash` no HEAD limpo (`7f42db17`) produzem exatamente as mesmas 9
falhas. Nenhuma regressão introduzida por G1.

```text
test_aura_brain_vivo_mais.py (2)      test_auth_unit.py::test_dev_defaults_safe
test_azure_ptu_finops.py (1)          test_azure_ptu_gold.py (1)
test_datamesh_dashboard.py (1)        test_gold_materialization.py (1)
test_sinfonia_dashboard.py (2)
```

## G1.6 Aceite de G1

| Critério | Situação | Evidência |
|---|---|---|
| Períodos incompletos não alimentam comparações silenciosas | **Atendido** | 202504/202505/202604–202607 `PARTIAL`; `azure-ti/202609` `CURRENT_OPEN`; `comparable_pairs` gera 9 pares, todos consecutivos e da mesma conexão |
| Serviços com diferença de caixa são agrupados | **Atendido** | 83 → 62 chaves; `Microsoft.Compute` agora é uma linha de R$ 11.614.067,28 (era 10.543.453,38 + 1.070.613,90) |
| Cobranças globais usam `GLOBAL`; ausências desconhecidas usam `UNKNOWN` | **Atendido** | `GLOBAL` = 559.225 linhas / R$ 6.766.578,53 (todas `Microsoft.Capacity`); `UNKNOWN` = 5.294.906 linhas / **R$ 0,00** (só linhas de ajuste) |
| Nenhum total financeiro muda por normalização | **Atendido** | `verify_reconciliation` OK nas duas dimensões; delta de custo R$ 0,00 e delta de linhas 0 em 17/17 competências |
| `focus_enriched` removido sem quebrar imports ou testes | **Atendido** | `app.main` importa; registry com 13 modelos; zero referências; suíte sem regressão |

**Aceite de G1 cumprido.**

## G1.7 Arquivos para G2

| Arquivo | Ação | Conteúdo |
|---|---|---|
| `app/services/gold_materialization.py` | alterar | registrar `finops_period_coverage` como dependência de C1; usar `semantic_normalization` para as dimensões; manifesto com namespace físico |
| `app/models/gold_mart_rule.py` | alterar | corrigir docstrings que descrevem fontes como tabelas Gold |
| `app/services/gold_cubes_service.py` | remover | código morto (zero consumidores) |
| `app/services/azure_ptu_gold.py` | migrar | CTAS paralelo → caminho governado (namespace `gold`, hoje fora do manifesto) |
| `app/services/ai_usage_gold.py` | migrar | CTAS paralelo chamado de dentro do materializador governado |
| `app/services/ai_cost_materialization.py` | migrar | idem |
| `app/services/gold_builder.py` | avaliar | CTAS dentro do reprocess (`routers/etl.py:1412`) |
| namespace `dimon` | reconciliar | 5 tabelas `dest_dm_ai_*` referenciadas pela governança mas materializadas em `dimon` |
| `_tmp_vm_monthly` | renomear | existe em `gold` e `dimon`; não pode permanecer como contrato público |
| `compatibility.py` (novo) | criar | medida de divergência `dest_modelo_focus` × `resource_cost` para C11 |

## G1.8 Registro de publicação

```text
branch : novo_dev
commit : 9a141969  feat(G1): fundacao semantica, cobertura de competencia e remocao de focus_enriched
push   : origin/novo_dev  7f42db17..9a141969
testes : pytest tests/ -q  ->  517 passed, 9 failed (9 pre-existentes no HEAD limpo)
recon. : verify_reconciliation -> OK em 17/17 competencias, delta R$ 0,00 e 0 linhas
```

## G1.9 Próximo passo

**G2 não foi iniciado nesta execução.** Ordem proposta, sustentada por G0.2.4 e
G1.7:

1. manifesto de materialização com namespace físico, fontes, hash e rollback;
2. migrar os CTAS paralelos (`ai_usage_gold`, `ai_cost_materialization`,
   `azure_ptu_gold`) para o caminho governado;
3. remover `gold_cubes_service.py`; corrigir docstrings de `GoldMartRule`;
4. tratar `_tmp_vm_monthly` (dois namespaces) e a compatibilidade entre
   `dest_modelo_focus` e `resource_cost`.

---

# G2 — Governança Gold unificada e compatibilidade entre os dois livros

**Status:** concluído.

## G2.0 Regra de execução do pacote

```text
entrada     : G1 (normalizadores + cobertura) e G0.2.4 (sete caminhos de CTAS)
premissas   : resource_cost e o canonico; dest_modelo_focus atende consumidores
              legados ate o cutover; um unico ponto de publicacao governado
arquivos    : +app/services/gold_governance.py
              +app/services/cost_book_compatibility.py
              +app/models/gold_manifest.py
              -app/services/gold_cubes_service.py (removido: zero consumidores)
              ~app/services/gold_materialization.py (manifesto; auxiliar de VM fora da Gold)
              ~app/services/ai_usage_gold.py (publica pelo caminho governado)
              ~app/services/ai_cost_materialization.py (idem)
              ~app/services/azure_ptu_gold.py (idem)
              ~app/services/gold_builder.py (registra publicacao em memoria)
              ~app/services/oci_operational_monitoring.py (idem)
              ~app/models/gold_mart_rule.py (docstrings corrigidos)
              ~app/main.py (tabela gold_manifest + registro do modelo)
              +tests/test_gold_governance.py
              +tests/test_cost_book_compatibility.py
testes      : 49 novos; suite completa 569 passam, 9 falhas pre-existentes
rollback    : git revert do commit de G2. A tabela gold_manifest e aditiva e pode
              permanecer vazia; nenhum dado financeiro foi tocado.
```

## G2.1 Um único ponto de publicação

`apps/api/app/services/gold_governance.py` passa a ser o único lugar que faz
staging + swap de cubo Gold a partir de SQL. Os sete caminhos de G0.2.4 foram
tratados assim:

| Caminho | Antes | Depois |
|---|---|---|
| `gold_materialization` (governado) | swap direto em 2 pontos | swap direto + registro de manifesto nos 3 caminhos da regra |
| `ai_usage_gold` | staging + swap próprio | `publish_gold_cube` |
| `ai_cost_materialization` | staging + swap próprio | `publish_gold_cube` |
| `azure_ptu_gold` | staging + swap próprio | `publish_gold_cube` |
| `gold_cubes_service` | código morto | **removido** |
| `focus_enrichment` | código morto | **removido em G1** |
| `gold_builder` | swap por fatia, sem rastro | swap por fatia + registro no manifesto |
| `oci_operational_monitoring` | swap por fatia, sem rastro | swap por fatia + registro no manifesto |

`gold_builder` e `oci_operational_monitoring` publicam a partir de frame **já em
memória**, por fatia, e existem exatamente para evitar reler o lakehouse.
Reescrevê-los para `publish_gold_cube` custaria o scan que eles evitam, então eles
registram a publicação em vez de passar pelo publicador de SQL. A distinção é
explícita (`PUBLICATION_IN_MEMORY`).

Um teste de arquitetura impede a regressão: qualquer módulo que chame
`swap_staged_table` sem chamar `publish_gold_cube` ou `record_frame_publication`
reprova a suíte.

## G2.2 Manifesto de execução

Tabela `gold_manifest` (PostgreSQL, porque é metadata de execução — linhas
financeiras continuam no lakehouse). Cada materialização registra:

`cube_code`, `physical_table`, **`namespace`**, `contract_version`, `run_id`,
`rule_id`, `status`, `reason`, `sql_hash`, `source_tables`,
`rows_read`/`rows_written`, `financial_total_in`/`financial_total_out`,
`financial_delta`, `tolerance`, `reconciliation_status`, `currency`,
**`previous_snapshot_id`** (ponteiro de rollback), `rollback_table`, `error`
sanitizado, `started_at`/`finished_at`.

Por que a coluna `namespace` importa: em 23/09/2026 cinco regras estavam com
`materialization_status = ERROR` e `materialization_rows` preenchido, e as
tabelas existiam em **`iceberg.dimon`**, não em `iceberg.gold` — o registro de
governança apontava para uma tabela que não existia no namespace declarado.
`materialization_rows` não é evidência de publicação; o manifesto é.

### G2.2.1 Erro sanitizado

`error` passa por `sanitize_error`, que redige URL de conexão com senha,
`password=`, `account_key=`, `Bearer` e tokens de provedor. Achado da
implementação: o padrão inicial exigia `-` como separador e **não pegava
`ghp_…`**, que é justamente o tipo de token presente no `.env` deste repositório.
Corrigido para aceitar `-` ou `_`.

### G2.2.2 Reconciliação financeira no manifesto

`compute_financial_delta` distingue três estados: `OK` (delta dentro de R$ 0,01),
`MISMATCH` (acima) e `NOT_MEASURED` (um dos lados ausente). Cubo de dimensão, que
não soma dinheiro, cai em `NOT_MEASURED` — e não é reprovado por isso. Total
medido que vale zero (`delta = 0,0`) é diferente de total não medido (`None`).

## G2.3 Publicação atômica e dependência fresca

- o swap continua sendo uma transação Iceberg (`iceberg.swap_staged_table`), então
  falha preserva a versão publicada anterior. O que faltava era o registro do
  **snapshot anterior** para tornar o rollback um procedimento, não uma intenção;
- `assert_dependencies_fresh` bloqueia um dependente quando um cubo-fonte tem
  manifesto mais velho que `DEFAULT_SOURCE_MAX_AGE_HOURS` (48 h). Cubo sem
  manifesto não bloqueia: os cubos anteriores ao manifesto existem e são usados, e
  bloquear seria pior que publicar;
- só cubo Gold entra no gate. Ler `silver.resource_cost` não tem limite de idade
  declarado;
- `publish_gold_cube` verifica **moeda única no staging, antes do swap**. Somar
  BRL com USD falha e a tabela publicada nunca fica inválida.

## G2.4 Auxiliar de VM fora do namespace público

`_tmp_vm_monthly` existia em `iceberg.gold` (66.570 linhas) e `iceberg.dimon`
(0 linhas). Uma tabela chamada `_tmp_` no namespace Gold é contrato público por
posição: qualquer consumidor podia passar a lê-la e o cubo publicado dependia de
uma "temporária".

Agora é `silver.vm_monthly_history`, auxiliar de detalhe com nome que diz o que é.
As cópias legadas em `gold`, `gold` (legado) e `dimon` são removidas de forma
defensiva na mesma execução. `prepare_vm_daily` (que criava `_tmp_vm_daily`, com
**zero consumidores**) foi removido.

## G2.5 Namespace legado `dimon`

`retire_legacy_namespace_copy` só remove a cópia legada **depois** de confirmar
que a versão canônica existe: não se troca "duas cópias" por "nenhuma".

Verificação real (dry run) em 23/09/2026:

| tabela | canônica em `gold` | cópia em `dimon` | decisão |
|---|---|---|---|
| `_tmp_vm_monthly` | sim | sim | removível |
| `dest_dm_ai_consumption_daily` | **não** | sim | **preservada** |
| `dest_dm_ai_license_snapshot` | **não** | sim | **preservada** |
| `dest_dm_copilot_usage_daily` | **não** | sim | **preservada** |
| `dest_dm_copilot_license_snapshot` | **não** | sim | **preservada** |
| `dest_dm_copilot_user_period` | **não** | sim | **preservada** |

**Achado material (D15):** os cinco cubos auxiliares de IA/Dashboards não existem
em `iceberg.gold`. Os endpoints que os leem apontam para
`iceberg.{GOLD}.dest_dm_ai_*` (por exemplo
`routers/ai_provider_dashboards.py:33-34` e `routers/ai_copilot.py:35-36`), e as
tabelas vivem **apenas** em `iceberg.dimon`. O status `ERROR` das regras 17–21
não era falso positivo: a publicação desses cubos está falhando desde antes do
baseline. Não corrigido em G2 porque republicar move 1,7 M linhas — é um item
próprio, com janela e verificação.

## G2.6 Compatibilidade entre os dois livros

`cost_book_compatibility.py` mede a divergência por competência entre
`resource_cost` (canônico) e `dest_modelo_focus` (legado). Ele **não decide** qual
está certo: a ordem das fontes de verdade decide, e o canônico é `resource_cost`.

Medição real em 23/09/2026 (`FULL OUTER JOIN`, para competência presente em só um
livro não desaparecer):

| competência | canônico (linhas) | legado (linhas) | razão de linhas | delta de custo | material |
|---|---:|---:|---:|---:|---|
| 202504–202511 | 434.954 a 3.763.016 | **ausente** | — | — | não |
| 202512 | 823.720 | 3.577.709 | 4,34× | +R$ 3.977.624,00 | sim |
| 202601 | 790.406 | 3.611.104 | 4,57× | +R$ 3.970.502,66 | sim |
| 202602 | 693.389 | 3.291.347 | 4,75× | +R$ 5.535.291,23 | sim |
| 202603 | 683.474 | 3.709.750 | 5,43× | +R$ 6.607.321,43 | sim |
| 202604–202607 | 51 / 1 / 1 / 3 | ~3,5–4,0 M | 70.000× a 1.341.181× | +R$ 10,2 M a 18,8 M | sim |
| 202608 | **ausente** | 4.711.931 | — | — | não |
| 202609 | 548.606 | 2.338.021 | 4,26× | +R$ 5.153.320,26 | sim |

Duas conclusões que mudam o cutover:

1. **os livros não são comparáveis linha a linha.** O legado tem 4,3× a 5,4× mais
   linhas no mesmo mês. `dest_modelo_focus` é o FOCUS do provedor; `resource_cost`
   é o modelo canônico com o grão declarado. Um total diferente entre as telas não
   é erro de um dos lados — é diferença de modelagem que precisa ser explicada
   antes do cutover;
2. **`dest_modelo_focus` é o único livro com 202608** (4.711.931 linhas) e o único
   ativo em 202604–202607. `resource_cost` para em 202607 com 3 linhas. Trocar a
   tela antes de `resource_cost` receber 202608 mostraria um mês faltando.

Por isso o cutover não é só apontar a tela nova: exige popular `resource_cost`
para as competências que só o legado tem, e explicar a diferença de grão em C11.

## G2.7 Docstrings e código morto

- `GoldMartRule` e `GoldMartRuleSource`: os docstrings chamavam as fontes de "Gold
  tables". As fontes são **Silver** detalhada (`resource_cost`,
  `dest_modelo_focus` e o espelho `_planning`), e o cruzamento é o que o
  materializador produz. O texto na camada errada sustentava a suposição de um
  cruzamento que nunca existiu;
- `gold_materialization` (docstring do módulo): mesma correção, mais a explicação
  do caminho único de publicação;
- `gold_cubes_service.py` removido (173 linhas, zero consumidores);
- `prepare_vm_daily` removido (criava `_tmp_vm_daily`, zero consumidores).

## G2.8 Testes

| Arquivo | Testes | Cobre |
|---|---:|---|
| `tests/test_gold_governance.py` | 38 | hash, dependências, sanitização de erro (incluindo `ghp_`), delta financeiro, manifesto, freshness, falha preserva o anterior, moeda mista, guarda de arquitetura, namespace legado |
| `tests/test_cost_book_compatibility.py` | 11 | delta assinado, tolerância, base zero, ausência de lado, conversão de tipo do Trino, forma da linha |
| **total novo** | **49** | |

Suíte completa: **569 passam, 9 falham**. As mesmas 9 falhas pré-existentes de G1
(confirmadas em `git stash` no HEAD limpo). Nenhuma regressão.

## G2.9 Aceite de G2

| Critério | Situação | Evidência |
|---|---|---|
| Falha conserva cubo anterior | **Atendido** | `test_publish_failure_preserves_previous_table` prova swap ausente + staging removido + manifesto `FAILED` |
| Manifesto prova fontes e totais | **Atendido** | `gold_manifest` criada e escrita de ponta a ponta; `sql_hash`, `source_tables`, totais, delta e `previous_snapshot_id` |
| Busca no código não encontra consumidor do caminho desativado | **Atendido** | `gold_cubes_service` e `focus_enrichment` removidos; zero referências |
| Testes de materialização e rollback passam | **Atendido** | 49 testes novos; guarda de arquitetura impede novo publicador sem manifesto |
| Um único executor governado e observável | **Atendido com exceção justificada** | `gold_builder` e `oci_operational_monitoring` publicam frame em memória por fatia e registram manifesto |

**Aceite de G2 cumprido.**

## G2.10 Próximo passo

**C1–C3 não foram iniciados nesta execução.**

Antes de materializar C1–C3, dois itens que G2 revelou:

1. **D15 — republicar os cinco cubos de IA em `iceberg.gold`.** É pré-requisito de
   qualquer cutover da aba `dashboards-ia`; hoje os endpoints leem tabela ausente;
2. **Cobertura temporal do canônico.** `resource_cost` precisa receber 202608 (e as
   competências operacionais de 202604–202607) antes de a tela nova substituir a
   antiga, sob pena de mostrar um mês faltando.

Ordem proposta:

1. `finops_period_coverage` materializada a partir de `period_coverage.collect_coverage`;
2. C1 `finops_cost_monthly` com as dimensões de `semantic_normalization`;
3. C2 a partir de C1, com join 1:1 provado contra `resource_inventory`;
4. C3 por `driver_type`, reconciliando com o delta de C1;
5. só então o cutover, com a divergência de grão publicada em C11.
