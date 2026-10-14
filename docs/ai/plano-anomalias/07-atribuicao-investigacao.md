# E7/E8 — Atribuição, notificação, investigação, remediação e encerramento

Etapas **E7** e **E8** do plano *Gestão de anomalias de custo cloud: detecção
até encerramento*.

## Aceite das etapas

| Critério do plano | Como foi cumprido | Evidência |
|---|---|---|
| Caso tem responsável ou fila explícita | Roteamento por especificidade; sem regra/ambíguo → fila FinOps | `test_maior_especificidade_prevalece`, `test_ambiguidade_e_rejeitada_e_vai_para_a_fila` |
| Notificações não repetem a cada carga | `dedup_key` único por caso + evento + destinatário | `test_dedup_por_caso_evento_destinatario`; índice único em E1 |
| Decisão possui justificativa | `decide` exige `decision`, `cause_category`, `note` e evidência | E1 `test_decide_exige_...` |
| Caso não encerra apenas porque o forecast aumentou | `close` exige decisão, causa, evidência, aprendizado, medida preventiva e resultado de verificação quando houve remediação | E1 `test_encerramento_exige_...` |
| Gap não comprova sucesso | `apply_verification_days` zera a sequência em gap e em dado parcial | `test_gap_interrompe_a_sequencia`, `test_dado_parcial_nao_confirma_recuperacao` |
| Encerramento possui resultado e aprendizado | Campos obrigatórios validados antes de `close` | E1 + `_case_out` |
| Concorrência → 409 | `case_version` obrigatório nas transições | E1 `test_versao_divergente_e_conflito_409`; router converte para HTTP 409 |

---

## 1. Máquina de estados → comandos HTTP

Um único endpoint aplica os comandos:

```text
POST /anomalies/cases/{case_id}/transition
{ "command": "decide", "case_version": 3, "decision": "CONFIRMED_ACTION",
  "cause_category": "MISCONFIGURATION", "note": "...", "evidence": ["..."] }
```

O middleware RBAC mapeia `/transition` para a ação `update` do módulo
`anomalies`; o **escopo** (`assign`, `investigate`, `decide`, `close`) é
validado dentro do handler, porque depende do comando do corpo.

| Comando | Escopo |
|---|---|
| `assign`, `return_to_queue` | `assign` |
| `acknowledge`, `start_investigation`, `update_investigation` | `investigate` |
| `decide`, `redecide`, `start_remediation`, `cancel_remediation` | `decide` |
| `start_verification`, `fail_verification`, `validate_result`, `record_retrospective`, `close`, `reopen` | `close` |

Teste garante que **todo** comando do workflow tem escopo e que não há escopo
órfão (`test_todo_comando_do_workflow_tem_escopo`).

---

## 2. Atribuição e roteamento (E7)

`resolve_routing(rules, dimensions)`:

- casa a regra com **mais critérios atendidos** (maior especificidade);
- empate na maior especificidade é **ambiguidade**: nenhum responsável é
  atribuído e o caso vai para a fila FinOps — nunca uma escolha arbitrária;
- sem regra aplicável → fila FinOps;
- comparação é normalizada (case-insensitive, sem espaços).

**Nunca inferir usuário de texto livre em tags** (`test_nao_infere_usuario_de_texto_livre_em_tags`):
uma tag `owner=joao.silva` não vira responsável. Só regra explícita atribui.

`assign_case` calcula o prazo de ciência:

```text
due_at = agora + sla_hours[severidade]
```

Padrões: Crítica 4 h, Alta 24 h, demais 72 h (`anomaly_config.sla_hours`).
Tempo corrido no v1.

### 2.1 Notificações internas

| Tipo | Quando |
|---|---|
| `ASSIGNMENT` | atribuição |
| `SEVERITY_INCREASE` | aumento de severidade |
| `DUE_SOON` / `OVERDUE` | vencimento de prazo (o vencido sempre aparece também na fila) |
| `VERIFICATION_COMPLETE` | conclusão da verificação |
| `FINOPS_QUEUE` | caso sem responsável |

`dedup_key = sha1(case:{id}:{kind}:{user|queue})` com **índice único** no banco:
um novo ETL que reavalia o mesmo caso não cria outra notificação.

Endpoints: `GET /anomalies/notifications` (com contador de não lidas e
`include_queue`), `POST /anomalies/notifications/{id}/read?acknowledge=true`.

---

## 3. Investigação e decisão

`decide` exige, cumulativamente: `decision` no vocabulário, `cause_category` no
vocabulário, `note` (justificativa) e evidência. Categorias de causa (plano
§3.4) incluem `UNDETERMINED` — o encerramento aceita causa não determinada desde
que seja **explícita**.

O sistema apresenta evidências (`GET /cases/{id}/contributors`) e não afirma
causalidade automaticamente.

---

## 4. Remediação (E8)

`POST /anomalies/cases/{id}/actions` e `PUT /anomalies/actions/{id}`:

| Campo | Papel |
|---|---|
| `description` | obrigatório |
| `action_type` | `CORRECTIVE` \| `PREVENTIVE` |
| `owner_user_id`, `due_at` | responsável e prazo |
| `status` | `PLANNED` \| `IN_PROGRESS` \| `COMPLETED` \| `CANCELLED` |
| `executed_at` | gravado ao concluir |
| `evidence`, `external_reference` | prova da execução |
| `mandatory` | ação obrigatória pendente bloqueia a verificação |

`start_remediation` exige ≥ 1 ação corretiva cadastrada; `start_verification`
exige que **nenhuma** ação obrigatória esteja pendente. O v1 **não** executa
remediação: o responsável registra o que fez na cloud.

---

## 5. Verificação (E8, plano §3.7)

`apply_verification_days` percorre os dias posteriores à ação:

- **dia válido** = observado dentro do limite **e** sem dado parcial;
- **gap** (dia ausente) zera a sequência — o plano exige dias válidos
  **consecutivos**;
- **dado parcial nunca confirma recuperação**;
- ao atingir 3 dias válidos: `SUCCESS_PENDING_VALIDATION` — habilita **validação
  humana**, não encerra sozinho;
- desvio persistente devolve o caso à remediação (`fail_verification`).

Para eventos mensais, a verificação acompanha o **próximo evento esperado** e a
ausência de dados não confirma sucesso.

A referência original do caso é preservada: `current_reference_id` só muda em
reabertura explícita (E6). Atualizações normais do forecast **não** absorvem o
aumento nem encerram o caso silenciosamente.

---

## 6. Encerramento e retrospectiva (E8, plano §3.8)

`close` exige: decisão, causa (ou `UNDETERMINED`), evidência, `learning`,
`preventive_measure` **ou** `no_preventive_measure_reason`, e resultado de
verificação quando houve remediação.

`reopen` exige justificativa, incrementa `reopen_count`, limpa datas de
encerramento e verificação e devolve o caso a `INVESTIGATING`.

Aprendizados podem gerar ações de melhoria (`action_type = PREVENTIVE`); elas
**não** são aplicadas automaticamente.

Economia não é registrada apenas porque o custo caiu: o v1 apresenta custo
anômalo observado e resultado da verificação, sem atribuir causalidade.

---

## 7. Endpoints

```text
GET    /anomalies/configs
PUT    /anomalies/configs/{id}
GET    /anomalies/coverage
POST   /anomalies/configs/{id}/run
GET    /anomalies/runs
POST   /anomalies/runs/{id}/cancel
GET    /anomalies/cases                 (filtros: state, severity, assignee, queue_only,
                                         overdue_only, period, config_id, order_by)
GET    /anomalies/cases/summary
GET    /anomalies/cases/{id}            (com painel de etapas e ações)
GET    /anomalies/cases/{id}/events
GET    /anomalies/cases/{id}/contributors
POST   /anomalies/cases/{id}/transition
POST   /anomalies/cases/{id}/actions
PUT    /anomalies/actions/{id}
GET    /anomalies/notifications
POST   /anomalies/notifications/{id}/read
GET    /anomalies/metrics
```

Paginação padrão 50, máximo 200; ordenação por `score` ou `due`. Toda transição
é auditada (`audit()`) antes do `commit`.

Verificado em execução: **17 endpoints** publicados e respondendo
(`GET /anomalies/configs` → `200`, `GET /anomalies/cases/summary` → `200`).

---

## 8. Testes

```text
apps/api/tests/test_anomaly_workflow.py   20 testes
```

Suíte completa: **861 passaram**, 18 falhas pré-existentes.

## 9. Limitações declaradas

- `expire_overdue_notifications` precisa ser chamado por um agendador; o v1 não
  registra uma `scheduled_task` para isso. O endpoint
  `GET /cases?overdue_only=true` já expõe o vencimento no dashboard.
- `DUE_SOON` está no vocabulário mas ainda não é emitido (só `OVERDUE`).
- A verificação usa os dias publicados na avaliação corrente; o monitoramento
  agendado que recalcula a sequência automaticamente entra com o worker de
  reavaliação (o serviço `apply_verification_days` já é puro e testado).
- A API não executa remediação na cloud — por contrato do v1.
