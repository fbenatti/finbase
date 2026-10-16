export const BEHAVIOR_LABELS: Record<string, string> = {
  SOURCE_EXCLUDE_PREFIX: "Excluir da origem antes do forecast (prefixo)",
  SOURCE_MEAN_30D_PREFIX: "Compor pela média de 30 dias (prefixo)",
  SOURCE_MAX_PREVIOUS_MONTH_PREFIX: "Compor pelo maior ritmo diário: mês anterior ou modelo atual",
  SOURCE_GITHUB_COPILOT_OVERAGE_2_PHASE: "GitHub Copilot: projeção em duas fases do overage",
  SOURCE_VALIDITY: "Interromper previsão sem dados recentes",
  CHARGE_INTERVAL: "Ratear fatura mensal (ChargePeriodEnd)",
  RECURRING_MONTHLY: "Replicar última compra mensal",
  REPEAT_NEXT_MONTH: "Repetir só no mês seguinte",
  RECURRING_UNTIL_END: "Replicar até fim do período",
  CANCEL_ON_REFUND: "Cancelar compromisso (refund)",
  REPLACE_VALUE: "Substituir por valor fixo",
};
