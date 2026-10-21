import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Button, CircularProgress, Paper, Stack, Typography } from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { getSharedCostHistory, SharedCostJob } from "../../services/api";
import OperationalHistoryDataGrid from "../../components/OperationalHistoryDataGrid";
import { GridColDef } from "@mui/x-data-grid";

export default function SharedCostHistory() {
  const { runId } = useParams<{ runId: string }>(); const navigate = useNavigate();
  const [job, setJob] = useState<SharedCostJob | null>(null); const [error, setError] = useState("");
  useEffect(() => { getSharedCostHistory().then(rows => setJob(rows.find(row => row.id === Number(runId)) || null)).catch(e => setError(e instanceof Error ? e.message : String(e))); }, [runId]);
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!job) return <Stack alignItems="center" py={8}><CircularProgress /></Stack>;
  const snapshot = (job.snapshot || {}) as Record<string, unknown>;
  const nodes = Array.isArray(snapshot.shared_nodes) ? snapshot.shared_nodes as Record<string, unknown>[] : [];
  const columns: GridColDef[] = Object.keys(nodes[0] || {}).map(field => ({ field, headerName: field.replace(/_/g, " "), flex: 1, minWidth: 160 }));
  return <Box><Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}><Box><Typography variant="h4">Dados do custo compartilhado</Typography><Typography color="text.secondary">Execução {job.id} · snapshot utilizado pelo worker</Typography></Box><Button startIcon={<ArrowBack />} onClick={() => navigate("/operacoes/execucoes?tab=shared")}>Voltar</Button></Stack><Paper variant="outlined" sx={{ p: 2, mb: 2 }}><Typography>Status: {job.status}</Typography><Typography color="text.secondary">{job.phase || job.table || "Sem mensagem registrada"}</Typography></Paper>{nodes.length ? <OperationalHistoryDataGrid rows={nodes.map((row, index) => ({ id: index, ...row }))} columns={columns} /> : <Alert severity="info">Esta execução não possui linhas detalhadas no snapshot persistido.</Alert>}</Box>;
}
