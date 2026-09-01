import { useEffect, useState } from "react";
import Plot from "react-plotly.js";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { getWisperDashboard } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const cash = (v: unknown) => money.format(Number(v || 0));
const num = (v: unknown, d = 0) => Number(v || 0).toLocaleString("pt-BR", { maximumFractionDigits: d });
const pct = (v: unknown, d = 1) => `${Number(v || 0).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d })}%`;

const BUCKET_COLORS: Record<string, string> = {
  "Generative AI": "#7C3AED",
  "Hospedagem GPU": "#F97316",
  "Dados e armazenamento": "#2563EB",
  Infraestrutura: "#64748B",
};

export default function WisperDashboard() {
  const [data, setData] = useState<any>();
  const [error, setError] = useState("");
  const [showAllSkus, setShowAllSkus] = useState(false);
  const [expandedSku, setExpandedSku] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getWisperDashboard(controller.signal)
      .then(setData)
      .catch((e) => {
        if (e?.code !== "ERR_CANCELED") setError(e?.response?.data?.detail || "Falha ao consultar Wisper");
      });
    return () => controller.abort();
  }, []);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;
  if (data.empty) return <Alert severity="info">Sem dados financeiros para Wisper.</Alert>;

  const ref = data.reference || {};
  const exec = data.executive || {};
  const buckets = data.buckets || {};
  const capacity = data.capacity || {};
  const skus = data.skus || [];
  const TOP_SKUS = 5;
  const topSkus = skus.slice(0, TOP_SKUS);
  const restSkus = skus.slice(TOP_SKUS);
  const restCost = restSkus.reduce((s: number, r: any) => s + Number(r.cost || 0), 0);
  const restShare = restSkus.reduce((s: number, r: any) => s + Number(r.share_pct || 0), 0);
  const subaccounts = data.subaccounts || [];
  const insights = data.insights || [];
  const op = data.operational || {};
  const opKpis = op.kpis || {};
  const unitEco = data.unit_economics || {};
  const quality = data.data_quality || [];

  const bucketNames = Object.keys(BUCKET_COLORS);
  const months = (data.monthly || []).map((m: any) => ({
    ...m,
    totalDisplay: m.is_partial ? `${cash(m.total)} (parcial)` : cash(m.total),
  }));

  const dailyCost = (capacity.daily || []).map((d: any) => d.cost);
  const dailyGpuUnits = (capacity.daily || []).map((d: any) => d.gpu_units || 0);
  const dailyAiUnits = (capacity.daily || []).map((d: any) => d.ai_units || 0);
  const dailyDates = (capacity.daily || []).map((d: any) => d.date);

  const inventory = op.inventory || {};
  const aiClusters = inventory.ai_clusters || [];
  const endpoints = inventory.endpoints || [];
  const computeShapes = inventory.compute_by_shape || [];

  // Aggregated by shape: "A10_X1 → 20 units", "LARGE_GENERIC_V2 → 12 units"
  const clusterShapeMap = new Map<string, number>();
  aiClusters.forEach((c: any) => {
    const shape = c.unit_shape || "Desconhecido";
    clusterShapeMap.set(shape, (clusterShapeMap.get(shape) || 0) + Number(c.unit_count || 0));
  });
  const clusterShapes = [...clusterShapeMap.entries()].map(([shape, units]) => ({ shape, units }));

  const hostingCost = (buckets["Hospedagem GPU"]?.cost || 0) + (buckets["Generative AI"]?.cost || 0);
  const totalClosed = exec.total_closed || 0;

  return (
    <Stack gap={2.5}>
      {/* Header */}
      <Box>
        <Typography variant="h4">Wisper — OCI AI</Typography>
        <Typography color="text.secondary">
          Visão executiva de custo, capacidade faturada e operação OCI.
        </Typography>
        <Stack direction="row" gap={1} mt={1} flexWrap="wrap">
          <Chip size="small" color="success" label={`Mês fechado: ${ref.closed_label || "—"}`} />
          <Chip size="small" color="warning" variant="outlined" label={`Mês em curso: ${ref.current_label || "—"}`} />
          {quality.map((q: any, i: number) => (
            <Tooltip key={i} title={q.detail || ""}>
              <Chip
                size="small"
                color={q.ok ? "success" : "error"}
                variant="outlined"
                label={q.ok ? "✓ " + q.check : "✗ " + q.check}
              />
            </Tooltip>
          ))}
        </Stack>
        {insights.map((ins: any, i: number) => (
          <Alert key={i} severity={ins.name === "step_up" ? "info" : "warning"} sx={{ mt: 1 }}>
            {ins.note}
          </Alert>
        ))}
      </Box>

      {/* KPI cards */}
      <Grid container spacing={2}>
        {[
          ["Custo mês fechado", cash(totalClosed), exec.mom_delta_pct != null ? `${exec.mom_delta_pct > 0 ? "+" : ""}${pct(exec.mom_delta_pct, 1)} vs. anterior` : ""],
          ["Mês em curso (parcial)", cash(exec.current_partial), `Projeção: ${cash(exec.monthly_projection)}`],
          ["Hospedagem dedicada IA", cash(hostingCost), `${totalClosed ? pct(hostingCost / totalClosed) : "0%"} do mês fechado`],
          ["Custo por MM tokens", unitEco.cost_per_mm_total_tokens != null ? cash(unitEco.cost_per_mm_total_tokens) : "—", "Generative AI · últimos 30 dias de telemetria"],
        ].map(([label, value, sub], idx) => (
          <Grid item xs={12} sm={6} md={3} key={String(label)}>
            <Card sx={{ height: "100%", borderTop: `3px solid ${["#0F172A", "#F59E0B", "#F97316", "#7C3AED"][idx]}` }}>
              <CardContent>
                <Typography variant="body2" color="text.secondary">{label}</Typography>
                <Typography variant="h6">{value}</Typography>
                {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {/* Monthly evolution + buckets */}
      <Grid container spacing={2}>
        <Grid item xs={12} lg={8}>
          <Card>
            <CardContent>
              <Typography variant="h6">Evolução financeira</Typography>
              <Plot
                data={[
                  ...bucketNames.map((bucket) => ({
                    x: months.map((r: any) => r.label),
                    y: months.map((r: any) => r.buckets?.[bucket] || 0),
                    name: bucket,
                    type: "bar" as const,
                    marker: { color: BUCKET_COLORS[bucket] },
                  })),
                ]}
                layout={{
                  autosize: true,
                  barmode: "stack",
                  paper_bgcolor: "transparent",
                  plot_bgcolor: "transparent",
                  margin: { l: 60, r: 20, t: 20, b: 80 },
                  legend: { orientation: "h", y: -0.25 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: 320 }}
                useResizeHandler
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} lg={4}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Typography variant="h6">Composição do mês fechado</Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Bucket</TableCell>
                    <TableCell align="right">Custo</TableCell>
                    <TableCell align="right">%</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {bucketNames.map((bucket) => (
                    <TableRow key={bucket}>
                      <TableCell>
                        <Box display="flex" alignItems="center" gap={1}>
                          <Box width={12} height={12} borderRadius="50%" bgcolor={BUCKET_COLORS[bucket]} />
                          {bucket}
                        </Box>
                      </TableCell>
                      <TableCell align="right">{cash(buckets[bucket]?.cost)}</TableCell>
                      <TableCell align="right">{pct(buckets[bucket]?.share_pct)}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow>
                    <TableCell><b>Total</b></TableCell>
                    <TableCell align="right"><b>{cash(totalClosed)}</b></TableCell>
                    <TableCell align="right"><b>100%</b></TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Capacity */}
      <Grid container spacing={2}>
        <Grid item xs={12} md={4}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Typography variant="h6">Capacidade faturada — {ref.closed_label}</Typography>
              <Stack gap={0.8} mt={1}>
                <Box><Typography variant="body2" color="text.secondary">GPU Hours (B95909)</Typography><Typography variant="h6">{num(capacity.gpu_hours)}</Typography></Box>
                <Box><Typography variant="body2" color="text.secondary">GPUs médias faturadas</Typography><Typography variant="h6">{num(capacity.gpu_average, 1)}</Typography></Box>
                <Box><Typography variant="body2" color="text.secondary">AI unit-hours (B108085)</Typography><Typography variant="h6">{num(capacity.ai_unit_hours)}</Typography></Box>
                <Box><Typography variant="body2" color="text.secondary">AI units médias</Typography><Typography variant="h6">{num(capacity.ai_unit_average, 1)}</Typography></Box>
                <Box><Typography variant="body2" color="text.secondary">OCPU Hours (B97384)</Typography><Typography variant="body2"><b>{num(capacity.ocpu_hours)}</b></Typography></Box>
                <Box><Typography variant="body2" color="text.secondary">GB Hours (B97385)</Typography><Typography variant="body2"><b>{num(capacity.gb_hours)}</b></Typography></Box>
              </Stack>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={8}>
          <Card>
            <CardContent>
              <Typography variant="h6">Série diária — custo (R$/dia) e capacidade (GPUs médias/dia)</Typography>
              <Plot
                data={[
                  {
                    x: dailyDates,
                    y: dailyCost,
                    type: "scatter" as const,
                    mode: "lines+markers",
                    name: "Custo diário (R$)",
                    line: { color: "#0F172A" },
                    marker: { size: 4 },
                    yaxis: "y",
                  },
                  {
                    x: dailyDates,
                    y: dailyGpuUnits,
                    type: "bar" as const,
                    name: "GPUs médias/dia (B95909 ÷ 24h)",
                    marker: { color: "#F97316", opacity: 0.65 },
                    yaxis: "y2",
                  },
                  {
                    x: dailyDates,
                    y: dailyAiUnits,
                    type: "bar" as const,
                    name: "AI units médias/dia (B108085 ÷ 24h)",
                    marker: { color: "#7C3AED", opacity: 0.5 },
                    yaxis: "y2",
                  },
                ]}
                layout={{
                  autosize: true,
                  paper_bgcolor: "transparent",
                  plot_bgcolor: "transparent",
                  margin: { l: 60, r: 60, t: 20, b: 60 },
                  xaxis: { title: { text: "" } },
                  yaxis: { title: { text: "R$" } },
                  yaxis2: {
                    title: { text: "unidades médias/dia" },
                    overlaying: "y",
                    side: "right",
                  },
                  legend: { orientation: "h", y: -0.25 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: 280 }}
                useResizeHandler
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Executive inventory — agregado por shape/modelo, sem nomes de recursos */}
      <Card>
        <CardContent>
          <Typography variant="h6">Inventário executivo OCI</Typography>
          <Grid container spacing={2} mt={0.5}>
            <Grid item xs={12} md={4}>
              <Typography variant="subtitle2">Dedicated AI Clusters (units por shape)</Typography>
              {aiClusters.length ? (
                <Table size="small">
                  <TableHead>
                    <TableRow><TableCell>Shape</TableCell><TableCell align="right">Units</TableCell></TableRow>
                  </TableHead>
                  <TableBody>
                    {clusterShapes.map((c: any, i: number) => (
                      <TableRow key={i}>
                        <TableCell>{c.shape}</TableCell>
                        <TableCell align="right">{num(c.units)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <Typography color="text.secondary" variant="body2">Nenhum cluster encontrado.</Typography>
              )}
              <Typography variant="body2" mt={1}><b>Total GPU units dedicadas:</b> {num(inventory.totals?.gpu_units_dedicated || 0)}</Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography variant="subtitle2">Endpoints de inferência</Typography>
              <Typography variant="h6">{num(endpoints.length)}</Typography>
              <Typography variant="body2" color="text.secondary">
                {endpoints.length ? "endpoints ativos no escopo Wisper" : "Nenhum endpoint encontrado."}
              </Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography variant="subtitle2">Instâncias por shape</Typography>
              {computeShapes.length ? (
                <Table size="small">
                  <TableHead>
                    <TableRow><TableCell>Shape</TableCell><TableCell align="right">Qtd</TableCell><TableCell>Estado</TableCell></TableRow>
                  </TableHead>
                  <TableBody>
                    {computeShapes.map((s: any, i: number) => (
                      <TableRow key={i}>
                        <TableCell>{s.shape}</TableCell>
                        <TableCell align="right">{num(s.count)}</TableCell>
                        <TableCell>{Object.entries(s.states || {}).map(([k, v]) => `${k}: ${v}`).join(", ")}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <Typography color="text.secondary" variant="body2">Nenhuma instância de compute no escopo.</Typography>
              )}
              <Typography variant="body2" mt={1}><b>Total instâncias compute:</b> {num(inventory.totals?.compute_instances || 0)}</Typography>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {/* Subaccounts */}
      {subaccounts.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="h6">Custo por subprojeto (compartment)</Typography>
            <Grid container spacing={2} mt={0.5}>
              {subaccounts.map((s: any, i: number) => (
                <Grid item xs={12} sm={6} md={3} key={i}>
                  <Card variant="outlined">
                    <CardContent>
                      <Typography variant="body2" color="text.secondary">{s.name}</Typography>
                      <Typography variant="h6">{cash(s.cost)}</Typography>
                      <Typography variant="caption" color="text.secondary">{pct(s.share_pct || (totalClosed ? (s.cost / totalClosed) * 100 : 0))}</Typography>
                    </CardContent>
                  </Card>
                </Grid>
              ))}
            </Grid>
          </CardContent>
        </Card>
      )}

      {/* SKUs — TOP 5 + "Outros" agrupado (expandível) */}
      <Card>
        <CardContent>
          <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1}>
            <Typography variant="h6">SKUs e componentes — {ref.closed_label}</Typography>
            {restSkus.length > 0 && (
              <Button size="small" onClick={() => setShowAllSkus((v) => !v)}>
                {showAllSkus ? "Ocultar demais" : `Ver todos (${skus.length} SKUs)`}
              </Button>
            )}
          </Stack>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>SKU</TableCell>
                <TableCell>Descrição</TableCell>
                <TableCell>Shape</TableCell>
                <TableCell align="right">Quantidade</TableCell>
                <TableCell align="right">Custo</TableCell>
                <TableCell align="right">%</TableCell>
                <TableCell align="right"></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {topSkus.map((r: any, i: number) => (
                <SkuRow key={i} r={r} expanded={expandedSku === r.sku} onToggle={() => setExpandedSku(expandedSku === r.sku ? null : r.sku)} />
              ))}
              {restSkus.length > 0 && (
                <TableRow
                  sx={{ cursor: "pointer", bgcolor: "action.hover" }}
                  onClick={() => setShowAllSkus((v) => !v)}
                >
                  <TableCell colSpan={3}>
                    <b>Outros ({restSkus.length} SKUs)</b>
                  </TableCell>
                  <TableCell align="right">—</TableCell>
                  <TableCell align="right"><b>{cash(restCost)}</b></TableCell>
                  <TableCell align="right"><b>{pct(restShare)}</b></TableCell>
                  <TableCell align="right"></TableCell>
                </TableRow>
              )}
              {showAllSkus &&
                restSkus.map((r: any, i: number) => (
                  <SkuRow key={`rest-${i}`} r={r} expanded={expandedSku === r.sku} onToggle={() => setExpandedSku(expandedSku === r.sku ? null : r.sku)} />
                ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Operational */}
      <Card>
        <CardContent>
          <Typography variant="h6">
            Monitoramento OCI
            {op.window?.days && (
              <Typography component="span" variant="body2" color="text.secondary" ml={1}>
                (janela real: {op.window.days} dia{op.window.days > 1 ? "s" : ""})
              </Typography>
            )}
          </Typography>
          {op.available ? (
            <>
              <Grid container spacing={1} mt={0.5}>
                {[
                  ["Tokens entrada", num(opKpis.input_tokens)],
                  ["Tokens saída", num(opKpis.output_tokens)],
                  ["Total tokens", num((opKpis.input_tokens || 0) + (opKpis.output_tokens || 0))],
                  ["Chamadas", num(opKpis.invocations)],
                  ["Latência média", `${num(opKpis.latency, 1)} ms`],
                  ["Erros", num(opKpis.errors)],
                  ["Sucesso", opKpis.success_rate == null ? "—" : pct(opKpis.success_rate)],
                  ["Utilização GPU", opKpis.gpu_utilization == null ? "Sem métrica OCI" : pct(opKpis.gpu_utilization, 1) + (opKpis.gpu_utilization_source === "custom_gpu" ? " (agente nvidia-smi)" : "")],
                  ["Memória GPU", opKpis.gpu_memory_utilization == null ? "—" : pct(opKpis.gpu_memory_utilization, 1)],
                  ["CPU hosts", opKpis.cpu_utilization == null ? "—" : pct(opKpis.cpu_utilization)],
                  ["Memória hosts", opKpis.memory_utilization == null ? "—" : pct(opKpis.memory_utilization)],
                ].map(([label, value]) => (
                  <Grid item xs={6} sm={4} md={2} key={String(label)}>
                    <Typography variant="caption" color="text.secondary">{label}</Typography>
                    <Typography variant="body2"><b>{value}</b></Typography>
                  </Grid>
                ))}
              </Grid>
              {op.daily?.length > 0 && (
                <Box mt={2}>
                  <Plot
                    data={[
                      {
                        x: op.daily.map((d: any) => d.date),
                        y: op.daily.map((d: any) => d.input_tokens),
                        name: "Input tokens",
                        type: "scatter" as const,
                        mode: "lines+markers",
                        line: { color: "#7C3AED" },
                      },
                      {
                        x: op.daily.map((d: any) => d.date),
                        y: op.daily.map((d: any) => d.output_tokens),
                        name: "Output tokens",
                        type: "scatter" as const,
                        mode: "lines+markers",
                        line: { color: "#2563EB" },
                      },
                    ]}
                    layout={{
                      autosize: true,
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      margin: { l: 60, r: 20, t: 20, b: 60 },
                      legend: { orientation: "h", y: -0.25 },
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%", height: 260 }}
                    useResizeHandler
                  />
                </Box>
              )}
            </>
          ) : (
            <Alert severity="warning" sx={{ mt: 1 }}>
              A telemetria OCI ainda não foi materializada. Tokens e utilização real de GPU aparecerão aqui após a coleta.
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Footer notes */}
      <Box>
        {(data.notes || []).map((n: string, i: number) => (
          <Typography key={i} variant="caption" color="text.secondary" display="block">
            • {n}
          </Typography>
        ))}
        {unitEco.note && (
          <Typography variant="caption" color="text.secondary" display="block">
            • Custo por MM tokens: {unitEco.note}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

/** Linha de SKU com detalhe expansível ("mais" → dropdown com dados do produto). */
function SkuRow({ r, expanded, onToggle }: { r: any; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <TableRow>
        <TableCell>{r.sku}</TableCell>
        <TableCell>{r.description}</TableCell>
        <TableCell>{r.shapes || "—"}</TableCell>
        <TableCell align="right">{num(r.quantity)} {r.unit}</TableCell>
        <TableCell align="right">{cash(r.cost)}</TableCell>
        <TableCell align="right">{pct(r.share_pct)}</TableCell>
        <TableCell align="right">
          <Button size="small" onClick={onToggle}>{expanded ? "menos" : "mais"}</Button>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={7} sx={{ bgcolor: "action.hover" }}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Preço unitário médio</Typography>
                <Typography variant="body2"><b>{r.unit_price != null ? `R$ ${num(r.unit_price, 4)}` : "—"}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Região</Typography>
                <Typography variant="body2"><b>{r.region || "—"}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Recursos (resourceId)</Typography>
                <Typography variant="body2"><b>{num(r.resources)}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Variação vs. mês anterior</Typography>
                <Typography variant="body2">
                  <b>{r.mom_delta_pct == null ? "—" : `${r.mom_delta_pct > 0 ? "+" : ""}${pct(r.mom_delta_pct)}`}</b>
                </Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Custo de lista</Typography>
                <Typography variant="body2"><b>{cash(r.list_cost)}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Custo efetivo (faturado)</Typography>
                <Typography variant="body2"><b>{cash(r.effective_cost)}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Desconto contratual</Typography>
                <Typography variant="body2"><b>{r.discount_pct == null ? "—" : pct(r.discount_pct)}</b></Typography>
              </Grid>
            </Grid>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}
