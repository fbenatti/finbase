import { Dialog, DialogTitle, DialogContent, DialogActions } from "../../components/FinbaseDialog";
import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect, useRef, useCallback, useMemo, type ReactNode } from "react";
import { Box, Typography, Button, TableCell, Paper, Chip, LinearProgress, Alert, Select, MenuItem, FormControl, InputLabel, Checkbox, FormHelperText, CircularProgress, Tooltip, TextField, FormControlLabel, Switch, IconButton, Stack, Table, TableHead, TableRow, TableBody, TableContainer } from "@mui/material";
import { PlayArrow, Refresh, Stop, Delete, DeleteForever, Block, Undo, Storage, Schedule, Add, Edit, CloudUpload, Download, TableChart, Visibility } from "../../components/FinbaseIcons";
import { GridColDef } from "@mui/x-data-grid";
import {
  getConnections, scanBlobs, runEtl, getTaskStatus, getIngestedBlobs, cancelEtl,
  resetSourceForReimport,
  deleteIngestedBlob, deleteAllIngestedBlobs, downloadIngestedBlob,
  getSkippedBlobs, skipBlob as apiSkipBlob, unskipBlob as apiUnskipBlob,
  clearSkippedBlobs,
  processDestinationTables,
  getAvailablePeriods, AvailablePeriod,
  getSourcePreview, SourcePreview,
  planInfrastructureMetricsCollection, collectInfrastructureMetrics, getInfrastructureMetricsTaskStatus,
  InfrastructureMetricsPlan, InfrastructureMetricsTaskStatus,
  SourceConnection, BlobScanResult, BlobScanItem, IngestedBlob, SkippedBlob, TaskStatus,
  SCANNABLE_CONNECTOR_TYPES, formatApiError,
} from "../../services/api";
import SortableTable from "../../components/SortableTable";
import EmptyState from "../../components/EmptyState";
import ManualCsvUploadDialog from "../Connections/ManualCsvUploadDialog";
import SchedulesPanel from "./SchedulesPanel";

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

function toLocalDateTimeInput(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

// Seção da barra de extração: rótulo e explicação à esquerda, ações à direita.
function ToolbarSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "220px 1fr" }, gap: { xs: 1, md: 2 }, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
      <Box>
        <Typography variant="subtitle2">{title}</Typography>
        <Typography variant="caption" color="text.secondary">{description}</Typography>
      </Box>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>{children}</Stack>
    </Box>
  );
}

export default function Extraction({ embedded = false }: { embedded?: boolean }) {
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

  const [uploadConn, setUploadConn] = useState<SourceConnection | null>(null);

  // ── Databricks preview (header + amostra) state ──
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<SourcePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [infraStart, setInfraStart] = useState(() => toLocalDateTimeInput(new Date(Date.now() - 60 * 60 * 1000)));
  const [infraEnd, setInfraEnd] = useState(() => toLocalDateTimeInput(new Date()));
  const [infraResolution, setInfraResolution] = useState<60 | 300 | 900>(60);
  const [infraPlan, setInfraPlan] = useState<InfrastructureMetricsPlan | null>(null);
  const [infraTaskStatus, setInfraTaskStatus] = useState<InfrastructureMetricsTaskStatus | null>(null);
  const [infraPlanning, setInfraPlanning] = useState(false);
  const [infraCollecting, setInfraCollecting] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
  const isDatabricksSelected = useMemo(
    () => extractableConnections.some((c) => c.id === connId && c.connector_type === "databricks"),
    [extractableConnections, connId],
  );
  const selectedConnection = useMemo(
    () => extractableConnections.find((connection) => connection.id === connId) || null,
    [extractableConnections, connId],
  );
  const infraConfig = (selectedConnection?.config_json?.infrastructure_metrics || {}) as Record<string, unknown>;
  const isInfraMetricsSelected = selectedConnection?.connector_type === "oci" && infraConfig.enabled === true;
  const enabledInfraResolutions = Array.isArray(infraConfig.resolutions_seconds)
    ? (infraConfig.resolutions_seconds as number[]).filter((value): value is 60 | 300 | 900 => [60, 300, 900].includes(value as number))
    : [];
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
    }).catch((e) => setError(formatApiError(e, "Não foi possível carregar as conexões")));
  }, [extractableConnectorTypes]);

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
    if (isAzurePtuSelected) {
      setError("");
      setScanResult({ connection_id: connId, total: 0, new: 0, changed: 0, unchanged: 0, items: [], persisted_metrics: false } as any);
      return;
    }
    setScanning(true); setError(""); setTask(null); setSelectedPaths(new Set()); setBlobFilter("");
    try { setScanResult(await scanBlobs(connId as number)); }
    catch (e: unknown) { setError(formatApiError(e, "Não foi possível varrer a fonte")); }
    finally { setScanning(false); }
  };

  const doPreview = async () => {
    if (!connId) return;
    setPreviewLoading(true);
    setPreviewError("");
    setPreviewOpen(true);
    setPreviewData(null);
    try {
      setPreviewData(await getSourcePreview(connId as number, 20));
    } catch (e: unknown) {
      setPreviewError(formatApiError(e, "Não foi possível obter a prévia do Databricks"));
    } finally {
      setPreviewLoading(false);
    }
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
            setError("Escolha ao menos uma competência para buscar métricas históricas.");
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
        setError(result.message || "Coleta não disponível para este tipo de conexão");
        return;
      }
      pollTask(result.task_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível iniciar a coleta");
    }
  };

  const doResetSource = async () => {
    if (!connId || !confirm("Isso removerá o histórico de ingestão e a base de origem desta fonte. Os CSVs do Azure não serão apagados. Continuar?")) return;
    try {
      await resetSourceForReimport(connId as number);
      setTask(null); setBlobs([]); setScanResult(null);
      setSourceResetMessage("Fonte reiniciada. Execute Varrer fonte e depois importe os CSVs atuais.");
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Não foi possível reiniciar a fonte"); }
  };

  const doCancelTask = async (taskId: number) => {
    setTask(prev => prev?.id === taskId ? { ...prev, status: "CANCELLING" } : prev);
    try {
      await cancelEtl(taskId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível cancelar");
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
        setDestProcessResult(`Erro: ${result.error || "Erro não identificado"}`);
      }
    } catch (e: unknown) {
      setDestProcessResult(`Erro: ${e instanceof Error ? e.message : "Não foi possível processar o destino"}`);
    } finally {
      setProcessingDest(false);
    }
  };

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

  const infrastructureMetricsRequest = () => {
    if (!connId || !infraStart || !infraEnd) throw new Error("Informe a conexão e o período da coleta.");
    const start = new Date(infraStart);
    const end = new Date(infraEnd);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start || end.getTime() - start.getTime() > 24 * 60 * 60 * 1000) {
      throw new Error("O período deve ser válido, crescente e ter no máximo 24 horas.");
    }
    return {
      connection_id: connId as number,
      start_utc: start.toISOString(),
      end_utc: end.toISOString(),
      resolution_seconds: infraResolution,
    };
  };

  const doPlanInfrastructureMetrics = async () => {
    setInfraPlanning(true);
    setError("");
    try {
      const plan = await planInfrastructureMetricsCollection(infrastructureMetricsRequest());
      setInfraPlan(plan);
      setInfraTaskStatus(null);
    } catch (e: unknown) {
      setInfraPlan(null);
      setError(formatApiError(e, "Não foi possível estimar a coleta de métricas"));
    } finally {
      setInfraPlanning(false);
    }
  };

  const doCollectInfrastructureMetrics = async () => {
    setInfraCollecting(true);
    setError("");
    try {
      const result = await collectInfrastructureMetrics(infrastructureMetricsRequest());
      const initial: InfrastructureMetricsTaskStatus = {
        task_id: result.task_id, status: result.status, phase: "Na fila para coletar e publicar na Silver",
        rows_raw: 0, rows_destination: 0, errors: [], summary: {}, started_at: null, finished_at: null,
      };
      setInfraPlan(result.plan);
      setInfraTaskStatus(initial);
      setTask({
        id: result.task_id, status: result.status, total_blobs: 0, processed_blobs: 0,
        rows_destination: 0, current_blob: null, task_type: "infrastructure_metrics_collection",
        phase: initial.phase || "",
      });
      stopPolling();
      const fetchMetricsStatus = async () => {
        try {
          const status = await getInfrastructureMetricsTaskStatus(result.task_id);
          setInfraTaskStatus(status);
          setTask((previous) => previous ? {
            ...previous, status: status.status, phase: status.phase || "", rows_destination: status.rows_destination,
          } : previous);
          if (["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].includes(status.status)) stopPolling();
        } catch (e: unknown) {
          setError(formatApiError(e, "Não foi possível consultar o status da coleta de métricas"));
          stopPolling();
        }
      };
      void fetchMetricsStatus();
      pollRef.current = setInterval(fetchMetricsStatus, 2000);
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível iniciar a coleta de métricas"));
    } finally {
      setInfraCollecting(false);
    }
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
        setError(result.message || "Coleta não disponível para esta conexão");
        return;
      }
      setSelectedPaths(new Set(paths));
      pollTask(result.task_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível iniciar a carga");
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
        await Promise.all(paths.slice(index, index + 10).map(path => apiSkipBlob(connId as number, path, "Reconhecimento em massa")));
      }
      setSelectedPaths(new Set());
      await loadSkipped();
      await doScan();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível marcar os arquivos como ignorados");
    }
  };

  const doClearAllAcks = async () => {
    if (!connId || skippedBlobs.length === 0 || isActive) return;
    if (!confirm(`Remover todos os ${skippedBlobs.length} reconhecimentos (ACK) desta conexão?`)) return;
    try {
      await clearSkippedBlobs(connId as number);
      setSelectedPaths(new Set());
      await loadSkipped();
      await doScan();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível remover os reconhecimentos");
    }
  };

  const doDeleteBlob = async (blobId: number) => {
    if (!confirm("Remover este dataset da lista de carga?")) return;
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
      if (status !== 404) setError(e instanceof Error ? e.message : "Não foi possível excluir");
    }
  };

  const handleDownloadBlob = async (blobId: number, format: "csv" | "xlsx") => {
    const key = `${blobId}-${format}`;
    setDownloadingId(key);
    try {
      await downloadIngestedBlob(blobId, format);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Não foi possível baixar o arquivo");
    } finally {
      setDownloadingId(null);
    }
  };


  const doDeleteAll = async () => {
    if (!connId) return;
    if (!confirm("Remover TODOS os datasets carregados desta conexão? Esta ação não pode ser desfeita.")) return;
    try {
      await deleteAllIngestedBlobs(connId as number);
      setBlobs([]);
    } catch (e: unknown) {setError(e instanceof Error ? e.message : "Não foi possível excluir todos");}
  };

  const doSkipBlob = async (blobPath: string) => {
    if (!connId) return;
    setSkippingPaths(prev => new Set(prev).add(blobPath));
    try {
      await apiSkipBlob(connId as number, blobPath);
      loadSkipped();
      doScan();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Não foi possível ignorar"); }
    finally { setSkippingPaths(prev => { const next = new Set(prev); next.delete(blobPath); return next; }); }
  };

  const doUnskipBlob = async (skipId: number, blobPath: string) => {
    setSkippingPaths(prev => new Set(prev).add(blobPath));
    try {
      await apiUnskipBlob(skipId);
      loadSkipped();
      doScan();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Não foi possível reverter o ignorado"); }
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
      {!embedded && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6">Coletas</Typography>
          <Typography variant="body2" color="text.secondary">Varra a fonte, importe os arquivos para a base de origem e processe para a tabela de destino.</Typography>
        </Box>
      )}

      <>
          <Paper variant="outlined" sx={{ px: 2, pt: 2, pb: 0.5, mb: 3 }}>
            <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }} justifyContent="space-between">
              <FormControl size="small" sx={{ minWidth: 280 }}>
                <InputLabel>Conexão</InputLabel>
                <Select value={connId} label="Conexão de origem" onChange={(e) => { setConnId(e.target.value as number); setScanResult(null); setTask(null); setInfraPlan(null); setInfraTaskStatus(null); }}>
                  {extractableConnections.map((c) => (
                    <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
                  ))}
                </Select>
              </FormControl>
              {isActive && (
                <Button
                  variant="outlined"
                  color="error"
                  disabled={isCancelling}
                  startIcon={isCancelling ? <CircularProgress size={18} /> : <Stop />}
                  onClick={() => doCancelTask(task!.id)}
                >
                  {isCancelling ? "Interrompendo..." : "Parar execução"}
                </Button>
              )}
            </Stack>
            {connections.length > 0 && extractableConnections.length === 0 && (
              <Alert severity="warning" sx={{ mt: 2 }}>Nenhuma conexão disponível para coleta.</Alert>
            )}

            {selectedConnection?.connector_type === "oci" && (
              <ToolbarSection
                title="Métricas de infraestrutura"
                description="Consulta a OCI e publica os buckets coletados na base preparada (Silver)."
              >
                {!isInfraMetricsSelected ? (
                  <Alert severity="info" sx={{ width: "100%" }}>
                    Habilite <strong>Coletar recursos de infraestrutura</strong> ao editar esta conexão OCI. Informe o compartimento e até duas instâncias permitidas; a tela da conexão mostra a policy mínima e os detalhes da coleta.
                  </Alert>
                ) : (
                  <>
                    <TextField size="small" type="datetime-local" label="Início" value={infraStart} onChange={(event) => { setInfraStart(event.target.value); setInfraPlan(null); }} InputLabelProps={{ shrink: true }} sx={{ minWidth: 220 }} />
                    <TextField size="small" type="datetime-local" label="Fim" value={infraEnd} onChange={(event) => { setInfraEnd(event.target.value); setInfraPlan(null); }} InputLabelProps={{ shrink: true }} sx={{ minWidth: 220 }} />
                    <FormControl size="small" sx={{ minWidth: 145 }}>
                      <InputLabel>Resolução</InputLabel>
                      <Select label="Resolução" value={infraResolution} onChange={(event) => { setInfraResolution(Number(event.target.value) as 60 | 300 | 900); setInfraPlan(null); }}>
                        {[60, 300, 900].filter((value) => enabledInfraResolutions.includes(value as 60 | 300 | 900)).map((seconds) => <MenuItem key={seconds} value={seconds}>{seconds / 60} minuto(s)</MenuItem>)}
                      </Select>
                    </FormControl>
                    <Button variant="outlined" startIcon={infraPlanning ? <CircularProgress size={18} /> : <Refresh />} onClick={() => void doPlanInfrastructureMetrics()} disabled={infraPlanning || infraCollecting || isActive}>
                      {infraPlanning ? "Estimando..." : "Estimar consultas"}
                    </Button>
                    <Button variant="contained" startIcon={infraCollecting ? <CircularProgress size={18} /> : <PlayArrow />} onClick={() => void doCollectInfrastructureMetrics()} disabled={!infraPlan || infraPlanning || infraCollecting || isActive}>
                      {infraCollecting ? "Enfileirando..." : "Coletar e gravar na Silver"}
                    </Button>
                    {infraPlan && <Alert severity="info" sx={{ width: "100%" }}>
                      Plano: {infraPlan.resource_count} recurso(s), {infraPlan.metric_names.length} métricas, {infraPlan.statistics.join(", ")} por bucket de {infraPlan.resolution_seconds / 60} minuto(s), até {infraPlan.estimated_api_calls} chamadas OCI. Janela em UTC; retenção planejada: {infraPlan.retention_days} dias.
                    </Alert>}
                    {infraTaskStatus && <Alert severity={infraTaskStatus.status === "FAILED" ? "error" : infraTaskStatus.status === "COMPLETED_WITH_ERRORS" ? "warning" : ["COMPLETED", "CANCELLED"].includes(infraTaskStatus.status) ? "success" : "info"} sx={{ width: "100%" }}>
                      Tarefa #{infraTaskStatus.task_id}: {infraTaskStatus.status} — {infraTaskStatus.phase || ""}. Consultas concluídas: {Number(infraTaskStatus.summary.completed_query_count ?? infraTaskStatus.rows_raw)}; falhas: {Number(infraTaskStatus.summary.failed_query_count ?? infraTaskStatus.errors.length)}; linhas publicadas na Silver: {infraTaskStatus.rows_destination}.
                    </Alert>}
                  </>
                )}
              </ToolbarSection>
            )}

        {isManualCsvSelected ? (
          <ToolbarSection title="Carga manual" description="Envie um CSV diretamente para a base de origem.">
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
          </ToolbarSection>
        ) : (
          <ToolbarSection
            title="Carga manual"
            description={isAzurePtuSelected ? "Métricas do PTU: coleta recente ou por competência." : "Varra a fonte e importe os arquivos para a base de origem."}
          >
            {!isAzurePtuSelected && (
              <Button variant="outlined" startIcon={scanning ? <CircularProgress size={18} /> : <Refresh />} onClick={doScan} disabled={scanning || !connId}>
                {scanning ? "Varrendo..." : "Escanear"}
              </Button>
            )}
            {isDatabricksSelected && (
              <Button variant="outlined" color="inherit" startIcon={previewLoading ? <CircularProgress size={18} /> : <Visibility />} onClick={() => void doPreview()} disabled={previewLoading || !connId}>
                {previewLoading ? "Carregando cabeçalho..." : "Ver cabeçalho / prévia"}
              </Button>
            )}
            <Button variant="contained" startIcon={<PlayArrow />} onClick={() => doRun("backfill")}
              disabled={isActive || !connId || !!task?.status?.includes("CANCELLING")}
              color="primary">
              {isAzurePtuSelected ? "Importar todo o histórico disponível (~93 dias)" : (hasSelection ? `Importar ${visibleSelectedCount} selecionados` : "Importar tudo")}
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
                  variant="outlined"
                  color="secondary"
                  startIcon={<PlayArrow />}
                  onClick={() => doRun("period")}
                  disabled={isActive || !connId || selPtuPeriods.length === 0 || loadingPtuPeriods}
                >
                  Buscar métricas do período
                </Button>
              </>
            )}
          </ToolbarSection>
        )}

        <ToolbarSection title="Publicação" description="Publica os dados normalizados na tabela de destino, usando os mapeamentos de relação.">
          <Tooltip title="Converte os dados normalizados para a tabela de destino usando os mapeamentos configurados em Relacionamento.">
            <Button
              variant="contained"
              color="secondary"
              startIcon={processingDest ? <CircularProgress size={18} /> : <Storage />}
              onClick={doProcessDest}
              disabled={isActive || !connId || processingDest}
            >
              {processingDest ? "Em processamento..." : "Processar destino"}
            </Button>
          </Tooltip>
        </ToolbarSection>

        {!isManualCsvSelected && (
          <ToolbarSection title="Limpeza" description="Apaga os dados carregados desta conexão para reimportar do zero.">
            <Button variant="outlined" color="warning" startIcon={<DeleteForever />} onClick={doResetSource}
              disabled={isActive || !connId}>
              Reprocessar origem limpa
            </Button>
          </ToolbarSection>
        )}
      </Paper>

      {destProcessResult && (
        <Alert severity={destProcessResult.startsWith("OK") ? "success" : "error"} sx={{ mb: 2 }} onClose={() => setDestProcessResult("")}>
          {destProcessResult}
        </Alert>
      )}

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {sourceResetMessage && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSourceResetMessage("")}>{dataDisplayText(sourceResetMessage)}</Alert>}


      {extractableConnections.find(c => c.id === connId)?.connector_type === "oci" && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <strong>Consolidação automática OCI:</strong> Os dados da Oracle Cloud são agrupados e consolidados por dia e recurso para garantir alta performance e tabelas otimizadas, preservando 100% dos valores financeiros, quantidades e tags de negócio.
        </Alert>
      )}

      {scanning && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 2, my: 4, p: 3, bgcolor: "#f5f5f5", borderRadius: 2 }}>
          <CircularProgress size={28} />
          <Box>
            <Typography variant="body1" fontWeight={500}>
              {isAzurePtuSelected ? "Localizando deployments PTU..." : "Varrendo arquivos..."}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {isAzurePtuSelected
                ? "Consulta ao ARM Resource Graph e deployments com SKU provisionado"
                : "Buscando arquivos disponíveis na fonte de dados"}
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
                "Aguardando processamento..."
              ) : isPtuTask ? (
                <>
                  {task.total_blobs > 0
                    ? `${task.processed_blobs} / ${task.total_blobs} janelas Monitor`
                    : "Coleta ARM/Monitor em execução"}
                  {" | "}{(task.rows_destination ?? 0).toLocaleString()} registros consolidados
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
                  Fase: {dataDisplayText(task.phase)}
                </Typography>
              )}

              <Box sx={{ mt: 1, p: 1, bgcolor: "#f7f9fc", borderRadius: 1, border: "1px solid #E1E8F0" }}>
                <Typography variant="caption" fontWeight={700} sx={{ display: "block" }}>
                  {isPtuTask ? "Coleta operacional do Azure PTU" : "Carga de dados"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  {isPtuTask
                    ? "Sequência: descoberta ARM → Azure Monitor (janelas de até 30 dias) → gravação horária em dest_azure_ptu_metrics."
                    : "O download concluído não significa que a carga terminou. A sequência é: Leitura do arquivo → Dados de origem → Preparação e consolidação → Concluído."}
                </Typography>
                <Typography variant="caption" color="primary.main" sx={{ display: "block", mt: 0.25 }}>
                  {dataDisplayText(task.phase || (isPtuTask ? "Aguardando o início da coleta PTU" : "Aguardando início da carga"))}
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
                  blob {task.processed_blobs}/{task.total_blobs}: {dataDisplayText(task.current_blob)}
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
              <strong>Dados de origem carregados; preparo pendente.</strong>{" "}
              {dataDisplayText(task.phase || task.errors?.[0] || "Verifique a relação origem → destino.")}
            </Alert>
          )}
          {isTerminal && task.status === "COMPLETED" && (task.rows_raw ?? 0) > 0 && (task.rows_normalized ?? 0) === 0 && (
            <Alert severity="success" sx={{ mt: 1 }}>
              <strong>Carga da fonte concluída.</strong>  A origem foi carregada sem mapeamento ou transformação. A preparação e a consolidação serão executadas posteriormente.
            </Alert>
          )}

          {/* Current blob info when no download data available */}
          {task.current_blob && dlBlobSize === 0 && !isQueued && !isRunning && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {dataDisplayText(task.current_blob)}
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
                ? "Desmarcar os filtrados" : `Selecionar todos filtrados (${filteredEligibleItems.length})`}
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
              placeholder="Buscar nos arquivos varridos..."
              sx={{ mb: 1, minWidth: 320 }}
            />
            <DataGrid
              rows={filteredScanItems.map((item, index) => ({ ...item, id: item.blob_path || index }))}
              columns={[
                { field: "_check", headerName: "", width: 52, sortable: false, filterable: false, renderHeader: () => { const allSelected = filteredEligibleItems.length > 0 && filteredEligibleItems.every(item => selectedPaths.has(item.blob_path)); const someSelected = filteredEligibleItems.some(item => selectedPaths.has(item.blob_path)); return <Tooltip title={allSelected ? "Desmarcar itens filtrados" : "Marcar todos os itens filtrados"}><Checkbox size="small" checked={allSelected} indeterminate={!allSelected && someSelected} disabled={isActive || filteredEligibleItems.length === 0} onClick={event => event.stopPropagation()} onChange={toggleAll} /></Tooltip>; }, renderCell: ({ row }) => <Checkbox size="small" checked={selectedPaths.has(row.blob_path)} disabled={isActive || row.status === "SKIPPED" || skippedPaths.has(row.blob_path)} onClick={e => e.stopPropagation()} onChange={() => toggleSelect(row.blob_path)} /> },
                { field: "status", headerName: "Status", width: 120, renderCell: ({ value }) => <Chip size="small" label={value} color={STATUS_COLORS[String(value)] || "default"} /> },
                { field: "blob_path", headerName: "Blob", minWidth: 300, flex: 1 },
                { field: "billing_period", headerName: "Período", width: 120 },
                { field: "last_modified", headerName: "Modificado", width: 150, type: "dateTime", valueGetter: (_, row) => row.last_modified ? new Date(String(row.last_modified)) : null },
                { field: "content_length", headerName: "Registros / tamanho", width: 150, valueGetter: (_, row) => row.row_count != null ? Number(row.row_count).toLocaleString() : formatFileSize(Number(row.content_length || 0)) },
                { field: "_skip", headerName: "Ações", width: 90, sortable: false, filterable: false, renderCell: ({ row }) => <Tooltip title={skippedPaths.has(row.blob_path) ? "Desfazer aceite" : "Ignorar arquivo"}><IconButton size="small" color={skippedPaths.has(row.blob_path) ? "success" : "warning"} disabled={isActive || skippingPaths.has(row.blob_path)} onClick={event => { event.stopPropagation(); const entry = skippedBlobs.find(item => item.blob_path === row.blob_path); if (entry) void doUnskipBlob(entry.id, row.blob_path); else void doSkipBlob(row.blob_path); }}>{skippedPaths.has(row.blob_path) ? <Undo fontSize="small" /> : <Block fontSize="small" />}</IconButton></Tooltip> },
              ] as GridColDef[]}
              loading={scanning}
              disableRowSelectionOnClick
              pageSizeOptions={[50, 100, 500, 1000]}
              initialState={{ pagination: { paginationModel: { pageSize: 50, page: 0 } } }}
              sx={{ border: 1, borderColor: "divider", borderRadius: 2, bgcolor: "background.paper", "& .MuiDataGrid-columnHeaders": { bgcolor: "background.default" } }}
            />
          </Box>
          <Box sx={{ display: "none" }}>
          <SortableTable
            data={filteredScanItems}
            searchValue={blobFilter}
            onSearchChange={setBlobFilter}
            placeholder="Buscar arquivos: 202505, part_1..."
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
                    <Tooltip title={allFilteredSelected ? "Limpar seleção dos filtrados" : "Marcar todos os filtrados"}>
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
                key: "content_length", label: "Registros / tamanho", width: 110,
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
                      <Tooltip title="Desfazer — voltar a permitir a carga">
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
                    <Tooltip title="Ignorar — nunca carregar este arquivo">
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
        <Alert severity="info">Clique em "Escanear" para listar os arquivos disponíveis.</Alert>
      )}

      {!connId && <EmptyState message="Escolha uma conexão para visualizar os arquivos e executar a coleta." />}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/*  AGENDAMENTOS (painel dedicado, independente da conexão)       */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {!embedded && <SchedulesPanel connections={connections} />}

      {blobs.length > 0 && (
        <Box sx={{ mt: 4 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
            <Typography variant="h6">Histórico de carga</Typography>
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
                  key: "ingested_at", label: "Carregado em", width: 160,
                  render: (b: IngestedBlob) => new Date(b.ingested_at).toLocaleString(),
                },
                {
                  key: "_actions", label: "Ações", width: 130, sortable: false,
                  render: (b: IngestedBlob) => (
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <Tooltip title="Baixar CSV (origem)">
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
                      <Tooltip title="Baixar Excel (XLSX)">
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
                      <Tooltip title="Remover conjunto de dados">
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

      {/* ═══════════════════════════════════════════════════════════ */}
      {/*  PREVIEW DATABRICKS (header + amostra)                         */}
      {/* ═══════════════════════════════════════════════════════════ */}
      <Dialog open={previewOpen} onClose={() => setPreviewOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>Prévia do Databricks</DialogTitle>
        <DialogContent dividers sx={{ p: 0 }}>
          {previewLoading && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 2, p: 3 }}>
              <CircularProgress size={24} />
              <Typography variant="body2" color="text.secondary">Consultando o SQL Warehouse…</Typography>
            </Box>
          )}
          {!previewLoading && previewError && (
            <Alert severity="error" sx={{ m: 2 }} onClose={() => setPreviewError("")}>{dataDisplayText(previewError)}</Alert>
          )}
          {!previewLoading && previewData && (
            <Box sx={{ p: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Header ({previewData.columns.length} colunas) — amostra de {previewData.rows.length} linha(s). Use este schema para decidir a normalização/mapping (preparação e consolidação).
              </Typography>
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 2 }}>
                {previewData.columns.map((column) => (
                  <Chip key={column} size="small" variant="outlined" label={column} sx={{ fontFamily: "monospace" }} />
                ))}
              </Box>
              <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 420 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      {previewData.columns.map((column) => (
                        <TableCell key={column} sx={{ fontWeight: 700, fontFamily: "monospace", bgcolor: "#FAFBFD" }}>{column}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {previewData.rows.map((row, index) => (
                      <TableRow key={index} hover>
                        {previewData.columns.map((column) => {
                          const value = row[column];
                          return (
                            <TableCell key={column} sx={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {value === null || value === undefined ? "—" : String(value)}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    ))}
                    {previewData.rows.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={previewData.columns.length} align="center" sx={{ py: 4, color: "text.secondary" }}>
                          Nenhuma linha retornada pela query.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPreviewOpen(false)}>Fechar</Button>
        </DialogActions>
      </Dialog>
      </>

    </Box>
  );
}
