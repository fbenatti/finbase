/** Labels amigáveis por módulo para o diff do histórico. Fallback = nome técnico. */
export const FIELD_LABELS: Record<string, Record<string, string>> = {
  rules: {
    name: "Nome",
    description: "Finalidade",
    priority: "Prioridade",
    active: "Ativo",
    valid_from: "Vigência inicial",
    valid_until: "Vigência final",
    target_column: "Coluna de destino",
    rule_type: "Tipo de ajuste",
    config_json: "Parâmetros",
    conditions: "Critérios",
    expression: "Fórmula",
    connection_id: "Conexão de origem",
    destination_table_id: "Tabela de destino",
  },
  connections: {
    code: "Identificador",
    name: "Nome",
    connector_type: "Tipo de integração",
    cloud_name: "Provedor",
    config_json: "Parâmetros",
    blob_prefixes: "Prefixos de arquivo",
    active: "Ativo",
  },
  forecast: { growth_rate: "Crescimento mensal", horizon_months: "Horizonte do Forecast (meses)", cutoff_days: "Janela de corte (dias)" },
  planning: { name: "Nome", parent_id: "Unidade pai" },
};
