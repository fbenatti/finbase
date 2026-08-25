import { useEffect, useState } from "react";
import {
  Alert,
  Button,
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
  getConnectionsForDestination,
  getForecastMetadata,
  updateForecastConfig,
} from "../../services/api";

type Props = {
  open: boolean;
  initial: ForecastConfig | null;
  tables: DestinationTable[];
  onClose: () => void;
  onSaved: () => void;
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

export default function ForecastConfigDialog({ open, initial, tables, onClose, onSaved }: Props) {
  const [name, setName] = useState(emptyState.name);
  const [tableId, setTableId] = useState<number | "">(emptyState.tableId);
  const [meta, setMeta] = useState<ForecastMetadata | null>(emptyState.meta);
  const [measure, setMeasure] = useState(emptyState.measure);
  const [dateColumn, setDateColumn] = useState(emptyState.dateColumn);
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
  const [error, setError] = useState("");

  const loadConnectionsForTable = async (destTableId: number, rules: ForecastConfig["connection_allocation_rules"] = []) => {
    const connections = await getConnectionsForDestination(destTableId);
    setMappedConnections(connections);
    const selected = connections
      .filter((conn) => (rules || []).some((rule) => rule.connection_id === conn.id || rule.connection_code === conn.code))
      .map((conn) => conn.id);
    setIntervalConnections(selected);
  };

  const applyInitial = async (config: ForecastConfig | null) => {
    setError("");
    if (!config) {
      setName("Novo Forecast");
      setTableId("");
      setMeta(null);
      setMeasure("");
      setDateColumn("");
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
      return;
    }
    setName(config.name);
    setTableId(config.destination_table_id);
    try {
      const m = await getForecastMetadata(config.destination_table_id);
      setMeta(m);
      setMeasure(config.measure_column);
      setDateColumn(config.date_column);
      setDimensions(config.dimensions || []);
      setHorizon(config.horizon_months);
      setCutoff(config.cutoff_days);
      setGrowth(config.growth_rate * 100);
      setModel(config.model_mode);
      setWindows(config.enabled_windows || [7, 14, 28]);
      setWeight(Math.round(((config as any).recent_weight || 0.7) * 100));
      setMinDays((config as any).minimum_daily_points || 7);
      setProfile(detectProfile(config.model_mode, config.growth_rate * 100, Math.round(((config as any).recent_weight || 0.7) * 100)));
      const rules = config.connection_allocation_rules?.length
        ? config.connection_allocation_rules
        : config.period_allocation === "charge_interval"
          ? []
          : [];
      setChargeEndColumn(rules[0]?.charge_end_column || config.charge_end_column || m.default_charge_end_column || "");
      await loadConnectionsForTable(config.destination_table_id, rules);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar os metadados da configuração");
    }
  };

  useEffect(() => {
    if (open) void applyInitial(initial);
  }, [open, initial]);

  const selectTable = async (id: number) => {
    const m = await getForecastMetadata(id);
    setTableId(id);
    setMeta(m);
    setMeasure(m.default_measure || "");
    setDateColumn(m.default_date || m.default_period || "");
    setDimensions(m.suggestion_profile === "FOCUS" ? m.recommended_dimensions : []);
    if (!initial) {
      setChargeEndColumn(m.default_charge_end_column || "");
      setIntervalConnections([]);
    }
    await loadConnectionsForTable(id, initial?.connection_allocation_rules);
  };

  const save = async () => {
    if (!tableId || !measure || !dateColumn) {
      setError("Informe a tabela Gold, a medida e a coluna de data.");
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
    const body = {
      name,
      destination_table_id: tableId,
      measure_column: measure,
      date_column: dateColumn,
      period_column: meta?.default_period,
      type_column: "tipo",
      dimensions,
      horizon_months: horizon,
      cutoff_days: cutoff,
      growth_rate: growth / 100,
      model_mode: model,
      enabled_windows: windows,
      recent_weight: weight / 100,
      minimum_daily_points: minDays,
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
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao salvar a configuração");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{initial ? "Editar Forecast" : "Nova configuração de Forecast"}</DialogTitle>
      <DialogContent sx={{
        "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
        "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
      }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start" sx={{ mt: 1 }}>
          <TextField label="Nome" value={name} onChange={(e) => setName(e.target.value)} />
          <FormControl sx={{ minWidth: 240 }}>
            <InputLabel>Tabela Gold</InputLabel>
            <Select label="Tabela Gold" value={tableId} onChange={(e) => void selectTable(Number(e.target.value))}>
              {tables.map((t) => (
                <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          {meta && (
            <>
              <FormControl sx={{ minWidth: 160 }}>
                <InputLabel>Medida</InputLabel>
                <Select label="Medida" value={measure} onChange={(e) => setMeasure(e.target.value)}>
                  {meta.measures.map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                </Select>
              </FormControl>
              <FormControl sx={{ minWidth: 160 }}>
                <InputLabel>Data</InputLabel>
                <Select label="Data" value={dateColumn} onChange={(e) => setDateColumn(e.target.value)}>
                  {[...meta.date_columns, ...meta.period_columns].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField label="Horizonte" type="number" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
              <TextField label="Cutoff D-n" type="number" value={cutoff} onChange={(e) => setCutoff(Number(e.target.value))} />
              <FormControl sx={{ minWidth: 240 }}>
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
              <FormControl sx={{ minWidth: 280 }}>
                <InputLabel>Método</InputLabel>
                <Select label="Método" value={model} onChange={(e) => { setModel(e.target.value); setProfile("custom"); }}>
                  {Object.entries(METHOD_LABELS).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                </Select>
              </FormControl>
              <FormControl sx={{ minWidth: 180 }}>
                <InputLabel>Janelas</InputLabel>
                <Select
                  multiple
                  label="Janelas"
                  value={windows}
                  input={<OutlinedInput label="Janelas" />}
                  onChange={(e) => { setWindows(e.target.value as number[]); setProfile("custom"); }}
                  renderValue={(selected) => selected.length ? selected.join(", ") + " dias" : "nenhuma"}
                >
                  {WINDOW_OPTIONS.map((w) => <MenuItem key={w} value={w}>{w} dias</MenuItem>)}
                </Select>
              </FormControl>
              <TextField label="Crescimento %" type="number" value={growth} onChange={(e) => { setGrowth(Number(e.target.value)); setProfile("custom"); }} />
              <TextField
                label="Peso recente %"
                type="number"
                value={weight}
                onChange={(e) => { setWeight(Math.max(0, Math.min(100, Number(e.target.value)))); setProfile("custom"); }}
                sx={{ width: 130 }}
              />
              <TextField
                label="Mín. pontos diários"
                type="number"
                value={minDays}
                onChange={(e) => { setMinDays(Number(e.target.value)); setProfile("custom"); }}
                sx={{ width: 130 }}
              />
              <FormControl sx={{ minWidth: 300 }}>
                <InputLabel>Dimensões da série</InputLabel>
                <Select
                  multiple
                  value={dimensions}
                  input={<OutlinedInput label="Dimensões da série" />}
                  onChange={(e) => setDimensions(e.target.value as string[])}
                >
                  {meta.dimensions.map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                </Select>
              </FormControl>
            </>
          )}
        </Stack>

        <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>
          Conexões com rateio por intervalo
        </Typography>
        <Stack direction="row" gap={1.5} flexWrap="wrap">
          <FormControl sx={{ minWidth: 360 }} disabled={!tableId}>
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
          {intervalConnections.length > 0 && meta && (
            <FormControl sx={{ minWidth: 220 }}>
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
          )}
        </Stack>

        {!tableId && (
          <Typography variant="caption" color="text.secondary" display="block" mt={1}>
            Selecione uma tabela Gold para escolher conexões com rateio.
          </Typography>
        )}
        {tableId && mappedConnections.length === 0 && (
          <Typography variant="caption" color="text.secondary" display="block" mt={1}>
            Nenhuma conexão mapeada para esta Gold. Configure o relacionamento origem → destino primeiro.
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary" display="block" mt={1}>
          Demais conexões usam série diária padrão (sem rateio). IBM manual_csv costuma precisar de rateio entre
          ChargePeriodStart e ChargePeriodEnd; Azure/GCP permanecem diários.
        </Typography>
        {intervalConnections.length > 0 && meta && (
          <Typography variant="caption" color="text.secondary" display="block" mt={1}>
            Rateio uniforme entre a coluna de data e a coluna de fim (ou o último dia do período de faturamento).
          </Typography>
        )}
        {!!dimensions.length && (
          <Typography variant="caption" color="text.secondary" display="block" mt={1}>
            Séries por: {dimensions.join(" · ")}. Inclua cloud_origem para separar azure-4p, azure-ti, gcp-4p e gcp-ti.
          </Typography>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={saving} onClick={() => void save()}>
          {saving ? "Salvando..." : "Salvar configuração"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
