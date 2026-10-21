import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Plot from "@/components/Plot";
import {
  Alert, Box, Button, Card, Chip, CircularProgress, FormControl,
  Grid, InputLabel, MenuItem, Select, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, Tooltip, Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import CloudSyncIcon from "@mui/icons-material/CloudSync";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import LightbulbIcon from "@mui/icons-material/Lightbulb";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import SavingsOutlinedIcon from "@mui/icons-material/SavingsOutlined";
import PercentIcon from "@mui/icons-material/Percent";
import AccountBalanceWalletIcon from "@mui/icons-material/AccountBalanceWallet";
import {
  getReservationsDashboard, getReservationsCatalog, getReservationSources,
  testReservationConnection, collectReservations, getReservationsAdvisor,
  getReservationsRecommendations,
} from "../../services/api";

const usd = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd1 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD", maximumFractionDigits: 1 });
const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const num1 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });

const TAB_KEYS = ["executivo", "catalogo", "recomendacoes", "fontes"] as const;

const STATUS_COLOR: Record<string, string> = {
  Succeeded: "#16a34a",
  Active: "#16a34a",
  Expired: "#64748b",
  Cancelled: "#dc2626",
  Pending: "#f59e0b",
};
const STATUS_BG: Record<string, string> = {
  Succeeded: "#dcfce7",
  Active: "#dcfce7",
  Expired: "#f1f5f9",
  Cancelled: "#fee2e2",
  Pending: "#fef3c7",
};

const BENEFIT_LABELS: Record<string, string> = {
  reserved_instance: "Reserved Instance",
  savings_plan: "Savings Plan",
};
const BENEFIT_COLORS: Record<string, string> = {
  reserved_instance: "#0f766e",
  savings_plan: "#7c3aed",
};

const ADVISOR_CATEGORY_COLORS: Record<string, string> = {
  Cost: "#16a34a",
  Security: "#dc2626",
  HighAvailability: "#f59e0b",
  Performance: "#2563eb",
  OperationalExcellence: "#7c3aed",
};
const ADVISOR_CATEGORY_BG: Record<string, string> = {
  Cost: "#dcfce7",
  Security: "#fee2e2",
  HighAvailability: "#fef3c7",
  Performance: "#dbeafe",
  OperationalExcellence: "#f3e8ff",
};

function statusChip(status?: string | null) {
  const key = status || "—";
  return (
    <Chip
      size="small"
      label={key}
      sx={{
        bgcolor: STATUS_BG[key] || "#f1f5f9",
        color: STATUS_COLOR[key] || "#334155",
        fontWeight: 600,
        fontSize: 11,
      }}
    />
  );
}

function kpiCard(label: string, value: React.ReactNode, hint?: string, icon?: React.ReactNode) {
  return (
    <Card sx={{ p: 2, height: "100%", borderRadius: 2, boxShadow: 1 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
            {label}
          </Typography>
          <Typography variant="h5" sx={{ fontWeight: 700, mt: 0.5 }}>
            {value}
          </Typography>
          {hint && (
            <Typography variant="caption" color="text.secondary">
              {hint}
            </Typography>
          )}
        </Box>
        {icon && <Box sx={{ color: "text.secondary", opacity: 0.7 }}>{icon}</Box>}
      </Stack>
    </Card>
  );
}

function expiringLabel(days: number | null | undefined): string {
  if (days == null) return "—";
  if (days < 0) return `expirado há ${-days} dias`;
  if (days === 0) return "vence hoje";
  return `em ${days} dia(s)`;
}

const UTIL_BUCKETS: Array<{ label: string; min: number; max: number; color: string; n?: number }> = [
  { label: "0–50%", min: 0, max: 50, color: "#dc2626" },
  { label: "50–80%", min: 50, max: 80, color: "#f59e0b" },
  { label: "80–95%", min: 80, max: 95, color: "#84cc16" },
  { label: "95–100%", min: 95, max: 100.001, color: "#16a34a" },
];

const CATEGORY_LABELS: Record<string, string> = {
  Cost: "Custo",
  Security: "Segurança",
  HighAvailability: "Alta disponibilidade",
  Performance: "Performance",
  OperationalExcellence: "Excelência operacional",
};

export default function Reservations() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = Math.max(0, TAB_KEYS.indexOf((searchParams.get("subtab") as typeof TAB_KEYS[number]) || "executivo"));
  const setTab = (next: number) => {
    const key = TAB_KEYS[next];
    if (!key) return;
    const updated = new URLSearchParams(searchParams);
    updated.set("tab", "reservas");
    updated.set("subtab", key);
    setSearchParams(updated, { replace: true });
  };
  const [dash, setDash] = useState<any>(null);
  const [catalog, setCatalog] = useState<any>(null);
  const [sources, setSources] = useState<any>(null);
  const [advisor, setAdvisor] = useState<any>(null);
  const [recommendations, setRecommendations] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connectionFilter, setConnectionFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [advisorCategory, setAdvisorCategory] = useState("Cost");
  const [utilBucket, setUtilBucket] = useState<string | null>(null);
  const [lastCollect, setLastCollect] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    try {
      const params: Record<string, unknown> = {};
      if (connectionFilter) params.connection_id = Number(connectionFilter);
      if (typeFilter) params.benefit_types = typeFilter;
      const [d, c, s] = await Promise.all([
        getReservationsDashboard(params, controller.signal),
        getReservationsCatalog({ limit: 5000 }, controller.signal),
        getReservationSources(controller.signal),
      ]);
      if (!controller.signal.aborted) {
        setDash(d);
        setCatalog(c);
        setSources(s);
      }
    } catch (e: any) {
      if (e?.code === "ERR_CANCELED") return;
      setError(e?.response?.data?.detail || "Falha ao consultar reservas Azure");
    }
  };

  const loadRecommendations = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const [a, r] = await Promise.all([
        getReservationsAdvisor({ category: advisorCategory, limit: 500 }, controller.signal),
        getReservationsRecommendations({ limit: 500 }, controller.signal),
      ]);
      if (!controller.signal.aborted) {
        setAdvisor(a);
        setRecommendations(r);
      }
    } catch (e: any) {
      if (e?.code === "ERR_CANCELED") return;
      setError(e?.response?.data?.detail || "Falha ao consultar recomendações");
    }
  };

  useEffect(() => {
    load();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionFilter, typeFilter]);

  useEffect(() => {
    if (tab === 2) loadRecommendations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, advisorCategory]);

  const handleTest = async (connId: number) => {
    setBusy(true);
    setError(null);
    try {
      const result = await testReservationConnection(connId);
      alert(`${result.status}: ${result.message}`);
      load();
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha no teste de conexão");
    } finally {
      setBusy(false);
    }
  };

  const handleCollect = async (connIds: number[]) => {
    setBusy(true);
    setError(null);
    try {
      const result = await collectReservations(connIds);
      const names = (result.queued || []).map((q: any) => q.name).join(", ");
      setLastCollect(`Coleta enfileirada: ${names}`);
      alert(`Coleta enfileirada para: ${names}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Falha ao enfileirar coleta");
    } finally {
      setBusy(false);
    }
  };

  const kpis = dash?.kpis || {};
  const byType = dash?.by_type || [];
  const expiring = dash?.expiring || [];
  const catalogRows = catalog?.rows || [];
  const enabledSources = sources?.enabled || [];
  const allSources = sources?.all || [];
  const advisorRows = advisor?.rows || [];
  const advisorCats = advisor?.categories || [];
  const recRows = recommendations?.rows || [];
  const recSummary = recommendations?.summary || {};

  // Filtro do catálogo: usa min/max do bucket (não o label, que tem en dash)
  const filteredCatalog = useMemo(() => {
    let rows = catalogRows;
    if (statusFilter) rows = rows.filter((r: any) => (r.status || "") === statusFilter);
    if (utilBucket) {
      const bucket = UTIL_BUCKETS.find((b) => b.label === utilBucket);
      if (bucket) {
        rows = rows.filter((r: any) => r.util_1d != null && r.util_1d >= bucket.min && r.util_1d < bucket.max);
      }
    }
    return rows;
  }, [catalogRows, statusFilter, utilBucket]);

  const utilBuckets = useMemo(() => {
    return UTIL_BUCKETS.map((b) => ({
      ...b,
      n: catalogRows.filter((r: any) => r.util_1d != null && r.util_1d >= b.min && r.util_1d < b.max).length,
    }));
  }, [catalogRows]);

  const expiryBuckets = useMemo(() => {
    const buckets = [
      { label: "Vence em ≤30d", min: 0, max: 30, n: 0 },
      { label: "31–60d", min: 31, max: 60, n: 0 },
      { label: "61–90d", min: 61, max: 90, n: 0 },
      { label: ">90d", min: 91, max: 99999, n: 0 },
      { label: "Expirados", min: -99999, max: -1, n: 0 },
    ];
    for (const r of catalogRows) {
      const d = r._days_left;
      if (d == null) continue;
      for (const b of buckets) if (d >= b.min && d <= b.max) { b.n += 1; break; }
    }
    return buckets;
  }, [catalogRows]);

  // Donut único: um trace pie com todos os tipos
  const pieData: any[] = useMemo(() => {
    const labels = byType.map((b: any) => BENEFIT_LABELS[b.type] || b.type);
    const values = byType.map((b: any) => b.count);
    const colors = byType.map((b: any) => BENEFIT_COLORS[b.type] || "#0f766e");
    return [{
      type: "pie",
      labels,
      values,
      marker: { colors },
      hole: 0.55,
      textinfo: "label+value",
      textfont: { size: 12 },
      hovertemplate: "%{label}: %{value} benefícios<extra></extra>",
      sort: false,
    }];
  }, [byType]);

  const utilDrilldown = utilBucket
    ? UTIL_BUCKETS.find((b) => b.label === utilBucket)
    : null;

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Reservas Azure & Savings Plans
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Catálogo de benefícios coletado via ARM (Microsoft.Billing) na base de origem — sem normalização FOCUS.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={() => (tab === 2 ? loadRecommendations() : load())}
            disabled={busy}
          >
            Atualizar
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<CloudSyncIcon />}
            onClick={() => handleCollect(enabledSources.map((s: any) => s.id))}
            disabled={busy || enabledSources.length === 0}
          >
            Coletar agora
          </Button>
        </Stack>
      </Stack>

      {lastCollect && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setLastCollect(null)}>
          {lastCollect}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {dash && dash.available === false && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {dash.message || "Nenhum dado coletado ainda. Habilite collect_reservations nas conexões Azure e clique em Coletar agora."}
        </Alert>
      )}


      {tab === 0 && (
        <Box>
          {!dash ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
              <CircularProgress />
            </Box>
          ) : dash.available === false ? (
            <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
              Aguardando primeira coleta...
            </Typography>
          ) : (
            <Stack spacing={2}>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6} md={3}>
                  {kpiCard("Benefícios", num.format(kpis.benefits_total || 0), `${num.format(kpis.benefits_active || 0)} ativos`)}
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  {kpiCard("Utilização média", kpis.avg_utilization_catalog != null ? pct.format((kpis.avg_utilization_catalog || 0) / 100) : "—")}
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  {kpiCard("Vencem em 30d", num.format(kpis.expiring_30d || 0), `${num.format(kpis.expiring_90d || 0)} nos próximos 90 dias`)}
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  {kpiCard("Compromisso SP", kpis.sp_commitment_total ? `USD ${num1.format(kpis.sp_commitment_total)}` : "—", "por hora")}
                </Grid>
              </Grid>

              <Grid container spacing={2}>
                <Grid item xs={12} md={4}>
                  <Card sx={{ p: 2, borderRadius: 2 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                      Distribuição por tipo
                    </Typography>
                    <Plot
                      data={pieData}
                      layout={{
                        autosize: true,
                        margin: { l: 20, r: 20, t: 10, b: 20 },
                        showlegend: true,
                        legend: { orientation: "h", y: -0.15, font: { size: 11 } },
                        paper_bgcolor: "transparent",
                        plot_bgcolor: "transparent",
                      }}
                      style={{ width: "100%", height: 300 }}
                      useResizeHandler
                    />
                  </Card>
                </Grid>
                <Grid item xs={12} md={4}>
                  <Card sx={{ p: 2, borderRadius: 2 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                      Utilização (1 dia) — clique para detalhar
                    </Typography>
                    <Plot
                      data={[{
                        type: "bar",
                        x: utilBuckets.map((b) => b.label),
                        y: utilBuckets.map((b) => b.n),
                        marker: { color: utilBuckets.map((b) => b.color) },
                        text: utilBuckets.map((b) => String(b.n)),
                        textposition: "outside",
                        hovertemplate: "%{x}: %{y} benefícios<extra></extra>",
                      }]}
                      layout={{
                        autosize: true,
                        margin: { l: 40, r: 20, t: 10, b: 50 },
                        paper_bgcolor: "transparent",
                        plot_bgcolor: "transparent",
                        clickmode: "event+select",
                      }}
                      style={{ width: "100%", height: 300 }}
                      useResizeHandler
                      onClick={(e: any) => {
                        const pt = e?.points?.[0];
                        if (pt && pt.x != null) {
                          const label = String(pt.x);
                          const bucket = UTIL_BUCKETS.find((b) => b.label === label);
                          if (bucket) {
                            setUtilBucket(utilBucket === label ? null : label);
                            setTab(1);
                          }
                        }
                      }}
                    />
                  </Card>
                </Grid>
                <Grid item xs={12} md={4}>
                  <Card sx={{ p: 2, borderRadius: 2 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                      Vencimentos
                    </Typography>
                    <Plot
                      data={[{
                        type: "bar",
                        x: expiryBuckets.map((b) => b.label),
                        y: expiryBuckets.map((b) => b.n),
                        marker: { color: ["#dc2626", "#f59e0b", "#facc15", "#16a34a", "#64748b"] as string[] },
                        text: expiryBuckets.map((b) => String(b.n)),
                        textposition: "outside",
                      }]}
                      layout={{
                        autosize: true,
                        margin: { l: 40, r: 20, t: 10, b: 70 },
                        paper_bgcolor: "transparent",
                        plot_bgcolor: "transparent",
                      }}
                      style={{ width: "100%", height: 300 }}
                      useResizeHandler
                    />
                  </Card>
                </Grid>
                <Grid item xs={12}>
                  <Card sx={{ p: 2, borderRadius: 2 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                      Próximos vencimentos (top 15)
                    </Typography>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 700 }}>Benefício</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Tipo</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>SKU</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Termo</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Vencimento</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Utilização</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {expiring.slice(0, 15).map((r: any, i: number) => (
                          <TableRow key={`${r.benefit_id}-${i}`}>
                            <TableCell sx={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              <Tooltip title={r.display_name || r.benefit_id}><span>{r.display_name || r.benefit_id}</span></Tooltip>
                            </TableCell>
                            <TableCell>{BENEFIT_LABELS[r.benefit_type] || r.benefit_type}</TableCell>
                            <TableCell>{r.sku_name || r.sku_description || "—"}</TableCell>
                            <TableCell>{statusChip(r.status)}</TableCell>
                            <TableCell>{r.term || "—"}</TableCell>
                            <TableCell align="right">
                              <Chip
                                size="small"
                                label={expiringLabel(r.days_left)}
                                color={r.days_left != null && r.days_left <= 30 ? "error" : r.days_left != null && r.days_left <= 90 ? "warning" : "default"}
                              />
                            </TableCell>
                            <TableCell align="right">
                              {r.util_1d != null ? (
                                <Chip
                                  size="small"
                                  label={pct.format(r.util_1d / 100)}
                                  color={r.util_1d < 50 ? "error" : r.util_1d < 80 ? "warning" : "success"}
                                  sx={{ fontWeight: 600, fontSize: 11 }}
                                />
                              ) : "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </Card>
                </Grid>
              </Grid>
            </Stack>
          )}
        </Box>
      )}

      {tab === 1 && (
        <Stack spacing={2}>
          <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Tipo</InputLabel>
              <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} label="Tipo">
                <MenuItem value="">Todos</MenuItem>
                <MenuItem value="reserved_instance">Reserved Instance</MenuItem>
                <MenuItem value="savings_plan">Savings Plan</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Status</InputLabel>
              <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} label="Status">
                <MenuItem value="">Todos</MenuItem>
                <MenuItem value="Succeeded">Succeeded</MenuItem>
                <MenuItem value="Cancelled">Cancelled</MenuItem>
                <MenuItem value="Expired">Expired</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Conexão</InputLabel>
              <Select value={connectionFilter} onChange={(e) => setConnectionFilter(e.target.value)} label="Conexão">
                <MenuItem value="">Todas</MenuItem>
                {allSources.map((s: any) => (
                  <MenuItem key={s.id} value={String(s.id)}>{s.name}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <Typography variant="caption" color="text.secondary">
              {filteredCatalog.length} benefícios
            </Typography>
            {utilDrilldown && (
              <Button
                size="small"
                variant="outlined"
                color="secondary"
                startIcon={<ArrowBackIcon />}
                onClick={() => setUtilBucket(null)}
              >
                Sair do filtro {utilDrilldown.label} ({utilDrilldown.n})
              </Button>
            )}
          </Stack>

          {utilDrilldown && (
            <Alert severity="info">
              Mostrando reservas com utilização entre <b>{utilDrilldown.label}</b> ({utilDrilldown.n} benefícios). Clique em "Sair do filtro" para voltar.
            </Alert>
          )}

          <Card sx={{ borderRadius: 2, overflow: "hidden" }}>
            <Box sx={{ maxHeight: 640, overflow: "auto" }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 700 }}>Benefício</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Tipo</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>SKU</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Termo</TableCell>
                    <TableCell align="right" sx={{ fontWeight: 700 }}>Qtd</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Escopo</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Início</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Fim</TableCell>
                    <TableCell align="right" sx={{ fontWeight: 700 }}>Utilização</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Conexão</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filteredCatalog.map((r: any, i: number) => (
                    <TableRow key={`${r.benefit_id}-${i}`} hover>
                      <TableCell sx={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <Tooltip title={r.display_name || r.benefit_id}><span>{r.display_name || r.benefit_id}</span></Tooltip>
                      </TableCell>
                      <TableCell>{BENEFIT_LABELS[r.benefit_type] || r.benefit_type}</TableCell>
                      <TableCell>{r.sku_name || r.sku_description || "—"}</TableCell>
                      <TableCell>{r.term || "—"}</TableCell>
                      <TableCell align="right">{r.quantity != null ? num.format(r.quantity) : (r.commitment_amount != null ? num1.format(r.commitment_amount) : "—")}</TableCell>
                      <TableCell>{r.applied_scope_type || "—"}</TableCell>
                      <TableCell>{statusChip(r.status)}</TableCell>
                      <TableCell>{r.benefit_start ? String(r.benefit_start).slice(0, 10) : "—"}</TableCell>
                      <TableCell>{r.benefit_end ? String(r.benefit_end).slice(0, 10) : "—"}</TableCell>
                      <TableCell align="right">
                        {r.util_1d != null ? (
                          <Chip
                            size="small"
                            label={pct.format(r.util_1d / 100)}
                            color={r.util_1d < 50 ? "error" : r.util_1d < 80 ? "warning" : "success"}
                            sx={{ fontWeight: 600, fontSize: 11 }}
                          />
                        ) : "—"}
                      </TableCell>
                      <TableCell>{r.connection_code || "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          </Card>
        </Stack>
      )}

      {tab === 2 && (
        <Stack spacing={2}>
          {!advisor && !recommendations ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
              <CircularProgress />
            </Box>
          ) : (
            <>
              <Grid container spacing={2}>
                <Grid item xs={12} md={3}>
                  {kpiCard(
                    "Savings potencial",
                    recSummary.savings_total != null ? usd.format(recSummary.savings_total) : "—",
                    `${num.format(recSummary.n || 0)} recomendações`,
                    <SavingsOutlinedIcon fontSize="large" />
                  )}
                </Grid>
                <Grid item xs={12} md={3}>
                  {kpiCard(
                    "Savings % médio",
                    recSummary.avg_savings_pct != null ? pct.format((recSummary.avg_savings_pct || 0) / 100) : "—",
                    undefined,
                    <PercentIcon fontSize="large" />
                  )}
                </Grid>
                <Grid item xs={12} md={3}>
                  {kpiCard(
                    "Compromisso mensal est.",
                    recSummary.commitment_total != null ? usd1.format(recSummary.commitment_total) : "—",
                    "por hora",
                    <AccountBalanceWalletIcon fontSize="large" />
                  )}
                </Grid>
                <Grid item xs={12} md={3}>
                  {kpiCard(
                    "Advisor (Cost)",
                    num.format(advisorRows.length),
                    `${num.format(advisor?.total || 0)} no total da categoria`,
                    <LightbulbIcon fontSize="large" />
                  )}
                </Grid>
              </Grid>

              <Card sx={{ p: 2, borderRadius: 2 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                  Recomendações de compra — Savings Plan / Reserved Instance
                </Typography>
                <Box sx={{ maxHeight: 480, overflow: "auto" }}>
                  <Table size="small" stickyHeader>
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 700 }}>Tipo</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>SKU</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Termo</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Escopo</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Lookback</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>Compromisso</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>Utilização est.</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>Cobertura</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>Savings</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700 }}>Savings %</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Subscription</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {recRows.map((r: any, i: number) => (
                        <TableRow key={`rec-${r.recommendation_id}-${i}`} hover>
                          <TableCell>
                            <Chip
                              size="small"
                              label={r.kind === "SavingsPlan" ? "Savings Plan" : (r.kind || "—")}
                              sx={{
                                bgcolor: r.kind === "SavingsPlan" ? "#f3e8ff" : "#dbeafe",
                                color: r.kind === "SavingsPlan" ? "#6d28d9" : "#1d4ed8",
                                fontWeight: 600,
                                fontSize: 11,
                              }}
                            />
                          </TableCell>
                          <TableCell sx={{ fontWeight: 600 }}>{r.arm_sku_name || "—"}</TableCell>
                          <TableCell>
                            <Chip size="small" variant="outlined" label={r.term || "—"} sx={{ fontSize: 11 }} />
                          </TableCell>
                          <TableCell>{r.scope || "—"}</TableCell>
                          <TableCell sx={{ fontSize: 12, color: "text.secondary" }}>
                            {r.look_back_period || "—"}
                          </TableCell>
                          <TableCell align="right">{r.commitment_amount != null ? usd1.format(r.commitment_amount) : "—"}</TableCell>
                          <TableCell align="right">{r.average_utilization_pct != null ? pct.format(r.average_utilization_pct / 100) : "—"}</TableCell>
                          <TableCell align="right">{r.coverage_pct != null ? pct.format(r.coverage_pct / 100) : "—"}</TableCell>
                          <TableCell align="right" sx={{ color: "#16a34a", fontWeight: 700 }}>
                            {r.savings_amount != null ? usd.format(r.savings_amount) : "—"}
                          </TableCell>
                          <TableCell align="right">
                            <Chip
                              size="small"
                              label={r.savings_pct != null ? pct.format(r.savings_pct / 100) : "—"}
                              sx={{ bgcolor: "#dcfce7", color: "#16a34a", fontWeight: 600, fontSize: 11 }}
                            />
                          </TableCell>
                          <TableCell sx={{ fontSize: 11, color: "text.secondary" }}>
                            <Tooltip title={r.subscription_id || ""}>
                              <span>{String(r.subscription_id || "").slice(0, 8)}…</span>
                            </Tooltip>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              </Card>

              <Card sx={{ p: 2, borderRadius: 2 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1, flexWrap: "wrap", gap: 1 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                    <LightbulbIcon sx={{ fontSize: 18, verticalAlign: "middle", mr: 0.5, color: "#f59e0b" }} />
                    Recomendações do Azure Advisor
                  </Typography>
                  <FormControl size="small" sx={{ minWidth: 220 }}>
                    <InputLabel>Categoria</InputLabel>
                    <Select value={advisorCategory} onChange={(e) => setAdvisorCategory(e.target.value)} label="Categoria">
                      {advisorCats.map((c: any) => (
                        <MenuItem key={c.category} value={c.category}>
                          {CATEGORY_LABELS[c.category] || c.category} ({c.n})
                        </MenuItem>
                      ))}
                      {advisorCats.length === 0 && <MenuItem value="Cost">Custo</MenuItem>}
                    </Select>
                  </FormControl>
                </Stack>
                <Box sx={{ maxHeight: 480, overflow: "auto" }}>
                  <Table size="small" stickyHeader>
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 700 }}>Impacto</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Recomendação</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Solução</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Subscription</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {advisorRows.map((r: any, i: number) => (
                        <TableRow key={`adv-${r.recommendation_id}-${i}`} hover>
                          <TableCell>
                            <Chip
                              size="small"
                              label={r.impact || "—"}
                              color={r.impact === "High" ? "error" : r.impact === "Medium" ? "warning" : "default"}
                              sx={{ fontWeight: 600, fontSize: 11 }}
                            />
                          </TableCell>
                          <TableCell sx={{ maxWidth: 340 }}>
                            <Tooltip title={r.problem || ""} placement="top-start">
                              <Typography sx={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" as const }}>
                                {r.problem || "—"}
                              </Typography>
                            </Tooltip>
                          </TableCell>
                          <TableCell sx={{ maxWidth: 340 }}>
                            <Tooltip title={r.solution || ""} placement="top-start">
                              <Typography sx={{ fontSize: 13, color: "text.secondary", overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" as const }}>
                                {r.solution || "—"}
                              </Typography>
                            </Tooltip>
                          </TableCell>
                          <TableCell sx={{ fontSize: 11, color: "text.secondary" }}>
                            <Tooltip title={r.subscription_id || ""}>
                              <span>{String(r.subscription_id || "").slice(0, 8)}…</span>
                            </Tooltip>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              </Card>
            </>
          )}
        </Stack>
      )}

      {tab === 3 && (
        <Stack spacing={2}>
          {enabledSources.length === 0 && (
            <Alert severity="warning">
              Nenhuma conexão com <code>collect_reservations</code> habilitado. Edite uma conexão Azure e adicione o flag ao config_json.
            </Alert>
          )}
          <Grid container spacing={2}>
            {allSources.map((s: any) => {
              const enabled = Boolean(s.config?.collect_reservations);
              return (
                <Grid item xs={12} md={6} key={s.id}>
                  <Card sx={{ p: 2, borderRadius: 2, border: enabled ? "1px solid #16a34a" : undefined }}>
                    <Stack direction="row" justifyContent="space-between" alignItems="center">
                      <Box>
                        <Typography sx={{ fontWeight: 700 }}>{s.name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {s.code} · {s.connector_type}
                        </Typography>
                      </Box>
                      {enabled ? (
                        <Chip size="small" label="Coleta habilitada" color="success" icon={<FactCheckIcon />} />
                      ) : (
                        <Chip size="small" label="Desabilitada" />
                      )}
                    </Stack>
                    <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
                      <Button size="small" variant="outlined" onClick={() => handleTest(s.id)} disabled={busy}>
                        Testar conexão
                      </Button>
                      <Button size="small" variant="contained" onClick={() => handleCollect([s.id])} disabled={busy || !enabled}>
                        Coletar
                      </Button>
                    </Stack>
                  </Card>
                </Grid>
              );
            })}
          </Grid>
        </Stack>
      )}
    </Box>
  );
}
