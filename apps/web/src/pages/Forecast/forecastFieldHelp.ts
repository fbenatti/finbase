export const FORECAST_CONFIG_FIELD_HELP = {
  name: "Nome mostrado na lista de configurações e no reprocessamento. Não altera o cálculo.",
  table:
    "base consolidada de origem dos dados realizados. Cada tabela pode ter apenas uma configuração de forecast. Na edição, a tabela não pode ser trocada.",
  measure:
    "Coluna numérica que será projetada (ex.: efetivado, BilledCost, EffectiveCost). O horizonte e os métodos aplicam-se sobre o total diário dessa medida, agregado por dimensões.",
  date:
    "Coluna de data ou início do período de consumo usada para montar a série diária (ex.: ChargePeriodStart). O cutoff e as janelas usam os últimos dias com dados nessa coluna.",
  horizon:
    "Quantidade de meses futuros a prever a partir do mês de corte de cada série (1–36).\n\nEx.: 12 = estimativa do mês corrente + 11 meses seguintes, sempre sobre a medida selecionada.",
  cutoff:
    "Dias finais ignorados no efetivado e no histórico usado pelas janelas (D-n).\n\nEx.: cutoff 2 e último dado no dia 12 → efetivado até o dia 10; dias 11–12 ficam de fora; o forecast cobre o restante do mês.",
  profile:
    "Atalho que preenche método, crescimento e peso dos dados recentes. Escolha Personalizada para ajustar cada parâmetro manualmente.",
  method:
    "Como calcular a média diária (baseline):\n• AUTO_BACKTEST — compara as janelas habilitadas e escolhe a de menor erro (WAPE).\n• MEAN_7/14/28 — média fixa dos últimos N dias.\n• WEIGHTED — combina a menor e a maior janela habilitada com o peso recente.",
  windows:
    "Janelas de lookback (em dias) para calcular a média diária histórica. Opções: 7, 14 e 28 dias.\n\n• Uma janela — só ela entra nos candidatos (o método fixo correspondente precisa estar habilitado aqui).\n• Várias janelas — todas entram na disputa; em AUTO_BACKTEST o motor escolhe a de menor erro. WEIGHTED usa a menor e a maior entre as selecionadas.\n• Os últimos N dias respeitam o cutoff D-n.",
  growth:
    "Taxa de crescimento mensal composta aplicada aos meses futuros após o baseline diário.\n\nEx.: 0,83% ao mês ≈ 10% ao ano.",
  recentWeight:
    "Peso da janela mais curta na média ponderada (WEIGHTED e candidato do AUTO_BACKTEST).\n\nPadrão 70% = 70% da média dos últimos 7 dias + 30% da média dos últimos 28 dias (quando ambas estão habilitadas).",
  minDays:
    "Mínimo de pontos diários na janela para considerá-la válida. Séries com menos dados ignoram aquela janela ou usam fallback (MEAN_AVAILABLE).",
  dimensions:
    "Colunas que definem cada série independente (chave de agrupamento).\n\nMais dimensões = mais séries e execução mais pesada. Inclua cloud_origem para separar as origens de nuvem. Pode ficar vazio para uma série única.",
  intervalConnections:
    "Conexões cuja fatura mensal deve ser alocada uniformemente entre a coluna de data e o fim do intervalo (ex.: IBM com ChargePeriodStart → ChargePeriodEnd).\n\nDemais conexões usam série diária normal, sem alocação.",
  chargeEnd:
    "Coluna com o fim do intervalo de cobrança para a alocação (ex.: ChargePeriodEnd).\n\nVazio = último dia do período de faturamento (_billing_period) ou mesma coluna de início.",
} as const;

export const FORECAST_RULE_FIELD_HELP = {
  name: "Nome da regra para identificar na lista. Não altera o cálculo.",
  identificationMode:
    "Como localizar linhas na base consolidada:\n• Coluna única — um campo e lista de valores.\n• Cláusulas OR — qualquer cláusula (coluna + valores) que coincidir.",
  column: "Coluna da base consolidada usada para identificar registros afetados por esta regra.",
  values: "Valores exatos (separados por vírgula) que devem coincidir na coluna de identificação.",
  chargeType: "Tipo de cobrança filtrado pela regra (Usage, Purchase, Refund, etc.).",
  behavior:
    "O que fazer com os dados:\n• Excluir da origem — remove da base consolidada antes da agregação quando o campo começa com o valor informado; o campo não precisa ser dimensão.\n• Média de 30 dias — separa a origem e calcula esse componente com a média diária dos últimos 30 dias; ideal para overage que reinicia no ciclo mensal.\n• Ratear fatura mensal — distribui valor no intervalo Start→End.\n• Recorrente — replica compra nos meses seguintes.\n• Cancelar em refund — encerra ao detectar estorno.\n• Substituir — troca por valor fixo.",
  scope:
    "Filtro adicional por dimensão (formato dim=valor, separado por vírgula).\n\nEx.: cloud_origem=fonte-configurada restringe a regra a essa origem.",
  chargeEndColumn: "Coluna com o fim do período de cobrança usada na alocação (ex.: ChargePeriodEnd). Obrigatória para comportamento de alocação.",
  valueColumn: "Coluna numérica usada como base do valor da regra (geralmente a mesma medida do forecast).",
  replacementValue: "Valor fixado que substitui o Forecast quando o comportamento é Substituir por valor fixado.",
  startPeriod: "Primeiro mês (AAAA-MM) em que a regra passa a valer.",
  endPeriod: "Último mês (AAAA-MM) da regra. Vazio = sem data de término.",
  priority: "Ordem de aplicação quando várias regras coincidem. Menor número = maior prioridade.",
  active: "Regras inativas são ignoradas na execução do Forecast.",
} as const;
