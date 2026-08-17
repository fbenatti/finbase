import { useEffect, useMemo, useState } from "react";
import {
  Alert, Autocomplete, Box, Button, Chip, Divider, Drawer, FormControl, IconButton, InputLabel,
  MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { Add, Close, Delete, Download, PlayArrow, Save, Visibility } from "@mui/icons-material";
import Plot from "react-plotly.js";
import {
  DestinationAnalysis, PivotFilter, PivotMeasure, PivotMetadata, PivotRequest, PivotResult, PivotView,
  deletePivotView, drillDownPivot, getPivotMetadata, getPivotViews, runPivot, runPivotUnion, savePivotView,
  LakehouseSearchResult, searchPivotTable,
} from "../services/api";

interface Props { destinations: DestinationAnalysis[]; initialTableId?: number; showSearch?: boolean }

const money = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const key = (values: unknown[]) => JSON.stringify(values);
const formatKey = (values: unknown[]) => values.map(v => v == null || v === "" ? "(vazio)" : String(v)).join(" › ");

function downloadCsv(result: PivotResult) {
  const headers = [...result.rows, ...result.columns, ...result.measures.map(m => m.label || `${m.aggregation} ${m.column}`)];
  const lines = [headers, ...result.cells.map(cell => [...cell.row, ...cell.column, ...cell.values])]
    .map(row => row.map(value => `"${String(value ?? "").replace(/"/g, '""')}"`).join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob); const link = document.createElement("a");
  link.href = url; link.download = `pivot_${new Date().toISOString().slice(0, 10)}.csv`; link.click(); URL.revokeObjectURL(url);
}

export default function PivotAnalysis({ destinations, initialTableId, showSearch = false }: Props) {
  const first = initialTableId || destinations[0]?.id || 0;
  const [tableId, setTableId] = useState(first);
  const [sourceIds, setSourceIds] = useState<number[]>([first]);
  const [meta, setMeta] = useState<PivotMetadata | null>(null);
  const [rows, setRows] = useState<string[]>(["departamento", "cloud_origem", "ChargeCategory"]);
  const [cols, setCols] = useState<string[]>(["_billing_period"]);
  const [measures, setMeasures] = useState<PivotMeasure[]>([{ column: "EffectiveCost", aggregation: "SUM" }]);
  const [filters, setFilters] = useState<PivotFilter[]>([]);
  const [field, setField] = useState("");
  const [result, setResult] = useState<PivotResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [views, setViews] = useState<PivotView[]>([]);
  const [viewName, setViewName] = useState("");
  const [drill, setDrill] = useState<{ columns: string[]; rows: Record<string, unknown>[]; total: number } | null>(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResult, setSearchResult] = useState<LakehouseSearchResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [chartType, setChartType] = useState<"bar" | "line" | "pie" | "area">("bar");
  const [chartMeasure, setChartMeasure] = useState(0);

  const request = useMemo<PivotRequest>(() => ({ destination_table_id: tableId, rows, columns: cols, measures, filters, row_limit: 500, column_limit: 60, include_totals: true }), [tableId, rows, cols, measures, filters]);

  // Destination metadata is loaded asynchronously by the Analysis page. If
  // the user opens Pivot before that request completes, initialise as soon as
  // the table list arrives instead of leaving the screen on table ID zero.
  useEffect(() => {
    if (!tableId && destinations.length) {
      const selectedId = initialTableId || destinations[0].id;
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
      const isPlanning = tableId < 0 && dimensions.has("period") && dimensions.has("tipo") && next.measures.includes("value");
      if (isPlanning) {
        // Planning is a compact monthly table, not the detailed Gold fact.
        // Its useful default is immediately comparable realized × forecast.
        setRows(["tipo"]);
        setCols(["period"]);
        setMeasures([{ column: "value", aggregation: "SUM" }]);
        return;
      }
      setRows(current => { const valid = current.filter(item => dimensions.has(item)); return valid.length ? valid : next.recommended_dimensions.filter(item => item !== "_billing_period").slice(0, 3); });
      setCols(current => { const valid = current.filter(item => dimensions.has(item)); return valid.length ? valid : (dimensions.has("_billing_period") ? ["_billing_period"] : []); });
      setMeasures(current => current.filter(item => next.measures.includes(item.column)).length ? current.filter(item => next.measures.includes(item.column)) : (next.default_measure ? [{ column: next.default_measure, aggregation: "SUM" }] : []));
    }).catch(e => setError(e.message || "Não foi possível ler o schema Gold pelo Trino"));
    // Views are global to the workspace. Filtering only by the current table
    // made a saved dashboard disappear when the user opened another table.
    getPivotViews().then(setViews).catch(() => setViews([]));
  }, [tableId]);

  const execute = async () => {
    if (!tableId || !measures.length) return;
    setLoading(true); setError("");
    try {
      setResult(sourceIds.length > 1
        ? await runPivotUnion({ destination_table_ids: sourceIds, rows, columns: cols, measures, filters, row_limit: 500, column_limit: 60, include_totals: true, origin_column: "origem" })
        : await runPivot(request));
    }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Falha ao consultar o Trino"); }
    finally { setLoading(false); }
  };
  const executeSearch = async () => {
    if (!tableId || !searchTerm.trim()) return;
    setSearchLoading(true); setError("");
    try { setSearchResult(await searchPivotTable(tableId, searchTerm, 100, 0, filters)); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Falha ao pesquisar no Trino"); }
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
  const openDrill = async (rowKey: unknown[], colKey: unknown[]) => {
    const cellFilters: Record<string, unknown> = {};
    result?.rows.forEach((name, index) => { cellFilters[name] = rowKey[index] as string; });
    result?.columns.forEach((name, index) => { cellFilters[name] = colKey[index] as string; });
    setDrillLoading(true);
    try { const data = await drillDownPivot({ ...request, cell_filters: cellFilters, limit: 100, offset: 0 }); setDrill(data); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Falha no drill-down"); }
    finally { setDrillLoading(false); }
  };

  const rowKeys = result?.row_keys || [];
  const colKeys = result?.column_keys || [[]];
  const cells = useMemo(() => new Map((result?.cells || []).map(cell => [`${key(cell.row)}|${key(cell.column)}`, cell.values])), [result]);
  const totalFor = (axis: "rows" | "columns", values: unknown[]) => result?.totals?.[axis][key(values)] || [];
  const fieldOptions = meta?.columns || [];
  const chart = useMemo(() => {
    if (!result || !rowKeys.length || !result.measures.length) return null;
    const measureIndex = Math.min(chartMeasure, result.measures.length - 1);
    const temporal = result.columns.some(name => ["_billing_period", "period", "forecast_month"].includes(name.toLowerCase()));
    const labels = temporal ? colKeys.map(col => formatKey(col)) : rowKeys.map(row => formatKey(row));
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
        <FormControl size="small" sx={{ minWidth: 250 }}><InputLabel>Tabela Gold</InputLabel><Select value={tableId} label="Tabela Gold" onChange={e => { const id = Number(e.target.value); setTableId(id); setSourceIds([id]); }}>{destinations.filter(d => d.id > 0).map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}</Select></FormControl>
        <Box sx={{ display: "flex", alignItems: "center", gap: .5, flexWrap: "wrap" }}>
          <Typography variant="caption" color="text.secondary">Origem:</Typography>
          {destinations.filter(d => d.id === tableId || d.id === -Math.abs(tableId)).map(d => <Chip key={d.id} label={d.id < 0 ? "Forecast" : "Realizado"} color={sourceIds.includes(d.id) ? "primary" : "default"} variant={sourceIds.includes(d.id) ? "filled" : "outlined"} onClick={() => setSourceIds(current => current.includes(d.id) ? (current.length > 1 ? current.filter(id => id !== d.id) : current) : [...current, d.id])} />)}
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
      </Stack>
      {showSearch && <Stack direction="row" gap={1} alignItems="center" sx={{ mt: 1.5 }}>
        <TextField size="small" fullWidth label="Buscar texto em todas as colunas" placeholder="Ex.: marketplace" value={searchTerm} onChange={e => setSearchTerm(e.target.value)} onKeyDown={e => { if (e.key === "Enter") executeSearch(); }} />
        <Button variant="outlined" startIcon={<Visibility />} onClick={executeSearch} disabled={searchLoading || !searchTerm.trim()}>{searchLoading ? "Buscando..." : "Buscar"}</Button>
      </Stack>}
      <Divider sx={{ my: 1.5 }} />
      <Stack direction="row" gap={1.5} flexWrap="wrap">
        {(["rows", "cols", "filters"] as const).map(area => <Paper key={area} variant="outlined" sx={{ p: 1, minWidth: 210, flex: 1 }}><Typography variant="caption" fontWeight={700}>{area === "rows" ? "LINHAS" : area === "cols" ? "COLUNAS" : "FILTROS"}</Typography><Stack direction="row" gap={.5} flexWrap="wrap" mt={.5}>{(area === "rows" ? rows : area === "cols" ? cols : filters.map(f => f.column)).map(name => <Chip key={name} label={name} onDelete={() => removeField(area, name)} size="small" />)} {!((area === "rows" ? rows : area === "cols" ? cols : filters).length) && <Typography variant="caption" color="text.secondary">Nenhum campo</Typography>}</Stack></Paper>)}
        <Paper variant="outlined" sx={{ p: 1, minWidth: 260, flex: 1 }}><Typography variant="caption" fontWeight={700}>VALORES</Typography>{measures.map((m, index) => <Stack key={index} direction="row" gap={.5} mt={.5}><Select size="small" value={m.column} onChange={e => setMeasures(measures.map((x, i) => i === index ? { ...x, column: e.target.value } : x))} sx={{ flex: 1 }}>{meta?.measures.map(name => <MenuItem key={name} value={name}>{name}</MenuItem>)}</Select><Select size="small" value={m.aggregation} onChange={e => setMeasures(measures.map((x, i) => i === index ? { ...x, aggregation: e.target.value as PivotMeasure["aggregation"] } : x))}>{["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"].map(op => <MenuItem key={op} value={op}>{op}</MenuItem>)}</Select><IconButton size="small" onClick={() => setMeasures(measures.filter((_, i) => i !== index))}><Delete fontSize="small" /></IconButton></Stack>)}<Button size="small" startIcon={<Add />} onClick={() => meta?.default_measure && setMeasures([...measures, { column: meta.default_measure, aggregation: "SUM" }])}>Adicionar medida</Button></Paper>
      </Stack>
      {filters.map(filter => <Stack key={filter.column} direction="row" gap={1} alignItems="center" sx={{ mt: 1, mr: 1, flexWrap: "wrap" }}>
        <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel>Condição</InputLabel><Select label="Condição" value={filter.operator || "IN"} onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, operator: e.target.value as PivotFilter["operator"] } : f))}><MenuItem value="IN">É igual a</MenuItem><MenuItem value="NOT_IN">Não é</MenuItem><MenuItem value="CONTAINS">Contém</MenuItem><MenuItem value="NOT_CONTAINS">Não contém</MenuItem></Select></FormControl>
        <TextField
          size="small"
          label={`Valor do filtro: ${filter.column}`}
          placeholder={filter.column.toLowerCase() === "cloud_origem" ? "azure-4p" : "Digite o valor"}
          helperText="Use valores separados por vírgula para selecionar mais de um"
          sx={{ minWidth: 330 }}
          value={filter.values.join(", ")}
          onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, values: e.target.value.split(",").map(v => v.trim()).filter(Boolean) } : f))}
        />
        {filter.column.toLowerCase() === "cloud_origem" && ["azure-4p", "azure-ti", "gcp-4p", "gcp-ti"].map(value => <Chip key={value} label={value} size="small" variant={filter.values.includes(value) ? "filled" : "outlined"} color={filter.values.includes(value) ? "primary" : "default"} onClick={() => setFilters(filters.map(f => f.column === filter.column ? { ...f, values: f.values.includes(value) ? f.values.filter(v => v !== value) : [...f.values, value] } : f))} />)}
      </Stack>)}
      <Stack direction="row" gap={1} mt={2} alignItems="center" flexWrap="wrap"><TextField size="small" label="Salvar visão compartilhada" value={viewName} onChange={e => setViewName(e.target.value)} /><Button startIcon={<Save />} onClick={saveView}>Salvar</Button><Typography variant="caption" color="text.secondary">Dashboards salvos:</Typography>{views.map(view => <Chip key={view.id} label={view.name} color={view.destination_table_id === tableId ? "primary" : "default"} onClick={() => loadView(view)} onDelete={() => deletePivotView(view.id).then(() => getPivotViews().then(setViews))} />)}</Stack>
    </Paper>
    {result?.currency_added && <Alert severity="info" sx={{ mb: 2 }}>Currency foi adicionada às linhas para impedir a soma de moedas diferentes.</Alert>}
    {drillLoading && <Alert severity="info" sx={{ mb: 2 }}>Carregando drill-down...</Alert>}
    {result && <TableContainer component={Paper} sx={{ maxHeight: "60vh" }}><Table stickyHeader size="small"><TableHead><TableRow>{result.rows.map(name => <TableCell key={name}>{name}</TableCell>)}{colKeys.flatMap(col => result.measures.map(m => <TableCell key={`${key(col)}-${m.column}`}>{formatKey(col)}<br /><small>{m.label || `${m.aggregation} ${m.column}`}</small></TableCell>))}<TableCell>Total</TableCell></TableRow></TableHead><TableBody>{rowKeys.map(row => <TableRow key={key(row)}>{row.map((value, i) => <TableCell key={i}>{String(value ?? "(vazio)")}</TableCell>)}{colKeys.flatMap(col => (cells.get(`${key(row)}|${key(col)}`) || result.measures.map(() => null)).map((value, i) => <TableCell key={`${key(col)}-${i}`} onClick={() => openDrill(row, col)} sx={{ cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}>{typeof value === "number" ? money.format(value) : "—"}</TableCell>))}<TableCell>{totalFor("rows", row).map(value => typeof value === "number" ? money.format(value) : "—").join(" / ")}</TableCell></TableRow>)}{result.totals && <TableRow sx={{ bgcolor: "action.hover" }}>{result.rows.map((_, i) => <TableCell key={i}>{i === 0 ? "TOTAL" : ""}</TableCell>)}{colKeys.flatMap(col => totalFor("columns", col).map((value, i) => <TableCell key={`${key(col)}-${i}`}>{typeof value === "number" ? money.format(value) : "—"}</TableCell>))}<TableCell>{result.totals.grand.map(value => typeof value === "number" ? money.format(value) : "—").join(" / ")}</TableCell></TableRow>}</TableBody></Table></TableContainer>}
    {chart && <Paper sx={{ mt: 2, p: 2 }}><Stack direction="row" alignItems="center" gap={1} flexWrap="wrap"><Typography variant="subtitle1" fontWeight={700}>Visualização</Typography><FormControl size="small" sx={{ minWidth: 150 }}><InputLabel>Tipo</InputLabel><Select label="Tipo" value={chartType} onChange={e => setChartType(e.target.value as typeof chartType)}><MenuItem value="bar">Barras</MenuItem><MenuItem value="line">Linhas</MenuItem><MenuItem value="area">Área</MenuItem><MenuItem value="pie">Pizza</MenuItem></Select></FormControl><FormControl size="small" sx={{ minWidth: 220 }}><InputLabel>Medida</InputLabel><Select label="Medida" value={chartMeasure} onChange={e => setChartMeasure(Number(e.target.value))}>{(result?.measures ?? []).map((m, i) => <MenuItem key={i} value={i}>{m.label || `${m.aggregation} ${m.column}`}</MenuItem>)}</Select></FormControl></Stack><Plot useResizeHandler style={{ width: "100%", height: 420 }} data={chartData} layout={chartLayout} config={{ responsive: true, displaylogo: false }} /></Paper>}
    {showSearch && searchResult && <Paper sx={{ mt: 2, p: 2 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="subtitle1">Resultados para “{searchTerm}”</Typography><Chip label={`${searchResult.total.toLocaleString("pt-BR")}${searchResult.total_is_estimate ? " exibidos (limite da busca)" : " registros encontrados"}`} size="small" /></Stack><Typography variant="caption" color="text.secondary">Pesquisa realizada em {searchResult.searched_columns?.length || 0} colunas textuais. As colunas encontradas aparecem destacadas.</Typography><TableContainer sx={{ maxHeight: "55vh", mt: 1 }}><Table stickyHeader size="small"><TableHead><TableRow>{searchResult.columns.map(column => <TableCell key={column}>{column}</TableCell>)}</TableRow></TableHead><TableBody>{searchResult.rows.map((row, index) => <TableRow key={index}>{searchResult.columns.map(column => <TableCell key={column} sx={searchResult.matched_columns[index]?.includes(column) ? { bgcolor: "#fff3cd", fontWeight: 700 } : undefined}>{String(row[column] ?? "")}</TableCell>)}</TableRow>)}</TableBody></Table></TableContainer></Paper>}
    <Drawer anchor="right" open={!!drill} onClose={() => setDrill(null)} PaperProps={{ sx: { width: "65vw", p: 2 } }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="h6">Drill-down ({drill?.total || 0} registros)</Typography><IconButton onClick={() => setDrill(null)}><Close /></IconButton></Stack>{drill && <TableContainer><Table size="small"><TableHead><TableRow>{drill.columns.map(column => <TableCell key={column}>{column}</TableCell>)}</TableRow></TableHead><TableBody>{drill.rows.map((row, index) => <TableRow key={index}>{drill.columns.map(column => <TableCell key={column}>{String(row[column] ?? "")}</TableCell>)}</TableRow>)}</TableBody></Table></TableContainer>}</Drawer>
  </Box>;
}
