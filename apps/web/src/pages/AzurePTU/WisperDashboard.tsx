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
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
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
import MemoryIcon from "@mui/icons-material/Memory";
import HubIcon from "@mui/icons-material/Hub";
import CloudQueueIcon from "@mui/icons-material/CloudQueue";
import DnsIcon from "@mui/icons-material/Dns";
import { getWisperDashboard, type WisperDashboardPayload } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const money2 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 });
const cash = (v: unknown) => money.format(Number(v || 0));
const cash2 = (v: unknown) => money2.format(Number(v || 0));
const num = (v: unknown, d = 0) => Number(v || 0).toLocaleString("pt-BR", { maximumFractionDigits: d });
const pct = (v: unknown, d = 1) => `${Number(v || 0).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d })}%`;

/** Milhões de tokens, legíveis para os volumes atuais da plataforma. */
function formatMillions(tokens: number): string {
  const millions = tokens / 1_000_000;
  return millions.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const BUCKET_COLORS: Record<string, string> = {
  "Generative AI": "#7C3AED",
  "Hospedagem GPU (compute + suporte)": "#F97316",
  "Dados e armazenamento": "#2563EB",
  Infraestrutura: "#64748B",
};

const WISPER_ACCENT = "#7C3AED";

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

function InventoryStatCard({
  icon, label, value, hint, color, onClick,
}: {
  icon: React.ReactNode; label: string; value: React.ReactNode; hint?: string; color: string; onClick?: () => void;
}) {
  const content = (
    <Card sx={{ height: "100%", border: "1px solid", borderColor: "divider" }}>
      <CardContent>
        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <Box sx={{ color, mt: 0.25 }}>{icon}</Box>
          <Box>
            <Typography variant="body2" color="text.secondary">{label}</Typography>
            <Typography variant="h5" fontWeight={700}>{value}</Typography>
            {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
            {onClick && (
              <Typography variant="caption" color="primary" display="block" sx={{ mt: 0.5 }}>
                Clique para detalhes
              </Typography>
            )}
          </Box>
        </Stack>
      </CardContent>
    </Card>
  );
  if (!onClick) return content;
  return (
    <CardActionArea onClick={onClick} sx={{ height: "100%", borderRadius: 1 }}>
      {content}
    </CardActionArea>
  );
}

type InventoryDialogKey = "clusters" | "endpoints" | "gpu" | "support";

type InventoryData = NonNullable<NonNullable<WisperDashboardPayload["operational"]>["inventory"]>;

function InventoryDetailDialog({
  kind,
  inventory,
  clusterShapes,
  onClose,
}: {
  kind: InventoryDialogKey | null;
  inventory: InventoryData;
  clusterShapes: Array<{ shape: string; units: number }>;
  onClose: () => void;
}) {
  const titles: Record<InventoryDialogKey, string> = {
    clusters: "Units dedicadas — clusters DAC",
    endpoints: "Endpoints de inferência",
    gpu: "VMs GPU — hospedagem",
    support: "VMs de suporte — infraestrutura auxiliar",
  };
  const buckets: Record<InventoryDialogKey, string> = {
    clusters: "Generative AI",
    endpoints: "Generative AI",
    gpu: "Hospedagem GPU",
    support: "Infraestrutura",
  };

  const aiClusters = inventory.ai_clusters || [];
  const endpoints = inventory.endpoints || [];
  const computeGpu = inventory.compute_gpu || [];
  const computeSupport = inventory.compute_support || [];
  const byCompartment = inventory.by_compartment || [];

  const renderCompartmentTable = () => {
    if (!byCompartment.length) return null;
    return (
      <Box mt={3}>
        <Typography variant="subtitle2" gutterBottom>Distribuição por compartment</Typography>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Compartment</TableCell>
              <TableCell align="right">Clusters</TableCell>
              <TableCell align="right">Endpoints</TableCell>
              <TableCell align="right">VMs</TableCell>
              <TableCell align="right">Units GPU</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {byCompartment.map((row) => (
              <TableRow key={row.compartment}>
                <TableCell sx={{ wordBreak: "break-word" }}>{row.compartment}</TableCell>
                <TableCell align="right">{num(row.ai_clusters)}</TableCell>
                <TableCell align="right">{num(row.endpoints)}</TableCell>
                <TableCell align="right">{num(row.compute_instances)}</TableCell>
                <TableCell align="right">{num(row.gpu_units, 1)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    );
  };

  const renderStates = (states?: Record<string, number>) => {
    if (!states || !Object.keys(states).length) return "—";
    return Object.entries(states).map(([state, count]) => `${state}: ${count}`).join(" · ");
  };

  let body: React.ReactNode = null;

  if (kind === "clusters") {
    body = (
      <>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Clusters dedicados (DAC) provisionados via OCI GenAI API. Fonte: snapshot de inventário.
        </Typography>
        {clusterShapes.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>Resumo por shape</Typography>
            <Stack direction="row" gap={1} flexWrap="wrap">
              {clusterShapes.map((item) => (
                <Chip key={item.shape} label={`${item.shape}: ${num(item.units)} units`} size="small" variant="outlined" />
              ))}
            </Stack>
          </Box>
        )}
        {aiClusters.length ? (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Recurso</TableCell>
                <TableCell>Shape</TableCell>
                <TableCell align="right">Units</TableCell>
                <TableCell>Região</TableCell>
                <TableCell>Estado</TableCell>
                <TableCell>Compartment</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {aiClusters.map((item, index) => (
                <TableRow key={`${item.resource_name || "cluster"}-${index}`}>
                  <TableCell sx={{ wordBreak: "break-word" }}>{item.resource_name || "—"}</TableCell>
                  <TableCell>{item.unit_shape || "—"}</TableCell>
                  <TableCell align="right">{num(item.unit_count)}</TableCell>
                  <TableCell>{item.region || "—"}</TableCell>
                  <TableCell>{item.lifecycle_state || "—"}</TableCell>
                  <TableCell sx={{ wordBreak: "break-word" }}>{item.compartment_name || item.compartment_id || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Alert severity="info">Nenhum cluster dedicado encontrado no snapshot.</Alert>
        )}
        {renderCompartmentTable()}
      </>
    );
  } else if (kind === "endpoints") {
    const endpointsByModel = inventory.endpoints_by_model || [];
    body = (
      <>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Endpoints de inferência Generative AI. Agrupamento executivo por modelo; tabela abaixo lista cada recurso.
        </Typography>
        {endpointsByModel.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>Resumo por modelo</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Modelo</TableCell>
                  <TableCell align="right">Qtd.</TableCell>
                  <TableCell>Estados</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {endpointsByModel.map((item) => (
                  <TableRow key={item.model}>
                    <TableCell sx={{ wordBreak: "break-word" }}>{item.model}</TableCell>
                    <TableCell align="right">{num(item.count)}</TableCell>
                    <TableCell>{renderStates(item.states)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
        {endpoints.length ? (
          <>
            <Typography variant="subtitle2" gutterBottom>Detalhe por recurso</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Endpoint</TableCell>
                  <TableCell>Modelo</TableCell>
                  <TableCell>Região</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Compartment</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {endpoints.map((item, index) => (
                  <TableRow key={`${item.resource_name || "endpoint"}-${index}`}>
                    <TableCell sx={{ wordBreak: "break-word" }}>{item.resource_name || "—"}</TableCell>
                    <TableCell sx={{ wordBreak: "break-word" }}>{item.model_id || "—"}</TableCell>
                    <TableCell>{item.region || "—"}</TableCell>
                    <TableCell>{item.lifecycle_state || "—"}</TableCell>
                    <TableCell sx={{ wordBreak: "break-word" }}>{item.compartment_name || item.compartment_id || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : (
          <Alert severity="info">Nenhum endpoint encontrado no snapshot.</Alert>
        )}
        {renderCompartmentTable()}
      </>
    );
  } else if (kind === "gpu") {
    body = (
      <>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Instâncias Compute com shape GPU. Fonte: OCI Compute API · bucket Hospedagem GPU.
        </Typography>
        {computeGpu.length ? (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Shape</TableCell>
                <TableCell align="right">VMs</TableCell>
                <TableCell>Estados</TableCell>
                <TableCell>Fonte</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {computeGpu.map((item) => (
                <TableRow key={item.shape}>
                  <TableCell>{item.shape}</TableCell>
                  <TableCell align="right">{num(item.count)}</TableCell>
                  <TableCell>{renderStates(item.states)}</TableCell>
                  <TableCell>{item.source || "OCI Compute API"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Alert severity="info">Nenhuma VM GPU encontrada no snapshot.</Alert>
        )}
        {renderCompartmentTable()}
      </>
    );
  } else if (kind === "support") {
    body = (
      <>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          VMs de infraestrutura auxiliar (shapes sem GPU). Complementam a hospedagem dedicada.
        </Typography>
        {computeSupport.length ? (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Shape</TableCell>
                <TableCell align="right">VMs</TableCell>
                <TableCell>Estados</TableCell>
                <TableCell>Bucket</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {computeSupport.map((item) => (
                <TableRow key={item.shape}>
                  <TableCell>{item.shape}</TableCell>
                  <TableCell align="right">{num(item.count)}</TableCell>
                  <TableCell>{renderStates(item.states)}</TableCell>
                  <TableCell>{item.financial_bucket || "Infraestrutura"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Alert severity="info">Nenhuma VM de suporte encontrada no snapshot.</Alert>
        )}
        {renderCompartmentTable()}
      </>
    );
  }

  return (
    <Dialog open={kind !== null} onClose={onClose} maxWidth="lg" fullWidth>
      {kind && (
        <>
          <DialogTitle>
            <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
              <span>{titles[kind]}</span>
              <Chip size="small" label={`Bucket: ${buckets[kind]}`} />
            </Stack>
          </DialogTitle>
          <DialogContent dividers>{body}</DialogContent>
          <DialogActions>
            <Button onClick={onClose}>Fechar</Button>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
}

export default function WisperDashboard() {
  const [data, setData] = useState<WisperDashboardPayload | null>(null);
  const [error, setError] = useState("");
  const [showAllSkus, setShowAllSkus] = useState(false);
  const [expandedSku, setExpandedSku] = useState<string | null>(null);
  const [inventoryDialog, setInventoryDialog] = useState<InventoryDialogKey | null>(null);

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
  const insights = data.insights || [];
  const op = data.operational || {};
  const opKpis = op.kpis || {};
  const unitEco = data.unit_economics || {};
  const hosting = exec.hosting_dedicated || {};
  const totalClosed = exec.total_closed || 0;

  const months = data.monthly || [];
  const dailyClosed = capacity.daily_closed || [];
  const inventory = op.inventory || {};
  const computeGpu = inventory.compute_gpu || [];
  const computeSupport = inventory.compute_support || [];

  const tokens30d = op.tokens_30d || {};
  const totalTokens30d = unitEco.total_tokens ?? tokens30d.total_tokens ?? opKpis.total_tokens ?? 0;
  const inputTokens30d = unitEco.input_tokens ?? tokens30d.input_tokens ?? opKpis.input_tokens ?? 0;
  const outputTokens30d = unitEco.output_tokens ?? tokens30d.output_tokens ?? opKpis.output_tokens ?? 0;
  const tokenWindowDays = unitEco.telemetry_window_days || tokens30d.window_days || 30;
  const tokenDateFrom = unitEco.token_date_from || tokens30d.date_from;
  const tokenDateTo = unitEco.token_date_to || tokens30d.date_to;

  const tokenDaily = (op.daily || []).filter((d) =>
    Number(d.input_tokens || 0) > 0 || Number(d.output_tokens || 0) > 0,
  ).slice(-tokenWindowDays);

  const clusterShapeMap = new Map<string, number>();
  (inventory.ai_clusters || []).forEach((c) => {
    const shape = c.unit_shape || "Desconhecido";
    clusterShapeMap.set(shape, (clusterShapeMap.get(shape) || 0) + Number(c.unit_count || 0));
  });
  const clusterShapes = [...clusterShapeMap.entries()].map(([shape, units]) => ({ shape, units }));

  const topComponents = skus.slice(0, 5);
  const restComponents = skus.slice(5);
  const supportVmCount = computeSupport.reduce((sum, item) => sum + item.count, 0);

  return (
    <Stack gap={3}>
      <Box>
        <Typography variant="h4">Wisper — OCI AI</Typography>
        <Typography color="text.secondary">
          Quanto custa, quanto consome e com qual capacidade a plataforma opera.
        </Typography>
        <Stack direction="row" gap={1} mt={1.5} flexWrap="wrap">
          <Chip size="small" color="success" label={`Financeiro fechado: ${ref.closed_label || "—"} · FOCUS`} />
          <Chip size="small" color="warning" variant="outlined" label={`Mês em curso: ${ref.current_label || "—"} (parcial)`} />
          {ref.inventory_snapshot_at && (
            <Chip size="small" variant="outlined" label={`Inventário OCI: ${String(ref.inventory_snapshot_at).slice(0, 16).replace("T", " ")}`} />
          )}
        </Stack>
        {insights.map((ins, i) => (
          <Alert key={i} severity={ins.name === "step_up" ? "info" : "warning"} sx={{ mt: 1 }}>
            {ins.note}
          </Alert>
        ))}
      </Box>

      {/* Hero: consumo e eficiência (estilo Aura Brain / Vivo Mais) */}
      <Card sx={{ color: "white", background: `linear-gradient(120deg, ${WISPER_ACCENT}, #0F172A)` }}>
        <CardContent>
          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
            <Typography variant="overline" sx={{ opacity: 0.85 }}>Consumo e eficiência de IA</Typography>
            {unitEco.is_estimate && (
              <Chip size="small" label="estimativa" sx={{ bgcolor: "rgba(255,255,255,.18)", color: "white" }} />
            )}
          </Stack>
          <Grid container spacing={2} mt={0.25}>
            <Grid item xs={12} md={4}>
              <Typography variant="body2" sx={{ opacity: 0.8 }}>Hospedagem dedicada de IA</Typography>
              <Typography variant="h4">{cash(hosting.total)}</Typography>
              <Typography variant="caption" sx={{ opacity: 0.75 }}>
                {ref.closed_label} · GenAI {cash(hosting.generative_ai?.cost)} + GPU {cash(hosting.gpu_hosting?.cost)}
              </Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography variant="body2" sx={{ opacity: 0.8 }}>Milhões de tokens</Typography>
              <Typography variant="h4">
                {totalTokens30d > 0 ? formatMillions(Number(totalTokens30d)) : "—"}
              </Typography>
              <Typography variant="caption" sx={{ opacity: 0.75 }}>
                {totalTokens30d > 0 ? (
                  <>
                    input + output · últimos {tokenWindowDays} dias
                    {tokenDateFrom && tokenDateTo ? ` (${tokenDateFrom} a ${tokenDateTo})` : ""}
                  </>
                ) : (
                  <>sem telemetria de tokens nos últimos {tokenWindowDays || 30} dias</>
                )}
              </Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography variant="body2" sx={{ opacity: 0.8 }}>Custo Generative AI por MM de tokens</Typography>
              <Typography variant="h4">
                {unitEco.cost_per_mm_total_tokens != null ? cash2(unitEco.cost_per_mm_total_tokens) : "—"}
              </Typography>
              <Typography variant="caption" sx={{ opacity: 0.75 }}>
                apenas Generative AI · não inclui GPU
              </Typography>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {unitEco.note && <Alert severity="info">{unitEco.note}</Alert>}

      {/* Finanças */}
      <Box>
        <SectionTitle
          title="Finanças"
          subtitle={`Referência do mês fechado (${ref.closed_label}). Realizado: FOCUS · Forecast: Dimon/planning.`}
        />
        <Grid container spacing={2} sx={{ mb: 2 }}>
          <Grid item xs={12} sm={6} md={4}>
            <KpiCard
              label="Custo mês fechado"
              value={cash(totalClosed)}
              hint={exec.mom_delta_pct != null ? `${exec.mom_delta_pct > 0 ? "+" : ""}${pct(exec.mom_delta_pct, 1)} vs. anterior` : undefined}
              color="#0F172A"
            />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <KpiCard label="Mês em curso (parcial)" value={cash(exec.current_partial)} color="#F59E0B" />
          </Grid>
          <Grid item xs={12} sm={6} md={4}>
            <KpiCard
              label="Hospedagem dedicada de IA"
              value={cash(hosting.total)}
              hint={`${pct(hosting.share_pct)} do mês fechado`}
              color="#F97316"
              tooltip="Soma de Generative AI com Hospedagem GPU (compute + suporte)."
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
      </Box>

      {/* Capacidade e inventário */}
      <Box>
        <SectionTitle
          title="Capacidade e inventário"
          subtitle="Custo diário do mês fechado e recursos provisionados no snapshot OCI."
        />

        {dailyClosed.length > 0 && (
          <Card sx={{ mb: 2 }}>
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
                style={{ width: "100%", height: 280 }}
                useResizeHandler
              />
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>Inventário executivo OCI</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Snapshot das APIs OCI GenAI e Compute. Clique em um componente para ver o detalhamento técnico.
            </Typography>

            <Grid container spacing={2}>
              <Grid item xs={12} sm={6} md={3}>
                <InventoryStatCard
                  icon={<HubIcon />}
                  label="Units dedicadas"
                  value={num(inventory.totals?.gpu_units_dedicated || 0)}
                  hint="Generative AI · clusters DAC"
                  color={WISPER_ACCENT}
                  onClick={() => setInventoryDialog("clusters")}
                />
              </Grid>
              <Grid item xs={12} sm={6} md={3}>
                <InventoryStatCard
                  icon={<CloudQueueIcon />}
                  label="Endpoints"
                  value={num(inventory.totals?.endpoints || 0)}
                  hint="Generative AI · inferência"
                  color="#2563EB"
                  onClick={() => setInventoryDialog("endpoints")}
                />
              </Grid>
              <Grid item xs={12} sm={6} md={3}>
                <InventoryStatCard
                  icon={<MemoryIcon />}
                  label="VMs GPU"
                  value={num(computeGpu.reduce((s, r) => s + r.count, 0))}
                  hint="Hospedagem GPU"
                  color="#F97316"
                  onClick={() => setInventoryDialog("gpu")}
                />
              </Grid>
              <Grid item xs={12} sm={6} md={3}>
                <InventoryStatCard
                  icon={<DnsIcon />}
                  label="VMs de suporte"
                  value={num(supportVmCount)}
                  hint="Infraestrutura auxiliar"
                  color="#64748B"
                  onClick={() => setInventoryDialog("support")}
                />
              </Grid>
            </Grid>

            <InventoryDetailDialog
              kind={inventoryDialog}
              inventory={inventory}
              clusterShapes={clusterShapes}
              onClose={() => setInventoryDialog(null)}
            />
          </CardContent>
        </Card>
      </Box>

      {/* Operação */}
      <Box>
        <SectionTitle
          title="Operação e consumo de tokens"
          subtitle={`Telemetria Generative AI dos últimos ${tokenWindowDays} dias. Chamadas, latência e erros na mesma janela.`}
        />
        <Card>
          <CardContent>
            {op.available ? (
              <>
                <Grid container spacing={2} sx={{ mb: 2 }}>
                  {[
                    ["Tokens entrada", num(inputTokens30d)],
                    ["Tokens saída", num(outputTokens30d)],
                    ["Total tokens", num(totalTokens30d)],
                    ["Chamadas", num(opKpis.invocations)],
                    ["Latência p50", `${num(opKpis.latency, 1)} ms`],
                    ["Erros", num(opKpis.errors)],
                    ["Sucesso", opKpis.success_rate == null ? "—" : pct(opKpis.success_rate)],
                  ].map(([label, value]) => (
                    <Grid item xs={6} sm={4} md={3} lg={12 / 7} key={String(label)}>
                      <Typography variant="caption" color="text.secondary">{label}</Typography>
                      <Typography variant="body1" fontWeight={700}>{value}</Typography>
                    </Grid>
                  ))}
                </Grid>
                {tokenDaily.length > 0 ? (
                  <Box mt={1}>
                    <Typography variant="subtitle2" gutterBottom>
                      Consumo diário de tokens (input + output)
                    </Typography>
                    <Plot
                      data={[
                        {
                          x: tokenDaily.map((d) => d.date),
                          y: tokenDaily.map((d) => d.input_tokens ?? 0),
                          name: "Input",
                          type: "scatter",
                          mode: "lines",
                          stackgroup: "tokens",
                          line: { color: "#7C3AED", width: 2 },
                          fillcolor: "rgba(124, 58, 237, 0.25)",
                        },
                        {
                          x: tokenDaily.map((d) => d.date),
                          y: tokenDaily.map((d) => d.output_tokens ?? 0),
                          name: "Output",
                          type: "scatter",
                          mode: "lines",
                          stackgroup: "tokens",
                          line: { color: "#2563EB", width: 2 },
                          fillcolor: "rgba(37, 99, 235, 0.25)",
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
                      style={{ width: "100%", height: 280 }}
                      useResizeHandler
                    />
                  </Box>
                ) : (
                  <Alert severity="info" sx={{ mt: 1 }}>
                    Sem consumo de tokens na telemetria dos últimos {tokenWindowDays} dias.
                  </Alert>
                )}
              </>
            ) : (
              <Alert severity="warning">
                A telemetria OCI ainda não foi materializada.
              </Alert>
            )}
          </CardContent>
        </Card>
      </Box>

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
