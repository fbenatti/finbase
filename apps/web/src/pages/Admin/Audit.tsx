import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Chip, Divider, Drawer, FormControlLabel, IconButton, InputAdornment, Menu, MenuItem, Paper, Select, Stack, Switch, TextField, Tooltip, Typography } from "@mui/material";
import { Close, FilterAlt, Search } from "../../components/FinbaseIcons";
import { GridColDef, GridFilterModel, GridPaginationModel, GridSortModel, useGridApiRef } from "@mui/x-data-grid";
import {
  AuditLogItem, GridFilter, GridFilterOp, getAuditLog, getAuditLogs,
} from "../../services/api";
import { RowAction } from "../../components/RowAction";
import VersionTimeline, { type TimelineChange, type TimelineItem } from "../../components/VersionTimeline";
import { colors } from "../../theme/tokens";

const PAGE_SIZE = 50;

const ACTION_META: Record<string, { label: string; chipColor: "success" | "primary" | "error" | "default"; tone: NonNullable<TimelineItem["tag"]>["tone"] }> = {
  create: { label: "Criação", chipColor: "success", tone: "success" },
  update: { label: "Alteração", chipColor: "primary", tone: "primary" },
  delete: { label: "Exclusão", chipColor: "error", tone: "error" },
  failed: { label: "Falhou", chipColor: "error", tone: "error" },
  execute: { label: "Execução", chipColor: "default", tone: "neutral" },
  login: { label: "Acesso", chipColor: "default", tone: "neutral" },
  logout: { label: "Saída", chipColor: "default", tone: "neutral" },
};

const actionMeta = (action: string) => ACTION_META[action] ?? { label: action, chipColor: "default" as const, tone: "neutral" as const };

type AuditDetail = Awaited<ReturnType<typeof getAuditLog>> & { path?: string | null };

const HIDDEN_FIELDS = new Set(["updated_at", "created_at"]);

function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "medium" }).format(date);
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "sim" : "não";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function humanField(field: string): string {
  const text = field.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** Diff "campo: antes → depois". Usa o diff do servidor e, sem ele, compara os snapshots. */
function buildChanges(event: AuditDetail): TimelineChange[] {
  if (event.changes && event.changes.length > 0) {
    return event.changes.map((change) => ({
      label: humanField(change.field),
      from: displayValue(change.old),
      to: displayValue(change.new),
    }));
  }
  const before = asRecord(event.before_json);
  const after = asRecord(event.after_json);
  const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((field) => !HIDDEN_FIELDS.has(field));
  return fields.flatMap((field) => {
    const old = before[field];
    const next = after[field];
    if (JSON.stringify(old) === JSON.stringify(next)) return [];
    return [{
      label: humanField(field),
      from: old === undefined ? undefined : displayValue(old),
      to: next === undefined ? undefined : displayValue(next),
    }];
  });
}

function entityText(event: { entity_type?: string | null; entity_id?: string | null; entity_label?: string | null; module: string }): string {
  const base = event.entity_type ?? event.module;
  const id = event.entity_id ? ` #${event.entity_id}` : "";
  const label = event.entity_label ? ` · ${event.entity_label}` : "";
  return `${base}${id}${label}`;
}

function AuditDetailPanel({ event, onClose }: { event: AuditDetail | null; onClose: () => void }) {
  const [showJson, setShowJson] = useState(false);
  useEffect(() => { setShowJson(false); }, [event?.id]);
  if (!event) return null;

  const meta = actionMeta(event.action);
  const changes = buildChanges(event);
  const item: TimelineItem = {
    id: event.id,
    title: `${meta.label} ${entityText(event)}`,
    date: event.created_at ?? "",
    author: event.user?.username ?? "Sistema",
    tag: { label: meta.label, tone: meta.tone },
    summary: `${event.module} · ${event.method}${event.status_code ? ` · HTTP ${event.status_code}` : ""}`,
    changes,
  };
  const technical: Array<[string, string | null | undefined]> = [
    ["Rota", event.path],
    ["IP", event.ip],
    ["Cliente (navegador)", event.user_agent],
    ["ID da requisição", event.request_id],
  ];

  return (
    <Drawer anchor="right" open onClose={onClose} slotProps={{ paper: { sx: { width: { xs: "100%", sm: 560 }, p: 3 } } }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
        <Typography variant="h6">Evento #{event.id}</Typography>
        <IconButton onClick={onClose} aria-label="Fechar painel de detalhes"><Close /></IconButton>
      </Stack>

      <VersionTimeline items={[item]} />
      {changes.length === 0 && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Nenhum campo com valor diferente registrado.</Typography>
      )}

      <Divider sx={{ my: 2.5 }} />
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="subtitle2" fontWeight={700}>Detalhes técnicos</Typography>
        <FormControlLabel
          control={<Switch size="small" checked={showJson} onChange={(_, checked) => setShowJson(checked)} />}
          label={<Typography variant="body2" color={colors.textSecondary}>JSON bruto</Typography>}
        />
      </Stack>
      <Box sx={{ display: "grid", gridTemplateColumns: "110px 1fr", rowGap: 0.75, columnGap: 1.5, mb: 1.5 }}>
        {technical.map(([label, value]) => (
          <Box key={label} sx={{ display: "contents" }}>
            <Typography variant="body2" color="text.secondary">{label}</Typography>
            <Typography variant="body2" sx={{ wordBreak: "break-all" }}>{value || "—"}</Typography>
          </Box>
        ))}
      </Box>
      {showJson && (
        <Box component="pre" sx={{ m: 0, p: 1.5, bgcolor: colors.paper, border: `1px solid ${colors.border}`, borderRadius: 1, fontSize: 12, overflow: "auto", maxHeight: 360 }}>
          {JSON.stringify({ before: event.before_json ?? null, after: event.after_json ?? null }, null, 2)}
        </Box>
      )}
    </Drawer>
  );
}

export default function Audit() {
  const apiRef = useGridApiRef();
  const [rows, setRows] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [moduleText, setModuleText] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({ page: 0, pageSize: PAGE_SIZE });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [activeFilters, setActiveFilters] = useState<Record<string, GridFilter>>({});
  const [context, setContext] = useState<{ mouseX: number; mouseY: number; field: string; value: string } | null>(null);
  const [headerContext, setHeaderContext] = useState<{ mouseX: number; mouseY: number; field: string } | null>(null);
  const [detail, setDetail] = useState<AuditDetail | null>(null);
  const queryState = useRef<{ offset: number; limit: number; sort?: { field: string; direction: "asc" | "desc" }; filters: GridFilter[] }>({ offset: 0, limit: PAGE_SIZE, filters: [] });
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
      setError("Não foi possível carregar a trilha de auditoria");
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
    query({ offset: 0, limit: PAGE_SIZE });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSearch = (value: string) => {
    setSearch(value);
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      query({ offset: 0 });
    }, 350);
  };

  /** Aplica (ou remove, com valor vazio) um filtro de coluna e volta para a 1ª página. */
  const applyFilter = (field: string, operator: GridFilterOp, value: string) => {
    setPaginationModel((current) => ({ ...current, page: 0 }));
    const filters = { ...activeFilters };
    if (value) filters[field] = { field, operator, value };
    else delete filters[field];
    setActiveFilters(filters);
    setFilterModel({ items: [] });
    query({ offset: 0, filters: Object.values(filters) });
  };

  const filteredHeader = (label: string, field: string) => (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ overflow: "hidden" }}>
      <Typography component="span" variant="inherit" noWrap>{label}</Typography>
      {activeFilters[field] && <Tooltip title="Esta coluna tem filtro ativo"><FilterAlt fontSize="small" color="primary" /></Tooltip>}
    </Stack>
  );

  const columns = useMemo<GridColDef[]>(() => [
    {
      field: "created_at", headerName: "Data e hora", renderHeader: () => filteredHeader("Data e hora", "created_at"),
      minWidth: 175, type: "dateTime",
      valueGetter: (_v, row: AuditLogItem) => row.created_at ? new Date(row.created_at) : null,
      valueFormatter: (value) => formatDateTime(value as string | Date | null),
    },
    {
      field: "username", headerName: "Usuário", renderHeader: () => filteredHeader("Usuário", "username"),
      minWidth: 140, valueGetter: (_v, row: AuditLogItem) => row.user?.username ?? "Sistema",
    },
    {
      field: "action", headerName: "Ação", renderHeader: () => filteredHeader("Ação", "action_label"),
      minWidth: 120, valueGetter: (_v, row: AuditLogItem) => row.action,
      renderCell: ({ row }: { row: AuditLogItem }) => {
        const meta = actionMeta(row.action);
        return <Chip size="small" label={meta.label} color={meta.chipColor} variant="outlined" />;
      },
    },
    { field: "module", headerName: "Módulo", renderHeader: () => filteredHeader("Módulo", "module"), minWidth: 150 },
    {
      field: "entity_label", headerName: "Objeto alterado", renderHeader: () => filteredHeader("Objeto alterado", "entity_label"),
      minWidth: 260, flex: 1, valueGetter: (_v, row: AuditLogItem) => entityText(row),
    },
    {
      field: "status_code", headerName: "Status", renderHeader: () => filteredHeader("Status", "status_code"),
      width: 90, valueGetter: (_v, row: AuditLogItem) => row.status_code ?? "—",
    },
    {
      field: "detail", headerName: "Detalhes", width: 90, sortable: false, filterable: false,
      align: "center", headerAlign: "center",
      renderCell: ({ row }: { row: AuditLogItem }) => (
        <Tooltip title="Abrir detalhes">
          <RowAction kind="view" label="Abrir detalhes" onClick={() => { void openDetail(row); }} />
        </Tooltip>
      ),
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [activeFilters]);

  const openDetail = async (row: AuditLogItem) => {
    try {
      setDetail(await getAuditLog(row.id));
    } catch {
      setError("Não foi possível carregar os detalhes do evento");
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
    const val = item.value instanceof Date ? item.value.toISOString() : String(item.value);
    applyFilter(item.field, opByMui[item.operator] || "equals", val);
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
    const allowed = new Set(["created_at", "username", "module", "action", "method", "entity_label", "entity_type", "status_code"]);
    if (allowed.has(field) && row) {
      setContext({ mouseX: event.clientX - 2, mouseY: event.clientY - 4, field: field === "action" ? "action_label" : field, value: valueForField(row, field) });
    }
  };

  const applyContextFilter = (operator: GridFilterOp) => {
    if (!context) return;
    applyFilter(context.field, operator, context.value);
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
    setFilterModel({ items: [] });
    setPaginationModel((current) => ({ ...current, page: 0 }));
    query({ offset: 0, filters: Object.values(filters) });
    setContext(null);
    setHeaderContext(null);
    if (!field || field === "action_label") setActionFilter("");
    if (!field || field === "module") setModuleText("");
  };

  const downloadCsv = (content: string, filename: string) => {
    const blob = new Blob(["﻿" + content], { type: "text/csv;charset=utf-8;" });
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
      const header = ["ID", "Data e hora", "Usuário", "Módulo", "Ação", "Método", "Objeto alterado", "Status"];
      const lines = [header.join(";"), ...seen.map((r) =>
        [r.id, r.created_at ? formatDateTime(r.created_at) : "", r.user?.username ?? "Sistema", r.module, r.action, r.method, entityText(r), r.status_code ?? ""]
          .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(";")
      )];
      downloadCsv(lines.join("\r\n"), `auditoria-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch {
      setError("Não foi possível exportar a auditoria");
    }
  };

  return (
    <Box sx={{ display: "grid", gap: 2.5 }}>
      <Box>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Auditoria</Typography>
        <Typography variant="body2" color="text.secondary">
          Quem fez o quê, quando e em qual registro. Clique em um evento para ver o antes e o depois campo a campo.
        </Typography>
      </Box>

      <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 } }}>
        <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} justifyContent="space-between" gap={2} sx={{ mb: 2 }}>
          <Box>
            <Typography variant="h6">Eventos</Typography>
            <Typography variant="caption" color="text.secondary">{total} registros</Typography>
          </Box>
          <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} alignItems={{ sm: "center" }}>
            <TextField
              size="small" placeholder="Filtrar por usuário…" value={search}
              onChange={(e) => onSearch(e.target.value)}
              slotProps={{ input: { startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment>, sx: { height: 40 } } }}
              sx={{ minWidth: 220 }}
            />
            <TextField
              size="small" placeholder="Módulo (ex.: forecast)" value={moduleText}
              onChange={(e) => setModuleText(e.target.value)}
              onBlur={() => applyFilter("module", "equals", moduleText.trim())}
              onKeyDown={(e) => { if (e.key === "Enter") applyFilter("module", "equals", moduleText.trim()); }}
              sx={{ minWidth: 180 }}
            />
            <Select
              size="small" displayEmpty value={actionFilter} sx={{ minWidth: 160, height: 40 }}
              onChange={(e) => { setActionFilter(e.target.value); applyFilter("action_label", "equals", e.target.value); }}
              renderValue={(value) => value ? actionMeta(value).label : "Qualquer ação"}
            >
              <MenuItem value="">Todas as ações</MenuItem>
              {Object.keys(ACTION_META).map((key) => (
                <MenuItem key={key} value={key}>{ACTION_META[key].label}</MenuItem>
              ))}
            </Select>
          </Stack>
        </Stack>

        {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
        <Box sx={{ height: 620, width: "100%" }} onContextMenu={onCellContextMenu}>
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
              border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper",
              "& .MuiDataGrid-columnHeaders": { bgcolor: "background.default", borderBottomColor: "divider" },
            }}
          />
        </Box>
      </Paper>

      <Menu open={context !== null} onClose={() => setContext(null)} anchorReference="anchorPosition"
        anchorPosition={context ? { top: context.mouseY, left: context.mouseX } : undefined}>
        <MenuItem onClick={() => applyContextFilter("equals")}>Exibir valores iguais</MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>Exibir valores diferentes</MenuItem>
        <MenuItem disabled={!context || !activeFilters[context.field]} onClick={() => clearFilter(context?.field)}>Remover filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Remover todos os filtros</MenuItem>
      </Menu>
      <Menu open={headerContext !== null} onClose={() => setHeaderContext(null)} anchorReference="anchorPosition"
        anchorPosition={headerContext ? { top: headerContext.mouseY, left: headerContext.mouseX } : undefined}>
        <MenuItem onClick={() => { const field = headerContext?.field; setHeaderContext(null); if (field) apiRef.current.showColumnMenu(field); }}>Configurar coluna</MenuItem>
        <MenuItem onClick={() => { void exportFiltered(); }}>Exportar o que está na tela</MenuItem>
        <Divider />
        <MenuItem disabled={!headerContext || !activeFilters[headerContext.field]} onClick={() => clearFilter(headerContext?.field)}>Remover filtro da coluna</MenuItem>
        <MenuItem disabled={Object.keys(activeFilters).length === 0} onClick={() => clearFilter()}>Remover todos os filtros</MenuItem>
      </Menu>

      <AuditDetailPanel event={detail} onClose={() => setDetail(null)} />
    </Box>
  );
}
