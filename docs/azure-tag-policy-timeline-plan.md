# Plano: timeline da execução das policies de tags

## Objetivo

Exibir a evolução da aderência às tags por subscription e no consolidado em uma timeline executiva, acionada ao clicar no KPI “Aderência global”. A timeline inicia em 12/10/2026, sem inventar snapshots onde não há inventário histórico.

## Fontes e limites

- Inventários Resource Graph já ingeridos na Bronze: snapshots de 15, 16, 17, 19 e 20/10/2026.
- A aderência histórica é calculada com as chaves ativas no momento da importação (`projeto`, `diretoria`, `centro-de-custos`). Cada snapshot guarda as chaves usadas.
- 12–14/10 e 18/10 não têm snapshot Bronze conhecido; esses dias aparecem como lacunas, não como 0%.
- O percentual de presença de tags do Resource Graph é distinto do compliance oficial do Azure Policy.

## Persistência

1. `azure_tag_policy_snapshot`: uma linha append-only por coleta e subscription, incluindo timestamp, referência ao blob Bronze, chaves, denominador, completos, parciais, sem tags e percentual.
O consolidado é calculado pela soma dos snapshots das subscriptions no mesmo instante; não é gravado como uma linha independente.

## Entrega

- Importação idempotente dos inventários Bronze existentes desde 12/10. A consulta da timeline importa snapshots ainda não registrados automaticamente.
- API para consultar snapshots e lacunas.
- Timeline executiva no Dashboard de Tags, recolhida por padrão, com marcos de evolução do consolidado e seletor opcional de subscription.
- A ingestão não recalcula nem sobrescreve snapshots antigos; cada blob Bronze é importado uma vez por subscription.

## Critérios operacionais

- Reexecutar o backfill não duplica registros.
- Data sem evidência de inventário é exibida como lacuna, não como 0%.
