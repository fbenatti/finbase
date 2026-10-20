# E5 — Retrospectiva de dados

Etapa **E5** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Dados posteriores não entram no cálculo | O corte é o **último dia do mês anterior**; o `simulated_as_of` é derivado dele para que `effective_cutoff` fique no corte desejado | `test_corte_antes_do_mes_e_o_ultimo_dia_do_mes_anterior`, `assert_cutoff_excludes_month` |
| Reconstrução fica identificada | `reference_kind = RETROSPECTIVE` + `limitation_codes` + notas | `test_reconstrucao_fica_identificada_com_todas_as_limitacoes` |
| Reconstruir referências em lotes | `plan_reconstruction` agrupa meses; `run_reconstruction_batches` executa por lote | `test_lotes_de_meses`, `test_reconstrucao_em_lotes_usa_modo_simulado_e_nao_publica_planning` |
| Preservar cortes e versões disponíveis | Prefere execução real com corte anterior; declara versão divergente | `test_prefere_execucao_historica_com_corte_anterior_ao_mes`, `test_versao_de_configuracao_divergente_e_declarada` |
| Não publicar Planning | `simulated_as_of` sempre preenchido; guarda explícita | `test_reconstrucao_nunca_publica_planning`, `test_reconstrucao_que_publicasse_planning_falha_alto` |
| Registrar limitações históricas | 8 códigos de limitação + resumo legível | `limitation_summary` |

---

## 1. Duas origens de referência, nesta ordem

### 1.1 `HISTORICAL_RUN` — preferida

Uma execução de forecast **real** cujo `cutoff_date` é anterior ao primeiro dia
do mês alvo. Ela é, para aquele mês, a previsão que existia na época. Nada é
recalculado.

Verificação real (40 execuções `COMPLETED` em DEV):

```text
202608: kind=HISTORICAL_RUN corte=2026-07-31 run=368
202609: kind=HISTORICAL_RUN corte=2026-08-31 run=369
202610: kind=HISTORICAL_RUN corte=2026-09-30 run=400
```

A escolha é sempre pela **mais recente** com corte anterior ao mês
(`test_escolhe_a_execucao_historica_mais_recente`).

### 1.2 `RECONSTRUCTED` — com o motor compartilhado

Sem execução histórica, o mês é reconstruído:

```text
cutoff            = último dia do mês anterior ao alvo
simulated_as_of   = cutoff + cutoff_days
effective_cutoff  = simulated_as_of − cutoff_days = cutoff
```

Com `cutoff_days = 2` e mês alvo `202608`:

```text
cutoff = 2026-07-31   simulated_as_of = 2026-08-02
```

O motor é o **mesmo** (`run_forecast`), em modo simulado. Não há segundo motor.

### 1.3 `UNAVAILABLE`

Só quando a reconstrução não é autorizada
(`allow_reconstruction=False`) e não existia execução histórica — nunca por
conveniência.

---

## 2. Garantias

### 2.1 Dados posteriores não entram

`planning_publication_allowed` e `assert_cutoff_excludes_month` verificam, por
mês, que `effective_cutoff < primeiro dia do mês`. Um mês com corte inválido é
reportado em `failed` e **não** é contabilizado como concluído.

### 2.2 Nenhuma publicação de Planning

O motor já tem a guarda (`if simulated_as_of is None: write_forecast_planning`).
A E5 a torna explícita e verificável:

```text
planning_publication_allowed(simulated_as_of=2026-08-02) == False
assert_no_planning_published({"planning_table": "..."})  -> RuntimeError
```

A reconstrução que devolvesse `planning_table` falha alto em vez de alterar a
Planning corrente (`test_reconstrucao_que_publicasse_planning_falha_alto`).

### 2.3 Tabela de resultado própria

Cada mês grava em `anomaly_ref_{config_id}_{yyyymmdd}` — nunca em
`result_run_{id}`, para não sobrescrever o snapshot de uma execução normal.
Teste: `test_cada_mes_usa_sua_propria_tabela_de_resultado`.

### 2.4 Nada é apresentado como previsão original

Toda reconstrução carrega `NOT_THE_ORIGINAL_FORECAST` e a nota
*"não é a previsão originalmente disponível"*.

---

## 3. Códigos de limitação

| Código | Significado |
|---|---|
| `FROM_HISTORICAL_RUN` | referência de execução real do forecast |
| `RECONSTRUCTED_FROM_CURRENT_DATA` | referência reconstruída posteriormente |
| `CURRENT_RULES_USED` | regras **atuais** aplicadas na reconstrução |
| `LATER_REVISIONS_POSSIBLE` | a fonte pode conter revisões posteriores ao corte |
| `NOT_THE_ORIGINAL_FORECAST` | não é a previsão originalmente disponível |
| `CONFIG_VERSION_DIFFERS_FROM_CURRENT` | versão de configuração da execução difere da atual |
| `NO_HISTORICAL_RUN_BEFORE_MONTH` | não havia execução antes do mês |
| `PLANNING_NOT_PUBLISHED` | Planning corrente não foi alterada |

As regras da reconstrução são as ativas no momento da execução (o motor as
carrega no início); o plano declara isso em vez de fingir que recuperou as
regras da época. Preferir versões históricas recuperáveis é o caminho
`HISTORICAL_RUN`, que usa o `config_snapshot` congelado.

---

## 4. Execução em lotes

`run_reconstruction_batches`:

- itera apenas os meses `RECONSTRUCTED` (os `HISTORICAL_RUN` já têm referência);
- um mês com falha **não** derruba o lote;
- reporta progresso por callback (`progress(done, total, nome)`) — integra com
  o `etl_task` na E6;
- respeita cancelamento (`should_abort`);
- devolve `limitation_codes` e `planning_published = False`.

```text
lotes de 3 meses: [['202601','202602','202603'], ['202604','202605','202606'], ['202607']]
```

---

## 5. Testes

```text
apps/api/tests/test_anomaly_retrospective.py    22 testes
apps/api/scripts/verify_anomaly_retrospective.py verificação real (plano + execução opcional)
```

Suíte completa: **820 passaram**, 18 falhas pré-existentes.

## 6. Limitações declaradas

- A leitura continua sendo feita **pelo motor**, em uma execução por mês
  reconstruído. Não há varredura dia a dia do billing: a agregação acontece no
  Trino com o mesmo contrato do forecast.
- `HISTORICAL_RUN` não garante que a execução da época tenha usado exatamente as
  regras que o caso julga hoje — só que ela foi produzida antes do mês. A versão
  da configuração é declarada quando diverge.
- A reconstrução não gera `anomaly_publication`: publicar uma referência
  retrospectiva como "corrente" misturaria auditoria com operação (E6 trata a
  publicação).
