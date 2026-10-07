import { DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Autocomplete, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Drawer, FormControl, IconButton, InputLabel,
  MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { DataGrid, GridColDef, GridPaginationModel } from "@mui/x-data-grid";
import { Add, Close, ContentCopy, Delete, Download, PlayArrow, Save, TableChart, Visibility } from "@mui/icons-material";
import * as XLSX from "xlsx";
import {
  DestinationAnalysis, PivotFilter, PivotMeasure, PivotMetadata, PivotRequest, PivotResult, PivotView, PivotSort,
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
  onOpenQuery?: (sql: string) => void;
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

export default function PivotAnalysis({ destinations, initialTableId, showSearch = false, initialMeasure, initialFilters = [], autoRun = false, onOpenQuery }: Props) {
  const first = initialTableId || destinations.filter(d => d.id > 0)[0]?.id || destinations[0]?.id || 0;
  const [tableId, setTableId] = useState(first);
  const [sourceIds, setSourceIds] = useState<number[]>([first]);
  const [meta, setMeta] = useState<PivotMetadata | null>(null);
  const [rows, setRows] = useState<string[]>([]);
  const [cols, setCols] = useState<string[]>([]);
  const [measures, setMeasures] = useState<PivotMeasure[]>([]);
  const [sortRows, setSortRows] = useState<PivotSort[]>([]);
  const [sortCols, setSortCols] = useState<PivotSort[]>([]);
  const [filters, setFilters] = useState<PivotFilter[]>(initialFilters);
  const [filterInputs, setFilterInputs] = useState<Record<string, string>>(() => Object.fromEntries(initialFilters.map(filter => [filter.column, filter.values.join(", ")])));
  const [didAutoRun, setDidAutoRun] = useState(false);
  const [field, setField] = useState("");
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [queryOpen, setQueryOpen] = useState(false);
  const [copiedQuery, setCopiedQuery] = useState(false);
  const [result, setResult] = useState<PivotResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [views, setViews] = useState<PivotView[]>([]);
  const [viewName, setViewName] = useState("");
  const [savedViewsOpen, setSavedViewsOpen] = useState(false);
  const [drill, setDrill] = useState<{ columns: string[]; rows: Record<string, unknown>[]; total: number; limit: number; offset: number } | null>(null);
  const [drillQuery, setDrillQuery] = useState<{ destId: number; cellFilters: Record<string, unknown> } | null>(null);
  const [drillPage, setDrillPage] = useState({ page: 0, pageSize: 100 });
  const [drillLoading, setDrillLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResult, setSearchResult] = useState<LakehouseSearchResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);

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
    const names = drag.name.split("\u001f").filter(Boolean);
    const name = names[0];
    const targetIndex = dropIndexRef.current;
    dropIndexRef.current = null;
    setDrag(null);
    if (names.length > 1 || ![...rows, ...cols, ...filters.map(item => item.column)].includes(name)) {
      addFieldsToArea(names, target);
      return;
    }
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

  const addFieldsToArea = (names: string[], target: "rows" | "cols" | "filters") => {
    if (target === "rows") {
      setRows(current => [...current, ...names.filter(name => !current.includes(name) && !cols.includes(name))]);
    } else if (target === "cols") {
      setCols(current => [...current, ...names.filter(name => !current.includes(name) && !rows.includes(name))]);
    } else {
      setFilters(current => [...current, ...names.filter(name => !current.some(item => item.column === name)).map(column => ({ column, values: [], operator: "IN" as PivotFilter["operator"] }))]);
    }
  };

  const dragAvailableFields = (names: string[], e: DragEvent<HTMLDivElement>) => {
    setDrag({ area: "rows", name: names.join("\u001f") });
    e.dataTransfer.effectAllowed = "copy";
    try { e.dataTransfer.setData("text/plain", names.join("\u001f")); } catch { /* ignore */ }
  };

  const generatedSql = useMemo(() => {
    const pivotTarget = sourceIds.length === 1 ? sourceIds[0] : tableId;
    const destination = destinations.find(item => item.id === pivotTarget) || destinations.find(item => item.id === tableId);
    const tableName = destination?.physical_name;
    if (!tableName || !measures.length) return "-- Selecione uma tabela e ao menos uma medida para gerar a query.";
    const quote = (identifier: string) => `"${identifier.replace(/"/g, '""')}"`;
    const dims = [...rows, ...cols].filter((name, index, all) => all.indexOf(name) === index && !isOriginDim(name));
    const dimensionSql = dims.map(quote);
    const measureSql = measures.map((measure, index) => {
      const aggregate = measure.aggregation === "COUNT_DISTINCT" ? "COUNT(DISTINCT" : measure.aggregation;
      const expression = measure.aggregation === "COUNT_DISTINCT" ? `${aggregate} ${quote(measure.column)})` : `${aggregate}(${quote(measure.column)})`;
      return `${expression} AS ${quote(measure.label || `${measure.aggregation.toLowerCase()}_${measure.column}_${index + 1}`)}`;
    });
    const selected = [...dimensionSql, ...measureSql].join(",\n  ");
    const clauses = filters.flatMap(filter => {
      const column = quote(filter.column);
      const values = filter.values || [];
      if (filter.operator === "IS_NULL") return [`${column} IS NULL`];
      if (filter.operator === "IS_NOT_NULL") return [`${column} IS NOT NULL`];
      if (!values.length) return [];
      const literals = values.map(value => `'${String(value).replace(/'/g, "''")}'`);
      if (filter.operator === "NOT_IN") return [`${column} NOT IN (${literals.join(", ")})`];
      if (filter.operator === "CONTAINS" || filter.operator === "NOT_CONTAINS") return [`${column} ${filter.operator === "NOT_CONTAINS" ? "NOT " : ""}LIKE ${literals.map(value => `'%${value.slice(1, -1)}%'`).join(" AND ")}`];
      return [`${column} IN (${literals.join(", ")})`];
    });
    const sourceName = tableName.replace(/-/g, "_").replace(/\./g, "_");
    const from = `iceberg.silver.${sourceName}`;
    const groupBy = dimensionSql.length ? `\nGROUP BY ${dimensionSql.join(", ")}` : "";
    const orderDims = [...rows, ...cols].filter((name, index, all) => all.indexOf(name) === index && !isOriginDim(name)).map(quote);
    const orderBy = orderDims.length ? `\nORDER BY ${orderDims.join(", ")}` : "";
    const where = clauses.length ? `\nWHERE ${clauses.join("\n  AND ")}` : "";
    return `SELECT\n  ${selected}\nFROM ${from}${where}${groupBy}${orderBy}\nLIMIT 500`;
  }, [sourceIds, tableId, destinations, measures, rows, cols, filters]);

  const request = useMemo<PivotRequest>(() => ({ destination_table_id: tableId, rows, columns: cols, measures, filters, row_limit: 500, column_limit: 60, include_totals: true, sort_rows: sortRows, sort_columns: sortCols }), [tableId, rows, cols, measures, filters, sortRows, sortCols]);

  const setAxisSort = (axis: "rows" | "columns", column: string, direction: PivotSort["direction"]) => {
    const setter = axis === "rows" ? setSortRows : setSortCols;
    setter(current => [...current.filter(item => item.column !== column), { column, direction }]);
  };
  const setMeasureSort = (index: number, direction: "none" | PivotSort["direction"] | "only_desc" | "only_asc") => {
    if (direction === "only_desc" || direction === "only_asc") {
      setSortRows([{ column: `m${index}`, direction: direction === "only_desc" ? "desc" : "asc" }]);
      setSortCols([]);
      return;
    }
    setSortRows(current => {
      const withoutMeasures = current.filter(item => !/^m\d+$/.test(item.column));
      return direction === "none" ? withoutMeasures : [...withoutMeasures, { column: `m${index}`, direction }];
    });
  };

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
        setRows(isForecastManual ? ["organograma"] : ["tipo"]);
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
        ? await runPivotUnion({ destination_table_ids: sourceIds, rows: safeRows, columns: safeCols, measures: safeMeasures, filters: safeFilters, row_limit: 500, column_limit: 60, include_totals: true, origin_column: "origem", sort_rows: sortRows, sort_columns: sortCols })
        : await runPivot({ destination_table_id: pivotTarget, rows: safeRows, columns: safeCols, measures: safeMeasures, filters: safeFilters, row_limit: 500, column_limit: 60, include_totals: true, sort_rows: sortRows, sort_columns: sortCols }));
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
    if (target === "rows") { setRows(rows.filter(item => item !== name)); setSortRows(sortRows.filter(item => item.column !== name)); }
    if (target === "cols") { setCols(cols.filter(item => item !== name)); setSortCols(sortCols.filter(item => item.column !== name)); }
    if (target === "filters") {
      setFilters(filters.filter(item => item.column !== name));
      setFilterInputs(current => { const next = { ...current }; delete next[name]; return next; });
    }
  };
  const saveView = async () => {
    if (!viewName.trim()) return;
    const validSourceIds = sourceIds.filter(id => id !== 0);
    try { await savePivotView(viewName.trim(), { ...request, source_ids: validSourceIds.length ? validSourceIds : [tableId] } as PivotRequest & { perspective_config?: unknown }); setViews(await getPivotViews()); setViewName(""); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Não foi possível salvar a visão"); }
  };
  const loadView = (view: PivotView) => { setTableId(view.destination_table_id); const config = view.config as PivotView["config"] & { source_ids?: number[]; sort_rows?: PivotSort[]; sort_columns?: PivotSort[] }; const sourceIds = config.source_ids?.filter(id => id !== 0) || []; setSourceIds(sourceIds.length ? sourceIds : [view.destination_table_id]); setRows(view.config.rows); setCols(view.config.columns); setMeasures(view.config.measures); setFilters(view.config.filters); setSortRows(config.sort_rows || []); setSortCols(config.sort_columns || []); };
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
      limit: Math.min(100, pagination.pageSize),
      offset: pagination.page * Math.min(100, pagination.pageSize),
    });
    setDrill(data);
  };

  const openDrill = async (rowKey: unknown[], colKey: unknown[]) => {
    const originName = result?.origin_column || "origem";
    const cellFilters: Record<string, unknown> = {};
    const normalizeCellValue = (value: unknown): unknown =>
      value === "(vazio)" || value === "" ? null : value;
    let originValue: unknown;
    result?.rows.forEach((name, index) => {
      const value = normalizeCellValue(rowKey[index]);
      if (name.toLowerCase() === originName.toLowerCase()) originValue = value;
      else cellFilters[name] = value;
    });
    result?.columns.forEach((name, index) => {
      const value = normalizeCellValue(colKey[index]);
      if (name.toLowerCase() === originName.toLowerCase()) originValue = value;
      else cellFilters[name] = value;
    });
    const matched = result?.tables?.find(table => table.origin === originValue);
    // Without a union, the cell belongs to the single active source, which may
    // be the planning pseudo-destination (negative id).
    const destId = matched?.id ?? (sourceIds.length === 1 ? sourceIds[0] : tableId);
    // When the pivot auto-restricted to the latest period (no period on axes),
    // carry that partition into drill-down so the detail scan stays bounded.
    const autoPeriod = result?.period_auto_applied;
    if (autoPeriod && /^\d{6}$/.test(String(autoPeriod))) {
      const periodCol = meta?.columns.find(column => isPeriodDim(column.name))?.name;
      if (periodCol && cellFilters[periodCol] == null) {
        cellFilters[periodCol] = autoPeriod;
      }
    }
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

  return <Box>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Paper sx={{ p: 2, mb: 2 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
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
        </Stack>
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
          <Autocomplete
          size="small"
          options={fieldOptions}
          value={fieldOptions.find(c => c.name === field) || null}
          onChange={(_, value) => {
            const name = value?.name || "";
            setField(name);
            if (name) setSelectedFields(current => current.includes(name) ? current : [...current, name]);
          }}
          getOptionLabel={option => `${option.name} · ${option.type}`}
          isOptionEqualToValue={(option, value) => option.name === value.name}
          sx={{ minWidth: 300, flex: "1 1 300px" }}
          renderInput={params => <TextField {...params} label="Pesquisar campo para arrastar/adicionar" placeholder="Digite o nome da coluna" />}
        />
        <Button startIcon={<Add />} onClick={() => addField("rows")} disabled={!field}>Linhas</Button>
        <Button startIcon={<Add />} onClick={() => addField("cols")} disabled={!field}>Colunas</Button>
        <Button startIcon={<Add />} onClick={() => addField("filters")} disabled={!field}>Filtro</Button>
        <Button variant="outlined" startIcon={<Visibility />} onClick={() => setQueryOpen(true)}>Ver query</Button>
          <Button variant="contained" startIcon={<PlayArrow />} onClick={execute} disabled={loading || !meta}>{loading ? "Consultando Trino..." : "Atualizar pivot"}</Button>
          <Button variant="outlined" startIcon={<Download />} disabled={!result} onClick={() => result && downloadCsv(result)}>CSV</Button>
          <Button variant="outlined" startIcon={<TableChart />} disabled={!result} onClick={() => result && downloadExcel(result)}>Excel</Button>
        </Stack>
      </Stack>
      {showSearch && <Stack direction="row" gap={1} alignItems="center" sx={{ mt: 1.5 }}>
        <TextField size="small" fullWidth label="Buscar texto em todas as colunas" placeholder="Ex.: marketplace" value={searchTerm} onChange={e => setSearchTerm(e.target.value)} onKeyDown={e => { if (e.key === "Enter") executeSearch(); }} />
        <Button variant="outlined" startIcon={<Visibility />} onClick={executeSearch} disabled={searchLoading || !searchTerm.trim()}>{searchLoading ? "Buscando..." : "Buscar"}</Button>
      </Stack>}
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
        Marque campos na lista e arraste um ou vários para Linhas, Colunas ou Filtros. Os campos nas áreas também podem ser reordenados.
      </Typography>
      <Paper variant="outlined" sx={{ p: 1, mb: 1.5 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1} flexWrap="wrap">
          <Typography variant="caption" fontWeight={700}>CAMPOS DISPONÍVEIS · {selectedFields.length}</Typography>
          <Stack direction="row" gap={0.5}>
            {selectedFields.length > 0 && <Button size="small" onClick={() => setSelectedFields([])}>Limpar seleção</Button>}
            <Button size="small" startIcon={<Add />} disabled={!selectedFields.length} onClick={() => addFieldsToArea(selectedFields, "rows")}>Linhas</Button>
            <Button size="small" startIcon={<Add />} disabled={!selectedFields.length} onClick={() => addFieldsToArea(selectedFields, "cols")}>Colunas</Button>
          </Stack>
        </Stack>
        <Stack direction="row" gap={0.5} flexWrap="wrap" mt={0.75}>
          {fieldOptions.filter(option => selectedFields.includes(option.name)).map(option => {
            const selected = selectedFields.includes(option.name);
            return <Chip key={option.name} size="small" label={`${option.name} · ${option.type}`} color={selected ? "primary" : "default"} variant={selected ? "filled" : "outlined"}
              onClick={() => setSelectedFields(current => selected ? current.filter(name => name !== option.name) : [...current, option.name])}
              draggable onDragStart={e => dragAvailableFields(selected ? selectedFields : [option.name], e)} onDragEnd={() => setDrag(null)} sx={{ cursor: "grab" }} />;
          })}
        </Stack>
      </Paper>
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
                      <Stack direction="row" alignItems="center" spacing={0.25}>
                        <Chip label={name} onDelete={() => removeField(area, name)} size="small" />
                        <Select
                          variant="standard"
                          value={(area === "rows" ? sortRows : sortCols).find(item => item.column === name)?.direction || "asc"}
                          onChange={e => setAxisSort(area === "rows" ? "rows" : "columns", name, e.target.value as PivotSort["direction"])}
                          sx={{ fontSize: 11, minWidth: 42 }}
                          title="Ordenação"
                        >
                          <MenuItem value="asc">ASC</MenuItem>
                          <MenuItem value="desc">DESC</MenuItem>
                        </Select>
                      </Stack>
                      </Box>
                    ))}
                {items.length === 0 && <Typography variant="caption" color="text.secondary">Nenhum campo</Typography>}
              </Stack>
            </Paper>
          );
        })}
        <Paper variant="outlined" sx={{ p: 1, minWidth: 260, flex: 1 }}><Typography variant="caption" fontWeight={700}>VALORES</Typography>{measures.map((m, index) => { const measureSort = sortRows.find(item => item.column === `m${index}`); const exclusive = sortRows.length === 1 && measureSort; const sortValue = exclusive ? (measureSort.direction === "desc" ? "only_desc" : "only_asc") : (measureSort?.direction || "none"); return <Stack key={index} direction="row" gap={.5} mt={.5}><Select size="small" value={m.column} onChange={e => setMeasures(measures.map((x, i) => i === index ? { ...x, column: e.target.value } : x))} sx={{ flex: 1 }}>{meta?.measures.map(name => <MenuItem key={name} value={name}>{name}</MenuItem>)}</Select><Select size="small" value={m.aggregation} onChange={e => setMeasures(measures.map((x, i) => i === index ? { ...x, aggregation: e.target.value as PivotMeasure["aggregation"] } : x))}>{["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"].map(op => <MenuItem key={op} value={op}>{op}</MenuItem>)}</Select><Select size="small" value={sortValue} onChange={e => setMeasureSort(index, e.target.value as "none" | PivotSort["direction"] | "only_desc" | "only_asc")} title="Ordenar linhas pelo valor"><MenuItem value="none">Sem ordem</MenuItem><MenuItem value="desc">Maior → menor</MenuItem><MenuItem value="asc">Menor → maior</MenuItem><MenuItem value="only_desc">Somente valor: maior → menor</MenuItem><MenuItem value="only_asc">Somente valor: menor → maior</MenuItem></Select><IconButton size="small" onClick={() => setMeasures(measures.filter((_, i) => i !== index))}><Delete fontSize="small" /></IconButton></Stack>; })}<Button size="small" startIcon={<Add />} onClick={() => meta?.default_measure && setMeasures([...measures, { column: meta.default_measure, aggregation: "SUM" }])}>Adicionar medida</Button></Paper>
      </Stack>
      {filters.map(filter => <Stack key={filter.column} direction="row" gap={1} alignItems="center" sx={{ mt: 1, mr: 1, flexWrap: "wrap" }}>
        <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel>Condição</InputLabel><Select label="Condição" value={filter.operator || "IN"} onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, operator: e.target.value as PivotFilter["operator"], values: ["IS_NULL", "IS_NOT_NULL"].includes(e.target.value) ? [] : f.values } : f))}><MenuItem value="IN">É igual a</MenuItem><MenuItem value="NOT_IN">Não é</MenuItem><MenuItem value="CONTAINS">Contém</MenuItem><MenuItem value="NOT_CONTAINS">Não contém</MenuItem><MenuItem value="IS_NULL">É vazio</MenuItem><MenuItem value="IS_NOT_NULL">Não é vazio</MenuItem></Select></FormControl>
        <TextField
          size="small"
          label={`Valor do filtro: ${filter.column}`}
          placeholder="Digite o valor"
          helperText="Use valores separados por vírgula para selecionar mais de um"
          sx={{ minWidth: 330 }}
          value={filterInputs[filter.column] ?? filter.values.join(", ")}
          disabled={filter.operator === "IS_NULL" || filter.operator === "IS_NOT_NULL"}
          onChange={e => {
            const input = e.target.value;
            setFilterInputs(current => ({ ...current, [filter.column]: input }));
            setFilters(filters.map(f => f.column === filter.column ? { ...f, values: input.split(",").map(v => v.trim()).filter(Boolean) } : f));
          }}
        />
      </Stack>)}
      <Stack direction="row" gap={1} mt={2} alignItems="center" flexWrap="wrap">
        <TextField size="small" label="Salvar visão compartilhada" value={viewName} onChange={e => setViewName(e.target.value)} />
        <Button startIcon={<Save />} onClick={saveView} disabled={!viewName.trim()}>Salvar</Button>
        <Button variant="outlined" onClick={() => setSavedViewsOpen(true)} disabled={!views.length}>
          Visões salvas ({views.length})
        </Button>
      </Stack>
    </Paper>
    {result?.currency_added && <Alert severity="info" sx={{ mb: 2 }}>Currency foi adicionada às linhas para impedir a soma de moedas diferentes.</Alert>}
    {result?.period_auto_applied && <Alert severity="info" sx={{ mb: 2 }}>Sem período no eixo, o pivot restringiu Gold e forecast a {result.period_auto_applied}. Coloque <code>_billing_period</code> (ou <code>period</code>) em colunas para ver a série completa.</Alert>}
    {drillLoading && <Alert severity="info" sx={{ mb: 2 }}>Carregando drill-down...</Alert>}
    {(result || loading) && <Box sx={{ position: "relative" }} aria-busy={loading}>{result && <TableContainer component={Paper} sx={{ maxHeight: "60vh", opacity: loading ? 0.45 : 1, transition: "opacity .15s" }}><Table stickyHeader size="small"><TableHead><TableRow>{result.rows.map(name => <TableCell key={name}>{name}</TableCell>)}{colKeys.flatMap(col => result.measures.map(m => <TableCell key={`${key(col)}-${m.column}`}>{formatKey(col, result.columns)}<br /><small>{m.label || `${m.aggregation} ${m.column}`}</small></TableCell>))}<TableCell>Total</TableCell></TableRow></TableHead><TableBody>{rowKeys.map(row => <TableRow key={key(row)}>{row.map((value, i) => <TableCell key={i}>{dimLabel(result.rows[i], value)}</TableCell>)}{colKeys.flatMap(col => (cells.get(`${key(row)}|${key(col)}`) || result.measures.map(() => null)).map((value, i) => <TableCell key={`${key(col)}-${i}`} onClick={() => openDrill(row, col)} sx={{ cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}>{typeof value === "number" ? money.format(value) : "—"}</TableCell>))}<TableCell>{totalFor("rows", row).map(value => typeof value === "number" ? money.format(value) : "—").join(" / ")}</TableCell></TableRow>)}{result.totals && <TableRow sx={{ bgcolor: "action.hover" }}>{result.rows.map((_, i) => <TableCell key={i}>{i === 0 ? "TOTAL" : ""}</TableCell>)}{colKeys.flatMap(col => totalFor("columns", col).map((value, i) => <TableCell key={`${key(col)}-${i}`}>{typeof value === "number" ? money.format(value) : "—"}</TableCell>))}<TableCell>{result.totals.grand.map(value => typeof value === "number" ? money.format(value) : "—").join(" / ")}</TableCell></TableRow>}</TableBody></Table></TableContainer>}{loading && <Box sx={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", bgcolor: "rgba(255,255,255,.55)", zIndex: 1 }}><Paper elevation={3} sx={{ px: 2, py: 1.5, display: "flex", alignItems: "center", gap: 1 }}><CircularProgress size={20} /><Typography variant="body2">Atualizando resultado do Pivot…</Typography></Paper></Box>}</Box>}
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
          pageSizeOptions={[50, 100]}
          paginationModel={drillPage}
          onPaginationModelChange={model => { void onDrillPageChange(model); }}
        />
      </Box>
    </Drawer>
    <Dialog open={savedViewsOpen} onClose={() => setSavedViewsOpen(false)} fullWidth maxWidth="sm">
      <DialogTitle>Escolher visão salva</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={1}>
          {views.map(view => (
            <Paper key={view.id} variant="outlined" sx={{ p: 1.25, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="body2" fontWeight={600} noWrap>{view.name}</Typography>
                <Typography variant="caption" color="text.secondary" noWrap>
                  {destinations.find(destination => destination.id === view.destination_table_id)?.name || "Tabela indisponível"}
                </Typography>
              </Box>
              <Stack direction="row" spacing={0.5} flexShrink={0}>
                <Button size="small" variant="contained" onClick={() => { loadView(view); setSavedViewsOpen(false); }}>Selecionar</Button>
                <IconButton size="small" color="error" aria-label={`Excluir visão ${view.name}`} onClick={() => deletePivotView(view.id).then(() => getPivotViews().then(setViews))}>
                  <Delete fontSize="small" />
                </IconButton>
              </Stack>
            </Paper>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={() => setSavedViewsOpen(false)}>Fechar</Button></DialogActions>
    </Dialog>
    <Dialog open={queryOpen} onClose={() => setQueryOpen(false)} fullWidth maxWidth="md">
      <DialogTitle>Query SQL do Pivot</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>A query consulta a tabela de detalhe na camada Silver, usada pelo Pivot, e agrega pelas dimensões e medidas configuradas.</Typography>
        <TextField fullWidth multiline minRows={8} value={generatedSql} InputProps={{ readOnly: true }} sx={{ "& textarea": { fontFamily: "monospace", fontSize: 13 } }} />
      </DialogContent>
      <DialogActions>
        <Button startIcon={<ContentCopy />} onClick={() => { void navigator.clipboard?.writeText(generatedSql).then(() => { setCopiedQuery(true); setQueryOpen(false); }); }}>{copiedQuery ? "Copiada" : "Copiar query"}</Button>
        {onOpenQuery && <Button variant="contained" startIcon={<PlayArrow />} onClick={() => { onOpenQuery(generatedSql); setQueryOpen(false); }}>Abrir no Iceberg / Trino</Button>}
        <Button onClick={() => setQueryOpen(false)}>Fechar</Button>
      </DialogActions>
    </Dialog>
  </Box>;
}
