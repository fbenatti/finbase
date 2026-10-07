# Logs centralizados de processamento

## Objetivo

Manter uma visão operacional única para extrações, reprocessamentos e materializações. `processing_log` é o contrato canônico de consulta; `etl_task` continua sendo a fila e o estado de execução para os processos já executados pelo worker. Os detalhes financeiros continuam no Iceberg/Trino e não são copiados para PostgreSQL.

## Contrato

Cada execução publicada em `processing_log` contém:

- `process_type`, `status`, vínculo opcional com `etl_task_id` e `source_kind`;
- `source`, `destination`, `partitions`, `parameters` e `counts` em JSON;
- `phase`, `error`, `started_at`, `finished_at` e `details`;
- em `details`, eventos cronológicos, erros e payloads operacionais específicos.

Campos sem significado para um tipo ficam vazios. `counts.rows_source`, `rows_normalized` e `rows_destination` significam linhas lidas, normalizadas e publicadas quando o worker reporta tais métricas; `items_total` e `items_processed` representam blobs, regras, meses ou unidades do processo conforme seu tipo.

## Componentes e rotas

- `app/models/processing_log.py`: tabela canônica e índices.
- `app/services/processing_logs.py`: normalização/sincronização idempotente e registro de eventos.
- `GET /processing-logs`: paginação, busca e filtros por tipo/status/período.
- `GET /processing-logs/{id}`: detalhe completo com eventos e metadados.
- `/reprocess?tab=logs`: grid e dialog de detalhe.

O startup cria a tabela por `Base.metadata.create_all` e executa backfill idempotente das linhas legadas de `etl_task`. O payload legado permanece intacto. Novas atualizações de reprocessamento também sincronizam o registro canônico.

## Estado da migração

1. **Contrato e base canônica:** implementados.
2. **Backfill e leitura comum:** implementados para `etl_task` existente e expostos ao frontend.
3. **Sincronização contínua:** conectada ao enqueue de extração/forecast, claim/recuperação/cancelamento de ETL e atualizações operacionais dos fluxos de reprocessamento no worker/API.
4. **Registros próprios:** `forecast_run` continua sendo fonte especializada; seus parâmetros/resultado são ligados ao log ETL ou expostos como registro consolidado se não houver vínculo. Acurácia entra como `forecast_accuracy` via `etl_task`. Conteúdo Bronze permanece consultável por link para a visualização de dados da execução.
5. **Aposentadoria visual:** as abas de dados, extração, forecast, custo compartilhado, Gold e relacionamentos IA deixaram de renderizar históricos separados. As rotas antigas permanecem ativas para compatibilidade; devem ser removidas só após confirmar que não há outros consumidores.

## Implantação em produção

1. Fazer backup e confirmar espaço/índices para `processing_log`.
2. Implantar API que cria a tabela e faz o backfill incremental; validar `GET /processing-logs` e amostras de cada tipo.
3. Implantar workers compatíveis para sincronizar atualizações e eventos em execução.
4. Implantar frontend e validar filtros, paginação e dialog.
5. Comparar contagens e status entre endpoints antigos e a nova rota por pelo menos um ciclo operacional completo.
6. Em release futuro, migrar consumidores restantes e só então descontinuar as rotas antigas. Não excluir linhas de `etl_task` nem `forecast_run`: elas são estado de fila e snapshots operacionais/de domínio.

## Rollback

Frontend pode voltar a consultar as rotas antigas sem impacto nos jobs. A tabela `processing_log` é aditiva; em rollback não apagar seus dados. Reimplantar a versão anterior da API/worker mantém `etl_task` e os históricos específicos intactos. A remoção física da tabela, se algum dia necessária, deve ser uma migração separada após backup e auditoria de consumidores.

## Limitações conhecidas

- O contrato comum organiza metadados e eventos; não replica linhas financeiras de saída.
- Em registros antigos, origem/destino dependem do conteúdo que foi persistido à época; o backfill não consegue recuperar parâmetros ausentes.
- A tela resolve nomes de conexão/destino quando os IDs ainda apontam para registros válidos.
- O tamanho e a retenção de `details` precisam ser acompanhados; eventos são limitados ao último lote quando escritos pelos helpers de reprocessamento.
