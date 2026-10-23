# 10 — FinOps Intelligence, Dimon & Visão Contábil (lógica e caminhos para IAs)

Guia de contexto para agentes de IA que precisem entender, estender ou depurar o
**FinOps Intelligence** (`/dimon-dashboard`), a camada Dimon (pré-agregações) e a
**Visão Contábil** (pares Realizado × Provisão do Forecast Accuracy).

> Leia antes: [00-overview](00-overview.md), [01-architecture](01-architecture.md),
> [02-data-model](02-data-model.md), [04-api-reference](04-api-reference.md),
> [08-ui-design-reference](08-ui-design-reference.md).

---

## 1. O que é

**FinOps Intelligence** é um cockpit executivo pré-agregado, mensal, construído sobre
tabelas **Dimon** (namespace Iceberg `dimon`, pré-agregadas a partir da Gold FOCUS).
A página é um único arquivo React com abas; cada aba consulta a Dimon por um endpoint
de agregação e desenha KPIs/gráficos/tabelas.

Abas atuais (valor em `?tab=`):

| Aba | `value` | Conteúdo |
|-----|---------|----------|
| Visão Executiva | `visao-executiva` | ExpectedView embutido (KPIs, RF chart, quadro, AcksWidget) — padrão |
| Visão Geral | `geral` | KPIs totais, evolução mensal, mix, top diretorias/clouds/serviços, e-mail executivo |
| Evolução | `evolucao` | run rate, What Changed, heatmap, top desvios MoM |
| Organização | `organizacao` | tabela por níveis de departamento |
| Serviços & Regiões | `servicos` | top serviços/categorias/subcategorias/SKUs/regiões/subcontas + heatmap |
| Investigação | `investigacao` | pareto, anomalias, insights |
| Decisão | `decisao` | 13 widgets de decision intelligence (matriz, waterfall, drivers…) |
| Visão Contábil | `contabil` | pares Realizado X → Provisão X+1 (Forecast Accuracy) + detalhe por centro de custo + export SAP |
| Drilldown | `drilldown` | drill linear por cadeia de dimensões |
| Desligue Programado | `desligue` | saving por desligue de VMs Azure, exceções e custo de exceção |

A aba padrão é `visao-executiva`; `/expected-view` redireciona para
`/dimon-dashboard?tab=visao-executiva`.

---

## 2. Caminhos de arquivos (mapa rápido)

| Assunto | Arquivo |
|---------|---------|
| Página inteira (abas, widgets, export) | `apps/web/src/pages/DimonDashboard/DimonDashboard.tsx` |
| API de consulta da Dimon | `apps/api/app/routers/dimon.py` |
| Materialização da Dimon (regras → tabelas) | `apps/api/app/services/dimon_materialization.py` |
| Modelo da regra Dimon (PostgreSQL) | `apps/api/app/models/dimon_rule.py` |
| Forecast / Forecast Accuracy (runs, pivot, months) | `apps/api/app/routers/forecast.py` |
| Motor de forecast (gera `result_run_*`) | `apps/api/app/services/forecast_service.py` |
| Modelos de forecast (config/run/ack) | `apps/api/app/models/forecast.py` |
| Worker (processa fila `etl_task`) | `apps/api/worker.py` |
| Cliente da API no frontend | `apps/web/src/services/api.ts` |
| Padrão visual das páginas | `docs/ai/08-ui-design-reference.md` |

Regra de ouro: **PostgreSQL guarda só metadata/controle; dados financeiros vivem em
Iceberg/Parquet consultado via Trino** (`execute()` de `app/services/trino_service.py`).

---

## 3. Dados — da Gold à Dimon

### Fontes (Gold, namespace `gold`)

- `dest_modelo_focus` — **realizado** (31,5M linhas, 181 colunas). Tem `tipo`, `_billing_period`,
  `cloud_origem`, `departamento_nivel_1..7`, `servicename`, `servicecategory`, `servicesubcategory`,
  `skumeter`, `subaccountname`, **`centro_custo`**, **`regionname`**, `custom_fields` (JSON), etc.
- `dest_modelo_focus_planning` — **forecast/planejamento** (espelho, ~20 colunas). **NÃO tem**
  `centro_custo` nem `regionname`; usa `period` em vez de `_billing_period`.

### Dimon (namespace `dimon`)

Tabelas pré-agregadas (`dest_dm_cost_*`), criadas por **regras** (`DimonRule`):
`visao-completa`, `custo_projeto`, `custo_notebooklm`, `copilot-custo`.
Cada regra declara `gold_tables` (Gold + planning), `dimensions` (GROUP BY) e `measure_columns`.
A materialização faz `UNION ALL` das fontes (projetando as dimensões; fonte sem a coluna projeta
`NULL`, exceto via lookup) e agrega com `SUM`.

Colunas da `dest_dm_cost_visao_completa` (após o reprocess com `centro_custo` e `regionname`):

- **Dimensões:** `_billing_period` (YYYYMM), `period`, `tipo` (`realizado` | `forecast` | `rateio`),
  `cloud_origem`, `departamento`, `departamento_nivel_1..7`, `servicecategory`, `servicename`,
  `servicesubcategory`, `skumeter`, `subaccountname`, **`centro_custo`**, **`regionname`**,
  `chargecategory`, `source_connection_id`, `rateio_medida`.
- **Medidas:** `realizado`, `billedcost`, `consumedquantity` (+ alias lógico `quantity` → `consumedquantity`).

### Lookup de `centro_custo` para o forecast

O planning não tem `centro_custo`. Na materialização, quando a Dimon inclui `centro_custo` e uma
fonte não possui a coluna (mas tem `subaccountname`), a fonte é unida a uma CTE
`_dimon_centro_map` (subaccountname → max(centro_custo), derivada do Gold) e projeta
`cm.centro_custo`. O mesmo padrão pode ser estendido para outras dimensões ausentes no planning.

### Como adicionar uma dimensão nova à Dimon

1. Adicionar a coluna em `_ANALYTIC_DIMENSIONS` (`apps/api/app/services/dimon_materialization.py`)
   **e** em `rule.dimensions` de cada regra — **as regras têm `dimensions` explícitas**; o default
   só vale para regras sem lista configurada.
2. Atualizar as regras: `PUT /dimon/rules/{id}` com `{"dimensions": [...]}` (lista completa;
   a atualização **substitui** a lista).
3. Reprocessar (ver abaixo). Validar com `GET /dimon/info?table=dest_dm_cost_visao_completa`.

### Reprocessamento da Dimon

- `POST /dimon/reprocess` `{}` enfileira `dimon_reprocess` (409 se já houver um ativo) e o
  **worker** executa.
- **Atenção:** o worker **não recarrega código Python entre tasks** (importa `dimon_materialization`
  uma vez). Após mudar código de materialização, ou reinicie o worker, ou rode a materialização
  **inline** com `apps/api/reprocess_dimon_centro_custo.py`
  (`cd apps/api && ../../.venv/bin/python reprocess_dimon_centro_custo.py`), que cria a própria
  task e materializa todas as regras ativas. O script pré-importa todos os models (necessário para
  o metadata do SQLAlchemy; sem isso o flush falha com FK sem tabela).
- A agregação da `visao-completa` leva alguns minutos (~31,5M linhas × fontes).

---

## 4. API Dimon — endpoints e payloads

| Endpoint | Uso |
|----------|-----|
| `GET /dimon/info?table=` | colunas (dimension/measure), dimensões, medidas, períodos disponíveis |
| `POST /dimon/query` | agregação: `{table, rows[], measures[], filters[], row_limit}` — `GROUP BY rows` + `SUM`; `row_limit` **max 10000**; retorna `rows[]` com as chaves das colunas e das medidas |
| `GET /dimon/gold-schema?gold_table=` | DESCRIBE de uma Gold (para descobrir colunas disponíveis) |
| `POST /dimon/preview` | contagem/total de uma Gold com filtros (`filter_json.conditions[]`) |
| `PUT /dimon/rules/{id}` | atualiza regra (`dimensions` substitui a lista) |
| `POST /dimon/reprocess` | enfileira materialização de todas as regras ativas |

Exemplo de `POST /dimon/query` (detalhe contábil por cloud × centro de custo):

```json
{
  "table": "dest_dm_cost_visao_completa",
  "rows": ["cloud_origem", "centro_custo"],
  "measures": [{ "column": "realizado", "aggregation": "SUM" }],
  "filters": [
    { "column": "_billing_period", "operator": "IN", "values": ["202605"] },
    { "column": "tipo", "operator": "IN", "values": ["realizado"] }
  ],
  "row_limit": 10000
}
```

Filtros: `IN`, `NOT_IN`, `CONTAINS`, `NOT_CONTAINS`; valores são comparados com
`lower(CAST(col AS VARCHAR))`. **`IN` com `values: []` seleciona linhas com coluna NULL/vazia**
(útil para medir materialidade de classificação ausente). Colunas inválidas → **422**
("Coluna de linha inexistente na Dimon: X"); medidas inválidas → 422 ("Medida não permitida").

---

## 5. DimonDashboard (frontend) — padrões

Arquivo único: `apps/web/src/pages/DimonDashboard/DimonDashboard.tsx` (~2.200 linhas).

### Filtros globais

```ts
type Filters = {
  months: string[];            // [] = todos (YYYYMM)
  clouds: string[]; dirs: string[]; depts: string[]; servicos: string[]; cats: string[];
  path: string[];              // caminho de departamento: path[i] → departamento_nivel_{i+1}
  deptId: number | null;
  extra: Record<string, string[]>; // filtros por coluna arbitrária (contexto da árvore de decomposição)
};
```

- `filtersOf(filters, extraFilters?)` converte `Filters` → `DimonQueryFilter[]` (months → `_billing_period`,
  path → `departamento_nivel_N IN [segmento]`, extra → coluna IN valores).
- A aba Decisão usa `filters.extra` para "Aplicar contexto ao cockpit" (trilha da árvore em dimensões
  que não são níveis de departamento).

### Queries

- `useTabQueries(table, defs, key)` dispara as defs em paralelo (`Promise.all`), cada uma
  `queryDimonTable(...).catch(() => null)` (falha → `null`, não derruba a aba). Retorna
  `{results, loading, error}`. As defs têm `{id, rows?, measures?, filters?, row_limit?, sort_column?, sort_direction?}`.
- Helper `monthLabel("202605")` → "Mai/26"; `money/compact` (BRL compacto), `pct`, `num`, `esc`.
- Medidas de agregação usadas: `realizado` (BRL), `billedcost` (USD), `quantity` (alias→consumedquantity), `*` COUNT.

### Como adicionar uma aba nova

1. Adicionar o valor em `type TabId` e na lista de validação do `initialTab` (`?tab=`).
2. Adicionar `<Tab value="..." label="..." />` no `Tabs`.
3. Adicionar o render condicional `{tab === "..." && <MeuComponente table={table} filters={filters} filterKey={filterKey} />}`.
4. Criar o componente como função no mesmo arquivo (ou importar), usando `useTabQueries` + componentes
   `SectionPanel`/`KpiCard`/`RankBars`/`ChartCard`/`LoadingBlock` já existentes.

---

## 6. Aba Decisão — os 13 widgets e suas fontes

Todas as consultas usam a Dimon `visao_completa` com `tipo`:

| Widget | Fonte (def) |
|--------|-------------|
| Executive Pulse (situação financeira agora) | `pulse` = rows `[_billing_period, tipo]` |
| FinOps Risk Index + Executive Action Center | derivado de `pulse`/`dim`/`gov3` (IA cost por keyword de servicename) |
| O que merece atenção agora? | derivado (confiabilidade, MoM, concentração, governança, driver) |
| Prioritization Matrix (Custo × Crescimento) | `dim` = rows `[d, _billing_period, tipo]` |
| Waterfall executivo de variação (MoM por dimensão) | `dim` |
| Financial Variance Bridge (Realizado→Forecast→Gap) | `pulse` |
| Cost Drivers & Contribution to Growth | `dim` + `dimCloud` |
| Anomaly & Cost Acceleration (|Z| ≥ 2) | `dim` |
| Forecast Bias & Reliability | `dim` (pares forecast×realizado por entidade) |
| Prioridade financeira (v42: gap/confiabilidade/run rate) | `dim`, `dimServ`, `pulse` |
| Governance / Data Quality (completude ponderada por custo) | `gov1..gov6` (rows `[dimensão]`, tipo=realizado) |
| Comparator (2 entidades) | `dim` |
| Decomposição hierárquica encadeada (v233 — árvore de fluxo SVG, toggle Custo/Share/MoM) | `spine{i}` (rows vazias → soma por nó do caminho) + `tree{treeStart}` (rows `[campo, _billing_period, tipo]` → top 7 filhos + MoM) |

- Seletor de dimensão `DECISAO_DIMS`: `cloud_origem`, `departamento_nivel_3..5`, `servicename`,
  `servicecategory`, `subaccountname`. (Não incluir `skumeter` — alta cardinalidade estoura o `row_limit`.)
- Custo usa `tipo='realizado'`; confiabilidade/gap usam o par `realizado` × `forecast` (mesma query,
  separada client-side por `tipo`).
- "Não informado" da Dimon é `null` — helpers tratam (`isMissingValue`).

---

## 7. Aba Visão Contábil — pares Realizado × Provisão

Fluxo (o que uma IA precisa saber para mexer aqui):

1. **Pares (seleção vertical, um abaixo do outro):** `GET /forecast/accuracy/months?config_id=9`
   retorna, por mês, o snapshot **simulado mais recente** (`run_id`, `target_month`, `forecast_value`,
   `actual_value`, `closed`). O frontend ordena e monta pares consecutivos
   **Realizado X → Provisão X+1** (realizado = `actual_value` de X; provisão = `forecast_value` de X+1).
   O snapshot é **imutável** (dados só mudam quando o lote é reprocessado).
2. **Detalhe (por cloud × centro de custo):**
   - **Provisão** (mês X+1): `POST /forecast/runs/{run_id}/history/pivot` com
     `rows: ["cloud_origem","centro_custo"]`, `filters: {tipo:{operator:"in",value:["forecast"]},
     forecast_month:{operator:"in",value:[X+1]}}` — **`row_limit` máximo é 100** (acima disso → 422).
   - **Realizado** (mês X): `POST /dimon/query` com `rows:["cloud_origem","centro_custo"]`,
     `filters: _billing_period=X, tipo=realizado`.
   - Junta os dois client-side por (cloud, centro de custo).
3. **Export pedido SAP** (`Export pedido SAP`): gera XLSX via `xlsx` (SheetJS) — aba **Resumo**
   (totais por cloud) + **uma aba por cloud** (linhas por centro de custo com Realizado, Provisão,
   Δ, % prov/real) + linha TOTAL. **Provisório** — quando o modelo SAP for definido, ajustar
   `exportPedidoSap` (função dentro do componente `VisaoContabil`).

### Por que o snapshot tem `centro_custo` (preparação feita)

- A config de forecast **9** ("Forecast padrão FinOps") ganhou `centro_custo` nas `dimensions`
  (via `PUT /forecast/configs/9` — **atenção:** o PUT substitui todos os campos; mandar o body completo).
- O **reprocess full** regenerou os runs simulados com a dimensão nova:
  `GET /forecast/accuracy/available-months?config_id=9` → `POST /forecast/accuracy/reprocess`
  `{"config_id":9,"months":[...]}` → monitorar `GET /etl/status/{task_id}`.
  Cada mês gera um run simulado (`simulated_as_of` = dia 2 do mês), e o
  `/accuracy/months` passa a usar o snapshot mais recente por mês.

---

## 8. Forecast Accuracy — entendimento necessário

- `ForecastRun` = **snapshot imutável de auditoria**: guarda `config_snapshot`, `result_table`
  (tabela Iceberg `forecast.result_run_*`), `cutoff_date`, `simulated_as_of`.
- A `result_table` contém **apenas `tipo='forecast'`** (`forecast_month`, `forecast_value`, dimensões).
  O **realizado** ("actual side") é lido da fonte viva (Dimon/Gold) no momento da comparação —
  por isso a Visão Contábil busca realizado na Dimon e provisão no snapshot.
- `/forecast/accuracy/months` tem **cache de 5 min** (expira sozinho após um reprocess).
- Endpoints úteis: `GET /forecast/runs`, `GET /forecast/runs/{id}/history/metadata` (schema + snapshot),
  `POST /forecast/runs/{id}/history/pivot`, `POST /forecast/runs/{id}/history/comparison`,
  `GET /forecast/accuracy/months|summary|dimensions|delta|desvios`,
  `POST /forecast/accuracy/reprocess`, `GET /forecast/accuracy/available-months`.
- Para incluir uma dimensão nova no snapshot (ex.: `regionname`): adicionar na config de forecast
  (body completo no PUT) e reprocessar o accuracy full.

---

## 9. Armadilhas conhecidas (checklist para IAs)

- `POST /forecast/runs/{id}/history/pivot`: **`row_limit` ≤ 100** (422 acima).
- `PUT /forecast/configs/{id}` e `PUT /dimon/rules/{id}`: **substituem** os campos enviados —
  mandar o body completo (configs) / a lista completa de dimensions (rules).
- Medida `quantity` no frontend → resolvida no backend para `consumedquantity` (alias em
  `_DIMON_COLUMN_ALIASES`). Não remover o alias se for trocar o nome no frontend.
- `regionname` e `centro_custo` **só existem na Dimon após reprocessamento** com a dimensão.
- Valores ausentes na Dimon vêm como `null` (não "Não informado") — usar `isMissingValue`.
- Meses sempre `YYYYMM`; **provisão = forecast do mês seguinte** (regra de negócio da Visão Contábil).
- **Worker não recarrega código** entre tasks: após mudanças em `dimon_materialization.py` /
  `forecast_service.py`, reiniciar o worker ou rodar a materialização inline
  (`apps/api/reprocess_dimon_centro_custo.py`).
- `/forecast/accuracy/months` tem cache de 5 minutos.
- O 422 de `/dimon/query` aparece no console mesmo com `.catch(() => null)` (axios loga antes) —
  não é erro fatal, mas indica coluna/medida inválida; corrigir a def.
- `filters.extra` (filtros por coluna arbitrária) entra no SQL como `coluna IN (valores)` —
  colunas devem existir na Dimon.
- Eslint não está instalado no repo (npm cache EPERM) — validar com `npx tsc -b --noEmit` e
  `npx vite build` (em `apps/web`).

---

## 10. Novas IAs — "os caminhos" por tipo de tarefa

| Tarefa | Onde mexer |
|--------|------------|
| Adicionar/ajustar widget numa aba do FinOps Intelligence | `apps/web/src/pages/DimonDashboard/DimonDashboard.tsx` (componente da aba; defs do `useTabQueries`) |
| Adicionar aba nova | TabId + `initialTab` + `<Tab>` + render condicional (seção 5) |
| Criar coluna nova na Dimon (ex.: outra dimensão do Gold) | `_ANALYTIC_DIMENSIONS` + `PUT /dimon/rules/{id}` + reprocessar (seção 3) |
| Consultar dados de custo agregados | `POST /dimon/query` (payload na seção 4) |
| Ver colunas disponíveis numa Gold/Dimon | `GET /dimon/gold-schema?gold_table=` / `GET /dimon/info?table=` |
| Entender/mexer no Forecast Accuracy | `apps/api/app/routers/forecast.py` + `apps/api/app/services/forecast_service.py` (seção 8) |
| Reprocessar Forecast Accuracy full | `GET /forecast/accuracy/available-months` → `POST /forecast/accuracy/reprocess` → `GET /etl/status/{task_id}` |
| Detalhe Realizado × Provisão por centro de custo | pivot do run (`history/pivot`, row_limit ≤ 100) + Dimon (seção 7) |
| Mudar o export SAP (novo modelo) | `exportPedidoSap` dentro de `VisaoContabil` (seção 7) |
| Depurar 422 de `/dimon/query` | conferir coluna/medida contra `GET /dimon/info?table=` (seção 4) |
| Reprocessar Dimon sem depender do worker | `cd apps/api && ../../.venv/bin/python reprocess_dimon_centro_custo.py` |
| Padrão visual | `docs/ai/08-ui-design-reference.md`; dashboards usam `SectionPanel`/`KpiCard`/`RankBars` (unidades compactas Mi/Bi/Tr) |

**Sequência típica ao alterar dados → UI:** (1) ajustar materialização/regra → (2) reprocessar
Dimon ou accuracy → (3) validar via `curl` nos endpoints → (4) ajustar o frontend → (5)
`npx tsc -b --noEmit` + `npx vite build` (em `apps/web`).
