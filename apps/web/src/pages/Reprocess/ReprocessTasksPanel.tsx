import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, LinearProgress, Paper, Typography,
} from "@mui/material";
import { Stop } from "@mui/icons-material";
import {
  TaskStatus, cancelEtl, cancelForecastRun, cancelReprocess, getForecastRuns, getRunningTasks,
} from "../../services/api";

function kind(task: TaskStatus): "forecast" | "reprocess" | "extraction" | "shared_cost" {
  const type = String(task.task_type || task.type || "");
  if (type === "forecast") return "forecast";
  if (type === "shared_cost") return "shared_cost";
  if (type === "reprocess" || (!task.connection_id && task.destination_table_id && type !== "extraction")) return "reprocess";
  return "extraction";
}

function label(task: TaskStatus): string {
  const k = kind(task);
  if (k === "forecast") return "Forecast";
  if (k === "shared_cost") return "Custo compartilhado";
  if (k === "reprocess") return `Reprocessar dados: ${task.table || "?"}`;
  return `Extração${task.table ? `: ${task.table}` : ""}`;
}

export default function ReprocessTasksPanel() {
  const [tasks, setTasks] = useState<TaskStatus[]>([]);
  const [cancelling, setCancelling] = useState<number | null>(null);

  const load = useCallback(() => {
    getRunningTasks().then(setTasks).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [load]);

  const cancel = async (task: TaskStatus) => {
    setCancelling(task.id);
    try {
      const k = kind(task);
      if (k === "reprocess") await cancelReprocess(task.id);
      else if (k === "shared_cost" || k === "extraction") await cancelEtl(task.id);
      else if (k === "forecast") {
        const runs = await getForecastRuns().catch(() => []);
        const active = runs.find(run => ["QUEUED", "RUNNING"].includes(run.status));
        if (active) await cancelForecastRun(active.id);
        else await cancelEtl(task.id);
      } else {
        await cancelEtl(task.id);
      }
      await load();
    } catch { /* ignore */ }
    finally { setCancelling(null); }
  };

  if (tasks.length === 0) {
    return (
      <Alert severity="info" sx={{ mb: 2 }}>
        Nenhuma task em execução. Extração, dados, forecast e custo compartilhado aparecem aqui quando estiverem na fila ou rodando.
      </Alert>
    );
  }

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2, borderLeft: 4, borderColor: "info.main" }}>
      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>
        Tasks ativas ({tasks.length})
      </Typography>
      {tasks.map(task => {
        const pct = task.total_blobs > 0 ? Math.round((task.processed_blobs / task.total_blobs) * 100) : 0;
        const running = task.status === "RUNNING";
        return (
          <Box key={task.id} sx={{ mb: 1.5, p: 1.25, bgcolor: "#f8fafc", borderRadius: 1 }}>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
              <Box>
                <Typography variant="body2" fontWeight={600}>{label(task)}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {task.phase || task.status}
                  {task.current_blob ? ` · ${task.current_blob}` : ""}
                </Typography>
              </Box>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Chip size="small" color="info" label={running ? `${pct}%` : task.status} />
                <Button
                  size="small"
                  color="error"
                  variant="outlined"
                  disabled={cancelling === task.id}
                  startIcon={cancelling === task.id ? <CircularProgress size={14} /> : <Stop />}
                  onClick={() => void cancel(task)}
                >
                  Cancelar
                </Button>
              </Box>
            </Box>
            <LinearProgress
              variant={running && task.total_blobs > 0 ? "determinate" : "indeterminate"}
              value={pct}
              sx={{ mt: 1, height: 6, borderRadius: 3 }}
            />
          </Box>
        );
      })}
    </Paper>
  );
}
