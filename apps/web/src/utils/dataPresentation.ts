/** Product labels are separate from the identifiers used by APIs and storage. */
export const DATA_LAYER_LABELS: Record<string, string> = {
  bronze: "Base de origem",
  silver: "Base preparada",
  gold: "Base consolidada",
};

export const DATA_SERVICE_LABELS: Record<string, string> = {
  trino: "Serviço de consultas",
  iceberg_catalog: "Catálogo de dados",
  nessie: "Catálogo de dados",
  minio: "Armazenamento de arquivos",
};

const MODULE_LABELS: Record<string, string> = {
  modules: "Módulos",
  connections: "Fontes de dados", rules: "Regras de negócio", etl: "Operação de dados",
  analysis: "Explorador de dados", forecast: "Projeções", planning: "Planejamento",
  reservations: "Reservas e compromissos", savings: "Otimização", periods: "Períodos",
  destinations: "Catálogo de dados", schema: "Estrutura dos dados", fact: "Registros de custos",
  csv_upload: "Importação de arquivos", clone_rules: "Regras de compromissos", gold: "Dados consolidados",
  ai_copilot: "Assistente de IA", ai_dashboards: "Adoção de IA", ai_provider_dashboards: "Provedores de IA",
  azure_ptu: "Capacidade de IA", azure_resource_graph: "Inventário de recursos",
  azure_tag_policy: "Políticas de classificação", oci_operational: "Operação de recursos",
  ideas: "Iniciativas", auth: "Login corporativo",
  users: "Pessoas e acessos", groups: "Grupos", roles: "Funções e permissões", audit: "Trilha de auditoria",
  anomalies: "Alertas", notifications: "Avisos do sistema",
};

export function moduleDisplayName(module: string): string {
  return MODULE_LABELS[module] || dataDisplayText(module.replace(/_/g, " "));
}

/** Use only for display copy, never for queries, form values or API payloads. */
export function dataDisplayText(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .replace(/\biceberg\.(bronze|silver|gold)\.[a-z0-9_$]+/gi, (_, layer: string) => DATA_LAYER_LABELS[layer.toLowerCase()])
    .replace(/\biceberg\.(bronze|silver|gold)\b/gi, (_, layer: string) => DATA_LAYER_LABELS[layer.toLowerCase()])
    .replace(/\biceberg(?:\.[a-z0-9_]+){1,2}\b/gi, "base de dados")
    .replace(/\biceberg_catalog\b/gi, "catálogo de dados")
    .replace(/(^|[_-])(?:4p|tlf|vivo|telef[oó]nica|telefonica|ibm-ti|dimon)(?=$|[_-])/gi, "$1ambiente")
    .replace(/\b(?:4p|tlf|vivo|telef[oó]nica|telefonica|ibm-ti|dimon)\b/gi, "ambiente configurado")
    .replace(/\b(?:Apache\s+)?(?:Py)?Iceberg\b/gi, "acervo de dados")
    .replace(/\bTr(?:i|e)no\b/gi, "serviço de consultas")
    .replace(/\bNessie\b/gi, "catálogo de dados")
    .replace(/\bMinIO\b/gi, "armazenamento de arquivos")
    .replace(/\bPostgres(?:ql)?\b/gi, "banco de dados")
    .replace(/\bDuckDB\b/gi, "serviço de consultas local")
    .replace(/\b(?:Azure|AWS|GCP|OCI)\s+(?:Blob|S3|Storage|Object Storage)\b/gi, "armazenamento de arquivos")
    .replace(/\b(?:data[ -]?lake(?:house)?|lakehouse)\b/gi, "Central de dados")
    .replace(/\bETL\b/gi, "processamento de dados")
    .replace(/\bBronze\b/gi, "base de origem")
    .replace(/\bSilver\b/gi, "base preparada")
    .replace(/\bGolds\b/gi, "bases consolidadas")
    .replace(/\bGold\b/gi, "base consolidada")
    .replace(/\bstagings\b/gi, "tabelas temporárias")
    .replace(/\bstaging\b/gi, "área temporária")
    .replace(/\bno base\b/g, "na base")
    .replace(/\bdo base\b/g, "da base")
    .replace(/^o base\b/i, "A base")
    .replace(/\bo base\b/g, "a base");
}
