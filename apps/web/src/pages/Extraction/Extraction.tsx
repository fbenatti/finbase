import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box, Typography, Button, TableCell, Paper, Chip, LinearProgress,
  Alert, Select, MenuItem, FormControl, InputLabel, Checkbox,
  CircularProgress, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  FormControlLabel, Switch, IconButton, Tabs, Tab, Stack, Table, TableHead, TableRow, TableBody, TableContainer,
} from "@mui/material";
import { PlayArrow, Refresh, Stop, Delete, DeleteForever, Block, Undo, Storage, Schedule, Add, Edit, CloudUpload, Download, TableChart, Visibility, History as HistoryIcon } from "@mui/icons-material";
import OperationalHistoryDataGrid from "../../components/OperationalHistoryDataGrid";
import { GridColDef } from "@mui/x-data-grid";
import { DataGrid } from "@mui/x-data-grid";
import {
  getConnections, scanBlobs, runEtl, getTaskStatus, getIngestedBlobs, cancelEtl,
  resetSourceForReimport,
  deleteIngestedBlob, deleteAllIngestedBlobs, downloadIngestedBlob,
  getSkippedBlobs, skipBlob as apiSkipBlob, unskipBlob as apiUnskipBlob,
  clearSkippedBlobs,
  processDestinationTables,
  getSchedules, createSchedule, updateSchedule, deleteSchedule, runScheduleNow,
  getExtractionHistory, ExtractionHistoryItem,
  getAvailablePeriods, AvailablePeriod,
  ScheduledTask,
  SourceConnection, BlobScanResult, BlobScanItem, IngestedBlob, SkippedBlob, TaskStatus,
  SCANNABLE_CONNECTOR_TYPES, formatApiError,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable from "../../components/SortableTable";
import ManualCsvUploadDialog from "../Connections/ManualCsvUploadDialog";

const STATUS_COLORS: Record<string, "success" | "warning" | "error" | "info" | "default"> = {
  NEW: "success", CHANGED: "warning", UNCHANGED: "default", SKIPPED: "default",
  INGESTED: "success", FAILED: "error",
  RUNNING: "info", COMPLETED: "success", COMPLETED_WITH_ERRORS: "warning", CANCELLED: "warning",
};

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function Extraction({ embedded = false }: { embedded?: boolean }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tabKey = embedded ? "extTab" : "tab";
  const tab = params.get(tabKey) || "extraction";
  const setTab = (newTab: string) => {
    const next = new URLSearchParams(params);
    next.set(tabKey, newTab);
    setParams(next, { replace: true });
  };

  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [connId, setConnId] = useState<number | "">("");
  const [scanResult, setScanResult] = useState<BlobScanResult | null>(null);
  const [blobs, setBlobs] = useState<IngestedBlob[]>([]);
  const [scanning, setScanning] = useState(false);
  const [task, setTask] = useState<TaskStatus | null>(null);
  const [error, setError] = useState("");
  const [sourceResetMessage, setSourceResetMessage] = useState("");
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [blobFilter, setBlobFilter] = useState("");
  const [skippedBlobs, setSkippedBlobs] = useState<SkippedBlob[]>([]);
  const [skippingPaths, setSkippingPaths] = useState<Set<string>>(new Set());
  const [processingDest, setProcessingDest] = useState(false);
  const [destProcessResult, setDestProcessResult] = useState<string>("");
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const skippedPaths = new Set(skippedBlobs.map(s => s.blob_path));

  // ── Extraction History state ──
  const [historyList, setHistoryList] = useState<ExtractionHistoryItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // ── Schedules state ──
  const [schedules, setSchedules] = useState<ScheduledTask[]>([]);
  const [schedDialogOpen, setSchedDialogOpen] = useState(false);
  const [schedEditing, setSchedEditing] = useState<ScheduledTask | null>(null);
  const [schedForm, setSchedForm] = useState({
    name: "", connection_id: 0, cron_expression: "0 3 * * *",
    timezone: "America/Sao_Paulo", destination_reprocess: true, active: true,
    forecast_after_ingestion: false, forecast_config_id: "",
  });
  const [schedRunning, setSchedRunning] = useState<Set<number>>(new Set());
  const [uploadConn, setUploadConn] = useState<SourceConnection | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      setLoadingHistory(true);
      const data = await getExtractionHistory();
      setHistoryList(data);
    } catch {
      /* ignore */
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    if (tab === "history" || embedded) {
      void loadHistory();
    }
  }, [tab, loadHistory]);

  // Connections eligible for extraction/monitoring (cloud scan + manual CSV upload).
  const extractableConnectorTypes = useMemo(
    () => new Set([...SCANNABLE_CONNECTOR_TYPES, "manual_csv"]),
    [],
  );
  const extractableConnections = useMemo(
    () => connections.filter((c) => extractableConnectorTypes.has(c.connector_type)),
    [connections, extractableConnectorTypes],
  );
  const isManualCsvSelected = useMemo(
    () => !!uploadConn || extractableConnections.some((c) => c.id === connId && c.connector_type === "manual_csv"),
    [extractableConnections, connId, uploadConn],
  );
  const isAzurePtuSelected = useMemo(
    () => extractableConnections.some((c) => c.id === connId && c.connector_type === "azure_openai_ptu"),
    [extractableConnections, connId],
  );
  const [ptuPeriods, setPtuPeriods] = useState<AvailablePeriod[]>([]);
  const [selPtuPeriods, setSelPtuPeriods] = useState<string[]>([]);
  const [loadingPtuPeriods, setLoadingPtuPeriods] = useState(false);

  const formatPtuPeriod = (period: string) => {
    if (period.length !== 6) return period;
    return `${period.slice(4)}/${period.slice(0, 4)}`;
  };

  useEffect(() => {
    if (!connId || !isAzurePtuSelected) {
      setPtuPeriods([]);
      setSelPtuPeriods([]);
      return;
    }
    setLoadingPtuPeriods(true);
    getAvailablePeriods(connId as number)
      .then((rows) => {
        setPtuPeriods(rows);
        const current = rows[0]?.period;
        setSelPtuPeriods(current ? [current] : []);
      })
      .catch(() => {
        setPtuPeriods([]);
        setSelPtuPeriods([]);
      })
      .finally(() => setLoadingPtuPeriods(false));
  }, [connId, isAzurePtuSelected]);

  useEffect(() => {
    getConnections().then((list) => {
      setConnections(list);
      const extractable = list.filter((c) => extractableConnectorTypes.has(c.connector_type));
      if (extractable.length === 1) setConnId(extractable[0].id);
    }).catch((e) => setError(formatApiError(e, "Falha ao carregar conexoes")));
    loadSchedules();
  }, [extractableConnectorTypes]);

  const loadSchedules = () => {
    getSchedules().then(setSchedules).catch(() => {});
  };

  const loadBlobs = useCallback(async () => {
    if (!connId) return;
    try { setBlobs(await getIngestedBlobs(connId as number)); } catch { /* ignore */ }
  }, [connId]);

  const loadSkipped = useCallback(async () => {
    if (!connId) return;
    try { setSkippedBlobs(await getSkippedBlobs(connId as number)); } catch { /* ignore */ }
  }, [connId]);

  useEffect(() => { loadBlobs(); loadSkipped(); }, [connId, loadBlobs, loadSkipped]);

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  useEffect(() => () => stopPolling(), []);

  const doScan = async () => {
    if (!connId) return;
    setScanning(true); setError(""); setTask(null); setSelectedPaths(new Set()); setBlobFilter("");
    try { setScanResult(await scanBlobs(connId as number)); }
    catch (e: unknown) { setError(formatApiError(e, "Falha ao escanear")); }
    finally { setScanning(false); }
  };

  const doRun = async (mode: "incremental" | "period" | "backfill" = "incremental") => {
    if (!connId) return;
    setError(""); setTask(null);
    const visibleSelected = scanResult
      ? Array.from(selectedPaths).filter(path => filteredScanItems.some(item => item.blob_path === path))
      : Array.from(selectedPaths);
    const paths = visibleSelected.length > 0 ? visibleSelected : undefined;
    try {
      const body: Parameters<typeof runEtl>[0] = { connection_id: connId as number, selected_blobs: paths };
      if (isAzurePtuSelected) {
        if (mode === "period") {
          if (selPtuPeriods.length === 0) {
            setError("Selecione ao menos um mês para buscar métricas históricas.");
            return;
          }
          body.force_periods = selPtuPeriods;
        } else if (mode === "backfill") {
          body.backfill = true;
        } else if (mode === "incremental") {
          body.lookback_hours = 3;
        }
      }
      const result = await runEtl(body);
      if (result.task_id === 0) {
        setError(result.message || "ETL not available for this connector type");
        return;
      }
      pollTask(result.task_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start ETL");
    }
  };

  const doResetSource = async () => {
    if (!connId || !confirm("Isso removerá o histórico de ingestão e o Bronze desta fonte. Os CSVs do Azure não serão apagados. Continuar?")) return;
    try {
      await resetSourceForReimport(connId as number);
      setTask(null); setBlobs([]); setScanResult(null);
      setSourceResetMessage("Fonte resetada. Execute Escanear e depois importe os CSVs atuais.");
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Falha ao resetar a fonte"); }
  };

  const doCancelTask = async (taskId: number) => {
    setTask(prev => prev?.id === taskId ? { ...prev, status: "CANCELLING" } : prev);
    try {
      await cancelEtl(taskId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    }
  };

  const doProcessDest = async () => {
    if (!connId) return;
    setProcessingDest(true);
    setDestProcessResult("");
    try {
      const result = await processDestinationTables(connId as number);
      if (result.success) {
        setDestProcessResult(`OK: ${result.rows_written.toLocaleString()} linhas escritas na tabela destino`);
      } else {
        setDestProcessResult(`Erro: ${result.error || "Falha desconhecida"}`);
      }
    } catch (e: unknown) {
      setDestProcessResult(`Erro: ${e instanceof Error ? e.message : "Falha ao processar destino"}`);
    } finally {
      setProcessingDest(false);
    }
  };

  // ── Schedule handlers ──
  const openNewSched = () => {
    setSchedEditing(null);
    setSchedForm({ name: "", connection_id: connId as number || 0, cron_expression: "0 3 * * *", timezone: "America/Sao_Paulo", destination_reprocess: true, active: true, forecast_after_ingestion: false, forecast_config_id: "" });
    setSchedDialogOpen(true);
  };

  const openEditSched = (s: ScheduledTask) => {
    setSchedEditing(s);
    const cfg = s.config_json || {};
    setSchedForm({ name: s.name, connection_id: s.connection_id, cron_expression: s.cron_expression, timezone: s.timezone, destination_reprocess: s.destination_reprocess, active: s.active, forecast_after_ingestion: Boolean(cfg.forecast_after_ingestion), forecast_config_id: String(cfg.forecast_config_id || "") });
    setSchedDialogOpen(true);
  };

  const saveSched = async () => {
    try {
      if (schedEditing) {
        await updateSchedule(schedEditing.id, { ...schedForm, config_json: { forecast_after_ingestion: schedForm.forecast_after_ingestion, forecast_config_id: schedForm.forecast_config_id ? Number(schedForm.forecast_config_id) : null } });
      } else {
        await createSchedule({ ...schedForm, config_json: { forecast_after_ingestion: schedForm.forecast_after_ingestion, forecast_config_id: schedForm.forecast_config_id ? Number(schedForm.forecast_config_id) : null } });
      }
      setSchedDialogOpen(false);
      loadSchedules();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to save schedule");
    }
  };

  const deleteSched = async (id: number) => {
    if (!confirm("Remover agendamento?")) return;
    try { await deleteSchedule(id); loadSchedules(); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  };

  const runSchedNow = async (id: number) => {
    setSchedRunning(prev => new Set(prev).add(id));
    try {
      await runScheduleNow(id);
      setError("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to run schedule");
    } finally {
      setSchedRunning(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  };

  const formatNextRun = (s: ScheduledTask): string => {
    if (!s.next_run_at) return "—";
    const d = new Date(s.next_run_at);
    return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const formatLastRun = (s: ScheduledTask): string => {
    if (!s.last_run_at) return "Nunca";
    const d = new Date(s.last_run_at);
    return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const cronPresets = [
    { label: "Diário 03:00", value: "0 3 * * *" },
    { label: "Diário 06:00", value: "0 6 * * *" },
    { label: "Semanal (seg 03:00)", value: "0 3 * * 1" },
    { label: "Mensal (dia 1 03:00)", value: "0 3 1 * *" },
    { label: "A cada 12h", value: "0 */12 * * *" },
    { label: "A cada 6h", value: "0 */6 * * *" },
  ];

  const pollTask = (taskId: number) => {
    stopPolling();
    const fetchStatus = async () => {
      try {
        const t = await getTaskStatus(taskId);
        setTask(t);
        if (["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].includes(t.status)) {
          stopPolling();
          loadBlobs();
        }
      } catch { stopPolling(); }
    };
    void fetchStatus();
    pollRef.current = setInterval(fetchStatus, 2000);
  };

  const toggleSelect = (path: string) => {
    setSelectedPaths(prev => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });
  };

  const toggleAll = () => {
    if (!scanResult) return;
    const eligible = filteredScanItems
      .filter(i => i.status !== "UNCHANGED" && i.status !== "SKIPPED" && !skippedPaths.has(i.blob_path))
      .map(i => i.blob_path);
    const allSelected = eligible.length > 0 && eligible.every(path => selectedPaths.has(path));
    // Bulk selection belongs to the current filter only. Do not retain hidden
    // rows from a previous filter, otherwise subsequent import/ACK is unsafe.
    setSelectedPaths(allSelected ? new Set() : new Set(eligible));
  };

  const filteredScanItems = useMemo(() => {
    if (!scanResult || !blobFilter.trim()) return scanResult?.items || [];
    const query = blobFilter.trim().toLowerCase();
    return scanResult.items.filter(item => [item.blob_path, item.billing_period, item.status]
      .some(value => String(value ?? "").toLowerCase().includes(query)));
  }, [scanResult, blobFilter]);

  const filteredEligibleItems = useMemo(() => filteredScanItems.filter(item =>
    item.status !== "UNCHANGED" && item.status !== "SKIPPED" && !skippedPaths.has(item.blob_path)
  ), [filteredScanItems, skippedBlobs]);

  const runSelectedPaths = async (paths: string[]) => {
    if (!connId || paths.length === 0 || isActive) return;
    setError(""); setTask(null);
    try {
      const result = await runEtl({ connection_id: connId as number, selected_blobs: paths });
      if (result.task_id === 0) {
        setError(result.message || "ETL indisponível para esta conexão");
        return;
      }
      setSelectedPaths(new Set(paths));
      pollTask(result.task_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao iniciar a importação");
    }
  };

  const doImportFiltered = () => runSelectedPaths(filteredEligibleItems.map(item => item.blob_path));

  const doAckPaths = async (paths: string[]) => {
    if (!connId || paths.length === 0 || isActive) return;
    if (!confirm(`Marcar ${paths.length} blob(s) como ACK/ignorado(s)?`)) return;
    setError("");
    try {
      // Keep the requests in small batches so a scan with many files does not
      // overload the API or leave part of the selection without feedback.
      for (let index = 0; index < paths.length; index += 10) {
        await Promise.all(paths.slice(index, index + 10).map(path => apiSkipBlob(connId as number, path, "ACK em massa")));
      }
      setSelectedPaths(new Set());
      await loadSkipped();
      await doScan();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao marcar blobs como ignorados");
    }
  };

  const doClearAllAcks = async () => {
    if (!connId || skippedBlobs.length === 0 || isActive) return;
    if (!confirm(`Remover todos os ${skippedBlobs.length} ACK(s) desta conexão?`)) return;
    try {
      await clearSkippedBlobs(connId as number);
      setSelectedPaths(new Set());
      await loadSkipped();
      await doScan();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao remover ACKs");
    }
  };

  const doDeleteBlob = async (blobId: number) => {
    if (!confirm("Remover este dataset da lista de ingestao?")) return;
    try {
      await deleteIngestedBlob(blobId);
      // Remove immediately from the visible list and reconcile with the API.
      setBlobs(prev => prev.filter(b => b.id !== blobId));
      await loadBlobs();
    } catch (e: unknown) {
      // A stale row may have been deleted by another tab/session. Refresh so
      // the UI cannot keep offering an action for an already deleted blob.
      await loadBlobs();
      const status = (e as { response?: { status?: number } })?.response?.status;
      if (status !== 404) setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const handleDownloadBlob = async (blobId: number, format: "csv" | "xlsx") => {
    const key = `${blobId}-${format}`;
    setDownloadingId(key);
    try {
      await downloadIngestedBlob(blobId, format);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Falha ao baixar arquivo");
    } finally {
      setDownloadingId(null);
    }
  };


  const doDeleteAll = async () => {
    if (!connId) return;
    if (!confirm("Remover TODOS os datasets ingeridos desta conexao? Esta acao nao pode ser desfeita.")) return;
    try {
      await deleteAllIngestedBlobs(connId as number);
      setBlobs([]);
    } catch (e: unknown) {setError(e instanceof Error ? e.message : "Delete all failed");}
  };

  const doSkipBlob = async (blobPath: string) => {
    if (!connId) return;
    setSkippingPaths(prev => new Set(prev).add(blobPath));
    try {
      await apiSkipBlob(connId as number, blobPath);
      loadSkipped();
      doScan();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Skip failed"); }
    finally { setSkippingPaths(prev => { const next = new Set(prev); next.delete(blobPath); return next; }); }
  };

  const doUnskipBlob = async (skipId: number, blobPath: string) => {
    setSkippingPaths(prev => new Set(prev).add(blobPath));
    try {
      await apiUnskipBlob(skipId);
      loadSkipped();
      doScan();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Unskip failed"); }
    finally { setSkippingPaths(prev => { const next = new Set(prev); next.delete(blobPath); return next; }); }
  };

  const isTerminal = task?.status ? ["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "CANCELLING"].includes(task.status) : false;
  const isQueued = task?.status === "QUEUED";
  const isPtuTask = isAzurePtuSelected || task?.connector_type === "azure_openai_ptu" || /azure ptu/i.test(task?.phase || "");
  const progress = task && task.total_blobs > 0
    ? isTerminal ? 100 : (task.processed_blobs / task.total_blobs) * 100
    : isTerminal ? 100 : 0;
  const isRunning = task?.status === "RUNNING";
  const isCancelling = task?.status === "CANCELLING";
  const isActive = isRunning || isQueued || isCancelling;
  const visibleSelectedCount = scanResult
    ? Array.from(selectedPaths).filter(path => filteredScanItems.some(item => item.blob_path === path)).length
    : selectedPaths.size;
  const hasSelection = visibleSelectedCount > 0;

  // Download progress
  const dlBlobSize = task?.current_blob_size || 0;
  const dlBytes = task?.download_bytes || 0;
  const dlBlobProgress = dlBlobSize > 0 ? Math.min(100, (dlBytes / dlBlobSize) * 100) : 0;
  const dlTotal = task?.total_download_bytes || 0;
  const dlCumulative = task?.downloaded_bytes_total || 0;
  const dlTotalProgress = dlTotal > 0 ? Math.min(100, (dlCumulative / dlTotal) * 100) : 0;
  const isDownloading = isRunning && task?.current_blob && dlBlobSize > 0 && dlBytes < dlBlobSize;

  return (
    <Box>
      {!embedded && <Typography variant="h4" sx={{ mb: 2 }}>Extração & Monitoramento</Typography>}

      {!embedded && <Tabs value={tab} onChange={(_, val) => setTab(val)} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
        <Tab label="Extração & Agendamentos" value="extraction" />
        <Tab label="Histórico de Execuções" value="history" icon={<HistoryIcon sx={{ fontSize: 18 }} />} iconPosition="start" />
      </Tabs>}

      {(tab === "extraction" || embedded) && (
        <>
          <Box sx={{ display: "flex", gap: 2, mb: 3, alignItems: "flex-start", flexWrap: "wrap" }}>
            <FormControl size="small" sx={{ minWidth: 280 }}>
              <InputLabel>Conexao</InputLabel>
              <Select value={connId} label="Conexao" onChange={(e) => { setConnId(e.target.value as number); setScanResult(null); setTask(null); }}>
                {extractableConnections.map((c) => (
                  <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
                ))}
              </Select>
            </FormControl>
        {connections.length > 0 && extractableConnections.length === 0 && (
          <Alert severity="warning">Nenhuma conexao disponivel para extracao.</Alert>
        )}

        {isManualCsvSelected ? (
          <Button
            variant="contained"
            color="primary"
            startIcon={<CloudUpload />}
            onClick={() => {
              const conn = extractableConnections.find((c) => c.id === connId);
              if (conn) setUploadConn(conn);
            }}
          >
            Upload CSV
          </Button>
        ) : (
          <>
            {!isAzurePtuSelected && <Button variant="outlined" startIcon={scanning ? <CircularProgress size={18} /> : <Refresh />} onClick={doScan} disabled={scanning || !connId}>
              {scanning ? "Escaneando..." : "Escanear"}
            </Button>}
            <Button variant="contained" startIcon={<PlayArrow />} onClick={() => doRun("backfill")}
              disabled={isActive || !connId || !!task?.status?.includes("CANCELLING")}
              color="primary">
              {isAzurePtuSelected ? "Importar todos os dados disponíveis (~93 dias)" : (hasSelection ? `Importar ${visibleSelectedCount} selecionados` : "Importar Todos")}
            </Button>
            {isAzurePtuSelected && (
              <>
                <Button
                  variant="outlined"
                  startIcon={<PlayArrow />}
                  onClick={() => doRun("incremental")}
                  disabled={isActive || !connId}
                >
                  Coletar recente (lookback 3h)
                </Button>
                <Button
                  variant="contained"
                  color="secondary"
                  startIcon={<PlayArrow />}
                  onClick={() => doRun("period")}
                  disabled={isActive || !connId || selPtuPeriods.length === 0 || loadingPtuPeriods}
                >
                  Buscar métricas do período
                </Button>
              </>
            )}
            <Button variant="outlined" color="warning" startIcon={<DeleteForever />} onClick={doResetSource}
              disabled={isActive || !connId}>
              Reprocessar origem limpa
            </Button>
          </>
        )}
        {isActive && (
          <Button
            variant="outlined"
            color="error"
            disabled={isCancelling}
            startIcon={isCancelling ? <CircularProgress size={18} /> : <Stop />}
            onClick={() => doCancelTask(task!.id)}
          >
            {isCancelling ? "Cancelando..." : "Parar"}
          </Button>
        )}
        <Tooltip title="Processa os dados normalizados para a tabela de destino (MODELO_FOCUS) usando os mapeamentos configurados em Relacionamento.">
          <Button
            variant="outlined"
            color="secondary"
            startIcon={processingDest ? <CircularProgress size={18} /> : <Storage />}
            onClick={doProcessDest}
            disabled={isActive || !connId || processingDest}
          >
            {processingDest ? "Processando..." : "Processar Destino"}
          </Button>
        </Tooltip>
      </Box>

      {destProcessResult && (
        <Alert severity={destProcessResult.startsWith("OK") ? "success" : "error"} sx={{ mb: 2 }} onClose={() => setDestProcessResult("")}>
          {destProcessResult}
        </Alert>
      )}

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {sourceResetMessage && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSourceResetMessage("")}>{sourceResetMessage}</Alert>}

      {isAzurePtuSelected && connId && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2" sx={{ mb: 1 }}>
            Azure Monitor retém métricas de plataforma por <strong>~93 dias</strong>. Use <strong>Importar todos os dados disponíveis</strong>
            para carregar o histórico completo (~93 dias) em <code>dest_azure_ptu_metrics</code>, ou <strong>Buscar métricas do período</strong>
            para um mês específico. O agendamento horário (:10) coleta apenas o lookback recente (~3h).
          </Typography>
          {loadingPtuPeriods ? (
            <CircularProgress size={18} />
          ) : ptuPeriods.length > 0 ? (
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {ptuPeriods.map((p) => (
                <Chip
                  key={p.period}
                  label={`${p.label || formatPtuPeriod(p.period)} (${p.blob_count} janelas)`}
                  size="small"
                  variant={selPtuPeriods.includes(p.period) ? "filled" : "outlined"}
                  color={selPtuPeriods.includes(p.period) ? "primary" : "default"}
                  onClick={() => setSelPtuPeriods(prev =>
                    prev.includes(p.period) ? prev.filter(x => x !== p.period) : [...prev, p.period]
                  )}
                  sx={{ cursor: "pointer" }}
                />
              ))}
            </Stack>
          ) : (
            <Typography variant="caption" color="text.secondary">Nenhum mês disponível dentro da retenção Monitor.</Typography>
          )}
        </Alert>
      )}

      {extractableConnections.find(c => c.id === connId)?.connector_type === "oci" && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <strong>Consolidação Inteligente OCI:</strong> Os dados da Oracle Cloud são agrupados e consolidados por dia e recurso para garantir alta performance e tabelas otimizadas, preservando 100% dos valores financeiros, quantidades e tags de negócio.
        </Alert>
      )}

      {scanning && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 2, my: 4, p: 3, bgcolor: "#f5f5f5", borderRadius: 2 }}>
          <CircularProgress size={28} />
          <Box>
            <Typography variant="body1" fontWeight={500}>
              {isAzurePtuSelected ? "Descobrindo deployments PTU..." : "Escaneando blobs..."}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {isAzurePtuSelected
                ? "Consulta ARM Resource Graph e deployments com SKU provisionado"
                : "Buscando arquivos disponiveis na fonte de dados"}
            </Typography>
          </Box>
        </Box>
      )}

      {task && (
        <Box sx={{ mb: 3 }}>
          <Box sx={{ display: "flex", gap: 2, mb: 1, alignItems: "center", flexWrap: "wrap" }}>
            <Chip label={task.status} color={STATUS_COLORS[task.status] || "default"} size="small" />
            <Typography variant="body2">
              {isQueued ? (
                "Aguardando worker..."
              ) : isPtuTask ? (
                <>
                  {task.total_blobs > 0
                    ? `${task.processed_blobs} / ${task.total_blobs} janelas Monitor`
                    : "Coleta ARM/Monitor em andamento"}
                  {" | "}{(task.rows_destination ?? 0).toLocaleString()} registros Gold
                </>
              ) : (
                <>
                  {task.processed_blobs} / {task.total_blobs || "?"} blobs
                  {" | "}{(task.rows_raw ?? 0).toLocaleString()} raw / {(task.rows_normalized ?? 0).toLocaleString()} norm
                  {task.active_downloads ? ` | ${task.active_downloads}/2 downloads` : ""}
                  {task.rows_destination > 0 && ` / ${task.rows_destination.toLocaleString()} dest`}
                </>
              )}
            </Typography>
          </Box>
          <LinearProgress
            variant={isQueued || (isPtuTask && !task.total_blobs && !isTerminal) ? "indeterminate" : (task.total_blobs || isTerminal ? "determinate" : "indeterminate")}
            value={isQueued ? 0 : progress}
            color={isQueued ? "warning" : (isTerminal && task.status !== "FAILED" ? "success" : "primary")}
            sx={{ mb: 1 }}
          />

          {/* Download status — only for RUNNING tasks with download data */}
          {isRunning && !isPtuTask && (
            <Box sx={{ mb: 1 }}>
              {/* Per-blob download bar */}
              {task.current_blob && dlBlobSize > 0 && (
                <Box sx={{ mb: 0.5 }}>
                  <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 0.3 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace", fontSize: "0.7rem" }}>
                      {task.current_blob.split("/").pop()}
                    </Typography>
                    <Typography variant="caption" color="primary" sx={{ fontWeight: 600, fontSize: "0.7rem" }}>
                      {formatFileSize(dlBytes)} / {formatFileSize(dlBlobSize)}
                      {isDownloading && ` (${dlBlobProgress.toFixed(0)}%)`}
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={dlBlobProgress}
                    color={isDownloading ? "info" : "success"}
                    sx={{ height: 6, borderRadius: 3 }}
                  />
                </Box>
              )}

              {/* Overall download bar */}
              {dlTotal > 0 && (
                <Box>
                  <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 0.3 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                      Download total
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                      {formatFileSize(dlCumulative)} / {formatFileSize(dlTotal)} ({dlTotalProgress.toFixed(0)}%)
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={dlTotalProgress}
                    color="secondary"
                    sx={{ height: 4, borderRadius: 2 }}
                  />
                </Box>
              )}

                  {task.active_downloads !== undefined && task.active_downloads > 0 && (
                    <Typography variant="caption" color="info.main" sx={{ display: "block", mt: 0.5 }}>
                  {task.active_downloads}/2 downloads simultâneos ativos
                    </Typography>
                  )}

              {task.phase && (
                <Typography variant="caption" color="secondary.main" sx={{ display: "block", mt: 0.5 }}>
                  Fase: {task.phase}
                </Typography>
              )}

              <Box sx={{ mt: 1, p: 1, bgcolor: "#f7f9fc", borderRadius: 1, border: "1px solid #dbe3ef" }}>
                <Typography variant="caption" fontWeight={700} sx={{ display: "block" }}>
                  {isPtuTask ? "Coleta operacional Azure PTU" : "Importação para Iceberg"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  {isPtuTask
                    ? "Sequência: descoberta ARM → Azure Monitor (janelas de até 30 dias) → persistência horária em dest_azure_ptu_metrics."
                    : "O download concluído não significa que a importação terminou. A sequência é: Leitura CSV → Bronze → Silver/Gold → concluído."}
                </Typography>
                <Typography variant="caption" color="primary.main" sx={{ display: "block", mt: 0.25 }}>
                  {task.phase || (isPtuTask ? "Aguardando início da coleta PTU" : "Aguardando início da importação")}
                </Typography>
              </Box>

              {task.download_blobs && task.download_blobs.length > 0 && (
                <Box sx={{ mt: 1, display: "grid", gap: 0.5 }}>
                  {task.download_blobs.map((blob) => {
                    const pct = blob.size > 0 ? Math.min(100, blob.downloaded / blob.size * 100) : 0;
                    return (
                      <Box key={blob.blob_path}>
                        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                          <Typography variant="caption" sx={{ fontFamily: "monospace", fontSize: "0.65rem" }}>
                            {blob.status} — {blob.blob_path.split("/").pop()}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {formatFileSize(blob.downloaded)} / {formatFileSize(blob.size)} ({pct.toFixed(0)}%)
                          </Typography>
                        </Box>
                        <LinearProgress variant="determinate" value={pct} sx={{ height: 3, borderRadius: 2 }} />
                      </Box>
                    );
                  })}
                </Box>
              )}

              {/* Per-blob progress from DB fallback (no download bytes, but blob count is tracked) */}
              {task.current_blob && dlBlobSize === 0 && task.total_blobs > 0 && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  blob {task.processed_blobs}/{task.total_blobs}: {task.current_blob}
                </Typography>
              )}
            </Box>
          )}

          {/* Info text for QUEUED tasks */}
          {isQueued && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              Tarefa criada e aguardando o worker iniciar o processamento
            </Typography>
          )}

          {isTerminal && task.status === "COMPLETED_WITH_ERRORS" && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              <strong>Bronze concluído, Silver não gerado.</strong>{" "}
              {task.phase || task.errors?.[0] || "Verifique o relacionamento origem → destino."}
            </Alert>
          )}
          {isTerminal && task.status === "COMPLETED" && (task.rows_raw ?? 0) > 0 && (task.rows_normalized ?? 0) === 0 && (
            <Alert severity="success" sx={{ mt: 1 }}>
              <strong>Load Bronze concluído.</strong> A origem foi carregada sem mapping ou transformação. Silver/Gold serão executados posteriormente.
            </Alert>
          )}

          {/* Current blob info when no download data available */}
          {task.current_blob && dlBlobSize === 0 && !isQueued && !isRunning && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {task.current_blob}
            </Typography>
          )}
        </Box>
      )}

      {scanResult && (
        <Box>
          <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap", alignItems: "center" }}>
            <Chip label={`Total: ${scanResult.total}`} />
            <Chip label={`Novos: ${scanResult.new}`} color="success" />
            <Chip label={`Alterados: ${scanResult.changed}`} color="warning" />
            <Chip label={`Inalterados: ${scanResult.unchanged}`} />
            {skippedBlobs.length > 0 && (
              <Chip label={`Ignorados: ${skippedBlobs.length}`} color="default" variant="outlined" />
            )}
            <Typography variant="caption" color="text.secondary" sx={{ width: "100%" }}>
              {filteredScanItems.length} resultado(s) filtrado(s) · {filteredEligibleItems.length} disponível(is) para importar/ACK
            </Typography>
            <Button size="small" variant="outlined" onClick={toggleAll} disabled={!filteredEligibleItems.length || isActive}>
              {filteredEligibleItems.length > 0 && filteredEligibleItems.every(i => selectedPaths.has(i.blob_path))
                ? "Desmarcar filtrados" : `Selecionar todos filtrados (${filteredEligibleItems.length})`}
            </Button>
            <Button size="small" variant="contained" color="success" startIcon={<PlayArrow />}
              onClick={doImportFiltered} disabled={!filteredEligibleItems.length || isActive}>
              Importar filtrados ({filteredEligibleItems.length})
            </Button>
            <Button size="small" variant="outlined" color="warning" startIcon={<Block />}
              onClick={() => doAckPaths(filteredEligibleItems.map(item => item.blob_path))}
              disabled={!filteredEligibleItems.length || isActive}>
              ACK filtrados ({filteredEligibleItems.length})
            </Button>
            {skippedBlobs.length > 0 && (
              <Button size="small" variant="text" color="error" onClick={doClearAllAcks} disabled={isActive}>
                Remover todos os ACKs ({skippedBlobs.length})
              </Button>
            )}
          </Box>
          <Box sx={{ height: 520, width: "100%", mb: 2 }}>
            <TextField
              size="small"
              value={blobFilter}
              onChange={event => setBlobFilter(event.target.value)}
              placeholder="Buscar nos dados escaneados..."
              sx={{ mb: 1, minWidth: 320 }}
            />
            <DataGrid
              rows={filteredScanItems.map((item, index) => ({ ...item, id: item.blob_path || index }))}
              columns={[
                { field: "_check", headerName: "", width: 52, sortable: false, filterable: false, renderHeader: () => { const allSelected = filteredEligibleItems.length > 0 && filteredEligibleItems.every(item => selectedPaths.has(item.blob_path)); const someSelected = filteredEligibleItems.some(item => selectedPaths.has(item.blob_path)); return <Tooltip title={allSelected ? "Desmarcar itens filtrados" : "Selecionar todos os itens filtrados"}><Checkbox size="small" checked={allSelected} indeterminate={!allSelected && someSelected} disabled={isActive || filteredEligibleItems.length === 0} onClick={event => event.stopPropagation()} onChange={toggleAll} /></Tooltip>; }, renderCell: ({ row }) => <Checkbox size="small" checked={selectedPaths.has(row.blob_path)} disabled={isActive || row.status === "SKIPPED" || skippedPaths.has(row.blob_path)} onClick={e => e.stopPropagation()} onChange={() => toggleSelect(row.blob_path)} /> },
                { field: "status", headerName: "Status", width: 120, renderCell: ({ value }) => <Chip size="small" label={value} color={STATUS_COLORS[String(value)] || "default"} /> },
                { field: "blob_path", headerName: "Blob", minWidth: 300, flex: 1 },
                { field: "billing_period", headerName: "Período", width: 120 },
                { field: "last_modified", headerName: "Modificado", width: 150, type: "dateTime", valueGetter: (_, row) => row.last_modified ? new Date(String(row.last_modified)) : null },
                { field: "content_length", headerName: "Linhas / tamanho", width: 150, valueGetter: (_, row) => row.row_count != null ? Number(row.row_count).toLocaleString() : formatFileSize(Number(row.content_length || 0)) },
                { field: "_skip", headerName: "Ações", width: 90, sortable: false, filterable: false, renderCell: ({ row }) => <Tooltip title={skippedPaths.has(row.blob_path) ? "Desfazer ACK" : "Ignorar blob"}><IconButton size="small" color={skippedPaths.has(row.blob_path) ? "success" : "warning"} disabled={isActive || skippingPaths.has(row.blob_path)} onClick={event => { event.stopPropagation(); const entry = skippedBlobs.find(item => item.blob_path === row.blob_path); if (entry) void doUnskipBlob(entry.id, row.blob_path); else void doSkipBlob(row.blob_path); }}>{skippedPaths.has(row.blob_path) ? <Undo fontSize="small" /> : <Block fontSize="small" />}</IconButton></Tooltip> },
              ] as GridColDef[]}
              loading={scanning}
              disableRowSelectionOnClick
              pageSizeOptions={[100, 250, 500]}
              initialState={{ pagination: { paginationModel: { pageSize: 100, page: 0 } } }}
              sx={{ border: 1, borderColor: "divider", borderRadius: 2, bgcolor: "background.paper", "& .MuiDataGrid-columnHeaders": { bgcolor: "#f8fafc" } }}
            />
          </Box>
          <Box sx={{ display: "none" }}>
          <SortableTable
            data={filteredScanItems}
            searchValue={blobFilter}
            onSearchChange={setBlobFilter}
            placeholder="Buscar blobs: 4p, 202505, part_1..."
            getRowKey={(item) => item.blob_path}
            defaultSortKey="billing_period"
            defaultSortDir="desc"
            columns={[
              {
                key: "_check", label: "", width: 48, sortable: false,
                headerRender: () => {
                  const allFilteredSelected = filteredEligibleItems.length > 0 &&
                    filteredEligibleItems.every(item => selectedPaths.has(item.blob_path));
                  const someFilteredSelected = filteredEligibleItems.some(item => selectedPaths.has(item.blob_path));
                  return (
                    <Tooltip title={allFilteredSelected ? "Desmarcar todos os filtrados" : "Selecionar todos os filtrados"}>
                      <Checkbox
                        size="small"
                        checked={allFilteredSelected}
                        indeterminate={!allFilteredSelected && someFilteredSelected}
                        onChange={toggleAll}
                        disabled={!filteredEligibleItems.length || isActive}
                        onClick={e => e.stopPropagation()}
                      />
                    </Tooltip>
                  );
                },
                render: (item: BlobScanItem) => {
                  const isSkipped = item.status === "SKIPPED" || skippedPaths.has(item.blob_path);
                  return (
                    <Checkbox
                      checked={selectedPaths.has(item.blob_path)}
                      onChange={() => toggleSelect(item.blob_path)}
                      disabled={isActive || isSkipped}
                      size="small"
                      sx={isSkipped ? { color: "#bbb" } : undefined}
                    />
                  );
                },
              },
              {
                key: "status", label: "Status", width: 100,
                render: (item: BlobScanItem) => {
                  const isSkipped = item.status === "SKIPPED" || skippedPaths.has(item.blob_path);
                  const displayStatus = isSkipped ? "SKIPPED" : item.status;
                  return (
                    <Chip
                      label={displayStatus}
                      size="small"
                      color={STATUS_COLORS[displayStatus] || "default"}
                      sx={isSkipped ? { opacity: 0.5 } : undefined}
                    />
                  );
                },
              },
              {
                key: "blob_path", label: "Blob",
                render: (item: BlobScanItem) => {
                  const isSkipped = item.status === "SKIPPED" || skippedPaths.has(item.blob_path);
                  return (
                    <Typography
                      variant="body2"
                      sx={isSkipped ? { color: "#aaa", textDecoration: "line-through", fontStyle: "italic" } : undefined}
                    >
                      {item.blob_path}
                    </Typography>
                  );
                },
              },
              { key: "billing_period", label: "Periodo", width: 100 },
              {
                key: "last_modified", label: "Modificado", width: 110,
                render: (item: BlobScanItem) => new Date(item.last_modified).toLocaleDateString(),
              },
              {
                key: "content_length", label: "Linhas / Tamanho", width: 110,
                render: (item: BlobScanItem) => (
                  item.row_count != null
                    ? item.row_count.toLocaleString()
                    : formatFileSize(item.content_length || 0)
                ),
              },
              {
                key: "_skip", label: "", width: 48, sortable: false,
                render: (item: BlobScanItem) => {
                  const isSkipped = item.status === "SKIPPED" || skippedPaths.has(item.blob_path);
                  const skipEntry = skippedBlobs.find(s => s.blob_path === item.blob_path);
                  const isSkipping = skippingPaths.has(item.blob_path);

                  if (isSkipping) {
                    return (
                      <Box sx={{ display: "flex", justifyContent: "center", width: "100%" }}>
                        <CircularProgress size={18} />
                      </Box>
                    );
                  }
                  if (isSkipped) {
                    return (
                      <Tooltip title="Desfazer — voltar a permitir importação">
                        <button
                          onClick={(e) => { e.stopPropagation(); if (skipEntry) doUnskipBlob(skipEntry.id, item.blob_path); }}
                          style={{ background: "none", border: "none", cursor: "pointer", color: "#2e7d32", padding: 4 }}
                        >
                          <Undo sx={{ fontSize: 18 }} />
                        </button>
                      </Tooltip>
                    );
                  }
                  return (
                    <Tooltip title="Ignorar — nunca importar este blob">
                      <button
                        onClick={(e) => { e.stopPropagation(); doSkipBlob(item.blob_path); }}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "#ed6c02", padding: 4 }}
                      >
                        <Block sx={{ fontSize: 18 }} />
                      </button>
                    </Tooltip>
                  );
                },
              },
            ]}
          />
          </Box>
        </Box>
      )}

      {!scanResult && !task && !scanning && connId && (
        <Alert severity="info">Clique em "Escanear" para listar os blobs disponiveis.</Alert>
      )}

      {!connId && <EmptyState message="Selecione uma conexao para visualizar os blobs e executar o ETL." />}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/*  AGENDAMENTOS                                                */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {connId && (
        <Box sx={{ mt: 4 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
            <Typography variant="h5" sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Schedule /> Agendamentos
            </Typography>
            <Button size="small" variant="outlined" startIcon={<Add />} onClick={openNewSched}>
              Novo Agendamento
            </Button>
          </Box>

          {schedules.length === 0 ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              Nenhum agendamento configurado. Clique em "Novo Agendamento" para automatizar a extração e o reprocessamento da tabela destino.
            </Alert>
          ) : (
            <SortableTable
              data={schedules}
              getRowKey={(s) => s.id}
              defaultSortKey="created_at"
              defaultSortDir="desc"
              columns={[
                {
                  key: "name", label: "Nome",
                  render: (s: ScheduledTask) => (
                    <Box>
                      <Typography variant="body2" fontWeight={500}>{s.name}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                        {s.cron_expression}
                      </Typography>
                    </Box>
                  ),
                },
                {
                  key: "connection_id", label: "Conexao", width: 150,
                  render: (s: ScheduledTask) => {
                    const c = connections.find(c => c.id === s.connection_id);
                    return c?.code || `ID ${s.connection_id}`;
                  },
                },
                {
                  key: "_frequency", label: "Frequencia", width: 130,
                  render: (s: ScheduledTask) => {
                    const preset = cronPresets.find(p => p.value === s.cron_expression);
                    return preset?.label || s.cron_expression;
                  },
                },
                {
                  key: "next_run_at", label: "Proxima", width: 130,
                  render: (s: ScheduledTask) => (
                    <Chip
                      label={formatNextRun(s)}
                      size="small"
                      color={s.active ? "primary" : "default"}
                      variant={s.active ? "filled" : "outlined"}
                    />
                  ),
                },
                {
                  key: "last_run_at", label: "Ultima", width: 130,
                  render: (s: ScheduledTask) => formatLastRun(s),
                },
                {
                  key: "active", label: "Status", width: 80,
                  render: (s: ScheduledTask) => (
                    <Chip
                      label={s.active ? "Ativo" : "Inativo"}
                      size="small"
                      color={s.active ? "success" : "default"}
                    />
                  ),
                },
                {
                  key: "destination_reprocess", label: "Reprocessar", width: 110,
                  render: (s: ScheduledTask) => (
                    <Chip
                      label={s.destination_reprocess ? "Sim" : "Nao"}
                      size="small"
                      variant="outlined"
                      color={s.destination_reprocess ? "info" : "default"}
                    />
                  ),
                },
                {
                  key: "_actions", label: "Acoes", width: 140, sortable: false,
                  render: (s: ScheduledTask) => (
                    <Box sx={{ display: "flex", gap: 0.5 }}>
                      <Tooltip title="Executar agora">
                        <IconButton
                          size="small" color="primary"
                          onClick={() => runSchedNow(s.id)}
                          disabled={schedRunning.has(s.id)}
                        >
                          {schedRunning.has(s.id) ? <CircularProgress size={16} /> : <PlayArrow fontSize="small" />}
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Editar">
                        <IconButton size="small" onClick={() => openEditSched(s)}>
                          <Edit fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Remover">
                        <IconButton size="small" color="error" onClick={() => deleteSched(s.id)}>
                          <Delete fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  ),
                },
              ]}
            />
          )}
        </Box>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/*  SCHEDULE DIALOG                                              */}
      {/* ═══════════════════════════════════════════════════════════ */}
      <Dialog open={schedDialogOpen} onClose={() => setSchedDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{schedEditing ? "Editar" : "Novo"} Agendamento</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome" value={schedForm.name}
              onChange={e => setSchedForm({ ...schedForm, name: e.target.value })} />
            <FormControl size="small">
              <InputLabel>Conexao</InputLabel>
              <Select value={schedForm.connection_id} label="Conexao"
                onChange={e => setSchedForm({ ...schedForm, connection_id: e.target.value as number })}>
                {connections.map(c => (
                  <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
                ))}
              </Select>
            </FormControl>

            <Typography variant="subtitle2" sx={{ mt: 1 }}>Expressao Cron</Typography>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
              {cronPresets.map(p => (
                <Chip
                  key={p.value}
                  label={p.label}
                  size="small"
                  variant={schedForm.cron_expression === p.value ? "filled" : "outlined"}
                  color={schedForm.cron_expression === p.value ? "primary" : "default"}
                  onClick={() => setSchedForm({ ...schedForm, cron_expression: p.value })}
                  sx={{ cursor: "pointer" }}
                />
              ))}
            </Box>
            <TextField label="Expressao Cron (custom)" value={schedForm.cron_expression}
              onChange={e => setSchedForm({ ...schedForm, cron_expression: e.target.value })}
              helperText="Formato: minuto hora dia mes dia_semana. Ex: 0 3 * * * (diario as 3h)" />

            <FormControlLabel
              control={<Switch checked={schedForm.destination_reprocess}
                onChange={e => setSchedForm({ ...schedForm, destination_reprocess: e.target.checked })} />}
              label="Auto-reprocessar tabela destino apos extracao"
            />
            <FormControlLabel
              control={<Switch checked={schedForm.forecast_after_ingestion}
                onChange={e => setSchedForm({ ...schedForm, forecast_after_ingestion: e.target.checked })} />}
              label="Executar Forecast após ingestão e reprocessamento"
            />
            {schedForm.forecast_after_ingestion && <TextField label="ID da configuração Forecast" type="number" value={schedForm.forecast_config_id}
              onChange={e => setSchedForm({ ...schedForm, forecast_config_id: e.target.value })}
              helperText="Crie a configuração no menu Forecast e informe o ID exibido." />}
            <FormControlLabel
              control={<Switch checked={schedForm.active}
                onChange={e => setSchedForm({ ...schedForm, active: e.target.checked })} />}
              label="Ativo"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSchedDialogOpen(false)}>Cancelar</Button>
          <Button onClick={saveSched} variant="contained" disabled={!schedForm.name || !schedForm.connection_id}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>

      {blobs.length > 0 && (
        <Box sx={{ mt: 4 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
            <Typography variant="h6">Historico de Ingestao</Typography>
            <Button size="small" color="error" startIcon={<DeleteForever />} onClick={doDeleteAll}>
              Limpar Tudo
            </Button>
          </Box>
            <SortableTable
              data={blobs}
              getRowKey={(b) => b.id}
              defaultSortKey="ingested_at"
              defaultSortDir="desc"
              columns={[
                {
                  key: "status", label: "Status", width: 100,
                  render: (b: IngestedBlob) => (
                    <Chip label={b.status} size="small" color={STATUS_COLORS[b.status] || "default"} />
                  ),
                },
                { key: "billing_period", label: "Periodo", width: 80 },
                { key: "blob_path", label: "Blob" },
                {
                  key: "row_count", label: "Linhas", width: 90,
                  render: (b: IngestedBlob) => b.row_count?.toLocaleString() || "-",
                },
                {
                  key: "ingested_at", label: "Ingerido em", width: 160,
                  render: (b: IngestedBlob) => new Date(b.ingested_at).toLocaleString(),
                },
                {
                  key: "_actions", label: "Ações", width: 130, sortable: false,
                  render: (b: IngestedBlob) => (
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <Tooltip title="Download CSV (Origem)">
                        <span>
                          <IconButton
                            size="small"
                            color="primary"
                            disabled={downloadingId === `${b.id}-csv`}
                            onClick={() => handleDownloadBlob(b.id, "csv")}
                          >
                            {downloadingId === `${b.id}-csv` ? <CircularProgress size={16} /> : <Download sx={{ fontSize: 18 }} />}
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title="Download Excel (XLSX)">
                        <span>
                          <IconButton
                            size="small"
                            color="success"
                            disabled={downloadingId === `${b.id}-xlsx`}
                            onClick={() => handleDownloadBlob(b.id, "xlsx")}
                          >
                            {downloadingId === `${b.id}-xlsx` ? <CircularProgress size={16} /> : <TableChart sx={{ fontSize: 18 }} />}
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title="Remover dataset">
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => doDeleteBlob(b.id)}
                        >
                          <Delete sx={{ fontSize: 18 }} />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  ),
                },
              ]}
            />
        </Box>
      )}

      {uploadConn && (
        <ManualCsvUploadDialog
          open={!!uploadConn}
          onClose={() => setUploadConn(null)}
          connectionId={uploadConn.id}
          connectionName={uploadConn.name}
        />
      )}
        </>
      )}

      {(tab === "history" || embedded) && (
        <Box>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
            <Box>
              <Typography variant="h6" fontWeight={600}>Histórico de Execuções</Typography>
              <Typography variant="body2" color="text.secondary">
                Acompanhe o status e abra os dados reais importados na camada Bronze do Apache Iceberg
              </Typography>
            </Box>
            <Button
              variant="outlined"
              startIcon={loadingHistory ? <CircularProgress size={18} /> : <Refresh />}
              onClick={() => void loadHistory()}
              disabled={loadingHistory}
            >
              Atualizar Histórico
            </Button>
          </Stack>

          {loadingHistory && historyList.length === 0 ? (
            <Stack alignItems="center" py={8}>
              <CircularProgress />
              <Typography mt={2} color="text.secondary">Carregando histórico de extrações...</Typography>
            </Stack>
          ) : historyList.length === 0 ? (
            <EmptyState message="Nenhuma execução de extração registrada até o momento." />
          ) : (
            <>
            <OperationalHistoryDataGrid rows={historyList as unknown as Record<string, unknown>[]} columns={[
              { field: "id", headerName: "ID", width: 80 },
              { field: "status", headerName: "Status", width: 150, renderCell: ({ value }) => <Chip label={value} size="small" color={STATUS_COLORS[String(value)] || "default"} /> },
              { field: "connection_name", headerName: "Conexão", flex: 1, minWidth: 180 },
              { field: "processed_blobs", headerName: "Blobs", width: 120 }, { field: "rows_raw", headerName: "Linhas raw", width: 130 },
              { field: "started_at", headerName: "Início", type: "dateTime", minWidth: 170, valueGetter: (_, row) => row.started_at ? new Date(String(row.started_at)) : null },
              { field: "actions", headerName: "Ações", width: 90, sortable: false, filterable: false, renderCell: ({ row }) => <Tooltip title="Abrir dados"><IconButton onClick={() => navigate(`/extraction/history/${row.id}`)}><Visibility fontSize="small" /></IconButton></Tooltip> },
            ] as GridColDef[]} />
            <Box sx={{ display: "none" }}>
            <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
              <Table size="small">
                <TableHead sx={{ bgcolor: "grey.100" }}>
                  <TableRow>
                    <TableCell><strong>ID</strong></TableCell>
                    <TableCell><strong>Status</strong></TableCell>
                    <TableCell><strong>Conexão</strong></TableCell>
                    <TableCell><strong>Período(s)</strong></TableCell>
                    <TableCell align="right"><strong>Blobs</strong></TableCell>
                    <TableCell align="right"><strong>Linhas Raw</strong></TableCell>
                    <TableCell><strong>Iniciado em</strong></TableCell>
                    <TableCell><strong>Finalizado em</strong></TableCell>
                    <TableCell align="center"><strong>Ações</strong></TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {historyList.map((item) => {
                    const statusColor = STATUS_COLORS[item.status] || "default";
                    const isFinished = ["COMPLETED", "COMPLETED_WITH_ERRORS"].includes(item.status);
                    return (
                      <TableRow key={item.id} hover>
                        <TableCell sx={{ fontFamily: "monospace", fontWeight: 700 }}>#{item.id}</TableCell>
                        <TableCell>
                          <Chip label={item.status} size="small" color={statusColor} />
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" fontWeight={600}>
                            {item.connection_name || "—"}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {item.connection_code} ({item.connector_type})
                          </Typography>
                        </TableCell>
                        <TableCell>
                          {item.billing_periods && item.billing_periods.length > 0
                            ? item.billing_periods.join(", ")
                            : (item.result_json?.billing_periods as string[])?.join(", ") || "—"}
                        </TableCell>
                        <TableCell align="right">
                          {item.processed_blobs} / {item.total_blobs || "?"}
                        </TableCell>
                        <TableCell align="right">
                          <strong>{(item.rows_raw || 0).toLocaleString()}</strong>
                        </TableCell>
                        <TableCell>
                          {item.started_at ? new Date(item.started_at).toLocaleString("pt-BR") : "—"}
                        </TableCell>
                        <TableCell>
                          {item.finished_at ? new Date(item.finished_at).toLocaleString("pt-BR") : (item.phase || "Em andamento")}
                        </TableCell>
                        <TableCell align="center">
                          <Button
                            size="small"
                            variant="contained"
                            color="primary"
                            startIcon={<Visibility sx={{ fontSize: 16 }} />}
                            onClick={() => navigate(`/extraction/history/${item.id}`)}
                            disabled={!isFinished && (item.rows_raw || 0) === 0}
                          >
                            Abrir Dados
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
            </Box>
            </>
          )}
        </Box>
      )}
    </Box>
  );
}
