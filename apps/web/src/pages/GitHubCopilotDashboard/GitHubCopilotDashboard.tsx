import React, { useState, useEffect, useRef } from "react";
import {
  Box, Typography, Paper, Stack, Card, CardContent, FormControl, InputLabel,
  Select, MenuItem, CircularProgress, Chip, TextField, InputAdornment,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Grid,
  TablePagination, Alert, Drawer, IconButton, Divider
} from "@mui/material";
import {
  Search, InfoOutlined, CheckCircle, WarningAmber, CancelOutlined,
  AccessTime, Person, ArrowDownward, ArrowUpward, Close,
  Psychology, Bolt, Code, ChatBubbleOutline
} from "@mui/icons-material";
import Plot from "@/components/Plot";
import {
  getAICopilotEngagementUsers,
  getAICopilotFinancialInsights,
  getAICopilotHistory,
  getAICopilotModels,
  getAICopilotOverview,
  getAICopilotUserModelDetails,
} from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const num = new Intl.NumberFormat("pt-BR");

/** Calendar YYYYMM from the viewer's clock (in-progress month). */
const currentCalendarPeriod = (now = new Date()) =>
  `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}`;

/** "202609" -> "set de 2026" (rótulo de mês para os eixos dos gráficos). */
const periodLabel = (p: string) => {
  if (!p || p.length !== 6) return p;
  const d = new Date(Number(p.slice(0, 4)), Number(p.slice(4, 6)) - 1, 1);
  return d.toLocaleDateString("pt-BR", { month: "short", year: "numeric" }).replace(/\./g, "");
};

const buildAvailablePeriods = (now = new Date()) => {
  const periods: { value: string; label: string }[] = [];
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const value = currentCalendarPeriod(d);
    const monthName = d.toLocaleDateString("pt-BR", { month: "long" });
    const pretty = monthName.charAt(0).toUpperCase() + monthName.slice(1);
    const suffix = i === 0 ? " (Atual)" : "";
    periods.push({ value, label: `${pretty} / ${d.getFullYear()}${suffix}` });
  }
  return periods;
};

const AVAILABLE_PERIODS = buildAvailablePeriods();
const OPEN_PERIOD = currentCalendarPeriod();

/** Números grandes sumarizados: 1.234.567 -> "1,2 Mi"; 174.899.376.089 -> "174,9 Bi". */
const compact = (n: number) => {
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " Bi";
  if (abs >= 1e6) return (n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " Mi";
  if (abs >= 1e4) return (n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " K";
  return Math.round(n).toLocaleString("pt-BR");
};

interface CopilotMetrics {
  lines_accepted: number;
  chat_turns: number;
  total_tokens: number;
  total_credits: number;
  credits_pool: number;
  credits_utilization_pct: number;
  active_licenses: number;
  total_licenses: number;
  cloud_cost: number;
  cloud_cost_usd: number;
  cost_period_label?: string;
  processed_tokens?: number;
  cost_per_million_tokens?: number | null;
  current_period?: string;
  current_realized_brl?: number;
  projected_cost_brl?: number;
  license_cost: number;
  total_cost: number;
  inactive_30: number;
  inactive_60: number;
  inactive_90: number;
  potential_savings: number;
  utilization: {
    high: number;
    low: number;
    zero: number;
  };
}

interface EngagementUser {
  user_login: string;
  email: string;
  user_name: string;
  dept: string;
  days_inactive: number;
  last_activity: string;
  lines_accepted: number;
  chat_turns: number;
  total_tokens: number;
  total_credits: number;
  net_amount?: number;
  included_amount?: number;
  average_credits_per_account?: number;
  credits_vs_pool_average?: number;
  business_license_cost_per_account_brl?: number;
  individual_credit_allowance?: number | null;
  individual_credit_balance?: number | null;
  total_interactions: number;
  engagement_level: "high" | "low" | "zero" | "financial_only";
  consumption_signal?: string;
}

interface CreditPeriod {
  start?: string | null;
  end?: string | null;
  days?: number;
  basis?: string;
  quota_period?: string;
  description?: string;
}

interface PoolBenchmark {
  accounts: number;
  consumed_credits: number;
  average_credits_per_account: number;
  business_license_cost_brl: number;
  business_license_cost_per_account_brl: number;
  contractual_standard_pool?: number;
  covered_consumption_above_standard?: number;
}

interface EngagementCounts {
  all: number;
  high: number;
  low: number;
  zero: number;
  financial_only: number;
  inactive_30: number;
  inactive_60: number;
  inactive_90: number;
}

interface UserModelDetail {
  model_name: string;
  lines_accepted: number;
  acceptances_count: number;
  total_interactions: number;
  share_pct: number;
  primary_editor: string;
  primary_language: string;
  total_tokens?: number;
  token_source?: string; // "real" (relatório financeiro) | "estimado" (linhas × 7)
}

interface CostEvolutionSegment {
  subcategory: string;
  realized: number;
}

interface CostEvolution {
  period: string;
  total_realized: number;
  segments: CostEvolutionSegment[];
  is_current_month?: boolean;
  is_forecast?: boolean;
  projected_total?: number;
  forecast_remainder?: number;
}

interface UserModelDetailsResponse {
  user_login: string;
  email: string;
  period: string;
  is_billing_account?: boolean;
  billing_origin?: string;
  contractual_invoice_credits?: number;
  summary: {
    total_credits: number;
    quota: number;
    overage_credits: number;
    is_overage: boolean;
    overage_pct: number;
    total_lines_accepted: number;
    total_chat_acceptances: number;
    total_interactions: number;
    models_count: number;
  };
  models: UserModelDetail[];
}

interface CopilotModel {
  model_name: string;
  active_users: number;
  lines_accepted: number;
  acceptances_count: number;
  suggestions_count: number;
  total_credits: number;
  total_tokens?: number;
  token_source?: string; // "real" | "estimado"
  cost_usd?: number;
  cost_brl?: number;
  cost_per_million_tokens_usd?: number | null;
  cost_per_million_tokens_brl?: number | null;
  credit_share_pct: number;
}

interface CopilotHistory {
  period: string;
  lines_accepted: number;
  chat_turns: number;
  total_tokens: number;
  total_lic_cost: number;
  cost_usd: number;
  cost_brl: number;
  total_credits: number;
  total_cost: number;
  active_licenses: number;
  included_credits_pool: number;
  contracted_credits_pool?: number;
  effective_credits_pool?: number;
  promotional_credits?: number;
  overage_usd?: number;
  overage_credits?: number;
}

interface FinancialInsightRow {
  model?: string;
  user_login?: string;
  source_kind?: string;
  credits?: number;
  total_tokens?: number;
  cost_per_million_tokens?: number | null;
  adoption_signal?: string;
}

interface FinancialInsights {
  github: FinancialInsightRow[];
  github_alerts?: FinancialInsightRow[];
  reference?: { period?: string; token_date_from?: string | null; token_date_to?: string | null; last_collected_at?: string | null };
  azure: Array<{ meter_subcategory: string; meter_name: string; product_name: string; cost_category: string; usage_start_date?: string; usage_end_date?: string; quantity: number; unit_cost_brl: number | null; cost_brl: number; cost_usd: number; rows: number }>;
  totals?: { github_total_tokens: number; github_cost_per_million_tokens: number | null; github_cost_per_million_tokens_market_usd?: number | null; github_cost_per_million_tokens_market_brl?: number | null; github_credits?: number; azure_cost_brl: number; reconciliation_delta_brl: number; reconciliation_market_vs_paid_brl?: number; github_market_cost_brl?: number; current_period?: string; current_realized_brl?: number; current_forecast_brl?: number; current_projection_brl?: number; current_projection_total_brl?: number };
}

export default function GitHubCopilotDashboard({ embedded = false }: { embedded?: boolean }) {
  const [metrics, setMetrics] = useState<CopilotMetrics | null>(null);
  const [models, setModels] = useState<CopilotModel[]>([]);
  const [history, setHistory] = useState<CopilotHistory[]>([]);
  const [costEvolution, setCostEvolution] = useState<CostEvolution[]>([]);
  const [financialInsights, setFinancialInsights] = useState<FinancialInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState(OPEN_PERIOD);

  const [engagementUsers, setEngagementUsers] = useState<EngagementUser[]>([]);
  const [engagementCounts, setEngagementCounts] = useState<EngagementCounts>({
  all: 0, high: 0, low: 0, zero: 0, financial_only: 0, inactive_30: 0, inactive_60: 0, inactive_90: 0,
  });
  const [creditPeriod, setCreditPeriod] = useState<CreditPeriod | null>(null);
  const [poolBenchmark, setPoolBenchmark] = useState<PoolBenchmark | null>(null);
  const [selectedFilter, setSelectedFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [engagementLoading, setEngagementLoading] = useState(false);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [sortBy, setSortBy] = useState<keyof EngagementUser>("total_interactions");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  const [selectedUser, setSelectedUser] = useState<EngagementUser | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [userDetails, setUserDetails] = useState<UserModelDetailsResponse | null>(null);

  const tableSectionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function fetchData() {
      setLoading(true);
      try {
        const payload = { period: period, year: 0, department_id: null, department_path: "" };
        // As consultas foram otimizadas (Gold pré-agregada + filtro de partição);
        // agora podem rodar em paralelo para reduzir o tempo de carregamento.
        const [ovRes, mdRes, hsRes, fiRes] = await Promise.all([
          getAICopilotOverview(payload),
          getAICopilotModels(payload),
          getAICopilotHistory(payload),
          getAICopilotFinancialInsights(payload),
        ]);
        if (ovRes.metrics) setMetrics(ovRes.metrics);
        if (mdRes.models) setModels(mdRes.models);
        if (hsRes.history) setHistory(hsRes.history);
        if (hsRes.cost_evolution) setCostEvolution(hsRes.cost_evolution);
        if (fiRes.github || fiRes.azure) setFinancialInsights(fiRes);
      } catch (err) { console.error(err); } finally { setLoading(false); }
    }
    fetchData();
  }, [period]);

  useEffect(() => {
    async function fetchEngagementUsers() {
      setEngagementLoading(true);
      try {
        const payload = { period: period, engagement_filter: selectedFilter, search: searchQuery, limit: 5000, offset: 0 };
        const res = await getAICopilotEngagementUsers(payload);
        if (res.users) setEngagementUsers(res.users);
        if (res.counts) setEngagementCounts(res.counts);
        if (res.credit_period) setCreditPeriod(res.credit_period);
        if (res.pool_benchmark) setPoolBenchmark(res.pool_benchmark);
      } catch (err) { console.error(err); } finally { setEngagementLoading(false); }
    }
    fetchEngagementUsers();
  }, [period, selectedFilter, searchQuery]);

  const handleUserRowClick = async (user: EngagementUser) => {
    setSelectedUser(user);
    setDrawerOpen(true);
    setDrawerLoading(true);
    setUserDetails(null);
    try {
      const res = await getAICopilotUserModelDetails({
        user_login: user.user_login,
        email: user.email,
        period: period,
      });
      setUserDetails(res);
    } catch (err) { console.error(err); } finally { setDrawerLoading(false); }
  };

  const handleCardFilterClick = (filterType: string) => {
    setSelectedFilter(filterType);
    setPage(0);
    if (tableSectionRef.current) tableSectionRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const handleSort = (col: keyof EngagementUser) => {
    if (sortBy === col) setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    else { setSortBy(col); setSortOrder("desc"); }
  };

  const sortedUsers = [...engagementUsers].sort((a, b) => {
    const aVal = a[sortBy]; const bVal = b[sortBy];
    if (typeof aVal === "number" && typeof bVal === "number") return sortOrder === "asc" ? aVal - bVal : bVal - aVal;
    return sortOrder === "asc" ? String(aVal).localeCompare(String(bVal)) : String(bVal).localeCompare(String(aVal));
  });

  const paginatedUsers = sortedUsers.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage);
  const tokenReference = financialInsights?.reference;
  const tokenPeriodLabel = periodLabel(tokenReference?.period || period);
  const tokenWindowLabel = tokenReference?.token_date_from && tokenReference?.token_date_to
    ? `${tokenReference.token_date_from} até ${tokenReference.token_date_to}`
    : "janela ainda não informada pela coleta";

  const card = (label: string, value: string, color: string) => (
    <Card sx={{ flex: 1, minWidth: 170, borderTop: `3px solid ${color}` }}>
      <CardContent>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h5" fontWeight={700}>{value}</Typography>
      </CardContent>
    </Card>
  );

  return (
    <Box sx={embedded ? { p: 0 } : { mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      {!embedded && (
        <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
          <Typography variant="h4">GitHub Copilot — Dashboard</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>Métricas de adoção, governança de licenças e consumo financeiro de IA por desenvolvedor.</Typography>
        </Box>
      )}
      <Box sx={embedded ? undefined : { p: 3 }}>
        <Paper elevation={0} sx={embedded ? undefined : { p: 2.5, borderRadius: 2, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
      {embedded && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="h4">GitHub Copilot — Dashboard</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>Métricas de adoção, governança de licenças e consumo financeiro de IA por desenvolvedor.</Typography>
        </Box>
      )}
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 3 }}>
        <FormControl size="small" sx={{ minWidth: 220, bgcolor: "background.paper" }}>
          <InputLabel id="copilot-period-label">Período / Mês de Referência</InputLabel>
          <Select labelId="copilot-period-label" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {AVAILABLE_PERIODS.map((p) => <MenuItem key={p.value} value={p.value}>{p.label}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {loading ? (
        <Box p={6} display="flex" justifyContent="center" alignItems="center" gap={2}>
          <CircularProgress size={28} />
          <Typography color="text.secondary">Atualizando métricas...</Typography>
        </Box>
      ) : (
        <>
          <Card sx={{ color: "white", background: "linear-gradient(120deg, #24292F, #0969DA)", mb: 3 }}>
            <CardContent>
              <Typography variant="overline" sx={{ opacity: 0.85 }}>Eficiência econômica do GitHub Copilot</Typography>
              <Grid container spacing={2} mt={0.25}>
                <Grid item xs={12} md={4}>
                  <Typography variant="body2" sx={{ opacity: 0.8 }}>Custo realizado</Typography>
                  <Typography variant="h4">{money.format(metrics?.cloud_cost || 0)}</Typography>
                  <Typography variant="caption" sx={{ opacity: 0.75 }}>{periodLabel(metrics?.cost_period_label || "")} · SKUs GitHub Copilot faturados</Typography>
                </Grid>
                <Grid item xs={12} md={4}>
                  <Typography variant="body2" sx={{ opacity: 0.8 }}>Tokens processados</Typography>
                  <Typography variant="h4">{compact(metrics?.processed_tokens || 0)}</Typography>
                  <Typography variant="caption" sx={{ opacity: 0.75 }}>input + output + cache · {periodLabel(metrics?.cost_period_label || "")}</Typography>
                </Grid>
                <Grid item xs={12} md={4}>
                  <Typography variant="body2" sx={{ opacity: 0.8 }}>Custo por MM de tokens</Typography>
                  <Typography variant="h4">{metrics?.cost_per_million_tokens == null ? "—" : money.format(metrics.cost_per_million_tokens)}</Typography>
                  <Typography variant="caption" sx={{ opacity: 0.75 }}>custo realizado ÷ milhões de tokens</Typography>
                </Grid>
              </Grid>
            </CardContent>
          </Card>

          <Stack direction="row" gap={2} flexWrap="wrap" mb={4}>
            {card("Licenças Ativas", `${metrics?.active_licenses || 0}`, "#64748B")}
            {card("Linhas Aceitas", compact(metrics?.lines_accepted || 0), "#2563EB")}
            {card("Interações", compact(metrics?.chat_turns || 0), "#0284C7")}
            {card("Créditos de IA", `${compact(metrics?.total_credits || 0)}`, "#F59E0B")}
            {metrics?.current_period && (metrics?.projected_cost_brl || 0) > 0 && (
              <Card sx={{ flex: 1, minWidth: 170, borderTop: "3px solid #0284C7" }}>
                <CardContent>
                  <Typography variant="caption" color="text.secondary">Projeção {periodLabel(metrics.current_period)}</Typography>
                  <Typography variant="h5" fontWeight={700}>{money.format(metrics?.projected_cost_brl || 0)}</Typography>
                  <Typography variant="caption" color="text.secondary">realizado parcial + forecast</Typography>
                </CardContent>
              </Card>
            )}
          </Stack>

          <Paper elevation={0} sx={{ p: 3, border: "1px solid", borderColor: "divider", borderRadius: 2, mb: 4 }}>
            <Typography variant="h5" fontWeight="bold" color="primary.main" mb={1}>FinOps: GitHub × Azure</Typography>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Custo realizado e consumo processado são conciliados pelo mês fechado: SKUs faturados de GitHub Copilot na Gold
              <b> dest_dm_cost_copilot_custo</b> ÷ input + output + cache do relatório financeiro.
            </Typography>

            <Stack direction={{ xs: "column", md: "row" }} gap={2} mb={3}>
              <Box sx={{ flex: 1, p: 2, border: "1px solid #e2e8f0", borderRadius: 2, bgcolor: "#f8fafc" }}>
                <Typography variant="subtitle2" fontWeight={700} color="text.secondary">Tokens processados</Typography>
                <Typography variant="h5" fontWeight={800}>{compact(financialInsights?.totals?.github_total_tokens || 0)}</Typography>
                <Typography variant="caption" color="text.secondary">{tokenWindowLabel} · input + output + cache</Typography>
              </Box>
              <Box sx={{ flex: 1, p: 2, border: "1px solid #e2e8f0", borderRadius: 2, bgcolor: "#f8fafc" }}>
                <Typography variant="subtitle2" fontWeight={700} color="text.secondary">Créditos de IA consumidos</Typography>
                <Typography variant="h5" fontWeight={800}>{compact(financialInsights?.totals?.github_credits || 0)}</Typography>
                <Typography variant="caption" color="text.secondary">relatório real de AI Credits · 1 crédito ≈ US$ 0,01</Typography>
              </Box>
            </Stack>

            <Stack direction={{ xs: "column", md: "row" }} gap={3}>
              <Box flex={1}>
                <Typography variant="subtitle2" fontWeight={700} mb={0.25}>Custos Azure por SKU</Typography>
                <Typography variant="caption" color="text.secondary" display="block" mb={1}>
                  Realizado parcial · referência operacional: {tokenWindowLabel}
                </Typography>
                <TableContainer sx={{ border: "1px solid #e2e8f0", borderRadius: 1.5 }}>
                  <Table size="small">
                    <TableHead>
                      <TableRow sx={{ "& th": { bgcolor: "#f8fafc", fontWeight: 700 } }}>
                        <TableCell>SKU</TableCell><TableCell align="right">Qtde</TableCell><TableCell align="right">R$</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {(financialInsights?.azure || []).length === 0 && (
                        <TableRow><TableCell colSpan={3}><Typography color="text.secondary">Sem custos Azure no período.</Typography></TableCell></TableRow>
                      )}
                      {(financialInsights?.azure || []).map((row) => (
                        <TableRow key={`${row.meter_name}-${row.meter_subcategory}`}>
                          <TableCell>
                            <Typography variant="body2" fontWeight={600}>{row.meter_name}</Typography>
                            <Typography variant="caption" color="text.secondary">
                              {row.meter_subcategory} · {row.cost_category === "fixed_license" ? "Licença fixa" : row.cost_category === "variable_consumption" ? "Consumo variável" : "Fora de Copilot"}
                            </Typography>
                          </TableCell>
                          <TableCell align="right">{num.format(Math.round(row.quantity || 0))}</TableCell>
                          <TableCell align="right"><Typography variant="body2" fontWeight={600}>{money.format(row.cost_brl || 0)}</Typography></TableCell>
                        </TableRow>
                      ))}
                      {(financialInsights?.totals?.current_projection_brl || 0) > 0 && (
                        <>
                          <TableRow>
                            <TableCell>
                              <Typography variant="body2" fontWeight={600} color="text.secondary">Projeção (restante do mês)</Typography>
                              <Typography variant="caption" color="text.secondary">forecast da Gold para os dias restantes</Typography>
                            </TableCell>
                            <TableCell align="right">—</TableCell>
                            <TableCell align="right"><Typography variant="body2" fontWeight={600}>{money.format(financialInsights?.totals?.current_forecast_brl || 0)}</Typography></TableCell>
                          </TableRow>
                          <TableRow sx={{ bgcolor: "#f8fafc" }}>
                            <TableCell><Typography variant="body2" fontWeight={700}>Total projetado (mês corrente)</Typography></TableCell>
                            <TableCell align="right" />
                            <TableCell align="right"><Typography variant="body2" fontWeight={800}>{money.format(financialInsights?.totals?.current_projection_brl || 0)}</Typography></TableCell>
                          </TableRow>
                        </>
                      )}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Box>
              <Box flex={1}>
                <Typography variant="subtitle2" fontWeight={700} mb={1}>Alertas de qualidade</Typography>
                {(financialInsights?.github_alerts || financialInsights?.github?.filter((r) => r.adoption_signal === "financial_without_token_telemetry") || []).slice(0, 3).map((row, i) => (
                  <Alert key={`${row.user_login}-${row.model}-${i}`} severity="warning" sx={{ py: 0, mb: 0.5 }}>
                    {row.user_login || "usuário"}: custo sem tokens detalhados
                  </Alert>
                ))}
                {!(financialInsights?.github_alerts || financialInsights?.github?.filter((r) => r.adoption_signal === "financial_without_token_telemetry") || []).length && (
                  <Typography variant="body2" color="text.secondary">Nenhum alerta no período (tokens detalhados disponíveis).</Typography>
                )}
                <Alert severity="info" sx={{ mt: 1.5, borderRadius: 2 }}>
                  <Typography variant="subtitle2" fontWeight={700} mb={0.5}>Conciliação GitHub × Azure</Typography>
                  <Box display="flex" justifyContent="space-between"><Typography variant="body2">Custo de mercado GitHub (R$)</Typography><Typography variant="body2" fontWeight={600}>{money.format(financialInsights?.totals?.github_market_cost_brl || 0)}</Typography></Box>
                  <Box display="flex" justifyContent="space-between"><Typography variant="body2">Azure realizado parcial (R$)</Typography><Typography variant="body2" fontWeight={600}>{money.format(financialInsights?.totals?.azure_cost_brl || 0)}</Typography></Box>
                  <Box display="flex" justifyContent="space-between"><Typography variant="body2">Projeção do período (realizado + restante)</Typography><Typography variant="body2" fontWeight={700}>{money.format(financialInsights?.totals?.current_projection_total_brl || 0)}</Typography></Box>
                  <Box display="flex" justifyContent="space-between" sx={{ borderTop: "1px dashed #cbd5e1", mt: 0.5, pt: 0.5 }}>
                    <Typography variant="body2" fontWeight={700}>Δ valor de mercado − (projetado + realizado)</Typography>
                    <Typography variant="body2" fontWeight={800} color="warning.main">{money.format(financialInsights?.totals?.reconciliation_market_vs_paid_brl || 0)}</Typography>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                    Projeção do período = SKUs Azure realizados + forecast somente dos dias restantes. Δ = valor de mercado do consumo − projeção total.
                  </Typography>
                </Alert>
              </Box>
            </Stack>
          </Paper>

          <Typography variant="h5" mb={2} fontWeight="bold" color="text.primary">Otimização e Eficiência</Typography>
          
          <Stack direction={{ xs: "column", lg: "row" }} gap={3} mb={4}>
            <Paper elevation={0} sx={{ flex: 1, p: 3, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "#fff1f2" }}>
              <Typography variant="h6" color="error.main" mb={1}>Oportunidade de Revogação</Typography>
              <Typography variant="body2" color="text.secondary" mb={3}>Licenças sem uso detectado.</Typography>
              <Typography variant="h3" fontWeight={700} color="error.dark" mb={3}>{money.format(metrics?.potential_savings || 0)} <Typography component="span" variant="body1">/ mês</Typography></Typography>
              <Stack spacing={1.5}>
                <Box onClick={() => handleCardFilterClick("inactive_30")} sx={{ p: 1.5, borderRadius: 1.5, cursor: "pointer", display: "flex", justifyContent: "space-between", bgcolor: "rgba(255,255,255,0.7)", "&:hover": { bgcolor: "#fee2e2" } }}>
                  <Typography fontWeight={500} color="text.secondary">Inativos +30 dias</Typography>
                  <Typography fontWeight={700}>{metrics?.inactive_30 || 0}</Typography>
                </Box>
                <Box onClick={() => handleCardFilterClick("inactive_60")} sx={{ p: 1.5, borderRadius: 1.5, cursor: "pointer", display: "flex", justifyContent: "space-between", bgcolor: "rgba(255,255,255,0.7)", "&:hover": { bgcolor: "#fee2e2" } }}>
                  <Typography fontWeight={500} color="text.secondary">Inativos +60 dias</Typography>
                  <Typography fontWeight={700}>{metrics?.inactive_60 || 0}</Typography>
                </Box>
                <Box onClick={() => handleCardFilterClick("inactive_90")} sx={{ p: 1.5, borderRadius: 1.5, cursor: "pointer", display: "flex", justifyContent: "space-between", bgcolor: "rgba(255,255,255,0.7)", "&:hover": { bgcolor: "#fee2e2" } }}>
                  <Typography fontWeight={500} color="error.main">Inativos +90 dias</Typography>
                  <Typography fontWeight={700} color="error.main">{metrics?.inactive_90 || 0}</Typography>
                </Box>
              </Stack>
            </Paper>

            <Paper elevation={0} sx={{ flex: 1, p: 3, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "#f0fdf4" }}>
              <Typography variant="h6" color="success.main" mb={1}>Uso Adequado e Engajamento</Typography>
              <Typography variant="body2" color="text.secondary" mb={3}>Distribuição por volume de interações.</Typography>
              <Stack spacing={2}>
                <Box onClick={() => handleCardFilterClick("high")} sx={{ p: 1.5, borderRadius: 1.5, cursor: "pointer", bgcolor: "rgba(255,255,255,0.7)", "&:hover": { bgcolor: "#dcfce7" } }}>
                  <Box display="flex" justifyContent="space-between" alignItems="center">
                    <Typography fontWeight={600}>Alto Engajamento</Typography>
                    <Typography fontWeight={700}>{metrics?.utilization.high || 0} devs</Typography>
                  </Box>
                </Box>
                <Box onClick={() => handleCardFilterClick("low")} sx={{ p: 1.5, borderRadius: 1.5, cursor: "pointer", bgcolor: "rgba(255,255,255,0.7)", "&:hover": { bgcolor: "#fef08a" } }}>
                  <Box display="flex" justifyContent="space-between" alignItems="center">
                    <Typography fontWeight={600}>Baixo Engajamento</Typography>
                    <Typography fontWeight={700}>{metrics?.utilization.low || 0} devs</Typography>
                  </Box>
                </Box>
                <Box onClick={() => handleCardFilterClick("zero")} sx={{ p: 1.5, borderRadius: 1.5, cursor: "pointer", bgcolor: "rgba(255,255,255,0.7)", "&:hover": { bgcolor: "#fee2e2" } }}>
                  <Box display="flex" justifyContent="space-between" alignItems="center">
                    <Typography fontWeight={600} color="error.main">Sem Engajamento</Typography>
                    <Typography fontWeight={700} color="error.main">{metrics?.utilization.zero || 0} devs</Typography>
                  </Box>
                </Box>
              </Stack>
            </Paper>
          </Stack>

          <Paper ref={tableSectionRef} elevation={0} sx={{ p: 3, border: "1px solid", borderColor: "divider", borderRadius: 2, mb: 4, bgcolor: "background.paper" }}>
            <Box mb={2}>
              <Typography variant="h5" fontWeight="bold" color="primary.main">Relação de Desenvolvedores, Créditos e Engajamento</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>Resumo individual carregado da Gold por usuário/período. Clique em qualquer linha para ver o breakdown da Gold diária por modelo.</Typography>
            </Box>

            <Alert severity="info" sx={{ mb: 3, borderRadius: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>Critérios de Avaliação e Franquia</Typography>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ fontSize: "0.825rem" }}>
                <Box>🟢 Alto: ≥ 20 interações</Box>
                <Box>🟡 Baixo: 1-19 interações</Box>
                <Box>🔴 Sem uso: 0 interações</Box>
              </Stack>
              <Typography variant="caption" display="block" sx={{ mt: 1.5 }} color="text.secondary">
                Governança individual: franquia documentada pelo GitHub — Business 1.900 AI credits/mês/licença, Enterprise 3.900. Os créditos consumidos vêm do relatório real de AI Credits (ai_usage_report); consumo acima da franquia individual é sinalizado mesmo que o billing coletivo o cubra.
                {creditPeriod?.end ? ` Dados disponíveis até ${creditPeriod.end}.` : ""}
              </Typography>
            </Alert>

            {(poolBenchmark?.covered_consumption_above_standard || 0) > 0 && (
              <Alert severity="warning" sx={{ mb: 2, borderRadius: 2 }}>
                Consumo coletivo acima da franquia contratual padrão: {compact(poolBenchmark?.covered_consumption_above_standard || 0)} créditos.
                O dashboard não distribui essa diferença entre usuários até que a fonte informe a origem do crédito adicional.
              </Alert>
            )}

            <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center" mb={2.5}>
              <Chip label={`Todos (${num.format(engagementCounts.all)})`} clickable color={selectedFilter === "all" ? "primary" : "default"} onClick={() => { setSelectedFilter("all"); setPage(0); }} />
              <Chip label="Alto" clickable color={selectedFilter === "high" ? "success" : "default"} onClick={() => { setSelectedFilter("high"); setPage(0); }} />
              <Chip label="Baixo" clickable color={selectedFilter === "low" ? "warning" : "default"} onClick={() => { setSelectedFilter("low"); setPage(0); }} />
              <Chip label="Sem Uso" clickable color={selectedFilter === "zero" ? "error" : "default"} onClick={() => { setSelectedFilter("zero"); setPage(0); }} />
              <Chip label={`Sem telemetria IDE (${num.format(engagementCounts.financial_only)})`} clickable color={selectedFilter === "financial_only" ? "warning" : "default"} onClick={() => { setSelectedFilter("financial_only"); setPage(0); }} />
            </Stack>

            <TextField fullWidth size="small" placeholder="Buscar..." value={searchQuery} onChange={(e) => { setSearchQuery(e.target.value); setPage(0); }} sx={{ mb: 2 }} />

            {engagementLoading ? (
              <Box p={5} display="flex" justifyContent="center"><CircularProgress size={24} /></Box>
            ) : (
              <TableContainer sx={{ border: "1px solid #e2e8f0", borderRadius: 1.5, maxHeight: 600 }}>
                <Table stickyHeader size="small">
                  <TableHead>
                    <TableRow sx={{ "& th": { bgcolor: "#f8fafc", fontWeight: 700 } }}>
                      <TableCell onClick={() => handleSort("email")} sx={{ cursor: "pointer" }}>Desenvolvedor</TableCell>
                      <TableCell align="right" onClick={() => handleSort("total_credits")} sx={{ cursor: "pointer" }}>Créditos consumidos no mês</TableCell>
                      <TableCell align="right">Crédito individual</TableCell>
                      <TableCell align="right">Excesso sobre limite</TableCell>
                      <TableCell align="right" onClick={() => handleSort("lines_accepted")} sx={{ cursor: "pointer" }}>Linhas Aceitas</TableCell>
                      <TableCell align="right" onClick={() => handleSort("total_interactions")} sx={{ cursor: "pointer" }}>Interações</TableCell>
                      <TableCell align="center">Avaliação</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {paginatedUsers.map((u, i) => (
                      <TableRow key={i} hover onClick={() => handleUserRowClick(u)} sx={{ cursor: "pointer", "&:hover": { bgcolor: "#f1f5f9" } }}>
                        <TableCell>
                          <Typography variant="body2" fontWeight={600} color="primary.main">{u.email}</Typography>
                          <Typography variant="caption" color="text.secondary">login: {u.user_login}</Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography variant="body2" fontWeight={700}>
                            {compact(u.total_credits)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography variant="body2">
                            {u.individual_credit_allowance == null ? "—" : num.format(u.individual_credit_allowance)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography variant="body2" color={(u.individual_credit_balance || 0) < 0 ? "error.main" : "success.main"}>
                            {u.individual_credit_balance == null
                              ? "—"
                              : u.individual_credit_balance < 0
                                ? `+${num.format(Math.round(-u.individual_credit_balance))}`
                                : "Dentro do limite"}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">{compact(u.lines_accepted)}</TableCell>
                        <TableCell align="right">{compact(u.total_interactions)}</TableCell>
                        <TableCell align="center">
                          {u.consumption_signal === "alto_consumo_baixa_telemetria" ? (
                            <Chip size="small" label="Alto custo / baixa telemetria" color="error" />
                          ) : (
                            <Chip size="small" label={u.engagement_level === "high" ? "Alto" : u.engagement_level === "low" ? "Baixo" : u.engagement_level === "financial_only" ? "Sem telemetria IDE" : "Sem Uso"} color={u.engagement_level === "high" ? "success" : u.engagement_level === "low" || u.engagement_level === "financial_only" ? "warning" : "error"} />
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
            <TablePagination component="div" count={engagementUsers.length} page={page} onPageChange={(_, p) => setPage(p)} rowsPerPage={rowsPerPage} onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }} rowsPerPageOptions={[10, 25, 50, 100]} />
          </Paper>

          <Drawer anchor="right" open={drawerOpen} onClose={() => setDrawerOpen(false)} PaperProps={{ sx: { width: { xs: "100%", sm: 756 }, p: 3, bgcolor: "#f8fafc" } }}>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
              <Stack direction="row" alignItems="center" gap={1}><Psychology color="primary" /><Typography variant="h6" fontWeight="bold">Perfil de Consumo</Typography></Stack>
              <IconButton onClick={() => setDrawerOpen(false)}><Close /></IconButton>
            </Box>
            {selectedUser && (
              <Stack spacing={2.5}>
                <Paper elevation={0} sx={{ p: 2, border: "1px solid #e2e8f0", borderRadius: 2 }}>
                  <Typography variant="subtitle1" fontWeight={700} color="primary.main">{selectedUser.email}</Typography>
                  <Typography variant="caption" color="text.secondary">Login: {selectedUser.user_login} • Dept: {selectedUser.dept}</Typography>
                </Paper>

                {/* Banner específico se for Conta de Faturamento/Infraestrutura */}
                {userDetails?.is_billing_account && (
                  <Alert severity="info" sx={{ borderRadius: 2 }}>
                    <Typography variant="subtitle2" fontWeight={700}>Conta de Faturamento Corporativo / Infraestrutura</Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                      Origem: <strong>{userDetails.billing_origin}</strong>. Registro referente ao rateio contábil da fatura corporativa (<strong>{compact(userDetails.contractual_invoice_credits || 0)}</strong> créditos alocados na fatura institucional).
                    </Typography>
                    <Typography variant="caption" sx={{ display: "block", mt: 0.5, fontWeight: 600, color: "success.main" }}>
                      ✓ Uso Operacional de IA: 0 créditos (Sem Overage de Uso de Desenvolvedor)
                    </Typography>
                  </Alert>
                )}

                <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #e2e8f0", borderRadius: 2, bgcolor: "#f8fafc" }}>
                  <Box display="flex" justifyContent="space-between" mb={1}>
                    <Typography variant="subtitle2" fontWeight={700} color="primary.main">Consumo financeiro no pool compartilhado</Typography>
                  </Box>
                  <Typography variant="h4" fontWeight={800}>{compact(selectedUser.total_credits)} <Typography component="span" variant="body2">créditos de IA no mês</Typography></Typography>
                  <Typography variant="caption" color="text.secondary">Não é possível inferir excedente individual: as licenças contribuem para uma franquia compartilhada.</Typography>
                </Paper>

                <Paper elevation={0} sx={{ p: 2, border: "1px solid #e2e8f0", borderRadius: 2 }}>
                  <Typography variant="subtitle1" fontWeight={700} mb={1}>Motores e Modelos Utilizados (LLMs)</Typography>
                  {drawerLoading ? <CircularProgress size={24} /> : userDetails?.models && userDetails.models.length > 0 ? (
                    <>
                    <TableContainer sx={{ overflowX: "auto", "& table": { minWidth: 560 } }}>
                      <Table size="small" stickyHeader>
                        <TableHead><TableRow sx={{ "& th": { bgcolor: "#f8fafc", fontWeight: 700 } }}>
                          <TableCell>Modelo</TableCell>
                          <TableCell align="right">Linhas</TableCell>
                          <TableCell align="right">Chats</TableCell>
                          <TableCell align="right">Tokens</TableCell>
                          <TableCell align="right">Share %</TableCell>
                          <TableCell>Linguagem</TableCell>
                        </TableRow></TableHead>
                        <TableBody>
                          {userDetails.models.map((m, idx) => (
                            <TableRow key={idx}>
                              <TableCell>{m.model_name}</TableCell>
                              <TableCell align="right">{compact(m.lines_accepted)}</TableCell>
                              <TableCell align="right">{compact(m.acceptances_count)}</TableCell>
                              <TableCell align="right">
                                <Box display="flex" alignItems="center" justifyContent="flex-end" gap={0.5}>
                                  <Typography variant="body2" fontWeight={m.token_source === "real" ? 700 : 400}>
                                    {compact(m.total_tokens || 0)}
                                  </Typography>
                                  {m.token_source === "estimado" && (
                                    <Chip size="small" label="estimado" variant="outlined" sx={{ height: 18, fontSize: "0.65rem" }} />
                                  )}
                                </Box>
                              </TableCell>
                              <TableCell align="right">{m.share_pct}%</TableCell>
                              <TableCell>{m.primary_language}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                    <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                      Tokens reais vêm do relatório financeiro de AI Credits (input + output + cache) quando disponíveis;
                      os demais são estimados por linhas aceitas × 7 tokens.
                    </Typography>
                    </>
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ p: 1 }}>
                      {userDetails?.is_billing_account 
                        ? "Nenhum modelo de IDE acionado (consumo contratual/fatura de centro de custo)."
                        : "Nenhum consumo de modelo registrado na IDE para este usuário no período."}
                    </Typography>
                  )}
                </Paper>
              </Stack>
            )}
          </Drawer>

          {history.length > 0 && (
            <Paper elevation={0} sx={{ p: 3, border: "1px solid #e2e8f0", borderRadius: 2, mb: 4 }}>
              <Typography variant="h6" mb={0.5}>Evolução de Créditos (Consumo vs Franquia)</Typography>
              <Typography variant="caption" color="text.secondary" display="block" mb={2}>
                Últimos 12 meses. Desde junho/2026 a Microsoft/GitHub alterou o formato de cobrança do Copilot para AI Credits
                (por token) — o consumo mensal de créditos passou a existir a partir daí; meses anteriores aparecem zerados.
                Consumo = relatório real de AI Credits (ai_usage_report). O pool efetivo é inferido por
                consumo menos o overage efetivamente faturado, incluindo créditos promocionais quando existirem;
                a linha tracejada mostra somente a franquia contratada (licenças × 1.900 créditos).
              </Typography>
              <Plot
                data={[
                  { x: history.slice(-12).map(h => periodLabel(h.period)), y: history.slice(-12).map(h => Math.max(0, (h.total_credits || 0) - (h.overage_credits || 0))), type: "bar" as const, name: "Consumidos (pool coberto)", marker: { color: "#2563eb" } },
                  { x: history.slice(-12).map(h => periodLabel(h.period)), y: history.slice(-12).map(h => h.overage_credits || 0), type: "bar" as const, name: "Overage pago (extra)", marker: { color: "#dc2626" } },
                  { x: history.slice(-12).map(h => periodLabel(h.period)), y: history.slice(-12).map(h => h.effective_credits_pool ?? h.included_credits_pool), type: "scatter" as const, mode: "lines+markers" as const, name: "Pool efetivo (incl. promoção)", line: { color: "#d97706", width: 2 } },
                  { x: history.slice(-12).map(h => periodLabel(h.period)), y: history.slice(-12).map(h => h.contracted_credits_pool ?? h.included_credits_pool), type: "scatter" as const, mode: "lines" as const, name: "Franquia contratada", line: { color: "#64748b", width: 1.5, dash: "dash" } },
                ]}
                layout={{ autosize: true, height: 340, barmode: "stack", margin: { l: 70, r: 20, b: 40, t: 20 }, hovermode: "x unified", yaxis: { tickformat: ",.2s" } }}
                useResizeHandler
                style={{ width: "100%" }}
              />
              {/* Cards de resumo de créditos removidos da UI a pedido do negócio.
                  O código é preservado para eventual reativação. */}
              {/* <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 2 }}>
                {(() => {
                  const last = history[history.length - 1];
                  const totalConsumed = history.reduce((s, h) => s + (h.total_credits || 0), 0);
                  const totalOverage = history.reduce((s, h) => s + (h.overage_credits || 0), 0);
                  return (
                    <>
                      <Box sx={{ flex: 1, p: 1.5, border: "1px solid #e2e8f0", borderRadius: 1.5, bgcolor: "#eff6ff" }}>
                        <Typography variant="caption" color="text.secondary">Crédito real consumido</Typography>
                        <Typography variant="h6" fontWeight={700}>{compact(totalConsumed)}</Typography>
                        <Typography variant="caption" color="text.secondary">últimos 12 meses</Typography>
                      </Box>
                      <Box sx={{ flex: 1, p: 1.5, border: "1px solid #e2e8f0", borderRadius: 1.5, bgcolor: "#fffbeb" }}>
                        <Typography variant="caption" color="text.secondary">Pool efetivo coberto</Typography>
                        <Typography variant="h6" fontWeight={700}>{compact((last?.effective_credits_pool ?? last?.included_credits_pool) || 0)}</Typography>
                        <Typography variant="caption" color="text.secondary">mês atual · inclui promoção inferida</Typography>
                      </Box>
                      <Box sx={{ flex: 1, p: 1.5, border: "1px solid #e2e8f0", borderRadius: 1.5, bgcolor: "#fef2f2" }}>
                        <Typography variant="caption" color="text.secondary">Overage pago (consumo extra)</Typography>
                        <Typography variant="h6" fontWeight={700}>{compact(totalOverage)}</Typography>
                        <Typography variant="caption" color="text.secondary">créditos excedentes faturados (net_amount ÷ US$ 0,01)</Typography>
                      </Box>
                    </>
                  );
                })()}
              </Stack> */}
            </Paper>
          )}

          {costEvolution.length > 0 && (
            <Paper elevation={0} sx={{ p: 3, border: "1px solid #e2e8f0", borderRadius: 2, mb: 4 }}>
              <Typography variant="h6" mb={0.5}>Evolução do Custo Realizado — GitHub (PricingCategory = GitHub)</Typography>
              <Typography variant="body2" color="text.secondary" mb={2}>
                Total mensal = soma do realizado da Gold <b>dest_dm_cost_copilot_custo</b> (classificação PricingCategory=GitHub, mesma usada na criação da Gold). Julho ≈ 190 mil BRL.
                {" "}<b>Mês corrente ({periodLabel(OPEN_PERIOD)}): realizado parcial + projeção do restante</b>; meses futuros = projeção (forecast da Gold) até o fim do horizonte — padrão FinOps de mês em andamento.
              </Typography>
              <Plot
                data={[
                  ...[...new Set(costEvolution.flatMap(e => e.segments.map(s => s.subcategory)))].map((sub, i) => ({
                    x: costEvolution.map(e => periodLabel(e.period)),
                    y: costEvolution.map(e => (e.segments.find(s => s.subcategory === sub) || { realized: 0 }).realized || 0),
                    type: "bar" as const,
                    name: sub,
                    marker: {
                      color: sub.startsWith("Projeção")
                        ? "rgba(148, 163, 184, 0.55)"
                        : ["#2563eb", "#059669", "#d97706", "#7c3aed", "#dc2626"][i % 5],
                    },
                  })),
                  {
                    x: costEvolution.map(e => periodLabel(e.period)),
                    y: costEvolution.map(e => (e.is_current_month || e.is_forecast ? e.projected_total : e.total_realized) || 0),
                    type: "scatter" as const,
                    mode: "lines+markers" as const,
                    name: "Total",
                    line: { color: "#0f172a", width: 2 },
                  },
                ]}
                layout={{
                  autosize: true,
                  height: 360,
                  barmode: "stack",
                  margin: { l: 70, r: 20, b: 40, t: 20 },
                  hovermode: "x unified",
                  yaxis: { tickformat: ",.2s" },
                }}
                useResizeHandler
                style={{ width: "100%" }}
              />
            </Paper>
          )}

          <Paper elevation={0} sx={{ p: 3, border: "1px solid #e2e8f0", borderRadius: 2, mb: 4 }}>
            <Typography variant="h6" mb={0.5}>Engajamento e Custo por Modelo de IA</Typography>
            <Typography variant="caption" color="text.secondary" display="block" mb={2}>
              Custo por MM de tokens = créditos reais do modelo × preço de mercado do crédito (derivado do relatório de AI Credits:
              gross_amount ÷ ai_credits ≈ US$ 0,01/crédito) ÷ tokens do modelo (input+output+cache; "estimado" = linhas × 7 quando o
              relatório não traz tokens). A Microsoft concedeu créditos extras até setembro/2026 — consumidores intensos usam o pool
              compartilhado (créditos de quem não consome); o preço de mercado reflete o valor real do consumo extra.
            </Typography>
            <Box sx={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead><tr style={{ borderBottom: "2px solid #e2e8f0" }}>
                  <th style={{ padding: "10px", textAlign: "left" }}>Modelo</th>
                  <th style={{ padding: "10px", textAlign: "left" }}>Devs</th>
                  <th style={{ padding: "10px", textAlign: "left" }}>Tokens</th>
                  <th style={{ padding: "10px", textAlign: "left" }}>Créditos</th>
                  <th style={{ padding: "10px", textAlign: "left" }}>Custo / MM tokens</th>
                  <th style={{ padding: "10px", textAlign: "left" }}>Share %</th>
                </tr></thead>
                <tbody>{models.map((m, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: "10px", textAlign: "left" }}>{m.model_name}</td>
                    <td style={{ padding: "10px", textAlign: "left" }}>{m.active_users}</td>
                    <td style={{ padding: "10px", textAlign: "left" }}>
                      {compact(m.total_tokens || 0)}
                      {m.token_source === "estimado" && <span style={{ color: "#b45309", fontSize: "0.72rem" }}> est.</span>}
                    </td>
                    <td style={{ padding: "10px", textAlign: "left" }}>{compact(m.total_credits)}</td>
                    <td style={{ padding: "10px", textAlign: "left", fontWeight: 600 }}>
                      {m.cost_per_million_tokens_brl == null ? "—" : money.format(m.cost_per_million_tokens_brl)}
                    </td>
                    <td style={{ padding: "10px", textAlign: "left" }}>{m.credit_share_pct}%</td>
                  </tr>))}</tbody>
              </table>
            </Box>
          </Paper>
        </>
      )}
        </Paper>
      </Box>
    </Box>
  );
}
