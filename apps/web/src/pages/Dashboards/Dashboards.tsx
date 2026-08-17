import { useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import {
  Alert, Box, Button, Card, CardContent, Chip, FormControl, InputLabel, MenuItem,
  Paper, Select, Stack, Tab, Tabs, TextField, Typography,
} from "@mui/material";
import { Add, Delete, Refresh } from "@mui/icons-material";
import PivotAnalysis from "../../components/PivotAnalysis";
import EmptyState from "../../components/EmptyState";
import {
  ChartType, DashboardWidget, DestinationAnalysis, PivotView, SavedDashboard,
  createDashboard, createDashboardWidget, deleteDashboardWidget, getAnalysisDestinations,
  getDashboards, getPivotRecords, getPivotViews, queryDashboardWidget,
} from "../../services/api";

function WidgetChart({ widget, globalFilters }: { widget: DashboardWidget; globalFilters: Record<string, unknown> }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof queryDashboardWidget>> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!widget.pivot_view_id) return;
    queryDashboardWidget({ pivot_view_id: widget.pivot_view_id, chart_type: widget.chart_type, config: widget.config, global_filters: globalFilters })
      .then(setData).catch(e => setError(e instanceof Error ? e.message : "Falha ao carregar widget"));
  }, [widget, globalFilters]);
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Typography color="text.secondary">Carregando...</Typography>;
  if (widget.chart_type === "kpi") return <Typography variant="h3" color="primary">{(data.series.reduce((sum, p) => sum + Number(p.y || 0), 0)).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}</Typography>;
  const grouped = new Map<string, { x: unknown[]; y: number[] }>();
  data.series.forEach(point => { const name = point.series || "Valor"; const current = grouped.get(name) || { x: [], y: [] }; current.x.push(point.x); current.y.push(Number(point.y || 0)); grouped.set(name, current); });
  const traces = [...grouped.entries()].map(([name, values]) => widget.chart_type === "pie" || widget.chart_type === "donut"
    ? { type: "pie", labels: values.x, values: values.y, name, hole: widget.chart_type === "donut" ? .45 : 0 }
    : { type: widget.chart_type === "area" ? "scatter" : widget.chart_type === "stacked_bar" ? "bar" : widget.chart_type, x: values.x, y: values.y, name, fill: widget.chart_type === "area" ? "tozeroy" : undefined, mode: widget.chart_type === "line" ? "lines+markers" : undefined });
  return <Plot useResizeHandler style={{ width: "100%", height: 330 }} data={traces as any} layout={{ autosize: true, barmode: widget.chart_type === "stacked_bar" ? "stack" : undefined, margin: { l: 55, r: 15, t: 15, b: 75 }, xaxis: { tickangle: -35 }, yaxis: { tickformat: ",.2f" }, showlegend: traces.length > 1 }} config={{ responsive: true, displaylogo: false }} />;
}

function Visualizations({ views }: { views: PivotView[] }) {
  const [dashboards, setDashboards] = useState<SavedDashboard[]>([]);
  const [selected, setSelected] = useState<number | "">("");
  const [name, setName] = useState("");
  const [widgetTitle, setWidgetTitle] = useState("");
  const [widgetType, setWidgetType] = useState<ChartType>("line");
  const [viewId, setViewId] = useState<number | "">("");
  const [director, setDirector] = useState("");
  const [directors, setDirectors] = useState<string[]>([]);
  const [error, setError] = useState("");
  const load = () => getDashboards().then(items => { setDashboards(items); if (!selected && items[0]) setSelected(items[0].id); }).catch(e => setError(String(e)));
  useEffect(() => { load(); }, []);
  useEffect(() => { const view = views.find(item => item.id === viewId); if (!view) return; getPivotRecords({ destination_table_id: view.destination_table_id, columns: ["departamento"], limit: 1000 }).then(result => setDirectors([...new Set(result.rows.map(row => String(row.departamento || "")).filter(Boolean))].sort())).catch(() => setDirectors([])); }, [viewId, views]);
  const dashboard = dashboards.find(item => item.id === selected);
  const filters = useMemo(() => director.trim() ? { departamento: director.trim() } : {}, [director]);
  const saveDashboard = async () => { if (!name.trim()) return; const item = await createDashboard({ name: name.trim(), pivot_view_id: typeof viewId === "number" ? viewId : null }); setName(""); await load(); setSelected(item.id); };
  const addWidget = async () => { if (!dashboard || !viewId) return; await createDashboardWidget(dashboard.id, { dashboard_id: dashboard.id, pivot_view_id: Number(viewId), title: widgetTitle || "Nova visualização", chart_type: widgetType, config: {} }); setWidgetTitle(""); load(); };
  const createFromView = async (view: PivotView) => { const item = await createDashboard({ name: view.name, pivot_view_id: view.id }); await load(); setSelected(item.id); setViewId(view.id); };
  return <Box><Stack direction="row" gap={1} flexWrap="wrap" alignItems="center" mb={2}><FormControl size="small" sx={{ minWidth: 280 }}><InputLabel>Dashboard salvo</InputLabel><Select label="Dashboard salvo" value={selected} onChange={e => setSelected(Number(e.target.value))}>{dashboards.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}</Select></FormControl><TextField size="small" label="Novo dashboard" value={name} onChange={e => setName(e.target.value)} /><Button variant="contained" startIcon={<Add />} onClick={saveDashboard}>Criar</Button><Button startIcon={<Refresh />} onClick={load}>Atualizar</Button></Stack>
    <Paper sx={{ p: 2, mb: 2 }}><Stack direction="row" gap={1} flexWrap="wrap" alignItems="center"><Typography fontWeight={700}>Filtros globais</Typography><FormControl size="small" sx={{ minWidth: 240 }}><InputLabel>Diretoria</InputLabel><Select label="Diretoria" value={director} onChange={e => setDirector(String(e.target.value))}><MenuItem value="">Todas as diretorias</MenuItem>{directors.map(item => <MenuItem key={item} value={item}>{item}</MenuItem>)}</Select></FormControl><Chip label={director ? `Aplicado: ${director}` : "Todas as diretorias"} color={director ? "primary" : "default"} /></Stack></Paper>
    {!dashboard ? <><Alert severity="info" sx={{ mb: 2 }}>Nenhum dashboard visual foi criado ainda. Selecione uma visão pivot abaixo para criar um dashboard automaticamente.</Alert><Paper sx={{ p: 2 }}><Typography variant="h6" sx={{ mb: 1 }}>Biblioteca de pivots salvos</Typography>{views.map(view => <Stack key={view.id} direction="row" alignItems="center" gap={1} sx={{ py: .75 }}><Typography sx={{ flex: 1 }}>{view.name}</Typography><Chip label={`Pivot #${view.id}`} size="small" variant="outlined" /><Button size="small" variant="contained" onClick={() => createFromView(view)}>Criar dashboard</Button></Stack>)}</Paper></> : <><Paper sx={{ p: 2, mb: 2 }}><Stack direction="row" gap={1} flexWrap="wrap" alignItems="center"><Typography fontWeight={700}>Adicionar widget</Typography><FormControl size="small" sx={{ minWidth: 260 }}><InputLabel>Pivot salvo</InputLabel><Select label="Pivot salvo" value={viewId} onChange={e => setViewId(Number(e.target.value))}>{views.map(v => <MenuItem key={v.id} value={v.id}>{v.name}</MenuItem>)}</Select></FormControl><TextField size="small" label="Título" value={widgetTitle} onChange={e => setWidgetTitle(e.target.value)} /><FormControl size="small" sx={{ minWidth: 170 }}><InputLabel>Tipo de gráfico</InputLabel><Select label="Tipo de gráfico" value={widgetType} onChange={e => setWidgetType(e.target.value as ChartType)}>{(["kpi", "line", "area", "bar", "stacked_bar", "pie", "donut", "heatmap"] as ChartType[]).map(type => <MenuItem key={type} value={type}>{type}</MenuItem>)}</Select></FormControl><Button variant="contained" startIcon={<Add />} onClick={addWidget} disabled={!viewId}>Adicionar</Button></Stack></Paper><Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 2 }}>{dashboard.widgets.map(widget => <Card key={widget.id}><CardContent><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography fontWeight={700}>{widget.title}</Typography><Button color="error" size="small" startIcon={<Delete />} onClick={() => deleteDashboardWidget(dashboard.id, widget.id).then(load)}>Excluir</Button></Stack><WidgetChart widget={widget} globalFilters={filters} /></CardContent></Card>)}</Box></>}
    {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
  </Box>;
}

export default function Dashboards() {
  const [tab, setTab] = useState("pivot");
  const [destinations, setDestinations] = useState<DestinationAnalysis[]>([]);
  const [views, setViews] = useState<PivotView[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getAnalysisDestinations().then(setDestinations),
      getPivotViews().then(setViews),
    ]).finally(() => setLoading(false));
  }, []);

  const initialTableId = useMemo(() => {
    return destinations.find(d => d.name.toUpperCase() === "MODELO FOCUS")?.id || destinations[0]?.id;
  }, [destinations]);

  return (
    <Box>
      <Typography variant="h4" fontWeight={700}>Dashboards</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Monte pivots e transforme visões salvas em painéis gráficos reutilizáveis.
      </Typography>
      <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mb: 2 }}>
        <Tab value="pivot" label="Pivot" />
        <Tab value="visual" label="Visualizações" />
      </Tabs>
      {tab === "pivot" && (
        loading ? (
          <Typography color="text.secondary" sx={{ p: 2 }}>Carregando tabelas Gold...</Typography>
        ) : destinations.length > 0 ? (
          <PivotAnalysis destinations={destinations} initialTableId={initialTableId} showSearch />
        ) : (
          <EmptyState message="Nenhuma tabela Gold ativa disponível para análise pivot." />
        )
      )}
      {tab === "visual" && <Visualizations views={views} />}
    </Box>
  );
}

