import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useNotifications } from "../../context/NotificationContext";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, IconButton, Stack, Tooltip, Typography,
} from "@mui/material";
import { PlayArrow, Stop, Visibility } from "@mui/icons-material";
import OperationalHistoryDataGrid from "../../components/OperationalHistoryDataGrid";
import { GridColDef } from "@mui/x-data-grid";
import {
  ForecastConfig, ForecastRun, cancelForecastRun, createForecastRun, getForecastConfigs, getForecastRuns,
} from "../../services/api";

const isActive = (status: string) => status === "QUEUED" || status === "RUNNING";
const statusColor = (status: string): "success" | "error" | "info" | "warning" | "default" => {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "error";
  if (status === "CANCELLED") return "warning";
  if (isActive(status)) return "info";
  return "default";
};

export default function ReprocessForecastTab() {
  const navigate = useNavigate();
  const [configs, setConfigs] = useState<ForecastConfig[]>([]);
  const [runs, setRuns] = useState<ForecastRun[]>([]);
  const [error, setError] = useState("");
  const [runningConfig, setRunningConfig] = useState<number | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const notifications = useNotifications();

  const load = async () => {
    try {
      const [nextConfigs, nextRuns] = await Promise.all([getForecastConfigs(), getForecastRuns()]);
      setConfigs(nextConfigs);
      setRuns(nextRuns);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar o forecast");
    }
  };

  useEffect(() => {
    void load();
    const timer = setInterval(() => { getForecastRuns().then(setRuns).catch(() => {}); }, 3000);
    return () => clearInterval(timer);
  }, []);

  const run = async (configId: number) => {
    setRunningConfig(configId);
    setError("");
    try {
      const result = await createForecastRun(configId);
      await notifications.addTaskById(result.task_id, { kind: "forecast", title: "Forecast" });
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao enfileirar o forecast");
    } finally {
      setRunningConfig(null);
    }
  };

  const cancel = async (run: ForecastRun) => {
    setCancelling(run.id);
    try {
      await cancelForecastRun(run.id);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao cancelar o forecast");
    } finally {
      setCancelling(null);
    }
  };
  const runColumns: GridColDef[] = [
    { field: "id", headerName: "ID", width: 80 },
    { field: "status", headerName: "Status", width: 130, renderCell: ({ value }) => <Chip size="small" label={value} color={statusColor(String(value))} /> },
    { field: "rows_written", headerName: "Linhas", width: 120, valueFormatter: v => Number(v || 0).toLocaleString("pt-BR") },
    { field: "phase", headerName: "Fase / mensagem", flex: 1, minWidth: 220 },
    { field: "started_at", headerName: "Início", type: "dateTime", minWidth: 170, valueGetter: (_, row) => row.started_at ? new Date(String(row.started_at)) : null },
    { field: "actions", headerName: "Ações", width: 120, sortable: false, filterable: false, renderCell: ({ row }) => { const status = String(row.status || "").toUpperCase(); return <Stack direction="row"><Tooltip title="Cancelar"><span><IconButton color="error" disabled={!isActive(status) || cancelling === row.id} onClick={event => { event.stopPropagation(); void cancel(row as ForecastRun); }}>{cancelling === row.id ? <CircularProgress size={18} /> : <Stop fontSize="small" />}</IconButton></span></Tooltip><Tooltip title="Abrir resultado"><span><IconButton disabled={status !== "COMPLETED"} onClick={event => { event.stopPropagation(); navigate(`/forecast/history/${row.id}`); }}><Visibility fontSize="small" /></IconButton></span></Tooltip></Stack>; } },
  ];

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        Reprocessa o forecast da tabela Gold configurada. Na sequência, o worker também publica o rateio
        de custo compartilhado e enfileira a materialização do <strong>Gold de visão completa</strong>
        (realizado + forecast + rateio) automaticamente. Acompanhe o andamento no sino de notificações
        (canto superior direito).
      </Alert>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6">Configurações</Typography>
          {configs.length === 0 && (
            <Typography color="text.secondary" sx={{ mt: 1 }}>Nenhuma configuração de forecast. Crie em Forecast.</Typography>
          )}
          {configs.map(config => (
            <Stack key={config.id} direction="row" gap={1} alignItems="center" mt={1} flexWrap="wrap">
              <Typography sx={{ flex: 1 }}>
                {config.name} · {config.measure_column} · {config.horizon_months} meses
              </Typography>
              <Button
                size="small"
                variant="contained"
                startIcon={runningConfig === config.id ? <CircularProgress size={16} /> : <PlayArrow />}
                disabled={runningConfig === config.id}
                onClick={() => void run(config.id)}
              >
                Reprocessar forecast
              </Button>
            </Stack>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <Typography variant="h6">Execuções</Typography>
          {runs.length === 0 ? <Typography color="text.secondary" sx={{ mt: 1 }}>Nenhuma execução registrada.</Typography> : <OperationalHistoryDataGrid rows={runs as unknown as Record<string, unknown>[]} columns={runColumns} />}
        </CardContent>
      </Card>
    </Box>
  );
}
