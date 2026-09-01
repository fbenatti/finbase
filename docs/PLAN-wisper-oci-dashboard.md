# PLAN — Reconstrução do Dashboard "Wisper — OCI AI" (`/visoes-consumo-ia`)

> **Objetivo:** transformar a aba Wisper em um painel executivo correto e rico, corrigindo
> divergências de custo, capacidade zerada, inventário e monitoramento. Este documento foi
> escrito para ser executado por outra IA/desenvolvedor sem contexto prévio: contém o
> diagnóstico com evidências reais (consultadas em 2026-08-31), a causa raiz de cada
> sintoma, o contrato de dados proposto e o passo a passo de implementação com queries de
> validação e critérios de aceite.

---

## 0. STATUS DE EXECUÇÃO (atualizado em 2026-08-31, implementação em andamento)

| Fase | Status | Observações |
|---|---|---|
| Fase 0.1 — `read_destination` + coletor OCI | ✅ Concluída | `iceberg_service.py` (`read_destination` agora lê a tabela; nunca retorna `None`). `oci_operational_monitoring.py`: defensivo contra `None`, `collected_at`, `compartment_name`, snapshot de inventário (descarta histórico), log com traceback no scheduler. |
| Fase 0.2 — Recovery + backfill 90d | ✅ Concluída | `scripts/rebuild_oci_operational_gold.py` criado e executado (precisa `DATABASE_URL_SYNC=postgresql://finops:finops@localhost:5433/finops`). Gold passou de 1.645 → **122.107 linhas**, métricas de 02/06 a 31/08 (**90 dias**), 0 erros. |
| Fase 0.3 — Scheduler | ✅ Código corrigido | `scheduler_service.py` loga traceback. Job horário voltará a funcionar com o `read_destination` corrigido (próximo ciclo às :18). |
| Fase 1 — Backend payload v2 | ✅ Concluída | `wisper_dashboard.py` reescrito: fonte = gold FOCUS (grão dia, subaccount, SKU); mês fechado 202607 = R$ 187.717,79; GPU-h = 9.242,09; AI unit-h = 1.504,17; inventário escopado (2 DACs = 32 units, 3 endpoints, 33 computes); `unit_economics`, `insights` (step-down −57,4% em jul), `data_quality`. Endpoint validado por curl. |
| Fase 2 — Frontend | ✅ Concluída | `WisperDashboard.tsx` redesenhado (executivo): KPIs, composição por bucket, série diária, capacidade, inventário agregado, subprojetos, SKUs com %, monitoramento com janela real, data-quality chips. Vite compila (HTTP 200). |
| Fase 3 — Forecast | ⚠️ Pendente de decisão | Forecast 202608 = R$ 56,6k < parcial 67,7k continua errado; o payload **exibe o alerta** (`forecast_mes_aberto_coerente=false`). Mudar a config de forecast (id=9) afeta todas as visões — decidir com o usuário (baseline pós step-down, ~R$ 115–125k/mês). |
| Fase 4 — Enriquecimentos | 🟡 Parcial | Já entregue: nome do compartment no inventário, custo/MM tokens amortizado, detecção de step-down, latência p50. Pendente: `resourcename` nas linhas OCI da gold (join com inventário/bronze). |
| Fase 4b — **Nova fonte: GPU utilization via agente** | ✅ Código pronto, 🟡 deploy pendente | Como a OCI não expõe GPU para `VM.GPU.*`, foi criado `scripts/oci-gpu-agent/` (agente `nvidia-smi` → OCI Monitoring namespace `custom_gpu` + instalador systemd + README com dynamic group/policy). Coletor (`oci_operational_monitoring.py`) já lê `custom_metric_namespaces` da config da conexão; dashboard já resolve `gpu_utilization` de ambas as fontes. **Falta**: instalar o agente nas VMs.GPU e adicionar `custom_metric_namespaces` na config da conexão OCI (SQL no README). |
| Fase 4c — **Shapes nos SKUs + inventário agregado** | ✅ Concluída (com dependência IAM) | SKUs agora trazem `shapes`, `unit_price`, `region`, `resources`, `mom_delta_pct`, `list_cost`/`effective_cost`/`discount_pct`; frontend com coluna Shape e linha "mais" expansível. Inventário executivo sem nomes/modelos (agregado por shape). **Dependência**: shapes de GPU compute (B95909) exigem `compute:listInstances` nos compartments whisper/llm (hoje 404; alerta `shapes_gpu_compute` no dashboard). AI units (B108085) usam fallback da frota de DACs. |

### Correções ainda necessárias (decisão do usuário)
1. **Forecast**: definir baseline pós-step-down e reprocessar (Fase 3) ou manter alerta.
2. **GPU utilization**: OCI não expõe para `VM.GPU.A10.1` — **solucionado com nova fonte de dados** (`scripts/oci-gpu-agent/`, agente nvidia-smi → OCI Monitoring `custom_gpu`). Deploy pendente nas instâncias + `custom_metric_namespaces` na config da conexão. Para Dedicated AI Clusters, usar eficiência faturada (tokens ÷ GPU-h) — já calculável.
3. **Latência média**: com backfill, a média ficou dominada por outliers (119s); o KPI agora usa **p50 (18,3 ms é o dado real)**.
4. **Success rate** ficou em 23,7% (2.242 erros vs 2.940 invocações) — confirmar com o time de plataforma se o OCI conta throttling/429 como ServerErrorCount.

---

## 1. Mapa da arquitetura atual

### 1.1 Cadeia de dados

```
OCI (cost/usage reports + Monitoring API + GenAI API)
   │
   ├─[financeiro] oci_extractor (FOCUS) ──► iceberg.gold.dest_modelo_focus
   │                                            │  (filtro SubAccountName ~ whisper|llm|poc-stt
   │                                            │   via classification_rule, scripts/apply_oci_classification.py)
   │                                            ▼
   │                                    iceberg.dimon.dest_dm_cost_custo_wisper
   │                                            (inclui linhas tipo='forecast' geradas pelo forecast_service)
   │
   └─[operacional] oci_operational_monitoring.collect() ──► iceberg.bronze.raw_oci_prod_operational
        (scheduler: cron minuto 18, hours=3)                 + swap completo ──► iceberg.gold.dest_oci_operational_metrics

OBS: iceberg.bronze.raw_oci_prod (CSV legado de custo) está STALE — max _billing_period = 202509.
NÃO é a fonte do custo atual do Wisper. A verdade financeira atual é a gold FOCUS.
```

### 1.2 Código envolvido

| Camada | Arquivo | Papel |
|---|---|---|
| Backend serviço | `apps/api/app/services/wisper_dashboard.py` (64 linhas) | Monta o payload; **a maioria dos bugs está aqui** |
| Backend rota | `apps/api/app/routers/azure_ptu.py:1588` (`POST /visoes-consumo-ia/dashboard/wisper`, prefixo definido na linha 61) | Delega a `wisper_dashboard.load()` |
| Coletor operacional | `apps/api/app/services/oci_operational_monitoring.py` | Coleta inventário + métricas OCI; **job quebrado** |
| Scheduler | `apps/api/app/services/scheduler_service.py:184` (`_oci_operational_hourly_job`, registrado na linha 440) | Roda `collect(conn, hours=3)` a cada hora |
| Forecast | `apps/api/app/services/forecast_service.py` (`run_forecast`, config id=9 "Forecast padrão FinOps", measure=`realizado` sobre `dest_modelo_focus`) | Gera linhas `tipo='forecast'` na dimon |
| Frontend página | `apps/web/src/pages/AzurePTU/WisperDashboard.tsx` (26 linhas) | Renderiza tudo; precisa de redesign |
| Frontend container | `apps/web/src/pages/AzurePTU/AzurePtuOps.tsx:96-100` (aba `view === 8`) | Hospeda o componente |
| Frontend API | `apps/web/src/services/api.ts:64` (`getWisperDashboard`) | POST para o endpoint |
| Rota | `apps/web/src/App.tsx:51` (`/visoes-consumo-ia`) | — |

### 1.3 Escopo real do projeto Wisper (descoberto nos dados)

O Wisper é composto por **4 compartments OCI** (FOCUS `SubAccountName`/`subaccountid`), definidos
pela regra de classificação `SubAccountName ~ whisper|llm|poc-stt`:

| SubAccount | OCID | Custo 202607 (R$) |
|---|---|---|
| `whisper` | `ocid1.compartment.oc1..aaaaaaaaw5c2ho2wg4yyo76lymxxxaytbbpq7anssuzhshf3qu2erihg5pla` | 114.624,62 |
| `llm` | `ocid1.compartment.oc1..aaaaaaaavlqagjh7csvqmlvvxgjfn2uazugkxwafmg7mqaw5akfmxqfprusa` | 73.075,88 |
| `dev-whisper-poc` | `ocid1.compartment.oc1..aaaaaaaavffqgtwr2p434brd5atxb3mbuxrx3hzkj372mgvzc3asv5r42v6a` | 17,29 |
| `poc-stt` | `ocid1.compartment.oc1..aaaaaaaava3c3vet7c47ttyy3rakcosxaqleo5rmbwit43aie336nifpvvpa` | 0,01 |

O inventário GenAI (Dedicated AI Clusters e endpoints) vive em **outro compartment**:
`ocid1.compartment.oc1..aaaaaaaapichbvzqi6rkxxco7vpgf3krq6cmorjplnqy4mjedzkftuvtpqsa`
(o coletor não grava o *nome* do compartment — gravar; ver Fase 0).

Inventário GenAI real do projeto (gold `dest_oci_operational_metrics`):

| Recurso | Tipo | Shape/Modelo | Quantidade |
|---|---|---|---|
| `whisper-large-v3-turbo-a10` | dedicated_ai_cluster | **A10_X1** | **20 units** (= 20 GPUs A10) |
| `llama-scout-prod` | dedicated_ai_cluster | **LARGE_GENERIC_V2** | **12 units** |
| `ep-vvia-gru-stt-001` | endpoint | modelo importado (STT) | — |
| `ep-vvia-gru-001` / `ep-vvia-gru-002` | endpoint | `ocid1.generativeaimodel...toptemq` | — |

---

## 2. Verdade financeira (baseline para validação)

Totais da gold FOCUS (= dimon Wisper, conferem centavo a centavo):

| Período | Realizado (R$) | Observação |
|---|---|---|
| 202512 | 670.692 | |
| 202601 | 669.417 | |
| 202602 | 605.829 | |
| 202603 | 604.561 | |
| 202604 | 293.431 | queda (mudança de arquitetura) |
| 202605 | 287.789 | |
| 202606 | 262.951 | |
| **202607** | **187.718** | **mês de referência do usuário** |
| 202608 | 67.716 | **parcial** (mês aberto, até ~28/08) |
| 202608 forecast | **56.641** | ← valor errado exibido como "Custo total" |

Decomposição 202607 (realizado, por SKU):

| SKU | Serviço | Subcategoria | Custo (R$) | Quantidade |
|---|---|---|---|---|
| B95909 | COMPUTE | GPU Hours | 74.783 | **9.242 GPU-h** (26 recursos) |
| B108085 | GENERATIVE_AI | AI unit per Hour | 73.026 | **1.504 AI unit-h** (1 recurso, us-chicago-1) |
| B97385 | COMPUTE | GB Hours (RAM) | 16.421 | 2.029.440 GB-h |
| B97384 | COMPUTE | OCPU Hours | 15.395 | 126.840 OCPU-h |
| B91628 | OBJECTSTORE | GB Months | 3.124 | |
| demais | DATABASE, VPN, BLOCK_STORAGE, OKE, LB, Functions... | | ~4.900 | |

**Fatos executivos descobertos (usar no storytelling do dashboard):**

1. **Step-down em 13–14/07/2026**: custo diário cai de ~R$ 8.864/dia para ~R$ 3.778/dia.
   GPU Hours: 648 GPU-h/dia (≈27 GPUs médias, 26 recursos) até 12/07 → 24 GPU-h/dia
   (1 GPU, 1 recurso) a partir de 14/07. Desprovisionamento massivo de GPUs de compute.
2. O gasto de IA hoje é dominado por **hospedagem dedicada** (GPU Hours B95909 +
   AI unit-hours B108085 ≈ R$ 147,8k de R$ 187,7k em julho = **79% do total**), não por
   uso variável por token.
3. Run-rate atual (pós 14/07): ~R$ 3,8k/dia ⇒ ~R$ 117k/mês — ou seja, **até o forecast de
   agosto (R$ 56,6k) está subdimensionado** frente ao run-rate.

---

## 3. Diagnóstico: sintoma → causa raiz (com evidência)

### Bug A — "Custo total R$ 56.641" errado (deveria mostrar realizado)

- **Causa:** `wisper_dashboard.py:32-40`. `open_period` = mês corrente (202608). Para o
  período corrente, se existir **qualquer** linha `forecast`, o KPI usa **somente** o
  forecast (`chosen="forecast"`), ignorando o realizado parcial de R$ 67.716. O forecast de
  202608 vale R$ 56.641 — e nem isso é comparável ao realizado de julho (R$ 187.718).
- **Agravante:** o forecast gerado é materialmente errado (ver Bug D).
- **Correção:** para o mês aberto, exibir **realizado parcial + forecast restante** (a mesma
  semântica já documentada em `analysis.py:1824` — "realizado parcial + forecast do
  restante"), e nunca substituir um pelo outro. KPI principal do cabeçalho deve ser o
  **último mês fechado realizado** (202607 = R$ 187.718), com o mês corrente como
  card separado "Mês em curso (parcial + projeção)".

### Bug B — "Capacidade faturada" toda zerada (GPU Hours B95909: 0 etc.)

- **Causa:** as linhas `tipo='forecast'` na dimon têm **`skumeter = NULL` e
  `consumedquantity = 0`** (verificado: 29 linhas de forecast de 202608, todas com qtd 0).
  Como o Bug A escolhe o forecast para o período corrente, o loop de SKUs/capacidade
  (`wisper_dashboard.py:34-38`) itera só sobre linhas de forecast → tudo zero ou
  "Não informado".
- **Evidência:** em 202607 realizado, B95909 = 9.242 GPU-h e B108085 = 1.504 unit-h —
  os dados **existem**.
- **Correção:** capacidade faturada sempre calculada sobre `tipo IN ('realizado','rateio')`
  do mês de referência **fechado** (e, opcionalmente, do mês parcial marcado como parcial).

### Bug C — KPI diverge do gráfico

- **Causa:** o gráfico mensal soma realizado+rateio por mês (correto, mostra ~R$ 604k em
  março etc.), enquanto o KPI segue a lógica do Bug A. São duas fontes de verdade na mesma
  página.
- **Correção:** uma única função de seleção de período/valor compartilhada por KPIs,
  gráfico e tabelas (ver contrato §5).

### Bug D — Forecast do Wisper subdimensionado e sem dimensões

- **Evidência:** forecast 202608 = R$ 56.641 vs. realizado parcial 202608 = R$ 67.716 (o
  forecast do mês inteiro já é menor que o parcial!) e vs. run-rate pós-step-down
  (~R$ 117k/mês). GPU Hours forecast = R$ 2.913 vs. R$ 74.783 realizado em julho.
- **Causa provável:** a config de forecast (id=9) roda sobre `dest_modelo_focus` inteiro com
  o step-down de 13/07 na base; a janela recente ponderada derruba o baseline e/ou a
  regra de "dias restantes" está zerando o mês aberto (ver `forecast_service.py:1071-1077`:
  se `realized_value > 0` no mês corrente, `current_forecast_days = 0` — o forecast do mês
  corrente vira só o "restante", mas os R$ 56,6k nem chegam a isso). Diagnosticar com a
  query de validação V5 antes de mexer.
- **Correção:** revisar baseline do forecast pós-step-down (mínimo: forecast do mês aberto
  = realizado parcial + baseline_recente × dias_restantes) e **propagar `skumeter` e
  `consumedquantity` proporcional** nas linhas de forecast (hoje NULL/0), ou isolar o
  forecast por bucket sem alimentar KPIs de capacidade.

### Bug E — "Inventário OCI" horrível e fora de escopo

- **Causa 1 (escopo):** a conexão OCI (`source_connection.id=9`, code `oci-prod`) tem
  **`generative_ai_compartment_ids` com ~77 compartments** (praticamente a tenancy inteira).
  O coletor (`oci_operational_monitoring.py:99-105`) lista **todas** as instâncias de compute
  de todos esses compartments → 211 instâncias (nodes OKE genéricos, `vm-atlas-infra`,
  `RD-Team`, `DDOCI01`...) que não são do Wisper.
- **Causa 2 (modelo):** o swap da gold deduplica por
  `(timestamp, resource_id, metric_name, record_type)`; linhas de inventário têm
  `timestamp=NULL` ⇒ **acumulam recursos históricos para sempre** (há TERMINATED na lista).
- **Causa 3 (apresentação):** a UI lista nomes de máquina um a um — o usuário quer
  **quantidade × modelo**, não nomes.
- **Correção:** (i) filtrar inventário do payload para os compartments do Wisper (os 4 de
  billing + o compartment GenAI `...pichbvzq`, parametrizável); (ii) persistir snapshot de
  inventário com `collected_at` e exibir **somente o snapshot mais recente**; (iii) UI
  agregada (ver §6).

### Bug F — "Monitoramento OCI — últimos 90 dias" muito abaixo da realidade

- **Evidência:** a gold `dest_oci_operational_metrics` tem apenas **~24h de métricas**
  (2026-08-28T04:00 → 2026-08-29T03:00; 1.429 linhas). A query do dashboard filtra 90 dias
  mas só encontra 1 dia ⇒ tokens/chamadas parecem ridículos (input 106.773 tokens, 51
  invocações em "90 dias").
- **Causa 1 (job quebrado):** o job horário está falhando desde 29/08 ~03h:
  `OCI operational hourly collection failed: 'NoneType' object has no attribute 'empty'`
  (`.runtime-logs`). É `iceberg.read_destination(GOLD_TABLE)` retornando `None` em
  `oci_operational_monitoring.py:128` (a função retorna `None` em vez de DataFrame vazio
  quando a tabela não existe / falha de leitura). **A bronze continuou acumulando** (9.995
  linhas até 2026-08-31T11:00) — ou seja, o persist bronze roda antes do crash, e a gold
  ficou congelada.
- **Causa 2 (janela):** o job roda com `hours=3`; o início da série em 28/08 indica que a
  coleta operacional só começou a existir nessa data — **nunca houve backfill de 90 dias**,
  embora o OCI Monitoring retenha métricas por 90+ dias.
- **Correção:** (i) `read_destination` → tratamento de None (`old = ... or pd.DataFrame()`);
  (ii) backfill único com `hours=24*90` (cap já existe em `collect`); (iii) alarme/log de
  falha do job.

### Bug G — "Utilização GPU: Sem métrica OCI"

- **Causa:** o coletor só busca `GpuUtilization` no namespace `oci_computeagent` para
  instâncias com `".GPU"` no shape (`oci_operational_monitoring.py:103`). O inventário não
  tem **nenhuma** instância GPU de compute — as GPUs são **Dedicated AI Clusters**
  (A10_X1 ×20, LARGE_GENERIC_V2 ×12), cujas métricas não são `oci_computeagent`.
- **Correção:** consultar as métricas de DAC disponíveis no namespace `oci_generativeai`
  (e documentar se a OCI não expõe utilização de GPU para DAC — nesse caso o widget deve
  mostrar **eficiência faturada**: tokens/dia ÷ GPU-h faturadas/dia, e throughput por GPU).

### Bug H — "SKUs e componentes nada a ver"

- **Causa:** tabela montada sobre as linhas de forecast (Bug B) e sem descrição amigável —
  mostra códigos ("B95909") sem nome, sem serviço, sem % do total.
- **Correção:** tabela de SKUs do **mês fechado realizado**, colunas: SKU, descrição
  (servicename + servicesubcategory), quantidade + unidade, custo, % do total, variação vs.
  mês anterior.

### Bug I — Ausência do widget "custo por MM de tokens"

- **Causa:** depende de histórico de tokens (Bug F). Com tokens acumulados diariamente na
  gold, o widget é trivial: `custo_mes / (tokens_mes / 1e6)`. Enquanto não houver 30 dias de
  telemetria, exibir versão "período com telemetria" (custo diário médio ÷ tokens diários).
- **Nota de modelagem:** o Wisper é faturado majoritariamente por **AI unit-hour / GPU-hour**
  (capacidade dedicada), não por token — o widget deve deixar isso explícito ("custo
  amortizado por MM tokens").

---

## 4. Dados disponíveis para enriquecimento (já na base, sem nova coleta)

Fonte: `iceberg.gold.dest_modelo_focus` (181 colunas FOCUS), linhas OCI do Wisper.

| Dado | Coluna(s) | Status no Wisper |
|---|---|---|
| Custo diário | `chargeperiodstart` (grão dia confirmado) | ✅ populado — viabiliza série diária e detecção do step-down |
| Compartment | `subaccountname`, `subaccountid` | ✅ populado — drill whisper × llm × pocs |
| SKU, unidade, quantidade | `skumeter`, `consumedunit`, `consumedquantity` | ✅ populado |
| Preço unitário | `listunitprice`, `contractedunitprice`, `effectivecost`, `listcost` | ✅ — viabiliza "custo de lista vs. efetivo" (desconto) |
| Recurso | `resourceid` (394 distintos em 202607) | ✅ populado; `resourcename` e `resourcetype` = **NULL** nas linhas OCI → enriquecer |
| Região | `regionname` | ✅ (GenAI em `us-chicago-1`, resto `sa-saopaulo-1`, VPN `sa-vinhedo-1`) |
| Tags | `tags`, `x_tags`, `source_resource_group_name` | ⚠️ verificar preenchimento nas linhas OCI |
| Moeda | `billingcurrency` | ✅ BRL |
| Descrição do cargo | `chargedescription` | ❌ NULL nas linhas OCI |
| Inventário GenAI | gold `dest_oci_operational_metrics` (`unit_shape`, `unit_count`, `model_id`, `raw_json`) | ✅ 2 DACs + 3 endpoints; `raw_json` tem display_name etc. |
| Telemetria tokens/latência/erros | gold `dest_oci_operational_metrics` (record_type='metric') | ⚠️ só 24h; bronze tem até 31/08 (recovery possível) |

**Enriquecimentos propostos (sem custo de API):**
1. `resourcename`/`resourcetype`: join de `resourceid` com o inventário
   (`dest_oci_operational_metrics.raw_json`) e/ou bronze legada (`raw_oci_prod` tem
   `product/resourceid` + `tags/orcl-cloud.resource_name` + `tags/*`) — cobre pelo menos
   compute/OKE/DB.
2. Nome do compartment no inventário operacional (hoje só OCID): resolver via
   `subaccountid→subaccountname` da gold FOCUS ou `IdentityClient` na coleta.
3. Mapear endpoint → modelo → DAC (`cluster_id` já existe na tabela de inventário) para
   exibir "modelo servindo em N units de shape X".

---

## 5. Contrato de dados proposto (payload v2 do endpoint)

`POST /visoes-consumo-ia/dashboard/wisper` — resposta nova (manter chaves antigas até a UI
migrar, ou versionar como `/dashboard/wisper/v2`):

```jsonc
{
  "available": true,
  "generated_at": "2026-08-31T12:00:00Z",
  "reference": {
    "closed_period": "202607", "closed_label": "jul/2026",
    "current_period": "202608", "current_label": "ago/2026",
    "current_days_elapsed": 28, "current_days_in_month": 31
  },
  "executive": {                       // cabeçalho executivo — mês FECHADO
    "total_closed": 187717.79,
    "total_prev_closed": 262950.82,
    "mom_delta_pct": -28.6,
    "run_rate_daily": 3778.0,          // média móvel 7d do realizado diário
    "monthly_projection": 117118.0,    // run_rate × dias do mês corrente
    "current_partial": 67715.98,       // realizado parcial mês aberto
    "current_forecast_remaining": 0.0, // forecast restante (quando confiável)
    "forecast_next_month": null
  },
  "capacity": {                        // capacidade faturada — mês FECHADO (realizado+rateio)
    "gpu_hours": 9242.09, "gpu_average": 12.4,
    "ai_unit_hours": 1504.17, "ai_unit_average": 2.0,
    "ocpu_hours": 126840.0, "gb_hours": 2029440.0,
    "daily": [{"date": "2026-07-01", "gpu_hours": 648.0, "ai_unit_hours": 48.0, "cost": 8863.7}]
  },
  "buckets": {                         // mês fechado, com MoM
    "Hospedagem GPU": {"cost": 74783.0, "prev": 0.0, "share_pct": 39.8},
    "Generative AI": {"cost": 73026.4, "share_pct": 38.9},
    "Dados e armazenamento": {"cost": 5498.0, "share_pct": 2.9},
    "Infraestrutura": {"cost": 34410.4, "share_pct": 18.3}
  },
  "monthly": [ /* como hoje, mas SEMPRE realizado+rateio por mês fechado;
                  mês aberto: {partial, forecast_remaining, projection} separados */ ],
  "skus": [                            // mês fechado, realizado
    {"sku": "B95909", "service": "COMPUTE", "description": "GPU Hours",
     "quantity": 9242.09, "unit": "GPU-hours", "cost": 74782.97,
     "share_pct": 39.8, "mom_delta_pct": null}
  ],
  "subaccounts": [                     // drill por compartment
    {"name": "whisper", "cost": 114624.62, "share_pct": 61.1},
    {"name": "llm", "cost": 73075.88, "share_pct": 38.9}
  ],
  "unit_economics": {                  // widgets novos (dependem da Fase 0)
    "cost_per_mm_input_tokens": null,  // custo IA do período ÷ (input tokens/1e6)
    "cost_per_mm_output_tokens": null,
    "tokens_period_days": 1,           // janela REAL da telemetria (honestidade)
    "list_vs_effective_pct": null      // desconto contratual médio
  },
  "inventory": {                       // snapshot mais recente, filtrado ao escopo Wisper
    "collected_at": "...",
    "ai_clusters": [
      {"name": "whisper-large-v3-turbo-a10", "shape": "A10_X1", "units": 20, "state": "ACTIVE"},
      {"name": "llama-scout-prod", "shape": "LARGE_GENERIC_V2", "units": 12, "state": "ACTIVE"}
    ],
    "endpoints": [
      {"name": "ep-vvia-gru-stt-001", "model": "...", "cluster": "...", "state": "ACTIVE"}
    ],
    "compute_by_shape": [              // agregado executivo: quantidade × modelo
      {"shape": "VM.Standard.E4.Flex", "count": 5, "state": "RUNNING"}
    ],
    "totals": {"gpu_units_dedicated": 32, "endpoints": 3, "compute_instances": 5}
  },
  "operational": {                     // telemetria com janela explícita
    "available": true,
    "window_days": 30,                 // real, não "90" fixo
    "daily": [{"date": "...", "input_tokens": 0, "output_tokens": 0, "invocations": 0, "errors": 0, "p50_latency_ms": 0}],
    "kpis": {"input_tokens": 0, "output_tokens": 0, "invocations": 0, "errors": 0,
             "success_rate": null, "latency_avg_ms": 0,
             "gpu_utilization": null, "gpu_utilization_note": "OCI não expõe utilização de GPU para Dedicated AI Clusters" }
  },
  "data_quality": [                    // painel de saúde da pipeline (diferencial executivo)
    {"check": "gold_operacional_fresca", "ok": false, "detail": "última métrica 2026-08-29T03:00Z"},
    {"check": "forecast_mes_aberto_coerente", "ok": false, "detail": "forecast 56.6k < realizado parcial 67.7k"}
  ]
}
```

---

## 6. Plano de execução (fases ordenadas)

### Fase 0 — Restaurar e corrigir as coletas (pré-requisito de tudo)

1. **Corrigir crash do job horário.** `oci_operational_monitoring.py:128` —
   `old = iceberg.read_destination(GOLD_TABLE)` pode ser `None`. Trocar por
   `old = iceberg.read_destination(GOLD_TABLE) or pd.DataFrame()` (e corrigir
   `read_destination` em `iceberg_service.py` para nunca retornar None). Adicionar log com
   stacktrace no handler do scheduler (`scheduler_service.py:193-196`) — hoje só `str(exc)`.
2. **Recovery da gold operacional a partir da bronze.** A bronze
   `raw_oci_prod_operational` tem dados até 2026-08-31T11:00 que nunca chegaram à gold.
   Escrever script one-shot (ou função `rebuild_gold_operational()`) que lê a bronze,
   desduplica e reescreve a gold. Reusar a lógica de `collect()` (linhas 124-136).
3. **Backfill de 90 dias de telemetria.** Rodar `collect(conn, hours=24*90)` uma vez
   (o cap já existe). Validar que `InputTokenCount` passa a ter ~90 dias × endpoints.
4. **Escopo do inventário.** Criar config `wisper_scope_compartment_ids` na conexão
   (ou derivar: os 4 subaccountids de billing + compartments que contêm DACs/endpoints
   nomeados whisper/llm/stt). Filtrar o inventário persistido/exibido por esse escopo.
   Manter a coleta ampla se fizer sentido para outros usos, mas o **payload do Wisper filtra**.
5. **Snapshot de inventário.** Adicionar `collected_at` nas linhas de inventário e alterar
   o dedupe: inventário = snapshot do último run (replace por `record_type='inventory'`),
   métricas = append dedup (como hoje). Elimina TERMINATED históricos.
6. **GPU metrics de DAC.** Investigar namespaces disponíveis para Dedicated AI Cluster no
   OCI Monitoring (via `SummarizeMetricsData` com namespace `oci_generativeai` e dimensão
   `clusterId`). Se não existir, persistir KPI derivado `tokens_per_gpu_hour` no payload e
   exibir nota explicativa (Bug G).
7. **Nome do compartment no inventário** (resolver OCID→nome na coleta, 1 chamada
   `IdentityClient.get_compartment` por OCID, com cache).

**Critérios de aceite Fase 0:** job horário 24h sem erro nos logs; gold com ≥90 dias de
métricas (V4); inventário gold sem TERMINATED antigos e só recursos do escopo.

### Fase 1 — Backend: reescrever `wisper_dashboard.py`

1. Trocar a fonte do financeiro de `iceberg.dimon.dest_dm_cost_custo_wisper` para
   **`iceberg.gold.dest_modelo_focus` com o filtro de escopo**
   (`regexp_like(lower(subaccountname),'whisper|llm|poc-stt')`) — elimina a dependência do
   formato dimon e habilita grão dia, unitprice, resourceid. (Alternativa aceitável: manter
   dimon para o mensal e gold para o diário/detalhe; mas uma fonte só é mais simples.)
2. Implementar a semântica de período: `closed_period` = maior `_billing_period` com mês
   fechado (< mês corrente); KPIs/capacidade/SKUs/buckets sempre sobre ele; mês corrente
   sempre separado como parcial+projeção. **Corrige Bugs A, B, C.**
3. Calcular série diária (`chargeperiodstart`), run-rate (média 7d), projeção do mês e
   MoM. Detectar e expor step-downs (>30% de variação de custo diário sustentada por 3+ dias)
   — o de 13–14/07 deve aparecer como insight automático.
4. Montar `inventory` agregado por shape/modelo e filtrado ao escopo (Fase 0.4).
5. Montar `unit_economics` quando houver telemetria ≥7 dias; senão `null` + nota.
6. Montar `data_quality` com os checks das queries V1–V5.
7. Cache curto (5 min) — as queries de gold full-scan levam 2–5s.

**Critérios de aceite Fase 1:** payload v2 validado contra V1–V5; KPI do mês fechado =
R$ 187.717,79 para 202607; GPU Hours = 9.242,09; AI units = 1.504,17.

### Fase 2 — Frontend: redesign executivo de `WisperDashboard.tsx`

Estrutura proposta (MUI + Plotly, padrão já usado na página):

1. **Header executivo**: título, chips de período (fechado × mês em curso), e 4 KPI cards:
   *Custo mês fechado* (com MoM %), *Projeção do mês corrente* (parcial + run-rate),
   *Hospedagem dedicada IA* (GPU + AI units, % do total), *Custo por MM tokens* (quando
   disponível, com janela da telemetria).
2. **Evolução financeira** (gráfico): barras empilhadas por bucket (mensal, realizado) +
   linha de forecast + marcador no step-down de julho; mês corrente em barra "parcial"
   hachurada + ponto de projeção.
3. **Capacidade faturada** (card): GPU-h, GPUs médias, AI unit-h, AI units médias,
   OCPU/GB horas — do mês fechado; mini-gráfico diário de GPU-h mostrando o step-down.
4. **Inventário executivo** (substitui a tabela de nomes): cards agregados —
   "32 GPUs dedicadas (20× A10_X1 + 12× LARGE_GENERIC_V2)", "3 endpoints ativos",
   "N instâncias por shape" (tabela `quantidade × shape × estado`); nomes só em tooltip/expand.
5. **SKUs e componentes**: tabela do mês fechado com descrição, quantidade+unidade, custo,
   % e MoM.
6. **Consumo por subprojeto**: barras whisper × llm × pocs.
7. **Monitoramento OCI**: título com a janela real ("últimos N dias" dinâmico), série diária
   de tokens/invocações/erros/latência, KPIs de sucesso.
8. **Saúde dos dados**: faixa discreta com os checks de `data_quality` (verde/âmbar/vermelho).

**Critérios de aceite Fase 2:** nenhum valor zerado/estranho sem explicação na UI; nenhum
nome de máquina em visão default; todos os números batem com as queries de validação.

### Fase 3 — Forecast do Wisper (Bug D)

1. Diagnosticar por que o forecast 202608 = R$ 56,6k (V5). Suspeitas: janela recente pós
   step-down + regra de dias restantes (`forecast_service.py:1071-1077`) + agregação por
   servicename sem skumeter.
2. Decidir com o usuário: baseline pós-step-down (julho em diante) como referência —
   forecast esperado ~R$ 115–125k/mês para set+.
3. Propagar `skumeter`/`consumedquantity` estimados nas linhas de forecast **ou** garantir
   que nenhum KPI de capacidade leia linhas de forecast (Fase 1 já garante).
4. Reprocessar e validar: forecast do mês aberto ≥ realizado parcial (invariante).

### Fase 4 — Enriquecimentos opcionais (ordem de valor)

1. `resourcename`/`resourcetype` nas linhas OCI da gold (join com inventário/bronze legada).
2. "Custo de lista vs. efetivo" (desconto contratual) via `listcost`/`effectivecost`.
3. Throughput operacional: tokens/GPU-h faturada, latência por endpoint/modelo.
4. Alertas automáticos: step-down/up de custo diário, erro rate >2%, gold operacional
   atrasada >6h.
5. Export PDF executivo (padrão `azurePtuPdfExport.ts` já existe como referência).

---

## 7. Queries de validação (rodar no Trino, `scripts/trino_query.py`)

```sql
-- V1: verdade mensal (KPI do mês fechado deve bater)
SELECT "_billing_period", ROUND(SUM(billedcost),2)
FROM iceberg.gold.dest_modelo_focus
WHERE regexp_like(lower(COALESCE(subaccountname,'')), 'whisper|llm|poc-stt')
GROUP BY 1 ORDER BY 1;
-- esperado: 202607 = 187.717,79 ; 202608 parcial ≈ 67.716

-- V2: capacidade faturada do mês fechado
SELECT skumeter, ROUND(SUM(consumedquantity),2) qtd, ROUND(SUM(billedcost),2) custo
FROM iceberg.gold.dest_modelo_focus
WHERE "_billing_period"='202607'
  AND regexp_like(lower(COALESCE(subaccountname,'')), 'whisper|llm|poc-stt')
  AND skumeter IN ('B95909','B108085','B97384','B97385')
GROUP BY 1;
-- esperado: B95909=9.242,09 / R$74.782,97 ; B108085=1.504,17 / R$73.026,38

-- V3: série diária e step-down
SELECT substr(chargeperiodstart,1,10) dia, ROUND(SUM(billedcost),2)
FROM iceberg.gold.dest_modelo_focus
WHERE "_billing_period"='202607'
  AND regexp_like(lower(COALESCE(subaccountname,'')), 'whisper|llm|poc-stt')
GROUP BY 1 ORDER BY 1;
-- esperado: ~8.864/dia até 12/07; ~3.778/dia de 14/07 em diante

-- V4: frescor e cobertura da telemetria (após Fase 0)
SELECT metric_name, COUNT(*) n, MIN(timestamp), MAX(timestamp)
FROM iceberg.gold.dest_oci_operational_metrics
WHERE record_type='metric' GROUP BY 1;
-- esperado: MAX(timestamp) < 2h atrás; MIN ≥ 90 dias após backfill

-- V5: sanidade do forecast (invariante)
SELECT tipo, ROUND(SUM(realizado),2)
FROM iceberg.dimon.dest_dm_cost_custo_wisper
WHERE "_billing_period"='202608' GROUP BY 1;
-- invariante pós-Fase 3: forecast_mes_aberto >= realizado_parcial
```

---

## 8. Riscos e decisões pendentes

1. **Escopo "oficial" do Wisper**: confirmar se o compartment GenAI `...pichbvzq` (que hospeda
   os DACs/endpoints) deve entrar no filtro financeiro também — hoje o custo dele pode estar
   caindo em outro projeto ou em "SEM PROJETO DEFINIDO". **Checar antes da Fase 1** (pode
   explicar parte da diferença "abaixo da realidade" percebida pelo usuário).
2. **Bronze legada (`raw_oci_prod`) parou em 202509**: confirmar se a ingestão CSV foi
   substituída oficialmente pelo extrator FOCUS; se sim, documentar e não usar como fonte.
3. **GPU utilization de DAC**: se a OCI não expuser, o widget definitivo é eficiência faturada
   (tokens/GPU-h). Decisão de produto, não de engenharia.
4. **Forecast**: mudança de baseline altera números reportados em outras visões que usam a
   mesma config (id=9) — avaliar criar config específica para o recorte Wisper.
5. **Performance**: gold FOCUS full-scan ~2–5s por query; payload v2 deve agregar em 3–4
   queries com cache de 5 min.

---

## 9. Resumo executivo das correções (para o usuário)

| Sintoma reportado | Causa raiz | Correção |
|---|---|---|
| Custo total R$ 56.641 (não bate com gráfico) | KPI usa forecast do mês aberto em vez do realizado | Fase 1.2 — mês fechado realizado + mês corrente separado |
| Capacidade faturada zerada | Linhas de forecast têm SKU NULL e qtd 0 | Fase 1.2 — capacidade sempre do realizado |
| Inventário horrível / nomes de máquina | 77 compartments coletados; dedupe acumula histórico; UI nominal | Fase 0.4/0.5 + Fase 2.4 — agregado quantidade × modelo |
| Monitoramento 90 dias "abaixo da realidade" | Job quebrado desde 29/08; só 24h de dados na gold | Fase 0.1/0.2/0.3 — fix, recovery, backfill 90d |
| Hospedagem GPU fora da realidade | Mesma causa do forecast + bucket do mês errado | Fase 1 + Fase 3 |
| Falta custo por MM tokens | Sem histórico de tokens | Fase 0.3 + Fase 1.5 |
| SKUs "nada a ver" | Tabela sobre forecast, sem descrição | Fase 2.5 |
