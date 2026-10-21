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
  PlayArrow,
} from "@mui/icons-material";
import {
  DataGrid,
  GridColDef,
  GridFilterModel,
  GridPaginationModel,
  GridSortModel,
  useGridApiRef,
} from "@mui/x-data-grid";
import { ForecastConfig } from "../../services/api";

export type ForecastGridFilter = {
  field: string;
  operator: "equals" | "notEquals";
  value: string;
};

type Props = {
  rows: ForecastConfig[];
  total: number;
  loading: boolean;
  search: string;
  runningConfigId: number | null;
  onQuery: (query: {
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: ForecastGridFilter[];
  }) => void;
  onEdit: (config: ForecastConfig) => void;
  onRun: (config: ForecastConfig) => void;
  onDelete: (config: ForecastConfig) => void;
};

function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

const rateioLabel = (config: ForecastConfig): string => {
  const count = (config.connection_allocation_rules || []).length;
  if (count > 0) return "Rateio (" + count + ")";
  if (config.period_allocation === "charge_interval") return "Rateio (legado global)";
  return "Diário";
};

export default function ForecastConfigsDataGrid({
  rows,
  total,
  loading,
  search,
  runningConfigId,
  onQuery,
  onEdit,
  onRun,
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
      minWidth: 220,
      flex: 1.2,
      align: "left",
      headerAlign: "left",
      renderCell: ({ row }: { row: ForecastConfig }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%", width: "100%" }}>
          <Typography variant="body2" fontWeight={700} noWrap>{row.name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>{row.measure_column} · {row.model_mode}</Typography>
        </Stack>
      ),
    },
    {
      field: "table_name",
      headerName: "base consolidada",
      renderHeader: () => filteredHeader("base consolidada", "table_name"),
      minWidth: 180,
      flex: 1,
      align: "left",
      headerAlign: "left",
      valueGetter: (_: unknown, row: ForecastConfig) => row.table_name || "—",
      renderCell: ({ row }: { row: ForecastConfig }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%", width: "100%" }}>
          <Tooltip title={row.table_name || "—"}>
            <Typography variant="body2" noWrap>{row.table_name || "—"}</Typography>
          </Tooltip>
        </Stack>
      ),
    },
    {
      field: "measure_column",
      headerName: "Medida",
      renderHeader: () => filteredHeader("Medida", "measure_column"),
      minWidth: 120,
      align: "left",
      headerAlign: "left",
    },
    {
      field: "horizon_months",
      headerName: "Horizonte",
      renderHeader: () => filteredHeader("Horizonte", "horizon_months"),
      minWidth: 100,
      align: "left",
      headerAlign: "left",
      valueFormatter: (value: unknown) => Number(value || 0) + " meses",
    },
    {
      field: "version",
      headerName: "Versão",
      renderHeader: () => filteredHeader("Versão", "version"),
      minWidth: 90,
      align: "left",
      headerAlign: "left",
    },
    {
      field: "active",
      headerName: "Ativa",
      renderHeader: () => filteredHeader("Ativa", "active"),
      minWidth: 90,
      align: "left",
      headerAlign: "left",
      renderCell: ({ row }: { row: ForecastConfig }) => (
        <Stack justifyContent="center" sx={{ height: "100%", width: "100%" }}>
          <Chip size="small" label={row.active ? "Sim" : "Não"} color={row.active ? "success" : "default"} />
        </Stack>
      ),
    },
    {
      field: "updated_at",
      headerName: "Atualizado em",
      renderHeader: () => filteredHeader("Atualizado em", "updated_at"),
      minWidth: 165,
      type: "dateTime",
      align: "left",
      headerAlign: "left",
      valueGetter: (_, row: ForecastConfig) => (row.updated_at ? new Date(row.updated_at) : null),
      valueFormatter: (value) => formatDateTime(value),
    },
    {
      field: "actions",
      headerName: "Ações",
      width: 126,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: ForecastConfig }) => (
        <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
          <Tooltip title="Executar forecast">
            <span>
              <IconButton size="small" color="primary" disabled={runningConfigId === row.id} aria-label={"Executar " + row.name} onClick={(event) => { event.stopPropagation(); onRun(row); }}><PlayArrow fontSize="small" /></IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Editar configuração">
            <IconButton size="small" aria-label={"Editar " + row.name} onClick={(event) => { event.stopPropagation(); onEdit(row); }}><Edit fontSize="small" /></IconButton>
          </Tooltip>
          <Tooltip title="Excluir configuração">
            <IconButton size="small" color="error" aria-label={"Excluir " + row.name} onClick={(event) => { event.stopPropagation(); onDelete(row); }}><Delete fontSize="small" /></IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ], [activeFilters, runningConfigId]);

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

  // Re-query when the search term changes, back at page 0, keeping filters.
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
    const allowedFields = new Set(["name", "table_name", "measure_column", "horizon_months", "model_mode", "version", "active", "updated_at"]);
    let value = "";
    if (field === "name") value = row?.name || "";
    else if (field === "table_name") value = row?.table_name || "";
    else if (field === "measure_column") value = row?.measure_column || "";
    else if (field === "horizon_months") value = String(row?.horizon_months ?? "");
    else if (field === "model_mode") value = row?.model_mode || "";
    else if (field === "version") value = String(row?.version ?? "");
    else if (field === "active") value = String(row?.active ?? "");
    else if (field === "updated_at") value = row?.updated_at || "";
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
    const header = ["Nome", "base consolidada", "Medida", "Horizonte", "Rateio", "Versão", "Ativa", "Atualizado em"];
    const lines = [
      header.join(";"),
      ...rows.map((config) => [
        config.name,
        config.table_name || "",
        config.measure_column,
        config.horizon_months,
        rateioLabel(config),
        config.version,
        config.active ? "Sim" : "Não",
        config.updated_at ? formatDateTime(config.updated_at) : "",
      ].map(escape).join(";")),
    ];
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", "configuracoes-forecast-" + new Date().toISOString().slice(0, 10) + ".csv");
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
            "& .MuiDataGrid-columnHeaders": { bgcolor: "#f8fafc", borderBottomColor: "divider" },
            "& .MuiDataGrid-cell": { display: "flex", alignItems: "center" },
            "& .MuiDataGrid-cell[data-field='actions']": { justifyContent: "center" },
            "& .MuiDataGrid-columnHeader[data-field='actions']": { justifyContent: "center" },
            "& .MuiDataGrid-cell:not([data-field='actions'])": { justifyContent: "flex-start" },
            "& .MuiDataGrid-columnHeader:not([data-field='actions'])": { justifyContent: "flex-start" },
            "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus": { outline: "none" },
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
