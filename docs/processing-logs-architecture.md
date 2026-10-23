# Reestruturação operacional para PRD

## Performance da tela de logs e cubo operacional

A listagem unificada de execuções é servida por um cubo operacional materializado
em Gold, com nome `dest_dm_processing_logs`. O cubo contém uma
linha por execução e dimensões para tipo, status, origem, destino, partições,
início/fim, contagens e fase. A timeline detalhada, parâmetros e payloads de
erro/eventos permanecem em `processing_log` no PostgreSQL e são carregados sob
demanda ao abrir o diálogo. A grade lê apenas o cubo; não varre Silver nem
materializa dados de origem durante a navegação.

O cubo é atualizado por uma rotina em segundo plano a cada 15 segundos. Ela lê
somente linhas alteradas em `processing_log` desde o último `updated_at` (com
replay idempotente da fronteira de timestamp) e faz `MERGE` por `id` em lotes de
100. A primeira execução cria a tabela e carrega o histórico; reconstruções
manuais fazem upsert de todo o histórico sem apagar a publicação existente.
Uma trava consultiva PostgreSQL impede publicadores concorrentes. O cubo não
contém parâmetros, erros nem eventos extensos. A UI lê essa tabela com paginação
e filtros; o diálogo consulta `GET /processing-logs/{id}` para obter o registro
completo no PostgreSQL.

O cubo aparece em Governança → Geração de Cubos como recurso operacional do
sistema, com disponibilidade, contagem, última atualização e ação para
reconstrução. A grade tenta a Gold primeiro e usa a consulta PostgreSQL atual
como fallback durante o aquecimento ou indisponibilidade analítica. Os
endpoints operacionais são `GET /processing-logs/cube/status` e
`POST /processing-logs/cube/rebuild` (resposta assíncrona `202`).

Para PRD, confirmar Trino/Iceberg com suporte a `MERGE`, validar contagens e
atualizações de status de execuções existentes, observar atraso do ciclo e
testar reconstrução sem indisponibilidade. A origem do cubo é exclusivamente
PostgreSQL; a lista de logs não consulta Silver.

Este documento consolida as mudanças do branch atual que precisam ser avaliadas para produção: logs unificados, reprocessamento/clonagem com fallback de preços Azure, regras de classificação, ajustes de forecast e dashboard de compromissos. O branch contém commits anteriores e independentes da solicitação de logs; não os trate como escopo implícito de um único PR. Antes de abrir/mesclar, compare `origin/main...HEAD` e decida explicitamente quais blocos entram no release.

## 1. Resumo das mudanças

| Bloco | Mudança | Persistência principal |
|---|---|---|
| Logs | Nova camada única para listar e detalhar execuções; históricos separados deixam de ser exibidos nas abas de processamento | Tabela PostgreSQL `processing_log`; `etl_task` e `forecast_run` preservados |
| Azure Price Sheet | Ingestão de snapshot completo para referência de preço em Bronze e busca direcionada de `MeterId` como fallback de clone | Iceberg `bronze.raw_azure_pricesheet` e `bronze.raw_azure_price_reference`; metadados de snapshot na conexão `azure-pricesheet` |
| Reprocessamento e clones | Reuso de preço observado/curado e fallback Price Sheet com validação de produto/região/unidade e precedência determinística | Bronze/Silver/Gold existentes; tarefas em `etl_task` |
| Regras/classificação | Correções de importação/reimportação e classificação de retornos/folhas de projeto | Tabelas de regras e dados Silver/Gold existentes |
| Forecast | Cutoff limitado por data observada e `hoje - cutoff_days`; proteção de granularidade mensal restrita a CSV manual | `forecast_config`, `forecast_run`, Gold/forecast existentes |
| Dashboard de compromissos | Nova API e aba Savings para compra RI/Savings Plan, baseline OnDemand e projeção | Consultas Trino sobre FOCUS/planejamento; sem tabela PostgreSQL nova |

## 2. Logs centralizados

### Modelo PostgreSQL

Nova tabela `processing_log`, criada por `Base.metadata.create_all`, com índices por início, tipo/status e vínculo ETL:

- `id`, `process_type`, `status`, `etl_task_id`;
- `source_kind`, `source`, `destination`, `partitions`, `parameters`, `counts`;
- `phase`, `error`, `details`, `started_at`, `finished_at`, `created_at`, `updated_at`.

`etl_task` permanece sendo a fila e a fonte operacional do worker. O log canônico é a leitura padronizada. A FK para `etl_task` usa `ON DELETE SET NULL`; o índice único permite no máximo um registro canônico por tarefa. Registros especializados sem tarefa ETL, como um `ForecastRun` independente, podem existir sem esse vínculo e são identificados nos detalhes.

### API

- `GET /processing-logs`: filtros `limit`, `offset`, `search`, `process_type`, `status`, `started_after`, `started_before`, `connection_id` e `destination_table_id`; mantém `task_type` como alias de compatibilidade.
- `GET /processing-logs/{id}`: detalhe incluindo `details`, eventos, erros e payload específico.
- A API é registrada em `apps/api/app/main.py`; router em `apps/api/app/routers/processing_logs.py`; serialização/sincronização em `apps/api/app/services/processing_logs.py`.

### Escrita e cobertura

O serviço central sincroniza tarefas no enqueue de extração/forecast, claim/liberação/recuperação/cancelamento e persistência dos eventos de reprocessamento. O startup faz backfill idempotente apenas de tarefas sem log e dos registros de forecast existentes. Eventos legados em `etl_task.result_json.event_log` são espelhados em `details.event_log`; não são apagados.

Os campos comuns são snapshots. `source` e `destination` podem conter IDs, nomes, namespaces ou tabelas conforme o tipo. `counts` usa linhas de origem/normalizadas/destino quando informadas; `items_total` e `items_processed` cobrem unidades específicas, como blobs, regras ou meses. Dados financeiros não são duplicados no PostgreSQL.

### Frontend

`/reprocess?tab=logs` reúne busca, filtros por tipo/status, paginação server-side e dialog com origem, destino, partições, contagens, horários, parâmetros, erros e eventos. As abas Dados, Extração, Forecast, Custo compartilhado, Gold e Custo IA mantêm os controles de execução, mas não renderizam históricos paralelos. Cancelamento de tarefa ativa e link para conteúdo Bronze continuam no detalhe. Conteúdo de saída não é replicado: fica no destino analítico original.

As rotas antigas continuam disponíveis para outros consumidores. Esta entrega remove a duplicação visual; não remove tabelas, endpoints nem registros legados.

## 3. Azure Price Sheet e clones

### Dados e configuração

- `Azure Price Sheet` é uma `SourceConnection` Azure independente (`code=azure-pricesheet`, `connector_type=azure_pricesheet`), não uma seleção dentro de `azure-producao`/Cost Management. Ela tem credenciais, prefixo/blob, tarefa e Bronze próprios, mesmo quando usa os mesmos valores de tenant/client/secret.
- A conexão pode ser criada/editada em **Conexões** como Azure Price Sheet. O endpoint de refresh continua criando-a sob demanda para compatibilidade, caso ainda não exista.
- O catálogo PostgreSQL `azure_source_catalog` é sincronizado pelo seed do repositório (inserção das fontes declaradas, remoção apenas de metadata obsoleta e vínculo das conexões existentes); ele não guarda credenciais. Remover uma metadata do catálogo não apaga conexão, invoices, Bronze nem histórico. `contracts_mca` não é uma fonte selecionável no Cost Management: **Contratos MCA Brasil** permanece na sua conexão e fluxo de invoices independentes.
- O endpoint `POST /etl/azure-pricesheet/refresh` cria uma tarefa `azure_pricesheet_refresh`; worker executa ingestão.
- O snapshot completo é validado pelo manifest: arquivos CSV, bytes e linhas declaradas, tamanhos reais e contagem efetivamente ingerida.
- Bronze guarda as colunas do CSV normalizadas para minúsculas, além de `snapshot_id`, `_blob_path`, `_billing_period` e `_ingested_at` em `bronze.raw_azure_pricesheet`.
- Credenciais próprias (`tenant_id`, `client_id`, `client_secret`, `storage_account`, `container_name`) pertencem ao `config_json` dessa conexão e podem repetir as do Cost Management sem serem compartilhadas. Se estiverem ausentes, o coletor ainda usa as variáveis Azure 4P/TI legadas para facilitar a migração; esse fallback não substitui a configuração independente em PRD.
- O snapshot ativo e seu manifest/período/contagem são registrados em `source_connection.config_json` da conexão `azure-pricesheet`. Snapshot novo só fica ativo após validação; ingestões parciais anteriores são removidas por chave de snapshot em retry.
- Consultas direcionadas usam Query Acceleration para `MeterId` e persistem referências em `bronze.raw_azure_price_reference`.

Não há nova tabela PostgreSQL para armazenar preços nem DDL manual necessário. A metadata da conexão e do snapshot usa as tabelas existentes `source_connection` e `etl_task`; a Bronze é `raw_azure_pricesheet`. Em PRD, criar/editar a conexão pelo fluxo normal de **Conexões**, preenchendo as credenciais independentes e validando acesso ao container/prefixo `reports/report_-pricesheet/`. Não inserir segredo por SQL. O fallback para variáveis 4P/TI só deve ser usado durante a transição.

O item **Compromisso MCA (totais de invoice)** não pertence ao catálogo de coletas do Azure 4P Cost Management. Ele permanece na conexão independente **Contratos MCA Brasil**, com seu endpoint, autenticação, parâmetros e Bronze de invoices. Não habilitar esse item dentro da seleção de fontes de `azure-producao` nem enfileirá-lo pela carga full genérica.

### Regras do fallback

O reprocessamento tenta preços observados e catálogo curado antes do Price Sheet; este é fallback para chaves ainda sem preço compatível. Match valida `MeterId` e dimensões contratuais disponíveis, incluindo produto/região e unidade. Unidades equivalentes são normalizadas com regra conservadora. Preço incompatível não é aceito; gaps ficam auditáveis e não inventam valor.

Price Sheet fica em Bronze, fora do catálogo Silver de custos. Há mudança deliberada de comportamento de reprocessamento e potencial mudança de valores de clone, então deve-se validar amostras representativas contra saída atual antes de reprocessar produção em massa.

## 4. Classificação, reimportação e Gold

O branch também altera importação/reimportação de regras, classificação de retornos e folhas de projeto, seleção de meses usando Bronze físico, granularidade de linhas sintéticas de retorno e defaults de regras Gold. Isso afeta tabelas/configuração já existentes, incluindo `classification_rule`, `business_rule`, `gold_rule` e dados materializados.

O bootstrap da API muda seeds/upserts Gold auxiliares para declarar explicitamente `dimensions`, `measure_columns`, `snapshot_mode` e `snapshot_col`. Também muda a comparação de JSON legado no seed de `visao-completa`. Não cria nova tabela PostgreSQL para essa parte, mas pode atualizar registros de regra existentes ao subir a API. Revisar a query seed em `main.py` com uma cópia de PRD antes de deploy; reprocessamento Gold é uma ação separada e pode alterar tabelas Iceberg.

## 5. Forecast e granularidade

O cutoff passa a ser o menor entre a maior data válida encontrada na fonte e `hoje - cutoff_days`. Isso evita consumir datas futuras sem recuar duas vezes uma origem atrasada. A proteção contra dados mensais agregados no mês corrente passa a se aplicar a CSV manual, cuja granularidade é detectada e persistida em `manual_csv_metadata`; extrações Azure/GCP podem publicar o realizado parcial disponível.

Há alteração em `FORECAST.md` para documentar o novo contrato. Não é necessária tabela nova para logs; o processo usa `forecast_run`/`etl_task` atuais. Validar comportamento com configurações e fontes representativas antes de atualizar snapshots em PRD.

## 6. Dashboard de compromissos (Savings)

Nova rota `GET /savings/commitments-dashboard` calcula por período e origem (all, azure-dev, azure-producao): compras de Reservation/Savings Plan, baseline OnDemand a partir das linhas sintéticas Return vinculadas à linha de uso original, economia estimada e série de forecast. A aba nova em Savings exibe métricas, séries e visão por projeto.

As consultas usam Trino nas tabelas FOCUS detalhadas e planejamento da camada ativa. Não criam tabelas. As compras não são rateadas por projeto; o baseline de projeto é mostrado separadamente. Validar moeda e semântica contábil dos valores com FinOps antes de divulgar como saving realizado. O retorno usa `realizado_cloud` e a documentação/resposta da API informa unidade monetária conforme a fonte; não presumir BRL quando a origem trouxer outra moeda.

## 7. Tabelas, migrações e dados

### PostgreSQL

- **Nova:** `processing_log` e seus índices/FK, criados automaticamente no startup via SQLAlchemy metadata.
- **Alteradas por esta funcionalidade:** nenhuma coluna nova em `etl_task` ou `forecast_run`; são lidas como fontes e mantidas compatíveis.
- **Possíveis atualizações:** backfill cria linhas em `processing_log`; seeds idempotentes podem atualizar regras Gold existentes conforme `main.py`.
- `etl_task.result_json`, `forecast_run`, rotas antigas e dados legados não são eliminados.

### Iceberg / Trino

- **Novas tabelas Bronze sob demanda:** `raw_azure_pricesheet` e `raw_azure_price_reference`.
- Reprocessamento/clones podem publicar diferentes valores onde antes não havia preço compatível.
- Dashboard de compromissos é somente leitura.
- Regras Gold/FOCUS existentes podem ser reprocessadas depois do deploy, mas a implantação da aplicação não deve disparar esse reprocessamento automaticamente.

## 8. Plano de deploy em PRD

> **Gate de release:** todo o desenvolvimento, commits e validação inicial
> devem ocorrer exclusivamente em DEV. Este plano descreve uma promoção futura
> e só pode ser iniciado depois que o usuário confirmar que terminou de validar
> em DEV e autorizar explicitamente a subida para PRD. Um pedido de
> implementação, correção, teste ou commit não autoriza publicação em PRD.
> Commits e push para o repositório não podem disparar deploy automático em PRD
> enquanto essa autorização não existir.

### Antes do deploy

1. Revisar o diff completo `origin/main...HEAD`; os 126 commits locais no momento desta documentação incluem trabalho de preços Azure, forecast, regras, Savings e logs. Abrir PR somente com escopo aprovado.
2. Fazer backup PostgreSQL e confirmar backup/retention do catálogo Iceberg/warehouse.
3. Aplicar a metadata do catálogo com `PYTHONPATH=. ../../.venv/bin/python scripts/seed_azure_source_catalog.py --apply` (executado em `apps/api`). Confirmar em **Conexões** o registro independente `azure-pricesheet`; preencher seus próprios tenant/client/secret/storage account/container e validar permissão de list/read no prefixo `reports/report_-pricesheet/`. Não copiar credenciais via SQL. As variáveis de ambiente 4P/TI são somente fallback legado.
4. Confirmar que API/worker compartilham o mesmo banco, warehouse Iceberg e configuração Azure; alinhar versões de deploy.
5. Registrar contagens/status atuais de `etl_task`, `forecast_run`, regras Gold e outputs de clone para comparação.

### Ordem recomendada

1. Fazer deploy da API que cria `processing_log`, executa backfill e disponibiliza as rotas; observar startup/locks e tamanho do backfill.
2. Fazer deploy do worker compatível, que escreve status/eventos no log e reconhece `azure_pricesheet_refresh`.
3. Validar `GET /processing-logs`, filtros e detalhes para execução antiga e uma nova execução curta de cada tipo habilitado.
4. Fazer deploy do frontend e validar `/reprocess?tab=logs`, cancelamento e link Bronze.
5. Azure Price Sheet: usar a ação de refresh da conexão independente; verificar tarefa, manifest ativo, período, total de linhas e `bronze.raw_azure_pricesheet`. A carga full do ambiente `azure-producao` não deve enfileirar este snapshot.
6. Executar um reprocessamento/clonagem piloto de fonte/período aprovado; comparar preços observados, curados e fallback, e validar contagens de Bronze/Silver/Gold.
7. Validar forecast em uma configuração piloto e comparar cutoff/snapshot; validar `/savings/commitments-dashboard` contra consultas/relatórios financeiros de referência.
8. Só após aceite funcional planejar backfill/reprocessamento histórico amplo e eventual aposentadoria de endpoints antigos em release posterior.

## 9. Verificações pós-deploy

- API e worker saudáveis; fila ETL sem tarefa presa ou falhas novas de serialização.
- `processing_log` tem uma linha por `etl_task` histórico e novos jobs aparecem com tipo, origem/destino, parâmetros, status e timestamps.
- Comparar amostra entre log canônico e `etl_task`/`forecast_run`, sem perda de detalhes legados.
- Verificar latência de startup; backfill seleciona somente tarefas sem vínculo canônico, mais snapshots forecast especializados.
- Confirmar Bronze Price Sheet ativo não aponta para snapshot incompleto; validar produto/região/unidade em amostras do clone.
- Conferir totais da Gold/FOCUS e baseline de compromissos com FinOps.
- Build validado nesta implementação: `npm run build`; compilação dos módulos Python passou. Os testes automatizados do repositório não foram executados nesta etapa.

## 10. Rollback

- Frontend: voltar à versão anterior restaura as telas anteriores; os endpoints específicos seguem ativos.
- API/worker: retornar ambos a uma versão compatível. Não apagar `processing_log`; ele é aditivo e `etl_task`/`forecast_run` permanecem intactos.
- Logs: interromper o backfill/sincronização só após rollback coordenado de API/worker; nenhum dado legado precisa ser revertido.
- Price Sheet: não apagar a tabela Bronze nem limpar `active_snapshot_id` automaticamente. Reverter snapshot ativo exige decisão operacional e registro do snapshot anterior validado.
- Reprocessamento: restaurar saída Silver/Gold a partir de snapshot/backup conhecido; não presumir que deploy reverso desfaz materializações.
- Seeds: mudanças em `gold_rule` feitas no startup podem persistir após rollback do código; registrar os valores anteriores antes de deploy e restaurá-los somente se necessário.
- Não remover tabelas nem endpoints legados durante o rollback.

## 11. Riscos e itens que exigem aceite

- Este branch reúne vários blocos independentes; conferir e reduzir o PR para o release aprovado.
- Startup/backfill acessa todas as tarefas sem registro canônico e os runs forecast; medir em cópia de PRD se o volume for alto.
- O schema de `processing_log.details` inclui payload operacional; estabelecer retenção/limite antes de crescimento prolongado.
- Algumas execuções especializadas usam `etl_task` e outras `forecast_run`; o dashboard relaciona os detalhes, mas a cobertura de todo fluxo novo deve ser validada.
- Price Sheet pode mudar resultados de clone; precisa de aceite de valores e teste piloto.
- Métricas de Savings são estimativas com premissas de baseline; não publicar como economia contábil sem validação.
- Nenhum endpoint ou tabela de log antigo foi apagado; aposentadoria requer busca de consumidores e release de breaking change separado.
