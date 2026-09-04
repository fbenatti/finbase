/** Labels amigáveis por módulo para o diff do histórico. Fallback = nome técnico. */
export const FIELD_LABELS: Record<string, Record<string, string>> = {
  rules: {
    name: "Nome",
    description: "Descrição",
    priority: "Prioridade",
    active: "Ativo",
    valid_from: "Válido de",
    valid_until: "Válido até",
    target_column: "Coluna alvo",
    rule_type: "Tipo de regra",
    config_json: "Configuração",
    conditions: "Condições",
    expression: "Expressão",
    connection_id: "Conexão",
    destination_table_id: "Tabela destino",
  },
  connections: {
    code: "Código",
    name: "Nome",
    connector_type: "Tipo de conector",
    cloud_name: "Provedor",
    config_json: "Configuração",
    blob_prefixes: "Prefixos de blob",
    active: "Ativo",
  },
  forecast: { growth_rate: "Taxa de crescimento", horizon_months: "Horizonte (meses)", cutoff_days: "Dias de corte" },
  planning: { name: "Nome", parent_id: "Departamento pai" },
};
