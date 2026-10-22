import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import React, { useEffect, useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, FormControl, FormControlLabel, IconButton, InputLabel, MenuItem, Paper, Select, Stack, Switch, TextField, Tooltip, Typography } from "@mui/material";
import { AddOutlined as AddIcon, RefreshOutlined as RefreshIcon, ScienceOutlined as ScienceIcon } from "@mui/icons-material";
import { api } from "../../services/api";
import { RowAction } from "../../components/RowAction";

interface SsoProvider {
  id: number;
  name: string;
  protocol: "oidc" | "saml";
  enabled: boolean;
  config_json: Record<string, unknown>;
  created_at?: string;
}

const OIDC_FIELDS: Array<{ key: string; label: string; secret?: boolean; help?: string }> = [
  { key: "issuer", label: "Emissor (issuer)" },
  { key: "discovery_url", label: "URL de discovery (se diferente de issuer/.well-known/openid-configuration)" },
  { key: "client_id", label: "ID do cliente (Client ID)" },
  { key: "client_secret", label: "Segredo do cliente (Client secret)", secret: true },
  { key: "redirect_uri", label: "URI de redirecionamento (callback)" },
  { key: "username_domain", label: "Domínio corporativo (login_hint)", help: "Opcional. Quando preenchido, a tela aceita apenas o usuário e substitui qualquer domínio informado por este domínio." },
  { key: "scopes", label: "Escopos (padrão: openid profile email)" },
  { key: "groups_claim", label: "Claim de grupos (padrão: groups)" },
];

const SAML_FIELDS: Array<{ key: string; label: string; secret?: boolean; help?: string }> = [
  { key: "idp_entity_id", label: "Entity ID do provedor de identidade (IdP)" },
  { key: "idp_sso_url", label: "URL de login único do IdP" },
  { key: "idp_slo_url", label: "URL de logout único do IdP (opcional)" },
  { key: "x509_cert", label: "Certificado X.509 do provedor" },
  { key: "sp_entity_id", label: "Entity ID da aplicação (opcional)" },
  { key: "acs_url", label: "URL do ACS da aplicação (opcional)" },
  { key: "groups_attribute", label: "Atributo de grupos (padrão: groups)" },
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

  const load = () => api.get<SsoProvider[]>("/administracao/identidade/provedores").then((r) => setProviders(r.data)).catch((e) => setError(String(e)));
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
    if (!name.trim()) { setError("Dê um nome ao provedor"); return; }
    const body = { name: name.trim(), protocol, enabled: editing?.enabled ?? false, config_json: { ...cfg, auto_create_groups: autoCreate } };
    try {
      if (editing) {
        await api.put(`/administracao/identidade/provedores/${editing.id}`, {
          name: body.name, enabled: body.enabled, config_json: body.config_json,
        });
      } else {
        await api.post("/administracao/identidade/provedores", body);
      }
      setOpen(false);
      load();
      setMsg("Provedor salvo. O segredo do cliente é cifrado e não é exibido novamente.");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Não foi possível salvar o provedor");
    }
  };

  const toggleEnabled = async (p: SsoProvider) => {
    try {
      await api.put(`/administracao/identidade/provedores/${p.id}`, { enabled: !p.enabled });
      load();
    } catch (e: any) { setError(e?.response?.data?.detail || "Não foi possível alterar o status"); }
  };

  const remove = async (p: SsoProvider) => {
    try { await api.delete(`/administracao/identidade/provedores/${p.id}`); load(); }
    catch (e: any) { setError(e?.response?.data?.detail || "Não foi possível excluir"); }
  };

  const runTest = async (p: SsoProvider) => {
    setTesting(p.id); setTestResult(null); setError(null);
    try {
      const r = await api.post(`/administracao/identidade/provedores/${p.id}/testar`);
      setTestResult(`${p.name}: ${r.data.ok ? "OK — " : "Erro — "}${r.data.message}`);
    } catch (e: any) {
      setTestResult(`${p.name}: erro — ${e?.response?.data?.detail || e}`);
    } finally {
      setTesting(null);
    }
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#FAFBFD" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Provedores de login único</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          OIDC e SAML2 corporativos para login único. Segredos são cifrados e nunca reaparecem depois de salvos.
        </Typography>
      </Box>
      <Box sx={{ p: 3 }}>
        <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h6">Provedores</Typography>
        <Box flex={1} />
        <Button size="small" variant="contained" startIcon={<AddIcon />} onClick={openCreate}>Incluir provedor</Button>
        <IconButton size="small" onClick={load}><RefreshIcon /></IconButton>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
      {msg && <Alert severity="success" sx={{ mb: 2 }}>{dataDisplayText(msg)}</Alert>}
      {testResult && <Alert severity="info" sx={{ mb: 2 }}>{testResult}</Alert>}

      {providers.length === 0 && <Typography color="text.secondary">Nenhum provedor de login configurado.</Typography>}
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
            <FormControlLabel control={<Switch checked={p.enabled} onChange={() => toggleEnabled(p)} />} label="Habilitado" />
            <Box flex={1} />
            <Tooltip title="Testar conexão (valida o discovery OIDC)">
              <IconButton size="small" disabled={testing === p.id} onClick={() => runTest(p)}><ScienceIcon fontSize="small" /></IconButton>
            </Tooltip>
            <Button size="small" variant="outlined" onClick={() => openEdit(p)}>Editar</Button>
            <RowAction kind="delete" onClick={() => remove(p)} />
          </Paper>
        ))}
      </Stack>
      </Paper>
      </Box>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? `Editar ${editing.name}` : "Incluir provedor de login"}</DialogTitle>
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
              <Alert severity="info">SAML exige <code>xmlsec1</code> + <code>python3-saml</code> no servidor. A metadata do SP fica em <code>/auth/sso/metadata</code>.</Alert>
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
              label="Criar automaticamente os grupos vindos do IdP (sem perfis até associação manual)" />
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
