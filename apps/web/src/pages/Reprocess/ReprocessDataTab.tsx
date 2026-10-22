import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect, useRef, useCallback, type ReactNode } from "react";
import { useNotifications } from "../../context/NotificationContext";
import {
  Box, Typography, Button, Alert, Chip, CircularProgress, Checkbox, FormControlLabel,
  FormControl, InputLabel, Select, MenuItem, Paper, LinearProgress, Stack, ToggleButton, ToggleButtonGroup,
} from "@mui/material";
import { RefreshOutlined as Refresh, StopOutlined as Stop } from "@mui/icons-material";
import {
  getDestinationTables, DestinationTable,
  getConnectionsForDestination, MappedConnection,
  getAvailablePeriods, AvailablePeriod,
  reprocessDest, reclassifyDest, getReprocessStatus, cancelReprocess, ReprocessStatus,
  getRunningTasks, TaskStatus, getWorkerHealth, WorkerHealth,
  getGoldCubeRules, GoldCubeRule,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";

type Mode = "update" | "rebuild" | "classify";

const MODE_HINTS: Record<Mode, string> = {
  update: "Recarrega as competências escolhidas e substitui só o que mudou nelas.",
  rebuild: "Apaga a tabela de destino e reprocessa tudo a partir das fontes. Use com cautela.",
  classify: "Não recarrega dados. Reaplica a classificação dos projetos.",
};

function StepCard({ n, title, hint, disabled, children }: {
  n: number; title: string; hint?: string; disabled?: boolean; children: ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2.5, opacity: disabled ? 0.55 : 1 }}>
      <Stack direction="row" spacing={1.5} alignItems="baseline" sx={{ mb: hint ? 0.25 : 1.5 }}>
        <Typography variant="body2" sx={{ fontWeight: 700, color: "primary.main", minWidth: 20 }}>{n}</Typography>
        <Typography variant="subtitle1" fontWeight={700}>{title}</Typography>
      </Stack>
      {hint && <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1.5, ml: 3.5 }}>{hint}</Typography>}
      <Box sx={{ ml: { xs: 0, sm: 3.5 } }}>{children}</Box>
    </Paper>
  );
}

export default function ReprocessDataTab() {
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [mappedConns, setMappedConns] = useState<MappedConnection[]>([]);
  const [selDestId, setSelDestId] = useState<number | "">("");
  const [selConnIds, setSelConnIds] = useState<number[]>([]);
  const [selPeriods, setSelPeriods] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("update");
  const [chainGoldCubes, setChainGoldCubes] = useState(false);
  const [chainGoldCubeRuleId, setChainRuleId] = useState<number | "">("");
  const [goldRules, setGoldRules] = useState<GoldCubeRule[]>([]);
  const [periods, setPeriods] = useState<AvailablePeriod[]>([]);
  const [processing, setProcessing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<ReprocessStatus | null>(null);
  const [result, setResult] = useState<{
    type: "success" | "error"; message: string;
  } | null>(null);
  const [loadingPeriods, setLoadingPeriods] = useState(false);
  const [loadingConns, setLoadingConns] = useState(false);
  const [workerHealth, setWorkerHealth] = useState<WorkerHealth | null>(null);
  const notifications = useNotifications();

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getDestinationTables(true).then(setDestTables).catch(() => {});
    getGoldCubeRules().then(setGoldRules).catch(() => {});
    const refreshWorker = () => getWorkerHealth().then(setWorkerHealth).catch(() => setWorkerHealth({ status: "offline", heartbeat_age_seconds: null }));
    refreshWorker();
    const healthTimer = setInterval(refreshWorker, 3000);
    return () => clearInterval(healthTimer);
  }, []);

  // Ao trocar a tabela de destino: carrega só as fontes com mapeamento ativo
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

  // Ao mudar as fontes: carrega os meses disponíveis
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

  // Acompanha as tarefas de reprocessamento
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
            setResult({ type: "error", message: failed.error || "Não foi possível reprocessar" });
          } else {
            setResult({ type: "error", message: "Processamento interrompido." });
          }
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setProcessing(false);
      }
    }, taskIds.length > 4 ? 4000 : 2000);
  }, []);

  // Ao escolher o destino: retoma tarefas em andamento da fila global
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
    const interval = setInterval(check, 5000);
    return () => clearInterval(interval);
  }, [selDestId, pollTask]);

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
      const res = mode === "classify"
        ? await reclassifyDest({ destination_table_id: selDestId as number, connection_ids: selConnIds, billing_periods: selPeriods })
        : await reprocessDest({
            destination_table_id: selDestId as number,
            connection_ids: selConnIds,
            billing_periods: selPeriods,
            replace_existing: mode === "rebuild",
            chain_gold_cubes: { enabled: chainGoldCubes, rule_ids: chainGoldCubeRuleId === "" ? [] : [chainGoldCubeRuleId as number] },
          });
      await notifications.addTaskById(res.task_id, { kind: "reprocess", title: mode === "classify" ? "Refazer a classificação de projetos" : "Refazer a carga de dados" });
      setTaskId(res.task_id);
      pollTask(res.task_ids || [res.task_id]);
    } catch (e: unknown) {
      setResult({
        type: "error",
        message: e instanceof Error ? e.message : "Erro de conexão",
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

  const changeMode = (next: Mode | null) => {
    if (!next) return;
    setMode(next);
    if (next === "classify") setChainGoldCubes(false);
  };

  const selectedDest = selDestId ? destTables.find(t => t.id === selDestId) : undefined;
  const selectedPtuConns = mappedConns.filter(c => selConnIds.includes(c.id) && c.connector_type === "azure_openai_ptu");
  const isPtuReprocess = selectedPtuConns.length > 0 && selectedPtuConns.length === selConnIds.length;

  const toggleConn = (id: number) => {
    setSelConnIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };
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
  const progress = taskStatus?.progress ?? 0;
  const busy = !!isRunning || processing;
  const workerOk = workerHealth?.status === "ok";
  const canRun = !!selDestId && selConnIds.length > 0 && !busy && !!workerOk;

  const periodLabel = (p: AvailablePeriod) =>
    isPtuReprocess
      ? `${p.label || `${p.period.slice(4)}/${p.period.slice(0, 4)}`} (${p.blob_count} janelas)`
      : `${p.period} (${p.blob_count} blobs)`;

  const monthsSummary = selPeriods.length === 0 || selPeriods.length === periods.length
    ? `todos (${periods.length} meses)`
    : `${selPeriods.length} de ${periods.length} meses`;

  return (
    <Stack spacing={2}>
      {workerHealth && workerHealth.status !== "ok" && (
        <Alert severity="error">
          Serviço de processamento {workerHealth.status === "offline" ? "indisponível" : "sem sinal de atividade"}. O reprocessamento será liberado quando o serviço estiver ativo.
        </Alert>
      )}

      {result && (
        <Alert severity={result.type} onClose={() => setResult(null)}>
          {dataDisplayText(result.message)}
        </Alert>
      )}

      {busy && (
        <Paper variant="outlined" sx={{ p: 2.5 }}>
          <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} spacing={2}>
            <Box sx={{ flex: 1, width: "100%" }}>
              <Typography variant="subtitle2" fontWeight={700}>
                {selectedDest?.name ?? "Reprocessamento"} · {isQueued ? "aguardando worker" : `${Math.round(progress)}%`}
              </Typography>
              <LinearProgress variant={isQueued ? "indeterminate" : "determinate"} value={progress} sx={{ mt: 1, height: 8, borderRadius: 4 }} />
              <Typography variant="caption" color="text.secondary">
                Acompanhe também no ícone de notificações.
              </Typography>
            </Box>
            <Button variant="outlined" color="error" disabled={cancelling || !taskId}
              startIcon={cancelling ? <CircularProgress size={18} /> : <Stop />} onClick={doCancel}>
              {cancelling ? "Interrompendo..." : "Cancelar"}
            </Button>
          </Stack>
        </Paper>
      )}

      {/* 1 · Destino */}
      <StepCard n={1} title="Escolha onde gravar os dados" hint="Onde os dados serão gravados.">
        <FormControl sx={{ minWidth: 320 }}>
          <InputLabel>Destino dos dados</InputLabel>
          <Select value={selDestId} label="Destino dos dados" disabled={busy}
            onChange={(e) => setSelDestId(e.target.value as number | "")}>
            {destTables.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
          </Select>
        </FormControl>
      </StepCard>

      {/* 2 · Fontes */}
      <StepCard n={2} title="Escolha as fontes"
        hint="Aparecem apenas as fontes com mapeamento configurado para o destino."
        disabled={!selDestId}>
        {!selDestId && <Typography variant="body2" color="text.secondary">Escolha primeiro um destino.</Typography>}
        {selDestId && loadingConns && <Typography variant="body2" color="text.secondary">Carregando fontes…</Typography>}
        {selDestId && !loadingConns && mappedConns.length === 0 && (
          <EmptyState message={`Nenhuma fonte mapeada para "${selectedDest?.name}". Configure em Dados › Relacionamentos.`} />
        )}
        {mappedConns.length > 0 && (
          <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
            {mappedConns.map((c) => {
              const on = selConnIds.includes(c.id);
              return (
                <Paper key={c.id} variant="outlined" role="checkbox" aria-checked={on} tabIndex={0}
                  onClick={() => !busy && toggleConn(c.id)}
                  onKeyDown={(e) => { if ((e.key === " " || e.key === "Enter") && !busy) { e.preventDefault(); toggleConn(c.id); } }}
                  sx={{
                    p: 1.5, width: 260, cursor: busy ? "default" : "pointer", display: "flex", gap: 1, alignItems: "flex-start",
                    borderColor: on ? "primary.main" : "divider", bgcolor: on ? "action.selected" : "background.paper",
                  }}>
                  <Checkbox size="small" checked={on} tabIndex={-1} sx={{ p: 0.5 }} />
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600} noWrap>{c.name}</Typography>
                    <Typography variant="caption" color="text.secondary" component="p">
                      {c.code} · {c.mapping_count} colunas mapeadas
                    </Typography>
                  </Box>
                </Paper>
              );
            })}
          </Stack>
        )}
      </StepCard>

      {/* 3 · Meses */}
      <StepCard n={3} title="Escolha as competências"
        hint={isPtuReprocess ? "Fonte Azure PTU: escolha uma competência. Sem seleção, busca os últimos ~93 dias." : "Sem seleção, todas as competências disponíveis entram."}
        disabled={selConnIds.length === 0}>
        {loadingPeriods && <Typography variant="body2" color="text.secondary">Carregando competências…</Typography>}
        {!loadingPeriods && selConnIds.length > 0 && periods.length === 0 && (
          <Typography variant="body2" color="text.secondary">Nenhuma competência disponível para as fontes escolhidas.</Typography>
        )}
        {periods.length > 0 && (
          <>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
              <Button size="small" onClick={selectAllPeriods} disabled={busy}>Todos ({periods.length})</Button>
              <Button size="small" onClick={clearPeriods} disabled={busy}>Sem seleção</Button>
              <Typography variant="caption" color="text.secondary">{monthsSummary}</Typography>
            </Stack>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {periods.map(p => {
                const on = selPeriods.includes(p.period);
                return (
                  <Chip key={p.period} label={periodLabel(p)} size="small" disabled={busy}
                    variant={on ? "filled" : "outlined"} color={on ? "primary" : "default"}
                    onClick={() => togglePeriod(p.period)} sx={{ cursor: "pointer" }} />
                );
              })}
            </Stack>
          </>
        )}
      </StepCard>

      {/* 4 · Modo */}
      <StepCard n={4} title="Escolha o tipo de recarga" hint={MODE_HINTS[mode]} disabled={selConnIds.length === 0}>
        <ToggleButtonGroup exclusive value={mode} disabled={busy} onChange={(_, v: Mode | null) => changeMode(v)} sx={{ flexWrap: "wrap" }}>
          <ToggleButton value="update">Recarregar competências</ToggleButton>
          <ToggleButton value="rebuild">Recriar tabela inteira</ToggleButton>
          <ToggleButton value="classify">Apenas reclassificar projetos</ToggleButton>
        </ToggleButtonGroup>
        {mode !== "classify" && (
          <Box sx={{ mt: 2 }}>
            <FormControlLabel
              control={<Checkbox checked={chainGoldCubes} disabled={busy} onChange={(e) => setChainGoldCubes(e.target.checked)} />}
              label="Materializar a base consolidada ao concluir"
            />
            {chainGoldCubes && (
              <FormControl sx={{ minWidth: 320, ml: 4, mt: 1 }}>
                <InputLabel>Base consolidada de destino</InputLabel>
                <Select value={chainGoldCubeRuleId} label="Base consolidada de destino" disabled={busy}
                  onChange={(e) => setChainRuleId(e.target.value as number | "")}>
                  <MenuItem value=""><em>Todos os ajustes ativos</em></MenuItem>
                  {goldRules.filter(r => r.active).map(r => (
                    <MenuItem key={r.id} value={r.id!}>{r.name} ({r.materialization_table || r.code})</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
          </Box>
        )}
      </StepCard>

      {/* Resumo e ação */}
      <Paper variant="outlined" sx={{ p: 2.5, position: "sticky", bottom: 16, bgcolor: "background.paper", boxShadow: 2 }}>
        <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} justifyContent="space-between" spacing={2}>
          <Typography variant="body2" color="text.secondary">
            {selDestId && selConnIds.length > 0 ? (
              <>
                <strong>{selectedDest?.name}</strong> · {selConnIds.length} fonte(s) · {monthsSummary}
                {!isPtuReprocess && totalBlobs ? ` · ${totalBlobs} blobs` : ""} · {mode === "update" ? "atualizar meses" : mode === "rebuild" ? "reconstruir tabela" : "reclassificar projetos"}
                {chainGoldCubes && mode !== "classify" ? " · materializa base consolidada" : ""}
              </>
            ) : "Conclua os passos 1 e 2 para reprocessar."}
          </Typography>
          <Button variant="contained" size="large" startIcon={<Refresh />} onClick={doReprocess} disabled={!canRun}>
            {mode === "classify" ? "Reclassificar" : "Reprocessar"}
          </Button>
        </Stack>
        {!workerOk && workerHealth && (
          <Typography variant="caption" color="error" component="p" sx={{ mt: 1 }}>
            O serviço de processamento precisa estar ativo para liberar o botão.
          </Typography>
        )}
      </Paper>
    </Stack>
  );
}
