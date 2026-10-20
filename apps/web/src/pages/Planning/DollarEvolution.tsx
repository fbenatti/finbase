import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Card, CardContent, CircularProgress, Grid, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { queryDimonTable } from "../../services/api";

const TABLE = "dest_dm_cost_evolucao_dolar";
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 2, signDisplay: "always" });
const monthName = (month: string) => {
  const match = month.match(/^(\d{4})[-/]?(\d{2})/);
  if (!match) return month;
  return new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
};
const rateLabel = (rate: number) => rate.toLocaleString("pt-BR", { style: "currency", currency: "BRL", minimumFractionDigits: 4, maximumFractionDigits: 4 });
const compactMonth = (period: string) => {
  const match = period.match(/^(\d{4})[-/]?(\d{2})/);
  if (!match) return period;
  const month = new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString("pt-BR", { month: "short" }).replace(".", "");
  return `${month}/${match[1]}`;
};

type MonthRow = { period: string; rate: number; deltaPct: number | null };

export default function DollarEvolution() {
  const [rows, setRows] = useState<MonthRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    queryDimonTable({
      table: TABLE,
      rows: ["billing_month"],
      measures: [{ column: "taxa_brl_usd", aggregation: "SUM" }],
      row_limit: 120,
      sort_column: "billing_month",
      sort_direction: "asc",
    }).then(result => {
      if (!alive) return;
      const ordered = result.rows
        .map(row => ({ period: String(row.billing_month ?? ""), rate: Number(row.taxa_brl_usd ?? 0) }))
        .filter(row => /^\d{4}[-/]?\d{2}/.test(row.period) && Number.isFinite(row.rate) && row.rate > 0)
        .sort((a, b) => a.period.localeCompare(b.period));
      setRows(ordered.map((row, index) => {
        const prior = ordered[index - 1];
        return { ...row, deltaPct: prior && prior.rate !== 0 ? (row.rate - prior.rate) / prior.rate : null };
      }));
      setError("");
    }).catch((cause: unknown) => {
      if (!alive) return;
      const status = (cause as { response?: { status?: number } })?.response?.status;
      setError(status === 404
        ? `O cubo ${TABLE} ainda não foi materializado. Reprocesse os cubos Gold em Governança → Geração de Cubos.`
        : `Não foi possível consultar o cubo ${TABLE}${status ? ` (HTTP ${status})` : ""}. Verifique a disponibilidade da API Gold e do Trino.`);
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const periodAverages = useMemo(() => {
    if (!rows.length) return { rate: null as number | null, deltaPct: null as number | null };
    const monthlyChanges = rows.flatMap(row => row.deltaPct === null ? [] : [row.deltaPct]);
    return {
      rate: rows.reduce((sum, row) => sum + row.rate, 0) / rows.length,
      deltaPct: monthlyChanges.length ? monthlyChanges.reduce((sum, value) => sum + value, 0) / monthlyChanges.length : null,
    };
  }, [rows]);
  const periodRange = rows.length ? `${compactMonth(rows[0].period)} a ${compactMonth(rows[rows.length - 1].period)}` : "";
  const minRate = useMemo(() => Math.min(...rows.map(row => row.rate)), [rows]);
  const maxRate = useMemo(() => Math.max(...rows.map(row => row.rate)), [rows]);
  const span = Math.max(maxRate - minRate, 0.01);

  return <Box>
    <Typography variant="h5" fontWeight={750} mb={0.5}>Evolução do dólar</Typography>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    {loading ? <Box display="flex" justifyContent="center" p={5}><CircularProgress /></Box> : error ? null : rows.length === 0 ? <Alert severity="warning">O cubo está disponível, mas não retornou taxas mensais para Azure.</Alert> : <>
      <Grid container spacing={2} mb={2}>
        <Grid item xs={12} md={6}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Dólar médio · {periodRange}</Typography><Typography variant="h5" fontWeight={750}>{periodAverages.rate === null ? "—" : rateLabel(periodAverages.rate)}</Typography></CardContent></Card></Grid>
        <Grid item xs={12} md={6}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Variação mensal média · {periodRange}</Typography><Typography variant="h5" fontWeight={750}>{periodAverages.deltaPct === null ? "—" : pct.format(periodAverages.deltaPct)}</Typography></CardContent></Card></Grid>
      </Grid>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={700} mb={2}>Taxa mensal BRL/USD</Typography>
        <Stack spacing={1}>{rows.slice(-18).map(row => <Stack key={row.period} direction="row" alignItems="center" gap={1.5}>
          <Typography variant="caption" sx={{ width: 92, flexShrink: 0 }}>{monthName(row.period)}</Typography>
          <Box sx={{ height: 18, width: `${Math.max(2, (row.rate - minRate) / span * 100)}%`, bgcolor: "primary.main", borderRadius: 1 }} />
          <Typography variant="caption" sx={{ whiteSpace: "nowrap", fontWeight: 650 }}>{rateLabel(row.rate)}</Typography>
        </Stack>)}</Stack>
      </Paper>
      <Paper variant="outlined" sx={{ overflow: "auto" }}><Table size="small"><TableHead><TableRow><TableCell>Mês</TableCell><TableCell align="right">Dólar (BRL/USD)</TableCell><TableCell align="right">Variação mensal</TableCell></TableRow></TableHead><TableBody>{rows.map(row => <TableRow key={row.period} hover><TableCell>{monthName(row.period)}</TableCell><TableCell align="right">{rateLabel(row.rate)}</TableCell><TableCell align="right">{row.deltaPct === null ? "—" : pct.format(row.deltaPct)}</TableCell></TableRow>)}</TableBody></Table></Paper>
    </>}
  </Box>;
}
