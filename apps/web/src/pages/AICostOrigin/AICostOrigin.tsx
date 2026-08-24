import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Box, Button, Chip, Divider, FormControl, FormControlLabel, Grid, InputLabel,
  Link, List, ListItem, ListItemButton, ListItemText, MenuItem, Paper, Select, Stack,
  Switch, Tab, Tabs, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add, Link as LinkIcon, PlayArrow, Save } from "@mui/icons-material";
import {
  AICostLink, AICostLinkSource, AIUnlinkedLicense, applyAICostAllocation,
  formatApiError, getAICostLinks, getAICostPtuConflicts, getAIUnlinkedLicenses,
  previewAICostFilter, previewAICostPtu, previewAIManualEntry, saveAICostLink,
} from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

// ── Colunas de valor disponíveis ──────────────────────────────────────────────
// TotalCostOnDemandBRL = "Realizado" — custo com taxas e impostos (BRL enriquecido),
// é a coluna que alimenta os dashboards de FinOps.
const VALUE_COLUMN_OPTIONS = [
  { value: "BilledCost", label: "BilledCost" },
  { value: "EffectiveCost", label: "EffectiveCost" },
  { value: "realizado", label: "realizado" },
] as const;

// ── Operadores de filtro disponíveis ──────────────────────────────────────────
const OPERATOR_OPTIONS = [
  { value: "CONTAINS",     label: "Contém" },
  { value: "IN",           label: "Igual a (IN)" },
  { value: "=",            label: "Igual exato (=)" },
  { value: "NOT_CONTAINS", label: "Não contém" },
  { value: "NOT_IN",       label: "Diferente (NOT IN)" },
  { value: "NOT_EMPTY",    label: "Preenchido (NOT EMPTY)" },
] as const;

// ── Guia de campos por cloud ──────────────────────────────────────────────────
// Anthropic (Claude) NÃO possui billing integrado às clouds — use custo unitário por licença.
// GCP: Gemini / NotebookLM  |  Azure: GitHub Copilot
const CLOUD_FIELD_HINTS = [
  { cloud: "GCP",   field: "ServiceName",  example: "Vertex AI API",                note: "Gemini / NotebookLM" },
  { cloud: "GCP",   field: "ServiceName",  example: "AI Platform",                  note: "Gemini tokens" },
  { cloud: "GCP",   field: "x_Project",    example: "telefonica-notebook-lm",        note: "Filtro de projeto NotebookLM" },
  { cloud: "Azure", field: "ServiceName",  example: "GitHub",                       note: "GitHub Copilot" },
  { cloud: "Azure", field: "SkuName",      example: "GitHub Copilot Business",      note: "SKU exato do Copilot" },
  { cloud: "Azure", field: "ChargeDescription", example: "GitHub Copilot",          note: "Descrição da cobrança" },
  { cloud: "Azure", field: "skumeter",         example: "Provisioned Managed Global Unit", note: "PTU reservado (global)" },
  { cloud: "Azure", field: "skumeter",         example: "Provisioned Managed Unit",        note: "PTU regional / OnDemand" },
  { cloud: "Azure", field: "departamento",     example: "Vivo Mais",                       note: "Pool PTU por departamento" },
  { cloud: "Azure", field: "ReservationName",  example: "(preenchido)",                    note: "Reserva vs OnDemand (NOT_EMPTY/EMPTY)" },
];

const emptySource = (): AICostLinkSource => ({
  filter_json: [{ column: "ServiceName", operator: "CONTAINS", values: [""] }],
  value_column: "realizado",   // ← Realizado por padrão
  percent: 100,
});

const emptyLink = (): Partial<AICostLink> => ({
  name: "",
  provider: "",
  product_name: "",
  target_type: "license_pool",
  license_sku: "",
  active: true,
  distribution_enabled: false,
  distribution_basis: "tokens",
  distribution_user_key: "email",
  unit_cost: null,
  sources: [emptySource()],
});

export default function AICostOrigin({ embedded }: { embedded?: boolean } = {}) {
  const [period, setPeriod] = useState("202608");
  const [links, setLinks] = useState<AICostLink[]>([]);
  const [unlinked, setUnlinked] = useState<AIUnlinkedLicense[]>([]);
  const [selected, setSelected] = useState<Partial<AICostLink>>(emptyLink());
  const [preview, setPreview] = useState<{ matched_rows: number; total_value: number } | null>(null);
  const [manualPreview, setManualPreview] = useState<{ active_license_count: number; total_cost: number } | null>(null);
  const [unitCost, setUnitCost] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [poolTab, setPoolTab] = useState(0);
  const [ptuPreview, setPtuPreview] = useState<any>(null);
  const [ptuConflicts, setPtuConflicts] = useState<any[]>([]);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [linkRows, unlinkedRows, conflictRows] = await Promise.all([
        getAICostLinks().catch((e) => {
          console.warn("[AICostOrigin] Falha ao carregar links:", e);
          return [] as AICostLink[];
        }),
        getAIUnlinkedLicenses(period).catch(async (e) => {
          console.warn("[AICostOrigin] Falha com período específico, tentando sem filtro:", e);
          return getAIUnlinkedLicenses().catch(() => [] as AIUnlinkedLicense[]);
        }),
        getAICostPtuConflicts().catch(() => ({ conflicts: [] })),
      ]);
      console.log("[AICostOrigin] links carregados:", linkRows.length, "| unlinked:", unlinkedRows.length);
      setLinks(linkRows);
      setUnlinked(unlinkedRows);
      setPtuConflicts(conflictRows.conflicts || []);
    } catch (e) {
      setError(formatApiError(e, "Não foi possível carregar o portal de origem de custo"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [period]);

  const selectUnlinked = (item: AIUnlinkedLicense) => {
    console.log("[AICostOrigin] selecionando licença não vinculada:", item.provider, item.product_name);
    setSelected({
      ...emptyLink(),
      provider: item.provider,
      product_name: item.product_name,
      license_sku: item.license_sku,
      name: `${item.provider} · ${item.product_name}`,
    });
    setUnitCost("");
    setPreview(null);
    setManualPreview(null);
  };

  const selectLink = (link: AICostLink) => {
    console.log("[AICostOrigin] selecionando vínculo salvo:", link.id, link.name, "| unit_cost:", link.unit_cost);
    setSelected({ ...link, sources: link.sources?.length ? link.sources : [emptySource()] });
    // Recarrega o unit_cost salvo no vínculo
    setUnitCost(link.unit_cost != null ? String(link.unit_cost) : "");
    setPreview(null);
    setManualPreview(null);
  };

  const updateSource = (index: number, patch: Partial<AICostLinkSource>) => {
    const sources = [...(selected.sources || [])];
    sources[index] = { ...sources[index], ...patch };
    setSelected({ ...selected, sources });
  };

  /** Atualiza uma condição dentro do filter_json de uma origem */
  const updateSourceFilter = (
    sourceIndex: number,
    filterIndex: number,
    patch: Partial<{ column: string; operator: string; values: string[] }>,
  ) => {
    const sources = [...(selected.sources || [])];
    const filters = [...(sources[sourceIndex].filter_json || [])];
    filters[filterIndex] = { ...filters[filterIndex], ...patch };
    sources[sourceIndex] = { ...sources[sourceIndex], filter_json: filters };
    setSelected({ ...selected, sources });
  };

  const runPreview = async () => {
    if (!selected.sources?.length) return;
    console.log("[AICostOrigin] executando preview filtro, período:", period);
    try {
      if ((selected.target_type || "license_pool") === "ptu_pool") {
        const result = await previewAICostPtu({
          period,
          sources: selected.sources,
          project_key: selected.project_key || undefined,
          purchase_mode: selected.purchase_mode || undefined,
          deployment_type: selected.deployment_type || undefined,
        });
        setPtuPreview(result);
        setPreview({ matched_rows: result.focus.matched_rows, total_value: result.focus.realized_cost });
      } else {
        const result = await previewAICostFilter({ period, sources: selected.sources });
        setPreview({ matched_rows: result.matched_rows, total_value: result.total_value });
        setPtuPreview(null);
      }
    } catch (e) {
      setError(formatApiError(e, "Preview indisponível — configure o backend ai_cost_origin"));
    }
  };

  const runManualPreview = async () => {
    const cost = Number(unitCost);
    if (!selected.provider || !selected.product_name || !cost) return;
    console.log("[AICostOrigin] preview custo unitário:", cost, "| provider:", selected.provider);
    try {
      const result = await previewAIManualEntry({
        provider: selected.provider,
        product_name: selected.product_name,
        period,
        unit_cost: cost,
        license_sku: selected.license_sku || undefined,
      });
      setManualPreview({ active_license_count: result.active_license_count, total_cost: result.total_cost });
      console.log("[AICostOrigin] preview unitário resultado:", result);
    } catch (e) {
      setError(formatApiError(e, "Preview de custo unitário indisponível"));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // Propaga o unit_cost digitado para o payload do vínculo
      const payload: Partial<AICostLink> = {
        ...selected,
        unit_cost: unitCost !== "" ? Number(unitCost) : null,
      };
      console.log("[AICostOrigin] salvando vínculo:", payload.name, "| unit_cost:", payload.unit_cost);
      const saved = await saveAICostLink(payload);
      setSelected(saved);
      setUnitCost(saved.unit_cost != null ? String(saved.unit_cost) : "");
      await load();
    } catch (e) {
      setError(formatApiError(e, "Não foi possível salvar o vínculo"));
    } finally {
      setSaving(false);
    }
  };

  const handleApply = async () => {
    if (!selected.id) return;
    try {
      console.log("[AICostOrigin] aplicando rateio, link_id:", selected.id, "| período:", period);
      await applyAICostAllocation({ link_id: selected.id, period });
      await load();
    } catch (e) {
      setError(formatApiError(e, "Apply indisponível"));
    }
  };

  return (
    <Box sx={{ mx: embedded ? 0 : -3, mt: embedded ? 0 : -3, pb: embedded ? 0 : 3, minHeight: embedded ? undefined : "calc(100vh - 64px)", bgcolor: embedded ? "transparent" : "#f8fafc" }}>
      {!embedded && (
        <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
          <Typography variant="h4">Relacionamento Origem de Custo IA</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
            Vincule licenças de IA às linhas de fato do FOCUS ou cadastre custo unitário manual (ex.: licença Claude).
          </Typography>
        </Box>
      )}

      <Box sx={{ p: embedded ? 0 : 3 }}>
        <Stack direction="row" gap={1} mb={2} alignItems="center">
          <TextField label="Período (YYYYMM)" size="small" value={period} onChange={e => setPeriod(e.target.value)} sx={{ width: 160 }} />
          <Button component={RouterLink} to="/dashboards-ia" variant="outlined" size="small">Ver dashboards</Button>
          <Button component={RouterLink} to="/azure-ptu" variant="outlined" size="small">Azure PTU</Button>
        </Stack>
        <Tabs value={poolTab} onChange={(_, v) => setPoolTab(v)} sx={{ mb: 2 }}>
          <Tab label="Licenças de IA" />
          <Tab label="Pools Azure PTU" />
          <Tab label="PAYG / Spillover" />
          <Tab label="Conflitos" />
        </Tabs>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

        {poolTab === 3 && ptuConflicts.length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {ptuConflicts.length} vínculo(s) PTU com conflito de medidor ou tipo de deployment.
          </Alert>
        )}

        <Grid container spacing={2}>
          {/* ── Coluna esquerda: licenças ─────────────────────────────────── */}
          <Grid item xs={12} md={3}>
            <Paper elevation={0} sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2, minHeight: 480 }}>
              <Typography variant="h6" gutterBottom>
                {poolTab === 3 ? "Conflitos PTU" : "Licenças detectadas"}
              </Typography>
              {poolTab === 3 ? (
                <List dense disablePadding>
                  {ptuConflicts.map(item => (
                    <ListItem key={item.link_id} sx={{ borderRadius: 1, mb: 0.5, bgcolor: "#FEE2E2" }}>
                      <ListItemText
                        primary={item.name}
                        secondary={`${item.project_key || "—"} · ${item.conflicts.join(", ")}${item.meters?.length ? ` · medidores: ${item.meters.join(", ")}` : ""}`}
                      />
                      <Chip size="small" color="error" label="Conflito" />
                    </ListItem>
                  ))}
                  {ptuConflicts.length === 0 && !loading && (
                    <ListItem><ListItemText secondary="Nenhum conflito PTU detectado nos vínculos salvos." /></ListItem>
                  )}
                </List>
              ) : (
              <>
              <Typography variant="caption" color="text.secondary" display="block" mb={1}>
                Pools sem origem amarrada aparecem em destaque.
              </Typography>
              <List dense disablePadding>
                {unlinked.map(item => (
                  <ListItemButton
                    key={`${item.provider}-${item.product_name}-${item.license_sku}`}
                    selected={selected.provider === item.provider && selected.product_name === item.product_name}
                    onClick={() => selectUnlinked(item)}
                    sx={{ borderRadius: 1, mb: 0.5, bgcolor: item.has_link ? undefined : "#FEF3C7" }}
                  >
                    <ListItemText
                      primary={`${item.provider} · ${item.product_name}`}
                      secondary={`${item.license_count} licenças${item.license_sku ? ` · ${item.license_sku}` : ""}`}
                    />
                    {!item.has_link && <Chip size="small" color="warning" label="Sem origem" />}
                  </ListItemButton>
                ))}
                {unlinked.length === 0 && !loading && (
                  <ListItem><ListItemText secondary="Nenhuma licença detectada no período." /></ListItem>
                )}
              </List>
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2" gutterBottom>Vínculos salvos</Typography>
              <List dense disablePadding>
                {links.filter(link => {
                  const t = link.target_type || "license_pool";
                  if (poolTab === 1) return t === "ptu_pool";
                  if (poolTab === 2) return t === "payg_ai";
                  return t === "license_pool";
                }).map(link => (
                  <ListItemButton key={link.id} selected={selected.id === link.id} onClick={() => selectLink(link)}>
                    <LinkIcon fontSize="small" sx={{ mr: 1, opacity: 0.6 }} />
                    <ListItemText
                      primary={link.name}
                      secondary={
                        link.unit_cost != null
                          ? `${link.provider} · ${link.product_name} · R$ ${link.unit_cost.toLocaleString("pt-BR")}/lic`
                          : `${link.provider} · ${link.product_name}`
                      }
                    />
                  </ListItemButton>
                ))}
              </List>
              </>
              )}
            </Paper>
          </Grid>

          {/* ── Coluna central: origens FOCUS ────────────────────────────── */}
          <Grid item xs={12} md={5}>
            <Paper elevation={0} sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2, minHeight: 480 }}>
              <Typography variant="h6" gutterBottom>Origens de custo (FOCUS)</Typography>
              <Stack spacing={1.5} mb={2}>
                <TextField label="Nome do vínculo" size="small" value={selected.name || ""} onChange={e => setSelected({ ...selected, name: e.target.value })} />
                <Stack direction="row" gap={1}>
                  <TextField label="Provedor" size="small" value={selected.provider || ""} onChange={e => setSelected({ ...selected, provider: e.target.value })} fullWidth />
                  <TextField label="Produto" size="small" value={selected.product_name || ""} onChange={e => setSelected({ ...selected, product_name: e.target.value })} fullWidth />
                </Stack>
                <FormControl size="small" fullWidth>
                  <InputLabel>Tipo de pool</InputLabel>
                  <Select
                    label="Tipo de pool"
                    value={selected.target_type || "license_pool"}
                    onChange={e => setSelected({ ...selected, target_type: e.target.value as AICostLink["target_type"] })}
                  >
                    <MenuItem value="license_pool">Licenças de IA</MenuItem>
                    <MenuItem value="ptu_pool">Azure OpenAI PTU</MenuItem>
                    <MenuItem value="payg_ai">PAYG / Spillover</MenuItem>
                  </Select>
                </FormControl>
                {(selected.target_type || "license_pool") === "ptu_pool" && (
                  <Stack spacing={1}>
                    <Stack direction="row" gap={1}>
                      <TextField label="Projeto (project_key)" size="small" fullWidth value={selected.project_key || ""} onChange={e => setSelected({ ...selected, project_key: e.target.value })} placeholder="vivo-mais" />
                      <FormControl size="small" fullWidth>
                        <InputLabel>Modo compra</InputLabel>
                        <Select label="Modo compra" value={selected.purchase_mode || ""} onChange={e => setSelected({ ...selected, purchase_mode: e.target.value })}>
                          <MenuItem value="reservation">Reserva</MenuItem>
                          <MenuItem value="ondemand">OnDemand</MenuItem>
                        </Select>
                      </FormControl>
                    </Stack>
                    <Stack direction="row" gap={1}>
                      <FormControl size="small" fullWidth>
                        <InputLabel>Tipo deployment</InputLabel>
                        <Select label="Tipo deployment" value={selected.deployment_type || ""} onChange={e => setSelected({ ...selected, deployment_type: e.target.value })}>
                          <MenuItem value="global">Global</MenuItem>
                          <MenuItem value="regional">Regional</MenuItem>
                          <MenuItem value="data_zone">Data Zone</MenuItem>
                        </Select>
                      </FormControl>
                      <FormControl size="small" fullWidth>
                        <InputLabel>Nível alocação</InputLabel>
                        <Select label="Nível alocação" value={selected.allocation_level || "project"} onChange={e => setSelected({ ...selected, allocation_level: e.target.value })}>
                          <MenuItem value="project">Projeto</MenuItem>
                          <MenuItem value="deployment">Deployment</MenuItem>
                          <MenuItem value="subproject">Subprojeto</MenuItem>
                        </Select>
                      </FormControl>
                    </Stack>
                  </Stack>
                )}
              </Stack>

              {(selected.sources || []).map((source, index) => (
                <Paper key={index} variant="outlined" sx={{ p: 1.5, mb: 1.5 }}>
                  <Typography variant="caption" color="text.secondary">Origem {index + 1}</Typography>
                  <Stack spacing={1} mt={1}>
                    {/* Filtros da origem */}
                    {(source.filter_json || []).map((cond, fi) => (
                      <Stack key={fi} direction="row" gap={1} alignItems="flex-start">
                        {/* Campo FOCUS */}
                        <TextField
                          label="Campo FOCUS"
                          size="small"
                          value={cond.column || "ServiceName"}
                          sx={{ flex: 2 }}
                          onChange={e => updateSourceFilter(index, fi, { column: e.target.value })}
                          helperText={
                            <Tooltip
                              title={
                                <Box>
                                  {CLOUD_FIELD_HINTS.map(h => (
                                    <Box key={`${h.cloud}-${h.field}`} sx={{ fontSize: 11 }}>
                                      <b>{h.cloud}</b> → {h.field}: "{h.example}"
                                    </Box>
                                  ))}
                                </Box>
                              }
                            >
                              <span style={{ cursor: "help", textDecoration: "underline dotted" }}>
                                Campos Azure/GCP ↗
                              </span>
                            </Tooltip>
                          }
                        />
                        {/* Operador */}
                        <FormControl size="small" sx={{ flex: 1.5 }}>
                          <InputLabel>Operador</InputLabel>
                          <Select
                            label="Operador"
                            value={cond.operator || "CONTAINS"}
                            onChange={e => updateSourceFilter(index, fi, { operator: e.target.value })}
                          >
                            {OPERATOR_OPTIONS.map(op => (
                              <MenuItem key={op.value} value={op.value}>{op.label}</MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                        {/* Valor */}
                        <TextField
                          label="Valor do filtro"
                          size="small"
                          sx={{ flex: 2 }}
                          value={(cond.values || [""])[0] || ""}
                          disabled={cond.operator === "NOT_EMPTY" || cond.operator === "IS_NOT_EMPTY"}
                          onChange={e => updateSourceFilter(index, fi, { values: [e.target.value] })}
                        />
                      </Stack>
                    ))}

                    {/* Coluna de valor + percentual */}
                    <Stack direction="row" gap={1}>
                      <FormControl size="small" fullWidth>
                        <InputLabel>Coluna de valor</InputLabel>
                        <Select
                          label="Coluna de valor"
                          value={source.value_column || "realizado"}
                          onChange={e => updateSource(index, { value_column: e.target.value })}
                        >
                          {VALUE_COLUMN_OPTIONS.map(opt => (
                            <MenuItem key={opt.value} value={opt.value}>{opt.label}</MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      <TextField
                        label="%"
                        size="small"
                        type="number"
                        value={source.percent}
                        onChange={e => updateSource(index, { percent: Number(e.target.value) })}
                        sx={{ width: 90 }}
                      />
                    </Stack>
                  </Stack>
                </Paper>
              ))}

              <Button
                size="small"
                startIcon={<Add />}
                onClick={() => setSelected({ ...selected, sources: [...(selected.sources || []), emptySource()] })}
              >
                Adicionar origem
              </Button>

              <Stack direction="row" gap={1} mt={2}>
                <Button variant="outlined" startIcon={<PlayArrow />} onClick={runPreview}>
                  {(selected.target_type || "license_pool") === "ptu_pool" ? "Preview PTU (FOCUS + operacional)" : "Preview filtro"}
                </Button>
                {preview && (
                  <Chip label={`${preview.matched_rows} linhas · ${money.format(preview.total_value)}`} color="primary" variant="outlined" />
                )}
              </Stack>

              {ptuPreview && (
                <Alert severity={ptuPreview.reconciliation?.conflicts?.length ? "warning" : "info"} sx={{ mt: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>Reconciliação financeiro × operacional</Typography>
                  <Typography variant="body2">FOCUS: {money.format(ptuPreview.focus?.realized_cost || 0)} · {ptuPreview.focus?.matched_rows || 0} linhas</Typography>
                  <Typography variant="body2">
                    Operacional: {ptuPreview.operational?.ptus ?? "—"} PTUs · {ptuPreview.operational?.deployments ?? "—"} deployments ·
                    utilização {ptuPreview.operational?.utilization_avg != null ? `${ptuPreview.operational.utilization_avg.toFixed(1)}%` : "—"}
                  </Typography>
                  <Typography variant="body2">Status: {ptuPreview.reconciliation?.status}</Typography>
                  {ptuPreview.reconciliation?.conflicts?.length > 0 && (
                    <Typography variant="body2" color="warning.main">Conflitos: {ptuPreview.reconciliation.conflicts.join(", ")}</Typography>
                  )}
                  {(ptuPreview.pool_allocations || []).length > 0 && (
                    <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                      Alocações: {ptuPreview.pool_allocations.map((a: any) => `${a.deployment_key || a.project_key}: ${money.format(a.allocated_amount)}`).join(" · ")}
                    </Typography>
                  )}
                </Alert>
              )}

              <Divider sx={{ my: 2 }} />

              {/* Custo unitário manual */}
              <Typography variant="subtitle2" gutterBottom>
                Custo unitário manual (licença Claude / SaaS)
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" mb={1}>
                Para licenças com preço fixo por usuário/mês (ex.: Anthropic Claude). O valor é salvo no vínculo e exibido nos dashboards.
              </Typography>
              <Stack direction="row" gap={1} alignItems="center">
                <TextField
                  label="R$/licença/mês"
                  size="small"
                  type="number"
                  value={unitCost}
                  onChange={e => setUnitCost(e.target.value)}
                  sx={{ width: 160 }}
                />
                <Button variant="outlined" size="small" onClick={runManualPreview}>Preview unitário</Button>
              </Stack>
              {manualPreview && (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {manualPreview.active_license_count} licenças ativas × {money.format(Number(unitCost))} = {money.format(manualPreview.total_cost)}
                </Alert>
              )}
            </Paper>
          </Grid>

          {/* ── Coluna direita: distribuição + ações ─────────────────────── */}
          <Grid item xs={12} md={4}>
            <Paper elevation={0} sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2, minHeight: 480 }}>
              <Typography variant="h6" gutterBottom>Distribuição por usuário</Typography>
              <FormControlLabel
                control={<Switch checked={Boolean(selected.distribution_enabled)} onChange={(_, checked) => setSelected({ ...selected, distribution_enabled: checked })} />}
                label="Distribuir custo de licença por usuário"
              />
              <FormControl fullWidth size="small" sx={{ mt: 2 }} disabled={!selected.distribution_enabled}>
                <InputLabel>Base de rateio</InputLabel>
                <Select
                  label="Base de rateio"
                  value={selected.distribution_basis || "tokens"}
                  onChange={e => setSelected({ ...selected, distribution_basis: e.target.value })}
                >
                  <MenuItem value="tokens">Tokens (TotalTokens)</MenuItem>
                  <MenuItem value="requests">Requisições</MenuItem>
                  <MenuItem value="activity_index">UsageIndex (percent_rank)</MenuItem>
                  <MenuItem value="chatturns">Chats (ChatTurns)</MenuItem>
                  <MenuItem value="equal">Igualitário</MenuItem>
                </Select>
              </FormControl>
              <FormControl fullWidth size="small" sx={{ mt: 2 }} disabled={!selected.distribution_enabled}>
                <InputLabel>Chave de usuário</InputLabel>
                <Select
                  label="Chave de usuário"
                  value={selected.distribution_user_key || "email"}
                  onChange={e => setSelected({ ...selected, distribution_user_key: e.target.value as "email" | "login" })}
                >
                  <MenuItem value="email">E-mail (UserEmail)</MenuItem>
                  <MenuItem value="login">Login (UserLogin)</MenuItem>
                </Select>
              </FormControl>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                Sem distribuição, o custo permanece no grão do vínculo. Com distribuição ligada, propaga para AllocatedLicenseCost e AllocatedCost.
              </Typography>

              <Stack direction="row" gap={1} mt={3}>
                <Button variant="contained" startIcon={<Save />} onClick={handleSave} disabled={saving}>
                  Salvar vínculo
                </Button>
                <Button variant="outlined" onClick={handleApply} disabled={!selected.id}>
                  Aplicar rateio
                </Button>
              </Stack>

              <Alert severity="info" sx={{ mt: 2 }}>
                API: <Link href="/api/docs#/ai-cost-origin" target="_blank">/ai-cost-origin</Link>. Pools não mapeados aparecem nos dashboards com cobertura parcial.
              </Alert>

              {/* Guia de campos por cloud */}
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2" gutterBottom>Referência de campos por cloud</Typography>

              {/* Aviso Anthropic */}
              <Alert severity="warning" sx={{ mb: 1.5, fontSize: 11, py: 0.5, px: 1 }}>
                <strong>Anthropic (Claude)</strong> não possui billing integrado ao Azure/GCP.
                Use apenas o campo <strong>Custo unitário manual</strong> (R$/licença/mês) abaixo.
              </Alert>

              <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <Box component="thead">
                  <Box component="tr" sx={{ "& th": { textAlign: "left", p: "2px 6px", bgcolor: "action.hover", fontWeight: 600 } }}>
                    <th>Cloud</th><th>Campo FOCUS</th><th>Valor de exemplo</th><th>Uso</th>
                  </Box>
                </Box>
                <Box component="tbody">
                  {CLOUD_FIELD_HINTS.map(h => (
                    <Box component="tr" key={`${h.cloud}-${h.field}-${h.example}`} sx={{ "& td": { p: "2px 6px", borderBottom: "1px solid", borderColor: "divider", verticalAlign: "middle" } }}>
                      <td><Chip label={h.cloud} size="small" color={h.cloud === "GCP" ? "success" : "primary"} sx={{ fontSize: 10, height: 18 }} /></td>
                      <td><code>{h.field}</code></td>
                      <td><em>{h.example}</em></td>
                      <td style={{ color: "#666" }}>{h.note}</td>
                    </Box>
                  ))}
                </Box>
              </Box>
            </Paper>
          </Grid>
        </Grid>
      </Box>
    </Box>
  );
}
