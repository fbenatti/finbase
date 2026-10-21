import { Dialog, DialogContent, DialogTitle } from "../components/FinbaseDialog";
import { dataDisplayText } from "../utils/dataPresentation";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Alert, AlertColor, Badge, Box, Button, Chip, CircularProgress, Divider, IconButton, LinearProgress, Snackbar, Stack, Typography } from "@mui/material";
import { Close, DeleteOutline, Notifications, Stop } from "@mui/icons-material";
import {
  cancelEtl,
  cancelForecastRun,
  cancelReprocess,
  getConnections,
  getForecastRuns,
  getRunningTasks,
  getTaskStatus,
  SourceConnection,
  TaskStatus,
} from "../services/api";

type NotificationKind = "extraction" | "reprocess" | "forecast" | "shared_cost" | "backup" | "other";

export interface NotificationItem {
  id: number;
  kind: NotificationKind;
  title: string;
  status: string;
  progress: number;
  total: number;
  processed: number;
  rows: number;
  forecastTotalSeries: number;
  forecastProcessedSeries: number;
  phase: string;
  current: string;
  scope: string;
  bytesWritten: number;
  totalBytes: number;
  startedAt: number;
  connectorType?: string;
  timestamp: number;
  read: boolean;
}

function kindOf(task: TaskStatus): NotificationKind {
  const type = String(task.task_type || task.type || "");
  if (type === "backup") return "backup";
  if (type === "forecast") return "forecast";
  if (type === "shared_cost") return "shared_cost";
  if (type === "gold_reprocess" || type === "ai_cost_relationship_reprocess") return "reprocess";
  if (type === "reprocess" || (!task.connection_id && task.destination_table_id && type !== "extraction")) return "reprocess";
  return "extraction";
}

function titleFor(task: TaskStatus, connections: SourceConnection[]): string {
  const k = kindOf(task);
  if (k === "backup") return "Backup Diário Full · #" + task.id;
  if (k === "shared_cost") return "Custo compartilhado";
  if (k === "forecast") return "Forecast";
  if (String(task.task_type || task.type || "") === "gold_reprocess") {
    return "Custos consolidados #" + task.id;
  }
  if (String(task.task_type || task.type || "") === "ai_cost_relationship_reprocess") {
    return "Relacionamentos de custo IA #" + task.id;
  }
  if (k === "reprocess") {
    const sources = task.connections?.length
      ? task.connections.map((source) => source.cloud_name || source.name || source.code)
      : (task.connection_ids || (task.connection_id ? [task.connection_id] : []))
          .map((id) => connections.find((connection) => connection.id === id)?.cloud_name
            || connections.find((connection) => connection.id === id)?.name
            || `conexão ${id}`);
    const periods = (task.billing_periods || []).map(formatBillingPeriod);
    const sourceLabel = sources.length ? [...new Set(sources)].join(", ") : "origem não informada";
    const periodLabel = periods.length ? [...new Set(periods)].join(", ") : "período não informado";
    return `Reprocessar dados · ${sourceLabel} · ${periodLabel}`;
  }
  const conn = connections.find((c) => c.id === task.connection_id);
  return conn
    ? "Extração: " + conn.name + " (" + conn.code + ")"
    : "Extração #" + String(task.connection_id || "?");
}

function formatBillingPeriod(period: string): string {
  const match = /^(\d{4})(\d{2})$/.exec(String(period));
  if (!match) return String(period);
  const month = Number(match[2]);
  const labels = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  return month >= 1 && month <= 12 ? `${labels[month - 1]}/${match[1]}` : String(period);
}

function scopeFor(task: TaskStatus, connections: SourceConnection[]): string {
  if (kindOf(task) !== "reprocess") return "";
  const sourceNames = task.connections?.length
    ? task.connections.map((source) => `${source.cloud_name || source.name} (${source.code})`)
    : (task.connection_ids || (task.connection_id ? [task.connection_id] : []))
        .map((id) => {
          const source = connections.find((connection) => connection.id === id);
          return source ? `${source.cloud_name || source.name} (${source.code})` : `conexão ${id}`;
        });
  const periods = (task.billing_periods || []).map(formatBillingPeriod);
  const destination = task.destination_table_name || task.table || "destino não informado";
  return `Origem: ${sourceNames.length ? [...new Set(sourceNames)].join(", ") : "não informada"} · Mês: ${periods.length ? [...new Set(periods)].join(", ") : "não informado"} · Destino: ${destination} (base preparada)`;
}

function isOperationalSource(task: TaskStatus): boolean {
  if (task.connector_type === "azure_openai_ptu") return true;
  const phase = String(task.phase || "");
  return /azure ptu/i.test(phase) || /azure monitor/i.test(phase);
}

function isDataPublication(task: TaskStatus): boolean {
  return kindOf(task) === "reprocess"
    && task.status === "RUNNING"
    && /publicando partição na camada silver/i.test(String(task.current_blob || task.phase || ""));
}

function reprocessStageLabel(item: NotificationItem): string {
  if (item.kind !== "reprocess" || item.status !== "RUNNING" || item.total <= 0) {
    return item.phase || item.status;
  }
  if (item.processed >= item.total) {
    return /publicando partição na camada silver/i.test(item.phase + " " + item.current)
      ? "Publicando dados preparados..."
      : "Origem processada · preparando publicação dos dados";
  }
  return item.phase || item.status;
}

function progressFor(task: TaskStatus): number {
  if (kindOf(task) === "forecast") {
    const total = Number(task.forecast_total_series || 0);
    const processed = Number(task.forecast_processed_series || 0);
    if (task.status === "COMPLETED" || task.status === "FAILED" || task.status === "CANCELLED") return 100;
    return total > 0 ? Math.min(99, Math.round((processed / total) * 100)) : 0;
  }
  if (kindOf(task) === "backup") {
    const total = Number(task.total_download_bytes || 0);
    return total > 0 ? Math.min(99, Math.round((Number(task.downloaded_bytes_total || 0) / total) * 100)) : 0;
  }
  if (isDataPublication(task)) return 95;
  if (isOperationalSource(task)) {
    if ((task.total_blobs || 0) > 0) {
      return Math.round(((task.processed_blobs || 0) / task.total_blobs) * 100);
    }
    return task.status === "COMPLETED" ? 100 : 0;
  }
  if ((task.total_blobs || 0) > 0) {
    const blobProgress = Math.min(100, (task.processed_blobs || 0) / task.total_blobs * 100);
    return task.status === "COMPLETED" ? 100 : Math.min(90, Math.round(blobProgress * 0.9));
  }
  return 0;
}

function progressCaption(task: TaskStatus): string {
  if (kindOf(task) === "forecast") {
    const rows = Number(task.rows_destination || 0).toLocaleString("pt-BR");
    const total = Number(task.forecast_total_series || 0);
    const processed = Number(task.forecast_processed_series || 0);
    if (total > 0) return `${rows} linhas · ${processed}/${total} séries`;
    if (task.status === "COMPLETED") {
      return Number(task.rows_destination || 0) > 0
        ? `${rows} linhas · total de séries indisponível`
        : "0 linhas · nenhuma série projetada";
    }
    return Number(task.rows_destination || 0) > 0
      ? `${rows} linhas · forecast em finalização`
      : "Calculando séries do forecast";
  }
  if (String(task.task_type || task.type || "") === "backup") {
    const bytes = Number(task.downloaded_bytes_total || 0);
    const elapsed = task.started_at ? formatElapsed(Date.now() - new Date(task.started_at).getTime()) : "calculando";
    const volume = bytes > 0 ? `${formatBytes(bytes)} compactados` : "preparando cópia do banco de dados";
    return `${volume} · ${elapsed} decorridos · aplicação + banco de dados + acervo`;
  }
  if (isOperationalSource(task)) {
    const total = task.total_blobs || 0;
    const processed = task.processed_blobs || 0;
    const gold = (task.rows_destination || 0).toLocaleString("pt-BR");
    if (total > 0) {
      return `${processed}/${total} janelas Monitor · ${gold} registros consolidados`;
    }
    return `${gold} registros consolidados · ARM/Monitor`;
  }
  return `${(task.rows_destination || 0).toLocaleString("pt-BR")} linhas · ${task.processed_blobs}/${task.total_blobs || "?"}`;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const step = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** step)).toFixed(step >= 3 ? 1 : 0)} ${units[step]}`;
}

function formatElapsed(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${hours}h ${String(minutes).padStart(2, "0")}m`
    : `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

/**
 * Keep the operational queue at the top of the notification dialog.  A
 * running task is the most useful item to see first; among queued tasks the
 * oldest task is next in line.  Finished notifications remain available
 * underneath, ordered from newest to oldest.
 */
function sortNotifications(items: NotificationItem[]): NotificationItem[] {
  const rank = (status: string) => (status === "RUNNING" ? 0 : status === "QUEUED" ? 1 : 2);
  return [...items].sort((a, b) => {
    const rankDifference = rank(a.status) - rank(b.status);
    if (rankDifference !== 0) return rankDifference;
    if (a.status === "QUEUED" && b.status === "QUEUED") {
      return a.startedAt - b.startedAt || a.id - b.id;
    }
    return b.timestamp - a.timestamp || b.id - a.id;
  });
}

interface NotificationContextValue {
  items: NotificationItem[];
  unreadCount: number;
  dialogOpen: boolean;
  openDialog: () => void;
  closeDialog: () => void;
  addTaskById: (taskId: number, opts?: { kind?: NotificationKind; title?: string }) => Promise<void>;
  remove: (taskId: number) => void;
  clearAll: () => void;
  snackbar: { open: boolean; message: string; severity: AlertColor };
  closeSnackbar: () => void;
  enqueueSnackbar: (message: string, severity?: AlertColor) => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: AlertColor }>({
    open: false,
    message: "",
    severity: "info",
  });
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const itemsRef = useRef<NotificationItem[]>([]);

  useEffect(() => {
    getConnections().then(setConnections).catch(() => {});
  }, []);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  const makeItem = useCallback(
    (task: TaskStatus): NotificationItem => ({
      id: task.id,
      kind: kindOf(task),
      title: titleFor(task, connections),
      status: task.status,
      progress: progressFor(task),
      total: kindOf(task) === "forecast" ? Number(task.forecast_total_series || 0) : task.total_blobs || 0,
      processed: kindOf(task) === "forecast" ? Number(task.forecast_processed_series || 0) : task.processed_blobs || 0,
      rows: task.rows_destination || 0,
      forecastTotalSeries: Number(task.forecast_total_series || 0),
      forecastProcessedSeries: Number(task.forecast_processed_series || 0),
      phase: task.phase || "",
      current: task.current_blob || "",
      scope: scopeFor(task, connections),
      bytesWritten: Number(task.downloaded_bytes_total || 0),
      totalBytes: Number(task.total_download_bytes || 0),
      startedAt: task.started_at ? new Date(task.started_at).getTime() : Date.now(),
      connectorType: task.connector_type,
      timestamp: Date.now(),
      read: false,
    }),
    [connections]
  );

  const enqueueSnackbar = useCallback((message: string, severity: AlertColor = "info") => {
    setSnackbar({ open: true, message, severity });
  }, []);

  const upsertTask = useCallback(
    (task: TaskStatus, userInitiated = false) => {
      setItems((prev) => {
        const idx = prev.findIndex((i) => i.id === task.id);
        if (idx >= 0) {
          const existing = prev[idx];
          const wasActive = existing.status === "QUEUED" || existing.status === "RUNNING";
          const isTerminal = task.status !== "QUEUED" && task.status !== "RUNNING";
          if (wasActive && isTerminal && !existing.read) {
            const statusText = task.status === "COMPLETED" ? "concluído" : "finalizado";
            enqueueSnackbar(titleFor(task, connections) + " " + statusText, task.status === "COMPLETED" ? "success" : "error");
          }
          const next = [...prev];
          next[idx] = {
            ...existing,
            kind: kindOf(task),
            title: titleFor(task, connections),
            status: task.status,
            progress: progressFor(task),
            total: kindOf(task) === "forecast" ? Number(task.forecast_total_series || 0) : task.total_blobs || 0,
            processed: kindOf(task) === "forecast" ? Number(task.forecast_processed_series || 0) : task.processed_blobs || 0,
            rows: task.rows_destination || 0,
            forecastTotalSeries: Number(task.forecast_total_series || 0),
            forecastProcessedSeries: Number(task.forecast_processed_series || 0),
            phase: task.phase || "",
            current: task.current_blob || "",
            scope: scopeFor(task, connections),
            bytesWritten: Number(task.downloaded_bytes_total || 0),
            totalBytes: Number(task.total_download_bytes || 0),
            startedAt: task.started_at ? new Date(task.started_at).getTime() : existing.startedAt,
            connectorType: task.connector_type || existing.connectorType,
            read: existing.read && (task.status === "QUEUED" || task.status === "RUNNING") ? existing.read : false,
          };
          return next;
        }
        return [makeItem(task), ...prev];
      });
      if (userInitiated) {
        enqueueSnackbar(titleFor(task, connections) + " iniciado", "info");
      }
    },
    [connections, makeItem, enqueueSnackbar]
  );

  const poll = useCallback(async () => {
    try {
      const tasks = await getRunningTasks();
      const activeIds = new Set(tasks.map((t) => t.id));
      const missing = itemsRef.current.filter(
        (i) => (i.status === "QUEUED" || i.status === "RUNNING") && !activeIds.has(i.id)
      );
      const finalTasks = new Map<number, TaskStatus>();
      if (missing.length > 0) {
        const finals = await Promise.all(
          missing.map((m) => getTaskStatus(m.id).catch(() => null))
        );
        missing.forEach((m, index) => {
          if (finals[index]) finalTasks.set(m.id, finals[index]);
        });
      }
      setItems((prev) => {
        let next = [...prev];
        for (const m of missing) {
          const task = finalTasks.get(m.id);
          if (!task) continue;
          const idx = next.findIndex((i) => i.id === m.id);
          if (idx >= 0) {
            const existing = next[idx];
            const isTerminal = task.status !== "QUEUED" && task.status !== "RUNNING";
            if (isTerminal && !existing.read) {
              const statusText = task.status === "COMPLETED" ? "concluído" : "finalizado";
              enqueueSnackbar(
                titleFor(task, connections) + " " + statusText,
                task.status === "COMPLETED" ? "success" : "error"
              );
            }
            next = next.map((i) =>
              i.id === m.id
                ? {
                    ...i,
                    kind: kindOf(task),
                    title: titleFor(task, connections),
                    status: task.status,
                    progress: progressFor(task),
                    total: kindOf(task) === "forecast" ? Number(task.forecast_total_series || 0) : task.total_blobs || 0,
                    processed: kindOf(task) === "forecast" ? Number(task.forecast_processed_series || 0) : task.processed_blobs || 0,
                    rows: task.rows_destination || 0,
                    forecastTotalSeries: Number(task.forecast_total_series || 0),
                    forecastProcessedSeries: Number(task.forecast_processed_series || 0),
                    phase: task.phase || "",
                    current: task.current_blob || "",
                    scope: scopeFor(task, connections),
                    bytesWritten: Number(task.downloaded_bytes_total || 0),
                    totalBytes: Number(task.total_download_bytes || 0),
                    startedAt: task.started_at ? new Date(task.started_at).getTime() : i.startedAt,
                    connectorType: task.connector_type || i.connectorType,
                    read: false,
                  }
                : i
            );
          }
        }
        for (const task of tasks) {
          const idx = next.findIndex((i) => i.id === task.id);
          if (idx >= 0) {
            const existing = next[idx];
            const wasActive = existing.status === "QUEUED" || existing.status === "RUNNING";
            const isTerminal = task.status !== "QUEUED" && task.status !== "RUNNING";
            if (wasActive && isTerminal && !existing.read) {
              const statusText = task.status === "COMPLETED" ? "concluído" : "finalizado";
              enqueueSnackbar(titleFor(task, connections) + " " + statusText, task.status === "COMPLETED" ? "success" : "error");
            }
            next = next.map((i) =>
              i.id === task.id
                ? {
                    ...i,
                    kind: kindOf(task),
                    title: titleFor(task, connections),
                    status: task.status,
                    progress: progressFor(task),
                    total: task.total_blobs || 0,
                    processed: task.processed_blobs || 0,
                    rows: task.rows_destination || 0,
                    phase: task.phase || "",
                    current: task.current_blob || "",
                    scope: scopeFor(task, connections),
                    bytesWritten: Number(task.downloaded_bytes_total || 0),
                    totalBytes: Number(task.total_download_bytes || 0),
                    startedAt: task.started_at ? new Date(task.started_at).getTime() : i.startedAt,
                    connectorType: task.connector_type || i.connectorType,
                    read: i.read && (task.status === "QUEUED" || task.status === "RUNNING") ? i.read : false,
                  }
                : i
            );
          } else {
            next = [makeItem(task), ...next];
          }
        }
        return next;
      });
    } catch { /* ignore */ }
  }, [connections, makeItem, enqueueSnackbar]);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, 3000);
    return () => clearInterval(timer);
  }, [poll]);

  const addTaskById = useCallback(
    async (taskId: number, opts?: { kind?: NotificationKind; title?: string }) => {
      try {
        const task = await getTaskStatus(taskId);
        if (opts?.kind) {
          (task as { task_type?: string }).task_type = opts.kind;
        }
        upsertTask(task, true);
      } catch { /* ignore */ }
    },
    [upsertTask]
  );

  const openDialog = useCallback(() => {
    setItems((prev) => {
      // Ao abrir: ack das notificações. Concluídas/falhas saem da lista; ativas continuam com progresso.
      return prev
        .filter((i) => i.status === "QUEUED" || i.status === "RUNNING")
        .map((i) => ({ ...i, read: true }));
    });
    setDialogOpen(true);
  }, []);

  const closeDialog = useCallback(() => setDialogOpen(false), []);
  const remove = useCallback((taskId: number) => setItems((prev) => prev.filter((i) => i.id !== taskId)), []);
  const clearAll = useCallback(() => setItems([]), []);
  const closeSnackbar = useCallback(() => setSnackbar((s) => ({ ...s, open: false })), []);

  const doCancel = useCallback(
    async (item: NotificationItem) => {
      try {
        if (item.kind === "forecast") {
          const runs = await getForecastRuns().catch(() => [] as { id: number; status: string }[]);
          const active = runs.find((r) => ["QUEUED", "RUNNING"].includes(r.status));
          if (active) await cancelForecastRun(active.id);
          else await cancelEtl(item.id);
        } else if (item.kind === "reprocess") {
          await cancelReprocess(item.id);
        } else {
          await cancelEtl(item.id);
        }
        poll();
      } catch { /* ignore */ }
    },
    [poll]
  );

  const unreadCount = useMemo(() => items.filter((i) => !i.read).length, [items]);
  const orderedItems = useMemo(() => sortNotifications(items), [items]);

  const value = useMemo(
    () => ({
      items,
      unreadCount,
      dialogOpen,
      openDialog,
      closeDialog,
      addTaskById,
      remove,
      clearAll,
      snackbar,
      closeSnackbar,
      enqueueSnackbar,
    }),
    [items, unreadCount, dialogOpen, openDialog, closeDialog, addTaskById, remove, clearAll, snackbar, closeSnackbar, enqueueSnackbar]
  );

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <Dialog open={dialogOpen} onClose={closeDialog} fullWidth maxWidth="sm">
        <DialogTitle>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>Notificações</span>
            <IconButton size="small" onClick={closeDialog}>
              <Close fontSize="small" />
            </IconButton>
          </Box>
        </DialogTitle>
        <DialogContent dividers>
          {orderedItems.length === 0 && (
            <Typography color="text.secondary" sx={{ py: 2, textAlign: "center" }}>
              Nenhuma notificação no momento.
            </Typography>
          )}
          <Stack spacing={1.5}>
            {orderedItems.map((item) => {
              const isActive = item.status === "QUEUED" || item.status === "RUNNING";
              const color = item.status === "COMPLETED" ? "success" : item.status === "FAILED" || item.status === "CANCELLED" ? "error" : "info";
              return (
                <Box key={item.id} sx={{ p: 1.5, bgcolor: "#FAFBFD", borderRadius: 1, border: "1px solid #E2E5EE" }}>
                  <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 1 }}>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography variant="body2" fontWeight={600} noWrap>
                        {dataDisplayText(item.title)}
                      </Typography>
                      {item.scope && (
                        <Typography variant="caption" color="text.primary" sx={{ display: "block" }}>
                          {dataDisplayText(item.scope)}
                        </Typography>
                      )}
                      <Typography variant="caption" color="text.secondary">
                        {dataDisplayText(reprocessStageLabel(item))}
                        {isActive && item.kind === "reprocess" ? ` · ${item.progress}%` : ""}
                        {item.kind !== "backup" && item.kind !== "reprocess" && item.current && item.current !== item.phase ? " · " + dataDisplayText(item.current) : ""}
                      </Typography>
                    </Box>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <Chip
                        size="small"
                        color={color}
                        label={isActive ? (item.kind === "backup" && item.totalBytes === 0 ? "EM ANDAMENTO" : item.progress + "%") : item.status}
                      />
                      {isActive && (
                        <IconButton size="small" color="error" onClick={() => void doCancel(item)}>
                          <Stop fontSize="small" />
                        </IconButton>
                      )}
                      <IconButton size="small" onClick={() => remove(item.id)}>
                        <DeleteOutline fontSize="small" />
                      </IconButton>
                    </Box>
                  </Box>
                  {isActive && (
                    <LinearProgress
                      variant={item.kind === "backup" && item.totalBytes === 0 ? "indeterminate" : item.total > 0 ? "determinate" : "indeterminate"}
                      value={item.progress}
                      sx={{ height: 5, borderRadius: 2, mt: 1 }}
                    />
                  )}
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                    {progressCaption({
                      id: item.id,
                      status: item.status,
                      total_blobs: item.total,
                      processed_blobs: item.processed,
                      rows_destination: item.rows,
                      forecast_total_series: item.forecastTotalSeries,
                      forecast_processed_series: item.forecastProcessedSeries,
                      task_type: item.kind === "backup" ? "backup" : item.kind === "forecast" ? "forecast" : undefined,
                      downloaded_bytes_total: item.bytesWritten,
                      started_at: new Date(item.startedAt).toISOString(),
                      total_download_bytes: item.totalBytes,
                      phase: item.phase,
                      connector_type: item.connectorType,
                    } as TaskStatus)}
                  </Typography>
                </Box>
              );
            })}
          </Stack>
        </DialogContent>
        {orderedItems.length > 0 && (
          <Box sx={{ p: 1.5, display: "flex", justifyContent: "flex-end" }}>
            <Button size="small" variant="outlined" onClick={clearAll} startIcon={<DeleteOutline fontSize="small" />}>
              Limpar notificações
            </Button>
          </Box>
        )}
      </Dialog>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={closeSnackbar}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity={snackbar.severity} onClose={closeSnackbar} sx={{ width: "100%" }}>
          {dataDisplayText(snackbar.message)}
        </Alert>
      </Snackbar>
    </NotificationContext.Provider>
  );
}

export function useNotifications(): NotificationContextValue {
  const ctx = useContext(NotificationContext);
  if (!ctx) throw new Error("useNotifications must be used within NotificationProvider");
  return ctx;
}

export function NotificationBell(): React.JSX.Element {
  const { unreadCount, openDialog } = useNotifications();
  return (
    <IconButton color="inherit" onClick={openDialog}>
      <Badge badgeContent={unreadCount} color="error">
        <Notifications />
      </Badge>
    </IconButton>
  );
}
