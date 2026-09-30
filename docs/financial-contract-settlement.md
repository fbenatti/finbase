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

Se uma linha contratual não tiver classificação organizacional, mas o contrato
possuir exatamente um `ID Orçamento` para aquela cloud, o ID é herdado da
configuração do contrato. Se houver dois ou mais IDs possíveis, a publicação é
bloqueada: o sistema não cria pool de orçamento zero nem escolhe um ID
arbitrariamente.

Registros de consumo sem projeto/subconta também não podem desaparecer do
dashboard executivo. Quando o pool possui um único nó organizacional que
declara explicitamente aquele `ID Orçamento`, essas linhas são classificadas no
próprio nó proprietário do pool (por exemplo, `Chargeback > IBM`), sem criar um
projeto fictício. Ambiguidade de proprietário bloqueia a publicação.

Exemplo: um pool IBM `EXTERNO-IBM` com orçamento mensal de R$ 2 milhões e
consumo calculado de R$ 600 mil no projeto A e R$ 400 mil no projeto B gera
pesos de 60% e 40%. O `realizado` será R$ 1,2 milhão e R$ 800 mil,
respectivamente.

## Materialização executiva

O cubo `dest_dm_cost_visao_completa` materializa como medidas somáveis:

- `realizado`;
- `realizado_cloud`;
- `consumo_realizado`;
- `ajuste_contratual`;
- `billedcost`;
- `consumedquantity`.

Fontes auxiliares que não possuem uma dessas medidas contribuem com zero. Isso
mantém forecast e orçamento compatíveis com o mesmo cubo sem apagar a memória
de cálculo das linhas realizadas.
