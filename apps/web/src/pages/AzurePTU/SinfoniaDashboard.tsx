import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useRef, useState } from "react";
import Plot from "@/components/Plot";
import { Alert, Box, Card, CardContent, Chip, CircularProgress, Grid, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { getSinfoniaDashboard, type SinfoniaDashboard } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const fmtMoney = (value?: number) => money.format(Number(value || 0));
const accents: Record<string, string> = { IBM: "#0F62FE", Azure: "#0078D4", "Fábrica de Agentes": "#7C3AED", "Sinfonia IBM": "#0F62FE" };

function Kpi({ label, value, color, hint }: { label: string; value?: number; color: string; hint?: string }) {
  return <Card sx={{ height: "100%", borderTop: `3px solid ${color}` }}><CardContent>
    <Typography variant="body2" color="text.secondary">{label}</Typography><Typography variant="h6">{fmtMoney(value)}</Typography>
    {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
  </CardContent></Card>;
}

function PortfolioCard({ title, cloud, items }: { title: string; cloud: string; items: Array<{ project: string; cost: number }> }) {
  return <Card sx={{ height: "100%", borderTop: `3px solid ${accents[title] || accents[cloud]}` }}><CardContent>
    <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}><Typography variant="h6">{title}</Typography><Chip size="small" label={cloud} /></Stack>
    {items.map(item => <Box key={item.project} sx={{ py: 1, borderTop: "1px solid", borderColor: "divider", display: "flex", justifyContent: "space-between", gap: 1 }}>
      <Typography variant="body2">{item.project}</Typography><Typography variant="body2" fontWeight={700}>{fmtMoney(item.cost)}</Typography>
    </Box>)}
  </CardContent></Card>;
}

export default function SinfoniaDashboard() {
  const [data, setData] = useState<SinfoniaDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => { const controller = new AbortController(); abort.current = controller; setError(null);
    getSinfoniaDashboard(controller.signal).then(value => { if (!controller.signal.aborted) setData(value); }).catch(error => {
      if (error?.code !== "ERR_CANCELED") setError(error?.response?.data?.detail || "Falha ao consultar o dashboard Sinfonia.");
    }); return () => controller.abort(); }, []);
  if (error) return <Alert severity="error">{dataDisplayText(error)}</Alert>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;
  if (data.empty) return <Alert severity="info">Nenhum custo do portfólio Sinfonia foi encontrado na base consolidada.</Alert>;
  const kpis = data.kpis || { total: 0, ibm: 0, factory: 0, forecast: 0 };
  const projects = data.projects || [];
  const ibm = projects.filter(x => x.group === "Sinfonia IBM");
  const factory = projects.filter(x => x.group === "Fábrica de Agentes");
  const monthly = data.monthly || [];
  return <Stack gap={2.5}>
    <Box><Typography variant="h4">Projeto Sinfonia</Typography><Typography color="text.secondary">Visão executiva consolidada de Sinfonia IBM e Fábrica de Agentes Azure.</Typography>
      <Stack direction="row" gap={1} mt={1} flexWrap="wrap"><Chip size="small" label={`Referência: ${data.reference?.label || "—"}`} /><Chip size="small" color={data.reference?.source === "forecast" ? "warning" : "success"} label={data.reference?.source === "forecast" ? "Forecast do mês aberto" : "Realizado fechado"} /><Chip size="small" variant="outlined" label="Fonte: Custos consolidados do portfólio Sinfonia" /></Stack></Box>
    <Grid container spacing={2}><Grid item xs={12} sm={6} md={3}><Kpi label="Custo total do projeto" value={kpis.total} color="#0F172A" hint="IBM + Azure, incluindo rateios" /></Grid><Grid item xs={12} sm={6} md={3}><Kpi label="Sinfonia IBM" value={kpis.ibm} color={accents.IBM} hint="Watson e VivoCog" /></Grid><Grid item xs={12} sm={6} md={3}><Kpi label="Fábrica de Agentes" value={kpis.factory} color={accents["Fábrica de Agentes"]} hint="Comercial, Financeiro e Suporte Técnico" /></Grid><Grid item xs={12} sm={6} md={3}><Kpi label="Forecast do mês" value={kpis.forecast} color="#F59E0B" hint="Referência enquanto o mês estiver aberto" /></Grid></Grid>
    <Grid container spacing={2}><Grid item xs={12} md={5}><PortfolioCard title="Sinfonia IBM" cloud="IBM" items={ibm} /></Grid><Grid item xs={12} md={7}><PortfolioCard title="Fábrica de Agentes" cloud="Azure" items={factory} /></Grid></Grid>
    <Card><CardContent><Typography variant="h6">Evolução mensal — consumo e forecast</Typography><Typography variant="body2" color="text.secondary" gutterBottom>As barras empilhadas mostram IBM e Fábrica; a linha indica o forecast consolidado.</Typography>
      <Plot data={[{ x: monthly.map(x => x.label), y: monthly.map(x => x.ibm), name: "Sinfonia IBM", type: "bar", marker: { color: accents.IBM } }, { x: monthly.map(x => x.label), y: monthly.map(x => x.factory), name: "Fábrica de Agentes", type: "bar", marker: { color: accents["Fábrica de Agentes"] } }, { x: monthly.map(x => x.label), y: monthly.map(x => x.forecast_total), name: "Forecast total", type: "scatter", mode: "lines+markers", line: { color: "#F59E0B", width: 3 } }]} layout={{ autosize: true, barmode: "stack", hovermode: "x unified", margin: { l: 70, r: 20, t: 20, b: 55 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", yaxis: { title: { text: "R$" }, rangemode: "tozero" }, legend: { orientation: "h", y: -0.25 } }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%", height: 340 }} useResizeHandler />
    </CardContent></Card>
    <Grid container spacing={2}><Grid item xs={12} md={4}><Card sx={{ height: "100%" }}><CardContent><Typography variant="h6">Custo por cloud</Typography>{(data.clouds || []).map(item => <Box key={item.cloud} sx={{ display: "flex", justifyContent: "space-between", py: 1, borderTop: "1px solid", borderColor: "divider" }}><Typography>{item.cloud}</Typography><Typography fontWeight={700}>{fmtMoney(item.cost)}</Typography></Box>)}</CardContent></Card></Grid><Grid item xs={12} md={8}><Card sx={{ height: "100%" }}><CardContent><Typography variant="h6">Famílias de IA identificadas</Typography>{(data.ai_families || []).length ? (data.ai_families || []).map(item => <Box key={item.family} sx={{ display: "flex", justifyContent: "space-between", py: 1, borderTop: "1px solid", borderColor: "divider" }}><Typography>{item.family}</Typography><Typography fontWeight={700}>{fmtMoney(item.cost)}</Typography></Box>) : <Alert severity="info" sx={{ mt: 1 }}>Sem família de IA identificada no recorte selecionado; isto não confirma custo zero fora dele.</Alert>}</CardContent></Card></Grid></Grid>
    <Card><CardContent><Typography variant="h6">Top serviços por custo</Typography><Table size="small"><TableHead><TableRow><TableCell>Cloud</TableCell><TableCell>Serviço</TableCell><TableCell align="right">Custo</TableCell></TableRow></TableHead><TableBody>{(data.services || []).map(item => <TableRow key={`${item.cloud}-${item.service}`}><TableCell>{item.cloud}</TableCell><TableCell>{item.service}</TableCell><TableCell align="right">{fmtMoney(item.cost)}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card>
    {(data.notes || []).map(note => <Alert severity="info" key={note}>{note}</Alert>)}
  </Stack>;
}
