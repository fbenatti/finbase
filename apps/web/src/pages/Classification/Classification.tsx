import { useState, useEffect } from "react";
import {
  Box, Typography, Button, IconButton, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Switch, FormControlLabel,
  Alert, CircularProgress, Divider, Autocomplete, Tabs, Tab,
} from "@mui/material";
import { Add, Edit, Delete, Download, ContentCopy, Check, Code, Terminal } from "@mui/icons-material";
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
import DepartmentTreePicker, { flattenDepartments } from "../../components/DepartmentTreePicker";

interface CondEntry { field: string; operator: string; values: string; logic: string }

const CLOUD_FILTER_OPTIONS = [
  { value: "every", label: "Todas as regras" },
  { value: "global", label: "Global (cross-cloud, sem escopo)" },
  { value: "azure", label: "Azure" },
  { value: "gcp", label: "GCP" },
  { value: "atlas", label: "MongoDB Atlas" },
  { value: "aws", label: "AWS" },
  { value: "oci", label: "Oracle (OCI)" },
  { value: "ibm", label: "IBM Cloud" },
] as const;

const CLOUD_SCOPE_FORM_OPTIONS = [
  { value: "global", label: "Global (cross-cloud — todas as clouds)" },
  { value: "azure", label: "Azure" },
  { value: "gcp", label: "GCP" },
  { value: "atlas", label: "MongoDB Atlas" },
  { value: "aws", label: "AWS" },
  { value: "oci", label: "Oracle (OCI)" },
  { value: "ibm", label: "IBM Cloud" },
] as const;

const cloudScopeLabel = (scope: string | null | undefined) => {
  if (!scope) return "Global (cross-cloud)";
  return CLOUD_SCOPE_FORM_OPTIONS.find((o) => o.value === scope)?.label ?? scope.toUpperCase();
};

const filterRulesByCloud = (rules: ClassificationRule[], cloudFilter: string) => {
  if (cloudFilter === "every") return rules;
  if (cloudFilter === "global") {
    return rules.filter((r) => !r.cloud_scope);
  }
  return rules.filter((r) => r.cloud_scope === cloudFilter);
};

const emptyForm = {
  name: "", target_field: "departamento",
  department_id: null as number | null,
  cloud_scope: "global" as string,
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
  const [cloudFilter, setCloudFilter] = useState<string>("every");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ClassificationRule | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [destColumns, setDestColumns] = useState<string[]>([]);

  useEffect(() => {
    getDepartments(false).then(setDepartments).catch(() => {});
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

  const flatDepartments = flattenDepartments(departments);

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
      cloud_scope: r.cloud_scope || "global",
      conditions: conds,
      priority: r.priority, active: r.active,
    });
    setDialogOpen(true);
  };

  const hasValidConditions = form.conditions.some(c => c.field.trim() && c.values.trim());

  const save = async () => {
    if (!form.department_id) {
      setError("Selecione o departamento responsável pela classificação.");
      return;
    }
    if (!hasValidConditions) {
      setError("Defina ao menos uma condição com campo e valor.");
      return;
    }
    const destId = selectedDestId === "" ? null : selectedDestId as number;

    const body: Record<string, unknown> = {
      connection_id: editing?.connection_id ?? null,
      destination_table_id: destId,
      department_id: form.department_id,
      name: form.name,
      target_field: "departamento",
      priority: form.priority, active: form.active,
      cloud_scope: form.cloud_scope === "global" ? null : form.cloud_scope,
      pattern_type: "multi_condition",
      match_value: form.department_id
        ? (flatDepartments.find((d) => d.id === form.department_id)?.name || "")
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
  const filteredRules = filterRulesByCloud(rules, cloudFilter);

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
            label={`${filteredRules.length} regras em ${selectedDestName}`}
            color="primary" size="small"
          />
        )}

        <FormControl sx={{ minWidth: 200 }} size="small">
          <InputLabel>Cloud</InputLabel>
          <Select
            value={cloudFilter}
            label="Cloud"
            onChange={(e) => setCloudFilter(e.target.value)}
          >
            {CLOUD_FILTER_OPTIONS.map((s) => (
              <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}

      {!selectedDestId ? (
        <Alert severity="info" sx={{ mb: 2 }}>
          Selecione uma Tabela Destino acima para visualizar as regras de classificação.
        </Alert>
      ) : loading ? (
        <CircularProgress sx={{ display: "block", mx: "auto", mt: 5 }} />
      ) : filteredRules.length === 0 ? (
        <EmptyState message="Nenhuma regra de classificação para esta tabela destino e cloud." />
      ) : (
        <SortableTable
          data={filteredRules as unknown as Record<string, unknown>[]}
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
                      : "-"}
                  </Typography>
                );
              },
            },
            {
              key: "target_field", label: "Campo Dest.", width: 100,
            },
            {
              key: "cloud_scope", label: "Cloud", width: 90,
              render: (r: Record<string, unknown>) => (
                <Chip
                  size="small"
                  label={cloudScopeLabel((r as unknown as ClassificationRule).cloud_scope)}
                  variant="outlined"
                  sx={{ fontSize: "0.65rem", height: 22 }}
                />
              ),
            },
            {
              key: "_dept", label: "Departamento", width: 140,
              render: (r: Record<string, unknown>) => {
                const d = flatDepartments.find((dep) => dep.id === (r as unknown as ClassificationRule).department_id);
                return d?.path || d?.name || "-";
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

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
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

            <DepartmentTreePicker
              departments={departments}
              value={form.department_id}
              onChange={(departmentId) => setForm({ ...form, department_id: departmentId })}
              label="Departamento"
              helperText="Escolha o departamento na hierarquia, como em Departamentos > Editar Departamento."
            />

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
              Define em quais provedores esta regra é aplicada no reprocessamento (ex.: GCP não usa regras Azure).
            </Typography>

            <Divider />
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <Typography variant="subtitle2">
                Condições ({form.conditions.filter(c => c.field && c.values).length} definidas)
              </Typography>
              <Button size="small" onClick={addCondition}>+ Condição</Button>
            </Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: -0.5 }}>
              Use | nos valores para OR entre opções do mesmo campo.
              {destColumns.length === 0 ? " Selecione a tabela destino no topo para listar os campos FOCUS." : ""}
            </Typography>

            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1.2fr) 120px minmax(0, 1.4fr) 40px",
                columnGap: 1.5,
                rowGap: 1,
                alignItems: "start",
              }}
            >
              <Typography variant="caption" color="text.secondary" fontWeight={600}>Campo</Typography>
              <Typography variant="caption" color="text.secondary" fontWeight={600}>Operador</Typography>
              <Typography variant="caption" color="text.secondary" fontWeight={600}>Valores</Typography>
              <Box />

              {form.conditions.map((cond, i) => (
                <Box key={i} sx={{ display: "contents" }}>
                  <Autocomplete
                    freeSolo
                    size="small"
                    options={destColumns}
                    value={cond.field}
                    onInputChange={(_, v) => updateCondition(i, "field", v)}
                    onChange={(_, v) => updateCondition(i, "field", v || "")}
                    sx={{ minWidth: 0 }}
                    renderInput={(params) => (
                      <TextField {...params} placeholder="SubAccountId" fullWidth />
                    )}
                  />
                  <FormControl size="small" fullWidth>
                    <Select
                      value={cond.operator}
                      onChange={(e) => updateCondition(i, "operator", e.target.value)}
                    >
                      <MenuItem value="=">= (igual)</MenuItem>
                      <MenuItem value="~">~ (contém)</MenuItem>
                    </Select>
                  </FormControl>
                  <TextField
                    size="small"
                    placeholder="valor1 | valor2"
                    value={cond.values}
                    onChange={(e) => updateCondition(i, "values", e.target.value)}
                    fullWidth
                  />
                  <IconButton
                    size="small"
                    onClick={() => removeCondition(i)}
                    color="error"
                    sx={{ mt: 0.25 }}
                  >
                    <Delete fontSize="small" />
                  </IconButton>
                </Box>
              ))}
            </Box>

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
            disabled={!form.name || !hasValidConditions}>
            Salvar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// ── Syntax Highlighting Helpers (Light Theme) ──

function highlightJsonHtml(jsonStr: string): string {
  const entityMap: Record<string, string> = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
  };
  const safe = jsonStr.replace(/[&<>]/g, (s) => entityMap[s] || s);

  return safe.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let clsColor = "#098658"; // number -> green
      let fontWeight = "normal";

      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          clsColor = "#0451a5"; // key -> dark blue
          fontWeight = "600";
        } else {
          clsColor = "#a31515"; // string -> red/wine
        }
      } else if (/true|false/.test(match)) {
        clsColor = "#0000ff"; // boolean -> blue
        fontWeight = "600";
      } else if (/null/.test(match)) {
        clsColor = "#800080"; // null -> purple
        fontWeight = "600";
      }
      return `<span style="color: ${clsColor}; font-weight: ${fontWeight};">${match}</span>`;
    }
  );
}

function highlightBashHtml(bashStr: string): string {
  const entityMap: Record<string, string> = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
  };
  const safe = bashStr.replace(/[&<>]/g, (s) => entityMap[s] || s);

  const lines = safe.split("\n");
  const highlightedLines = lines.map((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("#")) {
      return `<span style="color: #008000; font-style: italic;">${line}</span>`;
    }
    let res = line;
    // Highlight variables: $VAR or ${VAR}
    res = res.replace(/(\$\{?[a-zA-Z0-9_]+\}?)/g, '<span style="color: #e36209; font-weight: 600;">$1</span>');
    // Highlight az and common shell commands
    res = res.replace(/\b(az\s+policy\s+[a-z]+|az\s+[a-z]+|set\s+-[a-z]+|echo|cat|mktemp)\b/g, '<span style="color: #005cc5; font-weight: 600;">$1</span>');
    // Highlight flags --foo
    res = res.replace(/(--[a-zA-Z0-9-]+)/g, '<span style="color: #6f42c1; font-weight: 600;">$1</span>');
    // Highlight strings
    res = res.replace(/(".*?"|'.*?')/g, '<span style="color: #032f62;">$1</span>');
    return res;
  });

  return highlightedLines.join("\n");
}

function TagsTab() {
  const [tenants, setTenants] = useState<AzureTenant[]>([]);
  const [selectedTenant, setSelectedTenant] = useState<number | "">("");
  const [rows, setRows] = useState<AzureTenantPolicy[]>([]);
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [viewTab, setViewTab] = useState<"script" | "json">("script");
  const [selectedJsonTenantIndex, setSelectedJsonTenantIndex] = useState(0);
  const [isEditing, setIsEditing] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    getAzureTenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  const generate = async () => {
    try {
      setLoading(true);
      setError("");
      setSuccess("");
      const connId = selectedTenant === "" ? undefined : (selectedTenant as number);
      const result = await exportAzureTagPolicy(connId, "both");
      setRows(result.tenants);
      setScript(result.deployment_script);
      setSelectedJsonTenantIndex(0);
      setIsEditing(false);
      setSuccess(`Políticas geradas com sucesso para ${result.tenants.length} tenant(s).`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao gerar políticas");
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    setCopied(label);
    setTimeout(() => setCopied(null), 2500);
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

  const downloadCurrentJson = () => {
    const current = rows[selectedJsonTenantIndex];
    if (!current) return;
    const timestamp = new Date().toISOString().slice(0, 10);
    downloadText(
      `azure-tag-policy-${current.connection_code}-${timestamp}.json`,
      JSON.stringify(current, null, 2),
    );
  };

  const downloadCurrentScript = () => {
    if (!script) return;
    const timestamp = new Date().toISOString().slice(0, 10);
    downloadText(`azure-tag-policy-deploy-${timestamp}.sh`, script, "text/x-shellscript");
  };

  const currentJsonString = JSON.stringify(rows[selectedJsonTenantIndex] || {}, null, 2);

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
        <Button
          variant="outlined"
          startIcon={<Download />}
          onClick={downloadAll}
          disabled={rows.length === 0}
        >
          Baixar todos os arquivos
        </Button>
      </Box>

      {rows.length === 0 && !loading ? (
        <Alert severity="info">
          Selecione um tenant e clique em <strong>"Gerar políticas"</strong> para inspecionar o script Bash e os JSONs de Azure Policy antes de realizar o download ou implantação.
        </Alert>
      ) : (
        <Box>
          {/* Tenant Summary Cards */}
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(auto-fit, minmax(320px, 1fr))" }, gap: 2, mb: 3 }}>
            {rows.map((t) => (
              <Box key={t.connection_code} sx={{ p: 2, border: "1px solid #e0e0e0", borderRadius: 2, bgcolor: "#ffffff", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
                <Typography variant="subtitle1" fontWeight={700}>
                  {t.connection_name} <Chip size="small" color="primary" label={`${t.rule_count} regras`} sx={{ ml: 1 }} />
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
                  Tenant ID: {t.tenant_id} · Cod: {t.connection_code}
                </Typography>
                <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                  <LayerSummary
                    label="Herança de RG (Built-in)"
                    count={Array.isArray(t.layers?.inheritance) ? (t.layers.inheritance as unknown[]).length : 0}
                  />
                  <LayerSummary
                    label="Resource Groups Exatos (agrupados)"
                    count={Array.isArray(t.layers?.resource_groups) ? (t.layers.resource_groups as unknown[]).length : 0}
                  />
                  <LayerSummary
                    label="Resource Groups por Padrão (~)"
                    count={Array.isArray(t.layers?.rg_patterns) ? (t.layers.rg_patterns as unknown[]).length : 0}
                  />
                  <LayerSummary
                    label="Recursos Individuais / SKUs / Tipos"
                    count={Array.isArray(t.layers?.patterns) ? (t.layers.patterns as unknown[]).length : 0}
                  />
                </Box>
              </Box>
            ))}
          </Box>

          {/* Text Output Viewer with Sub-tabs & Syntax Highlighting */}
          <Box sx={{ border: "1px solid #d0d7de", borderRadius: 2, overflow: "hidden", bgcolor: "#ffffff", boxShadow: "0 1px 4px rgba(0,0,0,0.06)" }}>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #e1e4e8", px: 2, pt: 1, bgcolor: "#f6f8fa", flexWrap: "wrap", gap: 1 }}>
              <Tabs
                value={viewTab}
                onChange={(_, v) => setViewTab(v)}
                sx={{ minHeight: 44 }}
              >
                <Tab
                  value="script"
                  icon={<Terminal fontSize="small" />}
                  iconPosition="start"
                  label="Script de Deploy (.sh)"
                  sx={{ minHeight: 44, textTransform: "none", fontWeight: 600 }}
                />
                <Tab
                  value="json"
                  icon={<Code fontSize="small" />}
                  iconPosition="start"
                  label="Políticas JSON (.json)"
                  sx={{ minHeight: 44, textTransform: "none", fontWeight: 600 }}
                />
              </Tabs>

              <Box sx={{ display: "flex", gap: 1, pb: 1, alignItems: "center" }}>
                <FormControlLabel
                  control={
                    <Switch
                      size="small"
                      checked={isEditing}
                      onChange={(e) => setIsEditing(e.target.checked)}
                    />
                  }
                  label={<Typography variant="body2" sx={{ fontWeight: 500 }}>Modo Edição</Typography>}
                  sx={{ mr: 1 }}
                />

                {viewTab === "script" ? (
                  <>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={copied === "script" ? <Check color="success" /> : <ContentCopy />}
                      onClick={() => copyToClipboard(script, "script")}
                      disabled={!script}
                    >
                      {copied === "script" ? "Copiado!" : "Copiar Script"}
                    </Button>
                    <Button
                      size="small"
                      variant="contained"
                      startIcon={<Download />}
                      onClick={downloadCurrentScript}
                      disabled={!script}
                    >
                      Baixar Script
                    </Button>
                  </>
                ) : (
                  <>
                    {rows.length > 1 && (
                      <FormControl size="small" sx={{ minWidth: 200 }}>
                        <Select
                          value={selectedJsonTenantIndex}
                          onChange={(e) => setSelectedJsonTenantIndex(Number(e.target.value))}
                        >
                          {rows.map((r, idx) => (
                            <MenuItem key={r.connection_code} value={idx}>
                              {r.connection_name} ({r.connection_code})
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                    )}
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={copied === "json" ? <Check color="success" /> : <ContentCopy />}
                      onClick={() => copyToClipboard(currentJsonString, "json")}
                      disabled={rows.length === 0}
                    >
                      {copied === "json" ? "Copiado!" : "Copiar JSON"}
                    </Button>
                    <Button
                      size="small"
                      variant="contained"
                      startIcon={<Download />}
                      onClick={downloadCurrentJson}
                      disabled={rows.length === 0}
                    >
                      Baixar JSON
                    </Button>
                  </>
                )}
              </Box>
            </Box>

            {/* Code Box: White Background with Syntax Highlighting or Editable TextArea */}
            <Box sx={{ p: 2.5, bgcolor: "#ffffff", minHeight: 360, maxHeight: 600, overflowY: "auto" }}>
              {isEditing ? (
                <TextField
                  multiline
                  fullWidth
                  minRows={14}
                  maxRows={26}
                  value={viewTab === "script" ? script : currentJsonString}
                  onChange={(e) => {
                    if (viewTab === "script") setScript(e.target.value);
                  }}
                  variant="outlined"
                  InputProps={{
                    sx: {
                      fontFamily: 'Consolas, "Fira Code", Monaco, "Courier New", Courier, monospace',
                      fontSize: "0.875rem",
                      color: "#24292e",
                      bgcolor: "#ffffff",
                      lineHeight: 1.6,
                    },
                  }}
                />
              ) : (
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    p: 2,
                    bgcolor: "#fafbfc",
                    border: "1px solid #e1e4e8",
                    borderRadius: 1.5,
                    fontFamily: 'Consolas, "Fira Code", Monaco, "Courier New", Courier, monospace',
                    fontSize: "0.875rem",
                    lineHeight: 1.6,
                    color: "#24292e",
                    overflowX: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                  dangerouslySetInnerHTML={{
                    __html:
                      viewTab === "script"
                        ? highlightBashHtml(script || "# Nenhum script gerado.")
                        : highlightJsonHtml(currentJsonString || "{}"),
                  }}
                />
              )}
            </Box>
          </Box>
        </Box>
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
