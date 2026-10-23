/**
 * “Todos os recursos”: cada recurso da última execução com o consumo da janela
 * em mini gráficos, recomendação ou não. Padrões que não mudam o tamanho
 * (picos diários de backup, hosts parados, uso só em horário comercial) também
 * são oportunidades, e só aparecem olhando todos.
 *
 * Os mini gráficos vêm de um perfil horário agregado no servidor (P95 e pico de
 * cada hora), não dos buckets de 5 min de cada host.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Chip, FormControl, InputLabel, MenuItem, Paper, Select, Skeleton, Stack, ToggleButton,
  ToggleButtonGroup, Typography,
} from "@mui/material";
import type { HourlyProfile, HourlyProfiles, Opportunity } from "../../services/infraOpportunitiesApi";
import { getHourlyProfiles } from "../../services/infraOpportunitiesApi";
import { ACTION_COLOR, ACTION_LABEL, READINESS_LABEL, money, pct, sizeLabel } from "./opportunityLabels";

type Grain = "hour" | "day";
type Metric = "cpu" | "memory";
type SortKey = "cpu_max" | "cpu_p95" | "memory_max" | "memory_p95" | "current_monthly_cost" | "monthly_saving" | "name";

const COLORS = { cpu: "#0F62FE", memory: "#8a3ffc" } as const;
const BANDS = { cpu: "rgba(15,98,254,0.16)", memory: "rgba(138,63,252,0.16)" } as const;
const SORTS: { key: SortKey; label: string }[] = [
  { key: "cpu_max", label: "Pico de CPU" },
  { key: "cpu_p95", label: "P95 de CPU" },
  { key: "memory_max", label: "Pico de memória" },
  { key: "memory_p95", label: "P95 de memória" },
  { key: "current_monthly_cost", label: "Custo / mês" },
  { key: "monthly_saving", label: "Saving / mês" },
  { key: "name", label: "Nome" },
];

const pad = (value: number) => String(value).padStart(2, "0");
const utcHour = (hour: string) => new Date(`${hour}:00:00Z`);
const dayKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const max = (values: (number | null)[]) => values.reduce<number | null>((acc, value) => (value == null ? acc : acc == null ? value : Math.max(acc, value)), null);
const median = (values: number[]) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

type Series = { labels: string[]; p95: (number | null)[]; peak: (number | null)[] };

/** Série por hora (horário local) ou por dia (maior P95 horário e maior pico do dia). */
function toSeries(hours: Date[], p95: (number | null)[], peak: (number | null)[], grain: Grain): Series {
  const label = (date: Date) => `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${pad(date.getHours())}h`;
  if (grain === "hour") return { labels: hours.map(label), p95, peak };
  const days = new Map<string, { p95: (number | null)[]; peak: (number | null)[] }>();
  hours.forEach((date, index) => {
    const slot = days.get(dayKey(date)) ?? { p95: [], peak: [] };
    slot.p95.push(p95[index]);
    slot.peak.push(peak[index]);
    days.set(dayKey(date), slot);
  });
  const keys = [...days.keys()].sort();
  return {
    labels: keys.map((key) => `${key.slice(8, 10)}/${key.slice(5, 7)}`),
    p95: keys.map((key) => max(days.get(key)!.p95)),
    peak: keys.map((key) => max(days.get(key)!.peak)),
  };
}

/** Pico típico por hora do dia: mediana, entre os dias, do pico daquela hora local. */
function hourOfDay(hours: Date[], peak: (number | null)[]) {
  const slots: number[][] = Array.from({ length: 24 }, () => []);
  hours.forEach((date, index) => { if (peak[index] != null) slots[date.getHours()].push(peak[index] as number); });
  return slots.map(median);
}

function Sparkline({ series, metric }: { series: Series; metric: Metric }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 300;
  const height = 48;
  const top = Math.max(10, max(series.peak) ?? 0, max(series.p95) ?? 0);
  const n = series.p95.length;
  const x = (index: number) => (n <= 1 ? width / 2 : (index / (n - 1)) * width);
  const y = (value: number) => height - (value / top) * (height - 2) - 1;
  // Gaps break the line instead of being drawn as zero.
  const path = (values: (number | null)[], area: boolean) => {
    const runs: string[] = [];
    let current: [number, number][] = [];
    const flush = () => {
      if (!current.length) return;
      const line = current.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
      runs.push(area ? `${line}L${current[current.length - 1][0].toFixed(1)},${height}L${current[0][0].toFixed(1)},${height}Z` : line);
      current = [];
    };
    values.forEach((value, index) => { if (value == null) flush(); else current.push([x(index), y(value)]); });
    flush();
    return runs.join("");
  };
  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setHover(Math.min(n - 1, Math.max(0, Math.round(((event.clientX - rect.left) / rect.width) * (n - 1)))));
  };
  return <Box sx={{ position: "relative" }}>
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" width="100%" height={height}
         onMouseMove={onMove} onMouseLeave={() => setHover(null)} style={{ display: "block", overflow: "visible" }}>
      <path d={path(series.peak, true)} fill={BANDS[metric]} stroke="none" />
      <path d={path(series.p95, false)} fill="none" stroke={COLORS[metric]} strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
      {hover != null && <line x1={x(hover)} x2={x(hover)} y1={0} y2={height} stroke="#66747d" strokeWidth={1} strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />}
    </svg>
    <Typography variant="caption" color="text.secondary" sx={{ position: "absolute", top: -2, right: 0, fontSize: 10, lineHeight: 1, bgcolor: "background.paper", px: 0.25 }}>
      {hover != null ? `${series.labels[hover]} · P95 ${pct(series.p95[hover])} · pico ${pct(series.peak[hover])}` : `escala até ${top.toFixed(0)}%`}
    </Typography>
  </Box>;
}

function HourStrip({ values, metric }: { values: (number | null)[]; metric: Metric }) {
  const top = Math.max(1, max(values) ?? 0);
  return <Box sx={{ display: "grid", gridTemplateColumns: "repeat(24, 1fr)", gap: "1px", mt: 0.5 }}>
    {values.map((value, hour) => <Box key={hour} title={`${pad(hour)}h: pico típico ${pct(value)}`}
      sx={{ height: 8, borderRadius: "1px", bgcolor: value == null ? "#f1f3f5" : COLORS[metric], opacity: value == null ? 1 : 0.12 + 0.88 * (value / top) }} />)}
  </Box>;
}

function MetricCell({ label, metric, hours, profile, grain, stats, loading }: {
  label: string; metric: Metric; hours: Date[]; profile: HourlyProfile | undefined; grain: Grain; loading: boolean;
  stats: { p50: number | null; p95: number | null; p99: number | null; max: number | null };
}) {
  const content = useMemo(() => {
    if (!profile || !profile[`${metric}_hours`]) return null;
    return {
      series: toSeries(hours, profile[`${metric}_p95`], profile[`${metric}_peak`], grain),
      strip: hourOfDay(hours, profile[`${metric}_peak`]),
    };
  }, [profile, metric, hours, grain]);
  return <Box sx={{ minWidth: 0 }}>
    <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 0.25 }}>
      <Typography variant="caption" fontWeight={700} sx={{ color: COLORS[metric] }}>{label}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
        P50 {pct(stats.p50)} · P95 {pct(stats.p95)} · P99 {pct(stats.p99)} · pico <b>{pct(stats.max)}</b>
      </Typography>
    </Stack>
    {loading ? <Skeleton variant="rounded" height={58} />
      : content ? <><Sparkline series={content.series} metric={metric} /><HourStrip values={content.strip} metric={metric} /></>
        : <Box sx={{ height: 58, display: "grid", placeItems: "center", border: "1px dashed", borderColor: "divider", borderRadius: 1 }}>
          <Typography variant="caption" color="text.secondary">Sem telemetria na janela (host parado ou sem agente de métricas)</Typography>
        </Box>}
  </Box>;
}

type Props = { connectionId: number; runId: number | null; items: Opportunity[]; currency: string; onOpen: (id: number) => void };

export default function AllResourcesView({ connectionId, runId, items, currency, onOpen }: Props) {
  const [profiles, setProfiles] = useState<HourlyProfiles | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [grain, setGrain] = useState<Grain>("hour");
  const [action, setAction] = useState("");
  const [readiness, setReadiness] = useState("");
  const [sort, setSort] = useState<SortKey>("cpu_max");

  useEffect(() => {
    if (runId == null) return;
    let active = true;
    setProfiles(null);
    setError(null);
    getHourlyProfiles(connectionId, runId)
      .then((data) => { if (active) setProfiles(data); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Não foi possível ler o consumo dos recursos"); });
    return () => { active = false; };
  }, [connectionId, runId]);

  const hours = useMemo(() => (profiles?.hours ?? []).map(utcHour), [profiles]);
  const byId = useMemo(() => new Map((profiles?.items ?? []).map((item) => [item.opportunity_id, item])), [profiles]);
  const readinessOptions = useMemo(() => [...new Set(items.map((item) => item.readiness_status))].sort(), [items]);
  const visible = useMemo(() => {
    const value = (item: Opportunity) => (sort === "name" ? null : item[sort]);
    return items
      .filter((item) => (!action || item.action_type === action) && (!readiness || item.readiness_status === readiness))
      .sort((a, b) => {
        if (sort === "name") return (a.resource_name ?? a.resource_id).localeCompare(b.resource_name ?? b.resource_id);
        const left = value(a);
        const right = value(b);
        if (left == null) return right == null ? 0 : 1;
        if (right == null) return -1;
        return right - left;
      });
  }, [items, action, readiness, sort]);
  const loading = !profiles && !error;

  return <Box>
    <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
      <FormControl size="small" sx={{ minWidth: 160 }}>
        <InputLabel shrink>Ação</InputLabel>
        <Select label="Ação" notched displayEmpty value={action} onChange={(event) => setAction(event.target.value)}>
          <MenuItem value="">Todas</MenuItem>
          {(Object.keys(ACTION_LABEL) as Opportunity["action_type"][]).map((key) => <MenuItem key={key} value={key}>{ACTION_LABEL[key]}</MenuItem>)}
        </Select>
      </FormControl>
      <FormControl size="small" sx={{ minWidth: 200 }}>
        <InputLabel shrink>Prontidão</InputLabel>
        <Select label="Prontidão" notched displayEmpty value={readiness} onChange={(event) => setReadiness(event.target.value)}>
          <MenuItem value="">Todas</MenuItem>
          {readinessOptions.map((key) => <MenuItem key={key} value={key}>{READINESS_LABEL[key] ?? key}</MenuItem>)}
        </Select>
      </FormControl>
      <FormControl size="small" sx={{ minWidth: 170 }}>
        <InputLabel>Ordenar por</InputLabel>
        <Select label="Ordenar por" value={sort} onChange={(event) => setSort(event.target.value as SortKey)}>
          {SORTS.map((item) => <MenuItem key={item.key} value={item.key}>{item.label}</MenuItem>)}
        </Select>
      </FormControl>
      <ToggleButtonGroup size="small" exclusive value={grain} onChange={(_, next) => next && setGrain(next)}>
        <ToggleButton value="hour">Por hora</ToggleButton>
        <ToggleButton value="day">Por dia</ToggleButton>
      </ToggleButtonGroup>
      <Typography variant="body2" color="text.secondary">{visible.length} de {items.length} recurso(s) na análise #{runId ?? "—"}</Typography>
    </Stack>
    <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
      Horário local. Linha = P95 {grain === "hour" ? "de cada hora" : "horário mais alto do dia"}; faixa clara = até o pico {grain === "hour" ? "da hora" : "do dia"}.
      Cada gráfico tem a própria escala, para mostrar o padrão mesmo em hosts ociosos. A régua de 24 células é o pico típico (mediana entre os dias) de cada hora do dia: horas escuras repetem pico todo dia — rotinas como backup ou carga agendada.
    </Typography>
    {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}

    <Stack spacing={1}>
      {visible.map((item) => {
        const profile = byId.get(item.id);
        return <Paper key={item.id} variant="outlined" onClick={() => onOpen(item.id)}
          sx={{
            p: 1.5, cursor: "pointer", display: "grid", gap: 2, alignItems: "center",
            gridTemplateColumns: { xs: "1fr", lg: "minmax(220px, 1fr) minmax(0, 2fr) minmax(0, 2fr) minmax(130px, 0.6fr)" },
            "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
          }}>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={700} sx={{ overflowWrap: "anywhere" }}>{item.resource_name ?? item.resource_id}</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {item.from_config?.sku ?? "—"} · {sizeLabel(item.from_config)}
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
              <Chip size="small" color={ACTION_COLOR[item.action_type]} label={ACTION_LABEL[item.action_type]} />
              <Chip size="small" variant="outlined" label={READINESS_LABEL[item.readiness_status] ?? item.readiness_status} />
            </Stack>
          </Box>
          <MetricCell label="CPU" metric="cpu" hours={hours} profile={profile} grain={grain} loading={loading}
                      stats={{ p50: item.cpu_p50, p95: item.cpu_p95, p99: item.cpu_p99, max: item.cpu_max }} />
          <MetricCell label="Memória" metric="memory" hours={hours} profile={profile} grain={grain} loading={loading}
                      stats={{ p50: item.memory_p50, p95: item.memory_p95, p99: item.memory_p99, max: item.memory_max }} />
          <Box sx={{ textAlign: { lg: "right" } }}>
            <Typography variant="caption" color="text.secondary">Custo / mês</Typography>
            <Typography variant="body2" fontWeight={650}>{money(item.current_monthly_cost, currency)}</Typography>
            {item.action_type !== "KEEP" && item.monthly_saving != null && <Typography variant="caption" sx={{ color: item.monthly_saving > 0 ? "success.main" : "error.main" }}>
              {item.monthly_saving > 0 ? "saving" : "custo extra"} {money(Math.abs(item.monthly_saving), currency)}
            </Typography>}
          </Box>
        </Paper>;
      })}
      {!visible.length && <Typography variant="body2" color="text.secondary">Nenhum recurso com esses filtros.</Typography>}
    </Stack>
  </Box>;
}
