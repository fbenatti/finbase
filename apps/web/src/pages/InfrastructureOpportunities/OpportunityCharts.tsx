/**
 * Gráficos de/para da oportunidade: o consumo observado na janela do motor,
 * projetado sobre o tamanho recomendado. A projeção é linear (mesmo trabalho em
 * menos capacidade): utilização_após = utilização_hoje × atual / recomendado.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, CircularProgress, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import Plot from "@/components/Plot";
import type { FlexConfig, OpportunitySeries } from "../../services/infraOpportunitiesApi";
import { getOpportunitySeries } from "../../services/infraOpportunitiesApi";

type Metric = "cpu" | "memory";
type Mode = "capacity" | "utilization";

const COLORS = { now: "#0F62FE", after: "#149683", peak: "#ed8a3b", current: "#8497A8", target: "#149683", limit: "#d64c4c", grid: "#e8edf3" };
const HOUR_LABELS = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}h`);

const localStamp = (utc: string) => {
  const date = new Date(`${utc}Z`);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
const share = (values: number[], threshold: number) =>
  values.length ? values.filter((value) => value > threshold).length / values.length : 0;
const duration = (fraction: number, windowHours: number) => {
  const hours = fraction * windowHours;
  return hours < 1 ? `${Math.round(hours * 60)} min` : `${hours.toFixed(1)} h`;
};
const quantile = (values: number[], q: number) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
};

type Props = {
  opportunityId: number;
  from: FlexConfig | null;
  to: FlexConfig | null;
  targets: { cpu: number; memory: number };
};

export default function OpportunityCharts({ opportunityId, from, to, targets }: Props) {
  const [resolution, setResolution] = useState(300);
  const [metric, setMetric] = useState<Metric>("cpu");
  const [mode, setMode] = useState<Mode>("capacity");
  const [series, setSeries] = useState<OpportunitySeries | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setSeries(null);
    setError(null);
    getOpportunitySeries(opportunityId, resolution)
      .then((data) => { if (active) setSeries(data); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Não foi possível ler a série"); });
    return () => { active = false; };
  }, [opportunityId, resolution]);

  const view = useMemo(() => {
    if (!series || !from || !to) return null;
    const capacityNow = metric === "cpu" ? from.vcpus : from.memory_gb;
    const capacityAfter = metric === "cpu" ? to.vcpus : to.memory_gb;
    const unit = metric === "cpu" ? "vCPU" : "GB";
    const ratio = capacityNow / capacityAfter;
    const target = metric === "cpu" ? targets.cpu : targets.memory;
    const points = series.points.filter((point) => point[`${metric}_avg`] != null);
    const x = points.map((point) => localStamp(point.t));
    const avgNow = points.map((point) => point[`${metric}_avg`] as number);
    const maxNow = points.map((point) => (point[`${metric}_max`] ?? point[`${metric}_avg`]) as number);
    const avgAfter = avgNow.map((value) => value * ratio);
    const maxAfter = maxNow.map((value) => value * ratio);
    const windowHours = points.length * series.resolution_seconds / 3600;
    const heat: (number | null)[][] = [];
    const days = [...new Set(x.map((stamp) => stamp.slice(0, 10)))];
    const cells = new Map<string, number[]>();
    points.forEach((_, index) => {
      const key = `${x[index].slice(0, 10)}|${Number(x[index].slice(11, 13))}`;
      cells.set(key, [...(cells.get(key) ?? []), maxAfter[index]]);
    });
    days.forEach((day) => heat.push(HOUR_LABELS.map((_, hour) => {
      const values = cells.get(`${day}|${hour}`);
      return values ? Math.max(...values) : null;
    })));
    return {
      capacityNow, capacityAfter, unit, ratio, target, x, avgNow, maxNow, avgAfter, maxAfter, windowHours, days, heat,
      stats: {
        p95Now: quantile(avgNow, 0.95), p95After: quantile(avgAfter, 0.95),
        p99Now: quantile(avgNow, 0.99), p99After: quantile(avgAfter, 0.99),
        peakNow: Math.max(...maxNow), peakAfter: Math.max(...maxAfter),
        overTargetNow: share(avgNow, target * 100), overTargetAfter: share(avgAfter, target * 100),
        saturatedAfter: share(maxAfter, 100),
      },
    };
  }, [series, metric, from, to, targets]);

  const timeChart = useMemo(() => {
    if (!view) return null;
    const scale = (values: number[], capacity: number) => values.map((value) => value / 100 * capacity);
    const lines = (values: [number, string, string, string][]) => values.map(([y, color, dash, label]) => ({
      type: "line" as const, xref: "paper" as const, yref: "y" as const, x0: 0, x1: 1, y0: y, y1: y, line: { color, width: 1.5, dash },
      label: { text: label, textposition: "end" as const, font: { size: 10, color } },
    }));
    if (mode === "capacity") {
      const used = scale(view.avgNow, view.capacityNow);
      const peak = scale(view.maxNow, view.capacityNow);
      return {
        data: [
          { type: "scatter", mode: "lines", name: `Pico do bucket (${view.unit})`, x: view.x, y: peak, line: { color: COLORS.peak, width: 1 }, opacity: 0.7, hovertemplate: `Pico: %{y:.2f} ${view.unit}<extra></extra>` },
          { type: "scatter", mode: "lines", name: `Consumo médio (${view.unit})`, x: view.x, y: used, fill: "tozeroy", fillcolor: "rgba(15,98,254,0.12)", line: { color: COLORS.now, width: 1.5 }, hovertemplate: `Consumo: %{y:.2f} ${view.unit}<extra></extra>` },
        ],
        layout: {
          yaxis: { title: { text: view.unit }, rangemode: "tozero" as const, range: [0, view.capacityNow * 1.12], gridcolor: COLORS.grid },
          shapes: lines([
            [view.capacityNow, COLORS.current, "dash", `Hoje: ${view.capacityNow} ${view.unit}`],
            [view.capacityAfter, COLORS.target, "solid", `Recomendado: ${view.capacityAfter} ${view.unit}`],
            [view.capacityAfter * view.target, COLORS.target, "dot", `Alvo P95 (${Math.round(view.target * 100)}% do recomendado)`],
          ]),
        },
      };
    }
    return {
      data: [
        { type: "scatter", mode: "lines", name: "Pico após a mudança", x: view.x, y: view.maxAfter, line: { color: COLORS.peak, width: 1 }, opacity: 0.6, hovertemplate: "Pico após: %{y:.1f}%<extra></extra>" },
        { type: "scatter", mode: "lines", name: `Hoje (% de ${view.capacityNow} ${view.unit})`, x: view.x, y: view.avgNow, line: { color: COLORS.now, width: 1.5 }, hovertemplate: "Hoje: %{y:.1f}%<extra></extra>" },
        { type: "scatter", mode: "lines", name: `Após (% de ${view.capacityAfter} ${view.unit})`, x: view.x, y: view.avgAfter, line: { color: COLORS.after, width: 1.5 }, hovertemplate: "Após: %{y:.1f}%<extra></extra>" },
      ],
      layout: {
        yaxis: { title: { text: "Utilização (%)" }, ticksuffix: "%", rangemode: "tozero" as const, gridcolor: COLORS.grid },
        shapes: lines([
          [100, COLORS.limit, "dash", "100% da capacidade"],
          [view.target * 100, COLORS.target, "dot", `Alvo P95 ${Math.round(view.target * 100)}%`],
        ]),
      },
    };
  }, [view, mode]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!series || !view || !timeChart) return <Box sx={{ display: "grid", placeItems: "center", py: 6 }}><CircularProgress size={26} /></Box>;
  if (!view.x.length) return <Alert severity="info">Sem buckets na Silver para esta métrica na janela da análise.</Alert>;

  const sortedNow = [...view.avgNow].sort((a, b) => b - a);
  const sortedAfter = [...view.avgAfter].sort((a, b) => b - a);
  const percentOfTime = sortedNow.map((_, index) => (index + 1) / sortedNow.length * 100);
  const fmt = (value: number | null) => (value == null ? "—" : `${value.toFixed(1)}%`);
  const kpis = [
    { label: "P95", now: fmt(view.stats.p95Now), after: fmt(view.stats.p95After) },
    { label: "P99", now: fmt(view.stats.p99Now), after: fmt(view.stats.p99After) },
    { label: "Maior pico", now: fmt(view.stats.peakNow), after: fmt(view.stats.peakAfter) },
    { label: `Tempo acima de ${Math.round(view.target * 100)}%`, now: duration(view.stats.overTargetNow, view.windowHours), after: duration(view.stats.overTargetAfter, view.windowHours) },
    { label: "Tempo com pico ≥ 100%", now: "0 min", after: duration(view.stats.saturatedAfter, view.windowHours) },
  ];
  const commonLayout = {
    autosize: true, paper_bgcolor: "transparent", plot_bgcolor: "transparent", separators: ",.",
    font: { family: "inherit", color: "#526777", size: 11 }, hovermode: "x unified" as const,
    legend: { orientation: "h" as const, y: -0.2, x: 0, font: { size: 11 } },
  };

  return <Stack spacing={1.5}>
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
      <ToggleButtonGroup size="small" exclusive value={metric} onChange={(_, next) => next && setMetric(next)}>
        <ToggleButton value="cpu">CPU</ToggleButton>
        <ToggleButton value="memory">Memória</ToggleButton>
      </ToggleButtonGroup>
      <ToggleButtonGroup size="small" exclusive value={mode} onChange={(_, next) => next && setMode(next)}>
        <ToggleButton value="capacity">Capacidade ({view.unit})</ToggleButton>
        <ToggleButton value="utilization">Utilização (%)</ToggleButton>
      </ToggleButtonGroup>
      <ToggleButtonGroup size="small" exclusive value={resolution} onChange={(_, next) => next && setResolution(next)}>
        <ToggleButton value={300}>5 min</ToggleButton>
        <ToggleButton value={60}>1 min</ToggleButton>
      </ToggleButtonGroup>
      <Typography variant="caption" color="text.secondary">
        {view.x.length.toLocaleString("pt-BR")} buckets · {view.days.length} dia(s) · horário local
      </Typography>
    </Stack>

    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0,1fr))", md: "repeat(5, minmax(0,1fr))" }, gap: 1 }}>
      {kpis.map((item) => <Paper key={item.label} variant="outlined" sx={{ p: 1 }}>
        <Typography variant="caption" color="text.secondary">{item.label}</Typography>
        <Typography variant="body2"><Box component="span" sx={{ color: COLORS.now, fontWeight: 650 }}>{item.now}</Box> → <Box component="span" sx={{ color: COLORS.after, fontWeight: 700 }}>{item.after}</Box></Typography>
      </Paper>)}
    </Box>

    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Typography variant="subtitle2">Consumo no tempo — hoje × recomendado</Typography>
      <Typography variant="caption" color="text.secondary">
        {mode === "capacity"
          ? `Consumo convertido em ${view.unit} contra a capacidade de hoje e a recomendada. Tudo que fica abaixo da linha verde cabe no novo tamanho.`
          : `Mesma demanda expressa em % do tamanho atual e do recomendado (× ${view.ratio.toFixed(2)}).`}
      </Typography>
      <Plot data={timeChart.data as never} layout={{
        ...commonLayout, height: 340, margin: { l: 56, r: 150, t: 12, b: 70 },
        xaxis: { type: "date", tickformat: "%d/%m %Hh", hoverformat: "%d/%m %H:%M", showgrid: false, rangeslider: { visible: true, thickness: 0.07 } },
        ...timeChart.layout,
      } as never} />
    </Paper>

    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, gap: 1.5 }}>
      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Typography variant="subtitle2">Curva de permanência</Typography>
        <Typography variant="caption" color="text.secondary">Percentual do tempo em que a utilização ficou acima de cada nível.</Typography>
        <Plot data={[
          { type: "scatter", mode: "lines", name: "Hoje", x: percentOfTime, y: sortedNow, line: { color: COLORS.now, width: 2 }, hovertemplate: "%{x:.1f}% do tempo acima de %{y:.1f}%<extra>Hoje</extra>" },
          { type: "scatter", mode: "lines", name: "Após", x: percentOfTime, y: sortedAfter, line: { color: COLORS.after, width: 2 }, hovertemplate: "%{x:.1f}% do tempo acima de %{y:.1f}%<extra>Após</extra>" },
        ] as never} layout={{
          ...commonLayout, height: 280, hovermode: "closest", margin: { l: 48, r: 12, t: 10, b: 60 },
          xaxis: { title: { text: "% do tempo" }, ticksuffix: "%", range: [0, 100], gridcolor: COLORS.grid },
          yaxis: { ticksuffix: "%", rangemode: "tozero", gridcolor: COLORS.grid },
          shapes: [
            { type: "line", xref: "paper", x0: 0, x1: 1, y0: view.target * 100, y1: view.target * 100, line: { color: COLORS.target, dash: "dot", width: 1 } },
            { type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: COLORS.limit, dash: "dash", width: 1 } },
            { type: "line", yref: "paper", x0: 5, x1: 5, y0: 0, y1: 1, line: { color: "#A9BCCB", dash: "dot", width: 1 } },
          ],
          annotations: [{ x: 5, y: 1, yref: "paper", text: "P95", showarrow: false, yanchor: "bottom", font: { size: 10, color: "#8497A8" } }],
        }} />
      </Paper>
      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Typography variant="subtitle2">Picos após a mudança por dia e hora</Typography>
        <Typography variant="caption" color="text.secondary">Maior pico projetado em cada hora (% do tamanho recomendado). Vermelho = acima da capacidade.</Typography>
        <Plot data={[{
          type: "heatmap", x: HOUR_LABELS, y: view.days.map((day) => `${day.slice(8, 10)}/${day.slice(5, 7)}`), z: view.heat,
          zmin: 0, zmax: 120, colorscale: [[0, "#eef6f4"], [0.4, "#8fd3c4"], [view.target * 100 / 120, "#149683"], [100 / 120, "#f2b45c"], [1, "#d64c4c"]],
          hovertemplate: "%{y} %{x}: pico %{z:.0f}%<extra></extra>", colorbar: { ticksuffix: "%", thickness: 10 },
        }] as never} layout={{
          ...commonLayout, height: 280, hovermode: "closest", margin: { l: 48, r: 10, t: 10, b: 40 },
          xaxis: { tickangle: 0, dtick: 3 }, yaxis: { autorange: "reversed" },
        }} />
      </Paper>
    </Box>
  </Stack>;
}
