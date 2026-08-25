import { useCallback, useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import {
  Alert, Box, Chip, CircularProgress, FormControl, Grid, InputLabel, MenuItem,
  Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, Typography,
} from "@mui/material";
import {
  ForecastAccuracyMonth, ForecastAccuracyDimensions,
  getForecastAccuracyMonths, getForecastAccuracyDimensions,
} from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const money2 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const monthLabel = (month: string) => month.length === 6
  ? `${month.slice(0, 4)}-${month.slice(4, 6)}`
  : month;
const MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const prettyMonth = (month: string) => month.length === 6
  ? `${MONTH_NAMES[Number(month.slice(4, 6)) - 1]}/${month.slice(2, 4)}`
  : month;

function KpiCard({ label, value, hint, color }: { label: string; value: string; hint?: string; color?: string }) {
  return (
    <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider", height: "100%" }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ mt: 0.5, fontWeight: 600, color: color || "text.primary" }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </Paper>
  );
}

export default function ForecastAccuracy() {
  const [months, setMonths] = useState<ForecastAccuracyMonth[]>([]);
  const [summary, setSummary] = useState<ForecastAccuracyMonth | null>(null);
  const [dimensions, setDimensions] = useState<ForecastAccuracyDimensions | null>(null);
  const [selectedMonth, setSelectedMonth] = useState("");
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [selectedDim, setSelectedDim] = useState("cloud_origem");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // /accuracy/months já traz, para o snapshot mais recente de cada mês, todos os
  // KPIs (projetado, realizado, variação, acurácia, wape, bias, cutoff, run).
  // A tendência (history) e o resumo do mês selecionado são derivados dele —
  // nenhuma query Trino extra no carregamento.
  useEffect(() => {
    let cancelled = false;
    getForecastAccuracyMonths()
      .then((monthsList) => {
        if (cancelled) return;
        setMonths(monthsList);
        const first = monthsList[0];
        if (first && !selectedMonth) {
          setSelectedMonth(first.target_month);
          setSelectedRun(first.run_id);
          setSummary(first);
        }
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedRun == null) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    const monthEntry = months.find(item => item.target_month === selectedMonth);
    if (monthEntry) setSummary(monthEntry);
    getForecastAccuracyDimensions(selectedRun, selectedMonth || undefined, selectedDim)
      .then((dimsData) => {
        if (cancelled) return;
        setDimensions(dimsData);
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedRun, selectedMonth, selectedDim, months]);

  const handleMonthChange = (month: string) => {
    setSelectedMonth(month);
    const match = months.find(item => item.target_month === month);
    setSelectedRun(match ? match.run_id : null);
  };

  const chartData = useMemo(() => {
    if (!dimensions) return [];
    const records = dimensions.records.slice(0, 15);
    return [
      {
        type: "bar" as const,
        name: "Projetado",
        x: records.map(item => item.dimension_value),
        y: records.map(item => Number(item.forecast_value || 0)),
        marker: { color: "#2563eb" },
      },
      {
        type: "bar" as const,
        name: "Realizado",
        x: records.map(item => item.dimension_value),
        y: records.map(item => Number(item.actual_value || 0)),
        marker: { color: "#16A34A" },
      },
    ];
  }, [dimensions]);

  const chartLayout = useMemo(() => ({
    autosize: true,
    margin: { l: 70, r: 20, t: 30, b: 110 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    barmode: "group" as const,
    xaxis: { title: { text: dimensions?.dimension || "Dimensão" }, tickangle: -35 },
    yaxis: { title: { text: "Valor (R$)" }, tickformat: ".2s" },
    showlegend: true,
    legend: { orientation: "h" as const, y: -0.28 },
  }), [dimensions]);

  const accuracyColor = (value: number | null | undefined) => {
    if (value == null) return "text.primary";
    if (value >= 0.9) return "#16A34A";
    if (value >= 0.7) return "#D97706";
    return "#DC2626";
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, mb: 2, flexWrap: "wrap" }}>
          <Box>
            <Typography variant="h4">Acurácia de Forecast</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              O que foi projetado no início do mês × o que realmente foi realizado — snapshot imutável no Iceberg por mês simulado.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel id="accuracy-month-label">Mês projetado</InputLabel>
              <Select
                labelId="accuracy-month-label"
                label="Mês projetado"
                value={selectedMonth}
                onChange={event => handleMonthChange(event.target.value)}
              >
                {months.map(item => (
                  <MenuItem key={item.run_id} value={item.target_month}>
                    {prettyMonth(item.target_month)} {item.closed ? "" : "(em curso)"}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 180 }}>
              <InputLabel id="accuracy-dim-label">Dimensão</InputLabel>
              <Select
                labelId="accuracy-dim-label"
                label="Dimensão"
                value={selectedDim}
                onChange={event => setSelectedDim(event.target.value)}
              >
                <MenuItem value="cloud_origem">cloud_origem</MenuItem>
                <MenuItem value="departamento_nivel_1">departamento_nivel_1</MenuItem>
                <MenuItem value="servicename">servicename</MenuItem>
                <MenuItem value="marketplaceproductname">marketplaceproductname</MenuItem>
                <MenuItem value="subaccountname">subaccountname</MenuItem>
              </Select>
            </FormControl>
          </Stack>
        </Box>
      </Box>

      <Box sx={{ p: 3 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        {loading && !summary && (
          <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}><CircularProgress /></Box>
        )}

        {summary && (
          <>
            <Stack direction="row" spacing={1} sx={{ mb: 2, alignItems: "center" }}>
              <Typography variant="h6">Snapshot do simulado</Typography>
              <Chip
                size="small"
                color={summary.closed ? "success" : "warning"}
                label={summary.closed ? "Mês fechado" : "Em acompanhamento"}
              />
              <Chip size="small" variant="outlined" label={`simulado em ${monthLabel(summary.simulated_as_of)}`} />
              <Chip size="small" variant="outlined" label={`cutoff ${monthLabel(summary.cutoff_date)}`} />
              <Chip size="small" variant="outlined" label={`run #${summary.run_id} · ${summary.result_table}`} />
            </Stack>

            <Grid container spacing={2} sx={{ mb: 2 }}>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Projetado (mês)" value={money.format(Number(summary.forecast_value || 0))} hint="estimativa do snapshot" /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Realizado" value={money.format(Number(summary.actual_value || 0))} hint={summary.closed ? "mês fechado" : "parcial até hoje"} /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Variação (R$)" value={money.format(Number(summary.variance || 0))} color={Number(summary.variance || 0) >= 0 ? "#16A34A" : "#DC2626"} /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Variação (%)" value={pct(summary.variance_pct)} color={Number(summary.variance_pct || 0) >= 0 ? "#16A34A" : "#DC2626"} /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Acurácia" value={pct(summary.accuracy)} color={accuracyColor(summary.accuracy)} hint="1 − WAPE" /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="WAPE" value={summary.wape == null ? "—" : money2.format(Number(summary.wape))} hint={`bias: ${pct(summary.bias)}`} /></Grid>
            </Grid>

            <Grid container spacing={2}>
              <Grid item xs={12} lg={7}>
                <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="h6" sx={{ mb: 1 }}>Projetado × Realizado por {dimensions?.dimension}</Typography>
                  <Box sx={{ height: 340 }}>
                    <Plot
                      data={chartData}
                      layout={chartLayout}
                      style={{ width: "100%", height: "100%" }}
                      useResizeHandler
                      config={{ displayModeBar: false, responsive: true }}
                    />
                  </Box>
                </Paper>
              </Grid>
              <Grid item xs={12} lg={5}>
                <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="h6" sx={{ mb: 1 }}>Evolução da acurácia</Typography>
                  {months.length > 0 ? (
                    <TableContainer>
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>Mês</TableCell>
                            <TableCell align="right">Projetado</TableCell>
                            <TableCell align="right">Realizado</TableCell>
                            <TableCell align="right">Acurácia</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {months.map(item => (
                            <TableRow key={item.target_month}>
                              <TableCell>{prettyMonth(item.target_month)}</TableCell>
                              <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
                              <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
                              <TableCell align="right" sx={{ color: accuracyColor(item.accuracy) }}>{pct(item.accuracy)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      Nenhum simulado disponível ainda. Rode `POST /forecast/runs?config_id=…&simulated_as_of=YYYY-MM-DD` para cada mês.
                    </Typography>
                  )}
                </Paper>
              </Grid>
            </Grid>

            <Paper elevation={0} sx={{ mt: 2, p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
              <Typography variant="h6" sx={{ mb: 1 }}>Detalhe por {dimensions?.dimension}</Typography>
              {dimensions && dimensions.records.length > 0 ? (
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{dimensions.dimension}</TableCell>
                        <TableCell align="right">Projetado</TableCell>
                        <TableCell align="right">Realizado</TableCell>
                        <TableCell align="right">Variação (R$)</TableCell>
                        <TableCell align="right">Variação (%)</TableCell>
                        <TableCell align="right">Acurácia</TableCell>
                        <TableCell align="right">WAPE</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {dimensions.records.map(item => (
                        <TableRow key={item.dimension_value} hover>
                          <TableCell>{item.dimension_value}</TableCell>
                          <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
                          <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
                          <TableCell align="right" sx={{ color: Number(item.variance || 0) >= 0 ? "#16A34A" : "#DC2626" }}>
                            {money.format(Number(item.variance || 0))}
                          </TableCell>
                          <TableCell align="right">{pct(item.variance_pct)}</TableCell>
                          <TableCell align="right" sx={{ color: accuracyColor(item.accuracy) }}>{pct(item.accuracy)}</TableCell>
                          <TableCell align="right">{item.wape == null ? "—" : money2.format(Number(item.wape))}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              ) : (
                <Typography variant="body2" color="text.secondary">Sem linhas para esta dimensão.</Typography>
              )}
            </Paper>
          </>
        )}

        {!loading && months.length === 0 && !error && (
          <Alert severity="info">
            Nenhum simulado de acurácia encontrado. Crie um com
            <Box component="code" sx={{ mx: 1 }}>POST /forecast/runs?config_id=&lt;id&gt;&amp;simulated_as_of=YYYY-MM-DD</Box>
            para cada mês que deseja avaliar.
          </Alert>
        )}
      </Box>
    </Box>
  );
}
