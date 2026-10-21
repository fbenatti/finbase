import { Dialog, DialogActions, DialogContent, DialogTitle } from "./FinbaseDialog";
import DataGrid from "./FinbaseDataGrid";
import { dataDisplayText } from "../utils/dataPresentation";
import { DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Autocomplete, Box, Button, Chip, CircularProgress, Divider, Drawer, FormControl, IconButton, InputLabel, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { GridColDef, GridPaginationModel } from "@mui/x-data-grid";
import { Add, Close, CompareArrows, ContentCopy, Delete, Download, OpenInFull, PlayArrow, Save, TableChart, Visibility } from "./FinbaseIcons";
import * as XLSX from "xlsx";
import {
  DestinationAnalysis, PivotFilter, PivotMeasure, PivotMetadata, PivotRequest, PivotResult, PivotView, PivotSort,
  deletePivotView, drillDownPivot, getPivotMetadata, getPivotViews, runPivot, runPivotUnion, savePivotView,
  DataSearchResult, searchPivotTable, formatApiError, FORECAST_MANUAL_PIVOT_ID, getPivotSourceDetail, PivotSourceDetail,
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
const columnNameCompare = new Intl.Collator("pt-BR", { sensitivity: "base", numeric: true });
const key = (values: unknown[]) => JSON.stringify(values);
const formatPeriod = (value: unknown) => {
  const digits = String(value ?? "").replace(/\D/g, "");
  if (digits.length >= 6) return `${digits.slice(4, 6)}/${digits.slice(0, 4)}`;
  return value == null || value === "" ? "(vazio)" : String(value);
};
const isPeriodDim = (name?: string) => ["_billing_period", "period", "forecast_month"].includes((name || "").toLowerCase());
const formatKey = (values: unknown[], dimNames?: string[]) => values.map((v, index) => {
  if (isPeriodDim(dimNames?.[index])) return formatPeriod(v);
  return v == null || v === "" ? "(vazio)" : String(v);
}).join(" › ");

const ORIGIN_FIELD = { name: "origem", type: "varchar", numeric: false };
const isOriginDim = (name?: string) => (name || "").toLowerCase() === "origem";
const measureLabel = (m: PivotMeasure) => m.label || `${m.aggregation} ${m.column}`;
const dimLabel = (name: string, value: unknown) => {
  if (isPeriodDim(name)) return formatPeriod(value);
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
  const [selectedDrillRow, setSelectedDrillRow] = useState<Record<string, unknown> | null>(null);
  const [sourceDetail, setSourceDetail] = useState<PivotSourceDetail | null>(null);
  const [sourceDetailLoading, setSourceDetailLoading] = useState(false);
  const [relationDialogOpen, setRelationDialogOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResult, setSearchResult] = useState<DataSearchResult | null>(null);
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
      const isSharedCostSource = destinations.some(item => item.id === tableId && item.analysis_kind === "shared_cost");
      const isPlanning = tableId < 0 && dimensions.has("period") && dimensions.has("tipo") && next.measures.includes("value");
      if (isSharedCostSource) {
        setRows(["organograma_nivel_1", "organograma_nivel_2", "organograma_nivel_3", "organograma_nivel_4"].filter(name => dimensions.has(name)));
        setCols(dimensions.has("period") ? ["period"] : []);
        const measure = [initialMeasure, "realizado", next.default_measure].find(name => name && next.measures.includes(name));
        setMeasures(measure ? [{ column: measure, aggregation: "SUM" }] : []);
        return;
      }
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
    }).catch(e => setError(formatApiError(e, "Não foi possível carregar a estrutura da base consolidada")));
    // Views are global to the workspace. Filtering only by the current table
    // made a saved dashboard disappear when the user opened another table.
    getPivotViews().then(setViews).catch(() => setViews([]));
  }, [tableId]);

  const execute = async () => {
    if (!tableId || !measures.length) return;
    setLoading(true); setError("");
    try {
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
      setResult(sourceIds.length > 1
        ? await runPivotUnion({ destination_table_ids: sourceIds, rows: safeRows, columns: safeCols, measures: safeMeasures, filters: safeFilters, row_limit: 500, column_limit: 60, include_totals: true, origin_column: "origem", sort_rows: sortRows, sort_columns: sortCols })
        : await runPivot({ destination_table_id: pivotTarget, rows: safeRows, columns: safeCols, measures: safeMeasures, filters: safeFilters, row_limit: 500, column_limit: 60, include_totals: true, sort_rows: sortRows, sort_columns: sortCols }));
    }
    catch (e: unknown) { setError(formatApiError(e, "Falha ao consultar os dados")); }
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
    catch (e: unknown) { setError(formatApiError(e, "Falha ao pesquisar os dados")); }
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
      .filter(name => !["origem", "source_blob_path", "source_row_offset"].includes(name.toLowerCase()))
      .sort(columnNameCompare.compare);
    const byName = new Map(schema.map(column => [column.name, column]));
    return [
      {
        field: "_detail",
        headerName: "",
        sortable: false,
        filterable: false,
        width: 64,
        align: "center",
        headerAlign: "center",
        renderHeader: () => <OpenInFull fontSize="small" color="action" />,
        renderCell: params => <IconButton size="small" color="primary" aria-label="Abrir decomposição da linha" title="Abrir decomposição da linha" onClick={event => { event.stopPropagation(); openSelectedRow(params.row); }}><OpenInFull fontSize="small" /></IconButton>,
      },
      ...names.map(name => ({
        field: name,
        headerName: name,
        minWidth: 140,
        flex: 1,
        type: (byName.get(name)?.numeric ? "number" : "string") as GridColDef["type"],
        cellClassName: byName.get(name)?.enrichment === "financial"
          ? "pivot-enriched-financial"
          : byName.get(name)?.enrichment === "organization" ? "pivot-enriched-organization" : undefined,
        renderHeader: () => {
          const enrichment = byName.get(name)?.enrichment;
          const financial = enrichment === "financial";
          return <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 0 }}>
            <Typography variant="caption" fontWeight={700} noWrap>{name}</Typography>
            {enrichment && <Chip size="small" label={financial ? "Regra financeira" : "Organograma"} sx={{ height: 20, fontSize: 10, fontWeight: 700, bgcolor: financial ? "#fff1e6" : "#e8f3ff", color: financial ? "#b45309" : "#1d4ed8", "& .MuiChip-label": { px: 0.75 } }} />}
          </Stack>;
        },
      } as GridColDef)),
    ];
  }, [drill, meta]);

  const selectedRowEntries = useMemo(() => Object.entries(selectedDrillRow || {}).sort(([left], [right]) => columnNameCompare.compare(left, right)), [selectedDrillRow]);
  const selectedRowValuesByLowerName = useMemo(
    () => new Map(Object.entries(selectedDrillRow || {}).map(([name, value]) => [name.toLowerCase(), value])),
    [selectedDrillRow],
  );
  const parseJsonValue = (value: unknown): unknown => {
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) return value;
    try { return JSON.parse(trimmed); } catch { return value; }
  };
  const renderDetailValue = (value: unknown): React.ReactNode => {
    const parsed = parseJsonValue(value);
    if (parsed && typeof parsed === "object") {
      const entries = Array.isArray(parsed)
        ? parsed.map((item, index) => [String(index), item] as [string, unknown])
        : Object.entries(parsed as Record<string, unknown>).sort(([left], [right]) => columnNameCompare.compare(left, right));
      return <Stack spacing={0.75} sx={{ pl: 1.25, my: 0.5, borderLeft: "2px solid #c7d2fe" }}>{entries.map(([name, child]) => <Box key={name} sx={{ display: "grid", gridTemplateColumns: "minmax(120px, 30%) minmax(0, 1fr)", gap: 1, alignItems: "start", px: 1, py: 0.75, bgcolor: "#f8faff", borderRadius: 1.5 }}><Typography variant="caption" sx={{ color: "#4338ca", fontWeight: 700, fontFamily: '"SFMono-Regular", Consolas, monospace', overflowWrap: "anywhere" }}>{name}</Typography><Box sx={{ minWidth: 0 }}>{renderDetailValue(child)}</Box></Box>)}</Stack>;
    }
    if (parsed == null) return <Typography component="span" sx={{ color: "#94a3b8", fontFamily: '"SFMono-Regular", Consolas, monospace', fontSize: 13 }}>null</Typography>;
    if (typeof parsed === "boolean") return <Typography component="span" sx={{ color: parsed ? "#15803d" : "#be123c", fontWeight: 650, fontFamily: '"SFMono-Regular", Consolas, monospace', fontSize: 13 }}>{String(parsed)}</Typography>;
    if (typeof parsed === "number") return <Typography component="span" sx={{ color: "#0369a1", fontWeight: 650, fontFamily: '"SFMono-Regular", Consolas, monospace', fontSize: 13 }}>{String(parsed)}</Typography>;
    return <Typography component="span" variant="body2" sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", color: "#047857", fontFamily: '"SFMono-Regular", Consolas, monospace', fontSize: 13, lineHeight: 1.65 }}>{String(parsed)}</Typography>;
  };
  const renderDecomposedValue = (value: unknown): React.ReactNode => {
    const parsed = parseJsonValue(value);
    const isNull = parsed == null;
    const isNumeric = typeof parsed === "number";
    const label = isNull ? "null" : String(parsed);
    const palette = isNull
      ? { color: "#946c12", border: "#f3df9b", background: "#fff9e8" }
      : isNumeric
        ? { color: "#28754a", border: "#b9e3c9", background: "#effaf3" }
        : typeof parsed === "boolean"
          ? parsed
            ? { color: "#28754a", border: "#b9e3c9", background: "#effaf3" }
            : { color: "#b4233c", border: "#f2c2cb", background: "#fff1f3" }
          : { color: "#596273", border: "#d9dee7", background: "#f5f6f8" };
    return <Chip component="span" label={label} size="small" variant="outlined" sx={{ display: "inline-flex", verticalAlign: "middle", width: "fit-content", maxWidth: "100%", height: "auto", borderRadius: "999px", borderColor: palette.border, bgcolor: palette.background, color: palette.color, fontWeight: 650, lineHeight: 1.5, whiteSpace: "pre-wrap", overflowWrap: "anywhere", "& .MuiChip-label": { display: "block", px: 1.1, py: 0.4, whiteSpace: "inherit", overflowWrap: "anywhere" } }} />;
  };
  const renderBusinessRuleJson = (value: unknown): React.ReactNode => {
    const parsed = parseJsonValue(value);
    if (!parsed || typeof parsed !== "object") return renderDetailValue(value);
    const palettes = [
      { text: "#4338ca", border: "#c7d2fe", surface: "#f5f3ff" },
      { text: "#0369a1", border: "#bae6fd", surface: "#f0f9ff" },
      { text: "#b45309", border: "#fde68a", surface: "#fffbeb" },
    ];
    const renderValue = (item: unknown, depth = 0): React.ReactNode => {
      const current = parseJsonValue(item);
      const palette = palettes[depth % palettes.length];
      if (current && typeof current === "object") {
        const array = Array.isArray(current);
        const entries = array
          ? current.map((child, index) => [String(index), child] as [string, unknown])
          : Object.entries(current as Record<string, unknown>).sort(([left], [right]) => columnNameCompare.compare(left, right));
        if (!entries.length) return <Chip size="small" label={array ? "lista vazia" : "objeto vazio"} variant="outlined" sx={{ borderColor: palette.border, color: palette.text, bgcolor: palette.surface }} />;
        return <Stack spacing={0.75} sx={{ pl: depth ? 1.25 : 0, borderLeft: depth ? `2px solid ${palette.border}` : "none" }}>
          {entries.map(([name, child]) => {
            const childValue = parseJsonValue(child);
            const nested = childValue !== null && typeof childValue === "object";
            const childEntries = nested ? (Array.isArray(childValue) ? childValue : Object.values(childValue as Record<string, unknown>)) : [];
            return <Paper key={`${depth}-${name}`} variant="outlined" sx={{ overflow: "hidden", borderColor: nested ? palette.border : "#e2e8f0", borderRadius: 1.5, boxShadow: "none" }}>
              {nested ? <>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap", px: 1.25, py: 0.75, bgcolor: palette.surface }}>
                  <Chip size="small" label={array ? `#${Number(name) + 1}` : name} sx={{ height: 24, maxWidth: "100%", bgcolor: "white", color: palette.text, border: `1px solid ${palette.border}`, fontWeight: 700, fontFamily: '"Segoe UI", sans-serif', "& .MuiChip-label": { overflowWrap: "anywhere" } }} />
                  <Typography variant="caption" sx={{ color: "#64748b" }}>{Array.isArray(childValue) ? `${childEntries.length} ${childEntries.length === 1 ? "item" : "itens"}` : `${childEntries.length} ${childEntries.length === 1 ? "campo" : "campos"}`}</Typography>
                </Box>
                <Box sx={{ p: 1.25 }}>{renderValue(child, depth + 1)}</Box>
              </> : <Box sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "minmax(130px, 30%) minmax(0, 1fr)" }, gap: { xs: 0.5, sm: 1.25 }, alignItems: "center", px: 1.25, py: 0.9, bgcolor: "#fff" }}>
                <Chip size="small" label={array ? `#${Number(name) + 1}` : name} variant="outlined" sx={{ justifySelf: "start", height: 24, maxWidth: "100%", borderColor: palette.border, color: palette.text, bgcolor: palette.surface, fontWeight: 700, fontFamily: '"Segoe UI", sans-serif', "& .MuiChip-label": { overflowWrap: "anywhere" } }} />
                <Box sx={{ minWidth: 0 }}>{renderDetailValue(child)}</Box>
              </Box>}
            </Paper>;
          })}
        </Stack>;
      }
      return renderDetailValue(current);
    };
    return <Box sx={{ p: 1.25, border: "1px solid #e2e8f0", borderRadius: 2, bgcolor: "#fff", boxShadow: "0 2px 8px rgba(15,23,42,.04)" }}>{renderValue(parsed)}</Box>;
  };
  const renderDecomposedFieldValue = (name: string, value: unknown): React.ReactNode => {
    const parsed = parseJsonValue(value);
    if (name.toLowerCase() === "business_rule" || (parsed !== null && typeof parsed === "object")) {
      return renderBusinessRuleJson(value);
    }
    return renderDecomposedValue(value);
  };

  const openSelectedRow = (row: Record<string, unknown>) => {
    const cleanRow = { ...row };
    delete cleanRow.id;
    setSelectedDrillRow(cleanRow);
    setSourceDetail(null);
  };
  const loadSourceRelation = async () => {
    if (!selectedDrillRow) return;
    setRelationDialogOpen(true);
    setSourceDetailLoading(true);
    try { setSourceDetail(await getPivotSourceDetail(tableId, selectedDrillRow)); }
    catch (error: unknown) { setSourceDetail({ available: false, reason: formatApiError(error, "Não foi possível carregar o registro de origem.") }); }
    finally { setSourceDetailLoading(false); }
  };

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
  const selectedDestination = destinations.find(item => item.id === tableId);
  const selectedBaseId = selectedDestination?.base_destination_id || Math.abs(tableId);
  const selectedSourceOptions = destinations.filter(d =>
    d.id === selectedBaseId ||
    ((d.analysis_kind === "forecast" || d.analysis_kind === "shared_cost") && d.base_destination_id === selectedBaseId)
  );

  return <Box>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
    <Paper sx={{ p: 2, mb: 2 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
          <FormControl size="small" sx={{ minWidth: 250 }}><InputLabel>base consolidada</InputLabel><Select value={tableId || ""} label="base consolidada" onChange={e => { const id = Number(e.target.value); setTableId(id); setSourceIds([id]); }}>{[...destinations.filter(d => d.id > 0), ...destinations.filter(d => d.id <= 0)].map(d => <MenuItem key={d.id} value={d.id}>{d.name}{d.id < 0 ? " · análise" : ""}</MenuItem>)}</Select></FormControl>
          <Box sx={{ display: "flex", alignItems: "center", gap: .5, flexWrap: "wrap" }}>
          <Typography variant="caption" color="text.secondary">Origem:</Typography>
          {selectedSourceOptions.map(d => <Chip key={d.id} label={d.analysis_kind === "shared_cost" ? "Custo compartilhado" : d.id < 0 ? "Forecast" : "Realizado"} color={sourceIds.includes(d.id) ? "primary" : "default"} variant={sourceIds.includes(d.id) ? "filled" : "outlined"} onClick={() => setSourceIds(current => {
            const next = current.includes(d.id) ? (current.length > 1 ? current.filter(id => id !== d.id) : current) : [...current, d.id];
            if (next.length < 2) {
              setRows(currentRows => currentRows.filter(name => !isOriginDim(name)));
              setCols(currentCols => currentCols.filter(name => !isOriginDim(name)));
              setFilters(currentFilters => currentFilters.filter(item => !isOriginDim(item.column)));
            }
            return next;
          })} />)}
          {sourceIds.length > 1 && <Typography variant="caption" color="text.secondary">Para separar as fontes, adicione o campo <code>origem</code> em Linhas.</Typography>}
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
          <Button variant="contained" startIcon={<PlayArrow />} onClick={execute} disabled={loading || !meta}>{loading ? "Consultando dados..." : "Atualizar pivot"}</Button>
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
    {loading && (
      <Alert severity="info" icon={<CircularProgress size={18} />} sx={{ mb: 2 }}>

        Consultando os dados. O resultado aparecerá aqui quando estiver pronto.
      </Alert>
    )}
    {result?.currency_added && <Alert severity="info" sx={{ mb: 2 }}>Currency foi adicionada às linhas para impedir a soma de moedas diferentes.</Alert>}
    {result?.period_auto_applied && <Alert severity="info" sx={{ mb: 2 }}>Sem período no eixo, o pivot restringiu base consolidada e forecast a {result.period_auto_applied}. Coloque <code>_billing_period</code> (ou <code>period</code>) em colunas para ver a série completa.</Alert>}
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
          sx={{
            "& .pivot-enriched-financial": { bgcolor: "#fff8f1", color: "#7c2d12" },
            "& .pivot-enriched-financial:hover": { bgcolor: "#ffeddc" },
            "& .pivot-enriched-organization": { bgcolor: "#f2f8ff", color: "#1e3a8a" },
            "& .pivot-enriched-organization:hover": { bgcolor: "#e3f0ff" },
          }}
        />
      </Box>
    </Drawer>
    <Dialog open={!!selectedDrillRow} onClose={() => setSelectedDrillRow(null)} fullScreen>
      <DialogTitle sx={{ borderBottom: 1, borderColor: "divider", px: { xs: 2, md: 4 }, py: 2.5, background: "linear-gradient(120deg, #f8fafc 0%, #eef4ff 100%)" }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 750, letterSpacing: "-0.03em", color: "#172554" }}>Decomposição do registro</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.35 }}>Campos em ordem alfabética · valores estruturados aparecem em níveis</Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button startIcon={<CompareArrows />} variant="outlined" onClick={() => void loadSourceRelation()}>Ver relação com a origem</Button>
            <Button startIcon={<ContentCopy />} onClick={() => { if (selectedDrillRow) void navigator.clipboard?.writeText(JSON.stringify(selectedDrillRow, null, 2)); }}>Copiar JSON</Button>
            <IconButton aria-label="Fechar decomposição" onClick={() => setSelectedDrillRow(null)}><Close /></IconButton>
          </Stack>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ bgcolor: "#f5f7fb", p: { xs: 1.5, md: 4 } }}>
        {selectedDrillRow && <Stack spacing={2} sx={{ maxWidth: 1200, mx: "auto", mt: 1 }}>
          <Paper elevation={0} sx={{ overflow: "hidden", borderRadius: 3, border: "1px solid #e2e8f0", boxShadow: "0 12px 36px rgba(15, 23, 42, 0.06)" }}>
            <Stack direction="row" sx={{ px: 3, py: 1.75, bgcolor: "#f8fafc", borderBottom: 1, borderColor: "divider" }}>
              <Typography variant="overline" sx={{ width: { xs: "38%", md: "30%" }, color: "text.secondary", fontWeight: 750, letterSpacing: ".12em" }}>CAMPO</Typography>
              <Typography variant="overline" sx={{ color: "text.secondary", fontWeight: 750, letterSpacing: ".12em" }}>VALOR</Typography>
            </Stack>
            {selectedRowEntries.map(([name, value], index) => <Box key={name} sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "minmax(180px, 30%) minmax(0, 1fr)" }, gap: { xs: 0.5, sm: 2 }, alignItems: "start", px: 3, py: 1.75, borderBottom: index < selectedRowEntries.length - 1 ? 1 : 0, borderColor: "divider", bgcolor: index % 2 ? "#fcfdff" : "background.paper", transition: "background-color .15s", "&:hover": { bgcolor: "#f3f7ff" } }}>
              <Typography component="span" variant="body2" sx={{ minWidth: 0, color: "#64748b", fontWeight: 600, fontFamily: '"Segoe UI", sans-serif', overflowWrap: "anywhere" }}>{name}</Typography>
              <Box sx={{ minWidth: 0 }}>{renderDecomposedFieldValue(name, value)}</Box>
            </Box>)}
          </Paper>
        </Stack>}
      </DialogContent>
    </Dialog>
    <Dialog open={relationDialogOpen} onClose={() => setRelationDialogOpen(false)} fullWidth maxWidth="lg">
      <DialogTitle sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
          <Box><Typography variant="h6" fontWeight={750}>Origem → Dados preparados</Typography><Typography variant="body2" color="text.secondary">Correspondência direta pelo identificador persistido da linha</Typography></Box>
          <IconButton aria-label="Fechar relação com a origem" onClick={() => setRelationDialogOpen(false)}><Close /></IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ bgcolor: "#f8fafc", p: { xs: 1.5, md: 3 } }}>
        {sourceDetailLoading && <Box sx={{ minHeight: 180, display: "flex", alignItems: "center", justifyContent: "center", gap: 1.5 }}><CircularProgress size={22} /><Typography>Buscando a linha de origem na base de origem...</Typography></Box>}
        {!sourceDetailLoading && sourceDetail && !sourceDetail.available && <Alert severity="info" sx={{ mt: 2 }}>{dataDisplayText(sourceDetail.reason)}</Alert>}
        {!sourceDetailLoading && sourceDetail?.available && <Stack spacing={2} sx={{ maxWidth: 1100, mx: "auto", mt: 1 }}>
          <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "white" }}><Typography variant="subtitle1" fontWeight={750}>{sourceDetail.connection_name}</Typography><Typography variant="caption" color="text.secondary">{sourceDetail.blob_path || "Arquivo de origem identificado pelo registro"}</Typography></Paper>
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 56px 1fr" }, alignItems: "center", gap: 1 }}>
            <Typography variant="overline" color="#0369a1" fontWeight={800}>DADOS DE ORIGEM</Typography><CompareArrows sx={{ display: { xs: "none", md: "block" }, color: "#64748b", mx: "auto" }} /><Typography variant="overline" color="#6d28d9" fontWeight={800}>DADOS PREPARADOS</Typography>
          </Box>
          {(sourceDetail.pairs || []).map((pair, index) => <Box key={`${pair.source_key}-${pair.destination_key}-${index}`} sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 56px 1fr" }, gap: 1, alignItems: "stretch" }}>
            <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#f0f9ff", borderColor: "#bae6fd", minWidth: 0 }}><Typography variant="caption" fontWeight={750} color="#0369a1" display="block" mb={0.5}>{pair.source_key}</Typography>{renderDetailValue(pair.source_value)}</Paper>
            <Typography sx={{ display: { xs: "none", md: "grid" }, placeItems: "center", color: "#94a3b8", fontSize: 22 }}>→</Typography>
            <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#f5f3ff", borderColor: "#ddd6fe", minWidth: 0 }}><Typography variant="caption" fontWeight={750} color="#6d28d9" display="block" mb={0.5}>{pair.destination_key}</Typography>{renderDetailValue(selectedRowValuesByLowerName.get(pair.destination_key.toLowerCase()))}</Paper>
          </Box>)}
          {Object.keys(sourceDetail.source_unmatched || {}).length > 0 && <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "#fffbeb", borderColor: "#fde68a" }}><Typography variant="subtitle2" fontWeight={750} color="#92400e" mb={1}>Campos de origem sem correspondência ({Object.keys(sourceDetail.source_unmatched || {}).length})</Typography><Stack spacing={0.75}>{Object.entries(sourceDetail.source_unmatched || {}).sort(([a], [b]) => columnNameCompare.compare(a, b)).map(([name, value]) => <Box key={name} sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "minmax(160px, 30%) minmax(0,1fr)" }, gap: 1, p: 1, bgcolor: "white", borderRadius: 1.5 }}><Typography variant="caption" fontWeight={750} color="#a16207">{name}</Typography><Box>{renderDetailValue(value)}</Box></Box>)}</Stack></Paper>}
        </Stack>}
      </DialogContent>
      <DialogActions><Button onClick={() => setRelationDialogOpen(false)}>Fechar</Button></DialogActions>
    </Dialog>
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
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>A query consulta a tabela de detalhe na base preparada, usada pelo Pivot, e agrega pelas dimensões e medidas configuradas.</Typography>
        <TextField fullWidth multiline minRows={8} value={generatedSql} InputProps={{ readOnly: true }} sx={{ "& textarea": { fontFamily: "monospace", fontSize: 13 } }} />
      </DialogContent>
      <DialogActions>
        <Button startIcon={<ContentCopy />} onClick={() => { void navigator.clipboard?.writeText(generatedSql).then(() => { setCopiedQuery(true); setQueryOpen(false); }); }}>{copiedQuery ? "Copiada" : "Copiar query"}</Button>
        {onOpenQuery && <Button variant="contained" startIcon={<PlayArrow />} onClick={() => { onOpenQuery(generatedSql); setQueryOpen(false); }}>Abrir no editor de consultas</Button>}
        <Button onClick={() => setQueryOpen(false)}>Fechar</Button>
      </DialogActions>
    </Dialog>
  </Box>;
}
