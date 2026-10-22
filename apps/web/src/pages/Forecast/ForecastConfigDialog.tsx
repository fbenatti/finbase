import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, FormControl, InputAdornment, InputLabel, MenuItem, OutlinedInput,
  Paper, Select, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography,
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

type Props = {
  open: boolean;
  initial: ForecastConfig | null;
  tables: DestinationTable[];
  usedDestinationTableIds: number[];
  onClose: () => void;
  onSaved: (savedName: string) => void | Promise<void>;
};

const emptyState = {
  name: "Novo perfil de previsão",
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
  { key: "padrao", label: "Padrão", detail: "Melhor janela no backtest · 0,83%" },
  { key: "conservador", label: "Conservador", detail: "Média de 28 dias · 0,5%" },
  { key: "ponderado", label: "Ponderado", detail: "Peso maior no recente · 70%" },
  { key: "agressivo", label: "Agressivo", detail: "Média de 7 dias · 1,5%" },
  { key: "custom", label: "Personalizado", detail: "Você define os parâmetros" },
];

const METHOD_LABELS: Record<string, string> = {
  AUTO_BACKTEST: "AUTO_BACKTEST — melhor janela no teste retroativo",
  MEAN_7: "MEAN_7 — média de 7 dias",
  MEAN_14: "MEAN_14 — média de 14 dias",
  MEAN_28: "MEAN_28 — média de 28 dias",
  WEIGHTED: "WEIGHTED — média ponderada (peso no período recente)",
};

const WINDOW_OPTIONS = [7, 14, 28];

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

/** Bloco numerado: cada etapa da regra fica em um cartão próprio. */
function Step({ n, title, hint, children }: { n: number; title: string; hint: string; children: ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
      <Stack direction="row" spacing={1.25} alignItems="baseline" sx={{ mb: 0.25 }}>
        <Typography variant="caption" sx={{ fontWeight: 700, color: "primary.main" }}>{String(n).padStart(2, "0")}</Typography>
        <Typography variant="subtitle2" fontWeight={700}>{title}</Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1.5, ml: 3.25 }}>{hint}</Typography>
      <Box sx={{ ml: { xs: 0, sm: 3.25 } }}>{children}</Box>
    </Paper>
  );
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
      setName("Novo perfil de previsão");
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
        setError(e instanceof Error ? e.message : "Não foi possível carregar os metadados do perfil");
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
      await onSaved(name.trim() || "Perfil");
      onClose();
    } catch (e) {
      setError(formatApiError(e, "Não foi possível gravar o perfil"));
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

  const setters = { setModel, setGrowth, setWeight };
  const selectedTable = selectableTables.find((table) => table.id === tableId);
  const methodName = METHOD_LABELS[model]?.split(" — ")[0] ?? model;
  const profileLabel = PROFILES.find((p) => p.key === profile)?.label ?? "Personalizado";
  const rateioCount = intervalConnections.length;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <span>{initial ? "Alterar perfil de previsão" : "Novo perfil de previsão"}</span>
          {profile !== "custom" && <Chip size="small" variant="outlined" label={profileLabel} />}
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ bgcolor: "#FAFBFD" }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
        {loading && (
          <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 2 }}>
            <CircularProgress size={18} />
            <Typography variant="body2" color="text.secondary">Carregando perfil...</Typography>
          </Stack>
        )}

        <Box sx={{
          display: "grid", gap: 2, alignItems: "start", mt: 0.5,
          gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1fr) 320px" },
          opacity: loading ? 0.6 : 1, pointerEvents: loading ? "none" : "auto",
        }}>
          <Stack spacing={2} sx={{ minWidth: 0 }}>
            <Step n={1} title="Identificação e base" hint="Nome do perfil e de qual base consolidada ele projeta.">
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <TextField label="Nome do perfil" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
                <FormControl fullWidth disabled={!!initial}>
                  <InputLabel>Base consolidada</InputLabel>
                  <Select label="Base consolidada" value={tableId} onChange={(e) => void selectTable(Number(e.target.value))}>
                    {selectableTables.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
                  </Select>
                </FormControl>
              </Stack>
              {!initial && selectableTables.length === 0 && (
                <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
                  Todas as bases consolidadas já têm um perfil.
                </Typography>
              )}
              {meta && (
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 1.5 }}>
                  <FormControl fullWidth>
                    <InputLabel>Medida</InputLabel>
                    <Select label="Medida" value={measure} onChange={(e) => setMeasure(e.target.value)}>
                      {meta.measures.map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <FormControl fullWidth>
                    <InputLabel>Coluna de data</InputLabel>
                    <Select label="Coluna de data" value={dateColumn} onChange={(e) => setDateColumn(e.target.value)}>
                      {[...meta.date_columns, ...meta.period_columns].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Stack>
              )}
              {meta && (
                <FormControl fullWidth sx={{ mt: 1.5 }}>
                  <InputLabel>Desmembrar séries por</InputLabel>
                  <Select
                    multiple
                    label="Desmembrar séries por"
                    value={dimensions}
                    input={<OutlinedInput label="Desmembrar séries por" />}
                    onChange={(e) => setDimensions(e.target.value as string[])}
                    renderValue={(selected) => selected.join(" · ")}
                  >
                    {meta.dimensions.map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                  </Select>
                </FormControl>
              )}
              {meta && dimensions.length > 0 && !dimensions.includes("cloud_origem") && (
                <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 0.75 }}>
                  Inclua cloud_origem para separar azure-4p, azure-ti, gcp-4p e gcp-ti.
                </Typography>
              )}
            </Step>

            <Step n={2} title="Janela da previsão" hint="Até onde prever e quanto histórico recente considerar.">
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <TextField label="Horizonte de previsão" type="number" fullWidth value={horizon}
                  slotProps={{ htmlInput: { min: 1, max: 36 }, input: { endAdornment: <InputAdornment position="end">meses</InputAdornment> } }}
                  helperText="Quantos meses prever à frente."
                  onChange={(e) => setHorizon(safeInt(e.target.value, emptyState.horizon, 1, 36))} />
                <TextField label="Defasagem de dados" type="number" fullWidth value={cutoff}
                  slotProps={{ htmlInput: { min: 0, max: 31 }, input: { endAdornment: <InputAdornment position="end">dias</InputAdornment> } }}
                  helperText="Dias sem dado no fim da base."
                  onChange={(e) => setCutoff(safeInt(e.target.value, emptyState.cutoff, 0, 31))} />
                <TextField label="Mínimo de pontos de histórico" type="number" fullWidth value={minDays}
                  slotProps={{ input: { endAdornment: <InputAdornment position="end">dias</InputAdornment> } }}
                  helperText="Dias com consumo para aceitar a série."
                  onChange={(e) => { setMinDays(Number(e.target.value)); setProfile("custom"); }} />
              </Stack>
              <Box sx={{ mt: 1.5 }}>
                <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 0.75 }}>Janelas de média</Typography>
                <ToggleButtonGroup
                  size="small"
                  value={windows}
                  onChange={(_, next: number[]) => {
                    if (!next.length) return;
                    setWindows(normalizeWindows(next, []));
                    setProfile("custom");
                  }}
                >
                  {WINDOW_OPTIONS.map((w) => <ToggleButton key={w} value={w}>{w} dias</ToggleButton>)}
                </ToggleButtonGroup>
              </Box>
            </Step>

            <Step n={3} title="Método e crescimento" hint="Escolha um perfil pronto ou ajuste os parâmetros manualmente.">
              <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "repeat(3, minmax(0, 1fr))" } }}>
                {PROFILES.map((p) => {
                  const on = profile === p.key;
                  return (
                    <Paper key={p.key} variant="outlined" role="button" tabIndex={0}
                      onClick={() => { setProfile(p.key); applyProfile(p.key, setters); }}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setProfile(p.key); applyProfile(p.key, setters); } }}
                      sx={{ p: 1.25, cursor: "pointer", borderColor: on ? "primary.main" : "divider", bgcolor: on ? "action.selected" : "background.paper", borderWidth: on ? 2 : 1 }}>
                      <Typography variant="body2" fontWeight={700}>{p.label}</Typography>
                      <Typography variant="caption" color="text.secondary">{p.detail}</Typography>
                    </Paper>
                  );
                })}
              </Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 1.5 }}>
                <FormControl fullWidth>
                  <InputLabel>Modelo</InputLabel>
                  <Select label="Modelo" value={model} onChange={(e) => { setModel(e.target.value); setProfile("custom"); }}>
                    {Object.entries(METHOD_LABELS).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                  </Select>
                </FormControl>
                <TextField label="Crescimento mensal" type="number" fullWidth value={growth}
                  slotProps={{ input: { endAdornment: <InputAdornment position="end">% ao mês</InputAdornment> } }}
                  helperText="Aplicado sobre a média projetada."
                  onChange={(e) => { setGrowth(Number(e.target.value)); setProfile("custom"); }} />
                <TextField label="Peso do período recente" type="number" fullWidth value={weight}
                  slotProps={{ input: { endAdornment: <InputAdornment position="end">%</InputAdornment> } }}
                  helperText="Usado só pelo modelo ponderado."
                  onChange={(e) => { setWeight(Math.max(0, Math.min(100, Number(e.target.value)))); setProfile("custom"); }} />
              </Stack>
            </Step>

            <Step n={4} title="Alocação por conexão" hint="Use só nas fontes cuja cobrança cobre um intervalo de dias.">
              {!tableId && <Typography variant="caption" color="text.secondary">Escolha a base consolidada para listar as conexões.</Typography>}
              {tableId && mappedConnections.length === 0 && (
                <Typography variant="caption" color="text.secondary">Nenhuma conexão mapeada. Configure o relacionamento origem → destino primeiro.</Typography>
              )}
              {mappedConnections.length > 0 && (
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                  <FormControl fullWidth>
                    <InputLabel>Conexões com alocação</InputLabel>
                    <Select
                      multiple
                      label="Conexões com alocação"
                      value={intervalConnections}
                      input={<OutlinedInput label="Conexões com alocação" />}
                      onChange={(e) => setIntervalConnections(e.target.value as number[])}
                      renderValue={(selected) => selected.map((id) => mappedConnections.find((conn) => conn.id === id)?.code || String(id)).join(", ")}
                    >
                      {mappedConnections.map((conn) => <MenuItem key={conn.id} value={conn.id}>{conn.name} ({conn.code})</MenuItem>)}
                    </Select>
                  </FormControl>
                  {rateioCount > 0 && meta && (
                    <FormControl fullWidth>
                      <InputLabel>Fim do intervalo de cobrança</InputLabel>
                      <Select label="Fim do intervalo de cobrança" value={chargeEndColumn} onChange={(e) => setChargeEndColumn(e.target.value)} displayEmpty>
                        <MenuItem value="">
                          <em>{meta.default_period ? "Último dia do período de cobrança" : "Mesma coluna inicial"}</em>
                        </MenuItem>
                        {meta.date_columns.filter((x) => x !== dateColumn).map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                      </Select>
                    </FormControl>
                  )}
                </Stack>
              )}
              <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
                As demais conexões seguem em série diária. A alocação costuma ser precisa no IBM manual_csv.
              </Typography>
            </Step>
          </Stack>

          <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, position: { md: "sticky" }, top: 0, bgcolor: "background.paper" }}>
            <Typography variant="overline" color="text.secondary">Prévia da regra</Typography>
            <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>{name.trim() || "Sem nome"}</Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              {selectedTable
                ? <>Projeta <strong>{horizon} {horizon === 1 ? "mês" : "meses"}</strong> de <strong>{selectedTable.name}</strong>
                    {measure && <> pela medida <strong>{measure}</strong></>}, com o método <strong>{methodName}</strong> e crescimento de <strong>{growth}%</strong> ao mês.</>
                : "Escolha uma base consolidada para ver a prévia."}
            </Typography>
            <Stack spacing={1.25}>
              {[
                { label: "Perfil", value: profileLabel },
                { label: "Janelas", value: windows.length ? windows.map((w) => `${w}d`).join(" · ") : "nenhuma" },
                { label: "Defasagem", value: `${cutoff} dias` },
                { label: "Peso do período recente", value: `${weight}%` },
                { label: "Mínimo de pontos de histórico", value: `${minDays} dias` },
                { label: "Séries separadas por", value: dimensions.length ? dimensions.join(" · ") : "uma série só" },
                { label: "Conexões com alocação", value: rateioCount ? `${rateioCount}` : "nenhuma" },
              ].map((row) => (
                <Stack key={row.label} direction="row" justifyContent="space-between" spacing={1}>
                  <Typography variant="caption" color="text.secondary">{row.label}</Typography>
                  <Typography variant="caption" fontWeight={600} sx={{ textAlign: "right" }}>{row.value}</Typography>
                </Stack>
              ))}
            </Stack>
            {windows.length === 0 && <Alert severity="warning" sx={{ mt: 2 }}>Escolha ao menos uma janela.</Alert>}
          </Paper>
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={saving || loading} onClick={() => void save()}>
          {saving ? "Gravando..." : "Gravar perfil"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
