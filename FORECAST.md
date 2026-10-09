# Forecast FinOps

## Objetivo

O Forecast projeta custos futuros a partir dos dados realizados das tabelas Gold/Iceberg. A configuração é feita no frontend e usa o schema real da tabela selecionada; portanto, o motor não depende exclusivamente do MODELO FOCUS.

O PostgreSQL guarda somente configuração, ajustes e controle. Os resultados financeiros permanecem em Iceberg/Parquet.

## Arquitetura

```text
Gold realizada -> agregação diária no Trino -> motor de Forecast
              -> ajustes configuráveis -> resultado Iceberg
```

O motor não carrega a Gold detalhada inteira em Pandas. A agregação ocorre no Trino antes do processamento.

## Tipos e camadas

Dados realizados usam:

```text
tipo = realizado
```

Resultados projetados usam:

```text
tipo = forecast
```

Os resultados são gravados no namespace Iceberg `forecast`, em tabela separada da Gold realizada.

Campos principais do resultado:

```text
forecast_month
forecast_value
tipo
selected_method
fallback_level
history_points
backtest_wape
```

## Metadata automático

Ao escolher uma tabela Gold, a API consulta o Trino e descobre medidas numéricas, colunas de data e período, dimensões textuais, campos criados por regras de negócio e disponibilidade de `realizado`.

Nenhum nome de coluna de provedor deve ser tratado como universal.

Datas em texto podem estar nos formatos `YYYY-MM-DD`, `DD/MM/YYYY` ou `MM/DD/YYYY`. Os parsers são executados com `TRY` para que uma data inválida não interrompa todo o processamento.

## Sugestões por destino

Quando o nome da tabela ou do nome físico contém `FOCUS`, o sistema pode sugerir, somente se existirem no schema:

```text
cloud_origem
departamento
servicename
subaccountname
```

`ServiceName` is the FOCUS service column for Azure (`consumedService`) and GCP (`service.description`). `ResourceName` / `PricingCategory` are optional and much finer (Azure product vs GCP SKU/instance); they should not be the default forecast grain.

Para outros destinos Gold, nenhuma dimensão é pré-selecionada. Todas as dimensões reais ficam disponíveis para escolha.

Quando o destino FOCUS possui `ChargePeriodEnd`, a UI sugere essa coluna como fim
do intervalo de rateio. O forecast permanece **único**; cada conexão pode optar
por rateio (`charge_interval`) ou série diária padrão (`none`).

Configurações legadas com `period_allocation = charge_interval` global continuam
funcionando até serem editadas e migradas para regras por conexão.

O usuário pode selecionar, remover, selecionar todas ou limpar todas as dimensões. Também pode criar uma série sem dimensões.

## Configuração

Cada configuração pertence a uma tabela Gold.

Campos principais:

```text
name
destination_table_id
measure_column
currency_column
date_column
period_column
type_column
dimensions
cutoff_days
horizon_months
growth_rate
model_mode
enabled_windows
recent_weight
minimum_daily_points
minimum_monthly_points
null_dimension_label
period_allocation          # legado; preferir connection_allocation_rules
charge_end_column          # legado; default sugerido para regras por conexão
connection_allocation_rules
active
version
```

`connection_allocation_rules` — lista JSON na mesma linha de `forecast_config`:

```text
[
  {
    "connection_code": "ibm-ti",
    "connection_id": 5,
    "period_allocation": "charge_interval",
    "charge_end_column": "ChargePeriodEnd"
  }
]
```

- **Padrão para todas as conexões:** `none` (série diária; `MONTHLY_LUMP` se fatura mensal).
- **Conexões listadas:** `charge_interval` com `charge_end_column` (default FOCUS: `ChargePeriodEnd`).
- O motor identifica a conexão via `cloud_origem` e/ou `source_connection_id` na Gold.
- Demais conexões (ex.: GCP diário) não entram na lista e mantêm agregação diária.

`period_allocation` (legado):

```text
none             -> usa a série diária agregada como está (padrão)
charge_interval  -> rateio global (fallback se connection_allocation_rules vazio)
```

Quando uma regra usa `charge_interval`, informe `charge_end_column`
(ex.: `ChargePeriodEnd` no FOCUS) ou use o fallback para o último dia de
`_billing_period`. O motor gera uma série diária sintética e aplica os
métodos 7/14/28 ou `AUTO_BACKTEST` sobre ela.

Defaults:

```text
cutoff_days = 2
horizon_months = 12
growth_rate = 0,83% ao mês
model_mode = AUTO_BACKTEST
enabled_windows = 7, 14 e 28 dias
recent_weight = 70%
minimum_daily_points = 7
null_dimension_label = Não classificado
```

O perfil padrão é criado automaticamente quando existe uma Gold ativa com medida e data válidas.

## Medida e dimensões

`realizado` é sugerido quando existe e é numérico. Outras medidas numéricas podem ser escolhidas conforme o schema, como `EffectiveCost`, `BilledCost`, `ListCost` ou `ContractedCost`.

Se a medida configurada não existir ou estiver vazia, a execução deve ser bloqueada. Não há fallback silencioso.

As dimensões definem a chave da série. Quanto mais granular a configuração, maior a quantidade de séries e o custo da execução.

## Cutoff e mês corrente

O cutoff é o menor entre a maior data válida observada na fonte e o limite
`hoje - cutoff_days`. Isso mantém o último realizado conhecido quando uma fonte
está atrasada ou carimba o snapshot no início do mês; não subtrai a defasagem da
fonte uma segunda vez. O limite de hoje impede usar dados futuros. Com
`cutoff_days = 2` em 12/10:

```text
01 a 10 -> realizado
11 até o fim do mês -> forecast
```

Assim, os dados só avançam até 10/10 quando disponíveis; se a fonte só tiver
dados até 01/10, o cutoff permanece em 01/10 em vez de retroceder para o mês
anterior. Dados posteriores a 10/10 não entram no realizado nem nas janelas.

O realizado é somado até o cutoff. A média diária selecionada é multiplicada pelos dias restantes:

```text
forecast_restante = média_diária × dias_restantes
estimativa_do_mês = realizado_acumulado + forecast_restante
```

Não deve existir sobreposição entre realizado e forecast.

## Granularidade mensal (CSV/fatura)

Alguns provedores (ex.: IBM FOCUS) entregam o mês corrente como fatura
agregada — uma única data de cobrança ou intervalo Start→End cobrindo o mês,
sem detalhe diário.

Essa proteção é específica para **CSV enviados manualmente**, cuja granularidade
é detectada no preview e persistida em `manual_csv_metadata`. Não deve ser
inferida novamente para extrações dos provedores: por exemplo, o snapshot
Azure TI do mês corrente traz `Date` igual ao primeiro dia do período, embora
contenha custo realizado acumulado até D-2.

Regras:

```text
CSV manual no mês corrente + granularidade mensal -> Bronze OK; Gold realizado bloqueado; Forecast ignora o período
Mês fechado + granularidade mensal -> realizado normal na Gold
Extrações Azure/GCP do mês corrente -> realizado parcial na Gold e entrada para o forecast residual
```

No upload manual, preview e ingest retornam `monthly_grain`, `current_month`
e `warning_message` quando o mês corrente não pode alimentar realizado/forecast.

No motor de Forecast, cada série usa o próprio cutoff (última data observada
da série), sem zero-fill até a data global de outra origem. Séries com totais
mensais no dia 1 usam baseline `MONTHLY_LUMP` (média das taxas diárias de meses
fechados), evitando `MEAN_7 ≈ 0` por zero-fill entre meses e também a
multiplicação da fatura mensal por 28–31 dias quando o grid termina no dia da
cobrança (o que inflava reservas e assinaturas mensais em ~4×).

Detecção: quando a série tem ≤ 3 dias com cobrança na janela
(`nonzero_days <= 3`), o baseline é `fatura do último mês com cobrança ÷ dias
desse mês` e o método registrado é `MONTHLY_LUMP`. Séries diárias normais
continuam nos métodos 7/14/28/WEIGHTED/AUTO_BACKTEST.

Com regras por conexão (`charge_interval` só para IBM, etc.), totais mensais são
rateados uniformemente entre `date_column` e `charge_end_column` (ou o último dia
do período de faturamento) **apenas nas séries daquela conexão**, antes do
backtest. GCP/Azure diários permanecem na agregação diária normal.

## Meses futuros

```text
forecast_mês_n = baseline × (1 + growth_rate)^n
```

O número de dias do mês é considerado na conversão do baseline diário para o valor mensal.

## Métodos matemáticos

### Backtest automático

`AUTO_BACKTEST` compara as janelas habilitadas e escolhe a de menor erro histórico. Os candidatos são médias de 7, 14 e 28 dias, além da média ponderada quando houver dados suficientes.

### Médias fixas

```text
MEAN_7
MEAN_14
MEAN_28
```

### Média ponderada

O método `WEIGHTED` usa:

```text
baseline = média_janela_curta × recent_weight
         + média_janela_longa × (1 - recent_weight)
```

Com o padrão atual:

```text
70% × média dos últimos 7 dias
30% × média dos últimos 28 dias
```

`recent_weight` define a influência das janelas. `minimum_daily_points` define somente a quantidade mínima de dados necessários; eles não são o mesmo conceito.

## Perfis de agressividade

```text
Conservador: MEAN_28 e crescimento de 0,5%
Ponderado: WEIGHTED e peso recente de 70%
Padrão: AUTO_BACKTEST e crescimento de 0,83%
Agressivo: MEAN_7 e crescimento de 1,5%
```

Todos os parâmetros continuam editáveis.

## Ajustes configuráveis

### Regras de negócio do Forecast

As regras operacionais do forecast são configuradas e versionadas junto de cada
`ForecastConfig`, na tabela PostgreSQL `forecast_business_rule`. Elas não ficam
chumbadas no código e suas colunas são validadas contra o metadata da Gold.

Cada regra define coluna e valores de identificação, tipo de cobrança
(`Purchase`, `Usage`, `Refund`, `Credit` ou `Recurring commitment`), comportamento
(`REPEAT_NEXT_MONTH`, `RECURRING_MONTHLY`, `RECURRING_UNTIL_END`,
`CANCEL_ON_REFUND` ou `REPLACE_VALUE`), referência, período inicial/final,
crescimento, valor substituto, prioridade e escopo por dimensões da série.

A ordem é: histórico realizado, método matemático, regras de compra/reserva,
cancelamentos/refunds, ajustes percentuais e publicação. Refunds devem ser
associados por referência ou escopo; um refund não cancela projeções não
relacionadas.

Endpoints: `GET/POST /forecast/configs/{id}/business-rules` e
`PUT/DELETE /forecast/business-rules/{id}`. A validação da configuração também
valida as colunas e períodos das regras ativas.

Tipos permitidos:

```text
PERCENTAGE
FIXED_VALUE
ADD_VALUE
SUBTRACT_VALUE
ZERO
CUSTOM_GROWTH
```

Os ajustes podem filtrar dimensões, possuir vigência e prioridade. Não é permitido inserir SQL ou fórmula livre pelo frontend.

## Execução e acompanhamento

Fluxo manual:

```text
Escolher Gold -> carregar metadata -> configurar -> salvar -> rodar
```

Fases:

```text
Validando metadata
Validando histórico
Agregando histórico no Trino
Executando backtest
Calculando mês corrente
Calculando meses futuros
Aplicando ajustes
Gravando Iceberg
Publicando forecast
```

A tela atualiza a cada três segundos e mostra status, fase, linhas escritas, séries processadas, progresso e erros. O banner global diferencia Forecast de Extração e Reprocessamento.

Estados: `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED` e `CANCELLED`.

Configurações podem ser criadas, editadas, duplicadas e apagadas. Uma configuração com execução ativa não pode ser apagada. Apagar a configuração não apaga automaticamente resultados históricos.

## Agendamento

O fluxo automático é:

```text
Scan -> novos arquivos -> Bronze -> Gold -> Forecast
```

O Forecast só é enfileirado quando existem novos dados, o reprocessamento Gold termina com sucesso, a configuração está ativa, o worker está saudável e não há execução concorrente.

## Acurácia de Forecast (simulados mês a mês)

O dashboard de acurácia responde a pergunta: *"o que eu projetava no início do
mês está perto do que eu realmente realizei?"*

### Simulado (snapshot imutável)

Para cada mês-alvo M, uma execução de forecast roda **como se estivéssemos no
dia 2 de M (D-2)** — o snapshot é tirado no início do mês com os dados do mês
anterior fechado:

```text
POST /forecast/runs?config_id={id}&simulated_as_of=YYYY-MM-02
```

- `simulated_as_of` = dia 2 do mês-alvo (ex.: `2026-02-02` simula fevereiro;
  `2026-07-02` simula julho). O cutoff efetivo vira `as_of - cutoff_days` =
  último dia do mês anterior (ex.: 30/06 para o simulado de julho).
- O motor ignora a descoberta real do cutoff, usa `as_of - cutoff_days` como
  cutoff e limita a agregação a `<= as_of` — nenhum dado futuro vaza para o
  snapshot.
- O resultado é gravado como snapshot imutável no Iceberg
  (`forecast.result_run_{id}`), com `simulated_as_of` congelado no
  `config_snapshot` do `forecast_run`. **Nada é gravado no PostgreSQL**.
- Runs simulados **não** sobrescrevem a tabela de planning corrente.
- As **mesmas regras de negócio ativas** da configuração são aplicadas ao
  valor projetado. Para que regras que identificam por `chargecategory`
  tenham efeito, a coluna precisa estar entre as **dimensões** da
  configuração (senão o motor registra "não aplicada: campo fora das
  dimensões").

### Métricas

A comparação é feita sob demanda via Trino entre o snapshot congelado e a Gold
realizada:

```text
projetado_mês = SUM(forecast_value) do snapshot para o mês-alvo
realizado     = SUM(medida) da Gold para o mês-alvo (tipo = realizado)
variance      = realizado - projetado
variance_pct  = variance / projetado
wape          = |variance| / realizado
accuracy      = 1 - wape
bias          = (projetado - realizado) / realizado   # > 0 => superestimou
```

### Endpoints

```text
GET /forecast/accuracy/months                            # meses simulados + run de referência
GET /forecast/accuracy/summary?run_id=&target_month=     # KPIs do mês
GET /forecast/accuracy/dimensions?run_id=&target_month=&dimension=  # breakdown
GET /forecast/accuracy/history                           # evolução mês a mês
GET /forecast/accuracy/delta?run_id=&target_month=&mode=showback&parents=[]  # drill-down de ofensores
GET /forecast/accuracy/desvios?run_id=&target_month=&department=             # top 10 + / top 10 - no último nível
GET /forecast/accuracy/desvios/subcategories?run_id=&target_month=&department=<folha>  # por ServiceSubcategory
POST/GET /forecast/accuracy/acks · DELETE /forecast/accuracy/acks/{id}      # motivo do desvio (Ack)
```

A **aba Desvios** lista os desvios do mês no último nível de cada departamento
(top 10 positivos/subestimou e top 10 negativos/superestimou), respeitando o
departamento e o mês; `[+] ÚLTIMO NÍVEL` expande o breakdown por
`servicesubcategory` (a coluna precisa estar nas dimensões da configuração —
snapshots regenerados) e **Ack** registra categoria + motivo do desvio. O
widget **Motivos de desvio (Ack)** na Visão Esperada lista os acks do mês
anterior (padrão) com seletor de histórico.

O drill-down de ofensores (`/accuracy/delta`) mostra o **nível abaixo do
departamento selecionado** — mesma semântica do ExpectedView
(`driver = nível do caminho + 1`): `Consumo Cloud` → Ramo (Showback/Chargeback)
→ Provedor → Diretoria (nivel 4) → Projeto (nivel 5). `department` é o caminho
"N1 > N2 > ..." e `mode` = `showback` (exclui Chargeback, padrão) |
`chargeback` | `all`. Todos os endpoints de acurácia aceitam `department` e
`mode`, aplicando o filtro nos totais (KPIs, tendência, dimensões e ofensores).
Os agregados Gold/forecast por departamento são cacheados em memória — a
navegação por departamento não rescaneia a Gold.

O frontend expõe a página **Acurácia de Forecast** (`/forecast-accuracy`) com
KPIs, gráfico Projetado × Realizado por dimensão, detalhe por dimensão e a
evolução mês a mês.

### Reprocessamento de acurácia

Para regenerar os simulados (após mudanças de configuração/regras), use a aba
**Acurácia de Forecast** da página **Reprocessar** (`/reprocess?tab=accuracy`)
ou a API:

```text
POST /forecast/accuracy/reprocess
{"config_id": 9, "months": ["202602", "202603"]}
```

O worker processa um lote (`task_type = forecast_accuracy`), criando um
snapshot imutável por mês (`simulated_as_of = dia 2 do mês`). O dashboard usa
automaticamente o snapshot mais recente de cada mês. O andamento aparece na
própria aba (meses concluídos × total).

## APIs

```text
GET    /forecast/metadata/{destination_table_id}
GET    /forecast/configs
POST   /forecast/configs
PUT    /forecast/configs/{id}
DELETE /forecast/configs/{id}
POST   /forecast/configs/{id}/duplicate
POST   /forecast/configs/{id}/validate

GET    /forecast/configs/{id}/adjustments
POST   /forecast/configs/{id}/adjustments
PUT    /forecast/adjustments/{id}
DELETE /forecast/adjustments/{id}

POST   /forecast/runs?config_id={id}
GET    /forecast/runs
GET    /forecast/runs/{id}
POST   /forecast/runs/{id}/cancel
GET    /forecast/runs/{id}/results
GET    /forecast/runs/{id}/backtest
GET    /forecast/runs/{id}/export
```

## Controle PostgreSQL

`forecast_config` guarda a definição versionada. `forecast_adjustment` guarda ajustes dimensionais. `forecast_run` guarda status, progresso, métricas e tabela Iceberg publicada.

Essas tabelas não devem armazenar linhas financeiras detalhadas.

## Worker e estabilidade

Forecast usa a fila persistida de tarefas e o heartbeat do worker. A execução é bloqueada quando o worker está offline, evita concorrência por configuração, grava em Iceberg e mantém o resultado anterior em caso de falha.

## Boas práticas

### Cobranças recorrentes já realizadas no mês

As regras `RECURRING_MONTHLY` e `RECURRING_UNTIL_END` têm a opção visível
**Mês corrente**, persistida em `behavior_options.current_month_policy`:

- `AUTO`: preserva a classificação automática de cobrança mensal versus uso diário.
- `ZERO_WHEN_REALIZED`: qualquer total realizado diferente de zero encerra a
  projeção adicional da série no mês corrente, mesmo sem mês anterior. Se a
  cobrança ainda não entrou, projeta o total mensal de referência quando disponível.
- `DAILY_REMAINDER`: mantém a projeção matemática dos dias restantes.

Meses futuros continuam usando a referência mensal e o crescimento configurado.
Essas opções pertencem à regra, são versionadas e congeladas no snapshot do run;
não dependem de nomes fixos de provedores, projetos ou tipos de compromisso.

Em 09/10/2026 foi reproduzida a regressão de SAPTDF: compras de Reservation de
R$ 88.783,58 em outubro geravam mais R$ 329.767,60 de forecast (`MEAN_7D`).
O commit `3acdce04` havia condicionado o fechamento do mês à classificação
`lumpy`; `ab0609b4` passou a incluir todo o mês anterior no histórico. Como o
classificador conta até três datas de cobrança em todo o histórico, as três
datas de setembro mais duas de outubro perderam a classificação mensal.
A opção explícita `ZERO_WHEN_REALIZED` restaura o fechamento independente dessa
heurística. A regra de validade Azure-4P continua separada e configurável.

Agendamentos de reprocessamento precisam marcar as tarefas de reclassificação
como `scheduled_pipeline`. O worker espera as demais tarefas do mesmo agendamento
antes de liberar o forecast; em seguida usa as opções de rateio, acurácia e Gold
do agendamento. A execução manual isolada de forecast não materializa cubos Gold.

- Use `AUTO_BACKTEST` quando houver histórico suficiente.
- Use `WEIGHTED` quando quiser dar prioridade explícita ao consumo recente.
- Use `MEAN_28` para uma projeção mais conservadora.
- Comece com dimensões de cardinalidade controlada.
- Valide o cutoff antes de comparar realizado e forecast.
- Confira o método vencedor, o número de séries e os erros da execução.
