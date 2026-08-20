import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Typography, Button, Paper, IconButton, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Switch, FormControlLabel, FormControl,
  Alert, CircularProgress, Tooltip, Divider,
  List, Menu, MenuItem,
  Radio, RadioGroup, FormLabel,
} from "@mui/material";
import {
  Add, Edit, Delete, ExpandMore, ExpandLess,
  Business, AccountTree, UploadFile,
  AccountBalance,
} from "@mui/icons-material";
import {
    getDepartments, createDepartment, updateDepartment, deleteDepartment,
    bootstrapCloudOrganization,
    getProjects, createProject, updateProject, deleteProject,
    importCsvData, CsvImportResult, Department, Project,
    getSharedCosts, getDepartmentSharedCost, upsertDepartmentSharedCost,
    SharedCostConfig, SharedCostMode, SharedCostTarget,
    formatApiError,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import DepartmentTreePicker, {
  flattenDepartments,
  descendantIds,
} from "../../components/DepartmentTreePicker";

const emptyDeptForm = {
  parent_id: null as number | null,
  name: "", centro_custo: "", ambiente: "", cloud_filter: "",
  custom_fields: {} as Record<string, unknown>,
  active: true,
};

const emptyProjForm = {
  department_id: 0, projeto_name: "", cloud: "", subscription_rg: "",
  active: true,
};

interface CustomFieldEntry { key: string; value: string }

export default function Departments() {
  const navigate = useNavigate();
  const [departments, setDepartments] = useState<Department[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Department dialog
  const [deptDialog, setDeptDialog] = useState(false);
  const [editingDept, setEditingDept] = useState<Department | null>(null);
  const [deptForm, setDeptForm] = useState(emptyDeptForm);
  const [customFields, setCustomFields] = useState<CustomFieldEntry[]>([]);

  // Project dialog
  const [projDialog, setProjDialog] = useState(false);
  const [editingProj, setEditingProj] = useState<Project | null>(null);
  const [projForm, setProjForm] = useState(emptyProjForm);

  // Expand state
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Import
  const [importing, setImporting] = useState(false);
  const [financialMenu, setFinancialMenu] = useState<HTMLElement | null>(null);
  const [preparingOrganization, setPreparingOrganization] = useState(false);
  const [sharedCosts, setSharedCosts] = useState<SharedCostConfig[]>([]);
  const [sharedEnabled, setSharedEnabled] = useState(false);
  const [sharedMode, setSharedMode] = useState<SharedCostMode>("consumption");
  const [sharedTargets, setSharedTargets] = useState<SharedCostTarget[]>([]);
  const [sharedPicker, setSharedPicker] = useState<number | null>(null);
  const [sharedLoaded, setSharedLoaded] = useState(true);
  const [savingDept, setSavingDept] = useState(false);
  const [deptDialogError, setDeptDialogError] = useState("");

  const load = async () => {
    try {
      const [deps, projs, costs] = await Promise.all([
        getDepartments(false),
        getProjects(),
        getSharedCosts().catch(() => [] as SharedCostConfig[]),
      ]);
      setDepartments(deps);
      setProjects(projs);
      setSharedCosts(costs);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const prepareCloudOrganization = async () => {
    if (!confirm("Preparar Consumo Cloud > Showback/Chargeback > Provedores e mover as diretorias atuais para Showback > Azure? Isso não reprocessa os dados.")) return;
    setPreparingOrganization(true); setError(""); setSuccess("");
    try {
      const result = await bootstrapCloudOrganization();
      setSuccess(`${result.message} ${result.moved_directories.length} diretorias reposicionadas e ${result.planning_entries_updated} entradas de orçamento atualizadas.`);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível preparar o organograma");
    } finally {
      setPreparingOrganization(false);
    }
  };

  const handleImport = async () => {
    setImporting(true);
    setError(""); setSuccess("");
    try {
      const result: CsvImportResult = await importCsvData();
      setSuccess(
        `Importado: ${result.departments_created} departamentos, ` +
        `${result.projects_created} projetos, ` +
        `${result.classification_rules_created} regras de classificação`
      );
      if (result.errors.length > 0) {
        setError(result.errors.join("; "));
      }
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  // --- Department CRUD ---
  const openNewDept = (parentId: number | null = null) => {
    setEditingDept(null);
    setDeptForm({ ...emptyDeptForm, parent_id: parentId });
    setCustomFields([]);
    setSharedEnabled(false);
    setSharedMode("consumption");
    setSharedTargets([]);
    setSharedPicker(null);
    setSharedLoaded(true);
    setDeptDialogError("");
    setDeptDialog(true);
  };

  const openEditDept = async (d: Department) => {
    setEditingDept(d);
    setDeptForm({
      parent_id: d.parent_id,
      name: d.name, centro_custo: d.centro_custo || "",
      ambiente: d.ambiente || "", cloud_filter: d.cloud_filter || "",
      custom_fields: d.custom_fields || {},
      active: d.active,
    });
    setCustomFields(
      d.custom_fields
        ? Object.entries(d.custom_fields).map(([key, value]) => ({ key, value: String(value) }))
        : []
    );
    setSharedEnabled(false);
    setSharedMode("consumption");
    setSharedTargets([]);
    setSharedPicker(null);
    setSharedLoaded(false);
    setDeptDialogError("");
    setDeptDialog(true);
    try {
      const config = await getDepartmentSharedCost(d.id);
      setSharedEnabled(config.enabled);
      setSharedMode(config.mode);
      setSharedTargets(config.targets || []);
    } catch (e: unknown) {
      setDeptDialogError(formatApiError(e, "Não foi possível carregar o custo compartilhado"));
    } finally {
      setSharedLoaded(true);
    }
  };

  const saveDept = async () => {
    const cf: Record<string, unknown> = {};
    for (const { key, value } of customFields) {
      if (key.trim()) cf[key.trim()] = value;
    }

    const body = { ...deptForm, custom_fields: Object.keys(cf).length > 0 ? cf : null };
    setSavingDept(true);
    setDeptDialogError("");
    try {
      const saved = editingDept ? await updateDepartment(editingDept.id, body) : await createDepartment(body);
      await upsertDepartmentSharedCost(saved.id, {
        enabled: sharedEnabled,
        mode: sharedMode,
        targets: sharedMode === "consumption" ? [] : sharedTargets.map(target => ({
          department_id: target.department_id,
          percent: sharedMode === "custom" ? Number(target.percent || 0) : null,
        })),
      });
      setDeptDialog(false);
      setSuccess(sharedEnabled ? "Departamento e custo compartilhado salvos." : "Departamento salvo.");
      await load();
    } catch (e: unknown) {
      setDeptDialogError(formatApiError(e, "Não foi possível salvar o departamento"));
    } finally {
      setSavingDept(false);
    }
  };

  const blockedParentIds = editingDept ? descendantIds(editingDept) : new Set<number>();

  const removeDept = async (id: number) => {
    if (!confirm("Excluir este departamento? Filhos sobem para a raiz. Projetos deste nó e o cadastro de custo compartilhado vinculado serão removidos.")) return;
    try {
      await deleteDepartment(id);
      setSuccess("Departamento excluído.");
      await load();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível excluir o departamento"));
    }
  };

  // --- Custom fields ---
  const addCustomField = () => setCustomFields([...customFields, { key: "", value: "" }]);
  const removeCustomField = (i: number) => setCustomFields(customFields.filter((_, idx) => idx !== i));
  const updateCustomField = (i: number, field: "key" | "value", val: string) => {
    const updated = [...customFields];
    updated[i][field] = val;
    setCustomFields(updated);
  };

  // --- Project CRUD ---
  const openNewProj = (deptId: number) => {
    setEditingProj(null);
    setProjForm({ ...emptyProjForm, department_id: deptId });
    setProjDialog(true);
  };

  const openEditProj = (p: Project) => {
    setEditingProj(p);
    setProjForm({
      department_id: p.department_id,
      projeto_name: p.projeto_name,
      cloud: p.cloud || "",
      subscription_rg: p.subscription_rg || "",
      active: p.active,
    });
    setProjDialog(true);
  };

  const saveProj = async () => {
    try {
      if (editingProj) await updateProject(editingProj.id, projForm);
      else await createProject(projForm);
      setProjDialog(false);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  };

  const removeProj = async (id: number) => {
    if (!confirm("Excluir este projeto?")) return;
    try {
      await deleteProject(id);
      setSuccess("Projeto excluído.");
      await load();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível excluir o projeto"));
    }
  };

  const toggleExpand = (id: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderDeptNode = (dept: Department, depth: number) => {
    const deptProjects = projects.filter(p => p.department_id === dept.id);
    const isExpanded = expanded.has(dept.id);
    const isShared = sharedCosts.some(config => config.department_id === dept.id && config.enabled);

    return (
      <Box key={dept.id}>
        <Paper
          sx={{
            p: 1.5, mb: 0.5, ml: depth * 3,
            display: "flex", alignItems: "center", gap: 1,
            borderLeft: 4,
            borderColor: isShared ? "#ED6C02" : depth === 0 ? "primary.main" : depth === 1 ? "secondary.main" : "grey.400",
            bgcolor: isShared ? "#FFF4E5" : "background.paper",
          }}
        >
          {dept.children.length > 0 ? (
            <IconButton size="small" onClick={() => toggleExpand(dept.id)}>
              {isExpanded ? <ExpandLess /> : <ExpandMore />}
            </IconButton>
          ) : (
            <Box sx={{ width: 32 }} />
          )}
          {depth === 0 ? <Business color="primary" /> : <AccountTree color={isShared ? "warning" : "secondary"} />}
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="body2" fontWeight={600} component="div" color={isShared ? "warning.dark" : "inherit"}>
              {dept.name}
              {dept.parent_id && <Chip label="filho" size="small" sx={{ ml: 1, height: 18, fontSize: "0.6rem" }} />}
              {isShared && <Chip label="Custo compartilhado" size="small" color="warning" sx={{ ml: 1, height: 18, fontSize: "0.6rem" }} />}
            </Typography>
            <Box sx={{ display: "flex", gap: 1, mt: 0.25 }}>
              {dept.centro_custo && <Chip label={`CC: ${dept.centro_custo}`} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />}
              {dept.ambiente && <Chip label={dept.ambiente} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />}
              {dept.cloud_filter && <Chip label={dept.cloud_filter} size="small" variant="outlined" color="info" sx={{ height: 18, fontSize: "0.6rem" }} />}
              {deptProjects.length > 0 && <Chip label={`${deptProjects.length} projetos`} size="small" sx={{ height: 18, fontSize: "0.6rem" }} />}
            </Box>
            {dept.custom_fields && Object.keys(dept.custom_fields).length > 0 && (
              <Box sx={{ display: "flex", gap: 0.5, mt: 0.25, flexWrap: "wrap" }}>
                {Object.entries(dept.custom_fields).slice(0, 3).map(([k, v]) => (
                  <Chip key={k} label={`${k}: ${String(v).slice(0, 30)}`} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.55rem" }} />
                ))}
                {Object.keys(dept.custom_fields).length > 3 && (
                  <Chip label={`+${Object.keys(dept.custom_fields).length - 3}`} size="small" sx={{ height: 18, fontSize: "0.55rem" }} />
                )}
              </Box>
            )}
          </Box>
          <Tooltip title="Adicionar filho">
            <IconButton size="small" onClick={() => openNewDept(dept.id)}><Add fontSize="small" /></IconButton>
          </Tooltip>
          <Tooltip title="Adicionar projeto">
            <IconButton size="small" onClick={() => openNewProj(dept.id)}><AccountTree fontSize="small" /></IconButton>
          </Tooltip>
          <Tooltip title="Cadastro financeiro deste departamento">
            <IconButton size="small" onClick={() => navigate(`/departments/financial?department_id=${dept.id}`)}><AccountBalance fontSize="small" /></IconButton>
          </Tooltip>
          <IconButton size="small" onClick={() => openEditDept(dept)}><Edit fontSize="small" /></IconButton>
          <Tooltip title="Excluir departamento">
            <IconButton size="small" onClick={(event) => { event.stopPropagation(); void removeDept(dept.id); }} color="error"><Delete fontSize="small" /></IconButton>
          </Tooltip>
        </Paper>

        {/* Projects under this department */}
        {isExpanded && deptProjects.map(p => (
          <Paper key={p.id} sx={{
            p: 1, mb: 0.5, ml: depth * 3 + 4,
            display: "flex", alignItems: "center", gap: 1,
            bgcolor: "grey.50",
          }}>
            <Box sx={{ flexGrow: 1 }}>
              <Typography variant="body2" fontWeight={600}>
                {p.projeto_name}
              </Typography>
              <Box sx={{ display: "flex", gap: 0.5, mt: 0.25 }}>
                {p.cloud && <Chip label={p.cloud} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />}
                {p.subscription_rg && <Chip label={`RG: ${p.subscription_rg}`} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />}
              </Box>
            </Box>
            <IconButton size="small" onClick={() => openEditProj(p)}><Edit fontSize="small" /></IconButton>
            <Tooltip title="Excluir projeto">
              <IconButton size="small" onClick={(event) => { event.stopPropagation(); void removeProj(p.id); }} color="error"><Delete fontSize="small" /></IconButton>
            </Tooltip>
          </Paper>
        ))}

        {/* Children departments (recursive) */}
        {isExpanded && dept.children.map(child => renderDeptNode(child, depth + 1))}
      </Box>
    );
  };

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", mb: 3 }}>
        <Box>
          <Typography variant="h4">Departamentos & Projetos</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Hierarquia de Diretorias, Departamentos e Projetos com campos customizados
          </Typography>
        </Box>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button variant="outlined" startIcon={<AccountBalance />} onClick={(e) => setFinancialMenu(e.currentTarget)}>
            Cadastro financeiro
          </Button>
          <Menu anchorEl={financialMenu} open={Boolean(financialMenu)} onClose={() => setFinancialMenu(null)}>
            <MenuItem onClick={() => { setFinancialMenu(null); navigate("/departments/financial?type=orcamento"); }}>Importar orçamento</MenuItem>
            <MenuItem onClick={() => { setFinancialMenu(null); navigate("/departments/financial?type=forecast-manual"); }}>Importar forecast manual</MenuItem>
            <MenuItem onClick={() => { setFinancialMenu(null); navigate("/departments/financial?type=saldo-contratual"); }}>Importar saldo contratual</MenuItem>
            <MenuItem onClick={() => { setFinancialMenu(null); navigate("/departments/financial"); }}>Download modelo e editar cadastro</MenuItem>
          </Menu>
          <Button variant="outlined" startIcon={preparingOrganization ? <CircularProgress size={18} /> : <AccountTree />}
            onClick={prepareCloudOrganization} disabled={preparingOrganization}>
            Preparar organograma cloud
          </Button>
          <Button variant="outlined" startIcon={importing ? <CircularProgress size={18} /> : <UploadFile />}
            onClick={handleImport} disabled={importing}>
            {importing ? "Importando..." : "Importar CSV"}
          </Button>
          <Button variant="contained" startIcon={<Add />} onClick={() => openNewDept(null)}>
            Novo Departamento
          </Button>
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}

      {departments.length === 0 ? (
        <EmptyState message="Nenhum departamento cadastrado. Clique em 'Novo Departamento' ou 'Importar CSV' para começar." />
      ) : (
        <Box>
          {departments.map(d => renderDeptNode(d, 0))}
        </Box>
      )}

      {/* Department Dialog */}
      <Dialog open={deptDialog} onClose={() => setDeptDialog(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editingDept ? "Editar" : "Novo"} Departamento</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            {deptDialogError && <Alert severity="error">{deptDialogError}</Alert>}
            {!sharedLoaded && editingDept && (
              <Typography variant="body2" color="text.secondary">Carregando configuração de custo compartilhado…</Typography>
            )}
            <TextField label="Nome" autoFocus value={deptForm.name}
              onChange={(e) => setDeptForm({ ...deptForm, name: e.target.value })} />
            <DepartmentTreePicker
              departments={departments}
              value={deptForm.parent_id}
              onChange={(parentId) => setDeptForm({ ...deptForm, parent_id: parentId })}
              label="Departamento pai"
              placeholder="Raiz (sem departamento pai)"
              allowClear
              clearLabel="Raiz (sem departamento pai)"
              blockedIds={blockedParentIds}
            />
            <Box sx={{ display: "flex", gap: 2 }}>
              <TextField label="Centro de Custo" value={deptForm.centro_custo}
                onChange={(e) => setDeptForm({ ...deptForm, centro_custo: e.target.value })} fullWidth />
              <TextField label="Ambiente" value={deptForm.ambiente}
                onChange={(e) => setDeptForm({ ...deptForm, ambiente: e.target.value })} fullWidth />
            </Box>
            <TextField label="Cloud Filter" value={deptForm.cloud_filter}
              onChange={(e) => setDeptForm({ ...deptForm, cloud_filter: e.target.value })}
              helperText="Filtrar por cloud (Azure-4P, Azure-TI, GCP-4P, etc.)" />

            <Divider />
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <Typography variant="subtitle2">Campos Customizados</Typography>
              <Button size="small" startIcon={<Add />} onClick={addCustomField}>Adicionar</Button>
            </Box>
            {customFields.map((cf, i) => (
              <Box key={i} sx={{ display: "flex", gap: 1 }}>
                <TextField size="small" label="Chave" value={cf.key}
                  onChange={(e) => updateCustomField(i, "key", e.target.value)}
                  sx={{ width: "40%" }} />
                <TextField size="small" label="Valor" value={cf.value}
                  onChange={(e) => updateCustomField(i, "value", e.target.value)}
                  sx={{ width: "50%" }} />
                <IconButton size="small" onClick={() => removeCustomField(i)} color="error">
                  <Delete fontSize="small" />
                </IconButton>
              </Box>
            ))}
            <FormControlLabel
              control={<Switch checked={deptForm.active} onChange={(e) => setDeptForm({ ...deptForm, active: e.target.checked })} />}
              label="Ativo"
            />
            {editingDept && <>
              <Divider />
              <FormControlLabel
                control={<Switch checked={sharedEnabled} onChange={(e) => setSharedEnabled(e.target.checked)} disabled={!sharedLoaded} />}
                label="Custo compartilhado"
              />
              {sharedEnabled && <>
                <FormControl>
                  <FormLabel>Como ratear o custo</FormLabel>
                  <RadioGroup
                    value={sharedMode}
                    onChange={(event) => setSharedMode(event.target.value as SharedCostMode)}
                  >
                    <FormControlLabel value="consumption" control={<Radio />} label="Por consumo na mesma cloud de origem" />
                    <FormControlLabel value="equal" control={<Radio />} label="Equalitário entre quem consumiu no período" />
                    <FormControlLabel value="custom" control={<Radio />} label="Percentual personalizado (o que faltar fica no doador)" />
                  </RadioGroup>
                </FormControl>
                {sharedMode === "consumption" ? (
                  <Typography variant="body2" color="text.secondary">
                    O valor rateado é o gasto deste departamento e de todos os filhos. Ligue a flag no projeto
                    Condomínio (folha), não na diretoria de Infra — senão a DIR Dados/IA paga uma fatia de Compra de RIs,
                    redes e o resto da Infra, não só os 2 MM do Condomínio. O rateio vai para os projetos da mesma cloud,
                    na proporção do consumo. Quem não consumiu não paga.
                  </Typography>
                ) : <>
                <DepartmentTreePicker
                  departments={departments}
                  value={sharedPicker}
                  onChange={(departmentId) => {
                    if (departmentId == null) return;
                    if (sharedTargets.some(target => target.department_id === departmentId)) return;
                    const found = flattenDepartments(departments).find(item => item.id === departmentId);
                    if (!found) return;
                    setSharedTargets([...sharedTargets, { department_id: found.id, percent: null, name: found.name, path: found.path }]);
                    setSharedPicker(null);
                  }}
                  label="Adicionar projeto ou diretoria destino"
                  placeholder="Selecione para incluir no rateio"
                  blockedIds={blockedParentIds}
                />
                {sharedTargets.map((target, index) => (
                  <Box key={target.department_id} sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                    <Typography variant="body2" sx={{ flex: 1 }}>{target.path || target.name}</Typography>
                    {sharedMode === "custom" && (
                      <TextField
                        size="small"
                        type="number"
                        label="%"
                        value={target.percent ?? ""}
                        onChange={(event) => {
                          const next = [...sharedTargets];
                          next[index] = { ...target, percent: event.target.value === "" ? null : Number(event.target.value) };
                          setSharedTargets(next);
                        }}
                        sx={{ width: 100 }}
                      />
                    )}
                    <IconButton size="small" color="error" onClick={() => setSharedTargets(sharedTargets.filter(item => item.department_id !== target.department_id))}>
                      <Delete fontSize="small" />
                    </IconButton>
                  </Box>
                ))}
                {sharedMode === "custom" && (
                  <Typography variant="caption" color="text.secondary">
                    Soma: {sharedTargets.reduce((sum, target) => sum + Number(target.percent || 0), 0)}%. O restante permanece no doador.
                  </Typography>
                )}
                {sharedMode === "equal" && (
                  <Typography variant="caption" color="text.secondary">
                    O valor é dividido igualmente entre os destinos selecionados que tiverem consumo no período.
                  </Typography>
                )}
                </>}
              </>}
            </>}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeptDialog(false)} disabled={savingDept}>Cancelar</Button>
          <Button onClick={saveDept} variant="contained" disabled={savingDept || (Boolean(editingDept) && !sharedLoaded)}>
            {savingDept ? "Salvando..." : "Salvar"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Project Dialog */}
      <Dialog open={projDialog} onClose={() => setProjDialog(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editingProj ? "Editar" : "Novo"} Projeto</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome do Projeto" value={projForm.projeto_name}
              onChange={(e) => setProjForm({ ...projForm, projeto_name: e.target.value })} />
            <Box sx={{ display: "flex", gap: 2 }}>
              <TextField label="Cloud" value={projForm.cloud}
                onChange={(e) => setProjForm({ ...projForm, cloud: e.target.value })} fullWidth />
              <TextField label="Subscription/RG" value={projForm.subscription_rg}
                onChange={(e) => setProjForm({ ...projForm, subscription_rg: e.target.value })} fullWidth />
            </Box>
            <FormControlLabel
              control={<Switch checked={projForm.active} onChange={(e) => setProjForm({ ...projForm, active: e.target.checked })} />}
              label="Ativo"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setProjDialog(false)}>Cancelar</Button>
          <Button onClick={saveProj} variant="contained">Salvar</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
