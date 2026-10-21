import { dataDisplayText } from "../../utils/dataPresentation";
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  InputAdornment,
  IconButton,
  Menu,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Add, Download as CloudDownload, Edit, ChevronUp as ExpandLess,
  ChevronDown as ExpandMore, Reset as Restore, Save,
  Upload as UploadFile, Search,
} from "@carbon/react/icons";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import * as XLSX from "xlsx";
import {
  BudgetDefenseRequest,
  Department,
  downloadPlanningTemplate,
  downloadSimulatedBudget,
  getDepartments,
  getPlanningGrid,
  getPlanningScopes,
  getPlanningTimeline,
  getPlanningVersions,
  getPlanningYears,
  importPlanningExcel,
  getPlanningMissingDepartments,
  PlanningEventType,
  PlanningPublication,
  PlanningRow,
  PlanningScope,
  PlanningVersion,
  PlanningMissingDepartment,
  publishDefenseToBudget,
  restorePlanningVersion,
  savePlanningGrid,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import { useAuth } from "../../auth/AuthContext";
import BudgetDefensePanel from "./BudgetDefensePanel";

const MONTHS = [
  "Jan",
  "Fev",
  "Mar",
  "Abr",
  "Mai",
  "Jun",
  "Jul",
  "Ago",
  "Set",
  "Out",
  "Nov",
  "Dez",
];
const EVENTS: PlanningEventType[] = [
  "CARGA_INICIAL",
  "DEFESA",
  "APROVACAO",
  "CORTE",
  "AJUSTE",
  "INCLUSAO_PROJETO",
  "REALOCACAO",
  "RESTAURACAO",
  "OUTRO",
];
const EXPANDED_DEPARTMENTS_STORAGE_KEY =
  "openfinops.financialPlanning.expandedDepartments";

const loadExpandedDepartments = (): Set<number> => {
  try {
    const stored = window.localStorage.getItem(
      EXPANDED_DEPARTMENTS_STORAGE_KEY,
    );
    if (!stored) return new Set();
    const values: unknown = JSON.parse(stored);
    if (!Array.isArray(values)) return new Set();
    return new Set(
      values.filter(
        (value): value is number => Number.isInteger(value) && value > 0,
      ),
    );
  } catch {
    return new Set();
  }
};

const saveExpandedDepartments = (expanded: Set<number>) => {
  try {
    window.localStorage.setItem(
      EXPANDED_DEPARTMENTS_STORAGE_KEY,
      JSON.stringify([...expanded]),
    );
  } catch {
    // Storage may be unavailable in restricted/private browser contexts.
  }
};

const money = (value: number) =>
  Number(value || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
export type PlanningModuleProps = {
  type: string;
  title: string;
  help: string;
  budget?: boolean;
};
type DisplayRow = {
  id: string;
  department_id: number;
  name: string;
  centro_custo: string | null;
  depth: number;
  values: number[];
  accumulated: number[];
  hasOwn: boolean;
  hasChild: boolean;
};
type PublishAction = "save" | "import" | "restore" | null;

export default function Planning({
  type,
  title,
  help,
  budget = false,
}: PlanningModuleProps) {
  const currentYear = new Date().getFullYear();
  const fileInput = useRef<HTMLInputElement>(null);
  const [exportAnchor, setExportAnchor] = useState<HTMLElement | null>(null);
  const { hasPerm } = useAuth();
  const isDefense = type === "defesa-orcamentaria";
  const canRequestDefense =
    hasPerm("planning", "create", "defesa-solicitar") ||
    hasPerm("planning", "update", "defesa-solicitar");
  const canPublishDefense =
    hasPerm("planning", "create", "defesa-publicar") ||
    hasPerm("planning", "update", "defesa-publicar");
  const [year, setYear] = useState(currentYear),
    [years, setYears] = useState<number[]>([currentYear]),
    [departments, setDepartments] = useState<Department[]>([]),
    [rows, setRows] = useState<PlanningRow[]>([]),
    [dirtyDepartmentIds, setDirtyDepartmentIds] = useState<Set<number>>(new Set()),
    [versions, setVersions] = useState<PlanningVersion[]>([]),
    [timeline, setTimeline] = useState<PlanningVersion[]>([]),
    [snapshotId, setSnapshotId] = useState(""),
    [search, setSearch] = useState(""),
    [expanded, setExpanded] = useState<Set<number>>(loadExpandedDepartments),
    [loading, setLoading] = useState(true),
    [saving, setSaving] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [edit, setEdit] = useState<DisplayRow | null>(null),
    // Keep the transient "-" while a negative value is being typed. Parsing
    // immediately with Number("-") produces NaN and makes the input lose it.
    [editValues, setEditValues] = useState<Array<number | string>>([]),
    [addYearOpen, setAddYearOpen] = useState(false),
    [newYear, setNewYear] = useState(String(currentYear)),
    [publishAction, setPublishAction] = useState<PublishAction>(null),
    [importFile, setImportFile] = useState<File | null>(null),
    [restoreId, setRestoreId] = useState<number | null>(null);
  const [missingDepartments, setMissingDepartments] = useState<PlanningMissingDepartment[]>([]);
  const [selectedMissing, setSelectedMissing] = useState<string[][]>([]);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportYear, setExportYear] = useState(currentYear);
  const [publication, setPublication] = useState<PlanningPublication>({
    title: "",
    description: "",
    event_type: "CARGA_INICIAL",
    effective_date: new Date().toISOString().slice(0, 10),
  });
  const [scopes, setScopes] = useState<PlanningScope[]>([]);
  const [idOrcamentoFilter, setIdOrcamentoFilter] = useState("");
  const [cloudFilter, setCloudFilter] = useState("");
  const [defenseRequest, setDefenseRequest] = useState<BudgetDefenseRequest | null>(null);
  const [individualLines, setIndividualLines] = useState<BudgetDefenseRequest[]>([]);
  const [defenseRefreshKey, setDefenseRefreshKey] = useState(0);
  const [approveVersion, setApproveVersion] = useState<PlanningVersion | null>(
    null,
  );
  const [approval, setApproval] = useState({
    title: "",
    description: "",
    effective_date: new Date().toISOString().slice(0, 10),
  });
  const load = async () => {
    setLoading(true);
    try {
      const [grid, history, events, available] = await Promise.all([
        getPlanningGrid(
          type,
          year,
          snapshotId ? Number(snapshotId) : undefined,
          type === "defesa-orcamentaria" ? idOrcamentoFilter : undefined,
          type === "defesa-orcamentaria" && idOrcamentoFilter
            ? cloudFilter
            : undefined,
          type === "defesa-orcamentaria" ? defenseRequest?.id : undefined,
        ),
        getPlanningVersions(type, year),
        getPlanningTimeline(type, year),
        getPlanningYears(type),
      ]);
      setRows(grid.rows);
      setDirtyDepartmentIds(new Set());
      setIndividualLines(grid.individual_lines || []);
      setVersions(history);
      setTimeline(events);
      setYears(
        Array.from(new Set([currentYear, year, ...available])).sort(
          (a, b) => b - a,
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  const loadDepartments = async () => {
    try {
      const data = await getDepartments(false);
      setDepartments(flatten(data));
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    void Promise.all([getDepartments(false), getPlanningScopes()])
      .then(([data, scopeData]) => {
        setDepartments(flatten(data));
        setScopes(scopeData);
      })
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    setSnapshotId("");
    setIdOrcamentoFilter("");
    setCloudFilter("");
    setDefenseRequest(null);
  }, [year, type]);
  useEffect(() => {
    void load();
  }, [year, snapshotId, type, idOrcamentoFilter, cloudFilter, defenseRequest?.id]);
  const displayRows = useMemo(() => {
    const own = new Map(rows.map((row) => [row.department_id, row.values]));
    const byId = new Map(departments.map((d) => [d.id, d]));
    const children = new Map<number | null, Department[]>();
    departments.forEach((d) =>
      children.set(d.parent_id, [...(children.get(d.parent_id) || []), d]),
    );
    const total = (d: Department): number[] => {
      const result = [...(own.get(d.id) || Array(12).fill(0))];
      (children.get(d.id) || []).forEach((child) =>
        total(child).forEach((v, i) => (result[i] += v)),
      );
      return result;
    };
    return departments
      .filter((d) => d.active)
      .map((d) => {
        let depth = 0,
          parent = byId.get(d.parent_id || -1);
        while (parent) {
          depth++;
          parent = byId.get(parent.parent_id || -1);
        }
        const values = own.get(d.id) || Array(12).fill(0),
          accumulated = total(d);
        return {
          id: `department:${d.id}`,
          department_id: d.id,
          name: d.name,
          centro_custo: d.centro_custo,
          depth,
          values,
          accumulated,
          hasOwn: values.some((v) => Math.abs(v) > 1e-9),
          hasChild: accumulated.some((v, i) => Math.abs(v - values[i]) > 1e-9),
        };
      });
  }, [departments, rows]);
  const visibleRows = useMemo(() => {
    const byId = new Map(displayRows.map((row) => [row.department_id, row]));
    const kids = new Map<number | null, number[]>();
    departments.forEach((d) =>
      kids.set(d.parent_id, [...(kids.get(d.parent_id) || []), d.id]),
    );
    const term = search.toLowerCase().trim();
    const walk = (parent: number | null): DisplayRow[] =>
      (kids.get(parent) || []).flatMap((id) => {
        const row = byId.get(id);
        if (!row) return [];
        const childRows = walk(id);
        const match =
          !term ||
          `${row.name} ${row.centro_custo || ""}`.toLowerCase().includes(term);
        return term
          ? match
            ? [row, ...childRows]
            : childRows
          : [row, ...(expanded.has(id) ? childRows : [])];
      });
    return walk(null);
  }, [departments, displayRows, expanded, search]);
  const defenseLocked =
    isDefense &&
    Boolean(defenseRequest) &&
    defenseRequest?.status !== "RASCUNHO" &&
    defenseRequest?.status !== "SOLICITADA";
  const editable = !snapshotId && !defenseLocked;
  // Na Defesa Orçamentária a grade só faz sentido depois de escolher o escopo:
  // sem ID Orçamento não há linha para defender.
  const gridReady = !isDefense || Boolean(idOrcamentoFilter);
  // Linhas individuais: projetos defendidos fora do ID Orçamento herdado. Elas
  // não aparecem no recorte normal do organograma, mas pertencem à defesa.
  const individualLineIds = useMemo(
    () => new Set(individualLines.map((item) => item.department_id)),
    [individualLines],
  );
  const defenseProjectIds = useMemo(() => {
    if (!defenseRequest) return new Set<number>();
    const result = new Set<number>();
    const pending = [defenseRequest.department_id];
    const childrenByParent = new Map<number | null, number[]>();
    departments.forEach((item) =>
      childrenByParent.set(item.parent_id, [
        ...(childrenByParent.get(item.parent_id) || []),
        item.id,
      ]),
    );
    while (pending.length) {
      const current = pending.pop() as number;
      if (result.has(current)) continue;
      result.add(current);
      pending.push(...(childrenByParent.get(current) || []));
    }
    return result;
  }, [defenseRequest, departments]);
  const scopedRows = useMemo(() => {
    const source = idOrcamentoFilter || cloudFilter ? displayRows : visibleRows;
    // Com uma solicitação selecionada a grade é o projeto defendido: o backend
    // grava somente esse subtree, então editar outras linhas da conta seria
    // descartado silenciosamente.
    if (defenseRequest) {
      return source.filter((row) => defenseProjectIds.has(row.department_id));
    }
    return source.filter((row) => {
      if (!idOrcamentoFilter && !cloudFilter) return true;
      if (individualLineIds.has(row.department_id)) return true;
      const byId = new Map(departments.map((item) => [item.id, item]));
      const chain: Department[] = [];
      let current = byId.get(row.department_id);
      while (current) {
        chain.unshift(current);
        current = byId.get(current.parent_id || -1);
      }
      const inheritedId =
        [...chain].reverse().find((item) => item.id_orcamento)?.id_orcamento ||
        "";
      const inheritedCloud = chain[2]?.name || "";
      return (
        (!idOrcamentoFilter || inheritedId === idOrcamentoFilter) &&
        (!cloudFilter || inheritedCloud === cloudFilter)
      );
    });
  }, [
    visibleRows,
    displayRows,
    departments,
    idOrcamentoFilter,
    cloudFilter,
    individualLineIds,
    defenseProjectIds,
    defenseRequest,
  ]);
  const openEdit = (row: DisplayRow) => {
    setEdit(row);
    setEditValues([...row.values]);
  };
  const applyEdit = () => {
    if (!edit) return;
    const values = editValues.map((value) => {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? numeric : 0;
    });
    setRows((previous) => {
      const updated: PlanningRow = {
        entity_type: "department",
        department_id: edit.department_id,
        department_levels: [],
        values,
      };
      const index = previous.findIndex(
        (row) => row.department_id === edit.department_id,
      );
      return index < 0
        ? [...previous, updated]
        : previous.map((row, i) => (i === index ? updated : row));
    });
    setDirtyDepartmentIds((previous) => new Set(previous).add(edit.department_id));
    setEdit(null);
  };
  const startPublish = (action: PublishAction, restore?: number) => {
    setRestoreId(restore || null);
    setPublication({
      title: "",
      description: "",
      event_type:
        action === "restore"
          ? "RESTAURACAO"
          : type === "defesa-orcamentaria"
            ? "DEFESA"
            : "CARGA_INICIAL",
      effective_date: new Date().toISOString().slice(0, 10),
    });
    setPublishAction(action);
  };
  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      setImportFile(file);
      startPublish("import");
    }
    event.target.value = "";
  };
  const publish = async () => {
    if (
      !publishAction ||
      !publication.title.trim() ||
      !publication.description.trim()
    ) {
      setError("Preencha título, descrição, tipo e data efetiva.");
      return;
    }
    if (type === "defesa-orcamentaria" && !idOrcamentoFilter && !defenseRequest) {
      setError("Selecione um ID Orçamento antes de salvar a defesa.");
      return;
    }
    const scopedPublication: PlanningPublication = {
      ...publication,
      scope_id_orcamento:
        defenseRequest?.requested_id_orcamento || idOrcamentoFilter || null,
      scope_cloud: defenseRequest?.requested_cloud ?? (cloudFilter || null),
    };
    const defenseOptions =
      type === "defesa-orcamentaria" && defenseRequest
        ? { defenseRequestId: defenseRequest.id }
        : undefined;
    setSaving(true);
    try {
      if (publishAction === "import" && importFile)
        await importPlanningExcel(
          type,
          year,
          importFile,
          scopedPublication,
          defenseRequest?.id,
          selectedMissing.length ? selectedMissing : undefined,
        );
      else if (publishAction === "save") {
        if (!dirtyDepartmentIds.size) {
          setError("Edite ao menos um projeto antes de publicar.");
          return;
        }
        await savePlanningGrid(
          type,
          year,
          scopedRows
            .filter((row) => dirtyDepartmentIds.has(row.department_id))
            .map((row) => ({
              entity_type: "department",
              department_id: row.department_id,
              department_levels: [],
              values: row.values,
            })),
          scopedPublication,
          defenseOptions,
        );
      } else if (publishAction === "restore" && restoreId)
        await restorePlanningVersion(type, year, restoreId, scopedPublication);
      setMessage(
        isDefense
          ? "Defesa salva. Use Publicar no Orçamento na solicitação para efetivar o merge."
          : "Movimentação publicada como novo snapshot.",
      );
      setPublishAction(null);
      setImportFile(null);
      if (isDefense) setDefenseRefreshKey((key) => key + 1);
      await load();
    } catch (e) {
      const missing = getPlanningMissingDepartments(e);
      if (missing.length) {
        setMissingDepartments(missing);
        setSelectedMissing(missing.map((item) => item.path));
      } else setError(String(e));
    } finally {
      setSaving(false);
    }
  };
  const publishBudget = async () => {
    if (
      !approveVersion ||
      !idOrcamentoFilter ||
      !approval.title.trim() ||
      !approval.description.trim()
    ) {
      setError("Selecione o ID Orçamento e preencha aprovação e motivo.");
      return;
    }
    setSaving(true);
    try {
      await publishDefenseToBudget(year, approveVersion.id, {
        id_orcamento: idOrcamentoFilter,
        cloud_scope: cloudFilter || null,
        ...approval,
      });
      setApproveVersion(null);
      setMessage(
        "Defesa carregada no Orçamento; alterações seguintes devem ser publicadas como revisão orçamentária.",
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };
  const columns: GridColDef[] = [
    {
      field: "actions",
      headerName: "Ações",
      width: 88,
      align: "center",
      headerAlign: "center",
      sortable: false,
      filterable: false,
      disableColumnMenu: true,
      renderCell: ({ row }: { row: DisplayRow }) => (
        <Tooltip title={editable ? "Editar valores" : "Versão somente leitura"}>
          <span>
            <IconButton aria-label={`Editar valores de ${row.name}`} disabled={!editable || loading || saving} onClick={() => openEdit(row)}>
              <Edit size={20} />
            </IconButton>
          </span>
        </Tooltip>
      ),
    },
    {
      field: "name",
      headerName: "Hierarquia organizacional",
      minWidth: 320,
      flex: 1.2,
      cellClassName: "planning-hierarchy",
      renderCell: ({ row }: { row: DisplayRow }) => (
        <Stack direction="row" alignItems="center" sx={{ pl: row.depth * 1.5, minWidth: 0, width: "100%" }}>
          <IconButton
            aria-label={`${expanded.has(row.department_id) ? "Recolher" : "Expandir"} ${row.name}`}
            aria-expanded={expanded.has(row.department_id)}
            sx={{ flexShrink: 0, width: 32, height: 32, visibility: departments.some(item => item.parent_id === row.department_id) ? "visible" : "hidden" }}
            onClick={() =>
              setExpanded((value) => {
                const next = new Set(value);
                next.has(row.department_id)
                  ? next.delete(row.department_id)
                  : next.add(row.department_id);
                saveExpandedDepartments(next);
                return next;
              })
            }
          >
            {expanded.has(row.department_id) ? <ExpandLess size={16} /> : <ExpandMore size={16} />}
          </IconButton>
          <Tooltip title={row.name}>
            <Typography variant="body2" noWrap fontWeight={row.depth < 2 ? 600 : 400}>
              {row.name}
            </Typography>
          </Tooltip>
          {individualLineIds.has(row.department_id) && (
            <Tooltip title="Projeto defendido em um ID Orçamento diferente do organograma. Ao publicar, ele passa a pertencer ao ID final.">
              <Chip
                size="small"
                color="warning"
                variant="outlined"
                label="Linha individual"
                sx={{ ml: 1 }}
              />
            </Tooltip>
          )}
          {defenseRequest?.department_id === row.department_id && (
            <Chip
              size="small"
              color="primary"
              variant="outlined"
              label={`Defesa · ${defenseRequest.requested_id_orcamento}`}
              sx={{ ml: 1 }}
            />
          )}
        </Stack>
      ),
    },
    ...MONTHS.map(
      (month, i): GridColDef => ({
        field: month,
        headerName: `${month} ${year}`,
        width: 136,
        type: "number",
        valueGetter: (_value, row: DisplayRow) => row.accumulated[i],
        valueFormatter: (value) => money(Number(value)),
      }),
    ),
  ];
  const currentVersion = versions.find((version) => version.is_current);
  const addYear = () => {
    const value = Number(newYear);
    if (!Number.isInteger(value) || value <= 0)
      return setError("Informe um ano positivo válido.");
    setYear(value);
    setAddYearOpen(false);
  };
  const exportBudget = () => {
    const headers = ["Nivel 1", "Nivel 2", "Nivel 3", "Nivel 4", "Nivel 5", "Nivel 6", "Nivel 7", ...MONTHS];
    const sheet = XLSX.utils.aoa_to_sheet([
      headers,
      ...rows.map((row) => [
        ...row.department_levels,
        ...Array(7 - row.department_levels.length).fill(""),
        ...row.values,
      ]),
    ]);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, sheet, "Orçamento");
    XLSX.writeFile(workbook, `orcamento-${type}-${year}.xlsx`);
  };
  return (
    <Box sx={{ minWidth: 0 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2} sx={{ mb: 3 }}>
        <Box>
          <Typography variant="h4" component="h1" sx={{ mb: 0.75 }}>{title}</Typography>
          <Typography variant="body2" color="text.secondary">{help}</Typography>
        </Box>
        <Chip variant="outlined" label={snapshotId ? "Versão histórica" : "Versão atual"} />
      </Stack>
      <Box>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>
            {dataDisplayText(error)}
          </Alert>
        )}
        {message && (
          <Alert
            severity="success"
            sx={{ mb: 2 }}
            onClose={() => setMessage("")}
          >
            {dataDisplayText(message)}
          </Alert>
        )}
        {isDefense && (
          <BudgetDefensePanel
            year={year}
            selectedRequestId={defenseRequest?.id ?? null}
            canRequest={canRequestDefense}
            canPublish={canPublishDefense}
            refreshKey={defenseRefreshKey}
            onSelectRequest={(request) => {
              setDefenseRequest(request);
              if (request) {
                setIdOrcamentoFilter(request.requested_id_orcamento);
                setCloudFilter(request.requested_cloud || "");
              } else {
                setIdOrcamentoFilter("");
                setCloudFilter("");
              }
            }}
            onChanged={() => {
              // O organograma muda ao incluir projeto ou publicar a linha
              // individual; recarregue-o junto com a grade.
              void loadDepartments().then(() => load());
            }}
          />
        )}
        <Paper variant="outlined" sx={{ overflow: "hidden" }}>
          <Box sx={{ px: { xs: 2, md: 3 }, py: 2, borderBottom: 1, borderColor: "divider" }}>
            <Typography variant="h6">Distribuição mensal</Typography>
            <Typography variant="body2" color="text.secondary">Valores em reais por departamento. Expanda a hierarquia para consultar e editar cada nível.</Typography>
          </Box>
          <Stack direction={{ xs: "column", sm: "row" }} gap={2} flexWrap="wrap" sx={{ p: { xs: 2, md: 3 }, pb: 2 }}>
            <Stack direction="row" gap={0.5} alignItems="center">
              <FormControl sx={{ minWidth: 112 }}>
                <InputLabel id="planning-year-label">Ano</InputLabel>
                <Select labelId="planning-year-label" label="Ano" value={String(year)} onChange={(e) => setYear(Number(e.target.value))}>
                  {years.map(value => <MenuItem key={value} value={String(value)}>{value}</MenuItem>)}
                </Select>
              </FormControl>
              <Tooltip title="Adicionar ano">
                <IconButton aria-label="Adicionar ano" onClick={() => { setNewYear(String(year + 1)); setAddYearOpen(true); }}><Add size={20} /></IconButton>
              </Tooltip>
            </Stack>
            <FormControl sx={{ minWidth: 240, flex: { xs: 1, md: "0 1 320px" } }}>
              <InputLabel shrink id="planning-version-label">Versão do planejamento</InputLabel>
              <Select labelId="planning-version-label" label="Versão do planejamento" notched value={snapshotId} onChange={e => setSnapshotId(String(e.target.value))} displayEmpty>
                <MenuItem value="">Versão atual{currentVersion ? ` · v${currentVersion.version}` : ""}</MenuItem>
                {versions.filter(version => !version.is_current).map(version => <MenuItem key={version.id} value={String(version.id)}>v{version.version} · {version.title || version.source}</MenuItem>)}
              </Select>
            </FormControl>
          </Stack>
          {(type === "orcamento" || type === "defesa-orcamentaria") && (
            <Stack direction="row" gap={1.5} sx={{ px: { xs: 2, md: 3 }, pb: 2 }} flexWrap="wrap">
              <FormControl sx={{ minWidth: 180, flex: { xs: 1, md: "0 1 220px" } }} disabled={Boolean(defenseRequest)}>
                <InputLabel shrink id="planning-cloud-label">Provedor</InputLabel>
                <Select
                  labelId="planning-cloud-label"
                  label="Provedor"
                  notched
                  displayEmpty
                  value={cloudFilter}
                  onChange={(event) => {
                    setCloudFilter(String(event.target.value));
                    setIdOrcamentoFilter("");
                  }}
                >
                  <MenuItem value="">Todos os provedores</MenuItem>
                  {Array.from(
                    new Set(scopes.map((scope) => scope.cloud).filter(Boolean)),
                  ).map((value) => (
                    <MenuItem key={value} value={String(value)}>
                      {value}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormControl sx={{ minWidth: 220, flex: { xs: 1, md: "0 1 320px" } }} disabled={!cloudFilter || Boolean(defenseRequest)}>
                <InputLabel shrink id="planning-budget-label">ID do orçamento</InputLabel>
                <Select
                  labelId="planning-budget-label"
                  label="ID do orçamento"
                  notched
                  displayEmpty
                  value={idOrcamentoFilter}
                  onChange={(event) =>
                    setIdOrcamentoFilter(String(event.target.value))
                  }
                >
                  <MenuItem value="">Todos do provedor</MenuItem>
                  {Array.from(
                    new Set(
                      scopes
                        .filter((scope) => scope.cloud === cloudFilter)
                        .map((scope) => scope.id_orcamento),
                    ),
                  ).map((value) => (
                    <MenuItem key={value} value={value}>
                      {value}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>
          )}
          {gridReady ? (
            <>
              <Stack direction={{ xs: "column", lg: "row" }} justifyContent="space-between" gap={2} sx={{ p: 2, bgcolor: "background.default", borderTop: 1, borderBottom: 1, borderColor: "divider" }}>
                <TextField
                  label="Buscar departamento"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  InputProps={{ startAdornment: <InputAdornment position="start"><Search size={20} /></InputAdornment> }}
                  sx={{ width: { xs: "100%", lg: 300 }, bgcolor: "background.paper" }}
                />
                <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
                  <Button variant="outlined" startIcon={<UploadFile size={20} />} disabled={!editable || !gridReady || loading || saving} onClick={() => fileInput.current?.click()}>Importar</Button>
                  <Button variant="outlined" startIcon={<CloudDownload size={20} />} endIcon={<ExpandMore size={16} />} aria-controls={exportAnchor ? "planning-export-menu" : undefined} aria-haspopup="menu" aria-expanded={Boolean(exportAnchor)} onClick={e => setExportAnchor(e.currentTarget)}>Exportar</Button>
                  <Button variant="contained" startIcon={<Save size={20} />} disabled={!editable || !gridReady || loading || saving} onClick={() => startPublish("save")}>{isDefense ? "Salvar alterações" : "Publicar versão"}</Button>
                </Stack>
              </Stack>
              <Menu id="planning-export-menu" anchorEl={exportAnchor} open={Boolean(exportAnchor)} onClose={() => setExportAnchor(null)}>
                <MenuItem disabled={!gridReady || loading} onClick={() => { setExportAnchor(null); setExportYear(year); setExportOpen(true); }}>Exportar valores do orçamento</MenuItem>
                <MenuItem onClick={async () => {
                  setExportAnchor(null);
                  try {
                    const blob = await downloadPlanningTemplate(year);
                    const url = URL.createObjectURL(blob), link = document.createElement("a");
                    link.href = url; link.download = `modelo-${type}-${year}.xlsx`; link.click(); URL.revokeObjectURL(url);
                  } catch (error) { setError(String(error)); }
                }}>Baixar modelo Excel</MenuItem>
                {budget && <MenuItem onClick={async () => {
                  setExportAnchor(null);
                  try {
                    const blob = await downloadSimulatedBudget(year);
                    const url = URL.createObjectURL(blob), link = document.createElement("a");
                    link.href = url; link.download = `forecast-simulado-${year}.xlsx`; link.click(); URL.revokeObjectURL(url);
                  } catch (error) { setError(String(error)); }
                }}>Baixar projeção simulada</MenuItem>}
              </Menu>
              <input ref={fileInput} hidden type="file" accept=".xlsx" aria-label="Importar planilha de planejamento" onChange={chooseFile} />
              {snapshotId && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  Versão histórica em modo somente leitura.
                </Alert>
              )}
              {!departments.length ? (
                <EmptyState message="Nenhum departamento cadastrado." />
              ) : (
                <Box
                  sx={{
                    height: Math.min(520, Math.max(280, (scopedRows.length + 2) * 48)),
                    width: "100%",
                    "& .planning-own .planning-hierarchy": { borderLeft: "3px solid #24a148", bgcolor: "#defbe6" },
                    "& .planning-mixed .planning-hierarchy": { borderLeft: "3px solid #f1c21b", bgcolor: "#fff1c7" },
                  }}
                >
                  <DataGrid
                    rows={scopedRows}
                    columns={columns}
                    loading={loading}
                    aria-label={`${title} por departamento e mês`}
                    pageSizeOptions={[25, 50, 100]}
                    initialState={{ pagination: { paginationModel: { page: 0, pageSize: 25 } } }}
                    sx={{ border: 0, borderRadius: 0, "& .MuiDataGrid-cell:focus-within, & .MuiDataGrid-columnHeader:focus-within": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: -2 } }}
                    getRowClassName={({ row }) => {
                      const item = row as DisplayRow;
                      return !item.hasOwn
                        ? "planning-empty"
                        : item.hasChild
                          ? "planning-mixed"
                          : "planning-own";
                    }}
                    disableRowSelectionOnClick
                  />
                </Box>
              )}
            </>
          ) : (
            <Alert severity="info" sx={{ mb: 2 }}>
              Selecione um <b>ID Orçamento</b> para listar os departamentos e editar a
              defesa. Você também pode escolher uma solicitação na lista acima.
            </Alert>
          )}
          <Stack direction="row" gap={2} flexWrap="wrap" sx={{ px: 2, py: 1.5, borderTop: 1, borderColor: "divider" }}>
            <Typography variant="caption" color="text.secondary">Composição dos valores</Typography>
            {[{ color: "#e0e0e0", label: "Somente filhos" }, { color: "#24a148", label: "Valor próprio" }, { color: "#f1c21b", label: "Próprio + filhos" }].map(item => (
              <Stack key={item.label} direction="row" alignItems="center" gap={0.75}>
                <Box sx={{ width: 8, height: 8, bgcolor: item.color }} />
                <Typography variant="caption" color="text.secondary">{item.label}</Typography>
              </Stack>
            ))}
          </Stack>
        </Paper>
        <Paper variant="outlined" sx={{ mt: 2, p: 2 }}>
          <Typography variant="h6">Histórico de versões</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Publicações e revisões do planejamento de {year}.</Typography>
          {timeline.length === 0 ? (
            <Typography color="text.secondary" sx={{ mt: 1 }}>
              Ainda não há movimentações para {year}.
            </Typography>
          ) : (
            timeline.map((version) => (
              <Stack
                key={version.id}
                direction="row"
                justifyContent="space-between"
                gap={2}
                sx={{ py: 1.25, borderBottom: 1, borderColor: "divider" }}
              >
                <Box>
                  <Typography fontWeight={700}>
                    {version.title || `Versão legada v${version.version}`}
                  </Typography>
                  <Typography variant="body2">
                    {version.event_type || "OUTRO"} ·{" "}
                    {String(version.effective_date || version.created_at).slice(
                      0,
                      10,
                    )}{" "}
                    · v{version.version}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {version.description ||
                      "Snapshot sem narrativa cadastrada."}
                  </Typography>
                </Box>
                <Stack alignItems="end">
                  <Typography fontWeight={700}>
                    {money(version.annual_total || 0)}
                  </Typography>
                  {version.delta != null && (
                    <Typography variant="caption">
                      Δ {money(version.delta)}{" "}
                      {version.delta_pct != null
                        ? `(${version.delta_pct.toFixed(1)}%)`
                        : ""}
                    </Typography>
                  )}
                  <Button
                    size="small"
                    startIcon={<Restore />}
                    onClick={() => startPublish("restore", version.id)}
                  >
                    Restaurar
                  </Button>
                  {type === "defesa-orcamentaria" &&
                    !version.published_to_budget_at &&
                    !version.defense_request_id && (
                      <Button
                        size="small"
                        variant="contained"
                        onClick={() => {
                          if (version.scope_id_orcamento)
                            setIdOrcamentoFilter(version.scope_id_orcamento);
                          setCloudFilter(version.scope_cloud || "");
                          setApproveVersion(version);
                          setApproval({
                            title: `Orçamento aprovado ${year}`,
                            description: "",
                            effective_date: new Date()
                              .toISOString()
                              .slice(0, 10),
                          });
                        }}
                      >
                        Carregar no Orçamento
                      </Button>
                    )}
                  {type === "defesa-orcamentaria" && version.defense_request_id && (
                    <Typography variant="caption" color="primary.main">
                      Solicitação #{version.defense_request_id} — publique na lista de solicitações
                    </Typography>
                  )}
                  {version.published_to_budget_at && (
                    <Typography variant="caption" color="success.main">
                      Carregada no Orçamento
                    </Typography>
                  )}
                </Stack>
              </Stack>
            ))
          )}
        </Paper>
      </Box>
      <Dialog
        open={Boolean(edit)}
        onClose={() => setEdit(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="body2" color="text.secondary">Editar valores mensais</Typography>
          <Typography variant="h6" fontWeight={800}>{edit?.name}</Typography>
          <Typography variant="body2" color="text.secondary">Altere somente os valores próprios desta linha. Os totais dos níveis superiores são recalculados automaticamente.</Typography>
        </DialogTitle>
        <DialogContent dividers sx={{ py: 2.5 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", p: 1.5, mb: 2.5, borderRadius: 1, bgcolor: "background.default", border: "1px solid", borderColor: "divider" }}>
            <Box><Typography variant="caption" color="text.secondary">Centro de custo</Typography><Typography variant="body2" fontWeight={700}>{edit?.centro_custo || "Não informado"}</Typography></Box>
            <Box textAlign="right"><Typography variant="caption" color="text.secondary">Total anual editado</Typography><Typography variant="h6" color="primary.main" fontWeight={800}>{money(editValues.reduce<number>((total, value) => total + Number(value || 0), 0))}</Typography></Box>
          </Box>
          <Typography variant="subtitle2" fontWeight={750} sx={{ mb: 1.25 }}>Distribuição mensal · {year}</Typography>
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0, 1fr))", sm: "repeat(3, minmax(0, 1fr))", md: "repeat(4, minmax(0, 1fr))" }, gap: 1.25 }}>
            {MONTHS.map((month, i) => (
              <TextField
                key={month}
                label={`${month} ${year}`}
                type="number"
                value={editValues[i] ?? 0}
                onChange={(e) =>
                  setEditValues((values) =>
                    values.map((value, index) =>
                      index === i
                        ? (e.target.value === "" || e.target.value === "-"
                          ? e.target.value
                          : Number(e.target.value))
                        : value,
                    ),
                  )
                }
                size="small"
                sx={{ "& input": { fontWeight: 600 } }}
              />
            ))}
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button onClick={() => setEdit(null)}>Cancelar</Button>
          <Button variant="contained" onClick={applyEdit}>
            Aplicar
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog open={exportOpen} onClose={() => setExportOpen(false)}>
        <DialogTitle>Exportar orçamento</DialogTitle>
        <DialogContent>
          <FormControl fullWidth sx={{ mt: 1 }}>
            <InputLabel>Ano</InputLabel>
            <Select
              label="Ano"
              value={String(exportYear)}
              onChange={(event) => setExportYear(Number(event.target.value))}
            >
              {years.map((value) => (
                <MenuItem key={value} value={String(value)}>{value}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setExportOpen(false)}>Cancelar</Button>
          <Button
            variant="contained"
            onClick={() => {
              setExportOpen(false);
              if (exportYear === year) {
                exportBudget();
              } else {
                setYear(exportYear);
                setMessage(`Ano ${exportYear} carregado. Abra Exportar orçamento novamente para baixar os dados.`);
              }
            }}
          >
            Exportar
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog open={addYearOpen} onClose={() => setAddYearOpen(false)}>
        <DialogTitle>Adicionar ano</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            label="Ano"
            type="number"
            value={newYear}
            onChange={(e) => setNewYear(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddYearOpen(false)}>Cancelar</Button>
          <Button variant="contained" onClick={addYear}>
            Adicionar
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={Boolean(publishAction)}
        onClose={() => !saving && setPublishAction(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{isDefense ? "Salvar defesa" : "Publicar movimentação"}</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <TextField
              label="Título"
              placeholder="Ex.: Defesa PRC1"
              value={publication.title}
              onChange={(e) =>
                setPublication((value) => ({ ...value, title: e.target.value }))
              }
              required
            />
            <FormControl>
              <InputLabel>Tipo do evento</InputLabel>
              <Select
                label="Tipo do evento"
                value={publication.event_type}
                onChange={(e) =>
                  setPublication((value) => ({
                    ...value,
                    event_type: e.target.value as PlanningEventType,
                  }))
                }
              >
                {EVENTS.map((event) => (
                  <MenuItem key={event} value={event}>
                    {event.replace(/_/g, " ")}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              label="Data efetiva"
              type="date"
              InputLabelProps={{ shrink: true }}
              value={publication.effective_date}
              onChange={(e) =>
                setPublication((value) => ({
                  ...value,
                  effective_date: e.target.value,
                }))
              }
            />
            <TextField
              label="Descrição e motivo"
              multiline
              minRows={3}
              value={publication.description}
              onChange={(e) =>
                setPublication((value) => ({
                  ...value,
                  description: e.target.value,
                }))
              }
              required
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPublishAction(null)}>Cancelar</Button>
      <Dialog open={missingDepartments.length > 0} maxWidth="md" fullWidth>
        <DialogTitle>Departamentos ausentes no organograma</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Selecione os caminhos que devem ser criados antes da carga. Os caminhos desmarcados não serão importados.
          </Typography>
          <Stack gap={1}>
            {missingDepartments.map((item) => {
              const key = item.path.join(" > ");
              const checked = selectedMissing.some((path) => path.join(" > ") === key);
              return (
                <Box key={key} sx={{ display: "flex", gap: 1, alignItems: "flex-start", p: 1, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => setSelectedMissing((current) => checked ? current.filter((path) => path.join(" > ") !== key) : [...current, item.path])}
                  />
                  <Box>
                    <Typography variant="body2" fontWeight={700}>{item.path.join(" > ")}</Typography>
                    <Typography variant="caption" color="text.secondary">Linhas: {item.lines.join(", ")}</Typography>
                  </Box>
                </Box>
              );
            })}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => { setMissingDepartments([]); setSelectedMissing([]); }}>Cancelar</Button>
          <Button variant="contained" disabled={saving} onClick={() => { setMissingDepartments([]); void publish(); }}>
            Criar selecionados e continuar
          </Button>
        </DialogActions>
      </Dialog>
          <Button
            variant="contained"
            disabled={saving}
            onClick={() => void publish()}
          >
            {isDefense ? "Salvar" : "Publicar"}
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={Boolean(approveVersion)}
        onClose={() => !saving && setApproveVersion(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Carregar defesa no Orçamento</DialogTitle>
        <DialogContent>
          <Stack gap={2} sx={{ pt: 1 }}>
            <Alert severity="warning">
              Será criado um snapshot de Orçamento para {year}, substituindo
              somente o escopo {idOrcamentoFilter || "a selecionar"}
              {cloudFilter ? ` / ${cloudFilter}` : ""}. Esta defesa ficará
              encerrada.
            </Alert>
            <TextField
              label="Aprovação"
              value={approval.title}
              onChange={(event) =>
                setApproval((value) => ({
                  ...value,
                  title: event.target.value,
                }))
              }
              required
            />
            <TextField
              label="Data da aprovação"
              type="date"
              InputLabelProps={{ shrink: true }}
              value={approval.effective_date}
              onChange={(event) =>
                setApproval((value) => ({
                  ...value,
                  effective_date: event.target.value,
                }))
              }
              required
            />
            <TextField
              label="Motivo e observações da aprovação"
              multiline
              minRows={4}
              value={approval.description}
              onChange={(event) =>
                setApproval((value) => ({
                  ...value,
                  description: event.target.value,
                }))
              }
              required
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setApproveVersion(null)}>Cancelar</Button>
          <Button
            variant="contained"
            disabled={saving || !idOrcamentoFilter}
            onClick={() => void publishBudget()}
          >
            Carregar no Orçamento
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
function flatten(nodes: Department[]): Department[] {
  return nodes.flatMap((node) => [node, ...flatten(node.children || [])]);
}
