import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  OutlinedInput,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  DestinationTable,
  ForecastConfig,
  ForecastMetadata,
  MappedConnection,
  createForecastConfig,
  formatApiError,
  getConnectionsForDestination,
  getForecastConfigs,
  getForecastMetadata,
  updateForecastConfig,
} from "../../services/api";
import { FieldWithHelp } from "../../components/FieldHelper";
import { FORECAST_CONFIG_FIELD_HELP } from "./forecastFieldHelp";

type Props = {
  open: boolean;
  initial: ForecastConfig | null;
  tables: DestinationTable[];
  usedDestinationTableIds: number[];
  onClose: () => void;
  onSaved: (savedName: string) => void | Promise<void>;
};

const emptyState = {
  name: "Novo Forecast",
  tableId: "" as number | "",
  meta: null as ForecastMetadata | null,
  measure: "",
  dateColumn: "",
  dimensions: [] as string[],
  horizon: 12,
  cutoff: 2,
  growth: 0.83,
  model: "AUTO_BACKTEST",
  windows: [7, 14, 28] as number[],
  weight: 70,
  minDays: 7,
  profile: "padrao" as string,
  chargeEndColumn: "",
  intervalConnections: [] as number[],
  mappedConnections: [] as MappedConnection[],
};

// Perfis de agressividade (FORECAST.md): vinculam método + crescimento.
const PROFILES = [
  { key: "custom", label: "Personalizada" },
  { key: "conservador", label: "Conservador (MEAN_28 · 0,5%)" },
  { key: "ponderado", label: "Ponderado (WEIGHTED · peso 70%)" },
  { key: "padrao", label: "Padrão (AUTO_BACKTEST · 0,83%)" },
  { key: "agressivo", label: "Agressivo (MEAN_7 · 1,5%)" },
];

const METHOD_LABELS: Record<string, string> = {
  AUTO_BACKTEST: "AUTO_BACKTEST — melhor janela no backtest",
  MEAN_7: "MEAN_7 — média dos últimos 7 dias",
  MEAN_14: "MEAN_14 — média dos últimos 14 dias",
  MEAN_28: "MEAN_28 — média dos últimos 28 dias",
  WEIGHTED: "WEIGHTED — média ponderada (peso recente)",
};

const WINDOW_OPTIONS = [7, 14, 28];

const dialogFieldSx = {
  "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
  "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
} as const;

function normalizeWindows(raw: unknown, fallback: number[] = emptyState.windows): number[] {
  if (!Array.isArray(raw) || !raw.length) return [...fallback];
  const parsed = raw
    .map((value) => Number(value))
    .filter((value) => WINDOW_OPTIONS.includes(value));
  return parsed.length ? parsed : [...fallback];
}

async function fetchConfigById(configId: number): Promise<ForecastConfig | null> {
  const items = await getForecastConfigs();
  return items.find((item) => item.id === configId) ?? null;
}

function detectProfile(model: string, growthPct: number, weight: number): string {
  if (model === "MEAN_28" && Math.abs(growthPct - 0.5) < 0.001) return "conservador";
  if (model === "AUTO_BACKTEST" && Math.abs(growthPct - 0.83) < 0.001) return "padrao";
  if (model === "MEAN_7" && Math.abs(growthPct - 1.5) < 0.001) return "agressivo";
  if (model === "WEIGHTED" && weight === 70) return "ponderado";
  return "custom";
}

function applyProfile(key: string, setters: { setModel: (v: string) => void; setGrowth: (v: number) => void; setWeight: (v: number) => void }) {
  if (key === "conservador") { setters.setModel("MEAN_28"); setters.setGrowth(0.5); }
  else if (key === "padrao") { setters.setModel("AUTO_BACKTEST"); setters.setGrowth(0.83); }
  else if (key === "agressivo") { setters.setModel("MEAN_7"); setters.setGrowth(1.5); }
  else if (key === "ponderado") { setters.setModel("WEIGHTED"); setters.setWeight(70); }
}

function safeInt(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function safeFloat(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

export default function ForecastConfigDialog({ open, initial, tables, usedDestinationTableIds, onClose, onSaved }: Props) {
  const [name, setName] = useState(emptyState.name);
  const [tableId, setTableId] = useState<number | "">(emptyState.tableId);
  const [meta, setMeta] = useState<ForecastMetadata | null>(emptyState.meta);
  const [measure, setMeasure] = useState(emptyState.measure);
  const [dateColumn, setDateColumn] = useState(emptyState.dateColumn);
  const [periodColumn, setPeriodColumn] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState<string[]>(emptyState.dimensions);
  const [horizon, setHorizon] = useState(emptyState.horizon);
  const [cutoff, setCutoff] = useState(emptyState.cutoff);
  const [growth, setGrowth] = useState(emptyState.growth);
  const [model, setModel] = useState(emptyState.model);
  const [windows, setWindows] = useState<number[]>(emptyState.windows);
  const [weight, setWeight] = useState(emptyState.weight);
  const [minDays, setMinDays] = useState(emptyState.minDays);
  const [profile, setProfile] = useState(emptyState.profile);
  const [chargeEndColumn, setChargeEndColumn] = useState(emptyState.chargeEndColumn);
  const [intervalConnections, setIntervalConnections] = useState<number[]>(emptyState.intervalConnections);
  const [mappedConnections, setMappedConnections] = useState<MappedConnection[]>(emptyState.mappedConnections);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const applySeqRef = useRef(0);

  const loadConnectionsForTable = async (destTableId: number, rules: ForecastConfig["connection_allocation_rules"] = []) => {
    const connections = await getConnectionsForDestination(destTableId);
    setMappedConnections(connections);
    const selected = connections
      .filter((conn) => (rules || []).some((rule) => rule.connection_id === conn.id || rule.connection_code === conn.code))
      .map((conn) => conn.id);
    setIntervalConnections(selected);
  };

  const applyInitial = async (config: ForecastConfig | null) => {
    const seq = ++applySeqRef.current;
    setError("");
    setLoading(true);
    if (!config) {
      setName("Novo Forecast");
      setTableId("");
      setMeta(null);
      setMeasure("");
      setDateColumn("");
      setPeriodColumn(null);
      setDimensions([]);
      setHorizon(12);
      setCutoff(2);
      setGrowth(0.83);
      setModel("AUTO_BACKTEST");
      setWindows([7, 14, 28]);
      setWeight(70);
      setMinDays(7);
      setProfile("padrao");
      setChargeEndColumn("");
      setIntervalConnections([]);
      setMappedConnections([]);
      if (seq === applySeqRef.current) setLoading(false);
      return;
    }

    try {
      const resolved = config.id ? (await fetchConfigById(config.id)) ?? config : config;
      if (seq !== applySeqRef.current) return;

      const m = await getForecastMetadata(resolved.destination_table_id);
      if (seq !== applySeqRef.current) return;

      setName(resolved.name);
      setTableId(resolved.destination_table_id);
      setMeta(m);
      setMeasure(resolved.measure_column);
      setDateColumn(resolved.date_column);
      setPeriodColumn(resolved.period_column ?? m.default_period ?? null);
      setDimensions(Array.isArray(resolved.dimensions) ? [...resolved.dimensions] : []);
      setHorizon(safeInt(resolved.horizon_months, emptyState.horizon, 1, 36));
      setCutoff(safeInt(resolved.cutoff_days, emptyState.cutoff, 0, 31));
      const growthPct = safeFloat(resolved.growth_rate, emptyState.growth / 100, 0, 1) * 100;
      const recentWeightPct = Math.round(safeFloat((resolved as ForecastConfig & { recent_weight?: number }).recent_weight, 0.7, 0, 1) * 100);
      setGrowth(growthPct);
      setModel(resolved.model_mode);
      setWindows(normalizeWindows(resolved.enabled_windows, emptyState.windows));
      setWeight(recentWeightPct);
      setMinDays(safeInt((resolved as ForecastConfig & { minimum_daily_points?: number }).minimum_daily_points, emptyState.minDays, 1, 365));
      setProfile(detectProfile(resolved.model_mode, growthPct, recentWeightPct));
      const rules = resolved.connection_allocation_rules?.length
        ? resolved.connection_allocation_rules
        : resolved.period_allocation === "charge_interval"
          ? []
          : [];
      setChargeEndColumn(rules[0]?.charge_end_column || resolved.charge_end_column || m.default_charge_end_column || "");
      await loadConnectionsForTable(resolved.destination_table_id, rules);
    } catch (e) {
      if (seq === applySeqRef.current) {
        setError(e instanceof Error ? e.message : "Não foi possível carregar os metadados da configuração");
      }
    } finally {
      if (seq === applySeqRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    void applyInitial(initial);
  }, [open, initial?.id]);

  const selectTable = async (id: number) => {
    const m = await getForecastMetadata(id);
    setTableId(id);
    setMeta(m);
    setMeasure(m.default_measure || "");
    setDateColumn(m.default_date || m.default_period || "");
    setDimensions(m.suggestion_profile === "FOCUS" ? m.recommended_dimensions : []);
    setPeriodColumn(m.default_period ?? null);
    if (!initial) {
      setChargeEndColumn(m.default_charge_end_column || "");
      setIntervalConnections([]);
    }
    await loadConnectionsForTable(id, initial?.connection_allocation_rules);
  };

  const save = async () => {
    if (!tableId || !measure || !dateColumn) {
      setError("Informe a base consolidada, a medida e a coluna de data.");
      return;
    }
    const connectionRules = intervalConnections
      .map((connId) => mappedConnections.find((conn) => conn.id === connId))
      .filter(Boolean)
      .map((conn) => ({
        connection_id: conn!.id,
        connection_code: conn!.code,
        period_allocation: "charge_interval" as const,
        charge_end_column: chargeEndColumn || null,
      }));
    const horizonMonths = safeInt(horizon, emptyState.horizon, 1, 36);
    const cutoffDays = safeInt(cutoff, emptyState.cutoff, 0, 31);
    const growthRate = safeFloat(growth, emptyState.growth, 0, 100) / 100;
    const recentWeight = safeFloat(weight, emptyState.weight, 0, 100) / 100;
    const minimumDailyPoints = safeInt(minDays, emptyState.minDays, 1, 365);
    const selectedWindows = normalizeWindows(windows, []);
    if (!selectedWindows.length) {
      setError("Selecione ao menos uma janela (7, 14 ou 28 dias).");
      return;
    }
    const body = {
      name,
      destination_table_id: tableId,
      measure_column: measure,
      date_column: dateColumn,
      period_column: periodColumn ?? meta?.default_period,
      type_column: "tipo",
      dimensions,
      horizon_months: horizonMonths,
      cutoff_days: cutoffDays,
      growth_rate: growthRate,
      model_mode: model,
      enabled_windows: selectedWindows,
      recent_weight: recentWeight,
      minimum_daily_points: minimumDailyPoints,
      period_allocation: "none",
      charge_end_column: null,
      connection_allocation_rules: connectionRules,
      active: true,
    };
    setSaving(true);
    setError("");
    try {
      if (initial) await updateForecastConfig(initial.id, body);
      else await createForecastConfig(body);
      await onSaved(name.trim() || "Configuração");
      onClose();
    } catch (e) {
      setError(formatApiError(e, "Falha ao salvar a configuração"));
    } finally {
      setSaving(false);
    }
  };

  const selectableTables = useMemo(() => {
    const used = new Set(usedDestinationTableIds);
    const available = tables.filter((table) => !used.has(table.id));
    if (!initial) return available;
    const current = tables.find((table) => table.id === initial.destination_table_id);
    if (current && !available.some((table) => table.id === current.id)) {
      return [current, ...available];
    }
    return tables;
  }, [initial, tables, usedDestinationTableIds]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{initial ? "Editar Forecast" : "Nova configuração de Forecast"}</DialogTitle>
      <DialogContent sx={dialogFieldSx}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        {loading && (
          <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 2 }}>
            <CircularProgress size={18} />
            <Typography variant="body2" color="text.secondary">Carregando configuração...</Typography>
          </Stack>
        )}

        <Stack spacing={2} sx={{ mt: 1, opacity: loading ? 0.6 : 1, pointerEvents: loading ? "none" : "auto" }}>
          <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
            <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.name} sx={{ minWidth: 220 }}>
              <TextField label="Nome" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
            </FieldWithHelp>
            <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.table} sx={{ minWidth: 240 }}>
              <FormControl fullWidth>
                <InputLabel>base consolidada</InputLabel>
                <Select
                  label="base consolidada"
                  value={tableId}
                  disabled={!!initial}
                  onChange={(e) => void selectTable(Number(e.target.value))}
                >
                  {selectableTables.map((t) => (
                    <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            </FieldWithHelp>
            {meta && (
              <>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.measure} sx={{ minWidth: 160 }}>
                  <FormControl fullWidth>
                    <InputLabel>Medida</InputLabel>
                    <Select label="Medida" value={measure} onChange={(e) => setMeasure(e.target.value)}>
                      {meta.measures.map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                    </Select>
                  </FormControl>
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.date} sx={{ minWidth: 160 }}>
                  <FormControl fullWidth>
                    <InputLabel>Data</InputLabel>
                    <Select label="Data" value={dateColumn} onChange={(e) => setDateColumn(e.target.value)}>
                      {[...meta.date_columns, ...meta.period_columns].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                    </Select>
                  </FormControl>
                </FieldWithHelp>
              </>
            )}
          </Stack>

          {meta && (
            <>
              <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.horizon} sx={{ width: 126 }}>
                  <TextField
                    label="Horizonte"
                    type="number"
                    value={horizon}
                    fullWidth
                    slotProps={{ htmlInput: { min: 1, max: 36 } }}
                    onChange={(e) => setHorizon(safeInt(e.target.value, emptyState.horizon, 1, 36))}
                  />
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.cutoff} sx={{ width: 126 }}>
                  <TextField
                    label="Cutoff D-n"
                    type="number"
                    value={cutoff}
                    fullWidth
                    slotProps={{ htmlInput: { min: 0, max: 31 } }}
                    onChange={(e) => setCutoff(safeInt(e.target.value, emptyState.cutoff, 0, 31))}
                  />
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.profile} sx={{ minWidth: 236 }}>
                  <FormControl fullWidth>
                    <InputLabel>Perfil</InputLabel>
                    <Select
                      label="Perfil"
                      value={profile}
                      onChange={(e) => {
                        const key = e.target.value as string;
                        setProfile(key);
                        applyProfile(key, { setModel, setGrowth, setWeight });
                      }}
                    >
                      {PROFILES.map((p) => <MenuItem key={p.key} value={p.key}>{p.label}</MenuItem>)}
                    </Select>
                  </FormControl>
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.method} sx={{ minWidth: 296 }}>
                  <FormControl fullWidth>
                    <InputLabel>Método</InputLabel>
                    <Select label="Método" value={model} onChange={(e) => { setModel(e.target.value); setProfile("custom"); }}>
                      {Object.entries(METHOD_LABELS).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                    </Select>
                  </FormControl>
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.windows} sx={{ minWidth: 166 }}>
                  <FormControl fullWidth>
                    <InputLabel>Janelas</InputLabel>
                    <Select
                      multiple
                      label="Janelas"
                      value={windows}
                      input={<OutlinedInput label="Janelas" />}
                      onChange={(e) => {
                        const raw = e.target.value;
                        const next = typeof raw === "string" ? raw.split(",").map(Number) : raw.map(Number);
                        setWindows(normalizeWindows(next, []));
                        setProfile("custom");
                      }}
                      renderValue={(selected) => selected.length ? selected.join(", ") + " dias" : "nenhuma"}
                    >
                      {WINDOW_OPTIONS.map((w) => <MenuItem key={w} value={w}>{w} dias</MenuItem>)}
                    </Select>
                  </FormControl>
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.growth} sx={{ width: 136 }}>
                  <TextField
                    label="Crescimento %"
                    type="number"
                    value={growth}
                    fullWidth
                    onChange={(e) => { setGrowth(Number(e.target.value)); setProfile("custom"); }}
                  />
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.recentWeight} sx={{ width: 136 }}>
                  <TextField
                    label="Peso recente %"
                    type="number"
                    value={weight}
                    fullWidth
                    onChange={(e) => { setWeight(Math.max(0, Math.min(100, Number(e.target.value)))); setProfile("custom"); }}
                  />
                </FieldWithHelp>
                <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.minDays} sx={{ width: 156 }}>
                  <TextField
                    label="Mín. pontos diários"
                    type="number"
                    value={minDays}
                    fullWidth
                    onChange={(e) => { setMinDays(Number(e.target.value)); setProfile("custom"); }}
                  />
                </FieldWithHelp>
              </Stack>

              <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.dimensions} sx={{ width: "100%" }}>
                <FormControl fullWidth>
                  <InputLabel>Dimensões da série</InputLabel>
                  <Select
                    multiple
                    label="Dimensões da série"
                    value={dimensions}
                    input={<OutlinedInput label="Dimensões da série" />}
                    onChange={(e) => setDimensions(e.target.value as string[])}
                    renderValue={(selected) => selected.join(", ")}
                  >
                    {meta.dimensions.map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                  </Select>
                </FormControl>
              </FieldWithHelp>
              {!!dimensions.length && (
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: -1 }}>
                  Séries por: {dimensions.join(" · ")}. Inclua cloud_origem para separar azure-4p, azure-ti, gcp-4p e gcp-ti.
                </Typography>
              )}
            </>
          )}

          <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
            <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.intervalConnections} sx={{ minWidth: 320, flex: 1 }}>
              <FormControl fullWidth disabled={!tableId}>
                <InputLabel>Conexões com rateio</InputLabel>
                <Select
                  multiple
                  label="Conexões com rateio"
                  value={intervalConnections}
                  input={<OutlinedInput label="Conexões com rateio" />}
                  onChange={(e) => setIntervalConnections(e.target.value as number[])}
                  renderValue={(selected) =>
                    selected.map((id) => mappedConnections.find((conn) => conn.id === id)?.code || String(id)).join(", ")
                  }
                >
                  {mappedConnections.map((conn) => (
                    <MenuItem key={conn.id} value={conn.id}>{conn.name} ({conn.code})</MenuItem>
                  ))}
                </Select>
              </FormControl>
            </FieldWithHelp>
            {intervalConnections.length > 0 && meta && (
              <FieldWithHelp help={FORECAST_CONFIG_FIELD_HELP.chargeEnd} sx={{ minWidth: 236 }}>
                <FormControl fullWidth>
                  <InputLabel>Fim do intervalo</InputLabel>
                  <Select
                    label="Fim do intervalo"
                    value={chargeEndColumn}
                    onChange={(e) => setChargeEndColumn(e.target.value)}
                    displayEmpty
                  >
                    <MenuItem value="">
                      <em>{meta.default_period ? "Último dia do _billing_period" : "Mesma coluna de início"}</em>
                    </MenuItem>
                    {meta.date_columns.filter((x) => x !== dateColumn).map((x) => (
                      <MenuItem key={x} value={x}>{x}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </FieldWithHelp>
            )}
          </Stack>

          <Stack spacing={0.5}>
            {!initial && selectableTables.length === 0 && (
              <Typography variant="caption" color="text.secondary">
                Todas as bases consolidadas já possuem configuração de forecast.
              </Typography>
            )}
            {!tableId && (
              <Typography variant="caption" color="text.secondary">
                Selecione uma base consolidada para escolher conexões com rateio.
              </Typography>
            )}
            {tableId && mappedConnections.length === 0 && (
              <Typography variant="caption" color="text.secondary">
                Nenhuma conexão mapeada para esta base consolidada. Configure o relacionamento origem → destino primeiro.
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary">
              Demais conexões usam série diária padrão (sem rateio). IBM manual_csv costuma precisar de rateio entre
              ChargePeriodStart e ChargePeriodEnd; Azure/GCP permanecem diários.
            </Typography>
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={saving || loading} onClick={() => void save()}>
          {saving ? "Salvando..." : "Salvar configuração"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
