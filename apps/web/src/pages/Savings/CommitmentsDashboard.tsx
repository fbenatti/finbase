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
    { field: "usage_rows", headerName: "Linhas de consumo", width: 145, type: "number" },
  ];

  const monthly = data?.monthly || [];
  const forecasts = data?.forecast_monthly || [];
  const annual = data?.year_summary || {};
  const selected = data?.period_summary || {};

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
        A base OnDemand vem do valor das linhas sintéticas de retorno, pareadas ao uso e neutralizadas no custo. Ela é uma referência comparativa, não saving realizado; compras não são rateadas por projeto.
      </Alert>

      {loading && <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress /></Stack>}
      {!loading && data && <>
        <Typography variant="subtitle1" fontWeight={700} mb={1}>Período selecionado</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compra de RI" value={Number(selected.ri_purchase || 0)} />
          <MetricCard title="Compra de SavingPlan" value={Number(selected.savingplan_purchase || 0)} />
          <MetricCard title="Base OnDemand — RI" value={Number(selected.ri_ondemand || 0)} detail="Consumo coberto por reserva" />
          <MetricCard title="Base OnDemand — SavingPlan" value={Number(selected.savingplan_ondemand || 0)} detail="Consumo coberto por plano" />
        </Stack>

        <Typography variant="subtitle1" fontWeight={700} mb={1}>Acumulado do ano corrente — {currentYear}</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compras RI no ano" value={Number(annual.ri_purchase || 0)} />
          <MetricCard title="Compras SavingPlan no ano" value={Number(annual.savingplan_purchase || 0)} />
          <MetricCard title="Base OnDemand RI no ano" value={Number(annual.ri_ondemand || 0)} />
          <MetricCard title="Base OnDemand SavingPlan no ano" value={Number(annual.savingplan_ondemand || 0)} />
        </Stack>

        <Stack direction={{ xs: "column", xl: "row" }} gap={2} mb={2}>
          <Card sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Compras mensais — RI x SavingPlan</Typography>
            <Plot data={[
              { x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row.ri_purchase || 0)), type: "bar", name: "RI", marker: { color: "#1565c0" } },
              { x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row.savingplan_purchase || 0)), type: "bar", name: "SavingPlan", marker: { color: "#7b1fa2" } },
            ]} layout={{ height: 320, margin: { l: 72, r: 20, t: 18, b: 55 }, barmode: "group", paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { title: "BRL" }, legend: { orientation: "h", y: -0.2 } }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>
          <Card sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Base OnDemand mensal — RI x SavingPlan</Typography>
            <Plot data={[
              { x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row.ri_ondemand || 0)), type: "bar", name: "RI", marker: { color: "#42a5f5" } },
              { x: monthly.map((row) => monthLabel(row.period || "")), y: monthly.map((row) => Number(row.savingplan_ondemand || 0)), type: "bar", name: "SavingPlan", marker: { color: "#ba68c8" } },
            ]} layout={{ height: 320, margin: { l: 72, r: 20, t: 18, b: 55 }, barmode: "group", paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { title: "BRL" }, legend: { orientation: "h", y: -0.2 } }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>
        </Stack>

        <Stack direction={{ xs: "column", xl: "row" }} gap={2} mb={2}>
          <Card sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Projetos com consumo coberto — base OnDemand</Typography>
            <Typography variant="body2" color="text.secondary" mb={1}>Top 12 no período; modalidades exibidas separadamente.</Typography>
            <Plot data={[
              { x: projectChart.map((row) => row.project), y: projectChart.map((row) => row.RI), type: "bar", name: "RI", marker: { color: "#1565c0" } },
              { x: projectChart.map((row) => row.project), y: projectChart.map((row) => row.SavingPlan), type: "bar", name: "SavingPlan", marker: { color: "#7b1fa2" } },
            ]} layout={{ height: 390, margin: { l: 75, r: 20, t: 18, b: 120 }, barmode: "group", paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { title: "BRL" }, xaxis: { tickangle: -35 }, legend: { orientation: "h", y: -0.25 } }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>
          <Card sx={{ flex: 1, minWidth: 0 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Forecast mensal vigente</Typography>
            <Typography variant="body2" color="text.secondary" mb={1}>Projeção global por categoria; ainda sem vínculo a uma compra específica.</Typography>
            {forecasts.length ? <Plot data={[
              { x: forecasts.map((row) => monthLabel(row.period || "")), y: forecasts.map((row) => Number(row.usage || 0)), type: "bar", name: "Uso", marker: { color: "#90caf9" } },
              { x: forecasts.map((row) => monthLabel(row.period || "")), y: forecasts.map((row) => Number(row.purchase || 0)), type: "bar", name: "Compra", marker: { color: "#ef9a9a" } },
              { x: forecasts.map((row) => monthLabel(row.period || "")), y: forecasts.map((row) => Number(row.return || 0)), type: "bar", name: "Retorno", marker: { color: "#a5d6a7" } },
              { x: forecasts.map((row) => monthLabel(row.period || "")), y: forecasts.map((row) => Number(row.total || 0)), type: "scatter", mode: "lines+markers", name: "Total líquido", line: { color: "#263238", width: 3 } },
            ]} layout={{ height: 390, margin: { l: 75, r: 20, t: 18, b: 65 }, barmode: "relative", paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { title: "BRL" }, legend: { orientation: "h", y: -0.2 } }} config={{ displayModeBar: false, responsive: true }} /> : <Alert severity="info">Não há forecast para o período e origem selecionados.</Alert>}
          </CardContent></Card>
        </Stack>

        <Card><CardContent>
          <Typography variant="subtitle1" fontWeight={600} mb={1}>Detalhamento por projeto e compromisso</Typography>
          <Box sx={{ height: 520, width: "100%" }}>
            <DataGrid
              rows={data.projects}
              columns={projectColumns}
              getRowId={(row) => `${row.source}:${row.commitment_type}:${row.project}`}
              initialState={{ sorting: { sortModel: [{ field: "ondemand_baseline", sort: "desc" }] }, pagination: { paginationModel: { pageSize: 25, page: 0 } } }}
              pageSizeOptions={[10, 25, 50]}
              disableRowSelectionOnClick
            />
          </Box>
        </CardContent></Card>
      </>}
    </Box>
  );
}
