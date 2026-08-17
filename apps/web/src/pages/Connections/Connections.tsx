import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Typography, Button, Card, CardContent, CardActions, IconButton,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Chip, Alert, CircularProgress,
} from "@mui/material";
import { Add, Edit, Delete, Visibility, Cable, CloudUpload } from "@mui/icons-material";
import {
  getConnections, createConnection, updateConnection,
  deleteConnection, testConnection, SourceConnection,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import ManualCsvUploadDialog from "./ManualCsvUploadDialog";

const CONNECTOR_TYPES = [
  { value: "azure_blob", label: "Azure Blob Storage", scope: "azure" },
  { value: "gcp_bigquery", label: "GCP BigQuery", scope: "gcp" },
  { value: "mongodb_atlas", label: "MongoDB Atlas", scope: "atlas" },
  { value: "manual_csv", label: "Upload manual de CSV", scope: "" },
];

const CLOUD_SCOPES = [
  { value: "azure", label: "Azure" },
  { value: "gcp", label: "GCP" },
  { value: "ibm", label: "IBM Cloud" },
  { value: "aws", label: "AWS" },
  { value: "oci", label: "Oracle Cloud (OCI)" },
  { value: "atlas", label: "MongoDB Atlas" },
  { value: "other", label: "Outra / Desconhecida" },
];

const emptyForm = {
  code: "", name: "", connector_type: "azure_blob", cloud_name: "", cloud_scope: "azure",
  tenant_id: "", client_id: "", client_secret: "",
  storage_account: "", container_name: "", blob_prefixes: "",
  gcp_token: "", table_name: "",
  mongo_group_id: "", mongo_contract_type: "",
};

export default function Connections() {
  const navigate = useNavigate();
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [testMsgs, setTestMsgs] = useState<Record<number, { ok: boolean; severity: "success" | "warning" | "error"; msg: string }>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SourceConnection | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [uploadConn, setUploadConn] = useState<SourceConnection | null>(null);

  const load = async () => {
    try {
      setLoading(true);
      setConnections(await getConnections());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openNew = () => {
    setEditing(null);
    setForm({ ...emptyForm, connector_type: "azure_blob", cloud_scope: "azure" });
    setDialogOpen(true);
  };

  const openEdit = (conn: SourceConnection) => {
    setEditing(conn);
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

  const save = async () => {
    const configJson: Record<string, string> = {};
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
    } else if (form.connector_type === "mongodb_atlas") {
      if (form.tenant_id) configJson.base_url = form.tenant_id;
      if (form.client_id) configJson.public_key = form.client_id;
      if (form.client_secret) configJson.private_key = form.client_secret;
      if (form.mongo_group_id) configJson.group_id = form.mongo_group_id;
      if (form.mongo_contract_type) configJson.contract_type = form.mongo_contract_type;
    }

    const prefixes = form.blob_prefixes.split("\n").map((s) => s.trim()).filter(Boolean);

    const body = {
      code: form.code, name: form.name,
      connector_type: form.connector_type,
      cloud_name: form.cloud_name || null,
      cloud_scope: form.cloud_scope === "other" ? null : (form.cloud_scope || null),
      config_json: configJson,
      blob_prefixes: prefixes,
    };

    try {
      if (editing) await updateConnection(editing.id, body);
      else await createConnection(body as any);
      setDialogOpen(false);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to save");
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Delete this connection?")) return;
    try { await deleteConnection(id); load(); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const maskSecret = (s: string) => s ? s.slice(0, 10) + "..." : "";

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", mb: 3 }}>
        <Typography variant="h4">Conexoes</Typography>
        <Button variant="contained" startIcon={<Add />} onClick={openNew}>
          Nova Conexao
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

      {!loading && connections.length === 0 && (
        <EmptyState message="Nenhuma conexao cadastrada. Clique em 'Nova Conexao' para comecar." />
      )}

      <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))", gap: 2 }}>
        {connections.map((conn) => {
          const cfg = conn.config_json as Record<string, string>;
          return (
            <Card key={conn.id}>
              <CardContent sx={{ pb: 1 }}>
                <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>{conn.name}</Typography>
                  <Chip label={conn.connector_type} size="small" color="primary" variant="outlined" />
                </Box>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  Code: {conn.code}{conn.cloud_name ? ` | Cloud: ${conn.cloud_name}` : ""}
                </Typography>

                {conn.connector_type === "azure_blob" && (
                  <Box sx={{ mt: 1, p: 1.5, bgcolor: "grey.50", borderRadius: 1, fontSize: "0.8rem" }}>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">Storage:</Typography>
                      <Typography variant="caption">{cfg.storage_account || "-"}</Typography>
                    </Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">Container:</Typography>
                      <Typography variant="caption">{cfg.container_name || "-"}</Typography>
                    </Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">Tenant:</Typography>
                      <Typography variant="caption" sx={{ fontFamily: "monospace" }}>{cfg.tenant_id?.slice(0, 16) || "-"}...</Typography>
                    </Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                      <Typography variant="caption" color="text.secondary">Secret:</Typography>
                      <Typography variant="caption" sx={{ fontFamily: "monospace" }}>{maskSecret(cfg.client_secret)}</Typography>
                    </Box>
                  </Box>
                )}

                {conn.connector_type === "gcp_bigquery" && (
                  <Box sx={{ mt: 1, p: 1.5, bgcolor: "grey.50", borderRadius: 1, fontSize: "0.8rem" }}>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">Project:</Typography>
                      <Typography variant="caption">{cfg.project_id || "-"}</Typography>
                    </Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">Dataset:</Typography>
                      <Typography variant="caption">{cfg.dataset_id || "-"}</Typography>
                    </Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                      <Typography variant="caption" color="text.secondary">Token:</Typography>
                      <Typography variant="caption">{(cfg.token_json || cfg.token_path || "").slice(0, 30)}...</Typography>
                    </Box>
                  </Box>
                )}

                {conn.connector_type === "mongodb_atlas" && (
                  <Box sx={{ mt: 1, p: 1.5, bgcolor: "grey.50", borderRadius: 1, fontSize: "0.8rem" }}>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">Contract:</Typography>
                      <Typography variant="caption">{cfg.contract_type || "-"}</Typography>
                    </Box>
                    <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                      <Typography variant="caption" color="text.secondary">Group:</Typography>
                      <Typography variant="caption">{cfg.group_id || "-"}</Typography>
                    </Box>
                  </Box>
                )}

                <Box sx={{ mt: 1 }}>
                  {(conn.blob_prefixes || []).slice(0, 3).map((p) => (
                    <Chip key={p} label={p} size="small" sx={{ mr: 0.5, mb: 0.5 }} />
                  ))}
                </Box>
              </CardContent>
              <CardActions>
                <IconButton onClick={() => navigate(`/explorer/${conn.id}`)} title="Explorer">
                  <Visibility />
                </IconButton>
                <IconButton onClick={() => openEdit(conn)} title="Editar credenciais">
                  <Edit />
                </IconButton>
                {conn.connector_type === "manual_csv" ? (
                  <IconButton onClick={() => setUploadConn(conn)} title="Upload CSV" color="primary">
                    <CloudUpload />
                  </IconButton>
                ) : (
                  <IconButton
                    onClick={async () => {
                      setTestingId(conn.id);
                      setTestMsgs(prev => ({ ...prev, [conn.id]: { ok: false, severity: "error" as const, msg: "" } }));
                      try {
                        const r = await testConnection(conn.id);
                        const isOk = r.status === "OK";
                        const isPending = r.status === "PENDING";
                        setTestMsgs(prev => ({
                          ...prev,
                          [conn.id]: {
                            ok: isOk || isPending,
                            severity: isOk ? "success" : isPending ? "warning" : "error",
                            msg: isOk ? `OK: ${r.message}` : isPending ? `Aguardando: ${r.message}` : `Erro: ${r.message}`,
                          },
                        }));
                      } catch (e: any) {
                        setTestMsgs(prev => ({ ...prev, [conn.id]: { ok: false, severity: "error", msg: `Erro: ${e.message}` } }));
                      }
                      setTestingId(null);
                    }}
                    disabled={testingId === conn.id}
                    title="Testar Conexao"
                  >
                    <Cable color={testingId === conn.id ? "disabled" : "action"} />
                  </IconButton>
                )}
                <IconButton onClick={() => remove(conn.id)} color="error" title="Remover">
                  <Delete />
                </IconButton>
              </CardActions>
              {testMsgs[conn.id] && (
                <Alert
                  severity={testMsgs[conn.id].severity}
                  sx={{ mx: 1, mb: 1 }}
                  onClose={() => setTestMsgs(prev => {
                    const next = { ...prev };
                    delete next[conn.id];
                    return next;
                  })}
                >{testMsgs[conn.id].msg}</Alert>
              )}
            </Card>
          );
        })}
      </Box>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? "Editar Conexao" : "Nova Conexao"}</DialogTitle>
        <DialogContent>
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
              </>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={save} variant="contained">Salvar</Button>
        </DialogActions>
      </Dialog>

      {uploadConn && (
        <ManualCsvUploadDialog
          open={!!uploadConn}
          onClose={() => setUploadConn(null)}
          connectionId={uploadConn.id}
          connectionName={uploadConn.name}
        />
      )}
    </Box>
  );
}
