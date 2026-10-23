import { Dialog, DialogTitle, DialogContent, DialogActions } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect, useRef } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { Box, Typography, Button, TextField, Divider, Select, MenuItem, FormControl, InputLabel, Alert, CircularProgress, FormControlLabel, Checkbox, Paper, InputAdornment, Stack, Snackbar, Chip } from "@mui/material";
import { Add, AutoAwesome, Cloud, Dataset, Download, MenuBook, Psychology, Search, SmartToy, Storage, Storefront, Refresh, Validate, Save } from "./ConnectionIcons";
import {
  getConnections, createConnection, updateConnection,
  deleteConnection, getConnectionsGrid, testConnection, testConnectionConfig, SourceConnection,
  formatApiError, extractMcaCommitmentInvoices, getMcaCommitmentProfiles, getMcaCommitmentStatus,
  getHiddenConnectorTypes,
} from "../../services/api";
import { api } from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SourceSetupWizard from "./SourceSetupWizard";
import { CONNECTOR_TYPES, SOURCE_PROVIDERS, SourceProvider, kindOf, providerOf, visibleKinds } from "./sourceCatalog";

export { CONNECTOR_TYPES };
import ConnectionsDataGrid, { ConnectionTestMessage, getProviderLabel, getConnectorLabel } from "./ConnectionsDataGrid";
import RecordHistory from "../../components/RecordHistory";
import AzureSourcesPanel from "./AzureSourcesPanel";
import IntegrationsPanel from "./IntegrationsPanel";
import VersionHistorySection from "../../components/VersionHistorySection";

function describeConnectionLoadError(error: unknown, endpoint: string, fallback: string): string {
  const response = typeof error === "object" && error !== null && "response" in error
    ? (error as { response?: { status?: number } }).response
    : undefined;
  const status = response?.status ? ` (HTTP ${response.status})` : "";
  const detail = error instanceof Error ? error.message : fallback;
  return `${endpoint}${status}: ${detail}`;
}

// Sub-tipos de extração por ferramenta.
const TOOL_EXTRACTION_TYPES: Record<string, { value: string; connectorType: string; cloudName: string; emoji: string; title: string; description: string }[]> = {
  github: [
    { value: "billing", connectorType: "github_billing", cloudName: "github-billing", emoji: "billing", title: "Custos e faturas", description: "Relatórios de custo detalhados" },
    { value: "token", connectorType: "github_token", cloudName: "github-token", emoji: "token", title: "Consumo por mês", description: "Uso mensal detalhado (Copilot / Actions)" },
    { value: "license", connectorType: "github_license", cloudName: "github-license", emoji: "license", title: "Licenças e usuários", description: "Usuários e licenças ativas da Enterprise" },
  ],
  notebooklm: [
    { value: "token", connectorType: "notebooklm_token", cloudName: "notebooklm-token", emoji: "token", title: "Consumo por mês", description: "Relatório de consumo de tokens" },
    { value: "license", connectorType: "notebooklm_license", cloudName: "notebooklm-license", emoji: "license", title: "Licenças (usuários em uso)", description: "Colaboradores com licença em uso" },
  ],
  gemini: [
    { value: "token", connectorType: "gemini_token", cloudName: "gemini-token", emoji: "token", title: "Consumo por mês", description: "Relatório de consumo de tokens" },
    { value: "license", connectorType: "gemini_license", cloudName: "gemini-license", emoji: "license", title: "Licenças (usuários em uso)", description: "Usuários com licença ativa no Gemini" },
  ],
  claude: [
    { value: "token", connectorType: "claude_token", cloudName: "claude-token", emoji: "token", title: "Consumo por mês", description: "Relatório de tokens e atividades" },
    { value: "license", connectorType: "claude_license", cloudName: "claude-license", emoji: "license", title: "Licenças por colaborador", description: "Colaboradores com licença em uso" },
  ],
};

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
  const [searchParams] = useSearchParams();
  const activeView = searchParams.get("view") === "integrations" ? "integrations" : "connections";
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [connectionCatalog, setConnectionCatalog] = useState<SourceConnection[]>([]);
  // Tipos desligados em Conectores (Administração): ficam fora das abas e da lista.
  const [hiddenConnectorTypes, setHiddenConnectorTypes] = useState<Set<string>>(new Set());
  const [gridTotal, setGridTotal] = useState(0);
  const [gridLoading, setGridLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [testMsgs, setTestMsgs] = useState<Record<number, ConnectionTestMessage>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  // Fonte recém-criada: abre o passo a passo até o agendamento da coleta.
  const [setupConnection, setSetupConnection] = useState<SourceConnection | null>(null);
  const [tabsReady, setTabsReady] = useState(false);
  // Serviços auxiliares (Jev, ServiceNow) não são fontes de dado de nuvem, mas
  // seguem o mesmo princípio de configuração — por isso vivem aqui, e não numa
  // tela separada que ninguém encontra.
  const gridQuery = useRef<{ offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }> }>({ offset: 0, limit: 25, filters: [] });
  const gridRequestId = useRef(0);
  const [connectorPickerOpen, setConnectorPickerOpen] = useState(false);
  // Seletor em dois passos: provedor e depois o tipo de dado.
  const [pickerProvider, setPickerProvider] = useState<SourceProvider | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [savingConnection, setSavingConnection] = useState(false);
  const [editing, setEditing] = useState<SourceConnection | null>(null);
  const [historyConn, setHistoryConn] = useState<SourceConnection | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [testingForm, setTestingForm] = useState(false);
  const [formTestMessage, setFormTestMessage] = useState<{ severity: "success" | "warning" | "error" | "info"; msg: string } | null>(null);
  const [connectionPendingDeletion, setConnectionPendingDeletion] = useState<SourceConnection | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteFeedback, setDeleteFeedback] = useState("");
  const [deleteError, setDeleteError] = useState("");

  const load = async () => {
    try {
      setLoading(true);
      const [catalog, hidden] = await Promise.all([
        getConnections(),
        getHiddenConnectorTypes().catch(() => [] as string[]),
      ]);
      setConnectionCatalog(catalog);
      setHiddenConnectorTypes(new Set(hidden));
    } catch (e: unknown) {
      setError(describeConnectionLoadError(e, "Falha ao recarregar conexões — GET /connections/", "Erro não identificado"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Provedores que ainda têm algum tipo de dado habilitado em Conectores.
  const pickerProviders = SOURCE_PROVIDERS.filter((provider) => visibleKinds(provider, hiddenConnectorTypes).length > 0);

  // Start with every connection visible; categories and providers are filters.
  useEffect(() => { if (!loading) setTabsReady(true); }, [loading]);

  const loadGrid = async (query = gridQuery.current) => {
    const requestId = ++gridRequestId.current;
    try {
      setGridLoading(true);
      const data = await getConnectionsGrid({
        limit: query.limit,
        offset: query.offset,
        search: search || undefined,
        sort_field: query.sort?.field,
        sort_direction: query.sort?.direction,
        filters: query.filters,
      });
      if (requestId !== gridRequestId.current) return;
      setConnections(data.items);
      setGridTotal(data.total);
    } catch (gridError: unknown) {
      if (requestId !== gridRequestId.current) return;
      setError(describeConnectionLoadError(gridError, "Falha ao recarregar a tabela — GET /connections/grid", "Erro não identificado"));
    } finally {
      if (requestId === gridRequestId.current) setGridLoading(false);
    }
  };

  useEffect(() => {
    if (!tabsReady) return;
    gridQuery.current = { offset: 0, limit: gridQuery.current.limit, filters: [] };
    void loadGrid(gridQuery.current);
  }, [search, tabsReady]);

  const openNew = (connectorType = "azure_blob") => {
    setEditing(null);
    setError("");
    setFormTestMessage(null);
    const scope = providerOf(connectorType)?.key || "";
    const latestPeriod = latestMcaInvoiceMonth();
    const initialForm = { ...emptyForm, connector_type: connectorType, cloud_scope: scope, mca_start_period: latestPeriod, mca_end_period: latestPeriod };
    if (connectorType === "azure_pricesheet") {
      Object.assign(initialForm, {
        code: "azure-pricesheet", name: "Tabela de preços do Azure", cloud_name: "Tabela de preços do Azure",
        blob_prefixes: "reports/report_-pricesheet/",
      });
    }
    setForm(initialForm);
    if (connectorType === "azure_mca_invoice") {
      void getMcaCommitmentStatus().then((data) => {
        setForm((current) => ({ ...current, mca_auth_mode: data.auth_mode || "bearer", mca_billing_account_ids: (data.billing_account_ids || []).join("\n") }));
      }).catch(() => undefined);
    }
    setDialogOpen(true);
  };

  const closePicker = () => { setConnectorPickerOpen(false); setPickerProvider(null); };

  const chooseKind = (connectorType: string) => {
    closePicker();
    const tool = Object.keys(TOOL_EXTRACTION_TYPES).find((key) => TOOL_EXTRACTION_TYPES[key].some((type) => type.connectorType === connectorType));
    const toolType = tool ? TOOL_EXTRACTION_TYPES[tool].find((type) => type.connectorType === connectorType) : undefined;
    if (tool && toolType) chooseToolType(tool, toolType);
    else openNew(connectorType);
  };

  const chooseProvider = (provider: SourceProvider) => {
    const kinds = visibleKinds(provider, hiddenConnectorTypes);
    // Um único tipo de dado: não há o que perguntar.
    if (kinds.length === 1) chooseKind(kinds[0].connectorType);
    else setPickerProvider(provider);
  };

  const chooseToolType = (tool: string, type: { connectorType: string; cloudName: string; title: string }) => {
    setEditing(null);
    setFormTestMessage(null);
    const toolScope: Record<string, string> = { github: "github", notebooklm: "notebooklm", gemini: "gemini", claude: "claude" };
    const githubExtraction = type.connectorType.startsWith("github_") ? type.connectorType.replace("github_", "") : "token";
    const geminiExtraction = (type.connectorType.startsWith("notebooklm_") || type.connectorType.startsWith("gemini_")) ? type.connectorType.split("_")[1] : "token";
    setForm({
      ...emptyForm,
      name: `${providerOf(type.connectorType)?.label ?? tool} ${type.title}`,
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
    setError("");
    setFormTestMessage(null);
    const cfg = conn.config_json as Record<string, string>;
    if (conn.connector_type === "azure_mca_invoice") {
      const latestPeriod = latestMcaInvoiceMonth();
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "Azure MCA Brasil",
        cloud_scope: conn.cloud_scope || "azure",
        mca_start_period: latestPeriod,
        mca_end_period: latestPeriod,
      });
      void getMcaCommitmentStatus().then((status) => {
        setForm((current) => ({
          ...current,
          mca_auth_mode: status.auth_mode || "bearer",
          mca_billing_account_ids: (status.billing_account_ids || []).join("\n"),
        }));
      }).catch(() => undefined);
    } else if (conn.connector_type === "azure_blob" || conn.connector_type === "azure_pricesheet") {
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
        blob_prefixes: conn.connector_type === "azure_pricesheet" ? "reports/report_-pricesheet/" : prefixesToText(conn.blob_prefixes),
      });
    } else if (conn.connector_type === "azure_openai_ptu") {
      // The API deliberately omits secrets. Never repopulate the write-only field.
      setForm({
        ...emptyForm,
        code: conn.code, name: conn.name,
        connector_type: conn.connector_type,
        cloud_name: conn.cloud_name || "",
        cloud_scope: conn.cloud_scope || "azure",
        credential_profile: cfg.credential_profile || "",
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
        tenant_id: cfg.project_id || "",
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
        tenant_id: cfg.project_id || "",
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
        tenant_id: cfg.project_id || "",
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
        tenant_id: cfg.org_id || "",
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
    if (form.connector_type === "azure_blob" || form.connector_type === "azure_pricesheet") {
      if (form.tenant_id) configJson.tenant_id = form.tenant_id;
      if (form.client_id) configJson.client_id = form.client_id;
      if (form.client_secret) configJson.client_secret = form.client_secret;
      if (form.storage_account) configJson.storage_account = form.storage_account;
      if (form.container_name) configJson.container_name = form.container_name;
      if (form.connector_type === "azure_pricesheet") {
        configJson.source_key = "price_sheet";
        configJson.environment_slug = "azure-pricesheet";
        configJson.independent_connection = true;
        configJson.scope = "BillingAccountId";
      }
    } else if (form.connector_type === "azure_openai_ptu") {
      configJson.credential_profile = form.credential_profile || "";
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
        credential_ref: form.credential_profile || "",
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
    if (savingConnection) return;
    setSavingConnection(true);
    setError("");
    try {
      if (form.connector_type === "azure_mca_invoice") {
        const billingAccountIds = form.mca_billing_account_ids.split(/[\n,;]/).map((value) => value.trim()).filter(Boolean);
        await api.put("/custos/compromisso-mca/credenciais", {
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
      else setSetupConnection(await createConnection(body));
      setDialogOpen(false);
      void load();
      void loadGrid();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível salvar a conexão.");
    } finally {
      setSavingConnection(false);
    }
  };

  // A API não devolve segredos já salvos; só avisa que existem.
  const savedSecrets = Boolean((editing?.config_json as Record<string, unknown> | undefined)?.credentials_configured);

  const testFormConnection = async () => {
    setTestingForm(true);
    setFormTestMessage(null);
    try {
      if (form.connector_type === "azure_mca_invoice") {
        const result = await getMcaCommitmentProfiles();
        const profiles = Array.isArray(result.profiles) ? result.profiles : [];
        const scopes = new Set(profiles.map((profile: { billingAccountId?: string }) => profile.billingAccountId).filter(Boolean));
        setFormTestMessage({
          severity: "success",
          msg: `Token válido: ${profiles.length} billing profile(s) encontrados em ${scopes.size} Billing Account(s).`,
        });
        return;
      }
      const result = await testConnectionConfig(buildConnectionBody());
      const isOk = result.status === "OK";
      const isPending = result.status === "PENDING";
      setFormTestMessage({
        severity: isOk ? "success" : isPending ? "warning" : "error",
        msg: isOk ? "Conexão testada com sucesso: " + result.message : isPending ? "Pendente de configuração: " + result.message : "Falha no teste: " + result.message,
      });
    } catch (testError: unknown) {
      const message = testError instanceof Error ? testError.message : "Não foi possível executar o teste";
      setFormTestMessage({ severity: "error", msg: "Falha no teste: " + message });
    } finally {
      setTestingForm(false);
    }
  };

  const extractMcaData = async () => {
    const startPeriod = form.mca_start_period.replace("-", "");
    const endPeriod = form.mca_end_period.replace("-", "");
    const billingAccountIds = form.mca_billing_account_ids.split(/[\n,;]/).map((value) => value.trim()).filter(Boolean);
    if (!/^\d{6}$/.test(startPeriod) || !/^\d{6}$/.test(endPeriod) || startPeriod > endPeriod) {
      setFormTestMessage({ severity: "error", msg: "Escolha um período válido: o início não pode ser depois do fim." });
      return;
    }
    if (!billingAccountIds.length) {
      setFormTestMessage({ severity: "error", msg: "Nenhuma conta de cobrança está configurada para a extração." });
      return;
    }
    setTestingForm(true);
    setFormTestMessage({ severity: "info", msg: `Consultando invoices de ${form.mca_start_period} a ${form.mca_end_period}…` });
    try {
      const result = await extractMcaCommitmentInvoices({ start_period: startPeriod, end_period: endPeriod, billing_account_ids: billingAccountIds });
      setFormTestMessage({
        severity: "success",
        msg: `Extração concluída: ${Number(result.invoice_profile_month_rows || 0).toLocaleString("pt-BR")} linha(s), ${Number(result.invoice_ids || 0).toLocaleString("pt-BR")} invoice(s), ${Number(result.profiles_loaded || 0).toLocaleString("pt-BR")} profile(s), ${Number(result.months_requested || 0)} competência(s). base de origem atualizada.`,
      });
      void load();
      void loadGrid();
    } catch (extractError: unknown) {
      setFormTestMessage({ severity: "error", msg: extractError instanceof Error ? `Falha na extração MCA: ${extractError.message}` : "Falha na extração do MCA." });
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
      setDeleteFeedback(`Conexão "${connectionPendingDeletion.name}" excluída com sucesso. Tabela base de origem removida.`);
      await load();
      await loadGrid();
    } catch (e: unknown) {
      setDeleteError(formatApiError(e, "Falha ao remover conexão"));
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
      const message = testError instanceof Error ? testError.message : "Não foi possível executar o teste";
      setTestMsgs((previous) => ({ ...previous, [connection.id]: { severity: "error", msg: `Erro: ${message}` } }));
    } finally {
      setTestingId(null);
    }
  };

  const exportConnections = () => {
    const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const csv = [
      ["Nome", "Identificador", "Provedor", "Tipo", "Última atualização", "Resultado do último teste"],
      ...connections.map((connection) => [
        connection.name,
        connection.code,
        getProviderLabel(connection.cloud_scope),
        getConnectorLabel(connection.connector_type),
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

  if (loading || !tabsReady) return <Stack direction="row" gap={1} alignItems="center" sx={{ p: 3 }}><CircularProgress size={20} /><Typography variant="body2">Carregando origens de dados…</Typography></Stack>;

  return (
    <Box sx={{ minWidth: 0 }}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={2} sx={{ mb: 3 }}>
        <Box>
          <Typography variant="h4" component="h1">{activeView === "integrations" ? "Serviços integrados" : "Origens de dados"}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>Ligue provedores e aplicações, defina o acesso e acompanhe os testes das suas origens.</Typography>
        </Box>
        {activeView !== "integrations" && <Button variant="contained" startIcon={<Add />} onClick={() => setConnectorPickerOpen(true)}>Conectar fonte</Button>}
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {activeView === "integrations" ? <IntegrationsPanel /> : (
        <Paper variant="outlined" sx={{ overflow: "hidden" }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} sx={{ px: { xs: 2, md: 3 }, py: 2, borderBottom: 1, borderColor: "divider" }}>
            <Box><Typography variant="h6">Conexões configuradas</Typography><Typography variant="body2" color="text.secondary">Controle as origens de dados e as credenciais de acesso.</Typography></Box>
            <Chip variant="outlined" label={`${gridTotal} ${gridTotal === 1 ? "fonte" : "fontes"}`} />
          </Stack>
          <Stack direction={{ xs: "column", lg: "row" }} justifyContent="space-between" gap={2} sx={{ p: 2, bgcolor: "background.default", borderTop: 1, borderBottom: 1, borderColor: "divider" }}>
            <TextField label="Localizar conexão" placeholder="Nome, identificador ou origem" value={search} onChange={event => setSearch(event.target.value)} slotProps={{ input: { startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment> } }} sx={{ width: { xs: "100%", lg: 340 }, bgcolor: "background.paper" }} />
            <Stack direction="row" gap={1} flexWrap="wrap">
              <Button variant="outlined" startIcon={<Refresh />} disabled={gridLoading} onClick={() => { void load(); void loadGrid(); }}>Recarregar lista</Button>
              <Button variant="outlined" startIcon={<Download />} onClick={exportConnections} disabled={gridLoading || connections.length === 0}>Baixar esta página</Button>
            </Stack>
          </Stack>
          <ConnectionsDataGrid
            key={search}
            connections={connections} total={gridTotal} loading={gridLoading}
            testingId={testingId} testMessages={testMsgs}
            onEdit={openEdit} onHistory={setHistoryConn} onSetup={setSetupConnection}
            onTest={connection => void runTest(connection)} onDelete={setConnectionPendingDeletion}
            onQuery={query => { gridQuery.current = { ...gridQuery.current, ...query }; void loadGrid(gridQuery.current); }}
          />
        </Paper>
      )}

      <Dialog open={connectorPickerOpen} onClose={closePicker} maxWidth="md" fullWidth>
        <DialogTitle sx={{ pb: 0 }}>{pickerProvider ? `${pickerProvider.label}: o que coletar?` : "Conectar fonte"}</DialogTitle>
        <DialogContent sx={{ pt: "16px !important", pb: 4 }}>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            {pickerProvider ? "Escolha o tipo de dado que essa fonte vai trazer." : "Escolha de onde vêm os dados."}
          </Typography>
          {pickerProviders.length === 0 && <Alert severity="info">Nenhum tipo de fonte está habilitado. Habilite em Configurações › Conectores.</Alert>}
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" }, gap: 1 }}>
            {(pickerProvider
              ? visibleKinds(pickerProvider, hiddenConnectorTypes).map((kind) => ({ key: kind.connectorType, label: kind.label, description: kind.description, icon: pickerProvider.icon, onClick: () => chooseKind(kind.connectorType) }))
              : pickerProviders.map((provider) => ({ key: provider.key, label: provider.label, description: provider.description, icon: provider.icon, onClick: () => chooseProvider(provider) }))
            ).map((option) => (
              <Button
                key={option.key}
                variant="outlined"
                color="inherit"
                onClick={option.onClick}
                sx={{
                  width: "100%",
                  minHeight: 80,
                  justifyContent: "flex-start",
                  textAlign: "left",
                  gap: 2,
                  borderColor: "divider",
                  color: "text.primary",
                  "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
                }}
              >
                <Box sx={{ color: "primary.main", display: "flex", "& svg": { width: 24, height: 24 } }}>{option.icon}</Box>
                <Box><Typography variant="body2" fontWeight={600}>{option.label}</Typography><Typography variant="caption" color="text.secondary">{option.description}</Typography></Box>
              </Button>
            ))}
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          {pickerProvider && <Button onClick={() => setPickerProvider(null)}>Voltar</Button>}
          <Button onClick={closePicker}>Cancelar</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialogOpen} onClose={() => { if (!savingConnection) setDialogOpen(false); }} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? "Alterar conexão" : "Definir parâmetros da conexão"}</DialogTitle>
        <DialogContent dividers>
          {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
          {formTestMessage && <Alert severity={formTestMessage.severity} sx={{ mb: 2 }}>{dataDisplayText(formTestMessage.msg)}</Alert>}
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" }, gap: 2, mt: 1,
            "& > .MuiAlert-root, & > .MuiTypography-root, & > .MuiDivider-root, & > .MuiBox-root, & > .MuiTextField-root:has(textarea)": { gridColumn: "1 / -1" },
            // Acesso e parâmetros: uma coluna só, igual para qualquer tipo de fonte.
            "& > .access-section ~ *": { gridColumn: "1 / -1" } }}>
            <Typography variant="subtitle2">Dados de identificação</Typography>
            <TextField label="Nome da fonte" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
            <TextField label="Identificador da conexão" value={form.code} disabled={form.connector_type === "azure_pricesheet"} onChange={e => setForm({ ...form, code: e.target.value })} helperText="Chave usada nos fluxos de coleta." />
            {/* O tipo é escolhido em "Conectar fonte"; aqui só é exibido. */}
            <TextField label="Tipo de fonte" sx={{ gridColumn: "1 / -1" }} disabled
              value={[providerOf(form.connector_type)?.label, kindOf(form.connector_type)?.label].filter(Boolean).join(" · ") || form.connector_type} />
            <TextField label="Conta ou ambiente de faturamento" sx={{ gridColumn: "1 / -1" }} value={form.cloud_name} onChange={e => setForm({ ...form, cloud_name: e.target.value })} />
            <Divider />
            <Typography variant="subtitle2" className="access-section">Acesso e parâmetros — {providerOf(form.connector_type)?.label ?? "origem"}</Typography>
            {form.connector_type === "azure_mca_invoice" && (
              <>
                <Alert severity="info">Consulta valores mensais consolidados ligados a invoices. Cada Billing Account fica identificada e particionada sem sobrescrever as demais. A credencial é somente gravação.</Alert>
                <TextField label="IDs das contas de cobrança" multiline minRows={3} value={form.mca_billing_account_ids}
                  onChange={(e) => setForm({ ...form, mca_billing_account_ids: e.target.value })}
                  helperText="Um ID por linha. Ex.: accountGuid:tenantGuid_2019-05-31. Os profiles são descobertos dinamicamente em cada conta." />
                <FormControl fullWidth>
                  <InputLabel>Forma de autenticação</InputLabel>
                  <Select value={form.mca_auth_mode} label="Forma de autenticação"
                    onChange={(e) => setForm({ ...form, mca_auth_mode: e.target.value as "bearer" | "service_principal" | "azure_cli" })}>
                    <MenuItem value="bearer">Token bearer temporário</MenuItem>
                    <MenuItem value="service_principal">Service Principal</MenuItem>
                    <MenuItem value="azure_cli">Azure CLI (renova o token sozinho)</MenuItem>
                  </Select>
                </FormControl>
                {form.mca_auth_mode === "bearer" ? (
                  <TextField label="Token bearer" type="password" multiline minRows={2} value={form.mca_bearer_token}
                    onChange={(e) => setForm({ ...form, mca_bearer_token: e.target.value })}
                    helperText="Cole o token de acesso ao Azure Resource Manager." />
                ) : form.mca_auth_mode === "service_principal" ? (
                  <>
                    <TextField label="Diretório (tenant)" value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                    <TextField label="Aplicação (client ID)" value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                    <TextField label="Segredo de acesso" type="password" value={form.client_secret} onChange={(e) => setForm({ ...form, client_secret: e.target.value })} />
                  </>
                ) : (
                  <>
                    <TextField label="Diretório (tenant)" value={form.tenant_id || "9b03d892-fe8c-4e05-80f1-32e928a16ba2"}
                      onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                    <Alert severity="info">Usa a sessão do Azure CLI deste servidor e renova o access token automaticamente. Faça login com az login no mesmo usuário que executa a API.</Alert>
                  </>
                )}
                <Typography variant="body2" color="text.secondary">Origem de contratos · Faturas agrupadas por conta de cobrança · a partir de abril de 2025.</Typography>
                <Divider />
                <Alert severity="info">Grave a credencial primeiro. O teste e a extração abaixo usam o token já salvo, consultam os Billing Accounts configurados e nunca exibem o segredo.</Alert>
                <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                  <TextField label="Período inicial" type="month" value={form.mca_start_period} onChange={(e) => setForm({ ...form, mca_start_period: e.target.value })} InputLabelProps={{ shrink: true }} fullWidth />
                  <TextField label="Período final" type="month" value={form.mca_end_period} onChange={(e) => setForm({ ...form, mca_end_period: e.target.value })} InputLabelProps={{ shrink: true }} fullWidth />
                </Stack>
                <Button variant="outlined" startIcon={testingForm ? <CircularProgress size={16} /> : <Storage />} onClick={() => void extractMcaData()} disabled={testingForm || !form.mca_billing_account_ids.trim()}>
                  {testingForm ? "Buscando dados no Azure…" : "Carregar invoices na base de origem"}
                </Button>
              </>
            )}

            {form.connector_type === "azure_openai_ptu" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "text.primary" }}>Credenciais</Typography>
                <Alert severity="info">A coleta usa apenas recursos e métricas já existentes. Nenhum recurso Azure será criado. O secret nunca é devolvido pela API.</Alert>
                <TextField label="Conjunto de credenciais" value={form.credential_profile || ""}
                  onChange={(e) => setForm({ ...form, credential_profile: e.target.value })}
                  helperText="Perfil de execução" />
                <TextField label="Diretório (tenant)" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                <TextField label="Aplicação (client ID)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Segredo de acesso (para substituir)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Deixe em branco para manter o segredo já configurado." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Abrangência da coleta</Typography>
                <Alert severity="warning">Use Testar conexão para descobrir assinaturas, contas OpenAI e deployments PTU sem gravar métricas.</Alert>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Agendamento da coleta</Typography>
                <Typography variant="body2" color="text.secondary">Granularidade de 1 min, gravação horária em UTC, defasagem de 10 min, janela de 3 h e reprocessamento de 93 dias. Exibição no fuso America/Sao_Paulo.</Typography>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>4. Vínculo com projetos</Typography>
                <Typography variant="body2" color="text.secondary">Depois de salvar, vincule os projetos no Azure PTU (reservation/ondemand, global/regional, vigência).</Typography>
                <Typography variant="subtitle2" sx={{ mt: 1 }}>5. Monitoramento de subprojetos (Event Hub)</Typography>
                <TextField label="Namespace do Event Hub" value={form.eventhub_namespace}
                  onChange={(e) => setForm({ ...form, eventhub_namespace: e.target.value })} />
                <TextField label="Nome do Event Hub" value={form.eventhub_name}
                  onChange={(e) => setForm({ ...form, eventhub_name: e.target.value })} />
                <TextField label="Grupo de consumidores" value={form.eventhub_consumer_group}
                  onChange={(e) => setForm({ ...form, eventhub_consumer_group: e.target.value })}
                  helperText="Padrão: openfinops-azure-ptu. Teste de partições pelo botão Testar conexão." />
              </>
            )}


            {form.connector_type === "azure_resource_graph" && (
              <>
                <Typography variant="subtitle2" sx={{ mt: 1, color: "text.primary" }}>Credenciais</Typography>
                <Alert severity="info">A coleta consulta o Azure Resource Graph (inventário) (inventário e recursos ociosos). Nenhum recurso é criado. O secret nunca é devolvido pela API.</Alert>
                <TextField label="Conjunto de credenciais" value={form.credential_profile || ""}
                  onChange={(e) => setForm({ ...form, credential_profile: e.target.value })}
                  helperText="Perfil do .env. Em branco = usar os campos abaixo." />
                <TextField label="Diretório (tenant)" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} />
                <TextField label="Aplicação (client ID)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Segredo de acesso (para substituir)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Deixe em branco para manter o segredo já configurado." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Abrangência da coleta</Typography>
                <TextField label="IDs das assinaturas (uma por linha)" multiline rows={3}
                  value={form.arg_subscription_ids}
                  onChange={(e) => setForm({ ...form, arg_subscription_ids: e.target.value })}
                  helperText="Em branco = todas as subscriptions acessíveis. Recomendado preencher (limite de 10 mil linhas por query do ARG)." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Regras de recursos ociosos (opcional)</Typography>
                <TextField label="Regras KQL personalizadas (nome: KQL por linha)" multiline rows={4}
                  value={form.arg_orphan_rules}
                  onChange={(e) => setForm({ ...form, arg_orphan_rules: e.target.value })}
                  helperText="Substituem as regras padrão (disk_unattached, vm_deallocated, public_ip_idle, snapshot_orphan, lb_idle). Ex.: vm_deallocated: resources | where ..." />
                <Typography variant="subtitle2" sx={{ mt: 1 }}>Agendamento da coleta</Typography>
                <Typography variant="body2" color="text.secondary">
                  Scan lista as subscriptions acessíveis; a extração coleta o inventário completo e
                  os órfãos por snapshot diário, persiste na base de origem e permite reprocessar para a base consolidada
                  (MODELO INVENTÁRIO CLOUD / MODELO RECURSOS ÓRFÃOS). Governança → Recursos Órfãos.
                </Typography>
              </>
            )}

            {(form.connector_type === "azure_blob" || form.connector_type === "azure_pricesheet") && (
              <>
                {form.connector_type === "azure_pricesheet" && (
                  <Alert severity="info">
                    Conexão independente do Azure Cost Management. As credenciais ficam gravadas nesta conexão;
                    o snapshot vai para a base de origem <code>raw_azure_pricesheet</code> pelo prefixo próprio.
                  </Alert>
                )}
                <TextField label="Diretório (tenant)" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Identificador do diretório no Microsoft Entra ID." />
                <TextField label="Aplicação (client ID)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Código da aplicação registrada no provedor." />
                <TextField label="Segredo de acesso" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Valor do segredo da aplicação. Deixe em branco ao editar para manter o atual." />
                <TextField label="Conta de storage" value={form.storage_account}
                  onChange={(e) => setForm({ ...form, storage_account: e.target.value })} />
                <TextField label="Container de storage" value={form.container_name}
                  onChange={(e) => setForm({ ...form, container_name: e.target.value })} />
                {form.connector_type === "azure_blob" ? (
                  <TextField label="Caminhos do Blob (tipo: pasta, uma por linha)" multiline rows={4}
                    value={form.blob_prefixes}
                    onChange={(e) => setForm({ ...form, blob_prefixes: e.target.value })}
                    helperText={
                      "O tipo define qual coletor usa o caminho; nada fica fixo no código.\n"
                      + "Ex.:\nactual_cost: reports/actual-cost/\n"
                      + "amortized_cost: reports/amortized-cost/"
                    } />
                ) : (
                  <TextField label="Pasta dos preços contratados" value="reports/report_-pricesheet/" disabled />
                )}
              </>
            )}

            {form.connector_type === "gcp_bigquery" && (
              <>
                <TextField label="Identificador do projeto" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Identificador do projeto GCP (ex: billing-344820)" />
                <TextField label="Conjunto de dados (dataset)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Dataset do BigQuery (ex: billing_export)" />
                <TextField label="Tabela-fonte" value={form.table_name}
                  onChange={(e) => setForm({ ...form, table_name: e.target.value })}
                  helperText="Nome completo da tabela (ex: gcp_billing_export_resource_v1_...)" />
                <TextField label="Chave da conta de serviço (JSON)" multiline rows={6}
                  value={form.gcp_token}
                  onChange={(e) => setForm({ ...form, gcp_token: e.target.value })}
                  helperText="Cole o conteúdo completo do JSON da conta de serviço." />
              </>
            )}

            {form.connector_type === "oci" && (
              <>
                <TextField label="Tenancy (OCID)" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="OCID da Tenancy (ex: ocid1.tenancy.oc1..aaaa...)" />
                <TextField label="Usuário (OCID)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="OCID do usuário OCI (ex: ocid1.user.oc1..aaaa...)" />
                <TextField label="Fingerprint da chave" value={form.oci_fingerprint}
                  onChange={(e) => setForm({ ...form, oci_fingerprint: e.target.value })}
                  helperText="Fingerprint da API Key registrada na OCI" />
                <TextField label="Região da nuvem" value={form.oci_region}
                  onChange={(e) => setForm({ ...form, oci_region: e.target.value })}
                  helperText="Região principal da OCI (ex: sa-saopaulo-1, sa-vinhedo-1, us-ashburn-1)" />
                <TextField label="Arquivo PEM da chave privada" multiline rows={5}
                  value={form.oci_private_key}
                  onChange={(e) => setForm({ ...form, oci_private_key: e.target.value })}
                  placeholder={savedSecrets ? "•••••• chave já cadastrada" : undefined}
                  helperText={savedSecrets ? "Chave privada já cadastrada no servidor. Deixe em branco para manter ou cole uma nova para trocar." : "Cole o conteúdo completo do arquivo de chave privada (.pem)."} />
              </>
            )}

            {form.connector_type === "mongodb_atlas" && (
              <>
                <TextField label="URL da API" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Ex: https://cloud.mongodb.com/api/atlas/v1.0" />
                <TextField label="Chave pública" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
                <TextField label="Chave privada" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })} />
                <TextField label="Identificador do grupo" value={form.mongo_group_id}
                  onChange={(e) => setForm({ ...form, mongo_group_id: e.target.value })} />
                <TextField label="Modalidade de contrato" value={form.mongo_contract_type}
                  onChange={(e) => setForm({ ...form, mongo_contract_type: e.target.value })}
                  helperText="plano pro ou enterprise" />
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={form.marketplace}
                      onChange={(e) => setForm({ ...form, marketplace: e.target.checked })}
                    />
                  }
                  label="Oferta no marketplace"
                />
              </>
            )}

            {(form.connector_type === "github_billing" || form.connector_type === "github_license" || form.connector_type === "github_token") && (
              <>
                <FormControl fullWidth>
                  <InputLabel>Conteúdo</InputLabel>
                  <Select
                    value={form.github_extraction_type || (form.connector_type === "github_billing" ? "billing" : form.connector_type === "github_license" ? "license" : "token")}
                    label="Conteúdo"
                    onChange={(e) => {
                      const t = e.target.value as "billing" | "license" | "token";
                      const connectorType = t === "billing" ? "github_billing" : t === "license" ? "github_license" : "github_token";
                      const cloudName = t === "billing" ? "github-billing" : t === "license" ? "github-license" : "github-token";
                      setForm({ ...form, github_extraction_type: t, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="billing">Faturamento: relatórios de custo (Copilot / Actions / Packages)</MenuItem>
                    <MenuItem value="token">Tokens: consumo por mês</MenuItem>
                    <MenuItem value="license">Licenças: usuários criados</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Organização (identificador)" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Organização (identificador) no GitHub Enterprise." />
                <TextField label="PAT (token de acesso pessoal)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Token com o escopo manage_billing:enterprise" />
              </>
            )}

            {(form.connector_type === "notebooklm_license" || form.connector_type === "notebooklm_token") && (
              <>
                <FormControl fullWidth>
                  <InputLabel>Conteúdo</InputLabel>
                  <Select
                    value={form.gemini_extraction_type || (form.connector_type === "notebooklm_license" ? "license" : "token")}
                    label="Conteúdo"
                    onChange={(e) => {
                      const t = e.target.value as "license" | "token";
                      const connectorType = t === "license" ? "notebooklm_license" : "notebooklm_token";
                      const cloudName = t === "license" ? "notebooklm-license" : "notebooklm-token";
                      setForm({ ...form, gemini_extraction_type: t, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="token">Tokens: consumo por mês</MenuItem>
                    <MenuItem value="license">Licenças: usuários com licença ativa</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Identificador do projeto Google Cloud" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Project ID no GCP (ex: meu-projeto)" />
                <TextField label="Número do projeto (Google)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Número do projeto (Google) no Google Cloud." />
                <TextField label="E-mail do admin do Workspace" value={form.admin_email}
                  onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
                  helperText="Super admin para delegação em todo o domínio (ex.: admin@empresa.com)" />
                <TextField label="Chave da conta de serviço (JSON)" value={form.gcp_token} multiline minRows={4}
                  onChange={(e) => setForm({ ...form, gcp_token: e.target.value })}
                  helperText="Cole o JSON completo da conta de serviço (type=service_account). Chave AIza não lista usuários." />
                <TextField label="Chave de API (se houver)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Não substitui a conta de serviço para licenças e Reports API" />
              </>
            )}

            {(form.connector_type === "gemini_license" || form.connector_type === "gemini_token") && (
              <>
                <FormControl fullWidth>
                  <InputLabel>Conteúdo</InputLabel>
                  <Select
                    value={form.gemini_extraction_type || (form.connector_type === "gemini_license" ? "license" : "token")}
                    label="Conteúdo"
                    onChange={(e) => {
                      const t = e.target.value as "license" | "token";
                      const connectorType = t === "license" ? "gemini_license" : "gemini_token";
                      const cloudName = t === "license" ? "gemini-license" : "gemini-token";
                      setForm({ ...form, gemini_extraction_type: t, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="token">Tokens: consumo por mês</MenuItem>
                    <MenuItem value="license">Licenças: usuários com licença ativa</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Identificador do projeto Google Cloud" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Project ID no GCP (ex: meu-projeto)" />
                <TextField label="Número do projeto (Google)" value={form.client_id}
                  onChange={(e) => setForm({ ...form, client_id: e.target.value })}
                  helperText="Número do projeto (Google) no Google Cloud." />
                <TextField label="E-mail do admin do Workspace" value={form.admin_email}
                  onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
                  helperText="Super admin para delegação em todo o domínio (ex.: admin@empresa.com)" />
                <TextField label="Chave da conta de serviço (JSON)" value={form.gcp_token} multiline minRows={4}
                  onChange={(e) => setForm({ ...form, gcp_token: e.target.value })}
                  helperText="Cole o JSON completo da conta de serviço (type=service_account). Chave AIza não lista usuários." />
                <TextField label="Chave de API (se houver)" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Não substitui a conta de serviço para licenças e Reports API" />
              </>
            )}

            {(form.connector_type === "claude_license" || form.connector_type === "claude_token") && (
              <>
                <FormControl fullWidth>
                  <InputLabel>Conteúdo</InputLabel>
                  <Select
                    value={form.connector_type === "claude_license" ? "license" : "token"}
                    label="Conteúdo"
                    onChange={(e) => {
                      const t = e.target.value as "license" | "token";
                      const connectorType = t === "license" ? "claude_license" : "claude_token";
                      const cloudName = t === "license" ? "claude-license" : "claude-token";
                      setForm({ ...form, connector_type: connectorType, cloud_name: cloudName });
                    }}
                  >
                    <MenuItem value="token">Tokens: consumo de tokens e atividades por mês</MenuItem>
                    <MenuItem value="license">Licenças: colaboradores com licença ativa</MenuItem>
                  </Select>
                </FormControl>
                <TextField label="Organização (identificador)" value={form.tenant_id}
                  onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                  helperText="Organização (identificador) (ex: minha-organizacao)" />
                <TextField label="Chave de API de administrador" type="password" value={form.client_secret}
                  onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                  helperText="Chave de API de admin com read:members (licenças) e api:admin (consumo/tokens). Sem api:admin a licença funciona e o token falha. O arquivo .env-cloude local não alimenta a conexão — cole aqui e salve." />
              </>
            )}

            {form.connector_type === "databricks" && (
              <>
                <TextField label="Host do servidor" value={form.databricks_server_hostname}
                  onChange={(e) => setForm({ ...form, databricks_server_hostname: e.target.value })}
                  helperText="Host do workspace (NÃO é o token). Ex.: adb-7405612109211710.10.azuredatabricks.net" />
                <TextField label="Caminho HTTP do warehouse" value={form.databricks_http_path}
                  onChange={(e) => setForm({ ...form, databricks_http_path: e.target.value })}
                  helperText="Exemplo: /sql/1.0/warehouses/26efc2a4526c691f" />
                <TextField label="Token (opcional)" type="password" value={form.databricks_token}
                  onChange={(e) => setForm({ ...form, databricks_token: e.target.value })}
                  helperText="Preencha para usar uma credencial exclusiva desta conexão. Em branco mantém a credencial do ambiente." />
                <TextField label="Tabela ou nome da fonte" value={form.databricks_table_name}
                  onChange={(e) => setForm({ ...form, databricks_table_name: e.target.value })}
                  helperText="Identifica a fonte dos registros coletados (ex.: system.billing.usage)." />
                <TextField label="Consulta de consumo" multiline rows={4} value={form.databricks_query}
                  onChange={(e) => setForm({ ...form, databricks_query: e.target.value })}
                  helperText="SQL do consumo do workspace. Padrão: SELECT * FROM system.billing.usage. Pode apontar para outra tabela ou view (ex.: system.billing.list_purchases)." />
                <TextField label="Coluna de data (mês de faturamento)" value={form.databricks_month_column}
                  onChange={(e) => setForm({ ...form, databricks_month_column: e.target.value })}
                  helperText="Coluna usada para derivar o período de faturamento (padrão: usage_date)" />
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
          <Button disabled={savingConnection} onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button variant="outlined" startIcon={testingForm ? <CircularProgress size={20} /> : <Validate />} onClick={() => void testFormConnection()} disabled={testingForm || savingConnection}>
            {testingForm ? "Testando…" : form.connector_type === "azure_mca_invoice" ? "Testar acesso e contas" : "Testar conexão"}
          </Button>
          <Button startIcon={<Save />} onClick={() => void save()} variant="contained" disabled={testingForm || savingConnection}>{savingConnection ? "Salvando…" : "Salvar fonte"}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(connectionPendingDeletion)} onClose={() => { if (!deleting) { setConnectionPendingDeletion(null); setDeleteError(""); } }} maxWidth="xs" fullWidth>
        <DialogTitle>Remover conexão?</DialogTitle>
        <DialogContent>
          {deleteError ? (
            <Alert severity="error" sx={{ mb: 2 }}>
              {dataDisplayText(deleteError)}
            </Alert>
          ) : (
            <Typography>
              Você está prestes a remover a conexão <strong>{connectionPendingDeletion?.name}</strong>.
              A tabela de origem associada também será removida. Esta ação não pode ser desfeita.
            </Typography>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={deleting} onClick={() => { setConnectionPendingDeletion(null); setDeleteError(""); }}>Cancelar</Button>
          <Button variant="contained" color="error" disabled={deleting || !!deleteError} onClick={() => void remove()}>
            {deleting ? "Removendo…" : "Remover conexão"}
          </Button>
        </DialogActions>
      </Dialog>


      <Snackbar open={Boolean(deleteFeedback)} autoHideDuration={5000} onClose={() => setDeleteFeedback("")} anchorOrigin={{ vertical: "bottom", horizontal: "right" }}>
        <Alert severity="success" variant="filled" onClose={() => setDeleteFeedback("")}>{dataDisplayText(deleteFeedback)}</Alert>
      </Snackbar>

      <Dialog open={historyConn !== null} onClose={() => setHistoryConn(null)} maxWidth="md" fullWidth>
        <DialogTitle>Log da conexão<Typography variant="body2" color="text.secondary">{historyConn?.name ?? ""}</Typography></DialogTitle>
        <DialogContent>
          {historyConn && <VersionHistorySection><RecordHistory module="connections" entityType="connection" entityId={historyConn.id} /></VersionHistorySection>}
        </DialogContent>
        <DialogActions><Button onClick={() => setHistoryConn(null)}>Fechar histórico</Button></DialogActions>
      </Dialog>
      <SourceSetupWizard connection={setupConnection} onClose={() => { setSetupConnection(null); void loadGrid(); }} />
    </Box>
  );
}
