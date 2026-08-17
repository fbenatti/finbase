import { useState, useEffect } from "react";
import {
  Box, Typography, Button, IconButton, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Switch, FormControlLabel,
  Alert, CircularProgress, Divider, Autocomplete, Tabs, Tab,
} from "@mui/material";
import { Add, Edit, Delete, Download } from "@mui/icons-material";
import {
  getClassifications, createClassification, updateClassification,
  deleteClassification, ClassificationRule,
  getDepartments, Department,
  getDestinationTables, getDestinationSchema,
  DestinationTable,
  getAzureTenants, exportAzureTagPolicy, downloadText,
  AzureTenant, AzureTenantPolicy,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable from "../../components/SortableTable";

interface CondEntry { field: string; operator: string; values: string; logic: string }

const emptyForm = {
  name: "", target_field: "departamento",
  department_id: null as number | null,
  conditions: [{ field: "", operator: "=", values: "", logic: "AND" }] as CondEntry[],
  priority: 0, active: true,
};

export default function Classification() {
  const [tab, setTab] = useState(0);

  return (
    <Box>
      <Typography variant="h4" sx={{ mb: 1 }}>Regras de Classificação</Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
        <Tab label="Regras" />
        <Tab label="Criação de Tags" />
      </Tabs>
      {tab === 0 ? <RulesTab /> : <TagsTab />}
    </Box>
  );
}

function RulesTab() {
  const [rules, setRules] = useState<ClassificationRule[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [selectedDestId, setSelectedDestId] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ClassificationRule | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [destColumns, setDestColumns] = useState<string[]>([]);

  useEffect(() => {
    getDepartments(true).then(setDepartments).catch(() => {});
    getDestinationTables(true).then(setDestTables).catch(() => {});
  }, []);

  // Load destination columns for condition autocomplete
  useEffect(() => {
    if (selectedDestId) {
      getDestinationSchema(selectedDestId as number)
        .then(schema => setDestColumns(schema.columns.map(c => c.name)))
        .catch(() => setDestColumns([]));
    } else {
      setDestColumns([]);
    }
  }, [selectedDestId]);

  const load = async () => {
    try {
      setLoading(true);
      const destId = selectedDestId === "" ? undefined : selectedDestId as number;
      setRules(await getClassifications(undefined, undefined, destId));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [selectedDestId]);

  const openNew = () => {
    setEditing(null);
    setForm({ ...emptyForm, conditions: [{ field: "", operator: "=", values: "", logic: "AND" }] });
    setDialogOpen(true);
  };

  const openEdit = (r: ClassificationRule) => {
    setEditing(r);
    const conds = (r.conditions || []).map((c: Record<string, unknown>) => ({
      field: String(c.field || ""),
      operator: String(c.operator || "="),
      values: Array.isArray(c.values) ? c.values.join(" | ") : String(c.values || ""),
      logic: String(c.logic || "AND"),
    }));
    if (conds.length === 0) conds.push({ field: "", operator: "=", values: "", logic: "AND" });

    setForm({
      name: r.name,
      target_field: r.target_field,
      department_id: r.department_id,
      conditions: conds,
      priority: r.priority, active: r.active,
    });
    setDialogOpen(true);
  };

  const save = async () => {
    if (!form.department_id) {
      setError("Selecione o departamento responsável pela classificação.");
      return;
    }
    const destId = selectedDestId === "" ? null : selectedDestId as number;

    const body: Record<string, unknown> = {
      connection_id: null,
      destination_table_id: destId,
      department_id: form.department_id,
      name: form.name,
      target_field: "departamento",
      priority: form.priority, active: form.active,
      pattern_type: "multi_condition",
      match_value: form.department_id
        ? (departments.find(d => d.id === form.department_id)?.name || "")
        : form.name,
    };

    const validConds = form.conditions.filter(c => c.field && c.values);
    if (validConds.length > 0) {
      body.conditions = validConds.map(c => ({
        field: c.field,
        operator: c.operator,
        values: c.values.split("|").map(v => v.trim()).filter(Boolean),
        logic: c.logic,
      }));
      body.source_field = validConds[0].field;
      body.pattern = validConds
        .map(c => `${c.field}${c.operator}${c.values.replace(/\|/g, " | ")}`)
        .join(" ; ");
    }

    try {
      if (editing) await updateClassification(editing.id, body as Partial<ClassificationRule>);
      else await createClassification(body as Partial<ClassificationRule>);
      setDialogOpen(false);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Delete?")) return;
    try { await deleteClassification(id); load(); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const addCondition = () => setForm({
    ...form, conditions: [...form.conditions, { field: "", operator: "=", values: "", logic: "AND" }],
  });
  const removeCondition = (i: number) => setForm({
    ...form, conditions: form.conditions.filter((_, idx) => idx !== i),
  });
  const updateCondition = (i: number, key: keyof CondEntry, val: string) => {
    const updated = [...form.conditions];
    updated[i][key] = val;
    setForm({ ...form, conditions: updated });
  };

  const selectedDestName = selectedDestId ? destTables.find(t => t.id === selectedDestId)?.name : null;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "flex-end", mb: 3 }}>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            variant="contained" startIcon={<Add />}
            onClick={openNew}
            disabled={!selectedDestId}
          >
            Nova
          </Button>
        </Box>
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
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}

      {!selectedDestId ? (
        <Alert severity="info" sx={{ mb: 2 }}>
          Selecione uma Tabela Destino acima para visualizar as regras de classificação.
        </Alert>
      ) : loading ? (
        <CircularProgress sx={{ display: "block", mx: "auto", mt: 5 }} />
      ) : rules.length === 0 ? (
        <EmptyState message="Nenhuma regra de classificação para esta tabela destino." />
      ) : (
        <SortableTable
          data={rules as unknown as Record<string, unknown>[]}
          getRowKey={(r) => (r as unknown as ClassificationRule).id}
          defaultSortKey="priority"
          defaultSortDir="asc"
          columns={[
            { key: "name", label: "Nome" },
            {
              key: "_conditions", label: "Condições", width: 300,
              render: (r: Record<string, unknown>) => {
                const rule = r as unknown as ClassificationRule;
                const conds = rule.conditions || [];
                return (
                  <Typography variant="body2" sx={{ fontSize: "0.7rem", fontFamily: "monospace" }}>
                    {conds.length > 0
                      ? conds.slice(0, 2).map((c: Record<string, unknown>) =>
                          `${c.field || "?"}${c.operator || "="}${Array.isArray(c.values) ? (c.values as string[]).join("|") : c.values}`
                        ).join(" ; ")
                      : rule.pattern || "-"}
                  </Typography>
                );
              },
            },
            {
              key: "target_field", label: "Campo Dest.", width: 100,
            },
            {
              key: "_dept", label: "Departamento", width: 140,
              render: (r: Record<string, unknown>) => {
                const d = departments.find(dep => dep.id === (r as unknown as ClassificationRule).department_id);
                return d?.name || "-";
              },
            },
            { key: "priority", label: "Pri", width: 50 },
            {
              key: "active", label: "Ativo", width: 60,
              render: (r: Record<string, unknown>) => (r as unknown as ClassificationRule).active ? "Sim" : "Não",
            },
            {
              key: "_actions", label: "", width: 80, sortable: false,
              render: (r: Record<string, unknown>) => (
                <>
                  <IconButton size="small" onClick={() => openEdit(r as unknown as ClassificationRule)}>
                    <Edit fontSize="small" />
                  </IconButton>
                  <IconButton size="small"
                    onClick={() => remove((r as unknown as ClassificationRule).id)} color="error">
                    <Delete fontSize="small" />
                  </IconButton>
                </>
              ),
            },
          ]}
        />
      )}

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? "Editar" : "Nova"} Classificação</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            {/* Context banner */}
            {selectedDestName && (
              <Alert severity="info" icon={false} sx={{ py: 0 }}>
                Tabela Destino: <strong>{selectedDestName}</strong>
              </Alert>
            )}

            <TextField label="Nome" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />

            <FormControl fullWidth>
              <InputLabel>Departamento</InputLabel>
              <Select
                value={form.department_id ?? ""}
                label="Departamento"
                onChange={(e) => setForm({ ...form, department_id: e.target.value ? Number(e.target.value) : null })}
              >
                <MenuItem value="">Nenhum</MenuItem>
                {departments.map((d) => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
              </Select>
            </FormControl>

            <Divider />
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <Typography variant="subtitle2">
                Condições ({form.conditions.filter(c => c.field).length} definidas)
              </Typography>
              <Button size="small" onClick={addCondition}>+ Condição</Button>
            </Box>

            {form.conditions.map((cond, i) => (
              <Box key={i} sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                <Autocomplete
                  freeSolo size="small"
                  options={destColumns}
                  value={cond.field}
                  onInputChange={(_, v) => updateCondition(i, "field", v)}
                  onChange={(_, v) => updateCondition(i, "field", v || "")}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Campo"
                      sx={{ width: 200 }}
                      helperText={destColumns.length === 0 ? "Selecione tabela destino no topo" : undefined}
                    />
                  )}
                />
                <Select size="small" value={cond.operator}
                  onChange={(e) => updateCondition(i, "operator", e.target.value)}
                  sx={{ width: 80 }}>
                  <MenuItem value="=">= (igual)</MenuItem>
                  <MenuItem value="~">~ (contem)</MenuItem>
                </Select>
                <TextField size="small" label="Valores (| = OR)"
                  value={cond.values}
                  onChange={(e) => updateCondition(i, "values", e.target.value)}
                  sx={{ width: 220 }}
                  helperText="Use | para separar valores (OR)" />
                <IconButton size="small" onClick={() => removeCondition(i)} color="error">
                  <Delete fontSize="small" />
                </IconButton>
              </Box>
            ))}

            <TextField label="Prioridade" type="number" value={form.priority}
              onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })} />
            <FormControlLabel
              control={<Switch checked={form.active}
                onChange={(e) => setForm({ ...form, active: e.target.checked })} />}
              label="Ativo" />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button onClick={save} variant="contained"
            disabled={!form.name || !form.conditions.some(c => c.field)}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

function TagsTab() {
  const [tenants, setTenants] = useState<AzureTenant[]>([]);
  const [selectedTenant, setSelectedTenant] = useState<number | "">("");
  const [rows, setRows] = useState<AzureTenantPolicy[]>([]);
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    getAzureTenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  const generate = async () => {
    try {
      setLoading(true);
      setError("");
      setSuccess("");
      const connId = selectedTenant === "" ? undefined : selectedTenant as number;
      const result = await exportAzureTagPolicy(connId, "both");
      setRows(result.tenants);
      setScript(result.deployment_script);
      setSuccess(`Políticas geradas para ${result.tenants.length} tenant(s).`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao gerar");
    } finally {
      setLoading(false);
    }
  };

  const downloadAll = () => {
    const timestamp = new Date().toISOString().slice(0, 10);
    rows.forEach((t) => {
      downloadText(
        `azure-tag-policy-${t.connection_code}-${timestamp}.json`,
        JSON.stringify(t, null, 2),
      );
    });
    if (script) {
      downloadText(`azure-tag-policy-deploy-${timestamp}.sh`, script, "text/x-shellscript");
    }
  };

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}

      <Box sx={{ display: "flex", gap: 2, mb: 3, flexWrap: "wrap", alignItems: "center" }}>
        <FormControl sx={{ minWidth: 280 }} size="small">
          <InputLabel>Tenant Azure *</InputLabel>
          <Select
            value={selectedTenant}
            label="Tenant Azure *"
            onChange={(e) => setSelectedTenant(e.target.value as number | "")}
          >
            <MenuItem value="">Todos os tenants</MenuItem>
            {tenants.map((t) => (
              <MenuItem key={t.connection_id} value={t.connection_id}>
                {t.name} ({t.code})
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <Button variant="contained" onClick={generate} disabled={loading}>
          {loading ? "Gerando..." : "Gerar políticas"}
        </Button>
        <Button variant="outlined" startIcon={<Download />} onClick={downloadAll}
          disabled={rows.length === 0}>
          Baixar arquivos
        </Button>
      </Box>

      {rows.length === 0 && !loading ? (
        <Alert severity="info">
          Selecione um tenant e clique em "Gerar políticas" para gerar os arquivos de
          Azure Policy (JSON + script de deploy) a partir das regras de classificação.
        </Alert>
      ) : (
        rows.map((t) => (
          <Box key={t.connection_code} sx={{ mb: 3, p: 2, border: "1px solid #e0e0e0", borderRadius: 2 }}>
            <Typography variant="subtitle1" fontWeight={700}>
              {t.connection_name} <Chip size="small" label={`${t.rule_count} regras`} sx={{ ml: 1 }} />
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
              Tenant ID: {t.tenant_id} · Cod: {t.connection_code}
            </Typography>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
              <LayerSummary label="Herança de RG"
                count={Array.isArray(t.layers?.inheritance) ? (t.layers.inheritance as unknown[]).length : 0} />
              <LayerSummary label="Resource Groups (agrupados)"
                count={Array.isArray(t.layers?.resource_groups) ? (t.layers.resource_groups as unknown[]).length : 0} />
              <LayerSummary label="Padrões (nome/SKU/região)"
                count={Array.isArray(t.layers?.patterns) ? (t.layers.patterns as unknown[]).length : 0} />
            </Box>
          </Box>
        ))
      )}
    </Box>
  );
}

function LayerSummary({ label, count }: { label: string; count: number }) {
  return (
    <Box sx={{ display: "flex", justifyContent: "space-between", py: 0.25 }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="body2" fontWeight={600}>{count}</Typography>
    </Box>
  );
}
