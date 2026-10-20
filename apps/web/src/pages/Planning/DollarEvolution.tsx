import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Card, CardContent, CircularProgress, Grid, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { queryDimonTable } from "../../services/api";

const TABLE = "dest_dm_cost_evolucao_dolar";
const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1, signDisplay: "always" });
const monthName = (month: string) => {
  const match = month.match(/^(\d{4})[-/]?(\d{2})/);
  if (!match) return month;
  return new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
};

type MonthRow = { period: string; usd: number; brl: number; rate: number | null; usdDelta: number | null; usdDeltaPct: number | null; rateDeltaPct: number | null; fxImpact: number | null; usageImpact: number | null };

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
      measures: ["consumo_usd", "realizado_brl", "taxa_brl_usd"].map(column => ({ column, aggregation: "SUM" as const })),
      row_limit: 120,
      sort_column: "billing_month",
      sort_direction: "asc",
    }).then(result => {
      if (!alive) return;
      const ordered = result.rows
        .map(row => ({ period: String(row.billing_month ?? ""), usd: Number(row.consumo_usd ?? 0), brl: Number(row.realizado_brl ?? 0), rate: Number(row.taxa_brl_usd ?? 0) || null }))
        .filter(row => /^\d{4}[-/]?\d{2}/.test(row.period))
        .sort((a, b) => a.period.localeCompare(b.period));
      setRows(ordered.map((row, index) => {
        const prior = ordered[index - 1];
        const usdChange = prior ? row.usd - prior.usd : null;
        const rateChange = prior && row.rate !== null && prior.rate !== null ? row.rate - prior.rate : null;
        const meanRate = prior && row.rate !== null && prior.rate !== null ? (row.rate + prior.rate) / 2 : null;
        const meanUsd = prior ? (row.usd + prior.usd) / 2 : null;
        return {
          ...row,
          usdDelta: usdChange,
          usdDeltaPct: prior && prior.usd !== 0 ? (row.usd - prior.usd) / Math.abs(prior.usd) : null,
          rateDeltaPct: prior?.rate && row.rate !== null ? (row.rate - prior.rate) / prior.rate : null,
          usageImpact: usdChange !== null && meanRate !== null ? usdChange * meanRate : null,
          fxImpact: rateChange !== null && meanUsd !== null ? rateChange * meanUsd : null,
        };
      }));
      setError("");
    }).catch((cause: unknown) => {
      if (!alive) return;
      const status = (cause as { response?: { status?: number } })?.response?.status;
      setError(status === 404
        ? `O cubo ${TABLE} ainda não foi materializado. Reprocesse os cubos Gold em Governança → Geração de Cubos.`
        : `Não foi possível consultar o cubo ${TABLE}${status ? ` (HTTP ${status})` : ""}. Verifique a disponibilidade da API Gold e do Trino.`);
    })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const latest = rows[rows.length - 1];
  const previous = rows[rows.length - 2];
  const maxValue = useMemo(() => Math.max(1, ...rows.map(row => row.usd)), [rows]);

  return <Box>
    <Typography variant="h5" fontWeight={750} mb={0.5}>Evolução do dólar × consumo Azure</Typography>
    <Typography color="text.secondary" mb={2}>Decomposição mensal do realizado Azure em efeito de consumo em USD e taxa BRL/USD. Fonte: cubo {TABLE}.</Typography>
    <Alert severity="info" sx={{ mb: 2 }}>A taxa é calculada como realizado Azure em BRL ÷ custo faturado Azure em USD no próprio mês (taxa efetiva ponderada). A decomposição usa valores médios entre meses, distribuindo a interação de forma simétrica.</Alert>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    {loading ? <Box display="flex" justifyContent="center" p={5}><CircularProgress /></Box> : rows.length === 0 ? <Alert severity="warning">Não há realizado mensal Azure disponível no cubo selecionado.</Alert> : <>
      <Grid container spacing={2} mb={2}>
        <Grid item xs={12} md={3}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Realizado · {monthName(latest!.period)}</Typography><Typography variant="h5" fontWeight={750}>{brl.format(latest!.brl)}</Typography></CardContent></Card></Grid>
        <Grid item xs={12} md={3}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Taxa efetiva BRL/USD</Typography><Typography variant="h5" fontWeight={750}>{latest!.rate === null ? "—" : latest!.rate.toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 4 })}</Typography></CardContent></Card></Grid>
        <Grid item xs={12} md={3}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Variação do consumo USD</Typography><Typography variant="h5" fontWeight={750}>{latest!.usdDeltaPct === null ? "—" : pct.format(latest!.usdDeltaPct)}</Typography></CardContent></Card></Grid>
        <Grid item xs={12} md={3}><Card variant="outlined"><CardContent><Typography color="text.secondary" variant="body2">Efeito cambial vs. mês anterior</Typography><Typography variant="h5" fontWeight={750}>{latest!.fxImpact === null ? "—" : brl.format(latest!.fxImpact)}</Typography></CardContent></Card></Grid>
      </Grid>
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={700} mb={2}>Consumo Azure faturado em USD por mês</Typography>
        <Stack spacing={1}>{rows.slice(-18).map(row => <Stack key={row.period} direction="row" alignItems="center" gap={1.5}>
          <Typography variant="caption" sx={{ width: 92, flexShrink: 0 }}>{monthName(row.period)}</Typography>
          <Box sx={{ height: 18, width: `${Math.max(1, row.usd / maxValue * 100)}%`, minWidth: 2, bgcolor: row.usdDelta !== null && row.usdDelta < 0 ? "success.main" : "primary.main", borderRadius: 1 }} />
          <Typography variant="caption" sx={{ whiteSpace: "nowrap", fontWeight: 650 }}>{new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(row.usd)}</Typography>
        </Stack>)}</Stack>
      </Paper>
      <Paper variant="outlined" sx={{ overflow: "auto" }}><Table size="small"><TableHead><TableRow><TableCell>Mês</TableCell><TableCell align="right">Consumo USD</TableCell><TableCell align="right">Taxa efetiva</TableCell><TableCell align="right">Realizado BRL</TableCell><TableCell align="right">Δ consumo %</TableCell><TableCell align="right">Δ taxa %</TableCell><TableCell align="right">Efeito consumo</TableCell><TableCell align="right">Efeito câmbio</TableCell></TableRow></TableHead><TableBody>{rows.map(row => <TableRow key={row.period} hover><TableCell>{monthName(row.period)}</TableCell><TableCell align="right">{new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(row.usd)}</TableCell><TableCell align="right">{row.rate === null ? "—" : row.rate.toLocaleString("pt-BR", { maximumFractionDigits: 4 })}</TableCell><TableCell align="right">{brl.format(row.brl)}</TableCell><TableCell align="right">{row.usdDeltaPct === null ? "—" : pct.format(row.usdDeltaPct)}</TableCell><TableCell align="right">{row.rateDeltaPct === null ? "—" : pct.format(row.rateDeltaPct)}</TableCell><TableCell align="right">{row.usageImpact === null ? "—" : brl.format(row.usageImpact)}</TableCell><TableCell align="right">{row.fxImpact === null ? "—" : brl.format(row.fxImpact)}</TableCell></TableRow>)}</TableBody></Table></Paper>
    </>}
  </Box>;
}
