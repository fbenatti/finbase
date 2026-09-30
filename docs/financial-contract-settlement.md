# Liquidação de contratos financeiros na Silver

## Colunas financeiras

- `BilledCost`: valor bruto recebido do provedor.
- `realizado_cloud`: resultado da regra financeira do provedor. Para IBM:
  `BilledCost * 0.79036 + (BilledCost * 0.10) * 0.29505`.
- `consumo_realizado`: cópia canônica de `realizado_cloud`, usada pela
  liquidação contratual.
- `realizado`: saída financeira final. Em contratos de compromisso, recebe o
  orçamento mensal do pool rateado pelo peso positivo de `realizado_cloud`.
- `ajuste_contratual`: `realizado - realizado_cloud`. Valor positivo representa
  crédito gerado; valor negativo representa consumo de crédito/overage coberto.

## Resolução do orçamento

O orçamento não é localizado pelos campos opcionais da versão de carga. O
escopo é resolvido pelo organograma:

1. Cada lançamento mensal aponta para um departamento.
2. O departamento herda o `ID Orçamento` mais próximo na cadeia de pais.
3. A cloud é obtida do caminho canônico
   `Consumo Cloud > Chargeback/Showback > Cloud`.
4. O compromisso mensal soma o valor próprio do nó e os valores de todos os
   descendentes com a mesma combinação `cloud + ID Orçamento`.
5. O rateio usa somente a participação de cada linha no `realizado_cloud` do
   pool; o orçamento individual de um projeto não limita seu consumo.

Exemplo: um pool IBM `EXTERNO-IBM` com orçamento mensal de R$ 2 milhões e
consumo calculado de R$ 600 mil no projeto A e R$ 400 mil no projeto B gera
pesos de 60% e 40%. O `realizado` será R$ 1,2 milhão e R$ 800 mil,
respectivamente.
