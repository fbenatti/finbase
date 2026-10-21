import { Dialog, DialogTitle, DialogContent, DialogActions } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect } from "react";
import { Box, Typography, Button, IconButton, Chip, TextField, Select, MenuItem, FormControl, InputLabel, Switch, FormControlLabel, Alert, CircularProgress, Divider, Paper, Autocomplete } from "@mui/material";
import { Add, Edit, Delete } from "@mui/icons-material";
import {
  getBusinessRules, createBusinessRule, updateBusinessRule, deleteBusinessRule, BusinessRule,
  getDestinationTables, getDestinationSchema, DestinationTable, getFinancialContracts, FinancialContract,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable from "../../components/SortableTable";

const OPERATIONS: { value: string; label: string; symbol: string }[] = [
  { value: "%", label: "Porcentagem", symbol: "%" },
  { value: "*", label: "Multiplicar", symbol: "×" },
  { value: "+", label: "Somar valor", symbol: "+" },
  { value: "-", label: "Subtrair valor", symbol: "−" },
  { value: "col+", label: "Somar coluna", symbol: "+" },
  { value: "col-", label: "Subtrair coluna", symbol: "−" },
  { value: "=", label: "Valor Fixo", symbol: "=" },
];

const isValueLessOp = (op: string) => ["not_empty", "empty"].includes(op.toLowerCase());

const conditionOpLabel = (op: string): string => {
  const lower = op.toLowerCase();
  if (lower === "not_empty") return "não vazio";
  if (lower === "empty") return "vazio";
  return op;
};

interface CondRow {
  field: string;
  op: string;
  value: string;
  logic: "AND" | "OR";
}

const isValidCondition = (c: CondRow) => c.field && (isValueLessOp(c.op) || Boolean(c.value));

const CLOUD_SCOPE_FORM_OPTIONS = [
  { value: "global", label: "Todas as nuvens (fallback)" },
  { value: "azure", label: "Azure" },
  { value: "gcp", label: "GCP" },
  { value: "atlas", label: "MongoDB Atlas" },
  { value: "aws", label: "AWS" },
  { value: "oci", label: "Oracle (OCI)" },
  { value: "ibm", label: "IBM Cloud" },
] as const;

const cloudScopeLabel = (scope: string | null | undefined) => {
  if (!scope) return "Todas as nuvens";
  return CLOUD_SCOPE_FORM_OPTIONS.find((o) => o.value === scope)?.label ?? scope.toUpperCase();
};

const INTERNAL_SCOPE_COLUMNS = ["cloud_origem", "source_connection_id", "tipo"];

interface ScopeFilterRow {
  field: string;
  op: string;
  value: string;
}

const emptyScopeFilter = (): ScopeFilterRow => ({ field: "", op: "=", value: "" });

const isValidScopeFilter = (scope: ScopeFilterRow) =>
  scope.field && (isValueLessOp(scope.op) || Boolean(scope.value));

const emptyForm = {
  name: "", description: "",
  source_column: "", operand_column: "", operation: "%", rate: "",
  target_column: "",
  conditions: [] as CondRow[],
  cloud_scope: "global",
  scope_filter: emptyScopeFilter(),
  priority: 0, valid_from: "", valid_until: "", active: true,
  financial_effect: "NONE" as "NONE" | "DIRECT_REALIZED" | "BUDGET_COMMITMENT" | "MARKETPLACE_ALLOCATION",
  financial_contract_id: null as number | null,
  allocation_method: "",
  marketplace_connection_code: "",
  cloud_connection_code: "",
  cloud_product_name: "",
  cloud_sku: "",
  cloud_resource_group: "",
};

const findKnownColumn = (columns: string[], token: string): string => {
  const exact = columns.find((c) => c === token);
  if (exact) return exact;
  const lower = token.toLowerCase();
  return columns.find((c) => c.toLowerCase() === lower) || token;
};

const buildExpressionFromForm = (form: typeof emptyForm): string => {
  const src = form.source_column;
  const rv = parseFloat(form.rate) || 0;
  if (form.operation === "%" && src) return `${src} * ${rv / 100}`;
  if (form.operation === "*" && src) return `${src} * ${rv}`;
  if (form.operation === "+" && src) return `${src} + ${rv}`;
  if (form.operation === "-" && src) return `${src} - ${rv}`;
  if (form.operation === "col+" && src && form.operand_column) {
    return `${src} + ${form.operand_column}`;
  }
  if (form.operation === "col-" && src && form.operand_column) {
    return `${src} - ${form.operand_column}`;
  }
  if (form.operation === "=") return String(rv);
  return "";
};

const parseExpression = (
  expr: string,
  targetCol: string,
  columns: string[],
  config?: Record<string, unknown>,
): Partial<typeof emptyForm> => {
  if (!expr) return {};

  const trimmed = expr.trim();
  if (/^[\d.]+$/.test(trimmed)) {
    return { source_column: "", operand_column: "", operation: "=", rate: trimmed, target_column: targetCol };
  }

  const colOpCol = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*([+-])\s*([A-Za-z_][A-Za-z0-9_]*)$/);
  if (colOpCol) {
    const [, left, op, right] = colOpCol;
    const rightNum = Number(right);
    if (Number.isNaN(rightNum) || !/^[\d.]+$/.test(right)) {
      return {
        source_column: findKnownColumn(columns, left),
        operand_column: findKnownColumn(columns, right),
        operation: op === "+" ? "col+" : "col-",
        rate: "",
        target_column: targetCol,
      };
    }
  }

  const match = trimmed.match(/^(.+?)\s*([*/+-])\s*([\d.]+)$/);
  if (match) {
    const [, srcToken, sop, val] = match;
    const src = findKnownColumn(columns, srcToken.trim());
    const op = sop === "*" ? (parseFloat(val) < 100 ? "*" : "%") : sop;
    return { source_column: src, operand_column: "", operation: op, rate: val, target_column: targetCol };
  }

  const cfgOp = String(config?.operation || "");
  const cfgOperand = String(config?.operand_column || "");
  if (cfgOp === "col+" || cfgOp === "col-") {
    return {
      source_column: String(config?.source_column || ""),
      operand_column: cfgOperand,
      operation: cfgOp,
      rate: "",
      target_column: targetCol,
    };
  }

  const col = columns.find((c) => trimmed.includes(c)) || "";
  return { source_column: col, operand_column: "", operation: "%", rate: "", target_column: targetCol };
};

const formatOperationSummary = (r: BusinessRule): string => {
  const cfg = (r.config_json || {}) as Record<string, unknown>;
  const op = String(cfg.operation || "");
  const src = String(cfg.source_column || "");
  const operand = String(cfg.operand_column || "");
  const rate = cfg.rate ?? cfg.percentage ?? "";

  if (r.rule_type === "EXPRESSION" && r.expression) {
    if (op === "col+" && src && operand) return `${src} + ${operand}`;
    if (op === "col-" && src && operand) return `${src} − ${operand}`;
    if (op === "=" || /^[\d.]+$/.test(String(r.expression).trim())) {
      return `= ${r.expression}`;
    }
    if (src && op) {
      const opMeta = OPERATIONS.find((item) => item.value === op);
      return `${src} ${opMeta?.symbol || op} ${rate}`;
    }
    return String(r.expression);
  }
  if (r.rule_type === "CONDITIONAL" && r.conditions?.length) {
    const thenVal = String((r.conditions[0] as Record<string, unknown>)?.then_value || "");
    return thenVal || "condicional";
  }
  return r.rule_type;
};

export default function Rules({ embedded = false }: { embedded?: boolean } = {}) {
  const [rules, setRules] = useState<BusinessRule[]>([]);
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [selectedDestId, setSelectedDestId] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<BusinessRule | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [sourceColumns, setSourceColumns] = useState<string[]>([]);
  const [numericColumns, setNumericColumns] = useState<string[]>([]);
  const [contracts, setContracts] = useState<FinancialContract[]>([]);
  const scopeColumnOptions = [...INTERNAL_SCOPE_COLUMNS, ...sourceColumns.filter((c) => !INTERNAL_SCOPE_COLUMNS.includes(c))];

  useEffect(() => {
    getDestinationTables(true).then(setDestTables).catch(() => {});
    getFinancialContracts().then(setContracts).catch(() => {});
  }, []);

  const load = async () => {
    try {
      const destId = selectedDestId === "" ? undefined : selectedDestId as number;
      setRules(await getBusinessRules(undefined, destId));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedDestId]);

  // Load columns from destination table — only numeric ones for math operations
  useEffect(() => {
    if (selectedDestId) {
      getDestinationSchema(selectedDestId as number)
        .then(schema => {
          setSourceColumns(schema.columns.map(c => c.name));
          const numericTypes = new Set(["NUMERIC", "INTEGER", "DECIMAL", "FLOAT", "REAL", "DOUBLE", "BIGINT", "SMALLINT", "MONEY"]);
          setNumericColumns(
            schema.columns
              .filter(c => numericTypes.has(c.type.toUpperCase()))
              .map(c => c.name)
          );
        })
        .catch(() => { setSourceColumns([]); setNumericColumns([]); });
    } else {
      setSourceColumns([]);
      setNumericColumns([]);
    }
  }, [selectedDestId]);

  // Parse an expression like "CostInBillingCurrency * 0.186065" back to form fields
  const parseExpressionForForm = (expr: string, targetCol: string, config?: Record<string, unknown>) =>
    parseExpression(expr, targetCol, sourceColumns, config);

  const openNew = () => { setEditing(null); setForm(emptyForm); setDialogOpen(true); };

  const openEdit = (r: BusinessRule) => {
    setEditing(r);
    const cfg = r.config_json as Record<string, unknown> | undefined;
    let conds: CondRow[] = [];
    let parsed: Partial<typeof emptyForm> = {};

    if (r.rule_type === "CONDITIONAL" && r.conditions) {
      conds = (r.conditions as Record<string, unknown>[]).map((c, idx) => ({
        field: String(c.field || ""),
        op: String(c.op || "=="),
        value: Array.isArray(c.value) ? (c.value as string[]).join("|") : String(c.value || ""),
        logic: (["AND", "OR"].includes(String(c.logic || "").toUpperCase())
          ? String(c.logic).toUpperCase()
          : "AND") as "AND" | "OR",
      }));
      const thenField = String((r.conditions as Record<string, unknown>[])[0]?.then_field || "");
      const thenVal = String((r.conditions as Record<string, unknown>[])[0]?.then_value || "");
      parsed = parseExpressionForForm(thenVal, thenField || r.target_column, cfg);
    } else if (r.rule_type === "EXPRESSION") {
      parsed = parseExpressionForForm(r.expression || "", r.target_column, cfg);
    } else if (r.config_json) {
      // Legacy PTAX_BRL / TAX_BASE / MARKUP
      parsed = {
        source_column: String(cfg?.source_column || ""),
        operation: String(cfg?.operation || (cfg?.rate && Number(cfg.rate) > 1 ? "*" : "%")),
        rate: String(cfg?.rate || cfg?.percentage || ""),
        target_column: r.target_column,
      };
    }

    const rawScope = cfg?.scope_filter as Record<string, unknown> | undefined;

    setForm({
      name: r.name, description: r.description || "",
      source_column: parsed.source_column || "", operand_column: parsed.operand_column || "",
      operation: parsed.operation || "%",
      rate: parsed.rate || "",
      target_column: parsed.target_column || r.target_column || "",
      conditions: conds, priority: r.priority,
      cloud_scope: r.cloud_scope || "global",
      scope_filter: {
        field: String(rawScope?.field || ""),
        op: String(rawScope?.op || "="),
        value: String(rawScope?.value || ""),
      },
      valid_from: r.valid_from || "", valid_until: r.valid_until || "",
      active: r.active,
      financial_effect: r.financial_effect || "NONE",
      financial_contract_id: r.financial_contract_id || null,
      allocation_method: String(cfg?.allocation_method || ""),
      marketplace_connection_code: String(cfg?.marketplace_connection_code || ""),
      cloud_connection_code: String(cfg?.cloud_connection_code || ""),
      cloud_product_name: String(cfg?.cloud_product_name || ""),
      cloud_sku: String(cfg?.cloud_sku || ""),
      cloud_resource_group: String(cfg?.cloud_resource_group || ""),
    });
    setDialogOpen(true);
  };

  const save = async () => {
    const destId = selectedDestId === "" ? null : selectedDestId as number;
    const expression = buildExpressionFromForm(form);

    const hasConditions = form.conditions.some(isValidCondition);
    const isMarketplaceAllocation = form.financial_effect === "MARKETPLACE_ALLOCATION";
    const ruleType = isMarketplaceAllocation ? "MARKETPLACE_ALLOCATION" : hasConditions ? "CONDITIONAL" : "EXPRESSION";

    const configJson: Record<string, unknown> = {
      source_column: form.source_column,
      operation: form.operation,
      rate: parseFloat(form.rate) || 0,
    };
    if (isMarketplaceAllocation) {
      Object.assign(configJson, {
        allocation_method: "MARKETPLACE_USD_SHARE",
        marketplace_connection_code: form.marketplace_connection_code,
        cloud_connection_code: form.cloud_connection_code,
        cloud_product_name: form.cloud_product_name,
        cloud_sku: form.cloud_sku,
        cloud_resource_group: form.cloud_resource_group,
        test_period: "202608",
      });
    }
    if (form.operation === "col+" || form.operation === "col-") {
      configJson.operand_column = form.operand_column;
      delete configJson.rate;
    }
    if (isValidScopeFilter(form.scope_filter)) {
      configJson.scope_filter = {
        field: form.scope_filter.field,
        op: form.scope_filter.op,
        value: isValueLessOp(form.scope_filter.op) ? "" : form.scope_filter.value,
      };
    }

    const body: Record<string, unknown> = {
      destination_table_id: destId,
      name: form.name, description: form.description || null,
      target_column: form.target_column, rule_type: ruleType,
      cloud_scope: form.cloud_scope === "global" ? null : form.cloud_scope,
      config_json: configJson,
      priority: form.priority,
      valid_from: form.valid_from || null,
      valid_until: form.valid_until || null,
      active: form.active,
      financial_effect: form.financial_effect,
      financial_contract_id: form.financial_effect === "BUDGET_COMMITMENT" ? form.financial_contract_id : null,
    };

    if (ruleType === "EXPRESSION") {
      body.expression = expression;
    } else if (ruleType === "CONDITIONAL") {
      body.conditions = form.conditions
        .filter(isValidCondition)
        .map((c) => ({
          field: c.field,
          op: c.op,
          logic: c.logic || "AND",
          value: isValueLessOp(c.op)
            ? ""
            : c.op === "in"
              ? c.value.split("|").map(v => v.trim()).filter(Boolean)
              : c.value,
          then_field: form.target_column,
          then_value: expression,
        }));
    }

    try {
      if (editing) await updateBusinessRule(editing.id, body as Partial<BusinessRule>);
      else await createBusinessRule(body as Partial<BusinessRule>);
      setDialogOpen(false);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Delete?")) return;
    try { await deleteBusinessRule(id); load(); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  // Condition helpers
  const addCondition = () => setForm({
    ...form, conditions: [...form.conditions, { field: "", op: "=", value: "", logic: "AND" }],
  });
  const removeCondition = (i: number) => setForm({
    ...form, conditions: form.conditions.filter((_, idx) => idx !== i),
  });
  const updateCondition = (i: number, key: keyof CondRow, val: string) => {
    const updated = [...form.conditions];
    if (key === "logic") updated[i][key] = val === "OR" ? "OR" : "AND";
    else updated[i][key] = val;
    setForm({ ...form, conditions: updated });
  };

  // Format rule summary for the table
  const formatRuleSummary = (r: BusinessRule): string => {
    if (r.rule_type === "CONDITIONAL" && r.conditions) {
      const conds = r.conditions as Record<string, unknown>[];
      const parts = conds.map((c, i) => {
        const val = Array.isArray(c.value) ? (c.value as string[]).join("|") : String(c.value || "");
        const logic = i > 0 ? ` ${String(c.logic || "AND")} ` : "";
        const op = conditionOpLabel(String(c.op || "=="));
        const valPart = isValueLessOp(String(c.op || "")) ? "" : ` ${val}`;
        return `${logic}${c.field} ${op}${valPart}`;
      });
      const thenPart = (conds[0] as Record<string, unknown>)?.then_field || r.target_column;
      const thenVal = (conds[0] as Record<string, unknown>)?.then_value || "?";
      return `SE ${parts.join("")} → ${thenPart} = ${thenVal}`;
    }
    if (r.rule_type === "EXPRESSION" && r.expression) {
      return `${r.target_column} = ${r.expression}`;
    }
    return `${r.rule_type}: ${r.target_column}`;
  };

  const selectedDestName = selectedDestId ? destTables.find(t => t.id === selectedDestId)?.name : null;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2, gap: 2, flexWrap: "wrap" }}>
        {!embedded && <Typography variant="h4">Regras de Negócio</Typography>}
        <Button
          variant="contained" startIcon={<Add />}
          onClick={openNew}
          disabled={!selectedDestId}
        >
          Nova
        </Button>
      </Box>

      {/* ── Primary: Tabela Destino (mandatory) ── */}
      <Box sx={{ display: "flex", gap: 2, mb: 2, flexWrap: "wrap", alignItems: "center" }}>
        <FormControl sx={{ minWidth: 260 }} size="small">
          <InputLabel>Tabela Destino *</InputLabel>
          <Select
            value={selectedDestId}
            label="Tabela Destino *"
            onChange={(e) => {
              setSelectedDestId(e.target.value as number | "");
              setLoading(true);
            }}
          >
            {destTables.map((t) => (
              <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        {selectedDestName && (
          <Chip
            label={`${rules.length} regras em ${selectedDestName}`}
            color="primary" size="small"
          />
        )}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      {!selectedDestId ? (
        <Alert severity="info" sx={{ mb: 2 }}>
          Selecione uma Tabela Destino acima para visualizar as regras de negócio.
        </Alert>
      ) : loading ? (
        <CircularProgress sx={{ display: "block", mx: "auto", mt: 5 }} />
      ) : rules.length === 0 ? (
        <EmptyState message="Nenhuma regra de negócio para esta tabela destino. Clique em 'Nova' para começar." />
      ) : (
        <SortableTable
          data={rules}
          getRowKey={(r) => r.id}
          defaultSortKey="priority"
          defaultSortDir="asc"
          columns={[
            { key: "name", label: "Nome" },
            {
              key: "_operation", label: "Operação", width: 220,
              render: (r: BusinessRule) => (
                <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                  {formatOperationSummary(r)} → {r.target_column}
                </Typography>
              ),
            },
            {
              key: "_summary", label: "Regra", width: 350,
              render: (r: BusinessRule) => (
                <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                  {formatRuleSummary(r)}
                </Typography>
              ),
            },
            {
              key: "cloud_scope", label: "Cloud", width: 120,
              render: (r: BusinessRule) => (
                <Chip size="small" label={cloudScopeLabel(r.cloud_scope)} variant={r.cloud_scope ? "filled" : "outlined"} />
              ),
            },
            {
              key: "financial_effect", label: "Modo", width: 125,
              render: (r: BusinessRule) => (
                <Chip size="small" label={r.financial_effect === "BUDGET_COMMITMENT" ? "Compromisso" : r.financial_effect === "DIRECT_REALIZED" ? "Direto" : r.financial_effect === "MARKETPLACE_ALLOCATION" ? "Marketplace" : "Nenhum"} color={r.financial_effect === "BUDGET_COMMITMENT" ? "warning" : r.financial_effect === "DIRECT_REALIZED" ? "success" : r.financial_effect === "MARKETPLACE_ALLOCATION" ? "info" : "default"} />
              ),
            },
            {
              key: "priority", label: "Pri", width: 50,
              render: (r: BusinessRule) => r.priority,
            },
            {
              key: "active", label: "Ativo", width: 60,
              render: (r: BusinessRule) => r.active ? "Sim" : "Não",
            },
            {
              key: "_actions", label: "", width: 80, sortable: false,
              render: (r: BusinessRule) => (
                <>
                  <IconButton size="small" onClick={() => openEdit(r)}><Edit fontSize="small" /></IconButton>
                  <IconButton size="small" onClick={() => remove(r.id)} color="error"><Delete fontSize="small" /></IconButton>
                </>
              ),
            },
          ]}
        />
      )}

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? "Editar" : "Nova"} Regra de Negócio</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5, mt: 1 }}>
            {/* Context banner */}
            {selectedDestName && (
              <Alert severity="info" icon={false} sx={{ py: 0 }}>
                Tabela Destino: <strong>{selectedDestName}</strong>
              </Alert>
            )}

            <TextField label="Nome" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <TextField label="Descrição (opcional)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />

            <FormControl fullWidth>
              <InputLabel>Efeito financeiro</InputLabel>
              <Select
                value={form.financial_effect}
                label="Efeito financeiro"
                onChange={(e) => setForm({ ...form, financial_effect: e.target.value as typeof form.financial_effect, allocation_method: e.target.value === "MARKETPLACE_ALLOCATION" ? "MARKETPLACE_USD_SHARE" : "", financial_contract_id: e.target.value === "BUDGET_COMMITMENT" ? form.financial_contract_id : null })}
              >
                <MenuItem value="NONE">Não participa do realizado</MenuItem>
                <MenuItem value="DIRECT_REALIZED">Realizado direto — sem compromisso</MenuItem>
                <MenuItem value="BUDGET_COMMITMENT">Compromisso orçamentário</MenuItem>
                <MenuItem value="MARKETPLACE_ALLOCATION">Correlacionamento Marketplace</MenuItem>
              </Select>
            </FormControl>
            {form.financial_effect === "BUDGET_COMMITMENT" && (
              <FormControl fullWidth required>
                <InputLabel>Contrato financeiro</InputLabel>
                <Select value={form.financial_contract_id ?? ""} label="Contrato financeiro" onChange={(e) => setForm({ ...form, financial_contract_id: Number(e.target.value) || null })}>
                  {contracts.filter((contract) => contract.active).map((contract) => <MenuItem key={contract.id} value={contract.id}>{contract.name} ({contract.code})</MenuItem>)}
                </Select>
              </FormControl>
            )}

            <FormControl fullWidth>
              <InputLabel>Cloud (escopo da regra)</InputLabel>
              <Select
                value={form.cloud_scope}
                label="Cloud (escopo da regra)"
                onChange={(e) => setForm({ ...form, cloud_scope: e.target.value })}
              >
                {CLOUD_SCOPE_FORM_OPTIONS.map((s) => (
                  <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <Typography variant="caption" color="text.secondary" sx={{ mt: -1 }}>
              Regras com escopo Azure/GCP/etc. só rodam no reprocessamento da nuvem correspondente.
              Use &quot;Todas as nuvens&quot; para o fallback final (<code>realizado = BilledCost</code>).
            </Typography>

            <Divider />
            <Typography variant="subtitle2">Rateio Marketplace → Cloud</Typography>
            <Typography variant="caption" color="text.secondary">
              Preencha estes campos para criar uma regra de teste que distribui o realizado Cloud em BRL pela participação USD do Marketplace.
            </Typography>
            {form.financial_effect === "MARKETPLACE_ALLOCATION" && <>
              <TextField label="Conexão Marketplace" value={form.marketplace_connection_code} onChange={(e) => setForm({ ...form, marketplace_connection_code: e.target.value })} placeholder="mongodb-atlas-pro" />
              <TextField label="Conexão Cloud" value={form.cloud_connection_code} onChange={(e) => setForm({ ...form, cloud_connection_code: e.target.value })} placeholder="azure-ti" />
              <TextField label="Produto Cloud" value={form.cloud_product_name} onChange={(e) => setForm({ ...form, cloud_product_name: e.target.value })} placeholder="Private Offer..." />
              <TextField label="SKU Cloud" value={form.cloud_sku} onChange={(e) => setForm({ ...form, cloud_sku: e.target.value })} />
              <TextField label="Resource Group Cloud" value={form.cloud_resource_group} onChange={(e) => setForm({ ...form, cloud_resource_group: e.target.value })} />
            </>}

            <Divider />

            <Box sx={{ bgcolor: "#e3f2fd", borderRadius: 2, p: 2, border: "1px solid #90caf9" }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, color: "#1565c0", mb: 1 }}>
                Delimitador por linha (opcional)
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
                Filtro adicional sobre os dados já mapeados — ex.: <code>cloud_origem</code>, <code>ServiceProviderName</code>.
              </Typography>
              <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                <Autocomplete
                  freeSolo
                  size="small"
                  options={scopeColumnOptions}
                  value={form.scope_filter.field}
                  onInputChange={(_, v) => setForm({ ...form, scope_filter: { ...form.scope_filter, field: v } })}
                  onChange={(_, v) => setForm({ ...form, scope_filter: { ...form.scope_filter, field: v || "" } })}
                  renderInput={(params) => (
                    <TextField {...params} label="Campo" sx={{ width: 220 }} />
                  )}
                />
                <Select
                  size="small"
                  value={form.scope_filter.op}
                  onChange={(e) => setForm({ ...form, scope_filter: { ...form.scope_filter, op: e.target.value } })}
                  sx={{ minWidth: 140 }}
                >
                  <MenuItem value="=">= (igual)</MenuItem>
                  <MenuItem value="!=">!= (diferente)</MenuItem>
                  <MenuItem value="contains">contém</MenuItem>
                  <MenuItem value="in">em lista (|)</MenuItem>
                  <MenuItem value="not_empty">não vazio</MenuItem>
                  <MenuItem value="empty">vazio</MenuItem>
                </Select>
                {!isValueLessOp(form.scope_filter.op) && (
                  <TextField
                    size="small"
                    label="Valor"
                    value={form.scope_filter.value}
                    onChange={(e) => setForm({ ...form, scope_filter: { ...form.scope_filter, value: e.target.value } })}
                    placeholder={form.scope_filter.op === "in" ? "valor1|valor2" : "valor"}
                    sx={{ flex: 1, minWidth: 180 }}
                  />
                )}
              </Box>
            </Box>

            <Divider />

            {/* ═══════════════════════════════════════════════════════ */}
            {/*  SEÇÃO: SE (condições)                                   */}
            {/* ═══════════════════════════════════════════════════════ */}
            <Box sx={{ bgcolor: "#f3e5f5", borderRadius: 2, p: 2, border: "1px solid #ce93d8" }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1.5 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 600, color: "#7b1fa2", display: "flex", alignItems: "center", gap: 1 }}>
                  <Chip label="SE" size="small" color="secondary" sx={{ fontWeight: 700 }} />
                  Condições
                </Typography>
                <Button size="small" startIcon={<Add />} onClick={addCondition} variant="outlined" color="secondary">
                  Adicionar Condição
                </Button>
              </Box>

              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
                Use o seletor <strong>E / OU</strong> entre as linhas para definir a lógica. Deixe vazio para aplicar em <strong>todas as linhas</strong> (fallback padrão).
              </Typography>

              {form.conditions.length === 0 && (
                <Box sx={{ textAlign: "center", py: 1.5, color: "text.secondary", bgcolor: "#fafafa", borderRadius: 1, border: "1px dashed #ddd" }}>
                  <Typography variant="body2" sx={{ fontStyle: "italic" }}>
                    (sem condições — regra aplicada em todas as linhas)
                  </Typography>
                </Box>
              )}

              {form.conditions.map((cond, i) => (
                <Box key={i}>
                  {/* AND/OR connector (between rows) */}
                  {i > 0 && form.conditions[i - 1]?.field && cond.field && (
                    <Box sx={{ display: "flex", justifyContent: "center", my: 0.5 }}>
                      <FormControl size="small" sx={{ minWidth: 70 }}>
                        <Select
                          value={cond.logic}
                          onChange={(e) => updateCondition(i, "logic", e.target.value)}
                        >
                          <MenuItem value="AND">E</MenuItem>
                          <MenuItem value="OR">OU</MenuItem>
                        </Select>
                      </FormControl>
                    </Box>
                  )}

                  <Box sx={{ display: "flex", gap: 1, alignItems: "center", mb: 1, bgcolor: "#fff", p: 1, borderRadius: 1, border: "1px solid #E2E5EE" }}>
                    <Autocomplete
                      freeSolo
                      size="small"
                      options={sourceColumns}
                      value={cond.field}
                      onInputChange={(_, v) => updateCondition(i, "field", v)}
                      onChange={(_, v) => updateCondition(i, "field", v || "")}
                      renderInput={(params) => (
                        <TextField {...params} label="Campo" sx={{ width: 240 }} />
                      )}
                    />
                    <Select size="small" value={cond.op}
                      onChange={(e) => updateCondition(i, "op", e.target.value)}
                      sx={{ minWidth: 140 }}>
                      <MenuItem value="=">= (igual / exato)</MenuItem>
                      <MenuItem value="!=">!= (diferente)</MenuItem>
                      <MenuItem value="contains">contém (~)</MenuItem>
                      <MenuItem value="in">em lista (|)</MenuItem>
                      <MenuItem value="not_empty">não vazio</MenuItem>
                      <MenuItem value="empty">vazio</MenuItem>
                    </Select>
                    {!isValueLessOp(cond.op) && (
                    <TextField size="small" label="Valor" value={cond.value}
                      onChange={(e) => updateCondition(i, "value", e.target.value)}
                      placeholder={cond.op === "in" ? "valor1|valor2|..." : "valor"}
                      sx={{ flex: 1, minWidth: 180 }} />
                    )}
                    <IconButton size="small" onClick={() => removeCondition(i)} color="error">
                      <Delete fontSize="small" />
                    </IconButton>
                  </Box>
                </Box>
              ))}
            </Box>

            {/* ═══════════════════════════════════════════════════════ */}
            {/*  SEÇÃO: ENTÃO (operação)                                */}
            {/* ═══════════════════════════════════════════════════════ */}
            <Box sx={{ bgcolor: "#e8f5e9", borderRadius: 2, p: 2, border: "1px solid #a5d6a7" }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, color: "#2e7d32", display: "flex", alignItems: "center", gap: 1, mb: 1.5 }}>
                <Chip label="ENTÃO" size="small" color="success" sx={{ fontWeight: 700 }} />
                Operação
              </Typography>

              <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start", flexWrap: "wrap" }}>
                {form.operation !== "=" && (
                  <Autocomplete
                    freeSolo
                    size="small"
                    options={numericColumns}
                    value={form.source_column}
                    onInputChange={(_, v) => setForm({ ...form, source_column: v })}
                    onChange={(_, v) => setForm({ ...form, source_column: v || "" })}
                    renderInput={(params) => (
                      <TextField {...params} label="Coluna Origem" sx={{ width: 240 }}
                        helperText="Somente colunas numéricas" />
                    )}
                  />
                )}
                <FormControl size="small" sx={{ minWidth: 160 }}>
                  <InputLabel>Operação</InputLabel>
                  <Select value={form.operation} label="Operação"
                    onChange={(e) => {
                      const operation = e.target.value;
                      setForm({
                        ...form,
                        operation,
                        operand_column: operation === "col+" || operation === "col-" ? form.operand_column : "",
                        rate: operation === "col+" || operation === "col-" ? "" : form.rate,
                      });
                    }}>
                    {OPERATIONS.map(op => (
                      <MenuItem key={op.value} value={op.value}>{op.label} ({op.symbol})</MenuItem>
                    ))}
                  </Select>
                </FormControl>
                {(form.operation === "col+" || form.operation === "col-") ? (
                  <Autocomplete
                    freeSolo
                    size="small"
                    options={numericColumns.filter((c) => c !== form.source_column)}
                    value={form.operand_column}
                    onInputChange={(_, v) => setForm({ ...form, operand_column: v })}
                    onChange={(_, v) => setForm({ ...form, operand_column: v || "" })}
                    renderInput={(params) => (
                      <TextField {...params} label="Coluna Operando" sx={{ width: 240 }}
                        helperText="Segunda coluna numérica (ex.: CreditAmount)" />
                    )}
                  />
                ) : (
                  <TextField
                    size="small"
                    label={form.operation === "%" ? "Percentual" : form.operation === "=" ? "Valor Fixo" : "Taxa / Valor"}
                    value={form.rate}
                    onChange={(e) => setForm({ ...form, rate: e.target.value })}
                    type="number"
                    inputProps={{ step: "any" }}
                    sx={{ width: 130 }}
                  />
                )}
                <Typography variant="h6" sx={{ color: "#666", alignSelf: "center" }}>→</Typography>
                <Autocomplete
                  freeSolo
                  size="small"
                  options={numericColumns}
                  value={form.target_column}
                  onInputChange={(_, v) => setForm({ ...form, target_column: v })}
                  onChange={(_, v) => setForm({ ...form, target_column: v || "" })}
                  renderInput={(params) => (
                    <TextField {...params} label="Coluna Destino" sx={{ width: 220 }}
                      helperText="Onde escrever o resultado" />
                  )}
                />
              </Box>
            </Box>

            {/* ═══════════════════════════════════════════════════════ */}
            {/*  PREVIEW                                                 */}
            {/* ═══════════════════════════════════════════════════════ */}
            {(form.source_column || form.operation === "=" || ((form.operation === "col+" || form.operation === "col-") && form.operand_column))
              && (form.operation === "col+" || form.operation === "col-" || form.rate)
              && form.target_column && (
              <Paper variant="outlined" sx={{ p: 1.5, bgcolor: "#fafafa", borderLeft: "4px solid #1976d2" }}>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.3 }}>
                  Preview da regra
                </Typography>
                <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.82rem" }}>
                  {form.conditions.some(isValidCondition) ? (
                    <>
                      <Box component="span" sx={{ color: "#7b1fa2", fontWeight: 600 }}>SE </Box>
                      {form.conditions.filter(isValidCondition).map((c, i) => (
                        <Box component="span" key={i}>
                          {i > 0 && (
                            <Box component="span" sx={{ color: "#9c27b0", fontWeight: 700, mx: 0.5 }}>
                              {c.logic === "OR" ? "OU" : "E"}
                            </Box>
                          )}
                          <Box component="span" sx={{ color: "#1565c0" }}>{c.field}</Box>
                          <Box component="span" sx={{ color: "#666" }}> {conditionOpLabel(c.op)} </Box>
                          {!isValueLessOp(c.op) && (
                            <Box component="span" sx={{ color: "#e65100" }}>"{c.value}"</Box>
                          )}
                        </Box>
                      ))}
                      <br />
                      <Box component="span" sx={{ color: "#2e7d32", fontWeight: 600 }}>ENTÃO </Box>
                    </>
                  ) : (
                    <Box component="span" sx={{ color: "#2e7d32", fontWeight: 600 }}>SEMPRE </Box>
                  )}
                  {form.operation === "%" && <>{form.source_column} × {(parseFloat(form.rate) || 0) / 100}</>}
                  {form.operation === "*" && <>{form.source_column} × {form.rate}</>}
                  {form.operation === "+" && <>{form.source_column} + {form.rate}</>}
                  {form.operation === "-" && <>{form.source_column} − {form.rate}</>}
                  {form.operation === "col+" && <>{form.source_column} + {form.operand_column}</>}
                  {form.operation === "col-" && <>{form.source_column} − {form.operand_column}</>}
                  {form.operation === "=" && <>{form.rate}</>}
                  <Box component="span" sx={{ color: "#2e7d32", fontWeight: 600 }}> → {form.target_column}</Box>
                </Typography>
              </Paper>
            )}

            <Divider />

            {/* Priority + validity */}
            <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap", alignItems: "center" }}>
              <TextField label="Prioridade" type="number" value={form.priority}
                onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })}
                size="small" sx={{ width: 110 }}
                helperText="Ordem de aplicação" />
              <TextField label="Válido de (YYYYMM)" value={form.valid_from}
                onChange={(e) => setForm({ ...form, valid_from: e.target.value })} size="small" sx={{ width: 150 }} />
              <TextField label="Válido até (YYYYMM)" value={form.valid_until}
                onChange={(e) => setForm({ ...form, valid_until: e.target.value })} size="small" sx={{ width: 150 }} />
              <FormControlLabel
                control={<Switch checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />}
                label="Ativo"
              />
            </Box>

            {/* Cardinality hint for multi-rule strategy */}
            <Alert severity="info" sx={{ fontSize: "0.8rem" }}>
              <strong>Ordem de prioridade:</strong> números menores rodam primeiro. Regras específicas por cloud
              (ex.: Azure 20, 25) devem vir <strong>antes</strong> do fallback global (ex.: prioridade 100).
              Cada regra só preenche linhas ainda vazias — por isso o fallback precisa ser o último.
            </Alert>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={save} variant="contained" disabled={!form.name || !form.target_column || (form.financial_effect === "BUDGET_COMMITMENT" && !form.financial_contract_id)}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
