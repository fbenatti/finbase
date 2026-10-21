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
import {
  Delete,
  Edit,
  FilterAlt,
} from "@mui/icons-material";
import {
  DataGrid,
  GridColDef,
  GridFilterModel,
  GridPaginationModel,
  GridSortModel,
  useGridApiRef,
} from "@mui/x-data-grid";
import { ForecastBusinessRule } from "../../services/api";
import { BEHAVIOR_LABELS } from "./forecastConstants";

export type ForecastGridFilter = {
  field: string;
  operator: "equals" | "notEquals";
  value: string;
};

type Props = {
  rows: ForecastBusinessRule[];
  total: number;
  loading: boolean;
  search: string;
  onQuery: (query: {
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: ForecastGridFilter[];
  }) => void;
  onEdit: (rule: ForecastBusinessRule) => void;
  onDelete: (rule: ForecastBusinessRule) => void;
};

function formatPeriod(value: string | null | undefined): string {
  if (!value) return "—";
  const cleaned = String(value).replace("-", "");
  if (cleaned.length !== 6) return value;
  return cleaned.slice(0, 4) + "-" + cleaned.slice(4);
}

function formatIdentification(row: ForecastBusinessRule): string {
  if (row.identification_clauses?.length) {
    return row.identification_clauses
      .map((clause) => clause.column + "=" + clause.values.join("|"))
      .join(" OR ");
  }
  if (row.identification_column) {
    return row.identification_column + "=" + row.identification_values.join(", ");
  }
  const scope = Object.entries(row.scope || {})
    .map(([k, v]) => k + "=" + v)
    .join(", ");
  return scope ? "escopo " + scope : "—";
}

export default function ForecastRulesDataGrid({
  rows,
  total,
  loading,
  search,
  onQuery,
  onEdit,
  onDelete,
}: Props) {
  const apiRef = useGridApiRef();
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 10 });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, ForecastGridFilter>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>{label}</Typography>
      {activeFilters[field] && (
        <Tooltip title="Esta coluna possui um filtro ativo">
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
      minWidth: 210,
      flex: 1.2,
      renderCell: ({ row }: { row: ForecastBusinessRule }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          <Typography variant="body2" fontWeight={700} noWrap>{row.name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>v{row.version} · prioridade {row.priority}</Typography>
        </Stack>
      ),
    },
    {
      field: "identification",
      headerName: "Identificação",
      minWidth: 220,
      flex: 1.4,
      sortable: false,
      filterable: false,
      valueGetter: (_, row: ForecastBusinessRule) => formatIdentification(row),
      renderCell: ({ row }: { row: ForecastBusinessRule }) => (
        <Tooltip title={formatIdentification(row)}>
          <Typography variant="body2" noWrap color="text.secondary">{formatIdentification(row)}</Typography>
        </Tooltip>
      ),
    },
    {
      field: "charge_type",
      headerName: "Tipo",
      renderHeader: () => filteredHeader("Tipo", "charge_type"),
      minWidth: 105,
    },
    {
      field: "behavior",
      headerName: "Comportamento",
      renderHeader: () => filteredHeader("Comportamento", "behavior"),
      minWidth: 200,
      flex: 1,
      valueGetter: (_, row: ForecastBusinessRule) => BEHAVIOR_LABELS[row.behavior] || row.behavior,
      renderCell: ({ row }: { row: ForecastBusinessRule }) => (
        <Tooltip title={BEHAVIOR_LABELS[row.behavior] || row.behavior}>
          <Typography variant="body2" noWrap>{BEHAVIOR_LABELS[row.behavior] || row.behavior}</Typography>
        </Tooltip>
      ),
    },
    {
      field: "start_period",
      headerName: "Início",
      renderHeader: () => filteredHeader("Início", "start_period"),
      minWidth: 100,
      valueFormatter: (value) => formatPeriod(value),
    },
    {
      field: "end_period",
      headerName: "Fim",
      renderHeader: () => filteredHeader("Fim", "end_period"),
      minWidth: 100,
      valueFormatter: (value) => formatPeriod(value),
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
      renderCell: ({ row }: { row: ForecastBusinessRule }) => (
        <Chip size="small" label={row.active ? "Sim" : "Não"} color={row.active ? "success" : "default"} />
      ),
    },
    {
      field: "actions",
      headerName: "Ações",
      width: 110,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: ForecastBusinessRule }) => (
        <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
          <Tooltip title="Editar regra">
            <IconButton size="small" aria-label={"Editar " + row.name} onClick={(event) => { event.stopPropagation(); onEdit(row); }}><Edit fontSize="small" /></IconButton>
          </Tooltip>
          <Tooltip title="Excluir regra">
            <IconButton size="small" color="error" aria-label={"Excluir " + row.name} onClick={(event) => { event.stopPropagation(); onDelete(row); }}><Delete fontSize="small" /></IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ], [activeFilters]);

  const currentSort = (): { field: string; direction: "asc" | "desc" } | undefined => {
    const item = sortModel[0];
    return item?.sort ? { field: item.field, direction: item.sort } : undefined;
  };

  const query = (
    next: Partial<{ offset: number; limit: number; sort: { field: string; direction: "asc" | "desc" }; filters: ForecastGridFilter[] }> = {},
  ) => {
    const sort = next.sort ?? currentSort();
    onQuery({
      offset: next.offset ?? paginationModel.page * paginationModel.pageSize,
      limit: next.limit ?? paginationModel.pageSize,
      ...(sort ? { sort } : {}),
      filters: next.filters ?? Object.values(activeFilters),
    });
  };

  const firstSearch = useRef(true);
  useEffect(() => {
    if (firstSearch.current) {
      firstSearch.current = false;
      return;
    }
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
    setFilterModel(model);
    const item = model.items.find((candidate) => candidate.value !== undefined && candidate.value !== "");
    if (!item?.field) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const value = item.value instanceof Date ? item.value.toISOString() : String(item.value);
    const filter: ForecastGridFilter = { field: item.field, operator: "equals", value };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
  };

  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const field = cell?.getAttribute("data-field") || "";
    const rowId = cell?.parentElement?.getAttribute("data-id");
    const row = rows.find((item) => String(item.id) === rowId);
    const allowedFields = new Set(["name", "charge_type", "behavior", "start_period", "end_period", "priority", "active", "version"]);
    let value = "";
    if (field === "name") value = row?.name || "";
    else if (field === "charge_type") value = row?.charge_type || "";
    else if (field === "behavior") value = row?.behavior || "";
    else if (field === "start_period") value = row?.start_period || "";
    else if (field === "end_period") value = row?.end_period || "";
    else if (field === "priority") value = String(row?.priority ?? "");
    else if (field === "active") value = String(row?.active ?? "");
    else if (field === "version") value = String(row?.version ?? "");
    else value = cell?.textContent?.trim() || "";

    if (allowedFields.has(field) && value) {
      setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field, value });
    }
  };

  const applyContextFilter = (operator: "equals" | "notEquals") => {
    if (!context) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filter: ForecastGridFilter = { field: context.field, operator, value: context.value };
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
    const header = ["Nome", "Identificação", "Tipo", "Comportamento", "Início", "Fim", "Prioridade", "Ativa", "Versão"];
    const lines = [
      header.join(";"),
      ...rows.map((rule) => [
        rule.name,
        formatIdentification(rule),
        rule.charge_type,
        BEHAVIOR_LABELS[rule.behavior] || rule.behavior,
        formatPeriod(rule.start_period),
        formatPeriod(rule.end_period),
        rule.priority,
        rule.active ? "Sim" : "Não",
        rule.version,
      ].map(escape).join(";")),
    ];
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", "regras-forecast-" + new Date().toISOString().slice(0, 10) + ".csv");
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <Box sx={{ height: 460, width: "100%" }} onContextMenu={onCellContextMenu}>
        <DataGrid
          apiRef={apiRef}
          rows={rows}
          columns={columns}
          loading={loading}
          rowCount={total}
          getRowId={(row) => row.id}
          disableRowSelectionOnClick
          pagination
          paginationMode="server"
          sortingMode="server"
          filterMode="server"
          pageSizeOptions={[10, 25, 50]}
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
        <MenuItem onClick={() => applyContextFilter("equals")}>Mostrar iguais</MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>Mostrar diferentes</MenuItem>
        <MenuItem disabled={!context || !activeFilters[context.field]} onClick={() => clearFilter(context?.field)}>Limpar filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Limpar todos os filtros</MenuItem>
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
          if (field) apiRef.current.showColumnMenu(field);
        }}>Opções da coluna</MenuItem>
        <MenuItem onClick={() => { exportDisplayed(); setHeaderContext(null); }}>Exportar resultados exibidos</MenuItem>
        <Divider />
        <MenuItem disabled={!headerContext || !activeFilters[headerContext.field]} onClick={() => clearFilter(headerContext?.field)}>Limpar filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Limpar todos os filtros</MenuItem>
      </Menu>
    </>
  );
}
