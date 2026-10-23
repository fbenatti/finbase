import { NotificationPanel, NotificationToast, type NotificationKind, type NotificationView } from "../components/NotificationCenter";
import { dataDisplayText } from "../utils/dataPresentation";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AlertColor, Badge, IconButton, Tooltip } from "@mui/material";
import { NotificationsOutlined } from "@mui/icons-material";
import { useAuth } from "../auth/AuthContext";
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
  if (k === "backup") return "Backup diário completo · nº " + task.id;
  if (k === "shared_cost") return "Rateio de custo compartilhado";
  if (k === "forecast") return "Forecast";
  if (String(task.task_type || task.type || "") === "gold_reprocess") {
    return "Base de custos consolidada nº " + task.id;
  }
  if (String(task.task_type || task.type || "") === "ai_cost_relationship_reprocess") {
    return "Relações de custo (IA) nº " + task.id;
  }
  if (k === "reprocess") {
    const sources = task.connections?.length
      ? task.connections.map((source) => source.cloud_name || source.name || source.code)
      : (task.connection_ids || (task.connection_id ? [task.connection_id] : []))
          .map((id) => connections.find((connection) => connection.id === id)?.cloud_name
            || connections.find((connection) => connection.id === id)?.name
            || `conexão nº ${id}`);
    const periods = (task.billing_periods || []).map(formatBillingPeriod);
    const sourceLabel = sources.length ? [...new Set(sources)].join(", ") : "origem não informada";
    const periodLabel = periods.length ? [...new Set(periods)].join(", ") : "período não informado";
    return `Reprocessar dados · ${sourceLabel} · ${periodLabel}`;
  }
  const conn = connections.find((c) => c.id === task.connection_id);
  return conn
    ? "Coleta: " + conn.name + " (" + conn.code + ")"
    : "Coleta nº " + String(task.connection_id || "?");
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
          return source ? `${source.cloud_name || source.name} (${source.code})` : `conexão nº ${id}`;
        });
  const periods = (task.billing_periods || []).map(formatBillingPeriod);
  const destination = task.destination_table_name || task.table || "tabela de destino não informada";
  return `Origem: ${sourceNames.length ? [...new Set(sourceNames)].join(", ") : "sem informação"} · Mês: ${periods.length ? [...new Set(periods)].join(", ") : "não informado"} · Destino: ${destination} (base preparada)`;
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
      ? "Publicando os dados preparados..."
      : "Fonte processada · preparando publicação dos dados";
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
        : "0 linhas · nenhuma série prevista";
    }
    return Number(task.rows_destination || 0) > 0
      ? `${rows} linhas · Forecast em finalização`
      : "Calculando séries do Forecast";
  }
  if (String(task.task_type || task.type || "") === "backup") {
    const bytes = Number(task.downloaded_bytes_total || 0);
    const elapsed = task.started_at ? formatElapsed(Date.now() - new Date(task.started_at).getTime()) : "calculando";
    const volume = bytes > 0 ? `${formatBytes(bytes)} compactados` : "preparando réplica do banco de dados";
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
  /** Permissão de leitura do módulo "notifications" (avisos do sistema). */
  canView: boolean;
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
  const { hasPerm } = useAuth();
  const canView = hasPerm("notifications", "read");
  const canCancel = hasPerm("etl", "create");

  useEffect(() => {
    if (canView) getConnections().then(setConnections).catch(() => {});
  }, [canView]);

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
    if (!canView) {
      setItems([]);
      setDialogOpen(false);
      return;
    }
    poll();
    const timer = setInterval(poll, 3000);
    return () => clearInterval(timer);
  }, [poll, canView]);

  const addTaskById = useCallback(
    async (taskId: number, opts?: { kind?: NotificationKind; title?: string }) => {
      if (!canView) return;
      try {
        const task = await getTaskStatus(taskId);
        if (opts?.kind) {
          (task as { task_type?: string }).task_type = opts.kind;
        }
        upsertTask(task, true);
      } catch { /* ignore */ }
    },
    [upsertTask, canView]
  );

  const openDialog = useCallback(() => {
    // Ao abrir: tudo continua visível e passa a contar como lido.
    setItems((prev) => prev.map((i) => ({ ...i, read: true })));
    setDialogOpen(true);
  }, []);

  // Ao fechar: concluídas/falhas já vistas saem; ativas e as que terminaram com o painel aberto ficam.
  const closeDialog = useCallback(() => {
    setItems((prev) => prev.filter((i) => i.status === "QUEUED" || i.status === "RUNNING" || !i.read));
    setDialogOpen(false);
  }, []);
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
  const views = useMemo<NotificationView[]>(() => sortNotifications(items).map((item) => ({
    id: item.id,
    kind: item.kind,
    title: dataDisplayText(item.title),
    status: item.status,
    progress: item.progress,
    indeterminate: item.kind === "backup" ? item.totalBytes === 0 : item.total <= 0,
    stage: dataDisplayText(reprocessStageLabel(item)),
    caption: progressCaption({
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
    } as TaskStatus),
    scope: item.scope ? dataDisplayText(item.scope) : "",
    startedAt: item.startedAt,
    timestamp: item.timestamp,
  })), [items]);

  const value = useMemo(
    () => ({
      canView,
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
    [canView, items, unreadCount, dialogOpen, openDialog, closeDialog, addTaskById, remove, clearAll, snackbar, closeSnackbar, enqueueSnackbar]
  );

  return (
    <NotificationContext.Provider value={value}>
      {children}
      {canView && (
        <NotificationPanel
          open={dialogOpen}
          items={views}
          onClose={closeDialog}
          onCancel={canCancel ? (id) => {
            const item = items.find((candidate) => candidate.id === id);
            return item ? doCancel(item) : undefined;
          } : undefined}
          onRemove={remove}
          onClearAll={clearAll}
        />
      )}
      <NotificationToast
        open={snackbar.open}
        message={dataDisplayText(snackbar.message)}
        severity={snackbar.severity}
        onClose={closeSnackbar}
        onOpenPanel={canView ? openDialog : undefined}
      />
    </NotificationContext.Provider>
  );
}

export function useNotifications(): NotificationContextValue {
  const ctx = useContext(NotificationContext);
  if (!ctx) throw new Error("useNotifications must be used within NotificationProvider");
  return ctx;
}

export function NotificationBell(): React.JSX.Element | null {
  const { canView, unreadCount, openDialog } = useNotifications();
  if (!canView) return null;
  return (
    <Tooltip title="Avisos">
      <IconButton color="inherit" onClick={openDialog} aria-label={unreadCount ? `Avisos (${unreadCount} novos)` : "Avisos"}>
        <Badge badgeContent={unreadCount} color="error">
          <NotificationsOutlined />
        </Badge>
      </IconButton>
    </Tooltip>
  );
}
