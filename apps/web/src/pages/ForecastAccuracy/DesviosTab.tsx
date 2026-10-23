import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import Plot from "@/components/Plot";
import { Alert, Box, Button, Chip, CircularProgress, FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography } from "@mui/material";
import { AddOutlined, BlockOutlined, EditOutlined, RemoveOutlined } from "@mui/icons-material";
import {
  ForecastAck, ForecastAccuracyDeltaSeries, ForecastDesvioItem, ForecastDesvios, ForecastDesvioSubcat,
  createForecastAck, deactivateForecastAck, getForecastAccuracyDeltaSeries, getForecastAccuracyDesvios, getForecastAccuracyDesviosSubcategories,
  getForecastAcks, updateForecastAck,
} from "../../services/api";
import AckRegisterDialog from "./AckRegisterDialog";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const ACK_CATEGORIES = ["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"];

function DesvioTable({
  title, items, color, expanded, onToggle, subcats, subcatLoading, onAck, ackByPath, onSubcatSeries, onDeactivateAck,
}: {
  title: string; items: ForecastDesvioItem[]; color: string;
  expanded: string | null; onToggle: (path: string) => void;
  subcats: Record<string, ForecastDesvioSubcat[]>;
  subcatLoading: boolean; onAck: (item: ForecastDesvioItem) => void;
  ackByPath: Map<string, ForecastAck>;
  onSubcatSeries: (item: ForecastDesvioItem, sub: ForecastDesvioSubcat) => void;
  onDeactivateAck: (ack: ForecastAck) => void;
}) {
  if (items.length === 0) return null;
  const maxVariance = Math.max(...items.map(item => Math.abs(Number(item.variance || 0))), 1);
  return (
    <Paper variant="outlined" sx={{ minWidth: 0, overflow: "hidden", borderRadius: 2 }}>
      <Box sx={{ px: 2, py: 1.5, borderBottom: "1px solid", borderColor: "divider", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: color }} />
          <Typography variant="subtitle1" fontWeight={750}>{title}</Typography>
        </Stack>
        <Chip size="small" label={`${items.length} ${items.length === 1 ? "unidade" : "unidades"}`} sx={{ fontWeight: 650 }} />
      </Box>
      <Stack spacing={0}>
        {items.map(item => {
          const key = item.path.join(" > ");
          const open = expanded === key;
          const ack = ackByPath.get(key);
          return (
            <FragmentRow key={key} item={item} color={color} open={open} ack={ack} maxVariance={maxVariance}
              onToggle={() => onToggle(key)} subcats={subcats[key]} subcatLoading={subcatLoading && open}
              onAck={() => onAck(item)} onSubcatSeries={sub => onSubcatSeries(item, sub)}
              onDeactivateAck={() => ack && onDeactivateAck(ack)} />
          );
        })}
      </Stack>
    </Paper>
  );
}

function FragmentRow({ item, color, open, onToggle, subcats, subcatLoading, onAck, ack, onSubcatSeries, onDeactivateAck, maxVariance }: {
  item: ForecastDesvioItem; color: string; open: boolean; onToggle: () => void;
  subcats: ForecastDesvioSubcat[] | undefined; subcatLoading: boolean; onAck: () => void;
  ack: ForecastAck | undefined; onSubcatSeries: (sub: ForecastDesvioSubcat) => void;
  onDeactivateAck: () => void; maxVariance: number;
}) {
  const stop = (event: React.MouseEvent) => event.stopPropagation();
  const variance = Number(item.variance || 0);
  const barWidth = Math.max(4, Math.min(100, Math.abs(variance) / maxVariance * 100));

  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider", bgcolor: ack ? "#F5F9FF" : "background.paper", "&:last-of-type": { borderBottom: 0 } }}>
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onToggle(); } }}
        sx={{ px: 2, py: 1.5, cursor: "pointer", display: "grid", alignItems: "center", gap: 1.5, gridTemplateColumns: { xs: "minmax(0, 1fr) auto", md: "minmax(145px, 1.5fr) repeat(2, minmax(82px, .8fr)) minmax(118px, 1fr) minmax(64px, .55fr) auto" }, "&:hover": { bgcolor: "action.hover" }, "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: -2 } }}
      >
        <Box sx={{ minWidth: 0, gridColumn: { xs: "1 / -1", md: "auto" }, display: "flex", alignItems: "center", gap: 1 }}>
          <IconButton size="small" aria-label={open ? "recolher" : "expandir"} tabIndex={-1}>
            {open ? <RemoveOutlined fontSize="small" /> : <AddOutlined fontSize="small" />}
          </IconButton>
          <Box sx={{ minWidth: 0 }}>
            <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
              <Typography variant="body2" fontWeight={700}>{item.name}</Typography>
              {ack && <Chip size="small" color="info" label={ack.category} />}
            </Stack>
            <Tooltip title={item.path.join(" > ")}>
              <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block", maxWidth: { xs: "70vw", md: 260 } }}>{item.path.join(" › ")}</Typography>
            </Tooltip>
          </Box>
        </Box>
        {[
          { label: "Previsto", value: money.format(Number(item.forecast_value || 0)) },
          { label: "Efetivado", value: money.format(Number(item.actual_value || 0)) },
        ].map(metric => (
          <Box key={metric.label} sx={{ pl: { xs: 5, md: 0 }, minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary">{metric.label}</Typography>
            <Typography variant="body2" fontWeight={600} noWrap>{metric.value}</Typography>
          </Box>
        ))}
        <Box sx={{ pl: { xs: 5, md: 0 }, minWidth: 0 }}>
          <Typography variant="caption" color="text.secondary">Variação</Typography>
          <Typography variant="body2" fontWeight={750} sx={{ color }}>{variance > 0 ? "+" : ""}{money.format(variance)} <Box component="span" sx={{ fontSize: "0.75rem", fontWeight: 500 }}>({pct(item.variance_pct)})</Box></Typography>
          <Box sx={{ mt: 0.5, width: "100%", height: 4, borderRadius: 4, bgcolor: "action.hover", overflow: "hidden" }}>
            <Box sx={{ width: `${barWidth}%`, height: "100%", bgcolor: color }} />
          </Box>
        </Box>
        <Box sx={{ pl: { xs: 5, md: 0 }, minWidth: 0 }}>
          <Typography variant="caption" color="text.secondary">Precisão</Typography>
          <Typography variant="body2" fontWeight={700}>{pct(item.accuracy)}</Typography>
        </Box>
        <Box onClick={stop} sx={{ gridColumn: { xs: "2", md: "auto" }, gridRow: { xs: "1", md: "auto" }, justifySelf: "end" }}>
          {ack ? (
            <Stack direction="row" alignItems="center">
              <Tooltip title="Alterar justificativa"><IconButton size="small" aria-label={`Editar justificativa de ${item.name}`} onClick={onAck}><EditOutlined fontSize="small" /></IconButton></Tooltip>
              <Tooltip title="Desativar justificativa"><IconButton size="small" color="error" aria-label={`Desativar justificativa de ${item.name}`} onClick={onDeactivateAck}><BlockOutlined fontSize="small" /></IconButton></Tooltip>
            </Stack>
          ) : <Button size="small" variant="outlined" onClick={onAck}>Justificar</Button>}
        </Box>
      </Box>
      {ack && <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2, pb: 1.25, pl: { xs: 7, md: 6 } }}>{ack.reason}</Typography>}
      {open && (
        <Box sx={{ bgcolor: "#F7F9FC", px: { xs: 1, md: 2 }, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
          {subcatLoading ? <LinearProgress sx={{ m: 1 }} /> : subcats && subcats.length > 0 ? (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>ServiceSubcategory</TableCell>
                    <TableCell align="right">Previsto</TableCell>
                    <TableCell align="right">Efetivado</TableCell>
                    <TableCell align="right">Variação (R$)</TableCell>
                    <TableCell align="right">Precisão</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {subcats.slice(0, 10).map(sub => (
                    <TableRow key={sub.subcategory}>
                      <TableCell>
                        <Stack direction="row" alignItems="center" gap={0.5}>
                          <IconButton size="small" onClick={() => onSubcatSeries(sub)} aria-label={`Gráfico do período de ${sub.subcategory}`} title="Gráfico do período (Estimado × efetivado dia a dia)">
                            <AddOutlined fontSize="small" />
                          </IconButton>
                          <Typography variant="body2">{sub.subcategory}</Typography>
                        </Stack>
                      </TableCell>
                      <TableCell align="right">{money.format(Number(sub.forecast_value || 0))}</TableCell>
                      <TableCell align="right">{money.format(Number(sub.actual_value || 0))}</TableCell>
                      <TableCell align="right" sx={{ color: Number(sub.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>{money.format(Number(sub.variance || 0))}</TableCell>
                      <TableCell align="right">{pct(sub.accuracy)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : <Typography variant="body2" color="text.secondary">Nenhuma subcategoria neste nível.</Typography>}
        </Box>
      )}
    </Box>
  );
}

export default function DesviosTab({ runId, targetMonth, department }: { runId: number | null; targetMonth: string; department: string }) {
  const [data, setData] = useState<ForecastDesvios | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [subcats, setSubcats] = useState<Record<string, ForecastDesvioSubcat[]>>({});
  const [subcatLoading, setSubcatLoading] = useState(false);
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [ackTarget, setAckTarget] = useState<ForecastDesvioItem | null>(null);
  const [ackEditing, setAckEditing] = useState<ForecastAck | null>(null);
  const [ackCategory, setAckCategory] = useState(ACK_CATEGORIES[0]);
  const [ackReason, setAckReason] = useState("");
  const [savingAck, setSavingAck] = useState(false);
  const [message, setMessage] = useState("");
  // Dialog com o gráfico acumulado do período para um ServiceSubcategory.
  const [seriesDialog, setSeriesDialog] = useState<{ subcategory: string; data: ForecastAccuracyDeltaSeries | null; loading: boolean; error: string } | null>(null);
  // Cadastro livre de um item (Ack) com departamento + data (pode ser futura).
  const [registerOpen, setRegisterOpen] = useState(false);
  // Confirmação de desativação de Ack.
  const [deactivateTarget, setDeactivateTarget] = useState<ForecastAck | null>(null);
  const [deactivating, setDeactivating] = useState(false);

  const confirmDeactivate = async () => {
    if (!deactivateTarget) return;
    setDeactivating(true);
    setError("");
    try {
      await deactivateForecastAck(deactivateTarget.id);
      setMessage(`Justificativa de ${deactivateTarget.department_name} desativado — não aparece mais nos dashboards.`);
      setDeactivateTarget(null);
      await loadAcks(targetMonth);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível desativar a justificativa");
    } finally {
      setDeactivating(false);
    }
  };

  const onRegisterSaved = async (monthKey: string, departmentName: string) => {
    setMessage(`Item cadastrado para ${departmentName} (mês ${monthKey}).`);
    await loadAcks(targetMonth);
  };

  const openSubcatSeries = (item: ForecastDesvioItem, sub: ForecastDesvioSubcat) => {
    if (runId == null) return;
    setSeriesDialog({ subcategory: sub.subcategory, data: null, loading: true, error: "" });
    getForecastAccuracyDeltaSeries(runId, targetMonth || undefined, item.path.join(" > "), sub.subcategory)
      .then(data => setSeriesDialog({ subcategory: sub.subcategory, data, loading: false, error: "" }))
      .catch(e => setSeriesDialog({ subcategory: sub.subcategory, data: null, loading: false, error: e instanceof Error ? e.message : String(e) }));
  };

  const ackByPath = useMemo(() => {
    const map = new Map<string, ForecastAck>();
    acks.forEach(ack => map.set(ack.department_path.join(" > "), ack));
    return map;
  }, [acks]);

  const loadAcks = useCallback(async (month: string) => {
    try {
      const result = await getForecastAcks(month || undefined);
      setAcks(result.items);
    } catch {
      // acks são secundários; falha aqui não bloqueia a lista de desvios
    }
  }, []);

  useEffect(() => {
    if (runId == null) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setExpanded(null);
    setSubcats({});
    getForecastAccuracyDesvios(runId, targetMonth || undefined, department || undefined)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    void loadAcks(targetMonth);
    return () => { cancelled = true; };
  }, [runId, targetMonth, department, loadAcks]);

  const toggle = useCallback((path: string) => {
    setExpanded(current => {
      const next = current === path ? null : path;
      if (next && runId != null && !subcats[next]) {
        setSubcatLoading(true);
        getForecastAccuracyDesviosSubcategories(runId, targetMonth || undefined, next, department || undefined)
          .then(result => setSubcats(prev => ({ ...prev, [next]: result.records })))
          .catch(e => setError(e instanceof Error ? e.message : String(e)))
          .finally(() => setSubcatLoading(false));
      }
      return next;
    });
  }, [runId, targetMonth, subcats]);

  const openAck = (item: ForecastDesvioItem) => {
    const existing = ackByPath.get(item.path.join(" > ")) || null;
    setAckTarget(item);
    setAckEditing(existing);
    setAckCategory(existing?.category || ACK_CATEGORIES[0]);
    setAckReason(existing?.reason || "");
  };

  const saveAck = async () => {
    if (!ackTarget || runId == null || !ackReason.trim()) return;
    setSavingAck(true);
    setError("");
    try {
      if (ackEditing) {
        await updateForecastAck(ackEditing.id, { category: ackCategory, reason: ackReason.trim() });
        setMessage(`Justificativa de ${ackTarget.name} editado — versão anterior preservada no histórico.`);
      } else {
        await createForecastAck({
          run_id: runId, target_month: targetMonth, department: ackTarget.path.join(" > "),
          category: ackCategory, reason: ackReason.trim(),
        });
        setMessage(`Justificativa registrada para ${ackTarget.name} (${ackCategory}).`);
      }
      setAckTarget(null);
      setAckEditing(null);
      setAckReason("");
      await loadAcks(targetMonth);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível gravar a justificativa");
    } finally {
      setSavingAck(false);
    }
  };

  return (
    <Box>
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage("")}>{dataDisplayText(message)}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 2 }} gap={1} flexWrap="wrap">
        <Typography variant="body2" color="text.secondary">
          Variações do mês selecionado no último nível de cada unidade, respeitando a unidade filtrada.
          <Box component="span" sx={{ color: "#DC2626" }}> Vermelho = subestimou</Box> (efetivado acima do projetado — gastou mais) ·{" "}
          <Box component="span" sx={{ color: "#16A34A" }}>verde = superestimou</Box> (efetivado abaixo do projetado — gastou menos). Use o <b>+</b> à esquerda do nome
          para detalhar por ServiceSubcategory e o botão <b>Justificar</b> para explicar a variação.
        </Typography>
        <Button variant="outlined" size="small" startIcon={<AddOutlined fontSize="small" />} onClick={() => setRegisterOpen(true)}>
          Cadastrar item
        </Button>
      </Stack>
      {loading && !data ? (
        <Box sx={{ py: 6, display: "flex", justifyContent: "center" }}><CircularProgress /></Box>
      ) : runId == null ? (
        <Box sx={{ py: 6, display: "flex", justifyContent: "center", flexDirection: "column", alignItems: "center", gap: 1 }}>
          <CircularProgress size={24} />
          <Typography variant="body2" color="text.secondary">Carregando o mês escolhido…</Typography>
        </Box>
      ) : data ? (
        <>
          <DesvioTable title={`Top 10 positivos (subestimou) · ${data.positive.length}`} items={data.positive} color="#DC2626"
            expanded={expanded} onToggle={toggle} subcats={subcats} subcatLoading={subcatLoading} onAck={openAck} ackByPath={ackByPath} onSubcatSeries={openSubcatSeries} onDeactivateAck={setDeactivateTarget} />
          <DesvioTable title={`Top 10 negativos (superestimou) · ${data.negative.length}`} items={data.negative} color="#16A34A"
            expanded={expanded} onToggle={toggle} subcats={subcats} subcatLoading={subcatLoading} onAck={openAck} ackByPath={ackByPath} onSubcatSeries={openSubcatSeries} onDeactivateAck={setDeactivateTarget} />
          {data.positive.length === 0 && data.negative.length === 0 && (
            <Typography variant="body2" color="text.secondary">Sem variações para o mês/unidade selecionados.</Typography>
          )}
        </>
      ) : null}

      <Dialog open={ackTarget !== null} onClose={() => setAckTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{ackEditing ? `Alterar justificativa — ${ackTarget?.name}` : `Justificativa da variação — ${ackTarget?.name}`}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {ackTarget && ackTarget.path.join(" > ")} — projetado {ackTarget && money.format(Number(ackTarget.forecast_value || 0))} × efetivado {ackTarget && money.format(Number(ackTarget.actual_value || 0))} (delta {ackTarget && money.format(Number(ackTarget.variance || 0))}).
          </Typography>
          <FormControl fullWidth sx={{ mb: 2, mt: 1 }}>
            <InputLabel>Categoria</InputLabel>
            <Select label="Categoria" value={ackCategory} onChange={e => setAckCategory(e.target.value)}>
              {ACK_CATEGORIES.map(cat => <MenuItem key={cat} value={cat}>{cat}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            fullWidth multiline minRows={3} label="Justificativa da variação vs Forecast do início do mês"
            value={ackReason} onChange={e => setAckReason(e.target.value)}
            placeholder="Ex.: migração de cargas anunciada após o fechamento da Forecast..."
          />
          {ackEditing && ackEditing.history && ackEditing.history.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="caption" color="text.secondary" fontWeight={700}>Histórico de alterações:</Typography>
              {ackEditing.history.map((h, index) => (
                <Typography key={index} variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  • {h.category}: {h.reason} <Box component="span" sx={{ opacity: 0.7 }}>(em {new Date(h.changed_at).toLocaleString("pt-BR")})</Box>
                </Typography>
              ))}
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setAckTarget(null)}>Cancelar</Button>
          <Button variant="contained" disabled={savingAck || !ackReason.trim()} onClick={() => void saveAck()}>
            {savingAck ? "Gravando..." : ackEditing ? "Gravar edição" : "Registrar justificativa"}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={seriesDialog !== null} onClose={() => setSeriesDialog(null)} maxWidth="lg" fullWidth>
        {seriesDialog && (
          <>
            <DialogTitle>
              <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap">
                <Box>
                  <Typography variant="h6">Período — {seriesDialog.subcategory}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {seriesDialog.data?.target_month || targetMonth} · {seriesDialog.data?.department || department || "Todas as unidades"}
                  </Typography>
                </Box>
                <Button size="small" onClick={() => setSeriesDialog(null)}>Fechar</Button>
              </Stack>
            </DialogTitle>
            <DialogContent dividers>
              {seriesDialog.loading && <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />}
              {seriesDialog.error && <Alert severity="error" sx={{ mb: 1.5 }}>{dataDisplayText(seriesDialog.error)}</Alert>}
              {seriesDialog.data && seriesDialog.data.points.length > 0 && (
                <>
                  <Stack direction="row" spacing={3} sx={{ mb: 2 }} flexWrap="wrap">
                    <Box>
                      <Typography variant="caption" color="text.secondary">Projetado (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{money.format(seriesDialog.data.forecast_total)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Efetivado (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{money.format(seriesDialog.data.actual_total)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Delta</Typography>
                      <Typography variant="subtitle1" fontWeight={600} sx={{ color: Number(seriesDialog.data.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>
                        {money.format(seriesDialog.data.variance)} ({pct(seriesDialog.data.variance_pct)})
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Precisão</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{pct(seriesDialog.data.accuracy)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Início da variação a crescer</Typography>
                      <Typography variant="subtitle1" fontWeight={600} sx={{ color: seriesDialog.data.daily_deviation_day != null ? "#DC2626" : "#16A34A" }}>
                        {seriesDialog.data.daily_deviation_day != null ? `dia ${seriesDialog.data.daily_deviation_day}` : "não houve (redução)"}
                      </Typography>
                    </Box>
                  </Stack>
                  <Plot
                    data={[
                      {
                        type: "bar",
                        name: "Efetivado (dentro do ritmo diário)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => Math.min(p.actual, p.forecast_rate)),
                        marker: { color: "#16A34A" },
                      },
                      {
                        type: "bar",
                        name: "Excesso do dia (variação)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => Math.max(0, p.actual - p.forecast_rate)),
                        marker: { color: "#DC2626" },
                      },
                      {
                        type: "scatter",
                        mode: "lines",
                        name: "Previsto (ritmo diário)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => p.forecast_rate),
                        line: { color: "#0F62FE", dash: "dash", width: 2 },
                      },
                      ...(seriesDialog.data.daily_deviation_day != null ? [{
                        type: "scatter" as const,
                        mode: "lines" as const,
                        name: "Início da variação",
                        x: [seriesDialog.data.daily_deviation_day, seriesDialog.data.daily_deviation_day],
                        y: [0, Math.max(...seriesDialog.data.points.map(p => p.actual))],
                        line: { color: "#DC2626", dash: "dot" as const, width: 1.5 },
                        hovertemplate: "Variação começou a crescer — dia %{x}<extra></extra>",
                      }] : []),
                    ]}
                    layout={{
                      autosize: true,
                      height: 420,
                      margin: { l: 70, r: 20, t: 40, b: 50 },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      barmode: "stack",
                      xaxis: { title: { text: "Dia do período" }, dtick: 1 },
                      yaxis: { title: { text: "Valor diário (R$)" }, tickformat: ".2s" },
                      showlegend: true,
                      legend: { orientation: "h", y: -0.18 },
                    }}
                    config={{ displayModeBar: true, responsive: true }}
                    style={{ width: "100%" }}
                    useResizeHandler
                  />
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
                    Consumo diário vs ritmo previsto (total mensal ÷ dias). A partir do dia{" "}
                    {seriesDialog.data.daily_deviation_day ?? "—"} o efetivado do dia passa a ultrapassar o
                    rate projetado — é quando a variação começa a crescer (excesso empilhado em vermelho).
                  </Typography>
                </>
              )}
            </DialogContent>
          </>
        )}
      </Dialog>

      <AckRegisterDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        defaultDepartment={department}
        onSaved={(monthKey, departmentName) => void onRegisterSaved(monthKey, departmentName)}
      />

      <Dialog open={deactivateTarget !== null} onClose={() => { if (!deactivating) setDeactivateTarget(null); }} maxWidth="xs" fullWidth>
        <DialogTitle>Desativar justificativa?</DialogTitle>
        <DialogContent>
          <Typography>
            Desativar o Justificativa de <strong>{deactivateTarget?.department_name}</strong>? Ele deixará de aparecer nos
            dashboards (Ex.: Visão Esperada) sem perder o registro nem o histórico.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={deactivating} onClick={() => setDeactivateTarget(null)}>Cancelar</Button>
          <Button variant="contained" color="error" disabled={deactivating} onClick={() => void confirmDeactivate()}>
            {deactivating ? "Inativando..." : "Desativar"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
