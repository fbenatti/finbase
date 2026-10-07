import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Card,
  CardContent,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import Plot from "@/components/Plot";
import {
  CommitmentsDashboard as CommitmentsDashboardData,
  CommitmentDashboardRow,
  getCommitmentsDashboard,
} from "../../services/api";

const currentMonth = new Date().toISOString().slice(0, 7);
const currentYear = currentMonth.slice(0, 4);
const toPeriod = (month: string) => month.replace("-", "");
const money = (value: unknown) =>
  new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(
    Number(value || 0),
  );
const monthLabel = (period: string) => `${period.slice(4, 6)}/${period.slice(0, 4)}`;

function MetricCard({ title, value, detail }: { title: string; value: number; detail?: string }) {
  return (
    <Card sx={{ flex: 1, minWidth: 210 }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">{title}</Typography>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>{money(value)}</Typography>
        {detail && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{detail}</Typography>}
      </CardContent>
    </Card>
  );
}

export default function CommitmentsDashboard() {
  const [startMonth, setStartMonth] = useState(`${currentYear}-01`);
  const [endMonth, setEndMonth] = useState(`${currentYear}-12`);
  const [source, setSource] = useState<"all" | "azure-ti" | "azure-4p">("all");
  const [data, setData] = useState<CommitmentsDashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    if (!startMonth || !endMonth || startMonth > endMonth) {
      setError("O período inicial deve ser anterior ou igual ao período final.");
      setData(null);
      return () => { active = false; };
    }
    setLoading(true);
    setError("");
    getCommitmentsDashboard({
      start_period: toPeriod(startMonth),
      end_period: toPeriod(endMonth),
      source,
    })
      .then((result) => { if (active) setData(result); })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [startMonth, endMonth, source]);

  const projectChart = useMemo(() => {
    const totals = new Map<string, { RI: number; SavingPlan: number }>();
    for (const row of data?.projects || []) {
      const project = row.project || "Sem projeto";
      const bucket = totals.get(project) || { RI: 0, SavingPlan: 0 };
      bucket[row.commitment_type === "RI" ? "RI" : "SavingPlan"] += Number(row.ondemand_baseline || 0);
      totals.set(project, bucket);
    }
    return [...totals.entries()]
      .map(([project, values]) => ({ project, ...values }))
      .sort((a, b) => b.RI + b.SavingPlan - (a.RI + a.SavingPlan))
      .slice(0, 12);
  }, [data?.projects]);

  const projectColumns: GridColDef<CommitmentDashboardRow>[] = [
    { field: "project", headerName: "Projeto consumidor", minWidth: 240, flex: 1 },
    { field: "commitment_type", headerName: "Compromisso", width: 145 },
    { field: "source", headerName: "Origem", width: 120 },
    { field: "ondemand_baseline", headerName: "Base OnDemand", width: 175, valueFormatter: (value) => money(value) },
    { field: "purchase_allocated", headerName: "Compra rateada", width: 165, valueFormatter: (value) => money(value) },
    { field: "estimated_saving", headerName: "Saving estimado", width: 175, valueFormatter: (value) => money(value) },
    { field: "usage_rows", headerName: "Linhas de consumo", width: 145, type: "number" },
  ];

  const monthly = data?.monthly || [];
  const forecasts = data?.forecast_monthly || [];
  const selected = data?.period_summary || {};
  const currencyAxis = { tickprefix: "R$ ", separatethousands: true, automargin: true };
  const chartLayout = { height: 290, margin: { l: 76, r: 20, t: 12, b: 48 }, paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { ...currencyAxis, zerolinecolor: "#cbd5e1", gridcolor: "#e2e8f0" }, xaxis: { gridcolor: "#f1f5f9" }, showlegend: false };

  return (
    <Box sx={{ p: 3, bgcolor: "#f8fafc", minHeight: "calc(100vh - 180px)" }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} gap={2} mb={2}>
        <Box>
          <Typography variant="h5">Compra de RIs e SavingPlan</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Compras, consumo elegível e referência OnDemand simulada por projeto.
          </Typography>
        </Box>
        <Stack direction={{ xs: "column", sm: "row" }} gap={1.5}>
          <TextField label="De" type="month" size="small" value={startMonth} onChange={(event) => setStartMonth(event.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField label="Até" type="month" size="small" value={endMonth} onChange={(event) => setEndMonth(event.target.value)} InputLabelProps={{ shrink: true }} />
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Origem</InputLabel>
            <Select label="Origem" value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
              <MenuItem value="all">Todas</MenuItem>
              <MenuItem value="azure-ti">Azure TI</MenuItem>
              <MenuItem value="azure-4p">Azure 4P</MenuItem>
            </Select>
          </FormControl>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Alert severity="info" sx={{ mb: 2 }}>
        Saving estimado = retorno sintético (base OnDemand) menos compra do mesmo tipo. No detalhamento por projeto, a compra é rateada mês a mês, por origem e compromisso, proporcionalmente à base OnDemand; os valores dos projetos reconciliam com o total do período.
      </Alert>

      {loading && <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress /></Stack>}
      {!loading && data && <>
        <Typography variant="subtitle1" fontWeight={700} mb={1}>Período selecionado</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compra de RI" value={Number(selected.ri_purchase || 0)} />
          <MetricCard title="Compra de SavingPlan" value={Number(selected.savingplan_purchase || 0)} />
          <MetricCard title="Base OnDemand — RI" value={Number(selected.ri_ondemand || 0)} detail="Consumo coberto por reserva" />
          <MetricCard title="Base OnDemand — SavingPlan" value={Number(selected.savingplan_ondemand || 0)} detail="Consumo coberto por plano" />
          <MetricCard title="Saving estimado — RI" value={Number(selected.ri_saving || 0)} detail="Retorno sintético − compra RI" />
          <MetricCard title="Saving estimado — SavingPlan" value={Number(selected.savingplan_saving || 0)} detail="Retorno sintético − compra SavingPlan" />
        </Stack>

        <Stack direction={{ xs: "column", xl: "row" }} gap={2} mb={2}>
          {([ ["Compras mensais — RI", "ri_purchase", "#1565c0"], ["Compras mensais — SavingPlan", "savingplan_purchase", "#7b1fa2"] ] as const).map(([title, field, color]) => <Card key={field} sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>{title}</Typography>
            <Plot data={[{ x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row[field] || 0)), type: "bar", name: title, marker: { color }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra></extra>" }]} layout={{ ...chartLayout, height: 270 }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>)}
        </Stack>

        <Stack direction={{ xs: "column", xl: "row" }} gap={2} mb={2}>
          {([ ["Base OnDemand mensal — RI", "ri_ondemand", "#0284c7"], ["Base OnDemand mensal — SavingPlan", "savingplan_ondemand", "#9333ea"] ] as const).map(([title, field, color]) => <Card key={field} sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>{title}</Typography>
            <Plot data={[{ x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row[field] || 0)), type: "scatter", mode: "lines+markers", name: title, line: { color, width: 3 }, marker: { size: 7 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra></extra>" }]} layout={chartLayout} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>)}
        </Stack>

        <Stack direction={{ xs: "column", xl: "row" }} gap={2} mb={2}>
          {([ ["Saving estimado mensal — RI", "ri_saving", "#15803d"], ["Saving estimado mensal — SavingPlan", "savingplan_saving", "#7e22ce"] ] as const).map(([title, field, color]) => <Card key={field} sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>{title}</Typography>
            <Typography variant="body2" color="text.secondary">Base OnDemand sintética menos compra do mês; valores abaixo de zero indicam compra superior à referência.</Typography>
            <Plot data={[{ x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row[field] || 0)), type: "bar", name: title, marker: { color }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra></extra>" }]} layout={{ ...chartLayout, yaxis: { ...chartLayout.yaxis, zeroline: true, zerolinewidth: 2, zerolinecolor: "#64748b" } }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>)}
        </Stack>

        <Stack direction={{ xs: "column", xl: "row" }} gap={2} mb={2}>
          <Card sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Projetos com consumo coberto — base OnDemand</Typography>
            <Typography variant="body2" color="text.secondary" mb={1}>Top 12 no período, por projeto e tipo de compromisso. Cada modalidade usa sua própria escala.</Typography>
            <Plot data={[
              { type: "bar", orientation: "h", x: projectChart.map((row) => row.RI), y: projectChart.map((row) => row.project), name: "RI", marker: { color: "#1565c0" }, hovertemplate: "%{y}<br>R$ %{x:,.2f}<extra>RI</extra>" },
              { type: "bar", orientation: "h", x: projectChart.map((row) => row.SavingPlan), y: projectChart.map((row) => row.project), name: "SavingPlan", marker: { color: "#7b1fa2" }, xaxis: "x2", yaxis: "y2", hovertemplate: "%{y}<br>R$ %{x:,.2f}<extra>SavingPlan</extra>" },
            ]} layout={{ height: 390, margin: { l: 18, r: 18, t: 25, b: 35 }, grid: { rows: 1, columns: 2, pattern: "independent" }, paper_bgcolor: "white", plot_bgcolor: "white", xaxis: { ...currencyAxis, domain: [0, 0.48], gridcolor: "#e2e8f0" }, xaxis2: { ...currencyAxis, domain: [0.55, 1], gridcolor: "#e2e8f0" }, yaxis: { automargin: true, autorange: "reversed" }, yaxis2: { automargin: true, autorange: "reversed" }, showlegend: false, annotations: [{ text: "RI", x: 0.24, y: 1.08, xref: "paper", yref: "paper", showarrow: false }, { text: "SavingPlan", x: 0.775, y: 1.08, xref: "paper", yref: "paper", showarrow: false }] }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>
          <Card sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Forecast mensal vigente</Typography>
            <Typography variant="body2" color="text.secondary" mb={1}>Projeção global por categoria; ainda sem vínculo a uma compra específica.</Typography>
            {forecasts.length ? <>
              <Plot data={[{ x: forecasts.map((row) => monthLabel(row.period || "")), y: forecasts.map((row) => Number(row.total || 0)), type: "scatter", mode: "lines+markers", name: "Total líquido previsto", line: { color: "#0f766e", width: 3 }, marker: { size: 7 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra></extra>" }]} layout={chartLayout} config={{ displayModeBar: false, responsive: true }} />
              <Typography variant="body2" color="text.secondary">Uso: {money(forecasts.reduce((sum, row) => sum + Number(row.usage || 0), 0))} · Compra: {money(forecasts.reduce((sum, row) => sum + Number(row.purchase || 0), 0))} · Retorno: {money(forecasts.reduce((sum, row) => sum + Number(row.return || 0), 0))}</Typography>
            </> : <Alert severity="info">Não há forecast para o período e origem selecionados.</Alert>}
          </CardContent></Card>
        </Stack>

        <Card><CardContent>
          <Typography variant="subtitle1" fontWeight={600} mb={1}>Detalhamento por projeto e compromisso</Typography>
          <Box sx={{ height: 520, width: "100%" }}>
            <DataGrid
              rows={data.projects}
              columns={projectColumns}
              getRowId={(row) => `${row.source}:${row.commitment_type}:${row.project}`}
              initialState={{ sorting: { sortModel: [{ field: "estimated_saving", sort: "desc" }] }, pagination: { paginationModel: { pageSize: 25, page: 0 } } }}
              pageSizeOptions={[10, 25, 50]}
              disableRowSelectionOnClick
            />
          </Box>
        </CardContent></Card>
      </>}
    </Box>
  );
}
