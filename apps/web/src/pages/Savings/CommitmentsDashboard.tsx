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
const percentage = (value: unknown) => `${Number(value || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const monthLabel = (period: string) => `${period.slice(4, 6)}/${period.slice(0, 4)}`;

function MetricCard({ title, value, detail, isPercent = false }: { title: string; value: number; detail?: string; isPercent?: boolean }) {
  return (
    <Card sx={{ flex: 1, minWidth: 210 }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">{title}</Typography>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>{isPercent ? percentage(value) : money(value)}</Typography>
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
    type ProjectTotal = { project: string; baseline: number; purchase: number; saving: number };
    const grouped: Record<"RI" | "SavingPlan", Map<string, ProjectTotal>> = {
      RI: new Map(), SavingPlan: new Map(),
    };
    for (const row of data?.projects || []) {
      const project = row.project || "Sem projeto";
      const type = row.commitment_type === "RI" ? "RI" : "SavingPlan";
      const bucket = grouped[type].get(project) || { project, baseline: 0, purchase: 0, saving: 0 };
      bucket.baseline += Number(row.ondemand_baseline || 0);
      bucket.purchase += Number(row.purchase_allocated || 0);
      bucket.saving += Number(row.estimated_saving || 0);
      grouped[type].set(project, bucket);
    }
    const rank = (rows: Map<string, ProjectTotal>) => [...rows.values()]
      .map((row) => ({ ...row, purchasePct: row.baseline ? row.purchase / row.baseline * 100 : 0 }))
      .sort((a, b) => b.baseline - a.baseline)
      .slice(0, 8);
    return { RI: rank(grouped.RI), SavingPlan: rank(grouped.SavingPlan) };
  }, [data?.projects]);

  const projectColumns: GridColDef<CommitmentDashboardRow>[] = [
    { field: "project", headerName: "Projeto", minWidth: 220, flex: 1 },
    { field: "source", headerName: "Origem", width: 120 },
    { field: "ondemand_baseline", headerName: "Base OnDemand", width: 170, valueFormatter: (value) => money(value) },
    { field: "purchase_allocated", headerName: "Compra atribuída", width: 165, valueFormatter: (value) => money(value) },
    { field: "estimated_saving", headerName: "Saving realizado", width: 170, valueFormatter: (value) => money(value) },
    { field: "purchase_pct_of_ondemand", headerName: "Compra / OnDemand", width: 155, valueFormatter: (value) => percentage(value) },
    { field: "saving_pct_of_ondemand", headerName: "Saving / OnDemand", width: 155, valueFormatter: (value) => percentage(value) },
  ];

  const monthly = data?.monthly || [];
  const forecasts = data?.forecast_monthly || [];
  const selected = data?.period_summary || {};
  const rangeLabel = `${monthLabel(toPeriod(startMonth))} a ${monthLabel(toPeriod(endMonth))}`;
  const monthCount = Math.max(1, (Number(endMonth.slice(0, 4)) - Number(startMonth.slice(0, 4))) * 12 + Number(endMonth.slice(5, 7)) - Number(startMonth.slice(5, 7)) + 1);
  const forecastByPeriod = new Map(forecasts.map((row) => [row.period, row]));
  const monthlyByPeriod = new Map(monthly.map((row) => [row.period, row]));
  const chartPeriods = [...new Set([...monthly.map((row) => row.period || ""), ...forecasts.map((row) => row.period || "")])].filter(Boolean).sort();
  const riMonthlyBaseline = Number(selected.ri_ondemand || 0) / monthCount;
  const savingplanMonthlyBaseline = Number(selected.savingplan_ondemand || 0) / monthCount;
  const currencyAxis = { tickprefix: "R$ ", separatethousands: true, automargin: true };
  const chartLayout = { height: 290, margin: { l: 76, r: 20, t: 12, b: 48 }, paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { ...currencyAxis, zerolinecolor: "#cbd5e1", gridcolor: "#e2e8f0" }, xaxis: { gridcolor: "#f1f5f9" }, showlegend: false };

  return (
    <Box sx={{ p: 3, bgcolor: "#f8fafc", minHeight: "calc(100vh - 180px)" }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} gap={2} mb={2}>
        <Box>
          <Typography variant="h5">Compra de RIs e SavingPlan</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Compra, referência OnDemand e saving realizado por compromisso e projeto.
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
      {loading && <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress /></Stack>}
      {!loading && data && <>
        <Typography variant="subtitle1" fontWeight={700} mb={0.5}>Período selecionado</Typography>
        <Typography variant="body2" color="text.secondary" mb={1}>De {rangeLabel}</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compra de RI" value={Number(selected.ri_purchase || 0)} detail={`Realizado · ${rangeLabel}`} />
          <MetricCard title="Compra de SavingPlan" value={Number(selected.savingplan_purchase || 0)} detail={`Realizado · ${rangeLabel}`} />
          <MetricCard title="Base OnDemand média / mês — RI" value={riMonthlyBaseline} detail={`Média no período · ${rangeLabel}`} />
          <MetricCard title="Base OnDemand média / mês — SavingPlan" value={savingplanMonthlyBaseline} detail={`Média no período · ${rangeLabel}`} />
          <MetricCard title="Saving realizado — RI" value={Number(selected.ri_saving || 0)} detail={`Realizado · ${rangeLabel} · sem forecast`} />
          <MetricCard title="Saving realizado — SavingPlan" value={Number(selected.savingplan_saving || 0)} detail={`Realizado · ${rangeLabel} · sem forecast`} />
          <MetricCard title="Compra RI / Base OnDemand" value={Number(selected.ri_purchase_pct_of_ondemand || 0)} detail="Compra como proporção da referência OnDemand" isPercent />
          <MetricCard title="Compra SavingPlan / Base OnDemand" value={Number(selected.savingplan_purchase_pct_of_ondemand || 0)} detail="Compra como proporção da referência OnDemand" isPercent />
        </Stack>

        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600}>Compras mensais</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Cada modalidade mostra compra realizada e previsão empilhadas em cores diferentes, junto à base OnDemand sintética.</Typography>
          <Stack direction={{ xs: "column", lg: "row" }} spacing={2}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" fontWeight={600}>RI</Typography>
              <Plot data={[
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_purchase || 0)), type: "bar", name: "Compra realizada", marker: { color: "#1565c0" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — compra realizada</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.ri_forecast_purchase || 0)), type: "bar", name: "Forecast de compra", marker: { color: "#60a5fa" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — forecast de compra</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_ondemand || 0)), type: "scatter", mode: "lines+markers", name: "Base OnDemand", line: { color: "#0284c7", width: 3 }, marker: { size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — base OnDemand</extra>" },
              ]} layout={{ ...chartLayout, height: 340, barmode: "stack", showlegend: true, legend: { orientation: "h", y: -0.24 } }} config={{ displayModeBar: false, responsive: true }} />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" fontWeight={600}>SavingPlan</Typography>
              <Plot data={[
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_purchase || 0)), type: "bar", name: "Compra realizada", marker: { color: "#7b1fa2" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — compra realizada</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.savingplan_forecast_purchase || 0)), type: "bar", name: "Forecast de compra", marker: { color: "#c084fc" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — forecast de compra</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_ondemand || 0)), type: "scatter", mode: "lines+markers", name: "Base OnDemand", line: { color: "#c084fc", width: 3 }, marker: { size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — base OnDemand</extra>" },
              ]} layout={{ ...chartLayout, height: 340, barmode: "stack", showlegend: true, legend: { orientation: "h", y: -0.24 } }} config={{ displayModeBar: false, responsive: true }} />
            </Box>
          </Stack>
        </CardContent></Card>

        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600}>Saving mensal — realizado e forecast</Typography>
          <Typography variant="body2" color="text.secondary">Saving = base OnDemand sintética menos compra. No forecast, Return é armazenado negativo; saving previsto = −Return − Purchase, discriminado por tipo de compromisso.</Typography>
          <Plot data={[
            { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_saving || 0)), type: "bar", name: "RI — saving realizado", marker: { color: "#15803d" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — realizado</extra>" },
            { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_saving || 0)), type: "bar", name: "SavingPlan — saving realizado", marker: { color: "#7e22ce" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — realizado</extra>" },
            { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.ri_forecast_saving || 0)), type: "bar", name: "RI — saving forecast", marker: { color: "#86efac" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — saving forecast</extra>" },
            { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.savingplan_forecast_saving || 0)), type: "bar", name: "SavingPlan — saving forecast", marker: { color: "#d8b4fe" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — saving forecast</extra>" },
          ]} layout={{ ...chartLayout, height: 340, barmode: "stack", yaxis: { ...chartLayout.yaxis, zeroline: true, zerolinewidth: 2, zerolinecolor: "#64748b" }, legend: { orientation: "h", y: -0.3 } }} config={{ displayModeBar: false, responsive: true }} />
          {forecasts.length === 0 && <Alert severity="info">Não há forecast para o período e origem selecionados.</Alert>}
        </CardContent></Card>

        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600} mb={0.5}>Forecast por compromisso e PricingCategory</Typography>
          <Typography variant="body2" color="text.secondary" mb={1}>Return aparece negativo por representar neutralização de custo; o gráfico de saving converte esse valor para base OnDemand positiva.</Typography>
          <Box sx={{ height: 340, width: "100%" }}>
            <DataGrid
              rows={forecastBreakdownRows}
              columns={forecastBreakdownColumns}
              getRowId={(row) => String(row.id)}
              initialState={{ sorting: { sortModel: [{ field: "forecast_value", sort: "desc" }] }, pagination: { paginationModel: { pageSize: 10, page: 0 } } }}
              pageSizeOptions={[10, 25, 50]}
              disableRowSelectionOnClick
            />
          </Box>
        </CardContent></Card>

        <Card sx={{ mb: 2 }}><CardContent>
            <Typography variant="subtitle1" fontWeight={600}>Projetos — base OnDemand x saving estimado</Typography>
            <Typography variant="body2" color="text.secondary" mb={1}>Top 10 por base OnDemand; saving calculado após rateio da compra por modalidade.</Typography>
            <Plot data={[
              { type: "bar", orientation: "h", x: projectChart.map((row) => row.RI), y: projectChart.map((row) => row.project), name: "RI — base OnDemand", marker: { color: "#93c5fd" }, hovertemplate: "%{y}<br>R$ %{x:,.2f}<extra>RI — base OnDemand</extra>" },
              { type: "bar", orientation: "h", x: projectChart.map((row) => row.riSaving), y: projectChart.map((row) => row.project), name: "RI — saving", marker: { color: "#15803d" }, hovertemplate: "%{y}<br>R$ %{x:,.2f}<extra>RI — saving estimado</extra>" },
              { type: "bar", orientation: "h", x: projectChart.map((row) => row.SavingPlan), y: projectChart.map((row) => row.project), name: "SavingPlan — base OnDemand", marker: { color: "#d8b4fe" }, xaxis: "x2", yaxis: "y2", hovertemplate: "%{y}<br>R$ %{x:,.2f}<extra>SavingPlan — base OnDemand</extra>" },
              { type: "bar", orientation: "h", x: projectChart.map((row) => row.savingplanSaving), y: projectChart.map((row) => row.project), name: "SavingPlan — saving", marker: { color: "#7e22ce" }, xaxis: "x2", yaxis: "y2", hovertemplate: "%{y}<br>R$ %{x:,.2f}<extra>SavingPlan — saving estimado</extra>" },
            ]} layout={{ height: 390, margin: { l: 18, r: 18, t: 25, b: 35 }, grid: { rows: 1, columns: 2, pattern: "independent" }, barmode: "group", paper_bgcolor: "white", plot_bgcolor: "white", xaxis: { ...currencyAxis, domain: [0, 0.48], gridcolor: "#e2e8f0" }, xaxis2: { ...currencyAxis, domain: [0.55, 1], gridcolor: "#e2e8f0" }, yaxis: { automargin: true, autorange: "reversed" }, yaxis2: { automargin: true, autorange: "reversed" }, showlegend: true, legend: { orientation: "h", y: -0.22 }, annotations: [{ text: "RI", x: 0.24, y: 1.08, xref: "paper", yref: "paper", showarrow: false }, { text: "SavingPlan", x: 0.775, y: 1.08, xref: "paper", yref: "paper", showarrow: false }] }} config={{ displayModeBar: false, responsive: true }} />
          </CardContent></Card>

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
