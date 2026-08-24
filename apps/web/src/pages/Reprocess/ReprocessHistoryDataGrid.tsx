import { useMemo, useState } from "react";
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
import { FilterAlt, Replay } from "@mui/icons-material";
import {
  DataGrid,
  GridColDef,
  GridFilterModel,
  GridPaginationModel,
  GridSortModel,
  useGridApiRef,
} from "@mui/x-data-grid";
import { ReprocessStatus } from "../../services/api";

export type ReprocessGridFilter = {
  field: string;
  operator: "equals" | "notEquals";
  value: string;
};

type Props = {
  rows: ReprocessStatus[];
  total: number;
  loading: boolean;
  onReprocess?: (row: ReprocessStatus) => void;
  onQuery: (query: {
    offset: number;
    limit: number;
    sort?: { field: string; direction: "asc" | "desc" };
    filters: ReprocessGridFilter[];
  }) => void;
};

function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

export default function ReprocessHistoryDataGrid({
  rows,
  total,
  loading,
  onReprocess,
  onQuery,
}: Props) {
  const apiRef = useGridApiRef();
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({
    page: 0,
    pageSize: 10,
  });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, ReprocessGridFilter>>({});
  const [context, setContext] = useState<{
    mouseX: number;
    mouseY: number;
    field: string;
    value: string;
    row: ReprocessStatus | null;
  } | null>(null);
  const [headerContext, setHeaderContext] = useState<{
    mouseX: number;
    mouseY: number;
    field: string;
  } | null>(null);

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>
        {label}
      </Typography>
      {activeFilters[field] && (
        <Tooltip title="Esta coluna possui um filtro ativo">
          <FilterAlt fontSize="small" color="primary" />
        </Tooltip>
      )}
    </Stack>
  );

  const columns = useMemo<GridColDef[]>(() => [
    {
      field: "status",
      headerName: "Status",
      renderHeader: () => filteredHeader("Status", "status"),
      minWidth: 135,
      renderCell: ({ row }: { row: ReprocessStatus }) => {
        const val = row.status;
        const color =
          val === "COMPLETED"
            ? "success"
            : val === "FAILED" || val === "CANCELLED"
            ? "error"
            : val === "RUNNING"
            ? "warning"
            : "info";
        return <Chip size="small" label={val || "UNKNOWN"} color={color} />;
      },
    },
    {
      field: "table",
      headerName: "Tabela destino",
      renderHeader: () => filteredHeader("Tabela destino", "table"),
      minWidth: 200,
      flex: 1,
      renderCell: ({ row }: { row: ReprocessStatus }) => (
        <Typography variant="body2" noWrap fontWeight={500}>
          {row.table || "—"}
        </Typography>
      ),
    },
    {
      field: "rows_written",
      headerName: "Linhas",
      renderHeader: () => filteredHeader("Linhas", "rows_written"),
      minWidth: 120,
      valueFormatter: (value) => Number(value || 0).toLocaleString("pt-BR"),
    },
    {
      field: "phase",
      headerName: "Fase / Mensagem",
      renderHeader: () => filteredHeader("Fase / Mensagem", "phase"),
      minWidth: 240,
      flex: 1.2,
      renderCell: ({ row }: { row: ReprocessStatus }) => (
        <Tooltip title={String(row.phase || "Aguardando worker...")}>
          <Typography noWrap variant="body2" color="text.secondary">
            {row.phase || "Aguardando worker..."}
          </Typography>
        </Tooltip>
      ),
    },
    {
      field: "started_at",
      headerName: "Início",
      renderHeader: () => filteredHeader("Início", "started_at"),
      minWidth: 165,
      type: "dateTime",
      valueGetter: (_, row: ReprocessStatus) =>
        row.started_at ? new Date(row.started_at) : null,
      valueFormatter: (value) => formatDateTime(value),
    },
    {
      field: "finished_at",
      headerName: "Fim",
      renderHeader: () => filteredHeader("Fim", "finished_at"),
      minWidth: 165,
      type: "dateTime",
      valueGetter: (_, row: ReprocessStatus) =>
        row.finished_at ? new Date(row.finished_at) : null,
      valueFormatter: (value) => formatDateTime(value),
    },
    {
      field: "actions",
      headerName: "Ações",
      minWidth: 90,
      sortable: false,
      filterable: false,
      renderCell: ({ row }: { row: ReprocessStatus }) => {
        const isActionDisabled = row.status === "RUNNING" || row.status === "QUEUED" || !onReprocess;
        const isFailed = row.status === "FAILED";
        return (
          <Tooltip title={isFailed ? "Reprocessar carga com falha" : "Reprocessar esta execução"}>
            <span>
              <IconButton
                size="small"
                color={isFailed ? "error" : "primary"}
                disabled={isActionDisabled}
                onClick={(e) => {
                  e.stopPropagation();
                  onReprocess?.(row);
                }}
              >
                <Replay fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        );
      },
    },
  ], [activeFilters, onReprocess]);

  const query = (
    next: Partial<{
      offset: number;
      limit: number;
      sort: { field: string; direction: "asc" | "desc" };
      filters: ReprocessGridFilter[];
    }> = {}
  ) => {
    const currentSort = sortModel[0]?.sort
      ? { field: sortModel[0].field, direction: sortModel[0].sort }
      : undefined;
    onQuery({
      offset: next.offset ?? paginationModel.page * paginationModel.pageSize,
      limit: next.limit ?? paginationModel.pageSize,
      ...(next.sort ?? currentSort ? { sort: next.sort ?? currentSort } : {}),
      filters: next.filters ?? Object.values(activeFilters),
    });
  };

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
    if (!item?.field) {
      return;
    }
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const val = item.value instanceof Date ? item.value.toISOString() : String(item.value);
    const filter: ReprocessGridFilter = {
      field: item.field,
      operator: "equals",
      value: val,
    };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
  };

  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const field = cell?.getAttribute("data-field") || "";
    const rowId = cell?.parentElement?.getAttribute("data-id");
    const row = rows.find((r) => String(r.id) === rowId);
    const allowedFields = new Set(["status", "table", "phase", "rows_written", "started_at", "finished_at"]);
    let value = "";
    if (field === "status") value = row?.status || "";
    else if (field === "table") value = row?.table || "";
    else if (field === "phase") value = row?.phase || "";
    else if (field === "rows_written") value = String(row?.rows_written ?? "");
    else if (field === "started_at") value = row?.started_at || "";
    else if (field === "finished_at") value = row?.finished_at || "";
    else value = cell?.textContent?.trim() || "";

    if (allowedFields.has(field) && value) {
      setContext({
        mouseX: event.clientX - 2,
        mouseY: event.clientY - 4,
        field,
        value,
        row: row || null,
      });
    }
  };

  const applyContextFilter = (operator: "equals" | "notEquals") => {
    if (!context) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filter: ReprocessGridFilter = {
      field: context.field,
      operator,
      value: context.value,
    };
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
    const csvHeader = ["ID", "Status", "Tabela Destino", "Linhas", "Fase / Mensagem", "Início", "Fim"];
    const csvRows = rows.map((r) => [
      r.id,
      r.status,
      r.table || "",
      r.rows_written ?? 0,
      r.phase || "",
      r.started_at ? new Date(r.started_at).toLocaleString("pt-BR") : "",
      r.finished_at ? new Date(r.finished_at).toLocaleString("pt-BR") : "",
    ]);
    const csvContent = [
      csvHeader.join(";"),
      ...csvRows.map((row) =>
        row
          .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
          .join(";")
      ),
    ].join("\r\n");

    const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `historico-reprocessamento-${new Date().toISOString().slice(0, 10)}.csv`);
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
          getRowId={(r) => r.id}
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
            setHeaderContext({
              mouseX: event.clientX - 2,
              mouseY: event.clientY - 4,
              field: params.field,
            });
          }}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 2,
            bgcolor: "background.paper",
            "& .MuiDataGrid-columnHeaders": {
              bgcolor: "#f8fafc",
              borderBottomColor: "divider",
            },
            "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus": {
              outline: "none",
            },
          }}
        />
      </Box>

      {/* Cell Context Menu */}
      <Menu
        open={context !== null}
        onClose={() => setContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          context ? { top: context.mouseY, left: context.mouseX } : undefined
        }
      >
        {onReprocess && context?.row && context.row.status !== "RUNNING" && context.row.status !== "QUEUED" && (
          <MenuItem
            onClick={() => {
              if (context.row) onReprocess(context.row);
              setContext(null);
            }}
          >
            Reprocessar esta execução
          </MenuItem>
        )}
        {onReprocess && context?.row && (
          <Divider />
        )}
        <MenuItem onClick={() => applyContextFilter("equals")}>
          Mostrar iguais
        </MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>
          Mostrar diferentes
        </MenuItem>
        <MenuItem
          disabled={!context || !activeFilters[context.field]}
          onClick={() => clearFilter(context?.field)}
        >
          Limpar filtro da coluna
        </MenuItem>
        <MenuItem
          disabled={Object.keys(activeFilters).length === 0}
          onClick={() => clearFilter()}
        >
          Limpar todos os filtros
        </MenuItem>
      </Menu>

      {/* Header Context Menu */}
      <Menu
        open={headerContext !== null}
        onClose={() => setHeaderContext(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          headerContext
            ? { top: headerContext.mouseY, left: headerContext.mouseX }
            : undefined
        }
      >
        <MenuItem
          onClick={() => {
            const field = headerContext?.field;
            setHeaderContext(null);
            if (field) apiRef.current.showColumnMenu(field);
          }}
        >
          Opções da coluna
        </MenuItem>
        <MenuItem
          onClick={() => {
            exportDisplayed();
            setHeaderContext(null);
          }}
        >
          Exportar resultados exibidos
        </MenuItem>
        <Divider />
        <MenuItem
          disabled={!headerContext || !activeFilters[headerContext.field]}
          onClick={() => clearFilter(headerContext?.field)}
        >
          Limpar filtro da coluna
        </MenuItem>
        <MenuItem
          disabled={Object.keys(activeFilters).length === 0}
          onClick={() => clearFilter()}
        >
          Limpar todos os filtros
        </MenuItem>
      </Menu>
    </>
  );
}
