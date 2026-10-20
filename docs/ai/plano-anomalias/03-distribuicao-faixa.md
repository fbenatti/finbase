# E3 — Distribuição diária, faixa, perfis e materialidade

Etapa **E3** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Distribuição reconcilia | A soma da distribuição diária é **exatamente** o valor do forecast (correção de resíduo no último dia) | `test_distribuicao_soma_exatamente_o_valor_do_forecast`; verificação real `soma=3.329.093,83` |
| Horizonte não altera total | O horizonte de 15 dias define **quais meses** entram; o valor mensal nunca é dividido | `test_horizonte_de_15_dias_nao_divide_o_forecast_mensal`, `test_distribuicao_normaliza_sobre_todo_o_intervalo_coberto` |
| Falta de amostra é explicitada | Fallback uniforme identificado; faixa vira tolerância explícita **declarada** | `test_fallback_uniforme_quando_falta_evidencia_e_identificado`, `test_tolerancia_explicita_quando_a_amostra_e_insuficiente` |
| Perfis, tolerâncias e materialidade | Tabela do plano implementada com overrides explícitos | `test_tabela_de_perfis_do_plano`, `test_materialidade_explicita_por_moeda` |

---

## 1. Perfis (plano §2.6)

| Perfil | Quantil diário | Desvio relativo mínimo | Nota para investigar |
|---|---:|---:|---:|
| `CONSERVATIVE` | 99% | 30% | 65 |
| `BALANCED` | 97,5% | 20% | 55 |
| `SENSITIVE` | 95% | 10% | 40 |

Valores editáveis por configuração (`daily_quantile`,
`min_relative_deviation`, `investigate_score`); o perfil é o ponto de partida.

### 1.1 Amostra mínima do quantil

```text
min_sample = ceil(1 / alpha) - 1        alpha = 1 − quantil
```

| Quantil | `alpha` | Amostra mínima |
|---|---:|---:|
| 99% | 0,01 | 99 |
| 97,5% | 0,025 | 39 |
| 95% | 0,05 | 19 |

A configuração inicial usa o perfil `BALANCED` (quantil 97,5% → 39 resíduos).

---

## 2. Distribuição por dia da semana (plano §2.3)

### 2.1 Como é calculada

1. **Mediana** do consumo observado por dia da semana nas **últimas oito
   semanas válidas**. A janela é alinhada ao início da semana ISO de `as_of`
   (oito semanas completas, não nove parciais).
2. **Normalização sobre todo o intervalo coberto pela projeção**: o peso de cada
   dia coberto é `mediana[dia da semana] ÷ Σ mediana[dos dias cobertos]`.
3. **Fallback uniforme** quando falta evidência (< 4 dias da semana com ao menos
   3 amostras, ou medianas somando zero) — identificado em `distribution_method`
   e em `note`.
4. **Soma da distribuição completa igual ao valor do forecast**: a última parcela
   absorve o resíduo de ponto flutuante.

O forecast mensal **não** é dividido pelos 15 dias.

### 2.2 Verificação em dados reais

`scripts/verify_anomaly_reference.py`, janela de 180 dias até o corte
2026-09-30, dados de `silver.dest_modelo_focus`:

```text
perfil semanal: metodo=WEEKDAY_MEDIAN semanas=8 amostras=52
   medianas por dia da semana (0=seg):
   {0: 526.396,25  1: 524.117,40  2: 524.954,33  3: 614.037,07
    4: 529.893,10  5: 512.119,33  6: 477.048,98}
```

O fim de semana recebe menos que os dias úteis, coerente com a operação.
Distribuição do mês de maior valor:

```text
mês 202610: 3.329.093,83 em 31 dias
método=WEEKDAY_MEDIAN  soma=3.329.093,83   → reconcilia
```

---

## 3. Faixa e calibração (plano §2.5, §2.6)

### 3.1 Quantil unilateral para aumentos

Os resíduos são `observado − esperado` da **mesma granularidade** da avaliação
(plano §2.6 proíbe misturar diário e evento mensal):

```text
residual_i = observado_i − esperado_i

limite_superior = esperado + max(0, quantil_{1−alpha}(resíduos))
limite_inferior = esperado − max(0, −quantil_{alpha}(resíduos))
```

A faixa é unilateral porque o plano trata **aumentos**; a cauda inferior existe
apenas para não apresentar a faixa como intervalar simétrica.

### 3.2 Amostra insuficiente

Sem ao menos `min_sample` resíduos:

| Campo | Valor |
|---|---|
| `band_method` | `EXPLICIT_TOLERANCE` |
| `band_confidence` | `LOW` (ou `UNAVAILABLE` sem nenhuma amostra) |
| `note` | *"amostra insuficiente para quantil X% (n de m): tolerância explícita de Y% — não é intervalo estatístico calibrado"* |

Tolerância inicial: **15%**.

### 3.3 Eventos mensais

Poucos eventos mensais não sustentam promessa de confiança de 99%
(plano §2.6). `calibrate_monthly_event_band`:

- com menos de `monthly_min_events` (3) eventos comparáveis → tolerância de valor
  de 15% com **confiança limitada** (`LOW`);
- mesmo calibrado, a confiança é **limitada ao teto `MEDIUM`**.

### 3.4 Faixa por granularidade na referência

A referência compacta grava as duas leituras:

| Campo | Significado |
|---|---|
| `band_lower_cloud` / `band_upper_cloud` | faixa do **total do mês** |
| `daily_band_lower_offset` / `daily_band_upper_offset` | offset aplicado a **um único dia** (só na granularidade diária) |
| `band_method`, `band_confidence`, `band_alpha`, `band_sample_size`, `band_min_sample` | como a faixa foi obtida |

No caso diário, a faixa do mês é `offset diário × dias cobertos` — hipótese
conservadora de desvio sistemático, não diluída por `√n`
(`scale_band_to_coverage`). No caso de evento mensal a faixa **não** é escalada
por dia; `daily_band_*` fica nulo.

### 3.5 Verificação em dados reais

```text
faixa: metodo=HISTORICAL_QUANTILE amostra=181/39
       offset_superior=1.334.360,28 confianca=HIGH
       nota: quantil unilateral 97,5% sobre 181 resíduos de DAILY
```

---

## 4. Desvio e custo anômalo (plano §2.5)

Medidas **separadas**:

```text
desvio_cloud        = observado_cloud − esperado_cloud
custo_anomalo_cloud = max(0, observado_cloud − limite_superior_cloud)
```

`evaluate_deviation` devolve as duas, mais `above_band`, `relative_deviation` e
os limites. Exemplo verificado:

```text
dia 2026-10-31, observado 60% acima do esperado do dia:
   desvio=62.032,53   custo_anômalo=0,00   acima_da_faixa=False
```

O desvio mostra a diferença para a previsão central; o custo anômalo mostra só a
parte acima da faixa tolerada. Um dia dentro da faixa **não** gera custo
anômalo, mesmo com desvio positivo; uma queda gera desvio negativo e custo
anômalo zero.

### 4.1 Entrada na investigação

`is_candidate` exige, cumulativamente:

1. `observed > limite_superior` (acima da faixa);
2. desvio relativo ≥ `min_relative_deviation` do perfil;
3. `custo_anomalo_cloud` ≥ materialidade.

Quando falha, retorna os motivos (`WITHIN_BAND`,
`BELOW_MIN_RELATIVE_DEVIATION`, `BELOW_MATERIALITY`) — nunca um booleano opaco.

---

## 5. Materialidade (plano §2.6)

```text
materiality_by_currency = {"BRL": 100.0}
```

`materiality_for(tabela, moeda)` devolve `(valor, explicito)`. A função **não
recebe** `BillingCurrency` — teste impede a regressão
(`test_materialidade_nao_e_inferida_da_moeda_da_linha`). Moeda não declarada usa
o padrão **e declara que não é explícito**.

---

## 6. Testes

```text
apps/api/tests/test_anomaly_calibration.py   27 testes
apps/api/tests/test_anomaly_reference.py     33 testes (3 de integração com E3)
apps/api/scripts/verify_anomaly_reference.py verificação real (Trino + PostgreSQL)
```

Suíte completa: **741 passaram**, 18 falhas pré-existentes.

## 7. Limitações declaradas

- A verificação real calibrou a faixa sobre o **total observado de toda a
  base**, não por série. Isso é adequado para auditar a mecânica, mas a faixa
  por série é obrigatória na detecção: séries de ordens de grandeza diferentes
  não podem compartilhar o mesmo offset. A E4 calibra por série (ou por bucket
  de série) e o teste do detector cobre isso.
- `weekday_weights` na referência compacta guarda as medianas por dia da semana
  normalizadas do intervalo coberto; a grade diária completa é derivada na
  leitura, nunca persistida.
- A distribuição só é calculada quando a cobertura tem início e fim conhecidos
  (meses `FULL_MONTH` e `REMAINING_MONTH`); `ELAPSED_MONTH` é descartado antes.
