import { useEffect, useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, Stack, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import { CheckBox, CheckBoxOutlineBlank, PlayArrow, Refresh } from "@mui/icons-material";
import { GoldCubeRule, formatApiError, getGoldCubeRules, reprocessGoldCubes } from "../../services/api";
import ReprocessAICostRelationshipsTab from "./ReprocessAICostRelationshipsTab";

export default function ReprocessGoldTab() {
  const [params] = useSearchParams();
  const subtab = params.get("subtab") === "ai-cost" ? "ai-cost" : "cubes";
  const [rules, setRules] = useState<GoldCubeRule[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const load = async () => {
    const ruleList = await getGoldCubeRules().catch(() => [] as GoldCubeRule[]);
    setRules(ruleList);
    setSelectedIds(current => new Set([...current].filter(id => ruleList.some(r => r.id === id))));
  };
  useEffect(() => { void load(); }, []);

  const toggleRule = (id: number) => setSelectedIds(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const allSelected = rules.length > 0 && rules.every(r => r.id != null && selectedIds.has(r.id));
  const toggleAll = () => setSelectedIds(allSelected ? new Set() : new Set(rules.flatMap(r => r.id == null ? [] : [r.id])));

  const startAll = async () => {
    setRunning(true); setError("");
    try { await reprocessGoldCubes([]); }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };

  const startSelected = async () => {
    if (!selectedIds.size) return;
    setRunning(true); setError("");
    try { await reprocessGoldCubes([...selectedIds]); }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };

  const statusChip = (r: GoldCubeRule) => {
    const s = r.materialization_status || "PENDING";
    const materializedAt = r.materialized_at ? new Date(r.materialized_at) : null;
    const hasValidDate = materializedAt != null && !Number.isNaN(materializedAt.getTime());
    const stale = s === "UPDATED" && (!hasValidDate || now - materializedAt.getTime() > 24 * 60 * 60 * 1000);
    const label = stale ? "Cubo desatualizado" : s === "UPDATED" ? "Atualizado" : s === "EMPTY" ? "Sem dados" : s === "ERROR" ? "Com erro" : "Pendente";
    const color = stale ? "warning" : s === "UPDATED" ? "success" : s === "ERROR" ? "error" : "warning";
    const dateLabel = hasValidDate
      ? `Última atualização: ${new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(materializedAt)}`
      : s === "UPDATED" ? "Última atualização não registrada" : null;
    return (
      <Stack spacing={0.25} alignItems="flex-end">
        <Chip size="small" label={label} color={color} />
        {dateLabel && <Typography variant="caption" color="text.secondary">{dateLabel}</Typography>}
      </Stack>
    );
  };

  return <Stack spacing={2}>
    {subtab === "ai-cost" ? <ReprocessAICostRelationshipsTab /> : <>
    <Typography variant="h6">Materialização Gold</Typography>
    <Typography color="text.secondary">Lê a Silver (FOCUS, planejamento/forecast/rateio...), aplica os filtros das regras e publica cubos agregados no namespace iceberg.gold. Escolha reprocessar <b>todas</b> ou apenas uma regra/fonte.</Typography>
    {error && <Alert severity="error">{error}</Alert>}

    <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
      <Button variant="outlined" startIcon={<PlayArrow />} disabled={running || selectedIds.size === 0} onClick={startSelected}>Reprocessar selecionadas ({selectedIds.size})</Button>
      <Button variant="text" startIcon={allSelected ? <CheckBox /> : <CheckBoxOutlineBlank />} disabled={running || rules.length === 0} onClick={toggleAll}>{allSelected ? "Desmarcar todos" : "Marcar todos"}</Button>
    </Stack>

    {/* Regras ativas com status */}
    <Box>
      <Typography variant="subtitle2" mb={1}>Regras Gold ({rules.length}) · {selectedIds.size} selecionada(s)</Typography>
      {rules.length === 0 ? <Typography color="text.secondary">Nenhuma regra de cubo Gold configurada.</Typography> : <Stack spacing={1}>
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
    </>}
  </Stack>;
}
