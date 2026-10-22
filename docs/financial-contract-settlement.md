# Liquidação de contratos financeiros na Silver

## Colunas financeiras

- `BilledCost`: valor bruto recebido do provedor.
- `realizado_cloud`: resultado da regra financeira do provedor. Para IBM:
  `BilledCost * 0.79036 + (BilledCost * 0.10) * 0.29505`.
- `consumo_realizado`: cópia canônica de `realizado_cloud`, usada pela
  liquidação contratual.
- `realizado`: saída financeira final. Em contratos de compromisso, recebe o
  orçamento mensal do pool rateado pelo peso positivo de `realizado_cloud`.
- `ajuste_contratual`: crédito gerado menos overage bruto. Valor positivo
  representa crédito; valor negativo representa overage.

## Saldos acumulados durante a vigência

Cada pool (contrato + cloud + ID Orçamento) carrega crédito e overage entre
competências. O crédito gerado no mês é somado ao crédito de abertura; o
overage bruto do mês é somado ao overage de abertura. Em seguida, os dois
saldos se compensam. O fechamento registra ambos os saldos, com overage
negativo para manter uma única convenção visual. O reprocessamento refaz a
sequência cronológica desde a primeira competência disponível.

Na competência final da vigência, o overage remanescente é registrado como
`settled_overage` (valor negativo) e o saldo de fechamento de overage zera.
Assim o dashboard mostra tanto o valor liquidado quanto o saldo após o
pagamento. Os campos `opening_overage`, `closing_overage` e
`settled_overage` registram abertura, saldo e liquidação; os campos existentes
`gross_overage`, `credit_used`, `credit_generated` e `closing_credit` preservam
os movimentos e o saldo de crédito.

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

## Tela de uso de compromissos

Rota: `/planejamento/consumo-de-compromissos` (componente
`FinancialContractsDashboard`, subaba `executive=compromisso`).

- Valor contratado: soma do orçamento mensal (`commitment`) de todas as
  competências do contrato, entre `valid_from` e `valid_until`.
- Consumido: soma de `consumption` nas competências publicadas.
- Saldo a consumir: contratado menos consumido.
- Projeção até o fim: consumido mais a média de `consumption` das últimas três
  competências, multiplicada pelas competências restantes da vigência.
- Situação por contrato: acima de 100% do contratado é "Estourado"; projeção
  acima de 105% é "Risco de estouro"; projeção abaixo de 85% é "Risco de sobra".
- Gráfico: acumulado real contra o planejado (orçamento acumulado por
  competência) e a projeção a partir da última competência publicada.
- Filtro de competência afeta os saldos de compensação, o acerto mensal e o
  detalhamento; os indicadores do topo são do contrato inteiro.

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
