import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Chip, CircularProgress, FormControl, InputLabel, LinearProgress,
  MenuItem, Paper, Select, Stack, Typography,
} from "@mui/material";
import { PlayArrowOutlined, VisibilityOutlined } from "@mui/icons-material";
import {
  ForecastConfig, getForecastAccuracyAvailableMonths, getForecastConfigs, getReprocessStatus, reprocessForecastAccuracy,
} from "../../services/api";

const MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const prettyMonth = (month: string) => `${MONTH_NAMES[Number(month.slice(4, 6)) - 1]}/${month.slice(2, 4)}`;

const isActive = (status?: string) => status === "QUEUED" || status === "RUNNING";
const statusColor = (status?: string): "success" | "error" | "info" | "warning" | "default" => {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "error";
  if (status === "CANCELLED") return "warning";
  if (isActive(status)) return "info";
  return "default";
};

export default function ReprocessAccuracyTab() {
  const navigate = useNavigate();
  const [configs, setConfigs] = useState<ForecastConfig[]>([]);
  const [configId, setConfigId] = useState<number | "">("");
  const [months, setMonths] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [taskId, setTaskId] = useState<number | null>(null);
  const [task, setTask] = useState<{ status?: string; phase?: string; progress?: number; processed_blobs?: number; total_blobs?: number; error?: string } | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [starting, setStarting] = useState(false);

  const loadConfigs = useCallback(async () => {
    try {
      const list = await getForecastConfigs();
      setConfigs(list);
      const active = list.find((c) => c.active) || list[0];
      if (active) setConfigId(active.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar as configurações");
    }
  }, []);

  useEffect(() => {
    void loadConfigs();
  }, [loadConfigs]);

  // Meses disponíveis: vem da API (períodos com dados na Gold + simulados já criados).
  // Nada chumbado — quando setembro/2026 tiver dados, aparece automaticamente aqui.
  useEffect(() => {
    if (!configId) return;
    let cancelled = false;
    getForecastAccuracyAvailableMonths(Number(configId))
      .then((list) => {
        if (cancelled) return;
        setMonths(list);
        setSelected(new Set(list));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Não foi possível carregar as competências disponíveis");
      });
    return () => { cancelled = true; };
  }, [configId]);

  const pollTask = useCallback(async (id: number) => {
    try {
      const status = await getReprocessStatus(id);
      setTask({
        status: status.status,
        phase: status.phase || status.status,
        processed_blobs: status.completed_batches,
        total_blobs: status.total_batches,
        error: status.error,
      });
      if (isActive(status.status)) {
        window.setTimeout(() => void pollTask(id), 3000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível consultar a situação");
    }
  }, []);

  const run = async () => {
    if (!configId || selected.size === 0) {
      setError("Escolha a configuração e ao menos uma competência.");
      return;
    }
    setStarting(true);
    setError("");
    setMessage("");
    try {
      const result = await reprocessForecastAccuracy(Number(configId), [...selected].sort());
      setTaskId(result.task_id);
      setMessage(`Reprocessamento de acerto enviado para a fila (task ${result.task_id}): ${result.months.length} meses.`);
      await pollTask(result.task_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível enviar o reprocessamento para a fila");
    } finally {
      setStarting(false);
    }
  };

  const toggle = (month: string) => {
    const next = new Set(selected);
    if (next.has(month)) next.delete(month);
    else next.add(month);
    setSelected(next);
  };

  const progress = task?.total_blobs && task.total_blobs > 0
    ? Math.round(((task.processed_blobs || 0) / task.total_blobs) * 100)
    : 0;

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h6">Acerto das forecasts</Typography>
        <Typography variant="body2" color="text.secondary">
          Mostra apenas meses com efetivado no mês e no mês anterior, necessário para o forecast simulado em 02/MM
          (cutoff D-2). O painel de precisão usa automaticamente o snapshot mais recente de cada mês.
        </Typography>
      </Box>

      {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {message && <Alert severity="success" onClose={() => setMessage("")}>{dataDisplayText(message)}</Alert>}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }} justifyContent="space-between">
          <FormControl size="small" sx={{ minWidth: 320 }}>
            <InputLabel id="acc-reproc-config-label">Configuração de Forecast</InputLabel>
            <Select
              labelId="acc-reproc-config-label"
              label="Configuração de Forecast"
              value={configId}
              onChange={(e) => setConfigId(e.target.value as number)}
            >
              {configs.map((c) => <MenuItem key={c.id} value={c.id}>{c.name} (config {c.id})</MenuItem>)}
            </Select>
          </FormControl>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            {task && <Chip size="small" color={statusColor(task.status)} label={task.status || "—"} />}
            <Button size="small" startIcon={<VisibilityOutlined />} onClick={() => navigate("/projecoes/acuracia/visao")}>
              Abrir painel de precisão
            </Button>
            <Button
              variant="contained"
              startIcon={starting ? <CircularProgress size={16} /> : <PlayArrowOutlined />}
              disabled={starting || !configId || selected.size === 0 || isActive(task?.status)}
              onClick={() => void run()}
            >
              Reprocessar precisão ({selected.size})
            </Button>
          </Stack>
        </Stack>
      </Paper>

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>Competências (simuladas em 02/MM, corte = último dia do mês anterior)</Typography>
        {months.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            Nenhum mês elegível: é necessário haver efetivado no mês e no mês anterior.
          </Typography>
        ) : (
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {months.map((month) => {
              const isSelected = selected.has(month);
              return <Chip
                key={month}
                label={prettyMonth(month)}
                onClick={() => toggle(month)}
                color={isSelected ? "primary" : "default"}
                variant={isSelected ? "filled" : "outlined"}
              />;
            })}
            <Button size="small" onClick={() => setSelected(new Set(months))}>Marcar todas</Button>
            <Button size="small" onClick={() => setSelected(new Set())}>Limpar</Button>
          </Stack>
        )}
      </Box>

      {task && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2">Em andamento</Typography>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 1 }}>
            <Typography variant="body2" color="text.secondary">{task.phase || task.status}</Typography>
            <Typography variant="body2">
              {task.processed_blobs ?? 0}/{task.total_blobs ?? "—"} meses ({progress}%)
            </Typography>
          </Stack>
          <LinearProgress variant={isActive(task.status) ? "indeterminate" : "determinate"} value={progress} sx={{ mt: 1 }} />
          {task.error && <Alert severity="error" sx={{ mt: 1.5 }}>{dataDisplayText(task.error)}</Alert>}
        </Paper>
      )}

      {taskId && task?.status === "COMPLETED" && (
        <Alert severity="info">
          Task {taskId} finalizada. Os snapshots novos já estão disponíveis no painel de precisão.
        </Alert>
      )}
    </Stack>
  );
}
