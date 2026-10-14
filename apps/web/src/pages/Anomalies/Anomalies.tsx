/**
 * Página `/anomalies` — gestão de anomalias de custo cloud.
 *
 * Visões: Visão geral · Meus casos / fila FinOps · Casos · Eventos esperados ·
 * Qualidade · Retrospectiva.
 *
 * O detalhe do caso mostra o painel de etapas (responsável, prazo, próxima
 * ação), o gráfico observado/faixa/área anômala, as ações (remediação) e o
 * workflow por comandos explícitos.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, LinearProgress,
  MenuItem, Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs,
  TextField, Tooltip, Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import NotificationsIcon from "@mui/icons-material/Notifications";
import RefreshIcon from "@mui/icons-material/Refresh";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import type {
  AnomalyCase, AnomalyConfig, AnomalyNotification, AnomalySeries, CaseSummary,
  ExpectedEventFamily, QualityBucket,
} from "../../services/anomaliesApi";
import {
  createAnomalyAction, enqueueAnomalyRun, getAnomalyCase, getAnomalyCaseEvents,
  getAnomalyCaseSeries, getAnomalyMetrics, getExpectedEvents, getQuality,
  listAnomalyCases, listAnomalyConfigs, listAnomalyNotifications,
  readAnomalyNotification, transitionAnomalyCase, updateAnomalyAction,
  updateAnomalyConfig,
} from "../../services/anomaliesApi";

const STATE_LABEL: Record<string, string> = {
  NEW: "Novo",
  ASSIGNED: "Atribuído",
  INVESTIGATING: "Em investigação",
  DECIDED: "Decidido",
  IN_REMEDIATION: "Em remediação",
  VERIFYING: "Em verificação",
  RETROSPECTIVE: "Retrospectiva",
  CLOSED: "Encerrado",
};

const SEVERITY_COLOR: Record<string, "default" | "info" | "warning" | "error"> = {
  LOW: "default",
  MEDIUM: "info",
  HIGH: "warning",
  CRITICAL: "error",
};

const DECISION_LABEL: Record<string, string> = {
  EXPECTED: "Esperado",
  DATA_ISSUE: "Problema de dados",
  CONFIRMED_ACTION: "Confirmado — com ação",
  CONFIRMED_ACCEPTED: "Confirmado — aceito",
  LOW_PRIORITY_NO_ACTION: "Baixa prioridade, sem ação",
};

const CAUSE_LABEL: Record<string, string> = {
  LEGITIMATE_GROWTH: "Crescimento legítimo",
  EXPECTED_PURCHASE: "Compra/renovação esperada",
  MISCONFIGURATION: "Configuração indevida",
  FORGOTTEN_RESOURCE: "Recurso esquecido",
  PRICE_OR_DISCOUNT_CHANGE: "Mudança de preço/desconto",
  DATA_ISSUE: "Problema de dados",
  SECURITY_INCIDENT: "Possível incidente de segurança",
  UNDETERMINED: "Causa não determinada",
};

const money = (value: number | null | undefined, currency = "BRL") =>
  value === null || value === undefined
    ? "—"
    : value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 2 });

const dt = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("pt-BR") : "—";

const chartColors = { observed: "#263746", expected: "#64748b", band: "#dbeafe", limit: "#2583c5", anomaly: "#d14343", grid: "#e8edf3", axis: "#748094" };

/** Contexto histórico de realizado para tornar o desvio comparável antes/depois. */
function ContextChart({ series, kase, currency }: { series: AnomalySeries; kase: AnomalyCase; currency: string }) {
  const points = [...(series.context_items ?? [])].sort((a, b) => a.event_date.localeCompare(b.event_date));
  if (!points.length) return null;
  const before = points.filter((point) => point.event_date < (series.context_event_date ?? kase.event_date) && point.observed_cloud !== null).map((point) => point.observed_cloud as number);
  const after = points.filter((point) => point.event_date > (series.context_event_date ?? kase.event_date) && point.observed_cloud !== null).map((point) => point.observed_cloud as number);
  const median = (values: number[]) => {
    const sorted = [...values].sort((a, b) => a - b);
    if (!sorted.length) return null;
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };
  const beforeMedian = median(before);
  const afterMedian = median(after);
  const width = 880;
  const height = 276;
  const plot = { left: 76, right: 18, top: 24, bottom: 42 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const values = points.map((point) => point.observed_cloud).filter((value): value is number => value !== null && Number.isFinite(value));
  if (values.length === 0) return <Alert severity="info" sx={{ mt: 2 }}>Ainda não há dias observados nesta janela. A ausência de linha não significa consumo zero.</Alert>;
  const rawMin = Math.min(0, ...values);
  const rawMax = Math.max(0, ...values);
  const padding = (rawMax - rawMin || Math.abs(rawMax) || 1) * 0.14;
  const min = rawMin - padding;
  const max = rawMax + padding;
  const y = (value: number) => plot.top + (max - value) / (max - min) * plotHeight;
  const x = (index: number) => plot.left + index * plotWidth / Math.max(1, points.length - 1);
  const eventIndex = points.findIndex((point) => point.is_event_day || point.event_date === (series.context_event_date ?? kase.event_date));
  const ticks = Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4);
  const segments: Array<Array<{ index: number; value: number }>> = [];
  points.forEach((point, index) => {
    if (point.observed_cloud === null) return;
    const last = segments[segments.length - 1];
    if (last && last[last.length - 1].index === index - 1) last.push({ index, value: point.observed_cloud });
    else segments.push([{ index, value: point.observed_cloud }]);
  });
  const labelIndexes = [0, Math.round((points.length - 1) * 0.33), Math.round((points.length - 1) * 0.66), points.length - 1];
  return (
    <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2.5 }, borderRadius: 2.5, borderColor: "#e5eaf0", mb: 2 }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", md: "center" }} spacing={1} sx={{ mb: 1 }}>
        <Box><Typography variant="subtitle1" fontWeight={700}>O que mudou ao redor da detecção?</Typography><Typography variant="caption" color="text.secondary">Realizado diário · 30 dias antes e 15 dias depois · linha vertical marca o evento</Typography></Box>
        <Stack direction="row" spacing={1}>
          <Chip size="small" variant="outlined" label={`Mediana antes ${beforeMedian === null ? "—" : money(beforeMedian, currency)}`} />
          <Chip size="small" color={afterMedian !== null && beforeMedian !== null && afterMedian > beforeMedian ? "error" : "default"} variant="outlined" label={`Mediana depois ${afterMedian === null ? "—" : money(afterMedian, currency)}`} />
        </Stack>
      </Stack>
      <Box sx={{ width: "100%", overflow: "hidden" }}><svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Custo cloud realizado nos 30 dias anteriores e 15 posteriores à anomalia" style={{ display: "block", fontFamily: "inherit" }}>
        {ticks.map((tick) => <g key={tick}><line x1={plot.left} x2={width - plot.right} y1={y(tick)} y2={y(tick)} stroke={chartColors.grid} /><text x={plot.left - 10} y={y(tick) + 4} textAnchor="end" fontSize="11" fill={chartColors.axis}>{money(tick, currency)}</text></g>)}
        {eventIndex >= 0 && <><line x1={x(eventIndex)} x2={x(eventIndex)} y1={plot.top} y2={height - plot.bottom} stroke={chartColors.anomaly} strokeWidth={2} strokeDasharray="5 5" /><text x={Math.min(x(eventIndex) + 8, width - 150)} y={plot.top + 12} fontSize="11" fontWeight="700" fill={chartColors.anomaly}>Detecção · {points[eventIndex].event_date}</text></>}
        {beforeMedian !== null && <line x1={plot.left} x2={eventIndex >= 0 ? x(eventIndex) : width - plot.right} y1={y(beforeMedian)} y2={y(beforeMedian)} stroke={chartColors.expected} strokeWidth={1.5} strokeDasharray="4 5" opacity={0.8} />}
        {segments.map((segment, index) => <polyline key={index} points={segment.map(({ index: i, value }) => `${x(i)},${y(value)}`).join(" ")} fill="none" stroke={chartColors.observed} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />)}
        {points.map((point, index) => point.observed_cloud === null ? null : <circle key={point.event_date} cx={x(index)} cy={y(point.observed_cloud)} r={point.is_event_day ? 6 : 3} fill={point.is_event_day ? chartColors.anomaly : chartColors.observed} stroke="white" strokeWidth={1.5}><title>{`${point.event_date} · realizado ${money(point.observed_cloud, currency)}`}</title></circle>)}
        {labelIndexes.map((index) => <text key={index} x={x(index)} y={height - 12} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"} fontSize="11" fill={chartColors.axis}>{new Date(`${points[index].event_date}T00:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", timeZone: "UTC" })}</text>)}
      </svg></Box>
      <Typography variant="caption" color="text.secondary">Dias sem registro foram mantidos como lacunas, não como zero. A linha tracejada é a mediana do realizado anterior; ela dá contexto histórico e não substitui a faixa estatística do detector.</Typography>
    </Paper>
  );
}

/** Série diária com escala, datas e lacunas reais preservadas. */
function SeriesChart({ series, currency }: { series: AnomalySeries; currency: string }) {
  const points = [...series.items].sort((a, b) => a.event_date.localeCompare(b.event_date));
  if (!points.length) return <Alert severity="info">{series.note ?? "Série diária indisponível para este caso."}</Alert>;

  const width = 880;
  const height = 300;
  const plot = { left: 76, right: 18, top: 18, bottom: 42 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const values = points.flatMap((point) => [point.expected_cloud, point.upper_cloud, point.lower_cloud, point.observed_cloud].filter((value): value is number => value !== null && Number.isFinite(value)));
  const rawMin = Math.min(0, ...values);
  const rawMax = Math.max(0, ...values);
  const padding = (rawMax - rawMin || Math.abs(rawMax) || 1) * 0.12;
  const min = rawMin - padding;
  const max = rawMax + padding;
  const y = (value: number) => plot.top + (max - value) / (max - min) * plotHeight;
  const x = (index: number) => plot.left + index * plotWidth / Math.max(1, points.length - 1);
  const segments = <T,>(select: (point: typeof points[number]) => number | null) => {
    const result: Array<Array<{ index: number; value: number }>> = [];
    points.forEach((point, index) => {
      const value = select(point);
      if (value === null || !Number.isFinite(value)) return;
      const last = result[result.length - 1];
      if (last && last[last.length - 1].index === index - 1) last.push({ index, value });
      else result.push([{ index, value }]);
    });
    return result;
  };
  const bandSegments: Array<Array<{ index: number; point: typeof points[number] }>> = [];
  points.forEach((point, index) => {
    const last = bandSegments[bandSegments.length - 1];
    if (last && last[last.length - 1].index === index - 1) last.push({ index, point });
    else bandSegments.push([{ index, point }]);
  });
  const ticks = Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4);
  const labelIndexes = points.length <= 8 ? points.map((_, index) => index) : Array.from(new Set([0, Math.round((points.length - 1) / 3), Math.round(2 * (points.length - 1) / 3), points.length - 1]));

  return (
    <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2.5 }, borderRadius: 2.5, borderColor: "#e5eaf0" }}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "center" }} spacing={1} sx={{ mb: 1.5 }}>
        <Box><Typography variant="subtitle1" fontWeight={700}>Custo cloud ao longo do tempo</Typography><Typography variant="caption" color="text.secondary">Consumo diário comparado à referência anterior ao evento</Typography></Box>
        <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
          {[[chartColors.observed, "Observado"], [chartColors.expected, "Esperado"], [chartColors.band, "Faixa esperada"], [chartColors.anomaly, "Acima do limite"]].map(([color, label]) => <Stack key={label} direction="row" spacing={0.6} alignItems="center"><Box sx={{ width: 12, height: 3, bgcolor: color, borderRadius: 3 }} /><Typography variant="caption" color="text.secondary">{label}</Typography></Stack>)}
        </Stack>
      </Stack>
      <Box sx={{ width: "100%", overflow: "hidden" }}>
        <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Série diária de custo cloud observado, esperado e intervalo esperado" style={{ display: "block", fontFamily: "inherit" }}>
          {ticks.map((tick) => <g key={tick}><line x1={plot.left} x2={width - plot.right} y1={y(tick)} y2={y(tick)} stroke={chartColors.grid} /><text x={plot.left - 10} y={y(tick) + 4} textAnchor="end" fontSize="11" fill={chartColors.axis}>{money(tick, currency)}</text></g>)}
          {bandSegments.map((segment, index) => <polygon key={`band-${index}`} points={`${segment.map(({ index: i, point }) => `${x(i)},${y(point.upper_cloud)}`).join(" ")} ${[...segment].reverse().map(({ index: i, point }) => `${x(i)},${y(point.lower_cloud)}`).join(" ")}`} fill={chartColors.band} opacity={0.8} />)}
          {segments((point) => point.expected_cloud).map((segment, index) => <polyline key={`expected-${index}`} points={segment.map(({ index: i, value }) => `${x(i)},${y(value)}`).join(" ")} fill="none" stroke={chartColors.expected} strokeWidth={2} strokeDasharray="5 5" />)}
          {segments((point) => point.upper_cloud).map((segment, index) => <polyline key={`upper-${index}`} points={segment.map(({ index: i, value }) => `${x(i)},${y(value)}`).join(" ")} fill="none" stroke={chartColors.limit} strokeWidth={1.2} strokeDasharray="3 5" opacity={0.85} />)}
          {segments((point) => point.observed_cloud).map((segment, index) => <polyline key={`observed-${index}`} points={segment.map(({ index: i, value }) => `${x(i)},${y(value)}`).join(" ")} fill="none" stroke={chartColors.observed} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />)}
          {points.map((point, index) => point.observed_cloud === null ? null : <g key={point.event_date}><circle cx={x(index)} cy={y(point.observed_cloud)} r={point.anomalous_cost_cloud ? 5 : 3.5} fill={point.anomalous_cost_cloud ? chartColors.anomaly : chartColors.observed} stroke="white" strokeWidth={1.5}><title>{`${point.event_date} · observado ${money(point.observed_cloud, currency)} · esperado ${money(point.expected_cloud, currency)} · anômalo ${money(point.anomalous_cost_cloud, currency)}`}</title></circle>{(point.anomalous_cost_cloud ?? 0) > 0 && <line x1={x(index)} x2={x(index)} y1={y(point.observed_cloud)} y2={y(point.upper_cloud)} stroke={chartColors.anomaly} strokeWidth={4} strokeLinecap="round" />}</g>)}
          {labelIndexes.map((index) => <text key={points[index].event_date} x={x(index)} y={height - 12} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"} fontSize="11" fill={chartColors.axis}>{new Date(`${points[index].event_date}T00:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", timeZone: "UTC" })}</text>)}
        </svg>
      </Box>
      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
        <Chip size="small" variant="outlined" label={`${points.length} dias no período`} />
        {series.data_cutoff && <Chip size="small" variant="outlined" label={`Dados até ${series.data_cutoff}`} />}
        {(series.distribution_methods ?? []).map((method) => <Chip key={method} size="small" variant="outlined" label={`Distribuição: ${method}`} />)}
        {(series.band_methods ?? []).map((method) => <Chip key={method} size="small" variant="outlined" label={`Faixa: ${method}`} />)}
      </Stack>
    </Paper>
  );
}

/** Comparação de um evento: uma escala de valor, sem sugerir série temporal. */
function BandChart({ kase, currency }: { kase: AnomalyCase; currency: string }) {
  const observed = kase.observed_cloud;
  const expected = kase.expected_cloud;
  const upper = kase.upper_cloud ?? expected;
  const lower = kase.lower_cloud ?? expected;
  const values = [observed, expected, upper, lower].filter((value): value is number => value !== null && Number.isFinite(value));
  if (observed === null || expected === null || upper === null || lower === null || values.length === 0) return <Alert severity="info">Valores insuficientes para comparar o evento à referência.</Alert>;
  const rawMin = Math.min(0, ...values);
  const rawMax = Math.max(0, ...values);
  const padding = (rawMax - rawMin || Math.abs(rawMax) || 1) * 0.15;
  const min = rawMin - padding;
  const max = rawMax + padding;
  const left = 54;
  const right = 850;
  const x = (value: number) => left + (value - min) / (max - min) * (right - left);
  const anomalous = Math.max(0, observed - upper);
  const ticks = Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4);
  return (
    <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2.5 }, borderRadius: 2.5, borderColor: "#e5eaf0" }}>
      <Typography variant="subtitle1" fontWeight={700}>Comparação do evento</Typography>
      <Typography variant="caption" color="text.secondary">Valor observado em relação à expectativa e à faixa de tolerância</Typography>
      <Box sx={{ mt: 2 }}><svg width="100%" viewBox="0 0 880 176" role="img" aria-label="Comparação de custo observado com expectativa e faixa esperada" style={{ display: "block", fontFamily: "inherit" }}>
        {ticks.map((tick) => <g key={tick}><line x1={x(tick)} x2={x(tick)} y1={22} y2={130} stroke={chartColors.grid} /><text x={x(tick)} y={153} textAnchor="middle" fontSize="11" fill={chartColors.axis}>{money(tick, currency)}</text></g>)}
        <line x1={left} x2={right} y1={82} y2={82} stroke="#aeb8c5" strokeWidth={2} />
        <line x1={x(lower)} x2={x(upper)} y1={82} y2={82} stroke={chartColors.limit} strokeWidth={18} strokeLinecap="round" opacity={0.28} />
        <line x1={x(expected)} x2={x(expected)} y1={48} y2={116} stroke={chartColors.expected} strokeWidth={2} strokeDasharray="4 4" />
        {anomalous > 0 && <line x1={x(upper)} x2={x(observed)} y1={82} y2={82} stroke={chartColors.anomaly} strokeWidth={7} strokeLinecap="round" />}
        <circle cx={x(observed)} cy={82} r={8} fill={anomalous > 0 ? chartColors.anomaly : chartColors.observed} stroke="white" strokeWidth={3}><title>Observado: {money(observed, currency)}</title></circle>
        <text x={x(lower)} y={43} textAnchor="middle" fontSize="11" fill={chartColors.limit}>Limite inferior</text><text x={x(upper)} y={43} textAnchor="middle" fontSize="11" fill={chartColors.limit}>Limite superior</text><text x={x(expected)} y={125} textAnchor="middle" fontSize="11" fill={chartColors.expected}>Esperado</text>
      </svg></Box>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
        <Chip size="small" variant="outlined" label={`Observado ${money(observed, currency)}`} />
        <Chip size="small" variant="outlined" label={`Esperado ${money(expected, currency)}`} />
        <Chip size="small" color={anomalous > 0 ? "error" : "default"} label={`Custo anômalo ${money(anomalous, currency)}`} />
        <Chip size="small" variant="outlined" label={`Referência ${kase.reference_kind ?? "—"}`} />
        <Chip size="small" variant="outlined" label={`Grão ${kase.evaluation_grain}`} />
      </Stack>
    </Paper>
  );
}

function StepPanel({ kase }: { kase: AnomalyCase }) {
  const steps = (kase.steps ?? []).filter((step) => "etapa" in step);
  const next = (kase.steps ?? []).find((step) => "proxima_acao" in step);
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>Etapas do caso</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Etapa</TableCell>
            <TableCell>Quando</TableCell>
            <TableCell>Responsável</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {steps.map((step, index) => (
            <TableRow key={index}>
              <TableCell>{String(step.etapa)}</TableCell>
              <TableCell>{dt(step.quando as string | null)}</TableCell>
              <TableCell>
                {step.responsavel ? `usuário ${step.responsavel}` : "—"}
                {step.decisao ? ` · ${DECISION_LABEL[String(step.decisao)] ?? step.decisao}` : ""}
                {step.acoes_abertas ? ` · ${step.acoes_abertas} ação(ões) aberta(s)` : ""}
                {step.dias_validos ? ` · ${step.dias_validos} dia(s) válido(s)` : ""}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Divider sx={{ my: 1.5 }} />
      <Stack direction="row" spacing={2} flexWrap="wrap">
        <Typography variant="body2">
          <strong>Prazo de ciência:</strong> {dt(kase.due_at)}
        </Typography>
        <Typography variant="body2">
          <strong>Próxima ação:</strong> {next ? String(next.proxima_acao) : "—"}
        </Typography>
        <Typography variant="body2">
          <strong>Responsável:</strong> {kase.assignee_user_id ? `usuário ${kase.assignee_user_id}` : "fila FinOps"}
        </Typography>
        <Typography variant="body2">
          <strong>Confiança:</strong> {kase.confidence}
        </Typography>
        <Typography variant="body2">
          <strong>Qualidade:</strong> {kase.data_quality}
        </Typography>
      </Stack>
    </Paper>
  );
}

type ExpectedEventFamiliesResult = { items: ExpectedEventFamily[]; total: number; note?: string };

type TransitionForm = {
  command: string;
  decision: string;
  cause_category: string;
  note: string;
  evidence: string;
  verification_outcome: string;
  learning: string;
  preventive_measure: string;
  no_preventive_measure_reason: string;
  assignee_user_id: string;
  action_description: string;
};

const EMPTY_FORM: TransitionForm = {
  command: "acknowledge",
  decision: "CONFIRMED_ACCEPTED",
  cause_category: "UNDETERMINED",
  note: "",
  evidence: "",
  verification_outcome: "SUCCESS",
  learning: "",
  preventive_measure: "",
  no_preventive_measure_reason: "",
  assignee_user_id: "",
  action_description: "",
};

export default function Anomalies() {
  const [tab, setTab] = useState(0);
  const [configs, setConfigs] = useState<AnomalyConfig[]>([]);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [cases, setCases] = useState<AnomalyCase[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<AnomalyCase | null>(null);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [series, setSeries] = useState<AnomalySeries | null>(null);
  const [expectedEvents, setExpectedEvents] = useState<ExpectedEventFamiliesResult>({ items: [], total: 0 });
  const [quality, setQuality] = useState<QualityBucket[]>([]);
  const [qualityNote, setQualityNote] = useState<string | null>(null);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<AnomalyNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [form, setForm] = useState<TransitionForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stateFilter, setStateFilter] = useState<string>("");
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [queueOnly, setQueueOnly] = useState(false);

  const config = configs[0];
  const currency = config?.currency ?? "BRL";

  const loadAll = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [configList, metrics, notificationPage] = await Promise.all([
        listAnomalyConfigs(),
        getAnomalyMetrics(),
        listAnomalyNotifications({ limit: 25 }),
      ]);
      setConfigs(configList);
      setSummary(metrics);
      setNotifications(notificationPage.items);
      setUnread(notificationPage.unread);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao carregar anomalias");
    } finally {
      setBusy(false);
    }
  }, []);

  const loadExpected = useCallback(async () => {
    try {
      setExpectedEvents(await getExpectedEvents({ limit: 200 }));
      setRemoteError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Falha ao listar eventos esperados";
      setExpectedEvents({ items: [], total: 0, note: message.includes("409")
        ? "Nenhuma publicação corrente: execute a detecção para gerar avaliações."
        : message });
      setRemoteError(null);
    }
  }, []);

  const loadQuality = useCallback(async () => {
    try {
      const payload = await getQuality({ limit: 200 });
      setQuality(payload.items);
      setQualityNote(payload.note ?? null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Falha ao listar qualidade";
      setQuality([]);
      setQualityNote(message.includes("409")
        ? "Nenhuma publicação corrente: execute a detecção para gerar avaliações."
        : message);
    }
  }, []);

  const loadCases = useCallback(async () => {
    setBusy(true);
    try {
      const params: Record<string, unknown> = { limit: 50, order_by: "score" };
      if (stateFilter) params.state = [stateFilter];
      if (severityFilter) params.severity = [severityFilter];
      if (queueOnly) params.queue_only = true;
      const page = await listAnomalyCases(params);
      setCases(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao listar casos");
    } finally {
      setBusy(false);
    }
  }, [stateFilter, severityFilter, queueOnly]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  useEffect(() => {
    if (tab === 3) void loadExpected();
    if (tab === 4) void loadQuality();
  }, [tab, loadExpected, loadQuality]);

  const openCase = async (id: number) => {
    setBusy(true);
    try {
      const [detail, eventPage] = await Promise.all([getAnomalyCase(id), getAnomalyCaseEvents(id)]);
      setSelected(detail);
      setEvents(eventPage.items);
      setForm(EMPTY_FORM);
      setSeries(null);
      try {
        setSeries(await getAnomalyCaseSeries(id));
      } catch {
        setSeries({ items: [], note: "Série diária indisponível para este caso." });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao abrir o caso");
    } finally {
      setBusy(false);
    }
  };

  const submitCommand = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        command: form.command,
        case_version: selected.case_version,
      };
      if (form.note) payload.note = form.note;
      if (form.evidence) payload.evidence = form.evidence.split("\n").filter(Boolean);
      if (form.command === "decide" || form.command === "redecide") {
        payload.decision = form.decision;
        payload.cause_category = form.cause_category;
      }
      if (form.command === "assign") payload.assignee_user_id = Number(form.assignee_user_id);
      if (form.command === "validate_result") {
        payload.verification_outcome = form.verification_outcome;
        payload.verification_note = form.note || "validado";
      }
      if (form.command === "record_retrospective" || form.command === "close") {
        payload.learning = form.learning;
        if (form.preventive_measure) payload.preventive_measure = form.preventive_measure;
        else payload.no_preventive_measure_reason = form.no_preventive_measure_reason || "sem medida preventiva";
      }
      await transitionAnomalyCase(selected.id, payload);
      await openCase(selected.id);
      await loadCases();
      await loadAll();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Falha no comando";
      setError(message.includes("409") ? `Conflito de versão (409): ${message}` : message);
    } finally {
      setBusy(false);
    }
  };

  const addAction = async () => {
    if (!selected || !form.action_description) return;
    setBusy(true);
    try {
      await createAnomalyAction(selected.id, {
        description: form.action_description,
        action_type: "CORRECTIVE",
        mandatory: true,
      });
      await openCase(selected.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao criar ação");
    } finally {
      setBusy(false);
    }
  };

  const completeAction = async (actionId: number) => {
    if (!selected) return;
    setBusy(true);
    try {
      await updateAnomalyAction(actionId, { status: "COMPLETED" });
      await openCase(selected.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao concluir ação");
    } finally {
      setBusy(false);
    }
  };

  const readNotification = async (id: number) => {
    await readAnomalyNotification(id, true);
    const page = await listAnomalyNotifications({ limit: 25 });
    setNotifications(page.items);
    setUnread(page.unread);
  };

  const toggleEnabled = async () => {
    if (!config) return;
    setBusy(true);
    try {
      await updateAnomalyConfig(config.id, { enabled: !config.enabled });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao atualizar a configuração");
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    if (!config) return;
    setBusy(true);
    try {
      await enqueueAnomalyRun(config.id);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao enfileirar a detecção");
    } finally {
      setBusy(false);
    }
  };

  const retrospectiveCases = useMemo(
    () => cases.filter((item) => item.state === "RETROSPECTIVE" || item.state === "CLOSED"),
    [cases],
  );

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 1 }}>
        <Typography variant="h5">Anomalias de custo cloud</Typography>
        {busy && <CircularProgress size={18} />}
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title="Caixa de notificações">
          <IconButton onClick={() => setInboxOpen(true)}>
            <NotificationsIcon />
            {unread > 0 && <Chip size="small" color="error" label={unread} sx={{ ml: -1, mt: -2 }} />}
          </IconButton>
        </Tooltip>
        <Button size="small" startIcon={<RefreshIcon />}
                onClick={() => { void loadAll(); void loadCases(); void loadExpected(); void loadQuality(); }}>
          Atualizar
        </Button>
        {config && (
          <>
            <Chip
              size="small"
              color={config.enabled ? "success" : "default"}
              label={config.enabled ? "Habilidada" : "Desabilitada"}
            />
            <Button size="small" onClick={toggleEnabled}>
              {config.enabled ? "Desabilitar" : "Habilitar"}
            </Button>
            <Button size="small" startIcon={<PlayArrowIcon />} onClick={runNow} disabled={!config.enabled}>
              Executar detecção
            </Button>
          </>
        )}
      </Stack>

      {config && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Medida <strong>{config.measure_column}</strong> · moeda <strong>{config.currency}</strong> ·
          perfil {config.profile} · quantil {(config.daily_quantile * 100).toFixed(1)}% · materialidade{" "}
          {money(config.materiality_by_currency?.[config.currency] ?? 100, config.currency)} · janela{" "}
          {config.history_days}d (calibração {config.calibration_days}d) · horizonte {config.horizon_days}d
        </Typography>
      )}
      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError(null)}>{error}</Alert>}

      <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{ mb: 1 }}>
        <Tab label="Visão geral" />
        <Tab label="Meus casos / fila FinOps" />
        <Tab label="Casos" />
        <Tab label="Eventos esperados" />
        <Tab label="Qualidade" />
        <Tab label="Retrospectiva" />
      </Tabs>

      {tab === 0 && summary && (
        <Box>
          <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 2 }}>
            {[
              { label: "Casos novos", value: summary.new },
              { label: "Abertos", value: summary.open },
              { label: "Vencidos", value: summary.overdue },
              { label: "Encerrados", value: summary.closed },
              { label: "Sem responsável", value: summary.unassigned },
              { label: "Custo anômalo observado", value: money(summary.anomalous_cost_cloud, currency) },
              { label: "Tempo até ciência (h)", value: summary.median_acknowledgement_hours ?? "—" },
              { label: "Tempo até resolução (h)", value: summary.median_resolution_hours ?? "—" },
              { label: "Classificados", value: summary.classified },
              { label: "Famílias reincidentes", value: summary.recurrence_families },
            ].map((item) => (
              <Paper key={item.label} variant="outlined" sx={{ p: 1.5, minWidth: 150 }}>
                <Typography variant="caption" color="text.secondary">{item.label}</Typography>
                <Typography variant="h6">{item.value}</Typography>
              </Paper>
            ))}
          </Stack>
          <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>Distribuição por estado</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {Object.entries(summary.by_state).map(([state, count]) => (
                <Chip key={state} label={`${STATE_LABEL[state] ?? state}: ${count}`} size="small" />
              ))}
              {Object.keys(summary.by_state).length === 0 && (
                <Typography variant="body2" color="text.secondary">Nenhum caso registrado ainda.</Typography>
              )}
            </Stack>
            <Divider sx={{ my: 1.5 }} />
            <Typography variant="subtitle2" gutterBottom>Decisões</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {Object.entries(summary.decisions).map(([decision, count]) => (
                <Chip key={decision} size="small"
                      label={`${DECISION_LABEL[decision] ?? decision}: ${count}`} />
              ))}
            </Stack>
            <Alert severity="info" sx={{ mt: 2 }}>
              {summary.notes.join(" · ")}
            </Alert>
          </Paper>
        </Box>
      )}

      {(tab === 1 || tab === 2) && (
        <Box>
          <Stack direction="row" spacing={2} sx={{ mb: 1 }} flexWrap="wrap">
            <TextField select size="small" label="Estado" value={stateFilter}
                       onChange={(event) => setStateFilter(event.target.value)} sx={{ minWidth: 170 }}>
              <MenuItem value="">Todos</MenuItem>
              {Object.keys(STATE_LABEL).map((state) => (
                <MenuItem key={state} value={state}>{STATE_LABEL[state]}</MenuItem>
              ))}
            </TextField>
            <TextField select size="small" label="Severidade" value={severityFilter}
                       onChange={(event) => setSeverityFilter(event.target.value)} sx={{ minWidth: 150 }}>
              <MenuItem value="">Todas</MenuItem>
              {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((severity) => (
                <MenuItem key={severity} value={severity}>{severity}</MenuItem>
              ))}
            </TextField>
            {tab === 1 && (
              <Button size="small" variant={queueOnly ? "contained" : "outlined"}
                      onClick={() => setQueueOnly((value) => !value)}>
                Somente fila FinOps
              </Button>
            )}
            <Typography variant="body2" sx={{ alignSelf: "center" }}>
              {total} caso(s)
            </Typography>
          </Stack>
          <Paper variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>#</TableCell>
                  <TableCell>Nota</TableCell>
                  <TableCell>Severidade</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Evento</TableCell>
                  <TableCell>Data</TableCell>
                  <TableCell align="right">Observado</TableCell>
                  <TableCell align="right">Esperado</TableCell>
                  <TableCell align="right">Custo anômalo</TableCell>
                  <TableCell>Responsável</TableCell>
                  <TableCell>Prazo</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {cases.map((item) => (
                  <TableRow key={item.id} hover sx={{ cursor: "pointer" }}
                            onClick={() => void openCase(item.id)}>
                    <TableCell>{item.id}</TableCell>
                    <TableCell>{item.score.toFixed(0)}</TableCell>
                    <TableCell>
                      <Chip size="small" color={SEVERITY_COLOR[item.severity] ?? "default"}
                            label={item.severity} />
                    </TableCell>
                    <TableCell>{STATE_LABEL[item.state] ?? item.state}</TableCell>
                    <TableCell>{item.event_family}</TableCell>
                    <TableCell>{item.event_date}</TableCell>
                    <TableCell align="right">{money(item.observed_cloud, currency)}</TableCell>
                    <TableCell align="right">{money(item.expected_cloud, currency)}</TableCell>
                    <TableCell align="right">{money(item.anomalous_cost_cloud, currency)}</TableCell>
                    <TableCell>{item.assignee_user_id ?? "fila FinOps"}</TableCell>
                    <TableCell>{dt(item.due_at)}</TableCell>
                  </TableRow>
                ))}
                {cases.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={11}>
                      <Typography variant="body2" color="text.secondary">
                        Nenhum caso. Habilite a configuração e execute a detecção.
                      </Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Paper>
        </Box>
      )}

      {tab === 3 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Eventos esperados reconhecidos (permanecem na tabela analítica, sem workflow)
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {expectedEvents.total} avaliação(ões) de evento esperado na publicação corrente
            {expectedEvents.note ? ` · ${expectedEvents.note}` : ""}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Família (recorrência)</TableCell>
                <TableCell align="right">Ocorrências</TableCell>
                <TableCell align="right">Séries</TableCell>
                <TableCell align="right">Esperado</TableCell>
                <TableCell align="right">Observado</TableCell>
                <TableCell align="right">Desvio</TableCell>
                <TableCell>Meses</TableCell>
                <TableCell>Confiança</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {expectedEvents.items.map((item) => (
                <TableRow key={item.family}>
                  <TableCell>{item.family}</TableCell>
                  <TableCell align="right">{item.occurrences}</TableCell>
                  <TableCell align="right">{item.series_count}</TableCell>
                  <TableCell align="right">{money(item.expected_cloud, currency)}</TableCell>
                  <TableCell align="right">{money(item.observed_cloud, currency)}</TableCell>
                  <TableCell align="right">{money(item.delta_cloud, currency)}</TableCell>
                  <TableCell>{item.months.join(", ")}</TableCell>
                  <TableCell>{item.confidence.join(", ") || "—"}</TableCell>
                </TableRow>
              ))}
              {expectedEvents.items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8}>
                    <Typography variant="body2" color="text.secondary">
                      Nenhum evento esperado publicado.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {tab === 4 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Qualidade dos dados</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Dados parciais, referências reconstruídas, tolerância explícita e ausência de coleta.
            Dado parcial nunca confirma recuperação nem encerra caso.
            {qualityNote ? ` ${qualityNote}` : ""}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Código</TableCell>
                <TableCell align="right">Avaliações</TableCell>
                <TableCell align="right">Custo anômalo</TableCell>
                <TableCell>Períodos</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {quality.map((bucket) => (
                <TableRow key={bucket.reason_code}>
                  <TableCell>{bucket.reason_code}</TableCell>
                  <TableCell align="right">{bucket.count}</TableCell>
                  <TableCell align="right">{money(bucket.anomalous_cost_cloud, currency)}</TableCell>
                  <TableCell>{bucket.periods.join(", ") || "—"}</TableCell>
                  <TableCell>{bucket.statuses.join(", ") || "—"}</TableCell>
                </TableRow>
              ))}
              {quality.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5}>
                    <Typography variant="body2" color="text.secondary">
                      Nenhuma limitação declarada nas avaliações publicadas.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {tab === 5 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Retrospectiva</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Causas, decisões e aprendizados registrados. Não se atribui economia causal não comprovada.
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell>
                <TableCell>Causa</TableCell>
                <TableCell>Decisão</TableCell>
                <TableCell>Aprendizado</TableCell>
                <TableCell>Medida preventiva</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {retrospectiveCases.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>#{item.id}</TableCell>
                  <TableCell>{item.cause_category ? CAUSE_LABEL[item.cause_category] ?? item.cause_category : "—"}</TableCell>
                  <TableCell>{item.decision ? DECISION_LABEL[item.decision] ?? item.decision : "—"}</TableCell>
                  <TableCell>{item.learning ?? "—"}</TableCell>
                  <TableCell>{item.preventive_measure ?? item.no_preventive_measure_reason ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Drawer anchor="right" open={Boolean(selected)} onClose={() => setSelected(null)}
              PaperProps={{ sx: { width: "min(1100px, 96vw)", p: 2 } }}>
        {selected && (
          <Box>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography variant="h6">Caso #{selected.id}</Typography>
              <Chip size="small" color={SEVERITY_COLOR[selected.severity] ?? "default"}
                    label={`${selected.severity} · nota ${selected.score.toFixed(0)}`} />
              <Chip size="small" label={STATE_LABEL[selected.state] ?? selected.state} />
              <Chip size="small" label={`confiança ${selected.confidence}`} />
              <Box sx={{ flexGrow: 1 }} />
              <IconButton onClick={() => setSelected(null)}><CloseIcon /></IconButton>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {selected.event_family} · {selected.event_date} · competência de origem{" "}
              {selected.source_billing_period ?? "—"} · período {selected.period}
            </Typography>
            {busy && <LinearProgress sx={{ mb: 1 }} />}

            {series && <ContextChart series={series} kase={selected} currency={currency} />}
            {series && series.items.length > 0 ? (
              <SeriesChart series={series} currency={currency} />
            ) : (
              <>
                {series?.note && <Alert severity="info" sx={{ mb: 1 }}>{series.note}</Alert>}
                <BandChart kase={selected} currency={currency} />
              </>
            )}
            <Divider sx={{ my: 2 }} />
            <StepPanel kase={selected} />

            <Divider sx={{ my: 2 }} />
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1 }}>
              {selected.reason_codes.map((code) => (
                <Chip key={code} size="small" variant="outlined" label={code} />
              ))}
              {selected.applied_rule_refs.map((rule, index) => (
                <Tooltip key={index} title={rule.status}>
                  <Chip size="small" label={`regra: ${rule.name}`} />
                </Tooltip>
              ))}
            </Stack>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Ações (remediação)</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Descrição</TableCell>
                  <TableCell>Tipo</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Prazo</TableCell>
                  <TableCell>Executada</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {(selected.actions ?? []).map((action) => (
                  <TableRow key={action.id}>
                    <TableCell>{action.description}</TableCell>
                    <TableCell>{action.action_type}</TableCell>
                    <TableCell>{action.status}</TableCell>
                    <TableCell>{dt(action.due_at)}</TableCell>
                    <TableCell>{dt(action.executed_at)}</TableCell>
                    <TableCell>
                      {action.status !== "COMPLETED" && (
                        <Button size="small" onClick={() => void completeAction(action.id)}>
                          Concluir
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {(selected.actions ?? []).length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6}>
                      <Typography variant="body2" color="text.secondary">Nenhuma ação cadastrada.</Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField size="small" label="Nova ação corretiva" fullWidth
                         value={form.action_description}
                         onChange={(event) => setForm({ ...form, action_description: event.target.value })} />
              <Button variant="outlined" onClick={addAction}>Adicionar</Button>
            </Stack>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Comando do workflow</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1 }}>
              <TextField select size="small" label="Comando" value={form.command}
                         onChange={(event) => setForm({ ...form, command: event.target.value })}
                         sx={{ minWidth: 220 }}>
                {["assign", "acknowledge", "return_to_queue", "start_investigation", "update_investigation",
                  "decide", "redecide", "start_remediation", "cancel_remediation", "start_verification",
                  "fail_verification", "validate_result", "record_retrospective", "close", "reopen"]
                  .map((command) => <MenuItem key={command} value={command}>{command}</MenuItem>)}
              </TextField>
              {form.command === "assign" && (
                <TextField size="small" label="Responsável (user id)" value={form.assignee_user_id}
                           onChange={(event) => setForm({ ...form, assignee_user_id: event.target.value })} />
              )}
              {(form.command === "decide" || form.command === "redecide") && (
                <>
                  <TextField select size="small" label="Decisão" value={form.decision}
                             onChange={(event) => setForm({ ...form, decision: event.target.value })}
                             sx={{ minWidth: 220 }}>
                    {Object.keys(DECISION_LABEL).map((decision) => (
                      <MenuItem key={decision} value={decision}>{DECISION_LABEL[decision]}</MenuItem>
                    ))}
                  </TextField>
                  <TextField select size="small" label="Causa" value={form.cause_category}
                             onChange={(event) => setForm({ ...form, cause_category: event.target.value })}
                             sx={{ minWidth: 220 }}>
                    {Object.keys(CAUSE_LABEL).map((cause) => (
                      <MenuItem key={cause} value={cause}>{CAUSE_LABEL[cause]}</MenuItem>
                    ))}
                  </TextField>
                </>
              )}
              {form.command === "validate_result" && (
                <TextField select size="small" label="Resultado" value={form.verification_outcome}
                           onChange={(event) => setForm({ ...form, verification_outcome: event.target.value })}>
                  <MenuItem value="SUCCESS">Sucesso</MenuItem>
                  <MenuItem value="CONFIRMED_ABSENCE">Ausência confirmada</MenuItem>
                </TextField>
              )}
            </Stack>
            <TextField size="small" fullWidth multiline minRows={2} label="Justificativa / nota"
                       value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })}
                       sx={{ mb: 1 }} />
            {(form.command === "decide" || form.command === "redecide") && (
              <TextField size="small" fullWidth multiline minRows={2} label="Evidências (uma por linha)"
                         value={form.evidence}
                         onChange={(event) => setForm({ ...form, evidence: event.target.value })}
                         sx={{ mb: 1 }} />
            )}
            {(form.command === "record_retrospective" || form.command === "close") && (
              <>
                <TextField size="small" fullWidth label="Aprendizado" value={form.learning}
                           onChange={(event) => setForm({ ...form, learning: event.target.value })}
                           sx={{ mb: 1 }} />
                <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                  <TextField size="small" fullWidth label="Medida preventiva"
                             value={form.preventive_measure}
                             onChange={(event) => setForm({ ...form, preventive_measure: event.target.value })} />
                  <TextField size="small" fullWidth label="Justificativa de ausência"
                             value={form.no_preventive_measure_reason}
                             onChange={(event) => setForm({ ...form, no_preventive_measure_reason: event.target.value })} />
                </Stack>
              </>
            )}
            <Button variant="contained" onClick={submitCommand} disabled={busy}>
              Aplicar comando
            </Button>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Histórico de eventos</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Quando</TableCell>
                  <TableCell>Evento</TableCell>
                  <TableCell>De → Para</TableCell>
                  <TableCell>Versão</TableCell>
                  <TableCell>Nota</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {events.map((event) => (
                  <TableRow key={String(event.id)}>
                    <TableCell>{dt(event.created_at as string)}</TableCell>
                    <TableCell>{String(event.event_type)}{event.command ? ` (${event.command})` : ""}</TableCell>
                    <TableCell>{String(event.from_state ?? "—")} → {String(event.to_state ?? "—")}</TableCell>
                    <TableCell>{String(event.case_version)}</TableCell>
                    <TableCell>{String(event.note ?? "—")}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Drawer>

      <Drawer anchor="right" open={inboxOpen} onClose={() => setInboxOpen(false)}
              PaperProps={{ sx: { width: "min(520px, 96vw)", p: 2 } }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="h6">Notificações</Typography>
          <Chip size="small" color={unread ? "error" : "default"} label={`${unread} não lida(s)`} />
          <Box sx={{ flexGrow: 1 }} />
          <IconButton onClick={() => setInboxOpen(false)}><CloseIcon /></IconButton>
        </Stack>
        <Divider sx={{ my: 1 }} />
        {notifications.map((notification) => (
          <Paper key={notification.id} variant="outlined" sx={{ p: 1, mb: 1,
                  bgcolor: notification.read_at ? "transparent" : "action.hover" }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={notification.kind} />
              {notification.is_finops_queue && <Chip size="small" color="warning" label="fila FinOps" />}
              <Box sx={{ flexGrow: 1 }} />
              <Typography variant="caption">{dt(notification.created_at)}</Typography>
            </Stack>
            <Typography variant="body2" sx={{ mt: 0.5 }}>{notification.title}</Typography>
            {notification.body && (
              <Typography variant="caption" color="text.secondary">{notification.body}</Typography>
            )}
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
              <Button size="small" onClick={() => void readNotification(notification.id)}>
                Assumir / confirmar ciência
              </Button>
              <Button size="small" onClick={() => { setInboxOpen(false); void openCase(notification.case_id); }}>
                Abrir caso
              </Button>
            </Stack>
          </Paper>
        ))}
        {notifications.length === 0 && (
          <Typography variant="body2" color="text.secondary">Nenhuma notificação.</Typography>
        )}
      </Drawer>
    </Box>
  );
}
