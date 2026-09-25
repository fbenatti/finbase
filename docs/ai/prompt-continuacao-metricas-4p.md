# Prompt de continuação: métricas diárias 4P e dashboards

Você está no repositório OpenFinops. Leia primeiro `docs/ai/metricas-diarias-4p.md` e os docstrings de `apps/api/app/services/azure_monitor_vm_service.py` e `apps/api/app/services/azure_container_insights_service.py`. Trabalhe sobre o estado atual do workspace; preserve alterações existentes de outras pessoas/IA.

## Estado já concluído e comprovado em 25/09/2026

- As fontes `monitor_vm_metrics` e `container_insights` da conexão Azure 4P (ID 1) usam `_billing_period=YYYYMMDD` nas tabelas Bronze e Silver. Isso é o **dia UTC do evento**, não o dia em que o worker executou. Custos e licenças continuam `YYYYMM`.
- As quatro tabelas de séries antigas foram removidas e recarregadas somente para 18–24/09/2026. Monitor VM: 2.575.119 linhas Bronze = Silver, tarefa 6181. Container Insights: 136.925 linhas Bronze = Silver, tarefa 6185, sendo 134.108 de performance. Existem exatamente sete partições por fonte; a antiga `202609` não existe nessas quatro tabelas. O total de amostras de performance com dia divergente da partição é zero.
- Container Insights limita consultas de `Perf` a um dia por requisição, pois sete dias em uma resposta excediam 8 MB no Log Analytics. Pod e node inventory são agrupados por dia. O parser aceita datas diárias e timestamps horários no mesmo lote. As linhas de cobertura têm `record_kind='coverage'` apenas no dia final do backfill; cinco clusters tinham dados e 41 estavam sem o add-on (`PARTIAL`).
- `sample_count` da série de VM é `double`, preservando 85.793 valores fracionários entregues pela Azure. Para contagem de pontos observados, conte linhas `quality_status='OK'` com `value IS NOT NULL`.
- A coleta diária programada usa uma tarefa por fonte com `window_days=1` e `collection_until` à meia-noite UTC. Para apresentar 30 dias completos, a consulta lê 30 partições `YYYYMMDD`; **só sete dias estão carregados agora**. O backfill de 30 dias é uma etapa futura explícita, limitada à 4P.
- O worker e seu supervisor estão ativos; o watchdog reage a 15 minutos sem progresso persistido. A API responde HTTP 200. O catálogo de fontes aponta para `silver.resource_metric_series` e `silver.container_metric_series`.
- Testes de particionamento em `apps/api/tests/test_daily_metric_partitions.py` passaram (3 testes). Não repita a coleta de sete dias sem necessidade.

## Trabalho restante

1. Confirme o estado real antes de alterar dados: tarefas 6181/6185 `COMPLETED`, ausência de `QUEUED`/`RUNNING`, sete partições e igualdade Bronze/Silver por dia. Use o SQL de validação do documento de contexto. Se o ambiente tiver mudado, relate a diferença antes de escrever.
2. Defina de modo explícito nos contratos da API o intervalo histórico em UTC: `from_day` e `to_day` inclusivos (`YYYYMMDD`); `to_day` é o último dia UTC completo; para 30 dias, `from_day = to_day - 29 dias`. Use `_billing_period BETWEEN :from_day AND :to_day` para poda de partições e filtre `_source_connection_id=1` e `_silver_owner` em toda consulta de série 4P. Para janelas parciais, acrescente filtro do timestamp do evento.
3. Revise `apps/api/app/routers/finops_center.py` e `apps/api/app/services/resource_profile_service.py`: consultas históricas sem filtro de partição/fonte; limites de 3.000/5.000 linhas que cortam séries; topologia AKS que escolhe um único `max(snapshot_date)` para performance horária e inventário diário. Agregue por dia/hora no servidor ou pagine; escolha o snapshot recente por `record_kind` e cluster; separe performance, inventário e cobertura. Não transforme lacunas em zero.
4. Trate o perfil derivado: `iceberg.silver.resource_profile_window` ainda tem 1.746 linhas antigas em `202609`. Elas não são parte da validação de sete dias. O SQL atual associa métricas diárias a custo mensal por igualdade de `_billing_period`, que não funciona. Modele uma janela de perfil com início/fim UTC e proveniência da série; junte custo ao mês correspondente com regra explícita, evitando atribuir custo mensal integral a sete dias. Recalcule/substitua perfis antigos somente depois de definir essa semântica. Não derrube a tabela inteira: seus registros podem envolver outros escopos.
5. Ajuste os dashboards para indicar a cobertura disponível: somente sete dias de histórico real agora, apesar de uma seleção visual de 30 dias. Para Container Insights, `coverage` do backfill é um snapshot no dia final; clusters sem add-on são `UNAVAILABLE`. Mostre dias sem amostra como lacunas.
6. Quando for solicitada a expansão para 30 dias, execute backfill explícito das duas fontes apenas na 4P, com `collection_until` fixo em meia-noite UTC. Confirme uma partição por dia, ausência de duplicação em rerun, Bronze = Silver e performance no dia correto. A função de fonte limita `window_days` a 90 e divide chamadas de performance por dia.

## Critérios de aceite da continuação

- API/dashboard retornam 7 dias reais para o recorte atualmente carregado e não inventam os outros 23 dias.
- Consultas de 30 dias, após futuro backfill, restringem 30 partições e uma conexão/fonte, sem truncamento silencioso.
- Perfil derivado não mostra dados antigos como se fossem da série nova e não mistura período mensal de custo com período diário sem regra declarada.
- Testes direcionados e consulta real do Iceberg comprovam data do evento = partição para séries diárias.

Entregue as alterações, a evidência de validação e qualquer limitação real encontrada. Não execute limpeza de outros ambientes nem o backfill de 30 dias por inferência: a carga autorizada até aqui foi de sete dias na 4P.
