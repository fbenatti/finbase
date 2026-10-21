import DataGrid from "../../components/FinbaseDataGrid";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Divider,
  InputAdornment,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Search } from "../../components/FinbaseIcons";
import { FilterAlt } from "../../components/FinbaseIcons";
import { GridColDef, GridFilterModel, GridPaginationModel, GridSortModel, useGridApiRef } from "@mui/x-data-grid";
import { ForecastHistoryPivot, HistoryFilter } from "../../services/api";

type Props = {
  result: ForecastHistoryPivot;
  loading: boolean;
  onQuery: (search: string, offset: number, filters?: Record<string, HistoryFilter>, sort?: { field: string; direction: "asc" | "desc" }) => void;
  measureLabel?: string;
};

type GridFilterSpec = { operator: string; value: string | string[] };

export default function HistoryGrid({ result, loading, onQuery, measureLabel }: Props) {
  const apiRef = useGridApiRef();
  const [search, setSearch] = useState("");
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 100 });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, GridFilterSpec>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);

  const fields = [...result.rows, ...result.columns];
  const offset = (result as any).offset || 0;
  const limit = (result as any).limit || 100;

  useEffect(() => {
    setPaginationModel({ page: Math.floor(offset / limit), pageSize: limit });
  }, [offset, limit]);

  // Campo real do backend a partir do campo da grade (d0..dn / value)
  const realFieldFor = (gridField: string): string => {
    if (gridField === "value") return "value";
    const index = Number(gridField.slice(1));
    return Number.isNaN(index) ? "" : fields[index];
  };

  const filtersForQuery = (): Record<string, HistoryFilter> => {
    const out: Record<string, HistoryFilter> = {};
    for (const [gridField, spec] of Object.entries(activeFilters)) {
      const real = realFieldFor(gridField);
      if (real) out[real] = spec;
    }
    return out;
  };

  const filteredHeader = (label: string, gridField: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>{label}</Typography>
      {activeFilters[gridField] && (
        <Tooltip title="Esta coluna possui um filtro ativo">
          <FilterAlt fontSize="small" color="primary" />
        </Tooltip>
      )}
    </Stack>
  );

  const columns = useMemo<GridColDef[]>(() => [
    ...fields.map((field, index): GridColDef => ({
      field: "d" + index,
      headerName: field,
      renderHeader: () => filteredHeader(field, "d" + index),
      flex: 1,
      minWidth: 160,
    })),
    {
      field: "value",
      headerName: measureLabel || "Valor",
      renderHeader: () => filteredHeader(measureLabel || "Valor", "value"),
      flex: 1,
      minWidth: 160,
      type: "number",
      filterable: false,
      valueFormatter: (v: unknown) => Number(v || 0).toLocaleString("pt-BR"),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [fields, measureLabel, activeFilters]);

  const rows = result.records.map((record, index) => ({
    id: offset + "-" + index,
    ...record,
    value: Number(record.value ?? record.forecast_value ?? 0),
  }));

  const applyFilters = (next: Record<string, GridFilterSpec>, nextPage = 0) => {
    setActiveFilters(next);
    setFilterModel({
      items: Object.entries(next).map(([gridField, spec], index) => ({
        id: "f" + index,
        field: gridField,
        operator: spec.operator,
        value: Array.isArray(spec.value) ? spec.value.join(", ") : String(spec.value),
      })),
    });
    setPaginationModel((prev) => ({ ...prev, page: nextPage }));
    onQuery(search, nextPage * limit, filtersForQueryWith(next));
  };

  const filtersForQueryWith = (next: Record<string, GridFilterSpec>): Record<string, HistoryFilter> => {
    const out: Record<string, HistoryFilter> = {};
    for (const [gridField, spec] of Object.entries(next)) {
      const real = realFieldFor(gridField);
      if (real) out[real] = spec;
    }
    return out;
  };

  const onFilter = (model: GridFilterModel) => {
    setFilterModel(model);
    const next: Record<string, GridFilterSpec> = {};
    for (const item of model.items) {
      if (!item.field || item.value === undefined || item.value === "") continue;
      const operator = String(item.operator || "contains");
      const value = operator.toLowerCase() === "isanyof"
        ? String(item.value).split(",").map(v => v.trim()).filter(Boolean)
        : item.value;
      next[item.field] = { operator, value };
    }
    applyFilters(next);
  };

  const onPagination = (model: GridPaginationModel) => {
    setPaginationModel(model);
    onQuery(search, model.page * limit, filtersForQuery());
  };

  const onSort = (model: GridSortModel) => {
    setSortModel(model);
    const item = model[0];
    if (!item?.sort) { onQuery(search, 0, filtersForQuery()); return; }
    const field = item.field === "value" ? "value" : realFieldFor(item.field);
    if (field) onQuery(search, 0, filtersForQuery(), { field, direction: item.sort });
  };

  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const gridField = cell?.getAttribute("data-field") || "";
    const real = realFieldFor(gridField);
    if (!real || real === "value") return;
    const rowId = cell?.parentElement?.getAttribute("data-id");
    const row = rows.find(item => String(item.id) === rowId);
    const raw = row ? (row as unknown as Record<string, unknown>)[gridField] : undefined;
    const value = raw === undefined || raw === null ? "" : String(raw);
    if (value) setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field: gridField, value });
  };

  const applyContextFilter = (operator: "equals" | "notEquals") => {
    if (!context) return;
    const next = { ...activeFilters, [context.field]: { operator, value: context.value } };
    applyFilters(next);
    setContext(null);
  };

  const clearFilter = (gridField?: string) => {
    if (gridField && !activeFilters[gridField]) {
      setContext(null);
      setHeaderContext(null);
      return;
    }
    const next = { ...activeFilters };
    if (gridField) delete next[gridField];
    else Object.keys(next).forEach(key => delete next[key]);
    applyFilters(next);
    setContext(null);
    setHeaderContext(null);
  };

  const exportDisplayed = () => {
    const escape = (value: unknown) => '"' + String(value ?? "").replace(/"/g, '""') + '"';
    const header = [...fields, measureLabel || "Valor"];
    const lines = [
      header.join(";"),
      ...result.records.map((record) => [
        ...fields.map(f => record[f]),
        record.value ?? record.forecast_value ?? "",
      ].map(escape).join(";")),
    ];
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", "forecast-resultado-" + new Date().toISOString().slice(0, 10) + ".csv");
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <TextField
        fullWidth
        size="small"
        placeholder="Buscar no resultado do forecast (qualquer dimensão do snapshot)"
        value={search}
        onChange={(event) => {
          setSearch(event.target.value);
          onQuery(event.target.value, 0, filtersForQuery());
        }}
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
      <Box sx={{ height: 520, width: "100%" }} onContextMenu={onCellContextMenu}>
        <DataGrid
          apiRef={apiRef}
          rows={rows}
          columns={columns}
          loading={loading}
          rowCount={-1}
          pagination
          paginationMode="server"
          sortingMode="server"
          filterMode="server"
          pageSizeOptions={[limit]}
          paginationModel={paginationModel}
          sortModel={sortModel}
          filterModel={filterModel}
          onPaginationModelChange={onPagination}
          onSortModelChange={onSort}
          onFilterModelChange={onFilter}
          sortingOrder={["asc", "desc"]}
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
