# Auditoria operacional — 20/10/2026

## Estado de validação

**Validação real interrompida. Não considerar o motor v2.1 homologado.**
A execução manual de validação #9 (tarefa 8928) foi cancelada após o usuário
relatar sobrecarga da máquina. A consulta remanescente no Trino foi cancelada
por HTTP DELETE; a inspeção posterior retornou zero tarefas e zero consultas
ativas. Nenhuma segunda execução real foi iniciada.

## Falha original confirmada

As execuções #3 a #7 falharam antes de persistir os casos:
`module 'app.services.iceberg_service' has no attribute 'publish_dataframe_atomic'`.
A última execução concluída era #2, de 15/10/2026, com 408 sinais, 105 alertas
e 32 casos. Isso não representava ausência de anomalias: a publicação estava
quebrada. O escritor foi restabelecido com commit de snapshot Iceberg,
validação de schema e tratamento de resultado vazio.

## Divergências encontradas e alterações

- Referência incluía o próprio dia avaliado e dependia da ordem das linhas.
  O baseline agora utiliza apenas dias anteriores, com resíduos calculados
  sobre referências anteriores e referência congelada no início do episódio.
- Ausência de observação podia contar como zero e confirmar retorno. Lacunas
  e dias de carga parcial não confirmam os três dias válidos.
- Meses não coletados reduziam a média mensal como se fossem meses zerados.
- Compromissos não preservavam todas as dimensões do escopo, e compras normais
  podiam cair no detector de consumo contínuo. A comparação agora usa cobranças
  anteriores por recurso e escopo completo.
- Picos pontuais, compras e acumulados mensais eram multiplicados por 30,4.
  A projeção foi corrigida; a severidade ainda considera o excesso incorrido.
- A consolidação descartava contribuintes além do maior alerta e dos primeiros
  vinte. Os escopos alertados agora permanecem acessíveis como casos.
- Casos existentes eram simplesmente ignorados. Casos ativos agora recebem
  atualização auditada, preservando a referência e classificações humanas;
  casos encerrados não reabrem por reprocessamento.
- A prontidão consultava o fingerprint da própria execução nova. Agora consulta
  a execução concluída anterior e inclui snapshot da origem, versão da
  configuração e versão do modelo. Correções históricas podem ser reavaliadas.
- O total anual de impacto acumulava subtotais repetidos; agora é doze vezes
  o total mensal.
- A página não acompanhava a execução. Agora mostra falha/conclusão e atualiza
  os casos ao término.

## Contenção da sobrecarga

Na validação, o limite de leitura foi ampliado de 90 para os 180 dias da
configuração. Essa ampliação foi revertida. O código anterior também permitia
até doze tentativas de uma consulta após falhas do coordenador, e o cancelamento
do worker não chegava às consultas em andamento.

Proteções atuais, específicas das consultas do motor v2:

- Janela máxima de 90 dias para histórico de detecção.
- Uma tentativa por consulta, sem reenvio automático após falha.
- Timeout máximo de 120 segundos por consulta.
- Cancelamento do worker propagado ao protocolo HTTP de cancelamento do Trino.

O worker local foi reiniciado ocioso para carregar essas proteções. Não foi
iniciada nova consulta financeira para validá-las.

## Evidências e limites

- 338 testes de anomalias passaram em uma execução intermediária da suíte.
- Após as proteções finais, 11 testes locais de operação/cancelamento passaram,
  sem consultas ao Trino.
- TypeScript passou após a inclusão do acompanhamento da execução na página.
- O escritor atômico foi testado com catálogo Iceberg local temporário:
  substituição, resultado vazio e preservação do snapshot em erro de schema.
- A publicação e o ciclo completo no ambiente real continuam sem validação
  após a correção. A execução #9 não é evidência de sucesso.
- Os 32 casos históricos permanecem preservados; não houve recálculo integral
  de seus valores nem certificação individual de sua aderência.
- As demais tabelas analíticas v2 declaradas no schema não foram homologadas
  como materializações operacionais nesta auditoria.
