import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  FormLabel,
  IconButton,
  InputAdornment,
  Menu,
  MenuItem,
  Paper,
  Radio,
  RadioGroup,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Add,
  AccountBalance,
  AccountTree,
  Business,
  Delete,
  Edit,
  ExpandLess,
  ExpandMore,
  Search,
  UploadFile,
} from "@mui/icons-material";
import {
  getDepartments,
  createDepartment,
  updateDepartment,
  deleteDepartment,
  bootstrapCloudOrganization,
  importCsvData,
  CsvImportResult,
  Department,
  getSharedCosts,
  getDepartmentSharedCost,
  upsertDepartmentSharedCost,
  SharedCostConfig,
  SharedCostMode,
  SharedCostTarget,
  formatApiError,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import DepartmentTreePicker, {
  flattenDepartments,
  descendantIds,
} from "../../components/DepartmentTreePicker";
import { ORG } from "../../constants/orgLabels";

const emptyDeptForm = {
  parent_id: null as number | null,
  name: "",
  centro_custo: "",
  id_orcamento: "",
  ambiente: "",
  cloud_filter: "",
  custom_fields: {} as Record<string, unknown>,
  active: true,
};

interface CustomFieldEntry {
  key: string;
  value: string;
}

export default function Departments() {
  const navigate = useNavigate();
  const [departments, setDepartments] = useState<Department[]>([]);
  const [sharedCosts, setSharedCosts] = useState<SharedCostConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Busca do organograma (filtra a arvore mantendo os ancestrais)
  const [deptSearch, setDeptSearch] = useState("");

  // Department dialog
  const [deptDialog, setDeptDialog] = useState(false);
  const [editingDept, setEditingDept] = useState<Department | null>(null);
  const [deptForm, setDeptForm] = useState(emptyDeptForm);
  const [customFields, setCustomFields] = useState<CustomFieldEntry[]>([]);
  const [savingDept, setSavingDept] = useState(false);
  const [deptDialogError, setDeptDialogError] = useState("");

  // Expand state do organograma
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Import / organizacao
  const [importing, setImporting] = useState(false);
  const [financialMenu, setFinancialMenu] = useState<HTMLElement | null>(null);
  const [preparingOrganization, setPreparingOrganization] = useState(false);

  // Shared cost (dentro do dialogo de departamento)
  const [sharedEnabled, setSharedEnabled] = useState(false);
  const [sharedMode, setSharedMode] = useState<SharedCostMode>("consumption");
  const [sharedTargets, setSharedTargets] = useState<SharedCostTarget[]>([]);
  const [sharedPicker, setSharedPicker] = useState<number | null>(null);
  const [sharedLoaded, setSharedLoaded] = useState(true);

  // Confirmacoes Material
  const [deleteDeptPending, setDeleteDeptPending] = useState<Department | null>(
    null,
  );
  const [prepareOrgPending, setPrepareOrgPending] = useState(false);

  const load = async () => {
    try {
      const [deps, costs] = await Promise.all([
        getDepartments(false),
        getSharedCosts().catch(() => [] as SharedCostConfig[]),
      ]);
      setDepartments(deps);
      setSharedCosts(costs);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  // ── Acoes de página ──
  const prepareCloudOrganization = async () => {
    setPrepareOrgPending(false);
    setPreparingOrganization(true);
    setError("");
    setSuccess("");
    try {
      const result = await bootstrapCloudOrganization();
      setSuccess(
        result.message +
          " " +
          result.moved_directories.length +
          " diretorias reposicionadas e " +
          result.planning_entries_updated +
          " entradas de orçamento atualizadas.",
      );
      await load();
    } catch (e: unknown) {
      setError(
        e instanceof Error
          ? e.message
          : "Não foi possível preparar o organograma",
      );
    } finally {
      setPreparingOrganization(false);
    }
  };

  const handleImport = async () => {
    setImporting(true);
    setError("");
    setSuccess("");
    try {
      const result: CsvImportResult = await importCsvData();
      setSuccess(
        "Importado: " +
          result.departments_created +
          " nós do organograma e " +
          result.classification_rules_created +
          " regras de classificação",
      );
      if (result.errors.length > 0) {
        setError(result.errors.join("; "));
      }
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  // ── Department CRUD ──
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
      name: d.name,
      centro_custo: d.centro_custo || "",
      id_orcamento: d.id_orcamento || "",
      ambiente: d.ambiente || "",
      cloud_filter: d.cloud_filter || "",
      custom_fields: d.custom_fields || {},
      active: d.active,
    });
    setCustomFields(
      d.custom_fields
        ? Object.entries(d.custom_fields).map(([key, value]) => ({
            key,
            value: String(value),
          }))
        : [],
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
      setDeptDialogError(
        formatApiError(e, "Não foi possível carregar o custo compartilhado"),
      );
    } finally {
      setSharedLoaded(true);
    }
  };

  const saveDept = async () => {
    const cf: Record<string, unknown> = {};
    for (const { key, value } of customFields) {
      if (key.trim()) cf[key.trim()] = value;
    }

    const body = {
      ...deptForm,
      custom_fields: Object.keys(cf).length > 0 ? cf : null,
    };
    setSavingDept(true);
    setDeptDialogError("");
    try {
      const saved = editingDept
        ? await updateDepartment(editingDept.id, body)
        : await createDepartment(body);
      await upsertDepartmentSharedCost(saved.id, {
        enabled: sharedEnabled,
        mode: sharedMode,
        targets:
          sharedMode === "consumption"
            ? []
            : sharedTargets.map((target) => ({
                department_id: target.department_id,
                percent:
                  sharedMode === "custom" ? Number(target.percent || 0) : null,
              })),
      });
      setDeptDialog(false);
      setSuccess(sharedEnabled ? ORG.savedWithShared : ORG.saved);
      await load();
    } catch (e: unknown) {
      setDeptDialogError(
        formatApiError(e, "Não foi possível salvar o organograma"),
      );
    } finally {
      setSavingDept(false);
    }
  };

  const blockedParentIds = editingDept
    ? descendantIds(editingDept)
    : new Set<number>();

  const removeDept = async () => {
    if (!deleteDeptPending) return;
    try {
      await deleteDepartment(deleteDeptPending.id);
      setDeleteDeptPending(null);
      setSuccess(ORG.deleted);
      await load();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível excluir o organograma"));
    }
  };

  // ── Custom fields ──
  const addCustomField = () =>
    setCustomFields([...customFields, { key: "", value: "" }]);
  const removeCustomField = (i: number) =>
    setCustomFields(customFields.filter((_, idx) => idx !== i));
  const updateCustomField = (
    i: number,
    field: "key" | "value",
    val: string,
  ) => {
    const updated = [...customFields];
    updated[i][field] = val;
    setCustomFields(updated);
  };

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Organograma filtrado pela busca: mantem o no quando ele ou um descendente
  // corresponde ao termo (os ancestrais ficam visiveis para dar contexto).
  const searchTerm = deptSearch.trim().toLowerCase();
  const filteredTree = useMemo(() => {
    if (!searchTerm) return departments;
    const matches = (d: Department): boolean =>
      [d.name, d.centro_custo, d.id_orcamento, d.ambiente, d.cloud_filter]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(searchTerm));
    const filter = (nodes: Department[]): Department[] =>
      nodes
        .map((node) => {
          const children = filter(node.children || []);
          if (matches(node) || children.length > 0)
            return { ...node, children };
          return null;
        })
        .filter(Boolean) as Department[];
    return filter(departments);
  }, [departments, searchTerm]);

  const renderDeptNode = (
    dept: Department,
    depth: number,
    forceExpanded: boolean,
  ) => {
    const isExpanded = forceExpanded || expanded.has(dept.id);
    const isShared = sharedCosts.some(
      (config) => config.department_id === dept.id && config.enabled,
    );

    return (
      <Box key={dept.id}>
        <Paper
          sx={{
            p: 1.5,
            mb: 0.5,
            ml: depth * 3,
            display: "flex",
            alignItems: "center",
            gap: 1,
            borderLeft: 4,
            borderColor: isShared
              ? "#ED6C02"
              : depth === 0
                ? "primary.main"
                : depth === 1
                  ? "secondary.main"
                  : "grey.400",
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
          {depth === 0 ? (
            <Business color="primary" />
          ) : (
            <AccountTree color={isShared ? "warning" : "secondary"} />
          )}
          <Box sx={{ flexGrow: 1 }}>
            <Typography
              variant="body2"
              fontWeight={600}
              component="div"
              color={isShared ? "warning.dark" : "inherit"}
            >
              {dept.name}
              {dept.parent_id && (
                <Chip
                  label="filho"
                  size="small"
                  sx={{ ml: 1, height: 18, fontSize: "0.6rem" }}
                />
              )}
              {isShared && (
                <Chip
                  label="Custo compartilhado"
                  size="small"
                  color="warning"
                  sx={{ ml: 1, height: 18, fontSize: "0.6rem" }}
                />
              )}
            </Typography>
            <Box sx={{ display: "flex", gap: 1, mt: 0.25 }}>
              {dept.centro_custo && (
                <Chip
                  label={"CC: " + dept.centro_custo}
                  size="small"
                  variant="outlined"
                  sx={{ height: 18, fontSize: "0.6rem" }}
                />
              )}
              {dept.id_orcamento && (
                <Chip
                  label={"ID Orçamento: " + dept.id_orcamento}
                  size="small"
                  variant="outlined"
                  sx={{ height: 18, fontSize: "0.6rem" }}
                />
              )}
              {dept.ambiente && (
                <Chip
                  label={dept.ambiente}
                  size="small"
                  variant="outlined"
                  sx={{ height: 18, fontSize: "0.6rem" }}
                />
              )}
              {dept.cloud_filter && (
                <Chip
                  label={dept.cloud_filter}
                  size="small"
                  variant="outlined"
                  color="info"
                  sx={{ height: 18, fontSize: "0.6rem" }}
                />
              )}
            </Box>
            {dept.custom_fields &&
              Object.keys(dept.custom_fields).length > 0 && (
                <Box
                  sx={{ display: "flex", gap: 0.5, mt: 0.25, flexWrap: "wrap" }}
                >
                  {Object.entries(dept.custom_fields)
                    .slice(0, 3)
                    .map(([k, v]) => (
                      <Chip
                        key={k}
                        label={k + ": " + String(v).slice(0, 30)}
                        size="small"
                        variant="outlined"
                        sx={{ height: 18, fontSize: "0.55rem" }}
                      />
                    ))}
                  {Object.keys(dept.custom_fields).length > 3 && (
                    <Chip
                      label={"+" + (Object.keys(dept.custom_fields).length - 3)}
                      size="small"
                      sx={{ height: 18, fontSize: "0.55rem" }}
                    />
                  )}
                </Box>
              )}
          </Box>
          <Tooltip title="Adicionar filho">
            <IconButton size="small" onClick={() => openNewDept(dept.id)}>
              <Add fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={ORG.financialTooltip}>
            <IconButton
              size="small"
              onClick={() =>
                navigate("/departments/financial?department_id=" + dept.id)
              }
            >
              <AccountBalance fontSize="small" />
            </IconButton>
          </Tooltip>
          <IconButton size="small" onClick={() => void openEditDept(dept)}>
            <Edit fontSize="small" />
          </IconButton>
          <Tooltip title="Excluir organograma">
            <IconButton
              size="small"
              color="error"
              onClick={(event) => {
                event.stopPropagation();
                setDeleteDeptPending(dept);
              }}
            >
              <Delete fontSize="small" />
            </IconButton>
          </Tooltip>
        </Paper>

        {/* Children departments (recursive) */}
        {isExpanded &&
          dept.children.map((child) =>
            renderDeptNode(child, depth + 1, forceExpanded),
          )}
      </Box>
    );
  };

  if (loading)
    return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  return (
    <Box
      sx={{
        mx: -3,
        mt: -3,
        pb: 3,
        minHeight: "calc(100vh - 64px)",
        bgcolor: "#f8fafc",
      }}
    >
      {/* Faixa de contexto branca */}
      <Box
        sx={{
          px: 3,
          pt: 3,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ mb: 2 }}>
          <Typography variant="h4">{ORG.modelTitle}</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            {ORG.modelSubtitle}
          </Typography>
        </Box>
      </Box>

      {/* Área de trabalho com campos simples de 40px */}
      <Box
        sx={{
          p: 3,
          "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": {
            height: 40,
          },
          "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": {
            transform: "translate(14px, 11px) scale(1)",
          },
        }}
      >
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>
            {error}
          </Alert>
        )}
        {success && (
          <Alert
            severity="success"
            sx={{ mb: 2 }}
            onClose={() => setSuccess("")}
          >
            {success}
          </Alert>
        )}

        <Paper
          elevation={0}
          sx={{
            p: { xs: 1.5, md: 2 },
            borderRadius: 2,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 2, minHeight: 52, gap: 2, flexWrap: "wrap" }}
          >
            <Box>
              <Typography variant="h6">{ORG.hierarchySection}</Typography>
              <Typography variant="body2" color="text.secondary">
                {ORG.hierarchySectionDesc}
              </Typography>
            </Box>
            <Stack direction="row" spacing={1} flexWrap="wrap" flexShrink={0}>
              <Button
                size="small"
                startIcon={<AccountBalance />}
                onClick={(e) => setFinancialMenu(e.currentTarget)}
              >
                Cadastro financeiro
              </Button>
              <Menu
                anchorEl={financialMenu}
                open={Boolean(financialMenu)}
                onClose={() => setFinancialMenu(null)}
              >
                <MenuItem
                  onClick={() => {
                    setFinancialMenu(null);
                    navigate("/financial-planning?tab=orcamento");
                  }}
                >
                  Importar orçamento
                </MenuItem>
                <MenuItem
                  onClick={() => {
                    setFinancialMenu(null);
                    navigate("/financial-planning?tab=forecast-manual");
                  }}
                >
                  Importar forecast manual
                </MenuItem>
                <MenuItem
                  onClick={() => {
                    setFinancialMenu(null);
                    navigate("/financial-planning?tab=base-line-contratual");
                  }}
                >
                  Importar base line contratual
                </MenuItem>
                <MenuItem
                  onClick={() => {
                    setFinancialMenu(null);
                    navigate("/financial-planning");
                  }}
                >
                  Download modelo e editar cadastro
                </MenuItem>
              </Menu>
              <Button
                size="small"
                startIcon={
                  preparingOrganization ? (
                    <CircularProgress size={16} />
                  ) : (
                    <AccountTree />
                  )
                }
                onClick={() => setPrepareOrgPending(true)}
                disabled={preparingOrganization}
              >
                Preparar organograma cloud
              </Button>
              <Button
                size="small"
                startIcon={
                  importing ? <CircularProgress size={16} /> : <UploadFile />
                }
                onClick={handleImport}
                disabled={importing}
              >
                {importing ? "Importando..." : "Importar CSV"}
              </Button>
              <Button
                size="small"
                variant="contained"
                startIcon={<Add />}
                onClick={() => openNewDept(null)}
              >
                {ORG.newNode}
              </Button>
            </Stack>
          </Stack>
          <TextField
            fullWidth
            size="small"
            placeholder={ORG.searchPlaceholder}
            value={deptSearch}
            onChange={(event) => setDeptSearch(event.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <Search fontSize="small" />
                  </InputAdornment>
                ),
                sx: { height: 40 },
              },
            }}
            sx={{ mb: 2, maxWidth: 520 }}
          />
          {departments.length === 0 ? (
            <Box sx={{ py: 7 }}>
              <EmptyState message={ORG.emptyNone} />
            </Box>
          ) : filteredTree.length === 0 ? (
            <Box sx={{ py: 7 }}>
              <EmptyState message={ORG.emptySearch} />
            </Box>
          ) : (
            <Box>
              {filteredTree.map((d) =>
                renderDeptNode(d, 0, Boolean(searchTerm)),
              )}
            </Box>
          )}
        </Paper>
      </Box>

      {/* Department Dialog */}
      <Dialog
        open={deptDialog}
        onClose={() => setDeptDialog(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>{editingDept ? ORG.editNode : ORG.newNode}</DialogTitle>
        <DialogContent
          sx={{
            "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": {
              height: 40,
            },
            "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": {
              transform: "translate(14px, 11px) scale(1)",
            },
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            {deptDialogError && (
              <Alert severity="error" onClose={() => setDeptDialogError("")}>
                {deptDialogError}
              </Alert>
            )}
            {!sharedLoaded && editingDept && (
              <Typography variant="body2" color="text.secondary">
                Carregando configuração de custo compartilhado…
              </Typography>
            )}
            <TextField
              label="Nome"
              autoFocus
              value={deptForm.name}
              onChange={(e) =>
                setDeptForm({ ...deptForm, name: e.target.value })
              }
            />
            <DepartmentTreePicker
              departments={departments}
              value={deptForm.parent_id}
              onChange={(parentId) =>
                setDeptForm({ ...deptForm, parent_id: parentId })
              }
              label={ORG.parentNode}
              placeholder={ORG.parentRoot}
              allowClear
              clearLabel={ORG.parentRoot}
              blockedIds={blockedParentIds}
            />
            <Box sx={{ display: "flex", gap: 2 }}>
              <TextField
                label="Centro de Custo"
                value={deptForm.centro_custo}
                onChange={(e) =>
                  setDeptForm({ ...deptForm, centro_custo: e.target.value })
                }
                fullWidth
              />
              <TextField
                label="ID Orçamento"
                value={deptForm.id_orcamento}
                onChange={(e) =>
                  setDeptForm({ ...deptForm, id_orcamento: e.target.value })
                }
                fullWidth
              />
              <TextField
                label="Ambiente"
                value={deptForm.ambiente}
                onChange={(e) =>
                  setDeptForm({ ...deptForm, ambiente: e.target.value })
                }
                fullWidth
              />
            </Box>
            <TextField
              label="Cloud Filter"
              value={deptForm.cloud_filter}
              onChange={(e) =>
                setDeptForm({ ...deptForm, cloud_filter: e.target.value })
              }
              helperText="Filtrar por cloud (Azure-4P, Azure-TI, GCP-4P, etc.)"
            />

            <Divider />
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography variant="subtitle2">Campos Customizados</Typography>
              <Button size="small" startIcon={<Add />} onClick={addCustomField}>
                Adicionar
              </Button>
            </Box>
            {customFields.map((cf, i) => (
              <Box key={i} sx={{ display: "flex", gap: 1 }}>
                <TextField
                  label="Chave"
                  value={cf.key}
                  onChange={(e) => updateCustomField(i, "key", e.target.value)}
                  sx={{ width: "40%" }}
                />
                <TextField
                  label="Valor"
                  value={cf.value}
                  onChange={(e) =>
                    updateCustomField(i, "value", e.target.value)
                  }
                  sx={{ width: "50%" }}
                />
                <IconButton
                  size="small"
                  onClick={() => removeCustomField(i)}
                  color="error"
                >
                  <Delete fontSize="small" />
                </IconButton>
              </Box>
            ))}
            <FormControlLabel
              control={
                <Switch
                  checked={deptForm.active}
                  onChange={(e) =>
                    setDeptForm({ ...deptForm, active: e.target.checked })
                  }
                />
              }
              label="Ativo"
            />
            {editingDept && (
              <>
                <Divider />
                <FormControlLabel
                  control={
                    <Switch
                      checked={sharedEnabled}
                      onChange={(e) => setSharedEnabled(e.target.checked)}
                      disabled={!sharedLoaded}
                    />
                  }
                  label="Custo compartilhado"
                />
                {sharedEnabled && (
                  <>
                    <FormControl>
                      <FormLabel>Como ratear o custo</FormLabel>
                      <RadioGroup
                        value={sharedMode}
                        onChange={(event) =>
                          setSharedMode(event.target.value as SharedCostMode)
                        }
                      >
                        <FormControlLabel
                          value="consumption"
                          control={<Radio />}
                          label="Por consumo na mesma cloud de origem"
                        />
                        <FormControlLabel
                          value="equal"
                          control={<Radio />}
                          label="Equalitário entre quem consumiu no período"
                        />
                        <FormControlLabel
                          value="custom"
                          control={<Radio />}
                          label="Percentual personalizado (o que faltar fica no doador)"
                        />
                      </RadioGroup>
                    </FormControl>
                    {sharedMode === "consumption" ? (
                      <Typography variant="body2" color="text.secondary">
                        O valor rateado é o gasto deste nó do organograma e de
                        todos os filhos. Ligue a flag no nó Condomínio (folha),
                        não na diretoria de Infra — senão a DIR Dados/IA paga
                        uma fatia de Compra de RIs, redes e o resto da Infra,
                        não só os 2 MM do Condomínio. O rateio vai para os nós
                        da mesma cloud, na proporção do consumo. Quem não
                        consumiu não paga.
                      </Typography>
                    ) : (
                      <>
                        <DepartmentTreePicker
                          departments={departments}
                          value={sharedPicker}
                          onChange={(departmentId) => {
                            if (departmentId == null) return;
                            if (
                              sharedTargets.some(
                                (target) =>
                                  target.department_id === departmentId,
                              )
                            )
                              return;
                            const found = flattenDepartments(departments).find(
                              (item) => item.id === departmentId,
                            );
                            if (!found) return;
                            setSharedTargets([
                              ...sharedTargets,
                              {
                                department_id: found.id,
                                percent: null,
                                name: found.name,
                                path: found.path,
                              },
                            ]);
                            setSharedPicker(null);
                          }}
                          label="Adicionar nó destino no organograma"
                          placeholder="Selecione para incluir no rateio"
                          blockedIds={blockedParentIds}
                        />
                        {sharedTargets.map((target, index) => (
                          <Box
                            key={target.department_id}
                            sx={{
                              display: "flex",
                              gap: 1,
                              alignItems: "center",
                            }}
                          >
                            <Typography variant="body2" sx={{ flex: 1 }}>
                              {target.path || target.name}
                            </Typography>
                            {sharedMode === "custom" && (
                              <TextField
                                size="small"
                                type="number"
                                label="%"
                                value={target.percent ?? ""}
                                onChange={(event) => {
                                  const next = [...sharedTargets];
                                  next[index] = {
                                    ...target,
                                    percent:
                                      event.target.value === ""
                                        ? null
                                        : Number(event.target.value),
                                  };
                                  setSharedTargets(next);
                                }}
                                sx={{ width: 100 }}
                              />
                            )}
                            <IconButton
                              size="small"
                              color="error"
                              onClick={() =>
                                setSharedTargets(
                                  sharedTargets.filter(
                                    (item) =>
                                      item.department_id !==
                                      target.department_id,
                                  ),
                                )
                              }
                            >
                              <Delete fontSize="small" />
                            </IconButton>
                          </Box>
                        ))}
                        {sharedMode === "custom" && (
                          <Typography variant="caption" color="text.secondary">
                            Soma:{" "}
                            {sharedTargets.reduce(
                              (sum, target) =>
                                sum + Number(target.percent || 0),
                              0,
                            )}
                            %. O restante permanece no doador.
                          </Typography>
                        )}
                        {sharedMode === "equal" && (
                          <Typography variant="caption" color="text.secondary">
                            O valor é dividido igualmente entre os destinos
                            selecionados que tiverem consumo no período.
                          </Typography>
                        )}
                      </>
                    )}
                  </>
                )}
              </>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeptDialog(false)} disabled={savingDept}>
            Cancelar
          </Button>
          <Button
            onClick={() => void saveDept()}
            variant="contained"
            disabled={savingDept || (Boolean(editingDept) && !sharedLoaded)}
          >
            {savingDept ? "Salvando..." : "Salvar"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirmações Material — nunca window.confirm */}
      <Dialog
        open={Boolean(deleteDeptPending)}
        onClose={() => setDeleteDeptPending(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>{ORG.deleteConfirm}</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a excluir{" "}
            <strong>{deleteDeptPending?.name}</strong>. Filhos sobem para a raiz
            e o cadastro de custo compartilhado vinculado será removido.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteDeptPending(null)}>Cancelar</Button>
          <Button
            variant="contained"
            color="error"
            onClick={() => void removeDept()}
          >
            {ORG.deleteAction}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={prepareOrgPending}
        onClose={() => setPrepareOrgPending(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Preparar organograma cloud?</DialogTitle>
        <DialogContent>
          <Typography>
            Preparar Consumo Cloud &gt; Showback/Chargeback &gt; Provedores e
            mover as diretorias atuais para Showback &gt; Azure? Isso não
            reprocessa os dados.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setPrepareOrgPending(false)}>Cancelar</Button>
          <Button
            variant="contained"
            color="warning"
            onClick={() => void prepareCloudOrganization()}
          >
            Preparar organograma
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
