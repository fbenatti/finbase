import { useState, useEffect, useMemo, useCallback, memo, useRef } from "react";
import {
  Box, Typography, Button, IconButton, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Switch, Checkbox, FormControlLabel,
  Alert, CircularProgress, Divider, Autocomplete, Tabs, Tab, InputAdornment, Stack,
} from "@mui/material";
import { Add, Edit, Delete, Download, ContentCopy, Check, Code, Terminal, Search, Refresh, Clear, AccountTree, Close } from "@mui/icons-material";
import {
  getClassifications, createClassification, updateClassification,
  deleteClassification, ClassificationRule, getClassificationsGrid,
  getClassificationFieldValues, refreshClassificationFieldValues, ClassificationFieldValue,
  getDepartments, Department,
  getDestinationTables, getDestinationSchema,
  DestinationTable,
  getAzureTenants, getAzurePolicySubscriptions, exportAzureTagPolicy, downloadText, getAzureTagMappings, createAzureTagMapping, updateAzureTagMapping,
  AzureTenant, AzurePolicySubscription, AzureTenantPolicy, AzureTagMapping,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import DepartmentTreePicker, { flattenDepartments } from "../../components/DepartmentTreePicker";
import ClassificationDataGrid, { ClassificationGridFilter } from "./ClassificationDataGrid";
import PolicyFlow from "./PolicyFlow";
import { ORG } from "../../constants/orgLabels";

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


const emptyForm = {
  name: "", target_field: "organograma",
  department_id: null as number | null,
  cloud_scope: "global" as string,
  conditions: [{ field: "", operator: "=", values: "", logic: "AND" }] as CondEntry[],
  priority: 20, active: true,
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
      {tab === 0 ? <ClassificationRulesTab /> : <TagsTab />}
    </Box>
  );
}

function formFromRule(r: ClassificationRule | null) {
  if (!r) {
    return { ...emptyForm, conditions: [{ field: "", operator: "=", values: "", logic: "AND" }] };
  }
  const conds = (r.conditions || []).map((c: Record<string, unknown>) => ({
    field: String(c.field || ""),
    operator: String(c.operator || "="),
    values: Array.isArray(c.values) ? c.values.join(" | ") : String(c.values || ""),
    logic: String(c.logic || "AND"),
  }));
  if (conds.length === 0) conds.push({ field: "", operator: "=", values: "", logic: "AND" });
  return {
    name: r.name,
    target_field: r.target_field,
    department_id: r.department_id,
    cloud_scope: r.cloud_scope || "global",
    conditions: conds,
    priority: r.priority,
    active: r.active,
  };
}

const ClassificationRuleDialog = memo(function ClassificationRuleDialog({
  open,
  editing,
  cloning,
  selectedDestName,
  destColumns,
  departments,
  destinationTableId,
  onClose,
  onSaved,
  onError,
}: {
  open: boolean;
  editing: ClassificationRule | null;
  cloning: ClassificationRule | null;
  selectedDestName: string | null;
  destColumns: string[];
  departments: Department[];
  destinationTableId: number | null;
  onClose: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [form, setForm] = useState(emptyForm);

  useEffect(() => {
    if (open) {
      const source = editing || cloning;
      setForm(source ? { ...formFromRule(source), ...(cloning && !editing ? { name: `${source.name} (cópia)` } : {}) } : formFromRule(null));
    }
  }, [open, editing, cloning]);

  // Autocomplete de valores reais por campo (tabela auxiliar no Postgres, enriquecida no reprocessamento).
  const [valueSearch, setValueSearch] = useState<Record<number, string>>({});
  const [valueOptions, setValueOptions] = useState<Record<number, ClassificationFieldValue[]>>({});
  const [valueLoading, setValueLoading] = useState<Record<number, boolean>>({});

  useEffect(() => {
    if (open) {
      setValueSearch({});
      setValueOptions({});
      setValueLoading({});
    }
  }, [open, editing, cloning]);

  const conditionKey = form.conditions.map((c) => c.field.trim()).join("|");
  useEffect(() => {
    if (!open || !destinationTableId) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    form.conditions.forEach((cond, i) => {
      const fieldName = cond.field.trim();
      if (!fieldName) return;
      const searchTerm = valueSearch[i] || "";
      timers.push(
        setTimeout(() => {
          setValueLoading((prev) => ({ ...prev, [i]: true }));
          getClassificationFieldValues({
            destination_table_id: destinationTableId,
            field: fieldName,
            cloud: form.cloud_scope === "global" ? undefined : form.cloud_scope,
            search: searchTerm || undefined,
            limit: 50,
          })
            .then((values) => setValueOptions((prev) => ({ ...prev, [i]: values })))
            .catch(() => setValueOptions((prev) => ({ ...prev, [i]: [] })))
            .finally(() => setValueLoading((prev) => ({ ...prev, [i]: false })));
        }, 350),
      );
    });
    return () => timers.forEach(clearTimeout);
  }, [open, destinationTableId, form.cloud_scope, conditionKey, valueSearch]);

  const flatDepartments = useMemo(() => flattenDepartments(departments), [departments]);
  const hasValidConditions = form.conditions.some((c) => c.field.trim() && c.values.trim());

  const updateCondition = useCallback((i: number, key: keyof CondEntry, val: string) => {
    setForm((prev) => {
      const updated = prev.conditions.map((cond, idx) =>
        idx === i ? { ...cond, [key]: val } : cond,
      );
      return { ...prev, conditions: updated };
    });
  }, []);

  const addCondition = useCallback(() => {
    setForm((prev) => ({
      ...prev,
      conditions: [...prev.conditions, { field: "", operator: "=", values: "", logic: "AND" }],
    }));
  }, []);

  const removeCondition = useCallback((i: number) => {
    setForm((prev) => ({
      ...prev,
      conditions: prev.conditions.filter((_, idx) => idx !== i),
    }));
  }, []);

  const save = async () => {
    if (!form.department_id) {
      onError("Selecione o departamento responsável pela classificação.");
      return;
    }
    if (!hasValidConditions) {
      onError("Defina ao menos uma condição com campo e valor.");
      return;
    }

    const body: Record<string, unknown> = {
      connection_id: editing?.connection_id ?? cloning?.connection_id ?? null,
      destination_table_id: editing?.destination_table_id ?? cloning?.destination_table_id ?? destinationTableId,
      department_id: form.department_id,
      name: form.name,
      target_field: "organograma",
      priority: form.priority,
      active: form.active,
      cloud_scope: form.cloud_scope === "global" ? null : form.cloud_scope,
      pattern_type: "multi_condition",
      match_value: form.department_id
        ? (flatDepartments.find((d) => d.id === form.department_id)?.name || "")
        : form.name,
    };

    const validConds = form.conditions.filter((c) => c.field && c.values);
    if (validConds.length > 0) {
      body.conditions = validConds.map((c) => ({
        field: c.field,
        operator: c.operator,
        values: c.values.split("|").map((v) => v.trim()).filter(Boolean),
        logic: c.logic,
      }));
      body.source_field = validConds[0].field;
      body.pattern = validConds
        .map((c) => `${c.field}${c.operator}${c.values.replace(/\|/g, " | ")}`)
        .join(" ; ");
    }

    try {
      if (editing) await updateClassification(editing.id, body as Partial<ClassificationRule>);
      else await createClassification(body as Partial<ClassificationRule>);
      onClose();
      onSaved();
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : "Save failed");
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{editing ? "Editar" : "Nova"} Classificação</DialogTitle>
      <DialogContent>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
          {selectedDestName && (
            <Alert severity="info" icon={false} sx={{ py: 0 }}>
              Tabela Destino: <strong>{selectedDestName}</strong>
            </Alert>
          )}

          <TextField
            label="Nome"
            value={form.name}
            onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
          />

          <DepartmentTreePicker
            departments={departments}
            value={form.department_id}
            onChange={(departmentId) => setForm((prev) => ({ ...prev, department_id: departmentId }))}
            label="Organograma"
            helperText="Escolha o departamento na hierarquia, como em Departamentos > Editar Departamento."
          />

          <FormControl fullWidth>
            <InputLabel>Cloud (escopo da regra)</InputLabel>
            <Select
              value={form.cloud_scope}
              label="Cloud (escopo da regra)"
              onChange={(e) => setForm((prev) => ({ ...prev, cloud_scope: e.target.value }))}
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
              Condições ({form.conditions.filter((c) => c.field && c.values).length} definidas)
            </Typography>
            <Button size="small" onClick={addCondition}>+ Condição</Button>
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: -0.5 }}>
            Use | nos valores para OR entre opções do mesmo campo.
            {destColumns.length === 0 ? " Os campos FOCUS serão sugeridos quando a tabela estiver disponível." : ""}
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
                  onChange={(_, value) => updateCondition(i, "field", value || "")}
                  onInputChange={(_, v, reason) => {
                    if (reason === "reset") return;
                    updateCondition(i, "field", v);
                  }}
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
                <Autocomplete
                  multiple
                  freeSolo
                  size="small"
                  options={(valueOptions[i] || []).map((option) => option.value)}
                  value={cond.values.split("|").map((v) => v.trim()).filter(Boolean)}
                  onInputChange={(_, v) => setValueSearch((prev) => ({ ...prev, [i]: v }))}
                  onChange={(_, newValue) => updateCondition(i, "values", (newValue as string[]).join(" | "))}
                  loading={Boolean(valueLoading[i])}
                  filterOptions={(options, state) => {
                    const input = state.inputValue.trim().toLowerCase();
                    if (!input) return options;
                    return options.filter((option) => option.toLowerCase().includes(input)).slice(0, 50);
                  }}
                  renderTags={(value, getTagProps) =>
                    value.map((option, index) => <Chip size="small" {...getTagProps({ index })} label={option} />)
                  }
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      fullWidth
                      placeholder="Selecione ou digite valor | valor2"
                      helperText={
                        cond.field && destinationTableId && !(valueOptions[i] || []).length && !valueLoading[i]
                          ? "Sem sugestões ainda — digite manualmente ou clique em Atualizar valores."
                          : undefined
                      }
                    />
                  )}
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

          <TextField
            label="Prioridade"
            type="number"
            value={form.priority}
            onChange={(e) => setForm((prev) => ({ ...prev, priority: Number(e.target.value) }))}
          />
          <FormControlLabel
            control={
              <Switch
                checked={form.active}
                onChange={(e) => setForm((prev) => ({ ...prev, active: e.target.checked }))}
              />
            }
            label="Ativo"
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button onClick={save} variant="contained" disabled={!form.name || !hasValidConditions}>
          Salvar
        </Button>
      </DialogActions>
    </Dialog>
  );
});

export function ClassificationRulesTab() {
  const [rules, setRules] = useState<ClassificationRule[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const queryRef = useRef<{ offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: ClassificationGridFilter[] }>({ offset: 0, limit: 25, filters: [] });
  const [departments, setDepartments] = useState<Department[]>([]);
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [selectedDestId, setSelectedDestId] = useState<number | "">("");
  const [cloudFilter, setCloudFilter] = useState<string>("every");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ClassificationRule | null>(null);
  const [cloning, setCloning] = useState<ClassificationRule | null>(null);
  const [deletePending, setDeletePending] = useState<ClassificationRule | null>(null);
  const [selectedRuleIds, setSelectedRuleIds] = useState<number[]>([]);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [destColumns, setDestColumns] = useState<string[]>([]);

  useEffect(() => {
    getDepartments(false).then(setDepartments).catch(() => {});
    getDestinationTables(true).then(setDestTables).catch(() => {});
  }, []);

  useEffect(() => {
    const focusTable = destTables.find(table => `${table.name} ${table.physical_name}`.toLowerCase().includes("focus"));
    const schemaTableId = focusTable?.id || selectedDestId;
    if (schemaTableId) {
      getDestinationSchema(schemaTableId as number)
        .then(schema => setDestColumns(schema.columns.map(c => c.name)))
        .catch(() => setDestColumns([]));
    } else {
      setDestColumns([]);
    }
  }, [selectedDestId, destTables]);

  // cloud/dest sao passados explicitamente: setState e assincrono, entao ler do
  // estado em load usaria valores antigos no mesmo tick (stale closure).
  const load = async (
    query: { offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: ClassificationGridFilter[] },
    cloudOverride?: string,
    destOverride?: number | "",
  ) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setLoading(true);
    try {
      const cloud = cloudOverride ?? cloudFilter;
      const destId = destOverride === undefined ? (selectedDestId === "" ? undefined : (selectedDestId as number)) : (destOverride === "" ? undefined : (destOverride as number));
      const { sort, ...paginationAndFilters } = query;
      const data = await getClassificationsGrid({
        ...paginationAndFilters,
        sort_field: sort?.field,
        sort_direction: sort?.direction,
        search: search.trim() || undefined,
        destination_table_id: destId,
        cloud,
      }, abortRef.current.signal);
      setRules(data.items);
      setTotal(data.total);
    } catch (e: unknown) {
      // Axios aborts throw CanceledError (code ERR_CANCELED, message "canceled"),
      // not AbortError — treat both as "superseded by newer filter" and stay silent.
      if (e instanceof Error && (e.name === "AbortError" || (e as { code?: string }).code === "ERR_CANCELED")) {
        return;
      }
      setError(e instanceof Error ? e.message : "Falha ao carregar classificações");
    } finally {
      setLoading(false);
    }
  };

  const handleQuery = (query: { offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: ClassificationGridFilter[] }) => {
    queryRef.current = query;
    void load(query);
  };

  const changeFilters = (nextCloud: string, nextDest: number | "") => {
    // Primary filters changed — reset column filters/search so they don't apply to the new scope.
    queryRef.current = { offset: 0, limit: queryRef.current.limit, filters: [] };
    setSearch("");
    void load(queryRef.current, nextCloud, nextDest);
  };

  const openNew = () => {
    setEditing(null);
    setCloning(null);
    setDialogOpen(true);
  };

  const openEdit = (r: ClassificationRule) => {
    setCloning(null);
    setEditing(r);
    setDialogOpen(true);
  };

  const openClone = (rule: ClassificationRule) => {
    setEditing(null);
    setCloning(rule);
    setSelectedDestId(rule.destination_table_id ?? "");
    setDialogOpen(true);
  };

  const remove = async () => {
    if (!deletePending) return;
    try {
      await deleteClassification(deletePending.id);
      setDeletePending(null);
      setSuccess("Classificação excluída.");
      void load(queryRef.current);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Falha ao excluir classificação");
    }
  };

  const removeSelected = async () => {
    const outcomes = await Promise.allSettled(selectedRuleIds.map(id => deleteClassification(id)));
    const failed = outcomes.filter(result => result.status === "rejected").length;
    setBulkDeleteOpen(false);
    setSelectedRuleIds([]);
    setSuccess(failed ? `${outcomes.length - failed} regras excluídas; ${failed} falharam.` : `${outcomes.length} regras excluídas.`);
    void load(queryRef.current);
  };

  const flatDepartments = useMemo(() => flattenDepartments(departments), [departments]);
  const departmentById = useMemo(
    () => new Map(flatDepartments.map((d) => [d.id, d])),
    [flatDepartments],
  );

  const selectedDestName = selectedDestId ? destTables.find(t => t.id === selectedDestId)?.name : null;
  const filtersReady = true;

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2, minHeight: 52, gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h6">Classificar projetos</Typography>
          <Typography variant="body2" color="text.secondary">
            Regras que classificam linhas de custo em departamentos da hierarquia.
          </Typography>
        </Box>
        <Stack direction="row" gap={1}>
          <Button
            variant="outlined"
            startIcon={<Refresh />}
            onClick={() => {
              setSuccess("");
              refreshClassificationFieldValues(selectedDestId === "" ? undefined : (selectedDestId as number))
                .then(() => setSuccess("Atualização de valores iniciada em segundo plano."))
                .catch((e: unknown) => setError(e instanceof Error ? e.message : "Falha ao iniciar a atualização de valores"));
            }}
          >
            Atualizar valores
          </Button>
          <Button variant="contained" startIcon={<Add />} onClick={openNew}>
            Nova
          </Button>
        </Stack>
      </Stack>

      {/* Filtros: Cloud primeiro, depois Tabela Destino */}
      <Stack direction="row" spacing={1.5} sx={{ mb: 2, flexWrap: "wrap" }} alignItems="center">
        <FormControl sx={{ minWidth: 220 }} size="small">
          <InputLabel>Cloud</InputLabel>
          <Select
            value={cloudFilter}
            label="Cloud"
            onChange={(e) => { const value = e.target.value; setCloudFilter(value); changeFilters(value, selectedDestId); }}
          >
            {CLOUD_FILTER_OPTIONS.map((s) => (
              <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl sx={{ minWidth: 260 }} size="small">
          <InputLabel>Tabela Destino</InputLabel>
          <Select
            value={selectedDestId}
            label="Tabela Destino"
            onChange={(e) => { const value = e.target.value as number | ""; setSelectedDestId(value); changeFilters(cloudFilter, value); }}
          >
            <MenuItem value="">Todas as tabelas</MenuItem>
            {destTables.map((t) => (
              <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        {selectedDestName && (
          <Chip
            label={total + " regras em " + selectedDestName}
            color="primary" size="small"
          />
        )}
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}

      {!filtersReady ? (
        <Box sx={{ py: 7 }}>
          <EmptyState
            message="Nenhuma classificação cadastrada para os filtros selecionados."
          />
        </Box>
      ) : (
        <>
          <TextField
            fullWidth
            size="small"
            placeholder="Buscar classificações (nome, departamento ou padrão)"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <Search fontSize="small" />
                  </InputAdornment>
                ),
                endAdornment: search ? (
                  <InputAdornment position="end">
                    <IconButton size="small" aria-label="Limpar busca" onClick={() => setSearch("")} edge="end">
                      <Clear fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ) : undefined,
                sx: { height: 40 },
              },
            }}
            sx={{ mb: 2, maxWidth: 520 }}
          />

          {selectedRuleIds.length >= 2 && (
            <Stack direction="row" justifyContent="flex-end" sx={{ mb: 1 }}>
              <Button color="error" variant="contained" startIcon={<Delete />} onClick={() => setBulkDeleteOpen(true)}>
                Excluir selecionadas ({selectedRuleIds.length})
              </Button>
            </Stack>
          )}
          <ClassificationDataGrid
            key={cloudFilter + "|" + selectedDestId}
            rows={rules}
            total={total}
            loading={loading}
            search={search}
            onQuery={handleQuery}
            onEdit={openEdit}
            onClone={openClone}
            onDelete={setDeletePending}
            departments={departments}
            selectedRuleIds={selectedRuleIds}
            onSelectionChange={setSelectedRuleIds}
          />
        </>
      )}

      <ClassificationRuleDialog
        open={dialogOpen}
        editing={editing}
        cloning={cloning}
        selectedDestName={selectedDestName ?? null}
        destColumns={destColumns}
        departments={departments}
        destinationTableId={selectedDestId === "" ? null : (selectedDestId as number)}
        onClose={() => setDialogOpen(false)}
        onSaved={() => void load(queryRef.current)}
        onError={setError}
      />

      {/* Confirmação Material */}
      <Dialog open={Boolean(deletePending)} onClose={() => setDeletePending(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Excluir classificação?</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a excluir a classificação <strong>{deletePending?.name}</strong>. Esta ação não pode ser desfeita.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeletePending(null)}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={() => void remove()}>
            Excluir classificação
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog open={bulkDeleteOpen} onClose={() => setBulkDeleteOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Excluir regras selecionadas?</DialogTitle>
        <DialogContent><Typography>Serão excluídas {selectedRuleIds.length} regras de classificação. Esta ação não pode ser desfeita.</Typography></DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setBulkDeleteOpen(false)}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={() => void removeSelected()}>Excluir regras</Button>
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
    // Do not run another regex over generated <span> markup. It previously
    // matched the style attributes and leaked fragments such as "color: #005cc5"
    // into the script preview.
    return res;
  });

  return highlightedLines.join("\n");
}

function MappingRow({ mapping, focusFields, onSave }: {
  mapping: AzureTagMapping;
  focusFields: string[];
  onSave: (body: Omit<AzureTagMapping, "id">, id?: number) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Omit<AzureTagMapping, "id">>({
    tag_key: mapping.tag_key, focus_field: mapping.focus_field, active: mapping.active,
  });
  const [saving, setSaving] = useState(false);
  useEffect(() => setDraft({ tag_key: mapping.tag_key, focus_field: mapping.focus_field, active: mapping.active }), [mapping]);
  const changed = draft.tag_key !== mapping.tag_key || draft.focus_field !== mapping.focus_field || draft.active !== mapping.active;
  const save = async () => {
    setSaving(true);
    try { await onSave(draft, mapping.id); } finally { setSaving(false); }
  };
  return <>
    <TextField size="small" label="Chave da tag Azure" value={draft.tag_key} onChange={(e) => setDraft({ ...draft, tag_key: e.target.value })} />
    <Autocomplete size="small" options={focusFields} value={draft.focus_field}
      onChange={(_, value) => setDraft({ ...draft, focus_field: value || "" })}
      renderInput={(params) => <TextField {...params} label="Campo FOCUS" />} />
    <Box sx={{ display: "flex", gap: .75, alignItems: "center", justifyContent: "flex-start", flexWrap: "wrap" }}>
      <Switch size="small" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} />
      {changed && <Chip size="small" color="warning" label="Não salvo" />}
      <Button size="small" variant="contained" disabled={!changed || saving} onClick={save}>
        {saving ? "Salvando…" : "Salvar alterações"}
      </Button>
    </Box>
  </>;
}

function TagMappingsTab() {
  const [mappings, setMappings] = useState<AzureTagMapping[]>([]);
  const [focusFields, setFocusFields] = useState<string[]>([]);
  const [newMapping, setNewMapping] = useState({ tag_key: "", focus_field: "", active: true });
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAzureTagMappings()
      .then(({ mappings: rows, focus_fields }) => { setMappings(rows); setFocusFields(focus_fields); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Não foi possível carregar os mapeamentos"))
      .finally(() => setLoading(false));
  }, []);

  const saveMapping = async (body: Omit<AzureTagMapping, "id">, id?: number) => {
    try {
      setError("");
      const saved = id ? await updateAzureTagMapping(id, body) : await createAzureTagMapping(body);
      setMappings((current) => id ? current.map((item) => item.id === id ? saved : item) : [...current, saved]);
      setNewMapping({ tag_key: "", focus_field: "", active: true });
      setSuccess(id ? "Mapeamento atualizado." : "Nova tag adicionada ao mapeamento.");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível salvar o mapeamento");
    }
  };

  return <Box>
    <Typography variant="h6" sx={{ mb: .5 }}>Mapeamento global de tags Azure</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
      A chave é a tag criada no Azure; o valor vem do campo FOCUS selecionado. As mudanças valem para todos os tenants na próxima geração de policies.
    </Typography>
    {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
    {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}
    {loading ? <CircularProgress size={26} /> : <Box sx={{ maxWidth: 900, border: "1px solid", borderColor: "divider", borderRadius: 2, p: 2 }}>
      <Box sx={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) minmax(260px, 1.4fr) minmax(270px, auto)", gap: 1.5, alignItems: "center" }}>
        <Typography variant="subtitle2">Chave da tag</Typography>
        <Typography variant="subtitle2">Campo FOCUS (valor)</Typography>
        <Typography variant="subtitle2">Status e ações</Typography>
        {mappings.map((mapping) => <MappingRow key={mapping.id} mapping={mapping} focusFields={focusFields} onSave={saveMapping} />)}
        <Divider sx={{ gridColumn: "1 / -1", my: .5 }} />
        <TextField size="small" label="Nova chave" placeholder="ex.: unidade_negocio" value={newMapping.tag_key} onChange={(e) => setNewMapping({ ...newMapping, tag_key: e.target.value })} />
        <Autocomplete size="small" options={focusFields} value={newMapping.focus_field}
          onChange={(_, value) => setNewMapping({ ...newMapping, focus_field: value || "" })}
          renderInput={(params) => <TextField {...params} label="Campo FOCUS" placeholder="ex.: service_name" />} />
        <Button variant="contained" startIcon={<Add />} disabled={!newMapping.tag_key.trim() || !newMapping.focus_field.trim()} onClick={() => saveMapping(newMapping)}>Adicionar</Button>
      </Box>
    </Box>}
  </Box>;
}

export function TagsTab() {
  const [tenants, setTenants] = useState<AzureTenant[]>([]);
  const [selectedTenant, setSelectedTenant] = useState<number | "">("");
  const [selectedSubscription, setSelectedSubscription] = useState("");
  const [policySubscriptions, setPolicySubscriptions] = useState<AzurePolicySubscription[]>([]);
  const [subscriptionsLoading, setSubscriptionsLoading] = useState(false);
  const [planOnly, setPlanOnly] = useState(false);
  const [rows, setRows] = useState<AzureTenantPolicy[]>([]);
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [viewTab, setViewTab] = useState<"script" | "json">("script");
  const [tagSubTab, setTagSubTab] = useState<"policies" | "mapping">("policies");
  const [flowDialogOpen, setFlowDialogOpen] = useState(false);
  const [selectedJsonTenantIndex, setSelectedJsonTenantIndex] = useState(0);
  const [isEditing, setIsEditing] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    getAzureTenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  useEffect(() => {
    setSelectedSubscription("");
    setPolicySubscriptions([]);
    setRows([]);
    setScript("");
    setSuccess("");
    setError("");
    if (selectedTenant === "") return;
    setSubscriptionsLoading(true);
    getAzurePolicySubscriptions(selectedTenant as number)
      .then(setPolicySubscriptions)
      .catch((e: unknown) => {
        const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
        setError(typeof detail === "string" ? detail : "Não foi possível listar as subscriptions Azure");
      })
      .finally(() => setSubscriptionsLoading(false));
  }, [selectedTenant]);

  const generate = async () => {
    if (!selectedSubscription) {
      setError("Selecione uma subscription Azure para montar o mapa completo de projetos e policies.");
      return;
    }
    try {
      setLoading(true);
      setError("");
      setSuccess("");
      const connId = selectedTenant === "" ? undefined : (selectedTenant as number);
      const result = await exportAzureTagPolicy(connId, "both", undefined, planOnly, selectedSubscription);
      setRows(result.tenants);
      setScript(result.deployment_script);
      setSelectedJsonTenantIndex(0);
      setIsEditing(false);
      const selectedName = policySubscriptions.find((item) => item.subscription_id === selectedSubscription)?.name || selectedSubscription;
      const mapItems = result.tenants.flatMap((tenant) => [
        ...(tenant.layers?.resource_groups || []),
        ...(tenant.layers?.resources || []),
      ]);
      const projectCount = new Set(mapItems.map((item) => item.department_name || item.tags.projeto).filter(Boolean)).size;
      setSuccess(`Mapa completo gerado para ${selectedName}: ${projectCount} projeto(s), ${mapItems.length} policy(s).`);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : (e instanceof Error ? e.message : "Falha ao gerar políticas"));
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
  const policyMap = rows.flatMap((tenant) => [
    ...(tenant.layers?.resource_groups || []).map((item) => ({ ...item, connection_code: tenant.connection_code })),
    ...(tenant.layers?.resources || []).map((item) => ({ ...item, connection_code: tenant.connection_code })),
  ]);

  const tagTabs = <Tabs value={tagSubTab} onChange={(_, value) => setTagSubTab(value)} sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}>
    <Tab value="policies" label="Policies Azure" />
    <Tab value="mapping" label="Mapeamento de Tags" />
  </Tabs>;

  if (tagSubTab === "mapping") return <Box>{tagTabs}<TagMappingsTab /></Box>;

  return (
    <Box>
      {tagTabs}
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

        <FormControl sx={{ minWidth: 400 }} size="small" disabled={selectedTenant === "" || subscriptionsLoading || policySubscriptions.length === 0}>
          <InputLabel>Subscription Azure *</InputLabel>
          <Select
            value={selectedSubscription}
            label="Subscription Azure *"
            onChange={(e) => {
              setSelectedSubscription(String(e.target.value));
              setRows([]);
              setScript("");
              setError("");
              setSuccess("");
            }}
          >
            <MenuItem value="">{subscriptionsLoading ? "Carregando subscriptions…" : "Selecione uma subscription"}</MenuItem>
            {policySubscriptions.map((subscription) => (
              <MenuItem key={subscription.subscription_id} value={subscription.subscription_id}>
                {subscription.name} ({subscription.subscription_id})
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <Button variant="contained" onClick={generate} disabled={loading || !selectedSubscription}>
          {loading ? "Gerando..." : "Gerar políticas"}
        </Button>
        <FormControlLabel
          control={<Checkbox checked={planOnly} onChange={(e) => setPlanOnly(e.target.checked)} />}
          label="Gerar script em modo PLAN"
        />
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
          Selecione o tenant da conexão e uma subscription. A geração mostrará todas as regras de projeto aplicáveis, suas exceções por prioridade e as policies de Resource Groups e recursos.
        </Alert>
      ) : (
        <Box>
          <Box sx={{ mb: 3, p: 2, border: "1px solid #d0d7de", borderRadius: 2, bgcolor: "#fff" }}>
            <Typography variant="h6" fontWeight={700} sx={{ mb: 0.5 }}>Mapa de projetos e policies da subscription</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              Este mapa reúne todos os projetos cujas regras ARM podem atingir a subscription selecionada. As exceções mostram qual regra de maior prioridade impede sobreposição.
            </Typography>
            {policyMap.length === 0 ? (
              <Alert severity="warning">Nenhuma policy ARM elegível foi encontrada para esta subscription. Consulte os diagnósticos no JSON.</Alert>
            ) : (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                {policyMap.map((item) => (
                  <Box key={`${item.connection_code}-${item.layer}-${item.rule_id}`} sx={{ p: 1.5, border: "1px solid #e6e8eb", borderRadius: 1.5 }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap", mb: 0.5 }}>
                      <Typography variant="subtitle2" fontWeight={700}>{item.department_name || item.tags.projeto || "Projeto sem nome"}</Typography>
                      <Chip size="small" label={item.layer === "resource_groups" ? "Resource Groups" : "Recursos"} />
                      <Chip size="small" variant="outlined" label={`Prioridade ${item.priority}`} />
                      <Typography variant="caption" color="text.secondary">Regra #{item.rule_id}: {item.rule_name}</Typography>
                    </Box>
                    <Typography variant="body2"><strong>Alvo:</strong> {item.observed?.resource_group_name || item.observed?.resource_id || "Subscription conforme seletor da regra"}</Typography>
                    <Typography variant="body2"><strong>Tags:</strong> {Object.entries(item.tags).map(([key, value]) => `${key}=${value}`).join(" · ")}</Typography>
                    <Typography variant="body2"><strong>Exceções:</strong> {item.exclusions.length ? item.exclusions.map((exclusion) => `${exclusion.department_name || exclusion.rule_name} (#${exclusion.rule_id})`).join(" · ") : "Nenhuma"}</Typography>
                  </Box>
                ))}
              </Box>
            )}
          </Box>
          {/* Tenant Summary Cards */}
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(auto-fit, minmax(320px, 1fr))" }, gap: 2, mb: 3 }}>
            {rows.map((t) => (
              <Box key={t.connection_code} sx={{ p: 2, border: "1px solid #e0e0e0", borderRadius: 2, bgcolor: "#ffffff", boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
                <Typography variant="subtitle1" fontWeight={700}>
                  {t.connection_name} <Chip size="small" color="primary" label={`${t.rule_count} regras`} sx={{ ml: 1 }} />
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
                  Tenant ID: {t.tenant_id} · Cod: {t.connection_code}{t.department_name ? ` · Teste: ${t.department_name}` : ""}
                </Typography>
                <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                  <LayerSummary
                    label="Regras ARM elegíveis"
                    count={t.coverage?.eligible_rules ?? 0}
                  />
                  <LayerSummary
                    label="Policies para Resource Groups"
                    count={Array.isArray(t.layers?.resource_groups) ? (t.layers.resource_groups as unknown[]).length : 0}
                  />
                  <LayerSummary
                    label="Policies para recursos"
                    count={Array.isArray(t.layers?.resources) ? (t.layers.resources as unknown[]).length : 0}
                  />
                  <LayerSummary
                    label="Subscriptions GOLD cobertas"
                    count={t.subscriptions?.length ?? 0}
                  />
                  <LayerSummary
                    label="Regras não implantáveis/bloqueadas"
                    count={t.coverage?.diagnostics?.filter((d) => d.status !== "warning").length ?? 0}
                  />
                </Box>
                {(t.coverage?.diagnostics?.length ?? 0) > 0 && (
                  <Alert severity="warning" sx={{ mt: 1.5, py: 0 }}>
                    {t.coverage?.diagnostics.slice(0, 2).map((d, idx) => (
                      <Typography key={`${d.rule_id ?? "diag"}-${idx}`} variant="caption" display="block">
                        {d.rule_name ? `${d.rule_name}: ` : ""}{d.reason}
                      </Typography>
                    ))}
                  </Alert>
                )}
                {(t.subscriptions?.length ?? 0) > 0 && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.25 }}>
                    Escopo GOLD: {t.subscriptions!.map((s) => `${s.subscription_id.slice(0, 8)}… (${s.policies.length})`).join(" · ")}
                  </Typography>
                )}
              </Box>
            ))}
          </Box>

          {/* Text Output Viewer with Sub-tabs & Syntax Highlighting */}
          <Box sx={{ border: "1px solid #d0d7de", borderRadius: 2, overflow: "hidden", bgcolor: "#ffffff", boxShadow: "0 1px 4px rgba(0,0,0,0.06)" }}>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #e1e4e8", px: 2, pt: 1, bgcolor: "#f6f8fa", flexWrap: "wrap", gap: 1 }}>
              <Tabs
                value={viewTab}
                onChange={(_, v) => {
                  if (v === "flow") {
                    setFlowDialogOpen(true);
                    return;
                  }
                  setViewTab(v);
                }}
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
                <Tab value="flow" icon={<AccountTree fontSize="small" />} iconPosition="start" label="Fluxo da policy" sx={{ minHeight: 44, textTransform: "none", fontWeight: 600 }} />
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
                ) : viewTab === "json" ? (
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
                ) : null}
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
      <Dialog fullScreen open={flowDialogOpen} onClose={() => setFlowDialogOpen(false)}>
        <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: "1px solid", borderColor: "divider" }}>
          Fluxo de policies — {policyFlowTitle(rows[selectedJsonTenantIndex])}
          <IconButton aria-label="Fechar fluxo de policies" onClick={() => setFlowDialogOpen(false)}><Close /></IconButton>
        </DialogTitle>
        <DialogContent sx={{ p: 3, bgcolor: "#f8fafc" }}>
          <PolicyFlow tenant={rows[selectedJsonTenantIndex]} />
        </DialogContent>
      </Dialog>
    </Box>
  );
}

function policyFlowTitle(tenant?: AzureTenantPolicy): string {
  if (!tenant) return "projeto selecionado";
  const ids = tenant.deployment_subscriptions ?? [];
  const names = ids.map((id) => tenant.subscription_catalog?.find((entry) => entry.ids.includes(id))?.name || id);
  if (names.length === 1) return names[0];
  if (names.length > 1) return names.join(" · ");
  return tenant.department_name || "projeto selecionado";
}

function LayerSummary({ label, count }: { label: string; count: number }) {
  return (
    <Box sx={{ display: "flex", justifyContent: "space-between", py: 0.25 }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="body2" fontWeight={600}>{count}</Typography>
    </Box>
  );
}
