# F1–F5 — Operacionalização, piloto real e fechamento

Etapa de fechamento: itens que a E10 havia listado como próximos passos.

| Item | Entrega |
|---|---|
| **F1** | Agendamento: `anomaly_detection` (inativo) e `anomaly_sla` (ativo) |
| **F2** | `GET /anomalies/cases/{id}/series` — série diária de 15 pontos derivada |
| **F3** | Imutabilidade de `anomaly_case_event` no banco (gatilho) + defaults convergidos |
| **F4** | Eventos esperados publicados na analítica + `GET /anomalies/expected-events` e `/quality` |
| **F5** | Piloto real ponta a ponta, ambiente de teste reproduzível e este documento |

---

## 1. Piloto ponta a ponta (F5) — resultado

`scripts/run_anomaly_pilot.py`, contra o ambiente real (Trino + PostgreSQL +
Iceberg), configuração 11, snapshot do forecast 399 (corte 2026-10-12):

```text
status = COMPLETED
forecast_run_id = 399
data_cutoff = 2026-10-12
reference_kind = PROSPECTIVE
reference_table = dest_modelo_focus_anomaly_reference
assessment_table = dest_modelo_focus_anomalies
reference_rows = 10903
assessment_rows = 914
series = 10903
cases_created = 0
cases_updated = 1

execução registrada: id=9 status=COMPLETED fase='Detecção publicada'
                     forecast_run=399 corte=2026-10-12 séries=10903 linhas=914
publicação corrente: run=9 referência (10903 linhas) avaliações (914 linhas) séries=10903

por status:
   BELOW_PROFILE:  611  (custo anômalo 11.378,33)
   EXPECTED_EVENT: 302  (custo anômalo 0,00)
   OPEN:             1  (custo anômalo 1.323,50)

eventos esperados: total=302
qualidade: EXPLICIT_TOLERANCE 76 · FEW_MONTHLY_EVENTS 76
```

Casos no banco: **1**.

```text
#13 nota=89 HIGH estado=NEW
    família=cloud_origem=azure-dev|servicename=Microsoft.CognitiveServices|
            servicesubcategory=Azure OpenAI|chargecategory=Usage|
            commitmentdiscounttype=Reservation
    data=2026-10-13  esperado=0,00  observado=1.431,15  anômalo=1.323,50
    referência=PROSPECTIVE
```

### 1.1 O que o piloto prova

| Propriedade | Evidência |
|---|---|
| Detecção independe da fatura | `data_cutoff=2026-10-12`, evento em `2026-10-13` |
| Referência prospectiva | `reference_kind=PROSPECTIVE`, snapshot 399 |
| Referência compacta | 10.903 linhas para 10.903 séries (1 por série × mês) |
| Evento esperado não abre caso | 302 avaliações `EXPECTED_EVENT`, **0** casos criados para elas |
| Reprocessamento não duplica | segunda execução: `cases_created=0`, `cases_updated=1` |
| Publicação consistente | tabelas no Iceberg + publicação corrente apontando para run 9 |
| Rastreabilidade | a execução guarda `forecast_run_id`, corte, tipo de referência e tabelas |

### 1.2 Série diária real (F2)

`GET /anomalies/cases/13/series`:

```text
corte: 2026-10-12 | meses: ['202610'] | dias: 15
distribuição: ['WEEKDAY_MEDIAN'] | faixa: ['HISTORICAL_QUANTILE'] | confiança: ['HIGH']
  2026-10-13 obs=1431.15 esp=0.00 sup=107.65 anômalo=1323.50
  2026-10-14 obs=None   esp=0.00 sup=107.65 anômalo=None
  2026-10-15 obs=None   esp=0.00 sup=107.65 anômalo=None
```

Dia sem coleta fica **nulo**, nunca zero — o plano proíbe tratar ausência de
coleta como gasto zero.

### 1.3 Achado para triagem humana

O caso 13 é **consumo com expectativa zero**: o forecast não projeta essa série
nos dias restantes de outubro, mas ela consumiu 1.431,15 em 13/10. Não é o
"restante zero" que o plano §2.4 protege (esse se refere a **compra mensal
esperada já realizada**, tratado em E2 pelo `select_pre_event_reference`), e sim
uma série de uso contínuo sem projeção — exatamente o tipo de evento que a
detecção existe para levantar. A decisão (esperado, configuração indevida,
problema de dados) é humana, por desenho.

---

## 2. Defeitos encontrados pelo piloto e corrigidos

O piloto real encontrou **7 defeitos** que os testes herméticos não pegavam.
Todos corrigidos com teste de regressão onde aplicável.

| # | Defeito | Sintoma | Correção |
|---:|---|---|---|
| 1 | `ForecastContract` sem `reference_kind` | `AttributeError` na etapa 4 | campo no contrato, marcado explicitamente pelo runner |
| 2 | Meses além do horizonte na referência | referência com novembro para horizonte de 10-12→10-27 | `horizon_days` filtra meses não alcançados (§4.2) |
| 3 | Séries sem observação fora da referência | reconciliação falhava em 39,94 num total de 17,7 M | referência cobre **toda** série esperada; detecção só onde há observado |
| 4 | `REFERENCE_TABLE` não importado | `NameError` na publicação | import no runner |
| 5 | `time` não importado | `NameError` no `publish_dataframe_atomic` | `import time` local (convenção do arquivo) |
| 6 | `datetime64[ns]` no Iceberg | *"does not yet support 'ns' timestamp precision"* | `_downcast_datetime_columns` → `datetime64[us, UTC]` |
| 7 | `LIMIT n OFFSET m` no Trino | `mismatched input 'OFFSET'` | `OFFSET n LIMIT m` (gramática do Trino) |
| 8 | `anomaly_run` sem o contrato | execução registrava `forecast_run=None corte=None séries=0` | `finish_run` grava contrato, corte, tipo e tabelas |

O teste `test_build_outcome_avalia_series_e_reconcilia` (hermético, sem Trino)
foi criado a partir do defeito 1 e cobre o caminho completo de avaliação.

---

## 3. Agendamento (F1)

| `schedule_type` | Cron | Ativo | Efeito |
|---|---|---|---|
| `anomaly_detection` | `0 6 * * *` | **não** | enfileira uma detecção por configuração habilitada |
| `anomaly_sla` | `*/30 * * * *` | **sim** | `DUE_SOON` (janela de 4 h) e `OVERDUE` com cópia na fila |

A detecção nasce inativa: o plano exige piloto manual antes de automatizar. O
monitor de prazos nasce ativo porque só emite notificação interna (§3.3).

Verificado em ambiente real: configuração desabilitada não enfileira;
habilitada cria tarefa + execução `SCHEDULED`; segunda chamada não duplica;
seleção inexistente não enfileira.

**Para ligar a automação da detecção:** ativar o agendamento "Detecção de
anomalias" na tela de agendamentos (ou `PUT` no `scheduled_task`).

---

## 4. Imutabilidade e defaults (F3)

Gatilho `trg_anomaly_case_event_immutable` bloqueia `UPDATE` e `DELETE` em
`anomaly_case_event`, com mensagem explícita. Casos nunca são apagados — o ciclo
termina em `CLOSED`.

A migração também converge os defaults de **todas** as colunas NOT NULL que só
tinham default do ORM, para que INSERT por SQL direto (script operacional,
verificação, backfill) funcione.

---

## 5. Eventos esperados e qualidade (F4)

Eventos esperados passaram a ser **publicados** (`assessment_status =
EXPECTED_EVENT`, custo anômalo zero, `case_id` nulo): o plano exige que
permaneçam na tabela analítica sem abrir workflow. Antes eram descartados.

```text
GET /anomalies/expected-events?limit=5   → 302 avaliações, agrupadas por família
GET /anomalies/quality                   → EXPLICIT_TOLERANCE 76 · FEW_MONTHLY_EVENTS 76
```

---

## 6. Ambiente de teste reproduzível (F5)

| Arquivo | Papel |
|---|---|
| `apps/api/requirements-dev.txt` | declara `pytest` e `pytest-asyncio` (antes só existiam ad-hoc no `.venv`) |
| `apps/api/conftest.py` | garante `apps/api` no `sys.path`, para `pytest` puro e `python -m pytest` |

A imagem de produção continua sem dependências de teste.

---

## 7. Estado final verificado

| Verificação | Resultado |
|---|---|
| Suíte do backend | **923 passaram**, 18 falhas pré-existentes |
| Frontend | `tsc -b` limpo · `vite build` OK |
| API | 21 endpoints `/anomalies`, todos respondendo (com dados reais) |
| Publicação | 10.903 referências + 914 avaliações no Iceberg |
| Casos | 1 caso real criado, reprocessamento sem duplicar |
| Agendamentos | 2 registrados (detecção inativa, prazos ativo) |
| Gatilho de imutabilidade | presente e bloqueando `UPDATE`/`DELETE` |
| Migração | idempotente, defaults convergidos |

### 7.1 Como repetir

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/ -q --no-header          # suíte
../../.venv/bin/python scripts/verify_anomaly_schema.py         # migração + gatilho
../../.venv/bin/python scripts/verify_anomaly_reference.py      # referência × snapshot
../../.venv/bin/python scripts/verify_anomaly_series.py         # série diária (Trino)
../../.venv/bin/python scripts/verify_anomaly_retrospective.py  # reconstrução
../../.venv/bin/python scripts/verify_anomaly_publication.py    # publicação + casos
../../.venv/bin/python scripts/verify_anomaly_scheduler.py      # agendamentos
../../.venv/bin/python scripts/run_anomaly_pilot.py             # piloto ponta a ponta
```

---

## 7.1 Teste ao vivo da automação (F1 ligado de verdade)

Sequência executada no ambiente de DEV com o **agendador e o worker reais**:

```text
18:00:00  agendamento anomaly_detection dispara (cron */2) → tarefa 8487
18:05:36  worker reiniciado assume a tarefa (o processo antigo não conhecia o
          task_type: foi preciso reiniciar) → COMPLETED em ~70s
          anomaly_run 10: trigger=SCHEDULED forecast_run=399 corte=2026-10-12
          10.903 séries · 914 linhas · cases_created=1
          caso #14: ASSIGNED · HIGH · nota 89 · responsável=6
          regra de roteamento=azure-dev-financeiro · prazo 24h
          notificação ASSIGNMENT → usuário 6
```

Ciclo completo do caso #14 pela API, com histórico auditado de 11 eventos:

```text
ASSIGNED → acknowledge → INVESTIGATING → update_investigation → decide
        → (beco sem saída corrigido) → cancel_remediation → DECIDED
        → start_remediation → IN_REMEDIATION → start_verification → VERIFYING
        → validate_result → RETROSPECTIVE → record_retrospective → close → CLOSED
```

Com decisão, causa (`MISCONFIGURATION`), evidências, aprendizado, medida
preventiva e resultado de verificação gravados. O conflito de versão respondeu
**409** com a mensagem correta.

Reprocessamento com o caso encerrado (disparo automático às 18:08):

```text
anomaly_run 11: cases_created=0 cases_updated=0
caso #14 permaneceu CLOSED na versão 11, mesmo custo anômalo
evento REPROCESSING_IGNORED: "caso encerrado não reabre por simples repetição
do processamento"
```

Monitor de prazos (mesmo código que o APScheduler chama):

```text
janela de 48h   → {'due_soon': 2, 'overdue': 0}
prazo vencido   → {'due_soon': 0, 'overdue': 2}
3ª execução     → {'due_soon': 0, 'overdue': 0}   ← deduplicação

notificações: #3 ASSIGNMENT → usuário 6
              #4 DUE_SOON   → usuário 6     #5 DUE_SOON  → fila FinOps
              #6 OVERDUE    → usuário 6     #7 OVERDUE   → fila FinOps
```

Detecção manual via `POST /anomalies/configs/11/run` gerou o caso #15, também
roteado para o usuário 6 (`azure-dev-financeiro`), com prazo de 24h.

### 7.1.1 Dois defeitos encontrados **ao vivo** e corrigidos

| # | Defeito | Sintoma observado | Correção |
|---:|---|---|---|
| A7-1 | Roteamento nunca era chamado na criação do caso | todo caso em `NEW`, sem responsável e sem notificação | `apply_detection_to_case` resolve o roteamento e nasce `ASSIGNED` ou na fila |
| A1-1 | `decide(CONFIRMED_ACTION)` saltava direto para `IN_REMEDIATION` | caso #14 **preso**: `start_remediation`, `start_verification`, `validate_result`, `record_retrospective` e `close` todos recusados | a decisão para em `DECIDED`; `start_remediation` exige ação e marca `remediation_started_at` |

Nenhum teste hermético pegava os dois: o primeiro porque a função estava correta
e simplesmente não era chamada; o segundo porque só se revela ao percorrer a
sequência completa de comandos.

### 7.1.2 Nota operacional

O worker é um processo **persistente**: depois de adicionar um `task_type` novo,
reiniciar o worker é obrigatório. O processo antigo ignorou a tarefa agendada por
5 minutos até o restart — o sintoma é "tarefa `QUEUED` que ninguém pega".

## 8. Limitações remanescentes

1. **Faixa por granularidade de série** — a calibração usa os resíduos da própria
   série (correto), mas séries com histórico curto caem em tolerância explícita;
   em DEV, 76 avaliações carregam `EXPLICIT_TOLERANCE`/`FEW_MONTHLY_EVENTS`.
2. **Séries sem observação** geram referência (para reconciliar) mas nenhuma
   avaliação — o dashboard mostra a expectativa sem observado, o que é honesto,
   porém não sinaliza "fonte parada". Os diagnósticos de `SOURCE_VALIDITY` do
   forecast (E0 §2.4) cobrem esse ângulo e podem ser expostos como razão.
3. **Transação distribuída** — Iceberg e PostgreSQL não compartilham transação; a
   ordem de commit preserva o invariante exigido, mas um crash entre os dois
   commits exige nova execução.
4. **Escala do piloto** — 10.903 séries em ~1 min por execução; a carga completa
   (12.547 séries, 180 dias) é viável, mas a primeira execução de produção deve
   ser agendada fora do horário de pico.
5. **Vencimento de prazos** depende do agendador ativo; em ambientes onde o
   scheduler está desligado, `GET /anomalies/cases?overdue_only=true` continua
   mostrando o vencimento no dashboard.
6. **Cadência da detecção** ficou em `0 * * * *` (horária) para observação do
   piloto. Para produção, `0 6 * * *` (diária) é o padrão do plano.
7. **Exclusão de caso para limpeza** exige desabilitar o gatilho
   explicitamente — foi o que fiz para recriar o caso de teste. Isso é
   intencional (histórico imutável), mas significa que limpeza de dados de teste
   é uma operação consciente.
