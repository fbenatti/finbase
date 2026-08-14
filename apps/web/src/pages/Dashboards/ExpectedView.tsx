import { useEffect, useState } from "react";
import Plot from "react-plotly.js";
import { Alert, Box, Card, CardContent, CircularProgress, FormControl, Grid, InputLabel, MenuItem, Paper, Select, Stack, Typography } from "@mui/material";
import { getExpectedView, ExpectedViewRow } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

export default function ExpectedView() {
  const [rows, setRows] = useState<ExpectedViewRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [periodFilter, setPeriodFilter] = useState("current");
  useEffect(() => { getExpectedView().then(result => setRows(result.rows)).catch(e => setError(e instanceof Error ? e.message : "Não foi possível carregar a Visão Esperada")).finally(() => setLoading(false)); }, []);
  const years = [...new Set(rows.map(row => row.period.slice(0, 4)))].sort();
  const filteredRows = periodFilter === "all" ? rows : periodFilter === "current" ? rows.filter(row => row.period.startsWith(String(new Date().getFullYear()))) : rows.filter(row => row.period.startsWith(periodFilter));
  const toDate = (period: string) => period.length === 6 ? `${period.slice(0, 4)}-${period.slice(4, 6)}-01` : period;
  const labels = filteredRows.map(row => toDate(row.period));
  const actual = filteredRows.reduce((sum, row) => sum + Number(row.actual_value || 0), 0);
  const forecast = filteredRows.reduce((sum, row) => sum + Number(row.forecast_value || 0), 0);
  const estimate = filteredRows.length ? filteredRows[filteredRows.length - 1].cumulative_value : 0;
  const variance = forecast - estimate;
  const actualEnd = filteredRows.reduce((last, row, index) => row.actual_value == null ? last : index, -1);
  const actualCumulative = filteredRows.map((row, index) => index <= actualEnd ? filteredRows.slice(0, index + 1).reduce((sum, item) => sum + Number(item.actual_value || 0), 0) : null);
  const forecastCumulative = filteredRows.map((row, index) => index < actualEnd ? null : filteredRows.slice(0, index + 1).reduce((sum, item) => sum + Number(item.expected_value || 0), 0));
  const chartData: any[] = [{ x: labels, y: actualCumulative, type: "scatter", mode: "lines+markers", name: "Actual", line: { color: "#F2A900", width: 3 }, marker: { size: 6 } }, { x: labels, y: forecastCumulative, type: "scatter", mode: "lines+markers", name: "Forecast", line: { color: "#16A34A", width: 3 }, marker: { size: 6 } }];
  const chartLayout: any = { autosize: true, margin: { l: 82, r: 24, t: 20, b: 82 }, hovermode: "x unified", xaxis: { type: "date", tickformat: "%b %Y", dtick: "M1", title: { text: "Período", standoff: 18 }, side: "bottom", showticklabels: true, tickangle: -35 }, yaxis: { title: { text: "Valor acumulado", standoff: 12 }, side: "left", showticklabels: true, tickprefix: "$", tickformat: ".2s", zeroline: true }, legend: { orientation: "h", y: -0.28 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" };
  const waterfallLabels = filteredRows.slice(-8).map(row => toDate(row.period));
  const waterfallValues = filteredRows.slice(-8).map(row => Number(row.expected_value || 0));
  const waterfallData: any[] = [{ type: "waterfall", x: waterfallLabels, y: waterfallValues, measure: waterfallValues.map(() => "relative"), connector: { line: { color: "#9CA3AF" } }, increasing: { marker: { color: "#16A34A" } }, decreasing: { marker: { color: "#EF4444" } }, name: "Variação mensal" }];
  const waterfallLayout: any = { autosize: true, margin: { l: 82, r: 20, t: 25, b: 82 }, yaxis: { title: { text: "Variação", standoff: 12 }, side: "left", showticklabels: true, tickprefix: "$", tickformat: ".2s" }, xaxis: { type: "date", tickformat: "%b %Y", dtick: "M1", title: { text: "Período", standoff: 18 }, side: "bottom", showticklabels: true, tickangle: -35 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" };
  const card = (label: string, value: number, color: string) => <Card sx={{ flex: 1, minWidth: 190, borderTop: `3px solid ${color}` }}><CardContent><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="h5" fontWeight={700}>{money.format(value)}</Typography><Typography variant="caption">Current Year</Typography></CardContent></Card>;
  return <Box><Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}><Box><Typography variant="h4" fontWeight={700}>Visão Esperada</Typography><Typography color="text.secondary">Forecast Summary · DIR DADOS E INTELIGENCIA ARTIFICIAL</Typography></Box><FormControl size="small" sx={{ minWidth: 170 }}><InputLabel>Período</InputLabel><Select label="Período" value={periodFilter} onChange={e => setPeriodFilter(String(e.target.value))}><MenuItem value="current">Current Year</MenuItem><MenuItem value="all">Todos os períodos</MenuItem>{years.map(year => <MenuItem key={year} value={year}>{year}</MenuItem>)}</Select></FormControl></Stack><Alert severity="info" sx={{ mb: 2 }}>Realizado é usado até o último período disponível; o forecast projeta os períodos seguintes. O filtro atualiza cards e gráficos.</Alert>{error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}{loading ? <CircularProgress sx={{ display: "block", mx: "auto", mt: 6 }} /> : <><Stack direction="row" gap={2} flexWrap="wrap" mb={2}>{card("Actual", actual, "#F2A900")}{card("Estimate", estimate, "#64748B")}{card("Forecast", forecast, "#16A34A")}{card("Variance", variance, variance >= 0 ? "#16A34A" : "#EF4444")}</Stack><Grid container spacing={2}><Grid item xs={12} md={7}><Paper sx={{ p: 2 }}><Typography variant="h6">Spend Over Time</Typography><Typography variant="caption" color="text.secondary">Cumulative · Actual and Forecast</Typography><Plot useResizeHandler style={{ width: "100%", height: 420 }} data={chartData} layout={chartLayout} config={{ responsive: true, displaylogo: false }} /></Paper></Grid><Grid item xs={12} md={5}><Paper sx={{ p: 2 }}><Typography variant="h6">Forecast Variance</Typography><Typography variant="caption" color="text.secondary">Monthly expected movement</Typography><Plot useResizeHandler style={{ width: "100%", height: 420 }} data={waterfallData} layout={waterfallLayout} config={{ responsive: true, displaylogo: false }} /></Paper></Grid></Grid></>}{!loading && filteredRows.length > 0 && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>Último acumulado esperado: {money.format(estimate)}</Typography>}</Box>;
}
