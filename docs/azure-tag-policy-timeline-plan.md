# Plano: timeline da execução das policies de tags

## Objetivo

Exibir a evolução da aderência às tags por subscription e no consolidado, junto aos eventos de publicação e execução das Azure Policies. A timeline inicia em 12/10/2026, sem inventar snapshots onde não há inventário histórico.

## Fontes e limites

- Inventários Resource Graph já ingeridos na Bronze: snapshots de 15, 16, 17, 19 e 20/10/2026.
- Azure Activity Log: buscar eventos de definições, assignments, avaliações e remediações desde 12/10/2026, enquanto dentro da retenção padrão de 90 dias.
- Azure Policy Insights: consultar resumos de remediação para expor os deployments com sucesso e falha, além do estado do recurso de remediação.
- A aderência histórica é calculada com as chaves ativas no momento da importação (`projeto`, `diretoria`, `centro-de-custos`). Cada snapshot guarda as chaves usadas.
- 12–14/10 e 18/10 não têm snapshot Bronze conhecido. Eventos nesses dias não equivalem a uma medição da aderência.
- O percentual de presença de tags do Resource Graph é distinto do compliance oficial do Azure Policy.

## Persistência

1. `azure_tag_policy_snapshot`: uma linha append-only por coleta e subscription, incluindo timestamp, referência ao blob Bronze, chaves, denominador, completos, parciais, sem tags e percentual.
2. `azure_tag_policy_event`: uma linha por evento Azure com timestamp, subscription, operação, status, assignment, remediação, correlation ID e payload sanitizado.

O consolidado é calculado pela soma dos snapshots das subscriptions no mesmo instante; não é gravado como uma linha independente.

## Entrega

- Backfill idempotente dos inventários Bronze existentes e dos eventos Azure desde 12/10. A consulta da timeline também importa novos snapshots Bronze automaticamente; a leitura de eventos Azure pode ser repetida pelo botão.
- API para consultar snapshots, lacunas e eventos; API para executar novamente o backfill.
- Seção “Evolução” no Dashboard Executivo de Tags com consolidado, seletor de subscription e trilha de execução.
- A ingestão não recalcula nem sobrescreve snapshots antigos; cada blob Bronze é importado uma vez por subscription.

## Critérios operacionais

- Reexecutar o backfill não duplica registros.
- Uma falha de acesso a uma subscription fica registrada como erro parcial; não invalida snapshots Bronze já importados.
- Data sem evidência de inventário é exibida como lacuna, não como 0%.
- Eventos sem evidência suficiente para identificar assignment ou remediação continuam visíveis com os campos disponíveis.
