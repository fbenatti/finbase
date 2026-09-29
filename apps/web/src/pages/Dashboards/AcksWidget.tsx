import { useEffect, useState } from "react";
import Plot from "@/components/Plot";
import {
  Alert, Box, Button, Chip, CircularProgress, Collapse, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Paper, Select, Stack, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add, ArrowDownward, ArrowUpward, EditNote, Remove } from "@mui/icons-material";
import { ForecastAck, ForecastAccuracyDeltaSeries, ForecastDesvioItem, ForecastDesvioSubcat, createForecastAck, getForecastAccuracyDeltaSeries, getForecastAccuracyDesvios, getForecastAccuracyDesviosSubcategories, getForecastAccuracyMonths, getForecastAcks } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pct = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const prettyMonth = (month: string) => `${MONTH_NAMES[Number(month.slice(4, 6)) - 1]}/${month.slice(2, 4)}`;

function previousMonth(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1; // 1-12
  const prev = month === 1 ? { y: year - 1, m: 12 } : { y: year, m: month - 1 };
  return `${prev.y}${String(prev.m).padStart(2, "0")}`;
}

function lastMonths(count: number): string[] {
  const out: string[] = [];
  const now = new Date();
  for (let i = 0; i < count; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    out.push(`${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`);
  }
  return out;
}

function severityColor(severity?: string): "info" | "warning" | "error" | "success" {
  switch ((severity || "").toLowerCase()) {
    case "perigo": return "warning";
    case "crítico":
    case "critico": return "error";
    case "sucesso": return "success";
    default: return "info";
  }
}

// Ordem dos highlights: Crítico > Perigo > Informação > Sucesso; empate por data (mais recente primeiro).
const SEVERITY_ORDER: Record<string, number> = {
  "crítico": 0, "critico": 0, "perigo": 1, "informação": 2, "informacao": 2, "sucesso": 3,
};

function DesvioRow({ item, ack, expanded, subcategories, loading, onToggle, onSubcatSeries, onAck }: { item: ForecastDesvioItem; ack: ForecastAck | undefined; expanded: boolean; subcategories?: ForecastDesvioSubcat[]; loading: boolean; onToggle: () => void; onSubcatSeries: (item: ForecastDesvioItem, subcategory: ForecastDesvioSubcat) => void; onAck: () => void }) {
  const justified = Boolean(ack && ack.active !== false);
  const variance = Number(item.variance || 0);
  const statusLabel = variance > 0 ? "Acima do projetado" : variance < 0 ? "Abaixo do projetado" : "Sem variação";
  return (
    <>
    <TableRow hover>
      <TableCell padding="checkbox"><IconButton size="small" aria-label={`${expanded ? "Fechar" : "Abrir"} detalhe de ${item.name}`} onClick={onToggle}>{expanded ? <Remove fontSize="small" /> : <Add fontSize="small" />}</IconButton></TableCell>
      <TableCell>
        <Typography variant="body2" fontWeight={600}>{item.name}</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", maxWidth: 240 }}>
          {item.path.join(" > ")}
        </Typography>
      </TableCell>
      <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
      <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
      <TableCell align="right" sx={{ color: variance >= 0 ? "#DC2626" : "#16A34A", fontWeight: 600 }}>
        {money.format(variance)}
      </TableCell>
      <TableCell align="right">{pct(item.variance_pct)}</TableCell>
      <TableCell align="center">
        <Tooltip title={statusLabel}>
          <Box component="span" sx={{ display: "inline-flex", color: variance > 0 ? "#DC2626" : variance < 0 ? "#16A34A" : "text.disabled" }}>
            {variance > 0 ? <ArrowUpward fontSize="small" /> : variance < 0 ? <ArrowDownward fontSize="small" /> : <Remove fontSize="small" />}
          </Box>
        </Tooltip>
      </TableCell>
      <TableCell sx={{ minWidth: 220 }}>
        {justified ? (
          <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
            <Chip size="small" color="info" label={ack?.category} variant="outlined" />
            <Typography variant="body2" sx={{ maxWidth: 300 }}>{ack?.reason}</Typography>
          </Stack>
        ) : <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
          <Chip size="small" color="warning" label="Desvio não justificado" variant="outlined" />
          <Tooltip title="Reconhecer desvio">
            <IconButton size="small" onClick={onAck} aria-label="Reconhecer desvio" sx={{ color: "#5B00CB" }}>
              <EditNote fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>}
      </TableCell>
    </TableRow>
    <TableRow><TableCell colSpan={8} sx={{ py: 0, borderBottom: expanded ? undefined : 0 }}><Collapse in={expanded} timeout="auto" unmountOnExit><Box sx={{ py: 1.5, px: 2 }}>
      <Typography variant="subtitle2" fontWeight={700} mb={1}>Detalhe por subcategoria · {item.path.join(" > ")}</Typography>
      {loading ? <CircularProgress size={20} /> : subcategories?.length ? <Table size="small"><TableHead><TableRow><TableCell>Subcategoria</TableCell><TableCell align="right">Projetado</TableCell><TableCell align="right">Realizado</TableCell><TableCell align="right" sx={{ color: "#DC2626" }}>Desvio</TableCell></TableRow></TableHead><TableBody>{subcategories.map((sub) => <TableRow key={sub.subcategory}><TableCell><Stack direction="row" alignItems="center" gap={0.5}><IconButton size="small" onClick={() => onSubcatSeries(item, sub)} aria-label={`Abrir série diária de ${sub.subcategory}`} title="Ver delta dia a dia"><Add fontSize="small" /></IconButton><Typography variant="body2">{sub.subcategory}</Typography></Stack></TableCell><TableCell align="right">{money.format(sub.forecast_value)}</TableCell><TableCell align="right">{money.format(sub.actual_value)}</TableCell><TableCell align="right" sx={{ color: "#DC2626", fontWeight: 600 }}>{money.format(sub.variance)}</TableCell></TableRow>)}</TableBody></Table> : <Typography variant="body2" color="text.secondary">Nenhuma subcategoria encontrada.</Typography>}
    </Box></Collapse></TableCell></TableRow>
    </>
  );
}

export default function AcksWidget({ department, hideManualHighlights = false }: { department?: string; hideManualHighlights?: boolean }) {
  const [month, setMonth] = useState(previousMonth());
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [positive, setPositive] = useState<ForecastDesvioItem[]>([]);
  const [negative, setNegative] = useState<ForecastDesvioItem[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [subcategories, setSubcategories] = useState<Record<string, ForecastDesvioSubcat[]>>({});
  const [subcatLoading, setSubcatLoading] = useState<string | null>(null);
  const [showAllPositive, setShowAllPositive] = useState(false);
  const [showAllNegative, setShowAllNegative] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [desviosWarning, setDesviosWarning] = useState("");
  const [ackTarget, setAckTarget] = useState<ForecastDesvioItem | null>(null);
  const [ackCategory, setAckCategory] = useState("Pico sazonal");
  const [ackReason, setAckReason] = useState("");
  const [savingAck, setSavingAck] = useState(false);
  const [seriesDialog, setSeriesDialog] = useState<{ subcategory: string; data: ForecastAccuracyDeltaSeries | null; loading: boolean; error: string } | null>(null);
  const options = lastMonths(13);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setDesviosWarning("");
    setPositive([]);
    setNegative([]);
    setRunId(null);
    setExpanded(null);
    setSubcategories({});
    setShowAllPositive(false);
    setShowAllNegative(false);
    // Só acks ativos aparecem no dashboard — desativados ficam fora.
    getForecastAcks(month, department || undefined, true)
      .then(result => { if (!cancelled) setAcks(result.items); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    // Top 5 positivos/negativos dos desvios do mês: os sem ack ativo são
    // marcados como "Desvio não justificado" e aparecem junto aos justificados.
    getForecastAccuracyMonths(undefined, department || undefined, "all", false)
      .then(months => {
        if (cancelled) return;
        const run = months.find(item => item.target_month === month);
        if (!run) {
          if (!cancelled) setDesviosWarning(`Nenhum simulado de acurácia para ${prettyMonth(month)} — sem Top 5 de desvios.`);
          return;
        }
        setRunId(run.run_id);
        return getForecastAccuracyDesvios(run.run_id, month, department || undefined, undefined, 0)
          .then(desvios => {
            if (cancelled) return;
            // A visão executiva de forecast é Showback Azure. O endpoint
            // também devolve Chargeback e outros provedores quando o escopo é
            // apenas "Consumo Cloud"; filtre antes do ranking para manter o
            // mesmo universo financeiro exibido no dashboard.
            const inExecutiveScope = (item: ForecastDesvioItem) =>
              item.path[1] === "Showback" && item.path[2] === "Azure";
            setPositive(desvios.positive.filter(inExecutiveScope));
            setNegative(desvios.negative.filter(inExecutiveScope));
          })
          .catch(e => { if (!cancelled) setDesviosWarning(e instanceof Error ? e.message : String(e)); });
      })
      .catch(e => { if (!cancelled) setDesviosWarning(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [month, department]);

  const toggleDetail = (item: ForecastDesvioItem) => {
    const key = item.path.join(" > ");
    if (expanded === key) { setExpanded(null); return; }
    setExpanded(key);
    if (runId == null || subcategories[key]) return;
    setSubcatLoading(key);
    getForecastAccuracyDesviosSubcategories(runId, month, key, department || undefined)
      .then((result) => setSubcategories((current) => ({ ...current, [key]: result.records })))
      .catch((reason) => setDesviosWarning(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setSubcatLoading(null));
  };

  const openSubcatSeries = (item: ForecastDesvioItem, sub: ForecastDesvioSubcat) => {
    if (runId == null) return;
    setSeriesDialog({ subcategory: sub.subcategory, data: null, loading: true, error: "" });
    getForecastAccuracyDeltaSeries(runId, month || undefined, item.path.join(" > "), sub.subcategory)
      .then((data) => setSeriesDialog({ subcategory: sub.subcategory, data, loading: false, error: "" }))
      .catch((error) => setSeriesDialog({ subcategory: sub.subcategory, data: null, loading: false, error: error instanceof Error ? error.message : String(error) }));
  };

  const ackByPath = new Map<string, ForecastAck>();
  acks.forEach(ack => ackByPath.set(ack.department_path.join(" > "), ack));
  // Registros criados manualmente não possuem run_id (cadastro livre).
  const manualAcks = acks.filter(ack => ack.run_id == null);
  // Highlights ordenados: campo `ordem` (menor primeiro), depois severidade e data.
  const orderedManual = [...manualAcks].sort((a, b) => {
    const oa = Number(a.ordem ?? SEVERITY_ORDER[(a.severity || "").toLowerCase()] ?? 2);
    const ob = Number(b.ordem ?? SEVERITY_ORDER[(b.severity || "").toLowerCase()] ?? 2);
    if (oa !== ob) return oa - ob;
    const sa = SEVERITY_ORDER[(a.severity || "").toLowerCase()] ?? 2;
    const sb = SEVERITY_ORDER[(b.severity || "").toLowerCase()] ?? 2;
    if (sa !== sb) return sa - sb;
    const da = a.registered_at || a.created_at || "";
    const db = b.registered_at || b.created_at || "";
    return da < db ? 1 : da > db ? -1 : 0;
  });

  const saveAck = async () => {
    if (!ackTarget || runId == null || !ackReason.trim()) return;
    setSavingAck(true);
    setError("");
    try {
      await createForecastAck({
        run_id: runId,
        target_month: month,
        department: ackTarget.path.join(" > "),
        category: ackCategory,
        reason: ackReason.trim(),
      });
      const result = await getForecastAcks(month, department || undefined, true);
      setAcks(result.items);
      setAckTarget(null);
      setAckReason("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Falha ao registrar o Ack");
    } finally {
      setSavingAck(false);
    }
  };

  return (
    <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1, flexWrap: "wrap", gap: 1 }}>
        <Box>
          <Typography variant="h6">Motivos de desvio (Ack)</Typography>
          <Typography variant="body2" color="text.secondary">
            Desvios reconhecidos no forecast — padrão: mês anterior ao atual
            {department ? ` · filtrado por: ${department}` : ""}.
          </Typography>
        </Box>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel id="acks-month-label">Mês</InputLabel>
          <Select labelId="acks-month-label" label="Mês" value={month} onChange={e => setMonth(e.target.value)}>
            {options.map(m => <MenuItem key={m} value={m}>{prettyMonth(m)}{m === previousMonth() ? " (anterior)" : ""}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError("")}>{error}</Alert>}
      {desviosWarning && !error && <Alert severity="warning" sx={{ mb: 1 }} onClose={() => setDesviosWarning("")}>{desviosWarning}</Alert>}
      {loading ? (
        <Box sx={{ py: 3, display: "flex", justifyContent: "center" }}><CircularProgress size={24} /></Box>
      ) : (
        <>
          {/* Top 5 positivos (subestimou) */}
          {positive.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ color: "#DC2626", mb: 0.5 }}>
                {showAllPositive ? "Subestimou (gastou mais)" : "Top 5 subestimou (gastou mais)"}
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell />
                      <TableCell>Projeto</TableCell>
                      <TableCell align="right">Projetado</TableCell>
                      <TableCell align="right">Realizado</TableCell>
                      <TableCell align="right">Desvio</TableCell>
                      <TableCell align="right">Desvio (%)</TableCell>
                      <TableCell align="center">Status</TableCell>
                      <TableCell>Motivo de desvio</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(showAllPositive ? positive : positive.slice(0, 5)).map(item => (
                      <DesvioRow key={item.path.join(" > ")} item={item} ack={ackByPath.get(item.path.join(" > "))} expanded={expanded === item.path.join(" > ")} subcategories={subcategories[item.path.join(" > ")]} loading={subcatLoading === item.path.join(" > ")} onToggle={() => toggleDetail(item)} onSubcatSeries={openSubcatSeries} onAck={() => setAckTarget(item)} />
                    ))}
                  </TableBody>
              </Table>
              </TableContainer>
              {positive.length > 5 && <Button size="small" onClick={() => setShowAllPositive((value) => !value)}>{showAllPositive ? "Ver menos" : "Ver tudo"}</Button>}
            </Box>
          )}
          {/* Top 5 negativos (superestimou) */}
          {negative.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ color: "#16A34A", mb: 0.5 }}>
                {showAllNegative ? "Superestimou (gastou menos)" : "Top 5 superestimou (gastou menos)"}
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell />
                      <TableCell>Projeto</TableCell>
                      <TableCell align="right">Projetado</TableCell>
                      <TableCell align="right">Realizado</TableCell>
                      <TableCell align="right">Desvio</TableCell>
                      <TableCell align="right">Desvio (%)</TableCell>
                      <TableCell align="center">Status</TableCell>
                      <TableCell>Motivo de desvio</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(showAllNegative ? negative : negative.slice(0, 5)).map(item => (
                      <DesvioRow key={item.path.join(" > ")} item={item} ack={ackByPath.get(item.path.join(" > "))} expanded={expanded === item.path.join(" > ")} subcategories={subcategories[item.path.join(" > ")]} loading={subcatLoading === item.path.join(" > ")} onToggle={() => toggleDetail(item)} onSubcatSeries={openSubcatSeries} onAck={() => setAckTarget(item)} />
                    ))}
                  </TableBody>
              </Table>
              </TableContainer>
              {negative.length > 5 && <Button size="small" onClick={() => setShowAllNegative((value) => !value)}>{showAllNegative ? "Ver menos" : "Ver tudo"}</Button>}
            </Box>
          )}
          {/* Highlights — registros criados manualmente (sem run_id), como alertas ordenados */}
          {!hideManualHighlights && orderedManual.length > 0 && (
            <Box>
              <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
                Highlights
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
                Registros criados manualmente · ordenados por severidade (Crítico → Sucesso)
              </Typography>
              <Stack spacing={1}>
                {orderedManual.map((ack, index) => (
                  <Alert key={ack.id} severity={severityColor(ack.severity)} sx={{ alignItems: "flex-start" }}>
                    <Typography variant="body2" fontWeight={700}>{index + 1}. {ack.department_name}</Typography>
                    <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.25 }}>
                      <Chip size="small" label={ack.severity || "Informação"} />
                      <Chip size="small" label={ack.category} variant="outlined" />
                    </Stack>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>{ack.department_path.join(" > ")}</Typography>
                    <Typography variant="body2" sx={{ mt: 0.5 }}>{ack.reason}</Typography>
                    {ack.registered_at && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                        {new Date(ack.registered_at).toLocaleDateString("pt-BR")}
                      </Typography>
                    )}
                  </Alert>
                ))}
              </Stack>
            </Box>
          )}
        </>
      )}
      <Dialog open={ackTarget !== null} onClose={() => !savingAck && setAckTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Justificar desvio — {ackTarget?.name}</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ mt: 1 }}>
            <Typography variant="body2" color="text.secondary">{ackTarget?.path.join(" > ")}</Typography>
            <FormControl fullWidth>
              <InputLabel>Categoria</InputLabel>
              <Select value={ackCategory} label="Categoria" onChange={(event) => setAckCategory(String(event.target.value))}>
                {["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"].map((category) => <MenuItem key={category} value={category}>{category}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField label="Motivo do Ack" value={ackReason} onChange={(event) => setAckReason(event.target.value)} multiline minRows={4} required autoFocus placeholder="Descreva o motivo do desvio..." />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAckTarget(null)} disabled={savingAck}>Cancelar</Button>
          <Button variant="contained" onClick={() => void saveAck()} disabled={savingAck || !ackReason.trim()}>{savingAck ? "Salvando..." : "Registrar Ack"}</Button>
        </DialogActions>
      </Dialog>
      <Dialog open={seriesDialog !== null} onClose={() => setSeriesDialog(null)} maxWidth="lg" fullWidth>
        {seriesDialog && <>
          <DialogTitle>Período — {seriesDialog.subcategory}</DialogTitle>
          <DialogContent dividers>
            {seriesDialog.loading && <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />}
            {seriesDialog.error && <Alert severity="error" sx={{ mb: 1.5 }}>{seriesDialog.error}</Alert>}
            {seriesDialog.data && <>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>{prettyMonth(seriesDialog.data.target_month)} · {seriesDialog.data.department || department || "Todos os departamentos"}</Typography>
              <Stack direction="row" spacing={3} sx={{ mb: 2 }} flexWrap="wrap">
                <Box><Typography variant="caption" color="text.secondary">Projetado</Typography><Typography fontWeight={600}>{money.format(seriesDialog.data.forecast_total)}</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Realizado</Typography><Typography fontWeight={600}>{money.format(seriesDialog.data.actual_total)}</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Delta</Typography><Typography fontWeight={600} color={seriesDialog.data.variance >= 0 ? "error.main" : "success.main"}>{money.format(seriesDialog.data.variance)} ({pct(seriesDialog.data.variance_pct)})</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Acurácia</Typography><Typography fontWeight={600}>{pct(seriesDialog.data.accuracy)}</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Desvio diário</Typography><Typography fontWeight={600}>{seriesDialog.data.daily_deviation_day != null ? `dia ${seriesDialog.data.daily_deviation_day}` : "não houve"}</Typography></Box>
              </Stack>
              {seriesDialog.data.points.length ? <Plot data={[
                { type: "bar", name: "Realizado até o rate", x: seriesDialog.data.points.map((point) => point.day), y: seriesDialog.data.points.map((point) => Math.min(point.actual, point.forecast_rate)), marker: { color: "#16A34A" }, hovertext: seriesDialog.data.points.map((point) => `Dia ${point.day}<br>Realizado total: R$ ${money.format(point.actual)}<br>Rate projetado: R$ ${money.format(point.forecast_rate)}`), hovertemplate: "%{hovertext}<extra>Realizado</extra>" },
                { type: "bar", name: "Excesso do dia", x: seriesDialog.data.points.map((point) => point.day), y: seriesDialog.data.points.map((point) => Math.max(0, point.actual - point.forecast_rate)), marker: { color: "#DC2626" }, hovertext: seriesDialog.data.points.map((point) => `Dia ${point.day}<br>Realizado total: R$ ${money.format(point.actual)}<br>Rate projetado: R$ ${money.format(point.forecast_rate)}<br>Excesso: R$ ${money.format(Math.max(0, point.actual - point.forecast_rate))}`), hovertemplate: "%{hovertext}<extra>Desvio</extra>" },
                { type: "scatter", mode: "lines", name: "Rate projetado", x: seriesDialog.data.points.map((point) => point.day), y: seriesDialog.data.points.map((point) => point.forecast_rate), line: { color: "#2563eb", dash: "dash", width: 2 }, hovertext: seriesDialog.data.points.map((point) => `Dia ${point.day}<br>Rate projetado: R$ ${money.format(point.forecast_rate)}`), hovertemplate: "%{hovertext}<extra>Projetado</extra>" },
                ...(seriesDialog.data.daily_deviation_day != null ? [{ type: "scatter" as const, mode: "lines" as const, name: "Desvio começou", x: [seriesDialog.data.daily_deviation_day, seriesDialog.data.daily_deviation_day], y: [0, Math.max(...seriesDialog.data.points.map((point) => point.actual))], line: { color: "#DC2626", dash: "dot" as const, width: 1.5 }, hovertemplate: `Desvio começou a crescer — dia ${seriesDialog.data.daily_deviation_day}<extra></extra>` }] : []),
              ]} layout={{ autosize: true, height: 420, margin: { l: 70, r: 20, t: 40, b: 50 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", barmode: "stack", xaxis: { title: { text: "Dia do mês" }, dtick: 1 }, yaxis: { title: { text: "Valor diário (R$)" }, tickformat: ".2s" }, showlegend: true, legend: { orientation: "h", y: -0.18 } }} config={{ displayModeBar: true, responsive: true }} style={{ width: "100%" }} useResizeHandler /> : <Typography color="text.secondary">Sem pontos diários para este item.</Typography>}
              <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>Consumo diário vs rate projetado (total mensal ÷ dias). A partir do dia {seriesDialog.data.daily_deviation_day ?? "—"} o realizado do dia passa a ultrapassar o rate projetado — é quando o desvio começa a crescer (excesso empilhado em vermelho).</Typography>
            </>}
          </DialogContent>
        </>}
      </Dialog>
    </Paper>
  );
}
