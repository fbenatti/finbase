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
  CompareArrows,
  FilterAlt,
  Stop,
  Visibility,
} from "@mui/icons-material";
import {
  DataGrid,
  GridColDef,
  GridFilterModel,
  GridPaginationModel,
  GridSortModel,
  useGridApiRef,
} from "@mui/x-data-grid";
import { ForecastRun } from "../../services/api";

export type ForecastGridFilter = {
  field: string;
  operator: "equals" | "notEquals";
  value: string;
};

type Props = {
  rows: ForecastRun[];
  total: number;
  loading: boolean;
  search: string;
  pollTick: number;
  cancellingId: number | null;
  onQuery: (query: {
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: ForecastGridFilter[];
  }) => void;
  onCancel: (run: ForecastRun) => void;
  onOpenResult: (run: ForecastRun, comparison: boolean) => void;
};

const isActiveRun = (status: string) => status === "QUEUED" || status === "RUNNING";

const runStatusColor = (status: string): "success" | "error" | "info" | "warning" | "default" => {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "error";
  if (status === "CANCELLED") return "warning";
  if (isActiveRun(status)) return "info";
  return "default";
};

function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

export default function ForecastHistoryDataGrid({
  rows,
  total,
  loading,
  search,
  pollTick,
  cancellingId,
  onQuery,
  onCancel,
  onOpenResult,
}: Props) {
  const apiRef = useGridApiRef();
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 25 });
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
      field: "id",
      headerName: "ID",
      renderHeader: () => filteredHeader("ID", "id"),
      minWidth: 80,
    },
    {
      field: "status",
      headerName: "Status",
      renderHeader: () => filteredHeader("Status", "status"),
      minWidth: 135,
      renderCell: ({ row }: { row: ForecastRun }) => {
        const status = String(row.status || "UNKNOWN");
        return <Chip size="small" label={status} color={runStatusColor(status)} />;
      },
    },
    {
      field: "config_name",
      headerName: "Configuração",
      renderHeader: () => filteredHeader("Configuração", "config_name"),
      minWidth: 200,
      flex: 1,
      valueGetter: (_: unknown, row: ForecastRun) => row.config_name || "—",
      renderCell: ({ row }: { row: ForecastRun }) => (
        <Tooltip title={row.config_name || "Configuração excluída"}>
          <Typography variant="body2" noWrap>{row.config_name || "—"}</Typography>
        </Tooltip>
      ),
    },
    {
      field: "rows_written",
      headerName: "Linhas",
      renderHeader: () => filteredHeader("Linhas", "rows_written"),
      minWidth: 110,
      valueFormatter: (value: unknown) => Number(value || 0).toLocaleString("pt-BR"),
    },
    {
      field: "phase",
      headerName: "Fase / Mensagem",
      renderHeader: () => filteredHeader("Fase / Mensagem", "phase"),
      minWidth: 230,
      flex: 1.2,
      renderCell: ({ row }: { row: ForecastRun }) => (
        <Tooltip title={row.phase || "Aguardando worker..."}>
          <Typography noWrap variant="body2" color="text.secondary">{row.phase || "Aguardando worker..."}</Typography>
        </Tooltip>
      ),
    },
    {
      field: "started_at",
      headerName: "Início",
      renderHeader: () => filteredHeader("Início", "started_at"),
      minWidth: 165,
      type: "dateTime",
      valueGetter: (_, row: ForecastRun) => (row.started_at ? new Date(row.started_at) : null),
      valueFormatter: (value) => formatDateTime(value),
    },
    {
      field: "finished_at",
      headerName: "Fim",
      renderHeader: () => filteredHeader("Fim", "finished_at"),
      minWidth: 165,
      type: "dateTime",
      valueGetter: (_, row: ForecastRun) => (row.finished_at ? new Date(row.finished_at) : null),
      valueFormatter: (value) => formatDateTime(value),
    },
    {
      field: "actions",
      headerName: "Ações",
      width: 118,
      sortable: false,
      filterable: false,
      align: "center",
      headerAlign: "center",
      renderCell: ({ row }: { row: ForecastRun }) => {
        const status = String(row.status || "").toUpperCase();
        const active = isActiveRun(status);
        const completed = status === "COMPLETED";
        return (
          <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
            <Tooltip title="Cancelar execução">
              <span>
                <IconButton
                  size="small"
                  color="error"
                  disabled={!active || cancellingId === row.id}
                  aria-label={"Cancelar execução " + row.id}
                  onClick={(event) => { event.stopPropagation(); onCancel(row); }}
                >
                  <Stop fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Abrir resultado">
              <span>
                <IconButton
                  size="small"
                  disabled={!completed}
                  aria-label={"Abrir resultado da execução " + row.id}
                  onClick={(event) => { event.stopPropagation(); onOpenResult(row, false); }}
                >
                  <Visibility fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Comparar com realizado">
              <span>
                <IconButton
                  size="small"
                  disabled={!completed}
                  aria-label={"Comparar resultado da execução " + row.id}
                  onClick={(event) => { event.stopPropagation(); onOpenResult(row, true); }}
                >
                  <CompareArrows fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        );
      },
    },
  ], [activeFilters, cancellingId]);

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

  // Re-query whenever the search term changes, always back at page 0 while
  // keeping the accumulated column filters (AND across columns). The page
  // performs the initial query on mount, so skip the first run here.
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

  // Poll tick: refresh the current page so running statuses stay live.
  const firstPoll = useRef(true);
  useEffect(() => {
    if (firstPoll.current) {
      firstPoll.current = false;
      return;
    }
    query({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollTick]);

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
    const allowedFields = new Set(["id", "status", "config_name", "rows_written", "phase", "started_at", "finished_at"]);
    let value = "";
    if (field === "id") value = String(row?.id ?? "");
    else if (field === "status") value = row?.status || "";
    else if (field === "config_name") value = row?.config_name || "";
    else if (field === "rows_written") value = String(row?.rows_written ?? "");
    else if (field === "phase") value = row?.phase || "";
    else if (field === "started_at") value = row?.started_at || "";
    else if (field === "finished_at") value = row?.finished_at || "";
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
    const header = ["ID", "Status", "Configuração", "Linhas", "Fase / Mensagem", "Início", "Fim"];
    const lines = [
      header.join(";"),
      ...rows.map((run) => [
        run.id,
        run.status,
        run.config_name || "",
        run.rows_written ?? 0,
        run.phase || "",
        run.started_at ? formatDateTime(run.started_at) : "",
        run.finished_at ? formatDateTime(run.finished_at) : "",
      ].map(escape).join(";")),
    ];
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", "historico-forecast-" + new Date().toISOString().slice(0, 10) + ".csv");
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <Box sx={{ height: 520, width: "100%" }} onContextMenu={onCellContextMenu}>
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
