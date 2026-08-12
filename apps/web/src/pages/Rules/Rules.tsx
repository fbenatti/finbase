import { useState, useEffect } from "react";
import {
  Box, Typography, Button, IconButton, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Switch, FormControlLabel,
  Alert, CircularProgress, Divider, Paper, Autocomplete,
} from "@mui/material";
import { Add, Edit, Delete } from "@mui/icons-material";
import {
  getBusinessRules, createBusinessRule, updateBusinessRule, deleteBusinessRule, BusinessRule,
  getDestinationTables, getDestinationSchema, DestinationTable,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable from "../../components/SortableTable";

const OPERATIONS: { value: string; label: string; symbol: string }[] = [
  { value: "%", label: "Porcentagem", symbol: "%" },
  { value: "*", label: "Multiplicar", symbol: "×" },
  { value: "+", label: "Somar", symbol: "+" },
  { value: "-", label: "Subtrair", symbol: "−" },
  { value: "=", label: "Valor Fixo", symbol: "=" },
];

interface CondRow {
  field: string;
  op: string;
  value: string;
  logic: "AND" | "OR";
}

const emptyForm = {
  name: "", description: "",
  source_column: "", operation: "%", rate: "",
  target_column: "",
  conditions: [] as CondRow[],
  priority: 0, valid_from: "", valid_until: "", active: true,
};

export default function Rules() {
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

  useEffect(() => {
    getDestinationTables(true).then(setDestTables).catch(() => {});
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
  const parseExpression = (expr: string, targetCol: string): Partial<typeof emptyForm> => {
    if (!expr) return {};
    const col = sourceColumns.find(c => expr.includes(c)) || "";
    let op = "%";
    let rate = "";
    // Try to parse operation and rate from expression
    const match = expr.match(/^(.+?)\s*([*/+-])\s*([\d.]+)$/);
    if (match) {
      const [, src, sop, val] = match;
      op = sop === "*" ? (parseFloat(val) < 100 ? "*" : "%") : sop;
      rate = val;
    }
    // Fixed value pattern: just a number
    if (/^[\d.]+$/.test(expr.trim())) {
      op = "=";
      rate = expr.trim();
    }
    return { source_column: col || "", operation: op, rate, target_column: targetCol };
  };

  const openNew = () => { setEditing(null); setForm(emptyForm); setDialogOpen(true); };

  const openEdit = (r: BusinessRule) => {
    setEditing(r);
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
      parsed = parseExpression(thenVal, thenField || r.target_column);
    } else if (r.rule_type === "EXPRESSION") {
      parsed = parseExpression(r.expression || "", r.target_column);
    } else if (r.config_json) {
      // Legacy PTAX_BRL / TAX_BASE / MARKUP
      const cfg = r.config_json as Record<string, unknown>;
      parsed = {
        source_column: String(cfg.source_column || ""),
        operation: String(cfg.operation || (cfg.rate && Number(cfg.rate) > 1 ? "*" : "%")),
        rate: String(cfg.rate || cfg.percentage || ""),
        target_column: r.target_column,
      };
    }

    setForm({
      name: r.name, description: r.description || "",
      source_column: parsed.source_column || "", operation: parsed.operation || "%",
      rate: parsed.rate || "",
      target_column: parsed.target_column || r.target_column || "",
      conditions: conds, priority: r.priority,
      valid_from: r.valid_from || "", valid_until: r.valid_until || "",
      active: r.active,
    });
    setDialogOpen(true);
  };

  const save = async () => {
    const destId = selectedDestId === "" ? null : selectedDestId as number;

    // Build expression from operation
    const src = form.source_column;
    const rv = parseFloat(form.rate) || 0;
    let expression = "";
    if (form.operation === "%" && src) {
      expression = `${src} * ${rv / 100}`;
    } else if (form.operation === "*" && src) {
      expression = `${src} * ${rv}`;
    } else if (form.operation === "+" && src) {
      expression = `${src} + ${rv}`;
    } else if (form.operation === "-" && src) {
      expression = `${src} - ${rv}`;
    } else if (form.operation === "=") {
      expression = String(rv);
    }

    const hasConditions = form.conditions.some(c => c.field && c.value);
    const ruleType = hasConditions ? "CONDITIONAL" : "EXPRESSION";

    const body: Record<string, unknown> = {
      destination_table_id: destId,
      name: form.name, description: form.description || null,
      target_column: form.target_column, rule_type: ruleType,
      config_json: {
        source_column: form.source_column,
        operation: form.operation,
        rate: parseFloat(form.rate) || 0,
      },
      priority: form.priority,
      valid_from: form.valid_from || null,
      valid_until: form.valid_until || null,
      active: form.active,
    };

    if (ruleType === "EXPRESSION") {
      body.expression = expression;
    } else {
      body.conditions = form.conditions
        .filter(c => c.field && c.value)
        .map((c, idx) => ({
          field: c.field,
          op: c.op,
          logic: c.logic || "AND",
          value: c.op === "in" ? c.value.split("|").map(v => v.trim()).filter(Boolean) : c.value,
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
    updated[i][key] = val;
    setForm({ ...form, conditions: updated });
  };

  // Format rule summary for the table
  const formatRuleSummary = (r: BusinessRule): string => {
    if (r.rule_type === "CONDITIONAL" && r.conditions) {
      const conds = r.conditions as Record<string, unknown>[];
      const parts = conds.map((c, i) => {
        const val = Array.isArray(c.value) ? (c.value as string[]).join("|") : String(c.value || "");
        const logic = i > 0 ? ` ${String(c.logic || "AND")} ` : "";
        return `${logic}${c.field} ${c.op} ${val}`;
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
      <Box sx={{ display: "flex", justifyContent: "space-between", mb: 3 }}>
        <Typography variant="h4">Regras de Negócio</Typography>
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

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

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
              key: "_summary", label: "Regra", width: 350,
              render: (r: BusinessRule) => (
                <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                  {formatRuleSummary(r)}
                </Typography>
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

                  <Box sx={{ display: "flex", gap: 1, alignItems: "center", mb: 1, bgcolor: "#fff", p: 1, borderRadius: 1, border: "1px solid #e0e0e0" }}>
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
                    </Select>
                    <TextField size="small" label="Valor" value={cond.value}
                      onChange={(e) => updateCondition(i, "value", e.target.value)}
                      placeholder={cond.op === "in" ? "valor1|valor2|..." : "valor"}
                      sx={{ flex: 1, minWidth: 180 }} />
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
                <FormControl size="small" sx={{ minWidth: 140 }}>
                  <InputLabel>Operação</InputLabel>
                  <Select value={form.operation} label="Operação"
                    onChange={(e) => setForm({ ...form, operation: e.target.value })}>
                    {OPERATIONS.map(op => (
                      <MenuItem key={op.value} value={op.value}>{op.label} ({op.symbol})</MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <TextField
                  size="small"
                  label={form.operation === "%" ? "Percentual" : form.operation === "=" ? "Valor Fixo" : "Taxa / Valor"}
                  value={form.rate}
                  onChange={(e) => setForm({ ...form, rate: e.target.value })}
                  type="number"
                  inputProps={{ step: "any" }}
                  sx={{ width: 130 }}
                />
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
            {(form.source_column || form.operation === "=") && form.rate && form.target_column && (
              <Paper variant="outlined" sx={{ p: 1.5, bgcolor: "#fafafa", borderLeft: "4px solid #1976d2" }}>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.3 }}>
                  Preview da regra
                </Typography>
                <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.82rem" }}>
                  {form.conditions.filter(c => c.field && c.value).length > 0 ? (
                    <>
                      <Box component="span" sx={{ color: "#7b1fa2", fontWeight: 600 }}>SE </Box>
                      {form.conditions.filter(c => c.field && c.value).map((c, i) => (
                        <Box component="span" key={i}>
                          {i > 0 && (
                            <Box component="span" sx={{ color: "#9c27b0", fontWeight: 700, mx: 0.5 }}>
                              {c.logic === "OR" ? "OU" : "E"}
                            </Box>
                          )}
                          <Box component="span" sx={{ color: "#1565c0" }}>{c.field}</Box>
                          <Box component="span" sx={{ color: "#666" }}> {c.op} </Box>
                          <Box component="span" sx={{ color: "#e65100" }}>"{c.value}"</Box>
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
              <strong>Estratégia de fallback:</strong> Crie uma regra para cada condição com prioridades diferentes
              (ex: 0, 1, 2) e uma regra final <strong>sem condições</strong> como valor padrão.
              As regras são executadas em ordem de prioridade e <strong>não sobrescrevem</strong> valores já preenchidos.
            </Alert>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={save} variant="contained" disabled={!form.name || !form.target_column}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
