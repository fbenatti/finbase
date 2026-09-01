import { useState, useEffect } from "react";
import {
  Box, Typography, Button, Chip, Alert, CircularProgress, Tooltip, Dialog,
  DialogTitle, DialogContent, DialogActions, TextField, FormControl, InputLabel,
  Select, MenuItem, FormHelperText, FormControlLabel, Switch, IconButton,
} from "@mui/material";
import { Schedule, Add, Edit, Delete, PlayArrow } from "@mui/icons-material";
import {
  getSchedules, createSchedule, updateSchedule, deleteSchedule, runScheduleNow,
  ScheduledTask, SourceConnection, formatApiError,
} from "../../services/api";
import SortableTable from "../../components/SortableTable";

const cronPresets = [
  { label: "Diário 03:00", value: "0 3 * * *" },
  { label: "Diário 06:00", value: "0 6 * * *" },
  { label: "Semanal (seg 03:00)", value: "0 3 * * 1" },
  { label: "Mensal (dia 1 03:00)", value: "0 3 1 * *" },
  { label: "A cada 12h", value: "0 */12 * * *" },
  { label: "A cada 6h", value: "0 */6 * * *" },
];

interface SchedForm {
  name: string;
  connection_id: number;
  connection_ids: number[];
  cron_expression: string;
  timezone: string;
  destination_reprocess: boolean;
  active: boolean;
  forecast_after_ingestion: boolean;
  forecast_config_id: string;
}

const EMPTY_FORM: SchedForm = {
  name: "", connection_id: 0, connection_ids: [],
  cron_expression: "0 3 * * *", timezone: "America/Sao_Paulo",
  destination_reprocess: true, active: true,
  forecast_after_ingestion: false, forecast_config_id: "",
};

export default function SchedulesPanel({ connections }: { connections: SourceConnection[] }) {
  const [schedules, setSchedules] = useState<ScheduledTask[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduledTask | null>(null);
  const [form, setForm] = useState<SchedForm>(EMPTY_FORM);
  const [running, setRunning] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  const loadSchedules = () => {
    getSchedules().then(setSchedules).catch((e) => setError(formatApiError(e, "Falha ao carregar agendamentos")));
  };

  useEffect(() => { loadSchedules(); }, []);

  const openNew = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM });
    setDialogOpen(true);
  };

  const openEdit = (s: ScheduledTask) => {
    setEditing(s);
    const cfg = s.config_json || {};
    const ids = Array.isArray(s.connection_ids) && s.connection_ids.length ? s.connection_ids : [s.connection_id];
    setForm({
      name: s.name, connection_id: s.connection_id, connection_ids: ids,
      cron_expression: s.cron_expression, timezone: s.timezone,
      destination_reprocess: s.destination_reprocess, active: s.active,
      forecast_after_ingestion: Boolean(cfg.forecast_after_ingestion),
      forecast_config_id: String(cfg.forecast_config_id || ""),
    });
    setDialogOpen(true);
  };

  const save = async () => {
    try {
      const payload = {
        ...form,
        connection_id: form.connection_id,
        connection_ids: form.connection_ids.length ? form.connection_ids : [form.connection_id],
        config_json: {
          forecast_after_ingestion: form.forecast_after_ingestion,
          forecast_config_id: form.forecast_config_id ? Number(form.forecast_config_id) : null,
        },
      };
      if (editing) {
        await updateSchedule(editing.id, payload);
      } else {
        await createSchedule(payload);
      }
      setDialogOpen(false);
      loadSchedules();
    } catch (e: unknown) {
      setError(formatApiError(e, "Falha ao salvar agendamento"));
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Remover agendamento?")) return;
    try { await deleteSchedule(id); loadSchedules(); } catch (e: unknown) {
      setError(formatApiError(e, "Falha ao remover"));
    }
  };

  const runNow = async (id: number) => {
    setRunning(prev => new Set(prev).add(id));
    try {
      await runScheduleNow(id);
      setError("");
    } catch (e: unknown) {
      setError(formatApiError(e, "Falha ao executar agendamento"));
    } finally {
      setRunning(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  };

  const formatNextRun = (s: ScheduledTask): string => {
    if (!s.next_run_at) return "—";
    return new Date(s.next_run_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const formatLastRun = (s: ScheduledTask): string => {
    if (!s.last_run_at) return "Nunca";
    return new Date(s.last_run_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
        <Typography variant="h5" sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Schedule /> Agendamentos
        </Typography>
        <Button size="small" variant="outlined" startIcon={<Add />} onClick={openNew}>
          Novo Agendamento
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

      {schedules.length === 0 ? (
        <Alert severity="info" sx={{ mb: 2 }}>
          Nenhum agendamento configurado. Clique em "Novo Agendamento" para automatizar a extração e o reprocessamento da tabela destino.
        </Alert>
      ) : (
        <SortableTable
          data={schedules}
          getRowKey={(s) => s.id}
          defaultSortKey="created_at"
          defaultSortDir="desc"
          columns={[
            {
              key: "name", label: "Nome",
              render: (s: ScheduledTask) => (
                <Box>
                  <Typography variant="body2" fontWeight={500}>{s.name}</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                    {s.cron_expression}
                  </Typography>
                </Box>
              ),
            },
            {
              key: "connection_id", label: "Conexoes", width: 200,
              render: (s: ScheduledTask) => {
                const ids = Array.isArray(s.connection_ids) && s.connection_ids.length ? s.connection_ids : [s.connection_id];
                const codes = ids.map(id => connections.find(c => c.id === id)?.code || `ID ${id}`);
                return codes.length === 1 ? codes[0] : codes.join(", ");
              },
            },
            {
              key: "_frequency", label: "Frequencia", width: 130,
              render: (s: ScheduledTask) => {
                const preset = cronPresets.find(p => p.value === s.cron_expression);
                return preset?.label || s.cron_expression;
              },
            },
            {
              key: "next_run_at", label: "Proxima", width: 130,
              render: (s: ScheduledTask) => (
                <Chip
                  label={formatNextRun(s)}
                  size="small"
                  color={s.active ? "primary" : "default"}
                  variant={s.active ? "filled" : "outlined"}
                />
              ),
            },
            {
              key: "last_run_at", label: "Ultima", width: 130,
              render: (s: ScheduledTask) => formatLastRun(s),
            },
            {
              key: "active", label: "Status", width: 90,
              render: (s: ScheduledTask) => (
                <Chip
                  label={s.active ? "Ativo" : "Inativo"}
                  size="small"
                  color={s.active ? "success" : "default"}
                />
              ),
            },
            {
              key: "destination_reprocess", label: "Reprocessar", width: 110,
              render: (s: ScheduledTask) => (
                <Chip
                  label={s.destination_reprocess ? "Sim" : "Nao"}
                  size="small"
                  variant="outlined"
                  color={s.destination_reprocess ? "info" : "default"}
                />
              ),
            },
            {
              key: "_actions", label: "Acoes", width: 140, sortable: false,
              render: (s: ScheduledTask) => (
                <Box sx={{ display: "flex", gap: 0.5 }}>
                  <Tooltip title="Executar agora">
                    <IconButton
                      size="small" color="primary"
                      onClick={() => runNow(s.id)}
                      disabled={running.has(s.id)}
                    >
                      {running.has(s.id) ? <CircularProgress size={16} /> : <PlayArrow fontSize="small" />}
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Editar">
                    <IconButton size="small" onClick={() => openEdit(s)}>
                      <Edit fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Remover">
                    <IconButton size="small" color="error" onClick={() => remove(s.id)}>
                      <Delete fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              ),
            },
          ]}
        />
      )}

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? "Editar" : "Novo"} Agendamento</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome" value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })} />
            <FormControl size="small">
              <InputLabel>Conexoes (uma ou varias)</InputLabel>
              <Select
                multiple
                value={form.connection_ids.length ? form.connection_ids : [form.connection_id]}
                label="Conexoes (uma ou varias)"
                onChange={e => {
                  const ids = e.target.value as number[];
                  setForm({ ...form, connection_ids: ids, connection_id: ids[0] || 0 });
                }}
                renderValue={(selected) => {
                  const sel = selected as number[];
                  const names = sel.map(id => connections.find(c => c.id === id)?.code || `ID ${id}`);
                  return names.length ? names.join(", ") : "—";
                }}
              >
                {connections.map(c => (
                  <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
                ))}
              </Select>
              <FormHelperText>Selecione todas as origens que este agendamento deve coletar.</FormHelperText>
            </FormControl>

            <Typography variant="subtitle2" sx={{ mt: 1 }}>Expressao Cron</Typography>
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
              {cronPresets.map(p => (
                <Chip
                  key={p.value}
                  label={p.label}
                  size="small"
                  variant={form.cron_expression === p.value ? "filled" : "outlined"}
                  color={form.cron_expression === p.value ? "primary" : "default"}
                  onClick={() => setForm({ ...form, cron_expression: p.value })}
                  sx={{ cursor: "pointer" }}
                />
              ))}
            </Box>
            <TextField label="Expressao Cron (custom)" value={form.cron_expression}
              onChange={e => setForm({ ...form, cron_expression: e.target.value })}
              helperText="Formato: minuto hora dia mes dia_semana. Ex: 0 3 * * * (diario as 3h)" />

            <FormControlLabel
              control={<Switch checked={form.destination_reprocess}
                onChange={e => setForm({ ...form, destination_reprocess: e.target.checked })} />}
              label="Auto-reprocessar tabela destino apos extracao"
            />
            <FormControlLabel
              control={<Switch checked={form.forecast_after_ingestion}
                onChange={e => setForm({ ...form, forecast_after_ingestion: e.target.checked })} />}
              label="Executar Forecast após ingestão e reprocessamento"
            />
            {form.forecast_after_ingestion && <TextField label="ID da configuração Forecast" type="number" value={form.forecast_config_id}
              onChange={e => setForm({ ...form, forecast_config_id: e.target.value })}
              helperText="Crie a configuração no menu Forecast e informe o ID exibido." />}
            <FormControlLabel
              control={<Switch checked={form.active}
                onChange={e => setForm({ ...form, active: e.target.checked })} />}
              label="Ativo"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={save} variant="contained" disabled={!form.name || form.connection_ids.length === 0}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
