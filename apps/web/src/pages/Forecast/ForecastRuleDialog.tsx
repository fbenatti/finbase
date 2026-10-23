import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState, type ReactNode } from "react";
import {
  Alert, Box, Button, Chip, FormControl, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack, Switch, TextField,
  ToggleButton, ToggleButtonGroup, Typography, FormControlLabel, IconButton,
} from "@mui/material";
import { AddOutlined, DeleteOutlined } from "@mui/icons-material";
import {
  ForecastBusinessRule,
  ForecastConfig,
  ForecastIdentificationClause,
  ForecastMetadata,
  createForecastBusinessRule,
  formatApiError,
  updateForecastBusinessRule,
} from "../../services/api";
import { BEHAVIOR_LABELS } from "./forecastConstants";

type Props = {
  open: boolean;
  initial: ForecastBusinessRule | null;
  config: ForecastConfig;
  meta: ForecastMetadata | null;
  onClose: () => void;
  onSaved: (savedName: string) => void | Promise<void>;
};

// Uma frase curta por comportamento; some quando não há nada a dizer.
const BEHAVIOR_HINTS: Record<string, string> = {
  SOURCE_EXCLUDE_PREFIX: "Remove da base as linhas cujo campo começa com o prefixo. Não precisa estar nas dimensões do forecast.",
  SOURCE_MEAN_30D_PREFIX: "Separa o prefixo e projeta pela média diária dos últimos 30 dias.",
  SOURCE_MAX_PREVIOUS_MONTH_PREFIX: "Projeta o mês corrente com o maior entre o ritmo atual e a média do mês fechado anterior.",
  SOURCE_GITHUB_COPILOT_OVERAGE_2_PHASE: "Antes da primeira cobrança do mês, usa a média dos dias cobrados (30 dias). Depois, usa a maior média.",
  SOURCE_VALIDITY: "Suspende o Forecast do escopo quando o último efetivado estiver além da validade.",
};

const isChargeIntervalRule = (behavior: string) => behavior === "CHARGE_INTERVAL";

const formatScope = (scope: Record<string, string>) =>
  Object.entries(scope || {})
    .map(([k, v]) => k + "=" + v)
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
  end_period: "",
  value_column: "",
  reference_column: "",
  scopeText: "",
  priority: 10,
  active: true,
  currentMonthPolicy: "AUTO",
};

/** Bloco numerado, no mesmo padrão do editor de perfis. */
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

export default function ForecastRuleDialog({ open, initial, config, meta, onClose, onSaved }: Props) {
  const [rule, setRule] = useState<any>(emptyRule);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    if (!initial) {
      setRule({ ...emptyRule, value_column: config.measure_column });
      return;
    }
    setRule({
      ...initial,
      clauseMode: initial.identification_clauses?.length ? "or" : "legacy",
      identification_clauses: initial.identification_clauses?.length
        ? initial.identification_clauses
        : [{ column: initial.identification_column, values: initial.identification_values }],
      start_period: initial.start_period.slice(0, 4) + "-" + initial.start_period.slice(4),
      end_period: initial.end_period ? initial.end_period.slice(0, 4) + "-" + initial.end_period.slice(4) : "",
      value_column: initial.value_column || config.measure_column,
      reference_column: initial.reference_column || "",
      scopeText: formatScope(initial.scope || {}),
      maxAgeDays: Number(initial.behavior_options?.max_age_days ?? 30),
      currentMonthPolicy: String(initial.behavior_options?.current_month_policy ?? "AUTO"),
    });
  }, [open, initial, config]);

  const save = async () => {
    if (!rule.name) {
      setError("Dê um nome à regra.");
      return;
    }
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
    if (rule.behavior === "SOURCE_VALIDITY" && (!Number.isInteger(Number(rule.maxAgeDays)) || Number(rule.maxAgeDays) < 1 || Number(rule.maxAgeDays) > 365)) {
      setError("Informe a validade da fonte entre 1 e 365 dias.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload = rulePayload({
        ...rule,
        identification_values: String(rule.identification_values || "")
          .split(",")
          .map((x: string) => x.trim())
          .filter(Boolean),
        value_column: rule.value_column || config.measure_column,
        scope: parseScope(rule.scopeText || ""),
        behavior_options: rule.behavior === "SOURCE_VALIDITY"
          ? { max_age_days: Number(rule.maxAgeDays || 30) }
          : ["RECURRING_MONTHLY", "RECURRING_UNTIL_END"].includes(rule.behavior)
            ? { current_month_policy: rule.currentMonthPolicy || "AUTO" }
            : {},
      });
      if (initial) await updateForecastBusinessRule(initial.id, payload);
      else await createForecastBusinessRule(config.id, payload);
      await onSaved(rule.name.trim() || "Regra");
      onClose();
    } catch (e) {
      setError(formatApiError(e, "Não foi possível gravar a regra"));
    } finally {
      setSaving(false);
    }
  };

  const columnNames = meta?.columns.map((c) => c.name) ?? [];
  const behaviorLabel = (BEHAVIOR_LABELS as Record<string, string>)[rule.behavior] ?? rule.behavior;
  const hint = BEHAVIOR_HINTS[rule.behavior];
  const identificationSummary = rule.clauseMode === "or"
    ? (rule.identification_clauses || []).filter((c: ForecastIdentificationClause) => c.column && c.values?.length)
        .map((c: ForecastIdentificationClause) => `${c.column} em ${(Array.isArray(c.values) ? c.values : []).join(", ")}`).join(" ou ")
    : rule.identification_column
      ? `${rule.identification_column} = ${String(Array.isArray(rule.identification_values) ? rule.identification_values.join(", ") : rule.identification_values || "…")}`
      : "";

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <span>{initial ? "Alterar regra de negócio do Forecast" : "Nova regra de negócio do Forecast"}</span>
          <Chip size="small" variant="outlined" label={rule.active !== false ? "Ativa" : "Inativa"} />
        </Stack>
        <Typography variant="body2" color="text.secondary">Perfil: {config.name}</Typography>
      </DialogTitle>
      <DialogContent sx={{ bgcolor: "#FAFBFD" }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

        <Box sx={{ display: "grid", gap: 2, alignItems: "start", gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1fr) 320px" } }}>
          <Stack spacing={2} sx={{ minWidth: 0 }}>
            <Step n={1} title="Nome e comportamento" hint="O que a regra faz com as cobranças que encontrar.">
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <TextField label="Nome da regra" value={rule.name} fullWidth onChange={(e) => setRule({ ...rule, name: e.target.value })} />
                <FormControl fullWidth>
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
                    {Object.entries(BEHAVIOR_LABELS).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                  </Select>
                </FormControl>
              </Stack>
              {hint && <Alert severity="info" sx={{ mt: 1.5 }}>{hint}</Alert>}
            </Step>

            <Step n={2} title="Quais cobranças" hint="Identifique as linhas da base consolidada que a regra atinge.">
              <ToggleButtonGroup
                size="small"
                exclusive
                value={rule.clauseMode || "legacy"}
                onChange={(_, v: "legacy" | "or" | null) => v && setRule({ ...rule, clauseMode: v })}
                sx={{ mb: 1.5 }}
              >
                <ToggleButton value="legacy">Uma coluna</ToggleButton>
                <ToggleButton value="or">Várias condições (OR)</ToggleButton>
              </ToggleButtonGroup>

              {rule.behavior === "SOURCE_VALIDITY" && (
                <TextField
                  type="number"
                  label="Validade da fonte"
                  value={rule.maxAgeDays ?? 30}
                  slotProps={{ htmlInput: { min: 1, max: 365, step: 1 }, input: { endAdornment: <InputAdornment position="end">dias</InputAdornment> } }}
                  helperText="Idade máxima do efetivado para continuar projetando."
                  onChange={(e) => setRule({ ...rule, maxAgeDays: Number(e.target.value) })}
                  sx={{ width: 240, mb: 1.5 }}
                />
              )}

              {rule.clauseMode === "or" ? (
                <Stack spacing={1.25}>
                  {(rule.identification_clauses || []).map((clause: ForecastIdentificationClause, index: number) => (
                    <Stack key={index} direction={{ xs: "column", sm: "row" }} gap={1} alignItems={{ sm: "center" }}>
                      <FormControl fullWidth>
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
                          {columnNames.map((name) => <MenuItem key={name} value={name}>{name}</MenuItem>)}
                        </Select>
                      </FormControl>
                      <TextField
                        label="Valores"
                        placeholder="separe por vírgula"
                        value={Array.isArray(clause.values) ? clause.values.join(", ") : clause.values}
                        fullWidth
                        onChange={(e) => {
                          const next = [...(rule.identification_clauses || [])];
                          next[index] = { ...next[index], values: e.target.value.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean) };
                          setRule({ ...rule, identification_clauses: next });
                        }}
                      />
                      <IconButton
                        color="error"
                        aria-label="Remover condição"
                        onClick={() =>
                          setRule({
                            ...rule,
                            identification_clauses: (rule.identification_clauses || []).filter((_: unknown, i: number) => i !== index),
                          })
                        }
                      >
                        <DeleteOutlined />
                      </IconButton>
                    </Stack>
                  ))}
                  <Box>
                    <Button size="small" startIcon={<AddOutlined />} onClick={() =>
                      setRule({
                        ...rule,
                        identification_clauses: [...(rule.identification_clauses || []), { column: "", values: [] }],
                      })
                    }>
                      Adicionar condição
                    </Button>
                  </Box>
                </Stack>
              ) : (
                <Stack direction={{ xs: "column", sm: "row" }} gap={1.5}>
                  <FormControl fullWidth>
                    <InputLabel>Coluna</InputLabel>
                    <Select label="Coluna" value={rule.identification_column} onChange={(e) => setRule({ ...rule, identification_column: e.target.value })}>
                      {columnNames.map((name) => <MenuItem key={name} value={name}>{name}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <TextField
                    label="Valores"
                    placeholder="separe por vírgula"
                    fullWidth
                    value={Array.isArray(rule.identification_values) ? rule.identification_values.join(", ") : rule.identification_values}
                    onChange={(e) => setRule({ ...rule, identification_values: e.target.value })}
                  />
                </Stack>
              )}

              <TextField
                label="Escopo opcional"
                placeholder="cloud_origem=oci-prod"
                helperText="Pares dimensão=valor, separados por vírgula."
                value={rule.scopeText || ""}
                fullWidth
                onChange={(e) => setRule({ ...rule, scopeText: e.target.value })}
                sx={{ mt: 1.5 }}
              />
            </Step>

            <Step n={3} title="Valor da regra" hint="De onde vem o número e como ele entra no Forecast.">
              <Stack direction={{ xs: "column", sm: "row" }} gap={1.5}>
                <FormControl fullWidth>
                  <InputLabel>Tipo de cobrança</InputLabel>
                  <Select label="Tipo de cobrança" value={rule.charge_type} onChange={(e) => setRule({ ...rule, charge_type: e.target.value })}>
                    {["Usage", "Return", "Purchase", "Refund", "Credit"].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
                  </Select>
                </FormControl>
                {isChargeIntervalRule(rule.behavior) ? (
                  <FormControl fullWidth>
                    <InputLabel>Coluna de fim do período</InputLabel>
                    <Select label="Coluna de fim do período" value={rule.reference_column || meta?.default_charge_end_column || ""}
                      onChange={(e) => setRule({ ...rule, reference_column: e.target.value })}>
                      {columnNames.map((name) => <MenuItem key={name} value={name}>{name}</MenuItem>)}
                    </Select>
                  </FormControl>
                ) : (
                  <FormControl fullWidth>
                    <InputLabel>Coluna do valor</InputLabel>
                    <Select label="Coluna do valor" value={rule.value_column || config.measure_column}
                      onChange={(e) => setRule({ ...rule, value_column: e.target.value })}>
                      {columnNames.map((name) => <MenuItem key={name} value={name}>{name}</MenuItem>)}
                    </Select>
                  </FormControl>
                )}
              </Stack>
              {rule.behavior === "REPLACE_VALUE" && (
                <TextField
                  type="number"
                  label="Valor fixado"
                  helperText="Substitui o valor original por este número."
                  value={rule.replacement_value ?? ""}
                  onChange={(e) => setRule({ ...rule, replacement_value: e.target.value === "" ? null : Number(e.target.value) })}
                  sx={{ width: 240, mt: 1.5 }}
                />
              )}
              {["RECURRING_MONTHLY", "RECURRING_UNTIL_END"].includes(rule.behavior) && (
                <FormControl fullWidth sx={{ mt: 1.5 }}>
                  <InputLabel>Mês em curso</InputLabel>
                  <Select label="Mês em curso" value={rule.currentMonthPolicy || "AUTO"}
                    onChange={(e) => setRule({ ...rule, currentMonthPolicy: e.target.value })}>
                    <MenuItem value="AUTO">Automático, pelo histórico de cobranças</MenuItem>
                    <MenuItem value="ZERO_WHEN_REALIZED">Zerar se já houver efetivado no mês</MenuItem>
                    <MenuItem value="DAILY_REMAINDER">Projetar o consumo diário dos dias restantes</MenuItem>
                  </Select>
                </FormControl>
              )}
            </Step>

            <Step n={4} title="Vigência e prioridade" hint="Quando a regra vale e qual ganha em caso de conflito.">
              <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} alignItems={{ sm: "center" }}>
                <TextField type="month" label="Início" value={rule.start_period} fullWidth slotProps={{ inputLabel: { shrink: true } }}
                  onChange={(e) => setRule({ ...rule, start_period: e.target.value })} />
                <TextField type="month" label="Fim" value={rule.end_period || ""} fullWidth slotProps={{ inputLabel: { shrink: true } }}
                  helperText="Vazio = sem fim."
                  onChange={(e) => setRule({ ...rule, end_period: e.target.value })} />
                <TextField type="number" label="Prioridade" value={rule.priority ?? 10} fullWidth
                  helperText="Maior número vence."
                  onChange={(e) => setRule({ ...rule, priority: Number(e.target.value) })} />
              </Stack>
              <FormControlLabel sx={{ mt: 1 }}
                control={<Switch checked={rule.active !== false} onChange={(e) => setRule({ ...rule, active: e.target.checked })} />}
                label="Regra ativa" />
            </Step>
          </Stack>

          <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, position: { md: "sticky" }, top: 0, bgcolor: "background.paper" }}>
            <Typography variant="overline" color="text.secondary">Prévia da regra</Typography>
            <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>{rule.name?.trim() || "Sem nome"}</Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              Aplica <strong>{behaviorLabel}</strong> às cobranças do tipo <strong>{rule.charge_type}</strong>
              {identificationSummary ? <> onde <strong>{identificationSummary}</strong></> : " (defina a identificação)"}
              {rule.start_period && <>, a partir de <strong>{rule.start_period}</strong></>}
              {rule.end_period ? <> até <strong>{rule.end_period}</strong></> : ", sem fim"}.
            </Typography>
            <Stack spacing={1.25}>
              {[
                { label: "Valor", value: isChargeIntervalRule(rule.behavior) ? (rule.reference_column || "—") : (rule.value_column || config.measure_column) },
                { label: "Escopo", value: String(rule.scopeText || "").trim() || "todas as séries" },
                { label: "Prioridade", value: String(rule.priority ?? 10) },
                { label: "Situação", value: rule.active !== false ? "Ativa" : "Inativa" },
              ].map((row) => (
                <Stack key={row.label} direction="row" justifyContent="space-between" spacing={1}>
                  <Typography variant="caption" color="text.secondary">{row.label}</Typography>
                  <Typography variant="caption" fontWeight={600} sx={{ textAlign: "right", wordBreak: "break-all" }}>{row.value}</Typography>
                </Stack>
              ))}
            </Stack>
          </Paper>
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={saving} onClick={() => void save()}>
          {saving ? "Salvando..." : initial ? "Salvar regra" : "Incluir regra"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
