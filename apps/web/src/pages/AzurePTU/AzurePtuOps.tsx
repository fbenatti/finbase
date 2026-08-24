import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Plot from "react-plotly.js";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, FormControl, Grid,
  InputLabel, MenuItem, Select, Stack, Tab, Tabs, Table, TableBody, TableCell, TableHead,
  TableRow, Typography, Button,
} from "@mui/material";
import { getAzurePtuOpsOverview } from "../../services/api";

const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });

const TAB_KEYS = ["overview", "projects", "deployments", "recommendations"] as const;
const TAB_LABELS = ["Visão operacional", "Projetos", "Deployments", "Eficiência"];
const PERIODS = [
  { id: "7d", label: "Últimos 7 dias", hours: 7 * 24, grain: "hour" },
  { id: "30d", label: "Últimos 30 dias", hours: 30 * 24, grain: "day" },
  { id: "90d", label: "Últimos 90 dias", hours: 90 * 24, grain: "day" },
] as const;
const GRAIN_LABELS: Record<string, string> = { hour: "Hora", day: "Dia", month: "Mês" };
const PROJECT_LABELS: Record<string, string> = { todos: "Todos", "vivo-mais": "Vivo Mais", "aura-brain": "Aura Brain" };
const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const UTIL_HEATMAP_SCALE: [number, string][] = [
  [0, "#E8F5E9"],
  [0.35, "#81C784"],
  [0.7, "#2E7D32"],
  [1, "#1B5E20"],
];

const bandColor = (band?: string | null) => {
  if (band === "0-30") return "warning";
  if (band === "80-95" || band === "95-100") return "error";
  return "success";
};

const lineTrace = (trace: Record<string, unknown>) => ({ ...trace, connectgaps: false });
  autosize: true,
  margin: { l: 60, r: 24, t: 24, b: 60 },
  hovermode: "x unified",
  xaxis: { title: { text: "Período" } },
  yaxis: { title: { text: yTitle }, rangemode: "tozero", ticksuffix: "%" },
  legend: { orientation: "h", y: -0.2 },
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  shapes: [
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 30, y1: 30, line: { color: "#F59E0B", dash: "dot", width: 1 } },
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 80, y1: 80, line: { color: "#EF4444", dash: "dot", width: 1 } },
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 95, y1: 95, line: { color: "#B91C1C", dash: "dash", width: 1 } },
  ],
});

export default function AzurePtuOps() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [project, setProject] = useState("todos");
  const [period, setPeriod] = useState<(typeof PERIODS)[number]["id"]>("30d");
  const [grain, setGrain] = useState("day");

  const selectedPeriod = PERIODS.find(p => p.id === period) || PERIODS[1];
  const effectiveGrain = grain === "hour" && selectedPeriod.hours > 7 * 24 ? "day" : grain;
  const filters = useMemo(() => ({
    grain: effectiveGrain,
    hours: selectedPeriod.hours,
    project_keys: project === "todos" ? [] : [project],
  }), [effectiveGrain, project, selectedPeriod.hours]);

  useEffect(() => {
    if (grain === "hour" && selectedPeriod.hours > 7 * 24) {
      setGrain("day");
    }
  }, [grain, selectedPeriod.hours]);

  const filtersKey = useMemo(() => JSON.stringify(filters), [filters]);
  const overviewAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      overviewAbortRef.current?.abort();
      const controller = new AbortController();
      overviewAbortRef.current = controller;
      setError(null);
      getAzurePtuOpsOverview(filters, controller.signal)
        .then(payload => {
          if (!controller.signal.aborted) setData(payload);
        })
        .catch(e => {
          if ((e as { code?: string }).code === "ERR_CANCELED") return;
          setError(e?.response?.data?.detail || "Falha ao consultar métricas PTU");
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      overviewAbortRef.current?.abort();
    };
  }, [filtersKey, filters]);

  const utilizationSeries = data?.utilization_series || [];
  const tokenSeries = data?.token_series || [];
  const latencySeries = data?.latency_series || [];
  const rateLimitSeries = data?.rate_limit_series || [];
  const heatmapCells = data?.utilization_heatmap || [];
  const deploymentStats = data?.deployment_utilization || [];
  const imbalance = data?.deployment_imbalance || [];
  const recommendations = data?.recommendations || [];
  const coverage = data?.data_coverage;

  const heatmapMatrix = useMemo(() => {
    const matrix = Array.from({ length: 7 }, () => Array(24).fill(null as number | null));
    for (const cell of heatmapCells) {
      if (cell.weekday >= 0 && cell.weekday < 7 && cell.hour >= 0 && cell.hour < 24) {
        matrix[cell.weekday][cell.hour] = cell.utilization_avg;
      }
    }
    return matrix;
  }, [heatmapCells]);

  if (error && !data) return <Box p={3}><Alert severity="error">{error}</Alert></Box>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;

  const k = data.kpis || {};
  const projects = Object.values(data.projects || {}) as any[];

  const kpi = (label: string, value: string, hint?: string) => (
    <Grid item xs={12} sm={6} md={3} key={label}>
      <Card><CardContent>
        <Typography color="text.secondary" variant="body2">{label}</Typography>
        <Typography variant="h6">{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
      </CardContent></Card>
    </Grid>
  );

  const dataSourceLabel = data.data_source === "gold"
    ? "Gold Iceberg"
    : data.data_source === "stale"
      ? "Gold (parcial)"
      : "Azure Monitor live";

  return (
    <Box p={3}>
      <Typography variant="h4" gutterBottom>Azure PTU — Monitoramento</Typography>
      <Typography color="text.secondary" gutterBottom>
        Métricas operacionais do Azure Monitor (Gold: dest_azure_ptu_metrics / dest_azure_ptu_capacity).
        Sem integração FOCUS — apenas capacidade, utilização, tokens, latência e throttling.
      </Typography>
      <Stack direction="row" gap={2} mb={2} flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel>Projeto</InputLabel>
          <Select label="Projeto" value={project} onChange={e => setProject(e.target.value)}>
            <MenuItem value="todos">Todos</MenuItem>
            <MenuItem value="vivo-mais">Vivo Mais</MenuItem>
            <MenuItem value="aura-brain">Aura Brain</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Período</InputLabel>
          <Select
            label="Período"
            value={period}
            onChange={e => {
              const next = PERIODS.find(p => p.id === e.target.value);
              setPeriod(e.target.value as typeof period);
              if (next) setGrain(next.grain);
            }}
          >
            {PERIODS.map(p => <MenuItem key={p.id} value={p.id}>{p.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>Granularidade</InputLabel>
          <Select label="Granularidade" value={grain} onChange={e => setGrain(e.target.value)}>
            <MenuItem value="hour">Hora</MenuItem>
            <MenuItem value="day">Dia</MenuItem>
            <MenuItem value="month">Mês</MenuItem>
          </Select>
        </FormControl>
        <Chip size="small" label={`Fonte: ${dataSourceLabel}`} />
        <Button component={RouterLink} to="/azure-ptu" size="small" variant="outlined">
          Dashboard PTU + FOCUS
        </Button>
      </Stack>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        {TAB_LABELS.map(label => <Tab key={label} label={label} />)}
      </Tabs>

      {error && (
        <Alert severity="warning" sx={{ mb: 2 }}>{error} — exibindo último recorte disponível.</Alert>
      )}

      {tab === 0 && (
        <Stack gap={3}>
          <Grid container spacing={2}>
            {kpi("PTUs totais", String(k.total_ptus ?? "—"), "Inventário Gold capacity")}
            {kpi("Reservados / OnDemand", `${num.format(k.reserved_ptus || 0)} / ${num.format(k.ondemand_ptus || 0)}`)}
            {kpi("Deployments", String(k.deployment_count ?? "—"))}
            {kpi("Utilização média", `${num.format(k.utilization_avg || 0)}%`, "ProvisionedManagedUtilizationV2")}
            {kpi("Utilização P95", k.utilization_p95 != null ? `${num.format(k.utilization_p95)}%` : "—")}
            {kpi("Pico de utilização", k.peak_utilization != null ? `${num.format(k.peak_utilization)}%` : "—")}
            {kpi("Capacidade ociosa", pct.format(k.unused_capacity_pct || 0))}
            {kpi("Input / Output tokens", `${num.format(k.input_tokens || 0)} / ${num.format(k.output_tokens || 0)}`)}
            {kpi("TPM in / out (obs.)", `${k.input_tpm != null ? num.format(k.input_tpm) : "—"} / ${k.output_tpm != null ? num.format(k.output_tpm) : "—"}`, "tokens ÷ minutos no período")}
            {kpi("Latência TTR / TTLB", `${k.time_to_response_avg_ms != null ? `${num.format(k.time_to_response_avg_ms)} ms` : "—"} / ${k.time_to_last_byte_avg_ms != null ? `${num.format(k.time_to_last_byte_avg_ms)} ms` : "—"}`)}
            {kpi("Pico RPM", k.request_peak_per_minute != null ? num.format(k.request_peak_per_minute) : "—")}
            {kpi("429 / rate limit", num.format(k.rate_limited_count || 0), "Soma no período")}
          </Grid>

          {imbalance.length > 0 && (
            <Alert severity="warning">
              <Typography fontWeight={700} gutterBottom>Desbalanceamento entre deployments</Typography>
              {imbalance.map((item: any) => (
                <Typography key={item.project_key} variant="body2">
                  {item.project_key}: spread {num.format(item.utilization_spread)} p.p. ({item.ptus_split})
                </Typography>
              ))}
            </Alert>
          )}

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Série de utilização (média / P95 / pico)</Typography>
              {utilizationSeries.length > 0 ? (
                <Plot
                  data={[
                    { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_avg), name: "Média", type: "scatter", mode: "lines", line: { color: "#2563EB" } },
                    { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_p95), name: "P95", type: "scatter", mode: "lines", line: { color: "#7C3AED", dash: "dot" } },
                    { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_max), name: "Pico", type: "scatter", mode: "lines", line: { color: "#DC2626", dash: "dash" } },
                  ]}
                  layout={chartLayout("Utilização (%)")}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 320 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Sem série temporal — aguarde coleta Gold ou conexão Azure Monitor.</Alert>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Tokens de entrada e saída</Typography>
              {tokenSeries.length > 0 ? (
                <Plot
                  data={[
                    { x: tokenSeries.map((r: any) => r.timestamp), y: tokenSeries.map((r: any) => r.input_tokens), name: "Input", type: "scatter", mode: "lines", line: { color: "#2563EB" } },
                    { x: tokenSeries.map((r: any) => r.timestamp), y: tokenSeries.map((r: any) => r.output_tokens), name: "Output", type: "scatter", mode: "lines", line: { color: "#059669" } },
                    { x: tokenSeries.map((r: any) => r.timestamp), y: tokenSeries.map((r: any) => r.total_tokens), name: "Total", type: "scatter", mode: "lines", line: { color: "#7C3AED", dash: "dot" } },
                  ]}
                  layout={{
                    autosize: true,
                    margin: { l: 60, r: 24, t: 24, b: 60 },
                    hovermode: "x unified",
                    xaxis: { title: { text: "Período" } },
                    yaxis: { title: { text: "Tokens" }, rangemode: "tozero" },
                    legend: { orientation: "h", y: -0.2 },
                    paper_bgcolor: "transparent",
                    plot_bgcolor: "transparent",
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 320 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Sem série de tokens no período.</Alert>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Latência (TTR / TTLB)</Typography>
              {latencySeries.length > 0 ? (
                <Plot
                  data={[
                    { x: latencySeries.map((r: any) => r.timestamp), y: latencySeries.map((r: any) => r.time_to_response_avg_ms), name: "Time to response", type: "scatter", mode: "lines", line: { color: "#2563EB" } },
                    { x: latencySeries.map((r: any) => r.timestamp), y: latencySeries.map((r: any) => r.time_to_last_byte_avg_ms), name: "Time to last byte", type: "scatter", mode: "lines", line: { color: "#059669" } },
                  ]}
                  layout={{
                    autosize: true,
                    margin: { l: 60, r: 24, t: 24, b: 60 },
                    hovermode: "x unified",
                    xaxis: { title: { text: "Período" } },
                    yaxis: { title: { text: "ms" }, rangemode: "tozero" },
                    legend: { orientation: "h", y: -0.2 },
                    paper_bgcolor: "transparent",
                    plot_bgcolor: "transparent",
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 320 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Sem dados de latência no período.</Alert>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>429 / rate limit</Typography>
              {rateLimitSeries.length > 0 ? (
                <Plot
                  data={[{
                    x: rateLimitSeries.map((r: any) => r.timestamp),
                    y: rateLimitSeries.map((r: any) => r.rate_limited_count),
                    name: "429",
                    type: "bar",
                    marker: { color: "#DC2626" },
                  }]}
                  layout={{
                    autosize: true,
                    margin: { l: 60, r: 24, t: 24, b: 60 },
                    hovermode: "x unified",
                    xaxis: { title: { text: "Período" } },
                    yaxis: { title: { text: "Requisições 429" }, rangemode: "tozero" },
                    paper_bgcolor: "transparent",
                    plot_bgcolor: "transparent",
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 260 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Sem eventos 429 no período.</Alert>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Heatmap utilização (dia × hora)</Typography>
              {heatmapCells.length > 0 ? (
                <Plot
                  data={[{
                    z: heatmapMatrix,
                    x: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}h`),
                    y: WEEKDAYS,
                    type: "heatmap",
                    colorscale: UTIL_HEATMAP_SCALE,
                    zmin: 0,
                    zmax: 100,
                    hovertemplate: "%{y} %{x}: %{z:.1f}%<extra></extra>",
                  }]}
                  layout={{ autosize: true, margin: { l: 60, r: 24, t: 20, b: 40 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 280 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Heatmap indisponível sem métricas horárias.</Alert>
              )}
            </CardContent>
          </Card>
        </Stack>
      )}

      {tab === 1 && (
        <Grid container spacing={2}>
          {projects.map((p: any) => (
            <Grid item xs={12} md={6} key={p.project_key}>
              <Card>
                <CardContent>
                  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
                    <Typography variant="h6">{PROJECT_LABELS[p.project_key] || p.project_key}</Typography>
                    <Chip size="small" label={`${p.ptus || 0} PTUs`} />
                  </Stack>
                  <Typography color="text.secondary" gutterBottom>
                    {p.deployments || 0} deployments · {num.format(p.tokens || 0)} tokens · {p.pricing_label || ""}
                  </Typography>
                  <Typography>Média: {num.format(p.utilization_avg || 0)}% · P95: {p.utilization_p95 != null ? `${num.format(p.utilization_p95)}%` : "—"}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Ociosa: {pct.format(p.unused_capacity_pct || 0)} · Faixa {p.utilization_band || "—"}
                  </Typography>
                  {(p.deployment_comparison || []).length > 0 && (
                    <Box mt={2}>
                      <Plot
                        data={[{
                          x: p.deployment_comparison.map((d: any) => d.deployment_name),
                          y: p.deployment_comparison.map((d: any) => d.utilization_avg || 0),
                          type: "bar",
                          marker: { color: "#2563EB" },
                          name: "Utilização média",
                        }]}
                        layout={{ ...chartLayout("Utilização (%)"), margin: { l: 50, r: 16, t: 10, b: 80 }, showlegend: false }}
                        config={{ displayModeBar: false, responsive: true }}
                        style={{ width: "100%", height: 220 }}
                        useResizeHandler
                      />
                    </Box>
                  )}
                </CardContent>
              </Card>
            </Grid>
          ))}
          {!projects.length && <Alert severity="info">Nenhum projeto no recorte atual.</Alert>}
        </Grid>
      )}

      {tab === 2 && (
        <Stack gap={2}>
          {imbalance.length > 0 && (
            <Alert severity="warning">
              <Typography fontWeight={700} gutterBottom>Desbalanceamento entre deployments</Typography>
              {imbalance.map((item: any) => (
                <Typography key={item.project_key} variant="body2">
                  {item.project_key}: spread {num.format(item.utilization_spread)} p.p. ({item.ptus_split})
                </Typography>
              ))}
            </Alert>
          )}

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Comparação por deployment</Typography>
              <Table size="small">
                <TableHead><TableRow>
                  {["Projeto", "Deployment", "Modelo", "Região", "Tipo", "Preço", "PTUs", "TPM in", "TPM out", "Pico RPM", "Latência", "429", "Média", "P95", "Pico", "Ociosa %", "Faixa"].map(h => <TableCell key={h}>{h}</TableCell>)}
                </TableRow></TableHead>
                <TableBody>
                  {deploymentStats.map((d: any) => (
                    <TableRow key={d.deployment_name}>
                      <TableCell>{PROJECT_LABELS[d.project_key] || d.project_key || "não mapeado"}</TableCell>
                      <TableCell>{d.deployment_name}</TableCell>
                      <TableCell>{d.model_name || "—"}</TableCell>
                      <TableCell>{d.region || "—"}</TableCell>
                      <TableCell>{d.deployment_type || "—"}</TableCell>
                      <TableCell>{d.pricing_label || "—"}</TableCell>
                      <TableCell>{d.ptus ?? "—"}</TableCell>
                      <TableCell>{d.input_tpm != null ? num.format(d.input_tpm) : "—"}</TableCell>
                      <TableCell>{d.output_tpm != null ? num.format(d.output_tpm) : "—"}</TableCell>
                      <TableCell>{d.request_peak_per_minute != null ? num.format(d.request_peak_per_minute) : "—"}</TableCell>
                      <TableCell>{d.time_to_response_avg_ms != null ? `${num.format(d.time_to_response_avg_ms)} ms` : "—"}</TableCell>
                      <TableCell>{d.rate_limited_count != null ? num.format(d.rate_limited_count) : "—"}</TableCell>
                      <TableCell>{num.format(d.utilization_avg || 0)}%</TableCell>
                      <TableCell>{d.utilization_p95 != null ? `${num.format(d.utilization_p95)}%` : "—"}</TableCell>
                      <TableCell>{d.utilization_max != null ? `${num.format(d.utilization_max)}%` : "—"}</TableCell>
                      <TableCell>{pct.format(d.unused_capacity_pct || 0)}</TableCell>
                      <TableCell><Chip size="small" color={bandColor(d.utilization_band)} label={d.utilization_band || "—"} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </Stack>
      )}

      {tab === 3 && (
        <Stack gap={1}>
          {recommendations.map((rec: any) => (
            <Alert key={rec.code} severity={rec.severity === "high" ? "error" : "warning"}>
              <Typography fontWeight={700}>{rec.code}</Typography>
              <Typography variant="body2">Evidência: {rec.evidence}</Typography>
              <Typography variant="body2">Impacto: {rec.impact} · Confiança: {rec.confidence}</Typography>
              <Typography variant="body2">{rec.action}</Typography>
            </Alert>
          ))}
          {!recommendations.length && <Alert severity="info">Nenhuma recomendação no recorte atual.</Alert>}
        </Stack>
      )}
    </Box>
  );
}
