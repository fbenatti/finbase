import { lazy, Suspense, useEffect, useState } from "react";
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
import {
  CommitmentsDashboard as CommitmentsDashboardData,
  CommitmentDashboardRow,
  getCommitmentsDashboard,
} from "../../services/api";

const Plot = lazy(() => import("@/components/Plot"));

const currentMonth = new Date().toISOString().slice(0, 7);
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
  const [startMonth, setStartMonth] = useState(currentMonth);
  const [endMonth, setEndMonth] = useState(currentMonth);
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

  const projectColumns: GridColDef<CommitmentDashboardRow>[] = [
    { field: "project", headerName: "Projeto", minWidth: 220, flex: 1 },
    { field: "source", headerName: "Origem", width: 120 },
    { field: "ondemand_baseline", headerName: "Base OnDemand", width: 170, valueFormatter: (value) => money(value) },
    { field: "purchase_allocated", headerName: "Compra atribuída", width: 165, valueFormatter: (value) => money(value) },
    { field: "estimated_saving", headerName: "Saving realizado", width: 170, valueFormatter: (value) => money(value) },
    { field: "purchase_pct_of_ondemand", headerName: "Compra / OnDemand", width: 155, valueGetter: (_value, row) => row.ondemand_baseline ? Number(row.purchase_allocated || 0) / Number(row.ondemand_baseline) * 100 : 0, valueFormatter: (value) => percentage(value) },
    { field: "saving_pct_of_ondemand", headerName: "Saving / OnDemand", width: 155, valueGetter: (_value, row) => row.ondemand_baseline ? Number(row.estimated_saving || 0) / Number(row.ondemand_baseline) * 100 : 0, valueFormatter: (value) => percentage(value) },
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
  const riPurchasePct = Number(selected.ri_ondemand || 0) ? Number(selected.ri_purchase || 0) / Number(selected.ri_ondemand) * 100 : 0;
  const savingplanPurchasePct = Number(selected.savingplan_ondemand || 0) ? Number(selected.savingplan_purchase || 0) / Number(selected.savingplan_ondemand) * 100 : 0;
  const riSavingPct = Number(selected.ri_ondemand || 0) ? Number(selected.ri_saving || 0) / Number(selected.ri_ondemand) * 100 : 0;
  const savingplanSavingPct = Number(selected.savingplan_ondemand || 0) ? Number(selected.savingplan_saving || 0) / Number(selected.savingplan_ondemand) * 100 : 0;
  const totalPurchase = Number(selected.ri_purchase || 0) + Number(selected.savingplan_purchase || 0);
  const totalBaseline = Number(selected.ri_ondemand || 0) + Number(selected.savingplan_ondemand || 0);
  const totalSaving = Number(selected.ri_saving || 0) + Number(selected.savingplan_saving || 0);
  const totalSavingPct = totalBaseline ? totalSaving / totalBaseline * 100 : 0;
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
      {loading && !data && <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress /></Stack>}
      {loading && data && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>Atualizando o período…</Typography>}
      {data && <>
        <Typography variant="subtitle1" fontWeight={700} mb={0.5}>Consolidado do período</Typography>
        <Typography variant="body2" color="text.secondary" mb={1}>De {rangeLabel} · valores realizados, exceto as séries identificadas como forecast.</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compra total" value={totalPurchase} detail="RI + SavingPlan" />
          <MetricCard title="Saving total realizado" value={totalSaving} detail="Base OnDemand menos compra" />
          <MetricCard title="Saving / base OnDemand" value={totalSavingPct} detail="Percentual consolidado do período" isPercent />
          <MetricCard title="Base OnDemand média / mês" value={(Number(selected.ri_ondemand || 0) + Number(selected.savingplan_ondemand || 0)) / monthCount} detail="RI + SavingPlan" />
        </Stack>

        <Typography variant="subtitle1" fontWeight={700} mb={1}>Reserva (RI)</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compra de RI" value={Number(selected.ri_purchase || 0)} detail={rangeLabel} />
          <MetricCard title="Base OnDemand média / mês" value={riMonthlyBaseline} detail={rangeLabel} />
          <MetricCard title="Saving RI realizado" value={Number(selected.ri_saving || 0)} detail="Sem forecast" />
          <MetricCard title="Compra / OnDemand" value={riPurchasePct} detail="Percentual da referência" isPercent />
          <MetricCard title="Saving / OnDemand" value={riSavingPct} detail="Percentual da referência" isPercent />
        </Stack>

        <Typography variant="subtitle1" fontWeight={700} mb={1}>SavingPlan</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Compra de SavingPlan" value={Number(selected.savingplan_purchase || 0)} detail={rangeLabel} />
          <MetricCard title="Base OnDemand média / mês" value={savingplanMonthlyBaseline} detail={rangeLabel} />
          <MetricCard title="Saving SavingPlan realizado" value={Number(selected.savingplan_saving || 0)} detail="Sem forecast" />
          <MetricCard title="Compra / OnDemand" value={savingplanPurchasePct} detail="Percentual da referência" isPercent />
          <MetricCard title="Saving / OnDemand" value={savingplanSavingPct} detail="Percentual da referência" isPercent />
        </Stack>

        <Suspense fallback={<Stack alignItems="center" sx={{ py: 3 }}><CircularProgress size={24} /></Stack>}>
        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600}>Compras mensais</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Compra realizada e forecast são separados visualmente; saving realizado é calculado pela base OnDemand menos a compra.</Typography>
          <Stack direction={{ xs: "column", lg: "row" }} spacing={2}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" fontWeight={600}>RI</Typography>
              <Plot data={[
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_purchase || 0)), type: "bar", name: "Compra realizada", offsetgroup: "purchase", marker: { color: "#1565c0" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — compra realizada</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.ri_forecast_purchase || 0)), type: "bar", name: "Forecast de compra", offsetgroup: "purchase", marker: { color: "#60a5fa" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — forecast de compra</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_saving || 0)), type: "bar", name: "Saving realizado", offsetgroup: "saving", marker: { color: "#15803d" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — saving realizado</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_ondemand || 0)), type: "scatter", mode: "lines+markers", name: "Base OnDemand", line: { color: "#0284c7", width: 3 }, marker: { size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — base OnDemand</extra>" },
              ]} layout={{ ...chartLayout, height: 340, barmode: "stack", showlegend: true, legend: { orientation: "h", y: -0.24 } }} config={{ displayModeBar: false, responsive: true }} />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" fontWeight={600}>SavingPlan</Typography>
              <Plot data={[
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_purchase || 0)), type: "bar", name: "Compra realizada", offsetgroup: "purchase", marker: { color: "#7b1fa2" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — compra realizada</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.savingplan_forecast_purchase || 0)), type: "bar", name: "Forecast de compra", offsetgroup: "purchase", marker: { color: "#c084fc" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — forecast de compra</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_saving || 0)), type: "bar", name: "Saving realizado", offsetgroup: "saving", marker: { color: "#15803d" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — saving realizado</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_ondemand || 0)), type: "scatter", mode: "lines+markers", name: "Base OnDemand", line: { color: "#c084fc", width: 3 }, marker: { size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — base OnDemand</extra>" },
              ]} layout={{ ...chartLayout, height: 340, barmode: "stack", showlegend: true, legend: { orientation: "h", y: -0.24 } }} config={{ displayModeBar: false, responsive: true }} />
            </Box>
          </Stack>
        </CardContent></Card>

        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600}>Compras e saving por projeto</Typography>
          <Typography variant="body2" color="text.secondary" mb={2}>Valores do período selecionado; compra forecast e saving realizado aparecem separados.</Typography>
          <Stack direction={{ xs: "column", xl: "row" }} spacing={2}>
            {(["RI", "SavingPlan"] as const).map((type) => {
              const actualRows = data.projects.filter((row) => row.commitment_type === type)
                .sort((a, b) => Number(b.purchase_allocated || 0) - Number(a.purchase_allocated || 0)).slice(0, 10);
              const forecastRows = (data.forecast_projects || []).filter((row) => row.commitment_type === type);
              const forecastByProject = new Map<string, number>();
              forecastRows.forEach((row) => forecastByProject.set(row.project || "Sem projeto", (forecastByProject.get(row.project || "Sem projeto") || 0) + Number(row.forecast_purchase || 0)));
              const names = actualRows.map((row) => row.project || "Sem projeto");
              return <Box key={type} sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="subtitle2" fontWeight={600}>{type}</Typography>
                <Plot data={[
                  { x: names, y: actualRows.map((row) => Number(row.purchase_allocated || 0)), type: "bar", name: "Compra realizada", marker: { color: type === "RI" ? "#1565c0" : "#7b1fa2" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>Compra realizada</extra>" },
                  { x: names, y: names.map((name) => forecastByProject.get(name) || 0), type: "bar", name: "Forecast de compra", marker: { color: type === "RI" ? "#93c5fd" : "#d8b4fe" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>Forecast de compra</extra>" },
                  { x: names, y: actualRows.map((row) => Number(row.estimated_saving || 0)), type: "bar", name: "Saving realizado", marker: { color: "#15803d" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>Saving realizado</extra>" },
                ]} layout={{ ...chartLayout, height: 360, margin: { l: 76, r: 20, t: 12, b: 105 }, barmode: "group", showlegend: true, legend: { orientation: "h", y: -0.38 }, xaxis: { ...chartLayout.xaxis, tickangle: -35, automargin: true } }} config={{ displayModeBar: false, responsive: true }} />
              </Box>;
            })}
          </Stack>
        </CardContent></Card>

        </Suspense>

        <Stack direction={{ xs: "column", xl: "row" }} spacing={2}>
          {(["RI", "SavingPlan"] as const).map((type) => {
            const rows = data.projects.filter((row) => row.commitment_type === type);
            return <Card key={type} sx={{ flex: 1, minWidth: 0 }}><CardContent>
              <Typography variant="subtitle1" fontWeight={600} mb={1}>Detalhamento — {type}</Typography>
              <Box sx={{ height: 460, width: "100%" }}>
                <DataGrid
                  rows={rows}
                  columns={projectColumns}
                  getRowId={(row) => `${row.source}:${row.commitment_type}:${row.project}`}
                  initialState={{ sorting: { sortModel: [{ field: "estimated_saving", sort: "desc" }] }, pagination: { paginationModel: { pageSize: 10, page: 0 } } }}
                  pageSizeOptions={[10, 25, 50]}
                  disableRowSelectionOnClick
                />
              </Box>
            </CardContent></Card>;
          })}
        </Stack>
      </>}
    </Box>
  );
}
