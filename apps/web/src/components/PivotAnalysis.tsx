import { DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Autocomplete, Box, Button, Chip, Divider, Drawer, FormControl, IconButton, InputLabel,
  MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { DataGrid, GridColDef, GridPaginationModel } from "@mui/x-data-grid";
import { Add, Close, Delete, Download, PlayArrow, Save, TableChart, Visibility } from "@mui/icons-material";
import Plot from "react-plotly.js";
import * as XLSX from "xlsx";
import {
  DestinationAnalysis, PivotFilter, PivotMeasure, PivotMetadata, PivotRequest, PivotResult, PivotView,
  deletePivotView, drillDownPivot, getPivotMetadata, getPivotViews, runPivot, runPivotUnion, savePivotView,
  LakehouseSearchResult, searchPivotTable, formatApiError, FORECAST_MANUAL_PIVOT_ID,
} from "../services/api";

interface Props {
  destinations: DestinationAnalysis[];
  initialTableId?: number;
  showSearch?: boolean;
  initialMeasure?: string;
  initialFilters?: PivotFilter[];
  autoRun?: boolean;
}

const money = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const key = (values: unknown[]) => JSON.stringify(values);
const formatPeriod = (value: unknown) => {
  const digits = String(value ?? "").replace(/\D/g, "");
  if (digits.length >= 6) return `${digits.slice(4, 6)}/${digits.slice(0, 4)}`;
  return value == null || value === "" ? "(vazio)" : String(value);
};
const isPeriodDim = (name?: string) => ["_billing_period", "period", "forecast_month"].includes((name || "").toLowerCase());
const formatKey = (values: unknown[], dimNames?: string[]) => values.map((v, index) => {
  if (isPeriodDim(dimNames?.[index]) || String(v ?? "").replace(/\D/g, "").length === 6) return formatPeriod(v);
  return v == null || v === "" ? "(vazio)" : String(v);
}).join(" › ");

const ORIGIN_FIELD = { name: "origem", type: "varchar", numeric: false };
const isOriginDim = (name?: string) => (name || "").toLowerCase() === "origem";
const measureLabel = (m: PivotMeasure) => m.label || `${m.aggregation} ${m.column}`;
const dimLabel = (name: string, value: unknown) => {
  if (isPeriodDim(name) || String(value ?? "").replace(/\D/g, "").length === 6) return formatPeriod(value);
  return value == null || value === "" ? "(vazio)" : String(value);
};

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function downloadCsv(result: PivotResult) {
  const headers = [...result.rows, ...result.columns, ...result.measures.map(measureLabel)];
  const lines = [headers, ...result.cells.map(cell => [...cell.row, ...cell.column, ...cell.values])]
    .map(row => row.map(value => `"${String(value ?? "").replace(/"/g, '""')}"`).join(","));
  downloadBlob(new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" }), `pivot_${new Date().toISOString().slice(0, 10)}.csv`);
}

function pivotMatrix(result: PivotResult) {
  const rowKeys = result.row_keys || [];
  const colKeys = result.column_keys?.length ? result.column_keys : [[]];
  const cells = new Map((result.cells || []).map(cell => [`${key(cell.row)}|${key(cell.column)}`, cell.values]));
  const header = [
    ...result.rows,
    ...colKeys.flatMap(col => result.measures.map(m => `${formatKey(col, result.columns)} — ${measureLabel(m)}`)),
    ...result.measures.map(m => result.measures.length > 1 ? `Total — ${measureLabel(m)}` : "Total"),
  ];
  const body = rowKeys.map(row => [
    ...row.map((value, index) => dimLabel(result.rows[index], value)),
    ...colKeys.flatMap(col => {
      const values = cells.get(`${key(row)}|${key(col)}`) || result.measures.map(() => null);
      return values.map(value => (typeof value === "number" ? value : null));
    }),
    ...(result.totals?.rows[key(row)] || result.measures.map(() => null)).map(value => (typeof value === "number" ? value : null)),
  ]);
  const totals = result.totals
    ? [[
      "TOTAL",
      ...result.rows.slice(1).map(() => ""),
      ...colKeys.flatMap(col => (result.totals?.columns[key(col)] || result.measures.map(() => null)).map(value => (typeof value === "number" ? value : null))),
      ...(result.totals.grand || []).map(value => (typeof value === "number" ? value : null)),
    ]]
    : [];
  return [header, ...body, ...totals];
}

function downloadExcel(result: PivotResult) {
  const matrix = pivotMatrix(result).map(row => row.map(value => (value == null ? "" : value)));
  const sheet = XLSX.utils.aoa_to_sheet(matrix);
  const range = XLSX.utils.decode_range(sheet["!ref"] || "A1");
  for (let row = range.s.r; row <= range.e.r; row++) {
    for (let col = range.s.c; col <= range.e.c; col++) {
      const cell = sheet[XLSX.utils.encode_cell({ r: row, c: col })];
      if (cell && typeof cell.v === "number") cell.z = "#,##0.00";
    }
  }
  sheet["!views"] = [{ state: "frozen", ySplit: 1, topLeftCell: "A2", activePane: "bottomLeft" }];
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, sheet, "Pivot");
  XLSX.writeFile(workbook, `pivot_${new Date().toISOString().slice(0, 10)}.xlsx`);
}

export default function PivotAnalysis({ destinations, initialTableId, showSearch = false, initialMeasure, initialFilters = [], autoRun = false }: Props) {
  const first = initialTableId || destinations.filter(d => d.id > 0)[0]?.id || destinations[0]?.id || 0;
  const [tableId, setTableId] = useState(first);
  const [sourceIds, setSourceIds] = useState<number[]>([first]);
  const [meta, setMeta] = useState<PivotMetadata | null>(null);
  const [rows, setRows] = useState<string[]>([]);
  const [cols, setCols] = useState<string[]>([]);
  const [measures, setMeasures] = useState<PivotMeasure[]>([]);
  const [filters, setFilters] = useState<PivotFilter[]>(initialFilters);
  const [didAutoRun, setDidAutoRun] = useState(false);
  const [field, setField] = useState("");
  const [result, setResult] = useState<PivotResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [views, setViews] = useState<PivotView[]>([]);
  const [viewName, setViewName] = useState("");
  const [drill, setDrill] = useState<{ columns: string[]; rows: Record<string, unknown>[]; total: number; limit: number; offset: number } | null>(null);
  const [drillQuery, setDrillQuery] = useState<{ destId: number; cellFilters: Record<string, unknown> } | null>(null);
  const [drillPage, setDrillPage] = useState({ page: 0, pageSize: 100 });
  const [drillLoading, setDrillLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResult, setSearchResult] = useState<LakehouseSearchResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [chartType, setChartType] = useState<"bar" | "line" | "pie" | "area">("bar");
  const [chartMeasure, setChartMeasure] = useState(0);

  const [drag, setDrag] = useState<{ area: "rows" | "cols" | "filters"; name: string } | null>(null);
  const [dragOverZone, setDragOverZone] = useState<"rows" | "cols" | "filters" | null>(null);
  const dropIndexRef = useRef<number | null>(null);

  const reorderStrings = (list: string[], name: string, targetIndex: number | null) => {
    const from = list.indexOf(name);
    if (from < 0 || targetIndex == null || from === targetIndex) return list;
    const next = list.filter(item => item !== name);
    const insertAt = from < targetIndex ? targetIndex - 1 : targetIndex;
    next.splice(insertAt, 0, name);
    return next;
  };
  const reorderFilters = (list: PivotFilter[], name: string, targetIndex: number | null) => {
    const from = list.findIndex(item => item.column === name);
    if (from < 0 || targetIndex == null || from === targetIndex) return list;
    const next = list.filter(item => item.column !== name);
    const insertAt = from < targetIndex ? targetIndex - 1 : targetIndex;
    next.splice(insertAt, 0, list[from]);
    return next;
  };
  const startDrag = (area: "rows" | "cols" | "filters", name: string, e: DragEvent<HTMLDivElement>) => {
    setDrag({ area, name });
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", name); } catch { /* ignore */ }
  };
  const dropField = (target: "rows" | "cols" | "filters") => {
    if (!drag) return;
    const source = drag.area;
    const name = drag.name;
    const targetIndex = dropIndexRef.current;
    dropIndexRef.current = null;
    setDrag(null);
    if (source === target) {
      if (target === "rows") setRows(reorderStrings(rows, name, targetIndex));
      else if (target === "cols") setCols(reorderStrings(cols, name, targetIndex));
      else setFilters(reorderFilters(filters, name, targetIndex));
      return;
    }
    const nextRows = rows.filter(item => item !== name);
    const nextCols = cols.filter(item => item !== name);
    const nextFilters = filters.filter(item => item.column !== name);
    const insertAt = targetIndex == null ? Number.POSITIVE_INFINITY : targetIndex;
    if (target === "rows") {
      if (!nextRows.includes(name)) nextRows.splice(insertAt, 0, name);
    } else if (target === "cols") {
      if (!nextCols.includes(name)) nextCols.splice(insertAt, 0, name);
    } else {
      if (!nextFilters.some(item => item.column === name)) {
        nextFilters.splice(insertAt, 0, { column: name, values: [], operator: "IN" as PivotFilter["operator"] });
      }
    }
    setRows(nextRows);
    setCols(nextCols);
    setFilters(nextFilters);
  };

  const request = useMemo<PivotRequest>(() => ({ destination_table_id: tableId, rows, columns: cols, measures, filters, row_limit: 500, column_limit: 60, include_totals: true }), [tableId, rows, cols, measures, filters]);

  // Destination metadata is loaded asynchronously by the Analysis page. If
  // the user opens Pivot before that request completes, initialise as soon as
  // the table list arrives instead of leaving the screen on table ID zero.
  useEffect(() => {
    if (!tableId && destinations.length) {
      const selectedId = initialTableId || destinations.filter(d => d.id > 0)[0]?.id || destinations[0].id;
      setTableId(selectedId);
      setSourceIds([selectedId]);
    }
  }, [tableId, destinations, initialTableId]);

  useEffect(() => {
    if (!tableId) return;
    setMeta(null); setResult(null); setError("");
    getPivotMetadata(tableId).then(next => {
      setMeta(next);
      const dimensions = new Set(next.dimensions);
      const isForecastManual = tableId === FORECAST_MANUAL_PIVOT_ID;
      const isPlanning = tableId < 0 && dimensions.has("period") && dimensions.has("tipo") && next.measures.includes("value");
      if (isPlanning) {
        // Planning is a compact monthly table, not the detailed Gold fact.
        // Its useful default is immediately comparable realized × forecast.
        // Forecast manual is a single-tipo planning source: default to the
        // department hierarchy × future periods instead of the tipo axis.
        setRows(isForecastManual ? ["departamento"] : ["tipo"]);
        setCols(["period"]);
        setMeasures([{ column: "value", aggregation: "SUM" }]);
        return;
      }
      setRows(current => { const valid = current.filter(item => dimensions.has(item)); return valid.length ? valid : next.recommended_dimensions.filter(item => item !== "_billing_period" && !isOriginDim(item)).slice(0, 3); });
      setCols(current => { const valid = current.filter(item => dimensions.has(item)); return valid.length ? valid : (dimensions.has("_billing_period") ? ["_billing_period"] : (dimensions.has("period") ? ["period"] : [])); });
      const known = new Set(next.columns.map(column => column.name));
      setFilters(current => current.filter(item => known.has(item.column)));
      const wanted = [initialMeasure, "realizado", next.default_measure].find(name => name && next.measures.includes(name));
      setMeasures(wanted ? [{ column: wanted, aggregation: "SUM" }] : []);
    }).catch(e => setError(formatApiError(e, "Não foi possível ler o schema Gold pelo Trino")));
    // Views are global to the workspace. Filtering only by the current table
    // made a saved dashboard disappear when the user opened another table.
    getPivotViews().then(setViews).catch(() => setViews([]));
  }, [tableId]);

  const execute = async () => {
    if (!tableId || !measures.length) return;
    // A single active source may be the planning pseudo-destination (negative
    // id): pivot that table directly instead of the Gold table, otherwise a
    // forecast-only view would query the realized Gold and never show forecast.
    const pivotTarget = sourceIds.length === 1 ? sourceIds[0] : tableId;
    let schema = meta;
    if (pivotTarget !== tableId) {
      try { schema = await getPivotMetadata(pivotTarget); } catch { schema = meta; }
    }
    const known = new Set((schema?.columns || []).map(column => column.name));
    const originAllowed = sourceIds.length > 1;
    const allowOrigin = (name: string) => known.has(name) || (originAllowed && isOriginDim(name));
    // Planning tables expose the month as ``period`` while Gold uses
    // ``_billing_period``; the backend aliases either spelling, so keep period
    // names even when they are not in the target schema.
    const isPeriodName = (name: string) => ["_billing_period", "period", "forecast_month"].includes((name || "").toLowerCase());
    const safeRows = known.size ? rows.filter(name => allowOrigin(name) || isPeriodName(name)) : rows;
    const safeCols = known.size ? cols.filter(name => allowOrigin(name) || isPeriodName(name)) : cols;
    const safeFilters = known.size ? filters.filter(item => known.has(item.column) || isPeriodName(item.column)) : filters;
    const safeMeasures = known.size ? measures.filter(item => known.has(item.column)) : measures;
    if (!safeMeasures.length) return;
    setLoading(true); setError("");
    try {
      setResult(sourceIds.length > 1
        ? await runPivotUnion({ destination_table_ids: sourceIds, rows: safeRows, columns: safeCols, measures: safeMeasures, filters: safeFilters, row_limit: 500, column_limit: 60, include_totals: true, origin_column: "origem" })
        : await runPivot({ destination_table_id: pivotTarget, rows: safeRows, columns: safeCols, measures: safeMeasures, filters: safeFilters, row_limit: 500, column_limit: 60, include_totals: true }));
    }
    catch (e: unknown) { setError(formatApiError(e, "Falha ao consultar o Trino")); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (!autoRun || didAutoRun || !meta || !tableId || !measures.length) return;
    setDidAutoRun(true);
    void execute();
  }, [autoRun, didAutoRun, meta, tableId, measures, rows, cols, filters, sourceIds]);
  const executeSearch = async () => {
    if (!tableId || !searchTerm.trim()) return;
    setSearchLoading(true); setError("");
    try { setSearchResult(await searchPivotTable(tableId, searchTerm, 100, 0, filters)); }
    catch (e: unknown) { setError(formatApiError(e, "Falha ao pesquisar no Trino")); }
    finally { setSearchLoading(false); }
  };

  const addField = (target: "rows" | "cols" | "filters") => {
    if (!field) return;
    if (target === "rows" && !rows.includes(field) && !cols.includes(field)) setRows([...rows, field]);
    if (target === "cols" && !rows.includes(field) && !cols.includes(field)) setCols([...cols, field]);
    if (target === "filters" && !filters.some(f => f.column === field)) setFilters([...filters, { column: field, values: [], operator: "IN" }]);
  };
  const removeField = (target: "rows" | "cols" | "filters", name: string) => {
    if (target === "rows") setRows(rows.filter(item => item !== name));
    if (target === "cols") setCols(cols.filter(item => item !== name));
    if (target === "filters") setFilters(filters.filter(item => item.column !== name));
  };
  const saveView = async () => {
    if (!viewName.trim()) return;
    const validSourceIds = sourceIds.filter(id => id !== 0);
    try { await savePivotView(viewName.trim(), { ...request, source_ids: validSourceIds.length ? validSourceIds : [tableId] } as PivotRequest & { perspective_config?: unknown }); setViews(await getPivotViews()); setViewName(""); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Não foi possível salvar a visão"); }
  };
  const loadView = (view: PivotView) => { setTableId(view.destination_table_id); const sourceIds = (view.config as PivotView["config"] & { source_ids?: number[] }).source_ids?.filter(id => id !== 0) || []; setSourceIds(sourceIds.length ? sourceIds : [view.destination_table_id]); setRows(view.config.rows); setCols(view.config.columns); setMeasures(view.config.measures); setFilters(view.config.filters); };
  const loadDrill = async (
    destId: number,
    cellFilters: Record<string, unknown>,
    pagination: GridPaginationModel,
  ) => {
    const originName = (result?.origin_column || "origem").toLowerCase();
    const payloadRows = request.rows.filter(name => name.toLowerCase() !== originName);
    const payloadCols = request.columns.filter(name => name.toLowerCase() !== originName);
    const data = await drillDownPivot({
      ...request,
      destination_table_id: destId,
      rows: payloadRows,
      columns: payloadCols,
      cell_filters: cellFilters,
      limit: pagination.pageSize,
      offset: pagination.page * pagination.pageSize,
    });
    setDrill(data);
  };

  const openDrill = async (rowKey: unknown[], colKey: unknown[]) => {
    const originName = result?.origin_column || "origem";
    const cellFilters: Record<string, unknown> = {};
    let originValue: unknown;
    result?.rows.forEach((name, index) => {
      if (name.toLowerCase() === originName.toLowerCase()) originValue = rowKey[index];
      else cellFilters[name] = rowKey[index] as string;
    });
    result?.columns.forEach((name, index) => {
      if (name.toLowerCase() === originName.toLowerCase()) originValue = colKey[index];
      else cellFilters[name] = colKey[index] as string;
    });
    const matched = result?.tables?.find(table => table.origin === originValue);
    // Without a union, the cell belongs to the single active source, which may
    // be the planning pseudo-destination (negative id).
    const destId = matched?.id ?? (sourceIds.length === 1 ? sourceIds[0] : tableId);
    const pagination = { page: 0, pageSize: 100 };
    setDrillQuery({ destId, cellFilters });
    setDrillPage(pagination);
    setDrillLoading(true);
    try { await loadDrill(destId, cellFilters, pagination); }
    catch (e: unknown) { setError(formatApiError(e, "Falha no drill-down")); }
    finally { setDrillLoading(false); }
  };

  const onDrillPageChange = async (model: GridPaginationModel) => {
    if (!drillQuery) return;
    setDrillPage(model);
    setDrillLoading(true);
    try { await loadDrill(drillQuery.destId, drillQuery.cellFilters, model); }
    catch (e: unknown) { setError(formatApiError(e, "Falha no drill-down")); }
    finally { setDrillLoading(false); }
  };

  const drillColumns: GridColDef[] = useMemo(() => {
    const schema = meta?.columns || [];
    const names = (drill?.columns?.length ? drill.columns : schema.map(column => column.name))
      .filter(name => name.toLowerCase() !== "origem");
    const byName = new Map(schema.map(column => [column.name, column]));
    return names.map(name => ({
      field: name,
      headerName: name,
      minWidth: 140,
      flex: 1,
      type: byName.get(name)?.numeric ? "number" : "string",
    }));
  }, [drill, meta]);

  const rowKeys = result?.row_keys || [];
  const colKeys = result?.column_keys || [[]];
  const cells = useMemo(() => new Map((result?.cells || []).map(cell => [`${key(cell.row)}|${key(cell.column)}`, cell.values])), [result]);
  const totalFor = (axis: "rows" | "columns", values: unknown[]) => result?.totals?.[axis][key(values)] || [];
  const fieldOptions = useMemo(() => {
    const columns = meta?.columns || [];
    if (sourceIds.length > 1 && !columns.some(column => isOriginDim(column.name))) {
      return [ORIGIN_FIELD, ...columns];
    }
    return columns;
  }, [meta, sourceIds]);
  const chart = useMemo(() => {
    if (!result || !rowKeys.length || !result.measures.length) return null;
    const measureIndex = Math.min(chartMeasure, result.measures.length - 1);
    const temporal = result.columns.some(name => ["_billing_period", "period", "forecast_month"].includes(name.toLowerCase()));
    const labels = temporal ? colKeys.map(col => formatKey(col, result.columns)) : rowKeys.map(row => formatKey(row, result.rows));
    const values = temporal
      ? colKeys.map(col => (cells.get(`${key(rowKeys[0])}|${key(col)}`)?.[measureIndex] ?? 0) as number)
      : rowKeys.map(row => totalFor("rows", row)[measureIndex] ?? 0);
    const measure = result.measures[measureIndex];
    return { labels, values, measure };
  }, [result, rowKeys, chartMeasure]);
  const chartData: any[] = chart && result ? (chartType === "pie"
    ? [{ type: "pie", labels: chart.labels, values: chart.values, hole: 0.35 }]
    : [{ type: chartType === "area" ? "scatter" : chartType, x: chart.labels, y: chart.values, name: chart.measure.label || chart.measure.column, fill: chartType === "area" ? "tozeroy" : undefined, mode: chartType === "line" ? "lines+markers" : undefined, marker: { color: "#0A8FDC" } }]) : [];
  const chartLayout: any = chart && result ? { autosize: true, margin: { l: 60, r: 20, t: 30, b: 90 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", xaxis: { title: result.rows.join(" / ") || "Categoria", tickangle: -35 }, yaxis: { title: chart.measure.label || chart.measure.column, tickformat: ",.2f" }, showlegend: chartType === "pie" } : {};

  return <Box>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Paper sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="center">
        <FormControl size="small" sx={{ minWidth: 250 }}><InputLabel>Tabela Gold</InputLabel><Select value={tableId || ""} label="Tabela Gold" onChange={e => { const id = Number(e.target.value); setTableId(id); setSourceIds([id]); }}>{[...destinations.filter(d => d.id > 0), ...destinations.filter(d => d.id <= 0)].map(d => <MenuItem key={d.id} value={d.id}>{d.name}{d.id < 0 ? " · análise" : ""}</MenuItem>)}</Select></FormControl>
        <Box sx={{ display: "flex", alignItems: "center", gap: .5, flexWrap: "wrap" }}>
          <Typography variant="caption" color="text.secondary">Origem:</Typography>
          {destinations.filter(d => d.id === tableId || d.id === -Math.abs(tableId)).map(d => <Chip key={d.id} label={d.id < 0 ? "Forecast" : "Realizado"} color={sourceIds.includes(d.id) ? "primary" : "default"} variant={sourceIds.includes(d.id) ? "filled" : "outlined"} onClick={() => setSourceIds(current => {
            const next = current.includes(d.id) ? (current.length > 1 ? current.filter(id => id !== d.id) : current) : [...current, d.id];
            if (next.length < 2) {
              setRows(currentRows => currentRows.filter(name => !isOriginDim(name)));
              setCols(currentCols => currentCols.filter(name => !isOriginDim(name)));
              setFilters(currentFilters => currentFilters.filter(item => !isOriginDim(item.column)));
            }
            return next;
          })} />)}
          {sourceIds.length > 1 && <Typography variant="caption" color="text.secondary">Para separar realizado e forecast, adicione o campo <code>origem</code> em Linhas.</Typography>}
        </Box>
        <Autocomplete
          size="small"
          options={fieldOptions}
          value={fieldOptions.find(c => c.name === field) || null}
          onChange={(_, value) => setField(value?.name || "")}
          getOptionLabel={option => `${option.name} · ${option.type}`}
          isOptionEqualToValue={(option, value) => option.name === value.name}
          sx={{ minWidth: 300, flex: 1 }}
          renderInput={params => <TextField {...params} label="Pesquisar campo para arrastar/adicionar" placeholder="Digite o nome da coluna" />}
        />
        <Button startIcon={<Add />} onClick={() => addField("rows")} disabled={!field}>Linhas</Button>
        <Button startIcon={<Add />} onClick={() => addField("cols")} disabled={!field}>Colunas</Button>
        <Button startIcon={<Add />} onClick={() => addField("filters")} disabled={!field}>Filtro</Button>
        <Box flexGrow={1} />
        <Button variant="contained" startIcon={<PlayArrow />} onClick={execute} disabled={loading || !meta}>{loading ? "Consultando Trino..." : "Atualizar pivot"}</Button>
        <Button variant="outlined" startIcon={<Download />} disabled={!result} onClick={() => result && downloadCsv(result)}>CSV</Button>
        <Button variant="outlined" startIcon={<TableChart />} disabled={!result} onClick={() => result && downloadExcel(result)}>Excel</Button>
      </Stack>
      {showSearch && <Stack direction="row" gap={1} alignItems="center" sx={{ mt: 1.5 }}>
        <TextField size="small" fullWidth label="Buscar texto em todas as colunas" placeholder="Ex.: marketplace" value={searchTerm} onChange={e => setSearchTerm(e.target.value)} onKeyDown={e => { if (e.key === "Enter") executeSearch(); }} />
        <Button variant="outlined" startIcon={<Visibility />} onClick={executeSearch} disabled={searchLoading || !searchTerm.trim()}>{searchLoading ? "Buscando..." : "Buscar"}</Button>
      </Stack>}
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
        Arraste os campos para reordenar ou movê-los entre Linhas, Colunas e Filtros.
      </Typography>
      <Stack direction="row" gap={1.5} flexWrap="wrap">
        {(["rows", "cols", "filters"] as const).map(area => {
          const items = area === "rows" ? rows : area === "cols" ? cols : filters;
          const label = area === "rows" ? "LINHAS" : area === "cols" ? "COLUNAS" : "FILTROS";
          return (
            <Paper
              key={area}
              variant="outlined"
              sx={{
                p: 1,
                minWidth: 210,
                flex: 1,
                borderColor: dragOverZone === area ? "primary.main" : undefined,
                bgcolor: dragOverZone === area ? "action.hover" : undefined,
                transition: "border-color 0.15s",
              }}
              onDragOver={(e) => { e.preventDefault(); setDragOverZone(area); }}
              onDragLeave={() => setDragOverZone(prev => prev === area ? null : prev)}
              onDrop={(e) => { e.preventDefault(); setDragOverZone(null); dropField(area); }}
            >
              <Typography variant="caption" fontWeight={700}>{label}</Typography>
              <Stack direction="row" gap={0.5} flexWrap="wrap" mt={0.5}>
                {area === "filters"
                  ? filters.map((f, index) => (
                      <Box
                        key={f.column}
                        draggable
                        sx={{ cursor: "grab" }}
                        onDragStart={(e) => startDrag(area, f.column, e)}
                        onDragEnd={() => setDrag(null)}
                        onDragOver={(e) => e.preventDefault()}
                        onDragEnter={() => { dropIndexRef.current = index; }}
                      >
                        <Chip label={f.column} onDelete={() => removeField(area, f.column)} size="small" />
                      </Box>
                    ))
                  : (area === "rows" ? rows : cols).map((name, index) => (
                      <Box
                        key={name}
                        draggable
                        sx={{ cursor: "grab" }}
                        onDragStart={(e) => startDrag(area, name, e)}
                        onDragEnd={() => setDrag(null)}
                        onDragOver={(e) => e.preventDefault()}
                        onDragEnter={() => { dropIndexRef.current = index; }}
                      >
                        <Chip label={name} onDelete={() => removeField(area, name)} size="small" />
                      </Box>
                    ))}
                {items.length === 0 && <Typography variant="caption" color="text.secondary">Nenhum campo</Typography>}
              </Stack>
            </Paper>
          );
        })}
        <Paper variant="outlined" sx={{ p: 1, minWidth: 260, flex: 1 }}><Typography variant="caption" fontWeight={700}>VALORES</Typography>{measures.map((m, index) => <Stack key={index} direction="row" gap={.5} mt={.5}><Select size="small" value={m.column} onChange={e => setMeasures(measures.map((x, i) => i === index ? { ...x, column: e.target.value } : x))} sx={{ flex: 1 }}>{meta?.measures.map(name => <MenuItem key={name} value={name}>{name}</MenuItem>)}</Select><Select size="small" value={m.aggregation} onChange={e => setMeasures(measures.map((x, i) => i === index ? { ...x, aggregation: e.target.value as PivotMeasure["aggregation"] } : x))}>{["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"].map(op => <MenuItem key={op} value={op}>{op}</MenuItem>)}</Select><IconButton size="small" onClick={() => setMeasures(measures.filter((_, i) => i !== index))}><Delete fontSize="small" /></IconButton></Stack>)}<Button size="small" startIcon={<Add />} onClick={() => meta?.default_measure && setMeasures([...measures, { column: meta.default_measure, aggregation: "SUM" }])}>Adicionar medida</Button></Paper>
      </Stack>
      {filters.map(filter => <Stack key={filter.column} direction="row" gap={1} alignItems="center" sx={{ mt: 1, mr: 1, flexWrap: "wrap" }}>
        <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel>Condição</InputLabel><Select label="Condição" value={filter.operator || "IN"} onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, operator: e.target.value as PivotFilter["operator"] } : f))}><MenuItem value="IN">É igual a</MenuItem><MenuItem value="NOT_IN">Não é</MenuItem><MenuItem value="CONTAINS">Contém</MenuItem><MenuItem value="NOT_CONTAINS">Não contém</MenuItem></Select></FormControl>
        <TextField
          size="small"
          label={`Valor do filtro: ${filter.column}`}
          placeholder="Digite o valor"
          helperText="Use valores separados por vírgula para selecionar mais de um"
          sx={{ minWidth: 330 }}
          value={filter.values.join(", ")}
          onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, values: e.target.value.split(",").map(v => v.trim()).filter(Boolean) } : f))}
        />
      </Stack>)}
      <Stack direction="row" gap={1} mt={2} alignItems="center" flexWrap="wrap"><TextField size="small" label="Salvar visão compartilhada" value={viewName} onChange={e => setViewName(e.target.value)} /><Button startIcon={<Save />} onClick={saveView}>Salvar</Button><Typography variant="caption" color="text.secondary">Dashboards salvos:</Typography>{views.map(view => <Chip key={view.id} label={view.name} color={view.destination_table_id === tableId ? "primary" : "default"} onClick={() => loadView(view)} onDelete={() => deletePivotView(view.id).then(() => getPivotViews().then(setViews))} />)}</Stack>
    </Paper>
    {result?.currency_added && <Alert severity="info" sx={{ mb: 2 }}>Currency foi adicionada às linhas para impedir a soma de moedas diferentes.</Alert>}
    {result?.period_auto_applied && <Alert severity="info" sx={{ mb: 2 }}>Sem período no eixo, o pivot restringiu Gold e forecast a {result.period_auto_applied}. Coloque <code>_billing_period</code> (ou <code>period</code>) em colunas para ver a série completa.</Alert>}
    {drillLoading && <Alert severity="info" sx={{ mb: 2 }}>Carregando drill-down...</Alert>}
    {result && <TableContainer component={Paper} sx={{ maxHeight: "60vh" }}><Table stickyHeader size="small"><TableHead><TableRow>{result.rows.map(name => <TableCell key={name}>{name}</TableCell>)}{colKeys.flatMap(col => result.measures.map(m => <TableCell key={`${key(col)}-${m.column}`}>{formatKey(col, result.columns)}<br /><small>{m.label || `${m.aggregation} ${m.column}`}</small></TableCell>))}<TableCell>Total</TableCell></TableRow></TableHead><TableBody>{rowKeys.map(row => <TableRow key={key(row)}>{row.map((value, i) => <TableCell key={i}>{dimLabel(result.rows[i], value)}</TableCell>)}{colKeys.flatMap(col => (cells.get(`${key(row)}|${key(col)}`) || result.measures.map(() => null)).map((value, i) => <TableCell key={`${key(col)}-${i}`} onClick={() => openDrill(row, col)} sx={{ cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}>{typeof value === "number" ? money.format(value) : "—"}</TableCell>))}<TableCell>{totalFor("rows", row).map(value => typeof value === "number" ? money.format(value) : "—").join(" / ")}</TableCell></TableRow>)}{result.totals && <TableRow sx={{ bgcolor: "action.hover" }}>{result.rows.map((_, i) => <TableCell key={i}>{i === 0 ? "TOTAL" : ""}</TableCell>)}{colKeys.flatMap(col => totalFor("columns", col).map((value, i) => <TableCell key={`${key(col)}-${i}`}>{typeof value === "number" ? money.format(value) : "—"}</TableCell>))}<TableCell>{result.totals.grand.map(value => typeof value === "number" ? money.format(value) : "—").join(" / ")}</TableCell></TableRow>}</TableBody></Table></TableContainer>}
    {chart && <Paper sx={{ mt: 2, p: 2 }}><Stack direction="row" alignItems="center" gap={1} flexWrap="wrap"><Typography variant="subtitle1" fontWeight={700}>Visualização</Typography><FormControl size="small" sx={{ minWidth: 150 }}><InputLabel>Tipo</InputLabel><Select label="Tipo" value={chartType} onChange={e => setChartType(e.target.value as typeof chartType)}><MenuItem value="bar">Barras</MenuItem><MenuItem value="line">Linhas</MenuItem><MenuItem value="area">Área</MenuItem><MenuItem value="pie">Pizza</MenuItem></Select></FormControl><FormControl size="small" sx={{ minWidth: 220 }}><InputLabel>Medida</InputLabel><Select label="Medida" value={chartMeasure} onChange={e => setChartMeasure(Number(e.target.value))}>{(result?.measures ?? []).map((m, i) => <MenuItem key={i} value={i}>{m.label || `${m.aggregation} ${m.column}`}</MenuItem>)}</Select></FormControl></Stack><Plot useResizeHandler style={{ width: "100%", height: 420 }} data={chartData} layout={chartLayout} config={{ responsive: true, displaylogo: false }} /></Paper>}
    {showSearch && searchResult && <Paper sx={{ mt: 2, p: 2 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="subtitle1">Resultados para “{searchTerm}”</Typography><Chip label={`${searchResult.total.toLocaleString("pt-BR")}${searchResult.total_is_estimate ? " exibidos (limite da busca)" : " registros encontrados"}`} size="small" /></Stack><Typography variant="caption" color="text.secondary">Pesquisa realizada em {searchResult.searched_columns?.length || 0} colunas textuais. As colunas encontradas aparecem destacadas.</Typography><TableContainer sx={{ maxHeight: "55vh", mt: 1 }}><Table stickyHeader size="small"><TableHead><TableRow>{searchResult.columns.map(column => <TableCell key={column}>{column}</TableCell>)}</TableRow></TableHead><TableBody>{searchResult.rows.map((row, index) => <TableRow key={index}>{searchResult.columns.map(column => <TableCell key={column} sx={searchResult.matched_columns[index]?.includes(column) ? { bgcolor: "#fff3cd", fontWeight: 700 } : undefined}>{String(row[column] ?? "")}</TableCell>)}</TableRow>)}</TableBody></Table></TableContainer></Paper>}
    <Drawer
      anchor="right"
      open={!!drill || drillLoading}
      onClose={() => { setDrill(null); setDrillQuery(null); }}
      PaperProps={{ sx: { width: "80vw", p: 2 } }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
        <Typography variant="h6">Detalhe ({(drill?.total || 0).toLocaleString("pt-BR")} registros)</Typography>
        <IconButton onClick={() => { setDrill(null); setDrillQuery(null); }}><Close /></IconButton>
      </Stack>
      <Box sx={{ height: "80vh", width: "100%" }}>
        <DataGrid
          rows={(drill?.rows || []).map((row, index) => ({ id: drillPage.page * drillPage.pageSize + index, ...row }))}
          columns={drillColumns}
          loading={drillLoading}
          disableRowSelectionOnClick
          pagination
          paginationMode="server"
          rowCount={drill?.total || 0}
          pageSizeOptions={[50, 100, 200]}
          paginationModel={drillPage}
          onPaginationModelChange={model => { void onDrillPageChange(model); }}
        />
      </Box>
    </Drawer>
  </Box>;
}
