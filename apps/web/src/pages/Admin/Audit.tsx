import { Dialog, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Chip, Divider, IconButton, InputAdornment, Menu, MenuItem, Paper, Stack, TextField, Tooltip, Typography, Alert } from "@mui/material";
import { FilterAlt, Search, Visibility as VisibilityIcon } from "../../components/FinbaseIcons";
import { GridColDef, GridFilterModel, GridPaginationModel, GridSortModel, useGridApiRef } from "@mui/x-data-grid";
import {
  AuditLogItem, GridFilter, GridFilterOp, getAuditLog, getAuditLogs,
} from "../../services/api";

const ACTION_COLOR: Record<string, "success" | "primary" | "error" | "warning" | "default"> = {
  create: "success", update: "primary", delete: "error", execute: "default",
  login: "default", logout: "default", failed: "error",
};

function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

export default function Audit() {
  const apiRef = useGridApiRef();
  const [rows, setRows] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: 25 });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, GridFilter>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const queryState = useRef<{ offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: GridFilter[] }>({ offset: 0, limit: 25, filters: [] });
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runQuery = useCallback(async (queryParam?: { offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: GridFilter[] }) => {
    setLoading(true);
    setError(null);
    try {
      if (queryParam) queryState.current = queryParam;
      const q = queryState.current;
      const response = await getAuditLogs({
        limit: q.limit,
        offset: q.offset,
        sort_field: q.sort?.field,
        sort_direction: q.sort?.direction,
        filters: q.filters,
        search: search || undefined,
      });
      setRows(response.items);
      setTotal(response.total);
    } catch {
      setError("Falha ao carregar a auditoria");
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const query = (next: Partial<{ offset: number; limit: number; sort: { field: string; direction: "asc" | "desc" }; filters: GridFilter[] }> = {}) => {
    const currentSort = sortModel[0]?.sort ? { field: sortModel[0].field, direction: sortModel[0].sort } : undefined;
    runQuery({
      offset: next.offset ?? paginationModel.page * paginationModel.pageSize,
      limit: next.limit ?? paginationModel.pageSize,
      ...(next.sort ?? currentSort ? { sort: next.sort ?? currentSort } : {}),
      filters: next.filters ?? Object.values(activeFilters),
    });
  };

  useEffect(() => {
    query({ offset: 0, limit: 25 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSearch = (value: string) => {
    setSearch(value);
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      query({ offset: 0 });
    }, 350);
  };

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>{label}</Typography>
      {activeFilters[field] && <Tooltip title="Esta coluna possui um filtro ativo"><FilterAlt fontSize="small" color="primary" /></Tooltip>}
    </Stack>
  );

  const columns = useMemo<GridColDef[]>(() => [
    { field: "id", headerName: "ID", renderHeader: () => filteredHeader("ID", "id"), width: 90 },
    {
      field: "created_at", headerName: "Data/Hora", renderHeader: () => filteredHeader("Data/Hora", "created_at"),
      minWidth: 170, type: "dateTime",
      valueGetter: (_v, row: AuditLogItem) => row.created_at ? new Date(row.created_at) : null,
      valueFormatter: (value) => formatDateTime(value as string | Date | null),
    },
    { field: "username", headerName: "Usuário", renderHeader: () => filteredHeader("Usuário", "username"), minWidth: 130, valueGetter: (_v, row: AuditLogItem) => row.user?.username ?? "—" },
    { field: "module", headerName: "Módulo", renderHeader: () => filteredHeader("Módulo", "module"), minWidth: 150 },
    {
      field: "action", headerName: "Ação", renderHeader: () => filteredHeader("Ação", "action_label"),
      minWidth: 120, valueGetter: (_v, row: AuditLogItem) => row.action,
      renderCell: ({ row }: { row: AuditLogItem }) => (
        <Chip size="small" label={row.action} color={ACTION_COLOR[row.action] || "default"} variant="outlined" />
      ),
    },
    { field: "method", headerName: "Método", renderHeader: () => filteredHeader("Método", "method"), width: 100 },
    { field: "entity_label", headerName: "Registro", renderHeader: () => filteredHeader("Registro", "entity_label"), minWidth: 230, flex: 1 },
    {
      field: "detail", headerName: "Ações", width: 90, sortable: false, filterable: false,
      align: "center", headerAlign: "center",
      renderCell: ({ row }: { row: AuditLogItem }) => (
        <Tooltip title="Ver detalhe do evento">
          <IconButton size="small" onClick={() => { void openDetail(row); }}><VisibilityIcon fontSize="small" /></IconButton>
        </Tooltip>
      ),
    },
  ], [activeFilters]);

  const openDetail = async (row: AuditLogItem) => {
    try {
      setDetail(await getAuditLog(row.id));
      setOpen(true);
    } catch {
      setError("Falha ao carregar detalhe do evento");
    }
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

  const opByMui: Record<string, GridFilterOp> = {
    is: "equals", equals: "equals", "=": "equals",
    not: "notEquals", isNot: "notEquals", "!=": "notEquals",
    contains: "contains", doesNotContain: "notContains", notContains: "notContains",
    isAfter: "after", after: "after", ">": "after", onOrAfter: "after",
    isBefore: "before", before: "before", "<": "before", onOrBefore: "before",
  };

  const onFilter = (model: GridFilterModel) => {
    setFilterModel(model);
    const item = model.items.find((candidate) => candidate.value !== undefined && candidate.value !== "");
    if (!item?.field) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const val = item.value instanceof Date ? item.value.toISOString() : String(item.value);
    const filter: GridFilter = { field: item.field, operator: opByMui[item.operator] || "equals", value: val };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
  };

  const valueForField = (row: AuditLogItem, field: string): string => {
    switch (field) {
      case "username": return row.user?.username ?? "";
      case "created_at": return row.created_at ? new Date(row.created_at).toISOString() : "";
      case "action": return row.action;
      case "entity_type": return row.entity_type ?? "";
      default: return String((row as unknown as Record<string, unknown>)[field] ?? "");
    }
  };

  const onCellContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const field = cell?.getAttribute("data-field") || "";
    const rowId = cell?.parentElement?.getAttribute("data-id");
    const row = rows.find((r) => String(r.id) === rowId);
    const allowed = new Set(["id", "created_at", "username", "module", "action", "method", "entity_label", "entity_type", "status_code"]);
    if (allowed.has(field) && row) {
      setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field: field === "action" ? "action_label" : field, value: valueForField(row, field) });
    }
  };

  const applyContextFilter = (operator: GridFilterOp) => {
    if (!context) return;
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filter: GridFilter = { field: context.field, operator, value: context.value };
    const filters = { ...activeFilters, [filter.field]: filter };
    setActiveFilters(filters);
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
  };

  const clearFilter = (field?: string) => {
    if (field && !activeFilters[field]) {
      setContext(null); setHeaderContext(null);
      return;
    }
    const filters = { ...activeFilters };
    if (field) delete filters[field];
    else Object.keys(filters).forEach((key) => delete filters[key]);
    setActiveFilters(filters);
    setFilterModel({ items: Object.values(filters).map((f) => ({ id: f.field, field: f.field, operator: "equals", value: f.value })) });
    setPaginationModel((current) => ({ ...current, page: 0 }));
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
  };

  const downloadCsv = (content: string, filename: string) => {
    const blob = new Blob(["\uFEFF" + content], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const exportFiltered = async () => {
    setHeaderContext(null);
    const q = queryState.current;
    const seen: AuditLogItem[] = [];
    try {
      for (let offset = 0; ; offset += 200) {
        const page = await getAuditLogs({
          limit: 200, offset, sort_field: q.sort?.field, sort_direction: q.sort?.direction,
          filters: q.filters, search: search || undefined,
        });
        seen.push(...page.items);
        if (offset + page.items.length >= page.total || page.items.length === 0) break;
      }
      const header = ["ID", "Data/Hora", "Usuário", "Módulo", "Ação", "Método", "Registro"];
      const lines = [header.join(";"), ...seen.map((r) =>
        [r.id, r.created_at ? formatDateTime(r.created_at) : "", r.user?.username ?? "", r.module, r.action, r.method, r.entity_label ?? ""]
          .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(";")
      )];
      downloadCsv(lines.join("\r\n"), `auditoria-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch {
      setError("Falha ao exportar auditoria");
    }
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Auditoria</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Trilha de compliance de todas as mutações do sistema, com snapshots antes/depois e diff campo a campo.
        </Typography>
      </Box>
      <Box sx={{ p: 3 }}>
        <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 } }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 2, mb: 2, flexWrap: "wrap" }}>
            <Box>
              <Typography variant="h6">Eventos</Typography>
              <Typography variant="caption" color="text.secondary">{total} registros</Typography>
            </Box>
            <TextField
              size="small" placeholder="Buscar por usuário…" value={search}
              onChange={(e) => onSearch(e.target.value)}
              slotProps={{ input: { startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment>, sx: { height: 40 } } }}
              sx={{ minWidth: 260 }}
            />
          </Box>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
          <Box sx={{ height: 540, width: "100%" }} onContextMenu={onCellContextMenu}>
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
                setHeaderContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field: params.field });
              }}
              sx={{
                border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper",
                "& .MuiDataGrid-columnHeaders": { bgcolor: "background.default", borderBottomColor: "divider" },
              }}
            />
          </Box>
        </Paper>
      </Box>

      <Menu open={context !== null} onClose={() => setContext(null)} anchorReference="anchorPosition"
        anchorPosition={context ? { top: context.mouseY, left: context.mouseX } : undefined}>
        <MenuItem onClick={() => applyContextFilter("equals")}>Mostrar iguais</MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>Mostrar diferentes</MenuItem>
        <MenuItem disabled={!context || !activeFilters[context.field]} onClick={() => clearFilter(context?.field)}>Limpar filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Limpar todos os filtros</MenuItem>
      </Menu>
      <Menu open={headerContext !== null} onClose={() => setHeaderContext(null)} anchorReference="anchorPosition"
        anchorPosition={headerContext ? { top: headerContext.mouseY, left: headerContext.mouseX } : undefined}>
        <MenuItem onClick={() => { const field = headerContext?.field; setHeaderContext(null); if (field) apiRef.current.showColumnMenu(field); }}>Opções da coluna</MenuItem>
        <MenuItem onClick={() => { void exportFiltered(); }}>Exportar resultados exibidos</MenuItem>
        <Divider />
        <MenuItem disabled={!headerContext || !activeFilters[headerContext.field]} onClick={() => clearFilter(headerContext?.field)}>Limpar filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Limpar todos os filtros</MenuItem>
      </Menu>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          Evento #{detail?.id ?? ""}
          <Box flex={1} />
          {detail?.module && <Chip size="small" label={detail.module} />}
          {detail?.action && <Chip size="small" label={detail.action} color={ACTION_COLOR[detail.action] || "default"} />}
        </DialogTitle>
        <DialogContent>
          {detail && (
            <>
              <Typography variant="body2" sx={{ mb: 1 }}>
                {detail.user?.username ?? "—"} · {detail.created_at ? formatDateTime(detail.created_at) : ""} · {detail.method} {detail.path}
                {detail.entity_type ? ` · ${detail.entity_type}#${detail.entity_id}` : ""}
              </Typography>
              {detail.changes?.map((c: any, i: number) => (
                <Typography key={i} variant="body2"><b>{c.field}</b>: {JSON.stringify(c.old)} → {JSON.stringify(c.new)}</Typography>
              ))}
              {!detail.changes && <Typography variant="body2">Sem mudanças campo a campo.</Typography>}
              <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 2 }}>
                <Box flex={1} sx={{ bgcolor: "#fafafa", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 300 }}>
                  <Typography variant="caption" fontWeight={700}>Snapshot antes</Typography>
                  <pre style={{ fontSize: 11, margin: 0 }}>{JSON.stringify(detail.before_json ?? null, null, 2)}</pre>
                </Box>
                <Box flex={1} sx={{ bgcolor: "#f0f7ff", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 300 }}>
                  <Typography variant="caption" fontWeight={700}>Snapshot depois</Typography>
                  <pre style={{ fontSize: 11, margin: 0 }}>{JSON.stringify(detail.after_json ?? null, null, 2)}</pre>
                </Box>
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                IP: {detail.ip} · {detail.user_agent} · request {detail.request_id}
              </Typography>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
