import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  AlertColor,
  Badge,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  LinearProgress,
  Snackbar,
  Stack,
  Typography,
} from "@mui/material";
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

type NotificationKind = "extraction" | "reprocess" | "forecast" | "shared_cost" | "other";

export interface NotificationItem {
  id: number;
  kind: NotificationKind;
  title: string;
  status: string;
  progress: number;
  total: number;
  processed: number;
  rows: number;
  phase: string;
  current: string;
  connectorType?: string;
  timestamp: number;
  read: boolean;
}

function kindOf(task: TaskStatus): NotificationKind {
  const type = String(task.task_type || task.type || "");
  if (type === "forecast") return "forecast";
  if (type === "shared_cost") return "shared_cost";
  if (type === "reprocess" || (!task.connection_id && task.destination_table_id && type !== "extraction")) return "reprocess";
  return "extraction";
}

function titleFor(task: TaskStatus, connections: SourceConnection[]): string {
  const k = kindOf(task);
  if (k === "shared_cost") return "Custo compartilhado";
  if (k === "forecast") return "Forecast";
  if (k === "reprocess") return "Reprocessar dados: " + (task.table || "?");
  const conn = connections.find((c) => c.id === task.connection_id);
  return conn
    ? "Extração: " + conn.name + " (" + conn.code + ")"
    : "Extração #" + String(task.connection_id || "?");
}

function isOperationalSource(task: TaskStatus): boolean {
  if (task.connector_type === "azure_openai_ptu") return true;
  const phase = String(task.phase || "");
  return /azure ptu/i.test(phase) || /azure monitor/i.test(phase);
}

function progressFor(task: TaskStatus): number {
  if (isOperationalSource(task)) {
    if ((task.total_blobs || 0) > 0) {
      return Math.round(((task.processed_blobs || 0) / task.total_blobs) * 100);
    }
    return task.status === "COMPLETED" ? 100 : 0;
  }
  if ((task.total_blobs || 0) > 0) {
    return Math.round(((task.processed_blobs || 0) / task.total_blobs) * 100);
  }
  return 0;
}

function progressCaption(task: TaskStatus): string {
  if (isOperationalSource(task)) {
    const total = task.total_blobs || 0;
    const processed = task.processed_blobs || 0;
    const gold = (task.rows_destination || 0).toLocaleString("pt-BR");
    if (total > 0) {
      return `${processed}/${total} janelas Monitor · ${gold} registros Gold`;
    }
    return `${gold} registros Gold · ARM/Monitor`;
  }
  return `${(task.rows_destination || 0).toLocaleString("pt-BR")} linhas · ${task.processed_blobs}/${task.total_blobs || "?"}`;
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
      total: task.total_blobs || 0,
      processed: task.processed_blobs || 0,
      rows: task.rows_destination || 0,
      phase: task.phase || "",
      current: task.current_blob || "",
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
            total: task.total_blobs || 0,
            processed: task.processed_blobs || 0,
            rows: task.rows_destination || 0,
            phase: task.phase || "",
            current: task.current_blob || "",
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
      const finalStatus = new Map<number, string>();
      if (missing.length > 0) {
        const finals = await Promise.all(
          missing.map((m) => getTaskStatus(m.id).catch(() => null))
        );
        missing.forEach((m, index) => {
          finalStatus.set(m.id, finals[index]?.status || "COMPLETED");
        });
      }
      setItems((prev) => {
        let next = [...prev];
        for (const m of missing) {
          const status = finalStatus.get(m.id) || "COMPLETED";
          const idx = next.findIndex((i) => i.id === m.id);
          if (idx >= 0) {
            const existing = next[idx];
            if (!existing.read) {
              const statusText = status === "COMPLETED" ? "concluído" : "finalizado";
              enqueueSnackbar(
                titleFor({ ...existing, status } as unknown as TaskStatus, connections) + " " + statusText,
                status === "COMPLETED" ? "success" : "error"
              );
            }
            next = next.map((i) =>
              i.id === m.id
                ? {
                    ...i,
                    status,
                    progress: 100,
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
    }),
    [items, unreadCount, dialogOpen, openDialog, closeDialog, addTaskById, remove, clearAll, snackbar, closeSnackbar]
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
          {items.length === 0 && (
            <Typography color="text.secondary" sx={{ py: 2, textAlign: "center" }}>
              Nenhuma notificação no momento.
            </Typography>
          )}
          <Stack spacing={1.5}>
            {items.map((item) => {
              const isActive = item.status === "QUEUED" || item.status === "RUNNING";
              const color = item.status === "COMPLETED" ? "success" : item.status === "FAILED" || item.status === "CANCELLED" ? "error" : "info";
              return (
                <Box key={item.id} sx={{ p: 1.5, bgcolor: "#f8fafc", borderRadius: 1, border: "1px solid #e2e8f0" }}>
                  <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 1 }}>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography variant="body2" fontWeight={600} noWrap>
                        {item.title}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {item.phase || item.status}
                        {item.current ? " · " + item.current : ""}
                      </Typography>
                    </Box>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <Chip size="small" color={color} label={isActive ? item.progress + "%" : item.status} />
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
                      variant={item.total > 0 ? "determinate" : "indeterminate"}
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
                      phase: item.phase,
                      connector_type: item.connectorType,
                    } as TaskStatus)}
                  </Typography>
                </Box>
              );
            })}
          </Stack>
        </DialogContent>
        {items.length > 0 && (
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
          {snackbar.message}
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
