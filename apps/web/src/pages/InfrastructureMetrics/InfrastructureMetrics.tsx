/**
 * Consumo de infraestrutura — leitura da Silver genérica `compute_usage_metrics`.
 *
 * Não coleta nada: as fontes de métricas (ex.: OCI Monitoring) gravam o dado
 * puro na Bronze pela Extração e a Silver nasce do Reprocessar com os
 * mapeamentos do catálogo. Serve a qualquer cloud com fonte cadastrada.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, CircularProgress, FormControl, InputLabel, MenuItem, Paper, Select, Stack,
  Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import TimelineOutlined from "@mui/icons-material/TimelineOutlined";
import Plot from "@/components/Plot";
import { api } from "../../services/api";
import { listMetricSources, type MetricSource } from "../../services/infraOpportunitiesApi";

type Resource = {
  resource_id: string;
  resource_name: string | null;
  cloud_provider: string | null;
  resource_type: string | null;
  metrics: string[];
  resolutions: number[];
  first_bucket: string | null;
  last_bucket: string | null;
  points: number;
};
type SeriesPoint = { t: string; mean: number | null; max: number | null; p95: number | null; p99: number | null };
type Profile = {
  samples: number; expected_buckets: number; coverage: number; days_observed: number;
  p50: number | null; p95: number | null; p99: number | null; average: number | null; max: number | null;
};
type SeriesResponse = { points: SeriesPoint[]; profile: Profile; daily: { day: string; p95: number | null; max: number; average: number }[] };

const METRIC_LABEL: Record<string, string> = {
  "cpu.utilization": "CPU (% de uso)",
  "memory.utilization": "Memória (% de uso)",
  "disk.read_bytes_counter": "Disco — bytes lidos",
  "disk.write_bytes_counter": "Disco — bytes gravados",
  "disk.read_operations_counter": "Disco — operações de leitura",
  "disk.write_operations_counter": "Disco — operações de escrita",
  "network.in_bytes_counter": "Rede — bytes recebidos",
  "network.out_bytes_counter": "Rede — bytes enviados",
};
const WINDOWS = [1, 7, 14, 30];
const fmt = (value: number | null | undefined, digits = 1) =>
  value == null ? "—" : value.toLocaleString("pt-BR", { maximumFractionDigits: digits });
const localStamp = (utc: string) => {
  const date = new Date(utc.endsWith("Z") || utc.includes("+") ? utc : `${utc}Z`);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export default function InfrastructureMetrics() {
  const [sources, setSources] = useState<MetricSource[]>([]);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [resources, setResources] = useState<Resource[]>([]);
  const [resourceId, setResourceId] = useState("");
  const [metric, setMetric] = useState("cpu.utilization");
  const [resolution, setResolution] = useState(300);
  const [days, setDays] = useState(7);
  const [series, setSeries] = useState<SeriesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    listMetricSources()
      .then((items) => { setSources(items); setSourceId((current) => current ?? items[0]?.id ?? null); })
      .catch(() => setError("Não foi possível carregar as fontes de métricas."));
  }, []);

  useEffect(() => {
    if (sourceId == null) return;
    let active = true;
    setResources([]);
    api.get("/custos/metricas-infra/recursos", { params: { connection_id: sourceId } })
      .then(({ data }) => {
        if (!active) return;
        setResources(data.items);
        setResourceId((current) => (data.items.some((item: Resource) => item.resource_id === current) ? current : data.items[0]?.resource_id ?? ""));
      })
      .catch(() => { if (active) setError("A Silver de métricas não respondeu para esta fonte."); });
    return () => { active = false; };
  }, [sourceId]);

  const resource = resources.find((item) => item.resource_id === resourceId);
  const end = useMemo(() => {
    const last = resource?.last_bucket ? new Date(resource.last_bucket) : new Date();
    return new Date(last.getTime() + resolution * 1000);
  }, [resource?.last_bucket, resolution]);

  useEffect(() => {
    if (sourceId == null || !resourceId || !metric) { setSeries(null); return; }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.get("/custos/metricas-infra/series", {
      params: {
        connection_id: sourceId, resource_id: resourceId, metric, resolution_seconds: resolution,
        start_utc: new Date(end.getTime() - days * 86_400_000).toISOString(), end_utc: end.toISOString(),
      },
      signal: controller.signal,
    })
      .then(({ data }) => setSeries(data))
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setSeries(null);
        setError(reason?.response?.data?.detail ?? "Não foi possível ler a série.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [sourceId, resourceId, metric, resolution, days, end]);

  const isPercent = metric.endsWith(".utilization");
  const x = (series?.points ?? []).map((point) => localStamp(point.t));
  const profile = series?.profile;

  return <Stack spacing={2.5} sx={{ maxWidth: 1440, mx: "auto", pb: 4, px: { xs: 1.5, md: 3 }, pt: { xs: 1.5, md: 3 } }}>
    <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
      <TimelineOutlined sx={{ color: "#087f8c", fontSize: 32 }} />
      <Box>
        <Typography variant="h5">Consumo de infraestrutura</Typography>
        <Typography color="text.secondary">Série da Silver genérica <code>compute_usage_metrics</code>, de qualquer cloud com fonte de métricas cadastrada.</Typography>
      </Box>
    </Box>

    <Paper variant="outlined" sx={{ p: 2, borderTop: "3px solid #087f8c" }}>
      <Stack direction={{ xs: "column", lg: "row" }} spacing={1.5} useFlexGap flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 240 }}>
          <InputLabel>Fonte de métricas</InputLabel>
          <Select label="Fonte de métricas" value={sourceId ?? ""} onChange={(event) => setSourceId(Number(event.target.value))}>
            {sources.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 240 }} disabled={!resources.length}>
          <InputLabel>Recurso</InputLabel>
          <Select label="Recurso" value={resourceId} onChange={(event) => setResourceId(String(event.target.value))}>
            {resources.map((item) => <MenuItem key={item.resource_id} value={item.resource_id}>{item.resource_name || item.resource_id}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 220 }} disabled={!resource}>
          <InputLabel>Métrica</InputLabel>
          <Select label="Métrica" value={metric} onChange={(event) => setMetric(String(event.target.value))}>
            {(resource?.metrics ?? []).map((name) => <MenuItem key={name} value={name}>{METRIC_LABEL[name] ?? name}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 130 }}>
          <InputLabel>Resolução</InputLabel>
          <Select label="Resolução" value={resolution} onChange={(event) => setResolution(Number(event.target.value))}>
            {(resource?.resolutions?.length ? resource.resolutions : [300]).map((seconds) => <MenuItem key={seconds} value={seconds}>{seconds / 60} min</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Janela</InputLabel>
          <Select label="Janela" value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {WINDOWS.map((value) => <MenuItem key={value} value={value}>{value} dia(s)</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>
      {resource && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
        {(resource.cloud_provider ?? "").toUpperCase()} · {resource.resource_type ?? "—"} · dados de {resource.first_bucket ? localStamp(resource.first_bucket) : "—"} a {resource.last_bucket ? localStamp(resource.last_bucket) : "—"} · {resource.points.toLocaleString("pt-BR")} valores na Silver
      </Typography>}
    </Paper>

    {!sources.length && !error && <Alert severity="info">Cadastre uma fonte de métricas (ex.: OCI Monitoring) em Conexões de nuvem, colete e reprocesse para ver o consumo aqui.</Alert>}
    {sources.length > 0 && !resources.length && !error && <Alert severity="info">A Silver ainda não tem dados desta fonte. Rode a Extração da fonte e o Reprocessar da tabela “Métricas de uso de computação”.</Alert>}
    {error && <Alert severity="error">{error}</Alert>}

    {profile && <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0,1fr))", md: "repeat(6, minmax(0,1fr))" }, gap: 1.5 }}>
      {[
        ["P50 (mediana)", profile.p50, "Metade do tempo abaixo deste valor"],
        ["P95", profile.p95, "95% dos buckets abaixo; 5% do tempo acima"],
        ["P99", profile.p99, "Só 1% do tempo acima"],
        ["Pico", profile.max, "Maior valor nativo observado (max)"],
        ["Média", profile.average, "Média simples dos buckets"],
      ].map(([label, value, help]) => <Paper key={String(label)} variant="outlined" sx={{ p: 1.5 }}>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h6">{fmt(value as number | null)}{isPercent && value != null ? "%" : ""}</Typography>
        <Typography variant="caption" color="text.secondary">{help}</Typography>
      </Paper>)}
      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Typography variant="caption" color="text.secondary">Cobertura</Typography>
        <Typography variant="h6">{fmt(profile.coverage * 100)}%</Typography>
        <Typography variant="caption" color="text.secondary">{profile.samples.toLocaleString("pt-BR")} de {profile.expected_buckets.toLocaleString("pt-BR")} buckets · {profile.days_observed} dia(s)</Typography>
      </Paper>
    </Box>}

    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="baseline">
        <Typography variant="subtitle1" fontWeight={650}>Série no tempo</Typography>
        <Typography variant="caption" color="text.secondary">horário local · {x.length.toLocaleString("pt-BR")} buckets de {resolution / 60} min</Typography>
      </Stack>
      {loading ? <Box sx={{ display: "grid", placeItems: "center", minHeight: 360 }}><CircularProgress size={28} /></Box>
        : x.length ? <Plot data={[
          { type: "scatter", mode: "lines", name: "Máximo do bucket", x, y: series!.points.map((p) => p.max), line: { color: "#ed8a3b", width: 1 }, opacity: 0.55 },
          { type: "scatter", mode: "lines", name: "P99 do bucket", x, y: series!.points.map((p) => p.p99), line: { color: "#8f6bd8", width: 1 }, visible: "legendonly" },
          { type: "scatter", mode: "lines", name: "P95 do bucket", x, y: series!.points.map((p) => p.p95), line: { color: "#d64c4c", width: 1 }, visible: "legendonly" },
          { type: "scatter", mode: "lines", name: "Média do bucket", x, y: series!.points.map((p) => p.mean), line: { color: "#0F62FE", width: 1.6 } },
        ] as never} layout={{
          autosize: true, height: 380, margin: { l: 56, r: 16, t: 10, b: 60 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent", separators: ",.", hovermode: "x unified",
          xaxis: { type: "date", tickformat: "%d/%m %Hh", showgrid: false, rangeslider: { visible: true, thickness: 0.06 } },
          yaxis: { ticksuffix: isPercent ? "%" : "", rangemode: "tozero", gridcolor: "#e8edf3" },
          legend: { orientation: "h", y: -0.25 },
        } as never} />
          : <Typography color="text.secondary" sx={{ py: 6, textAlign: "center" }}>Sem buckets para os filtros escolhidos.</Typography>}
      <Alert severity="info" sx={{ mt: 1 }}>
        <strong>Como ler:</strong> cada ponto é um bucket de {resolution / 60} min. A OCI calcula média, máximo e P95/P99 <em>dentro</em> do bucket a partir das amostras brutas
        (≈ a cada 10 s). Os cartões acima (P50/P95/P99) são outra coisa: o percentil da janela inteira, calculado sobre as médias dos buckets.
      </Alert>
    </Paper>

    {series && series.daily.length > 0 && <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" fontWeight={650} gutterBottom>Resumo por dia</Typography>
      <Table size="small">
        <TableHead><TableRow><TableCell>Dia (UTC)</TableCell><TableCell align="right">Média</TableCell><TableCell align="right">P95</TableCell><TableCell align="right">Maior média</TableCell></TableRow></TableHead>
        <TableBody>{series.daily.map((row) => <TableRow key={row.day}>
          <TableCell>{row.day}</TableCell>
          <TableCell align="right">{fmt(row.average)}{isPercent ? "%" : ""}</TableCell>
          <TableCell align="right">{fmt(row.p95)}{isPercent ? "%" : ""}</TableCell>
          <TableCell align="right">{fmt(row.max)}{isPercent ? "%" : ""}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </Paper>}
  </Stack>;
}
