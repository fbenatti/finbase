import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, CircularProgress, FormControl, InputLabel, MenuItem,
  Paper, Select, Stack, Typography,
} from "@mui/material";
import TimelineOutlined from "@mui/icons-material/TimelineOutlined";
import { api, getConnections, type SourceConnection } from "../../services/api";

type MetricRow = {
  resource_id: string;
  metric: string;
  source_metric_name: string;
  bucket_start_utc: string;
  value_average?: number | null;
  value_minimum?: number | null;
  value_maximum?: number | null;
  native_p95?: number | null;
  resolution_seconds: number;
  origin_kind: string;
};
type Profile = {
  p90: number | null;
  p95: number | null;
  p99: number | null;
  value_max: number | null;
  coverage: number;
  largest_gap_seconds: number;
  samples: number;
  percentile_basis: string;
  quantile_method: string;
};
type MetricConfig = { enabled?: boolean; resource_ids?: string[]; metric_names?: string[] };

const localInput = (date: Date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};
const asUtc = (value: string) => new Date(value).toISOString();
const numeric = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function MetricSparkline({ rows }: { rows: MetricRow[] }) {
  const points = rows.filter(row => numeric(row.value_average)).slice().sort(
    (a, b) => Date.parse(a.bucket_start_utc) - Date.parse(b.bucket_start_utc),
  );
  if (points.length < 2) return <Box sx={{ py: 6, textAlign: "center", color: "text.secondary" }}>Ainda não há pontos suficientes para desenhar a série.</Box>;
  const values = points.map(row => row.value_average as number);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, Math.abs(max) * 0.05, 1e-9);
  const coords = values.map((value, index) => `${28 + index * 744 / (values.length - 1)},${154 - (value - min) * 128 / span}`).join(" ");
  return <Box sx={{ width: "100%", overflow: "hidden" }}>
    <svg viewBox="0 0 800 190" role="img" aria-label="Média observada por bucket" style={{ width: "100%", height: 230 }}>
      <line x1="28" y1="26" x2="772" y2="26" stroke="#e4e9ed" />
      <line x1="28" y1="90" x2="772" y2="90" stroke="#e4e9ed" />
      <line x1="28" y1="154" x2="772" y2="154" stroke="#e4e9ed" />
      <polyline points={coords} fill="none" stroke="#087f8c" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
      <text x="28" y="178" fill="#66747d" fontSize="11">{new Date(points[0].bucket_start_utc).toLocaleString()}</text>
      <text x="772" y="178" textAnchor="end" fill="#66747d" fontSize="11">{new Date(points[points.length - 1].bucket_start_utc).toLocaleString()}</text>
      <text x="28" y="17" fill="#66747d" fontSize="11">máx. {max.toPrecision(4)}</text>
      <text x="28" y="148" fill="#66747d" fontSize="11">mín. {min.toPrecision(4)}</text>
    </svg>
  </Box>;
}

export default function InfrastructureMetrics() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [start, setStart] = useState(() => localInput(new Date(Date.now() - 24 * 60 * 60_000)));
  const [end, setEnd] = useState(() => localInput(new Date()));
  const [resolution, setResolution] = useState(60);
  const [resourceId, setResourceId] = useState("");
  const [metric, setMetric] = useState("");
  const [rows, setRows] = useState<MetricRow[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [coverageRows, setCoverageRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [profiling, setProfiling] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    getConnections().then(items => {
      const oci = items.filter(item => item.connector_type === "oci");
      if (!active) return;
      setConnections(oci);
      setConnectionId(current => current || String(oci[0]?.id ?? ""));
    }).catch(() => { if (active) setError("Não foi possível carregar as conexões OCI."); });
    return () => { active = false; };
  }, []);

  const selected = connections.find(item => String(item.id) === connectionId);
  const metricConfig = (selected?.config_json?.infrastructure_metrics || {}) as MetricConfig;
  const resources = metricConfig.enabled ? metricConfig.resource_ids || [] : [];
  const metrics = metricConfig.enabled ? metricConfig.metric_names || [] : [];

  useEffect(() => {
    if (!resources.includes(resourceId)) setResourceId(resources[0] || "");
    if (!metrics.includes(metric)) setMetric(metrics[0] || "");
  }, [connectionId, resources.join("|"), metrics.join("|")]);

  useEffect(() => {
    if (!connectionId || !start || !end) {
      setRows([]); setProfiles([]); setCoverageRows([]); return;
    }
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError(""); setMessage("");
    const params = {
      connection_id: Number(connectionId), start_utc: asUtc(start), end_utc: asUtc(end),
      resource_id: resourceId || undefined, source_metric_name: metric || undefined, resolution_seconds: resolution,
    };
    Promise.all([
      api.get("/custos/metricas-infra/series", { params, signal: controller.signal }),
      api.get("/custos/metricas-infra/coverage", { params, signal: controller.signal }),
      api.get("/custos/metricas-infra/perfis", { params: { connection_id: Number(connectionId), start_utc: params.start_utc, end_utc: params.end_utc }, signal: controller.signal }),
    ]).then(([series, coverage, profile]) => {
      if (!active) return;
      setRows((series.data.rows || []).filter((row: MetricRow) => row.resolution_seconds === resolution));
      setCoverageRows((coverage.data.rows || []).filter((row: Record<string, unknown>) => !resourceId || row.resource_id === resourceId));
      setProfiles((profile.data.profiles || []).filter((row: Profile & { source_metric_name?: string; resolution_seconds?: number }) =>
        (!metric || row.source_metric_name === metric) && (!resolution || row.resolution_seconds === resolution),
      ));
    }).catch((reason: unknown) => {
      if (!active || controller.signal.aborted) return;
      setError("A Silver não conseguiu responder. Verifique se há dados para esta conexão e janela.");
      setRows([]); setProfiles([]); setCoverageRows([]);
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [connectionId, start, end, resourceId, metric, resolution]);

  const visibleRows = useMemo(() => rows.slice().sort((a, b) => Date.parse(a.bucket_start_utc) - Date.parse(b.bucket_start_utc)), [rows]);
  const latestProfile = profiles[0];
  const calculate = async () => {
    if (!connectionId || !resourceId || !metric) return;
    setProfiling(true); setError(""); setMessage("");
    try {
      const { data } = await api.post("/custos/metricas-infra/perfis/gerar", {
        connection_id: Number(connectionId), resource_id: resourceId, metric,
        start_utc: asUtc(start), end_utc: asUtc(end), resolution_seconds: resolution,
        value_statistic: "value_average",
      });
      setProfiles(data.profiles || []);
      setMessage(data.coverage === "NO_DATA" ? "Não há amostras para calcular o perfil." : "Perfil calculado sobre os buckets disponíveis e salvo na Silver.");
    } catch {
      setError("Não foi possível calcular o perfil. Confirme se existem buckets na Silver para os filtros selecionados.");
    } finally { setProfiling(false); }
  };

  return <Stack spacing={2.5} sx={{ maxWidth: 1440, mx: "auto", pb: 4 }}>
    <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
      <TimelineOutlined sx={{ color: "#087f8c", fontSize: 32 }} />
      <Box><Typography variant="h4">Consumo de infraestrutura</Typography><Typography color="text.secondary">Séries Silver, cobertura e percentis calculados por janela.</Typography></Box>
    </Box>

    <Paper variant="outlined" sx={{ p: 2.5, borderTop: "3px solid #087f8c" }}>
      <Stack direction={{ xs: "column", lg: "row" }} spacing={2} alignItems={{ lg: "center" }}>
        <FormControl size="small" sx={{ minWidth: 220 }}><InputLabel>Conexão OCI</InputLabel><Select label="Conexão OCI" value={connectionId} onChange={event => setConnectionId(event.target.value)}>{connections.map(item => <MenuItem key={item.id} value={String(item.id)}>{item.name}</MenuItem>)}</Select></FormControl>
        <FormControl size="small" sx={{ minWidth: 190 }} disabled={!resources.length}><InputLabel>Instância</InputLabel><Select label="Instância" value={resourceId} onChange={event => setResourceId(event.target.value)}>{resources.map(id => <MenuItem key={id} value={id}>{id}</MenuItem>)}</Select></FormControl>
        <FormControl size="small" sx={{ minWidth: 190 }} disabled={!metrics.length}><InputLabel>Métrica</InputLabel><Select label="Métrica" value={metric} onChange={event => setMetric(event.target.value)}>{metrics.map(name => <MenuItem key={name} value={name}>{name}</MenuItem>)}</Select></FormControl>
        <FormControl size="small" sx={{ minWidth: 120 }}><InputLabel>Resolução</InputLabel><Select label="Resolução" value={resolution} onChange={event => setResolution(Number(event.target.value))}><MenuItem value={60}>1 minuto</MenuItem><MenuItem value={300}>5 minutos</MenuItem><MenuItem value={900}>15 minutos</MenuItem></Select></FormControl>
        <Box sx={{ display: "flex", gap: 1, flex: 1 }}><Box><Typography variant="caption" color="text.secondary">Início</Typography><input aria-label="Início" type="datetime-local" value={start} onChange={event => setStart(event.target.value)} style={{ display: "block", minHeight: 38, padding: "0 8px", border: "1px solid #c7d0d6", borderRadius: 4, font: "inherit" }} /></Box><Box><Typography variant="caption" color="text.secondary">Fim</Typography><input aria-label="Fim" type="datetime-local" value={end} onChange={event => setEnd(event.target.value)} style={{ display: "block", minHeight: 38, padding: "0 8px", border: "1px solid #c7d0d6", borderRadius: 4, font: "inherit" }} /></Box></Box>
      </Stack>
    </Paper>

    {!selected && <Alert severity="info">Cadastre uma conexão OCI para consultar métricas.</Alert>}
    {selected && !metricConfig.enabled && <Alert severity="info">A coleta de infraestrutura está desativada nesta conexão. Ative a opção na edição da conexão e libere as policies OCI indicadas.</Alert>}
    {error && <Alert severity="error">{error}</Alert>}
    {message && <Alert severity="info">{message}</Alert>}

    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 2.2fr) minmax(280px, 1fr)" }, gap: 2 }}>
      <Paper variant="outlined" sx={{ p: 2.5, minHeight: 330 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="baseline"><Typography variant="h6">Série observada</Typography><Typography variant="body2" color="text.secondary">{visibleRows.length.toLocaleString()} buckets na Silver</Typography></Stack>
        {loading ? <Box sx={{ display: "grid", placeItems: "center", minHeight: 260 }}><CircularProgress size={28} /></Box> : visibleRows.length ? <MetricSparkline rows={visibleRows} /> : <Box sx={{ py: 7, textAlign: "center" }}><Typography color="text.secondary">Sem dados para os filtros selecionados.</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>Abrir esta tela consulta somente a Silver; não inicia coleta na OCI.</Typography></Box>}
        <Typography variant="caption" color="text.secondary">O gráfico mostra value_average por bucket. Percentis nativos do provedor, quando disponíveis, são outra medida.</Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2.5, bgcolor: "#f8fafb" }}>
        <Typography variant="h6" sx={{ mb: 1.5 }}>Perfil da janela</Typography>
        {latestProfile ? <><Typography variant="body2" color="text.secondary">Percentis lineares sobre value_average dos buckets</Typography><Stack direction="row" spacing={2} sx={{ my: 2 }}>{([["P90", latestProfile.p90], ["P95", latestProfile.p95], ["P99", latestProfile.p99]] as const).map(([label, value]) => <Box key={label}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="h5" sx={{ fontVariantNumeric: "tabular-nums" }}>{value == null ? "—" : Number(value).toPrecision(4)}</Typography></Box>)}</Stack><Typography variant="body2">Máximo: {latestProfile.value_max == null ? "—" : Number(latestProfile.value_max).toPrecision(4)}</Typography><Typography variant="body2">Cobertura: {(latestProfile.coverage * 100).toFixed(1)}%</Typography><Typography variant="body2">Maior lacuna: {latestProfile.largest_gap_seconds}s</Typography><Typography variant="body2">Amostras: {latestProfile.samples}</Typography><Button sx={{ mt: 2 }} variant="outlined" onClick={() => void calculate()} disabled={profiling}>{profiling ? "Calculando…" : "Recalcular perfil"}</Button></> : <><Typography color="text.secondary">Ainda não há perfil persistido para este recorte.</Typography><Button sx={{ mt: 2 }} variant="contained" onClick={() => void calculate()} disabled={profiling || !metric || !resourceId}>{profiling ? "Calculando…" : "Calcular P90, P95 e P99"}</Button></>}
        <Alert severity="warning" sx={{ mt: 2 }}>Este P95 resume a janela de buckets. Não é o P95 nativo OCI de cada bucket.</Alert>
      </Paper>
    </Box>

    <Paper variant="outlined" sx={{ p: 2.5 }}><Stack direction="row" justifyContent="space-between" alignItems="baseline"><Typography variant="h6">Cobertura e lacunas</Typography><Typography variant="body2" color="text.secondary">{coverageRows.length} registros de cobertura</Typography></Stack>{coverageRows.length ? <Stack spacing={1} sx={{ mt: 1.5 }}>{coverageRows.slice(0, 8).map((row, index) => <Box key={`${String(row.series_id)}-${index}`} sx={{ display: "grid", gridTemplateColumns: "minmax(130px, 1fr) auto auto", gap: 2, py: 1, borderBottom: "1px solid #e4e9ed" }}><Typography variant="body2">{String(row.metric || row.series_id || "Série")}</Typography><Typography variant="body2">{String(row.coverage_status || "—")}</Typography><Typography variant="body2" color="text.secondary">{String(row.missing_buckets ?? 0)} buckets ausentes</Typography></Box>)}</Stack> : <Typography color="text.secondary" sx={{ mt: 1 }}>A cobertura aparecerá quando houver um registro publicado para esta janela.</Typography>}</Paper>
  </Stack>;
}
