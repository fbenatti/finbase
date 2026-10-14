# E4 — Detectores, nota e episódios

Etapa **E4** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Evento esperado não abre caso | `detect_monthly_event` devolve `EXPECTED_EVENT` e `resolve_episode` devolve `EXPECTED_ONLY` (sem workflow) | `test_compra_esperada_nao_abre_caso` |
| Excesso persistente pertence ao mesmo caso | `case_key` estável por série + família de evento; o episódio agrupa dias consecutivos | `test_excesso_persistente_pertence_ao_mesmo_caso`, `test_gap_separa_episodios_e_o_maior_vence` |
| Reprocessamento não duplica impacto | `resolve_episode` usa `max(anterior, novo)` e nunca soma | `test_reprocessamento_nao_duplica_impacto`, `test_reprocessamento_atualiza_quando_o_novo_impacto_e_maior` |
| Desvio e custo acima da faixa separados | `evaluate_deviation` (E3) usado por todos os detectores | `test_dia_dentro_da_faixa_nao_e_candidato` |
| Nota, qualidade e razões | `anomaly_scoring` + `reason_codes` + `confidence` | 30 testes em `test_anomaly_scoring.py` |
| Consolidar episódios e contribuintes | `consolidate_family` recalcula no nível da família | `test_contribuintes_nao_duplicam_impacto` |

---

## 1. Três detectores, uma granularidade cada

| Detector | `event_type` | Granularidade | Faixa usada |
|---|---|---|---|
| `detect_daily_consumption` | `DAILY_CONSUMPTION` | diária | offset diário da referência |
| `detect_monthly_event` | `MONTHLY_EVENT` | evento mensal | faixa do evento |
| `detect_monthly_accumulated` | `MONTHLY_ACCUMULATED` | acumulado do mês | soma das faixas das cobranças |

> **Achado A4-1 — a referência é a fonte de verdade dos limites.**
> A primeira versão do detector recalculava a faixa a partir do objeto de
> calibração e ignorava `upper_cloud`/`lower_cloud` da linha de referência. Isso
> quebrava a regra do plano §4.2 ("a referência guarda a faixa") e produzia
> detecção divergente do que foi publicado. Corrigido com
> `band_from_limits`: a referência fornece os números; a calibração fornece
> método, confiança e tamanho de amostra.

### 1.1 Consumo diário

1. Cada dia é classificado como candidato com `is_candidate` (acima da faixa +
   desvio relativo mínimo do perfil + materialidade).
2. **Dias anômalos consecutivos** formam um episódio; um `gap` separa episódios.
3. O maior episódio vence; o custo anômalo é a **soma dos excessos diários** —
   cada dia conta uma vez.
4. O `case_key` **não** inclui o dia: o episódio atravessa dias e por isso o
   excesso persistente pertence ao mesmo caso.

### 1.2 Evento mensal

- Um `MonthlyEvent` carrega observado, esperado e a faixa; `pre_event_reference`
  indica se a referência foi congelada **antes** da compra (E2).
- Sem referência pré-evento → `DATA_INSUFFICIENT` (`NO_WORKFLOW`).
- Regra casou mas valor/contexto incompatível → `FORECAST_RULE_MISMATCH`
  (`NO_WORKFLOW`).
- Dentro da faixa → `EXPECTED_EVENT` (`EXPECTED_ONLY`): permanece na tabela
  analítica sem abrir workflow.
- Acima → `OPEN` com `anomalous_cost = observado − limite_superior`.
- **Deslocamento de calendário só com ≥ 3 eventos anteriores comparáveis**
  (`calendar_shift_days`): uma regra mensal não implica conhecimento do dia
  exato. A evidência entra como `CALENDAR_SHIFT_EVIDENCE`, nunca como anomalia
  por si só.

### 1.3 Acumulado do mês

Duas compras do mesmo tipo no mês:

```text
compra A: observado 30.000  esperado 30.000  limite 34.500  excesso     0
compra B: observado 40.000  esperado 30.000  limite 34.500  excesso 5.500
acumulado: observado 70.000  esperado 60.000  limite 69.000  excesso 1.000
```

O caso apresenta **as duas compras** como contribuintes e usa o excesso do
acumulado (**1.000**), não a soma dos excessos individuais (5.500) nem o gasto
total (70.000). O excesso acumulado não é somado de novo a cada dia.

---

## 2. Nota, severidade e confiança (plano §2.7)

```text
nota = round(0,55 × intensidade + 0,30 × impacto + 0,15 × persistência)
```

| Componente | Fórmula | Comportamento |
|---|---|---|
| Intensidade | `100 × clamp(ratio / 2, 0, 1)` | satura em `ratio = 2` |
| `ratio` | `desvio positivo ÷ (limite − esperado)` | piso absoluto 1 e relativo 1% |
| Impacto | `min(100, 25 × log2(1 + custo_anômalo / materialidade))` | logarítmico |
| Persistência | `100 × min(dias consecutivos / 3, 1)` | satura em 3 |

| Severidade | Faixa da nota |
|---|---|
| Baixa | `< 60` |
| Média | `60–79` |
| Alta | `80–89` |
| Crítica | `90–100` |

**Confiança é medida separada**, sobre a qualidade da *comparação*:

| Condição | Confiança |
|---|---|
| dado parcial | `LOW` |
| referência indisponível | `UNAVAILABLE` |
| faixa não calibrada | `LOW` |
| poucos eventos mensais | `LOW` |
| calibrada + janela completa de 60 dias | `HIGH` |
| calibrada com janela incompleta | `LOW` |

A nota é prioridade operacional; a confiança não entra nela.

`default_sla_hours` implementa os prazos do plano §3.3 (Crítica 4 h, Alta 24 h,
demais 72 h), configuráveis por `anomaly_config.sla_hours`.

---

## 3. Status da avaliação

Eventos reconhecidos como esperados **são publicados** com
`assessment_status = EXPECTED_EVENT`, custo anômalo zero e `case_id` nulo: o
plano exige que permaneçam na tabela analítica, sem abrir workflow. Eles são
expostos em `GET /anomalies/expected-events` (F4).

| Status | Significado | Abre workflow? |
|---|---|---|
| `OPEN` | anomalia confirmada pela faixa e materialidade | sim |
| `CANDIDATE` | provisório (dado parcial já acima do limite de um dia completo) | sim, com dados parciais |
| `EXPECTED_EVENT` | evento esperado reconhecido | **não** (`EXPECTED_ONLY`) |
| `BELOW_PROFILE` | abaixo da nota de investigação do perfil | **não** (`NO_WORKFLOW`) |
| `DATA_INSUFFICIENT` | sem referência utilizável | **não** (`NO_WORKFLOW`) |
| `FORECAST_RULE_MISMATCH` | regra casou mas valor/contexto incompatível | **não** (`NO_WORKFLOW`) |
| `NO_APPLICABLE_FORECAST_RULE` | nenhuma regra aplicável | **não** (`NO_WORKFLOW`) |

Nunca uma avaliação analítica vira caso por acidente: só `OPEN` e `CANDIDATE`
abrem workflow.

---

## 4. Episódios (plano §3.2)

`resolve_episode(detection, existing_case, valid_recovery_days, continuation)`:

| Situação | Ação | Motivo |
|---|---|---|
| evento esperado | `EXPECTED_ONLY` | permanece analítico |
| status sem workflow | `NO_WORKFLOW` | `BELOW_PROFILE`/`DATA_INSUFFICIENT`/`RULE_MISMATCH`/`NO_RULE` |
| sem caso | `CREATE` | `NOVO_EPISODIO` |
| caso ativo | `UPDATE` | impacto `max(anterior, novo)` |
| caso `CLOSED` + repetição técnica | `IGNORE` | não reabre, impacto preservado |
| caso `CLOSED` + continuação | `REOPEN` | evento auditado |
| caso `CLOSED` + ≥ 3 dias válidos de recuperação | `LINK_NEW_EPISODE` | caso novo vinculado ao anterior |

Constantes: `RECOVERY_VALID_DAYS = 3`.

`case_key = sha1(series_id | event_family | event_type[| período])`. No mensal o
período esperado entra na chave, para relacionar todas as avaliações ao **mesmo**
evento/compra; no diário não entra, para o episódio atravessar dias.

---

## 5. Contribuintes sem impacto duplicado

`consolidate_family` soma **observado, esperado e limites** dos contribuintes e
recalcula o excesso no nível da família:

```text
pai:  observado 2.000  esperado 1.000  limite 1.200  excesso 800
filho: observado 1.100  esperado 1.000  limite 1.200  excesso   0
família: observado 3.100  esperado 2.000  limite 2.400  excesso 700
```

O consolidado é 700 — nunca o gasto total (3.100) nem a soma de pai e filho
tratados como gastos distintos. A folga do filho absorve parte do excesso do
pai, que é exatamente o comportamento correto de uma faixa aditiva.

---

## 6. Testes

```text
apps/api/tests/test_anomaly_scoring.py    30 testes
apps/api/tests/test_anomaly_detector.py   27 testes
```

Suíte completa: **798 passaram**, 18 falhas pré-existentes.

## 7. Limitações declaradas

- O detector é **puro**: não lê Trino, não escreve Iceberg e não abre transação.
  A leitura da série observada e a persistência das avaliações são da E6.
- `consolidate_family` soma limites superiores (hipótese conservadora de
  excessos perfeitamente correlacionados). Uma faixa estatística para a soma
  exigiria a covariância dos resíduos, que o plano não pede.
- `NO_APPLICABLE_FORECAST_RULE` é produzido pela ausência de referência de regra
  (`matched_rule_refs` vazio) combinada com `EXPECTED_EVENT`/`MISMATCH`; a
  avaliação de qual regra casa com a série continua sendo do motor de forecast
  (`applied_rule_refs`, E2) — não há segundo casamento de regras aqui.
