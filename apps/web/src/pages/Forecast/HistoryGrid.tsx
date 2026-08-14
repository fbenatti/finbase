import { useState } from "react";
import { Box, Button, Menu, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { DataGrid, GridToolbar, GridFilterModel, GridPaginationModel, GridSortModel, GridColDef } from "@mui/x-data-grid";
import { ForecastHistoryPivot, HistoryFilter } from "../../services/api";

type Props = { result: ForecastHistoryPivot; loading: boolean; onQuery: (search: string, offset: number, filters?: Record<string, HistoryFilter>, sort?: { field: string; direction: "asc" | "desc" }) => void; measureLabel?: string };

export default function HistoryGrid({ result, loading, onQuery, measureLabel }: Props) {
  const [search, setSearch] = useState("");
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const offset = (result as any).offset || 0;
  const limit = (result as any).limit || 100;
  const fields = [...result.rows, ...result.columns];
  const columns: GridColDef[] = [...fields.map((field, index): GridColDef => ({ field: `d${index}`, headerName: field, flex: 1, minWidth: 160, filterable: true })), { field: "value", headerName: measureLabel || "Valor", flex: 1, minWidth: 160, type: "number", filterable: false }];
  const rows = result.records.map((record, index) => ({ id: `${offset}-${index}`, ...record, value: Number(record.value ?? record.forecast_value ?? record.forecast_value ?? 0) }));
  const onFilter = (model: GridFilterModel) => {
    setFilterModel(model);
    const filters: Record<string, HistoryFilter> = {};
    for (const item of model.items) {
      if (!item.value) continue;
      const index = Number(item.field.slice(1));
      const field = fields[index];
      if (field) {
        const operator = item.operator || "equals";
        const value = operator === "isAnyOf" ? String(item.value).split(",").map(v => v.trim()).filter(Boolean) : item.value;
        filters[field] = { operator, value };
      }
    }
    onQuery(search, 0, filters);
  };
  const onPagination = (model: GridPaginationModel) => onQuery(search, model.page * limit);
  const onSort = (model: GridSortModel) => {
    setSortModel(model);
    const item = model[0];
    if (!item?.sort) return onQuery(search, 0);
    const index = Number(item.field.slice(1));
    const field = item.field === "value" ? "value" : fields[index];
    if (field) onQuery(search, 0, {}, { field, direction: item.sort });
  };
  const clearFilters = () => {
    setSearch("");
    setFilterModel({ items: [] });
    setSortModel([]);
    onQuery("", 0, {});
  };
  const closeContext = () => setContext(null);
  const applyContextFilter = (operator: "equals" | "notEquals") => {
    if (!context) return;
    onQuery(search, 0, { [context.field]: { operator, value: context.value } });
    closeContext();
  };
  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const dataField = cell?.getAttribute("data-field") || "";
    const fieldIndex = Number(dataField.slice(1));
    const field = Number.isNaN(fieldIndex) ? "" : fields[fieldIndex];
    const value = cell?.textContent?.trim() || "";
    if (field && value) setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field, value });
  };
  return <Box mt={2}>
    <Stack direction="row" gap={1} mb={1}>
      <TextField size="small" fullWidth label="Buscar no resultado do Forecast" placeholder="Texto em qualquer dimensão do snapshot" value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === "Enter" && onQuery(search, 0)} />
      <Button variant="contained" disabled={loading} onClick={() => onQuery(search, 0)}>Buscar</Button>
      <Button variant="outlined" disabled={loading} onClick={clearFilters}>Limpar filtros</Button>
    </Stack>
    <Typography variant="caption">DataGrid server-side · filtros por campo no menu da coluna · clique direito em uma célula para mostrar iguais/diferentes · página de {limit} linhas · {offset + 1}–{offset + result.records.length}</Typography>
    <Box sx={{ mt: 1, height: 560, width: "100%" }} onContextMenu={onCellContextMenu}><DataGrid rows={rows} columns={columns} loading={loading} pagination paginationMode="server" filterMode="server" sortingMode="server" rowCount={offset + result.records.length + (result.records.length === limit ? 1 : 0)} pageSizeOptions={[100]} paginationModel={{ page: Math.floor(offset / limit), pageSize: limit }} filterModel={filterModel} sortModel={sortModel} onPaginationModelChange={onPagination} onFilterModelChange={onFilter} sortingOrder={["asc", "desc"]} onSortModelChange={onSort} slots={{ toolbar: GridToolbar }} disableRowSelectionOnClick /></Box>
    <Menu open={context !== null} onClose={closeContext} anchorReference="anchorPosition" anchorPosition={context ? { top: context.mouseY, left: context.mouseX } : undefined}>
      <MenuItem onClick={() => applyContextFilter("equals")}>Mostrar iguais a “{context?.value}”</MenuItem>
      <MenuItem onClick={() => applyContextFilter("notEquals")}>Mostrar diferentes de “{context?.value}”</MenuItem>
    </Menu>
  </Box>;
}
