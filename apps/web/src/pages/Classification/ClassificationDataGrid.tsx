import DataGrid from "../../components/FinbaseDataGrid";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Chip,
  Divider,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { Delete, Edit, ContentCopy, FilterAlt } from "../../components/FinbaseIcons";
import { GridColDef, GridFilterModel, GridPaginationModel, GridSortModel, useGridApiRef } from "@mui/x-data-grid";
import { ClassificationRule, Department } from "../../services/api";
import { flattenDepartments } from "../../components/DepartmentTreePicker";
import { RowAction } from "../../components/RowAction";

export type ClassificationGridFilter = {
  field: string;
  operator: "equals" | "notEquals" | "contains" | "notContains" | "startsWith" | "endsWith";
  value: string;
};

type Props = {
  rows: ClassificationRule[];
  total: number;
  loading: boolean;
  search: string;
  onQuery: (query: {
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: ClassificationGridFilter[];
  }) => void;
  onEdit: (rule: ClassificationRule) => void;
  onClone: (rule: ClassificationRule) => void;
  onDelete: (rule: ClassificationRule) => void;
  departments: Department[];
  selectedRuleIds: number[];
  onSelectionChange: (ids: number[]) => void;
};

const cloudLabel = (scope: string | null | undefined): string => {
  if (!scope) return "Global";
  return scope.toUpperCase();
};

function formatConditions(rule: ClassificationRule): string {
  const raw = rule.conditions;
  if (!raw || !Array.isArray(raw) || raw.length === 0) {
    return rule.pattern || "—";
  }
  const parts = raw.map((c) => {
    const cond = c as Record<string, unknown>;
    const field = String(cond.field ?? "").trim();
    const operator = String(cond.operator ?? "=");
    const values = Array.isArray(cond.values)
      ? (cond.values as unknown[]).map(String).join(" | ")
      : String(cond.values ?? "");
    return `${field} ${operator} ${values}`.trim();
  });
  return parts.filter(Boolean).join(" ; ");
}

export default function ClassificationDataGrid({
  rows,
  total,
  loading,
  search,
  onQuery,
  onEdit,
  onClone,
  onDelete,
  departments,
  selectedRuleIds,
  onSelectionChange,
}: Props) {
  const apiRef = useGridApiRef();
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 50 });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, ClassificationGridFilter>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);
  const departmentPathById = useMemo(() => new Map(flattenDepartments(departments).map(item => [item.id, item.path])), [departments]);

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>{label}</Typography>
      {activeFilters[field] && (
        <Tooltip title="Esta coluna tem um filtro ativo">
          <FilterAlt fontSize="small" color="primary" />
        </Tooltip>
      )}
    </Stack>
  );

  const columns = useMemo<GridColDef[]>(() => [
    {
      field: "name",
      headerName: "Nome",
      renderHeader: () => filteredHeader("Nome", "name"),
      minWidth: 220,
      flex: 1.2,
      renderCell: ({ row }: { row: ClassificationRule }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          <Typography variant="body2" fontWeight={700} noWrap>{row.name}</Typography>
        </Stack>
      ),
    },
    {
      field: "conditions",
      headerName: "Critérios",
      renderHeader: () => filteredHeader("Critérios", "conditions"),
      minWidth: 320,
      flex: 2,
      sortable: false,
      filterable: false,
      renderCell: ({ row }: { row: ClassificationRule }) => {
        const text = formatConditions(row);
        return (
          <Tooltip title={text} placement="top">
            <Typography
              variant="body2"
              noWrap
              sx={{
                fontFamily: 'Consolas, "Fira Code", Monaco, "Courier New", Courier, monospace',
                fontSize: "0.78rem",
                color: "text.primary",
              }}
            >
              {text}
            </Typography>
          </Tooltip>
        );
      },
    },
    {
      field: "department_name",
      headerName: "Hierarquia",
      renderHeader: () => filteredHeader("Hierarquia", "department_name"),
      minWidth: 280,
      flex: 1.4,
      valueGetter: (_: unknown, row: ClassificationRule) => row.department_name || "—",
      renderCell: ({ row }: { row: ClassificationRule }) => (
        <Tooltip title={row.department_id ? departmentPathById.get(row.department_id) || row.department_name || "—" : "—"}>
          <Typography variant="body2" noWrap color="text.secondary">{row.department_id ? departmentPathById.get(row.department_id) || row.department_name || "—" : "—"}</Typography>
        </Tooltip>
      ),
    },
    {
      field: "cloud_scope",
      headerName: "Cloud",
      renderHeader: () => filteredHeader("Cloud", "cloud_scope"),
      minWidth: 110,
      valueGetter: (_: unknown, row: ClassificationRule) => cloudLabel(row.cloud_scope),
      renderCell: ({ row }: { row: ClassificationRule }) => (
        <Chip size="small" label={cloudLabel(row.cloud_scope)} variant={row.cloud_scope ? "filled" : "outlined"} sx={{ fontSize: "0.7rem", height: 22 }} />
      ),
    },
    {
      field: "priority",
      headerName: "Prioridade",
      renderHeader: () => filteredHeader("Prioridade", "priority"),
      minWidth: 100,
    },
    {
      field: "active",
      headerName: "Ativa",
      renderHeader: () => filteredHeader("Ativa", "active"),
      minWidth: 90,
      renderCell: ({ row }: { row: ClassificationRule }) => (
        <Chip size="small" label={row.active ? "Sim" : "Não"} color={row.active ? "success" : "default"} />
      ),
    },
    {
      field: "actions",
      headerName: "Opções",
      width: 132,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: ClassificationRule }) => (
        <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
          <Tooltip title="Editar regra">
            <RowAction kind="edit" label={"Editar " + row.name} onClick={(event) => { event.stopPropagation(); onEdit(row); }} />
          </Tooltip>
          <Tooltip title="Duplicar regra">
            <RowAction kind="clone" label={"Clonar " + row.name} onClick={(event) => { event.stopPropagation(); onClone(row); }} />
          </Tooltip>
          <Tooltip title="Excluir regra">
            <RowAction kind="delete" label={"Excluir " + row.name} onClick={(event) => { event.stopPropagation(); onDelete(row); }} />
          </Tooltip>
        </Stack>
      ),
    },
  ], [activeFilters, departmentPathById]);

  const currentSort = (): { field: string; direction: "asc" | "desc" } | undefined => {
    const item = sortModel[0];
    return item?.sort ? { field: item.field, direction: item.sort } : undefined;
  };

  const query = (
    next: Partial<{ offset: number; limit: number; sort: { field: string; direction: "asc" | "desc" }; filters: ClassificationGridFilter[] }> = {},
  ) => {
    const sort = next.sort ?? currentSort();
    onQuery({
      offset: next.offset ?? paginationModel.page * paginationModel.pageSize,
      limit: next.limit ?? paginationModel.pageSize,
      ...(sort ? { sort } : {}),
      filters: next.filters ?? Object.values(activeFilters),
    });
  };

  useEffect(() => {
    setPaginationModel((current) => ({ ...current, page: 0 }));
    query({ offset: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const onPagination = (model: GridPaginationModel) => {
    setPaginationModel(model);
    query({ offset: model.page * model.pageSize, limit: model.pageSize });
  };

  const onSort = (model: GridSortModel) => {
    setSortModel(model);
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const item = model[0];
    if (item?.sort) query({ offset: 0, sort: { field: item.field, direction: item.sort } });
    else query({ offset: 0 });
  };

  const onFilter = (model: GridFilterModel) => {
    const previousModel = filterModel;
    setFilterModel(model);
    if (model.items.length === 0) {
      setActiveFilters({});
      setPaginationModel((current) => ({ ...current, page: 0 }));
      query({ offset: 0, filters: [] });
      return;
    }
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const operatorMap: Record<string, ClassificationGridFilter["operator"]> = {
      equals: "equals",
      is: "equals",
      "=": "equals",
      not: "notEquals",
      "!=": "notEquals",
      contains: "contains",
      doesNotContain: "notContains",
      startsWith: "startsWith",
      endsWith: "endsWith",
    };
    const nextFilters = { ...activeFilters };
    const currentFields = new Set(model.items.map(item => item.field).filter(Boolean));
    for (const previous of previousModel.items) {
      if (previous.field && !currentFields.has(previous.field)) delete nextFilters[previous.field];
    }
    for (const item of model.items) {
      if (!item.field) continue;
      if (item.value === undefined || item.value === "") {
        if (previousModel.items.some(previous => previous.field === item.field)) delete nextFilters[item.field];
        continue;
      }
      const value = item.value instanceof Date ? item.value.toISOString() : String(item.value);
      const filter: ClassificationGridFilter = { field: item.field, operator: operatorMap[item.operator || "contains"] || "contains", value };
      nextFilters[filter.field] = filter;
    }
    setActiveFilters(nextFilters);
    query({ offset: 0, filters: Object.values(nextFilters) });
  };

  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const field = cell?.getAttribute("data-field") || "";
    const rowId = cell?.parentElement?.getAttribute("data-id");
    const row = rows.find((item) => String(item.id) === rowId);
    const allowedFields = new Set(["name", "department_name", "cloud_scope", "priority", "active"]);
    let value = "";
    if (field === "name") value = row?.name || "";
    else if (field === "department_name") value = row?.department_id ? departmentPathById.get(row.department_id) || row.department_name || "" : row?.department_name || "";
    else if (field === "cloud_scope") value = row?.cloud_scope || "";
    else if (field === "priority") value = String(row?.priority ?? "");
    else if (field === "active") value = String(row?.active ?? "");
    else value = cell?.textContent?.trim() || "";

    if (allowedFields.has(field) && value) {
      setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field, value });
    }
  };

  const applyContextFilter = (operator: ClassificationGridFilter["operator"]) => {
    if (!context) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filter: ClassificationGridFilter = { field: context.field, operator, value: context.value };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
  };

  const clearFilter = (field?: string) => {
    if (field && !activeFilters[field]) {
      setContext(null);
      setHeaderContext(null);
      return;
    }
    const filters = { ...activeFilters };
    if (field) delete filters[field];
    else Object.keys(filters).forEach((key) => delete filters[key]);
    setActiveFilters(filters);
    setFilterModel({
      items: Object.values(filters).map((filter) => ({
        id: filter.field,
        field: filter.field,
        operator: "equals",
        value: filter.value,
      })),
    });
    setPaginationModel((current) => ({ ...current, page: 0 }));
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
    setHeaderContext(null);
  };

  const exportDisplayed = () => {
    const escape = (value: unknown) => '"' + String(value ?? "").replace(/"/g, '""') + '"';
    const header = ["Nome", "Critérios", "Hierarquia", "Cloud", "Prioridade", "Ativa"];
    const lines = [
      header.join(";"),
      ...rows.map((row) => [
        row.name,
        formatConditions(row),
        row.department_id ? departmentPathById.get(row.department_id) || row.department_name || "" : "",
        cloudLabel(row.cloud_scope),
        row.priority,
        row.active ? "Sim" : "Não",
      ].map(escape).join(";")),
    ];
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", "classificacoes-" + new Date().toISOString().slice(0, 10) + ".csv");
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <Box sx={{ width: "100%" }} onContextMenu={onCellContextMenu}>
        <DataGrid
          apiRef={apiRef}
          rows={rows}
          columns={columns}
          loading={loading}
          rowCount={total}
          getRowId={(row) => row.id}
          disableRowSelectionOnClick
          disableMultipleRowSelection={false}
          checkboxSelection
          rowSelectionModel={selectedRuleIds}
          onRowSelectionModelChange={(model) => onSelectionChange(model.map(Number))}
          autoHeight
          pagination
          paginationMode="server"
          sortingMode="server"
          filterMode="server"
          pageSizeOptions={[50, 100, 500, 1000]}
          paginationModel={paginationModel}
          sortModel={sortModel}
          filterModel={filterModel}
          onPaginationModelChange={onPagination}
          onSortModelChange={onSort}
          onFilterModelChange={onFilter}
          onColumnHeaderContextMenu={(params, event) => {
            event.preventDefault();
            setHeaderContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field: params.field });
          }}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 2,
            bgcolor: "background.paper",
            "& .MuiDataGrid-columnHeaders": { bgcolor: "background.default", borderBottomColor: "divider" },
          }}
        />
      </Box>

      <Menu
        open={context !== null}
        onClose={() => setContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={context ? { top: context.mouseY, left: context.mouseX } : undefined}
      >
        {context?.field === "department_name" ? (
          <MenuItem onClick={() => applyContextFilter("contains")}>Filtrar por esta estrutura</MenuItem>
        ) : <MenuItem onClick={() => applyContextFilter("equals")}>Mostrar valores iguais</MenuItem>}
        <MenuItem onClick={() => applyContextFilter("notEquals")}>Mostrar valores diferentes</MenuItem>
        <MenuItem disabled={!context || !activeFilters[context.field]} onClick={() => clearFilter(context?.field)}>Limpar filtro desta coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Remover todos os filtros</MenuItem>
      </Menu>

      <Menu
        open={headerContext !== null}
        onClose={() => setHeaderContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={headerContext ? { top: headerContext.mouseY, left: headerContext.mouseX } : undefined}
      >
        <MenuItem onClick={() => {
          const field = headerContext?.field;
          setHeaderContext(null);
          if (field) apiRef.current.showFilterPanel(field);
        }}>Filtrar por esta coluna</MenuItem>
        <MenuItem onClick={() => {
          const field = headerContext?.field;
          setHeaderContext(null);
          if (field) apiRef.current.showColumnMenu(field);
        }}>Opções desta coluna</MenuItem>
        <MenuItem onClick={() => { exportDisplayed(); setHeaderContext(null); }}>Exportar linhas exibidas</MenuItem>
        <Divider />
        <MenuItem disabled={!headerContext || !activeFilters[headerContext.field]} onClick={() => clearFilter(headerContext?.field)}>Limpar filtro desta coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Remover todos os filtros</MenuItem>
      </Menu>
    </>
  );
}
