import React, { useState, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box, Typography, Button, Card, CardContent, Chip, Alert,
  FormControl, InputLabel, Select, MenuItem, Tabs, Tab, Stack,
  TextField, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Paper, CircularProgress,
  TablePagination, TableSortLabel,
} from "@mui/material";
import {
  Storage, AttachMoney, PlayArrow, Refresh, Delete,
  Schema as SchemaIcon, Tune, BarChart, Terminal, Search as SearchIcon,
} from "@mui/icons-material";
import {
  getAnalysisHealth, getAnalysisTables, runAnalysisQuery,
  TrinoQueryResult, TrinoHealth, TrinoTableList,
  getAnalysisSources, queryAnalysisSource,
  getAnalysisDestinations, queryAnalysisDestination,
  SourceAnalysis, SourceDataResult,
  DestinationAnalysis, DestinationDataResult,
  formatApiError, PivotFilter, deleteDimonTable,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import PivotAnalysis from "../../components/PivotAnalysis";
import SourceSearch from "../../components/SourceSearch";

const PAGE_SIZE = 50;

function filtersFromSearch(params: URLSearchParams): PivotFilter[] {
  const filters: PivotFilter[] = [];
  const path = params.get("path") || "";
  path.split(" > ").map(part => part.trim()).filter(Boolean).forEach((name, index) => {
    filters.push({ column: `departamento_nivel_${index + 1}`, values: [name], operator: "IN" });
  });
  const year = params.get("year");
  if (year) filters.push({ column: "_billing_period", values: [year], operator: "CONTAINS" });
  return filters;
}

type AnalysisTab = "destino" | "fontes" | "iceberg" | "pivot" | "busca";

// Pre-built queries by layer
const PREBUILT: Record<string, { label: string; sql: string }[]> = {
  bronze: [
    { label: "Listar tabelas raw", sql: "SELECT * FROM iceberg.bronze.raw_azure_4p LIMIT 10" },
    { label: "Contagem por período", sql: "SELECT _billing_period, COUNT(*) AS rows FROM iceberg.bronze.raw_azure_4p GROUP BY _billing_period ORDER BY _billing_period DESC" },
  ],
  silver: [
    { label: "Listar dados Silver Azure 4P", sql: "SELECT * FROM iceberg.silver.azure_4p_dest_modelo_focus LIMIT 10" },
    { label: "Schema Silver Azure 4P", sql: "SELECT * FROM iceberg.silver.azure_4p_dest_modelo_focus LIMIT 1" },
  ],
  gold: [
    { label: "Top 10 serviços por custo", sql: 'SELECT "ServiceName", SUM("BilledCost") AS total_billed, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "ServiceName" ORDER BY total_billed DESC LIMIT 10' },
    { label: "Custo por departamento", sql: 'SELECT "departamento", SUM("BilledCost") AS total_billed, SUM("EffectiveCost") AS total_effective, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "departamento" ORDER BY total_billed DESC LIMIT 20' },
    { label: "Custo por período", sql: 'SELECT "_billing_period", SUM("BilledCost") AS total_billed, SUM("EffectiveCost") AS total_effective, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "_billing_period" ORDER BY "_billing_period" DESC' },
    { label: "Custo por região", sql: 'SELECT "RegionName", SUM("BilledCost") AS total_billed, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "RegionName" ORDER BY total_billed DESC LIMIT 20' },
    { label: "Custo por origem e mês", sql: 'SELECT "cloud_origem", "_billing_period", SUM("BilledCost") AS total_billed, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "cloud_origem", "_billing_period" ORDER BY "_billing_period" DESC, total_billed DESC' },
    { label: "Departamento / origem por mês", sql: 'SELECT "departamento", "cloud_origem", "_billing_period", SUM("BilledCost") AS total_billed, SUM("EffectiveCost") AS total_effective, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "departamento", "cloud_origem", "_billing_period" ORDER BY "_billing_period" DESC, total_billed DESC LIMIT 500' },
  ],
};

export default function Analysis() {
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<AnalysisTab>((searchParams.get("tab") as AnalysisTab) || "fontes");
  const [health, setHealth] = useState<TrinoHealth>({ trino: "?", nessie: "?", minio: "?" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // ─── Iceberg state ───
  const [layer, setLayer] = useState<"bronze" | "silver" | "gold" | "dimon">("gold");
  const [tables, setTables] = useState<TrinoTableList>({ bronze: [], silver: [], gold: [], dimon: [] });
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<TrinoQueryResult | null>(null);
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  // ─── Fontes (Sources) state ───
  const [sources, setSources] = useState<SourceAnalysis[]>([]);
  const [selectedSrcId, setSelectedSrcId] = useState<number | "">("");
  const [srcData, setSrcData] = useState<SourceDataResult | null>(null);
  const [srcPage, setSrcPage] = useState(0);

  // ─── Destino state ───
  const [destinations, setDestinations] = useState<DestinationAnalysis[]>([]);
  const [selectedDestId, setSelectedDestId] = useState<number | "">("");
  const [destData, setDestData] = useState<DestinationDataResult | null>(null);
  const [destPage, setDestPage] = useState(0);

  // ─── Init ───
  useEffect(() => {
    getAnalysisHealth().then(setHealth).catch(() => {});
    getAnalysisTables().then(setTables).catch(() => {});
    getAnalysisSources().then(setSources).catch(() => {});
    getAnalysisDestinations().then(setDestinations).catch(() => {});
  }, []);

  const refreshLakehouse = async () => {
    try {
      const next = await getAnalysisDestinations();
      setDestinations(next);
    } catch (e: unknown) {
      setError(formatApiError(e, "Falha ao atualizar o Data Lakehouse"));
    }
  };

  const removeDimonTable = async (t: string) => {
    if (!window.confirm('Excluir a tabela ' + t + '? A regra Dimon associada (se houver) e a tabela física no Iceberg serão removidas.')) return;
    setError("");
    try {
      await deleteDimonTable(t);
      getAnalysisTables().then(setTables).catch(() => {});
      if (sql.startsWith('SELECT * FROM iceberg.gold.' + t)) setSql("");
    } catch (e: unknown) {
      setError(formatApiError(e, 'Não foi possível excluir a tabela Dimon'));
    }
  };

  // ─── Source query ───
  const loadSourceData = async (connId: number, offset: number = 0) => {
    setLoading(true); setError("");
    try {
      const d = await queryAnalysisSource(connId, { limit: PAGE_SIZE, offset });
      setSrcData(d);
      setSrcPage(0);
    } catch (e: unknown) {
      setError(formatApiError(e, "Query failed"));
    } finally { setLoading(false); }
  };

  const handleSrcChange = (id: number | "") => {
    setSelectedSrcId(id);
    setSrcData(null);
    if (id) loadSourceData(id as number);
  };

  // ─── Destination query ───
  const loadDestData = async (tableId: number, offset: number = 0) => {
    setLoading(true); setError("");
    try {
      const d = await queryAnalysisDestination(tableId, { limit: PAGE_SIZE, offset });
      setDestData(d);
      setDestPage(0);
    } catch (e: unknown) {
      setError(formatApiError(e, "Query failed"));
    } finally { setLoading(false); }
  };

  const handleDestChange = (id: number | "") => {
    setSelectedDestId(id);
    setDestData(null);
    if (id) loadDestData(id as number);
  };

  // ─── Trino query ───
  const executeSql = async (querySql?: string) => {
    const q = querySql || sql;
    if (!q.trim()) return;
    setLoading(true); setError(""); setSearch(""); setSortKey(null);
    try {
      const res = await runAnalysisQuery(q);
      setResult(res);
      setPage(0);
    } catch (e: unknown) {
      setError(formatApiError(e, "Query failed"));
    } finally { setLoading(false); }
  };

  const setPrebuilt = (q: string) => { setSql(q); executeSql(q); };

  // ─── Data table helpers ───
  const filteredRows = useMemo(() => {
    if (!result) return [];
    let rows = result.rows;
    if (search.trim()) {
      const q = search.toLowerCase();
      rows = rows.filter((row) => result.columns.some((col) => {
        const val = row[col];
        return val != null && String(val).toLowerCase().includes(q);
      }));
    }
    if (sortKey) {
      rows = [...rows].sort((a, b) => {
        const va = a[sortKey], vb = b[sortKey];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        let cmp = 0;
        if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
        else cmp = String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: "base" });
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return rows;
  }, [result, search, sortKey, sortDir]);

  const pagedRows = useMemo(() => filteredRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE), [filteredRows, page]);

  const handleSort = (col: string) => {
    if (sortKey === col) {
      if (sortDir === "asc") setSortDir("desc");
      else { setSortKey(null); setSortDir("asc"); }
    } else { setSortKey(col); setSortDir("asc"); }
  };

  const formatCell = (val: unknown): string => {
    if (val === null || val === undefined) return "-";
    if (typeof val === "number") return val.toLocaleString("pt-BR", { maximumFractionDigits: 4 });
    return String(val);
  };

  const srcColumns = useMemo(() => {
    if (!srcData || srcData.rows.length === 0) return [];
    return Object.keys(srcData.rows[0]);
  }, [srcData]);

  const srcPaged = useMemo(() => {
    if (!srcData) return [];
    return srcData.rows.slice(srcPage * PAGE_SIZE, (srcPage + 1) * PAGE_SIZE);
  }, [srcData, srcPage]);

  const destColumns = useMemo(() => destData?.columns || [], [destData]);
  const dimonTablesCount = useMemo(() => (tables.dimon || []).length, [tables]);
  const destPaged = useMemo(() => {
    if (!destData) return [];
    return destData.rows.slice(destPage * PAGE_SIZE, (destPage + 1) * PAGE_SIZE);
  }, [destData, destPage]);

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      {/* Faixa de contexto branca */}
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, mb: 2 }}>
          <Box>
            <Typography variant="h4">Análise — Data Lakehouse</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Explore os dados importados, classificados e aplicados com regras de negócio.
            </Typography>
          </Box>
          <Button size="small" variant="outlined" startIcon={<Refresh />} onClick={refreshLakehouse} sx={{ flexShrink: 0 }}>
            Atualizar
          </Button>
        </Box>
        <Tabs value={tab} onChange={(_, v) => { setTab(v); setResult(null); setSrcData(null); setDestData(null); setError(""); }} sx={{ mb: 0, minHeight: 42 }}>
          <Tab label="Tabela Destino" value="destino" icon={<AttachMoney />} iconPosition="start" sx={{ minHeight: 42 }} />
          <Tab label="Fontes de Origem" value="fontes" icon={<Storage />} iconPosition="start" sx={{ minHeight: 42 }} />
          <Tab label="Iceberg / Trino" value="iceberg" icon={<Terminal />} iconPosition="start" sx={{ minHeight: 42 }} />
          <Tab label="Pivot" value="pivot" icon={<BarChart />} iconPosition="start" sx={{ minHeight: 42 }} />
          <Tab label="Busca na Fonte" value="busca" icon={<SearchIcon />} iconPosition="start" sx={{ minHeight: 42 }} />
        </Tabs>
      </Box>

      {/* Área de trabalho com campos simples de 40px */}
      <Box sx={{
        p: 3,
        "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
        "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
      }}>
        {/* Health status */}
        <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
          {Object.entries(health).map(([k, v]) => (
            <Chip key={k} label={k + ": " + v} size="small" color={v === "ok" ? "success" : "warning"} variant="outlined" />
          ))}
        </Box>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

      <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>


      {/* ================================================================ */}
      {/* TAB: DESTINO (classified data)                                    */}
      {/* ================================================================ */}
      {tab === "destino" && (
        <>
          <Box sx={{ display: "flex", gap: 2, mb: 2, alignItems: "center", flexWrap: "wrap" }}>
            <FormControl sx={{ minWidth: 260 }} size="small">
              <InputLabel>Tabela Destino</InputLabel>
              <Select value={selectedDestId} label="Tabela Destino" onChange={(e) => handleDestChange(e.target.value as number | "")}>
                {destinations.map((d) => (
                  <MenuItem key={d.id} value={d.id}>
                    {d.name} ({d.row_count.toLocaleString()} linhas)
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {destData && (
              <Chip label={`${destData.total.toLocaleString()} registros | ${destData.columns.length} colunas`} color="primary" size="small" />
            )}
          </Box>

          {loading && <CircularProgress sx={{ display: "block", mx: "auto", mt: 4 }} />}

          {!loading && !selectedDestId && (
            <EmptyState message="Selecione uma Tabela Destino acima para visualizar os dados classificados e com regras de negócio aplicadas." />
          )}

          {!loading && destData && destData.rows.length === 0 && (
            <Alert severity="info">Nenhum dado na tabela destino ainda. Execute o ETL para popular os dados.</Alert>
          )}

          {!loading && destData && destData.rows.length > 0 && (
            <>
              <TableContainer component={Paper} sx={{ maxHeight: 600, mb: 2 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      {destColumns.map((col) => (
                        <TableCell key={col} sx={{ fontWeight: 600, fontFamily: "monospace", fontSize: "0.72rem", whiteSpace: "nowrap" }}>
                          {col}
                        </TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {destPaged.map((row, i) => (
                      <TableRow key={i} hover>
                        {destColumns.map((col) => (
                          <TableCell key={col} sx={{ maxWidth: 250, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.78rem" }}>
                            {formatCell(row[col])}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <TablePagination
                component="div"
                count={destData.rows.length}
                page={destPage}
                onPageChange={(_, p) => { setDestPage(p); }}
                rowsPerPage={PAGE_SIZE}
                rowsPerPageOptions={[PAGE_SIZE]}
              />
            </>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* TAB: FONTES (source ingested data)                                */}
      {/* ================================================================ */}
      {tab === "fontes" && (
        <>
          <Box sx={{ display: "flex", gap: 2, mb: 2, alignItems: "center", flexWrap: "wrap" }}>
            <FormControl sx={{ minWidth: 300 }} size="small">
              <InputLabel>Fonte de Dados</InputLabel>
              <Select value={selectedSrcId} label="Fonte de Dados" onChange={(e) => handleSrcChange(e.target.value as number | "")}>
                {sources.map((s) => (
                  <MenuItem key={s.id} value={s.id}>
                    {s.name} ({s.code}) — {s.row_count.toLocaleString()} linhas
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {srcData && (
              <>
                <Chip label={`${srcData.total.toLocaleString()} registros`} color="info" size="small" />
                <FormControl sx={{ minWidth: 150 }} size="small">
                  <InputLabel>Período</InputLabel>
                  <Select value="" label="Período" onChange={() => {}}>
                    <MenuItem value="">Todos</MenuItem>
                    {srcData.periods.map((p) => (
                      <MenuItem key={p.period} value={p.period}>{p.period} ({p.count.toLocaleString()})</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </>
            )}
          </Box>

          {/* Source summary cards */}
          {!selectedSrcId && !loading && (
            <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 2, mb: 3 }}>
              {sources.filter(s => s.row_count > 0).map((s) => (
                <Paper
                  key={s.id}
                  elevation={0}
                  sx={{ p: 2, cursor: "pointer", borderRadius: 2, bgcolor: "background.paper", border: "2px solid", borderColor: "divider", "&:hover": { borderColor: "primary.main" } }}
                  onClick={() => handleSrcChange(s.id)}
                >
                  <Typography variant="caption" color="text.secondary">{s.code}</Typography>
                  <Typography variant="body1" sx={{ fontWeight: 600 }}>{s.name}</Typography>
                  <Typography variant="h5" sx={{ mt: 1, fontWeight: 700, color: "primary.main" }}>
                    {s.row_count.toLocaleString()}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">linhas ingeridas</Typography>
                </Paper>
              ))}
            </Box>
          )}

          {loading && <CircularProgress sx={{ display: "block", mx: "auto", mt: 4 }} />}

          {!loading && !selectedSrcId && sources.every(s => s.row_count === 0) && (
            <EmptyState message="Nenhuma fonte possui dados ingeridos. Execute o ETL primeiro em 'Extração & Monitoramento'." />
          )}

          {!loading && srcData && srcData.rows.length > 0 && (
            <>
              <TableContainer component={Paper} sx={{ maxHeight: 600, mb: 2 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      {srcColumns.slice(0, 20).map((col) => (
                        <TableCell key={col} sx={{ fontWeight: 600, fontFamily: "monospace", fontSize: "0.72rem", whiteSpace: "nowrap" }}>
                          {col}
                        </TableCell>
                      ))}
                      {srcColumns.length > 20 && (
                        <TableCell sx={{ color: "text.secondary", fontSize: "0.7rem" }}>+{srcColumns.length - 20} colunas</TableCell>
                      )}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {srcPaged.map((row, i) => (
                      <TableRow key={i} hover>
                        {srcColumns.slice(0, 20).map((col) => (
                          <TableCell key={col} sx={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.78rem" }}>
                            {formatCell(row[col])}
                          </TableCell>
                        ))}
                        {srcColumns.length > 20 && <TableCell />}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <TablePagination
                component="div"
                count={srcData.rows.length}
                page={srcPage}
                onPageChange={(_, p) => { setSrcPage(p); }}
                rowsPerPage={PAGE_SIZE}
                rowsPerPageOptions={[PAGE_SIZE]}
              />
            </>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* TAB: ICEBERG / TRINO                                             */}
      {/* ================================================================ */}
      {tab === "iceberg" && (
        <>
          {/* Layer sub-tabs */}
          <Paper sx={{ mb: 2 }}>
            <Tabs value={layer} onChange={(_, v) => { setLayer(v); setResult(null); setError(""); }}>
              <Tab label={`Bronze (${(tables.bronze || []).length})`} value="bronze" icon={<Storage />} iconPosition="start" />
              <Tab label={`Silver (${(tables.silver || []).length})`} value="silver" icon={<Tune />} iconPosition="start" />
              <Tab label={`Gold (${(tables.gold || []).length})`} value="gold" icon={<AttachMoney />} iconPosition="start" />
              <Tab label={`Dimon (${dimonTablesCount})`} value="dimon" icon={<SchemaIcon />} iconPosition="start" />
            </Tabs>
          </Paper>

          {layer === "gold" && (
            <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
              {(tables.gold || []).map((t) => (
                <Chip key={t} label={t} size="small" icon={<SchemaIcon />} variant="outlined"
                  onClick={() => setSql(`SELECT * FROM iceberg.gold.${t} LIMIT 10`)} sx={{ cursor: "pointer" }} />
              ))}
            </Box>
          )}
          {layer === "dimon" && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5, flexWrap: "wrap" }}>
                <SchemaIcon color="primary" fontSize="small" />
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Dimon — tabelas pré-agregadas (dest_dm_cost_*)</Typography>
                <Chip label={`${dimonTablesCount} tabela(s)`} size="small" color="primary" variant="outlined" />
              </Box>
              {dimonTablesCount === 0 ? (
                <Alert severity="info" sx={{ mb: 2 }}>Nenhuma tabela Dimon gerada ainda. Ela é criada automaticamente ao final do reprocess do destino MODELO FOCUS.</Alert>
              ) : (
                <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
                  {(tables.dimon || []).map((t) => (
                    <Chip key={t} label={t} size="small" icon={<SchemaIcon />} variant="outlined"
                      onClick={() => setSql(`SELECT * FROM iceberg.dimon.${t} LIMIT 10`)} sx={{ cursor: "pointer" }}
                      onDelete={() => void removeDimonTable(t)} deleteIcon={<Delete fontSize="small" />} />
                  ))}
                </Box>
              )}
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
                Clique em uma tabela para montar a query e depois em <b>Run</b> — mesmo comportamento da camada Gold. As Dimons são pré-agregadas (mensal) com medidas <b>quantity</b>, <b>realizado</b> e <b>billedcost</b>.
              </Typography>
            </>
          )}
          {layer !== "gold" && layer !== "dimon" && (
            <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
              {(tables[layer] || []).map((t) => (
                <Chip key={t} label={t} size="small" icon={<SchemaIcon />} variant="outlined"
                  onClick={() => setSql(`SELECT * FROM iceberg.${layer}.${t} LIMIT 10`)} sx={{ cursor: "pointer" }} />
              ))}
            </Box>
          )}

          <Paper elevation={0} sx={{ p: 2, mb: 2, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Box sx={{ display: "flex", gap: 1, mb: 1, flexWrap: "wrap" }}>
              {(PREBUILT[layer] || []).map((q) => (
                <Button key={q.label} size="small" variant="outlined" onClick={() => setPrebuilt(q.sql)}>{q.label}</Button>
              ))}
            </Box>
            <Box sx={{ display: "flex", gap: 1 }}>
              <TextField fullWidth multiline minRows={2} maxRows={6} size="small"
                placeholder={layer === "dimon" ? "SELECT * FROM iceberg.dimon.dest_dm_cost_<code> LIMIT 10" : "SELECT * FROM iceberg." + layer + "." + ((tables[layer] || [])[0] || "...") + " LIMIT 10"}
                value={sql} onChange={(e) => setSql(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && e.metaKey && executeSql()}
                sx={{ fontFamily: "monospace", "& textarea": { fontSize: "0.85rem" } }} />
              <Button variant="contained" startIcon={<PlayArrow />} onClick={() => executeSql()}
                disabled={loading || !sql.trim()} sx={{ minWidth: 100 }}>Run</Button>
            </Box>
          </Paper>

          {loading && <CircularProgress sx={{ display: "block", mx: "auto", mt: 4 }} />}

          {!loading && !result && (
            <EmptyState message="Execute uma query SQL acima. O Trino precisa estar ativo para consultas SQL; a navegação das tabelas Iceberg continua disponível pelo fallback local." />
          )}

          {!loading && result && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 2, mb: 2, flexWrap: "wrap" }}>
                <BarChart color="action" />
                <Typography variant="h6">{result.row_count.toLocaleString()} registros</Typography>
                <Chip label={`${result.columns.length} colunas`} size="small" variant="outlined" />
                <TextField size="small" placeholder="Filtrar..." value={search}
                  onChange={(e) => { setSearch(e.target.value); setPage(0); }}
                  InputProps={{ startAdornment: <SearchIcon sx={{ mr: 0.5, color: "text.secondary", fontSize: 18 }} /> }}
                  sx={{ minWidth: 200, ml: "auto" }} />
                {search && <Typography variant="caption" color="text.secondary">{filteredRows.length} de {result.row_count}</Typography>}
              </Box>

              <TableContainer component={Paper} sx={{ maxHeight: 600, mb: 2 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      {result.columns.map((col) => (
                        <TableCell key={col} sx={{ fontWeight: 600, fontFamily: "monospace", fontSize: "0.75rem", cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                          onClick={() => { handleSort(col); setPage(0); }}>
                          <TableSortLabel active={sortKey === col} direction={sortKey === col ? sortDir : "asc"}>{col}</TableSortLabel>
                        </TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {pagedRows.map((row, i) => (
                      <TableRow key={i} hover>
                        {result.columns.map((col) => (
                          <TableCell key={col} sx={{ maxWidth: 250, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.8rem" }}>
                            {formatCell(row[col])}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <TablePagination
                component="div" count={filteredRows.length} page={page}
                onPageChange={(_, p) => setPage(p)} rowsPerPage={PAGE_SIZE} rowsPerPageOptions={[PAGE_SIZE]} />
            </>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* TAB: PIVOT                                                       */}
      {/* ================================================================ */}
      {tab === "pivot" && (
        destinations.length > 0
          ? <PivotAnalysis
              destinations={destinations}
              initialTableId={destinations.find(d => d.id > 0 && d.name.toUpperCase() === "MODELO FOCUS")?.id || destinations.find(d => d.id > 0)?.id}
              initialMeasure={searchParams.get("measure") || "realizado"}
              initialFilters={filtersFromSearch(searchParams)}
              autoRun={searchParams.get("tab") === "pivot"}
              showSearch
            />
          : <EmptyState message="Nenhuma tabela Gold ativa disponível para análise pivot." />
      )}
      {tab === "busca" && (
        destinations.length > 0
          ? <SourceSearch destinations={destinations} initialTableId={destinations.find(d => d.name.toUpperCase() === "MODELO FOCUS")?.id} />
          : <EmptyState message="Nenhuma tabela Gold ativa disponível para busca." />
      )}
      </Paper>
      </Box>
    </Box>
  );
}
