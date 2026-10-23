import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, FormControl, IconButton, InputLabel, MenuItem, Paper, Select, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { AddOutlined, BlockOutlined, EditOutlined } from "@mui/icons-material";
import {
  ForecastAck, ForecastDesvioItem, ForecastDesvios,
  createForecastAck, deactivateForecastAck, getForecastAccuracyDesvios, formatApiError,
  getForecastAcks, updateForecastAck,
} from "../../services/api";
import AckRegisterDialog from "./AckRegisterDialog";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const ACK_CATEGORIES = ["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"];

function DesvioTable({ title, items, color, maxVariance, onAck, ackByPath, onDeactivateAck }: {
  title: string; items: ForecastDesvioItem[]; color: string; maxVariance: number;
  onAck: (item: ForecastDesvioItem) => void;
  ackByPath: Map<string, ForecastAck>;
  onDeactivateAck: (ack: ForecastAck) => void;
}) {
  if (items.length === 0) return null;
  return (
    <Paper variant="outlined" sx={{ minWidth: 0, overflow: "hidden", borderRadius: 2 }}>
      <Box sx={{ px: 2, py: 1.5, borderBottom: "1px solid", borderColor: "divider", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: color }} />
          <Typography variant="subtitle1" fontWeight={750}>{title}</Typography>
        </Stack>
        <Chip size="small" label={`${items.length} ${items.length === 1 ? "SKU" : "SKUs"}`} sx={{ fontWeight: 650 }} />
      </Box>
      <Stack spacing={0}>
        {items.map(item => {
          const key = item.path.join(" > ");
          const ack = ackByPath.get(key);
          const variance = Number(item.variance || 0);
          const barWidth = Math.max(4, Math.min(100, Math.abs(variance) / maxVariance * 100));
          const scope = item.path.slice(0, item.path.indexOf("SKU") >= 0 ? item.path.indexOf("SKU") : -1).join(" › ");
          return (
            <Box key={key} sx={{ borderBottom: "1px solid", borderColor: "divider", bgcolor: ack ? "#F5F9FF" : "background.paper", "&:last-of-type": { borderBottom: 0 } }}>
              <Box sx={{ px: 2, py: 1.5, display: "grid", alignItems: "center", gap: 1.5, gridTemplateColumns: { xs: "minmax(0, 1fr) auto", md: "minmax(145px, 1.5fr) repeat(2, minmax(82px, .8fr)) minmax(118px, 1fr) minmax(64px, .55fr) auto" } }}>
                <Box sx={{ minWidth: 0, gridColumn: { xs: "1", md: "auto" } }}>
                  <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
                    <Typography variant="body2" fontWeight={700}>{item.sku || item.name}</Typography>
                    {ack && <Chip size="small" color="info" label={ack.category} />}
                  </Stack>
                  {scope && <Tooltip title={scope}><Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block", maxWidth: { xs: "70vw", md: 260 } }}>{scope}</Typography></Tooltip>}
                </Box>
                {[
                  { label: "Previsto", value: money.format(Number(item.forecast_value || 0)) },
                  { label: "Efetivado", value: money.format(Number(item.actual_value || 0)) },
                ].map(metric => (
                  <Box key={metric.label} sx={{ pl: { xs: 0, md: 0 }, minWidth: 0 }}>
                    <Typography variant="caption" color="text.secondary">{metric.label}</Typography>
                    <Typography variant="body2" fontWeight={600} noWrap>{metric.value}</Typography>
                  </Box>
                ))}
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="caption" color="text.secondary">Variação</Typography>
                  <Typography variant="body2" fontWeight={750} sx={{ color }}>{variance > 0 ? "+" : ""}{money.format(variance)} <Box component="span" sx={{ fontSize: "0.75rem", fontWeight: 500 }}>({pct(item.variance_pct)})</Box></Typography>
                  <Box sx={{ mt: 0.5, width: "100%", height: 4, borderRadius: 4, bgcolor: "action.hover", overflow: "hidden" }}>
                    <Box sx={{ width: `${barWidth}%`, height: "100%", bgcolor: color }} />
                  </Box>
                </Box>
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="caption" color="text.secondary">Precisão</Typography>
                  <Typography variant="body2" fontWeight={700}>{pct(item.accuracy)}</Typography>
                </Box>
                <Box sx={{ gridColumn: { xs: "2", md: "auto" }, gridRow: { xs: "1", md: "auto" }, justifySelf: "end" }}>
                  {ack ? (
                    <Stack direction="row" alignItems="center">
                      <Tooltip title="Alterar justificativa"><IconButton size="small" aria-label={`Editar justificativa de ${item.sku || item.name}`} onClick={() => onAck(item)}><EditOutlined fontSize="small" /></IconButton></Tooltip>
                      <Tooltip title="Desativar justificativa"><IconButton size="small" color="error" aria-label={`Desativar justificativa de ${item.sku || item.name}`} onClick={() => onDeactivateAck(ack)}><BlockOutlined fontSize="small" /></IconButton></Tooltip>
                    </Stack>
                  ) : <Button size="small" variant="outlined" onClick={() => onAck(item)}>Justificar</Button>}
                </Box>
              </Box>
              {ack && <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2, pb: 1.25, pl: { xs: 2, md: 3 } }}>{ack.reason}</Typography>}
            </Box>
          );
        })}
      </Stack>
    </Paper>
  );
}

export default function DesviosTab({ runId, targetMonth, department }: { runId: number | null; targetMonth: string; department: string }) {
  const [data, setData] = useState<ForecastDesvios | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [ackTarget, setAckTarget] = useState<ForecastDesvioItem | null>(null);
  const [ackEditing, setAckEditing] = useState<ForecastAck | null>(null);
  const [ackCategory, setAckCategory] = useState(ACK_CATEGORIES[0]);
  const [ackReason, setAckReason] = useState("");
  const [savingAck, setSavingAck] = useState(false);
  const [message, setMessage] = useState("");
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


  const ackByPath = useMemo(() => {
    const map = new Map<string, ForecastAck>();
    acks.forEach(ack => map.set(ack.department_path.join(" > "), ack));
    return map;
  }, [acks]);
  const displayedItems = [...(data?.positive ?? []), ...(data?.negative ?? [])];
  const acknowledgedDisplayedCount = displayedItems.filter(item => ackByPath.has(item.path.join(" > "))).length;
  const largestDeviation = displayedItems.reduce<ForecastDesvioItem | null>((largest, item) =>
    !largest || Math.abs(Number(item.variance || 0)) > Math.abs(Number(largest.variance || 0)) ? item : largest, null);

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
    getForecastAccuracyDesvios(runId, targetMonth || undefined, department || undefined)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(e => { if (!cancelled) setError(formatApiError(e, "Não foi possível carregar os desvios por SKU")); })
      .finally(() => { if (!cancelled) setLoading(false); });
    void loadAcks(targetMonth);
    return () => { cancelled = true; };
  }, [runId, targetMonth, department, loadAcks]);


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
          Compare os SKUs com maior diferença entre o forecast e o efetivado. A hierarquia acima filtra o escopo da análise.
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
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" }, gap: 1, mb: 2 }}>
            <Paper variant="outlined" sx={{ px: 1.5, py: 1.25, borderRadius: 1.5, borderLeft: "3px solid #DC2626" }}>
              <Typography variant="caption" color="text.secondary">SKUs acima do previsto</Typography>
              <Typography variant="h6" sx={{ lineHeight: 1.25, color: "#B42318" }}>{data.positive.length}</Typography>
            </Paper>
            <Paper variant="outlined" sx={{ px: 1.5, py: 1.25, borderRadius: 1.5, borderLeft: "3px solid #16A34A" }}>
              <Typography variant="caption" color="text.secondary">SKUs abaixo do previsto</Typography>
              <Typography variant="h6" sx={{ lineHeight: 1.25, color: "#15803D" }}>{data.negative.length}</Typography>
            </Paper>
            <Paper variant="outlined" sx={{ px: 1.5, py: 1.25, borderRadius: 1.5 }}>
              <Typography variant="caption" color="text.secondary">Variações justificadas</Typography>
              <Typography variant="h6" sx={{ lineHeight: 1.25 }}>{acknowledgedDisplayedCount} de {displayedItems.length}</Typography>
            </Paper>
          </Box>
          {largestDeviation && (
            <Paper variant="outlined" sx={{ display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 1, mb: 2, borderRadius: 1.5, bgcolor: "action.hover" }}>
              <Typography variant="body2" color="text.secondary">Maior variação exibida</Typography>
              <Typography variant="body2" fontWeight={700}>{largestDeviation.name}</Typography>
              <Typography variant="body2" fontWeight={750} sx={{ color: Number(largestDeviation.variance || 0) >= 0 ? "#B42318" : "#15803D", ml: "auto" }}>
                {Number(largestDeviation.variance || 0) > 0 ? "+" : ""}{money.format(Number(largestDeviation.variance || 0))} ({pct(largestDeviation.variance_pct)})
              </Typography>
            </Paper>
          )}
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", xl: "repeat(2, minmax(0, 1fr))" }, alignItems: "start", gap: 2 }}>
            <DesvioTable title="Acima do previsto · maior gasto" items={data.positive} color="#DC2626" maxVariance={Math.max(...displayedItems.map(item => Math.abs(Number(item.variance || 0))), 1)} onAck={openAck} ackByPath={ackByPath} onDeactivateAck={setDeactivateTarget} />
            <DesvioTable title="Abaixo do previsto · menor gasto" items={data.negative} color="#16A34A" maxVariance={Math.max(...displayedItems.map(item => Math.abs(Number(item.variance || 0))), 1)} onAck={openAck} ackByPath={ackByPath} onDeactivateAck={setDeactivateTarget} />
          </Box>
          {data.positive.length === 0 && data.negative.length === 0 && (
            <Typography variant="body2" color="text.secondary">Sem variações para o mês/unidade selecionados.</Typography>
          )}
        </>
      ) : null}

      <Dialog open={ackTarget !== null} onClose={() => setAckTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{ackEditing ? `Alterar justificativa — ${ackTarget?.name}` : `Justificativa da variação — ${ackTarget?.name}`}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            SKU {ackTarget?.sku || ackTarget?.name} — escopo {ackTarget && ackTarget.path.slice(0, ackTarget.path.indexOf("SKU")).join(" > ")} — projetado {ackTarget && money.format(Number(ackTarget.forecast_value || 0))} × efetivado {ackTarget && money.format(Number(ackTarget.actual_value || 0))} (delta {ackTarget && money.format(Number(ackTarget.variance || 0))}).
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
