import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Typography, Button, Paper, IconButton, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Select, MenuItem, FormControl, InputLabel, Switch, FormControlLabel,
  Alert, CircularProgress, Tooltip, Divider, List, ListItemButton, Menu,
  ListItemIcon, ListItemText, Collapse, Grid,
} from "@mui/material";
import {
  Add, Edit, Delete, ExpandMore, ExpandLess,
  Business, AccountTree, UploadFile, CloudDownload,
  AccountBalance,
} from "@mui/icons-material";
import {
  getDepartments, createDepartment, updateDepartment, deleteDepartment,
  getProjects, createProject, updateProject, deleteProject,
  importCsvData, CsvImportResult, Department, Project,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";

interface CustomFieldEntry { key: string; value: string }

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

  const load = async () => {
    try {
      const [deps, projs] = await Promise.all([
        getDepartments(false),
        getProjects(),
      ]);
      setDepartments(deps);
      setProjects(projs);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

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
    setDeptDialog(true);
  };

  const openEditDept = (d: Department) => {
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
    setDeptDialog(true);
  };

  const saveDept = async () => {
    const cf: Record<string, unknown> = {};
    for (const { key, value } of customFields) {
      if (key.trim()) cf[key.trim()] = value;
    }

    const body = { ...deptForm, custom_fields: Object.keys(cf).length > 0 ? cf : null };
    try {
      if (editingDept) await updateDepartment(editingDept.id, body);
      else await createDepartment(body);
      setDeptDialog(false);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  };

  const removeDept = async (id: number) => {
    if (!confirm("Deletar este departamento? Filhos serão desassociados.")) return;
    try { await deleteDepartment(id); load(); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
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
    if (!confirm("Deletar projeto?")) return;
    try { await deleteProject(id); load(); } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
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

    return (
      <Box key={dept.id}>
        <Paper
          sx={{
            p: 1.5, mb: 0.5, ml: depth * 3,
            display: "flex", alignItems: "center", gap: 1,
            borderLeft: 4,
            borderColor: depth === 0 ? "primary.main" : depth === 1 ? "secondary.main" : "grey.400",
          }}
        >
          {dept.children.length > 0 ? (
            <IconButton size="small" onClick={() => toggleExpand(dept.id)}>
              {isExpanded ? <ExpandLess /> : <ExpandMore />}
            </IconButton>
          ) : (
            <Box sx={{ width: 32 }} />
          )}
          {depth === 0 ? <Business color="primary" /> : <AccountTree color="secondary" />}
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="body2" fontWeight={600}>
              {dept.name}
              {dept.parent_id && <Chip label="filho" size="small" sx={{ ml: 1, height: 18, fontSize: "0.6rem" }} />}
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
          <IconButton size="small" onClick={() => removeDept(dept.id)} color="error"><Delete fontSize="small" /></IconButton>
        </Paper>

        {/* Projects under this department */}
        {isExpanded && deptProjects.map(p => (
          <Paper key={p.id} sx={{
            p: 1, mb: 0.5, ml: depth * 3 + 4,
            display: "flex", alignItems: "center", gap: 1,
            bgcolor: "grey.50",
          }}>
            <Box sx={{ flexGrow: 1 }}>
              <Typography variant="body2" fontWeight={600}>{p.projeto_name}</Typography>
              <Box sx={{ display: "flex", gap: 0.5, mt: 0.25 }}>
                {p.cloud && <Chip label={p.cloud} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />}
                {p.subscription_rg && <Chip label={`RG: ${p.subscription_rg}`} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />}
              </Box>
            </Box>
            <IconButton size="small" onClick={() => openEditProj(p)}><Edit fontSize="small" /></IconButton>
            <IconButton size="small" onClick={() => removeProj(p.id)} color="error"><Delete fontSize="small" /></IconButton>
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
      <Dialog open={deptDialog} onClose={() => setDeptDialog(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editingDept ? "Editar" : "Novo"} Departamento</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome" value={deptForm.name}
              onChange={(e) => setDeptForm({ ...deptForm, name: e.target.value })} />
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
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeptDialog(false)}>Cancelar</Button>
          <Button onClick={saveDept} variant="contained">Salvar</Button>
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
