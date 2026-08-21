import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useNotifications } from "../../context/NotificationContext";
import { Alert, Box, Button, Chip, CircularProgress, IconButton, Paper, Tooltip, Typography } from "@mui/material";
import { Refresh, Stop, Visibility } from "@mui/icons-material";
import OperationalHistoryDataGrid from "../../components/OperationalHistoryDataGrid";
import { GridColDef } from "@mui/x-data-grid";
import {
  SharedCostConfig, SharedCostJob, cancelEtl, formatApiError, getSharedCostHistory, getSharedCosts,
  reprocessSharedCost,
} from "../../services/api";

const isActive = (status: string) => status === "QUEUED" || status === "RUNNING";
const statusColor = (status: string): "success" | "error" | "info" | "warning" | "default" => {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "error";
  if (status === "CANCELLED") return "warning";
  if (isActive(status)) return "info";
  return "default";
};

export default function ReprocessSharedCostTab() {
  const navigate = useNavigate();
  const notifications = useNotifications();
  const [configs, setConfigs] = useState<SharedCostConfig[]>([]);
  const [history, setHistory] = useState<SharedCostJob[]>([]);
  const [error, setError] = useState("");
  const [startMessage, setStartMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState<number | null>(null);

  const loadConfigs = async () => {
    setConfigs(await getSharedCosts());
  };

  const loadHistory = async () => {
    setHistory(await getSharedCostHistory());
  };

  const load = async () => {
    setError("");
    try {
      await Promise.all([loadConfigs(), loadHistory()]);
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível carregar o custo compartilhado"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    const timer = setInterval(() => { getSharedCostHistory().then(setHistory).catch(() => {}); }, 3000);
    return () => clearInterval(timer);
  }, []);

  const enabled = configs.filter(config => config.enabled);
  const activeJob = history.find(job => isActive(job.status));

  const run = async () => {
    setRunning(true);
    setError("");
    setStartMessage("");
    try {
      const result = await reprocessSharedCost();
      setStartMessage(result.message + " (task " + result.task_id + "). Acompanhe o andamento no ícone de notificações.");
      await notifications.addTaskById(result.task_id, { kind: "shared_cost", title: "Custo compartilhado" });
      await loadHistory();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível reprocessar o custo compartilhado"));
    } finally {
      setRunning(false);
    }
  };

  const cancel = async (job: SharedCostJob) => {
    setCancelling(job.id);
    try {
      await cancelEtl(job.id);
      await loadHistory();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível cancelar o reprocessamento"));
    } finally {
      setCancelling(null);
    }
  };
  const historyColumns: GridColDef[] = [
    { field: "id", headerName: "ID", width: 80 }, { field: "status", headerName: "Status", width: 130, renderCell: ({ value }) => <Chip size="small" label={value} color={statusColor(String(value))} /> },
    { field: "phase", headerName: "Fase / mensagem", flex: 1, minWidth: 240 }, { field: "rows_written", headerName: "Linhas", width: 120 },
    { field: "started_at", headerName: "Início", type: "dateTime", minWidth: 170, valueGetter: (_, row) => row.started_at ? new Date(String(row.started_at)) : null },
    { field: "actions", headerName: "Ações", width: 120, sortable: false, filterable: false, renderCell: ({ row }) => { const status = String(row.status || "").toUpperCase(); return <Box><Tooltip title="Cancelar"><span><IconButton size="small" color="error" disabled={!isActive(status) || cancelling === row.id} onClick={event => { event.stopPropagation(); void cancel(row as SharedCostJob); }}>{cancelling === row.id ? <CircularProgress size={18} /> : <Stop fontSize="small" />}</IconButton></span></Tooltip><Tooltip title="Ver dados do rateio"><IconButton size="small" onClick={event => { event.stopPropagation(); navigate(`/reprocess/shared-cost/history/${row.id}`); }}><Visibility fontSize="small" /></IconButton></Tooltip></Box>; } },
  ];

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {startMessage && <Alert severity="info" sx={{ mb: 2 }} onClose={() => setStartMessage("")}>{startMessage}</Alert>}
      <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
        <Button
          variant="contained"
          startIcon={running ? <CircularProgress size={16} /> : <Refresh />}
          disabled={running || enabled.length === 0 || Boolean(activeJob)}
          onClick={() => void run()}
        >
          Reprocessar custo compartilhado
        </Button>
        <Button variant="outlined" onClick={() => navigate("/expected-view?shared=1")}>
          Abrir Visão Esperada com custo compartilhado
        </Button>
        <Button variant="text" onClick={() => navigate("/departments")}>Configurar departamentos</Button>
        <Button variant="text" onClick={() => void load()}>Atualizar</Button>
      </Box>
      {loading && <Typography color="text.secondary">Carregando custos compartilhados…</Typography>}
      {!loading && enabled.length === 0 && (
        <Typography color="text.secondary">
          Nenhuma configuração ativa de custo compartilhado. Em Departamentos, edite o nó doador, ligue Custo compartilhado e salve.
        </Typography>
      )}
      <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
        <Typography variant="h6" sx={{ mb: 1 }}>Histórico</Typography>
        {history.length === 0 && (
          <Typography color="text.secondary">Nenhum reprocessamento de custo compartilhado registrado.</Typography>
        )}
        {history.length > 0 && <OperationalHistoryDataGrid rows={history as unknown as Record<string, unknown>[]} columns={historyColumns} />}
      </Paper>
    </Box>
  );
}
