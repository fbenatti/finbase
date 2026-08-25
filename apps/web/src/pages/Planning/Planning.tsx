import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, IconButton, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack, Tab, Tabs, TextField, Tooltip, Typography,
} from "@mui/material";
import { CloudDownload, Edit, ExpandLess, ExpandMore, History, Save, Search, UploadFile } from "@mui/icons-material";
import {
  DataGrid, GridColDef, GridRowModel,
} from "@mui/x-data-grid";
import { Department, downloadPlanningTemplate, downloadSimulatedBudget, FinancialType, getDepartments, getDestinationSchema, getDestinationTables, getFinancialTypes, getPlanningGrid, getPlanningVersions, importPlanningExcel, PlanningRow, PlanningVersion, restorePlanningVersion, savePlanningGrid } from "../../services/api";
import EmptyState from "../../components/EmptyState";

const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const SYSTEM = new Set(["realizado", "forecast"]);

type DisplayRow = {
  id: string;
  department_id: number;
  parent_id: number | null;
  name: string;
  centro_custo: string | null;
  idPath: string[];
  department_levels: string[];
  values: number[];
  accumulated: number[];
};

const fmt = (value: unknown) => Number(value || 0).toLocaleString("pt-BR");

export default function Planning({ budgetMode = false }: { budgetMode?: boolean }) {
  const yearNow = new Date().getFullYear();
  const fileInput = useRef<HTMLInputElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [types, setTypes] = useState<FinancialType[]>([]);
  // Initializa com a aba da URL para a grade carregar imediatamente e o Tabs
  // nunca receber um value inválido na primeira renderização.
  const [type, setType] = useState<string>(() =>
    budgetMode
      ? (searchParams.get("tab") === "forecast-manual" ? "forecast-manual" : "orcamento")
      : "",
  );
  const [year, setYear] = useState(yearNow);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [rows, setRows] = useState<PlanningRow[]>([]);
  const [versions, setVersions] = useState<PlanningVersion[]>([]);
  const [snapshotId, setSnapshotId] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  // Dialogo de edicao mensal por departamento
  const [editTarget, setEditTarget] = useState<DisplayRow | null>(null);
  const [editValues, setEditValues] = useState<number[]>([]);
  const [editError, setEditError] = useState("");

  // Forecast simulado
  const [forecastDialog, setForecastDialog] = useState(false);
  const [basePeriod, setBasePeriod] = useState(yearNow + "-01");
  const [detailedForecast, setDetailedForecast] = useState(false);
  const [focusColumns, setFocusColumns] = useState<string[]>([]);
  const [focusColumn, setFocusColumn] = useState("");
  const [forecastCloud, setForecastCloud] = useState("gcp");

  const load = async () => {
    if (!type) return;
    setLoading(true);
    try {
      const [grid, history] = await Promise.all([
        getPlanningGrid(type, year, snapshotId ? Number(snapshotId) : undefined),
        getPlanningVersions(type, year),
      ]);
      setRows(grid.rows);
      setVersions(history);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    Promise.all([getFinancialTypes(), getDepartments(false)])
      .then(([t, d]) => {
        const requested = searchParams.get("type");
        setTypes(t);
        setType(prev => budgetMode ? (searchParams.get("tab") === "forecast-manual" ? "forecast-manual" : "orcamento") : prev || (requested && t.some(x => x.code === requested) ? requested : "") || t.find(x => x.active && !SYSTEM.has(x.code))?.code || "");
        setDepartments(flatten(d));
      })
      .catch(e => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, budgetMode]);

  useEffect(() => { setSnapshotId(""); }, [type, year]);
  useEffect(() => { void load(); }, [type, year, snapshotId]);

  // ── Constroi as linhas da arvore (valores proprios + acumulado por hierarquia) ──
  const displayRows = useMemo(() => {
    const byDepartment = new Map(departments.map(d => [d.id, d]));
    const own = new Map<number, number[]>();
    rows.filter(r => r.entity_type === "department").forEach(r => own.set(r.department_id, r.values));
    const children = new Map<number | null, number[]>();
    departments.forEach(d => children.set(d.parent_id, [...(children.get(d.parent_id) || []), d.id]));
    const total = (id: number): number[] => {
      const v = [...(own.get(id) || Array(12).fill(0))];
      (children.get(id) || []).forEach(child => total(child).forEach((n, i) => v[i] += n));
      return v;
    };
    const idPathOf = (d: Department): string[] => {
      const ids: string[] = [];
      let cur: Department | undefined = d;
      while (cur) { ids.unshift("department:" + cur.id); cur = cur.parent_id ? byDepartment.get(cur.parent_id) : undefined; }
      return ids;
    };
    const levelsOf = (d: Department): string[] => {
      const a: string[] = [];
      let cur: Department | undefined = d;
      while (cur) { a.unshift(cur.name); cur = cur.parent_id ? byDepartment.get(cur.parent_id) : undefined; }
      return a;
    };
    return departments.filter(d => d.active).map(d => ({
      id: "department:" + d.id,
      department_id: d.id,
      parent_id: d.parent_id,
      name: d.name,
      centro_custo: d.centro_custo,
      idPath: idPathOf(d),
      department_levels: levelsOf(d),
      values: own.get(d.id) || Array(12).fill(0),
      accumulated: total(d.id),
    }));
  }, [rows, departments]);

  // Conjunto de ids que possuem filhos
  const hasChildrenIds = useMemo(() => {
    const set = new Set<string>();
    displayRows.forEach(r => r.idPath.slice(0, -1).forEach(p => set.add(p)));
    return set;
  }, [displayRows]);

  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // Expande o primeiro nivel quando uma nova grade (tipo/ano/snapshot) carrega
  const treeInitRef = useRef<string | null>(null);
  useEffect(() => {
    const key = type + "|" + year + "|" + snapshotId;
    if (!displayRows.length || treeInitRef.current === key) return;
    treeInitRef.current = key;
    setExpandedIds(new Set(displayRows.filter(r => r.idPath.length === 1 && hasChildrenIds.has(r.id)).map(r => r.id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayRows, hasChildrenIds, type, year, snapshotId]);

  // Ordem da arvore (DFS pre-order) a partir da lista plana
  const treeOrder = useMemo(() => {
    const byParent = new Map<number | null, number[]>();
    departments.forEach(d => byParent.set(d.parent_id, [...(byParent.get(d.parent_id) || []), d.id]));
    const order: number[] = [];
    const visit = (ids: number[] | undefined) => {
      (ids || []).forEach(id => { order.push(id); visit(byParent.get(id)); });
    };
    visit(byParent.get(null));
    return order;
  }, [departments]);

  const toggleExpand = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // ── Busca: mantem os nos correspondentes e os ancestrais; expande tudo ──
  const searchTerm = search.trim().toLowerCase();
  const filteredRows = useMemo(() => {
    if (!searchTerm) return displayRows;
    const visible = new Set<string>();
    for (const r of displayRows) {
      const hay = [r.name, r.centro_custo || ""].join(" ").toLowerCase();
      if (hay.includes(searchTerm)) r.idPath.forEach(p => visible.add(p));
    }
    return displayRows.filter(r => visible.has(r.id));
  }, [displayRows, searchTerm]);

  // Ao limpar a busca, mantem expandidos os ancestrais dos resultados, para o
  // departamento encontrado nao "sumir" da arvore.
  const lastSearchPaths = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (searchTerm) {
      lastSearchPaths.current = new Set(filteredRows.flatMap(r => r.idPath));
    } else if (lastSearchPaths.current.size > 0) {
      setExpandedIds(prev => new Set([...prev, ...lastSearchPaths.current]));
      lastSearchPaths.current = new Set();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTerm, filteredRows]);

  // Linhas visiveis: durante a busca, tudo do filtro; senao, respeita expansao
  const visibleRows = useMemo(() => {
    if (searchTerm) return filteredRows;
    const byId = new Map(displayRows.map(r => [r.id, r]));
    return treeOrder
      .map(id => byId.get("department:" + id))
      .filter((r): r is DisplayRow => Boolean(r))
      .filter(r => r.idPath.slice(0, -1).every(p => expandedIds.has(p)));
  }, [treeOrder, displayRows, expandedIds, searchTerm, filteredRows]);

  const editable = !!type && !SYSTEM.has(type) && !!types.find(t => t.code === type)?.active && !snapshotId;

  // ── Edicao de celula ──
  const processRowUpdate = (newRow: GridRowModel) => {
    const changed = newRow as DisplayRow;
    const raw = changed as unknown as Record<string, unknown>;
    const values = MONTHS.map((m, i) => {
      const v = raw[m];
      return v === undefined || v === null || v === "" ? (changed.values[i] ?? 0) : Number(v);
    });
    setRows(prev => {
      const idx = prev.findIndex(r => r.entity_type === "department" && r.department_id === changed.department_id);
      const updated: PlanningRow = { entity_type: "department", department_id: changed.department_id, department_levels: changed.department_levels, values };
      if (idx >= 0) return prev.map((r, i) => i === idx ? updated : r);
      return [...prev, updated];
    });
    return { ...changed, values };
  };

  // ── Dialogo de edicao mensal ──
  const openEdit = (row: DisplayRow) => {
    setEditTarget(row);
    setEditValues([...row.values]);
    setEditError("");
  };

  const applyEdit = () => {
    if (!editTarget) return;
    const values = editValues.map(v => (Number.isFinite(v) ? v : 0));
    setRows(prev => {
      const idx = prev.findIndex(r => r.entity_type === "department" && r.department_id === editTarget.department_id);
      const updated: PlanningRow = { entity_type: "department", department_id: editTarget.department_id, department_levels: editTarget.department_levels, values };
      if (idx >= 0) return prev.map((r, i) => i === idx ? updated : r);
      return [...prev, updated];
    });
    setEditTarget(null);
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const result = await savePlanningGrid(type, year, displayRows.map(r => ({ entity_type: "department" as const, department_id: r.department_id, department_levels: r.department_levels, values: r.values })));
      const previous = versions.find(v => v.is_current);
      setMessage(
        "Alterações publicadas como v" + result.version.version + "." +
        (previous ? " O snapshot anterior (v" + previous.version + ") foi preservado no histórico." : ""),
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !type) return;
    setSaving(true);
    try {
      await importPlanningExcel(type, year, file);
      setMessage("Importação publicada em um novo snapshot.");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
      event.target.value = "";
    }
  };

  const template = async () => {
    const blob = await downloadPlanningTemplate(year);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "modelo-cadastro-financeiro-" + year + ".xlsx";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const loadFocusColumns = async () => {
    try {
      const destinations = await getDestinationTables(true);
      const focus = destinations.find(destination => /focus/i.test(destination.name + " " + destination.physical_name));
      if (!focus) { setFocusColumns([]); setFocusColumn(""); return; }
      const schema = await getDestinationSchema(focus.id);
      const columns = schema.columns.map(column => column.name).filter(name => !/^departamento_nivel_|^departamento$/i.test(name));
      const suggested = columns.find(name => name.replace(/_/g, "").toLowerCase() === "servicename") || columns.find(name => name.replace(/_/g, "").toLowerCase() === "metercategory") || columns[0] || "";
      setFocusColumns(columns);
      setFocusColumn(suggested);
    } catch {
      setFocusColumns([]);
      setFocusColumn("");
    }
  };

  const simulatedForecast = async () => {
    setSaving(true);
    setError("");
    try {
      const period = basePeriod.replace("-", "");
      const blob = await downloadSimulatedBudget(year, period, detailedForecast, detailedForecast ? focusColumn : undefined, forecastCloud);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "forecast-simulado-" + (detailedForecast ? "detalhado" : "consolidado") + (forecastCloud ? "-" + forecastCloud : "") + "-" + year + "-base-" + period + ".xlsx";
      link.click();
      URL.revokeObjectURL(link.href);
      setMessage("Forecast simulado " + (detailedForecast ? "detalhado" : "consolidado") + " " + (forecastCloud || "todas as clouds") + " gerado com a base de " + basePeriod + ".");
      setForecastDialog(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const restoreSnapshot = async () => {
    const selected = versions.find(version => version.id === Number(snapshotId)) || versions.find(version => !version.is_current);
    if (!selected) return setError("Selecione um snapshot anterior para restaurar.");
    await restorePlanningVersion(type, year, selected.id);
    setSnapshotId("");
    await load();
    setMessage("Snapshot restaurado como nova versão válida.");
  };

  const columns: GridColDef[] = [
    {
      field: "name",
      headerName: "Hierarquia de departamentos",
      minWidth: 360,
      flex: 1.3,
      editable: false,
      renderCell: ({ row }: { row: DisplayRow }) => {
        const hasChildren = hasChildrenIds.has(row.id);
        const isExpanded = searchTerm || expandedIds.has(row.id);
        const label = row.centro_custo ? row.name + " · CC: " + row.centro_custo : row.name;
        return (
          <Stack
            direction="row"
            alignItems="center"
            sx={{
              minWidth: 0,
              width: "100%",
              height: "100%",
              overflow: "hidden",
              lineHeight: 1.2,
              whiteSpace: "nowrap",
            }}
          >
            <Box sx={{ width: (row.idPath.length - 1) * 20, flexShrink: 0 }} />
            {hasChildren ? (
              <IconButton size="small" sx={{ mr: 0.25, flexShrink: 0 }} aria-label={"Expandir " + row.name} onClick={(event) => { event.stopPropagation(); toggleExpand(row.id); }}>
                {isExpanded ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
              </IconButton>
            ) : (
              <Box sx={{ width: 34, flexShrink: 0 }} />
            )}
            <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
              <Tooltip title={label}>
                <Typography variant="body2" fontWeight={600} noWrap sx={{ display: "block", overflow: "hidden", textOverflow: "ellipsis" }}>{row.name}</Typography>
              </Tooltip>
              {row.centro_custo && (
                <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block", overflow: "hidden", textOverflow: "ellipsis" }}>CC: {row.centro_custo}</Typography>
              )}
            </Box>
          </Stack>
        );
      },
    },
    ...MONTHS.map((month, index): GridColDef => ({
      field: month,
      headerName: month.toUpperCase(),
      width: 100,
      type: "number",
      editable,
      valueGetter: (_, row: DisplayRow) => row.values[index],
      valueSetter: (value, row: DisplayRow) => ({ ...row, [month]: Number(value) || 0 }),
    })),
    ...MONTHS.map((month, index): GridColDef => ({
      field: "acc_" + month,
      headerName: month.toUpperCase() + " acum.",
      width: 118,
      type: "number",
      editable: false,
      valueGetter: (_, row: DisplayRow) => row.accumulated[index],
      valueFormatter: (value) => fmt(value),
    })),
    {
      field: "actions",
      headerName: "Ações",
      width: 64,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: DisplayRow }) => (
        <IconButton size="small" disabled={!editable} aria-label={"Editar " + row.name} onClick={(event) => { event.stopPropagation(); openEdit(row); }}>
          <Edit fontSize="small" />
        </IconButton>
      ),
    },
  ];

  const currentVersion = versions.find(version => version.is_current);

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      {/* Faixa de contexto branca */}
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h4">{budgetMode ? "Orçamentos" : "Cadastro financeiro"}</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            {budgetMode
              ? (type === "forecast-manual"
                  ? "Carga mensal, edição e snapshots publicados de forecast manual na hierarquia de departamentos."
                  : "Carga mensal, edição e snapshots publicados de orçamento na hierarquia de departamentos.")
              : "Valores próprios por nível de departamento; totais acumulados seguem a hierarquia."}
          </Typography>
        </Box>
        {budgetMode && (
          <Tabs
            value={type}
            onChange={(_, value: string) => {
              setType(value);
              setSearchParams({ tab: value }, { replace: true });
            }}
            aria-label="Cadastro de orçamentos"
            sx={{ mb: 0, minHeight: 42 }}
          >
            <Tab value="orcamento" label="Orçamento" sx={{ minHeight: 42 }} />
            <Tab value="forecast-manual" label="Forecast Manual" sx={{ minHeight: 42 }} />
          </Tabs>
        )}
      </Box>

      {/* Área de trabalho com campos simples de 40px */}
      <Box sx={{
        p: 3,
        "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
        "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
      }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>
        )}
        {message && (
          <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage("")}>{message}</Alert>
        )}

        <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2, minHeight: 52, gap: 2, flexWrap: "wrap" }}>
            <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
              {!budgetMode && (
                <FormControl sx={{ minWidth: 230 }}>
                  <InputLabel>Tipificação</InputLabel>
                  <Select label="Tipificação" value={type} onChange={e => setType(e.target.value)}>
                    {types.map(t => <MenuItem key={t.code} value={t.code} disabled={!t.active}>{t.name}{SYSTEM.has(t.code) ? " (sistema)" : ""}</MenuItem>)}
                  </Select>
                </FormControl>
              )}
              <TextField label="Ano" type="number" value={year} onChange={e => setYear(Number(e.target.value))} sx={{ width: 110 }} />
              <FormControl sx={{ minWidth: 290 }}>
                <InputLabel>Snapshot</InputLabel>
                <Select label="Snapshot" value={snapshotId} onChange={e => setSnapshotId(String(e.target.value))}>
                  <MenuItem value="">Versão atual{currentVersion ? " · v" + currentVersion.version : ""}</MenuItem>
                  {versions.filter(version => !version.is_current).map(version => (
                    <MenuItem key={version.id} value={String(version.id)}>
                      {"v" + version.version + " · " + version.source + " · " + new Date(version.created_at).toLocaleString("pt-BR")}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>
            <Stack direction="row" spacing={1} flexWrap="wrap" flexShrink={0}>
              <Button size="small" startIcon={<CloudDownload />} onClick={() => void template()}>Baixar modelo</Button>
              {type === "orcamento" && <Button size="small" startIcon={<CloudDownload />} disabled={saving} onClick={() => setForecastDialog(true)}>Forecast simulado</Button>}
              <Button size="small" startIcon={<UploadFile />} disabled={!editable || saving} onClick={() => fileInput.current?.click()}>Importar Excel</Button>
              <Button size="small" startIcon={<History />} disabled={!snapshotId || saving} onClick={() => void restoreSnapshot()}>Restaurar snapshot</Button>
              <Button size="small" variant="contained" startIcon={saving ? <CircularProgress size={16} /> : <Save />} disabled={!editable || saving} onClick={() => void save()}>Salvar</Button>
              <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={importFile} />
            </Stack>
          </Stack>

          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
            <TextField
              fullWidth
              size="small"
              placeholder="Buscar departamento (nome ou centro de custo)"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
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
              sx={{ maxWidth: 520 }}
            />
            <Stack direction="row" spacing={1} flexShrink={0}>
              <Button size="small" disabled={Boolean(searchTerm)} onClick={() => setExpandedIds(new Set(hasChildrenIds))}>Expandir tudo</Button>
              <Button size="small" disabled={Boolean(searchTerm)} onClick={() => setExpandedIds(new Set())}>Recolher</Button>
            </Stack>
          </Stack>

          {snapshotId && (
            <Alert severity="info" sx={{ mb: 2 }}>Você está visualizando um snapshot histórico em modo somente leitura. Restaure-o para publicar uma nova versão corrente.</Alert>
          )}
          {!editable && type && (
            <Alert severity="info" sx={{ mb: 2 }}>Esta tipificação é alimentada pelo processo do sistema e não aceita edição manual.</Alert>
          )}

          {departments.length === 0 ? (
            <Box sx={{ py: 7 }}>
              <EmptyState message="Nenhum departamento cadastrado para montar a hierarquia de orçamento." />
            </Box>
          ) : filteredRows.length === 0 ? (
            <Box sx={{ py: 7 }}>
              <EmptyState message={searchTerm ? "Nenhum departamento corresponde à busca." : "Nenhum departamento disponível."} />
            </Box>
          ) : (
            <Box sx={{ height: 560, width: "100%" }}>
              <DataGrid
                rows={visibleRows}
                columns={columns}
                loading={loading}
                getRowId={(row: DisplayRow) => row.id}
                getRowHeight={() => 56}
                processRowUpdate={processRowUpdate}
                onProcessRowUpdateError={(e) => setError(String(e))}
                disableRowSelectionOnClick
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 2,
                  bgcolor: "background.paper",
                  "& .MuiDataGrid-columnHeaders": { bgcolor: "#f8fafc", borderBottomColor: "divider" },
                  "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus": { outline: "none" },
                }}
              />
            </Box>
          )}
        </Paper>
      </Box>

      {/* Dialogo de edicao mensal */}
      <Dialog open={Boolean(editTarget)} onClose={() => setEditTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Editar valores — {editTarget?.name}</DialogTitle>
        <DialogContent sx={{
          "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
          "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
        }}>
          {editError && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setEditError("")}>{editError}</Alert>}
          <Stack direction="row" gap={1.5} flexWrap="wrap" sx={{ mt: 1 }}>
            {MONTHS.map((month, i) => (
              <TextField
                key={month}
                label={month.toUpperCase()}
                type="number"
                value={editValues[i] ?? 0}
                onChange={(e) => {
                  const next = [...editValues];
                  next[i] = Number(e.target.value);
                  setEditValues(next);
                }}
                sx={{ width: 120 }}
              />
            ))}
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 2 }}>
            O total acumulado da linha e dos nós pais é recalculado pela hierarquia automaticamente.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditTarget(null)}>Cancelar</Button>
          <Button variant="contained" onClick={applyEdit}>Aplicar</Button>
        </DialogActions>
      </Dialog>

      {/* Forecast simulado */}
      <Dialog open={forecastDialog} onClose={() => !saving && setForecastDialog(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Gerar forecast simulado</DialogTitle>
        <DialogContent sx={{
          "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
          "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
        }}>
          <Stack gap={2} sx={{ pt: 1 }}>
            <TextField label="Mês de referência" type="month" value={basePeriod} onChange={event => setBasePeriod(event.target.value)} InputLabelProps={{ shrink: true }} inputProps={{ min: "2000-01", max: "2100-12" }} />
            <FormControl fullWidth>
              <InputLabel>Cloud</InputLabel>
              <Select label="Cloud" value={forecastCloud} onChange={event => setForecastCloud(String(event.target.value))}>
                <MenuItem value="gcp">GCP (4P e TI)</MenuItem>
                <MenuItem value="azure">Azure</MenuItem>
                <MenuItem value="">Todas</MenuItem>
              </Select>
            </FormControl>
            <FormControlLabel
              control={<Checkbox checked={detailedForecast} onChange={event => { setDetailedForecast(event.target.checked); if (event.target.checked) void loadFocusColumns(); }} />}
              label="Gerar arquivo detalhado para defesa orçamentária"
            />
            {detailedForecast && (
              <FormControl fullWidth>
                <InputLabel>Coluna Focus (sugestão)</InputLabel>
                <Select label="Coluna Focus (sugestão)" value={focusColumn} onChange={event => setFocusColumn(String(event.target.value))} disabled={!focusColumns.length}>
                  {focusColumns.map(column => <MenuItem key={column} value={column}>{column}</MenuItem>)}
                </Select>
              </FormControl>
            )}
            <Alert severity="info">Replica o realizado do mês escolhido nos 12 meses. Com GCP, só entram departamentos com custo gcp-4p/gcp-ti. O detalhado usa ServiceName. Importar o consolidado publica um snapshot novo e substitui o orçamento atual — não misture com Azure no mesmo arquivo se quiser manter as duas clouds.</Alert>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setForecastDialog(false)} disabled={saving}>Cancelar</Button>
          <Button variant="contained" onClick={() => void simulatedForecast()} disabled={saving}>Baixar {detailedForecast ? "detalhado" : "consolidado"}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

function flatten(nodes: Department[]): Department[] {
  return nodes.flatMap(d => [d, ...flatten(d.children || [])]);
}
