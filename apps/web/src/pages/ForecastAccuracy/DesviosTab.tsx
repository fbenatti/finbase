import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import Plot from "@/components/Plot";
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Paper, Select, Stack, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add, Block, Edit, Remove } from "@mui/icons-material";
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
  return (
    <Box sx={{ mb: 3 }}>
      <Typography variant="subtitle1" fontWeight={700} sx={{ color, mb: 1 }}>{title}</Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Departamento (último nível)</TableCell>
              <TableCell align="right">Projetado</TableCell>
              <TableCell align="right">Realizado</TableCell>
              <TableCell align="right">Delta (R$)</TableCell>
              <TableCell align="right">Delta (%)</TableCell>
              <TableCell align="right">Acurácia</TableCell>
              <TableCell align="center">Ack</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map(item => {
              const key = item.path.join(" > ");
              const open = expanded === key;
              const ack = ackByPath.get(key);
              return (
                <FragmentRow key={key} item={item} color={color} open={open} ack={ack}
                  onToggle={() => onToggle(key)} subcats={subcats[key]} subcatLoading={subcatLoading && open}
                  onAck={() => onAck(item)} onSubcatSeries={sub => onSubcatSeries(item, sub)}
                  onDeactivateAck={() => ack && onDeactivateAck(ack)} />
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

function FragmentRow({ item, color, open, onToggle, subcats, subcatLoading, onAck, ack, onSubcatSeries, onDeactivateAck }: {
  item: ForecastDesvioItem; color: string; open: boolean; onToggle: () => void;
  subcats: ForecastDesvioSubcat[] | undefined; subcatLoading: boolean; onAck: () => void;
  ack: ForecastAck | undefined; onSubcatSeries: (sub: ForecastDesvioSubcat) => void;
  onDeactivateAck: () => void;
}) {
  return (
    <>
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer", bgcolor: ack ? "#EFF6FF" : undefined }}>
        <TableCell>
          <Stack direction="row" alignItems="center" gap={0.5} sx={{ flexWrap: "wrap" }}>
            <IconButton size="small" onClick={event => { event.stopPropagation(); onToggle(); }} aria-label={open ? "recolher" : "expandir"}>
              {open ? <Remove fontSize="small" /> : <Add fontSize="small" />}
            </IconButton>
            <Typography variant="body2" fontWeight={600}>{item.name}</Typography>
            <Tooltip title={item.path.join(" > ")}>
              <Chip size="small" variant="outlined" label={item.path.join(" > ")} sx={{ maxWidth: 260 }} />
            </Tooltip>
            {ack && <Chip size="small" color="info" label={`✓ Ack: ${ack.category}`} />}
          </Stack>
          {ack && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", pl: 4, maxWidth: 520 }}>
              {ack.reason}
            </Typography>
          )}
        </TableCell>
        <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
        <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
        <TableCell align="right" sx={{ color, fontWeight: 600 }}>{money.format(Number(item.variance || 0))}</TableCell>
        <TableCell align="right">{pct(item.variance_pct)}</TableCell>
        <TableCell align="right">{pct(item.accuracy)}</TableCell>
        <TableCell align="center">
          {ack ? (
            <Stack direction="row" alignItems="center" justifyContent="center">
              <Tooltip title="Editar Ack">
                <IconButton size="small" aria-label={`Editar Ack de ${item.name}`} onClick={event => { event.stopPropagation(); onAck(); }}>
                  <Edit fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="Desativar Ack">
                <IconButton size="small" color="error" aria-label={`Desativar Ack de ${item.name}`} onClick={event => { event.stopPropagation(); onDeactivateAck(); }}>
                  <Block fontSize="small" />
                </IconButton>
              </Tooltip>
            </Stack>
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={event => { event.stopPropagation(); onAck(); }}
            >
              Ack
            </Button>
          )}
        </TableCell>
      </TableRow>
      {open && (
        <TableRow>
          <TableCell colSpan={7} sx={{ bgcolor: "#f8fafc" }}>
            {subcatLoading ? <LinearProgress sx={{ m: 1 }} /> : subcats && subcats.length > 0 ? (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>ServiceSubcategory</TableCell>
                    <TableCell align="right">Projetado</TableCell>
                    <TableCell align="right">Realizado</TableCell>
                    <TableCell align="right">Delta (R$)</TableCell>
                    <TableCell align="right">Acurácia</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {subcats.slice(0, 10).map(sub => (
                    <TableRow key={sub.subcategory}>
                      <TableCell>
                        <Stack direction="row" alignItems="center" gap={0.5}>
                          <IconButton size="small" onClick={() => onSubcatSeries(sub)} aria-label={`Gráfico do período de ${sub.subcategory}`} title="Gráfico do período (Projetado × Realizado dia a dia)">
                            <Add fontSize="small" />
                          </IconButton>
                          <Typography variant="body2">{sub.subcategory}</Typography>
                        </Stack>
                      </TableCell>
                      <TableCell align="right">{money.format(Number(sub.forecast_value || 0))}</TableCell>
                      <TableCell align="right">{money.format(Number(sub.actual_value || 0))}</TableCell>
                      <TableCell align="right" sx={{ color: Number(sub.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>
                        {money.format(Number(sub.variance || 0))}
                      </TableCell>
                      <TableCell align="right">{pct(sub.accuracy)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Typography variant="body2" color="text.secondary">Sem subcategorias para este nível.</Typography>
            )}
          </TableCell>
        </TableRow>
      )}
    </>
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
      setMessage(`Ack de ${deactivateTarget.department_name} desativado — não aparece mais nos dashboards.`);
      setDeactivateTarget(null);
      await loadAcks(targetMonth);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao desativar o Ack");
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
        setMessage(`Ack de ${ackTarget.name} editado — versão anterior preservada no histórico.`);
      } else {
        await createForecastAck({
          run_id: runId, target_month: targetMonth, department: ackTarget.path.join(" > "),
          category: ackCategory, reason: ackReason.trim(),
        });
        setMessage(`Ack registrado para ${ackTarget.name} (${ackCategory}).`);
      }
      setAckTarget(null);
      setAckEditing(null);
      setAckReason("");
      await loadAcks(targetMonth);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao salvar Ack");
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
          Desvios do mês selecionado no último nível de cada departamento, respeitando o departamento filtrado.
          <Box component="span" sx={{ color: "#DC2626" }}> Vermelho = subestimou</Box> (realizado acima do projetado — gastou mais) ·{" "}
          <Box component="span" sx={{ color: "#16A34A" }}>verde = superestimou</Box> (realizado abaixo do projetado — gastou menos). Use o <b>+</b> à esquerda do nome
          para detalhar por ServiceSubcategory e o botão <b>Ack</b> para justificar o desvio.
        </Typography>
        <Button variant="outlined" size="small" startIcon={<Add fontSize="small" />} onClick={() => setRegisterOpen(true)}>
          Cadastrar item
        </Button>
      </Stack>
      {loading && !data ? (
        <Box sx={{ py: 6, display: "flex", justifyContent: "center" }}><CircularProgress /></Box>
      ) : runId == null ? (
        <Box sx={{ py: 6, display: "flex", justifyContent: "center", flexDirection: "column", alignItems: "center", gap: 1 }}>
          <CircularProgress size={24} />
          <Typography variant="body2" color="text.secondary">Carregando mês selecionado…</Typography>
        </Box>
      ) : data ? (
        <>
          <DesvioTable title={`Top 10 positivos (subestimou) · ${data.positive.length}`} items={data.positive} color="#DC2626"
            expanded={expanded} onToggle={toggle} subcats={subcats} subcatLoading={subcatLoading} onAck={openAck} ackByPath={ackByPath} onSubcatSeries={openSubcatSeries} onDeactivateAck={setDeactivateTarget} />
          <DesvioTable title={`Top 10 negativos (superestimou) · ${data.negative.length}`} items={data.negative} color="#16A34A"
            expanded={expanded} onToggle={toggle} subcats={subcats} subcatLoading={subcatLoading} onAck={openAck} ackByPath={ackByPath} onSubcatSeries={openSubcatSeries} onDeactivateAck={setDeactivateTarget} />
          {data.positive.length === 0 && data.negative.length === 0 && (
            <Typography variant="body2" color="text.secondary">Sem desvios para o mês/departamento selecionados.</Typography>
          )}
        </>
      ) : null}

      <Dialog open={ackTarget !== null} onClose={() => setAckTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{ackEditing ? `Editar Ack — ${ackTarget?.name}` : `Ack do desvio — ${ackTarget?.name}`}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {ackTarget && ackTarget.path.join(" > ")} — projetado {ackTarget && money.format(Number(ackTarget.forecast_value || 0))} × realizado {ackTarget && money.format(Number(ackTarget.actual_value || 0))} (delta {ackTarget && money.format(Number(ackTarget.variance || 0))}).
          </Typography>
          <FormControl fullWidth sx={{ mb: 2, mt: 1 }}>
            <InputLabel>Categoria</InputLabel>
            <Select label="Categoria" value={ackCategory} onChange={e => setAckCategory(e.target.value)}>
              {ACK_CATEGORIES.map(cat => <MenuItem key={cat} value={cat}>{cat}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            fullWidth multiline minRows={3} label="Motivo do desvio vs forecast do início do mês"
            value={ackReason} onChange={e => setAckReason(e.target.value)}
            placeholder="Ex.: migração de workloads anunciada após o fechamento do forecast..."
          />
          {ackEditing && ackEditing.history && ackEditing.history.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="caption" color="text.secondary" fontWeight={700}>Histórico de edições:</Typography>
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
            {savingAck ? "Salvando..." : ackEditing ? "Salvar edição" : "Registrar Ack"}
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
                    {seriesDialog.data?.target_month || targetMonth} · {seriesDialog.data?.department || department || "Todos os departamentos"}
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
                      <Typography variant="caption" color="text.secondary">Realizado (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{money.format(seriesDialog.data.actual_total)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Delta</Typography>
                      <Typography variant="subtitle1" fontWeight={600} sx={{ color: Number(seriesDialog.data.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>
                        {money.format(seriesDialog.data.variance)} ({pct(seriesDialog.data.variance_pct)})
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Acurácia</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{pct(seriesDialog.data.accuracy)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Desvio começou a crescer</Typography>
                      <Typography variant="subtitle1" fontWeight={600} sx={{ color: seriesDialog.data.daily_deviation_day != null ? "#DC2626" : "#16A34A" }}>
                        {seriesDialog.data.daily_deviation_day != null ? `dia ${seriesDialog.data.daily_deviation_day}` : "não houve (redução)"}
                      </Typography>
                    </Box>
                  </Stack>
                  <Plot
                    data={[
                      {
                        type: "bar",
                        name: "Realizado (dentro do rate diário)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => Math.min(p.actual, p.forecast_rate)),
                        marker: { color: "#16A34A" },
                      },
                      {
                        type: "bar",
                        name: "Excesso do dia (desvio)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => Math.max(0, p.actual - p.forecast_rate)),
                        marker: { color: "#DC2626" },
                      },
                      {
                        type: "scatter",
                        mode: "lines",
                        name: "Projetado (rate diário)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => p.forecast_rate),
                        line: { color: "#2563eb", dash: "dash", width: 2 },
                      },
                      ...(seriesDialog.data.daily_deviation_day != null ? [{
                        type: "scatter" as const,
                        mode: "lines" as const,
                        name: "Desvio começou",
                        x: [seriesDialog.data.daily_deviation_day, seriesDialog.data.daily_deviation_day],
                        y: [0, Math.max(...seriesDialog.data.points.map(p => p.actual))],
                        line: { color: "#DC2626", dash: "dot" as const, width: 1.5 },
                        hovertemplate: "Desvio começou a crescer — dia %{x}<extra></extra>",
                      }] : []),
                    ]}
                    layout={{
                      autosize: true,
                      height: 420,
                      margin: { l: 70, r: 20, t: 40, b: 50 },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      barmode: "stack",
                      xaxis: { title: { text: "Dia do mês" }, dtick: 1 },
                      yaxis: { title: { text: "Valor diário (R$)" }, tickformat: ".2s" },
                      showlegend: true,
                      legend: { orientation: "h", y: -0.18 },
                    }}
                    config={{ displayModeBar: true, responsive: true }}
                    style={{ width: "100%" }}
                    useResizeHandler
                  />
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
                    Consumo diário vs rate projetado (total mensal ÷ dias). A partir do dia{" "}
                    {seriesDialog.data.daily_deviation_day ?? "—"} o realizado do dia passa a ultrapassar o
                    rate projetado — é quando o desvio começa a crescer (excesso empilhado em vermelho).
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
        <DialogTitle>Desativar Ack?</DialogTitle>
        <DialogContent>
          <Typography>
            Desativar o Ack de <strong>{deactivateTarget?.department_name}</strong>? Ele deixará de aparecer nos
            dashboards (Ex.: Visão Esperada) sem perder o registro nem o histórico.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={deactivating} onClick={() => setDeactivateTarget(null)}>Cancelar</Button>
          <Button variant="contained" color="error" disabled={deactivating} onClick={() => void confirmDeactivate()}>
            {deactivating ? "Desativando..." : "Desativar"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
