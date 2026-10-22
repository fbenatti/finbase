export const BEHAVIOR_LABELS: Record<string, string> = {
  SOURCE_EXCLUDE_PREFIX: "Excluir da origem antes do forecast (prefixo)",
  SOURCE_MEAN_30D_PREFIX: "Compor pela média de 30 dias (prefixo)",
  SOURCE_MAX_PREVIOUS_MONTH_PREFIX: "Combinar pelo maior ritmo diário: mês anterior ou modelo atual",
  SOURCE_GITHUB_COPILOT_OVERAGE_2_PHASE: "GitHub Copilot: previsão em duas fases do overage",
  SOURCE_VALIDITY: "Parar a previsão sem dados recentes",
  CHARGE_INTERVAL: "Ratear fatura mensal (ChargePeriodEnd)",
  RECURRING_MONTHLY: "Repetir a última compra mensal",
  REPEAT_NEXT_MONTH: "Repetir apenas no mês seguinte",
  RECURRING_UNTIL_END: "Replicar até o fim do período",
  CANCEL_ON_REFUND: "Cancelar compromisso (refund)",
  REPLACE_VALUE: "Substituir por valor fixado",
};
