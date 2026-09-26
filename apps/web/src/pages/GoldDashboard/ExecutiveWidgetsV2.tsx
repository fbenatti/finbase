import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, ButtonGroup, FormControl, InputLabel, MenuItem, Paper, Select, Stack, Tab, Tabs, Typography } from "@mui/material";
import Plot from "@/components/Plot";
import { GoldInfo, GoldQueryFilter, GoldQueryResult, queryDimonTable, queryGoldDailyConsumption } from "../../services/api";

const LENSES: Array<{ label: string; field: string }> = [
  { label: "VP", field: "organograma_nivel_3" },
  { label: "Diretoria", field: "organograma_nivel_4" },
  { label: "Projeto", field: "organograma_nivel_5" },
  { label: "Família de serviço", field: "servicecategory" },
  { label: "Região", field: "regionname" },
];
const fmt = (value: number) => Math.abs(value) >= 1_000_000
  ? `R$ ${(value / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MM`
  : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }).format(value);
const rows = (result: GoldQueryResult | null) => result?.rows || [];
const monthLabel = (month: string) => `${month.slice(4, 6)}/${month.slice(0, 4)}`;

export default function ExecutiveWidgetsV2({ table, info, filters, filterGroups, refreshKey = 0, onAddFilter, year }: {
  table: string; info: GoldInfo | null; filters: GoldQueryFilter[]; filterGroups: GoldQueryFilter[][];
  refreshKey?: number; onAddFilter: (column: string, value: string) => void; year: number;
}) {
  const availableLenses = useMemo(() => LENSES.filter((lens) => info?.dimensions.some((field) => field.toLowerCase() === lens.field)), [info]);
  const [lens, setLens] = useState("organograma_nivel_3");
  const [tab, setTab] = useState(0);
  const [heatMode, setHeatMode] = useState<"cost" | "percent">("cost");
  const [heatYear, setHeatYear] = useState(year);
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [monthly, setMonthly] = useState<GoldQueryResult | null>(null);
  const [annualHeat, setAnnualHeat] = useState<GoldQueryResult | null>(null);
  const [daily, setDaily] = useState<{ available: boolean; rows: Array<{ day: string; realizado: number }> } | null>(null);
  const key = JSON.stringify([table, filters, filterGroups, lens, refreshKey, heatYear]);
  useEffect(() => {
    if (availableLenses.length && !availableLenses.some((item) => item.field === lens)) setLens(availableLenses[0].field);
  }, [availableLenses, lens]);
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
      queryDimonTable({ ...context, rows: [lens, "_billing_period"] }),
      queryDimonTable({ table, filters: yearFilters, filter_groups: filterGroups.length ? filterGroups : undefined, rows: [lens, "_billing_period"], row_limit: 10000 }),
      queryGoldDailyConsumption({ table, filters: realized, filter_groups: filterGroups.length ? filterGroups : undefined, row_limit: 10000 }),
    ]).then(([byMonth, heatmap, day]) => {
      if (active) { setMonthly(byMonth); setAnnualHeat(heatmap); setDaily(day); }
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Falha ao carregar os quadros executivos"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [key, info]);

  const monthlyRows = rows(monthly);
  const months = [...new Set(monthlyRows.map((row) => String(row._billing_period ?? "")))].filter(Boolean).sort();
  const currentMonth = months[months.length - 1] || "";
  const previousMonth = months[months.length - 2] || "";
  const scopedRank = monthlyRows.filter((row) => String(row._billing_period) === currentMonth).reduce<Record<string, number>>((result, row) => {
    const name = String(row[lens] ?? "Não informado"); result[name] = (result[name] || 0) + Number(row.realizado || 0); return result;
  }, {});
  const total = Object.values(scopedRank).reduce((sum, value) => sum + value, 0);
  const ranking = Object.entries(scopedRank).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
  const topFiveShare = total ? ranking.slice(0, 5).reduce((sum, item) => sum + item.value, 0) / total * 100 : 0;
  const priorByName = monthlyRows.filter((row) => String(row._billing_period) === previousMonth).reduce<Record<string, number>>((result, row) => {
    const name = String(row[lens] ?? "Não informado"); result[name] = (result[name] || 0) + Number(row.realizado || 0); return result;
  }, {});
  const annualRows = rows(annualHeat);
  const heatMonths = Array.from({ length: 12 }, (_, i) => `${heatYear}${String(i + 1).padStart(2, "0")}`);
  const heatDims = [...new Set(annualRows.map((row) => String(row[lens] ?? "Não informado")))].sort((a, b) => {
    const sum = (name: string) => annualRows.filter((row) => String(row[lens] ?? "Não informado") === name).reduce((totalValue, row) => totalValue + Number(row.realizado || 0), 0);
    return sum(b) - sum(a);
  }).slice(0, 30);
  const heatValues = (name: string, month: string) => Number(annualRows.find((row) => String(row[lens] ?? "Não informado") === name && String(row._billing_period) === month)?.realizado || 0);

  return <>
    <Paper variant="outlined" sx={{ p: 2, mt: 2, borderRadius: "20px", borderColor: "#e2e7f0", bgcolor: "#fff", boxShadow: "0 8px 28px rgba(24, 39, 75, 0.045)" }}>
      <Typography variant="overline" sx={{ color: "#6416dc", letterSpacing: ".08em" }} fontWeight={800}>03 · COST CONCENTRATION</Typography>
      <Stack direction={{ xs: "column", md: "row" }} alignItems={{ xs: "stretch", md: "flex-start" }} justifyContent="space-between" gap={2}>
        <Box flex={1} minWidth={0}>
          <Typography variant="h6" fontWeight={750} sx={{ color: "#111b4e" }}>Onde o dinheiro está concentrado?</Typography>
          <Typography variant="caption" color="text.secondary">Troque a lente sem sair da página. O período segue os filtros executivos globais.</Typography>
          <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mt: 1, "& .MuiTabs-indicator": { bgcolor: "#6416dc", height: 3, borderRadius: 3 }, "& .MuiTab-root": { color: "#64708b", fontWeight: 750, minHeight: 42 }, "& .Mui-selected": { color: "#6416dc !important" } }}><Tab label="Cost Concentration" /><Tab label="FinOps Heatmap" /></Tabs>
        </Box>
        {tab === 0 && <ButtonGroup size="small" sx={{ flexWrap: "wrap", gap: .5, "& .MuiButtonGroup-grouped": { border: "1px solid #dce2ee !important", borderRadius: "9px !important", textTransform: "none", fontSize: 12, fontWeight: 700, color: "#44506c" } }}>{availableLenses.map((item) => <Button key={item.field} variant={lens === item.field ? "contained" : "outlined"} onClick={() => setLens(item.field)} sx={lens === item.field ? { bgcolor: "#f2eaff", color: "#6416dc", borderColor: "#c8aaff !important", boxShadow: "none", "&:hover": { bgcolor: "#eadcff", boxShadow: "none" } } : { bgcolor: "#fff" }}>{item.label}</Button>)}</ButtonGroup>}
      </Stack>
      {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
      {loading && <Typography color="text.secondary" mt={2}>Carregando dados realizados…</Typography>}
      {!loading && tab === 0 && !ranking.length && !error && <Alert severity="info" sx={{ mt: 1 }}>Sem dados realizados na última competência disponível para este recorte.</Alert>}
      {tab === 0 && !!ranking.length && <Stack direction={{ xs: "column", md: "row" }} gap={1.5} mt={1.5}>
        <Stack spacing={.7} flex={1}>{(showAll ? ranking : ranking.slice(0, 5)).map(({ name, value }) => {
          const share = total ? value / total * 100 : 0; const prior = priorByName[name]; const mom = prior ? (value - prior) / Math.abs(prior) * 100 : null;
          return <Paper variant="outlined" key={name} sx={{ p: .7, borderRadius: "12px", borderColor: "#e2e7f0", bgcolor: "#fff" }}><Stack direction="row" alignItems="center" gap={1}>
            <Button onClick={() => onAddFilter(lens, name)} sx={{ minWidth: 0, width: "34%", justifyContent: "flex-start", textAlign: "left", fontSize: 12, fontWeight: 700, color: "text.primary", textTransform: "uppercase" }}>{name}</Button>
            <Box sx={{ flex: 1, bgcolor: "action.hover", height: 7, borderRadius: 5 }}><Box sx={{ bgcolor: "#7025e8", backgroundImage: "linear-gradient(90deg,#6416dc,#9c4dff)", width: `${Math.min(100, share)}%`, height: "100%", borderRadius: 5 }} /></Box>
            <Typography variant="caption" fontWeight={800} sx={{ minWidth: 90, textAlign: "right" }}>{fmt(value)}</Typography>
            <Typography variant="caption" fontWeight={700} color={mom == null ? "text.secondary" : mom >= 0 ? "error.main" : "success.main"} sx={{ minWidth: 60, textAlign: "right" }}>{mom == null ? "—" : `${mom >= 0 ? "▲" : "▼"} ${Math.abs(mom).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`}</Typography>
          </Stack></Paper>;
        })}<Button onClick={() => setShowAll((value) => !value)} sx={{ alignSelf: "flex-start" }}>{showAll ? "Mostrar Top 5" : "+ Ver todos"}</Button></Stack>
        <Paper variant="outlined" sx={{ minWidth: { md: 180 }, borderRadius: "14px", borderColor: "#e2e7f0", display: "grid", placeContent: "center", textAlign: "center", p: 2, bgcolor: "#fafbfe" }}><Typography variant="caption" fontWeight={700} color="text.secondary">TOP 5 REPRESENTAM</Typography><Typography variant="h3" fontWeight={850} sx={{ color: "#6416dc" }}>{topFiveShare.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%</Typography><Typography variant="caption" color="text.secondary">do custo realizado na lente {availableLenses.find((item) => item.field === lens)?.label || lens}{currentMonth ? ` · ${monthLabel(currentMonth)}` : ""}</Typography></Paper>
      </Stack>}
      {tab === 1 && <>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", sm: "center" }} gap={1} mt={1}><Box><Typography variant="subtitle1" fontWeight={750}>FINOPS HEATMAP · {heatYear}</Typography><Typography variant="caption" color="text.secondary">Dimensão × competência. A intensidade mostra custo absoluto ou percentual do realizado de cada mês.</Typography></Box><Stack direction="row" gap={1}><FormControl size="small" sx={{ minWidth: 110 }}><InputLabel>Ano</InputLabel><Select label="Ano" value={heatYear} onChange={(event) => setHeatYear(Number(event.target.value))}>{Array.from({ length: 8 }, (_, i) => new Date().getFullYear() - 5 + i).map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}</Select></FormControl><ButtonGroup size="small"><Button variant={heatMode === "cost" ? "contained" : "outlined"} onClick={() => setHeatMode("cost")}>Custo</Button><Button variant={heatMode === "percent" ? "contained" : "outlined"} onClick={() => setHeatMode("percent")}>%</Button></ButtonGroup></Stack></Stack>
        {annualRows.length ? <Plot data={[{ type: "heatmap", x: heatMonths.map(monthLabel), y: heatDims, z: heatDims.map((name) => heatMonths.map((month) => { const value = heatValues(name, month); const denominator = annualRows.filter((row) => String(row._billing_period) === month).reduce((sum, row) => sum + Number(row.realizado || 0), 0); return heatMode === "percent" && denominator ? value / denominator * 100 : value; })), colorscale: "Purples", colorbar: { title: heatMode === "percent" ? "% do mês" : "R$" }, hovertemplate: "%{y}<br>%{x}<br>%{z:,.2f}<extra></extra>" } as any]} layout={{ height: Math.max(380, heatDims.length * 28), autosize: true, margin: { l: 180, r: 30, t: 15, b: 55 }, xaxis: { type: "category" } }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} /> : <Alert severity="info" sx={{ mt: 1 }}>{loading ? "Consultando o ano completo…" : "Sem competências realizadas disponíveis neste ano para os filtros atuais."}</Alert>}
      </>}
      <Typography variant="caption" color="text.secondary" display="block" mt={1}>Ajuda: Cost Concentration usa a última competência realizada dentro do período selecionado; Top 5 é calculado sobre o total do filtro antes do limite da lista. O Heatmap sempre cobre janeiro a dezembro do ano exibido. Clique em uma linha para aplicar sua dimensão aos filtros globais.</Typography>
    </Paper>
    <Paper variant="outlined" sx={{ p: 2, mt: 2, borderRadius: "20px", borderColor: "#e2e7f0", bgcolor: "#fff", boxShadow: "0 8px 28px rgba(24, 39, 75, 0.045)" }}><Typography variant="h6" fontWeight={750} sx={{ color: "#111b4e" }}>Consumo realizado dia a dia</Typography><Typography variant="caption" color="text.secondary">Data real de consumo da fonte Gold, sem distribuir a competência mensal entre dias.</Typography>{daily?.available && daily.rows.length ? <Plot data={[{ x: daily.rows.map((row) => row.day), y: daily.rows.map((row) => Number(row.realizado)), type: "scatter", mode: "lines+markers", line: { color: "#6416dc", width: 3 }, marker: { color: "#6416dc", size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra></extra>" } as any]} layout={{ height: 320, autosize: true, margin: { l: 80, r: 20, t: 20, b: 50 }, yaxis: { tickprefix: "R$ ", separatethousands: true, gridcolor: "#edf0f6" }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} /> : <Alert severity="info" sx={{ mt: 1 }}>{daily?.available === false ? "A fonte atual não expõe uma data diária para esse recorte." : loading ? "Consultando datas diárias…" : "Sem dados diários no recorte atual."}</Alert>}</Paper>
  </>;
}
