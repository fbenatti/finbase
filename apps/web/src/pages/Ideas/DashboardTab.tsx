import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import Plot from "@/components/Plot";
import { Alert, Box, Card, CardContent, Grid, Stack, Typography } from "@mui/material";
import { GridColDef } from "@mui/x-data-grid";
import { getIdeasDashboard, type IdeasDashboard, type Idea } from "../../services/ideasApi";
import { STATUS_LABELS, TYPE_LABELS } from "./constants";
import { colors } from "../../theme/tokens";

interface Props {
  onOpen: (id: number) => void;
  refreshKey: number;
}

function Kpi({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">{label}</Typography>
        <Typography variant="h4">{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
      </CardContent>
    </Card>
  );
}

const fmt = (v: number | null | undefined) => (v == null ? "—" : `${v}d`);

export default function DashboardTab({ onOpen, refreshKey }: Props) {
  const year = new Date().getFullYear();
  const [from, setFrom] = useState(`${year}-01-01`);
  const [to, setTo] = useState(`${year}-12-31`);
  const [data, setData] = useState<IdeasDashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getIdeasDashboard(from, to)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [from, to, refreshKey]);

  if (error) return <Alert severity="error">{dataDisplayText(error)}</Alert>;
  if (!data) return <Typography>Carregando painel...</Typography>;

  const k = data.kpis;
  const shippedCols: GridColDef[] = [
    { field: "title", headerName: "Ideia", flex: 1, minWidth: 180 },
    { field: "idea_type", headerName: "Tipo", width: 110, valueGetter: (_, row: Idea) => TYPE_LABELS[row.idea_type] || row.idea_type },
    { field: "time_to_start_days", headerName: "Até começar", width: 100, valueGetter: (_, row: Idea) => fmt(row.time_to_start_days) },
    { field: "time_in_impl_days", headerName: "Implementação", width: 120, valueGetter: (_, row: Idea) => fmt(row.time_in_impl_days) },
    { field: "time_to_prod_days", headerName: "Até produção", width: 100, valueGetter: (_, row: Idea) => fmt(row.time_to_prod_days) },
  ];

  const monthly = data.monthly_shipped;
  const types = Array.from(new Set(monthly.flatMap((m) => Object.keys(m.by_type))));

  return (
    <Box>
      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
      </Stack>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Entregues" value={k.implemented} hint={`${k.features_shipped} funcionalidades · ${k.panels_shipped} dashboards`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Defeitos corrigidos" value={k.bugs_fixed} hint={`${k.bugs_critical_fixed} críticos`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Tempo até começar" value={fmt(k.avg_time_to_start_days)} hint={`P50: ${fmt(k.p50_time_to_start_days)}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Tempo até produção" value={fmt(k.avg_time_to_prod_days)} hint={`P50: ${fmt(k.p50_time_to_prod_days)} · WIP: ${k.wip}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Em desenvolvimento" value={fmt(k.avg_time_in_impl_days)} hint={`P50: ${fmt(k.p50_time_in_impl_days)}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Descartadas" value={k.cancelled} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Encerradas" value={k.closed} /></Grid>
      </Grid>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} md={6}>
          <Card><CardContent>
            <Typography variant="subtitle1" gutterBottom>Entregas ao longo do tempo</Typography>
            <Plot
              data={types.map((t) => ({
                x: monthly.map((m) => m.month),
                y: monthly.map((m) => m.by_type[t] || 0),
                name: TYPE_LABELS[t as keyof typeof TYPE_LABELS] || t,
                type: "bar" as const,
              }))}
              layout={{ height: 300, barmode: "stack", margin: { l: 40, r: 20, t: 20, b: 40 }, paper_bgcolor: "white", plot_bgcolor: "white" }}
              config={{ displayModeBar: false }}
            />
          </CardContent></Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card><CardContent>
            <Typography variant="subtitle1" gutterBottom>Onde o tempo é consumido (média)</Typography>
            <Plot
              data={[{
                x: data.dwell_by_status.map((d) => STATUS_LABELS[d.status as keyof typeof STATUS_LABELS] || d.status),
                y: data.dwell_by_status.map((d) => d.avg_days || 0),
                type: "bar" as const,
                marker: { color: colors.brand },
              }]}
              layout={{ height: 300, margin: { l: 40, r: 20, t: 20, b: 80 }, paper_bgcolor: "white", plot_bgcolor: "white" }}
              config={{ displayModeBar: false }}
            />
          </CardContent></Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card><CardContent>
            <Typography variant="subtitle1" gutterBottom>Funil de execução</Typography>
            <Plot
              data={[{
                x: data.funnel.map((f) => STATUS_LABELS[f.status as keyof typeof STATUS_LABELS] || f.status),
                y: data.funnel.map((f) => f.count),
                type: "bar" as const,
                marker: { color: "#64748B" },
              }]}
              layout={{ height: 280, margin: { l: 40, r: 20, t: 20, b: 80 }, paper_bgcolor: "white", plot_bgcolor: "white" }}
              config={{ displayModeBar: false }}
            />
          </CardContent></Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card><CardContent>
            <Typography variant="subtitle1" gutterBottom>Envelhecimento (dias na situação atual)</Typography>
            <Plot
              data={[{
                x: data.aging_now.slice(0, 10).map((a) => a.title),
                y: data.aging_now.slice(0, 10).map((a) => a.days_in_current_status),
                type: "bar" as const,
                marker: { color: "#f59e0b" },
              }]}
              layout={{ height: 280, margin: { l: 40, r: 20, t: 20, b: 100 }, paper_bgcolor: "white", plot_bgcolor: "white" }}
              config={{ displayModeBar: false }}
            />
          </CardContent></Card>
        </Grid>
      </Grid>

      <Typography variant="h6" gutterBottom>Entregues na janela</Typography>
      <Box sx={{ height: 360, mb: 3 }}>
        <DataGrid rows={data.shipped_rows} columns={shippedCols} getRowId={(r) => r.id}
          onRowClick={(p) => onOpen(p.row.id)} disableRowSelectionOnClick />
      </Box>

      <Typography variant="h6" gutterBottom>Em desenvolvimento agora</Typography>
      <Box sx={{ height: 280, mb: 3 }}>
        <DataGrid rows={data.wip_rows} columns={shippedCols} getRowId={(r) => r.id}
          onRowClick={(p) => onOpen(p.row.id)} disableRowSelectionOnClick />
      </Box>
    </Box>
  );
}
