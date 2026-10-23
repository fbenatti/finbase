import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Box, Button, Chip, CircularProgress, Divider, IconButton, Menu, MenuItem, Paper, Popover, Stack, TextField, Typography } from "@mui/material";
import { AccountTreeOutlined, BarChartOutlined, CloseOutlined, ExpandMoreOutlined, FactCheckOutlined, ShowChartOutlined, TimelineOutlined } from "@mui/icons-material";
import Plot from "@/components/Plot";
import { chartSemantic, chartSeries, colors } from "../../theme/tokens";
import { ExpectedViewRow, GoldQueryFilter, formatApiError, getExpectedView, queryDimonTable, queryGoldDailyConsumption } from "../../services/api";
import DeviationMatrix from "./DeviationMatrix";
import AnomalyCaseDrawer from "../Anomalies/AnomalyCaseDrawer";
import HierarchicalDecompTree, { buildExecutiveDecompChain } from "./HierarchicalDecompTree";
import { dayKey, periodKey, useCostSignals } from "./costSignals";

// Analise de custos no estilo Azure Cost Analysis: escopo e periodo no topo, KPIs com
// desvios, grafico principal (acumulado, mensal ou diario de 90 dias), desvios por mes,
// cartoes de rosca e sinais (anomalias como marcadores).
export type CostScope = {
  monthFrom: string;
  monthTo: string;
  dateFrom: string;
  dateTo: string;
  dimensionFilters: Record<string, string[]>;
  filters: GoldQueryFilter[];
};

type ChartType = "area" | "column" | "daily";
type GroupBy = "none" | "servicename" | "regionname" | "subaccountname";
type Breakdown = { name: string; value: number };
type DailyRow = { day: string; realizado: number; tipo?: unknown };

const CHART_LABELS: Record<ChartType, string> = { area: "Área acumulada", column: "Colunas", daily: "Diário (90 dias)" };
const GROUP_LABELS: Record<GroupBy, string> = { none: "Nenhum", servicename: "Serviço", regionname: "Região", subaccountname: "Subconta" };
const HIERARCHY_LABELS = ["Segmento", "Modelo", "VP", "Diretoria", "Projeto", "Unidade"];
const MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const OTHERS = "Outros";
const UNKNOWN = "Sem classificação";
const CARD_SX = { p: 2.25, borderRadius: "18px", borderColor: colors.border, boxShadow: "0 6px 20px rgba(24, 39, 75, 0.035)" };
const SIGN_UP = colors.accent;
const SIGN_DOWN = colors.brand;

const brlCompact = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const brlFull = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }).format(value);
const signedBrl = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${brlCompact(Math.abs(value))}`;
const signedPct = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const shareOf = (value: number, total: number) => (total ? `${((value / total) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "—");

const periodLabel = (period: unknown) => {
  const key = periodKey(period);
  if (!/^\d{6}$/.test(key)) return key || "—";
  return `${MONTHS[Number(key.slice(4)) - 1]}/${key.slice(2, 4)}`;
};
const dayLabel = (day: string) => `${day.slice(8, 10)}/${day.slice(5, 7)}`;
const severityColor = (severity?: string) => {
  const key = (severity || "").toLowerCase();
  if (key.startsWith("cr")) return colors.error;
  if (key === "perigo") return colors.accent;
  if (key === "sucesso") return colors.success;
  return colors.brand;
};
const valueOf = (row: Record<string, any>, column: string) => Number(row[column] ?? 0);

const isoDay = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const isoMonth = (date: Date) => isoDay(date).slice(0, 7);
const lastDayOf = (yyyyMm: string) => {
  const [year, month] = yyyyMm.split("-").map(Number);
  return `${yyyyMm}-${String(new Date(year, month, 0).getDate()).padStart(2, "0")}`;
};
const monthsAgo = (n: number, from = new Date()) => new Date(from.getFullYear(), from.getMonth() - n, 1);

// Atalhos de periodo no topo, na mesma ordem dos chips do Azure.
function presetRanges(): Array<{ label: string; from: string; to: string }> {
  const now = new Date();
  const year = now.getFullYear();
  const quarterStart = new Date(year, Math.floor(now.getMonth() / 3) * 3, 1);
  const ranges: Array<[string, Date, Date]> = [
    ["Mês atual", monthsAgo(0), now],
    ["Trimestre atual", quarterStart, now],
    ["Últimos 3 meses", monthsAgo(2), now],
    ["Últimos 6 meses", monthsAgo(5), now],
    ["Últimos 12 meses", monthsAgo(11), now],
    ["Ano corrente", new Date(year, 0, 1), new Date(year, 11, 1)],
  ];
  return ranges.map(([label, start, end]) => ({ label, from: `${isoMonth(start)}-01`, to: lastDayOf(isoMonth(end)) }));
}

function Kpi({ label, value, full, sub, delta, accent, ratio, onClick }: {
  label: string; value: number; full: string; sub?: string; accent: string; ratio?: number; onClick?: () => void;
  delta?: { value: number; pct: number; base: string };
}) {
  const up = (delta?.value ?? 0) > 0;
  return (
    <Paper variant="outlined" onClick={onClick} sx={{ ...CARD_SX, height: "100%", borderTop: `3px solid ${accent}`, cursor: onClick ? "pointer" : "default", transition: "box-shadow .15s, transform .15s", "&:hover": onClick ? { boxShadow: "0 10px 28px rgba(24, 39, 75, 0.09)", transform: "translateY(-1px)" } : undefined }}>
      <Typography variant="overline" color="text.secondary" fontWeight={700}>{label}</Typography>
      <Typography variant="h3" fontWeight={800} color={colors.text} title={full} sx={{ lineHeight: 1.1, mt: 0.5 }}>{brlCompact(value)}</Typography>
      <Box sx={{ height: 6, bgcolor: colors.tableHead, borderRadius: 3, overflow: "hidden", my: 1.25 }}>
        <Box sx={{ width: `${Math.min(Math.max(ratio ?? 1, 0), 1) * 100}%`, height: "100%", bgcolor: accent, borderRadius: 3 }} />
      </Box>
      {delta ? (
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          <Chip size="small" label={`${signedBrl(delta.value)} (${signedPct(delta.pct)})`} sx={{ fontWeight: 700, bgcolor: delta.value === 0 ? colors.tableHead : up ? "#FFF1E6" : "#EEF4FF", color: up ? colors.accentDark : colors.brandDark }} />
          <Typography variant="caption" color="text.secondary">{delta.base}</Typography>
        </Stack>
      ) : (
        <Typography variant="caption" color="text.secondary">{sub}</Typography>
      )}
    </Paper>
  );
}

function DonutCard({ title, items }: { title: string; items: Breakdown[] }) {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  const head = items.slice(0, 5);
  const rest = items.slice(5).reduce((sum, item) => sum + item.value, 0);
  const slices = rest > 0 ? [...head, { name: OTHERS, value: rest }] : head;
  return (
    <Paper variant="outlined" sx={CARD_SX}>
      <Typography variant="subtitle2" fontWeight={800} color={colors.text}>{title}</Typography>
      {!slices.length ? (
        <Typography variant="body2" color="text.secondary" mt={1.5}>Sem dados na competência selecionada.</Typography>
      ) : (
        <Stack direction="row" gap={2} alignItems="center" mt={1.5}>
          <Box sx={{ width: 150, minWidth: 150, height: 150 }}>
            <Plot
              data={[{ type: "pie", labels: slices.map(s => s.name), values: slices.map(s => Math.max(s.value, 0)), hole: 0.66, sort: false, textinfo: "none", hovertemplate: "%{label}<br>%{value:,.0f}<extra></extra>", marker: { colors: slices.map((_, i) => chartSeries[i % chartSeries.length]) } } as any]}
              layout={{ height: 150, showlegend: false, margin: { l: 2, r: 2, t: 2, b: 2 }, annotations: [{ text: brlCompact(total), showarrow: false, font: { size: 14, color: colors.text } }] } as any}
              config={{ displayModeBar: false }}
            />
          </Box>
          <Stack gap={0.9} sx={{ flex: 1, minWidth: 0 }}>
            {slices.map((slice, i) => (
              <Stack key={slice.name} direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
                <Box sx={{ width: 9, height: 9, borderRadius: "3px", flexShrink: 0, bgcolor: chartSeries[i % chartSeries.length] }} />
                <Typography variant="body2" noWrap title={slice.name} sx={{ flex: 1, minWidth: 0 }}>{slice.name}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ width: 44, textAlign: "right" }}>{shareOf(slice.value, total)}</Typography>
              </Stack>
            ))}
          </Stack>
        </Stack>
      )}
    </Paper>
  );
}

export default function CostSummary({ table, director, departmentId, path, includeSharedCost, measureColumn, scope, refreshKey, onDateRangeChange }: {
  table: string;
  director: string;
  departmentId?: number;
  path: string[];
  includeSharedCost: boolean;
  measureColumn: "realizado" | "realizado_cloud";
  scope: CostScope;
  refreshKey?: number;
  onDateRangeChange: (from: string, to: string) => void;
}) {
  const navigate = useNavigate();
  const [chartType, setChartType] = useState<ChartType>("area");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [groupAnchor, setGroupAnchor] = useState<HTMLElement | null>(null);
  const [typeAnchor, setTypeAnchor] = useState<HTMLElement | null>(null);
  const [periodAnchor, setPeriodAnchor] = useState<HTMLElement | null>(null);
  // Recorte aberto na decomposicao inline (clique no valor, na barra ou no ponto do grafico).
  const [openCaseId, setOpenCaseId] = useState<number | null>(null);
  const [focus, setFocus] = useState<{ label: string; months: string[] } | null>(null);
  const [customFrom, setCustomFrom] = useState(scope.dateFrom.slice(0, 7));
  const [customTo, setCustomTo] = useState(scope.dateTo.slice(0, 7));
  const [rows, setRows] = useState<ExpectedViewRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [grouped, setGrouped] = useState<Record<string, Record<string, number>>>({});
  const [breakdowns, setBreakdowns] = useState<Record<string, Breakdown[]>>({});
  const [dailyRows, setDailyRows] = useState<DailyRow[]>([]);
  const [dailyLoading, setDailyLoading] = useState(false);

  const hierarchyColumn = `hierarquia_${Math.min(path.length + 1, 6)}`;
  const hierarchyLabel = HIERARCHY_LABELS[Math.min(path.length, 5)];
  const breakdownDefs = useMemo(() => [
    { column: "servicename", title: "Por serviço" },
    { column: "regionname", title: "Por região" },
    { column: hierarchyColumn, title: `Por ${hierarchyLabel}` },
  ], [hierarchyColumn, hierarchyLabel]);

  const scopeKey = JSON.stringify([table, director, departmentId, includeSharedCost, measureColumn, scope]);
  const measures = useMemo(() => [{ column: measureColumn, aggregation: "SUM" as const }], [measureColumn]);
  const signals = useCostSignals(scope, departmentId, path, table, refreshKey);

  // Ultimos 90 dias do periodo escolhido, sem passar de hoje.
  const todayIso = isoDay(new Date());
  const dayTo = scope.dateTo < todayIso ? scope.dateTo : todayIso;
  const dayStart = new Date(`${dayTo}T00:00:00`);
  dayStart.setDate(dayStart.getDate() - 89);
  const dayFrom = isoDay(dayStart) > scope.dateFrom ? isoDay(dayStart) : scope.dateFrom;
  const dailyKey = JSON.stringify([table, scope.filters, dayFrom, dayTo]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    getExpectedView(director, 0, departmentId, controller.signal, includeSharedCost, false, table, scope, measureColumn)
      .then((result) => setRows(result.rows))
      .catch((reason) => { if (!controller.signal.aborted) setError(formatApiError(reason, "Não foi possível carregar a análise de custo")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  useEffect(() => { setFocus(null); }, [scopeKey]);

  useEffect(() => {
    if (chartType !== "daily" || !dayFrom || !dayTo || dayFrom > dayTo) { setDailyRows([]); return; }
    let alive = true;
    setDailyLoading(true);
    queryGoldDailyConsumption({ table, filters: scope.filters, day_from: dayFrom, day_to: dayTo, group_by: null, row_limit: 10000 })
      .then((result) => { if (alive) setDailyRows(result.rows as DailyRow[]); })
      .catch(() => { if (alive) setDailyRows([]); })
      .finally(() => { if (alive) setDailyLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dailyKey, chartType]);

  useEffect(() => {
    let alive = true;
    Promise.all(breakdownDefs.map((b) => queryDimonTable({ table, rows: [b.column], measures, filters: scope.filters, row_limit: 500, sort_column: null, sort_direction: "desc" })))
      .then((results) => {
        if (!alive) return;
        const next: Record<string, Breakdown[]> = {};
        breakdownDefs.forEach((b, i) => {
          next[b.column] = (results[i]?.rows ?? [])
            .map((row) => ({ name: String(row[b.column] ?? UNKNOWN), value: valueOf(row, measureColumn) }))
            .sort((a, z) => z.value - a.value);
        });
        setBreakdowns(next);
      })
      .catch(() => { if (alive) setBreakdowns({}); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, hierarchyColumn]);

  useEffect(() => {
    if (groupBy === "none") { setGrouped({}); return; }
    let alive = true;
    queryDimonTable({ table, rows: ["_billing_period", groupBy], measures, filters: scope.filters, row_limit: 5000, sort_column: null, sort_direction: "desc" })
      .then((result) => {
        if (!alive) return;
        const next: Record<string, Record<string, number>> = {};
        result.rows.forEach((row) => {
          const period = periodKey(row._billing_period);
          const group = String(row[groupBy] ?? UNKNOWN);
          next[period] = next[period] ?? {};
          next[period][group] = (next[period][group] ?? 0) + valueOf(row, measureColumn);
        });
        setGrouped(next);
      })
      .catch(() => { if (alive) setGrouped({}); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, groupBy]);

  const series = useMemo(() => [...rows]
    .sort((a, z) => periodKey(a.period).localeCompare(periodKey(z.period)))
    .map((row) => {
      const actual = row.actual_value == null ? null : Number(row.actual_value);
      const forecast = Number(row.forecast_value ?? 0) + Number(row.forecast_manual_value ?? 0);
      return {
        key: periodKey(row.period),
        label: periodLabel(row.period),
        actual: actual ?? 0,
        closed: actual != null,
        forecast,
        total: (actual ?? 0) + forecast,
        budget: Number(row.budget_value || 0),
      };
    }), [rows]);

  const totals = useMemo(() => ({
    actual: series.reduce((sum, row) => sum + row.actual, 0),
    forecast: series.reduce((sum, row) => sum + row.total, 0),
    budget: series.reduce((sum, row) => sum + row.budget, 0),
  }), [series]);

  // Desvio do real contra o forecast (meses fechados com projecao) e contra o orcamento.
  const forecastDeviation = useMemo(() => {
    const months = signals.forecastMonths.map((month) => ({
      label: periodLabel(month.target_month),
      delta: Number(month.actual_value ?? 0) - Number(month.forecast_value ?? 0),
      base: Number(month.forecast_value ?? 0),
    }));
    const delta = months.reduce((sum, m) => sum + m.delta, 0);
    const base = months.reduce((sum, m) => sum + m.base, 0);
    return { months, delta, pct: base ? delta / base : 0 };
  }, [signals.forecastMonths]);

  const budgetDeviation = useMemo(() => {
    const closed = series.filter((row) => row.closed);
    const months = closed.map((row) => ({ label: row.label, delta: row.actual - row.budget, base: row.budget }));
    const delta = months.reduce((sum, m) => sum + m.delta, 0);
    const base = months.reduce((sum, m) => sum + m.base, 0);
    return { months, delta, pct: base ? delta / base : 0 };
  }, [series]);

  const cumulative = useMemo(() => {
    let actual = 0;
    let forecast = 0;
    let budget = 0;
    const anchor = series.reduce((last, row, index) => row.closed && row.forecast !== 0 ? index : last, -1);
    const firstProjected = anchor >= 0 ? anchor : series.findIndex((row) => !row.closed);
    const actualEnd = firstProjected >= 0 ? firstProjected - 1 : series.length - 1;
    return series.map((row, index) => {
      actual += row.actual;
      forecast += index < firstProjected || firstProjected < 0
        ? row.actual
        : row.closed ? row.actual + row.forecast : row.forecast;
      budget += row.budget;
      return {
        label: row.label,
        actual: index <= actualEnd ? actual : null,
        forecast: firstProjected >= 0 && index >= Math.max(0, firstProjected - 1) ? forecast : null,
        budget,
      };
    });
  }, [series]);

  const topGroups = useMemo(() => {
    const byGroup: Record<string, number> = {};
    Object.values(grouped).forEach((groups) => Object.entries(groups).forEach(([name, value]) => { byGroup[name] = (byGroup[name] ?? 0) + value; }));
    return Object.entries(byGroup).sort((a, z) => z[1] - a[1]).map(([name]) => name).slice(0, 7);
  }, [grouped]);

  const labels = series.map((row) => row.label);
  const anomalyPoints = useMemo(() => series
    .map((row, index) => ({ index, row, count: signals.anomalyByMonth[row.key] ?? 0 }))
    .filter((point) => point.count > 0), [series, signals.anomalyByMonth]);

  // Dias da janela (ate 90) com realizado, forecast e media movel de 7 dias.
  const calendar = useMemo(() => {
    const out: string[] = [];
    if (!dayFrom || !dayTo || dayFrom > dayTo) return out;
    const cursor = new Date(`${dayFrom}T00:00:00`);
    const end = new Date(`${dayTo}T00:00:00`);
    while (cursor <= end && out.length < 400) {
      out.push(isoDay(cursor));
      cursor.setDate(cursor.getDate() + 1);
    }
    return out;
  }, [dayFrom, dayTo]);

  const daily = useMemo(() => {
    const actualByDay = new Map<string, number>();
    const forecastByDay = new Map<string, number>();
    dailyRows.forEach((row) => {
      const day = dayKey(row.day);
      const value = Number(row.realizado) || 0;
      const isForecast = String(row.tipo ?? "").toLowerCase() === "forecast";
      const target = isForecast ? forecastByDay : actualByDay;
      target.set(day, (target.get(day) ?? 0) + value);
    });
    const actual = calendar.map((day) => actualByDay.get(day) ?? 0);
    const forecast = calendar.map((day) => forecastByDay.get(day) ?? 0);
    const movingAverage = actual.map((_, i) => {
      const window = actual.slice(Math.max(0, i - 6), i + 1);
      return window.reduce((sum, v) => sum + v, 0) / window.length;
    });
    const anomalyDays = new Map<string, number>();
    signals.anomalies.forEach((item) => {
      const day = dayKey(item.event_date);
      if (day >= dayFrom && day <= dayTo) anomalyDays.set(day, (anomalyDays.get(day) ?? 0) + 1);
    });
    const anomalyIndex = calendar.map((day, i) => ({ i, day, count: anomalyDays.get(day) ?? 0 })).filter((point) => point.count > 0);
    return { actual, forecast, movingAverage, anomalyIndex };
  }, [dailyRows, calendar, signals.anomalies, dayFrom, dayTo]);

  const chartTraces = useMemo<any[]>(() => {
    if (chartType === "daily") {
      const x = calendar.map(dayLabel);
      const markers = daily.anomalyIndex.length ? [{
        type: "scatter",
        mode: "markers",
        name: "Anomalias do dia",
        x: daily.anomalyIndex.map((point) => dayLabel(point.day)),
        y: daily.anomalyIndex.map((point) => daily.actual[point.i] ?? 0),
        text: daily.anomalyIndex.map((point) => `${point.count} anomalia(s)`),
        hovertemplate: "%{x}: %{text}<extra></extra>",
        marker: { symbol: "diamond", size: 10, color: colors.accent, line: { color: "#FFFFFF", width: 1.5 } },
      }] : [];
      return [
        { type: "bar", name: "Realizado diário", x, y: daily.actual, marker: { color: "rgba(15, 98, 254, 0.55)" } },
        { type: "bar", name: "Forecast diária", x, y: daily.forecast, marker: { color: chartSemantic.forecast, opacity: 0.45 } },
        { type: "scatter", mode: "lines", name: "Média móvel 7 dias", x, y: daily.movingAverage, line: { color: colors.text, width: 2 } },
        ...markers,
      ];
    }
    if (chartType === "area") {
      const x = cumulative.map((row) => row.label);
      const markers = anomalyPoints.length ? [{
        type: "scatter",
        mode: "markers",
        name: "Anomalias",
        x: anomalyPoints.map((point) => point.row.label),
        y: anomalyPoints.map((point) => cumulative[point.index]?.actual ?? 0),
        text: anomalyPoints.map((point) => `${point.count} anomalia(s)`),
        hovertemplate: "%{x}: %{text}<extra></extra>",
        marker: { symbol: "diamond", size: 10, color: colors.accent, line: { color: "#FFFFFF", width: 1.5 } },
      }] : [];
      return [
        { type: "scatter", mode: "lines", name: "Forecast", x, y: cumulative.map((row) => row.forecast), line: { color: chartSemantic.forecast, width: 3, shape: "spline", smoothing: 0.4 }, fill: "tozeroy", fillcolor: "rgba(14, 165, 198, 0.18)" },
        { type: "scatter", mode: "lines", name: "Custo efetivado", x, y: cumulative.map((row) => row.actual), line: { color: chartSemantic.actual, width: 3, shape: "spline", smoothing: 0.4 }, fill: "tozeroy", fillcolor: "rgba(15, 98, 254, 0.16)" },
        { type: "scatter", mode: "lines", name: "Orçamento", x, y: cumulative.map((row) => row.budget), line: { color: chartSemantic.budget, width: 2, dash: "dash" } },
        ...markers,
      ];
    }
    const traces: any[] = [];
    if (groupBy === "none") {
      traces.push({ type: "bar", name: "Custo efetivado", x: labels, y: series.map((row) => row.actual), marker: { color: chartSemantic.actual } });
    } else {
      topGroups.forEach((name, i) => traces.push({
        type: "bar",
        name,
        x: labels,
        y: series.map((row) => grouped[row.key]?.[name] ?? 0),
        marker: { color: chartSeries[i % chartSeries.length] },
      }));
      traces.push({
        type: "bar",
        name: OTHERS,
        x: labels,
        y: series.map((row) => Object.entries(grouped[row.key] ?? {}).filter(([name]) => !topGroups.includes(name)).reduce((sum, [, value]) => sum + value, 0)),
        marker: { color: "#CBD5E1" },
      });
    }
    traces.push({ type: "bar", name: "Forecast", x: labels, y: series.map((row) => row.forecast), marker: { color: chartSemantic.forecast, opacity: 0.45 } });
    traces.push({ type: "scatter", mode: "lines+markers", name: "Orçamento", x: labels, y: series.map((row) => row.budget), line: { color: chartSemantic.budget, width: 2, dash: "dash" }, marker: { size: 5 } });
    return traces;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType, groupBy, cumulative, series, grouped, topGroups, anomalyPoints, calendar, daily]);

  const chartLayout = useMemo(() => ({
    height: 340,
    barmode: chartType === "area" ? undefined : "stack",
    hovermode: "x unified",
    margin: { l: 64, r: 16, t: 12, b: 40 },
    legend: { orientation: "h", y: -0.16 },
    yaxis: { tickprefix: "R$ ", tickformat: "~s", separatethousands: true, gridcolor: colors.border },
    xaxis: { showgrid: false, tickangle: chartType === "daily" ? -45 : 0 },
  }) as any, [chartType]);

  const deviationTraces = useMemo<any[]>(() => {
    const x = forecastDeviation.months.map((m) => m.label);
    return [
      {
        type: "bar",
        name: "Real − Forecast",
        x,
        y: forecastDeviation.months.map((m) => m.delta),
        marker: { color: forecastDeviation.months.map((m) => (m.delta > 0 ? SIGN_UP : SIGN_DOWN)) },
        hovertemplate: "%{x}: %{y:,.0f}<extra>vs forecast</extra>",
      },
      {
        type: "scatter",
        mode: "markers",
        name: "Real − orçamento",
        x: budgetDeviation.months.map((m) => m.label),
        y: budgetDeviation.months.map((m) => m.delta),
        marker: { symbol: "circle-open", size: 11, color: colors.text, line: { width: 2 } },
        hovertemplate: "%{x}: %{y:,.0f}<extra>vs orçamento</extra>",
      },
    ];
  }, [forecastDeviation, budgetDeviation]);

  const deviationLayout = useMemo(() => ({
    height: 240,
    barmode: "group",
    hovermode: "x unified",
    margin: { l: 64, r: 16, t: 10, b: 36 },
    legend: { orientation: "h", y: -0.2 },
    yaxis: { tickprefix: "R$ ", tickformat: "~s", zeroline: true, zerolinecolor: colors.borderStrong, gridcolor: colors.border },
    xaxis: { showgrid: false },
  }) as any, []);

  const periods = presetRanges();
  const activePreset = periods.find((preset) => preset.from === scope.dateFrom && preset.to === scope.dateTo)?.label;
  const periodText = `${periodLabel(scope.dateFrom)} – ${periodLabel(scope.dateTo)}`;
  const variance = totals.budget - totals.forecast;

  const applyCustom = () => {
    if (!customFrom || !customTo || customFrom > customTo) return;
    onDateRangeChange(`${customFrom}-01`, lastDayOf(customTo));
    setPeriodAnchor(null);
  };

  const topAnomalies = [...signals.anomalies]
    .sort((a, z) => Number(z.anomalous_cost_cloud || 0) - Number(a.anomalous_cost_cloud || 0))
    .slice(0, 3);

  const decompChain = useMemo(() => buildExecutiveDecompChain(false, path.length), [path.length]);
  const focusFilters = useMemo(() => {
    if (!focus) return scope.filters;
    const months = focus.months;
    return [...scope.filters.filter((filter) => filter.column !== "_billing_period"), { column: "_billing_period", operator: "IN", values: months } as GoldQueryFilter];
  }, [focus, scope.filters]);
  const focusMonth = focus && focus.months.length === 1 ? focus.months[0] : null;
  const visibleJustifications = focusMonth ? signals.justifications.filter((ack) => periodKey(ack.target_month) === focusMonth) : signals.justifications;

  function openFocus(months: string[], label: string) {
    if (!months.length) return;
    setFocus({ months, label });
  }

  function onChartClick(event: any, daily = chartType === "daily") {
    const x = String(event?.points?.[0]?.x ?? "");
    if (!x) return;
    if (daily) {
      const day = calendar.find((item) => dayLabel(item) === x);
      if (day) openFocus([periodKey(day)], `Mês de ${dayLabel(day)}`);
      return;
    }
    const row = series.find((item) => item.label === x);
    if (row) openFocus([row.key], `Competência ${row.label}`);
  }

  return (
    <Stack gap={2.5}>
      <Stack direction="row" flexWrap="wrap" gap={0.75} alignItems="center">
        <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ mr: 0.5 }}>Período</Typography>
        {periods.map((preset) => {
          const active = activePreset === preset.label;
          return (
            <Chip
              key={preset.label}
              size="small"
              label={preset.label}
              clickable
              onClick={() => onDateRangeChange(preset.from, preset.to)}
              sx={{ height: 28, borderRadius: "8px", fontWeight: active ? 700 : 500, bgcolor: active ? colors.brand : "transparent", color: active ? "#FFFFFF" : colors.text, border: `1px solid ${active ? colors.brand : colors.border}`, "&:hover": { bgcolor: active ? colors.brandDark : colors.tableHead } }}
            />
          );
        })}
        <Chip
          size="small"
          label={activePreset ? "Personalizado" : periodText}
          clickable
          onClick={(event) => { setCustomFrom(scope.dateFrom.slice(0, 7)); setCustomTo(scope.dateTo.slice(0, 7)); setPeriodAnchor(event.currentTarget); }}
          sx={{ height: 28, borderRadius: "8px", fontWeight: activePreset ? 500 : 700, bgcolor: activePreset ? "transparent" : colors.brand, color: activePreset ? colors.text : "#FFFFFF", border: `1px solid ${activePreset ? colors.border : colors.brand}` }}
        />
        {(loading || signals.loading) && <CircularProgress size={14} sx={{ ml: 0.5 }} />}
      </Stack>

      <Popover open={Boolean(periodAnchor)} anchorEl={periodAnchor} onClose={() => setPeriodAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "left" }} transformOrigin={{ vertical: "top", horizontal: "left" }}>
        <Stack gap={1.5} sx={{ p: 2, minWidth: 260 }}>
          <Typography variant="overline" color="text.secondary" fontWeight={800}>Período personalizado</Typography>
          <TextField size="small" type="month" label="Mês inicial" value={customFrom} onChange={(event) => setCustomFrom(event.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField size="small" type="month" label="Mês final" value={customTo} onChange={(event) => setCustomTo(event.target.value)} InputLabelProps={{ shrink: true }} />
          {customFrom > customTo && <Typography variant="caption" color="error">A competência inicial deve ser igual ou anterior à final.</Typography>}
          <Stack direction="row" justifyContent="flex-end" gap={1}>
            <Button size="small" onClick={() => setPeriodAnchor(null)}>Cancelar</Button>
            <Button size="small" variant="contained" disabled={!customFrom || !customTo || customFrom > customTo} onClick={applyCustom} sx={{ bgcolor: colors.brandDark }}>Aplicar</Button>
          </Stack>
        </Stack>
      </Popover>

      {error && <Alert severity="error">{error}</Alert>}

      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
        <Kpi
          label="Custo efetivado"
          value={totals.actual}
          onClick={() => openFocus(series.filter((row) => row.closed || row.actual > 0).map((row) => row.key), `Custo efetivado · ${periodText}`)}
          full={brlFull(totals.actual)}
          accent={chartSemantic.actual}
          ratio={totals.forecast ? totals.actual / totals.forecast : 0}
          sub={`${shareOf(totals.actual, totals.forecast)} da Forecast do período`}
        />
        <Kpi
          label="Forecast"
          value={totals.forecast}
          onClick={() => openFocus(series.map((row) => row.key), `Forecast · ${periodText}`)}
          full={brlFull(totals.forecast)}
          accent={chartSemantic.forecast}
          ratio={totals.budget ? totals.forecast / totals.budget : 0}
          delta={{ value: forecastDeviation.delta, pct: forecastDeviation.pct, base: "Desvio real vs Forecast (meses fechados)" }}
        />
        <Kpi
          label="Meta orçamentária"
          value={totals.budget}
          full={brlFull(totals.budget)}
          accent={variance >= 0 ? colors.success : colors.error}
          ratio={totals.budget ? totals.forecast / totals.budget : 0}
          delta={{ value: budgetDeviation.delta, pct: budgetDeviation.pct, base: `Desvio real vs orçamento · ${variance >= 0 ? "folga" : "estouro"} de ${brlCompact(Math.abs(variance))}` }}
        />
      </Box>

      <Paper variant="outlined" sx={CARD_SX}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1} mb={1}>
          <Box>
            <Typography variant="subtitle1" fontWeight={800} color={colors.text}>{chartType === "daily" ? CHART_LABELS.daily : chartType === "area" ? "Custo acumulado" : "Custo mensal"}</Typography>
            <Typography variant="caption" color="text.secondary">
              {chartType === "daily" ? "Realizado por dia, média móvel de 7 dias e anomalias. Clique em um dia para ver onde está o custo do mês." : chartType === "area" ? "Efetivado, Forecast, orçamento e anomalias. Clique em um mês ou em um valor para ver onde está o custo." : "Efetivado, Forecast e orçamento. Clique em um mês ou em um valor para ver onde está o custo."}
            </Typography>
          </Box>
          <Stack direction="row" gap={1} flexWrap="wrap">
            <Button size="small" variant="outlined" onClick={(event) => setTypeAnchor(event.currentTarget)} startIcon={chartType === "area" ? <ShowChartOutlined /> : chartType === "daily" ? <TimelineOutlined /> : <BarChartOutlined />} endIcon={<ExpandMoreOutlined />} sx={{ textTransform: "none" }}>
              {CHART_LABELS[chartType]}
            </Button>
            <Button size="small" variant="outlined" disabled={chartType !== "column"} onClick={(event) => setGroupAnchor(event.currentTarget)} endIcon={<ExpandMoreOutlined />} sx={{ textTransform: "none" }}>
              Agrupar por: {GROUP_LABELS[groupBy]}
            </Button>
          </Stack>
        </Stack>
        {chartType === "daily" ? (
          dailyLoading ? <Typography variant="body2" color="text.secondary" sx={{ py: 8, textAlign: "center" }}>Carregando últimos 90 dias…</Typography>
            : calendar.length ? <Plot data={chartTraces} layout={chartLayout} config={{ displayModeBar: false }} style={{ height: 340, cursor: "pointer" }} onClick={(event: any) => onChartClick(event)} />
            : <Typography variant="body2" color="text.secondary" sx={{ py: 8, textAlign: "center" }}>Sem dias no período selecionado.</Typography>
        ) : series.length ? (
          <Plot data={chartTraces} layout={chartLayout} config={{ displayModeBar: false }} style={{ height: 340, cursor: "pointer" }} onClick={(event: any) => onChartClick(event)} />
        ) : (
          <Typography variant="body2" color="text.secondary" sx={{ py: 8, textAlign: "center" }}>{loading ? "Carregando custos…" : "Sem custos no período selecionado."}</Typography>
        )}

      </Paper>

      {focus && (
        <Paper variant="outlined" sx={{ ...CARD_SX, borderColor: colors.brand, borderWidth: 1.5 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={1} mb={1.5}>
            <Stack direction="row" gap={1.25} alignItems="center" sx={{ minWidth: 0 }}>
              <Box sx={{ width: 34, height: 34, borderRadius: "10px", bgcolor: "rgba(15, 98, 254, 0.1)", color: colors.brand, display: "grid", placeItems: "center", flexShrink: 0 }}><AccountTreeOutlined fontSize="small" /></Box>
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Onde está o custo</Typography>
                <Typography variant="caption" color="text.secondary">{focus.label} · Selecione um item para abrir o próximo nível.</Typography>
              </Box>
            </Stack>
            <IconButton size="small" aria-label="Fechar decomposição" onClick={() => setFocus(null)}><CloseOutlined fontSize="small" /></IconButton>
          </Stack>
          <HierarchicalDecompTree
            table={table}
            baseFilters={focusFilters}
            chain={decompChain}
            queryKey={`resumo-onde-${JSON.stringify(focusFilters)}`}
            measureColumn={measureColumn}
            shareCaption="participação no recorte"
            presentation="ranked"
          />
        </Paper>
      )}

      {signals.forecastError && <Alert severity="warning">{signals.forecastError}</Alert>}
      <DeviationMatrix
        months={series.map((row) => ({ key: row.key, label: row.label, actual: row.actual, budget: row.budget, closed: row.closed }))}
        anomalies={signals.anomalies}
        forecastMonths={signals.forecastMonths}
        justifications={signals.justifications}
        onOpenAccuracy={() => navigate("/projecoes/acuracia")}
        onOpenAnomalies={() => navigate("/alertas")}
        onOpenBudget={() => navigate("/planejamento?tab=orcamento")}
        onOpenCase={setOpenCaseId}
        table={table}
        filters={scope.filters}
        path={path}
        departmentId={departmentId}
      />

      <Menu anchorEl={typeAnchor} open={Boolean(typeAnchor)} onClose={() => setTypeAnchor(null)}>
        {(Object.keys(CHART_LABELS) as ChartType[]).map((item) => (
          <MenuItem key={item} selected={item === chartType} onClick={() => { setChartType(item); setTypeAnchor(null); }}>{CHART_LABELS[item]}</MenuItem>
        ))}
      </Menu>
      <Menu anchorEl={groupAnchor} open={Boolean(groupAnchor)} onClose={() => setGroupAnchor(null)}>
        {(Object.keys(GROUP_LABELS) as GroupBy[]).map((item) => (
          <MenuItem key={item} selected={item === groupBy} onClick={() => { setGroupBy(item); setGroupAnchor(null); }}>{GROUP_LABELS[item]}</MenuItem>
        ))}
      </Menu>

      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
        {breakdownDefs.map((b) => <DonutCard key={b.column} title={b.title} items={breakdowns[b.column] ?? []} />)}
      </Box>
      <AnomalyCaseDrawer caseId={openCaseId} onClose={() => setOpenCaseId(null)} />
    </Stack>
  );
}
