# Métricas históricas diárias da 4P — contexto para dashboards

## O que mudou

As fontes `monitor_vm_metrics` e `container_insights` gravam Bronze e Silver por **dia do dado em UTC**. A coluna de partição física Iceberg continua se chamando `_billing_period`, mas, somente para essas fontes de métricas, seu valor tem formato `YYYYMMDD`. Para custos e licenças, o período continua mensal (`YYYYMM`).

O Monitor VM usa a data de `bucket_utc` de cada amostra. Container Insights usa a data de `snapshot_date`: performance vem do `TimeBucket` horário do Log Analytics; inventários de pod e node são agregados por `bin(TimeGenerated, 1d)`; a linha de cobertura representa a situação no dia final da coleta. Um backfill de sete dias pode gravar até sete partições em uma tarefa. A coleta programada diária lê **um dia UTC fechado**, `[00:00, 00:00)` do dia anterior. Para mostrar os últimos 30 dias completos, o dashboard lê 30 partições diárias; uma carga inicial de 30 dias deve ser pedida explicitamente.

| Fonte | Bronze 4P | Silver para dashboards | Grão temporal |
| --- | --- | --- | --- |
| `monitor_vm_metrics` | `iceberg.bronze.raw_azure_4p_monitor_vm` | `iceberg.silver.resource_metric_series` | Amostra de 15 minutos em `bucket_start_utc` |
| `container_insights` | `iceberg.bronze.raw_azure_4p_container_insights` | `iceberg.silver.container_metric_series` | Performance horária; inventário diário; cobertura no dia final da coleta |

`ingested_blob` no PostgreSQL é somente metadado de ingestão, não o dataset dos dashboards. O catálogo de fontes foi alinhado aos nomes Silver reais. Não existe `silver.azure_vm_metric_series`, `silver.azure_container_metric_series` nem `gold.azure_vm_profile_cube` nesta implementação.

## Recarga e escopo

Em 25/09/2026 foram excluídas as duas tabelas Bronze acima, as duas Silver correspondentes e 52 registros `ingested_blob` de métricas da **conexão 4P (ID 1)**. A história de tarefas ETL foi preservada. Isso removeu os dados antigos gravados sob a partição mensal `202609`, inclusive as amostras que já tinham sido testadas em `20260924`; os dados precisam ser baixados novamente. Não havia tabela Gold de métricas a excluir.

Os sete dias UTC completos **18 a 24/09/2026** foram recarregados apenas para 4P. A tarefa 6181 concluiu Monitor VM; a tarefa 6185 concluiu Container Insights após corrigir o limite de resposta do Log Analytics e o parser de datas mistas. As tentativas intermediárias 6182–6184 ficam no histórico para auditoria, mas **não** são a carga final de Container Insights.

| Dia UTC | Monitor VM Bronze = Silver | Container Insights Bronze = Silver | Performance de containers |
| --- | ---: | ---: | ---: |
| 18/09 | 367.296 | 21.470 | 21.094 |
| 19/09 | 367.296 | 14.978 | 14.614 |
| 20/09 | 366.432 | 14.486 | 14.208 |
| 21/09 | 366.432 | 21.668 | 21.266 |
| 22/09 | 366.432 | 21.173 | 20.810 |
| 23/09 | 366.432 | 21.440 | 20.994 |
| 24/09 | 374.799 | 21.710 | 21.122 |
| **Total** | **2.575.119** | **136.925** | **134.108** |

O dia 24 contém 46 registros `record_kind='coverage'`: 5 clusters com dados e 41 sem o add-on. O estado `PARTIAL` decorre desses 41 clusters. A validação encontrou **zero** linhas de performance cuja data de `snapshot_date` difira de `_billing_period`. Nenhuma das quatro tabelas atuais contém a partição mensal antiga `202609`. `sample_count` de Monitor VM é `double`: a Azure devolveu 85.793 valores fracionários nesse campo, que agora são preservados. Para contar amostras presentes em um gráfico, use `count(*)` após os filtros de qualidade.

Esta carga só cobre sete dias. Para preencher os últimos 30 dias completos, executar um backfill explícito de 30 dias para as duas fontes 4P e validar os 30 períodos; a coleta programada continuará acrescentando um dia fechado por execução. Uma partição ausente pode representar ausência de dados na Azure ou falha de coleta: examinar cobertura e erros da tarefa.

## Como consultar o histórico

Use a Silver e sempre filtre a conexão e o intervalo de partições. Calcule `:from_day` e `:to_day` no backend em UTC, como strings `YYYYMMDD`. Para os últimos 30 dias UTC completos: ontem é `:to_day`; `:from_day` é 29 dias antes de ontem. A comparação lexical de oito dígitos segue a ordem cronológica e permite poda de partições Iceberg. Para um período parcial em horas, adicione filtro de timestamp.

```sql
SELECT _billing_period AS utc_day, resource_id, metric,
       avg(CAST(value AS DOUBLE)) AS average_value,
       approx_percentile(CAST(value AS DOUBLE), 0.95) AS p95_value,
       count(*) AS observed_samples
FROM iceberg.silver.resource_metric_series
WHERE _source_connection_id = 1
  AND _silver_owner = 'monitor_vm_metrics'
  AND _billing_period BETWEEN :from_day AND :to_day
  AND quality_status = 'OK'
  AND value IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY utc_day, resource_id, metric;
```

Para gráficos de 15 minutos, agrupe por `bucket_start_utc` e pelas identidades do recurso e da métrica. Não some percentuais ou médias entre recursos sem definir o peso. `GAP` e `NOT_AVAILABLE` indicam ausência de medição; mantenha essas informações visíveis, sem convertê-las em zero.

```sql
SELECT _billing_period AS utc_day, cluster_resource_id, namespace, computer,
       metric_name, avg(metric_average) AS average_value,
       sum(samples) AS observed_samples
FROM iceberg.silver.container_metric_series
WHERE _source_connection_id = 1
  AND _silver_owner = 'container_insights'
  AND _billing_period BETWEEN :from_day AND :to_day
  AND record_kind = 'performance'
  AND metric_average IS NOT NULL
GROUP BY 1, 2, 3, 4, 5
ORDER BY utc_day, cluster_resource_id, namespace, computer, metric_name;
```

Para inventário, filtre `record_kind IN ('pod_inventory', 'node_inventory')` e escolha o snapshot diário desejado. Na Silver, `snapshot_date` é timestamp; use `CAST(snapshot_date AS date)` se precisar agrupar pela data do evento. Para cobertura, filtre `record_kind = 'coverage'` e examine `coverage_status`/`coverage_reason`; um cluster sem Container Insights não tem consumo igual a zero. Uma coleta pode terminar `PARTIAL` quando vários clusters não expõem esse add-on. A linha de cobertura da coleta de vários dias é referente ao dia final; não a interprete como sete medições diárias.

## Contrato e correções do dashboard FinOps

Os endpoints de overview, detalhe de recurso, namespace, worker e disco aceitam
`from_day` e `to_day` como strings `YYYYMMDD` inclusivas. Informe ambos ou
nenhum; `to_day` precisa ser um dia UTC completo e a janela aceita até 90 dias.
Sem parâmetros, o backend consulta os últimos 30 dias completos (`to_day` é
ontem em UTC e `from_day = to_day - 29 dias`). A resposta inclui os limites
solicitados e `observed_days` calculado das partições com amostras. Pedir 30 dias
não significa que 30 dias já estejam carregados.

Toda consulta de série do FinOps Center restringe `_source_connection_id = 1`,
`_silver_owner` à fonte correspondente e `_billing_period BETWEEN from_day AND
to_day`. Séries VM, disco, namespace e worker são agregadas por hora no servidor;
não há corte silencioso de 3.000/5.000 linhas. Valores ausentes não são
convertidos em zero.

A topologia AKS escolhe o snapshot mais recente por cluster, tipo de registro e
dimensão; performance, inventário e cobertura são separados na resposta. O painel
mostra dias observados dentro da janela e avisa se a recomendação é anterior à
amostra mais recente.

### Atenção ao perfil derivado

`iceberg.silver.resource_profile_window` ainda contém **1.746 linhas na partição mensal `202609`**, produzidas antes desta recarga. Elas **não** validam a nova série diária. O campo `source_connection_name` dessa tabela vem do *join de custo* (`raw_azure_ti`/nulo), portanto não permite excluir apenas perfis 4P com segurança. O dashboard passou a calcular o perfil do detalhe diretamente da série diária filtrada. O serviço derivado escreve, quando explicitamente executado, em `silver.resource_profile_daily_window`, com início/fim UTC e proveniência da conexão/fonte. O custo mensal é rateado pelos dias de calendário que intersectam a janela; `cost_window_amount` guarda o custo alocado e `effective_cost` o run rate mensal (`cost_window_amount / requested_days * 30,4375`). O perfil legado não é removido nem tratado como perfil diário. Nesta continuação a consulta foi validada no Iceberg, mas **não** materializamos perfis nem recalculamos recomendações. A carga de sete dias validou as séries, não uma execução do novo perfil derivado.

## Validação da recarga

Conferido em 25/09/2026: tarefas 6181 e 6185 `COMPLETED`; Bronze e Silver com sete partições e contagens iguais por dia; partição `202609` ausente; `ingested_blob` com períodos diários apenas; nenhuma tarefa `QUEUED` ou `RUNNING`; API respondendo HTTP 200 e nenhum bloqueio ativo no PostgreSQL. Reexecutar essas verificações após futuros backfills ou mudanças de mapeamento.

## Reconciliação, retenção local e horizonte da origem (25/09/2026)

`/analysis` continua listando tabelas, com a contagem de tabelas visível. Ao
selecionar tabela Bronze/Silver, carrega as partições sob demanda via `$partitions`
do Trino e usa inspection metadata PyIceberg se Trino estiver indisponível.
`YYYYMMDD` aparece como dia UTC de métrica; `YYYYMM` como mês de custo/licença.

O scheduler reconcilia os sete dias UTC completos mais recentes, com janela de
um dia por conexão/fonte/dia. `metric_daily_collection` persiste o resultado,
contagens Bronze/Silver, tarefa e motivo com unicidade nessa chave. A coleta só
se confirma com as duas camadas; `COMPLETED_EMPTY` é terminal para evitar loop;
falha e indisponibilidade confirmada da origem têm estados distintos. Container
Insights sem clusters/workspaces recebe `SOURCE_UNAVAILABLE`. Cobertura parcial
segue como linhas `coverage` e não reabre dias confirmados por Bronze/Silver.

A política local é separada da manutenção de snapshots. O corte é `hoje UTC -
90 dias`; o preview lista tabela, dia e contagem. O delete limita-se a partições
métricas `YYYYMMDD`; nas Silver filtra também `_source_connection_id` e
`_silver_owner`, e nos metadados de ingestão filtra o prefixo da fonte. A rotina
usa o lock de escrita e só atualiza os metadados após verificar os deletes. O
preview propõe separadamente `expire_snapshots` e `remove_orphan_files` com
retenção mínima de sete dias; essa manutenção não roda junto com o delete de
linhas.

Via ARM, foram verificados quatro workspaces efetivamente associados aos
clusters 4P: um com 365 dias de retenção e três com 30 dias. `Perf`,
`KubePodInventory` e `KubeNodeInventory` estavam presentes em todos e cada tabela
tinha `retentionInDays`/`totalRetentionInDays` iguais ao respectivo workspace.
Azure Monitor mantém métricas de plataforma por 93 dias; o limite de 30 dias do
Metrics Explorer é o intervalo de um gráfico. Assim, o horizonte de Log
Analytics observado varia de 30 a 365 dias por workspace. O agendamento não
habilita backfill automático de 30 dias; reconcilia apenas os sete dias recentes.

Na validação, os quatro conjuntos de partições mantiveram sete dias com
contagens Bronze/Silver iguais. A segunda reconciliação criou zero tarefas
duplicadas. O preview do corte de 90 dias não encontrou partições antigas; uma
tabela Iceberg descartável com partições de teste confirmou que a regra preserva
meses `YYYYMM`, conexão/fonte diferentes e o dia do corte.
