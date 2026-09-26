import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, ButtonGroup, FormControl, InputLabel, MenuItem, Paper, Select, Stack, Tab, Tabs, Typography } from "@mui/material";
import Plot from "@/components/Plot";
import { GoldInfo, GoldQueryFilter, GoldQueryResult, queryDimonTable } from "../../services/api";
import HierarchicalDecompTree, { buildExecutiveDecompChain, type DecompPathStep } from "./HierarchicalDecompTree";

const DIMENSIONS: Array<{ label: string; field: string }> = [
  { label: "Cloud", field: "cloud_origem" },
  { label: "Segmento", field: "organograma_nivel_1" },
  { label: "Modelo", field: "organograma_nivel_2" },
  { label: "VP", field: "organograma_nivel_3" },
  { label: "Diretoria", field: "organograma_nivel_4" },
  { label: "Projeto", field: "organograma_nivel_5" },
  { label: "SubProjeto", field: "organograma_nivel_6" },
  { label: "Serviço", field: "servicename" },
  { label: "ServiceSubcategory", field: "servicesubcategory" },
  { label: "Sub Serviço", field: "skumeter" },
];
const fmt = (value: number) => Math.abs(value) >= 1_000_000
  ? `R$ ${(value / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MM`
  : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }).format(value);
const rows = (result: GoldQueryResult | null) => result?.rows || [];
const monthLabel = (month: string) => `${month.slice(4, 6)}/${month.slice(0, 4)}`;

export default function ExecutiveWidgetsV2({ table, info, filters, filterGroups, directorView = false, organizationPathDepth = 0, refreshKey = 0, onAddFilter, year }: {
  table: string; info: GoldInfo | null; filters: GoldQueryFilter[]; filterGroups: GoldQueryFilter[][];
  directorView?: boolean; organizationPathDepth?: number; refreshKey?: number;
  onAddFilter: (column: string, value: string) => void; year: number;
}) {
  const availableDimensions = useMemo(() => DIMENSIONS.filter((dimension) => info?.dimensions.some((field) => field.toLowerCase() === dimension.field)), [info]);
  const [dimension, setDimension] = useState("cloud_origem");
  const [tab, setTab] = useState(0);
  const [heatMode, setHeatMode] = useState<"cost" | "percent">("cost");
  const [heatYear, setHeatYear] = useState(year);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [monthly, setMonthly] = useState<GoldQueryResult | null>(null);
  const [annualHeat, setAnnualHeat] = useState<GoldQueryResult | null>(null);
  const [currentMonth, setCurrentMonth] = useState("");
  const treeChain = useMemo(() => {
    const orgAndService = buildExecutiveDecompChain(directorView, organizationPathDepth);
    const chain = organizationPathDepth === 0
      ? [{ field: "cloud_origem", label: "Cloud" }, ...orgAndService]
      : orgAndService;
    return chain.filter((item) => info?.dimensions.some((field) => field.toLowerCase() === item.field));
  }, [directorView, organizationPathDepth, info]);
  const key = JSON.stringify([table, filters, filterGroups, dimension, directorView, organizationPathDepth, refreshKey, heatYear]);

  useEffect(() => {
    if (availableDimensions.length && !availableDimensions.some((item) => item.field === dimension)) setDimension(availableDimensions[0].field);
  }, [availableDimensions, dimension]);
  useEffect(() => setHeatYear(year), [year]);

  useEffect(() => {
    if (!info) return;
    let active = true;
    setLoading(true); setError("");
    const hasType = info.columns.some((column) => column.name.toLowerCase() === "tipo");
    const realized = [...filters, ...(hasType ? [{ column: "tipo", operator: "IS_REALIZED" as const, values: [] }] : [])];
    const context = { table, filters: realized, filter_groups: filterGroups.length ? filterGroups : undefined, row_limit: 10000 };
    const yearFilters = [...realized.filter((filter) => filter.column !== "_billing_period"), { column: "_billing_period", operator: "IN" as const, values: Array.from({ length: 12 }, (_, i) => `${heatYear}${String(i + 1).padStart(2, "0")}`) }];
    Promise.all([
      queryDimonTable({ ...context, rows: [dimension, "_billing_period"] }),
      queryDimonTable({ table, filters: yearFilters, filter_groups: filterGroups.length ? filterGroups : undefined, rows: [dimension, "_billing_period"], row_limit: 10000 }),
    ]).then(([byMonth, heatmap]) => {
      if (!active) return;
      setMonthly(byMonth); setAnnualHeat(heatmap);
      const periods = [...new Set(rows(byMonth).map((row) => String(row._billing_period ?? "")))].filter(Boolean).sort();
      setCurrentMonth(periods[periods.length - 1] || "");
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Falha ao carregar os quadros executivos"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [key, info]);

  const annualRows = rows(annualHeat);
  const heatMonths = Array.from({ length: 12 }, (_, i) => `${heatYear}${String(i + 1).padStart(2, "0")}`);
  const heatDims = [...new Set(annualRows.map((row) => String(row[dimension] ?? "Não informado")))].sort((a, b) => {
    const sum = (name: string) => annualRows.filter((row) => String(row[dimension] ?? "Não informado") === name).reduce((total, row) => total + Number(row.realizado || 0), 0);
    return sum(b) - sum(a);
  }).slice(0, 30);
  const heatValues = (name: string, month: string) => Number(annualRows.find((row) => String(row[dimension] ?? "Não informado") === name && String(row._billing_period) === month)?.realizado || 0);
  const concentrationFilters = useMemo(() => {
    const base = filters.filter((filter) => filter.column !== "_billing_period");
    if (currentMonth) base.push({ column: "_billing_period", operator: "IN", values: [currentMonth] });
    if (info?.columns.some((column) => column.name.toLowerCase() === "tipo")) base.push({ column: "tipo", operator: "IS_REALIZED", values: [] });
    return base;
  }, [filters, currentMonth, info]);
  const concentrationKey = JSON.stringify([key, currentMonth, treeChain]);
  const applyPath = (path: DecompPathStep[]) => path.forEach((step) => onAddFilter(step.field, step.value));

  return <Paper variant="outlined" sx={{ p: 2, mt: 2, borderRadius: "20px", borderColor: "#e2e7f0", bgcolor: "#fff", boxShadow: "0 8px 28px rgba(24, 39, 75, 0.045)" }}>
    <Typography variant="overline" sx={{ color: "#6416dc", letterSpacing: ".08em" }} fontWeight={800}>03 · COST CONCENTRATION</Typography>
    <Stack direction={{ xs: "column", md: "row" }} alignItems={{ xs: "stretch", md: "flex-start" }} justifyContent="space-between" gap={2}>
      <Box flex={1} minWidth={0}>
        <Typography variant="h6" fontWeight={750} sx={{ color: "#111b4e" }}>Onde o dinheiro está concentrado?</Typography>
        <Typography variant="caption" color="text.secondary">Decomponha o realizado pelos níveis disponíveis até Sub Serviço. O recorte usa a última competência realizada dentro do período selecionado{currentMonth ? ` · ${monthLabel(currentMonth)}` : ""}.</Typography>
        <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mt: 1, "& .MuiTabs-indicator": { bgcolor: "#6416dc", height: 3, borderRadius: 3 }, "& .MuiTab-root": { color: "#64708b", fontWeight: 750, minHeight: 42 }, "& .Mui-selected": { color: "#6416dc !important" } }}><Tab label="Cost Concentration" /><Tab label="FinOps Heatmap" /></Tabs>
      </Box>
      {tab === 1 && <Stack direction={{ xs: "column", sm: "row" }} gap={1}>
        <FormControl size="small" sx={{ minWidth: 180 }}><InputLabel>Dimensão</InputLabel><Select label="Dimensão" value={dimension} onChange={(event) => setDimension(String(event.target.value))}>{availableDimensions.map((item) => <MenuItem key={item.field} value={item.field}>{item.label}</MenuItem>)}</Select></FormControl>
      </Stack>}
    </Stack>
    {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
    {loading && <Typography color="text.secondary" mt={2}>Carregando dados realizados…</Typography>}
    {!loading && tab === 0 && !currentMonth && <Alert severity="info" sx={{ mt: 1 }}>Sem dados realizados no período selecionado.</Alert>}
    {!loading && tab === 0 && currentMonth && <Box mt={1.5}><HierarchicalDecompTree table={table} baseFilters={concentrationFilters} filterGroups={filterGroups} chain={treeChain} queryKey={concentrationKey} onApplyContext={applyPath} shareCaption="participação no recorte executivo" filterRowTypes={false} /></Box>}
    {tab === 1 && <>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", sm: "center" }} gap={1} mt={1}><Box><Typography variant="subtitle1" fontWeight={750}>FINOPS HEATMAP · {heatYear}</Typography><Typography variant="caption" color="text.secondary">Dimensão × competência. A intensidade mostra custo absoluto ou percentual do realizado de cada mês.</Typography></Box><Stack direction="row" gap={1}><FormControl size="small" sx={{ minWidth: 110 }}><InputLabel>Ano</InputLabel><Select label="Ano" value={heatYear} onChange={(event) => setHeatYear(Number(event.target.value))}>{Array.from({ length: 8 }, (_, i) => new Date().getFullYear() - 5 + i).map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}</Select></FormControl><ButtonGroup size="small"><Button variant={heatMode === "cost" ? "contained" : "outlined"} onClick={() => setHeatMode("cost")}>Custo</Button><Button variant={heatMode === "percent" ? "contained" : "outlined"} onClick={() => setHeatMode("percent")}>%</Button></ButtonGroup></Stack></Stack>
      {annualRows.length ? <Plot data={[{ type: "heatmap", x: heatMonths.map(monthLabel), y: heatDims, z: heatDims.map((name) => heatMonths.map((month) => { const value = heatValues(name, month); const denominator = annualRows.filter((row) => String(row._billing_period) === month).reduce((sum, row) => sum + Number(row.realizado || 0), 0); return heatMode === "percent" && denominator ? value / denominator * 100 : value; })), colorscale: "Purples", colorbar: { title: heatMode === "percent" ? "% do mês" : "R$" }, hovertemplate: "%{y}<br>%{x}<br>%{z:,.2f}<extra></extra>" } as any]} layout={{ height: Math.max(380, heatDims.length * 28), autosize: true, margin: { l: 180, r: 30, t: 15, b: 55 }, xaxis: { type: "category" } }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} /> : <Alert severity="info" sx={{ mt: 1 }}>{loading ? "Consultando o ano completo…" : "Sem competências realizadas disponíveis neste ano para os filtros atuais."}</Alert>}
    </>}
    <Typography variant="caption" color="text.secondary" display="block" mt={1}>Ajuda: Cost Concentration segue a hierarquia do consumo e calcula custo, participação e variação mensal no escopo selecionado. Use os níveis até Sub Serviço para aprofundar; “Aplicar contexto” propaga o caminho para os filtros globais. O Heatmap exibe o ano completo.</Typography>
  </Paper>;
}
