import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Card, CardContent, CircularProgress, Grid, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { queryDimonTable } from "../../services/api";

const TABLE = "dest_dm_cost_visao_completa";
const AZURE_FILTER = { column: "cloud_origem", operator: "IN" as const, values: ["azure-ti", "azure-4p", "azure"] };
const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1, signDisplay: "always" });
const monthName = (month: string) => {
  const match = month.match(/^(\d{4})[-/]?(\d{2})/);
  if (!match) return month;
  return new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
};

type MonthRow = { period: string; value: number; delta: number | null; deltaPct: number | null };

export default function DollarEvolution() {
  const [rows, setRows] = useState<MonthRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    queryDimonTable({
      table: TABLE,
      rows: ["_billing_period"],
      measures: [{ column: "realizado", aggregation: "SUM" }],
      filters: [AZURE_FILTER, { column: "tipo", operator: "IS_REALIZED", values: [] }],
      row_limit: 120,
      sort_column: "_billing_period",
      sort_direction: "asc",
    }).then(result => {
      if (!alive) return;
      const ordered = result.rows
        .map(row => ({ period: String(row._billing_period ?? ""), value: Number(row.realizado ?? 0) }))
        .filter(row => /^\d{4}[-/]?\d{2}/.test(row.period))
        .sort((a, b) => a.period.localeCompare(b.period));
      setRows(ordered.map((row, index) => {
        const previous = ordered[index - 1]?.value;
        return { ...row, delta: previous === undefined ? null : row.value - previous, deltaPct: previous === undefined || previous === 0 ? null : (row.value - previous) / Math.abs(previous) };
      }));
      setError("");
    }).catch(() => { if (alive) setError("Não foi possível consultar o cubo Gold de custos. Confirme se dest_dm_cost_visao_completa está materializado."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const latest = rows[rows.length - 1];
  const previous = rows[rows.length - 2];
  const maxValue = useMemo(() => Math.max(1, ...rows.map(row => row.value)), [rows]);

  return <Box>
    <Typography variant="h5" fontWeight={750} mb={0.5}>Evolução do dólar × consumo Azure</Typography>
    <Typography color="text.secondary" mb={2}>Realizado mensal em BRL e variação contra o mês anterior. Fonte: {TABLE}, filtrado para Azure.</Typography>
    <Alert severity="info" sx={{ mb: 2 }}>O cubo atual não disponibiliza uma taxa USD/BRL mensal observada. Assim, o realizado abaixo mostra o efeito combinado de câmbio, volume e composição do consumo; a variação não deve ser interpretada isoladamente como impacto cambial.</Alert>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    {loading ? <Box display="flex" justifyContent="center" p={5}><CircularProgress /></Box> : rows.length === 0 ? <Alert severity="warning">Não há realizado mensal Azure disponível no cubo selecionado.</Alert> : <>
      <Grid container spacing={2} mb={2}>
        <Grid item xs={12} md={4}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Realizado mais recente · {monthName(latest!.period)}</Typography><Typography variant="h5" fontWeight={750}>{brl.format(latest!.value)}</Typography></CardContent></Card></Grid>
        <Grid item xs={12} md={4}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Variação mensal</Typography><Typography variant="h5" fontWeight={750}>{latest!.deltaPct === null ? "—" : pct.format(latest!.deltaPct)}</Typography></CardContent></Card></Grid>
        <Grid item xs={12} md={4}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Diferença contra mês anterior</Typography><Typography variant="h5" fontWeight={750}>{latest!.delta === null || !previous ? "—" : brl.format(latest!.delta)}</Typography></CardContent></Card></Grid>
      </Grid>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={700} mb={2}>Consumo Azure realizado por mês</Typography>
        <Stack spacing={1}>{rows.slice(-18).map(row => <Stack key={row.period} direction="row" alignItems="center" gap={1.5}>
          <Typography variant="caption" sx={{ width: 92, flexShrink: 0 }}>{monthName(row.period)}</Typography>
          <Box sx={{ height: 18, width: `${Math.max(1, row.value / maxValue * 100)}%`, minWidth: 2, bgcolor: row.delta !== null && row.delta < 0 ? "success.main" : "primary.main", borderRadius: 1 }} />
          <Typography variant="caption" sx={{ whiteSpace: "nowrap", fontWeight: 650 }}>{brl.format(row.value)}</Typography>
        </Stack>)}</Stack>
      </Paper>
      <Paper variant="outlined" sx={{ overflow: "auto" }}><Table size="small"><TableHead><TableRow><TableCell>Mês</TableCell><TableCell align="right">Realizado Azure</TableCell><TableCell align="right">Δ vs. mês anterior</TableCell><TableCell align="right">Δ %</TableCell></TableRow></TableHead><TableBody>{rows.map(row => <TableRow key={row.period} hover><TableCell>{monthName(row.period)}</TableCell><TableCell align="right">{brl.format(row.value)}</TableCell><TableCell align="right">{row.delta === null ? "—" : brl.format(row.delta)}</TableCell><TableCell align="right">{row.deltaPct === null ? "—" : pct.format(row.deltaPct)}</TableCell></TableRow>)}</TableBody></Table></Paper>
    </>}
  </Box>;
}
