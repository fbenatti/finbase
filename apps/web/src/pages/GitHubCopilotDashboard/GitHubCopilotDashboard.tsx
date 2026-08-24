import React, { useState, useEffect, useRef } from "react";
import {
  Box, Typography, Paper, Stack, Card, CardContent, FormControl, InputLabel,
  Select, MenuItem, CircularProgress, Chip, TextField, InputAdornment,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TablePagination, Alert, Drawer, IconButton, Divider
} from "@mui/material";
import {
  Search, InfoOutlined, CheckCircle, WarningAmber, CancelOutlined,
  AccessTime, Person, ArrowDownward, ArrowUpward, Close,
  Psychology, Bolt, Code, ChatBubbleOutline
} from "@mui/icons-material";
import Plot from "react-plotly.js";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const moneyUSD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const num = new Intl.NumberFormat("pt-BR");

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
  azure: Array<{ meter_subcategory: string; meter_name: string; product_name: string; cost_category: string; usage_start_date?: string; usage_end_date?: string; quantity: number; unit_cost_brl: number | null; cost_brl: number; cost_usd: number; rows: number }>;
  totals?: { github_total_tokens: number; github_cost_per_million_tokens: number | null; azure_cost_brl: number; reconciliation_delta_brl: number };
}

const AVAILABLE_PERIODS = [
  { value: "202608", label: "Agosto / 2026 (Atual)" },
  { value: "202607", label: "Julho / 2026" },
  { value: "202606", label: "Junho / 2026" },
  { value: "202605", label: "Maio / 2026" },
  { value: "202604", label: "Abril / 2026" },
  { value: "202603", label: "Março / 2026" },
  { value: "202602", label: "Fevereiro / 2026" },
  { value: "202601", label: "Janeiro / 2026" },
];

export default function GitHubCopilotDashboard() {
  const [metrics, setMetrics] = useState<CopilotMetrics | null>(null);
  const [models, setModels] = useState<CopilotModel[]>([]);
  const [history, setHistory] = useState<CopilotHistory[]>([]);
  const [financialInsights, setFinancialInsights] = useState<FinancialInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState("202608");

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
        const [ovRes, mdRes, hsRes] = await Promise.all([
          fetch("/api/ai-copilot/overview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then((r) => r.json()),
          fetch("/api/ai-copilot/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then((r) => r.json()),
          fetch("/api/ai-copilot/history", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then((r) => r.json()),
        ]);
        if (ovRes.metrics) setMetrics(ovRes.metrics);
        if (mdRes.models) setModels(mdRes.models);
        if (hsRes.history) setHistory(hsRes.history);
        // A consulta financeira varre a Bronze; executá-la depois das métricas
        // principais evita cinco consultas pesadas simultâneas no Trino.
        const fiRes = await fetch("/api/ai-copilot/financial-insights", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then((r) => r.json());
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
        const res = await fetch("/api/ai-copilot/engagement-users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then((r) => r.json());
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
      const res = await fetch("/api/ai-copilot/user-model-details", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_login: user.user_login, email: user.email, period: period }),
      }).then((r) => r.json());
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

  const card = (label: string, value: string, color: string) => (
    <Card sx={{ flex: 1, minWidth: 170, borderTop: `3px solid ${color}` }}>
      <CardContent>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h5" fontWeight={700}>{value}</Typography>
      </CardContent>
    </Card>
  );

  return (
    <Box p={3}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "center" }} gap={2} mb={3}>
        <Box>
          <Typography variant="h4" fontWeight="bold" color="primary.main">GitHub Copilot — Dashboard</Typography>
          <Typography variant="body1" color="text.secondary" sx={{ mt: 0.5 }}>Métricas de adoção, governança de licenças e consumo financeiro de IA por desenvolvedor.</Typography>
        </Box>
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
          <Stack direction="row" gap={2} flexWrap="wrap" mb={4}>
            {card("Licenças Ativas", `${metrics?.active_licenses || 0}`, "#64748B")}
            {card("Linhas Aceitas", num.format(metrics?.lines_accepted || 0), "#2563EB")}
            {card("Interações", num.format(metrics?.chat_turns || 0), "#0284C7")}
            {card("Créditos de IA", `${num.format(Math.round(metrics?.total_credits || 0))}`, "#F59E0B")}
            {card("Custo (USD)", moneyUSD.format(metrics?.cloud_cost_usd || 0), "#059669")}
            {card("Custo (BRL)", money.format(metrics?.cloud_cost || 0), "#16A34A")}
          </Stack>

          <Paper elevation={0} sx={{ p: 3, border: "1px solid", borderColor: "divider", borderRadius: 2, mb: 4 }}>
            <Typography variant="h5" fontWeight="bold" color="primary.main" mb={1}>FinOps: GitHub × Azure</Typography>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Os custos são reconciliados por SKU. O custo por MM só aparece quando o relatório financeiro fornece tokens detalhados.
            </Typography>
            <Stack direction={{ xs: "column", md: "row" }} gap={3}>
              <Box flex={1}>
                <Typography variant="subtitle2" fontWeight={700}>Custo líquido por MM de tokens</Typography>
                <Typography variant="h5" fontWeight={700}>
                  {financialInsights?.totals?.github_cost_per_million_tokens == null ? "Não disponível" : money.format(financialInsights.totals.github_cost_per_million_tokens)}
                </Typography>
                <Typography variant="caption" color="text.secondary">Tokens processados: {num.format(financialInsights?.totals?.github_total_tokens || 0)}</Typography>
              </Box>
              <Box flex={1}>
                <Typography variant="subtitle2" fontWeight={700}>Licenças fixas Azure</Typography>
                {financialInsights?.azure?.filter((row) => row.cost_category === "fixed_license").map((row) => (
                  <Box key={`${row.meter_name}-${row.product_name}`} display="flex" justifyContent="space-between" gap={2}>
                    <Typography variant="body2">{row.meter_name}: {num.format(Math.round(row.quantity || 0))} un.</Typography>
                    <Typography variant="body2" fontWeight={600}>{money.format(row.cost_brl || 0)}</Typography>
                  </Box>
                ))}
                {financialInsights?.azure?.[0]?.usage_end_date && <Typography variant="caption" color="text.secondary">Cobertura Azure até {financialInsights.azure[0].usage_end_date}; mês parcial não é custo realizado.</Typography>}
              </Box>
              <Box flex={1}>
                <Typography variant="subtitle2" fontWeight={700}>Consumo variável Azure</Typography>
                {financialInsights?.azure?.filter((row) => row.cost_category === "variable_consumption").map((row) => (
                  <Box key={`${row.meter_name}-${row.product_name}`} display="flex" justifyContent="space-between" gap={2}>
                    <Typography variant="body2">{row.meter_name}: {num.format(Math.round(row.quantity || 0))}</Typography>
                    <Typography variant="body2" fontWeight={600}>{money.format(row.cost_brl || 0)}</Typography>
                  </Box>
                ))}
                {!financialInsights?.azure?.some((row) => row.cost_category === "variable_consumption") && <Typography variant="body2" color="text.secondary">Nenhum consumo variável faturado.</Typography>}
                {financialInsights?.azure?.filter((row) => row.cost_category === "outside_copilot").map((row) => (
                  <Typography key={`${row.meter_name}-${row.meter_subcategory}`} variant="caption" display="block" color="text.secondary" sx={{ mt: 0.5 }}>
                    Fora de Copilot: {row.meter_subcategory} — {money.format(row.cost_brl || 0)}
                  </Typography>
                ))}
                <Typography variant="subtitle2" fontWeight={700} sx={{ mt: 1.5 }}>Alertas de qualidade</Typography>
                {(financialInsights?.github_alerts || financialInsights?.github?.filter((r) => r.adoption_signal === "financial_without_token_telemetry") || []).slice(0, 3).map((row, i) => (
                  <Alert key={`${row.user_login}-${row.model}-${i}`} severity="warning" sx={{ py: 0, mb: 0.5 }}>
                    {row.user_login || "usuário"}: custo sem tokens detalhados
                  </Alert>
                ))}
                {!(financialInsights?.github_alerts || financialInsights?.github?.filter((r) => r.adoption_signal === "financial_without_token_telemetry") || []).length && <Typography variant="body2" color="text.secondary">Nenhum alerta no período.</Typography>}
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
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>Detalhamento individual. Clique em qualquer linha para ver o breakdown completo.</Typography>
            </Box>

            <Alert severity="info" sx={{ mb: 3, borderRadius: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} gutterBottom>Critérios de Avaliação e Franquia</Typography>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ fontSize: "0.825rem" }}>
                <Box>🟢 Alto: ≥ 20 interações</Box>
                <Box>🟡 Baixo: 1-19 interações</Box>
                <Box>🔴 Sem uso: 0 interações</Box>
              </Stack>
              <Typography variant="caption" display="block" sx={{ mt: 1.5 }} color="text.secondary">
                Governança individual: Business recebe 1.900 créditos/mês por licença. Créditos consumidos vêm do relatório financeiro; qualquer consumo acima desse limite é sinalizado mesmo que o billing coletivo o cubra.
                {creditPeriod?.end ? ` Dados disponíveis até ${creditPeriod.end}.` : ""}
              </Typography>
            </Alert>

            {(poolBenchmark?.covered_consumption_above_standard || 0) > 0 && (
              <Alert severity="warning" sx={{ mb: 2, borderRadius: 2 }}>
                Consumo coletivo acima da franquia contratual padrão: {num.format(Math.round(poolBenchmark?.covered_consumption_above_standard || 0))} créditos.
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
                            {num.format(Math.round(u.total_credits))}
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
                        <TableCell align="right">{num.format(u.lines_accepted)}</TableCell>
                        <TableCell align="right">{num.format(u.total_interactions)}</TableCell>
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

          <Drawer anchor="right" open={drawerOpen} onClose={() => setDrawerOpen(false)} PaperProps={{ sx: { width: { xs: "100%", sm: 540 }, p: 3, bgcolor: "#f8fafc" } }}>
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
                      Origem: <strong>{userDetails.billing_origin}</strong>. Registro referente ao rateio contábil da fatura corporativa (<strong>{num.format(Math.round(userDetails.contractual_invoice_credits || 0))}</strong> créditos alocados na fatura institucional).
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
                  <Typography variant="h4" fontWeight={800}>{num.format(Math.round(selectedUser.total_credits))} <Typography component="span" variant="body2">créditos de IA no mês</Typography></Typography>
                  <Typography variant="caption" color="text.secondary">Não é possível inferir excedente individual: as licenças contribuem para uma franquia compartilhada.</Typography>
                </Paper>

                <Paper elevation={0} sx={{ p: 2, border: "1px solid #e2e8f0", borderRadius: 2 }}>
                  <Typography variant="subtitle1" fontWeight={700} mb={1}>Motores e Modelos Utilizados (LLMs)</Typography>
                  {drawerLoading ? <CircularProgress size={24} /> : userDetails?.models && userDetails.models.length > 0 ? (
                    <Table size="small">
                      <TableHead><TableRow><TableCell>Modelo</TableCell><TableCell align="right">Linhas</TableCell><TableCell align="right">Chats</TableCell><TableCell align="right">Share %</TableCell><TableCell>Linguagem</TableCell></TableRow></TableHead>
                      <TableBody>
                        {userDetails.models.map((m, idx) => (
                          <TableRow key={idx}>
                            <TableCell>{m.model_name}</TableCell>
                            <TableCell align="right">{num.format(m.lines_accepted)}</TableCell>
                            <TableCell align="right">{num.format(m.acceptances_count)}</TableCell>
                            <TableCell align="right">{m.share_pct}%</TableCell>
                            <TableCell>{m.primary_language}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
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
            <Stack direction={{ xs: "column", lg: "row" }} gap={3} mb={4}>
              <Paper elevation={0} sx={{ flex: 1, p: 3, border: "1px solid #e2e8f0", borderRadius: 2 }}>
                <Typography variant="h6" mb={2}>Evolução de Consumo (USD)</Typography>
                <Plot data={[{ x: history.map(h => h.period), y: history.map(h => h.cost_usd), type: "scatter", mode: "lines+markers" }]} layout={{ autosize: true, height: 320, margin: { l: 50, r: 20, b: 40, t: 20 } }} useResizeHandler style={{ width: "100%" }} />
              </Paper>
              <Paper elevation={0} sx={{ flex: 1, p: 3, border: "1px solid #e2e8f0", borderRadius: 2 }}>
                <Typography variant="h6" mb={2}>Evolução de Créditos (Consumo vs Franquia)</Typography>
                <Plot data={[{ x: history.map(h => h.period), y: history.map(h => h.total_credits), type: "bar", name: "Consumidos" }, { x: history.map(h => h.period), y: history.map(h => h.included_credits_pool), type: "scatter", mode: "lines+markers", name: "Franquia" }]} layout={{ autosize: true, height: 320, barmode: "overlay", margin: { l: 60, r: 20, b: 40, t: 20 } }} useResizeHandler style={{ width: "100%" }} />
              </Paper>
            </Stack>
          )}

          <Paper elevation={0} sx={{ p: 3, border: "1px solid #e2e8f0", borderRadius: 2, mb: 4 }}>
            <Typography variant="h6" mb={2}>Engajamento por Modelo de IA</Typography>
            <Box sx={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead><tr style={{ borderBottom: "2px solid #e2e8f0" }}><th style={{ padding: "10px" }}>Modelo</th><th style={{ padding: "10px" }}>Devs</th><th style={{ padding: "10px" }}>Share %</th></tr></thead>
                <tbody>{models.map((m, i) => <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}><td style={{ padding: "10px" }}>{m.model_name}</td><td style={{ padding: "10px" }}>{m.active_users}</td><td style={{ padding: "10px" }}>{m.credit_share_pct}%</td></tr>)}</tbody>
              </table>
            </Box>
          </Paper>
        </>
      )}
    </Box>
  );
}
