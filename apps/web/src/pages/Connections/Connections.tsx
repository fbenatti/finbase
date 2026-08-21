import { useState, useEffect, useMemo, useRef } from "react";
import {
  Box, Typography, Button,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Alert, CircularProgress,
  FormControlLabel, Checkbox, Tab, Tabs, Paper, InputAdornment, Stack, Snackbar,
} from "@mui/material";
import { Add, AutoAwesome, Cloud, Dataset, Download, MenuBook, Psychology, Search, SmartToy, Storage, Storefront } from "@mui/icons-material";
import {
  getConnections, createConnection, updateConnection,
  deleteConnection, getConnectionsGrid, testConnection, testConnectionConfig, SourceConnection,
  formatApiError,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import ConnectionsDataGrid, { ConnectionTestMessage, getProviderLabel } from "./ConnectionsDataGrid";

const CONNECTOR_TYPES = [
  { value: "azure_blob", label: "Azure Blob Storage", scope: "azure" },
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
  { value: "azure_blob", label: "Azure", description: "Blob Storage", icon: <Cloud />, color: "#0078D4", category: "provedor" },
  { value: "gcp_bigquery", label: "GCP", description: "BigQuery", icon: <Storage />, color: "#4285F4", category: "provedor" },
  { value: "oci", label: "OCI", description: "Oracle Cloud", icon: <Cloud />, color: "#F80000", category: "provedor" },
  // Ferramentas Cloud
  { value: "github", label: "GitHub", description: "Copilot / Tokens / Licenças", icon: <Cloud />, color: "#24292e", category: "ferramenta", tool: "github" },
  { value: "notebooklm", label: "NotebookLM", description: "Licenças / Tokens", icon: <MenuBook />, color: "#4285F4", category: "ferramenta", tool: "notebooklm" },
  { value: "gemini", label: "Gemini", description: "Licenças / Tokens", icon: <AutoAwesome />, color: "#8E24AA", category: "ferramenta", tool: "gemini" },
  { value: "claude", label: "Claude", description: "Claude.ai / Anthropic", icon: <Psychology />, color: "#D97757", category: "ferramenta", tool: "claude" },
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
  { value: "other", label: "Outra / Desconhecida" },
];

const emptyForm = {
  code: "", name: "", connector_type: "azure_blob", cloud_name: "", cloud_scope: "azure",
  tenant_id: "", client_id: "", client_secret: "",
  storage_account: "", container_name: "", blob_prefixes: "",
  gcp_token: "", table_name: "",
  mongo_group_id: "", mongo_contract_type: "", marketplace: false,
  oci_fingerprint: "", oci_region: "sa-saopaulo-1", oci_private_key: "",
  github_extraction_type: "token" as "billing" | "license" | "token",
  gemini_extraction_type: "token" as "license" | "token",
  admin_email: "",
};

export default function Connections() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [connectionCatalog, setConnectionCatalog] = useState<SourceConnection[]>([]);
  const [gridTotal, setGridTotal] = useState(0);
  const [gridLoading, setGridLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [testMsgs, setTestMsgs] = useState<Record<number, ConnectionTestMessage>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [activeCategory, setActiveCategory] = useState<"all" | ConnectionCategory>("all");
  const [activeProvider, setActiveProvider] = useState("all");
  const [search, setSearch] = useState("");
  const initializedCategoryTab = useRef(false);
  const gridQuery = useRef<{ offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }> }>({ offset: 0, limit: 25, filters: [] });
  const [connectorPickerOpen, setConnectorPickerOpen] = useState(false);
  const [toolSubPicker, setToolSubPicker] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SourceConnection | null>(null);
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
    if (loading) return;
    if (!initializedCategoryTab.current) {
      initializedCategoryTab.current = true;
      const firstCategory = categoryTabs.find((category) => category.count > 0);
      if (firstCategory) {
        setActiveCategory(firstCategory.value);
        const firstProvider = providerTabsFor(firstCategory.value);
        if (firstProvider.length) setActiveProvider(firstProvider[0]);
      } else {
        setActiveCategory("all");
        setActiveProvider("all");
      }
      return;
    }
    if (activeCategory !== "all" && !CATEGORY_TABS.some((category) => category.value === activeCategory)) setActiveCategory("all");
  }, [activeCategory, categoryTabs, loading]);

  // Ao trocar de categoria, seleciona o primeiro provedor da nova categoria.
  // Em "Todas", mostra todos os provedores (sem filtro de provider).
  useEffect(() => {
    if (loading || !initializedCategoryTab.current) return;
    if (activeCategory === "all") {
      setActiveProvider("all");
      return;
    }
    const providers = providerTabsFor(activeCategory);
    setActiveProvider(providers.length ? providers[0] : "all");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCategory, loading]);

  const loadGrid = async (query = gridQuery.current) => {
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
      setConnections(data.items);
      setGridTotal(data.total);
    } catch (gridError: unknown) {
      setError(gridError instanceof Error ? gridError.message : "Falha ao carregar conexões");
    } finally {
      setGridLoading(false);
    }
  };

  useEffect(() => {
    gridQuery.current = { offset: 0, limit: gridQuery.current.limit, filters: [] };
    void loadGrid(gridQuery.current);
  }, [activeCategory, activeProvider, search]);

  const openNew = (connectorType = "azure_blob") => {
    setEditing(null);
    setFormTestMessage(null);
    const scope = CONNECTOR_TYPES.find((type) => type.value === connectorType)?.scope || "";
    setForm({ ...emptyForm, connector_type: connectorType, cloud_scope: scope });
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
        blob_prefixes: (conn.blob_prefixes || []).join("\n"),
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
        blob_prefixes: (conn.blob_prefixes || []).join("\n"),
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
    }

    return {
      code: form.code,
      name: form.name,
      connector_type: form.connector_type,
      cloud_name: form.cloud_name || null,
      cloud_scope: form.cloud_scope === "other" ? null : (form.cloud_scope || null),
      config_json: configJson,
      blob_prefixes: form.blob_prefixes.split("\n").map((value) => value.trim()).filter(Boolean),
    };
  };

  const save = async () => {
    try {
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

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

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
          value={activeCategory}
          onChange={(_, value: string) => setActiveCategory(value as "all" | ConnectionCategory)}
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
                <TextField label="Blob Prefixes (um por linha)" multiline rows={3}
                  value={form.blob_prefixes}
                  onChange={(e) => setForm({ ...form, blob_prefixes: e.target.value })}
                  helperText="Ex: reports/Report4P-actual-cost/" />
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
                <TextField label="Blob Prefixes (um por linha)" multiline rows={2}
                  value={form.blob_prefixes}
                  onChange={(e) => setForm({ ...form, blob_prefixes: e.target.value })}
                  helperText="Padrão: reports/cost-csv (relatórios de custo financeiro)" />
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

    </Box>
  );
}
