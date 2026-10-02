import { useState, useEffect, useMemo, useRef } from "react";
import {
  Box, Typography, Button,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Divider,
  Select, MenuItem, FormControl, InputLabel, Alert, CircularProgress,
  FormControlLabel, Checkbox, Tab, Tabs, Paper, InputAdornment, Stack, Snackbar,
} from "@mui/material";
import { Add, AutoAwesome, Cloud, Dataset, Download, MenuBook, Psychology, Search, SmartToy, Storage, Storefront } from "@mui/icons-material";
import {
  getConnections, createConnection, updateConnection,
  deleteConnection, getConnectionsGrid, testConnection, testConnectionConfig, SourceConnection,
  formatApiError, extractMcaCommitmentInvoices, getMcaCommitmentProfiles, getMcaCommitmentStatus,
} from "../../services/api";
import { api } from "../../services/api";
import EmptyState from "../../components/EmptyState";
import ConnectionsDataGrid, { ConnectionTestMessage, getProviderLabel } from "./ConnectionsDataGrid";
import RecordHistory from "../../components/RecordHistory";
import AzureSourcesPanel from "./AzureSourcesPanel";
import IntegrationsPanel from "./IntegrationsPanel";

const CONNECTOR_TYPES = [
  { value: "azure_mca_invoice", label: "Azure MCA (Invoices)", scope: "azure" },
  { value: "azure_blob", label: "Azure Blob Storage", scope: "azure" },
  { value: "azure_openai_ptu", label: "Azure OpenAI PTU", scope: "azure" },
  { value: "azure_automation_jobs", label: "Azure Automation Jobs", scope: "azure" },
  { value: "azure_resource_graph", label: "Azure Resource Graph", scope: "azure" },
  { value: "gcp_bigquery", label: "GCP BigQuery", scope: "gcp" },
  { value: "oci", label: "Oracle Cloud (OCI)", scope: "oci" },
  { value: "mongodb_atlas", label: "MongoDB Atlas", scope: "atlas" },
  { value: "github_billing", label: "GitHub Billing (Copilot)", scope: "github" },
  { value: "github_license", label: "GitHub Licenças (Copilot)", scope: "github" },
  { value: "github_token", label: "GitHub Tokens (Copilot)", scope: "github" },
  { value: "notebooklm_license", label: "NotebookLM Licenças", scope: "notebooklm" },
  { value: "notebooklm_token", label: "NotebookLM Tokens", scope: "notebooklm" },
  { value: "gemini_license", label: "Gemini Licenças", scope: "gemini" },
  { value: "gemini_token", label: "Gemini Tokens", scope: "gemini" },
  { value: "claude_license", label: "Claude Licenças (Enterprise)", scope: "claude" },
  { value: "claude_token", label: "Claude Tokens (Enterprise)", scope: "claude" },
  { value: "databricks", label: "Databricks (SQL Warehouse)", scope: "databricks" },
];

// Categorias da página de Conexões.
export type ConnectionCategory = "provedor" | "ferramenta" | "marketplace";

const CATEGORY_TABS: { value: ConnectionCategory; label: string }[] = [
  { value: "provedor", label: "Provedores Cloud" },
  { value: "ferramenta", label: "Ferramentas Cloud" },
  { value: "marketplace", label: "Marketplace" },
];

const CATEGORY_META: Record<ConnectionCategory, { icon: React.ReactNode; subtitle: string; color: string }> = {
  provedor: { icon: <Cloud sx={{ color: "#0A8FDC" }} />, subtitle: "Conexões configuradas para provedores de nuvem.", color: "#0A8FDC" },
  ferramenta: { icon: <AutoAwesome sx={{ color: "#7C3AED" }} />, subtitle: "Ferramentas de IA e colaboração (Copilot, NotebookLM, Gemini, Claude).", color: "#7C3AED" },
  marketplace: { icon: <Storefront sx={{ color: "#13AA52" }} />, subtitle: "Serviços adquiridos via marketplace (MongoDB Atlas).", color: "#13AA52" },
};

function categoryOfConnection(connection: { connector_type: string; cloud_scope?: string | null }): ConnectionCategory {
  if (connection.connector_type === "mongodb_atlas" || connection.cloud_scope === "atlas") return "marketplace";
  if (
    connection.connector_type === "azure_openai_ptu" ||
    connection.connector_type === "azure_automation_jobs" ||
    connection.connector_type === "azure_resource_graph" ||
    connection.connector_type === "databricks" ||
    connection.connector_type.startsWith("github_") ||
    connection.connector_type.startsWith("notebooklm_") ||
    connection.connector_type.startsWith("gemini_") ||
    connection.connector_type.startsWith("claude_")
  ) return "ferramenta";
  return "provedor";
}

// Sub-tipos de extração por ferramenta.
const TOOL_EXTRACTION_TYPES: Record<string, { value: string; connectorType: string; cloudName: string; emoji: string; title: string; description: string }[]> = {
  github: [
    { value: "billing", connectorType: "github_billing", cloudName: "github-billing", emoji: "🧾", title: "Billing (Copilot / Actions / Packages)", description: "Relatórios detalhados de custo — cloud_name: github-billing" },
    { value: "token", connectorType: "github_token", cloudName: "github-token", emoji: "🔑", title: "Tokens (Consumo de tokens / mês)", description: "Uso detalhado por mês (Copilot / Actions) — cloud_name: github-token" },
    { value: "license", connectorType: "github_license", cloudName: "github-license", emoji: "👥", title: "Licenças (Usuários criados)", description: "Lista de usuários e licenças ativas da Enterprise — cloud_name: github-license" },
  ],
  notebooklm: [
    { value: "token", connectorType: "notebooklm_token", cloudName: "notebooklm-token", emoji: "🔑", title: "Tokens (Consumo mensal)", description: "Relatório detalhado de consumo de tokens — cloud_name: notebooklm-token" },
    { value: "license", connectorType: "notebooklm_license", cloudName: "notebooklm-license", emoji: "👥", title: "Licenças (Usuários ativos)", description: "Colaboradores com licenças ativas — cloud_name: notebooklm-license" },
  ],
  gemini: [
    { value: "token", connectorType: "gemini_token", cloudName: "gemini-token", emoji: "🔑", title: "Tokens (Consumo mensal)", description: "Relatório detalhado de consumo de tokens — cloud_name: gemini-token" },
    { value: "license", connectorType: "gemini_license", cloudName: "gemini-license", emoji: "👥", title: "Licenças (Usuários ativos)", description: "Usuários e licenças ativas do Gemini — cloud_name: gemini-license" },
  ],
  claude: [
    { value: "token", connectorType: "claude_token", cloudName: "claude-token", emoji: "🔑", title: "Tokens (Consumo mensal)", description: "Relatório detalhado de tokens e atividades — cloud_name: claude-token" },
    { value: "license", connectorType: "claude_license", cloudName: "claude-license", emoji: "👥", title: "Licenças (Colaboradores)", description: "Colaboradores com licenças ativas — cloud_name: claude-license" },
  ],
};

const TOOL_LABELS: Record<string, string> = {
  github: "GitHub",
  notebooklm: "NotebookLM",
  gemini: "Google Gemini",
  claude: "Claude (Anthropic)",
};

const CONNECTOR_PICKER_OPTIONS: { value: string; label: string; description: string; icon: React.ReactNode; color: string; category: ConnectionCategory; tool?: string }[] = [
  // Provedores Cloud
  { value: "azure_mca_invoice", label: "Azure MCA", description: "Invoices e compromisso global", icon: <Cloud />, color: "#0078D4", category: "provedor" },
  { value: "azure_blob", label: "Azure", description: "Blob Storage", icon: <Cloud />, color: "#0078D4", category: "provedor" },
  { value: "azure_openai_ptu", label: "Azure OpenAI", description: "PTU / capacidade provisionada", icon: <SmartToy />, color: "#0078D4", category: "ferramenta" },
  { value: "azure_automation_jobs", label: "Azure Automation", description: "Jobs de Start/Stop de VMs", icon: <Cloud />, color: "#0078D4", category: "ferramenta" },
  { value: "azure_resource_graph", label: "Azure Resource Graph", description: "Inventário / recursos órfãos", icon: <Search />, color: "#0078D4", category: "ferramenta" },
  { value: "gcp_bigquery", label: "GCP", description: "BigQuery", icon: <Storage />, color: "#4285F4", category: "provedor" },
  { value: "oci", label: "OCI", description: "Oracle Cloud", icon: <Cloud />, color: "#F80000", category: "provedor" },
  // Ferramentas Cloud
  { value: "github", label: "GitHub", description: "Copilot / Tokens / Licenças", icon: <Cloud />, color: "#24292e", category: "ferramenta", tool: "github" },
  { value: "notebooklm", label: "NotebookLM", description: "Licenças / Tokens", icon: <MenuBook />, color: "#4285F4", category: "ferramenta", tool: "notebooklm" },
  { value: "gemini", label: "Gemini", description: "Licenças / Tokens", icon: <AutoAwesome />, color: "#8E24AA", category: "ferramenta", tool: "gemini" },
  { value: "claude", label: "Claude", description: "Claude.ai / Anthropic", icon: <Psychology />, color: "#D97757", category: "ferramenta", tool: "claude" },
  { value: "databricks", label: "Databricks", description: "Consumo do Workspace (SQL)", icon: <Storage />, color: "#FF3621", category: "ferramenta" },
  // Marketplace
  { value: "mongodb_atlas", label: "MongoDB", description: "Atlas", icon: <Dataset />, color: "#13AA52", category: "marketplace" },
];

const PROVIDER_LOGOS: Record<string, string> = {
  azure: "/provider-logos/azure.svg",
  gcp: "/provider-logos/gcp.svg",
  ibm: "/provider-logos/ibm.svg",
  aws: "/provider-logos/aws.svg",
  oci: "/provider-logos/oci.svg",
  atlas: "/provider-logos/mongodb.svg",
  github: "/provider-logos/github.svg",
};

const CLOUD_SCOPES = [
  { value: "azure", label: "Azure" },
  { value: "gcp", label: "GCP" },
  { value: "ibm", label: "IBM Cloud" },
  { value: "aws", label: "AWS" },
  { value: "oci", label: "Oracle Cloud (OCI)" },
  { value: "atlas", label: "MongoDB Atlas" },
  { value: "github", label: "GitHub" },
  { value: "notebooklm", label: "NotebookLM" },
  { value: "gemini", label: "Google Gemini AI" },
  { value: "claude", label: "Claude (Anthropic)" },
  { value: "databricks", label: "Databricks" },
  { value: "other", label: "Outra / Desconhecida" },
];

const emptyForm = {
  code: "", name: "", connector_type: "azure_blob", cloud_name: "", cloud_scope: "azure",
  tenant_id: "", client_id: "", client_secret: "",
  storage_account: "", container_name: "", blob_prefixes: "",
  gcp_token: "", table_name: "",
  mongo_group_id: "", mongo_contract_type: "", marketplace: false,
  oci_fingerprint: "", oci_region: "sa-saopaulo-1", oci_private_key: "", oci_genai_compartments: "",
  github_extraction_type: "token" as "billing" | "license" | "token",
  gemini_extraction_type: "token" as "license" | "token",
  admin_email: "",
  credential_profile: "",
  eventhub_namespace: "",
  eventhub_name: "",
  eventhub_consumer_group: "openfinops-azure-ptu",
  databricks_server_hostname: "",
  databricks_http_path: "",
  databricks_table_name: "system.billing.usage",
  databricks_query: "SELECT * FROM system.billing.usage",
  databricks_month_column: "usage_date",
  databricks_token: "",
  automation_subscription_ids: "",
  automation_backfill_days: 93,
  arg_subscription_ids: "",
  arg_orphan_rules: "",
  mca_auth_mode: "bearer" as "bearer" | "service_principal" | "azure_cli",
  mca_bearer_token: "",
  mca_billing_account_ids: "",
  mca_start_period: "",
  mca_end_period: "",
};

const latestMcaInvoiceMonth = () => {
  const now = new Date();
  const previous = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${previous.getFullYear()}-${String(previous.getMonth() + 1).padStart(2, "0")}`;
};


/**
 * `blob_prefixes` aceita duas formas na API: lista de caminhos (antiga) e mapa
 * `{tipo de relatório: caminho}` (nova). A tela edita no formato do mapa, uma
 * linha por tipo, porque o tipo é o que decide qual coletor usa o caminho.
 */
function prefixesToText(prefixes: unknown): string {
  if (!prefixes) return "";
  if (Array.isArray(prefixes)) return prefixes.filter(Boolean).join("\n");
  if (typeof prefixes === "object") {
    return Object.entries(prefixes as Record<string, string>)
      .map(([tipo, caminho]) => `${tipo}: ${caminho}`)
      .join("\n");
  }
  return String(prefixes);
}

/** Converte as linhas da tela no mapa que a API espera. */
function textToPrefixes(texto: string): Record<string, string> {
  const saida: Record<string, string> = {};
  for (const linha of (texto || "").split("\n")) {
    const limpa = linha.trim();
    if (!limpa) continue;
    const separador = limpa.indexOf(":");
    if (separador > 0) {
      const tipo = limpa.slice(0, separador).trim();
      const caminho = limpa.slice(separador + 1).trim();
      if (tipo && caminho) saida[tipo] = caminho;
    } else {
      // Linha sem tipo é o caminho do custo realizado — mantém compatibilidade
      // com quem já digitava só o caminho.
      saida.actual_cost = limpa;
    }
  }
  return saida;
}

export default function Connections() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [connectionCatalog, setConnectionCatalog] = useState<SourceConnection[]>([]);
  const [gridTotal, setGridTotal] = useState(0);
  const [gridLoading, setGridLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [testMsgs, setTestMsgs] = useState<Record<number, ConnectionTestMessage>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [activeCategory, setActiveCategory] = useState<"all" | ConnectionCategory>("all");
  const [activeProvider, setActiveProvider] = useState("all");
  const [search, setSearch] = useState("");
  const [tabsReady, setTabsReady] = useState(false);
  // Serviços auxiliares (Jev, ServiceNow) não são fontes de dado de nuvem, mas
  // seguem o mesmo princípio de configuração — por isso vivem aqui, e não numa
  // tela separada que ninguém encontra.
  const [activeView, setActiveView] = useState<"connections" | "integrations">("connections");
  const gridQuery = useRef<{ offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }> }>({ offset: 0, limit: 25, filters: [] });
  const gridRequestId = useRef(0);
  const [connectorPickerOpen, setConnectorPickerOpen] = useState(false);
  const [toolSubPicker, setToolSubPicker] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SourceConnection | null>(null);
  const [historyConn, setHistoryConn] = useState<SourceConnection | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [testingForm, setTestingForm] = useState(false);
  const [formTestMessage, setFormTestMessage] = useState<{ severity: "success" | "warning" | "error"; msg: string } | null>(null);
  const [connectionPendingDeletion, setConnectionPendingDeletion] = useState<SourceConnection | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteFeedback, setDeleteFeedback] = useState("");
  const [deleteError, setDeleteError] = useState("");

  const load = async () => {
    try {
      setLoading(true);
      setConnectionCatalog(await getConnections());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const categoryTabs = useMemo(() => CATEGORY_TABS.map((category) => ({
    ...category,
    count: connectionCatalog.filter((connection) => categoryOfConnection(connection) === category.value).length,
  })), [connectionCatalog]);

  // Scopes de provedor presentes em uma categoria específica (ou em todas, quando "all").
  const providerTabsFor = (category: "all" | ConnectionCategory) => Array.from(new Set(
    connectionCatalog
      .filter((connection) => {
        if (!connection.cloud_scope) return false;
        if (category === "all") return true;
        return categoryOfConnection(connection) === category;
      })
      .map((connection) => connection.cloud_scope as string),
  )).sort((left, right) => getProviderLabel(left).localeCompare(getProviderLabel(right), "pt-BR"));

  // Abas de provedor (2º nível): scopes presentes nas conexões da categoria ativa.
  const providerTabs = useMemo(() => providerTabsFor(activeCategory), [activeCategory, connectionCatalog]);

  // Inicializa somente após o catálogo carregar, selecionando a primeira
  // categoria com conexões e o primeiro provedor dentro dela (nunca cai em "Todas").
  useEffect(() => {
    if (loading || tabsReady) return;
    const firstCategory = categoryTabs.find((category) => category.count > 0);
    if (firstCategory) {
      setActiveCategory(firstCategory.value);
      const firstProvider = providerTabsFor(firstCategory.value);
      setActiveProvider(firstProvider.length ? firstProvider[0] : "all");
    } else {
      setActiveCategory("all");
      setActiveProvider("all");
    }
    setTabsReady(true);
  }, [categoryTabs, loading, tabsReady]);

  const selectCategory = (category: "all" | ConnectionCategory) => {
    setActiveCategory(category);
    if (category === "all") {
      setActiveProvider("all");
      return;
    }
    const providers = providerTabsFor(category);
    setActiveProvider(providers.length ? providers[0] : "all");
  };

  const loadGrid = async (query = gridQuery.current) => {
    const requestId = ++gridRequestId.current;
    try {
      setGridLoading(true);
      const data = await getConnectionsGrid({
        limit: query.limit,
        offset: query.offset,
        search: search || undefined,
        category: activeCategory === "all" ? undefined : activeCategory,
        provider: activeProvider === "all" ? undefined : activeProvider,
        sort_field: query.sort?.field,
        sort_direction: query.sort?.direction,
        filters: query.filters,
      });
      if (requestId !== gridRequestId.current) return;
      setConnections(data.items);
      setGridTotal(data.total);
    } catch (gridError: unknown) {
      if (requestId !== gridRequestId.current) return;
      setError(gridError instanceof Error ? gridError.message : "Falha ao carregar conexões");
    } finally {
      if (requestId === gridRequestId.current) setGridLoading(false);
    }
  };

  useEffect(() => {
    if (!tabsReady) return;
    gridQuery.current = { offset: 0, limit: gridQuery.current.limit, filters: [] };
    void loadGrid(gridQuery.current);
  }, [activeCategory, activeProvider, search, tabsReady]);

  const openNew = (connectorType = "azure_blob") => {
    setEditing(null);
    setFormTestMessage(null);
    const scope = CONNECTOR_TYPES.find((type) => type.value === connectorType)?.scope || "";
    setForm({ ...emptyForm, connector_type: connectorType, cloud_scope: scope });
    if (connectorType === "azure_mca_invoice") {
      void api.get("/mca-commitment/status").then(({ data }) => {
        setForm((current) => ({ ...current, mca_billing_account_ids: (data.billing_account_ids || []).join("\n") }));
      }).catch(() => undefined);
    }
    setDialogOpen(true);
  };

  const chooseConnector = (connectorType: string) => {
    const option = CONNECTOR_PICKER_OPTIONS.find((candidate) => candidate.value === connectorType);
    if (option?.tool) {
      setConnectorPickerOpen(false);
      setToolSubPicker(option.tool);
      return;
    }
    setConnectorPickerOpen(false);
    openNew(connectorType);
  };

  const chooseToolType = (tool: string, type: { connectorType: string; cloudName: string; title: string }) => {
    setToolSubPicker(null);
    setEditing(null);
    setFormTestMessage(null);
    const toolScope: Record<string, string> = { github: "github", notebooklm: "notebooklm", gemini: "gemini", claude: "claude" };
    const githubExtraction = type.connectorType.startsWith("github_") ? type.connectorType.replace("github_", "") : "token";
    const geminiExtraction = (type.connectorType.startsWith("notebooklm_") || type.connectorType.startsWith("gemini_")) ? type.connectorType.split("_")[1] : "token";
    setForm({
      ...emptyForm,
      name: (TOOL_LABELS[tool] || tool) + " " + type.title,
      code: type.connectorType,
      connector_type: type.connectorType,
      cloud_scope: toolScope[tool] || tool,
      cloud_name: type.cloudName,
      github_extraction_type: githubExtraction as "billing" | "license" | "token",
      gemini_extraction_type: geminiExtraction as "license" | "token",
    });
    setDialogOpen(true);
  };

  const openEdit = (conn: SourceConnection) => {
    setEditing(conn);
    setFormTestMessage(null);
    const cfg = conn.config_json as Record<string, string>;
    if (conn.connector_type === "azure_blob") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "",
        cloud_scope: conn.cloud_scope || "azure",
        tenant_id: cfg.tenant_id || "",
        client_id: cfg.client_id || "",
        client_secret: cfg.client_secret || "",
        storage_account: cfg.storage_account || "",
        container_name: cfg.container_name || "",
        blob_prefixes: prefixesToText(conn.blob_prefixes),
      });
    } else if (conn.connector_type === "azure_openai_ptu") {
      // The API deliberately omits secrets. Never repopulate the write-only field.
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "Azure-4P",
        cloud_scope: conn.cloud_scope || "azure",
        credential_profile: cfg.credential_profile || "azure-4p",
        tenant_id: cfg.tenant_id || "",
        client_id: cfg.client_id || "",
        eventhub_namespace: (cfg.eventhub as any)?.namespace || "",
        eventhub_name: (cfg.eventhub as any)?.eventhub_name || "",
        eventhub_consumer_group: (cfg.eventhub as any)?.consumer_group || "openfinops-azure-ptu",
      });
    } else if (conn.connector_type === "gcp_bigquery") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "",
        cloud_scope: conn.cloud_scope || "gcp",
        tenant_id: cfg.project_id || "",
        client_id: cfg.dataset_id || "",
        gcp_token: cfg.token_json || cfg.token_path || "",
        table_name: cfg.table_name || "",
      });
    } else if (conn.connector_type === "oci") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "",
        cloud_scope: conn.cloud_scope || "oci",
        tenant_id: cfg.tenancy_ocid || cfg.tenancy || "",
        client_id: cfg.user_ocid || cfg.user || "",
        oci_fingerprint: cfg.fingerprint || "",
        oci_region: cfg.region || "sa-saopaulo-1",
        oci_private_key: cfg.private_key || cfg.key_content || "",
        oci_genai_compartments: (Array.isArray(cfg.generative_ai_compartment_ids) ? cfg.generative_ai_compartment_ids : []).join("\n"),
        blob_prefixes: prefixesToText(conn.blob_prefixes),
      });
    } else if (conn.connector_type === "mongodb_atlas") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "",
        cloud_scope: conn.cloud_scope || "atlas",
        tenant_id: cfg.base_url || "",
        client_id: cfg.public_key || cfg.contract_id || "",
        client_secret: cfg.private_key || "",
        mongo_group_id: cfg.group_id || "",
        mongo_contract_type: cfg.contract_type || "",
        marketplace: conn.config_json?.marketplace === true,
      });

    } else if (conn.connector_type === "github_billing") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "github-billing",
        cloud_scope: conn.cloud_scope || "github",
        tenant_id: cfg.enterprise_slug || "",
        client_secret: cfg.pat_token || "",
        github_extraction_type: "billing",
      });
    } else if (conn.connector_type === "github_license") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "github-license",
        cloud_scope: conn.cloud_scope || "github",
        tenant_id: cfg.enterprise_slug || "",
        client_secret: cfg.pat_token || "",
        github_extraction_type: "license",
      });
    } else if (conn.connector_type === "github_token") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "github-token",
        cloud_scope: conn.cloud_scope || "github",
        tenant_id: cfg.enterprise_slug || "",
        client_secret: cfg.pat_token || "",
        github_extraction_type: "token",
      });
    } else if (conn.connector_type === "notebooklm_license") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "notebooklm-license",
        cloud_scope: conn.cloud_scope || "gemini",
        tenant_id: cfg.project_id || "telefonica-notebook-lm",
        client_id: cfg.project_number || "198880944469",
        client_secret: cfg.api_key || cfg.client_secret || "",
        gcp_token: typeof cfg.service_account_json === "string" ? cfg.service_account_json : (cfg.service_account_json ? JSON.stringify(cfg.service_account_json, null, 2) : ""),
        admin_email: cfg.admin_email || cfg.delegated_admin_email || "",
        gemini_extraction_type: "license",
      });
    } else if (conn.connector_type === "notebooklm_token") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "notebooklm-token",
        cloud_scope: conn.cloud_scope || "gemini",
        tenant_id: cfg.project_id || "telefonica-notebook-lm",
        client_id: cfg.project_number || "198880944469",
        client_secret: cfg.api_key || cfg.client_secret || "",
        gcp_token: typeof cfg.service_account_json === "string" ? cfg.service_account_json : (cfg.service_account_json ? JSON.stringify(cfg.service_account_json, null, 2) : ""),
        admin_email: cfg.admin_email || cfg.delegated_admin_email || "",
        gemini_extraction_type: "token",
      });
    } else if (conn.connector_type === "gemini_license" || conn.connector_type === "gemini_token") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || (conn.connector_type === "gemini_license" ? "gemini-license" : "gemini-token"),
        cloud_scope: conn.cloud_scope || "gemini",
        tenant_id: cfg.project_id || "telefonica-notebook-lm",
        client_id: cfg.project_number || "198880944469",
        client_secret: cfg.api_key || cfg.client_secret || "",
        gcp_token: typeof cfg.service_account_json === "string" ? cfg.service_account_json : (cfg.service_account_json ? JSON.stringify(cfg.service_account_json, null, 2) : ""),
        admin_email: cfg.admin_email || cfg.delegated_admin_email || "",
        gemini_extraction_type: conn.connector_type === "gemini_license" ? "license" : "token",
      });
    } else if (conn.connector_type === "claude_license" || conn.connector_type === "claude_token") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || (conn.connector_type === "claude_license" ? "claude-license" : "claude-token"),
        cloud_scope: conn.cloud_scope || "claude",
        tenant_id: cfg.org_id || "telefonica",
        client_secret: cfg.api_token || cfg.api_key || cfg.client_secret || "",
      });
    } else if (conn.connector_type === "databricks") {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "databricks",
        cloud_scope: conn.cloud_scope || "databricks",
        databricks_server_hostname: cfg.server_hostname || "",
        databricks_http_path: cfg.http_path || "",
        databricks_table_name: cfg.table_name || "system.billing.usage",
        databricks_query: cfg.query || "SELECT * FROM system.billing.usage",
        databricks_month_column: cfg.month_column || "usage_date",
        databricks_token: "",
      });
    } else if (conn.connector_type === "azure_automation_jobs") {
      const subIds = Array.isArray(cfg.subscription_ids) ? cfg.subscription_ids.join("\n") : "";
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "Azure",
        cloud_scope: conn.cloud_scope || "azure",
        tenant_id: cfg.tenant_id || "",
        client_id: cfg.client_id || "",
        client_secret: cfg.client_secret || "",
        credential_profile: cfg.credential_profile || "",
        automation_subscription_ids: subIds,
        automation_backfill_days: Number(cfg.backfill_days) || 93,
      });
    } else if (conn.connector_type === "azure_resource_graph") {
      const subIds = Array.isArray(cfg.subscription_ids) ? cfg.subscription_ids.join("\n") : "";
      const rules = (cfg.orphan_rules && typeof cfg.orphan_rules === "object")
        ? Object.entries(cfg.orphan_rules as Record<string, string>)
            .map(([rule, kql]) => `${rule}: ${kql}`)
            .join("\n")
        : "";
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "Azure",
        cloud_scope: conn.cloud_scope || "azure",
        tenant_id: cfg.tenant_id || "",
        client_id: cfg.client_id || "",
        client_secret: cfg.client_secret || "",
        credential_profile: cfg.credential_profile || "",
        arg_subscription_ids: subIds,
        arg_orphan_rules: rules,
      });
    } else {
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "",
        cloud_scope: conn.cloud_scope || "",
      });
    }
    setDialogOpen(true);
  };

  const buildConnectionBody = () => {
    const configJson: Record<string, unknown> = {};
    if (form.connector_type === "azure_blob") {
      if (form.tenant_id) configJson.tenant_id = form.tenant_id;
      if (form.client_id) configJson.client_id = form.client_id;
      if (form.client_secret) configJson.client_secret = form.client_secret;
      if (form.storage_account) configJson.storage_account = form.storage_account;
      if (form.container_name) configJson.container_name = form.container_name;
    } else if (form.connector_type === "azure_openai_ptu") {
      configJson.credential_profile = form.credential_profile || "azure-4p";
      if (form.tenant_id) configJson.tenant_id = form.tenant_id;
      if (form.client_id) configJson.client_id = form.client_id;
      // Secret is write-only: only include it when explicitly entered.
      if (form.client_secret) configJson.client_secret = form.client_secret;
      configJson.metric_interval = "PT1M";
      configJson.rollup_interval = "PT1H";
      configJson.lookback_hours = 3;
      configJson.backfill_days = 93;
      configJson.discovery_mode = "selected_accounts";
      configJson.subscription_ids = [];
      configJson.account_resource_ids = [];
      configJson.eventhub = {
        namespace: form.eventhub_namespace || "",
        eventhub_name: form.eventhub_name || "",
        consumer_group: form.eventhub_consumer_group || "openfinops-azure-ptu",
        credential_ref: "azure-4p",
      };
    } else if (form.connector_type === "gcp_bigquery") {
      if (form.tenant_id) configJson.project_id = form.tenant_id;
      if (form.client_id) configJson.dataset_id = form.client_id;
      if (form.gcp_token) configJson.token_json = form.gcp_token;
      if (form.table_name) configJson.table_name = form.table_name;
    } else if (form.connector_type === "oci") {
      if (form.tenant_id) configJson.tenancy_ocid = form.tenant_id;
      if (form.client_id) configJson.user_ocid = form.client_id;
      if (form.oci_fingerprint) configJson.fingerprint = form.oci_fingerprint;
      if (form.oci_region) configJson.region = form.oci_region;
      if (form.oci_private_key) configJson.private_key = form.oci_private_key;
      configJson.generative_ai_compartment_ids = form.oci_genai_compartments.split("\n").map(s => s.trim()).filter(Boolean);
    } else if (form.connector_type === "mongodb_atlas") {
      if (form.tenant_id) configJson.base_url = form.tenant_id;
      if (form.client_id) configJson.public_key = form.client_id;
      if (form.client_secret) configJson.private_key = form.client_secret;
      if (form.mongo_group_id) configJson.group_id = form.mongo_group_id;
      if (form.mongo_contract_type) configJson.contract_type = form.mongo_contract_type;
      if (form.marketplace) configJson.marketplace = true;
    } else if (form.connector_type === "github_billing") {
      if (form.tenant_id) configJson.enterprise_slug = form.tenant_id;
      if (form.client_secret) configJson.pat_token = form.client_secret;
    } else if (form.connector_type === "github_license") {
      if (form.tenant_id) configJson.enterprise_slug = form.tenant_id;
      if (form.client_secret) configJson.pat_token = form.client_secret;
    } else if (form.connector_type === "github_token") {
      if (form.tenant_id) configJson.enterprise_slug = form.tenant_id;
      if (form.client_secret) configJson.pat_token = form.client_secret;
    } else if (form.connector_type === "notebooklm_license" || form.connector_type === "notebooklm_token") {
      if (form.tenant_id) configJson.project_id = form.tenant_id;
      if (form.client_id) configJson.project_number = form.client_id;
      if (form.client_secret) configJson.api_key = form.client_secret;
      if (form.gcp_token) configJson.service_account_json = form.gcp_token;
      if (form.admin_email) configJson.admin_email = form.admin_email;
      configJson.ai_product = "notebooklm";
    } else if (form.connector_type === "gemini_license" || form.connector_type === "gemini_token") {
      if (form.tenant_id) configJson.project_id = form.tenant_id;
      if (form.client_id) configJson.project_number = form.client_id;
      if (form.client_secret) configJson.api_key = form.client_secret;
      if (form.gcp_token) configJson.service_account_json = form.gcp_token;
      if (form.admin_email) configJson.admin_email = form.admin_email;
      configJson.ai_product = "gemini";
    } else if (form.connector_type === "claude_license" || form.connector_type === "claude_token") {
      if (form.tenant_id) configJson.org_id = form.tenant_id;
      if (form.client_secret) configJson.api_token = form.client_secret;
      configJson.ai_product = "claude_enterprise";
    } else if (form.connector_type === "databricks") {
      if (form.databricks_server_hostname) configJson.server_hostname = form.databricks_server_hostname;
      if (form.databricks_http_path) configJson.http_path = form.databricks_http_path;
      if (form.databricks_table_name) configJson.table_name = form.databricks_table_name;
      if (form.databricks_query) configJson.query = form.databricks_query;
      if (form.databricks_month_column) configJson.month_column = form.databricks_month_column;
      // Token é write-only e opcional: por padrão o runtime lê de .env-databriks (DATABRICKS_TOKEN).
      if (form.databricks_token) configJson.token = form.databricks_token;
    } else if (form.connector_type === "azure_automation_jobs") {
      if (form.credential_profile) configJson.credential_profile = form.credential_profile;
      if (form.tenant_id) configJson.tenant_id = form.tenant_id;
      if (form.client_id) configJson.client_id = form.client_id;
      if (form.client_secret) configJson.client_secret = form.client_secret;
      configJson.subscription_ids = form.automation_subscription_ids
        .split("\n").map(s => s.trim()).filter(Boolean);
      configJson.backfill_days = Number(form.automation_backfill_days) || 93;
    } else if (form.connector_type === "azure_resource_graph") {
      if (form.credential_profile) configJson.credential_profile = form.credential_profile;
      if (form.tenant_id) configJson.tenant_id = form.tenant_id;
      if (form.client_id) configJson.client_id = form.client_id;
      if (form.client_secret) configJson.client_secret = form.client_secret;
      configJson.subscription_ids = form.arg_subscription_ids
        .split("\n").map(s => s.trim()).filter(Boolean);
      // Regras de órfãos opcionais: "regra: KQL" por linha → override do padrão.
      const orphanRules: Record<string, string> = {};
      for (const line of form.arg_orphan_rules.split("\n")) {
        const idx = line.indexOf(":");
        if (idx > 0) {
          const rule = line.slice(0, idx).trim();
          const kql = line.slice(idx + 1).trim();
          if (rule && kql) orphanRules[rule] = kql;
        }
      }
      if (Object.keys(orphanRules).length) configJson.orphan_rules = orphanRules;
    }

    return {
      code: form.code,
      name: form.name,
      connector_type: form.connector_type,
      cloud_name: form.cloud_name || null,
      cloud_scope: form.cloud_scope === "other" ? null : (form.cloud_scope || null),
      config_json: configJson,
      blob_prefixes: textToPrefixes(form.blob_prefixes),
    };
  };

  const save = async () => {
    try {
      if (form.connector_type === "azure_mca_invoice") {
        const billingAccountIds = form.mca_billing_account_ids.split(/[\n,;]/).map((value) => value.trim()).filter(Boolean);
        await api.put("/mca-commitment/credentials", {
          auth_mode: form.mca_auth_mode,
          billing_account_ids: billingAccountIds,
          ...(form.mca_auth_mode === "bearer"
            ? { bearer_token: form.mca_bearer_token || undefined }
            : form.mca_auth_mode === "service_principal"
              ? { tenant_id: form.tenant_id, client_id: form.client_id, client_secret: form.client_secret || undefined }
              : { tenant_id: form.tenant_id || "9b03d892-fe8c-4e05-80f1-32e928a16ba2" }),
        });
        setDialogOpen(false);
        void load();
        void loadGrid();
        setFormTestMessage({ severity: "success", msg: "Fonte MCA configurada." });
        return;
      }
      if (form.connector_type === "databricks" && form.databricks_server_hostname.trim().toLowerCase().startsWith("dapi")) {
        setError("O token foi preenchido no campo Server Hostname. Server Hostname é o endereço do workspace (ex: adb-7405612109211710.10.azuredatabricks.net). O token vai em .env-databriks (DATABRICKS_TOKEN) ou no campo Access Token.");
        return;
      }
      const body = buildConnectionBody();
      if (editing) await updateConnection(editing.id, body);
      else await createConnection(body);
      setDialogOpen(false);
      void load();
      void loadGrid();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to save");
    }
  };

  const testFormConnection = async () => {
    setTestingForm(true);
    setFormTestMessage(null);
    try {
      const result = await testConnectionConfig(buildConnectionBody());
      const isOk = result.status === "OK";
      const isPending = result.status === "PENDING";
      setFormTestMessage({
        severity: isOk ? "success" : isPending ? "warning" : "error",
        msg: isOk ? "Conexão validada: " + result.message : isPending ? "Aguardando configuração: " + result.message : "Falha ao testar: " + result.message,
      });
    } catch (testError: unknown) {
      const message = testError instanceof Error ? testError.message : "Não foi possível testar a conexão";
      setFormTestMessage({ severity: "error", msg: "Falha ao testar: " + message });
    } finally {
      setTestingForm(false);
    }
  };

  const remove = async () => {
    if (!connectionPendingDeletion) return;
    try {
      setDeleting(true);
      setDeleteError("");
      await deleteConnection(connectionPendingDeletion.id);
      setConnectionPendingDeletion(null);
      setDeleteError("");
      setDeleteFeedback(`Conexão "${connectionPendingDeletion.name}" excluída com sucesso. Tabela Bronze removida.`);
      await load();
      await loadGrid();
    } catch (e: unknown) {
      setDeleteError(formatApiError(e, "Falha ao excluir conexão"));
    } finally {
      setDeleting(false);
    }
  };

  const runTest = async (connection: SourceConnection) => {
    setTestingId(connection.id);
    try {
      const result = await testConnection(connection.id);
      const isOk = result.status === "OK";
      const isPending = result.status === "PENDING";
      setTestMsgs((previous) => ({
        ...previous,
        [connection.id]: {
          severity: isOk ? "success" : isPending ? "warning" : "error",
          msg: isOk ? `OK: ${result.message}` : isPending ? `Aguardando: ${result.message}` : `Erro: ${result.message}`,
        },
      }));
      setConnections((previous) => previous.map((item) => item.id === connection.id ? {
        ...item,
        last_test_status: result.status,
        last_test_message: result.message,
        last_tested_at: new Date().toISOString(),
      } : item));
    } catch (testError: unknown) {
      const message = testError instanceof Error ? testError.message : "Não foi possível testar a conexão";
      setTestMsgs((previous) => ({ ...previous, [connection.id]: { severity: "error", msg: `Erro: ${message}` } }));
    } finally {
      setTestingId(null);
    }
  };

  const exportConnections = () => {
    const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const csv = [
      ["Conexão", "Código", "Provedor", "Tipo", "Atualizado em", "Último teste"],
      ...connections.map((connection) => [
        connection.name,
        connection.code,
        getProviderLabel(connection.cloud_scope),
        connection.connector_type,
        connection.updated_at,
        connection.last_test_status || "",
      ]),
    ].map((row) => row.map(escape).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "conexoes.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  const activeCategoryMeta = activeCategory === "all" ? null : CATEGORY_META[activeCategory];
  const activeProviderLogo = activeProvider === "all" ? null : PROVIDER_LOGOS[activeProvider];
  const headerTitle = activeCategory === "all"
    ? (activeProvider === "all" ? "Todas as fontes" : getProviderLabel(activeProvider))
    : (activeProvider === "all" ? CATEGORY_TABS.find((category) => category.value === activeCategory)?.label : getProviderLabel(activeProvider));
  const headerSubtitle = activeCategory === "all"
    ? (activeProvider === "all" ? "Conexões configuradas para ingestão de dados." : "Conexões configuradas para este provedor.")
    : (activeProvider === "all" ? activeCategoryMeta?.subtitle : "Conexões configuradas para este provedor.");

  if (loading || !tabsReady) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h4">Credenciais</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Integre as fontes que alimentam os dados de custo da plataforma.
          </Typography>
        </Box>
        </Box>

        <Tabs
          value={activeView}
          onChange={(_, value: "connections" | "integrations") => setActiveView(value)}
          aria-label="Conexões ou serviços auxiliares"
          sx={{ mb: 1.5, minHeight: 44 }}
        >
          <Tab value="connections" label="Conexões" />
          <Tab value="integrations" label="Serviços auxiliares" />
        </Tabs>

        {activeView === "integrations" ? (
          <Box sx={{ py: 3 }}>
            <IntegrationsPanel />
          </Box>
        ) : (
          <>
        <Tabs
          value={activeCategory}
          onChange={(_, value: string) => selectCategory(value as "all" | ConnectionCategory)}
          aria-label="Filtrar conexões por categoria"
          sx={{ mb: 1.5, minHeight: 44 }}
        >
          {categoryTabs.map((category) => (
            <Tab
              key={category.value}
              value={category.value}
              label={category.label + " (" + category.count + ")"}
              sx={{ minHeight: 44, fontWeight: 600 }}
            />
          ))}
          <Tab value="all" label={"Todas (" + connectionCatalog.length + ")"} sx={{ minHeight: 44, fontWeight: 600 }} />
        </Tabs>
        {providerTabs.length > 0 && (
          <Box
            sx={{
              bgcolor: (activeCategoryMeta?.color || "#94a3b8") + "14",
              border: "1px solid",
              borderColor: (activeCategoryMeta?.color || "#94a3b8") + "33",
              borderRadius: 1.5,
              px: 1.5,
              mb: 2,
            }}
          >
            <Tabs
              value={activeProvider}
              onChange={(_, value: string) => setActiveProvider(value)}
              aria-label="Filtrar conexões por provedor"
              sx={{
                minHeight: 42,
                "& .MuiTab-root": { minHeight: 42, py: 0.5, fontWeight: 600, textTransform: "none" },
                "& .Mui-selected": { color: activeCategoryMeta?.color || "primary.main" },
                "& .MuiTabs-indicator": { bgcolor: activeCategoryMeta?.color || "primary.main", height: 3, borderRadius: "3px 3px 0 0" },
              }}
            >
              {providerTabs.map((provider) => (
                <Tab
                  key={provider}
                  value={provider}
                  label={getProviderLabel(provider) + " (" + connectionCatalog.filter((connection) => connection.cloud_scope === provider && (activeCategory === "all" || categoryOfConnection(connection) === activeCategory)).length + ")"}
                  sx={{ minHeight: 42, py: 0.5 }}
                />
              ))}
              <Tab value="all" label="Todos os provedores" sx={{ minHeight: 42, py: 0.5 }} />
            </Tabs>
          </Box>
        )}
          </>
        )}
      </Box>

      <Box sx={{ p: 3 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

        <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2, minHeight: 52, mb: 2 }}>
            <Stack direction="row" alignItems="center">
              {activeProviderLogo ? (
                <Box component="img" src={activeProviderLogo} alt={getProviderLabel(activeProvider)} sx={{ width: 42, height: 42, mr: 1.25, objectFit: "contain" }} />
              ) : activeCategoryMeta ? (
                <Box sx={{ display: "flex", fontSize: 38, mr: 1.25 }}>{activeCategoryMeta.icon}</Box>
              ) : null}
              <Box>
                <Typography variant="h6">{headerTitle}</Typography>
                <Typography variant="body2" color="text.secondary">{headerSubtitle}</Typography>
              </Box>
            </Stack>
            <Stack direction="row" spacing={1} flexShrink={0}>
              <Button variant="outlined" size="small" startIcon={<Download />} onClick={exportConnections} disabled={connections.length === 0}>
                Exportar
              </Button>
              <Button variant="contained" size="small" startIcon={<Add />} onClick={() => setConnectorPickerOpen(true)} sx={{ px: 2 }}>
                Credenciais
              </Button>
            </Stack>
          </Box>
        <TextField
          fullWidth
          size="small"
          label="Buscar fontes de dados"
          placeholder="Nome, código, provedor ou origem"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          slotProps={{ input: { startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment> } }}
          sx={{ mb: 2, maxWidth: 520 }}
        />
        {!gridLoading && connections.length === 0 ? (
          <Box sx={{ py: 7 }}>
            <EmptyState message={search ? "Nenhuma fonte corresponde à busca." : "Nenhuma conexão cadastrada neste grupo."} />
          </Box>
        ) : (
          <ConnectionsDataGrid
            connections={connections}
            total={gridTotal}
            loading={gridLoading}
            testingId={testingId}
            testMessages={testMsgs}
            onEdit={openEdit}
            onHistory={(conn) => setHistoryConn(conn)}
            onTest={(connection) => void runTest(connection)}
            onDelete={setConnectionPendingDeletion}
            onQuery={(query) => {
              gridQuery.current = { ...gridQuery.current, ...query };
              void loadGrid(gridQuery.current);
            }}
          />
        )}
        </Paper>
      </Box>

      <Dialog open={connectorPickerOpen} onClose={() => setConnectorPickerOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ pb: 0 }}>Adicionar fonte de dados</DialogTitle>
        <DialogContent sx={{ pt: "16px !important", pb: 4 }}>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            Selecione o provedor ou formato de origem para configurar uma nova conexão.
          </Typography>
          <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1.5 }}>Fontes de entrada</Typography>
          {CATEGORY_TABS.map((category) => {
            const options = CONNECTOR_PICKER_OPTIONS.filter((option) => option.category === category.value);
            return (
              <Box key={category.value} sx={{ mb: 2.5 }}>
                <Typography variant="subtitle2" fontWeight={700} color="text.secondary" sx={{ mb: 1 }}>
                  {category.label}
                </Typography>
                <Stack direction="row" gap={1.5} flexWrap="wrap">
                  {options.map((option) => (
                    <Button
                      key={option.value}
                      variant="outlined"
                      color="inherit"
                      onClick={() => chooseConnector(option.value)}
                      sx={{
                        width: 156,
                        height: 148,
                        flexDirection: "column",
                        gap: 1,
                        borderColor: "divider",
                        color: "text.primary",
                        "&:hover": { borderColor: "primary.main", bgcolor: "rgba(10, 143, 220, 0.06)" },
                      }}
                    >
                      <Box sx={{ color: option.color, display: "flex", fontSize: 38 }}>{option.icon}</Box>
                      <Typography fontWeight={700}>{option.label}</Typography>
                      <Typography variant="caption" color="text.secondary">{option.description}</Typography>
                    </Button>
                  ))}
                </Stack>
              </Box>
            );
          })}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setConnectorPickerOpen(false)}>Cancelar</Button>
        </DialogActions>
        </Dialog>

      {/* Sub-seletor de tipo de extração por ferramenta (Copilot / NotebookLM / Gemini / Claude) */}
      <Dialog open={toolSubPicker !== null} onClose={() => setToolSubPicker(null)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ pb: 0, display: "flex", alignItems: "center", gap: 1 }}>
          <AutoAwesome sx={{ color: "#7C3AED" }} />
          {toolSubPicker ? TOOL_LABELS[toolSubPicker] || toolSubPicker : ""} — Selecione a fonte de dados
        </DialogTitle>
        <DialogContent sx={{ pt: "16px !important", pb: 4 }}>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            Selecione qual tipo de dado da ferramenta você deseja extrair:
          </Typography>
          <Stack direction="column" gap={1.5}>
            {toolSubPicker && TOOL_EXTRACTION_TYPES[toolSubPicker]?.map((type) => (
              <Button
                key={type.value}
                variant="outlined" color="inherit" fullWidth
                onClick={() => chooseToolType(toolSubPicker, type)}
                sx={{ justifyContent: "flex-start", gap: 2, p: 2, borderColor: "divider",
                  "&:hover": { borderColor: "primary.main", bgcolor: "rgba(10, 143, 220, 0.06)" } }}
              >
                <Typography fontSize={24}>{type.emoji}</Typography>
                <Box textAlign="left">
                  <Typography fontWeight={700}>{type.title}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {type.description}
                  </Typography>
                </Box>
              </Button>
            ))}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setToolSubPicker(null)}>Cancelar</Button>
        </DialogActions>
      </Dialog>



      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? "Editar Conexao" : "Nova Conexao"}</DialogTitle>
        <DialogContent sx={{
          "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
          "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
        }}>
          {formTestMessage && <Alert severity={formTestMessage.severity} sx={{ mb: 2 }}>{formTestMessage.msg}</Alert>}
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <FormControl fullWidth>
              <InputLabel>Connector Type</InputLabel>
              <Select value={form.connector_type} label="Connector Type"
                onChange={(e) => {
                  const type = e.target.value;
                  const scope = CONNECTOR_TYPES.find(t => t.value === type)?.scope || "";
                  setForm({ ...form, connector_type: type, cloud_scope: scope });
                }}>
                {CONNECTOR_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField label="Code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
            <TextField label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <TextField label="Cloud Name" value={form.cloud_name} onChange={(e) => setForm({ ...form, cloud_name: e.target.value })} />
            <FormControl fullWidth>
              <InputLabel>Provedor (escopo de regras)</InputLabel>
              <Select value={form.cloud_scope || "other"} label="Provedor (escopo de regras)"
                onChange={(e) => setForm({ ...form, cloud_scope: e.target.value })}>
                {CLOUD_SCOPES.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </Select>
            </FormControl>

            {form.connector_type === "azure_mca_invoice" && (
              <>
                <Alert severity="info">Consulta valores mensais consolidados ligados a invoices. Cada Billing Account fica identificado e é particionado sem sobrescrever os demais. A credencial é write-only.</Alert>
                <TextField label="Billing Account IDs" multiline minRows={3} value={form.mca_billing_account_ids}
                  onChange={(e) => setForm({ ...form, mca_billing_account_ids: e.target.value })}
                  helperText="Um ID por linha. Ex.: accountGuid:tenantGuid_2019-05-31. Os profiles são descobertos dinamicamente em cada conta." />
                <FormControl fullWidth>
                  <InputLabel>Método de autenticação</InputLabel>
                  <Select value={form.mca_auth_mode} label="Método de autenticação"
                    onChange={(e) => setForm({ ...form, mca_auth_mode: e.target.value as "bearer" | "service_principal" | "azure_cli" })}>
                    <MenuItem value="bearer">Bearer token temporário</MenuItem>
                    <MenuItem value="service_principal">Service Principal</MenuItem>
                    <MenuItem value="azure_cli">Azure CLI com renovação automática</MenuItem>
                  </Select>
                </FormControl>
                {form.mca_auth_mode === "bearer" ? (
                  <TextField label="Bearer token" type="password" multiline minRows={2} value={form.mca_bearer_token}
                    onChange={(e) => setForm({ ...form, mca_bearer_token: e.target.value })}
                    helperText="Cole o token de acesso ao Azure Resource Manager." />
                ) : form.mca_auth_mode === "service_principal" ? (
                  <>
                    <TextField label="Tenant ID" value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                    <TextField label="Client ID" value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                    <TextField label="Client Secret" type="password" value={form.client_secret} onChange={(e) => setForm({ ...form, client_secret: e.target.value })} />
                  </>
                ) : (
                  <>
                    <TextField label="Tenant ID" value={form.tenant_id || "9b03d892-fe8c-4e05-80f1-32e928a16ba2"}
                      onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                    <Alert severity="info">Usa a sessão Azure CLI deste servidor e renova o access token automaticamente. Faça login com az login no mesmo usuário que executa a API.</Alert>
                  </>
                )}
                <Typography variant="body2" color="text.secondary">Fonte contratos-mca · Cost Management Query agrupada por invoice · Bronze raw_azure_commitment_br · desde 2025-04.</Typography>
              </>
            )}

            {form.connector_type === "azure_openai_ptu" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>1. Identidade</Typography>
                <Alert severity="info">A coleta usa somente recursos e métricas já existentes. Nenhum recurso Azure será criado. O secret nunca é devolvido pela API.</Alert>
                <TextField label="Perfil de credencial" value={form.credential_profile || "azure-4p"}
                  onChange={(e) => setForm({ ...form, credential_profile: e.target.value })}
                  helperText="Perfil runtime (recomendado: azure-4p)" />
                <TextField label="Tenant ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                <TextField label="Client ID" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Client Secret (somente para credencial nova)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Deixe vazio para manter o segredo runtime existente." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>2. Escopo Azure</Typography>
                <Alert severity="warning">Use Testar conexão / Scan para descobrir assinaturas, contas OpenAI e deployments PTU sem persistir métricas.</Alert>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>3. Coleta</Typography>
                <Typography variant="body2" color="text.secondary">PT1M → persistência horária UTC, atraso 10 min, lookback 3h, backfill 93 dias. Exibição America/Sao_Paulo.</Typography>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>4. Mapeamento de projeto</Typography>
                <Typography variant="body2" color="text.secondary">Após salvar, cadastre projetos em Azure PTU (reservation/ondemand, global/regional, vigência).</Typography>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>5. Telemetria de subprojetos (Event Hub)</Typography>
                <TextField label="Event Hub namespace" value={form.eventhub_namespace}
                  onChange={(e) => setForm({ ...form, eventhub_namespace: e.target.value })} />
                <TextField label="Event Hub name" value={form.eventhub_name}
                  onChange={(e) => setForm({ ...form, eventhub_name: e.target.value })} />
                <TextField label="Consumer group" value={form.eventhub_consumer_group}
                  onChange={(e) => setForm({ ...form, eventhub_consumer_group: e.target.value })}
                  helperText="Padrão: openfinops-azure-ptu. Teste de partições via Testar conexão." />
              </>
            )}

            {form.connector_type === "azure_automation_jobs" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>1. Identidade</Typography>
                <Alert severity="info">A coleta lê jobs e schedules das Automation Accounts via ARM API. Nenhum recurso é criado. O secret nunca é devolvido pela API.</Alert>
                <TextField label="Perfil de credencial" value={form.credential_profile || ""}
                  onChange={(e) => setForm({ ...form, credential_profile: e.target.value })}
                  helperText="Ex: azure-4p ou azure-ti (usa .env). Vazio = usar campos abaixo." />
                <TextField label="Tenant ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                <TextField label="Client ID" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Client Secret (somente para credencial nova)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Deixe vazio para manter o segredo runtime existente." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>2. Escopo Azure</Typography>
                <TextField label="Subscription IDs (um por linha)" multiline rows={3}
                  value={form.automation_subscription_ids}
                  onChange={(e) => setForm({ ...form, automation_subscription_ids: e.target.value })}
                  helperText="Vazio = todas as subscriptions acessíveis pela credencial." />
                <TextField label="Backfill (dias)" type="number" value={form.automation_backfill_days}
                  onChange={(e) => setForm({ ...form, automation_backfill_days: Number(e.target.value) || 93 })}
                  helperText="Quantos dias de histórico de jobs coletar (padrão 93)." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>3. Coleta</Typography>
                <Typography variant="body2" color="text.secondary">
                  Scan descobre as Automation Accounts; a extração coleta jobs (últimos N dias) e
                  schedules atuais, persiste na Bronze e permite reprocessar para a Gold
                  (MODELO AUTOMATION / MODELO AUTOMATION SCHEDULE).
                </Typography>
              </>
            )}

            {form.connector_type === "azure_resource_graph" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>1. Identidade</Typography>
                <Alert severity="info">A coleta consulta o Azure Resource Graph (inventário + recursos órfãos). Nenhum recurso é criado. O secret nunca é devolvido pela API.</Alert>
                <TextField label="Perfil de credencial" value={form.credential_profile || ""}
                  onChange={(e) => setForm({ ...form, credential_profile: e.target.value })}
                  helperText="Ex: azure-4p ou azure-ti (usa .env). Vazio = usar campos abaixo." />
                <TextField label="Tenant ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                <TextField label="Client ID" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Client Secret (somente para credencial nova)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Deixe vazio para manter o segredo runtime existente." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>2. Escopo Azure</Typography>
                <TextField label="Subscription IDs (um por linha)" multiline rows={3}
                  value={form.arg_subscription_ids}
                  onChange={(e) => setForm({ ...form, arg_subscription_ids: e.target.value })}
                  helperText="Vazio = todas as subscriptions acessíveis pela credencial. Recomendado preencher (limite de 10k linhas por query do ARG)." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>3. Regras de órfãos (opcional)</Typography>
                <TextField label="Regras KQL customizadas (regra: KQL por linha)" multiline rows={4}
                  value={form.arg_orphan_rules}
                  onChange={(e) => setForm({ ...form, arg_orphan_rules: e.target.value })}
                  helperText="Override das regras padrão (disk_unattached, vm_deallocated, public_ip_idle, snapshot_orphan, lb_idle). Ex: vm_deallocated: resources | where ..." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>4. Coleta</Typography>
                <Typography variant="body2" color="text.secondary">
                  Scan lista as subscriptions acessíveis; a extração coleta o inventário completo e
                  os órfãos por snapshot diário, persiste na Bronze e permite reprocessar para a Gold
                  (MODELO INVENTÁRIO CLOUD / MODELO RECURSOS ÓRFÃOS). Governança → Recursos Órfãos.
                </Typography>
              </>
            )}

            {form.connector_type === "azure_blob" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>Azure Credentials</Typography>
                <TextField label="Tenant ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Directory (tenant) ID do Azure AD" />
                <TextField label="Client ID" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Application (client) ID do App Registration" />
                <TextField label="Client Secret" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Secret value do App Registration" />
                <TextField label="Storage Account" value={form.storage_account}
                  onChange={(e) => setForm({ ...form, storage_account: e.target.value })} />
                <TextField label="Container Name" value={form.container_name}
                  onChange={(e) => setForm({ ...form, container_name: e.target.value })} />
                <TextField label="Relatórios do Blob (tipo: caminho, um por linha)" multiline rows={4}
                  value={form.blob_prefixes}
                  onChange={(e) => setForm({ ...form, blob_prefixes: e.target.value })}
                  helperText={
                    "O tipo decide qual coletor usa o caminho — não é chumbado no código.\n"
                    + "Ex.:\nactual_cost: reports/Report4P-actual-cost/\n"
                    + "amortized_cost: reports/Report4P-amortized-cost/"
                  } />
              </>
            )}

            {form.connector_type === "gcp_bigquery" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>GCP BigQuery Config</Typography>
                <TextField label="Project ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="ID do projeto GCP (ex: billing-344820)" />
                <TextField label="Dataset ID" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Dataset do BigQuery (ex: billingVivo)" />
                <TextField label="Table Name" value={form.table_name}
                  onChange={(e) => setForm({ ...form, table_name: e.target.value })}
                  helperText="Nome completo da tabela (ex: gcp_billing_export_resource_v1_...)" />
                <TextField label="Service Account JSON Token" multiline rows={6}
                  value={form.gcp_token}
                  onChange={(e) => setForm({ ...form, gcp_token: e.target.value })}
                  helperText="Cole o conteudo completo do JSON da service account" />
              </>
            )}

            {form.connector_type === "oci" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>Oracle Cloud (OCI) Config</Typography>
                <TextField label="Tenancy OCID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="OCID da Tenancy (ex: ocid1.tenancy.oc1..aaaa...)" />
                <TextField label="User OCID" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="OCID do usuário OCI (ex: ocid1.user.oc1..aaaa...)" />
                <TextField label="Fingerprint" value={form.oci_fingerprint}
                  onChange={(e) => setForm({ ...form, oci_fingerprint: e.target.value })}
                  helperText="Fingerprint da API Key cadastrada na OCI" />
                <TextField label="Região" value={form.oci_region}
                  onChange={(e) => setForm({ ...form, oci_region: e.target.value })}
                  helperText="Região principal da OCI (ex: sa-saopaulo-1, sa-vinhedo-1, us-ashburn-1)" />
                <TextField label="Private Key PEM" multiline rows={5}
                  value={form.oci_private_key}
                  onChange={(e) => setForm({ ...form, oci_private_key: e.target.value })}
                  helperText="Cole o conteudo completo da chave privada (.pem)" />
                <TextField label="Relatórios do Blob (tipo: caminho, um por linha)" multiline rows={2}
                  value={form.blob_prefixes}
                  onChange={(e) => setForm({ ...form, blob_prefixes: e.target.value })}
                  helperText="Padrão: reports/cost-csv (relatórios de custo financeiro)" />
                <TextField label="Compartments OCI Generative AI / GPU (um OCID por linha)" multiline rows={3}
                  value={form.oci_genai_compartments}
                  onChange={(e) => setForm({ ...form, oci_genai_compartments: e.target.value })}
                  helperText="Escopo da fonte OCI Generative AI & GPU Monitoring; necessário para coletar inventário, tokens e tendências." />
                <Alert severity="info" sx={{ mt: 1, fontSize: "0.8rem" }}>
                  <strong>Consolidação Diária OCI:</strong> Os dados são automaticamente agrupados por dia, recurso e tags durante a ingestão para otimização de performance, preservando 100% dos valores financeiros em R$ e dimensões.
                </Alert>
              </>
            )}

            {form.connector_type === "mongodb_atlas" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>MongoDB Atlas Config</Typography>
                <TextField label="Base URL" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Ex: https://cloud.mongodb.com/api/atlas/v1.0" />
                <TextField label="Public Key" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Private Key" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })} />
                <TextField label="Group ID" value={form.mongo_group_id}
                  onChange={(e) => setForm({ ...form, mongo_group_id: e.target.value })} />
                <TextField label="Contract Type" value={form.mongo_contract_type}
                  onChange={(e) => setForm({ ...form, mongo_contract_type: e.target.value })}
                  helperText="pro ou enterprise" />
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={form.marketplace}
                      onChange={(e) => setForm({ ...form, marketplace: e.target.checked })}
                    />
                  }
                  label="Marketplace"
                />
              </>
            )}

            {(form.connector_type === "github_billing" || form.connector_type === "github_license" || form.connector_type === "github_token") && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "primary.main" }}>Configuração GitHub Enterprise</Typography>
                <FormControl fullWidth>
                  <InputLabel>Tipo de dado</InputLabel>
                  <Select
                    value={form.github_extraction_type || (form.connector_type === "github_billing" ? "billing" : form.connector_type === "github_license" ? "license" : "token")}
                    label="Tipo de dado"
                    onChange={(e) => {
                      const t = e.target.value as "billing" | "license" | "token";
                      const connectorType = t === "billing" ? "github_billing" : t === "license" ? "github_license" : "github_token";
                      const cloudName = t === "billing" ? "github-billing" : t === "license" ? "github-license" : "github-token";
                      setForm({ ...form, github_extraction_type: t, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="billing">🧾 Billing (github-billing) — Relatórios de custo (Copilot / Actions / Packages)</MenuItem>
                    <MenuItem value="token">🔑 Tokens (github-token) — Consumo de tokens/mês</MenuItem>
                    <MenuItem value="license">👥 Licenças (github-license) — Usuários criados</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Enterprise Slug" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Slug da Enterprise no GitHub (ex: tefbr)" />
                <TextField label="Personal Access Token (PAT)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Token com escopo manage_billing:enterprise" />
              </>
            )}

            {(form.connector_type === "notebooklm_license" || form.connector_type === "notebooklm_token") && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "secondary.main" }}>Configuração Google Gemini (NotebookLM)</Typography>
                <FormControl fullWidth>
                  <InputLabel>Tipo de dado</InputLabel>
                  <Select
                    value={form.gemini_extraction_type || (form.connector_type === "notebooklm_license" ? "license" : "token")}
                    label="Tipo de dado"
                    onChange={(e) => {
                      const t = e.target.value as "license" | "token";
                      const connectorType = t === "license" ? "notebooklm_license" : "notebooklm_token";
                      const cloudName = t === "license" ? "notebooklm-license" : "notebooklm-token";
                      setForm({ ...form, gemini_extraction_type: t, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="token">🔑 Tokens (notebooklm-token) — Consumo de tokens/mês</MenuItem>
                    <MenuItem value="license">👥 Licenças (notebooklm-license) — Usuários com licenças ativas</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Google Cloud Project ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Project ID no GCP (ex: telefonica-notebook-lm)" />
                <TextField label="Project Number" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Project Number no GCP (ex: 198880944469)" />
                <TextField label="E-mail admin do Workspace" value={form.admin_email}
                  onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
                  helperText="Super admin para domain-wide delegation (ex: admin@empresa.com)" />
                <TextField label="Service account JSON" value={form.gcp_token} multiline minRows={4}
                  onChange={(e) => setForm({ ...form, gcp_token: e.target.value })}
                  helperText="Cole o JSON completo da service account (type=service_account). Chave AIza não lista usuários." />
                <TextField label="API Key (opcional / legado)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Não substitui a service account para licenças e Reports API" />
              </>
            )}

            {(form.connector_type === "gemini_license" || form.connector_type === "gemini_token") && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "secondary.main" }}>Configuração Google Gemini</Typography>
                <FormControl fullWidth>
                  <InputLabel>Tipo de dado</InputLabel>
                  <Select
                    value={form.gemini_extraction_type || (form.connector_type === "gemini_license" ? "license" : "token")}
                    label="Tipo de dado"
                    onChange={(e) => {
                      const t = e.target.value as "license" | "token";
                      const connectorType = t === "license" ? "gemini_license" : "gemini_token";
                      const cloudName = t === "license" ? "gemini-license" : "gemini-token";
                      setForm({ ...form, gemini_extraction_type: t, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="token">🔑 Tokens (gemini-token) — Consumo de tokens/mês</MenuItem>
                    <MenuItem value="license">👥 Licenças (gemini-license) — Usuários com licenças ativas</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Google Cloud Project ID" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Project ID no GCP (ex: telefonica-notebook-lm)" />
                <TextField label="Project Number" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Project Number no GCP (ex: 198880944469)" />
                <TextField label="E-mail admin do Workspace" value={form.admin_email}
                  onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
                  helperText="Super admin para domain-wide delegation (ex: admin@empresa.com)" />
                <TextField label="Service account JSON" value={form.gcp_token} multiline minRows={4}
                  onChange={(e) => setForm({ ...form, gcp_token: e.target.value })}
                  helperText="Cole o JSON completo da service account (type=service_account). Chave AIza não lista usuários." />
                <TextField label="API Key (opcional / legado)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Não substitui a service account para licenças e Reports API" />
              </>
            )}

            {(form.connector_type === "claude_license" || form.connector_type === "claude_token") && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "#D97757" }}>Configuração Claude Enterprise (Anthropic)</Typography>
                <FormControl fullWidth>
                  <InputLabel>Tipo de dado</InputLabel>
                  <Select
                    value={form.connector_type === "claude_license" ? "license" : "token"}
                    label="Tipo de dado"
                    onChange={(e) => {
                      const t = e.target.value as "license" | "token";
                      const connectorType = t === "license" ? "claude_license" : "claude_token";
                      const cloudName = t === "license" ? "claude-license" : "claude-token";
                      setForm({ ...form, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="token">🔑 Tokens (claude-token) — Consumo de tokens / atividades por mês</MenuItem>
                    <MenuItem value="license">👥 Licenças (claude-license) — Colaboradores com licenças ativas</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Organização / Tenant" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Identificador da organização (ex: telefonica)" />
                <TextField label="API Token / Admin API Key" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Admin API Key com read:members (licenças) e api:admin (consumo/tokens). Sem api:admin a licença funciona e o token falha. O arquivo .env-cloude local não alimenta a conexão — cole aqui e salve." />
              </>
            )}

            {form.connector_type === "databricks" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "#FF3621" }}>Configuração Databricks (SQL Warehouse)</Typography>
                <TextField label="Server Hostname" value={form.databricks_server_hostname}
                  onChange={(e) => setForm({ ...form, databricks_server_hostname: e.target.value })}
                  helperText="Endereço do workspace (NÃO é o token). Ex: adb-7405612109211710.10.azuredatabricks.net" />
                <TextField label="HTTP Path" value={form.databricks_http_path}
                  onChange={(e) => setForm({ ...form, databricks_http_path: e.target.value })}
                  helperText="Ex: /sql/1.0/warehouses/26efc2a4526c691f" />
                <TextField label="Access Token (opcional)" type="password" value={form.databricks_token}
                  onChange={(e) => setForm({ ...form, databricks_token: e.target.value })}
                  helperText="Opcional — token específico desta conexão (write-only). Por padrão usa DATABRICKS_TOKEN do .env-databriks." />
                <TextField label="Tabela / alias lógico" value={form.databricks_table_name}
                  onChange={(e) => setForm({ ...form, databricks_table_name: e.target.value })}
                  helperText="Usado no blob path (ex: system.billing.usage)" />
                <TextField label="Query de consumo" multiline rows={4} value={form.databricks_query}
                  onChange={(e) => setForm({ ...form, databricks_query: e.target.value })}
                  helperText="SQL do consumo do Workspace. Padrão: SELECT * FROM system.billing.usage. Pode apontar para outra tabela/visão (ex: system.billing.list_purchases)." />
                <TextField label="Coluna de data (mês)" value={form.databricks_month_column}
                  onChange={(e) => setForm({ ...form, databricks_month_column: e.target.value })}
                  helperText="Coluna usada para derivar o billing period (padrão: usage_date)" />
              </>
            )}

            {/* Fontes coletáveis só existem para conexão com coleta automática.
                Carga manual (CSV) não tem o que ligar nem disparar, então o painel
                não aparece — a entrada é por upload de arquivo. */}
            {editing && form.cloud_scope === "azure" && form.connector_type !== "manual_csv" && (
              <>
                <Divider sx={{ my: 1 }} />
                <AzureSourcesPanel connectionId={editing?.id} />
              </>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={() => void testFormConnection()} disabled={testingForm}>
            {testingForm ? "Testando…" : "Testar conexão"}
          </Button>
          <Button onClick={() => void save()} variant="contained" disabled={testingForm}>Salvar</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(connectionPendingDeletion)} onClose={() => { if (!deleting) { setConnectionPendingDeletion(null); setDeleteError(""); } }} maxWidth="xs" fullWidth>
        <DialogTitle>Excluir conexão?</DialogTitle>
        <DialogContent>
          {deleteError ? (
            <Alert severity="error" sx={{ mb: 2 }}>
              {deleteError}
            </Alert>
          ) : (
            <Typography>
              Você está prestes a excluir a conexão <strong>{connectionPendingDeletion?.name}</strong>.
              A tabela Bronze associada também será removida. Esta ação não pode ser desfeita.
            </Typography>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={deleting} onClick={() => { setConnectionPendingDeletion(null); setDeleteError(""); }}>Cancelar</Button>
          <Button variant="contained" color="error" disabled={deleting || !!deleteError} onClick={() => void remove()}>
            {deleting ? "Excluindo…" : "Excluir conexão"}
          </Button>
        </DialogActions>
      </Dialog>


      <Snackbar open={Boolean(deleteFeedback)} autoHideDuration={5000} onClose={() => setDeleteFeedback("")} anchorOrigin={{ vertical: "bottom", horizontal: "right" }}>
        <Alert severity="success" variant="filled" onClose={() => setDeleteFeedback("")}>{deleteFeedback}</Alert>
      </Snackbar>

      <Dialog open={historyConn !== null} onClose={() => setHistoryConn(null)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ pb: 0 }}>Histórico de alterações — {historyConn?.name ?? ""}</DialogTitle>
        <DialogContent>
          {historyConn && <RecordHistory module="connections" entityType="connection" entityId={historyConn.id} />}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
