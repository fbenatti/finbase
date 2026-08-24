import { useEffect, useMemo, useState, type ReactElement } from "react";
import { Link as RouterLink } from "react-router-dom";
import Plot from "react-plotly.js";
import {
  Alert, Box, Breadcrumbs, Button, Card, CardContent, Chip, CircularProgress, Collapse,
  FormControl, IconButton, InputLabel, Link, List, ListItemButton, ListItemText, Menu,
  MenuItem, Paper, Select, Stack, Tab, Tabs, TextField, Typography,
} from "@mui/material";
import { ChevronRight, ExpandMore } from "@mui/icons-material";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import {
  AIMetricKind, AIConsumptionResult, AILicensesResult, AIOverviewResult, AIUserResult,
  Department, formatApiError, getAIDashboardConsumption, getAIDashboardLicenses,
  getAIDashboardOverview, getAIDashboardUser, getDepartments,
} from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });

const flattenDepartments = (nodes: Department[], parents: string[] = []): Array<Department & { path: string }> =>
  nodes.flatMap(node => {
    const path = [...parents, node.name];
    return [{ ...node, path: path.join(" > ") }, ...flattenDepartments(node.children || [], path)];
  });

const currentPeriod = () => {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}`;
};

const formatMetric = (value: number, metric: AIMetricKind) =>
  metric === "cost" ? money.format(value) : compact.format(value);

const chartLayout = (yTitle: string, prefix = ""): Record<string, unknown> => ({
  autosize: true,
  margin: { l: 82, r: 24, t: 20, b: 82 },
  hovermode: "x unified",
  xaxis: { type: "category", title: { text: "Período", standoff: 18 }, tickangle: -35 },
  yaxis: { title: { text: yTitle, standoff: 12 }, tickprefix: prefix, tickformat: ".2s", rangemode: "tozero" },
  legend: { orientation: "h", y: -0.28 },
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
});

export default function DashboardsIA() {
  const [tab, setTab] = useState(0);
  const [metric, setMetric] = useState<AIMetricKind>("tokens");
  const [period, setPeriod] = useState("202608");
  const [provider, setProvider] = useState<string>("todos");
  const [departmentId, setDepartmentId] = useState<number | undefined>();
  const [departmentPath, setDepartmentPath] = useState("");
  const [organization, setOrganization] = useState<Department[]>([]);
  const [departmentOptions, setDepartmentOptions] = useState<Array<Department & { path: string }>>([]);
  const [organizationAnchor, setOrganizationAnchor] = useState<HTMLElement | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [userEmail, setUserEmail] = useState("");
  const [selectedUser, setSelectedUser] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadNonce, setReloadNonce] = useState(0);
  const [overview, setOverview] = useState<AIOverviewResult | null>(null);
  const [licenses, setLicenses] = useState<AILicensesResult | null>(null);
  const [consumption, setConsumption] = useState<AIConsumptionResult | null>(null);
  const [userData, setUserData] = useState<AIUserResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDepartments().then(departments => {
      if (cancelled) return;
      const flat = flattenDepartments(departments).filter(d => d.active);
      setDepartmentOptions(flat);
      setOrganization(departments);
    }).catch(() => {}).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filterBody = useMemo(() => ({
    period,
    provider: provider === "todos" ? undefined : provider,
    department_id: departmentId,
    department_path: departmentPath || undefined,
    metric,
  }), [period, provider, departmentId, departmentPath, metric]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const tasks: Promise<void>[] = [
      getAIDashboardOverview(filterBody, controller.signal).then(setOverview).then(() => undefined),
    ];
    if (tab === 1) {
      tasks.push(
        getAIDashboardLicenses({ ...filterBody, reclaim_only: false, limit: 100 }, controller.signal).then(setLicenses).then(() => undefined),
        getAIDashboardConsumption(filterBody, controller.signal).then(setConsumption).then(() => undefined),
      );
    }
    if (tab === 2 && selectedUser) {
      tasks.push(getAIDashboardUser({ ...filterBody, user_email: selectedUser }, controller.signal).then(setUserData).then(() => undefined));
    }
    Promise.all(tasks).catch(e => {
      if ((e as { code?: string }).code === "ERR_CANCELED" || controller.signal.aborted) return;
      setError(formatApiError(e, "Não foi possível carregar os dashboards de IA"));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filterBody, tab, selectedUser, reloadNonce]);

  const coverage = overview?.cost_coverage_pct ?? licenses?.cost_coverage_pct ?? consumption?.cost_coverage_pct ?? userData?.cost_coverage_pct;
  const kpis = overview?.kpis;

  const seriesByProvider = useMemo(() => {
    const rows = overview?.monthly_series ?? [];
    const providers = [...new Set(rows.map(r => r.provider))];
    const periods = [...new Set(rows.map(r => r.period))].sort();
    return providers.map(provider => ({
      provider,
      x: periods,
      y: periods.map(p => rows.find(r => r.period === p && r.provider === provider)?.value ?? 0),
    }));
  }, [overview]);

  const card = (label: string, value: number, color: string, format?: "money" | "number" | string) => (
    <Card sx={{ flex: 1, minWidth: 170, borderTop: `3px solid ${color}` }}>
      <CardContent>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h5" fontWeight={700}>
          {format === "money" ? money.format(value) : format === "number" ? value.toLocaleString("pt-BR") : formatMetric(value, metric)}
        </Typography>
      </CardContent>
    </Card>
  );

  const toggleNode = (id: number) => setExpandedNodes(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const chooseDepartment = (department: Department) => {
    const path = flattenDepartments(organization).find(item => item.id === department.id)?.path || department.name;
    setDepartmentId(department.id);
    setDepartmentPath(path);
    setOrganizationAnchor(null);
  };

  const renderOrganizationNode = (department: Department, depth = 0): ReactElement => {
    const children = (department.children || []).filter(child => child.active);
    const expanded = expandedNodes.has(department.id);
    return (
      <Box key={department.id}>
        <ListItemButton selected={department.id === departmentId} onClick={() => chooseDepartment(department)} sx={{ pl: 1 + depth * 2, py: 0.25 }}>
          <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>
            {children.length > 0 && (
              <IconButton size="small" onClick={event => { event.stopPropagation(); toggleNode(department.id); }}>
                {expanded ? <ExpandMore fontSize="small" /> : <ChevronRight fontSize="small" />}
              </IconButton>
            )}
          </Box>
          <ListItemText primary={department.name} primaryTypographyProps={{ variant: "body2" }} />
        </ListItemButton>
        <Collapse in={expanded} timeout="auto" unmountOnExit>
          {children.map(child => renderOrganizationNode(child, depth + 1))}
        </Collapse>
      </Box>
    );
  };

  const reclaimColumns: GridColDef[] = [
    { field: "user_email", headerName: "Usuário", flex: 1, minWidth: 200 },
    { field: "provider", headerName: "Provedor", width: 120 },
    { field: "activity_status", headerName: "Status", width: 130 },
    { field: "days_since_activity", headerName: "Dias inativo", type: "number", width: 110 },
    { field: "allocated_cost", headerName: "Custo alocado", type: "number", width: 140, valueFormatter: (v) => money.format(Number(v || 0)) },
  ];

  const rankedColumns: GridColDef[] = [
    { field: "user_email", headerName: "Usuário", flex: 1, minWidth: 200 },
    { field: "provider", headerName: "Provedor", width: 120 },
    { field: "metric_value", headerName: metric === "cost" ? "Custo" : "Tokens", type: "number", width: 130, valueFormatter: (v) => formatMetric(Number(v || 0), metric) },
    { field: "usage_index", headerName: "UsageIndex", type: "number", width: 120, valueFormatter: (v) => pct.format(Number(v || 0)) },
  ];

  const userLicenseColumns: GridColDef[] = [
    { field: "provider", headerName: "Provedor", width: 120 },
    { field: "product_name", headerName: "Produto", flex: 1, minWidth: 160 },
    { field: "activity_status", headerName: "Atividade", width: 130 },
    { field: "allocated_cost", headerName: "Custo", type: "number", width: 120, valueFormatter: (v) => money.format(Number(v || 0)) },
  ];

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2} mb={2}>
          <Box>
            <Typography variant="h4">Dashboards IA</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Licenças, consumo de tokens e eficiência FinOps por provedor e usuário.
            </Typography>
          </Box>
          <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
            <FormControl sx={{ minWidth: 130 }}>
              <InputLabel>Métrica</InputLabel>
              <Select label="Métrica" value={metric} onChange={e => setMetric(e.target.value as AIMetricKind)}>
                <MenuItem value="tokens">Tokens</MenuItem>
                <MenuItem value="cost">Custo (R$)</MenuItem>
              </Select>
            </FormControl>
            <TextField label="Período (YYYYMM)" size="small" value={period} onChange={e => setPeriod(e.target.value)} sx={{ width: 150 }} />
          </Stack>
        </Stack>
        {coverage && (
          <Alert severity={coverage.overall >= 80 ? "success" : "warning"} sx={{ mb: 2 }}>
            Cobertura de custo — token: {coverage.token.toFixed(1)}% · licença: {coverage.license.toFixed(1)}% · geral: {coverage.overall.toFixed(1)}%.
            {(coverage.license < 100 || coverage.token < 100) && (
              <> <Link component={RouterLink} to="/tables?tab=ai-cost-relationship">Relacionamento Origem de Custo IA</Link></>
            )}
          </Alert>
        )}
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: -1 }}>
          <Tab label="Visão Executiva" />
          <Tab label="Eficiência FinOps" />
          <Tab label="Drilldown por usuário" />
        </Tabs>
      </Box>

      <Box sx={{
        p: 3,
        "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
        "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
      }}>
        <Paper elevation={0} sx={{ p: { xs: 2, md: 2.5 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
          <Breadcrumbs separator=">" sx={{ mb: 1.5 }}>
            <Typography color="text.secondary">Dashboards IA</Typography>
            {provider !== "todos" && (
              <Typography color="text.primary" fontWeight={600}>
                {provider === "anthropic" ? "Anthropic (Claude)" : provider === "github" ? "GitHub (Copilot)" : "Google (Gemini / NotebookLM)"}
              </Typography>
            )}
          </Breadcrumbs>

          <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1} mb={2}>
            <Typography variant="subtitle2" color="text.secondary">
              {tab === 0 ? "Adoção e consumo consolidado" : tab === 1 ? "Desperdício e outliers" : "Visão 360° do usuário"}
            </Typography>
            <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
              <FormControl size="small" sx={{ minWidth: 240 }}>
                <InputLabel>Provedor de IA</InputLabel>
                <Select
                  label="Provedor de IA"
                  value={provider}
                  onChange={e => setProvider(e.target.value)}
                >
                  <MenuItem value="todos">Todos os provedores</MenuItem>
                  <MenuItem value="anthropic">Anthropic (Claude)</MenuItem>
                  <MenuItem value="github">GitHub (Copilot)</MenuItem>
                  <MenuItem value="google">Google (NotebookLM / Gemini)</MenuItem>
                </Select>
              </FormControl>
              {provider !== "todos" && (
                <Button size="small" variant="text" onClick={() => setProvider("todos")}>
                  Limpar filtro
                </Button>
              )}
            </Stack>
          </Stack>

          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}<Button size="small" sx={{ ml: 1 }} onClick={() => setReloadNonce(n => n + 1)}>Tentar de novo</Button></Alert>}

          {loading ? (
            <Stack alignItems="center" mt={6} gap={1}><CircularProgress /><Typography color="text.secondary">Consultando dest_licencas_ia e dest_consumo_ia…</Typography></Stack>
          ) : tab === 0 && overview ? (
            <>
              <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
                {card("Consumo total", kpis?.total_usage ?? 0, "#2563EB")}
                {card("Licenças ativas", kpis?.active_licenses ?? 0, "#7C3AED", "")}
                {card("Taxa de utilização", (kpis?.utilization_rate ?? 0) * 100, "#16A34A", "%")}
                {card("Desperdício estimado", kpis?.estimated_waste ?? 0, "#EA580C")}
                {card("Variação MoM", kpis?.mom_change_pct ?? 0, kpis && kpis.mom_change_pct >= 0 ? "#EF4444" : "#16A34A", "%")}
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} gap={2} mb={2}>
                <Paper elevation={0} sx={{ flex: 2, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Consumo mensal por provedor</Typography>
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 380 }}
                    data={seriesByProvider.map((s, i) => ({
                      x: s.x,
                      y: s.y,
                      name: s.provider,
                      type: "bar" as const,
                      marker: { color: ["#2563EB", "#7C3AED", "#16A34A", "#EA580C"][i % 4] },
                    }))}
                    layout={{ ...chartLayout(metric === "cost" ? "Custo (R$)" : "Tokens", metric === "cost" ? "R$" : ""), barmode: "stack" }}
                    config={{ responsive: true, displaylogo: false }}
                  />
                </Paper>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Funil de adoção</Typography>
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 380 }}
                    data={[{
                      type: "funnel" as const,
                      y: ["Atribuídas", "Logaram", "Usaram", "Uso saudável"],
                      x: [
                        overview.adoption_funnel.assigned,
                        overview.adoption_funnel.logged_in,
                        overview.adoption_funnel.used,
                        overview.adoption_funnel.healthy,
                      ],
                      marker: { color: ["#64748B", "#2563EB", "#7C3AED", "#16A34A"] },
                    }]}
                    layout={{ autosize: true, margin: { l: 120, r: 20, t: 20, b: 20 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }}
                    config={{ responsive: true, displaylogo: false }}
                  />
                </Paper>
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} gap={2}>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Utilização por departamento</Typography>
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 320 }}
                    data={[{
                      type: "bar" as const,
                      x: overview.license_utilization_by_dept.map(d => d.department),
                      y: overview.license_utilization_by_dept.map(d => d.utilization_rate * 100),
                      marker: { color: "#2563EB" },
                    }]}
                    layout={{ ...chartLayout("Utilização (%)"), yaxis: { ...((chartLayout("") as { yaxis?: object }).yaxis), ticksuffix: "%" } }}
                    config={{ responsive: true, displaylogo: false }}
                  />
                </Paper>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Adoção × consumo por provedor</Typography>
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 320 }}
                    data={[{
                      type: "scatter" as const,
                      mode: "text+markers" as const,
                      x: overview.provider_bubble.map(b => b.licenses),
                      y: overview.provider_bubble.map(b => b.usage_value),
                      text: overview.provider_bubble.map(b => b.provider),
                      textposition: "top center" as const,
                      marker: { size: overview.provider_bubble.map(b => Math.max(12, Math.sqrt(b.licenses) * 4)), color: "#7C3AED", opacity: 0.75 },
                    }]}
                    layout={{
                      autosize: true,
                      margin: { l: 70, r: 20, t: 20, b: 60 },
                      xaxis: { title: { text: "Licenças" } },
                      yaxis: { title: { text: metric === "cost" ? "Custo" : "Tokens" } },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                    }}
                    config={{ responsive: true, displaylogo: false }}
                  />
                </Paper>
              </Stack>
            </>
          ) : tab === 1 && licenses && consumption ? (
            <>
              <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
                {card("Nunca usaram", licenses.summary.never_used ?? 0, "#64748B", "number")}
                {card("Inativos 30d", licenses.summary.inactive_30d ?? 0, "#F59E0B", "number")}
                {card("Inativos 60d", licenses.summary.inactive_60d ?? 0, "#EA580C", "number")}
                {card("Inativos 90d", licenses.summary.inactive_90d ?? 0, "#EF4444", "number")}
                {card("Economia potencial", licenses.summary.reclaim_potential ?? 0, "#16A34A", "money")}
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} gap={2} mb={2}>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Pareto de consumo</Typography>
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 340 }}
                    data={[{
                      type: "bar" as const,
                      x: consumption.pareto.map(p => p.provider),
                      y: consumption.pareto.map(p => p.value),
                      marker: { color: "#2563EB" },
                    }]}
                    layout={chartLayout(metric === "cost" ? "Custo" : "Tokens", metric === "cost" ? "R$" : "")}
                    config={{ responsive: true, displaylogo: false }}
                  />
                </Paper>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Custo por 1M tokens</Typography>
                  <Stack spacing={1} mt={1}>
                    {consumption.cost_per_million_tokens.slice(0, 8).map(row => (
                      <Stack key={row.provider + row.model_name} direction="row" justifyContent="space-between">
                        <Typography variant="body2">{row.provider} · {row.model_name}</Typography>
                        <Chip size="small" label={money.format(row.cost_per_million)} />
                      </Stack>
                    ))}
                    {consumption.cost_per_million_tokens.length === 0 && (
                      <Typography color="text.secondary" variant="body2">Sem dados de custo/token no período.</Typography>
                    )}
                  </Stack>
                </Paper>
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} gap={2} mb={2}>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Acima do p95 (outliers)</Typography>
                  <DataGrid
                    rows={consumption.outliers.map((r, i) => ({ id: i, ...r }))}
                    columns={rankedColumns}
                    autoHeight
                    hideFooter
                    density="compact"
                    onRowClick={p => { setSelectedUser(String(p.row.user_email)); setTab(2); }}
                    sx={{ mt: 1, cursor: "pointer" }}
                  />
                </Paper>
                <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                  <Typography variant="h6">Zumbi ativo (abaixo do p10)</Typography>
                  <DataGrid
                    rows={consumption.zombies.map((r, i) => ({ id: i, ...r }))}
                    columns={rankedColumns}
                    autoHeight
                    hideFooter
                    density="compact"
                    onRowClick={p => { setSelectedUser(String(p.row.user_email)); setTab(2); }}
                    sx={{ mt: 1, cursor: "pointer" }}
                  />
                </Paper>
              </Stack>
            </>
          ) : tab === 2 ? (
            <>
              <Stack direction="row" gap={1} mb={2} alignItems="center">
                <TextField
                  label="E-mail do usuário"
                  size="small"
                  value={userEmail}
                  onChange={e => setUserEmail(e.target.value)}
                  sx={{ minWidth: 320 }}
                  onKeyDown={e => { if (e.key === "Enter") setSelectedUser(userEmail.trim()); }}
                />
                <Button variant="contained" onClick={() => setSelectedUser(userEmail.trim())}>Consultar</Button>
              </Stack>
              {!selectedUser ? (
                <Typography color="text.secondary">Informe um e-mail para ver licenças, tokens e custo alocado.</Typography>
              ) : userData ? (
                <>
                  <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
                    {card("Consumo total", userData.summary.total_usage, "#2563EB")}
                    {card("Tokens", userData.summary.total_tokens, "#7C3AED")}
                    {card("Custo", userData.summary.total_cost, "#16A34A")}
                    {card("Percentil no dept.", userData.summary.dept_percent_rank * 100, "#EA580C", "%")}
                  </Stack>
                  <Stack direction={{ xs: "column", md: "row" }} gap={2}>
                    <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                      <Typography variant="h6">Licenças ({userData.summary.license_count})</Typography>
                      <DataGrid rows={userData.licenses.map((r, i) => ({ id: i, ...r }))} columns={userLicenseColumns} autoHeight hideFooter density="compact" sx={{ mt: 1 }} />
                    </Paper>
                    <Paper elevation={0} sx={{ flex: 1, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
                      <Typography variant="h6">Tokens por modelo/dia</Typography>
                      <Plot
                        useResizeHandler
                        style={{ width: "100%", height: 320 }}
                        data={[{
                          type: "scatter" as const,
                          mode: "lines+markers" as const,
                          x: userData.consumption_by_model.map(r => r.usage_date),
                          y: userData.consumption_by_model.map(r => r.total_tokens),
                          name: "Tokens",
                          line: { color: "#2563EB" },
                        }]}
                        layout={chartLayout("Tokens")}
                        config={{ responsive: true, displaylogo: false }}
                      />
                    </Paper>
                  </Stack>
                </>
              ) : null}
            </>
          ) : null}
        </Paper>
      </Box>
    </Box>
  );
}
