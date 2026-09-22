import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControl,
  IconButton, InputLabel, MenuItem, Paper, Select, Stack, Tab, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Tabs, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add, Remove } from "@mui/icons-material";
import Plot from "@/components/Plot";
import {
  ForecastAck, ForecastAccuracyDeltaSeries, ForecastDesvioItem, ForecastDesvioSubcat, createForecastAck,
  getForecastAccuracyDeltaSeries, getForecastAccuracyDesvios, getForecastAccuracyDesviosSubcategories,
  getForecastAccuracyMonths, getForecastAcks, updateForecastAck,
} from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const ACK_CATEGORIES = ["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"];
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

function DesvioRow({ item, ack, open, subcategories, loading, onToggle, onOpen }: {
  item: ForecastDesvioItem;
  ack: ForecastAck | undefined;
  open: boolean;
  subcategories: ForecastDesvioSubcat[] | undefined;
  loading: boolean;
  onToggle: () => void;
  onOpen: (subcategory: string) => void;
}) {
  const justified = Boolean(ack && ack.active !== false);
  return <>
    <TableRow hover>
      <TableCell>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <IconButton size="small" aria-label={open ? `Recolher ${item.name}` : `Expandir ${item.name}`} onClick={onToggle}>
            {open ? <Remove fontSize="small" /> : <Add fontSize="small" />}
          </IconButton>
          <Typography variant="body2" fontWeight={600}>{item.name}</Typography>
        </Stack>
        <Tooltip title={item.path.join(" > ")}>
          <Chip
            size="small"
            variant="outlined"
            label={item.path.join(" > ")}
            sx={{ mt: 0.25, maxWidth: 360 }}
          />
        </Tooltip>
      </TableCell>
      <TableCell>
        {justified ? (
          <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
            <Chip size="small" color="info" label={ack?.category} variant="outlined" />
            <Typography variant="body2" sx={{ maxWidth: 300 }}>{ack?.reason}</Typography>
          </Stack>
        ) : (
          <Chip size="small" color="warning" label="Desvio não justificado" variant="outlined" />
        )}
      </TableCell>
      <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
      <TableCell align="right" sx={{ color: Number(item.variance || 0) >= 0 ? "#DC2626" : "#16A34A", fontWeight: 600 }}>
        {money.format(Number(item.variance || 0))}
      </TableCell>
    </TableRow>
    {open && <TableRow>
      <TableCell colSpan={4} sx={{ bgcolor: "#f8fafc" }}>
        {loading ? <Box sx={{ py: 1, display: "flex", justifyContent: "center" }}><CircularProgress size={18} /></Box> : subcategories && subcategories.length > 0 ? (
          <Table size="small">
            <TableHead><TableRow>
              <TableCell>ServiceSubcategory</TableCell>
              <TableCell align="right">Realizado</TableCell>
              <TableCell align="right">Delta (R$)</TableCell>
            </TableRow></TableHead>
            <TableBody>{[...subcategories].sort((a, b) =>
              Number(b.variance || 0) - Number(a.variance || 0) || a.subcategory.localeCompare(b.subcategory)
            ).map(sub => <TableRow key={sub.subcategory}>
              <TableCell><Stack direction="row" alignItems="center" spacing={0.5}>
                <IconButton size="small" aria-label={`Detalhar ${sub.subcategory}`} onClick={() => onOpen(sub.subcategory)}><Add fontSize="small" /></IconButton>
                <Typography variant="body2">{sub.subcategory}</Typography>
              </Stack></TableCell>
              <TableCell align="right">{money.format(Number(sub.actual_value || 0))}</TableCell>
              <TableCell align="right" sx={{ color: Number(sub.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>{money.format(Number(sub.variance || 0))}</TableCell>
            </TableRow>)}</TableBody>
          </Table>
        ) : <Typography variant="body2" color="text.secondary">Sem ServiceSubcategory para este projeto.</Typography>}
      </TableCell>
    </TableRow>}
  </>;
}

function DeviationDialog({ selection, ack, runId, month, onClose, onSaved }: {
  selection: { item: ForecastDesvioItem; subcategory: string } | null;
  ack: ForecastAck | undefined;
  runId: number | null;
  month: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const item = selection?.item ?? null;
  const [tab, setTab] = useState(0);
  const [category, setCategory] = useState(ACK_CATEGORIES[0]);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [series, setSeries] = useState<ForecastAccuracyDeltaSeries | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(false);

  useEffect(() => {
    if (!item) return;
    setTab(0);
    setCategory(ack?.category || ACK_CATEGORIES[0]);
    setReason(ack?.reason || "");
    setError("");
  }, [item, ack]);

  useEffect(() => {
    if (!selection || runId == null) return;
    let cancelled = false;
    setSeries(null);
    setSeriesLoading(true);
    getForecastAccuracyDeltaSeries(runId, month, selection.item.path.join(" > "), selection.subcategory)
      .then(result => { if (!cancelled) setSeries(result); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setSeriesLoading(false); });
    return () => { cancelled = true; };
  }, [selection, runId, month]);

  const save = async () => {
    if (!item || runId == null || !reason.trim()) return;
    setSaving(true);
    setError("");
    try {
      if (ack) {
        await updateForecastAck(ack.id, { category, reason: reason.trim() });
      } else {
        await createForecastAck({ run_id: runId, target_month: month, department: item.path.join(" > "), category, reason: reason.trim() });
      }
      await onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={selection !== null} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Typography variant="h6">{selection?.subcategory}</Typography>
        <Typography variant="body2" color="text.secondary">{item?.path.join(" > ")}</Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Tabs value={tab} onChange={(_, value: number) => setTab(value)} sx={{ mb: 2 }}>
          <Tab label="Forecast vs realizado" />
          <Tab label="Reconhecer consumo" />
        </Tabs>
        {tab === 0 && (seriesLoading ? <Box sx={{ py: 5, display: "flex", justifyContent: "center" }}><CircularProgress /></Box> : series && (
          <Plot
            data={[
              {
                type: "bar", name: "Realizado (dentro do rate diário)",
                x: series.points.map(point => point.day), y: series.points.map(point => Math.min(point.actual, point.forecast_rate)),
                marker: { color: "#16A34A" },
              },
              {
                type: "bar", name: "Excesso do dia (desvio)",
                x: series.points.map(point => point.day), y: series.points.map(point => Math.max(0, point.actual - point.forecast_rate)),
                marker: { color: "#DC2626" },
              },
              {
                type: "scatter", mode: "lines", name: "Projetado (rate diário)",
                x: series.points.map(point => point.day), y: series.points.map(point => point.forecast_rate),
                line: { color: "#2563eb", dash: "dash", width: 2 },
              },
              ...(series.daily_deviation_day != null ? [{
                type: "scatter" as const, mode: "lines" as const, name: "Desvio começou",
                x: [series.daily_deviation_day, series.daily_deviation_day],
                y: [0, Math.max(...series.points.map(point => point.actual))],
                line: { color: "#DC2626", dash: "dot" as const, width: 1.5 },
                hovertemplate: "Desvio começou a crescer — dia %{x}<extra></extra>",
              }] : []),
            ]}
            layout={{ height: 420, margin: { l: 70, r: 20, t: 40, b: 50 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", barmode: "stack", xaxis: { title: { text: "Dia do mês" }, dtick: 1 }, yaxis: { title: { text: "Valor diário (R$)" }, tickformat: ".2s" }, showlegend: true, legend: { orientation: "h", y: -0.18 } }}
            config={{ displayModeBar: true, responsive: true }} style={{ width: "100%" }} useResizeHandler
          />
        ))}
        {tab === 1 && (
          <Box sx={{ maxWidth: 640 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {ack ? "Edite o reconhecimento já associado a este desvio." : "Registre o motivo deste consumo para o projeto selecionado."}
            </Typography>
            <FormControl fullWidth sx={{ mb: 2 }}>
              <InputLabel>Categoria</InputLabel>
              <Select label="Categoria" value={category} onChange={event => setCategory(event.target.value)}>
                {ACK_CATEGORIES.map(value => <MenuItem key={value} value={value}>{value}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField fullWidth multiline minRows={4} label="Motivo do desvio" value={reason} onChange={event => setReason(event.target.value)} />
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Fechar</Button>
        {tab === 1 && <Button variant="contained" disabled={saving || !reason.trim()} onClick={() => void save()}>{saving ? "Salvando..." : ack ? "Salvar edição" : "Reconhecer consumo"}</Button>}
      </DialogActions>
    </Dialog>
  );
}

export default function AcksWidget({ department }: { department?: string }) {
  const [month, setMonth] = useState(previousMonth());
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [positive, setPositive] = useState<ForecastDesvioItem[]>([]);
  const [negative, setNegative] = useState<ForecastDesvioItem[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [positiveExpanded, setPositiveExpanded] = useState(false);
  const [negativeExpanded, setNegativeExpanded] = useState(false);
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [subcategories, setSubcategories] = useState<Record<string, ForecastDesvioSubcat[]>>({});
  const [subcategoriesLoading, setSubcategoriesLoading] = useState(false);
  const [selectedDesvio, setSelectedDesvio] = useState<{ item: ForecastDesvioItem; subcategory: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [desviosWarning, setDesviosWarning] = useState("");
  const options = lastMonths(13);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setDesviosWarning("");
    setPositive([]);
    setNegative([]);
    setRunId(null);
    setPositiveExpanded(false);
    setNegativeExpanded(false);
    setExpandedPath(null);
    setSubcategories({});
    // Só acks ativos aparecem no dashboard — desativados ficam fora.
    getForecastAcks(month, department || undefined, true)
      .then(result => { if (!cancelled) setAcks(result.items); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    // Top 5 positivos/negativos dos desvios do mês: os sem ack ativo são
    // marcados como "Desvio não justificado" e aparecem junto aos justificados.
    getForecastAccuracyMonths(undefined, department || undefined, "all")
      .then(months => {
        if (cancelled) return;
        const run = months.find(item => item.target_month === month);
        if (!run) {
          if (!cancelled) setDesviosWarning(`Nenhum simulado de acurácia para ${prettyMonth(month)} — sem Top 5 de desvios.`);
          return;
        }
        setRunId(run.run_id);
        return getForecastAccuracyDesvios(run.run_id, month, department || undefined)
          .then(desvios => {
            if (cancelled) return;
            setPositive(desvios.positive);
            setNegative(desvios.negative);
          })
          .catch(e => { if (!cancelled) setDesviosWarning(e instanceof Error ? e.message : String(e)); });
      })
      .catch(e => { if (!cancelled) setDesviosWarning(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [month, department]);

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
  const showAll = async (direction: "positive" | "negative") => {
    if (!runId) return;
    try {
      const desvios = await getForecastAccuracyDesvios(runId, month, department || undefined, direction, 0);
      if (direction === "positive") {
        setPositive(desvios.positive);
        setPositiveExpanded(true);
      } else {
        setNegative(desvios.negative);
        setNegativeExpanded(true);
      }
    } catch (e) {
      setDesviosWarning(e instanceof Error ? e.message : String(e));
    }
  };
  const showTopFive = async (direction: "positive" | "negative") => {
    if (!runId) return;
    try {
      const desvios = await getForecastAccuracyDesvios(runId, month, department || undefined, direction, 5);
      if (direction === "positive") {
        setPositive(desvios.positive);
        setPositiveExpanded(false);
      } else {
        setNegative(desvios.negative);
        setNegativeExpanded(false);
      }
    } catch (e) {
      setDesviosWarning(e instanceof Error ? e.message : String(e));
    }
  };
  const refreshAcks = async () => {
    const result = await getForecastAcks(month, department || undefined, true);
    setAcks(result.items);
  };
  const toggleSubcategories = async (item: ForecastDesvioItem) => {
    const path = item.path.join(" > ");
    if (expandedPath === path) {
      setExpandedPath(null);
      return;
    }
    setExpandedPath(path);
    if (subcategories[path] || runId == null) return;
    setSubcategoriesLoading(true);
    try {
      const result = await getForecastAccuracyDesviosSubcategories(runId, month, path, department || undefined);
      setSubcategories(current => ({ ...current, [path]: result.records }));
    } catch (e) {
      setDesviosWarning(e instanceof Error ? e.message : String(e));
    } finally {
      setSubcategoriesLoading(false);
    }
  };

  return (
    <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1, flexWrap: "wrap", gap: 1 }}>
        <Box>
          <Typography variant="h6">Acurácia de Forecast</Typography>
          <Typography variant="body2" color="text.secondary">
            Valor projetado no início do mês versus o que efetivamente foi realizado.
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
          {runId && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ color: "#DC2626", mb: 0.5 }}>
                {positiveExpanded ? "Subestimou (gastou mais)" : "Top 5 subestimou (gastou mais)"}
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Departamento</TableCell>
                      <TableCell>Motivo</TableCell>
                      <TableCell align="right">Realizado</TableCell>
                      <TableCell align="right">Delta (R$)</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {positive.map(item => {
                      const path = item.path.join(" > ");
                      return <DesvioRow key={path} item={item} ack={ackByPath.get(path)} open={expandedPath === path}
                        subcategories={subcategories[path]} loading={subcategoriesLoading && expandedPath === path}
                        onToggle={() => void toggleSubcategories(item)} onOpen={subcategory => setSelectedDesvio({ item, subcategory })} />;
                    })}
                    {positive.length === 0 && <TableRow><TableCell colSpan={4}>Nenhum desvio encontrado.</TableCell></TableRow>}
                  </TableBody>
                </Table>
              </TableContainer>
              <Button size="small" sx={{ mt: 0.5 }} onClick={() => void (positiveExpanded ? showTopFive("positive") : showAll("positive"))}>
                {positiveExpanded ? "Ver menos" : "Ver tudo"}
              </Button>
            </Box>
          )}
          {/* Top 5 negativos (superestimou) */}
          {runId && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ color: "#16A34A", mb: 0.5 }}>
                {negativeExpanded ? "Superestimou (gastou menos)" : "Top 5 superestimou (gastou menos)"}
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Departamento</TableCell>
                      <TableCell>Motivo</TableCell>
                      <TableCell align="right">Realizado</TableCell>
                      <TableCell align="right">Delta (R$)</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {negative.map(item => {
                      const path = item.path.join(" > ");
                      return <DesvioRow key={path} item={item} ack={ackByPath.get(path)} open={expandedPath === path}
                        subcategories={subcategories[path]} loading={subcategoriesLoading && expandedPath === path}
                        onToggle={() => void toggleSubcategories(item)} onOpen={subcategory => setSelectedDesvio({ item, subcategory })} />;
                    })}
                    {negative.length === 0 && <TableRow><TableCell colSpan={4}>Nenhum desvio encontrado.</TableCell></TableRow>}
                  </TableBody>
                </Table>
              </TableContainer>
              <Button size="small" sx={{ mt: 0.5 }} onClick={() => void (negativeExpanded ? showTopFive("negative") : showAll("negative"))}>
                {negativeExpanded ? "Ver menos" : "Ver tudo"}
              </Button>
            </Box>
          )}
          {/* Highlights — registros criados manualmente (sem run_id), como alertas ordenados */}
          {orderedManual.length > 0 && (
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
          <DeviationDialog
            selection={selectedDesvio}
            ack={selectedDesvio ? ackByPath.get(selectedDesvio.item.path.join(" > ")) : undefined}
            runId={runId}
            month={month}
            onClose={() => setSelectedDesvio(null)}
            onSaved={refreshAcks}
          />
        </>
      )}
    </Paper>
  );
}
