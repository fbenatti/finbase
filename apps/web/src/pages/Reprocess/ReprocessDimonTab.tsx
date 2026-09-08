import { useEffect, useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import { CheckBox, CheckBoxOutlineBlank, PlayArrow, Refresh } from "@mui/icons-material";
import { DimonHistoryItem, DimonRule, formatApiError, getDimonHistory, getDimonRules, reprocessDimons } from "../../services/api";

export default function ReprocessDimonTab() {
  const [rules, setRules] = useState<DimonRule[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<DimonHistoryItem[]>([]);

  const load = async () => {
    const [ruleList, hist] = await Promise.all([
      getDimonRules().catch(() => [] as DimonRule[]),
      getDimonHistory().catch(() => [] as DimonHistoryItem[]),
    ]);
    setRules(ruleList);
    setHistory(hist);
    setSelectedIds(current => new Set([...current].filter(id => ruleList.some(r => r.id === id))));
  };
  useEffect(() => { void load(); }, []);

  const hasActiveJobs = history.some(item => item.status === "QUEUED" || item.status === "RUNNING");
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => { void load(); }, 4000);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs]);

  const toggleRule = (id: number) => setSelectedIds(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const allSelected = rules.length > 0 && rules.every(r => r.id != null && selectedIds.has(r.id));
  const toggleAll = () => setSelectedIds(allSelected ? new Set() : new Set(rules.flatMap(r => r.id == null ? [] : [r.id])));

  const startAll = async () => {
    setRunning(true); setError("");
    try { await reprocessDimons([]); await load(); }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };

  const startSelected = async () => {
    if (!selectedIds.size) return;
    setRunning(true); setError("");
    try { await reprocessDimons([...selectedIds]); await load(); }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };

  const statusChip = (r: DimonRule) => {
    const s = r.materialization_status || "PENDING";
    const label = s === "UPDATED" ? "Atualizada" : s === "EMPTY" ? "Sem dados" : s === "ERROR" ? "Com erro" : "Pendente";
    const color = s === "UPDATED" ? "success" : s === "ERROR" ? "error" : "warning";
    return <Chip size="small" label={label} color={color as any} />;
  };

  return <Stack spacing={2}>
    <Typography variant="h6">Camada Dimon de Custo</Typography>
    <Typography color="text.secondary">Lê as Golds (FOCUS, planejamento/forecast/rateio...), aplica os filtros das regras e publica a tabela pré-agregada dest_dm_cost_&lt;code&gt; no namespace iceberg.dimon. Escolha reprocessar <b>todas (full)</b> ou apenas uma regra/fonte.</Typography>
    {error && <Alert severity="error">{error}</Alert>}

    <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
      <Button variant="outlined" startIcon={<PlayArrow />} disabled={running || selectedIds.size === 0} onClick={startSelected}>Reprocessar selecionadas ({selectedIds.size})</Button>
      <Button variant="text" startIcon={allSelected ? <CheckBox /> : <CheckBoxOutlineBlank />} disabled={running || rules.length === 0} onClick={toggleAll}>{allSelected ? "Desmarcar todos" : "Marcar todos"}</Button>
    </Stack>

    {/* Regras ativas com status */}
    <Box>
      <Typography variant="subtitle2" mb={1}>Regras Dimon ({rules.length}) · {selectedIds.size} selecionada(s)</Typography>
      {rules.length === 0 ? <Typography color="text.secondary">Nenhuma regra criada — use o editor em Relacionamentos de custo IA.</Typography> : <Stack spacing={1}>
        {rules.map(r => <Box key={r.id} sx={{ display: "flex", alignItems: "center", gap: 1.5, p: 1, border: "1px solid", borderColor: selectedIds.has(r.id!) ? "primary.main" : "divider", borderRadius: 1, bgcolor: selectedIds.has(r.id!) ? "action.selected" : "transparent", cursor: "pointer" }} onClick={() => r.id != null && toggleRule(r.id)}>
          <Checkbox size="small" checked={r.id != null && selectedIds.has(r.id)} onChange={() => r.id != null && toggleRule(r.id)} onClick={e => e.stopPropagation()} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} noWrap>{r.name}</Typography>
            <Typography variant="caption" color="text.secondary" noWrap>{r.code} · {r.materialization_table || "Pendente"} · {r.materialization_rows ?? 0} linhas</Typography>
          </Box>
          {statusChip(r)}
        </Box>)}
      </Stack>}
    </Box>

    <Typography variant="subtitle2">Histórico recente</Typography>
    {history.length === 0 ? <Typography color="text.secondary">Nenhuma execução registrada.</Typography> : history.map(item => {
      const failed = item.result_json?.failed || [];
      const rowLabel = item.status === "COMPLETED" || item.status === "FAILED"
        ? ` · ${(item.rows_destination ?? 0).toLocaleString()} linhas`
        : "";
      return <Box key={item.id}>
        <Alert severity={item.status === "FAILED" ? "error" : item.status === "COMPLETED" ? (failed.length ? "warning" : "success") : "info"}>
          #{item.id} · {item.status} · {item.phase || "Sem detalhe"}{rowLabel}
        </Alert>
        {failed.length > 0 && <Box sx={{ mt: 0.5, ml: 2 }}>
          {failed.map((f, index) => <Typography key={`f-${index}`} variant="caption" display="block" color="error.main">
            ✕ {f.code || `Regra #${f.rule_id}`}: {f.error || "erro"}
          </Typography>)}
        </Box>}
      </Box>;
    })}
  </Stack>;
}
