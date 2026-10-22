import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { lazy, Suspense, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Card,
  CardContent,
  CircularProgress,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { GridColDef } from "@mui/x-data-grid";
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
const periodsBetween = (start: string, end: string) => {
  const [startYear, startMonth] = start.split("-").map(Number);
  const [endYear, endMonth] = end.split("-").map(Number);
  const periods: string[] = [];
  for (let year = startYear, month = startMonth; year < endYear || (year === endYear && month <= endMonth);) {
    periods.push(`${year}${String(month).padStart(2, "0")}`);
    if (++month > 12) { month = 1; year += 1; }
  }
  return periods;
};

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
  const [startMonth, setStartMonth] = useState(`${currentMonth.slice(0, 4)}-01`);
  const [endMonth, setEndMonth] = useState(currentMonth);
  const [source, setSource] = useState<"all" | "azure-ti" | "azure-4p">("all");
  const [showAllProjects, setShowAllProjects] = useState<Record<"RI" | "SavingPlan", boolean>>({ RI: false, SavingPlan: false });
  const [data, setData] = useState<CommitmentsDashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    if (!startMonth || !endMonth || startMonth > endMonth) {
      setError("A data inicial precisa ser igual ou anterior à data final.");
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
    { field: "project", headerName: "Projeto alocado", minWidth: 220, flex: 1 },
    { field: "source", headerName: "Fonte", width: 120 },
    { field: "ondemand_baseline", headerName: "Base sob demanda", width: 170, valueFormatter: (value) => money(value) },
    { field: "purchase_allocated", headerName: "Compra alocada", width: 165, valueFormatter: (value) => money(value) },
    { field: "estimated_saving", headerName: "Saving efetivado", width: 170, valueFormatter: (value) => money(value) },
    { field: "purchase_pct_of_ondemand", headerName: "Compra / sob demanda", width: 155, valueGetter: (_value, row) => row.ondemand_baseline ? Number(row.purchase_allocated || 0) / Number(row.ondemand_baseline) * 100 : 0, valueFormatter: (value) => percentage(value) },
    { field: "saving_pct_of_ondemand", headerName: "Saving / sob demanda", width: 155, valueGetter: (_value, row) => row.ondemand_baseline ? Number(row.estimated_saving || 0) / Number(row.ondemand_baseline) * 100 : 0, valueFormatter: (value) => percentage(value) },
  ];

  const monthly = data?.monthly || [];
  const forecasts = data?.forecast_monthly || [];
  const selected = data?.period_summary || {};
  const rangeLabel = `${monthLabel(toPeriod(startMonth))} a ${monthLabel(toPeriod(endMonth))}`;
  const monthCount = Math.max(1, (Number(endMonth.slice(0, 4)) - Number(startMonth.slice(0, 4))) * 12 + Number(endMonth.slice(5, 7)) - Number(startMonth.slice(5, 7)) + 1);
  const forecastByPeriod = new Map(forecasts.map((row) => [row.period, row]));
  const monthlyByPeriod = new Map(monthly.map((row) => [row.period, row]));
  const chartPeriods = periodsBetween(startMonth, endMonth);
  const riMonthlyBaseline = Number(selected.ri_ondemand || 0) / monthCount;
  const savingplanMonthlyBaseline = Number(selected.savingplan_ondemand || 0) / monthCount;
  const riPurchasePct = Number(selected.ri_ondemand || 0) ? Number(selected.ri_purchase || 0) / Number(selected.ri_ondemand) * 100 : 0;
  const savingplanPurchasePct = Number(selected.savingplan_ondemand || 0) ? Number(selected.savingplan_purchase || 0) / Number(selected.savingplan_ondemand) * 100 : 0;
  const riSavingPct = Number(selected.ri_ondemand || 0) ? Number(selected.ri_saving || 0) / Number(selected.ri_ondemand) * 100 : 0;
  const savingplanSavingPct = Number(selected.savingplan_ondemand || 0) ? Number(selected.savingplan_saving || 0) / Number(selected.savingplan_ondemand) * 100 : 0;
  const totalBaseline = Number(selected.ri_ondemand || 0) + Number(selected.savingplan_ondemand || 0);
  const totalSaving = Number(selected.ri_saving || 0) + Number(selected.savingplan_saving || 0);
  const totalSavingPct = totalBaseline ? totalSaving / totalBaseline * 100 : 0;
  const currencyAxis = { tickprefix: "R$ ", separatethousands: true, automargin: true };
  const chartLayout = { height: 290, margin: { l: 76, r: 20, t: 12, b: 48 }, paper_bgcolor: "white", plot_bgcolor: "white", yaxis: { ...currencyAxis, zerolinecolor: "#C5D2DE", gridcolor: "#E1E8F0" }, xaxis: { gridcolor: "#EEF3F8" }, showlegend: false };

  return (
    <Box sx={{ p: 3, bgcolor: "#FAFBFD", minHeight: "calc(100vh - 180px)" }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} gap={2} mb={2}>
        <Box>
          <Typography variant="h5">Compra de RIs e Savings Plans</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Compra, base sob demanda (sob demanda) e saving efetivado por compromisso e por projeto.
          </Typography>
        </Box>
        <Stack direction={{ xs: "column", sm: "row" }} gap={1.5}>
          <TextField label="Início" type="month" size="small" value={startMonth} onChange={(event) => setStartMonth(event.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField label="Fim" type="month" size="small" value={endMonth} onChange={(event) => setEndMonth(event.target.value)} InputLabelProps={{ shrink: true }} />
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Fonte</InputLabel>
            <Select label="Fonte" value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
              <MenuItem value="all">Todas as fontes</MenuItem>
              <MenuItem value="azure-ti">Azure TI</MenuItem>
              <MenuItem value="azure-4p">Azure 4P</MenuItem>
            </Select>
          </FormControl>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
      {loading && !data && <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress /></Stack>}
      {loading && data && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>Recalculando o período…</Typography>}
      {data && <>
        <Typography variant="subtitle1" fontWeight={700} mb={0.5}>Visão consolidada do período</Typography>
        <Typography variant="body2" color="text.secondary" mb={1}>De {rangeLabel} · valores efetivados, exceto as séries identificadas como previsão.</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Saving total efetivado" value={totalSaving} detail="Base sob demanda menos compra" />
          <MetricCard title="Saving / base sob demanda" value={totalSavingPct} detail="Índice consolidado do período" isPercent />
          <MetricCard title="Base sob demanda média / mês" value={(Number(selected.ri_ondemand || 0) + Number(selected.savingplan_ondemand || 0)) / monthCount} detail="RI + Savings Plan" />
        </Stack>

        <Typography variant="subtitle1" fontWeight={700} mb={1}>Reserva de instância (RI)</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Investimento em RI" value={Number(selected.ri_purchase || 0)} detail={rangeLabel} />
          <MetricCard title="Base sob demanda média / mês" value={riMonthlyBaseline} detail={rangeLabel} />
          <MetricCard title="Saving RI efetivado" value={Number(selected.ri_saving || 0)} detail="Sem forecast" />
          <MetricCard title="Compra / sob demanda" value={riPurchasePct} detail="Percentual da base de referência" isPercent />
          <MetricCard title="Saving / sob demanda" value={riSavingPct} detail="Percentual da base de referência" isPercent />
        </Stack>

        <Typography variant="subtitle1" fontWeight={700} mb={1}>Savings Plan</Typography>
        <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
          <MetricCard title="Investimento em Savings Plan" value={Number(selected.savingplan_purchase || 0)} detail={rangeLabel} />
          <MetricCard title="Base sob demanda média / mês" value={savingplanMonthlyBaseline} detail={rangeLabel} />
          <MetricCard title="Saving SavingPlan efetivado" value={Number(selected.savingplan_saving || 0)} detail="Sem forecast" />
          <MetricCard title="Compra / sob demanda" value={savingplanPurchasePct} detail="Percentual da base de referência" isPercent />
          <MetricCard title="Saving / sob demanda" value={savingplanSavingPct} detail="Percentual da base de referência" isPercent />
        </Stack>

        <Suspense fallback={<Stack alignItems="center" sx={{ py: 3 }}><CircularProgress size={24} /></Stack>}>
        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600}>Compras por mês</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>As barras empilham apenas a compra efetivada e a previsão de compra. Saving e base sob demanda não entram nas barras.</Typography>
          <Stack direction={{ xs: "column", lg: "row" }} spacing={2}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" fontWeight={600}>RI</Typography>
              <Plot data={[
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_purchase || 0)), type: "bar", name: "Compra efetivada", offsetgroup: "purchase", legendgroup: "purchase", marker: { color: "#0EA5C6" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — compra efetivada</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.ri_forecast_purchase || 0)), type: "bar", name: "Previsão de compra", offsetgroup: "purchase", legendgroup: "purchase", marker: { color: "#7DB4FF" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — previsão de compra</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.ri_ondemand || 0)), type: "scatter", mode: "lines+markers", name: "Base sob demanda", line: { color: "#0EA5C6", width: 3 }, marker: { size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>RI — base sob demanda</extra>" },
              ]} layout={{ ...chartLayout, height: 340, barmode: "stack", showlegend: true, legend: { orientation: "h", y: -0.24 } }} config={{ displayModeBar: false, responsive: true }} />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" fontWeight={600}>Savings Plan</Typography>
              <Plot data={[
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_purchase || 0)), type: "bar", name: "Compra efetivada", offsetgroup: "purchase", legendgroup: "purchase", marker: { color: "#0B3FA8" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — compra efetivada</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(forecastByPeriod.get(period)?.savingplan_forecast_purchase || 0)), type: "bar", name: "Previsão de compra", offsetgroup: "purchase", legendgroup: "purchase", marker: { color: "#7DB4FF" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — previsão de compra</extra>" },
                { x: chartPeriods.map(monthLabel), y: chartPeriods.map((period) => Number(monthlyByPeriod.get(period)?.savingplan_ondemand || 0)), type: "scatter", mode: "lines+markers", name: "Base sob demanda", line: { color: "#7DB4FF", width: 3 }, marker: { size: 6 }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>SavingPlan — base sob demanda</extra>" },
              ]} layout={{ ...chartLayout, height: 340, barmode: "stack", showlegend: true, legend: { orientation: "h", y: -0.24 } }} config={{ displayModeBar: false, responsive: true }} />
            </Box>
          </Stack>
        </CardContent></Card>

        <Card sx={{ mb: 2 }}><CardContent>
          <Typography variant="subtitle1" fontWeight={600}>Sob demanda x custo com compromisso por projeto</Typography>
          <Typography variant="body2" color="text.secondary" mb={2}>Os 8 projetos com maior base sob demanda no período. A economia é a diferença entre o preço sob demanda e o custo do compromisso, exibida ao passar o cursor.</Typography>
          <Stack direction={{ xs: "column", lg: "row" }} spacing={2}>
            {(["RI", "SavingPlan"] as const).map((type) => {
              const totals = new Map<string, { project: string; baseline: number; commitmentCost: number; saving: number }>();
              for (const row of data.projects.filter((item) => item.commitment_type === type)) {
                const project = row.project || "Sem projeto alocado";
                const total = totals.get(project) || { project, baseline: 0, commitmentCost: 0, saving: 0 };
                total.baseline += Number(row.ondemand_baseline || 0);
                total.commitmentCost += Number(row.purchase_allocated || 0);
                total.saving += Number(row.estimated_saving || 0);
                totals.set(project, total);
              }
              const topProjects = [...totals.values()]
                .sort((a, b) => b.baseline - a.baseline)
                .slice(0, 8);
              const names = topProjects.map((row) => row.project);
              return <Box key={type} sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="subtitle2" fontWeight={600}>{type}</Typography>
                <Plot data={[
                  { x: names, y: topProjects.map((row) => row.baseline), type: "bar", name: "Base sob demanda", marker: { color: type === "RI" ? "#A9C8FF" : "#A9C8FF" }, hovertemplate: "%{x}<br>R$ %{y:,.2f}<extra>Base sob demanda</extra>" },
                  { x: names, y: topProjects.map((row) => row.commitmentCost), type: "bar", name: "Custo com compromisso", customdata: topProjects.map((row) => [row.saving]), marker: { color: type === "RI" ? "#0EA5C6" : "#0B3FA8" }, hovertemplate: "%{x}<br>Custo: R$ %{y:,.2f}<br>Economia (sob demanda − custo): R$ %{customdata[0]:,.2f}<extra>Custo com compromisso</extra>" },
                ]} layout={{ ...chartLayout, height: 380, margin: { l: 76, r: 20, t: 12, b: 100 }, barmode: "group", showlegend: true, legend: { orientation: "h", y: -0.32 }, xaxis: { ...chartLayout.xaxis, tickangle: -35, automargin: true } }} config={{ displayModeBar: false, responsive: true }} />
              </Box>;
            })}
          </Stack>
        </CardContent></Card>

        </Suspense>

        <Stack direction="column" spacing={2}>
          {(["RI", "SavingPlan"] as const).map((type) => {
            const rows = data.projects.filter((row) => row.commitment_type === type);
            const visibleRows = showAllProjects[type] ? rows : rows.slice(0, 10);
            return <Card key={type} sx={{ flex: 1, minWidth: 0 }}><CardContent>
              <Typography variant="subtitle1" fontWeight={600} mb={1}>Detalhe por projeto — {type}</Typography>
              <Box sx={{ width: "100%" }}>
                <DataGrid
                  rows={visibleRows}
                  columns={projectColumns}
                  getRowId={(row) => `${row.source}:${row.commitment_type}:${row.project}`}
                  autoHeight
                  initialState={{ sorting: { sortModel: [{ field: "estimated_saving", sort: "desc" }] } }}
                  hideFooter
                  disableRowSelectionOnClick
                />
              </Box>
              <Button size="small" sx={{ mt: 1, px: 0 }} onClick={() => setShowAllProjects((current) => ({ ...current, [type]: !current[type] }))}>
                {showAllProjects[type] ? "Ver top 10" : "Mostrar todos"}
              </Button>
            </CardContent></Card>;
          })}
        </Stack>
      </>}
    </Box>
  );
}
