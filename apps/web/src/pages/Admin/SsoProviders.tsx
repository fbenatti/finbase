import React, { useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, IconButton, InputLabel, MenuItem, Paper, Select, Stack,
  Switch, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add as AddIcon, Delete as DeleteIcon, Refresh as RefreshIcon, Science as ScienceIcon } from "@mui/icons-material";
import { api } from "../../services/api";

interface SsoProvider {
  id: number;
  name: string;
  protocol: "oidc" | "saml";
  enabled: boolean;
  config_json: Record<string, unknown>;
  created_at?: string;
}

const OIDC_FIELDS: Array<{ key: string; label: string; secret?: boolean; help?: string }> = [
  { key: "issuer", label: "Issuer (autoridade)" },
  { key: "discovery_url", label: "Discovery URL (se diferente do issuer/.well-known/openid-configuration)" },
  { key: "client_id", label: "Client ID" },
  { key: "client_secret", label: "Client secret", secret: true },
  { key: "redirect_uri", label: "Redirect URI (callback)" },
  { key: "scopes", label: "Scopes (default: openid profile email)" },
  { key: "groups_claim", label: "Claim de grupos (default: groups)" },
];

const SAML_FIELDS: Array<{ key: string; label: string; secret?: boolean; help?: string }> = [
  { key: "idp_entity_id", label: "Entity ID do IdP" },
  { key: "idp_sso_url", label: "SSO URL do IdP" },
  { key: "idp_slo_url", label: "SLO URL do IdP (opcional)" },
  { key: "x509_cert", label: "Certificado X.509 do IdP" },
  { key: "sp_entity_id", label: "Entity ID do SP (opcional)" },
  { key: "acs_url", label: "ACS URL do SP (opcional)" },
  { key: "groups_attribute", label: "Atributo de grupos (default: groups)" },
];

export default function SsoProviders() {
  const [providers, setProviders] = useState<SsoProvider[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<SsoProvider | null>(null);
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<"oidc" | "saml">("oidc");
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [autoCreate, setAutoCreate] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  const load = () => api.get<SsoProvider[]>("/sso/providers").then((r) => setProviders(r.data)).catch((e) => setError(String(e)));
  useEffect(() => { load(); }, []);

  const fields = protocol === "oidc" ? OIDC_FIELDS : SAML_FIELDS;

  const resetForm = (p?: SsoProvider) => {
    setEditing(p ?? null);
    setName(p?.name ?? "");
    setProtocol(p?.protocol ?? "oidc");
    const initial: Record<string, string> = {};
    (p ? (p.protocol === "oidc" ? OIDC_FIELDS : SAML_FIELDS) : []).forEach((f) => {
      const v = p?.config_json?.[f.key];
      if (typeof v === "string") initial[f.key] = v;
    });
    setCfg(initial);
    setAutoCreate(Boolean(p?.config_json?.auto_create_groups ?? true));
    setError(null); setMsg(null); setTestResult(null);
  };

  const openCreate = () => { resetForm(); setOpen(true); };
  const openEdit = (p: SsoProvider) => { resetForm(p); setOpen(true); };

  const save = async () => {
    setError(null);
    if (!name.trim()) { setError("Informe um nome para o provedor"); return; }
    const body = { name: name.trim(), protocol, enabled: editing?.enabled ?? false, config_json: { ...cfg, auto_create_groups: autoCreate } };
    try {
      if (editing) {
        await api.put(`/sso/providers/${editing.id}`, {
          name: body.name, enabled: body.enabled, config_json: body.config_json,
        });
      } else {
        await api.post("/sso/providers", body);
      }
      setOpen(false);
      load();
      setMsg("Provedor salvo. O client secret é cifrado e nunca é exibido novamente.");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao salvar provedor");
    }
  };

  const toggleEnabled = async (p: SsoProvider) => {
    try {
      await api.put(`/sso/providers/${p.id}`, { enabled: !p.enabled });
      load();
    } catch (e: any) { setError(e?.response?.data?.detail || "Falha ao alterar estado"); }
  };

  const remove = async (p: SsoProvider) => {
    try { await api.delete(`/sso/providers/${p.id}`); load(); }
    catch (e: any) { setError(e?.response?.data?.detail || "Falha ao excluir"); }
  };

  const runTest = async (p: SsoProvider) => {
    setTesting(p.id); setTestResult(null); setError(null);
    try {
      const r = await api.post(`/sso/providers/${p.id}/test`);
      setTestResult(`${p.name}: ${r.data.ok ? "OK — " : "Falha — "}${r.data.message}`);
    } catch (e: any) {
      setTestResult(`${p.name}: erro — ${e?.response?.data?.detail || e}`);
    } finally {
      setTesting(null);
    }
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Provedores SSO</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          OIDC e SAML2 corporativos para login único. Segredos são cifrados e nunca reaparecem depois de salvos.
        </Typography>
      </Box>
      <Box sx={{ p: 3 }}>
        <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h6">Provedores</Typography>
        <Box flex={1} />
        <Button size="small" variant="contained" startIcon={<AddIcon />} onClick={openCreate}>Novo provedor</Button>
        <IconButton size="small" onClick={load}><RefreshIcon /></IconButton>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {msg && <Alert severity="success" sx={{ mb: 2 }}>{msg}</Alert>}
      {testResult && <Alert severity="info" sx={{ mb: 2 }}>{testResult}</Alert>}

      {providers.length === 0 && <Typography color="text.secondary">Nenhum provedor configurado.</Typography>}
      <Stack spacing={1}>
        {providers.map((p) => (
          <Paper key={p.id} variant="outlined" sx={{ p: 2, display: "flex", alignItems: "center", gap: 2 }}>
            <Chip size="small" label={p.protocol.toUpperCase()} color={p.protocol === "oidc" ? "primary" : "secondary"} variant="outlined" />
            <Box sx={{ minWidth: 180 }}>
              <Typography variant="body2" fontWeight={600}>{p.name}</Typography>
              <Typography variant="caption" color="text.secondary">
                {p.config_json.secrets_configured ? "segredos configurados" : "sem segredos"} ·{" "}
                {p.protocol === "oidc" ? (p.config_json.issuer as string) || "sem issuer" : (p.config_json.idp_entity_id as string) || "sem entity id"}
              </Typography>
            </Box>
            <FormControlLabel control={<Switch checked={p.enabled} onChange={() => toggleEnabled(p)} />} label="Ativo" />
            <Box flex={1} />
            <Tooltip title="Testar (valida discovery OIDC)">
              <IconButton size="small" disabled={testing === p.id} onClick={() => runTest(p)}><ScienceIcon fontSize="small" /></IconButton>
            </Tooltip>
            <Button size="small" variant="outlined" onClick={() => openEdit(p)}>Editar</Button>
            <IconButton size="small" color="error" onClick={() => remove(p)}><DeleteIcon fontSize="small" /></IconButton>
          </Paper>
        ))}
      </Stack>
      </Paper>
      </Box>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? `Editar ${editing.name}` : "Novo provedor SSO"}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField size="small" label="Nome (ex.: Entra ID)" value={name} onChange={(e) => setName(e.target.value)} />
            <FormControl size="small" fullWidth>
              <InputLabel>Protocolo</InputLabel>
              <Select label="Protocolo" value={protocol} onChange={(e) => { setProtocol(e.target.value as "oidc" | "saml"); setCfg({}); }}>
                <MenuItem value="oidc">OIDC (OpenID Connect)</MenuItem>
                <MenuItem value="saml">SAML2</MenuItem>
              </Select>
            </FormControl>
            {protocol === "saml" && (
              <Alert severity="info">SAML requer <code>xmlsec1</code> + <code>python3-saml</code> no servidor. A metadata do SP fica em <code>/auth/sso/metadata</code>.</Alert>
            )}
            {fields.map((f) => (
              <TextField
                key={f.key}
                size="small" fullWidth
                label={f.label + (f.secret ? " (deixe vazio para manter o atual)" : "")}
                value={cfg[f.key] ?? ""}
                type={f.secret ? "password" : "text"}
                onChange={(e) => setCfg((prev) => ({ ...prev, [f.key]: e.target.value }))}
                slotProps={{ inputLabel: { shrink: true } }}
              />
            ))}
            <FormControlLabel control={<Checkbox checked={autoCreate} onChange={(e) => setAutoCreate(e.target.checked)} />}
              label="Auto-criar grupos externos vindos do IdP (ficam sem roles até associação manual)" />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancelar</Button>
          <Button variant="contained" onClick={save}>Salvar</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
