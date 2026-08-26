import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton,
  InputLabel, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add, Block, Check, Edit } from "@mui/icons-material";
import {
  ForecastAck, activateForecastAck, deactivateForecastAck, getForecastAcks, updateForecastAck,
} from "../../services/api";
import AckRegisterDialog from "./AckRegisterDialog";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const ACK_CATEGORIES = ["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"];

export default function AcksRegisteredTab({ defaultMonth, department }: { defaultMonth: string; department: string }) {
  const [month, setMonth] = useState(defaultMonth);
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [registerOpen, setRegisterOpen] = useState(false);
  const [message, setMessage] = useState("");
  // Edição de um Ack existente.
  const [editTarget, setEditTarget] = useState<ForecastAck | null>(null);
  const [editCategory, setEditCategory] = useState(ACK_CATEGORIES[0]);
  const [editReason, setEditReason] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  // Confirmação de desativação.
  const [deactivateTarget, setDeactivateTarget] = useState<ForecastAck | null>(null);
  const [togglingActive, setTogglingActive] = useState(false);

  const load = useCallback(async (monthKey: string, dept: string) => {
    setLoading(true);
    setError("");
    try {
      const result = await getForecastAcks(monthKey || undefined, dept || undefined);
      setAcks(result.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Recarrega quando muda o mês ou o departamento selecionado.
  useEffect(() => {
    const monthKey = month ? month.slice(0, 7).replace("-", "") : "";
    void load(monthKey, department);
  }, [month, department, load]);

  // O mês é derivado da data selecionada (YYYYMM) — mesma regra do mês realizado.
  const monthKey = month ? month.slice(0, 7).replace("-", "") : "";

  const openEdit = (ack: ForecastAck) => {
    setEditTarget(ack);
    setEditCategory(ack.category || ACK_CATEGORIES[0]);
    setEditReason(ack.reason || "");
  };

  const saveEdit = async () => {
    if (!editTarget || !editReason.trim()) return;
    setSavingEdit(true);
    setError("");
    try {
      await updateForecastAck(editTarget.id, { category: editCategory, reason: editReason.trim() });
      setMessage(`Ack de ${editTarget.department_name} editado — versão anterior preservada no histórico.`);
      setEditTarget(null);
      await load(monthKey, department);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao salvar Ack");
    } finally {
      setSavingEdit(false);
    }
  };

  const toggleActive = async () => {
    if (!deactivateTarget) return;
    setTogglingActive(true);
    setError("");
    try {
      const target = deactivateTarget;
      if (target.active === false) {
        await activateForecastAck(target.id);
        setMessage(`Ack de ${target.department_name} reativado.`);
      } else {
        await deactivateForecastAck(target.id);
        setMessage(`Ack de ${target.department_name} desativado — não aparece mais nos dashboards.`);
      }
      setDeactivateTarget(null);
      await load(monthKey, department);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao alterar o Ack");
    } finally {
      setTogglingActive(false);
    }
  };

  // Merge: acks vinculados a desvios identificados (run_id preenchido, criados
  // pelo botão "Ack" nas abas Desvios/Top 5) não se repetem aqui. Esta lista
  // exibe somente itens manuais (cadastro livre sem run_id).
  const manualAcks = acks.filter(ack => ack.run_id == null);

  return (
    <Box>
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage("")}>{message}</Alert>}
      <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }} flexWrap="wrap">
        <TextField
          label="Data (mês)"
          type="date"
          value={month}
          onChange={e => setMonth(e.target.value)}
          InputLabelProps={{ shrink: true }}
          sx={{ width: 200 }}
          helperText="Pode ser futura — o mês segue a regra do mês realizado (YYYYMM)."
        />
        <Button size="small" variant="outlined" onClick={() => void load(monthKey, department)}>Atualizar</Button>
        <Chip size="small" variant="outlined" label={`Mês: ${monthKey || "—"}`} />
        <Box sx={{ flexGrow: 1 }} />
        <Button variant="outlined" size="small" startIcon={<Add fontSize="small" />} onClick={() => setRegisterOpen(true)}>
          Cadastrar item
        </Button>
      </Stack>
      {department && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Filtrado pelo departamento selecionado: <b>{department}</b>
        </Typography>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      <Alert severity="info" sx={{ mb: 2 }}>
        Esta lista mostra somente <strong>itens manuais</strong> (cadastrados via "Cadastrar item"), para explicar
        algo que não foi identificado como desvio. Acks vinculados a desvios identificados são justificados
        diretamente nas abas <strong>Desvios</strong> e no <strong>Top 5</strong> — não se repetem aqui.
      </Alert>
      {!loading && acks.length > manualAcks.length && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {acks.length - manualAcks.length} ack(s) vinculado(s) a desvios identificados ocultos desta lista (aparecem nas abas Desvios / Top 5).
        </Typography>
      )}
      {loading ? (
        <Typography variant="body2" color="text.secondary">Carregando acks registrados…</Typography>
      ) : manualAcks.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Nenhum item manual registrado para o mês {monthKey || "selecionado"}
          {department ? ` e departamento ${department}` : ""}. Use "Cadastrar item" para explicar um desvio
          que não foi identificado automaticamente.
        </Typography>
      ) : (
        <TableContainer component={Paper} elevation={0} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Departamento</TableCell>
                <TableCell>Motivo</TableCell>
                <TableCell align="center">Categoria</TableCell>
                <TableCell align="center">Status</TableCell>
                <TableCell align="right">Data registro</TableCell>
                <TableCell align="right">Projetado</TableCell>
                <TableCell align="right">Realizado</TableCell>
                <TableCell align="right">Delta</TableCell>
                <TableCell align="right">Acurácia</TableCell>
                <TableCell align="center">Ações</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {manualAcks.map(ack => (
                <TableRow key={ack.id} hover sx={{ opacity: ack.active === false ? 0.55 : 1 }}>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>{ack.department_name}</Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ display: "block", maxWidth: 280 }}>
                      {ack.department_path.join(" > ")}
                    </Typography>
                  </TableCell>
                  <TableCell><Typography variant="body2">{ack.reason}</Typography></TableCell>
                  <TableCell align="center"><Chip size="small" label={ack.category} variant="outlined" /></TableCell>
                  <TableCell align="center">
                    {ack.active === false
                      ? <Chip size="small" label="Desativado" color="error" variant="outlined" />
                      : <Chip size="small" label="Ativo" color="success" variant="outlined" />}
                  </TableCell>
                  <TableCell align="right">
                    {ack.registered_at
                      ? new Date(ack.registered_at).toLocaleDateString("pt-BR")
                      : "—"}
                  </TableCell>
                  <TableCell align="right">{money.format(Number(ack.forecast_value || 0))}</TableCell>
                  <TableCell align="right">{money.format(Number(ack.actual_value || 0))}</TableCell>
                  <TableCell align="right" sx={{ color: Number(ack.variance || 0) >= 0 ? "#DC2626" : "#16A34A", fontWeight: 600 }}>
                    {money.format(Number(ack.variance || 0))}
                  </TableCell>
                  <TableCell align="right">{pct(ack.accuracy)}</TableCell>
                  <TableCell align="center">
                    <Stack direction="row" alignItems="center" justifyContent="center">
                      <Tooltip title="Editar Ack">
                        <IconButton size="small" aria-label={`Editar Ack de ${ack.department_name}`} onClick={() => openEdit(ack)}>
                          <Edit fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={ack.active === false ? "Reativar Ack" : "Desativar Ack"}>
                        <IconButton
                          size="small"
                          color={ack.active === false ? "success" : "error"}
                          aria-label={ack.active === false ? `Reativar Ack de ${ack.department_name}` : `Desativar Ack de ${ack.department_name}`}
                          onClick={() => setDeactivateTarget(ack)}
                        >
                          {ack.active === false ? <Check fontSize="small" /> : <Block fontSize="small" />}
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <AckRegisterDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        defaultDepartment={department}
        onSaved={(monthKeySaved, departmentName) => {
          setMessage(`Item cadastrado para ${departmentName} (mês ${monthKeySaved}).`);
          setMonth(`${monthKeySaved.slice(0, 4)}-${monthKeySaved.slice(4, 6)}-01`);
          void load(monthKeySaved, department);
        }}
      />

      {/* Edição de Ack */}
      <Dialog open={editTarget !== null} onClose={() => setEditTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Editar Ack — {editTarget?.department_name}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {editTarget?.department_path.join(" > ")}
          </Typography>
          <FormControl fullWidth sx={{ mb: 2, mt: 1 }}>
            <InputLabel>Categoria</InputLabel>
            <Select label="Categoria" value={editCategory} onChange={e => setEditCategory(e.target.value)}>
              {ACK_CATEGORIES.map(cat => <MenuItem key={cat} value={cat}>{cat}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            fullWidth multiline minRows={3} label="Motivo do desvio"
            value={editReason} onChange={e => setEditReason(e.target.value)}
            placeholder="Ex.: migração de workloads anunciada após o fechamento do forecast..."
          />
          {editTarget?.history && editTarget.history.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="caption" color="text.secondary" fontWeight={700}>Histórico de edições:</Typography>
              {editTarget.history.map((h, index) => (
                <Typography key={index} variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  • {h.category}: {h.reason} <Box component="span" sx={{ opacity: 0.7 }}>(em {new Date(h.changed_at).toLocaleString("pt-BR")})</Box>
                </Typography>
              ))}
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditTarget(null)}>Cancelar</Button>
          <Button variant="contained" disabled={savingEdit || !editReason.trim()} onClick={() => void saveEdit()}>
            {savingEdit ? "Salvando..." : "Salvar edição"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirmação desativar/reativar */}
      <Dialog open={deactivateTarget !== null} onClose={() => { if (!togglingActive) setDeactivateTarget(null); }} maxWidth="xs" fullWidth>
        <DialogTitle>{deactivateTarget?.active === false ? "Reativar Ack?" : "Desativar Ack?"}</DialogTitle>
        <DialogContent>
          {deactivateTarget?.active === false ? (
            <Typography>
              Reativar o Ack de <strong>{deactivateTarget.department_name}</strong>? Ele voltará a aparecer nos dashboards.
            </Typography>
          ) : (
            <Typography>
              Desativar o Ack de <strong>{deactivateTarget?.department_name}</strong>? Ele deixará de aparecer nos
              dashboards (Ex.: Visão Esperada) sem perder o registro nem o histórico.
            </Typography>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={togglingActive} onClick={() => setDeactivateTarget(null)}>Cancelar</Button>
          <Button
            variant="contained"
            color={deactivateTarget?.active === false ? "success" : "error"}
            disabled={togglingActive}
            onClick={() => void toggleActive()}
          >
            {togglingActive ? "Alterando..." : deactivateTarget?.active === false ? "Reativar" : "Desativar"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
