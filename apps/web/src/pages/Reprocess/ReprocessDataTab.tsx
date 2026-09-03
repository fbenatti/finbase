import { useState, useEffect, useRef, useCallback } from "react";
import { useNotifications } from "../../context/NotificationContext";
import {
  Box, Typography, Button, Alert, Chip, CircularProgress,
  FormControl, InputLabel, Select, MenuItem, Checkbox, ListItemText,
  OutlinedInput, Paper, LinearProgress, Divider, FormControlLabel, Switch,
  TextField, InputAdornment,
} from "@mui/material";
import { Refresh, Stop, Search, Replay } from "@mui/icons-material";
import {
  getDestinationTables, DestinationTable,
  getConnectionsForDestination, MappedConnection,
  getAvailablePeriods, AvailablePeriod,
  reprocessDest, reclassifyDest, getReprocessStatus, cancelReprocess, ReprocessStatus,
  getRunningTasks, TaskStatus, getWorkerHealth, WorkerHealth, getReprocessHistory,
  getDimonRules, DimonRule,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import ReprocessHistoryDataGrid from "./ReprocessHistoryDataGrid";

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

export default function ReprocessDataTab() {
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [mappedConns, setMappedConns] = useState<MappedConnection[]>([]);
  const [selDestId, setSelDestId] = useState<number | "">("");
  const [selConnIds, setSelConnIds] = useState<number[]>([]);
  const [selPeriods, setSelPeriods] = useState<string[]>([]);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [classificationOnly, setClassificationOnly] = useState(false);
  const [chainDimons, setChainDimons] = useState(false);
  const [chainDimonRuleId, setChainDimonRuleId] = useState<number | "">("");
  const [dimonRules, setDimonRules] = useState<DimonRule[]>([]);
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
  const [historySearch, setHistorySearch] = useState("");
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const notifications = useNotifications();
  const historyQueryState = useRef<{
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
  }>({ offset: 0, limit: 10, filters: [] });

  const historyQuery = useCallback(async (queryParam?: {
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
  }) => {
    setHistoryLoading(true);
    try {
      if (queryParam) historyQueryState.current = queryParam;
      const response = await getReprocessHistory({
        ...historyQueryState.current,
        search: historySearch || undefined,
      });
      setHistory(response.items);
      setHistoryTotal(response.total);
    } catch {
      setHistory([]);
      setHistoryTotal(0);
    } finally {
      setHistoryLoading(false);
    }
  }, [historySearch]);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getDestinationTables(true).then(setDestTables).catch(() => {});
    getDimonRules().then(setDimonRules).catch(() => {});
    historyQuery({ offset: 0, limit: 10, filters: [] }).catch(() => {});
    const refreshWorker = () => getWorkerHealth().then(setWorkerHealth).catch(() => setWorkerHealth({ status: "offline", heartbeat_age_seconds: null }));
    refreshWorker();
    const healthTimer = setInterval(refreshWorker, 3000);
    return () => clearInterval(healthTimer);
  }, []);

  useEffect(() => {
    historyQuery({ offset: 0, limit: 10, filters: historyQueryState.current.filters });
  }, [historySearch]);

  // When dest table changes: load only connections with active mappings
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
        const onlyPtu = selConnIds.every(id => mappedConns.find(c => c.id === id)?.connector_type === "azure_openai_ptu");
        setSelPeriods(onlyPtu && sorted.length ? [sorted[0].period] : sorted.map(p => p.period));
      })
      .finally(() => setLoadingPeriods(false));
  }, [selConnIds]);

  // Poll task status — finds running reprocess tasks globally
  const pollTask = useCallback((taskIds: number[]) => {
    pollRef.current = setInterval(async () => {
      try {
        const statuses = await Promise.all(taskIds.map(getReprocessStatus));
        const active = statuses.find(s => ["QUEUED", "RUNNING"].includes(s.status));
        const s = active || statuses[statuses.length - 1];
        setTaskStatus(s);
        if (!active) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setProcessing(false);
          setCancelling(false);
          const failed = statuses.find(item => item.status === "FAILED");
          const cancelled = statuses.find(item => item.status === "CANCELLED");
          if (!failed && !cancelled) {
            setResult({
              type: "success",
              message: `${statuses.reduce((total, item) => total + item.rows_written, 0).toLocaleString()} linhas publicadas em ${statuses.length} fonte(s)/mês(es)`,
            });
          } else if (failed) {
            setResult({ type: "error", message: failed.error || "Falha ao reprocessar" });
          } else {
            setResult({ type: "error", message: "Processamento cancelado." });
          }
          historyQuery({ offset: 0, limit: 10, filters: [] }).catch(() => {});
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setProcessing(false);
      }
    }, taskIds.length > 4 ? 4000 : 2000);
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
          pollTask([reprocessTask.id]);
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
      const res = classificationOnly
        ? await reclassifyDest({ destination_table_id: selDestId as number, connection_ids: selConnIds, billing_periods: selPeriods })
        : await reprocessDest({
            destination_table_id: selDestId as number,
            connection_ids: selConnIds,
            billing_periods: selPeriods,
            replace_existing: replaceExisting,
            chain_dimons: { enabled: chainDimons, rule_ids: chainDimonRuleId === "" ? [] : [chainDimonRuleId as number] },
          });
      await notifications.addTaskById(res.task_id, { kind: "reprocess", title: classificationOnly ? "Reclassificar projetos" : "Reprocessar dados" });
      setTaskId(res.task_id);
      pollTask(res.task_ids || [res.task_id]);
      historyQuery({ offset: 0, limit: 10, filters: [] }).catch(() => {});
    } catch (e: unknown) {
      setResult({
        type: "error",
        message: e instanceof Error ? e.message : "Erro de conexao",
      });
      setProcessing(false);
    }
  };

  const handleReprocessItem = async (item: ReprocessStatus) => {
    const destId = item.destination_table_id || (destTables.find(t => t.name === item.table)?.id ?? (selDestId ? Number(selDestId) : undefined));
    if (!destId) {
      setResult({ type: "error", message: "Tabela de destino não identificada para este item." });
      return;
    }
    if (!item.connection_ids || item.connection_ids.length === 0) {
      setResult({ type: "error", message: "Conexões de origem não identificadas para este item." });
      return;
    }
    setProcessing(true);
    setResult(null);
    try {
      const res = await reprocessDest({
        destination_table_id: Number(destId),
        connection_ids: item.connection_ids,
        billing_periods: item.billing_periods,
        replace_existing: false,
        chain_dimons: { enabled: chainDimons, rule_ids: chainDimonRuleId === "" ? [] : [chainDimonRuleId as number] },
      });
      await notifications.addTaskById(res.task_id, { kind: "reprocess", title: `Reprocessar ${item.table || "dados"}` });
      setTaskId(res.task_id);
      pollTask(res.task_ids || [res.task_id]);
      historyQuery({ offset: 0, limit: 10, filters: [] }).catch(() => {});
    } catch (e: unknown) {
      setResult({
        type: "error",
        message: e instanceof Error ? e.message : "Erro ao disparar reprocessamento",
      });
      setProcessing(false);
    }
  };

  const failedItems = history.filter(h => h.status === "FAILED");

  const handleReprocessAllFailed = async () => {
    if (failedItems.length === 0) return;
    setProcessing(true);
    setResult(null);
    try {
      const allTaskIds: number[] = [];
      for (const item of failedItems) {
        const destId = item.destination_table_id || (destTables.find(t => t.name === item.table)?.id ?? (selDestId ? Number(selDestId) : undefined));
        if (destId && item.connection_ids && item.connection_ids.length > 0) {
          const res = await reprocessDest({
            destination_table_id: Number(destId),
            connection_ids: item.connection_ids,
            billing_periods: item.billing_periods,
            replace_existing: false,
            chain_dimons: { enabled: chainDimons, rule_ids: chainDimonRuleId === "" ? [] : [chainDimonRuleId as number] },
          });
          await notifications.addTaskById(res.task_id, { kind: "reprocess", title: `Reprocessar ${item.table || "dados"}` });
          if (res.task_ids) allTaskIds.push(...res.task_ids);
          else allTaskIds.push(res.task_id);
        }
      }
      if (allTaskIds.length > 0) {
        setTaskId(allTaskIds[0]);
        pollTask(allTaskIds);
        historyQuery({ offset: 0, limit: 10, filters: [] }).catch(() => {});
      } else {
        setProcessing(false);
      }
    } catch (e: unknown) {
      setResult({
        type: "error",
        message: e instanceof Error ? e.message : "Erro ao disparar reprocessamento das falhas",
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
  const selectedPtuConns = mappedConns.filter(c => selConnIds.includes(c.id) && c.connector_type === "azure_openai_ptu");
  const isPtuReprocess = selectedPtuConns.length > 0 && selectedPtuConns.length === selConnIds.length;

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
              control={<Switch checked={replaceExisting} onChange={(e) => setReplaceExisting(e.target.checked)} color="warning" disabled={classificationOnly} />}
              label="Reconstruir tabela completa (substituir dados antigos)"
            />
            <FormControlLabel
              control={<Switch checked={classificationOnly} onChange={(e) => { setClassificationOnly(e.target.checked); if (e.target.checked) { setReplaceExisting(false); setChainDimons(false); } }} color="primary" />}
              label="Reprocessar apenas classificação dos projetos"
            />
            <FormControlLabel
              control={<Switch checked={chainDimons} onChange={(e) => setChainDimons(e.target.checked)} color="primary" disabled={classificationOnly} />}
              label="Materializar Dimon na sequência (visão completa)"
            />
            {chainDimons && (
              <FormControl sx={{ minWidth: 280 }} size="small">
                <InputLabel>Tabela Dimon</InputLabel>
                <Select
                  value={chainDimonRuleId}
                  label="Tabela Dimon"
                  onChange={(e) => setChainDimonRuleId(e.target.value as number | "")}
                >
                  <MenuItem value=""><em>Todas as regras ativas</em></MenuItem>
                  {dimonRules.filter(r => r.active).map(r => (
                    <MenuItem key={r.id} value={r.id!}>{r.name} ({r.materialization_table || r.code})</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
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
              label={isPtuReprocess ? `${p.label || `${p.period.slice(4)}/${p.period.slice(0, 4)}`} (${p.blob_count} janelas)` : `${p.period} (${p.blob_count} blobs)`}
              size="small"
              variant={selPeriods.includes(p.period) ? "filled" : "outlined"}
              color={selPeriods.includes(p.period) ? "primary" : "default"}
              onClick={() => togglePeriod(p.period)}
              sx={{ cursor: "pointer" }}
            />
          ))}
        </Box>
      )}
      {isPtuReprocess && selConnIds.length > 0 && !isRunning && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Fonte Azure PTU: selecione o mês (ex.: <strong>08/2026</strong>) e clique em Reprocessar para buscar
          métricas horárias via Azure Monitor (janelas de até 30 dias) em <strong>dest_azure_ptu_metrics</strong>.
          Sem mês selecionado, busca os últimos ~93 dias. Azure Monitor retém plataforma por ~93 dias — não usa blobs Bronze.
        </Alert>
      )}
      {/* ── Andamento — movido para o ícone de notificações ── */}
      {chainDimons && !isRunning && (
        <Alert severity="info" sx={{ mb: 2 }}>
          A Dimon selecionada será materializada <strong>na sequência</strong>, depois que o último
          reprocessamento do grupo concluir (visão completa = realizado + forecast + rateio). Acompanhe
          a execução da Dimon no sino de notificações e na aba <strong>Dimon (Custo pré-agregado)</strong>.
        </Alert>
      )}
      {(isRunning || isQueued) && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Processando dados{isQueued ? " (aguardando worker)" : ""} — acompanhe o andamento no ícone de notificações (canto superior direito).
        </Alert>
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
            {isPtuReprocess
              ? (selPeriods.length === 0 ? "coleta completa (93 dias)" : `${selPeriods.length} mês(es) selecionado(s)`)
              : selPeriods.length === 0
              ? `todos (${periods.length} meses)`
              : selPeriods.length === periods.length && periods.length > 0
                ? `todos (${periods.length} meses)`
                : `${selPeriods.length} de ${periods.length}`}
            {" · "}
            <strong>{isPtuReprocess ? "Janelas Monitor:" : "Total blobs:"}</strong>{" "}
            {isPtuReprocess
              ? (selPeriods.length === 0
                ? totalBlobs || "backfill ~93d"
                : `${totalBlobs || selPeriods.reduce((sum, p) => sum + (periods.find(x => x.period === p)?.blob_count || 0), 0)} janelas`)
              : totalBlobs}
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
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 2, mb: 1.5, flexWrap: "wrap" }}>
          <Box>
            <Typography variant="h6">Histórico de reprocessamentos</Typography>
            <Typography variant="caption" color="text.secondary">Últimas execuções e mensagens operacionais</Typography>
          </Box>
          <Box sx={{ display: "flex", gap: 1.5, alignItems: "center", flexWrap: "wrap" }}>
            {failedItems.length > 0 && !isRunning && (
              <Button
                variant="outlined"
                color="error"
                size="small"
                startIcon={<Replay />}
                onClick={handleReprocessAllFailed}
                disabled={processing || workerHealth?.status !== "ok"}
              >
                Reprocessar falhas ({failedItems.length})
              </Button>
            )}
            <TextField
              size="small"
              placeholder="Buscar no histórico…"
              value={historySearch}
              onChange={(e) => setHistorySearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Search fontSize="small" />
                  </InputAdornment>
                ),
                sx: { height: 40 },
              }}
              sx={{ minWidth: 260 }}
            />
          </Box>
        </Box>
        <ReprocessHistoryDataGrid
          rows={history}
          total={historyTotal}
          loading={historyLoading}
          onReprocess={handleReprocessItem}
          onQuery={historyQuery}
        />
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
