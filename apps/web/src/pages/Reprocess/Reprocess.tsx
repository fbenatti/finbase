import { useState, useEffect, useRef, useCallback } from "react";
import {
  Box, Typography, Button, Alert, Chip, CircularProgress,
  FormControl, InputLabel, Select, MenuItem, Checkbox, ListItemText,
  OutlinedInput, Paper, LinearProgress, Divider, FormControlLabel, Switch,
} from "@mui/material";
import { Refresh, Stop } from "@mui/icons-material";
import {
  getDestinationTables, DestinationTable,
  getConnectionsForDestination, MappedConnection,
  getAvailablePeriods, AvailablePeriod,
  reprocessDest, getReprocessStatus, cancelReprocess, ReprocessStatus,
  getRunningTasks, TaskStatus, getWorkerHealth, WorkerHealth, getReprocessHistory,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";

const ITEM_HEIGHT = 48;
const ITEM_PADDING_TOP = 8;
const MenuProps = {
  PaperProps: {
    style: {
      maxHeight: ITEM_HEIGHT * 4.5 + ITEM_PADDING_TOP,
      width: 320,
    },
  },
};

export default function Reprocess() {
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [mappedConns, setMappedConns] = useState<MappedConnection[]>([]);
  const [selDestId, setSelDestId] = useState<number | "">("");
  const [selConnIds, setSelConnIds] = useState<number[]>([]);
  const [selPeriods, setSelPeriods] = useState<string[]>([]);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [periods, setPeriods] = useState<AvailablePeriod[]>([]);
  const [processing, setProcessing] = useState(false);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<ReprocessStatus | null>(null);
  const [result, setResult] = useState<{
    type: "success" | "error"; message: string;
  } | null>(null);
  const [loadingPeriods, setLoadingPeriods] = useState(false);
  const [loadingConns, setLoadingConns] = useState(false);
  const [workerHealth, setWorkerHealth] = useState<WorkerHealth | null>(null);
  const [history, setHistory] = useState<ReprocessStatus[]>([]);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getDestinationTables(true).then(setDestTables).catch(() => {});
    getReprocessHistory().then(setHistory).catch(() => {});
    const refreshWorker = () => getWorkerHealth().then(setWorkerHealth).catch(() => setWorkerHealth({ status: "offline", heartbeat_age_seconds: null }));
    refreshWorker();
    const healthTimer = setInterval(refreshWorker, 3000);
    return () => clearInterval(healthTimer);
  }, []);

  // When dest table changes: load only connections with mappings, reset selections
  useEffect(() => {
    if (!selDestId) {
      setMappedConns([]);
      setSelConnIds([]);
      setPeriods([]);
      setSelPeriods([]);
      return;
    }
    setLoadingConns(true);
    setSelConnIds([]);
    setPeriods([]);
    setSelPeriods([]);
    getConnectionsForDestination(selDestId as number)
      .then(conns => {
        setMappedConns(conns);
      })
      .catch(() => setMappedConns([]))
      .finally(() => setLoadingConns(false));
  }, [selDestId]);

  // Load periods when connection selection changes
  useEffect(() => {
    if (selConnIds.length === 0) { setPeriods([]); setSelPeriods([]); return; }
    setLoadingPeriods(true);
    Promise.all(selConnIds.map(id => getAvailablePeriods(id).catch(() => [] as AvailablePeriod[])))
      .then(results => {
        const merged = new Map<string, number>();
        for (const r of results) {
          for (const p of r) {
            merged.set(p.period, (merged.get(p.period) || 0) + p.blob_count);
          }
        }
        const sorted = Array.from(merged.entries())
          .map(([period, blob_count]) => ({ period, blob_count } as AvailablePeriod))
          .sort((a, b) => b.period.localeCompare(a.period));
        setPeriods(sorted);
        setSelPeriods(sorted.map(p => p.period));
      })
      .finally(() => setLoadingPeriods(false));
  }, [selConnIds]);

  // Poll task status — finds running reprocess tasks globally
  const pollTask = useCallback((tid: number) => {
    pollRef.current = setInterval(async () => {
      try {
        const s = await getReprocessStatus(tid);
        setTaskStatus(s);
        if (["COMPLETED", "FAILED", "CANCELLED"].includes(s.status)) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setProcessing(false);
          setCancelling(false);
          if (s.status === "COMPLETED") {
            setResult({
              type: "success",
              message: `${s.rows_written.toLocaleString()} linhas escritas em ${s.table}`,
            });
          } else if (s.status === "FAILED") {
            setResult({ type: "error", message: s.error || "Falha ao reprocessar" });
          } else if (s.status === "CANCELLED") {
            setResult({ type: "error", message: "Processamento cancelado." });
          }
          getReprocessHistory().then(setHistory).catch(() => {});
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setProcessing(false);
      }
    }, 1500);
  }, []);

  // On mount + when dest table changes: recover running tasks from global queue
  useEffect(() => {
    if (!selDestId) return;
    const check = async () => {
      try {
        const tasks = await getRunningTasks();
        const reprocessTask = tasks.find(
          (t: TaskStatus) =>
            t.type === "reprocess" &&
            t.destination_table_id === selDestId &&
            (t.status === "RUNNING" || t.status === "QUEUED")
        );
        if (reprocessTask) {
          setTaskId(reprocessTask.id);
          setProcessing(true);
          setResult(null);
          pollTask(reprocessTask.id);
        }
      } catch { /* ignore */ }
    };
    check();
    // Also set up periodic global check (every 5s) to catch tasks started elsewhere
    const interval = setInterval(check, 5000);
    return () => clearInterval(interval);
  }, [selDestId, pollTask]);

  // Cleanup poll on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const doReprocess = async () => {
    if (!selDestId || selConnIds.length === 0) return;
    setProcessing(true);
    setResult(null);
    setTaskId(null);
    setTaskStatus(null);
    try {
      const res = await reprocessDest({
        destination_table_id: selDestId as number,
        connection_ids: selConnIds,
        billing_periods: selPeriods,
        replace_existing: replaceExisting,
      });
      setTaskId(res.task_id);
      pollTask(res.task_id);
      getReprocessHistory().then(setHistory).catch(() => {});
    } catch (e: unknown) {
      setResult({
        type: "error",
        message: e instanceof Error ? e.message : "Erro de conexao",
      });
      setProcessing(false);
    }
  };

  const doCancel = async () => {
    if (!taskId) return;
    setCancelling(true);
    try {
      await cancelReprocess(taskId);
    } catch { /* ignore */ }
  };

  const selectedDestName = selDestId ? destTables.find(t => t.id === selDestId)?.name : null;

  const togglePeriod = (p: string) => {
    setSelPeriods(prev =>
      prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]
    );
  };
  const selectAllPeriods = () => setSelPeriods(periods.map(p => p.period));
  const clearPeriods = () => setSelPeriods([]);

  const totalBlobs = periods
    .filter(p => selPeriods.length === 0 || selPeriods.includes(p.period))
    .reduce((sum, p) => sum + p.blob_count, 0);

  const isRunning = taskStatus && (taskStatus.status === "RUNNING" || taskStatus.status === "QUEUED");
  const isQueued = taskStatus && taskStatus.status === "QUEUED";
  const [cancelling, setCancelling] = useState(false);
  const progress = taskStatus?.progress ?? 0;

  return (
    <Box>
      <Typography variant="h4" sx={{ mb: 3 }}>Reprocessar Dados</Typography>

      {workerHealth && workerHealth.status !== "ok" && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Worker ETL {workerHealth.status === "offline" ? "offline" : "sem heartbeat"}. O reprocessamento está bloqueado até o worker estar ativo.
        </Alert>
      )}

      <Box sx={{ display: "flex", gap: 2, mb: 3, flexWrap: "wrap", alignItems: "flex-start" }}>
        {/* ── Tabela Destino ── */}
        <FormControl sx={{ minWidth: 260 }} size="small">
          <InputLabel>Tabela Destino *</InputLabel>
          <Select
            value={selDestId}
            label="Tabela Destino *"
            onChange={(e) => setSelDestId(e.target.value as number | "")}
            disabled={!!isRunning}
          >
            {destTables.map((t) => (
              <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* ── Fontes de Origem (only those with mappings) ── */}
        <FormControl sx={{ minWidth: 320 }} size="small">
          <InputLabel>
            {loadingConns ? "Carregando..." : `Fontes de Origem (${selConnIds.length})`}
          </InputLabel>
          <Select
            multiple
            value={selConnIds}
            label={`Fontes de Origem (${selConnIds.length})`}
            onChange={(e) => setSelConnIds(e.target.value as number[])}
            input={<OutlinedInput label={`Fontes de Origem (${selConnIds.length})`} />}
            renderValue={(selected) => {
              const names = mappedConns
                .filter(c => (selected as number[]).includes(c.id))
                .map(c => c.code);
              return names.join(", ") || "Nenhuma";
            }}
            MenuProps={MenuProps}
            disabled={!selDestId || loadingConns || !!isRunning}
          >
            {mappedConns.length === 0 && selDestId && !loadingConns && (
              <MenuItem disabled><em>Nenhuma conexao mapeada</em></MenuItem>
            )}
            {mappedConns.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                <Checkbox checked={selConnIds.includes(c.id)} />
                <ListItemText
                  primary={`${c.name} (${c.code})`}
                  secondary={`${c.mapping_count} colunas mapeadas`}
                />
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* ── Meses ── */}
        <FormControl sx={{ minWidth: 280 }} size="small">
          <InputLabel>
            {loadingPeriods ? "Carregando..." : `Meses (${selPeriods.length})`}
          </InputLabel>
          <Select
            multiple
            value={selPeriods}
            label={`Meses (${selPeriods.length})`}
            onChange={() => {}}
            input={<OutlinedInput label={`Meses (${selPeriods.length})`} />}
            renderValue={(selected) => {
              const arr = selected as string[];
              if (arr.length === 0) return `Todos (${periods.length} meses)`;
              if (arr.length === periods.length) return `Todos (${arr.length} meses)`;
              return `${arr.length} de ${periods.length} meses`;
            }}
            readOnly
            disabled={periods.length === 0 || !!isRunning}
          />
        </FormControl>

        {!isRunning && (
          <>
            <Button
              variant="contained"
              color="primary"
              startIcon={<Refresh />}
              onClick={doReprocess}
            disabled={!selDestId || selConnIds.length === 0 || processing || workerHealth?.status !== "ok"}
            >
              Reprocessar
            </Button>
            <FormControlLabel
              control={<Switch checked={replaceExisting} onChange={(e) => setReplaceExisting(e.target.checked)} color="warning" />}
              label="Reconstruir tabela completa (substituir dados antigos)"
            />
          </>
        )}

        {isRunning && (
          <Button
            variant="outlined"
            color="error"
            disabled={cancelling}
            startIcon={cancelling ? <CircularProgress size={18} /> : <Stop />}
            onClick={doCancel}
          >
            {cancelling ? "Cancelando..." : "Cancelar"}
          </Button>
        )}
      </Box>

      {/* ── Seleção de meses ── */}
      {periods.length > 0 && !isRunning && (
        <Box sx={{ mb: 3, display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
          <Button size="small" variant="text" onClick={selectAllPeriods} sx={{ fontSize: "0.7rem" }}>
            Todos ({periods.length})
          </Button>
          <Button size="small" variant="text" onClick={clearPeriods} sx={{ fontSize: "0.7rem" }}>
            Nenhum
          </Button>
          <Divider orientation="vertical" flexItem />
          {periods.map(p => (
            <Chip
              key={p.period}
              label={`${p.period} (${p.blob_count} blobs)`}
              size="small"
              variant={selPeriods.includes(p.period) ? "filled" : "outlined"}
              color={selPeriods.includes(p.period) ? "primary" : "default"}
              onClick={() => togglePeriod(p.period)}
              sx={{ cursor: "pointer" }}
            />
          ))}
        </Box>
      )}

      {/* ── Progress bar ── */}
      {taskStatus && (isRunning || isQueued || taskStatus.status === "COMPLETED" || taskStatus.status === "FAILED" || taskStatus.status === "CANCELLED") && (
        <Paper variant="outlined" sx={{ p: 2, mb: 3, bgcolor: isQueued ? "#fff8e1" : isRunning ? "#f0f7ff" : "#f5f5f5" }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1 }}>
            <Box>
              <Typography variant="body2" fontWeight={600}>
                {isQueued ? "Aguardando worker..." : isRunning ? "Processando dados" : taskStatus.status === "COMPLETED" ? "Concluido" : taskStatus.status === "CANCELLED" ? "Cancelado" : "Falha"}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {isQueued ? "Tarefa na fila de processamento" : isRunning && taskStatus.phase ? `Fase: ${taskStatus.phase}` : ""}
                {" · "}{taskStatus.rows_written.toLocaleString()} linhas processadas
                {" · "}batch {taskStatus.completed_batches}/{taskStatus.total_batches}
              </Typography>
            </Box>
            <Chip
              label={isQueued ? "QUEUED" : isRunning ? `${progress}%` : "100%"}
              size="small"
              color={
                taskStatus.status === "COMPLETED" ? "success" :
                taskStatus.status === "FAILED" || taskStatus.status === "CANCELLED" ? "error" :
                isQueued ? "warning" :
                "info"
              }
            />
          </Box>
          <LinearProgress
            variant={isQueued ? "indeterminate" : isRunning && taskStatus.total_batches > 0 ? "determinate" : "indeterminate"}
            value={isQueued ? 0 : isRunning ? progress : 100}
            color={
              taskStatus.status === "COMPLETED" ? "success" :
              taskStatus.status === "FAILED" || taskStatus.status === "CANCELLED" ? "error" :
              isQueued ? "warning" : "primary"
            }
            sx={{ height: 8, borderRadius: 4 }}
          />
        </Paper>
      )}

      {/* ── Context summary ── */}
      {selectedDestName && selConnIds.length > 0 && !isRunning && (
        <Paper variant="outlined" sx={{ p: 2, mb: 3, bgcolor: "#f5f5f5" }}>
          <Typography variant="body2" color="text.secondary">
            <strong>Tabela Destino:</strong> {selectedDestName}
            {" · "}
            <strong>Fontes:</strong>{" "}
            {selConnIds.map(id => mappedConns.find(c => c.id === id)?.code || id).join(", ")}
            {" · "}
            <strong>Meses:</strong>{" "}
            {selPeriods.length === 0
              ? `todos (${periods.length} meses)`
              : selPeriods.length === periods.length && periods.length > 0
                ? `todos (${periods.length} meses)`
                : `${selPeriods.length} de ${periods.length}`}
            {" · "}
            <strong>Total blobs:</strong> {totalBlobs}
          </Typography>
        </Paper>
      )}

      {/* ── Result ── */}
      {result && (
        <Alert severity={result.type} sx={{ mb: 2 }} onClose={() => setResult(null)}>
          {result.message}
        </Alert>
      )}

      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6">Histórico de reprocessamentos</Typography>
        {history.length === 0 && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Nenhum reprocessamento registrado.</Typography>}
        {history.map(item => {
          const failed = item.status === "FAILED" || item.status === "CANCELLED";
          return <Box key={item.id} sx={{ mt: 1.5, p: 1.25, border: "1px solid #e0e0e0", borderRadius: 1 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
              <Chip size="small" label={item.status} color={item.status === "COMPLETED" ? "success" : failed ? "error" : "info"} />
              <Typography fontWeight={600}>Reprocessamento {item.id}</Typography>
              <Typography variant="body2">{(item.rows_written || 0).toLocaleString()} linhas · {item.completed_batches || 0}/{item.total_batches || 0} blobs</Typography>
              {item.table && <Chip size="small" variant="outlined" label={item.table} />}
            </Box>
            <Typography variant="body2" color={failed ? "error" : "text.secondary"} sx={{ mt: .5 }}>{item.error || item.phase || "Aguardando worker..."}</Typography>
            {(item.event_log || []).slice().reverse().map((event, index) => <Typography key={`${event.at}-${index}`} variant="caption" display="block" color={event.level === "ERROR" ? "error" : "text.secondary"} sx={{ mt: .25 }}>
              {new Date(event.at).toLocaleString("pt-BR")} · {event.message}
            </Typography>)}
          </Box>;
        })}
      </Paper>

      {/* ── Empty state ── */}
      {!selDestId && !isRunning && (
        <Box sx={{ mt: 4 }}>
          <EmptyState message="Selecione uma Tabela Destino. As fontes com mapeamento configurado aparecerao automaticamente." />
        </Box>
      )}

      {selDestId && !loadingConns && mappedConns.length === 0 && !isRunning && (
        <Box sx={{ mt: 4 }}>
          <EmptyState message={`Nenhuma fonte mapeada para "${selectedDestName}". Va em Relacionamento.`} />
        </Box>
      )}

      {selDestId && mappedConns.length > 0 && selConnIds.length === 0 && !isRunning && (
        <Box sx={{ mt: 4 }}>
          <Alert severity="info">
            Selecione ao menos uma Fonte de Origem para ver os meses disponiveis.
          </Alert>
        </Box>
      )}
    </Box>
  );
}
