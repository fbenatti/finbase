import { useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { getWisperDashboard, type WisperDashboardPayload } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const cash = (v: unknown) => money.format(Number(v || 0));
const num = (v: unknown, d = 0) => Number(v || 0).toLocaleString("pt-BR", { maximumFractionDigits: d });
const pct = (v: unknown, d = 1) => `${Number(v || 0).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d })}%`;

const BUCKET_COLORS: Record<string, string> = {
  "Generative AI": "#7C3AED",
  "Hospedagem GPU (compute + suporte)": "#F97316",
  "Dados e armazenamento": "#2563EB",
  Infraestrutura: "#64748B",
};

function SectionTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <Box sx={{ mb: 1.5 }}>
      <Typography variant="h5">{title}</Typography>
      {subtitle && <Typography variant="body2" color="text.secondary">{subtitle}</Typography>}
    </Box>
  );
}

function KpiCard({ label, value, hint, color, tooltip }: {
  label: string; value: React.ReactNode; hint?: string; color?: string; tooltip?: string;
}) {
  const content = (
    <Card sx={{ height: "100%", borderTop: color ? `3px solid ${color}` : undefined }}>
      <CardContent>
        <Typography variant="body2" color="text.secondary">{label}</Typography>
        <Typography variant="h6">{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary" display="block">{hint}</Typography>}
      </CardContent>
    </Card>
  );
  return tooltip ? <Tooltip title={tooltip} arrow placement="top">{content}</Tooltip> : content;
}

export default function WisperDashboard() {
  const [data, setData] = useState<WisperDashboardPayload | null>(null);
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

  const bucketNames = useMemo(() => Object.keys(BUCKET_COLORS), []);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;
  if (data.empty) return <Alert severity="info">Sem dados financeiros para Wisper.</Alert>;

  const ref = data.reference || {};
  const exec = data.executive || {};
  const buckets = data.buckets || {};
  const capacity = data.capacity || {};
  const skus = data.skus || [];
  const subaccounts = data.subaccounts || [];
  const insights = data.insights || [];
  const op = data.operational || {};
  const opKpis = op.kpis || {};
  const unitEco = data.unit_economics || {};
  const hosting = exec.hosting_dedicated || {};
  const totalClosed = exec.total_closed || 0;

  const months = data.monthly || [];
  const dailyClosed = capacity.daily_closed || [];
  const inventory = op.inventory || {};
  const endpointsByModel = inventory.endpoints_by_model || [];
  const computeGpu = inventory.compute_gpu || [];
  const computeSupport = inventory.compute_support || [];
  const inventoryByCompartment = inventory.by_compartment || [];

  const clusterShapeMap = new Map<string, number>();
  (inventory.ai_clusters || []).forEach((c) => {
    const shape = c.unit_shape || "Desconhecido";
    clusterShapeMap.set(shape, (clusterShapeMap.get(shape) || 0) + Number(c.unit_count || 0));
  });
  const clusterShapes = [...clusterShapeMap.entries()].map(([shape, units]) => ({ shape, units }));

  const topComponents = skus.slice(0, 5);
  const restComponents = skus.slice(5);

  return (
    <Stack gap={3}>
      <Box>
        <Typography variant="h4">Wisper — OCI AI</Typography>
        <Typography color="text.secondary">
          Quanto custa, com qual capacidade provisionada e com que eficiência operacional.
        </Typography>
        <Stack direction="row" gap={1} mt={1.5} flexWrap="wrap">
          <Chip size="small" color="success" label={`Financeiro fechado: ${ref.closed_label || "—"} · FOCUS`} />
          <Chip size="small" color="warning" variant="outlined" label={`Mês em curso: ${ref.current_label || "—"} (parcial)`} />
          {ref.inventory_snapshot_at && (
            <Chip size="small" variant="outlined" label={`Inventário OCI: ${String(ref.inventory_snapshot_at).slice(0, 16).replace("T", " ")}`} />
          )}
          {ref.telemetry_window_days && (
            <Chip size="small" variant="outlined" label={`Telemetria: últimos ${ref.telemetry_window_days} dias`} />
          )}
        </Stack>
        {insights.map((ins, i) => (
          <Alert key={i} severity={ins.name === "step_up" ? "info" : "warning"} sx={{ mt: 1 }}>
            {ins.note}
          </Alert>
        ))}
      </Box>

      {/* A. Finanças */}
      <Box>
        <SectionTitle
          title="Finanças"
          subtitle={`Referência do mês fechado (${ref.closed_label}). Realizado: FOCUS · Forecast: Dimon/planning.`}
        />
        <Grid container spacing={2} sx={{ mb: 2 }}>
          <Grid item xs={12} sm={6} md={3}>
            <KpiCard
              label="Custo mês fechado"
              value={cash(totalClosed)}
              hint={exec.mom_delta_pct != null ? `${exec.mom_delta_pct > 0 ? "+" : ""}${pct(exec.mom_delta_pct, 1)} vs. anterior` : undefined}
              color="#0F172A"
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <KpiCard
              label="Mês em curso (parcial)"
              value={cash(exec.current_partial)}
              hint={`Projeção run-rate: ${cash(exec.monthly_projection)}${exec.current_forecast ? ` · Forecast Dimon: ${cash(exec.current_forecast)}` : ""}`}
              color="#F59E0B"
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <KpiCard
              label="Hospedagem dedicada de IA"
              value={cash(hosting.total)}
              hint={`${pct(hosting.share_pct)} do mês fechado · GenAI ${cash(hosting.generative_ai?.cost)} + GPU ${cash(hosting.gpu_hosting?.cost)}`}
              color="#F97316"
              tooltip="Soma de Generative AI com Hospedagem GPU (compute + suporte: GPU-h, OCPU-h e GB-h)."
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <KpiCard
              label="Custo Generative AI por MM de tokens"
              value={unitEco.cost_per_mm_total_tokens != null ? cash(unitEco.cost_per_mm_total_tokens) : "—"}
              hint={unitEco.is_estimate ? "Estimativa · apenas Generative AI" : "Apenas Generative AI"}
              color="#7C3AED"
              tooltip={unitEco.summary}
            />
          </Grid>
        </Grid>

        <Grid container spacing={2}>
          <Grid item xs={12} lg={8}>
            <Card>
              <CardContent>
                <Typography variant="h6">Evolução financeira</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  Barras = realizado por componente. Linha laranja = forecast total até dezembro.
                </Typography>
                <Plot
                  data={[
                    ...bucketNames.map((bucket) => ({
                      x: months.map((r) => r.label),
                      y: months.map((r) => r.buckets?.[bucket] || 0),
                      name: bucket,
                      type: "bar" as const,
                      marker: { color: BUCKET_COLORS[bucket] },
                    })),
                    {
                      x: months.map((r) => r.label),
                      y: months.map((r) => r.forecast_total ?? null),
                      name: "Forecast total",
                      type: "scatter" as const,
                      mode: "lines+markers",
                      line: { color: "#F59E0B", width: 3 },
                    },
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
                      <TableCell>Componente</TableCell>
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

        {subaccounts.length > 0 && (
          <Card sx={{ mt: 2 }}>
            <CardContent>
              <Typography variant="h6">Custo por subprojeto (compartment)</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Compartments de billing na FOCUS. O inventário OCI pode incluir recursos fora desses subaccounts.
              </Typography>
              <Grid container spacing={2}>
                {subaccounts.map((s, i) => {
                  const inv = inventoryByCompartment.find((c) => c.compartment === s.name);
                  return (
                    <Grid item xs={12} sm={6} md={3} key={i}>
                      <Card variant="outlined">
                        <CardContent>
                          <Typography variant="body2" color="text.secondary">{s.name}</Typography>
                          <Typography variant="h6">{cash(s.cost)}</Typography>
                          <Typography variant="caption" color="text.secondary" display="block">{pct(s.share_pct)} do mês fechado</Typography>
                          {inv && (
                            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                              Inventário: {inv.ai_clusters} clusters · {inv.endpoints} endpoints · {inv.compute_instances} VMs
                            </Typography>
                          )}
                        </CardContent>
                      </Card>
                    </Grid>
                  );
                })}
              </Grid>
            </CardContent>
          </Card>
        )}
      </Box>

      {/* B. Capacidade e inventário */}
      <Box>
        <SectionTitle
          title="Capacidade e inventário"
          subtitle="Capacidade faturada = horas cobradas na FOCUS. Inventário = recursos provisionados no snapshot OCI."
        />
        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <Card sx={{ height: "100%" }}>
              <CardContent>
                <Typography variant="h6">Capacidade faturada — {ref.closed_label}</Typography>
                <Stack gap={0.8} mt={1}>
                  <Box><Typography variant="body2" color="text.secondary">GPU-h faturadas (B95909)</Typography><Typography variant="h6">{num(capacity.gpu_hours)}</Typography></Box>
                  <Box><Typography variant="body2" color="text.secondary">AI unit-h faturadas (B108085)</Typography><Typography variant="h6">{num(capacity.ai_unit_hours)}</Typography></Box>
                  <Box><Typography variant="body2" color="text.secondary">OCPU-h de suporte (B97384)</Typography><Typography variant="body2"><b>{num(capacity.ocpu_hours)}</b></Typography></Box>
                  <Box><Typography variant="body2" color="text.secondary">GB-h de suporte (B97385)</Typography><Typography variant="body2"><b>{num(capacity.gb_hours)}</b></Typography></Box>
                </Stack>
                {capacity.reconciliation?.note && (
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                    {capacity.reconciliation.note}
                  </Typography>
                )}
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={12} md={8}>
            <Grid container spacing={2}>
              <Grid item xs={12}>
                <Card>
                  <CardContent>
                    <Typography variant="h6">Custo diário por componente — {ref.closed_label}</Typography>
                    <Plot
                      data={bucketNames.map((bucket) => ({
                        x: dailyClosed.map((d) => d.date),
                        y: dailyClosed.map((d) => d.buckets?.[bucket] || 0),
                        name: bucket,
                        type: "bar" as const,
                        marker: { color: BUCKET_COLORS[bucket] },
                      }))}
                      layout={{
                        autosize: true,
                        barmode: "stack",
                        paper_bgcolor: "transparent",
                        plot_bgcolor: "transparent",
                        margin: { l: 60, r: 20, t: 20, b: 60 },
                        legend: { orientation: "h", y: -0.3 },
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", height: 240 }}
                      useResizeHandler
                    />
                  </CardContent>
                </Card>
              </Grid>
              <Grid item xs={12}>
                <Card>
                  <CardContent>
                    <Typography variant="h6">Capacidade faturada por dia — {ref.closed_label}</Typography>
                    <Plot
                      data={[
                        {
                          x: dailyClosed.map((d) => d.date),
                          y: dailyClosed.map((d) => d.gpu_hours || 0),
                          name: "GPU-h/dia (B95909)",
                          type: "scatter" as const,
                          mode: "lines+markers",
                          line: { color: "#F97316" },
                        },
                        {
                          x: dailyClosed.map((d) => d.date),
                          y: dailyClosed.map((d) => d.ai_unit_hours || 0),
                          name: "AI unit-h/dia (B108085)",
                          type: "scatter" as const,
                          mode: "lines+markers",
                          line: { color: "#7C3AED" },
                        },
                      ]}
                      layout={{
                        autosize: true,
                        paper_bgcolor: "transparent",
                        plot_bgcolor: "transparent",
                        margin: { l: 60, r: 20, t: 20, b: 60 },
                        yaxis: { title: { text: "horas/dia" } },
                        legend: { orientation: "h", y: -0.3 },
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", height: 220 }}
                      useResizeHandler
                    />
                  </CardContent>
                </Card>
              </Grid>
            </Grid>
          </Grid>
        </Grid>

        <Card sx={{ mt: 2 }}>
          <CardContent>
            <Typography variant="h6">Inventário executivo OCI</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              Origem: APIs OCI (GenAI e Compute). Bucket financeiro relacionado indicado em cada bloco.
            </Typography>
            <Grid container spacing={2}>
              <Grid item xs={12} md={4}>
                <Typography variant="subtitle2">Clusters dedicados (Generative AI)</Typography>
                {clusterShapes.length ? (
                  <Table size="small">
                    <TableHead>
                      <TableRow><TableCell>Shape</TableCell><TableCell align="right">Units</TableCell></TableRow>
                    </TableHead>
                    <TableBody>
                      {clusterShapes.map((c, i) => (
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
                <Typography variant="body2" mt={1}><b>Total units dedicadas:</b> {num(inventory.totals?.gpu_units_dedicated || 0)}</Typography>
              </Grid>
              <Grid item xs={12} md={4}>
                <Typography variant="subtitle2">Endpoints de inferência (Generative AI)</Typography>
                {endpointsByModel.length ? (
                  <Table size="small">
                    <TableHead>
                      <TableRow><TableCell>Modelo</TableCell><TableCell align="right">Qtd</TableCell></TableRow>
                    </TableHead>
                    <TableBody>
                      {endpointsByModel.slice(0, 6).map((e, i) => (
                        <TableRow key={i}>
                          <TableCell>{e.model}</TableCell>
                          <TableCell align="right">{num(e.count)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <Typography color="text.secondary" variant="body2">Nenhum endpoint encontrado.</Typography>
                )}
              </Grid>
              <Grid item xs={12} md={4}>
                <Typography variant="subtitle2">Compute GPU (Hospedagem GPU)</Typography>
                {computeGpu.length ? (
                  <Table size="small">
                    <TableHead>
                      <TableRow><TableCell>Shape</TableCell><TableCell align="right">Qtd</TableCell></TableRow>
                    </TableHead>
                    <TableBody>
                      {computeGpu.slice(0, 6).map((s, i) => (
                        <TableRow key={i}>
                          <TableCell>{s.shape}</TableCell>
                          <TableCell align="right">{num(s.count)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <Typography color="text.secondary" variant="body2">Nenhuma VM GPU no escopo.</Typography>
                )}
                {computeSupport.length > 0 && (
                  <Accordion sx={{ mt: 1, boxShadow: "none", border: "1px solid", borderColor: "divider" }}>
                    <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                      <Typography variant="body2">Infra de suporte ({computeSupport.reduce((s, r) => s + r.count, 0)} VMs)</Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ pt: 0 }}>
                      <Table size="small">
                        <TableBody>
                          {computeSupport.map((s, i) => (
                            <TableRow key={i}>
                              <TableCell>{s.shape}</TableCell>
                              <TableCell align="right">{num(s.count)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </AccordionDetails>
                  </Accordion>
                )}
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      </Box>

      {/* C. Operação e eficiência */}
      <Box>
        <SectionTitle
          title="Operação e eficiência"
          subtitle={`Telemetria dos últimos ${ref.telemetry_window_days || "—"} dias. Tokens medidos apenas em Generative AI.`}
        />
        {unitEco.note && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {unitEco.note}
          </Alert>
        )}
        <Card>
          <CardContent>
            {op.available ? (
              <>
                <Grid container spacing={1}>
                  {[
                    ["Tokens entrada", num(opKpis.input_tokens)],
                    ["Tokens saída", num(opKpis.output_tokens)],
                    ["Total tokens", num((opKpis.input_tokens || 0) + (opKpis.output_tokens || 0))],
                    ["Chamadas", num(opKpis.invocations)],
                    ["Latência p50", `${num(opKpis.latency, 1)} ms`],
                    ["Erros", num(opKpis.errors)],
                    ["Sucesso", opKpis.success_rate == null ? "—" : pct(opKpis.success_rate)],
                    ["Utilização GPU", opKpis.gpu_utilization == null ? "Sem telemetria de tokens em GPU" : pct(opKpis.gpu_utilization, 1) + (opKpis.gpu_utilization_source === "custom_gpu" ? " (agente)" : "")],
                  ].map(([label, value]) => (
                    <Grid item xs={6} sm={4} md={3} key={String(label)}>
                      <Typography variant="caption" color="text.secondary">{label}</Typography>
                      <Typography variant="body2"><b>{value}</b></Typography>
                    </Grid>
                  ))}
                </Grid>
                {op.daily && op.daily.length > 0 && (
                  <Box mt={2}>
                    <Plot
                      data={[
                        {
                          x: op.daily.map((d) => d.date),
                          y: op.daily.map((d) => d.input_tokens),
                          name: "Input tokens",
                          type: "scatter",
                          mode: "lines+markers",
                          line: { color: "#7C3AED" },
                        },
                        {
                          x: op.daily.map((d) => d.date),
                          y: op.daily.map((d) => d.output_tokens),
                          name: "Output tokens",
                          type: "scatter",
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
              <Alert severity="warning">
                A telemetria OCI ainda não foi materializada. Tokens e utilização aparecerão aqui após a coleta.
              </Alert>
            )}
          </CardContent>
        </Card>
      </Box>

      {/* Detalhamento técnico */}
      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="h6">Principais componentes de custo — {ref.closed_label}</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack direction="row" justifyContent="flex-end" mb={1}>
            {restComponents.length > 0 && (
              <Button size="small" onClick={() => setShowAllSkus((v) => !v)}>
                {showAllSkus ? "Ocultar demais" : `Ver todos (${skus.length})`}
              </Button>
            )}
          </Stack>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Componente</TableCell>
                <TableCell>Bucket</TableCell>
                <TableCell align="right">Custo</TableCell>
                <TableCell align="right">%</TableCell>
                <TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {topComponents.map((r, i) => (
                <SkuRow key={i} r={r} expanded={expandedSku === r.sku} onToggle={() => setExpandedSku(expandedSku === r.sku ? null : r.sku)} />
              ))}
              {showAllSkus && restComponents.map((r, i) => (
                <SkuRow key={`rest-${i}`} r={r} expanded={expandedSku === r.sku} onToggle={() => setExpandedSku(expandedSku === r.sku ? null : r.sku)} />
              ))}
            </TableBody>
          </Table>
        </AccordionDetails>
      </Accordion>

      <Box>
        {(data.notes || []).map((n, i) => (
          <Typography key={i} variant="caption" color="text.secondary" display="block">
            • {n}
          </Typography>
        ))}
      </Box>
    </Stack>
  );
}

function SkuRow({ r, expanded, onToggle }: { r: any; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <TableRow>
        <TableCell>
          <Typography variant="body2" fontWeight={600}>{r.executive_name || r.description}</Typography>
          <Typography variant="caption" color="text.secondary">{r.sku}</Typography>
        </TableCell>
        <TableCell>{r.bucket || "—"}</TableCell>
        <TableCell align="right">{cash(r.cost)}</TableCell>
        <TableCell align="right">{pct(r.share_pct)}</TableCell>
        <TableCell align="right">
          <Button size="small" onClick={onToggle}>{expanded ? "menos" : "detalhes"}</Button>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={5} sx={{ bgcolor: "action.hover" }}>
            <Grid container spacing={2}>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Descrição técnica</Typography>
                <Typography variant="body2"><b>{r.description}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Quantidade</Typography>
                <Typography variant="body2"><b>{num(r.quantity)} {r.unit}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Shape / recurso</Typography>
                <Typography variant="body2"><b>{r.shapes || "—"}</b></Typography>
              </Grid>
              <Grid item xs={6} sm={3}>
                <Typography variant="caption" color="text.secondary">Preço unitário médio</Typography>
                <Typography variant="body2"><b>{r.unit_price != null ? `R$ ${num(r.unit_price, 4)}` : "—"}</b></Typography>
              </Grid>
            </Grid>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}
