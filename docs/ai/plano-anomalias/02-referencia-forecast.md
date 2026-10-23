# E2 — Referência integrada ao forecast

Etapa **E2** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Resultados mensais existentes permanecem equivalentes | A referência **reconcilia ao centavo** com a soma do snapshot, mês a mês | `scripts/verify_anomaly_reference.py`: `202609` e `202610` com `snapshot == referência` |
| Restante zero não provoca falso positivo | Seleção da referência **anterior à compra**, não do restante corrente | `test_compra_mensal_esperada_nao_vira_anomalia_com_restante_zero`, `zero_remaining_is_legitimate` |
| Adaptador de snapshots | `app/services/anomaly_forecast_adapter.py` | 6 execuções reais carregadas |
| Registrar regras, corte e granularidade | `applied_rule_refs`, `data_cutoff`, `coverage_kind` | `contract.as_dict()`, `ReferenceRow.as_dict()` |
| Seleção prospectiva/retrospectiva | `select_reference`, `select_pre_event_reference` | 30 testes em `test_anomaly_reference.py` |
| Preservar referência anterior à compra | `select_pre_event_reference` | `REFERENCIA_ANTERIOR_A_COMPRA_PRESERVADA` |
| Não criar um segundo motor de recorrência | Nenhuma regra de negócio é reimplementada: a expectativa vem do snapshot; nenhum casamento de regra é refeito | `read_expectation` só lê `forecast.result_run_{id}` |

---

## 1. O contrato de snapshot varia por tipo de disparo

Medição no ambiente de DEV (`forecast_run` em `localhost:5433`):

| `trigger_type` | Chaves do `config_snapshot` | `business_rules` congeladas | `source_namespace` | Exemplo |
|---|---:|---|---|---|
| `MANUAL` (simulado de acurácia) | 19 | sim (11 regras) | sim (`silver`) | run 400 |
| `SCHEDULED_REPROCESS` (normal) | 12 | **não** | **não** | run 399 |
| `POST_INGESTION` (agendado) | 0 (`{}`) | não | não | — |

`config_snapshot` de uma execução normal (run 399):

```text
config_id · config_version · currency_column · cutoff_days · date_column ·
destination_physical_name · destination_table_id · dimensions · horizon_months ·
measure_column · period_column · type_column
```

> **Achado A0-7 (confirmado aqui) — o snapshot normal não congela as regras.**
> O rastro de aplicação sobrevive apenas em texto livre em
> `forecast_run.error_metrics["business_rules"]`. O adaptador usa esse rastro e
> **declara** quando precisou recorrer às regras atuais
> (`declares_current_rules = true`), como exige o plano §2.2.

### 1.1 A medida e o namespace do resultado são coisas diferentes

> **Achado A0-8 — `source_namespace` não é onde o resultado mora.**
> `config_snapshot["source_namespace"]` é a camada da **fonte** que alimentou o
> motor (Silver). O resultado do forecast **sempre** vive em
> `forecast.result_run_{id}`.
> Confundir os dois produziu, na primeira execução da verificação real,
> `Table 'iceberg.silver.result_run_400' does not exist`.

O contrato separa explicitamente:

| Atributo | Significado | Como é resolvido |
|---|---|---|
| `result_namespace` | onde está o snapshot | sempre `layers.forecast_layer()` = `forecast` |
| `source_namespace` | onde está a fonte observada/explicativa | `config_snapshot.source_namespace` → configuração de anomalia (`silver`) |
| `contract_source` | de onde veio o contrato | `SNAPSHOT` \| `RUN_COLUMNS` \| `ANOMALY_CONFIG` |

### 1.2 A medida lida é `realizado_cloud`

O forecast da configuração 9 projeta em `realizado` (medida contratual), mas o
resultado carrega as **duas** medidas quando a fonte tem `financial_effect`:

```text
result_run_395 / 399 / 400: forecast_value, realizado, realizado_cloud
```

A referência lê **`realizado_cloud`**, vindo da configuração de anomalia, nunca
do `measure_column` do forecast. Se a coluna não existir no resultado:

```text
has_measure = false  →  is_usable = false  →  reference_kind = UNAVAILABLE
warnings: "medida 'realizado_cloud' ausente em forecast.result_run_X:
           referência indisponível (não substituir a medida)"
```

Nenhum fallback para `forecast_value` ou `realizado`.

---

## 2. `applied_rule_refs` estruturado

O motor grava textos como:

```text
"Return simulado"
"Marketplace ONESHOT zera forecast (sem séries correspondentes)"
"Projeto Beta - PTU Azure OpenAI mensal (não aplicada: campo fora das dimensões)"
"Azure-Producao - suspender após 30 dias sem dados (suspensas 26 linhas; validade 30 dias; idade avaliada 104 dias em 2026-10-14)"
```

O adaptador converte em referências com estado explícito:

| Estado | Significado |
|---|---|
| `MATCHED_AND_APPLIED` | a regra encontrou a série e influenciou a projeção |
| `SERIES_NOT_FOUND` | nenhuma série correspondeu |
| `MATCHED_OUT_OF_DIMENSIONS` | identificação fora das dimensões da configuração |
| `MATCHED_OUT_OF_WINDOW` | vigência não cobre o período |
| `RULE_WITHOUT_IDENTIFICATION` | regra sem identificação/escopo |
| `RULE_UNKNOWN_BEHAVIOR` | comportamento desconhecido |
| `NOT_EVALUATED` | regra ativa congelada **sem** registro de aplicação no snapshot |
| `UNKNOWN_FROM_CURRENT_CONFIG` | rastro ausente: usou-se a configuração atual (declarado) |

**"Regra cadastrada" nunca é apresentada como "regra aplicada".** Uma regra
ativa do snapshot sem registro vira `NOT_EVALUATED`, não `MATCHED_AND_APPLIED`.

---

## 3. Referência compacta

### 3.1 Forma

**Uma linha por forecast × série × mês.** Nunca a grade de dias.

| Campo | Conteúdo |
|---|---|
| `reference_id` | `sha1(run_id:series_id:month)` — determinístico |
| `forecast_run_id`, `forecast_config_version`, `source_snapshot_id` | procedência do cálculo |
| `data_cutoff`, `generated_at` | corte e momento da geração |
| `target_start`, `target_end` | horizonte do alvo |
| `reference_kind` | `PROSPECTIVE` \| `RETROSPECTIVE` |
| `applied_rule_refs` | rastro estruturado (§2) |
| `series_id` | `sha1` canônico das dimensões — reprocessar não duplica |
| `reference_month` | mês alcançado |
| `coverage_kind`, `coverage_start`, `coverage_end`, `covered_days` | dias efetivamente cobertos |
| `predicted_cloud` | valor esperado em `realizado_cloud` |
| `normalizer`, `weekday_weights`, `distribution_method` | preenchidos na E3 |
| `band_lower_cloud`, `band_upper_cloud`, `band_method`, `band_confidence` | faixa (E3) |
| `partial_reference` | verdadeiro no mês do corte |

### 3.2 Cobertura

```text
mês > mês do corte     →  FULL_MONTH        (mês inteiro)
mês == mês do corte    →  REMAINING_MONTH   (corte+1 .. último dia)
mês <  mês do corte    →  ELAPSED_MONTH     (fora do horizonte; descartado)
```

### 3.3 Horizonte de 15 dias

O horizonte **não divide** o forecast mensal: ele define **quais meses** entram
na referência.

```text
corte 2026-10-12 + 15 dias = 2026-10-27  →  meses ['202610']
corte 2026-10-25 + 15 dias = 2026-11-09  →  meses ['202610', '202611']
```

### 3.4 Reconciliação em ambiente real

`scripts/verify_anomaly_reference.py` contra o snapshot run 400 (corte
2026-09-30, 14.111 linhas de expectativa → 14.111 linhas de referência):

```text
202609: snapshot=1.174.086,45  referência=1.174.086,45  OK
202610: snapshot=24.162.934,31 referência=24.162.934,31 OK
```

Isto cumpre o aceite "resultados mensais existentes permanecem equivalentes":
a referência é uma projeção **fiel** do snapshot, sem redistribuição.

---

## 4. Seleção prospectiva / retrospectiva / indisponível

`select_reference(contracts, event_date, event_month)`:

| Preferência | Condição | `reference_kind` | Motivo |
|---|---|---|---|
| 1 | gerado **antes** do evento **e** com corte anterior | `PROSPECTIVE` | `SNAPSHOT_ANTERIOR_AO_EVENTO` |
| 2 | gerado depois, mas com corte anterior ao evento | `RETROSPECTIVE` | `CORTE_ANTERIOR_AO_EVENTO_SNAPSHOT_POSTERIOR` |
| — | nenhum snapshot utilizável | `UNAVAILABLE` | `NENHUM_SNAPSHOT_UTILIZAVEL` |
| — | todos com corte ≥ evento | `UNAVAILABLE` | `CORTE_POSTERIOR_AO_EVENTO` |

Uma referência `RETROSPECTIVE` sempre carrega o aviso:

```text
"referência reconstruída: não foi a previsão originalmente disponível"
```

Nunca é apresentada como previsão original.

---

## 5. Preservar a referência anterior à compra

### 5.1 O problema

```text
02/10  forecast espera a compra mensal de 30.000 em outubro (restante > 0)
05/10  a compra de 30.000 é realizada
12/10  forecast rodou de novo: restante de outubro = 0
```

Comparar o observado (30.000 no dia 05) contra o restante de 12/10 (`0`)
acusaria **toda compra normal** como anomalia.

### 5.2 A solução

`select_pre_event_reference(contracts, event_date)` escolhe o **último snapshot
gerado antes do evento**, cujo corte também é anterior a ele:

```text
resultado: kind=PROSPECTIVE, run=394, motivo=REFERENCIA_ANTERIOR_A_COMPRA_PRESERVADA
esperado = 30.000   (do snapshot de 02/10)
observado = 30.000
desvio = 0          →  evento esperado, sem caso
```

Sem snapshot anterior ao evento:

```text
kind=UNAVAILABLE, motivo=SEM_REFERENCIA_ANTERIOR_A_COMPRA
```

`zero_remaining_is_legitimate()` documenta a regra de comportamento preservada
(`ZERO_WHEN_REALIZED`, regra 8 do forecast, prioridade 30): um restante `0` só é
legítimo quando o evento mensal **já foi realizado** no mês; em mês futuro o zero
não carrega esse significado.

---

## 6. Testes

```text
apps/api/tests/test_anomaly_reference.py          30 testes
apps/api/scripts/verify_anomaly_reference.py      verificação real (Trino + PostgreSQL)
```

Cobertura dos testes: contrato por tipo de disparo, medida ausente, rastro de
regras (aplicada / sem séries / fora das dimensões / não avaliada / regras
atuais), cobertura, horizonte, reconciliação mensal, compactação, determinismo
de ids, seleção prospectiva/retrospectiva/indisponível e preservação da
referência pré-compra.

Suíte completa: **711 passaram**, 18 falhas pré-existentes.

## 7. Limitações declaradas

- `source_snapshot_id` continua nulo porque o motor nunca o grava (A0-6). A
  camada de anomalia grava o seu próprio id no `anomaly_run`.
- `band_*`, `weekday_weights`, `normalizer` e `distribution_method` são
  preenchidos na E3; nesta etapa `normalizer = predicted_cloud` e
  `distribution_method = MONTHLY_TOTAL`.
- Execuções `POST_INGESTION` sem `config_snapshot` só são utilizáveis quando a
  configuração de anomalia fornece medida, camada e dimensões — e o contrato
  declara `contract_source = ANOMALY_CONFIG`.
