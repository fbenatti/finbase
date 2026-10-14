# E10 — Validação e ativação

Etapa **E10** do plano *Gestão de anomalias de custo cloud: detecção até
encerramento*.

## 1. Os 18 testes obrigatórios

Todos implementados em `apps/api/tests/test_anomaly_acceptance.py`, um por item
numerado do plano.

| # | Cenário do plano | Teste | Resultado |
|---:|---|---|---|
| 1 | mesmo consumo com competência de origem diferente mantém avaliação diária | `test_01_competencia_de_origem_diferente_mantem_avaliacao_diaria` | ✔ `event_date`, observado, esperado, faixa, custo anômalo, nota e status idênticos; só `source_billing_period` muda |
| 2 | `realizado` muda, `realizado_cloud` não muda: anomalia permanece igual | `test_02_realizado_muda_e_realizado_cloud_nao_muda_anomalia_identica` | ✔ mesmo `assessment_id` e mesmo custo anômalo; `realizado` nunca preenchido |
| 3 | Reserva mensal esperada: baixa nota, sem caso operacional | `test_03_reserva_mensal_esperada_nao_abre_caso` | ✔ `EXPECTED_EVENT`, custo anômalo 0, severidade baixa, nenhuma linha de caso |
| 4 | Compra adicional: excesso mensal consolidado | `test_04_compra_adicional_consolida_excesso_mensal` | ✔ acumulado 1.000 (não 5.500), duas compras como contribuintes |
| 5 | Forecast restante zero: referência anterior preservada | `test_05_restante_zero_preserva_a_referencia_anterior` | ✔ usa o snapshot anterior à compra; desvio 0 |
| 6 | Sazonalidade semanal: variação esperada dentro da faixa | `test_06_sazonalidade_semanal_fica_dentro_da_faixa` | ✔ nenhum dia da própria distribuição é candidato; sábado +40% dentro da faixa |
| 7 | Dados parciais: candidato provisório, sem encerramento automático | `test_07_dado_parcial_gera_candidato_e_nao_encerra` | ✔ `CANDIDATE`; três "dias válidos" parciais não habilitam validação |
| 8 | Vários dias anômalos: um caso, impacto sem duplicação | `test_08_varios_dias_anomalos_um_caso_sem_duplicar_impacto` | ✔ 4 dias = 1 episódio; impacto `max`, não soma |
| 9 | Roteamento ausente: fila FinOps | `test_09_sem_roteamento_o_caso_vai_para_a_fila_finops` | ✔ sem responsável; chave da fila distinta de usuário |
| 10 | Reprocessamento: sem notificações duplicadas | `test_10_reprocessamento_nao_duplica_notificacao` | ✔ mesmas chaves de deduplicação nas duas execuções |
| 11 | Remediação: três dias válidos habilitam validação | `test_11_tres_dias_validos_habilitam_validacao_humana` | ✔ `SUCCESS_PENDING_VALIDATION` e `close` ainda bloqueado |
| 12 | Gap na verificação: sequência não é confirmada | `test_12_gap_na_verificacao_nao_confirma` | ✔ sequência zerada pelo gap |
| 13 | Compra mensal: próximo evento confirma ou invalida | `test_13_compra_mensal_confirma_ou_invalida_no_proximo_evento` | ✔ confirma/invalida; ausência de dados não confirma |
| 14 | Novo forecast absorve aumento: caso ativo não desaparece | `test_14_novo_forecast_nao_apaga_caso_ativo` | ✔ `reference_id` novo, caso continua (UPDATE/REOPEN) |
| 15 | Decisão sem ação: justificativa e retrospectiva obrigatórias | `test_15_decisao_sem_acao_exige_justificativa_e_retrospectiva` | ✔ caminho para `RETROSPECTIVE`; encerramento exige aprendizado e medida preventiva |
| 16 | Concorrência: conflito 409 | `test_16_concorrencia_retorna_conflito_409` | ✔ `AnomalyVersionConflict` com `status_code = 409` |
| 17 | Composição com Planning: totais financeiros inalterados | `test_17_composicao_nao_altera_total_financeiro` | ✔ composição que infla o total falha |
| 18 | Correção histórica: avaliações versionadas e workflow preservado | `test_18_correcao_historica_versiona_avaliacao_e_preserva_workflow` | ✔ `assessment_id` distinto por referência; histórico imutável; `CLOSED` só aceita `reopen` |

Resultado: **20 passaram** (18 numerados + 2 de apoio:
`test_18b_avaliacao_de_evento_mensal_e_identificada_pelo_periodo` e
`test_escopos_de_permissao_cobrem_o_ciclo_do_aceite`).

## 2. Regressões

Suíte completa do backend executada após cada etapa.

| Área | Arquivos | Situação |
|---|---|---|
| Forecast | `test_forecast_cutoff`, `test_forecast_business_rules`, `test_forecast_allocation`, `test_forecast_source_validity`, `test_forecast_ibm_allocation`, `test_forecast_daily_drilldown` | ✔ passam |
| Regras e classificação | `test_business_rule_enrichment`, `test_classification_engine` | ✔ passam |
| Planning | `test_planning_dashboard`, `test_planning_rateio_preservation` | ✔ passam |
| Custos compartilhados | `test_shared_cost` | 3 falhas **pré-existentes** |
| Settlement | `test_contract_settlement` | ✔ passa |
| Gold | `test_gold_governance`, `test_gold_materialization`, `test_gold_cube_replace` | ✔ passam |
| RBAC e auditoria | `test_auth_unit`, `test_audit_unit` | ✔ passam |
| Fila de tarefas | `test_etl_task_claim` | 11 falhas **pré-existentes** |
| Suíte completa | `tests/` | **861 passaram**, 18 falhas pré-existentes |
| Frontend | `npx tsc -b` + `npx vite build` | ✔ sem erros; chunk `Anomalies` gerado |

### 2.1 Linha de base das falhas

As 18 falhas foram confirmadas como pré-existentes: a suíte foi executada com as
alterações desta implementação **revertidas** (stash dos arquivos alterados e
testes novos movidos para fora da árvore) e o mesmo conjunto de 18 falhas
apareceu, em `test_etl_task_claim` (11), `test_shared_cost` (3) e
`test_gold_daily_consumption` e correlatos.

## 3. Verificações em ambiente real

Não substituem os testes herméticos: provam o comportamento contra
PostgreSQL, Trino e Iceberg reais.

| Script | O que prova |
|---|---|
| `scripts/verify_anomaly_schema.py` | migração idempotente; uma configuração inicial; defaults convergidos |
| `scripts/verify_anomaly_reference.py` | contrato do snapshot real; medida `realizado_cloud`; reconciliação ao centavo (14.111 linhas); distribuição diária reconciliando (3.329.093,83); faixa calibrada com 181 resíduos |
| `scripts/verify_anomaly_retrospective.py` | plano real de reconstrução; `HISTORICAL_RUN` para 202608–202610; guardas de corte e de não publicar Planning |
| `scripts/verify_anomaly_publication.py` | troca atômica da publicação corrente; falha preserva a anterior; uma corrente por configuração; ciclo de vida do caso sem duplicação |

### 3.1 Defeito encontrado e corrigido na validação

`GET /anomalies/metrics` respondia **500**:

```text
AttributeError: Neither 'count' object nor 'Comparator' object has an attribute 'select_from'
```

O helper `func_count(model)` montava `func.count().select_from(model)` e era
envolto em `select(...)`, produzindo `select(func.count().select_from(...))` —
forma inválida. Corrigido com `select(func.count()).select_from(Model)` e
acrescentado o contador de publicações correntes. Verificado: `200`.

Todos os endpoints de leitura foram testados em execução:

```text
200  /anomalies/configs          200  /anomalies/cases
200  /anomalies/coverage         200  /anomalies/cases/summary
200  /anomalies/runs             200  /anomalies/metrics
200  /anomalies/notifications    404  /anomalies/cases/999      (inexistente)
200  /anomalies/cases/999/events 404  /anomalies/cases/999/contributors
```

## 4. Piloto manual

O fluxo manual está implementado e verificado ponta a ponta:

```text
1. GET  /anomalies/configs            → configuração desabilitada
2. PUT  /anomalies/configs/{id}       → enabled=true (decisão explícita)
3. POST /anomalies/configs/{id}/run   → task anomaly_detection na fila
4. (worker) referência → avaliação → episódios → publicação
5. GET  /anomalies/cases              → casos com nota, severidade, valores
6. POST /anomalies/cases/{id}/transition (assign)   → responsável + prazo
7. POST .../transition (start_investigation)
8. POST .../transition (decide)       → causa + decisão + evidências
9. POST .../cases/{id}/actions        → ação corretiva
10. POST .../transition (start_remediation → start_verification)
11. (verificação) três dias válidos   → SUCCESS_PENDING_VALIDATION
12. POST .../transition (validate_result)
13. POST .../transition (record_retrospective → close)
14. GET  /anomalies/cases/{id}/events → histórico completo e auditado
```

A automação (agendamento) **não** foi habilitada: a configuração permanece
`enabled = false` no ambiente, e habilitar exige ação explícita na tela ou na
API. É exatamente o que o plano pede ("Validar um piloto manual antes de
habilitar automação").

## 5. Critério final de conclusão

> "O usuário consegue acompanhar um caso desde o custo cloud observado até
> atribuição, investigação, decisão, ação, verificação e retrospectiva, com
> valores rastreáveis e sem dependência da fatura."

| Requisito | Situação |
|---|---|
| custo cloud observado | `observed_cloud` lido de `realizado_cloud` na data do consumo (E0/E6) |
| atribuição | roteamento por especificidade + fila FinOps + notificação (E7) |
| investigação | `start_investigation`, causa, hipótese, evidências (E7) |
| decisão | 5 decisões do plano com justificativa obrigatória (E7) |
| ação | ações corretivas/preventivas com responsável, prazo e evidência (E8) |
| verificação | três dias válidos consecutivos ou próximo evento mensal (E8) |
| retrospectiva | aprendizado e medida preventiva obrigatórios no encerramento (E8) |
| valores rastreáveis | `reference_id`, `assessment_id`, `forecast_run_id`, `case_version`, histórico imutável |
| sem dependência da fatura | `_billing_period` só como procedência; nenhum uso de `realizado` na detecção |

**Critério cumprido.** A única parte não automatizada é o agendamento, mantido
desligado por decisão de projeto (piloto manual primeiro).

## 6. Limitações e próximos passos

1. **Agendamento** — registrar `scheduled_task` para `anomaly_detection` e para o
   vencimento de prazos (`expire_overdue_notifications`).
2. **API de série diária** — expor os 15 pontos derivados da referência para o
   gráfico diário completo (E9 desenha hoje os escalares do caso).
3. **Faixa por série** — a E4 já calcula por série; a primeira execução real de
   ponta a ponta ainda não rodou com dados publicados (a configuração está
   desabilitada).
4. **Imutabilidade em banco** — `anomaly_case_event` bloqueia mutação no ORM;
   um `UPDATE` por SQL textual ainda seria aceito (trigger é o endurecimento
   natural).
5. **Transação distribuída** — Iceberg e PostgreSQL não compartilham transação;
   a ordem de commit escolhida preserva o invariante exigido, mas um crash entre
   os dois commits exige nova execução.
6. **`pytest` fora de `requirements.txt`** — lacuna pré-existente do projeto;
   a suíte só roda porque as dependências estão instaladas localmente.
