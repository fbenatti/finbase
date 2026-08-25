import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Plot from "react-plotly.js";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, FormControl, Grid,
  InputLabel, MenuItem, Select, Stack, Tab, Tabs, Typography,
} from "@mui/material";
import { getAzurePtuOpsOverview } from "../../services/api";
import KpiCard from "./ops/KpiCard";
import RiskBand from "./ops/RiskBand";
import CoverageWarning from "./ops/CoverageWarning";
import DeploymentTable, { DeploymentRanking } from "./ops/DeploymentTable";

const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });
const int = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

const TAB_KEYS = ["executive", "tokens", "capacity", "recommendations"] as const;
const TAB_LABELS = ["Resumo executivo", "Consumo de tokens", "Capacidade e operação", "Recomendações"];
const PERIODS = [
  { id: "7d", label: "Últimos 7 dias", hours: 7 * 24, grain: "hour" },
  { id: "30d", label: "Últimos 30 dias", hours: 30 * 24, grain: "day" },
  { id: "90d", label: "Últimos 90 dias", hours: 90 * 24, grain: "day" },
] as const;
const PROJECT_LABELS: Record<string, string> = { "vivo-mais": "Vivo Mais", "aura-brain": "Aura Brain" };
const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const UTIL_HEATMAP_SCALE: [number, string][] = [
  [0, "#E8F5E9"], [0.35, "#81C784"], [0.7, "#2E7D32"], [1, "#1B5E20"],
];

const lineTrace = (trace: Record<string, unknown>) => ({ ...trace, connectgaps: false });

const chartLayout = (yTitle: string, opts?: { percent?: boolean }): Record<string, unknown> => ({
  autosize: true,
  margin: { l: 60, r: 24, t: 24, b: 60 },
  hovermode: "x unified",
  xaxis: { title: { text: "Período" } },
  yaxis: { title: { text: yTitle }, rangemode: "tozero", ...(opts?.percent ? { ticksuffix: "%" } : {}) },
  legend: { orientation: "h", y: -0.2 },
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  shapes: opts?.percent ? [
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 80, y1: 80, line: { color: "#EF4444", dash: "dot", width: 1 } },
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 95, y1: 95, line: { color: "#B91C1C", dash: "dash", width: 1 } },
  ] : [],
});

const metricStatus = (availability: Record<string, any> | undefined, field: string) => {
  const meta = availability?.[field];
  if (!meta) return { available: false, label: "métrica ainda não coletada" };
  if (!meta.available) return { available: false, label: meta.status === "unsupported" ? "métrica não suportada" : "métrica ainda não coletada" };
  return { available: true, label: `${meta.sample_count} amostras (${meta.coverage_pct}%)` };
};

export default function AzurePtuOps() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [project, setProject] = useState("todos");
  const [period, setPeriod] = useState<(typeof PERIODS)[number]["id"]>("30d");
  const [grain, setGrain] = useState("day");

  const selectedPeriod = PERIODS.find(p => p.id === period) || PERIODS[1];
  const hourBlocked = grain === "hour" && selectedPeriod.hours > 7 * 24;
  const effectiveGrain = hourBlocked ? "day" : grain;
  const filters = useMemo(() => ({
    grain: effectiveGrain,
    hours: selectedPeriod.hours,
    project_keys: project === "todos" ? [] : [project],
  }), [effectiveGrain, project, selectedPeriod.hours]);

  useEffect(() => {
    if (hourBlocked) setGrain("day");
  }, [hourBlocked]);

  const filtersKey = useMemo(() => JSON.stringify(filters), [filters]);
  const overviewAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      overviewAbortRef.current?.abort();
      const controller = new AbortController();
      overviewAbortRef.current = controller;
      setError(null);
      getAzurePtuOpsOverview(filters, controller.signal)
        .then(payload => { if (!controller.signal.aborted) setData(payload); })
        .catch(e => {
          if ((e as { code?: string }).code === "ERR_CANCELED") return;
          setError(e?.response?.data?.detail || "Falha ao consultar métricas PTU");
        });
    }, 350);
    return () => { window.clearTimeout(timer); overviewAbortRef.current?.abort(); };
  }, [filtersKey, filters]);

  const heatmapMatrix = useMemo(() => {
    const cells = data?.capacity_risk?.heatmap || data?.utilization_heatmap || [];
    const matrix = Array.from({ length: 7 }, () => Array(24).fill(null as number | null));
    for (const cell of cells) {
      if (cell.weekday >= 0 && cell.weekday < 7 && cell.hour >= 0 && cell.hour < 24) {
        matrix[cell.weekday][cell.hour] = cell.utilization_avg;
      }
    }
    return matrix;
  }, [data]);

  if (error && !data) return <Box p={3}><Alert severity="error">{error}</Alert></Box>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;

  const k = data.kpis || {};
  const exec = data.executive_summary || {};
  const cap = exec.capacity_decision || {};
  const demand = exec.ai_demand || {};
  const reliability = exec.reliability_experience || {};
  const comparison = exec.period_comparison || {};
  const tokenConsumption = data.token_consumption || {};
  const capacityRisk = data.capacity_risk || {};
  const serviceQuality = data.service_quality || {};
  const recommendations = data.recommendations || [];
  const metricAvailability = data.metric_availability || {};
  const utilizationSeries = capacityRisk.utilization_series || data.utilization_series || [];
  const tokenSeries = tokenConsumption.series || data.token_series || [];
  const latencySeries = serviceQuality.latency_series || data.latency_series || [];
  const rateLimitSeries = serviceQuality.rate_limit_series || data.rate_limit_series || [];
  const deploymentStats = capacityRisk.deployment_table || data.deployment_utilization || [];
  const periodInfo = data.period || {};

  const dataSourceLabel = data.data_source === "gold" ? "Gold Iceberg"
    : data.data_source === "stale" ? "Gold (parcial)" : "Azure Monitor live";

  const summaryLine = [
    `${int.format(k.total_ptus ?? 0)} PTUs`,
    `${k.deployment_count ?? 0} deployments`,
    k.utilization_avg != null ? `${num.format(k.utilization_avg)}% utilização (ponderada)` : null,
    demand.total_tokens != null ? `${int.format(demand.total_tokens)} tokens no período selecionado` : null,
  ].filter(Boolean).join(" · ");

  const renderBlock = (title: string, children: ReactNode) => (
    <Card><CardContent>
      <Typography variant="h6" gutterBottom>{title}</Typography>
      {children}
    </CardContent></Card>
  );

  return (
    <Box p={3}>
      <Typography variant="h4" gutterBottom>Azure PTU — Operações</Typography>
      <Typography color="text.secondary" gutterBottom>
        Métricas operacionais do Azure Monitor (Gold). Sem custos ou FOCUS — capacidade, tokens, latência e throttling.
      </Typography>

      <Stack direction="row" gap={2} mb={2} flexWrap="wrap" alignItems="center">
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
          <Select label="Período" value={period} onChange={e => {
            const next = PERIODS.find(p => p.id === e.target.value);
            setPeriod(e.target.value as typeof period);
            if (next) setGrain(next.grain);
          }}>
            {PERIODS.map(p => <MenuItem key={p.id} value={p.id}>{p.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel>Granularidade</InputLabel>
          <Select label="Granularidade" value={grain} onChange={e => setGrain(e.target.value)}>
            <MenuItem value="hour" disabled={selectedPeriod.hours > 7 * 24}>Hora</MenuItem>
            <MenuItem value="day">Dia</MenuItem>
            <MenuItem value="month">Mês</MenuItem>
          </Select>
        </FormControl>
        <Chip size="small" label={`Fonte: ${dataSourceLabel}`} />
        <Chip size="small" variant="outlined" label={`Granularidade efetiva: ${effectiveGrain}`} />
      </Stack>

      {selectedPeriod.hours > 7 * 24 && grain === "hour" && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Granularidade horária disponível apenas para períodos de até 7 dias. Usando agregação diária.
        </Alert>
      )}

      <Alert severity="info" sx={{ mb: 2 }} icon={false}>
        <Typography variant="body2">{summaryLine}</Typography>
        {periodInfo.start && (
          <Typography variant="caption" color="text.secondary" display="block">
            {periodInfo.start} → {periodInfo.end}
            {periodInfo.last_sample ? ` · última amostra: ${periodInfo.last_sample}` : ""}
          </Typography>
        )}
      </Alert>

      <CoverageWarning coverage={data.data_coverage} period={periodInfo} />
      {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        {TAB_LABELS.map(label => <Tab key={label} label={label} />)}
      </Tabs>

      {tab === 0 && (
        <Stack gap={3}>
          {exec.narrative && (
            <Alert severity="info"><Typography variant="body2">{exec.narrative}</Typography></Alert>
          )}

          <Typography variant="subtitle1" fontWeight={700}>Capacidade & decisão</Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="PTUs totais" value={String(k.total_ptus ?? "—")} hint="Inventário Gold" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard
                label="Utilização ponderada"
                value={cap.utilization_weighted_avg != null ? `${num.format(cap.utilization_weighted_avg)}%` : "—"}
                hint="Ponderada por PTU"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard
                label={`Pico (${cap.utilization_peak_label || k.utilization_peak_label || "—"})`}
                value={cap.utilization_peak != null ? `${num.format(cap.utilization_peak)}%` : "—"}
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="Capacidade ociosa" value={pct.format(cap.unused_capacity_pct ?? k.unused_capacity_pct ?? 0)} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="Horas ≥80%" value={String(cap.hours_at_or_above_80 ?? k.hours_at_or_above_80 ?? "—")} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="Horas ≥95%" value={String(cap.hours_at_or_above_95 ?? k.hours_at_or_above_95 ?? "—")} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Stack direction="row" alignItems="center" gap={1} height="100%">
                <Typography variant="body2" color="text.secondary">Risco:</Typography>
                <RiskBand band={cap.risk_level} label={cap.risk_level || "—"} />
              </Stack>
            </Grid>
          </Grid>

          <Typography variant="subtitle1" fontWeight={700}>Demanda de IA</Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={4}>
              <KpiCard label="Tokens no período selecionado" value={int.format(demand.total_tokens ?? k.total_tokens ?? 0)} />
            </Grid>
            <Grid item xs={12} sm={6} md={4}>
              <KpiCard label="Input / Output" value={`${int.format(demand.input_tokens ?? 0)} / ${int.format(demand.output_tokens ?? 0)}`} />
            </Grid>
            <Grid item xs={12} sm={6} md={4}>
              <KpiCard
                label="TPM in / out"
                value={`${demand.input_tpm != null ? num.format(demand.input_tpm) : "—"} / ${demand.output_tpm != null ? num.format(demand.output_tpm) : "—"}`}
              />
            </Grid>
          </Grid>

          <Typography variant="subtitle1" fontWeight={700}>Confiabilidade & experiência</Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={3}>
              {(() => {
                const avail = metricStatus(metricAvailability, "availability_rate_avg");
                return (
                  <KpiCard
                    label="Disponibilidade"
                    value={reliability.availability_rate_avg != null ? `${num.format(reliability.availability_rate_avg)}%` : "—"}
                    hint={avail.label}
                    unavailable={!avail.available}
                  />
                );
              })()}
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="Latência TTR" value={reliability.time_to_response_avg_ms != null ? `${num.format(reliability.time_to_response_avg_ms)} ms` : "—"} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="TTFT / TBT" value={`${reliability.normalized_ttft_avg_ms != null ? num.format(reliability.normalized_ttft_avg_ms) : "—"} / ${reliability.normalized_tbt_avg_ms != null ? num.format(reliability.normalized_tbt_avg_ms) : "—"} ms`} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <KpiCard label="429 / throttle" value={`${int.format(reliability.rate_limited_count ?? 0)} (${reliability.throttle_rate_pct ?? 0}%)`} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              {(() => {
                const avail = metricStatus(metricAvailability, "context_cache_match_rate_avg");
                return (
                  <KpiCard
                    label="Cache match rate"
                    value={reliability.context_cache_match_rate_avg != null ? `${num.format(reliability.context_cache_match_rate_avg)}%` : "—"}
                    hint={avail.label}
                    unavailable={!avail.available}
                  />
                );
              })()}
            </Grid>
          </Grid>

          {comparison.comparable && (
            <Alert severity="info">
              Comparado ao período anterior:
              {comparison.utilization_delta_pp != null && ` utilização ${comparison.utilization_delta_pp >= 0 ? "+" : ""}${num.format(comparison.utilization_delta_pp)} p.p.`}
              {comparison.tokens_delta_pct != null && ` · tokens ${comparison.tokens_delta_pct >= 0 ? "+" : ""}${comparison.tokens_delta_pct}%`}
            </Alert>
          )}
        </Stack>
      )}

      {tab === 1 && (
        <Stack gap={3}>
          <Typography variant="body2" color="text.secondary">{tokenConsumption.explanation}</Typography>
          {renderBlock("Série temporal de tokens", tokenSeries.length > 0 ? (
            <Plot
              data={[
                lineTrace({ x: tokenSeries.map((r: any) => r.timestamp), y: tokenSeries.map((r: any) => r.input_tokens), name: "Input", type: "scatter", mode: "lines", line: { color: "#2563EB" } }),
                lineTrace({ x: tokenSeries.map((r: any) => r.timestamp), y: tokenSeries.map((r: any) => r.output_tokens), name: "Output", type: "scatter", mode: "lines", line: { color: "#059669" } }),
              ]}
              layout={chartLayout("Tokens")}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: 320 }}
              useResizeHandler
            />
          ) : <Alert severity="info">sem dados no recorte</Alert>)}

          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              {renderBlock("Por projeto", (tokenConsumption.by_project || []).length > 0 ? (
                <Stack gap={1}>
                  {(tokenConsumption.by_project || []).map((p: any) => (
                    <Box key={p.project_key}>
                      <Typography variant="body2" fontWeight={600}>{PROJECT_LABELS[p.project_key] || p.project_key}</Typography>
                      <Typography variant="caption">In: {int.format(p.input)} · Out: {int.format(p.output)} · Total: {int.format(p.total)}</Typography>
                    </Box>
                  ))}
                </Stack>
              ) : <Typography color="text.secondary">sem dados no recorte</Typography>)}
            </Grid>
            <Grid item xs={12} md={6}>
              {renderBlock("Ranking por deployment", <DeploymentRanking deployments={tokenConsumption.deployment_rankings || deploymentStats} />)}
            </Grid>
          </Grid>

          {renderBlock("Comparação por modelo/versão", (tokenConsumption.by_model || []).length > 0 ? (
            <Plot
              data={[{
                x: (tokenConsumption.by_model || []).map((m: any) => `${m.model_name}@${m.model_version}`),
                y: (tokenConsumption.by_model || []).map((m: any) => m.total_tokens),
                type: "bar",
                marker: { color: "#7C3AED" },
              }]}
              layout={{ ...chartLayout("Tokens"), showlegend: false, margin: { l: 50, r: 16, t: 10, b: 100 } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: 280 }}
              useResizeHandler
            />
          ) : <Typography color="text.secondary">sem dados no recorte</Typography>)}
        </Stack>
      )}

      {tab === 2 && (
        <Stack gap={3}>
          {renderBlock("Série de utilização (média ponderada / pico)", utilizationSeries.length > 0 ? (
            <Plot
              data={[
                lineTrace({ x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_avg), name: "Média PTU-pond.", type: "scatter", mode: "lines", line: { color: "#2563EB" } }),
                lineTrace({ x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_max), name: "Pico", type: "scatter", mode: "lines", line: { color: "#DC2626", dash: "dash" } }),
              ]}
              layout={chartLayout("Utilização (%)", { percent: true })}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: 320 }}
              useResizeHandler
            />
          ) : <Alert severity="info">sem dados no recorte</Alert>)}

          {effectiveGrain === "hour" ? renderBlock("Heatmap (dia × hora)", (capacityRisk.heatmap || []).length > 0 ? (
            <Plot
              data={[{
                z: heatmapMatrix,
                x: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}h`),
                y: WEEKDAYS,
                type: "heatmap",
                colorscale: UTIL_HEATMAP_SCALE,
                zmin: 0, zmax: 100,
                hovertemplate: "%{y} %{x}: %{z:.1f}%<extra></extra>",
              }]}
              layout={{ autosize: true, margin: { l: 60, r: 24, t: 20, b: 40 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: 280 }}
              useResizeHandler
            />
          ) : <Alert severity="info">Heatmap disponível apenas com granularidade horária.</Alert>) : (
            <Alert severity="info">Heatmap disponível apenas com granularidade horária (período ≤7 dias).</Alert>
          )}

          {renderBlock("Deployments por risco", <DeploymentTable deployments={deploymentStats} />)}
        </Stack>
      )}

      {tab === 3 && (
        <Stack gap={1}>
          {recommendations.map((rec: any) => (
            <Alert key={`${rec.code}-${rec.evidence}`} severity={rec.severity === "high" ? "error" : "warning"}>
              <Typography fontWeight={700}>{rec.code}</Typography>
              <Typography variant="body2">Evidência: {rec.evidence}</Typography>
              <Typography variant="body2">Impacto: {rec.impact}</Typography>
              <Typography variant="body2">Ação: {rec.action}</Typography>
              {rec.affected_deployments?.length > 0 && (
                <Typography variant="caption" color="text.secondary">
                  Deployments: {rec.affected_deployments.join(", ")}
                </Typography>
              )}
            </Alert>
          ))}
          {!recommendations.length && <Alert severity="info">Nenhuma recomendação no recorte atual.</Alert>}
        </Stack>
      )}
    </Box>
  );
}
