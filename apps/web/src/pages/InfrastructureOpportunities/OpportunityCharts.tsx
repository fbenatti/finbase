/**
 * Gráficos de/para da oportunidade: o consumo observado na janela do motor,
 * projetado sobre o tamanho recomendado. A projeção é linear (mesma demanda em
 * outra capacidade): utilização_após = utilização_hoje × atual / recomendado.
 *
 * Organização, pensada para quem decide a troca:
 * 1. leitura guiada e checklist (o que os números significam e se é seguro);
 * 2. consumo no tempo num painel só, agregado por hora: barras para hoje e
 *    linha para o recomendado, na mesma escala, para o delta saltar aos olhos;
 * 3. curva de permanência em seção própria;
 * 4. mapa de picos por dia e hora em seção própria.
 *
 * Sem tamanho recomendado (KEEP, capacidade ou telemetria insuficiente) não há
 * "após": ficam o painel de hoje, a curva e o mapa de picos do tamanho atual.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Chip, CircularProgress, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircleOutlined";
import WarningIcon from "@mui/icons-material/WarningAmberOutlined";
import ErrorIcon from "@mui/icons-material/ErrorOutlineOutlined";
import Plot from "@/components/Plot";
import type { FlexConfig, OpportunitySeries, RecurringPeakWindow } from "../../services/infraOpportunitiesApi";
import { getOpportunitySeries } from "../../services/infraOpportunitiesApi";
import { PEAK_SLOT_COLOR } from "./RecurringPeaksPanel";

type Metric = "cpu" | "memory";

const COLORS = {
  today: "#0F62FE", after: "#149683", band: "rgba(15,98,254,0.14)", bandAfter: "rgba(20,150,131,0.16)",
  target: "#b7791f", limit: "#d64c4c", grid: "#e8edf3", ink: "#33424b", muted: "#66747d",
};
const ZONES = { ok: "rgba(20,150,131,0.06)", attention: "rgba(240,163,94,0.10)", saturated: "rgba(214,76,76,0.10)" };
const HOUR_LABELS = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}h`);

const localDate = (utc: string) => new Date(utc.endsWith("Z") || utc.includes("+") ? utc : `${utc}Z`);
const pad = (value: number) => String(value).padStart(2, "0");
const hourKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:00`;
const share = (values: number[], threshold: number) =>
  values.length ? values.filter((value) => value > threshold).length / values.length : 0;
const duration = (fraction: number, windowHours: number) => {
  const hours = fraction * windowHours;
  if (hours === 0) return "nunca";
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
const pct = (value: number | null) => (value == null ? "—" : `${value.toFixed(1)}%`);

type Props = {
  opportunityId: number;
  from: FlexConfig | null;
  to: FlexConfig | null;
  targets: { cpu: number; memory: number; cpuP99?: number; memoryP99?: number };
  coverage?: number | null;
  daysObserved?: number | null;
  /** Horários de picos recorrentes detectados pelo motor, contornados no mapa. */
  recurringWindows?: RecurringPeakWindow[];
};

type Check = { level: "ok" | "warn" | "risk"; label: string; detail: string };

function CheckRow({ check }: { check: Check }) {
  const Icon = check.level === "ok" ? CheckCircleIcon : check.level === "warn" ? WarningIcon : ErrorIcon;
  const color = check.level === "ok" ? "success.main" : check.level === "warn" ? "warning.main" : "error.main";
  return <Stack direction="row" spacing={1.25} alignItems="flex-start" sx={{ py: 0.75 }}>
    <Icon sx={{ color, fontSize: 20, mt: 0.25 }} />
    <Box>
      <Typography variant="body2" fontWeight={650}>{check.label}</Typography>
      <Typography variant="body2" color="text.secondary">{check.detail}</Typography>
    </Box>
  </Stack>;
}

export default function OpportunityCharts({ opportunityId, from, to, targets, coverage, daysObserved, recurringWindows }: Props) {
  // A base da análise: buckets de 5 min da Silver genérica.
  const resolution = 300;
  const [metric, setMetric] = useState<Metric>("cpu");
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
    if (!series) return null;
    const unit = metric === "cpu" ? "vCPU" : "GB";
    const hasAfter = Boolean(from && to);
    const capacityNow = from ? (metric === "cpu" ? from.vcpus : from.memory_gb) : null;
    const capacityAfter = to ? (metric === "cpu" ? to.vcpus : to.memory_gb) : capacityNow;
    const ratio = hasAfter && capacityNow && capacityAfter ? capacityNow / capacityAfter : 1;
    const target = (metric === "cpu" ? targets.cpu : targets.memory) * 100;
    const targetP99 = ((metric === "cpu" ? targets.cpuP99 : targets.memoryP99) ?? 0.9) * 100;
    const points = series.points.filter((point) => point[`${metric}_avg`] != null);
    const avgNow = points.map((point) => point[`${metric}_avg`] as number);
    const maxNow = points.map((point) => (point[`${metric}_max`] ?? point[`${metric}_avg`]) as number);
    const avgAfter = avgNow.map((value) => value * ratio);
    const maxAfter = maxNow.map((value) => value * ratio);
    const windowHours = points.length * series.resolution_seconds / 3600;

    // Hourly view: the line is the hour's P95 of bucket averages, the band
    // spans up to the hour's peak. Same demand, two capacities.
    const hours = new Map<string, { avg: number[]; max: number[] }>();
    points.forEach((point, index) => {
      const key = hourKey(localDate(point.t));
      const slot = hours.get(key) ?? { avg: [], max: [] };
      slot.avg.push(avgNow[index]);
      slot.max.push(maxNow[index]);
      hours.set(key, slot);
    });
    const hourKeys = [...hours.keys()].sort();
    const hourP95 = hourKeys.map((key) => quantile(hours.get(key)!.avg, 0.95) ?? 0);
    const hourPeak = hourKeys.map((key) => Math.max(...hours.get(key)!.max));

    const days = [...new Set(hourKeys.map((key) => key.slice(0, 10)))];
    const heatAfter = days.map((day) => HOUR_LABELS.map((_, hour) => {
      const slot = hours.get(`${day} ${pad(hour)}:00`);
      return slot ? Math.max(...slot.max) * ratio : null;
    }));
    const saturatedHoursPerDay = days.map((day) => {
      let count = 0;
      for (let hour = 0; hour < 24; hour += 1) {
        const slot = hours.get(`${day} ${pad(hour)}:00`);
        if (slot && Math.max(...slot.max) * ratio > 100) count += 1;
      }
      return count;
    });

    const stats = {
      p95Now: quantile(avgNow, 0.95), p95After: quantile(avgAfter, 0.95),
      p99Now: quantile(avgNow, 0.99), p99After: quantile(avgAfter, 0.99),
      peakNow: Math.max(...maxNow), peakAfter: Math.max(...maxAfter),
      overTargetNow: share(avgNow, target), overTargetAfter: share(avgAfter, target),
      saturatedNow: share(maxNow, 100), saturatedAfter: share(maxAfter, 100),
    };
    return {
      hasAfter, unit, capacityNow, capacityAfter, ratio, target, targetP99, windowHours, stats,
      hourKeys, hourP95, hourPeak, days, heatAfter, saturatedHoursPerDay,
      sortedNow: [...avgNow].sort((a, b) => b - a), sortedAfter: [...avgAfter].sort((a, b) => b - a),
    };
  }, [series, metric, from, to, targets]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!series || !view) return <Box sx={{ display: "grid", placeItems: "center", py: 6 }}><CircularProgress size={26} /></Box>;

  const name = metric === "cpu" ? "CPU" : "memória";
  const metricToggle = <ToggleButtonGroup size="small" exclusive value={metric} onChange={(_, next) => next && setMetric(next)}>
    <ToggleButton value="cpu">CPU</ToggleButton>
    <ToggleButton value="memory">Memória</ToggleButton>
  </ToggleButtonGroup>;
  if (!view.hourKeys.length) return <Stack spacing={1.5} alignItems="flex-start">
    {metricToggle}
    <Alert severity="info" sx={{ alignSelf: "stretch" }}>
      {series.points.length
        ? `Sem telemetria de ${name} na janela da análise; veja a outra métrica.`
        : "Sem telemetria na janela da análise: o host pode ter ficado parado (desligado) ou sem o agente de métricas. Não há consumo para mostrar."}
    </Alert>
  </Stack>;

  const { stats } = view;
  const capacityText = (value: number | null) => (value == null ? "capacidade não comprovada" : `${value} ${view.unit}`);
  // Sem recomendação, os critérios do motor são lidos no tamanho atual.
  const when = view.hasAfter ? "após a mudança" : "no tamanho atual";
  const judged = view.hasAfter
    ? { p95: stats.p95After, p99: stats.p99After, saturated: stats.saturatedAfter }
    : { p95: stats.p95Now, p99: stats.p99Now, saturated: stats.saturatedNow };
  const checks: Check[] = [
    {
      level: (daysObserved ?? 0) >= 14 ? "ok" : (daysObserved ?? 0) >= 7 ? "warn" : "risk",
      label: `Histórico analisado: ${daysObserved ?? "—"} dia(s)`,
      detail: (daysObserved ?? 0) >= 14 ? "Cobre mais de uma semana de ciclos de uso." : "Uma semana capta o ciclo semanal, mas não fechamentos de mês ou sazonalidade.",
    },
    {
      level: (coverage ?? 0) >= 0.98 ? "ok" : (coverage ?? 0) >= 0.9 ? "warn" : "risk",
      label: `Cobertura dos dados: ${coverage == null ? "—" : `${(coverage * 100).toFixed(1)}%`}`,
      detail: "Percentual dos intervalos esperados que chegaram da nuvem. Lacunas podem esconder picos.",
    },
    {
      level: (judged.p95 ?? 0) <= view.target ? "ok" : "risk",
      label: `P95 de ${name} ${when}: ${pct(judged.p95)} (limite ${view.target.toFixed(0)}%)`,
      detail: "Em 95% do tempo o uso fica abaixo deste valor no novo tamanho. É o critério principal do motor.",
    },
    {
      level: (judged.p99 ?? 0) <= view.targetP99 ? "ok" : "risk",
      label: `P99 de ${name} ${when}: ${pct(judged.p99)} (limite ${view.targetP99.toFixed(0)}%)`,
      detail: "Só 1% do tempo fica acima. Protege contra rajadas frequentes que o P95 não enxerga.",
    },
    {
      level: judged.saturated === 0 ? "ok" : judged.saturated <= 0.005 ? "warn" : "risk",
      label: `Tempo com pico acima de 100% ${when}: ${duration(judged.saturated, view.windowHours)}`,
      detail: judged.saturated === 0
        ? `Nenhum pico observado ultrapassa${view.hasAfter ? "ria a capacidade recomendada" : " a capacidade atual"}.`
        : judged.saturated <= 0.005
          ? "Dentro da tolerância do motor (até 0,5% do tempo, ≈ 3,6 h/mês). Confira no mapa abaixo em que horários acontecem."
          : `Acima da tolerância de 0,5% do tempo. Nesses momentos a demanda passaria da capacidade: ${metric === "cpu" ? "processos esperam CPU (lentidão), sem queda" : "risco de falta de memória"}. Confira se coincidem com rotinas críticas no mapa abaixo.`,
    },
  ];

  const todayPeak = view.hourPeak;
  const todayP95 = view.hourP95;
  const afterPeak = view.hourPeak.map((value) => value * view.ratio);
  const afterP95 = view.hourP95.map((value) => value * view.ratio);
  const yMax = Math.max(110, Math.ceil(Math.max(...afterPeak) / 10) * 10 + 10);
  // The timeline scales to the hourly P95 (peaks start hidden): an idle resource
  // would otherwise be a flat line on 0–110% and the delta would vanish.
  const timelineMax = Math.min(yMax, Math.max(5, Math.ceil(Math.max(...afterP95, ...todayP95) * 1.3 / 5) * 5));
  const zoneShapes = (yref: "y" | "y2") => [
    { type: "rect", xref: "paper", yref, x0: 0, x1: 1, y0: 0, y1: view.target, fillcolor: ZONES.ok, line: { width: 0 }, layer: "below" },
    { type: "rect", xref: "paper", yref, x0: 0, x1: 1, y0: view.target, y1: 100, fillcolor: ZONES.attention, line: { width: 0 }, layer: "below" },
    { type: "rect", xref: "paper", yref, x0: 0, x1: 1, y0: 100, y1: yMax, fillcolor: ZONES.saturated, line: { width: 0 }, layer: "below" },
    { type: "line", xref: "paper", yref, x0: 0, x1: 1, y0: 100, y1: 100, line: { color: COLORS.limit, width: 1.2, dash: "dash" } },
    { type: "line", xref: "paper", yref, x0: 0, x1: 1, y0: view.target, y1: view.target, line: { color: COLORS.target, width: 1, dash: "dot" } },
  ];
  const commonLayout = {
    autosize: true, paper_bgcolor: "transparent", plot_bgcolor: "transparent", separators: ",.",
    font: { family: "inherit", color: COLORS.muted, size: 12 },
  };
  const dayLabels = view.days.map((day) => `${day.slice(8, 10)}/${day.slice(5, 7)}`);
  // Recurring slots: whole columns for daily jobs, single cells for weekly ones
  // (engine weekdays: 0 = Monday).
  const weekdayOf = (day: string) => (new Date(`${day}T12:00:00`).getDay() + 6) % 7;
  const slotShapes = (recurringWindows ?? []).flatMap((window) => window.hours.flatMap((hour) => (
    window.weekdays == null
      ? [{ type: "rect", xref: "x", yref: "paper", x0: hour - 0.5, x1: hour + 0.5, y0: 0, y1: 1, line: { color: PEAK_SLOT_COLOR, width: 2.5 }, fillcolor: "rgba(0,0,0,0)" }]
      : view.days.flatMap((day, row) => (window.weekdays!.includes(weekdayOf(day))
        ? [{ type: "rect", xref: "x", yref: "y", x0: hour - 0.5, x1: hour + 0.5, y0: row - 0.5, y1: row + 0.5, line: { color: PEAK_SLOT_COLOR, width: 2 }, fillcolor: "rgba(0,0,0,0)" }]
        : []))
  )));

  return <Stack spacing={2}>
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
      {metricToggle}
      <Typography variant="caption" color="text.secondary">
        {view.days.length} dia(s) · horário local · {name}: {view.hasAfter ? `${view.capacityNow} → ${view.capacityAfter} ${view.unit}` : `${capacityText(view.capacityNow)} · sem mudança recomendada`}
      </Typography>
    </Stack>
    {!view.hasAfter && <Alert severity="info">
      Sem mudança de tamanho recomendada para este recurso: os gráficos mostram só o consumo no tamanho atual. Padrões de uso (picos diários, rotinas, ociosidade) ainda podem indicar outras oportunidades.
    </Alert>}

    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Typography variant="subtitle1" fontWeight={700}>Como ler estes números e se a troca é segura</Typography>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, gap: 3, mt: 1.5 }}>
        <Box>
          <Typography variant="body2" sx={{ mb: 1 }}>
            A nuvem mede o uso a cada ~10 s e entrega um resumo a cada {series.resolution_seconds / 60} min (média, máximo e percentis).
            O motor olha a janela inteira e pergunta: <em>se a mesma demanda rodasse no tamanho recomendado, onde ela ficaria?</em>
          </Typography>
          <Stack spacing={0.75} component="dl" sx={{ m: 0 }}>
            {[
              ["P50 (mediana)", "O uso típico: metade do tempo abaixo, metade acima."],
              ["P95", `95% do tempo abaixo deste valor. O motor exige P95 ≤ ${view.target.toFixed(0)}% no novo tamanho — sobra margem para o dia a dia.`],
              ["P99", `Só 1% do tempo acima (≈ 7 h por mês). Exigimos P99 ≤ ${view.targetP99.toFixed(0)}% para absorver rajadas frequentes.`],
              ["Pico", "O maior valor medido pela nuvem dentro dos intervalos. Picos curtos acima de 100% não derrubam a VM: geram fila (lentidão) naquele momento."],
            ].map(([term, text]) => <Box key={term}>
              <Typography component="dt" variant="body2" fontWeight={700}>{term}</Typography>
              <Typography component="dd" variant="body2" color="text.secondary" sx={{ m: 0 }}>{text}</Typography>
            </Box>)}
          </Stack>
        </Box>
        <Box>
          <Typography variant="body2" fontWeight={700} sx={{ mb: 0.5 }}>Checklist da decisão</Typography>
          {checks.map((check) => <CheckRow key={check.label} check={check} />)}
        </Box>
      </Box>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0,1fr))", md: "repeat(5, minmax(0,1fr))" }, gap: 1, mt: 2 }}>
        {[
          ["P95", pct(stats.p95Now), pct(stats.p95After)],
          ["P99", pct(stats.p99Now), pct(stats.p99After)],
          ["Maior pico", pct(stats.peakNow), pct(stats.peakAfter)],
          [`Tempo acima de ${view.target.toFixed(0)}%`, duration(stats.overTargetNow, view.windowHours), duration(stats.overTargetAfter, view.windowHours)],
          ["Tempo com pico > 100%", duration(stats.saturatedNow, view.windowHours), duration(stats.saturatedAfter, view.windowHours)],
        ].map(([label, now, later]) => <Paper key={label} variant="outlined" sx={{ p: 1.25 }}>
          <Typography variant="caption" color="text.secondary">{label}</Typography>
          <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap">
            <Typography variant="body2" sx={{ color: COLORS.today, fontWeight: 650 }}>hoje {now}</Typography>
            {view.hasAfter && <Typography variant="body2" sx={{ color: COLORS.after, fontWeight: 700 }}>após {later}</Typography>}
          </Stack>
        </Paper>)}
      </Box>
    </Paper>

    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Typography variant="subtitle1" fontWeight={700}>{view.hasAfter ? "Consumo no tempo — hoje × recomendado" : "Consumo no tempo — tamanho atual"}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
<<<<<<< Updated upstream
        {view.hasAfter ? "Mesma demanda nos dois painéis, em % da capacidade de cada um. " : "Em % da capacidade atual. "}Linha = P95 de cada hora; faixa clara = até o pico daquela hora.
        Fundo verde: folga; amarelo: acima do alvo de {view.target.toFixed(0)}%; vermelho: acima da capacidade.
      </Typography>
      <Plot data={[
        { type: "scatter", mode: "lines", x: view.hourKeys, y: todayPeak, line: { width: 0 }, fill: "tozeroy", fillcolor: COLORS.band, hovertemplate: "Pico: %{y:.1f}%<extra>Hoje</extra>", xaxis: "x", yaxis: "y" },
        { type: "scatter", mode: "lines", x: view.hourKeys, y: todayP95, line: { color: COLORS.today, width: 2 }, hovertemplate: "P95 da hora: %{y:.1f}%<extra>Hoje</extra>", xaxis: "x", yaxis: "y" },
        ...(view.hasAfter ? [
          { type: "scatter", mode: "lines", x: view.hourKeys, y: afterPeak, line: { width: 0 }, fill: "tozeroy", fillcolor: COLORS.bandAfter, hovertemplate: "Pico: %{y:.1f}%<extra>Após</extra>", xaxis: "x", yaxis: "y2" },
          { type: "scatter", mode: "lines", x: view.hourKeys, y: afterP95, line: { color: COLORS.after, width: 2 }, hovertemplate: "P95 da hora: %{y:.1f}%<extra>Após</extra>", xaxis: "x", yaxis: "y2" },
        ] : []),
      ] as never} layout={{
        ...commonLayout, height: view.hasAfter ? 540 : 320, margin: { l: 72, r: 20, t: 36, b: 50 }, hovermode: "x unified", showlegend: false,
        xaxis: {
          type: "date", showgrid: false, anchor: view.hasAfter ? "y2" : "y", hoverformat: "%d/%m %H:%M",
=======
        Mesma demanda, em % da capacidade de cada tamanho. Barras = P95 de cada hora hoje ({view.capacityNow} {view.unit});
        linha = o mesmo uso no tamanho recomendado ({view.capacityAfter} {view.unit}). O pico de cada hora após a mudança
        (maior: {pct(stats.peakAfter)}) liga pela legenda.
        A distância entre a barra e a linha é a folga que a troca consome. Fundo verde: folga; amarelo: acima do alvo de {view.target.toFixed(0)}%; vermelho: acima da capacidade.
      </Typography>
      <Plot data={[
        {
          type: "bar", name: `Hoje — ${view.capacityNow} ${view.unit}`, x: view.hourKeys, y: todayP95,
          marker: { color: COLORS.today, opacity: 0.55 },
          customdata: todayPeak.map((peak, index) => [peak, afterP95[index] - todayP95[index]]),
          hovertemplate: "Hoje: P95 %{y:.1f}% · pico %{customdata[0]:.1f}%<extra></extra>",
        },
        {
          type: "scatter", mode: "lines", name: `Após — ${view.capacityAfter} ${view.unit}`, x: view.hourKeys, y: afterP95,
          line: { color: COLORS.after, width: 2.5 },
          customdata: afterP95.map((value, index) => value - todayP95[index]),
          hovertemplate: "Após: P95 %{y:.1f}% (+%{customdata:.1f} p.p.)<extra></extra>",
        },
        {
          type: "scatter", mode: "lines", name: "Pico após a mudança", x: view.hourKeys, y: afterPeak,
          line: { color: COLORS.after, width: 1, dash: "dot" }, visible: "legendonly",
          hovertemplate: "Pico após: %{y:.1f}%<extra></extra>",
        },
      ] as never} layout={{
        ...commonLayout, height: 440, margin: { l: 64, r: 20, t: 16, b: 50 }, hovermode: "x unified",
        bargap: 0, barmode: "overlay", showlegend: true, legend: { orientation: "h", y: -0.16, x: 0 },
        xaxis: {
          type: "date", showgrid: false, hoverformat: "%d/%m %H:%M",
>>>>>>> Stashed changes
          // Mais zoom, mais detalhe: dia → dia e hora → hora e minuto.
          tickformatstops: [
            { dtickrange: [null, 3_600_000], value: "%d/%m %H:%M" },
            { dtickrange: [3_600_000, 86_400_000], value: "%d/%m %Hh" },
            { dtickrange: [86_400_000, null], value: "%d/%m" },
          ],
        },
<<<<<<< Updated upstream
        yaxis: { domain: view.hasAfter ? [0.56, 1] : [0, 1], range: [0, yMax], ticksuffix: "%", gridcolor: COLORS.grid, title: { text: capacityText(view.capacityNow) } },
        ...(view.hasAfter ? { yaxis2: { domain: [0, 0.44], range: [0, yMax], ticksuffix: "%", gridcolor: COLORS.grid, title: { text: `${view.capacityAfter} ${view.unit}` } } } : {}),
        shapes: [...zoneShapes("y"), ...(view.hasAfter ? zoneShapes("y2") : [])],
        annotations: [
          { xref: "paper", yref: "paper", x: 0, y: view.hasAfter ? 1.05 : 1.1, xanchor: "left", showarrow: false, text: `<b>Hoje</b> · ${capacityText(view.capacityNow)} · P95 ${pct(stats.p95Now)}`, font: { color: COLORS.today, size: 13 } },
          view.hasAfter
            ? { xref: "paper", yref: "paper", x: 0, y: 0.49, xanchor: "left", showarrow: false, text: `<b>Após a mudança</b> · ${view.capacityAfter} ${view.unit} · P95 ${pct(stats.p95After)}`, font: { color: COLORS.after, size: 13 } }
            : { xref: "paper", yref: "paper", x: 1, y: 1.1, xanchor: "right", showarrow: false, text: "sem mudança recomendada", font: { color: COLORS.muted, size: 12 } },
=======
        // Fixed to the P95: turning the peak on clips it (its value stays on hover) instead of flattening the delta.
        yaxis: { range: [0, timelineMax], ticksuffix: "%", gridcolor: COLORS.grid, title: { text: `Uso de ${name}` } },
        shapes: zoneShapes("y"),
        annotations: [
          { xref: "paper", yref: "paper", x: 1, y: 1, xanchor: "right", yanchor: "top", showarrow: false, align: "right",
            text: `P95 <span style="color:${COLORS.today}">hoje ${pct(stats.p95Now)}</span> → <b style="color:${COLORS.after}">após ${pct(stats.p95After)}</b>`,
            font: { size: 13, color: COLORS.ink }, bgcolor: "rgba(255,255,255,0.85)" },
>>>>>>> Stashed changes
        ],
      } as never} />
    </Paper>

    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Typography variant="subtitle1" fontWeight={700}>Curva de permanência — quanto tempo o uso fica acima de cada nível</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Leia da esquerda para a direita: na marca de 5% do tempo está o P95, na de 1% o P99. Hoje a {name} passa de {view.target.toFixed(0)}% durante {duration(stats.overTargetNow, view.windowHours)}
        {view.hasAfter
          ? <>; após a mudança, durante {duration(stats.overTargetAfter, view.windowHours)}, e a média dos intervalos passa de 100% durante {duration(share(view.sortedAfter, 100), view.windowHours)}.</>
          : <> e a média dos intervalos passa de 100% durante {duration(share(view.sortedNow, 100), view.windowHours)}.</>}
      </Typography>
      <Plot data={[
        { type: "scatter", mode: "lines", name: `Hoje — ${capacityText(view.capacityNow)}`, x: view.sortedNow.map((_, i) => (i + 1) / view.sortedNow.length * 100), y: view.sortedNow, line: { color: COLORS.today, width: 2.5 }, hovertemplate: "%{x:.2f}% do tempo acima de %{y:.1f}%<extra>Hoje</extra>" },
        ...(view.hasAfter ? [
          { type: "scatter", mode: "lines", name: `Após — ${view.capacityAfter} ${view.unit}`, x: view.sortedAfter.map((_, i) => (i + 1) / view.sortedAfter.length * 100), y: view.sortedAfter, line: { color: COLORS.after, width: 2.5 }, hovertemplate: "%{x:.2f}% do tempo acima de %{y:.1f}%<extra>Após</extra>" },
        ] : []),
      ] as never} layout={{
        ...commonLayout, height: 400, hovermode: "closest", margin: { l: 72, r: 20, t: 16, b: 80 },
        xaxis: { title: { text: "% do tempo (escala logarítmica: detalha os poucos momentos de maior uso)" }, type: "log", range: [Math.log10(0.05), 2], ticksuffix: "%", gridcolor: COLORS.grid, tickvals: [0.1, 0.5, 1, 5, 10, 25, 50, 100] },
        yaxis: { title: { text: `Uso de ${name}` }, ticksuffix: "%", rangemode: "tozero", gridcolor: COLORS.grid },
        shapes: [
          { type: "rect", xref: "paper", x0: 0, x1: 1, y0: 0, y1: view.target, fillcolor: ZONES.ok, line: { width: 0 }, layer: "below" },
          { type: "rect", xref: "paper", x0: 0, x1: 1, y0: view.target, y1: 100, fillcolor: ZONES.attention, line: { width: 0 }, layer: "below" },
          { type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: COLORS.limit, dash: "dash", width: 1.2 } },
          { type: "line", yref: "paper", x0: 5, x1: 5, y0: 0, y1: 1, line: { color: COLORS.muted, dash: "dot", width: 1 } },
          { type: "line", yref: "paper", x0: 1, x1: 1, y0: 0, y1: 1, line: { color: COLORS.muted, dash: "dot", width: 1 } },
        ],
        annotations: [
          { x: Math.log10(5), y: 1, yref: "paper", text: "P95", showarrow: false, yanchor: "bottom", font: { size: 11, color: COLORS.ink } },
          { x: Math.log10(1), y: 1, yref: "paper", text: "P99", showarrow: false, yanchor: "bottom", font: { size: 11, color: COLORS.ink } },
          ...(view.hasAfter ? [{ x: Math.log10(5), y: stats.p95After ?? 0, text: `após ${pct(stats.p95After)}`, showarrow: true, arrowhead: 0, ax: 46, ay: -26, font: { color: COLORS.after } }] : []),
          { x: Math.log10(5), y: stats.p95Now ?? 0, text: `hoje ${pct(stats.p95Now)}`, showarrow: true, arrowhead: 0, ax: 46, ay: 22, font: { color: COLORS.today } },
        ],
        legend: { orientation: "h", y: -0.3, x: 0 },
      } as never} />
    </Paper>

    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Typography variant="subtitle1" fontWeight={700}>{view.hasAfter ? "Picos após a mudança, por dia e hora" : "Picos no tamanho atual, por dia e hora"}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {view.hasAfter
          ? `Cada célula é o maior pico daquela hora, já projetado no tamanho recomendado (${view.capacityAfter} ${view.unit}); vermelho é acima da capacidade.`
          : `Cada célula é o maior pico daquela hora no tamanho atual (${capacityText(view.capacityNow)}); vermelho é acima da capacidade.`}{" "}
        Veja se os picos coincidem com rotinas (backups, fechamentos, horário comercial). À direita, quantas horas de cada dia teriam pico acima de 100%.
      </Typography>
      <Plot data={[
        {
          type: "heatmap", x: HOUR_LABELS, y: dayLabels, z: view.heatAfter, zmin: 0, zmax: 130, xgap: 1, ygap: 1,
          colorscale: [[0, "#eef6f4"], [view.target / 130, "#7cc9b8"], [100 / 130, "#f2b45c"], [101 / 130, "#e06c6c"], [1, "#a83232"]],
          hovertemplate: `%{y} às %{x}: pico de %{z:.0f}% ${view.hasAfter ? "no novo tamanho" : "no tamanho atual"}<extra></extra>`,
          colorbar: { thickness: 12, len: 0.9, x: 0.79, tickvals: [0, view.target, 100, 130], ticktext: ["0%", `alvo ${view.target.toFixed(0)}%`, "100%", "130%+"] },
          xaxis: "x", yaxis: "y",
        },
        {
          type: "bar", orientation: "h", x: view.saturatedHoursPerDay, y: dayLabels, xaxis: "x2", yaxis: "y",
          marker: { color: view.saturatedHoursPerDay.map((count) => (count ? COLORS.limit : "#cfd8df")) },
          hovertemplate: "%{y}: %{x} hora(s) com pico > 100%<extra></extra>",
          text: view.saturatedHoursPerDay.map((count) => (count ? String(count) : "")), textposition: "outside",
        },
      ] as never} layout={{
        ...commonLayout, height: Math.max(380, view.days.length * 24 + 90), hovermode: "closest", margin: { l: 60, r: 24, t: 10, b: 44 },
        xaxis: { domain: [0, 0.74], tickangle: 0, dtick: 2 },
        xaxis2: { domain: [0.86, 1], title: { text: "horas > 100%" }, rangemode: "tozero", showgrid: false },
        yaxis: { autorange: "reversed", type: "category" },
        shapes: slotShapes,
        showlegend: false,
      } as never} />
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
        <Chip size="small" variant="outlined" color={view.saturatedHoursPerDay.some(Boolean) ? "warning" : "success"}
              label={`Dias com algum pico acima de 100%: ${view.saturatedHoursPerDay.filter(Boolean).length} de ${view.days.length}`} />
        <Chip size="small" variant="outlined" label={`Horas com pico acima de 100%: ${view.saturatedHoursPerDay.reduce((a, b) => a + b, 0)}`} />
        {!!recurringWindows?.length && <Chip size="small" variant="outlined" sx={{ borderColor: PEAK_SLOT_COLOR, color: PEAK_SLOT_COLOR }}
              label={`Contorno roxo: picos recorrentes ${recurringWindows.map((window) => `${window.start}–${window.end}`).join(", ")}`} />}
      </Stack>
    </Paper>
  </Stack>;
}
