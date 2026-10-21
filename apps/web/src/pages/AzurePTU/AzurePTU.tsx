import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Plot from "@/components/Plot";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, FormControl, Grid,
  InputLabel, MenuItem, Select, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { getAzurePtuOverview, getAzurePtuView } from "../../services/api";
import { exportAzurePtuPdf } from "./azurePtuPdfExport";

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });

const TAB_KEYS = ["overview", "projects", "deployments", "subprojects", "recommendations"] as const;
const TAB_LABELS = ["Visão executiva", "Projetos", "Operação", "Subprojetos", "Eficiência"];
const PERIODS = [
  { id: "7d", label: "Últimos 7 dias", hours: 7 * 24, grain: "hour" },
  { id: "30d", label: "Últimos 30 dias", hours: 30 * 24, grain: "day" },
  { id: "90d", label: "Últimos 90 dias", hours: 90 * 24, grain: "day" },
] as const;
const PROJECT_KEYS = ["vivo-mais", "aura-brain"] as const;
const INFRA_COST_CLASSES = ["vm", "storage", "network", "other_infra"] as const;
const GRAIN_LABELS: Record<string, string> = { hour: "Hora", day: "Dia", month: "Mês" };
const PROJECT_LABELS: Record<string, string> = { todos: "Todos", "vivo-mais": "Vivo Mais", "aura-brain": "Aura Brain" };
const COST_CLASS_LABELS: Record<string, string> = {
  ptu: "PTU capacidade",
  ia_ondemand: "IA OnDemand / Foundry / tokens",
  vm: "VM / compute",
  storage: "Storage",
  network: "Network",
  other_infra: "Outra infra Azure",
};
const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const UTIL_HEATMAP_SCALE: [number, string][] = [
  [0, "#E8F5E9"],
  [0.35, "#81C784"],
  [0.7, "#2E7D32"],
  [1, "#1B5E20"],
];

const bandColor = (band?: string | null) => {
  if (band === "0-30") return "warning";
  if (band === "80-95" || band === "95-100") return "error";
  return "success";
};

const chartLayout = (yTitle: string): Record<string, unknown> => ({
  autosize: true,
  margin: { l: 60, r: 24, t: 24, b: 60 },
  hovermode: "x unified",
  xaxis: { title: { text: "Período" } },
  yaxis: { title: { text: yTitle }, rangemode: "tozero", ticksuffix: "%" },
  legend: { orientation: "h", y: -0.2 },
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  shapes: [
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 30, y1: 30, line: { color: "#F59E0B", dash: "dot", width: 1 } },
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 80, y1: 80, line: { color: "#EF4444", dash: "dot", width: 1 } },
    { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 95, y1: 95, line: { color: "#B91C1C", dash: "dash", width: 1 } },
  ],
});

export default function AzurePTU() {
  const [data, setData] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [project, setProject] = useState("todos");
  const [period, setPeriod] = useState<(typeof PERIODS)[number]["id"]>("90d");
  const [grain, setGrain] = useState("day");

  const selectedPeriod = PERIODS.find(p => p.id === period) || PERIODS[2];
  const filters = useMemo(() => ({
    grain,
    hours: selectedPeriod.hours,
    project_keys: project === "todos" ? [] : [project],
  }), [grain, project, selectedPeriod.hours]);

  const filtersKey = useMemo(() => JSON.stringify(filters), [filters]);
  const overviewAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      overviewAbortRef.current?.abort();
      const controller = new AbortController();
      overviewAbortRef.current = controller;
      setError(null);
      getAzurePtuOverview(filters, controller.signal)
        .then(payload => {
          if (!controller.signal.aborted) setData(payload);
        })
        .catch(e => {
          if ((e as { code?: string }).code === "ERR_CANCELED") return;
          setError(e?.response?.data?.detail || "Falha ao consultar Azure PTU");
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      overviewAbortRef.current?.abort();
    };
  }, [filtersKey, filters]);

  useEffect(() => {
    if (TAB_KEYS[tab] !== "subprojects") {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    getAzurePtuView("subprojects", filters, controller.signal)
      .then(setDetail)
      .catch(() => undefined);
    return () => controller.abort();
  }, [tab, filtersKey, filters]);

  const utilizationSeries = data?.utilization_series || [];
  const tokenSeries = data?.token_series || [];
  const costSeries = data?.cost_series || [];
  const costPerMillionSeries = data?.cost_per_million_series || [];
  const originCosts = data?.origin_costs || [];
  const heatmapCells = data?.utilization_heatmap || [];
  const deploymentStats = data?.deployment_utilization || [];
  const imbalance = data?.deployment_imbalance || [];
  const recommendations = data?.recommendations || [];
  const iaOndemand = data?.ia_ondemand || data?.kpis?.ia_ondemand || {};
  const sharedInfra = data?.shared_infra || data?.kpis?.shared_infra || {};
  const costRows = data?.cost_rows || [];
  const costComposition = data?.cost_composition || data?.kpis?.cost_composition || {};

  const ptuCostRows = useMemo(
    () => costRows.filter((row: { cost_class?: string }) => row.cost_class === "ptu"),
    [costRows],
  );
  const iaOndemandCostRows = useMemo(
    () => costRows.filter((row: { cost_class?: string }) => row.cost_class === "ia_ondemand"),
    [costRows],
  );
  const infraCostRows = useMemo(
    () => costRows.filter((row: { cost_class?: string }) => INFRA_COST_CLASSES.includes(row.cost_class as typeof INFRA_COST_CLASSES[number])),
    [costRows],
  );

  const heatmapMatrix = useMemo(() => {
    const matrix = Array.from({ length: 7 }, () => Array(24).fill(null as number | null));
    for (const cell of heatmapCells) {
      if (cell.weekday >= 0 && cell.weekday < 7 && cell.hour >= 0 && cell.hour < 24) {
        matrix[cell.weekday][cell.hour] = cell.utilization_avg;
      }
    }
    return matrix;
  }, [heatmapCells]);

  if (error && !data) return <Box p={3}><Alert severity="error">{dataDisplayText(error)}</Alert></Box>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;

  const k = data.kpis || {};
  const projects = Object.values(data.projects || {}) as any[];

  const kpi = (label: string, value: string, hint?: string) => (
    <Grid item xs={12} sm={6} md={3} key={label}>
      <Card className="azure-ptu-print-card"><CardContent>
        <Typography color="text.secondary" variant="body2">{label}</Typography>
        <Typography variant="h6">{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
      </CardContent></Card>
    </Grid>
  );

  const handleExportPdf = () => {
    exportAzurePtuPdf({
      periodId: period,
      periodLabel: selectedPeriod.label,
      projectLabel: PROJECT_LABELS[project] || project,
      grainLabel: GRAIN_LABELS[grain] || grain,
      tabLabel: TAB_LABELS[tab],
      dataSourceLabel: data.data_source === "gold" ? "Base consolidada" : "Azure Monitor live",
    });
  };

  const exportedAt = new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date());

  return (
    <Box p={3} className="azure-ptu-export-root">
      <Box className="azure-ptu-print-meta" data-print-only>
        <h1>Azure OpenAI PTU — Relatório</h1>
        <p><strong>Período:</strong> {selectedPeriod.label}</p>
        <p><strong>Projeto:</strong> {PROJECT_LABELS[project] || project} · <strong>Granularidade:</strong> {GRAIN_LABELS[grain] || grain}</p>
        <p><strong>Aba:</strong> {TAB_LABELS[tab]} · <strong>Fonte:</strong> {data.data_source === "gold" ? "Base consolidada" : "Azure Monitor live"}</p>
        <p><strong>Exportado em:</strong> {exportedAt}</p>
        <p style={{ marginTop: 8, fontStyle: "italic" }}>
          Gráficos interativos abaixo refletem o recorte atual na tela. Para dados tabulares completos, consulte as tabelas neste PDF.
        </p>
      </Box>

      <Typography variant="h4" gutterBottom data-print-hide>Azure OpenAI PTU</Typography>
      <Typography color="text.secondary" gutterBottom data-print-hide>
        Capacidade, utilização Azure Monitor (ProvisionedManagedUtilizationV2), custo FOCUS e comparação entre projetos/deployments.
      </Typography>
      <Stack direction="row" gap={2} mb={2} flexWrap="wrap" data-print-hide>
        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel>Projeto</InputLabel>
          <Select label="Projeto" value={project} onChange={e => setProject(e.target.value)}>
            <MenuItem value="todos">Todos</MenuItem>
            <MenuItem value="vivo-mais">Vivo Mais</MenuItem>
            <MenuItem value="aura-brain">Aura Brain</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>Período</InputLabel>
          <Select
            label="Período"
            value={period}
            onChange={e => {
              const next = PERIODS.find(p => p.id === e.target.value);
              setPeriod((e.target.value as typeof period));
              if (next) setGrain(next.grain);
            }}
          >
            {PERIODS.map(p => <MenuItem key={p.id} value={p.id}>{p.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>Granularidade</InputLabel>
          <Select label="Granularidade" value={grain} onChange={e => setGrain(e.target.value)}>
            <MenuItem value="hour">Hora</MenuItem>
            <MenuItem value="day">Dia</MenuItem>
            <MenuItem value="month">Mês</MenuItem>
          </Select>
        </FormControl>
        <Chip size="small" label={`Fonte: ${data.data_source === "gold" ? "Base consolidada" : data.data_source === "stale" ? "Dados parciais" : "Azure Monitor live"}`} />
        <Button component={RouterLink} to="/dados/catalogo/tabelas" size="small" variant="outlined">Origem de custo IA</Button>
        <Button
          size="small"
          variant="contained"
          startIcon={<PictureAsPdfIcon />}
          onClick={handleExportPdf}
        >
          Exportar PDF
        </Button>
      </Stack>

      <FormControl size="small" sx={{ mb: 2, minWidth: 220 }} data-print-hide>
        <InputLabel id="legacy-ptu-view-label">Visão</InputLabel>
        <Select labelId="legacy-ptu-view-label" value={tab} label="Visão" onChange={event => setTab(Number(event.target.value))}>
          {TAB_LABELS.map((label, index) => <MenuItem key={label} value={index}>{label}</MenuItem>)}
        </Select>
      </FormControl>

      {error && (
        <Alert severity="warning" sx={{ mb: 2 }}>{dataDisplayText(error)} — exibindo último recorte disponível.</Alert>
      )}

      {tab === 0 && (
        <Stack gap={4} mb={3}>
          {/* Bloco 1 — PTU operacional */}
          <Box sx={{ pl: 2, borderLeft: 4, borderColor: "primary.main" }}>
            <Typography variant="h5" gutterBottom>PTU — capacidade e utilização</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Capacidade provisionada, utilização Azure Monitor e custo FOCUS de meters PTU (não inclui OnDemand/PAYG nem infra auxiliar).
            </Typography>
            <Grid container spacing={2} mb={2}>
              {kpi("PTUs totais", String(k.total_ptus ?? "—"))}
              {kpi("Reservados / OnDemand (PTU)", `${num.format(k.reserved_ptus || 0)} / ${num.format(k.ondemand_ptus || 0)}`)}
              {kpi("Custo PTU (capacidade)", brl.format((costComposition.ptu_capacity ?? k.realized_cost) || 0), "FOCUS meters provisionados")}
              {kpi("R$ / MM tokens", k.cost_per_million_tokens != null ? brl.format(k.cost_per_million_tokens) : "—", "custo PTU ÷ tokens Monitor")}
              {kpi("Input / Output tokens", `${num.format(k.input_tokens || 0)} / ${num.format(k.output_tokens || 0)}`)}
              {kpi("Utilização média", `${num.format(k.utilization_avg || 0)}%`, "AzureOpenAIProvisionedManagedUtilizationV2")}
              {kpi("Utilização P95", k.utilization_p95 != null ? `${num.format(k.utilization_p95)}%` : "—")}
              {kpi("Pico de utilização", k.peak_utilization != null ? `${num.format(k.peak_utilization)}%` : "—")}
              {kpi("Capacidade não aproveitada", pct.format(k.unused_capacity_pct || 0), k.unused_capacity_wording_reservation)}
              {kpi("Custo ocioso (est.)", brl.format(k.unused_capacity_cost_estimate || 0), k.unused_capacity_wording_ondemand)}
            </Grid>

            {imbalance.length > 0 && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <Typography fontWeight={700} gutterBottom>Desbalanceamento entre deployments</Typography>
                {imbalance.map((item: any) => (
                  <Typography key={item.project_key} variant="body2">
                    {item.project_key}: spread {num.format(item.utilization_spread)} p.p. ({item.ptus_split})
                  </Typography>
                ))}
              </Alert>
            )}

            <Stack gap={2}>
              <Card className="azure-ptu-print-card">
                <CardContent>
                  <Typography variant="h6" gutterBottom>Série de utilização (média / P95 / pico)</Typography>
                  {utilizationSeries.length > 0 ? (
                    <Plot
                      data={([
                        { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_avg), name: "Média", type: "scatter", mode: "lines", line: { color: "#2563EB" } },
                        { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_p95), name: "P95", type: "scatter", mode: "lines", line: { color: "#7C3AED", dash: "dot" } },
                        { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_max), name: "Pico", type: "scatter", mode: "lines", line: { color: "#DC2626", dash: "dash" } },
                      ] as any)}
                      layout={chartLayout("Utilização (%)")}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", height: 320 }}
                      useResizeHandler
                    />
                  ) : (
                    <Alert severity="info">Sem série temporal — aguarde coleta base consolidada ou conexão Azure Monitor.</Alert>
                  )}
                </CardContent>
              </Card>

              <Card className="azure-ptu-print-card">
                <CardContent>
                  <Typography variant="h6" gutterBottom>Custo PTU vs tokens (série histórica)</Typography>
                  {costSeries.length > 0 || tokenSeries.length > 0 ? (
                    <Plot
                      data={([
                        ...(costSeries.length > 0 ? [{
                          x: costSeries.map((r: { timestamp: string }) => r.timestamp),
                          y: costSeries.map((r: { realized_cost: number }) => r.realized_cost),
                          name: "Custo realizado (FOCUS)",
                          type: "scatter",
                          mode: "lines+markers",
                          yaxis: "y2",
                          line: { color: "#059669" },
                        }] : []),
                        ...(tokenSeries.length > 0 ? [{
                          x: tokenSeries.map((r: { timestamp: string }) => r.timestamp),
                          y: tokenSeries.map((r: { total_tokens: number }) => r.total_tokens),
                          name: "Tokens (base consolidada)",
                          type: "scatter",
                          mode: "lines",
                          line: { color: "#2563EB" },
                        }] : []),
                      ] as any)}
                      layout={{
                        autosize: true,
                        margin: { l: 60, r: 60, t: 24, b: 60 },
                        hovermode: "x unified",
                        xaxis: { title: { text: "Período" } },
                        yaxis: { title: { text: "Tokens" }, rangemode: "tozero" },
                        yaxis2: { title: { text: "Custo (BRL)" }, overlaying: "y", side: "right", rangemode: "tozero" },
                        legend: { orientation: "h", y: -0.2 },
                        paper_bgcolor: "transparent",
                        plot_bgcolor: "transparent",
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", height: 320 }}
                      useResizeHandler
                    />
                  ) : (
                    <Alert severity="info">Sem série histórica — execute Reprocessar na fonte PTU para carregar até 93 dias.</Alert>
                  )}
                </CardContent>
              </Card>

              <Card className="azure-ptu-print-card">
                <CardContent>
                  <Typography variant="h6" gutterBottom>Heatmap utilização (dia × hora)</Typography>
                  {heatmapCells.length > 0 ? (
                    <Plot
                      data={[{
                        z: heatmapMatrix,
                        x: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}h`),
                        y: WEEKDAYS,
                        type: "heatmap",
                        colorscale: UTIL_HEATMAP_SCALE,
                        zmin: 0,
                        zmax: 100,
                        hovertemplate: "%{y} %{x}: %{z:.1f}%<extra></extra>",
                      }]}
                      layout={{ autosize: true, margin: { l: 60, r: 24, t: 20, b: 40 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", height: 280 }}
                      useResizeHandler
                    />
                  ) : (
                    <Alert severity="info">Heatmap indisponível sem métricas horárias.</Alert>
                  )}
                </CardContent>
              </Card>

              {originCosts.length > 0 && (
                <Card className="azure-ptu-print-card">
                  <CardContent>
                    <Typography variant="h6" gutterBottom>Origem de custo PTU × tokens (R$/MM)</Typography>
                    <Table size="small">
                      <TableHead><TableRow>
                        {["Origem", "Projeto", "Modo", "Custo FOCUS", "Tokens Monitor", "R$/MM"].map(h => <TableCell key={h}>{h}</TableCell>)}
                      </TableRow></TableHead>
                      <TableBody>
                        {originCosts.map((row: any) => (
                          <TableRow key={row.link_id || row.link_name}>
                            <TableCell>{row.link_name}</TableCell>
                            <TableCell>{row.project_key}</TableCell>
                            <TableCell>{row.pricing_label || row.purchase_mode || "—"}</TableCell>
                            <TableCell>{brl.format(row.origin_cost || 0)}</TableCell>
                            <TableCell>{num.format(row.total_tokens || 0)}</TableCell>
                            <TableCell>{row.cost_per_million_tokens != null ? brl.format(row.cost_per_million_tokens) : "—"}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              )}

              {ptuCostRows.length > 0 && (
                <Card className="azure-ptu-print-card">
                  <CardContent>
                    <Typography variant="h6" gutterBottom>Detalhe FOCUS — PTU capacidade</Typography>
                    <Table size="small">
                      <TableHead><TableRow>
                        {["Projeto", "Classe", "ServiceName", "Realizado"].map(h => <TableCell key={h}>{h}</TableCell>)}
                      </TableRow></TableHead>
                      <TableBody>
                        {[...ptuCostRows].sort((a: any, b: any) => (b.realized || 0) - (a.realized || 0)).slice(0, 20).map((row: any, idx: number) => (
                          <TableRow key={`ptu-${row.project_key}-${row.service_name}-${idx}`}>
                            <TableCell>{PROJECT_LABELS[row.project_key] || row.project_key}</TableCell>
                            <TableCell>{COST_CLASS_LABELS[row.cost_class] || row.cost_class}</TableCell>
                            <TableCell>{row.service_name}</TableCell>
                            <TableCell>{brl.format(row.realized || 0)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              )}
            </Stack>
          </Box>

          {/* Bloco 2 — IA OnDemand */}
          <Box sx={{ pl: 2, borderLeft: 4, borderColor: "secondary.main" }}>
            <Typography variant="h5" gutterBottom>IA OnDemand</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Foundry, tokens PAYG e serviços Azure AI / Cognitive fora da capacidade PTU. Aura Brain aqui é consumo OnDemand do projeto — não é condomínio nem rateio de infra compartilhada.
            </Typography>
            <Grid container spacing={2} mb={2}>
              {kpi("IA OnDemand (PAYG)", brl.format(costComposition.ia_ondemand_total || 0), "Foundry, tokens e serviços Azure AI")}
              {kpi("Custo total IA", brl.format(costComposition.total_ia || 0), "PTU capacidade + OnDemand/PAYG")}
              {kpi("PTU capacidade (referência)", brl.format((costComposition.ptu_capacity ?? k.realized_cost) || 0), "Para comparação com OnDemand")}
            </Grid>
            <Card className="azure-ptu-print-card" sx={{ mb: 2 }}>
              <CardContent>
                <Typography variant="h6" gutterBottom>Custo OnDemand por projeto</Typography>
                <Table size="small">
                  <TableHead><TableRow>
                    {["Projeto", "IA OnDemand", "PTU capacidade", "Total IA"].map(h => <TableCell key={h}>{h}</TableCell>)}
                  </TableRow></TableHead>
                  <TableBody>
                    {PROJECT_KEYS.map(projectKey => {
                      const ondemandCost = Number((iaOndemand as Record<string, number>)[projectKey] || 0);
                      const projectRow = (data.projects || {})[projectKey] || {};
                      const ptuCost = Number(projectRow.ptu_capacity_cost ?? (costComposition.ptu_capacity || 0));
                      return (
                        <TableRow key={projectKey}>
                          <TableCell>{PROJECT_LABELS[projectKey] || projectKey}</TableCell>
                          <TableCell>{brl.format(ondemandCost)}</TableCell>
                          <TableCell>{brl.format(ptuCost)}</TableCell>
                          <TableCell>{brl.format(ondemandCost + ptuCost)}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
            <Card className="azure-ptu-print-card">
              <CardContent>
                <Typography variant="h6" gutterBottom>Detalhe FOCUS — IA OnDemand</Typography>
                {iaOndemandCostRows.length > 0 ? (
                  <Table size="small">
                    <TableHead><TableRow>
                      {["Projeto", "Classe", "ServiceName", "Realizado"].map(h => <TableCell key={h}>{h}</TableCell>)}
                    </TableRow></TableHead>
                    <TableBody>
                      {[...iaOndemandCostRows].sort((a: any, b: any) => (b.realized || 0) - (a.realized || 0)).slice(0, 20).map((row: any, idx: number) => (
                        <TableRow key={`ia-${row.project_key}-${row.service_name}-${idx}`}>
                          <TableCell>{PROJECT_LABELS[row.project_key] || row.project_key}</TableCell>
                          <TableCell>{COST_CLASS_LABELS[row.cost_class] || row.cost_class}</TableCell>
                          <TableCell>{row.service_name}</TableCell>
                          <TableCell>{brl.format(row.realized || 0)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <Alert severity="info">Sem linhas FOCUS de IA OnDemand no período — estrutura pronta para quando os dados chegarem.</Alert>
                )}
              </CardContent>
            </Card>
          </Box>

          {/* Bloco 3 — Infraestrutura auxiliar */}
          <Box sx={{ pl: 2, borderLeft: 4, borderColor: "grey.500" }}>
            <Typography variant="h5" gutterBottom>Infraestrutura auxiliar do projeto</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              VM, Storage, Network e demais recursos Azure dos projetos Aura Brain e Vivo Mais. Custos auxiliares do departamento — não são condomínio nem rateio de PTU.
            </Typography>
            <Grid container spacing={2} mb={2}>
              {kpi("Total infra auxiliar", brl.format(costComposition.shared_infra_total || 0), "VM + Storage + Network + outra infra")}
              {kpi("VM / compute", brl.format(PROJECT_KEYS.reduce((sum, key) => sum + Number((sharedInfra as Record<string, any>)[key]?.vm || 0), 0)))}
              {kpi("Storage", brl.format(PROJECT_KEYS.reduce((sum, key) => sum + Number((sharedInfra as Record<string, any>)[key]?.storage || 0), 0)))}
              {kpi("Network", brl.format(PROJECT_KEYS.reduce((sum, key) => sum + Number((sharedInfra as Record<string, any>)[key]?.network || 0), 0)))}
            </Grid>
            <Card className="azure-ptu-print-card" sx={{ mb: 2 }}>
              <CardContent>
                <Typography variant="h6" gutterBottom>Custos auxiliares por projeto</Typography>
                <Table size="small">
                  <TableHead><TableRow>
                    {["Projeto", "VM / compute", "Storage", "Network", "Outra infra", "Total auxiliar"].map(h => <TableCell key={h}>{h}</TableCell>)}
                  </TableRow></TableHead>
                  <TableBody>
                    {PROJECT_KEYS.map(projectKey => {
                      const row = (sharedInfra as Record<string, any>)[projectKey] || {};
                      return (
                        <TableRow key={projectKey}>
                          <TableCell>{PROJECT_LABELS[projectKey] || projectKey}</TableCell>
                          <TableCell>{brl.format(row.vm || 0)}</TableCell>
                          <TableCell>{brl.format(row.storage || 0)}</TableCell>
                          <TableCell>{brl.format(row.network || 0)}</TableCell>
                          <TableCell>{brl.format(row.other_infra || 0)}</TableCell>
                          <TableCell>{brl.format(row.total || 0)}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
            <Card className="azure-ptu-print-card">
              <CardContent>
                <Typography variant="h6" gutterBottom>Detalhe FOCUS — infraestrutura</Typography>
                {infraCostRows.length > 0 ? (
                  <Table size="small">
                    <TableHead><TableRow>
                      {["Projeto", "Classe", "ServiceName", "Realizado"].map(h => <TableCell key={h}>{h}</TableCell>)}
                    </TableRow></TableHead>
                    <TableBody>
                      {[...infraCostRows].sort((a: any, b: any) => (b.realized || 0) - (a.realized || 0)).slice(0, 20).map((row: any, idx: number) => (
                        <TableRow key={`infra-${row.project_key}-${row.service_name}-${idx}`}>
                          <TableCell>{PROJECT_LABELS[row.project_key] || row.project_key}</TableCell>
                          <TableCell>{COST_CLASS_LABELS[row.cost_class] || row.cost_class}</TableCell>
                          <TableCell>{row.service_name}</TableCell>
                          <TableCell>{brl.format(row.realized || 0)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <Alert severity="info">Sem linhas FOCUS de infraestrutura no período — estrutura pronta para quando os dados chegarem.</Alert>
                )}
              </CardContent>
            </Card>
          </Box>
        </Stack>
      )}

      {tab === 2 && (
        <Stack gap={2} mb={3}>
          {imbalance.length > 0 && (
            <Alert severity="warning">
              <Typography fontWeight={700} gutterBottom>Desbalanceamento entre deployments</Typography>
              {imbalance.map((item: any) => (
                <Typography key={item.project_key} variant="body2">
                  {item.project_key}: spread {num.format(item.utilization_spread)} p.p. ({item.ptus_split})
                </Typography>
              ))}
            </Alert>
          )}

          <Card className="azure-ptu-print-card">
            <CardContent>
              <Typography variant="h6" gutterBottom>Série de utilização (média / P95 / pico)</Typography>
              {utilizationSeries.length > 0 ? (
                <Plot
                  data={[
                    { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_avg), name: "Média", type: "scatter", mode: "lines", line: { color: "#2563EB" } },
                    { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_p95), name: "P95", type: "scatter", mode: "lines", line: { color: "#7C3AED", dash: "dot" } },
                    { x: utilizationSeries.map((r: any) => r.timestamp), y: utilizationSeries.map((r: any) => r.utilization_max), name: "Pico", type: "scatter", mode: "lines", line: { color: "#DC2626", dash: "dash" } },
                  ]}
                  layout={chartLayout("Utilização (%)")}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 320 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Sem série temporal — aguarde coleta base consolidada ou conexão Azure Monitor.</Alert>
              )}
            </CardContent>
          </Card>

          <Card className="azure-ptu-print-card">
            <CardContent>
              <Typography variant="h6" gutterBottom>Heatmap utilização (dia × hora)</Typography>
              {heatmapCells.length > 0 ? (
                <Plot
                  data={[{
                    z: heatmapMatrix,
                    x: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}h`),
                    y: WEEKDAYS,
                    type: "heatmap",
                    colorscale: UTIL_HEATMAP_SCALE,
                    zmin: 0,
                    zmax: 100,
                    hovertemplate: "%{y} %{x}: %{z:.1f}%<extra></extra>",
                  }]}
                  layout={{ autosize: true, margin: { l: 60, r: 24, t: 20, b: 40 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: 280 }}
                  useResizeHandler
                />
              ) : (
                <Alert severity="info">Heatmap indisponível sem métricas horárias.</Alert>
              )}
            </CardContent>
          </Card>

          <Card className="azure-ptu-print-card">
            <CardContent>
              <Typography variant="h6" gutterBottom>Comparação por deployment</Typography>
              <Table size="small">
                <TableHead><TableRow>
                  {["Projeto", "Deployment", "Modelo", "Região", "Tipo", "Preço", "PTUs", "TPM in", "TPM out", "Pico RPM", "Latência", "Média", "P95", "Pico", "Ociosa %", "Faixa"].map(h => <TableCell key={h}>{h}</TableCell>)}
                </TableRow></TableHead>
                <TableBody>
                  {deploymentStats.map((d: any) => (
                    <TableRow key={d.deployment_name}>
                      <TableCell>{d.project_key || "não mapeado"}</TableCell>
                      <TableCell>{d.deployment_name}</TableCell>
                      <TableCell>{d.model_name || "—"}</TableCell>
                      <TableCell>{d.region || "—"}</TableCell>
                      <TableCell>{d.deployment_type || "—"}</TableCell>
                      <TableCell>{d.pricing_label || "—"}</TableCell>
                      <TableCell>{d.ptus ?? "—"}</TableCell>
                      <TableCell>{d.input_tpm != null ? num.format(d.input_tpm) : "—"}</TableCell>
                      <TableCell>{d.output_tpm != null ? num.format(d.output_tpm) : "—"}</TableCell>
                      <TableCell>{d.request_peak_per_minute != null ? num.format(d.request_peak_per_minute) : "—"}</TableCell>
                      <TableCell>{d.time_to_response_avg_ms != null ? `${num.format(d.time_to_response_avg_ms)} ms` : "—"}</TableCell>
                      <TableCell>{num.format(d.utilization_avg || 0)}%</TableCell>
                      <TableCell>{d.utilization_p95 != null ? `${num.format(d.utilization_p95)}%` : "—"}</TableCell>
                      <TableCell>{d.utilization_max != null ? `${num.format(d.utilization_max)}%` : "—"}</TableCell>
                      <TableCell>{pct.format(d.unused_capacity_pct || 0)}</TableCell>
                      <TableCell><Chip size="small" color={bandColor(d.utilization_band)} label={d.utilization_band || "—"} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </Stack>
      )}

      {tab === 1 && (
        <Grid container spacing={2}>
          {projects.map((p: any) => (
            <Grid item xs={12} md={6} key={p.project_key}>
              <Card className="azure-ptu-print-card">
                <CardContent>
                  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
                    <Typography variant="h6">{p.project_key}</Typography>
                    <Chip size="small" label={`${p.ptus || 0} PTUs`} />
                  </Stack>
                  <Typography color="text.secondary" gutterBottom>
                    {p.deployments || 0} deployments · {num.format(p.tokens || 0)} tokens · {p.pricing_label || ""}
                  </Typography>
                  <Typography>Custo PTU: {brl.format(p.ptu_capacity_cost ?? p.origin_cost ?? 0)} · R$/MM: {p.cost_per_million_tokens != null ? brl.format(p.cost_per_million_tokens) : "—"}</Typography>
                  {p.ia_ondemand_cost != null && (
                    <Typography variant="body2">IA OnDemand (PAYG): {brl.format(p.ia_ondemand_cost)} · Total IA: {brl.format(p.total_ia_cost || 0)}</Typography>
                  )}
                  <Typography>Média: {num.format(p.utilization_avg || 0)}% · P95: {p.utilization_p95 != null ? `${num.format(p.utilization_p95)}%` : "—"}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Ociosa: {pct.format(p.unused_capacity_pct || 0)} · Faixa {p.utilization_band || "—"}
                  </Typography>
                  {(p.deployment_comparison || []).length > 0 && (
                    <Box mt={2}>
                      <Plot
                        data={[{
                          x: p.deployment_comparison.map((d: any) => d.deployment_name),
                          y: p.deployment_comparison.map((d: any) => d.utilization_avg || 0),
                          type: "bar",
                          marker: { color: "#2563EB" },
                          name: "Utilização média",
                        }]}
                        layout={{ ...chartLayout("Utilização (%)"), margin: { l: 50, r: 16, t: 10, b: 80 }, showlegend: false }}
                        config={{ displayModeBar: false, responsive: true }}
                        style={{ width: "100%", height: 220 }}
                        useResizeHandler
                      />
                    </Box>
                  )}
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {tab === 3 && (
        <Stack gap={2}>
          <Alert severity="info">
            Atribuição por subprojeto depende de eventos instrumentados. PTU-equivalente é estimativa (tokens subprojeto / tokens Monitor).
          </Alert>
          <Card className="azure-ptu-print-card">
            <CardContent>
              <Typography variant="h6" gutterBottom>Subprojetos cadastrados</Typography>
              <Table size="small">
                <TableHead><TableRow>
                  {["Projeto", "Subprojeto", "Nome", "Ambiente"].map(h => <TableCell key={h}>{h}</TableCell>)}
                </TableRow></TableHead>
                <TableBody>
                  {(detail?.subprojects || []).map((s: any) => (
                    <TableRow key={s.id || `${s.project_key}-${s.subproject_key}`}>
                      <TableCell>{s.project_key}</TableCell>
                      <TableCell>{s.subproject_key}</TableCell>
                      <TableCell>{s.name}</TableCell>
                      <TableCell>{s.environment || "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          {(detail?.attribution || []).length > 0 && (
            <Card className="azure-ptu-print-card">
              <CardContent>
                <Typography variant="h6" gutterBottom>PTU-equivalente por subprojeto</Typography>
                <Table size="small">
                  <TableHead><TableRow>
                    {["Projeto", "Subprojeto", "Tokens", "PTU-equiv. %"].map(h => <TableCell key={h}>{h}</TableCell>)}
                  </TableRow></TableHead>
                  <TableBody>
                    {detail.attribution.map((a: any) => (
                      <TableRow key={`${a.project_key}-${a.subproject_key}`}>
                        <TableCell>{a.project_key}</TableCell>
                        <TableCell>{a.subproject_key}</TableCell>
                        <TableCell>{num.format(a.total_tokens || 0)}</TableCell>
                        <TableCell>{a.ptu_equivalent_pct != null ? `${num.format(a.ptu_equivalent_pct)}%` : "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </Stack>
      )}

      {tab === 4 && (
        <Stack gap={1}>
          {recommendations.map((rec: any) => (
            <Alert key={rec.code} severity={rec.severity === "high" ? "error" : "warning"}>
              <Typography fontWeight={700}>{rec.code}</Typography>
              <Typography variant="body2">Evidência: {rec.evidence}</Typography>
              <Typography variant="body2">Impacto: {rec.impact} · Confiança: {rec.confidence}</Typography>
              <Typography variant="body2">{rec.action}</Typography>
            </Alert>
          ))}
          {!recommendations.length && <Alert severity="info">Nenhuma recomendação no recorte atual.</Alert>}
        </Stack>
      )}
    </Box>
  );
}
