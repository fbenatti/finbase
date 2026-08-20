import { useEffect, useState, type ReactElement } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Plot from "react-plotly.js";
import { Alert, Box, Breadcrumbs, Button, Card, CardContent, CircularProgress, Collapse, FormControl, FormControlLabel, Grid, IconButton, InputLabel, Link, List, ListItemButton, ListItemText, Menu, MenuItem, Paper, Select, Stack, Switch, Typography } from "@mui/material";
import { ChevronRight, ExpandMore } from "@mui/icons-material";
import { Department, formatApiError, getDepartments, getExpectedView, ExpectedViewDriver, ExpectedViewRow } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const flattenDepartments = (nodes: Department[], parents: string[] = []): Array<Department & { path: string }> => nodes.flatMap(node => {
  const path = [...parents, node.name];
  return [{ ...node, path: path.join(" > ") }, ...flattenDepartments(node.children || [], path)];
});

export default function ExpectedView() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [rows, setRows] = useState<ExpectedViewRow[]>([]);
  const [drivers, setDrivers] = useState<ExpectedViewDriver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [director, setDirector] = useState("Consumo Cloud");
  const [departmentOptions, setDepartmentOptions] = useState<Array<Department & { path: string }>>([]);
  const [departmentId, setDepartmentId] = useState<number | undefined>();
  const [organizationLoaded, setOrganizationLoaded] = useState(false);
  const [organization, setOrganization] = useState<Department[]>([]);
  const [organizationAnchor, setOrganizationAnchor] = useState<HTMLElement | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [periodFilter, setPeriodFilter] = useState("current");
  const [cumulative, setCumulative] = useState(true);
  const [includeSharedCost, setIncludeSharedCost] = useState(params.get("shared") === "1");
  const [reloadNonce, setReloadNonce] = useState(0);
  useEffect(() => {
    let cancelled = false;
    getDepartments().then((departments: Department[]) => {
      if (cancelled) return;
      const flat = flattenDepartments(departments).filter(department => department.active);
      setDepartmentOptions(flat);
      setOrganization(departments);
      const cloud = flat.find(department => department.name === "Consumo Cloud" && department.parent_id === null);
      if (cloud) {
        setDepartmentId(cloud.id);
        setDirector(cloud.path);
        setExpandedNodes(new Set([cloud.id]));
      }
    }).catch(() => {
      // The current director remains usable if the hierarchy is temporarily unavailable.
    }).finally(() => { if (!cancelled) setOrganizationLoaded(true); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    // Wait for the tree before querying.  Previously the page sent one broad
    // request during mount and a second request after resolving Consumo Cloud,
    // duplicating a costly seven-level Trino aggregation.
    if (!organizationLoaded) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const selectedId = departmentOptions.find(department => department.path === director)?.id;
    const year = periodFilter === "all" || periodFilter === "current" ? new Date().getFullYear() : Number(periodFilter);
    getExpectedView(director, year, departmentId ?? selectedId, controller.signal, includeSharedCost).then(result => { setRows(result.rows); setDrivers(result.drivers); }).catch(e => {
      if ((e as { code?: string }).code === "ERR_CANCELED" || controller.signal.aborted) return;
      setError(formatApiError(e, "Não foi possível carregar a Visão Esperada"));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [director, departmentId, organizationLoaded, periodFilter, reloadNonce, includeSharedCost]);
  const years = [...new Set(rows.map(row => row.period.slice(0, 4)))].sort();
  const filteredRows = periodFilter === "all" ? rows : periodFilter === "current" ? rows.filter(row => row.period.startsWith(String(new Date().getFullYear()))) : rows.filter(row => row.period.startsWith(periodFilter));
  const toDate = (period: string) => period.length === 6 ? `${period.slice(0, 4)}-${period.slice(4, 6)}-01` : period;
  const sharedOn = includeSharedCost;
  const sharedOf = (row: ExpectedViewRow) => Number(row.shared_value || 0);
  const receivedOf = (row: ExpectedViewRow) => Number(row.shared_received || 0);
  const givenOf = (row: ExpectedViewRow) => Number(row.shared_given || 0);
  const consumptionOf = (row: ExpectedViewRow) => {
    if (row.actual_value == null) return 0;
    return Math.max(0, Number(row.actual_value || 0) + (sharedOn ? givenOf(row) : 0));
  };
  const realizedOf = (row: ExpectedViewRow) => (
    row.actual_value == null ? 0 : Number(row.actual_value || 0) + (sharedOn ? sharedOf(row) : 0)
  );
  const expectedOf = (row: ExpectedViewRow) => (
    row.actual_value != null
      ? Number(row.actual_value || 0) + (sharedOn ? sharedOf(row) : 0)
      : Number(row.forecast_value ?? 0) + (sharedOn ? sharedOf(row) : 0)
  );
  const labels = filteredRows.map(row => toDate(row.period));
  const actual = filteredRows.reduce((sum, row) => sum + realizedOf(row), 0);
  const budget = filteredRows.reduce((sum, row) => sum + Number(row.budget_value || 0), 0);
  const forecast = filteredRows.reduce((sum, row) => sum + expectedOf(row), 0);
  const variance = budget - forecast;
  const actualEnd = filteredRows.reduce((last, row, index) => row.actual_value == null ? last : index, -1);
  const consumptionCumulative = filteredRows.map((_, index) => index <= actualEnd ? filteredRows.slice(0, index + 1).reduce((sum, item) => sum + consumptionOf(item), 0) : null);
  const actualCumulative = filteredRows.map((_, index) => index <= actualEnd ? filteredRows.slice(0, index + 1).reduce((sum, item) => sum + realizedOf(item), 0) : null);
  const forecastCumulative = filteredRows.map((_, index) => {
    if (index < actualEnd) return null;
    return filteredRows.slice(0, index + 1).reduce((sum, item) => sum + expectedOf(item), 0);
  });
  const forecastStart = actualEnd >= 0 ? actualEnd : 0;
  const forecastLabels = labels.slice(forecastStart);
  const forecastFilled = forecastCumulative.slice(forecastStart);
  const budgetCumulative = filteredRows.map((_, index) => filteredRows.slice(0, index + 1).reduce((sum, item) => sum + Number(item.budget_value || 0), 0));
  const forecastBars = filteredRows.map(row => (
    row.actual_value == null
      ? Math.max(0, Number(row.forecast_value || 0) + (sharedOn ? givenOf(row) : 0))
      : 0
  ));
  const receivedOnActual = filteredRows.map(row => sharedOn && row.actual_value != null ? Math.max(0, receivedOf(row)) : 0);
  const receivedOnForecast = filteredRows.map(row => sharedOn && row.actual_value == null ? Math.max(0, receivedOf(row)) : 0);
  const sharedLine = { color: "#EA580C", width: 3 };
  const sharedFill = "rgba(234,88,12,.28)";
  const forecastBaseCumulative = filteredRows.map((_, index) => {
    if (index < actualEnd) return null;
    return filteredRows.slice(0, index + 1).reduce((sum, item) => {
      if (item.actual_value != null) return sum + consumptionOf(item);
      return sum + Number(item.forecast_value || 0) + (sharedOn ? givenOf(item) : 0);
    }, 0);
  });
  const forecastBaseFilled = forecastBaseCumulative.slice(forecastStart);
  const chartData: any[] = cumulative
    ? [
        { x: labels, y: consumptionCumulative, type: "scatter", mode: "lines+markers", name: "Realizado", fill: "tozeroy", fillcolor: "rgba(242,169,0,.22)", line: { color: "#F2A900", width: 3 }, marker: { size: 6 } },
        ...(sharedOn ? [{ x: labels, y: actualCumulative, type: "scatter", mode: "lines+markers", name: "Custo compartilhado", legendgroup: "shared", fill: "tonexty", fillcolor: sharedFill, line: sharedLine, marker: { size: 6 } }] : []),
        { x: labels, y: budgetCumulative, type: "scatter", mode: "lines+markers", name: "Orçamento", line: { color: "#64748B", width: 3 }, marker: { size: 5 } },
        ...(sharedOn ? [
          { x: forecastLabels, y: forecastBaseFilled, type: "scatter", mode: "lines", name: "Forecast base", showlegend: false, hoverinfo: "skip", fill: "tozeroy", fillcolor: "rgba(34,197,94,.18)", line: { color: "#22C55E", width: 0 } },
          { x: forecastLabels, y: forecastFilled, type: "scatter", mode: "lines+markers", name: "Custo compartilhado", legendgroup: "shared", showlegend: false, fill: "tonexty", fillcolor: sharedFill, line: sharedLine, marker: { size: 6, color: "#EA580C" } },
        ] : []),
        { x: forecastLabels, y: forecastFilled, type: "scatter", mode: "lines+markers", name: "Forecast", fill: sharedOn ? "none" : "tozeroy", fillcolor: "rgba(34,197,94,.22)", line: { color: "#22C55E", width: 3 }, marker: { size: 6 } },
      ]
    : [
        { x: labels, y: filteredRows.map(consumptionOf), type: "bar", name: "Realizado", marker: { color: "#F2A900" } },
        ...(sharedOn ? [{ x: labels, y: receivedOnActual, type: "bar", name: "Custo compartilhado", legendgroup: "shared", marker: { color: "#EA580C" } }] : []),
        { x: labels, y: forecastBars, type: "bar", name: "Forecast", marker: { color: "#22C55E" } },
        ...(sharedOn ? [{ x: labels, y: receivedOnForecast, type: "bar", name: "Custo compartilhado", legendgroup: "shared", showlegend: false, marker: { color: "#EA580C" } }] : []),
        { x: labels, y: filteredRows.map(row => Number(row.budget_value || 0)), type: "scatter", mode: "lines+markers", name: "Orçamento", line: { color: "#64748B", width: 3 }, marker: { size: 6 } },
      ];
  const chartLayout: any = { autosize: true, barmode: "stack", margin: { l: 82, r: 24, t: 20, b: 82 }, hovermode: "x unified", xaxis: { type: "date", tickformat: "%b %Y", dtick: "M1", title: { text: "Período", standoff: 18 }, side: "bottom", showticklabels: true, tickangle: -35 }, yaxis: { title: { text: cumulative ? "Valor acumulado" : "Valor mensal", standoff: 12 }, side: "left", showticklabels: true, tickprefix: "$", tickformat: ".2s", zeroline: true, rangemode: "tozero" }, legend: { orientation: "h", y: -0.28 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" };
  const sortedDrivers = drivers.filter(driver => Math.abs(driver.variance) > 0.005).sort((a, b) => Math.abs(b.variance) - Math.abs(a.variance));
  const leadingDrivers = sortedDrivers.slice(0, 8);
  const otherDrivers = sortedDrivers.slice(8).reduce((total, item) => total + item.variance, 0);
  const waterfallLabels = ["Orçamento", ...leadingDrivers.map(item => item.name), ...(sortedDrivers.length > 8 ? ["Outros"] : []), "Forecast"];
  const waterfallValues = [budget, ...leadingDrivers.map(item => item.variance), ...(sortedDrivers.length > 8 ? [otherDrivers] : []), 0];
  const waterfallMeasures = ["absolute", ...leadingDrivers.map(() => "relative"), ...(sortedDrivers.length > 8 ? ["relative"] : []), "total"];
  const stackedDrivers = [...drivers]
    .filter(driver => Math.abs(Number(driver.actual_value || 0)) + Math.abs(Number(driver.shared_value || 0)) > 0.005)
    .sort((a, b) => (Number(b.actual_value || 0) + Number(b.shared_value || 0)) - (Number(a.actual_value || 0) + Number(a.shared_value || 0)))
    .slice(0, 8);
  const waterfallData: any[] = sharedOn && stackedDrivers.length > 0
    ? [
        { type: "bar", x: stackedDrivers.map(item => item.name), y: stackedDrivers.map(item => Number(item.actual_value || 0)), name: "Realizado", marker: { color: "#F2A900" } },
        { type: "bar", x: stackedDrivers.map(item => item.name), y: stackedDrivers.map(item => Number(item.shared_value || 0)), name: "Custo compartilhado", marker: { color: "#EA580C" } },
      ]
    : [{ type: "waterfall", x: waterfallLabels, y: waterfallValues, measure: waterfallMeasures, connector: { line: { color: "#9CA3AF" } }, increasing: { marker: { color: "#EF4444" } }, decreasing: { marker: { color: "#16A34A" } }, totals: { marker: { color: "#64748B" } }, name: "Drivers da variação" }];
  const pathParts = director.split(" > ").filter(Boolean);
  const childLevel = Math.min(pathParts.length + 1, 7);
  const waterfallLayout: any = sharedOn && stackedDrivers.length > 0
    ? { autosize: true, barmode: "relative", margin: { l: 82, r: 20, t: 25, b: 120 }, yaxis: { title: { text: "Consumo e rateio", standoff: 12 }, side: "left", showticklabels: true, tickprefix: "$", tickformat: ".2s" }, xaxis: { type: "category", title: { text: `Departamento nível ${childLevel} · clique para abrir`, standoff: 18 }, side: "bottom", showticklabels: true, tickangle: -35, automargin: true }, legend: { orientation: "h", y: -0.28 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }
    : { autosize: true, margin: { l: 82, r: 20, t: 25, b: 120 }, yaxis: { title: { text: "Variação", standoff: 12 }, side: "left", showticklabels: true, tickprefix: "$", tickformat: ".2s" }, xaxis: { type: "category", title: { text: `Departamento nível ${childLevel} · clique para abrir`, standoff: 18 }, side: "bottom", showticklabels: true, tickangle: -35, automargin: true }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" };
  const card = (label: string, value: number, color: string, onOpen?: () => void) => (
    <Card
      onClick={onOpen}
      sx={{ flex: 1, minWidth: 190, borderTop: `3px solid ${color}`, cursor: onOpen ? "pointer" : "default", "&:hover": onOpen ? { boxShadow: 3 } : undefined }}
    >
      <CardContent>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h5" fontWeight={700}>{money.format(value)}</Typography>
        <Typography variant="caption">{onOpen ? "Clique para ver o detalhe do consumo" : "Current Year"}</Typography>
      </CardContent>
    </Card>
  );
  const toggleNode = (id: number) => setExpandedNodes(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const expandAncestors = (department: Department) => {
    setExpandedNodes(previous => {
      const next = new Set(previous);
      let current: (Department & { path: string }) | undefined = departmentOptions.find(item => item.id === department.id);
      while (current) {
        next.add(current.id);
        current = current.parent_id ? departmentOptions.find(item => item.id === current!.parent_id) : undefined;
      }
      return next;
    });
  };
  const chooseDepartment = (department: Department) => {
    const path = flattenDepartments(organization).find(item => item.id === department.id)?.path || department.name;
    setDepartmentId(department.id);
    setDirector(path);
    expandAncestors(department);
    setOrganizationAnchor(null);
  };
  const choosePath = (path: string) => {
    const found = departmentOptions.find(item => item.path === path);
    if (found) chooseDepartment(found);
  };
  const openActualDetails = () => {
    const year = periodFilter === "all" ? "" : periodFilter === "current" ? String(new Date().getFullYear()) : periodFilter;
    const query = new URLSearchParams({ tab: "pivot", measure: "realizado", path: director, tipo: "realizado" });
    if (year) query.set("year", year);
    navigate(`/analysis?${query.toString()}`);
  };
  const openDriver = (name: string) => {
    if (!name || ["Orçamento", "Forecast", "Outros", "Realizado", "Custo compartilhado"].includes(name)) return;
    const found = departmentOptions.find(item => item.path === [...pathParts, name].join(" > ") && item.active);
    if (found) chooseDepartment(found);
  };
  const renderOrganizationNode = (department: Department, depth = 0): ReactElement => {
    const children = (department.children || []).filter(child => child.active);
    const expanded = expandedNodes.has(department.id);
    return <Box key={department.id}><ListItemButton selected={department.id === departmentId} onClick={() => chooseDepartment(department)} sx={{ pl: 1 + depth * 2, py: 0.25 }}>
      <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>{children.length > 0 && <IconButton size="small" onClick={event => { event.stopPropagation(); toggleNode(department.id); }}>{expanded ? <ExpandMore fontSize="small" /> : <ChevronRight fontSize="small" />}</IconButton>}</Box>
      <ListItemText primary={department.name} primaryTypographyProps={{ variant: "body2", fontWeight: depth < 3 ? 700 : 400 }} />
    </ListItemButton><Collapse in={expanded} timeout="auto" unmountOnExit>{children.map(child => renderOrganizationNode(child, depth + 1))}</Collapse></Box>;
  };
  return <Box>
    <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 1, pb: 1 }}>
      <Typography variant="h4" fontWeight={700}>Plano de Nuvem e IA</Typography>
      <Breadcrumbs separator=">" sx={{ mt: 0.5, "& .MuiBreadcrumbs-separator": { mx: 0.5 } }}>
        <Typography color="text.secondary">Dashboard</Typography>
        <Typography color="text.secondary">Variância de Forecast</Typography>
        {pathParts.map((name, index) => {
          const path = pathParts.slice(0, index + 1).join(" > ");
          const last = index === pathParts.length - 1;
          return last
            ? <Typography key={path} color="text.primary" fontWeight={600}>{name}</Typography>
            : <Link key={path} component="button" underline="hover" color="inherit" onClick={() => choosePath(path)}>{name}</Link>;
        })}
      </Breadcrumbs>
    </Box>
    <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1} mb={2}>
      <Typography variant="subtitle2" color="text.secondary">Variância de forecast</Typography>
      <Stack direction="row" gap={1}>
        <Button variant="outlined" size="small" onClick={event => setOrganizationAnchor(event.currentTarget)} sx={{ minWidth: 320, justifyContent: "flex-start", textTransform: "none" }}>
          {director || "Consumo Cloud"}
        </Button>
        <Menu anchorEl={organizationAnchor} open={Boolean(organizationAnchor)} onClose={() => setOrganizationAnchor(null)} PaperProps={{ sx: { width: 430, maxHeight: 520 } }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2, pt: 1 }}>Selecione um nível para consolidar todos os seus filhos</Typography>
          <List dense disablePadding>{organization.filter(department => department.active).map(department => renderOrganizationNode(department))}</List>
        </Menu>
        <FormControl size="small" sx={{ minWidth: 170 }}><InputLabel>Período</InputLabel><Select label="Período" value={periodFilter} onChange={e => setPeriodFilter(String(e.target.value))}><MenuItem value="current">Ano atual</MenuItem><MenuItem value="all">Todos os períodos</MenuItem>{years.map(year => <MenuItem key={year} value={year}>{year}</MenuItem>)}</Select></FormControl>
        <FormControlLabel control={<Switch checked={includeSharedCost} onChange={(_, checked) => setIncludeSharedCost(checked)} />} label="Incluir custo compartilhado" />
      </Stack>
    </Stack>
    <Alert severity="info" sx={{ mb: 2 }}>Forecast = realizado dos meses fechados + forecast dos meses futuros. Variação = orçamento menos forecast. Com o interruptor ligado, o custo compartilhado aparece empilhado em laranja sobre o consumo real — não mistura as duas barras. O card Realizado soma os dois.</Alert>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}<Button size="small" sx={{ ml: 1 }} onClick={() => setReloadNonce(n => n + 1)}>Tentar de novo</Button></Alert>}
    {loading ? <Stack alignItems="center" mt={6} gap={1}><CircularProgress /><Typography color="text.secondary">Consultando realizado e forecast no Trino…</Typography></Stack> : <>
      <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>{card("Realizado", actual, "#F2A900", openActualDetails)}{card("Orçamento", budget, "#64748B")}{card("Forecast", forecast, "#16A34A")}{card("Variação", variance, variance >= 0 ? "#16A34A" : "#EF4444")}</Stack>
      <Grid container spacing={2}>
        <Grid item xs={12} md={7}><Paper sx={{ p: 2 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Box><Typography variant="h6">Gasto ao longo do tempo</Typography><Typography variant="caption" color="text.secondary">{cumulative ? (sharedOn ? "Acumulado · realizado, custo compartilhado por cima, orçamento e forecast" : "Acumulado · Realizado, orçamento e forecast") : (sharedOn ? "Mensal · consumo real e custo compartilhado empilhados; forecast e orçamento" : "Mensal · Realizado e forecast empilhados; orçamento em linha")}</Typography></Box><FormControlLabel control={<Switch checked={cumulative} onChange={(_, checked) => setCumulative(checked)} />} label="Acumulado" /></Stack><Plot useResizeHandler style={{ width: "100%", height: 420 }} data={chartData} layout={chartLayout} config={{ responsive: true, displaylogo: false }} onClick={event => { if (String(event.points?.[0]?.data?.name ?? "") === "Realizado") openActualDetails(); }} /></Paper></Grid>
        <Grid item xs={12} md={5}><Paper sx={{ p: 2 }}><Typography variant="h6">{sharedOn ? "Rateio por departamento" : "Drivers da variação"}</Typography><Typography variant="caption" color="text.secondary">{sharedOn ? "Laranja empilhado no filho que recebe o custo · clique para abrir" : "Filhos do departamento selecionado · clique na barra para abrir o subnível"}</Typography><Plot useResizeHandler style={{ width: "100%", height: 420, cursor: "pointer" }} data={waterfallData} layout={waterfallLayout} config={{ responsive: true, displaylogo: false }} onClick={event => openDriver(String(event.points?.[0]?.x ?? ""))} /></Paper></Grid>
      </Grid>
    </>}
    {!loading && filteredRows.length > 0 && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>Orçamento anual carregado: {money.format(budget)}</Typography>}
  </Box>;
}
