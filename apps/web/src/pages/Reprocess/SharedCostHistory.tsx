import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Button, Chip, CircularProgress, Paper, Stack, Typography } from "@mui/material";
import { ArrowBackOutlined } from "@mui/icons-material";
import { getSharedCostHistory, SharedCostJob } from "../../services/api";
import OperationalHistoryDataGrid from "../../components/OperationalHistoryDataGrid";
import { GridColDef } from "@mui/x-data-grid";

const STATUS_COLOR: Record<string, "success" | "error" | "warning" | "default"> = {
  COMPLETED: "success",
  FAILED: "error",
  CANCELLED: "warning",
};

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD", minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" fontWeight={700} noWrap>{value}</Typography>
    </Paper>
  );
}

export default function SharedCostHistory() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<SharedCostJob | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getSharedCostHistory()
      .then(rows => setJob(rows.find(row => row.id === Number(runId)) || null))
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, [runId]);

  if (error) return <Alert severity="error">{dataDisplayText(error)}</Alert>;
  if (!job) return <Stack alignItems="center" py={8}><CircularProgress /></Stack>;

  const snapshot = (job.snapshot || {}) as Record<string, unknown>;
  const nodes = Array.isArray(snapshot.shared_nodes) ? snapshot.shared_nodes as Record<string, unknown>[] : [];
  const columns: GridColDef[] = Object.keys(nodes[0] || {}).map(field => ({
    field,
    headerName: field.replace(/_/g, " "),
    flex: 1,
    minWidth: 160,
  }));

  return (
    <Stack spacing={2.5}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "flex-start" }} spacing={1.5}>
        <Box>
          <Typography variant="overline" color="text.secondary">Custo compartilhado · execução {job.id}</Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="h5" fontWeight={700}>Detalhamento da execução</Typography>
            <Chip size="small" label={job.status} color={STATUS_COLOR[job.status] ?? "default"} variant="outlined" />
          </Stack>
          <Typography variant="body2" color="text.secondary">
            {job.phase || job.table || "Nenhuma mensagem registrada"}
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<ArrowBackOutlined />} onClick={() => navigate("/operacoes/rateios")}>Voltar</Button>
      </Stack>

      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, minmax(0, 1fr))" } }}>
        <Kpi label="Linhas publicadas" value={job.rows_written.toLocaleString("pt-BR")} />
        <Kpi label="Unidades rateadas" value={nodes.length} />
        <Kpi label="Arquivos processados" value={job.processed_blobs != null && job.total_blobs != null ? `${job.processed_blobs}/${job.total_blobs}` : "—"} />
        <Kpi label="Conclusão" value={job.finished_at ? new Date(job.finished_at).toLocaleString("pt-BR") : "—"} />
      </Box>

      {job.error && <Alert severity="error">{dataDisplayText(job.error)}</Alert>}

      {nodes.length
        ? <OperationalHistoryDataGrid rows={nodes.map((row, index) => ({ id: index, ...row }))} columns={columns} />
        : <Alert severity="info">Esta execução não tem linhas detalhadas na fotografia salva.</Alert>}
    </Stack>
  );
}
