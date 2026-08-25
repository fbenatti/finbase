import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Card, CardContent, Checkbox, Chip, CircularProgress, FormControl, FormControlLabel,
  InputLabel, LinearProgress, MenuItem, Paper, Select, Stack, Typography,
} from "@mui/material";
import { PlayArrow, Visibility } from "@mui/icons-material";
import {
  ForecastConfig, getForecastConfigs, getReprocessStatus, reprocessForecastAccuracy,
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
      setError(e instanceof Error ? e.message : "Falha ao carregar configurações");
    }
  }, []);

  useEffect(() => {
    void loadConfigs();
  }, [loadConfigs]);

  // Meses disponíveis: de 2026-02 (fevereiro, marco inicial do piloto) até o mês corrente.
  const availableMonths = useMemo(() => {
    const now = new Date();
    const end = now.getFullYear() * 100 + (now.getMonth() + 1);
    const out: string[] = [];
    let cursor = 202602;
    while (cursor <= end) {
      out.push(String(cursor));
      const year = Math.floor(cursor / 100);
      const month = (cursor % 100) + 1;
      cursor = month > 12 ? (year + 1) * 100 + 1 : year * 100 + month;
    }
    return out;
  }, []);

  useEffect(() => {
    setMonths(availableMonths);
    setSelected(new Set(availableMonths));
  }, [availableMonths]);

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
      setError(e instanceof Error ? e.message : "Falha ao consultar o status");
    }
  }, []);

  const run = async () => {
    if (!configId || selected.size === 0) {
      setError("Selecione a configuração e ao menos um mês.");
      return;
    }
    setStarting(true);
    setError("");
    setMessage("");
    try {
      const result = await reprocessForecastAccuracy(Number(configId), [...selected].sort());
      setTaskId(result.task_id);
      setMessage(`Reprocessamento de acurácia enfileirado (task ${result.task_id}): ${result.months.length} meses.`);
      await pollTask(result.task_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao enfileirar o reprocessamento");
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
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        Regenera os snapshots simulados de forecast (simulado em 02/MM, cutoff D-2) para os meses selecionados. O
        dashboard de acurácia usa automaticamente o snapshot mais recente de cada mês.
      </Alert>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage("")}>{message}</Alert>}

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction="row" gap={2} alignItems="center" flexWrap="wrap" sx={{ mb: 2 }}>
            <Typography variant="h6">Configuração</Typography>
            <FormControl size="small" sx={{ minWidth: 320 }}>
              <InputLabel id="acc-reproc-config-label">Configuração de forecast</InputLabel>
              <Select
                labelId="acc-reproc-config-label"
                label="Configuração de forecast"
                value={configId}
                onChange={(e) => setConfigId(e.target.value as number)}
              >
                {configs.map((c) => <MenuItem key={c.id} value={c.id}>{c.name} (config {c.id})</MenuItem>)}
              </Select>
            </FormControl>
          </Stack>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Meses (simulado em 02/MM, cutoff = último dia do mês anterior)</Typography>
          <Paper variant="outlined" sx={{ p: 1.5, mb: 2, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
            {months.map((month) => (
              <FormControlLabel
                key={month}
                control={<Checkbox size="small" checked={selected.has(month)} onChange={() => toggle(month)} />}
                label={prettyMonth(month)}
                sx={{ mr: 0.5 }}
              />
            ))}
            <Button size="small" sx={{ ml: 1 }} onClick={() => setSelected(new Set(months))}>Todos</Button>
            <Button size="small" onClick={() => setSelected(new Set())}>Nenhum</Button>
          </Paper>
          <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
            <Button
              variant="contained"
              startIcon={starting ? <CircularProgress size={16} /> : <PlayArrow />}
              disabled={starting || !configId || selected.size === 0 || isActive(task?.status)}
              onClick={() => void run()}
            >
              Reprocessar acurácia ({selected.size} mês/meses)
            </Button>
            {task && (
              <Chip
                size="small"
                color={statusColor(task.status)}
                label={task.status || "—"}
              />
            )}
            <Button size="small" startIcon={<Visibility />} onClick={() => navigate("/forecast-accuracy")}>
              Abrir dashboard de acurácia
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {task && (
        <Card>
          <CardContent>
            <Typography variant="h6">Andamento</Typography>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 1 }}>
              <Typography variant="body2" color="text.secondary">{task.phase || task.status}</Typography>
              <Typography variant="body2">
                {task.processed_blobs ?? 0}/{task.total_blobs ?? "—"} meses ({progress}%)
              </Typography>
            </Stack>
            <LinearProgress variant={isActive(task.status) ? "indeterminate" : "determinate"} value={progress} sx={{ mt: 1 }} />
            {task.error && <Alert severity="error" sx={{ mt: 1.5 }}>{task.error}</Alert>}
          </CardContent>
        </Card>
      )}

      {taskId && !isActive(task?.status) && (
        <Alert severity="info" sx={{ mt: 2 }}>
          Task {taskId} finalizada. Os snapshots novos já estão disponíveis no dashboard de acurácia.
        </Alert>
      )}
    </Box>
  );
}
