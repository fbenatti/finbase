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
3. **Sincronização contínua:** conectada ao ciclo de vida do reprocessamento no worker/API; ainda exige revisão dos fluxos secundários que escrevem em `etl_task` fora desses pontos.
4. **Registros próprios:** `forecast_run` continua sendo fonte especializada do resultado de forecast. A tarefa ETL vinculada aparece na visão geral; o detalhe do resultado ainda deve navegar/consultar `forecast_run`. Extração está coberta quando representada por `etl_task`.
5. **Aposentadoria de endpoints antigos:** não realizada. Rotas antigas continuam por compatibilidade e devem ser removidas apenas quando não houver consumidores, idealmente em release posterior.

## Implantação em produção

1. Fazer backup e confirmar espaço/índices para `processing_log`.
2. Implantar API que cria a tabela e faz o backfill; validar `GET /processing-logs` e amostras de cada tipo.
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
