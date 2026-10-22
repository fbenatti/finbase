import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, Paper, Stack, Typography } from "@mui/material";
import { CheckBoxOutlined, CheckBoxOutlineBlankOutlined, PlayArrowOutlined } from "@mui/icons-material";
import { GoldCubeRule, formatApiError, getGoldCubeRules, reprocessGoldCubes } from "../../services/api";

export default function ReprocessGoldTab() {
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
    catch (e) { setError(formatApiError(e, "Não foi possível enviar o reprocessamento para a fila")); }
    finally { setRunning(false); }
  };

  const startSelected = async () => {
    if (!selectedIds.size) return;
    setRunning(true); setError("");
    try { await reprocessGoldCubes([...selectedIds]); }
    catch (e) { setError(formatApiError(e, "Não foi possível enviar o reprocessamento para a fila")); }
    finally { setRunning(false); }
  };

  const statusChip = (r: GoldCubeRule) => {
    const s = r.materialization_status || "PENDING";
    const materializedAt = r.materialized_at ? new Date(r.materialized_at) : null;
    const hasValidDate = materializedAt != null && !Number.isNaN(materializedAt.getTime());
    const stale = s === "UPDATED" && (!hasValidDate || now - materializedAt.getTime() > 24 * 60 * 60 * 1000);
    const label = stale ? "Modelo desatualizado" : s === "UPDATED" ? "Atualizado" : s === "EMPTY" ? "Sem registros" : s === "ERROR" ? "Com falha" : "Pendente";
    const color = stale ? "warning" : s === "UPDATED" ? "success" : s === "ERROR" ? "error" : "warning";
    const dateLabel = hasValidDate
      ? `Última carga: ${new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(materializedAt)}`
      : s === "UPDATED" ? "Última atualização não anotada" : null;
    return (
      <Stack spacing={0.25} alignItems={{ xs: "flex-start", sm: "flex-end" }}>
        <Chip size="small" label={label} color={color} variant="outlined" />
        {dateLabel && <Typography variant="caption" color="text.secondary">{dateLabel}</Typography>}
      </Stack>
    );
  };

  const updatedCount = rules.filter(r => r.materialization_status === "UPDATED").length;
  const attentionCount = rules.filter(r => r.materialization_status === "ERROR" || (r.materialization_status !== "UPDATED" && r.materialization_status !== "EMPTY")).length;

  return <Stack spacing={2}>
    <>
    <Box>
      <Typography variant="h6">Materialização de modelos</Typography>
      <Typography variant="body2" color="text.secondary">
        Publica os cubos analíticos a partir dos dados preparados de custos, planejamento, projeções e rateios.
        Reprocesse todos os cubos ou apenas os selecionados.
      </Typography>
    </Box>

    {error && <Alert severity="error">{dataDisplayText(error)}</Alert>}

    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }} justifyContent="space-between">
        <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
          <Box>
            <Typography variant="caption" color="text.secondary">Modelos</Typography>
            <Typography variant="h6">{rules.length}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Em dia</Typography>
            <Typography variant="h6">{updatedCount}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Exigem atenção</Typography>
            <Typography variant="h6" color={attentionCount > 0 ? "warning.main" : "text.primary"}>{attentionCount}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Marcados</Typography>
            <Typography variant="h6">{selectedIds.size}</Typography>
          </Box>
        </Stack>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="text" startIcon={allSelected ? <CheckBoxOutlined /> : <CheckBoxOutlineBlankOutlined />} disabled={running || rules.length === 0} onClick={toggleAll}>
            {allSelected ? "Desmarcar seleção" : "Selecionar todos"}
          </Button>
          <Button variant="outlined" startIcon={<PlayArrowOutlined />} disabled={running || selectedIds.size === 0} onClick={startSelected}>
            Reprocessar selecionados ({selectedIds.size})
          </Button>
          <Button variant="contained" startIcon={<PlayArrowOutlined />} disabled={running || rules.length === 0} onClick={startAll}>
            Reprocessar todos
          </Button>
        </Stack>
      </Stack>
    </Paper>

    {rules.length === 0 ? <Typography color="text.secondary">Nenhum modelo analítico configurado.</Typography> : <Stack spacing={1}>
      {rules.map(r => {
        const selected = r.id != null && selectedIds.has(r.id);
        return <Box
          key={r.id}
          onClick={() => r.id != null && toggleRule(r.id)}
          sx={{
            display: "flex", alignItems: "center", gap: 1.5, p: 1.5, cursor: "pointer",
            border: "1px solid", borderColor: selected ? "primary.main" : "divider", borderRadius: 1,
            bgcolor: selected ? "action.selected" : "transparent",
          }}
        >
          <Checkbox size="small" checked={selected} onChange={() => r.id != null && toggleRule(r.id)} onClick={e => e.stopPropagation()} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} noWrap>{r.name}</Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {r.code} · {r.materialization_table || "Pendente"} · {r.materialization_rows ?? 0} linhas
            </Typography>
          </Box>
          {statusChip(r)}
        </Box>;
      })}
    </Stack>}
    </>
  </Stack>;
}
