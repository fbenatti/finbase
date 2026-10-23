import { Dialog, DialogTitle, DialogContent, DialogActions } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { DATA_LAYER_LABELS } from "../../utils/dataPresentation";
import { useState, useEffect } from "react";
import { Box, Typography, Button, Chip, Alert, CircularProgress, Tooltip, TextField, FormControl, InputLabel, Select, MenuItem, FormHelperText, FormControlLabel, Switch, IconButton, Checkbox, Divider } from "@mui/material";
import { AddOutlined, EditOutlined, DeleteOutlined, PlayArrowOutlined } from "@mui/icons-material";
import {
  getSchedules, createSchedule, updateSchedule, deleteSchedule, runScheduleNow,
  ScheduledTask, SourceConnection, formatApiError, getDestinationTables, getForecastConfigs,
  getGoldCubeRules, DestinationTable, ForecastConfig, GoldCubeRule,
  getBackupSizeEstimates,
} from "../../services/api";
import SortableTable from "../../components/SortableTable";

const cronPresets = [
  { label: "Todo dia às 03:00", value: "0 3 * * *" },
  { label: "Todo dia às 06:00", value: "0 6 * * *" },
  { label: "Semanal (seg, às 03:00)", value: "0 3 * * 1" },
  { label: "Mensal (dia 1, às 03:00)", value: "0 3 1 * *" },
  { label: "A cada 12 horas", value: "0 */12 * * *" },
  { label: "A cada 6 horas", value: "0 */6 * * *" },
];

interface SchedForm {
  schedule_type: "backup" | "collection" | "reprocess";
  name: string;
  connection_id: number;
  connection_ids: number[];
  cron_expression: string;
  timezone: string;
  destination_reprocess: boolean;
  active: boolean;
  forecast_after_ingestion: boolean;
  forecast_config_id: string;
  destination_table_ids: number[];
  period_mode: "selected" | "all";
  billing_periods: string;
  forecast_config_ids: number[];
  gold_rule_ids: number[];
  accuracy: boolean;
  shared_cost: boolean;
  backup_layers: ("bronze" | "silver" | "gold")[];
}

const EMPTY_FORM: SchedForm = {
  schedule_type: "collection",
  name: "", connection_id: 0, connection_ids: [],
  cron_expression: "0 3 * * *", timezone: "America/Sao_Paulo",
  destination_reprocess: true, active: true,
  forecast_after_ingestion: false, forecast_config_id: "",
  destination_table_ids: [], period_mode: "all", billing_periods: "",
  forecast_config_ids: [], gold_rule_ids: [], accuracy: true, shared_cost: false,
  backup_layers: ["bronze"],
};

function SectionTitle({ children }: { children: string }) {
  return <Divider textAlign="left"><Typography variant="overline" color="text.secondary">{children}</Typography></Divider>;
}

export default function SchedulesPanel({ connections }: { connections: SourceConnection[] }) {
  const [schedules, setSchedules] = useState<ScheduledTask[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduledTask | null>(null);
  const [form, setForm] = useState<SchedForm>(EMPTY_FORM);
  const [running, setRunning] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [destinations, setDestinations] = useState<DestinationTable[]>([]);
  const [forecastConfigs, setForecastConfigs] = useState<ForecastConfig[]>([]);
  const [goldRules, setGoldRules] = useState<GoldCubeRule[]>([]);
  const [forecastConfigsError, setForecastConfigsError] = useState(false);
  const [backupSizes, setBackupSizes] = useState<Record<"bronze" | "silver" | "gold", number> | null>(null);

  const focusDimonIds = (goldIds: number[]): number[] => {
    const goldNames = new Set(
      destinations.filter(d => goldIds.includes(d.id)).map(d => d.physical_name.toLowerCase()),
    );
    return goldRules.filter(rule => rule.active !== false && (rule.sources || []).some(source =>
      (source.gold_tables || [source.gold_table || ""]).some(table => goldNames.has(String(table).toLowerCase())),
    )).map(rule => rule.id).filter((id): id is number => id != null);
  };

  const forecastCandidates = forecastConfigs.filter(config =>
    config.active !== false && (
      !form.destination_table_ids.length
      || form.destination_table_ids.map(Number).includes(Number(config.destination_table_id))
    ),
  );

  const loadSchedules = () => {
    getSchedules().then(setSchedules).catch((e) => setError(formatApiError(e, "Não foi possível carregar as programações")));
  };

  useEffect(() => {
    loadSchedules();
    getDestinationTables(true).then(setDestinations).catch(() => {});
    getForecastConfigs()
      .then(configs => {
        setForecastConfigs(configs);
        setForecastConfigsError(false);
      })
      .catch(() => setForecastConfigsError(true));
    getGoldCubeRules().then(setGoldRules).catch(() => {});
    getBackupSizeEstimates().then(setBackupSizes).catch(() => setBackupSizes(null));
  }, []);

  const openNew = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM });
    getBackupSizeEstimates().then(setBackupSizes).catch(() => setBackupSizes(null));
    setDialogOpen(true);
  };

  const openEdit = (s: ScheduledTask) => {
    setEditing(s);
    getBackupSizeEstimates().then(setBackupSizes).catch(() => setBackupSizes(null));
    const cfg = s.config_json || {};
    const primary = s.connection_id ?? 0;
    const ids = Array.isArray(s.connection_ids) && s.connection_ids.length ? s.connection_ids : (primary ? [primary] : []);
    setForm({
      schedule_type: s.schedule_type || "collection",
      name: s.name, connection_id: primary, connection_ids: ids,
      cron_expression: s.cron_expression, timezone: s.timezone,
      destination_reprocess: s.destination_reprocess, active: s.active,
      forecast_after_ingestion: Boolean(cfg.forecast_after_ingestion),
      forecast_config_id: String(cfg.forecast_config_id || ""),
      destination_table_ids: Array.isArray(cfg.destination_table_ids) ? cfg.destination_table_ids.map(Number) : [],
      period_mode: cfg.period_mode === "selected" ? "selected" : "all",
      billing_periods: Array.isArray(cfg.billing_periods) ? cfg.billing_periods.join(", ") : "",
      forecast_config_ids: Array.isArray(cfg.forecast_config_ids) && cfg.forecast_config_ids.length
        ? cfg.forecast_config_ids.map(Number)
        : (cfg.forecast_config_id ? [Number(cfg.forecast_config_id)] : []),
      gold_rule_ids: Array.isArray(cfg.gold_rule_ids) && cfg.gold_rule_ids.length
        ? cfg.gold_rule_ids.map(Number)
        : focusDimonIds(Array.isArray(cfg.destination_table_ids) ? cfg.destination_table_ids.map(Number) : []),
      accuracy: cfg.accuracy !== false, shared_cost: cfg.shared_cost === true,
      backup_layers: Array.isArray(cfg.backup_layers) && cfg.backup_layers.length
        ? cfg.backup_layers.filter((layer): layer is "bronze" | "silver" | "gold" => ["bronze", "silver", "gold"].includes(String(layer)))
        : ["bronze"],
    });
    setDialogOpen(true);
  };

  // Existing Cloud schedules historically stored an empty Gold list to mean
  // "automatic". Surface the actual destination-related rules as checked items.
  useEffect(() => {
    if (!dialogOpen || !editing || form.schedule_type === "backup" || form.gold_rule_ids.length) return;
    const inferred = focusDimonIds(form.destination_table_ids);
    if (inferred.length) setForm(current => ({ ...current, gold_rule_ids: inferred }));
  }, [dialogOpen, editing, destinations, goldRules]);

  const save = async () => {
    try {
      const payload = {
        ...form,
        connection_id: form.schedule_type === "backup" ? null : form.connection_id,
        connection_ids: form.schedule_type === "backup" ? [] : (form.connection_ids.length ? form.connection_ids : [form.connection_id]),
        schedule_type: form.schedule_type,
        config_json: {
          forecast_after_ingestion: form.forecast_after_ingestion,
          forecast_config_id: form.forecast_after_ingestion || form.schedule_type === "reprocess"
            ? (form.forecast_config_ids[0] || (form.forecast_config_id ? Number(form.forecast_config_id) : null))
            : null,
          destination_table_ids: form.destination_table_ids,
          period_mode: form.period_mode,
          billing_periods: form.billing_periods.split(",").map(v => v.trim()).filter(Boolean),
          forecast_config_ids: form.forecast_after_ingestion || form.schedule_type === "reprocess" ? form.forecast_config_ids : [],
          gold_rule_ids: form.gold_rule_ids,
          accuracy: form.accuracy,
          shared_cost: form.shared_cost,
          backup_layers: form.backup_layers.length ? form.backup_layers : ["bronze"],
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
      setError(formatApiError(e, "Não foi possível salvar a programação"));
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Apagar agendamento?")) return;
    try { await deleteSchedule(id); loadSchedules(); } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível remover"));
    }
  };

  const runNow = async (id: number) => {
    setRunning(prev => new Set(prev).add(id));
    try {
      await runScheduleNow(id);
      setError("");
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível executar a programação"));
    } finally {
      setRunning(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  };

  const formatNextRun = (s: ScheduledTask): string => {
    if (!s.next_run_at) return "—";
    return new Date(s.next_run_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const formatSize = (bytes?: number) => {
    if (bytes == null) return "Buscando…";
    if (bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / (1024 ** index)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} ${units[index]}`;
  };

  const formatLastRun = (s: ScheduledTask): string => {
    if (!s.last_run_at) return "Nunca";
    return new Date(s.last_run_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap", mb: 2 }}>
        <Box>
          <Typography variant="h6">Programações de coleta</Typography>
          <Typography variant="body2" color="text.secondary">Coletas, reprocessamentos e backups rodados automaticamente.</Typography>
        </Box>
        <Button variant="contained" startIcon={<AddOutlined />} onClick={openNew}>
          Novo agendamento
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      {schedules.length === 0 ? (
        <Alert severity="info" sx={{ mb: 2 }}>
          Nenhum agendamento configurado. Clique em "Nova programação" para automatizar a extração e o reprocessamento da tabela destino.
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
                  <Chip size="small" variant="outlined" label={s.schedule_type === "backup" ? "Backup completo" : s.schedule_type === "reprocess" ? "Reprocessamento" : "Coleta"} sx={{ mt: 0.5 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                    {s.cron_expression}
                  </Typography>
                </Box>
              ),
            },
            {
              key: "connection_id", label: "Conexoes", width: 200,
              render: (s: ScheduledTask) => {
                const ids = Array.isArray(s.connection_ids) && s.connection_ids.length ? s.connection_ids : (s.connection_id ? [s.connection_id] : []);
                const codes = ids.map(id => connections.find(c => c.id === id)?.code || `ID ${id}`);
                return codes.length ? (codes.length === 1 ? codes[0] : codes.join(", ")) : "—";
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
                  <Tooltip title="Rodar agora">
                    <IconButton
                      size="small" color="primary"
                      onClick={() => runNow(s.id)}
                      disabled={running.has(s.id)}
                    >
                      {running.has(s.id) ? <CircularProgress size={16} /> : <PlayArrowOutlined fontSize="small" />}
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Editar">
                    <IconButton size="small" onClick={() => openEdit(s)}>
                      <EditOutlined fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Remover">
                    <IconButton size="small" color="error" onClick={() => remove(s.id)}>
                      <DeleteOutlined fontSize="small" />
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
            <SectionTitle>Identificação da programação</SectionTitle>
            <TextField label="Nome da programação" value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })} />
            <FormControl size="small">
              <InputLabel>Tipo de execução</InputLabel>
              <Select value={form.schedule_type} label="Tipo de execução" onChange={e => {
                const type = e.target.value as SchedForm["schedule_type"];
                setForm({ ...form, schedule_type: type, name: form.name || (type === "backup" ? "Backup diário completo" : type === "reprocess" ? "Reprocessamento" : "") });
              }}>
                <MenuItem value="collection">Coleta</MenuItem>
                <MenuItem value="reprocess">Reprocessamento</MenuItem>
                <MenuItem value="backup">Backup completo</MenuItem>
              </Select>
            </FormControl>
            <SectionTitle>Fonte</SectionTitle>
            {form.schedule_type === "backup" && <>
              <Alert severity="info">Cópia de segurança da aplicação, do banco de dados e das bases selecionadas. As estimativas abaixo representam o volume atual dos dados antes da compressão do arquivo.</Alert>
              <Box sx={{ border: 1, borderColor: "divider", borderRadius: 2, p: 1.5 }}>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Tabelas incluídas na cópia</Typography>
                {(["bronze", "silver", "gold"] as const).map(layer => (
                  <FormControlLabel key={layer} sx={{ display: "flex", justifyContent: "space-between", mx: 0 }}
                    control={<Checkbox checked={form.backup_layers.includes(layer)} onChange={e => {
                      setForm(current => {
                        const selected = e.target.checked
                          ? [...current.backup_layers, layer]
                          : current.backup_layers.filter(item => item !== layer);
                        return { ...current, backup_layers: selected.length ? selected : [layer] };
                      });
                    }} />}
                    label={<Box sx={{ display: "flex", justifyContent: "space-between", gap: 2, width: 300 }}>
                      <Typography variant="body2">{DATA_LAYER_LABELS[layer]}</Typography>
                      <Typography variant="body2" color="text.secondary">{formatSize(backupSizes?.[layer])}</Typography>
                    </Box>} />
                ))}
                <FormHelperText>Estimativa do volume dos arquivos da camada; o tamanho final varia após compactação. Pelo menos uma camada deve permanecer selecionada.</FormHelperText>
                <Typography variant="body2" fontWeight={600} sx={{ mt: 1 }}>
                  Selecionado: {formatSize(backupSizes ? form.backup_layers.reduce((sum, layer) => sum + backupSizes[layer], 0) : undefined)}
                </Typography>
              </Box>
            </>}
            {form.schedule_type !== "backup" && <FormControl size="small">
              <InputLabel>Conexões (uma ou mais)</InputLabel>
              <Select
                multiple
                value={form.connection_ids.length ? form.connection_ids : [form.connection_id]}
                label="Conexões (uma ou mais)"
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
              <FormHelperText>Escolha todas as fontes que esta programação deve coletar.</FormHelperText>
            </FormControl>}

            {form.schedule_type === "collection" && <>
              <FormControlLabel control={<Switch checked={form.destination_reprocess} onChange={e => setForm({ ...form, destination_reprocess: e.target.checked })} />} label="Reprocessar automaticamente a tabela de destino após a coleta" />
              <FormHelperText>Se desligado, a rodada termina na base de origem e as etapas seguintes ficam indisponíveis.</FormHelperText>
            </>}

            {form.schedule_type !== "backup" && (form.schedule_type !== "collection" || form.destination_reprocess) && <>
              <SectionTitle>Processamento pós-coleta</SectionTitle>
              <Box>
                <FormControl size="small" fullWidth>
                  <InputLabel>Bases consolidadas</InputLabel>
                  <Select multiple value={form.destination_table_ids} label="Bases consolidadas"
                    onChange={e => setForm({ ...form, destination_table_ids: e.target.value as number[] })}
                    renderValue={ids => (ids as number[]).map(id => destinations.find(d => d.id === id)?.name || `ID ${id}`).join(", ")}>
                    {destinations.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
                  </Select>
                  <FormHelperText>Bases consolidadas reprocessadas após a carga da origem (ou relidas na reclassificação).</FormHelperText>
                </FormControl>
                <Box sx={{ display: "flex", gap: 1, mt: 0.5 }}>
                  <Button size="small" onClick={() => setForm({ ...form, destination_table_ids: destinations.map(d => d.id) })}>Marcar todas</Button>
                  <Button size="small" onClick={() => setForm({ ...form, destination_table_ids: [] })}>Limpar seleção</Button>
                </Box>
              </Box>
              {form.schedule_type === "collection" && <FormControlLabel control={<Switch checked={form.forecast_after_ingestion} onChange={e => setForm({ ...form, forecast_after_ingestion: e.target.checked })} />} label="Gerar Forecast após a carga e o reprocessamento" />}
              {form.schedule_type === "reprocess" && <>
                <FormControl size="small"><InputLabel>Períodos</InputLabel><Select value={form.period_mode} label="Períodos" onChange={e => setForm({ ...form, period_mode: e.target.value as "selected" | "all" })}><MenuItem value="all">Todos disponíveis</MenuItem><MenuItem value="selected">Marcados</MenuItem></Select></FormControl>
                {form.period_mode === "selected" && <TextField label="Períodos (YYYYMM)" value={form.billing_periods} onChange={e => setForm({ ...form, billing_periods: e.target.value })} helperText="Ex.: 202601, 202602, 202603" />}
              </>}
              {(form.schedule_type === "reprocess" || form.forecast_after_ingestion) && <FormControl size="small"><InputLabel>Forecasts candidatas</InputLabel><Select multiple value={form.forecast_config_ids} label="Forecasts candidatas" onChange={e => { const ids = (e.target.value as number[]).map(Number); setForm({ ...form, forecast_config_ids: ids, forecast_config_id: String(ids[0] || "") }); }} renderValue={ids => (ids as number[]).map(id => { const candidate = forecastConfigs.find(c => c.id === Number(id)); return candidate ? `${candidate.name} — ${candidate.table_name || `base consolidada ${candidate.destination_table_id}`}` : `Configuração ${id}`; }).join(", ")}><MenuItem value={0}>Nenhum</MenuItem>{forecastCandidates.map(c => <MenuItem key={c.id} value={c.id}>{c.name} — {c.table_name || `base consolidada ${c.destination_table_id}`}</MenuItem>)}</Select><FormHelperText>{forecastConfigsError ? "Não foi possível carregar as configurações de forecast. Verifique a API." : forecastCandidates.length ? "Somente configurações ativas compatíveis com as bases consolidadas selecionadas." : "Nenhuma configuração ativa compatível com as bases consolidadas selecionadas."}</FormHelperText></FormControl>}
              <FormControlLabel control={<Switch checked={form.accuracy} onChange={e => setForm({ ...form, accuracy: e.target.checked })} />} label="Gerar acerto da Forecast" />
              <FormControlLabel control={<Switch checked={form.shared_cost} onChange={e => setForm({ ...form, shared_cost: e.target.checked })} />} label="Gerar rateio de custo compartilhado" />
              <Box>
                <FormControl size="small" fullWidth><InputLabel>cubos analíticos</InputLabel><Select multiple value={form.gold_rule_ids} label="cubos analíticos" onChange={e => setForm({ ...form, gold_rule_ids: e.target.value as number[] })} renderValue={ids => (ids as number[]).map(id => goldRules.find(r => r.id === id)?.name || `Regra ${id}`).join(", ")}><MenuItem value={0}>Nenhuma base</MenuItem>{goldRules.filter(r => r.active).map(r => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}</Select><FormHelperText>Os modelos analíticos relacionados à base escolhida já aparecem marcados.</FormHelperText></FormControl>
                <Box sx={{ display: "flex", gap: 1, mt: 0.5 }}>
                  <Button size="small" onClick={() => setForm({ ...form, gold_rule_ids: goldRules.filter(r => r.active).map(r => r.id).filter((id): id is number => id != null) })}>Marcar todas</Button>
                  <Button size="small" onClick={() => setForm({ ...form, gold_rule_ids: [] })}>Limpar seleção</Button>
                </Box>
              </Box>
            </>}

            <SectionTitle>Quando rodar</SectionTitle>
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
            <TextField label="Expressão cron (personalizada)" value={form.cron_expression}
              onChange={e => setForm({ ...form, cron_expression: e.target.value })}
              helperText="Formato: minuto hora dia mês dia_semana. Ex.: 0 3 * * * (diário às 3h)" />

            <FormControlLabel
              control={<Switch checked={form.active}
                onChange={e => setForm({ ...form, active: e.target.checked })} />}
              label="Em uso"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={save} variant="contained" disabled={!form.name || (form.schedule_type !== "backup" && form.connection_ids.length === 0)}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
