import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
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
  onSaved: () => void;
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
};

const fieldGrid = {
  display: "grid",
  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
  gap: 2,
} as const;

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
      onSaved();
      onClose();
    } catch (e) {
      setError(formatApiError(e, "Falha ao salvar a regra"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>{initial ? "Editar regra" : "Nova regra de negócio"}</DialogTitle>
      <DialogContent dividers sx={{ pt: 2 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Configuração {config.id}: {config.name}. Regras versionadas aplicadas após a projeção matemática.
        </Typography>

        <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1.5 }}>
          Identificação da regra
        </Typography>
        <Box sx={{ ...fieldGrid, mb: 3 }}>
          <TextField
            fullWidth
            size="small"
            label="Nome"
            value={rule.name}
            onChange={(e) => setRule({ ...rule, name: e.target.value })}
          />
          <FormControl fullWidth size="small">
            <InputLabel>Modo de identificação</InputLabel>
            <Select
              label="Modo de identificação"
              value={rule.clauseMode || "legacy"}
              onChange={(e) => setRule({ ...rule, clauseMode: e.target.value as "legacy" | "or" })}
            >
              <MenuItem value="legacy">Coluna única</MenuItem>
              <MenuItem value="or">Cláusulas OR</MenuItem>
            </Select>
          </FormControl>
        </Box>

        <Divider sx={{ mb: 2 }} />
        <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1.5 }}>
          Critérios de identificação
        </Typography>
        {rule.clauseMode === "or" ? (
          <Stack spacing={2} sx={{ mb: 3 }}>
            {(rule.identification_clauses || []).map((clause: ForecastIdentificationClause, index: number) => (
              <Box
                key={index}
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1.4fr auto" },
                  gap: 2,
                  alignItems: "start",
                }}
              >
                <FormControl fullWidth size="small">
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
                  fullWidth
                  size="small"
                  label="Valores"
                  placeholder="ONESHOT, Purchase"
                  helperText="Separe múltiplos valores por vírgula"
                  value={Array.isArray(clause.values) ? clause.values.join(", ") : clause.values}
                  onChange={(e) => {
                    const next = [...(rule.identification_clauses || [])];
                    next[index] = { ...next[index], values: e.target.value.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean) };
                    setRule({ ...rule, identification_clauses: next });
                  }}
                />
                <IconButton
                  color="error"
                  aria-label="Remover cláusula"
                  sx={{ mt: { xs: 0, sm: 0.5 } }}
                  onClick={() =>
                    setRule({
                      ...rule,
                      identification_clauses: (rule.identification_clauses || []).filter((_: unknown, i: number) => i !== index),
                    })
                  }
                >
                  <Delete />
                </IconButton>
              </Box>
            ))}
            <Box>
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
                Adicionar cláusula OR
              </Button>
            </Box>
          </Stack>
        ) : (
          <Box sx={{ ...fieldGrid, mb: 3 }}>
            <FormControl fullWidth size="small">
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
              fullWidth
              size="small"
              label="Valores"
              placeholder="valor1, valor2"
              helperText="Separe múltiplos valores por vírgula"
              value={Array.isArray(rule.identification_values) ? rule.identification_values.join(", ") : rule.identification_values}
              onChange={(e) => setRule({ ...rule, identification_values: e.target.value })}
            />
          </Box>
        )}

        <Divider sx={{ mb: 2 }} />
        <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1.5 }}>
          Classificação e comportamento
        </Typography>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
            gap: 2,
            mb: 3,
          }}
        >
          <FormControl fullWidth size="small">
            <InputLabel>Tipo</InputLabel>
            <Select label="Tipo" value={rule.charge_type} onChange={(e) => setRule({ ...rule, charge_type: e.target.value })}>
              {["Usage", "Return", "Purchase", "Refund", "Credit"].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl fullWidth size="small" sx={{ gridColumn: { md: "span 2" } }}>
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
            fullWidth
            size="small"
            label="Escopo (dim=valor)"
            placeholder="cloud_origem=oci-prod"
            helperText="Opcional. Ex.: cloud_origem=ibm-ti, servicename=xyz"
            value={rule.scopeText || ""}
            onChange={(e) => setRule({ ...rule, scopeText: e.target.value })}
            sx={{ gridColumn: { sm: "span 2", md: "span 3" } }}
          />
        </Box>

        <Divider sx={{ mb: 2 }} />
        <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1.5 }}>
          Valor e período de cobrança
        </Typography>
        <Box sx={{ ...fieldGrid, mb: 3 }}>
          {isChargeIntervalRule(rule.behavior) ? (
            <FormControl fullWidth size="small">
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
            <FormControl fullWidth size="small">
              <InputLabel>Coluna de valor</InputLabel>
              <Select
                label="Coluna de valor"
                value={rule.value_column || config.measure_column}
                onChange={(e) => setRule({ ...rule, value_column: e.target.value })}
              >
                {meta?.columns.map((c) => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}
              </Select>
            </FormControl>
          )}
        </Box>

        <Divider sx={{ mb: 2 }} />
        <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1.5 }}>
          Vigência e controle
        </Typography>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr 1fr auto" },
            gap: 2,
            alignItems: "center",
          }}
        >
          <TextField
            fullWidth
            size="small"
            type="month"
            label="Início"
            value={rule.start_period}
            onChange={(e) => setRule({ ...rule, start_period: e.target.value })}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            fullWidth
            size="small"
            type="month"
            label="Fim (opcional)"
            value={rule.end_period || ""}
            onChange={(e) => setRule({ ...rule, end_period: e.target.value })}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            fullWidth
            size="small"
            type="number"
            label="Prioridade"
            value={rule.priority ?? 10}
            onChange={(e) => setRule({ ...rule, priority: Number(e.target.value) })}
          />
          <FormControlLabel
            sx={{ m: 0, whiteSpace: "nowrap" }}
            control={
              <Switch checked={rule.active !== false} onChange={(e) => setRule({ ...rule, active: e.target.checked })} />
            }
            label="Ativa"
          />
        </Box>
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
