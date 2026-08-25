import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { Add, Delete } from "@mui/icons-material";
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

const dialogFieldSx = {
  "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
  "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
} as const;

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
};

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
    });
  }, [open, initial, config]);

  const save = async () => {
    if (!rule.name) {
      setError("Informe o nome da regra.");
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
      });
      if (initial) await updateForecastBusinessRule(initial.id, payload);
      else await createForecastBusinessRule(config.id, payload);
      await onSaved(rule.name.trim() || "Regra");
      onClose();
    } catch (e) {
      setError(formatApiError(e, "Falha ao salvar a regra"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{initial ? "Editar regra" : "Nova regra de negócio"}</DialogTitle>
      <DialogContent sx={dialogFieldSx}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Configuração {config.id}: {config.name}. Regras versionadas aplicadas após a projeção matemática.
        </Typography>

        <Stack spacing={2}>
          <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
            <TextField
              label="Nome"
              value={rule.name}
              onChange={(e) => setRule({ ...rule, name: e.target.value })}
              sx={{ minWidth: 240, flex: 1 }}
            />
            <FormControl sx={{ minWidth: 200 }}>
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
          </Stack>

          {rule.clauseMode === "or" ? (
            <Stack spacing={1.5}>
              {(rule.identification_clauses || []).map((clause: ForecastIdentificationClause, index: number) => (
                <Stack key={index} direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
                  <FormControl sx={{ minWidth: 200, flex: 1 }}>
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
                      {meta?.columns.map((c) => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <TextField
                    label="Valores"
                    value={Array.isArray(clause.values) ? clause.values.join(", ") : clause.values}
                    onChange={(e) => {
                      const next = [...(rule.identification_clauses || [])];
                      next[index] = { ...next[index], values: e.target.value.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean) };
                      setRule({ ...rule, identification_clauses: next });
                    }}
                    sx={{ minWidth: 220, flex: 1.4 }}
                  />
                  <IconButton
                    color="error"
                    aria-label="Remover cláusula"
                    onClick={() =>
                      setRule({
                        ...rule,
                        identification_clauses: (rule.identification_clauses || []).filter((_: unknown, i: number) => i !== index),
                      })
                    }
                  >
                    <Delete />
                  </IconButton>
                </Stack>
              ))}
              <Box>
                <Button size="small" startIcon={<Add />} onClick={() =>
                  setRule({
                    ...rule,
                    identification_clauses: [...(rule.identification_clauses || []), { column: "", values: [] }],
                  })
                }>
                  Cláusula OR
                </Button>
              </Box>
            </Stack>
          ) : (
            <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
              <FormControl sx={{ minWidth: 200 }}>
                <InputLabel>Coluna</InputLabel>
                <Select
                  label="Coluna"
                  value={rule.identification_column}
                  onChange={(e) => setRule({ ...rule, identification_column: e.target.value })}
                >
                  {meta?.columns.map((c) => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField
                label="Valores"
                value={Array.isArray(rule.identification_values) ? rule.identification_values.join(", ") : rule.identification_values}
                onChange={(e) => setRule({ ...rule, identification_values: e.target.value })}
                sx={{ minWidth: 220, flex: 1 }}
              />
            </Stack>
          )}

          <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
            <FormControl sx={{ minWidth: 140 }}>
              <InputLabel>Tipo</InputLabel>
              <Select label="Tipo" value={rule.charge_type} onChange={(e) => setRule({ ...rule, charge_type: e.target.value })}>
                {["Usage", "Return", "Purchase", "Refund", "Credit"].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl sx={{ minWidth: 280, flex: 1 }}>
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
            <TextField
              label="Escopo (dim=valor)"
              placeholder="cloud_origem=oci-prod"
              value={rule.scopeText || ""}
              onChange={(e) => setRule({ ...rule, scopeText: e.target.value })}
              sx={{ minWidth: 240, flex: 1 }}
            />
          </Stack>

          <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="flex-start">
            {isChargeIntervalRule(rule.behavior) ? (
              <FormControl sx={{ minWidth: 220 }}>
                <InputLabel>Coluna fim do período</InputLabel>
                <Select
                  label="Coluna fim do período"
                  value={rule.reference_column || meta?.default_charge_end_column || ""}
                  onChange={(e) => setRule({ ...rule, reference_column: e.target.value })}
                >
                  {meta?.columns.map((c) => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}
                </Select>
              </FormControl>
            ) : (
              <>
                <FormControl sx={{ minWidth: 180 }}>
                  <InputLabel>Coluna de valor</InputLabel>
                  <Select
                    label="Coluna de valor"
                    value={rule.value_column || config.measure_column}
                    onChange={(e) => setRule({ ...rule, value_column: e.target.value })}
                  >
                    {meta?.columns.map((c) => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}
                  </Select>
                </FormControl>
                {rule.behavior === "REPLACE_VALUE" && (
                  <TextField
                    type="number"
                    label="Valor fixo"
                    value={rule.replacement_value ?? ""}
                    onChange={(e) => setRule({ ...rule, replacement_value: e.target.value === "" ? null : Number(e.target.value) })}
                    sx={{ width: 140 }}
                  />
                )}
              </>
            )}
          </Stack>

          <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="center">
            <TextField
              type="month"
              label="Início"
              value={rule.start_period}
              onChange={(e) => setRule({ ...rule, start_period: e.target.value })}
              slotProps={{ inputLabel: { shrink: true } }}
              sx={{ width: 170 }}
            />
            <TextField
              type="month"
              label="Fim (opcional)"
              value={rule.end_period || ""}
              onChange={(e) => setRule({ ...rule, end_period: e.target.value })}
              slotProps={{ inputLabel: { shrink: true } }}
              sx={{ width: 170 }}
            />
            <TextField
              type="number"
              label="Prioridade"
              value={rule.priority ?? 10}
              onChange={(e) => setRule({ ...rule, priority: Number(e.target.value) })}
              sx={{ width: 110 }}
            />
            <FormControlLabel
              control={
                <Switch checked={rule.active !== false} onChange={(e) => setRule({ ...rule, active: e.target.checked })} />
              }
              label="Ativa"
            />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={saving} onClick={() => void save()}>
          {saving ? "Salvando..." : initial ? "Salvar regra" : "Adicionar regra"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
