# E0 — Contrato cloud e auditoria

Etapa **E0** do plano *Gestão de anomalias de custo cloud: detecção até encerramento*.

Objetivo da etapa: provar, com evidência medida no ambiente, que a detecção de
anomalia **independe da fatura e de `_billing_period`**, e que
`observado_cloud` e `esperado_cloud` são comparáveis.

Todos os números deste documento foram medidos no ambiente de DEV
(`localhost:5433` PostgreSQL, Trino `localhost:8080`, catálogo `iceberg`,
warehouse `apps/data/iceberg`) em **2026-10-14**.

---

## 1. Fonte de `realizado_cloud`

### 1.1 Onde o forecast lê

`apps/api/app/services/forecast_service.py:1322-1337`

```python
table = config.destination_physical_name
namespace = getattr(config, "source_namespace", "silver")
...
source = f"iceberg.{namespace}.{table}"
type_filter = (
    f"({_q(config.type_column)} = 'realizado' OR {_q(config.type_column)} IS NULL)"
    if getattr(config, "has_type_column", False) else "TRUE"
)
```

Para a configuração `forecast_config.id = 9` (única ativa em DEV):

| Campo | Valor |
|---|---|
| `destination_table_id` | 1 (`MODELO FOCUS` → `dest_modelo_focus`) |
| `measure_column` | `realizado` |
| `date_column` | `chargeperiodstart` |
| `period_column` | `_billing_period` |
| `type_column` | `tipo` |
| `cutoff_days` | 2 |
| `horizon_months` | 12 |
| `model_mode` | `AUTO_BACKTEST` |
| `currency_column` | `NULL` |
| `dimensions` | 18 dimensões (ver §4) |
| `version` | 21 |

Portanto a tabela efetivamente lida é **`iceberg.silver.dest_modelo_focus`**
(192 colunas), e não o cubo `iceberg.gold.dest_dm_cost_focus`.

> **Achado A0-1 — `realizado_cloud` está ausente no cubo Gold.**
> Em `gold.dest_dm_cost_focus`, `sum(realizado_cloud)` é `0.0` para
> `_billing_period >= 202608` (e irrisório em 202606/202607), enquanto
> `sum(realizado)` é `12.031.488,93` em 202608 e `10.232.791,95` em 202609.
> O cubo Gold **não** é fonte válida para a detecção de anomalia.
> A camada de anomalia deve ler a mesma tabela e a mesma projeção de colunas
> que o forecast lê (`silver.dest_modelo_focus`).

### 1.2 `realizado_cloud` x `realizado` — semântica

O par financeiro é produzido em `apps/api/app/services/reprocess_transform.py:192-200`:

```python
for rule in brules:
    _apply_business_rule_sync(target, rule, billing_period)

if "realizado_cloud" in target.columns:
    target["consumo_realizado"] = pd.to_numeric(target["realizado_cloud"], errors="coerce")
if "consumo_realizado" in target.columns:
    target["realizado_cloud"] = pd.to_numeric(target["consumo_realizado"], errors="coerce")
    target["realizado"] = pd.to_numeric(target["consumo_realizado"], errors="coerce")
    target["ajuste_contratual"] = 0.0
```

Regras financeiras ativas em DEV (`public.business_rule`):

| id | regra | `target_column` | `financial_effect` | expressão |
|---:|---|---|---|---|
| 20 | DEV IBM — consumo para compromisso | `realizado_cloud` | `BUDGET_COMMITMENT` | `BilledCost * 0.79036 + (BilledCost * 0.10) * 0.29505` |
| 19 | Regra Comitment OCI | `realizado_cloud` | `BUDGET_COMMITMENT` | `BilledCost * 0.47` |
| 5 | Taxa Marketplace Azure (13,83%) | `consumo_realizado` | `DIRECT_REALIZED` | `BilledCost * 1.1383` (condicional) |
| 6 | Demais impostos Azure (18,61%) | `consumo_realizado` | `DIRECT_REALIZED` | `BilledCost * 1.186065` |
| 10 | Realizado GCP com desconto de crédito | `consumo_realizado` | `DIRECT_REALIZED` | `BilledCost + CreditAmount` |
| 9 | Realizado padrão (qualquer nuvem, sem imposto) | `consumo_realizado` | `DIRECT_REALIZED` | `BilledCost` |

Conclusão de semântica:

```text
realizado_cloud  = consumo cloud observado (base da detecção)
consumo_realizado = mesmo valor na ausência de contrato
ajuste_contratual = diferença contratual (rateio de compromisso/orçamento)
realizado        = medida contratual (compromisso de orçamento), NÃO usar na detecção
```

`realizado_cloud` é a **medida primária de custo cloud**. O plano proíbe
substituí-la por `realizado`, `consumo_realizado` ou `billedcost` quando
estiver ausente; a ausência deve ser explicitada (ver §6).

### 1.3 Diferença observada entre as medidas

Medido em `iceberg.silver.dest_modelo_focus` com `tipo = 'realizado'`,
recorte `chargeperiodstart >= 2026-08-01`:

| cloud | linhas | `realizado_cloud` | `realizado` |
|---|---:|---:|---:|
| azure-ti | 7.992.624 | 97.411.758,16 | 97.411.758,16 |
| oci | 1.954.498 | 31.283.494,94 | 25.332.158,56 |
| azure-marketplace | 622 | 19.005.757,23 | 19.005.757,23 |
| azure-4p | — | 13.171.716,48 | 13.171.716,48 |
| gcp-ttech | 49.489 | 8.871.282,99 | 8.871.282,99 |
| mongodb-atlas-pro | 36.374 | 4.980.394,77 | 4.980.394,77 |
| gcp-4p | 142.492 | 3.320.584,60 | 3.320.584,60 |
| oci-prod | — | 2.153.402,92 | 2.153.402,92 |
| ibm | 1.520 | 1.248.053,54 | 10.228.302,00 |
| gcp-ti | 248 | 847.909,07 | 847.909,07 |

(Os totais são sobre todo o histórico da tabela; o recorte por mês está em §5.)

**IBM é o caso que prova a necessidade da medida `realizado_cloud`**: o
compromisso de orçamento infla `realizado` para ~8× o consumo cloud real.
Detectar sobre `realizado` produziria um falso positivo permanente em IBM.

### 1.4 Moeda validada de `realizado_cloud`

| cloud | `billingcurrency` | linhas | `realizado_cloud` | `billedcost` |
|---|---|---:|---:|---:|
| azure-4p | BRL | 3.116.998 | 13.171.716,48 | 11.107.000,83 |
| azure-marketplace | BRL | 1.759 | 19.005.757,23 | 16.696.615,31 |
| azure-ti | BRL | 24.506.349 | 97.411.758,16 | 82.343.643,32 |
| gcp-4p | BRL | 620.976 | 3.320.584,60 | 3.370.455,05 |
| gcp-ti | BRL | 849 | 847.909,07 | 854.890,93 |
| gcp-ttech | BRL | 224.350 | 8.871.282,99 | 9.435.092,24 |
| ibm | BRL | 4.644 | 1.248.053,54 | 1.522.267,13 |
| mongodb-atlas-enterprise | **USD** | 1.637 | 105.693,00 | 14.565,25 |
| mongodb-atlas-pro | **USD** | 121.414 | 4.980.394,77 | 781.059,76 |
| oci | BRL | 9.776.791 | 31.283.494,94 | 66.560.627,53 |
| oci (sem moeda) | — | 7.668 | 0,00 | 0,00 |
| oci-prod | BRL | 1.227.111 | 2.153.402,92 | 4.581.708,34 |
| oci-prod (sem moeda) | — | 6.900 | 0,00 | 0,00 |

> **Achado A0-2 — a moeda de `realizado_cloud` é BRL, e `BillingCurrency` mente.**
> As linhas `mongodb-atlas-*` declaram `BillingCurrency = USD`, mas o valor
> alocado em `realizado_cloud` (4.980.394,77 para `mongodb-atlas-pro`) é o
> rateio do Private Offer do Azure já convertido para BRL — razão de ~6,4×
> sobre o `BilledCost` bruto do export Atlas.
> **Nunca inferir a moeda de `realizado_cloud` a partir de `BillingCurrency`.**
> Como `currency_column` é `NULL` na configuração, a moeda é declarada
> **explicitamente na configuração de anomalia** (`anomaly_config.materiality_currency = 'BRL'`).

Materialidade inicial: **100 BRL**, configurável por moeda.

---

## 2. Filtros e semântica financeira do forecast

### 2.1 Filtro de tipo

```text
tipo = 'realizado'  OU  tipo IS NULL      (retrocompatibilidade)
```

Medição: 100% das 39.617.446 linhas de `silver.dest_modelo_focus` têm
`tipo = 'realizado'` (nenhuma `NULL` no snapshot atual). O `OR IS NULL`
permanece por segurança histórica.

As linhas de Planning (`tipo='forecast'`, `rateio`, `shared_cost`) vivem em
`iceberg.silver.dest_modelo_focus_planning` — tabela separada; nunca entram na
leitura do realizado.

### 2.2 Filtro de período e o que a detecção NÃO pode copiar

`_billing_period_values()` (`forecast_service.py:894-913`) monta a lista de
`_billing_period` a carregar, e `_fetch_period_records_by_cloud()` usa essa
lista como **poda de partição** para o histórico do modelo.

Contrato da camada de anomalia (obrigatório):

| Uso de `_billing_period` | Permitido? |
|---|---|
| Poda de partição para reduzir leitura no Trino | **Sim** — otimização, não resultado |
| Desambiguar `MM/DD/YYYY` x `DD/MM/YYYY` no parsing (como `gold_date_expr`) | **Sim** — é parsing, não seleção de dia |
| Selecionar, deslocar ou limitar **qual dia** é analisado | **Não** |
| Definir a janela de análise ou o `event_date` | **Não** |
| Determinar se o mês está fechado para efeito de detecção | **Não** |

`source_billing_period` é gravado apenas como **procedência** na linha de
avaliação (§4.3 do plano).

### 2.3 Cutoff

`_realized_cutoff()` = menor entre a maior data válida da fonte e
`hoje − cutoff_days`. Com `cutoff_days = 2` e hoje `2026-10-14`:
`cutoff = 2026-10-12`.

O cutoff governa **o que o forecast pode usar como realizado**. A detecção de
anomalia usa a **data completa do consumo/cobrança normalizada em UTC** e não
aguarda fechamento de fatura.

### 2.4 Regras de negócio do forecast efetivamente aplicadas

Fonte: `public.forecast_business_rule` (`forecast_config_id = 9`), 14 regras
cadastradas, **12 ativas** (id 23 e id 14 estão `active = false`).

| id | nome | comportamento | prioridade | ativa | opções |
|---:|---|---|---:|:--:|---|
| 19 | Excluir consumo horário de PTU | `SOURCE_EXCLUDE_PREFIX` | 1 | ✔ | — |
| 24 | Azure-4P — suspender após 30 dias sem dados | `SOURCE_VALIDITY` | 1 | ✔ | `max_age_days=30` |
| 20 | Aura Brain — PTU Azure OpenAI mensal | `RECURRING_MONTHLY` | 2 | ✔ | — |
| 23 | GitHub Copilot — overage média 30 dias | `SOURCE_EXCLUDE_PREFIX` | 2 | ✘ | — |
| 14 | OCI Marketplace compra | `REPLACE_VALUE` | 5 | ✘ | — |
| 17 | Marketplace ONESHOT zera forecast | `REPLACE_VALUE` | 5 | ✔ | — |
| 18 | Marketplace RECURRING mensal | `RECURRING_MONTHLY` | 6 | ✔ | — |
| 16 | IBM fatura mensal | `CHARGE_INTERVAL` | 8 | ✘ | — |
| 25 | Purchase recorrente Savings Plan | `RECURRING_MONTHLY` | 10 | ✔ | — |
| 15 | OCI Refund cancela compromisso | `CANCEL_ON_REFUND` | 15 | ✔ | — |
| 7 | Return simulado | `RECURRING_MONTHLY` | 20 | ✔ | — |
| 8 | Compras recorrentes — zerar mês já realizado | `RECURRING_MONTHLY` | 30 | ✔ | `current_month_policy=ZERO_WHEN_REALIZED` |
| 21 | Vivo Mais — encerrar reserva PTU Azure-4P estornada | `CANCEL_ON_REFUND` | 35 | ✔ | — |
| 9 | Refund cancela compromisso | `CANCEL_ON_REFUND` | 40 | ✔ | — |

> **Achado A0-3 — "regra cadastrada" ≠ "regra aplicada".**
> O motor não expõe hoje um registro de quais regras **encontraram a série**,
> respeitaram a vigência e influenciaram a projeção. O `config_snapshot` do
> `forecast_run` congela a definição das regras, não a decisão de aplicação.
> A E2 deve produzir `applied_rule_refs` com essa distinção
> (`MATCHED_AND_APPLIED` / `MATCHED_OUT_OF_WINDOW` / `SERIES_NOT_FOUND` /
> `OUT_OF_DIMENSIONS`), reaproveitando os avisos já emitidos pelo motor
> ("não aplicada: campo fora das dimensões").

Regras de comportamento preservadas (contrato do plano §2.4):

- `RECURRING_MONTHLY` / `RECURRING_UNTIL_END` e `ZERO_WHEN_REALIZED` são lidas
  do snapshot congelado, nunca do cadastro editável.
- `SOURCE_VALIDITY` (`max_age_days`) e `CANCEL_ON_REFUND` são preservadas.
- `Return` sintético **não** é tratado como cancelamento de compromisso.

---

## 3. Datas UTC e referências mensais/restantes

### 3.1 Formatos físicos de `chargeperiodstart`

`chargeperiodstart` é `varchar` com **quatro formatos distintos** convivendo:

| cloud | formato físico | exemplo |
|---|---|---|
| azure-4p, azure-marketplace, azure-ti | `MM/DD/YYYY` | `01/01/2026` |
| gcp-4p, gcp-ti, gcp-ttech | ISO-8601 com offset UTC | `2025-11-24T00:00:00+00:00` |
| ibm | ISO-8601 `Z` | `2026-01-01T00:00:00Z` |
| mongodb-atlas-* | ISO-8601 `Z` | `2025-12-01T00:00:00Z` |
| oci, oci-prod | `YYYY-MM-DD` | `2025-12-10` |

Por isso a ordenação lexicográfica é inválida (`max('01/01/2026','12/31/2025') = '12/31/2025'`)
e o motor usa `gold_date_expr()` (`forecast_service.py:638-678`), que:

1. trata ISO estrito só quando `regexp_like(raw, '^[0-9]{4}-[0-9]{2}-[0-9]{2}')`;
2. nunca usa `TRY_CAST` em string com barra (o parser do Trino é dependente de locale);
3. resolve ambiguidade `MM/DD` x `DD/MM` confrontando `year*100+month` com `_billing_period`;
4. só então cai em `%m/%d/%Y` e `%d/%m/%Y`.

Reutilizar essa função é **obrigatório** — a camada de anomalia não pode
implementar um segundo parser.

### 3.2 Validação de parsing (100% das linhas)

Medido com a expressão de `gold_date_expr` e `tipo='realizado'`:

| cloud | linhas | datas parseadas | menor dia | maior dia | dias distintos |
|---|---:|---:|---|---|---:|
| azure-ti | 7.992.624 | 7.992.624 | 2026-08-01 | **2026-10-13** | 74 |
| gcp-4p | 142.492 | 142.492 | 2026-07-24 | **2026-10-13** | 76 |
| gcp-ti | 248 | 248 | 2026-07-24 | **2026-10-13** | 77 |
| gcp-ttech | 49.489 | 49.489 | 2026-07-24 | **2026-10-13** | 76 |
| azure-marketplace | 622 | 622 | 2026-08-01 | 2026-09-30 | 61 |
| oci | 1.954.498 | 1.954.498 | 2026-07-31 | 2026-09-21 | 53 |
| ibm | 1.520 | 1.520 | 2026-08-01 | 2026-09-01 | **2** |
| mongodb-atlas-pro | 36.374 | 36.374 | 2026-08-01 | 2026-10-01 | **3** |

- **0 datas não parseadas** em 10.177.867 linhas do recorte.
- Normalização UTC: todos os formatos são resolvidos para `DATE` (dia civil UTC).
  GCP/IBM/Atlas já carregam `+00:00`/`Z`; Azure e OCI são datas puras.

### 3.3 Granularidade real por série (base para os dois detectores)

| classe | clouds | dias distintos | detector |
|---|---|---|---|
| consumo diário | azure-ti, azure-4p, azure-marketplace, gcp-4p, gcp-ti, gcp-ttech, oci, oci-prod | 53–77 | **consumo diário** |
| cobrança por intervalo/fatura | ibm (2 datas), mongodb-atlas-pro (3 datas) | 2–3 | **evento mensal / acumulado** |

`ibm` e `mongodb-atlas-*` não têm consumo diário observável. Uma média diária
nesses clouds é ficção estatística — é exatamente o caso do §2.4 do plano.

### 3.4 Referências mensais x restantes

O forecast mensal distingue, por série:

```text
estimativa_do_mês = realizado_acumulado_até_cutoff + forecast_restante
forecast_restante = média_diária × dias_restantes
forecast_mês_n    = baseline × (1 + growth_rate)^n
```

e marca `MONTHLY_LUMP` quando a série tem `nonzero_days <= 3` na janela
(baseline = fatura do último mês com cobrança ÷ dias daquele mês).

A **camada de referência de anomalia (E2/E3)** precisa registrar, por linha de
referência, se o valor previsto cobre:

```text
FULL_MONTH       -> projeção cobre o mês completo
REMAINING_MONTH  -> projeção cobre somente o restante do mês a partir do cutoff
```

> **Achado A0-4 — "restante zero" é legítimo e não pode virar falso positivo.**
> Quando a compra mensal esperada **já foi realizada**, o forecast restante é
> `0,00` por construção (`_replicated_month_value` com
> `current_month_policy = ZERO_WHEN_REALIZED`, regra 8 ativa, prioridade 30).
> Comparar o observado contra esse zero acusaria toda compra normal como
> anomalia. A detecção mensal deve usar o **valor esperado congelado antes da
> compra**, não o restante corrente.

Teste dedicado: E10 caso 5.

---

## 4. Cardinalidade

Dimensões da série (`forecast_config.dimensions`, 18 colunas):

```text
cloud_origem, marketplaceproductname, marketplacebillingmodel, servicename,
subaccountname, centro_custo, organograma_nivel_1..7, chargecategory,
serviceprovidername, servicesubcategory, pricingcategory, commitmentdiscounttype
```

Séries distintas com `tipo='realizado'` e `_billing_period >= '202604'`:

| cloud | séries |
|---|---:|
| azure-ti | 7.195 |
| gcp-4p | 2.804 |
| oci | 1.044 |
| gcp-ttech | 947 |
| mongodb-atlas-pro | 460 |
| ibm | 45 |
| azure-4p | 25 |
| azure-marketplace | 22 |
| gcp-ti | 5 |
| **total** | **12.547** |

Consequências de projeto:

- Grade diária completa de 180 dias × 12.547 séries = 2,26 M células. **Não
  persistir a grade** — a referência compacta (1 linha por forecast × série ×
  mês) é obrigatória, e os 15 pontos do horizonte são derivados dela em tempo
  de leitura (plano §4.2).
- A agregação diária deve acontecer **no Trino**, com `GROUP BY` na
  granularidade da série; o Python recebe apenas a série agregada, como o
  motor de forecast já faz.
- Ordem de grandeza da leitura por dia reconstruído: `azure-ti` tem 7,99 M
  linhas em 74 dias (~108 k/dia). Nenhuma reconstrução pode ler o billing
  inteiro por dia (plano §6).

---

## 5. Contrato de comparabilidade (entrada da detecção)

```text
observado_cloud(event_date, series_id) =
    SUM(realizado_cloud)
    na tabela/projeção lida pelo forecast
    WHERE tipo = 'realizado' OR tipo IS NULL
      AND day = event_date            -- dia civil UTC derivado por gold_date_expr
    GROUP BY <as dimensões da série>

esperado_cloud(event_date, series_id, reference_id) =
    valor previsto em realizado_cloud
    por uma referência cujo cálculo NÃO utilizou o realizado de event_date
    (reference_kind = PROSPECTIVE | RETROSPECTIVE; UNAVAILABLE se não houver)
```

Garantias exigidas e verificadas nesta etapa:

| # | Garantia | Evidência |
|---|---|---|
| 1 | `realizado` não participa da comparação | §1.2–1.3: IBM difere 8× entre as medidas; a detecção usa só `realizado_cloud` |
| 2 | `_billing_period` não seleciona/desloca/limita o dia | §2.2: poda de partição e desambiguação permitidas; seleção de dia proibida |
| 3 | Janela usa a data completa normalizada em UTC | §3.1–3.2: 10.177.867 linhas parseadas sem perda |
| 4 | Não aguarda fechamento de fatura | §2.3: `event_date` é a data do consumo/cobrança; cutoff não bloqueia detecção |
| 5 | GCP não é excluído nem tem tratamento fixo por nome de provedor | §4: GCP tem 3.756 séries e aparece nos dois detectores pela granularidade, não pelo nome |
| 6 | `realizado_cloud` ausente não é substituído por outra medida | §1.4 + §6: ausência vira razão explícita, não fallback |
| 7 | Regras de compra recorrente, cancelamento e validade preservadas | §2.4: 12 regras ativas lidas do snapshot congelado |

---

## 6. Limitações declaradas (entram como `reason_codes`)

| código | condição medida |
|---|---|
| `MEASURE_ABSENT` | coluna `realizado_cloud` inexistente na fonte configurada |
| `MEASURE_ALL_ZERO` | `realizado_cloud` presente mas `SUM = 0` em toda a janela |
| `CURRENCY_UNVERIFIED` | moeda da linha não é a moeda declarada em `anomaly_config` |
| `CURRENCY_FROM_BILLING_CURRENCY` | proibido — nunca emitir; a moeda vem da configuração |
| `NO_DAILY_GRAIN` | série com `nonzero_days <= 3` na janela — exige detector de evento mensal |
| `PARTIAL_DATA` | dia corrente com coleta incompleta (`oci` com 7.668 linhas sem moeda em §1.4) |
| `REFERENCE_UNAVAILABLE` | `reference_kind = UNAVAILABLE` — comparação não suportada |

> **Achado A0-5 — linhas com `BillingCurrency` vazio e `realizado_cloud = 0`.**
> `oci` (7.668 linhas) e `oci-prod` (6.900 linhas) têm `billingcurrency` vazio e
> ambas as medidas zeradas. São linhas de metadados/carga sem valor financeiro.
> Não podem ser confundidas com "queda de consumo" nem usadas para confirmar
> recuperação (`PARTIAL_DATA`).

---

## 7. Aceite da E0

| Critério | Situação |
|---|---|
| Detecção independe da fatura | ✔ `event_date` = dia do consumo/cobrança (§3.2) |
| Detecção independe de `_billing_period` | ✔ §2.2 — permitido só poda e desambiguação |
| Observado e esperado comparáveis | ✔ §5 — mesma tabela, mesma medida, mesma dimensão de série |
| Origem de `realizado_cloud` confirmada | ✔ `silver.dest_modelo_focus`, `tipo='realizado'` (§1.1) |
| Moeda confirmada sem `BillingCurrency` | ✔ BRL por configuração, `BillingCurrency` refutado (§1.4) |
| Datas UTC validadas | ✔ 4 formatos, 0 falhas em 10.177.867 linhas (§3.1–3.2) |
| Referências mensais/restantes validadas | ✔ §3.4, com `FULL_MONTH` / `REMAINING_MONTH` |
| Regras efetivamente aplicadas documentadas | ✔ §2.4, com lacuna `applied_rule_refs` atribuída à E2 |
| Cardinalidade medida | ✔ 12.547 séries, 9 clouds (§4) |

**Aceite E0: cumprido.** As lacunas identificadas (registro de regras
efetivamente aplicadas, referência compacta, separação diário x evento mensal)
são exatamente o escopo de E2/E3/E4.
