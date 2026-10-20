# E6 — Worker, publicação e composição

Etapa **E6** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## Aceite da etapa

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Falha conserva a publicação | Iceberg com staging + commit atômico; `anomaly_publication` corrente só troca depois | Verificação real: após falha simulada a corrente continuou na publicação anterior |
| Forecast não apaga anomalias | Tabelas próprias (`dest_modelo_focus_anomaly_reference`, `dest_modelo_focus_anomalies`), nunca a Planning | `test_projecao_da_planning_nao_traz_colunas_de_anomalia` |
| Anomalia não aumenta custo financeiro | `tipo='anomalia'`, `realizado` nulo, `is_financial_additive=false`, `realizado_cloud = anomalous_cost_cloud` | `test_linha_de_anomalia_aditiva_e_rejeitada`, `test_composicao_que_infla_o_total_financeiro_falha` |
| Task, progresso e cancelamento | `task_type='anomaly_detection'` com progresso por fase e aborto | `test_anomaly_release.py`, verificação real |
| Publicação consistente | staging → validação (unicidade + reconciliação) → commit → PostgreSQL | `verify_anomaly_publication.py` |
| Referência de casos ativos preservada | `current_reference_id` congelado enquanto o caso está ativo | Verificação real (reabertura explícita troca a referência) |
| Compor tabela irmã com Planning | Projeção explícita + `UNION ALL` | `sister_table_union_sql` |

---

## 1. Fluxo

```text
Silver atualizada
    → forecast normal
    → referência compacta            (E2/E3)
    → avaliação de dados novos       (E4)
    → atualização do episódio        (E4/E7)
    → roteamento e notificações      (E7)
    → monitoramento de ações e verificação (E8)
```

`task_type = 'anomaly_detection'` registrado em três pontos (como o motor
exige): `WORKER_TASK_TYPES`, a cadeia de despacho em `worker.py` e
`_ANALYTIC_WRITER_TASK_TYPES` (a escrita Silver é serializada com os demais
escritores analíticos).

Fases reportadas em `etl_task.phase` / `processada`:

```text
1/6 Carregando execuções do forecast
2/6 Lendo expectativa do snapshot do forecast
3/6 Lendo consumo observado (realizado_cloud)
4/6 Avaliando séries e consolidando episódios
5/6 Atualizando episódios
6/6 Publicando referência e avaliações
```

Cancelamento é reavaliado antes das etapas caras; a task nunca é sobrescrita de
`CANCELLED` para `FAILED`.

---

## 2. Ordem de commit e consistência

A ordem é deliberada:

1. montar referência e avaliações;
2. validar **unicidade** (`assessment_id`) e **reconciliação** (referência ×
   snapshot, mês a mês);
3. resolver os casos no PostgreSQL na **mesma transação** (a linha publicada já
   carrega `case_id`);
4. publicar reference e assessments no Iceberg (staging → commit atômico);
5. trocar a publicação corrente e finalizar a execução;
6. `commit`.

Consequências:

| Falha em | Efeito |
|---|---|
| validação | nada é publicado; casos não são criados |
| resolução de casos | rollback; publicação anterior intacta |
| publicação Iceberg | rollback dos casos; publicação anterior intacta |
| troca de publicação | rollback; Iceberg já publicado, mas a corrente não muda |

> **Achado A6-1 — `case_id` obriga resolver o caso antes de publicar.**
> A primeira versão publicava as avaliações e só depois criava os casos, o que
> deixava `case_id` nulo na tabela publicada. A ordem foi invertida: o caso é
> resolvido (e o `flush` garante o id) antes da publicação, dentro da mesma
> transação.

### 2.1 Publicação atômica no Iceberg

`iceberg_service.publish_dataframe_atomic(namespace, tabela, dataframe)`:
staging (`<tabela>_stg_<ns>`) → `_merge_staged_into_canonical_table`
(`DELETE` + `append` em **uma** transação) → remoção do staging. Falha antes do
commit remove o staging e não toca a tabela canônica.

---

## 3. Composição com Planning (plano §4.4)

A Planning **não** é alterada. A composição é declarativa:

```sql
SELECT <21 colunas comuns>, CAST(NULL AS DOUBLE) AS is_financial_additive, ...
  FROM iceberg.silver."dest_modelo_focus_planning" p
UNION ALL
SELECT <21 colunas comuns>, CAST(FALSE AS BOOLEAN) AS is_financial_additive,
       a."case_id", a."assessment_id", a."assessment_status", ...
  FROM iceberg.silver."dest_modelo_focus_anomalies" a
```

- projeção **explícita** (nunca `SELECT *`), para que uma coluna nova na
  Planning não mude a semântica da união;
- `financial_totals_sql` calcula o total financeiro **com** e **sem** a camada
  de anomalia; os dois números têm de ser idênticos;
- `assert_totals_unchanged` falha se a composição inflar o total;
- `allowed_financial_types()` mantém `anomalia` fora dos tipos financeiros.

---

## 4. Ciclo de vida do caso (verificado em PostgreSQL real)

```text
caso criado:                      id=2  ação=CREATE
reprocessamento (900 → 400):      ação=UPDATE  impacto=900  versão=2
caso encerrado + repetição:       ação=IGNORE  estado=CLOSED reopen_count=0
continuação do episódio:          ação=REOPEN  estado=INVESTIGATING reopen_count=1
histórico: ['CASE_CREATED','CASE_UPDATED','REPROCESSING_IGNORED','CASE_REOPENED']
evento esperado:                  ação=EXPECTED_ONLY  caso=None
```

- o impacto é `max(anterior, novo)` — **nunca** a soma;
- reprocessar não duplica o caso (`count(case_key) == 1`);
- caso encerrado não reabre por repetição técnica e mantém o impacto;
- a reabertura é explícita (`continuation=True`), incrementa `reopen_count` e
  registra evento auditado;
- todo evento entra em `anomaly_case_event`, que é **imutável** (E1).

### 4.1 Publicação corrente

```text
publicação 1: id=7 corrente=True anterior=None
publicação 2: id=8 corrente=True anterior(run)=5 | corrente agora=8
publicações correntes para a configuração: 1
após falha simulada, corrente = 8      ← preservada
```

Índice único parcial `uq_anomaly_publication_current` garante **uma** corrente
por configuração no banco, não só no código.

---

## 5. Índice de arquivos

| Arquivo | Papel |
|---|---|
| `app/services/anomaly_observed.py` | leitura do observado em `realizado_cloud` (parser do forecast, sem seleção por `_billing_period`) |
| `app/services/anomaly_runner.py` | orquestração: contrato → expectativa → observado → detecção → casos → publicação |
| `app/services/anomaly_release.py` | publicação atômica, publicação corrente e ciclo de vida dos casos |
| `app/services/anomaly_composition.py` | `UNION ALL` com Planning e guardas financeiras |
| `app/services/iceberg_service.py::publish_dataframe_atomic` | staging + commit atômico para um DataFrame |

## 6. Testes

```text
apps/api/tests/test_anomaly_release.py            21 testes
apps/api/scripts/verify_anomaly_publication.py    PostgreSQL real (rollback ao final)
apps/api/scripts/verify_anomaly_schema.py         migração idempotente real
```

Suíte completa: **841 passaram**, 18 falhas pré-existentes.

## 7. Limitações declaradas

- `execute_detection` exige a configuração **habilitada**: a configuração inicial
  nasce desabilitada (E1) e habilitar é decisão explícita (E10).
- A publicação Iceberg e o commit do PostgreSQL **não** formam uma transação
  distribuída. A ordem escolhida garante o invariante do plano (falha preserva a
  publicação anterior); um crash entre o commit do Iceberg e o commit do
  PostgreSQL deixa dados publicados sem publicação corrente — situação
  detectável e recuperável por nova execução, e nunca uma publicação corrente
  apontando para dados inexistentes.
- Notificações (fase 5 do fluxo) são E7; o contador
  `anomaly_run.notifications_created` já existe para receber o número.
- `case_id` na tabela de avaliação é preenchido no momento da publicação; casos
  criados depois (por reabertura ou novo episódio) aparecem na publicação
  seguinte.
