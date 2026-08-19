import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  OutlinedInput,
  Select,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { Add, Delete, Edit, PlayArrow, Refresh, Stop } from "@mui/icons-material";
import {
  DestinationTable,
  ForecastBusinessRule,
  ForecastIdentificationClause,
  ForecastConfig,
  ForecastMetadata,
  ForecastRun,
  createForecastBusinessRule,
  createForecastConfig,
  cancelForecastRun,
  createForecastRun,
  deleteForecastBusinessRule,
  deleteForecastConfig,
  getConnectionsForDestination,
  getDestinationTables,
  getForecastBusinessRules,
  getForecastConfigs,
  getForecastMetadata,
  getForecastRuns,
  MappedConnection,
  updateForecastBusinessRule,
  updateForecastConfig,
} from "../../services/api";

const showDate = (value?: string | null) => (value ? new Date(value).toLocaleString("pt-BR") : "—");

const isActiveForecastRun = (status: string) => status === "QUEUED" || status === "RUNNING";

const runStatusColor = (status: string): "success" | "error" | "info" | "warning" | "default" => {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "error";
  if (status === "CANCELLED") return "warning";
  if (isActiveForecastRun(status)) return "info";
  return "default";
};

const BEHAVIOR_LABELS: Record<string, string> = {
  CHARGE_INTERVAL: "Ratear fatura mensal (ChargePeriodEnd)",
  RECURRING_MONTHLY: "Replicar última compra mensal",
  REPEAT_NEXT_MONTH: "Repetir só no mês seguinte",
  RECURRING_UNTIL_END: "Replicar até fim do período",
  CANCEL_ON_REFUND: "Cancelar compromisso (refund)",
  REPLACE_VALUE: "Substituir por valor fixo",
};

const isChargeIntervalRule = (behavior: string) => behavior === "CHARGE_INTERVAL";

const formatScope = (scope: Record<string, string>) =>
  Object.entries(scope || {})
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");

const parseScope = (text: string): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const part of text.split(/[,\n]+/).map((s) => s.trim()).filter(Boolean)) {
    const eq = part.indexOf("=");
    if (eq > 0) out[part.slice(0, eq).trim()] = part.slice(eq + 1).trim();
  }
  return out;
};

const parseValues = (raw: string | string[]) =>
  (Array.isArray(raw) ? raw : raw.split(/[,;\n]+/)).map((s) => s.trim()).filter(Boolean);

const formatClauses = (clauses: ForecastBusinessRule["identification_clauses"]) =>
  (clauses || [])
    .map((clause) => `${clause.column}=${clause.values.join("|")}`)
    .join(" OR ");

const rulePayload = (
  r: Partial<ForecastBusinessRule> & {
    name: string;
    charge_type: string;
    behavior: string;
    start_period: string;
    identification_clauses?: ForecastBusinessRule["identification_clauses"];
    clauseMode?: "legacy" | "or";
  },
) => {
  const clauses =
    r.clauseMode === "or"
      ? (r.identification_clauses || [])
          .map((clause) => ({
            column: clause.column,
            values: Array.isArray(clause.values) ? clause.values : parseValues(String(clause.values || "")),
          }))
          .filter((clause) => clause.column && clause.values.length)
      : [];
  const { clauseMode: _mode, scopeText: _scopeText, ...rest } = r as typeof r & { clauseMode?: string; scopeText?: string };
  return {
    ...rest,
    identification_column: r.clauseMode === "or" ? "" : r.identification_column || "",
    identification_values:
      r.clauseMode === "or"
        ? []
        : parseValues(Array.isArray(r.identification_values) ? r.identification_values.join(", ") : String(r.identification_values || "")),
    identification_clauses: clauses,
    start_period: r.start_period.replace("-", ""),
    end_period: r.end_period?.replace("-", "") || null,
    reference_column: r.reference_column || null,
    reference_values: r.reference_values || [],
    value_column: r.value_column || null,
    growth_rate: r.growth_rate || 0,
    replacement_value: r.replacement_value ?? null,
    cancel_on_refund: r.cancel_on_refund || false,
    scope: r.scope || {},
    priority: r.priority || 0,
    active: r.active ?? true,
  };
};

const emptyRule = {
  name: "",
  identification_column: "",
  identification_values: [] as string[],
  identification_clauses: [{ column: "", values: [] as string[] }],
  clauseMode: "legacy" as "legacy" | "or",
  charge_type: "Usage",
  behavior: "RECURRING_MONTHLY",
  start_period: "2020-01",
  value_column: "",
  reference_column: "",
  scopeText: "",
  active: true,
};

export default function Forecast() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get("tab") || "config");
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [configs, setConfigs] = useState<ForecastConfig[]>([]);
  const [runs, setRuns] = useState<ForecastRun[]>([]);
  const [meta, setMeta] = useState<ForecastMetadata | null>(null);
  const [config, setConfig] = useState<ForecastConfig | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [name, setName] = useState("Forecast padrão FinOps");
  const [tableId, setTableId] = useState<number | "">("");
  const [measure, setMeasure] = useState("");
  const [dateColumn, setDateColumn] = useState("");
  const [dimensions, setDimensions] = useState<string[]>([]);
  const [horizon, setHorizon] = useState(12);
  const [cutoff, setCutoff] = useState(2);
  const [growth, setGrowth] = useState(0.83);
  const [model, setModel] = useState("AUTO_BACKTEST");
  const [windows, setWindows] = useState<number[]>([7, 14, 28]);
  const [weight, setWeight] = useState(70);
  const [minDays, setMinDays] = useState(7);
  const [chargeEndColumn, setChargeEndColumn] = useState("");
  const [intervalConnections, setIntervalConnections] = useState<number[]>([]);
  const [mappedConnections, setMappedConnections] = useState<MappedConnection[]>([]);

  const [rules, setRules] = useState<ForecastBusinessRule[]>([]);
  const [rule, setRule] = useState<any>(emptyRule);
  const [editingRule, setEditingRule] = useState<number | null>(null);
  const [cancellingRunId, setCancellingRunId] = useState<number | null>(null);

  const load = async () => {
    try {
      const [t, c, r] = await Promise.all([getDestinationTables(true), getForecastConfigs(), getForecastRuns()]);
      setTables(t);
      setConfigs(c);
      setRuns(r);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    const i = setInterval(() => getForecastRuns().then(setRuns).catch(() => {}), 3000);
    return () => clearInterval(i);
  }, []);

  const resetForm = () => {
    setConfig(null);
    setMeta(null);
    setName("Novo Forecast");
    setTableId("");
    setMeasure("");
    setDateColumn("");
    setDimensions([]);
    setHorizon(12);
    setCutoff(2);
    setGrowth(0.83);
    setChargeEndColumn("");
    setIntervalConnections([]);
    setMappedConnections([]);
    setRules([]);
  };

  const loadConnectionsForTable = async (destTableId: number, rules: ForecastConfig["connection_allocation_rules"] = []) => {
    const connections = await getConnectionsForDestination(destTableId);
    setMappedConnections(connections);
    const selected = connections
      .filter((conn) =>
        (rules || []).some((rule) => rule.connection_id === conn.id || rule.connection_code === conn.code),
      )
      .map((conn) => conn.id);
    setIntervalConnections(selected);
  };

  const openConfig = async (c: ForecastConfig) => {
    setTab("config");
    setConfig(c);
    setName(c.name);
    setTableId(c.destination_table_id);
    const m = await getForecastMetadata(c.destination_table_id);
    setMeta(m);
    setMeasure(c.measure_column);
    setDateColumn(c.date_column);
    setDimensions(c.dimensions || []);
    setHorizon(c.horizon_months);
    setCutoff(c.cutoff_days);
    setGrowth(c.growth_rate * 100);
    setModel(c.model_mode);
    setWindows(c.enabled_windows || [7, 14, 28]);
    setWeight(Math.round(((c as any).recent_weight || 0.7) * 100));
    setMinDays((c as any).minimum_daily_points || 7);
    const rules = c.connection_allocation_rules?.length
      ? c.connection_allocation_rules
      : c.period_allocation === "charge_interval"
        ? []
        : [];
    setChargeEndColumn(
      rules[0]?.charge_end_column || c.charge_end_column || m.default_charge_end_column || "",
    );
    await loadConnectionsForTable(c.destination_table_id, rules);
    setRules(await getForecastBusinessRules(c.id));
  };

  useEffect(() => {
    if (tab === "rules" && !config && configs.length === 1) void openConfig(configs[0]);
  }, [tab, config, configs]);

  useEffect(() => {
    if (tab === "rules" && config) {
      void getForecastBusinessRules(config.id).then(setRules).catch(() => {});
    }
  }, [tab, config?.id]);

  const selectTable = async (id: number) => {
    const m = await getForecastMetadata(id);
    setTableId(id);
    setMeta(m);
    setMeasure(m.default_measure || "");
    setDateColumn(m.default_date || m.default_period || "");
    setDimensions(m.suggestion_profile === "FOCUS" ? m.recommended_dimensions : []);
    if (!config) {
      setChargeEndColumn(m.default_charge_end_column || "");
      setIntervalConnections([]);
    }
    await loadConnectionsForTable(id, config?.connection_allocation_rules);
  };

  const saveConfig = async () => {
    if (!tableId || !measure || !dateColumn) return;
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
    try {
      const saved = config ? await updateForecastConfig(config.id, body) : await createForecastConfig(body);
      setConfig(saved);
      setRules(await getForecastBusinessRules(saved.id));
      setMessage("Configuração salva");
      await load();
    } catch (e) {
      setError(String(e));
    }
  };

  const saveRule = async () => {
    if (!config || !rule.name) return;
    const hasLegacyIdentity = rule.clauseMode !== "or" && rule.identification_column;
    const hasClauseIdentity =
      rule.clauseMode === "or" &&
      (rule.identification_clauses || []).some((clause: ForecastIdentificationClause) => clause.column && clause.values?.length);
    const hasScope = Boolean(String(rule.scopeText || "").trim());
    if (!hasLegacyIdentity && !hasClauseIdentity && !hasScope) {
      setError("Informe identificação (coluna única, cláusulas OR ou escopo).");
      return;
    }
    if (isChargeIntervalRule(rule.behavior) && !rule.reference_column) {
      setError("Informe a coluna de fim do período (ex.: ChargePeriodEnd).");
      return;
    }
    try {
      const payload = rulePayload({
        ...rule,
        identification_values: String(rule.identification_values || "")
          .split(",")
          .map((x: string) => x.trim())
          .filter(Boolean),
        value_column: rule.value_column || config.measure_column,
        scope: parseScope(rule.scopeText || ""),
      });
      if (editingRule) await updateForecastBusinessRule(editingRule, payload);
      else await createForecastBusinessRule(config.id, payload);
      setRules(await getForecastBusinessRules(config.id));
      setRule(emptyRule);
      setEditingRule(null);
    } catch (e) {
      setError(String(e));
    }
  };

  const openHistory = (run: ForecastRun, comparison: boolean) =>
    navigate(`/forecast/history/${run.id}${comparison ? "?comparison=1" : ""}`);

  const forceCancelRun = async (run: ForecastRun) => {
    if (!window.confirm(`Cancelar a execução ${run.id}? Use isso se o forecast estiver travado.`)) return;
    setCancellingRunId(run.id);
    setError("");
    try {
      await cancelForecastRun(run.id);
      setMessage(`Execução ${run.id} cancelada`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setCancellingRunId(null);
    }
  };

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" mb={2}>
        <Typography variant="h4">Forecast</Typography>
        <Button startIcon={<Refresh />} onClick={() => void load()}>
          Atualizar
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" onClose={() => setError("")}>
          {error}
        </Alert>
      )}
      {message && (
        <Alert severity="success" onClose={() => setMessage("")}>
          {message}
        </Alert>
      )}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab value="config" label="Configurações e execução" />
        <Tab value="rules" label="Regras de negócio" />
        <Tab value="history" label="Histórico e resultados" />
      </Tabs>

      {tab === "config" && (
        <>
          <Card>
            <CardContent>
              <Stack direction="row" justifyContent="space-between">
                <Typography variant="h6">{config ? "Editar Forecast" : "Novo Forecast"}</Typography>
                <Button startIcon={<Add />} onClick={resetForm}>
                  Novo Forecast
                </Button>
              </Stack>

              <Stack direction="row" gap={1} flexWrap="wrap" mt={2}>
                <TextField label="Nome" value={name} onChange={(e) => setName(e.target.value)} />
                <FormControl sx={{ minWidth: 240 }}>
                  <InputLabel>Tabela Gold</InputLabel>
                  <Select label="Tabela Gold" value={tableId} onChange={(e) => void selectTable(Number(e.target.value))}>
                    {tables.map((t) => (
                      <MenuItem key={t.id} value={t.id}>
                        {t.name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                {meta && (
                  <>
                    <FormControl sx={{ minWidth: 160 }}>
                      <InputLabel>Medida</InputLabel>
                      <Select label="Medida" value={measure} onChange={(e) => setMeasure(e.target.value)}>
                        {meta.measures.map((x) => (
                          <MenuItem key={x} value={x}>
                            {x}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <FormControl sx={{ minWidth: 160 }}>
                      <InputLabel>Data</InputLabel>
                      <Select label="Data" value={dateColumn} onChange={(e) => setDateColumn(e.target.value)}>
                        {[...meta.date_columns, ...meta.period_columns].map((x) => (
                          <MenuItem key={x} value={x}>
                            {x}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <TextField label="Horizonte" type="number" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
                    <TextField label="Cutoff D-n" type="number" value={cutoff} onChange={(e) => setCutoff(Number(e.target.value))} />
                    <TextField label="Crescimento %" type="number" value={growth} onChange={(e) => setGrowth(Number(e.target.value))} />
                    <FormControl sx={{ minWidth: 300 }}>
                      <InputLabel>Dimensões da série</InputLabel>
                      <Select
                        multiple
                        value={dimensions}
                        input={<OutlinedInput label="Dimensões da série" />}
                        onChange={(e) => setDimensions(e.target.value as string[])}
                      >
                        {meta.dimensions.map((x) => (
                          <MenuItem key={x} value={x}>
                            {x}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </>
                )}
              </Stack>

              <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>
                Conexões com rateio por intervalo
              </Typography>
              <Stack direction="row" gap={1} flexWrap="wrap">
                <FormControl sx={{ minWidth: 360 }} disabled={!tableId}>
                  <InputLabel>Conexões com rateio</InputLabel>
                  <Select
                    multiple
                    label="Conexões com rateio"
                    value={intervalConnections}
                    input={<OutlinedInput label="Conexões com rateio" />}
                    onChange={(e) => setIntervalConnections(e.target.value as number[])}
                    renderValue={(selected) =>
                      selected
                        .map((id) => mappedConnections.find((conn) => conn.id === id)?.code || String(id))
                        .join(", ")
                    }
                  >
                    {mappedConnections.map((conn) => (
                      <MenuItem key={conn.id} value={conn.id}>
                        {conn.name} ({conn.code})
                      </MenuItem>
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
                      {meta.date_columns
                        .filter((x) => x !== dateColumn)
                        .map((x) => (
                          <MenuItem key={x} value={x}>
                            {x}
                          </MenuItem>
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

              <Button sx={{ mt: 2 }} variant="contained" onClick={() => void saveConfig()}>
                Salvar configuração
              </Button>
            </CardContent>
          </Card>

          <Card sx={{ mt: 2 }}>
            <CardContent>
              <Typography variant="h6">Configurações criadas</Typography>
              {configs.map((c) => (
                <Stack key={c.id} direction="row" gap={1} alignItems="center" mt={1}>
                  <Typography sx={{ flex: 1 }}>
                    {c.name} · {c.measure_column} · {c.horizon_months} meses
                    {(c.connection_allocation_rules?.length
                      ? ` · rateio (${c.connection_allocation_rules.length} conexões)`
                      : c.period_allocation === "charge_interval"
                        ? " · rateio (legado global)"
                        : "")}
                  </Typography>
                  <Button startIcon={<Edit />} onClick={() => void openConfig(c)}>
                    Editar
                  </Button>
                  <Button startIcon={<PlayArrow />} onClick={() => void createForecastRun(c.id).then(load)}>
                    Rodar
                  </Button>
                  <IconButton
                    color="error"
                    onClick={() => {
                      if (confirm(`Apagar somente a configuração ${c.id}?`)) void deleteForecastConfig(c.id).then(load);
                    }}
                  >
                    <Delete />
                  </IconButton>
                </Stack>
              ))}
            </CardContent>
          </Card>
        </>
      )}

      {tab === "rules" && (
        <Card>
          <CardContent>
            <Typography variant="h6">Regras de negócio do Forecast</Typography>
            {!config ? (
              <Alert severity="info">Abra uma configuração para editar suas regras.</Alert>
            ) : (
              <>
                <Typography variant="body2">
                  Configuração {config.id}: {config.name}
                </Typography>
                <Typography variant="body2" color="text.secondary" mt={1}>
                  Use <strong>Ratear fatura mensal</strong> para CSVs sem granularidade diária: identifique a conexão em{" "}
                  <code>cloud_origem</code> (ex.: <code>ibm-ti</code>) e informe <code>ChargePeriodEnd</code> como coluna de fim do período.
                </Typography>
                <Stack direction="row" gap={1} flexWrap="wrap" mt={2}>
                  <TextField size="small" label="Nome" value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} />
                  <FormControl size="small">
                    <InputLabel>Identificação</InputLabel>
                    <Select
                      label="Identificação"
                      value={rule.clauseMode || "legacy"}
                      onChange={(e) => setRule({ ...rule, clauseMode: e.target.value as "legacy" | "or" })}
                    >
                      <MenuItem value="legacy">Coluna única</MenuItem>
                      <MenuItem value="or">Cláusulas OR</MenuItem>
                    </Select>
                  </FormControl>
                  {rule.clauseMode === "or" ? (
                    <>
                      {(rule.identification_clauses || []).map((clause: ForecastIdentificationClause, index: number) => (
                        <Stack key={index} direction="row" gap={1} alignItems="center">
                          <FormControl size="small" sx={{ minWidth: 180 }}>
                            <InputLabel>Coluna</InputLabel>
                            <Select
                              label="Coluna"
                              value={clause.column}
                              onChange={(e) => {
                                const next = [...(rule.identification_clauses || [])];
                                next[index] = { ...next[index], column: e.target.value };
                                setRule({ ...rule, identification_clauses: next });
                              }}
                            >
                              {meta?.columns.map((c) => (
                                <MenuItem key={c.name} value={c.name}>
                                  {c.name}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                          <TextField
                            size="small"
                            label="Valores"
                            value={Array.isArray(clause.values) ? clause.values.join(", ") : clause.values}
                            onChange={(e) => {
                              const next = [...(rule.identification_clauses || [])];
                              next[index] = { ...next[index], values: e.target.value.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean) };
                              setRule({ ...rule, identification_clauses: next });
                            }}
                          />
                          <IconButton
                            color="error"
                            onClick={() =>
                              setRule({
                                ...rule,
                                identification_clauses: (rule.identification_clauses || []).filter((_, i) => i !== index),
                              })
                            }
                          >
                            <Delete />
                          </IconButton>
                        </Stack>
                      ))}
                      <Button
                        size="small"
                        startIcon={<Add />}
                        onClick={() =>
                          setRule({
                            ...rule,
                            identification_clauses: [...(rule.identification_clauses || []), { column: "", values: [] }],
                          })
                        }
                      >
                        Cláusula OR
                      </Button>
                    </>
                  ) : (
                    <>
                      <FormControl size="small" sx={{ minWidth: 180 }}>
                        <InputLabel>Coluna</InputLabel>
                        <Select
                          label="Coluna"
                          value={rule.identification_column}
                          onChange={(e) => setRule({ ...rule, identification_column: e.target.value })}
                        >
                          {meta?.columns.map((c) => (
                            <MenuItem key={c.name} value={c.name}>
                              {c.name}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      <TextField
                        size="small"
                        label="Valores"
                        value={Array.isArray(rule.identification_values) ? rule.identification_values.join(", ") : rule.identification_values}
                        onChange={(e) => setRule({ ...rule, identification_values: e.target.value })}
                      />
                    </>
                  )}
                  <FormControl size="small">
                    <InputLabel>Tipo</InputLabel>
                    <Select label="Tipo" value={rule.charge_type} onChange={(e) => setRule({ ...rule, charge_type: e.target.value })}>
                      {["Usage", "Return", "Purchase", "Refund", "Credit"].map((x) => (
                        <MenuItem key={x} value={x}>
                          {x}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <FormControl size="small" sx={{ minWidth: 220 }}>
                    <InputLabel>Comportamento</InputLabel>
                    <Select
                      label="Comportamento"
                      value={rule.behavior}
                      onChange={(e) => {
                        const behavior = e.target.value;
                        setRule({
                          ...rule,
                          behavior,
                          reference_column:
                            behavior === "CHARGE_INTERVAL"
                              ? rule.reference_column || meta?.default_charge_end_column || meta?.columns.find((c) => c.name.toLowerCase() === "chargeperiodend")?.name || ""
                              : rule.reference_column,
                        });
                      }}
                    >
                      {Object.entries(BEHAVIOR_LABELS).map(([value, label]) => (
                        <MenuItem key={value} value={value}>
                          {label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <TextField
                    size="small"
                    label="Escopo (dim=valor)"
                    placeholder="cloud_origem=oci-prod"
                    value={rule.scopeText || ""}
                    onChange={(e) => setRule({ ...rule, scopeText: e.target.value })}
                    sx={{ minWidth: 220 }}
                  />
                  {isChargeIntervalRule(rule.behavior) && (
                    <FormControl size="small" sx={{ minWidth: 200 }} required>
                      <InputLabel>Coluna fim do período</InputLabel>
                      <Select
                        label="Coluna fim do período"
                        value={rule.reference_column || meta?.default_charge_end_column || ""}
                        onChange={(e) => setRule({ ...rule, reference_column: e.target.value })}
                      >
                        {meta?.columns.map((c) => (
                          <MenuItem key={c.name} value={c.name}>
                            {c.name}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  )}
                  {!isChargeIntervalRule(rule.behavior) && (
                    <FormControl size="small" sx={{ minWidth: 160 }}>
                      <InputLabel>Coluna de valor</InputLabel>
                      <Select
                        label="Coluna de valor"
                        value={rule.value_column || config.measure_column}
                        onChange={(e) => setRule({ ...rule, value_column: e.target.value })}
                      >
                        {meta?.columns.map((c) => (
                          <MenuItem key={c.name} value={c.name}>
                            {c.name}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  )}
                  <TextField
                    size="small"
                    type="month"
                    label="Início"
                    value={rule.start_period}
                    onChange={(e) => setRule({ ...rule, start_period: e.target.value })}
                  />
                  <FormControlLabel
                    control={
                      <Switch
                        checked={rule.active !== false}
                        onChange={(e) => setRule({ ...rule, active: e.target.checked })}
                      />
                    }
                    label="Ativa"
                  />
                  <Button variant="contained" onClick={() => void saveRule()}>
                    {editingRule ? "Salvar regra" : "Adicionar regra"}
                  </Button>
                  {editingRule && (
                    <Button
                      onClick={() => {
                        setEditingRule(null);
                        setRule(emptyRule);
                      }}
                    >
                      Cancelar
                    </Button>
                  )}
                </Stack>
                {rules.map((r) => (
                  <Stack key={r.id} direction="row" alignItems="center" gap={1} mt={2}>
                    <Chip label={r.active ? "ATIVA" : "INATIVA"} color={r.active ? "success" : "default"} />
                    <Typography sx={{ flex: 1 }}>
                      {r.name} ·{" "}
                      {r.identification_clauses?.length
                        ? formatClauses(r.identification_clauses)
                        : `${r.identification_column}=${r.identification_values.join(", ")}`}
                      {Object.keys(r.scope || {}).length ? ` · escopo ${formatScope(r.scope)}` : ""}
                      {r.behavior === "CHARGE_INTERVAL" && r.reference_column ? ` · fim ${r.reference_column}` : ""}
                      · {BEHAVIOR_LABELS[r.behavior] || r.behavior}
                    </Typography>
                    <Button
                      size="small"
                      startIcon={<Edit />}
                      onClick={() => {
                        setEditingRule(r.id);
                        setRule({
                          ...r,
                          clauseMode: r.identification_clauses?.length ? "or" : "legacy",
                          identification_clauses: r.identification_clauses?.length
                            ? r.identification_clauses
                            : [{ column: r.identification_column, values: r.identification_values }],
                          start_period: `${r.start_period.slice(0, 4)}-${r.start_period.slice(4)}`,
                          end_period: r.end_period ? `${r.end_period.slice(0, 4)}-${r.end_period.slice(4)}` : "",
                          value_column: r.value_column || config.measure_column,
                          reference_column: r.reference_column || "",
                          scopeText: formatScope(r.scope || {}),
                        });
                      }}
                    >
                      Editar
                    </Button>
                    <IconButton
                      color="error"
                      onClick={() => void deleteForecastBusinessRule(r.id).then(() => getForecastBusinessRules(config.id).then(setRules))}
                    >
                      <Delete />
                    </IconButton>
                  </Stack>
                ))}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "history" && (
        <Card>
          <CardContent>
            <Typography variant="h6">Acompanhamento das execuções</Typography>
            {runs.length === 0 && (
              <Typography color="text.secondary" mt={1}>
                Nenhuma execução registrada ainda.
              </Typography>
            )}
            {runs.map((r) => (
              <Stack key={r.id} direction="row" gap={1} flexWrap="wrap" alignItems="center" mt={1}>
                <Chip label={r.status} color={runStatusColor(r.status)} />
                <Typography>
                  Execução {r.id} · {(r.rows_written || 0).toLocaleString()} linhas · iniciada: {showDate(r.started_at)} · finalizada:{" "}
                  {showDate(r.finished_at)}
                </Typography>
                {isActiveForecastRun(r.status) && r.phase && (
                  <Typography variant="body2" color="text.secondary">
                    {r.phase}
                    {typeof r.processed_series === "number" && typeof r.total_series === "number" && r.total_series > 0
                      ? ` · ${r.processed_series}/${r.total_series} séries`
                      : ""}
                  </Typography>
                )}
                {r.status === "FAILED" && r.error && (
                  <Typography variant="body2" color="error">
                    {r.error}
                  </Typography>
                )}
                {isActiveForecastRun(r.status) && (
                  <Button
                    variant="outlined"
                    color="error"
                    size="small"
                    disabled={cancellingRunId === r.id}
                    startIcon={cancellingRunId === r.id ? <CircularProgress size={16} /> : <Stop />}
                    onClick={() => void forceCancelRun(r)}
                  >
                    {cancellingRunId === r.id ? "Cancelando..." : "Forçar cancelamento"}
                  </Button>
                )}
                {r.status === "COMPLETED" && r.result_table && (
                  <>
                    <Button onClick={() => openHistory(r, false)}>Abrir Pivot</Button>
                    <Button onClick={() => openHistory(r, true)}>Comparar realizado</Button>
                  </>
                )}
              </Stack>
            ))}
          </CardContent>
        </Card>
      )}
    </Box>
  );
}
