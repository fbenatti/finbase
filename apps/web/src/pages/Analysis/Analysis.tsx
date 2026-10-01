import React, { useState, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box, Typography, Button, Card, CardContent, Chip, Alert,
  FormControl, InputLabel, Select, MenuItem, Tabs, Tab, Stack,
  TextField, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Paper, CircularProgress,
  TablePagination, TableSortLabel,
  Accordion, AccordionSummary, AccordionDetails,
} from "@mui/material";
import {
  Storage, AttachMoney, PlayArrow, Refresh, Delete,
  Schema as SchemaIcon, Tune, BarChart, Terminal, Search as SearchIcon, DeleteSweep, TableChart,
  ExpandMore,
  Add, Remove,
} from "@mui/icons-material";
import axios from "axios";
import * as XLSX from "xlsx";
import {
  getAnalysisHealth, getAnalysisTables, runAnalysisQuery,
  getAnalysisTablePartitions, AnalysisPartition, AnalysisTablePartitionResult,
  TrinoQueryResult, TrinoHealth, TrinoTableList,
  getAnalysisSources, queryAnalysisSource,
  getAnalysisDestinations, queryAnalysisDestination,
  SourceAnalysis, SourceDataResult,
  DestinationAnalysis, DestinationDataResult,
  formatApiError, PivotFilter, deleteGoldTable,
  AnalysisStagingTable, getAnalysisStagingTables, deleteAnalysisStagingTable,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import PivotAnalysis from "../../components/PivotAnalysis";
import SourceSearch from "../../components/SourceSearch";

const PAGE_SIZE = 50;

function filtersFromSearch(params: URLSearchParams): PivotFilter[] {
  const filters: PivotFilter[] = [];
  const path = params.get("path") || "";
  path.split(" > ").map(part => part.trim()).filter(Boolean).forEach((name, index) => {
    filters.push({ column: `organograma_nivel_${index + 1}`, values: [name], operator: "IN" });
  });
  const year = params.get("year");
  if (year) filters.push({ column: "_billing_period", values: [year], operator: "CONTAINS" });
  return filters;
}

type AnalysisTab = "destino" | "fontes" | "iceberg" | "staging" | "pivot" | "busca";

interface IcebergTableMember {
  tableName: string;
  period?: string;
  dimension?: string;
  scopeId?: string;
}

interface IcebergTableGroup {
  name: string;
  members: IcebergTableMember[];
}

function groupIcebergTables(names: string[]): IcebergTableGroup[] {
  const groups = new Map<string, IcebergTableGroup>();
  const costSlice = /^(finops_cost_driver_slice_azure_4p)_(\d{6})_(service|sub_account|region|organization)_(\d+)$/;
  for (const tableName of names) {
    const match = tableName.match(costSlice);
    const groupName = match?.[1] || tableName;
    const group = groups.get(groupName) || { name: groupName, members: [] };
    group.members.push(match
      ? { tableName, period: match[2], dimension: match[3], scopeId: match[4] }
      : { tableName });
    groups.set(groupName, group);
  }
  return [...groups.values()].map(group => ({
    ...group,
    members: group.members.sort((a, b) =>
      (b.period || "").localeCompare(a.period || "") ||
      (a.dimension || "").localeCompare(b.dimension || "") ||
      (a.scopeId || "").localeCompare(b.scopeId || "", undefined, { numeric: true }) ||
      a.tableName.localeCompare(b.tableName)),
  })).sort((a, b) => a.name.localeCompare(b.name));
}

// Pre-built queries by layer
const PREBUILT: Record<string, { label: string; sql: string }[]> = {
  bronze: [],
  silver: [],
  gold: [
    { label: "Top 10 serviços por custo", sql: 'SELECT "ServiceName", SUM("BilledCost") AS total_billed, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "ServiceName" ORDER BY total_billed DESC LIMIT 10' },
    { label: "Custo por departamento", sql: 'SELECT "organograma", SUM("BilledCost") AS total_billed, SUM("EffectiveCost") AS total_effective, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "organograma" ORDER BY total_billed DESC LIMIT 20' },
    { label: "Custo por período", sql: 'SELECT "_billing_period", SUM("BilledCost") AS total_billed, SUM("EffectiveCost") AS total_effective, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "_billing_period" ORDER BY "_billing_period" DESC' },
    { label: "Custo por região", sql: 'SELECT "RegionName", SUM("BilledCost") AS total_billed, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "RegionName" ORDER BY total_billed DESC LIMIT 20' },
    { label: "Custo por origem e mês", sql: 'SELECT "cloud_origem", "_billing_period", SUM("BilledCost") AS total_billed, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "cloud_origem", "_billing_period" ORDER BY "_billing_period" DESC, total_billed DESC' },
    { label: "Departamento / origem por mês", sql: 'SELECT "organograma", "cloud_origem", "_billing_period", SUM("BilledCost") AS total_billed, SUM("EffectiveCost") AS total_effective, COUNT(*) AS rows FROM iceberg.gold.dest_modelo_focus GROUP BY "organograma", "cloud_origem", "_billing_period" ORDER BY "_billing_period" DESC, total_billed DESC LIMIT 500' },
  ],
};

export default function Analysis() {
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<AnalysisTab>((searchParams.get("tab") as AnalysisTab) || "fontes");
  const [health, setHealth] = useState<TrinoHealth>({ trino: "?", nessie: "?", minio: "?" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // ─── Iceberg state ───
  const [layer, setLayer] = useState<"bronze" | "silver" | "gold">("gold");
  const [tables, setTables] = useState<TrinoTableList>({ bronze: [], silver: [], gold: [] });
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<TrinoQueryResult | null>(null);
  const [executedSql, setExecutedSql] = useState("");
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [stagingTables, setStagingTables] = useState<AnalysisStagingTable[]>([]);
  const [stagingLoading, setStagingLoading] = useState(false);
  const [selectedIcebergTable, setSelectedIcebergTable] = useState("");
  const [partitionRows, setPartitionRows] = useState<AnalysisPartition[]>([]);
  const [partitionsLoading, setPartitionsLoading] = useState(false);
  const [expandedTableGroups, setExpandedTableGroups] = useState<Set<string>>(new Set());
  const [openPartitionNodes, setOpenPartitionNodes] = useState<Set<string>>(new Set());
  const [tablePartitionDetails, setTablePartitionDetails] = useState<Record<string, AnalysisTablePartitionResult>>({});
  const [partitionDetailsLoading, setPartitionDetailsLoading] = useState<Set<string>>(new Set());

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
    getAnalysisStagingTables().then(setStagingTables).catch(() => {});
  }, []);

  const refreshLakehouse = async () => {
    try {
      const next = await getAnalysisDestinations();
      setDestinations(next);
      const [nextTables, nextStaging] = await Promise.all([
        getAnalysisTables(), getAnalysisStagingTables(),
      ]);
      setTables(nextTables);
      setStagingTables(nextStaging);
    } catch (e: unknown) {
      setError(formatApiError(e, "Falha ao atualizar o Data Lakehouse"));
    }
  };

  const removeStagingTable = async (item: AnalysisStagingTable) => {
    if (item.protected) return;
    if (!window.confirm(
      `Excluir permanentemente a staging ${item.table_name}?\n\n` +
      "A ação remove somente a tabela temporária; a Gold publicada não será alterada."
    )) return;
    setStagingLoading(true); setError("");
    try {
      await deleteAnalysisStagingTable(item.table_name);
      const [nextStaging, nextTables] = await Promise.all([
        getAnalysisStagingTables(), getAnalysisTables(),
      ]);
      setStagingTables(nextStaging);
      setTables(nextTables);
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível excluir a tabela staging"));
    } finally {
      setStagingLoading(false);
    }
  };

  const removeGoldTable = async (t: string) => {
    if (!window.confirm('Excluir a tabela ' + t + '? A regra Gold associada (se houver) e a tabela física no Iceberg serão removidas.')) return;
    setError("");
    try {
      await deleteGoldTable(t);
      getAnalysisTables().then(setTables).catch(() => {});
      if (sql.startsWith('SELECT * FROM iceberg.gold.' + t)) setSql("");
    } catch (e: unknown) {
      setError(formatApiError(e, 'Não foi possível excluir a tabela Gold'));
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
      setExecutedSql(q);
      setPage(0);
    } catch (e: unknown) {
      setError(formatApiError(e, "Query failed"));
    } finally { setLoading(false); }
  };

  const setPrebuilt = (q: string) => { setSql(q); executeSql(q); };

  const selectIcebergTable = async (tableName: string) => {
    setSelectedIcebergTable(tableName);
    setSql(`SELECT * FROM iceberg.${layer}.${tableName} LIMIT 10`);
    setPartitionRows([]);
    setPartitionsLoading(true);
    try {
      const metadata = await getAnalysisTablePartitions(layer, tableName);
      setPartitionRows(metadata.partitions || []);
    } catch (e: unknown) {
      if (axios.isAxiosError(e) && e.response?.status === 404) {
        getAnalysisTables().then(setTables).catch(() => {});
        return;
      }
      setError(formatApiError(e, "Não foi possível carregar as partições"));
    } finally { setPartitionsLoading(false); }
  };

  const loadTablePartitions = async (tableName: string) => {
    if (tablePartitionDetails[tableName] || partitionDetailsLoading.has(tableName)) return;
    setPartitionDetailsLoading(current => new Set(current).add(tableName));
    try {
      const metadata = await getAnalysisTablePartitions(layer, tableName);
      setTablePartitionDetails(current => ({ ...current, [tableName]: metadata }));
    } catch (e: unknown) {
      if (axios.isAxiosError(e) && e.response?.status === 404) {
        getAnalysisTables().then(setTables).catch(() => {});
        return;
      }
      setError(formatApiError(e, `Não foi possível carregar partições de ${tableName}`));
    } finally {
      setPartitionDetailsLoading(current => {
        const next = new Set(current);
        next.delete(tableName);
        return next;
      });
    }
  };

  const sqlLiteral = (value: string) => `'${value.replace(/'/g, "''")}'`;

  const useIcebergSlice = (tableName: string, partition?: Partial<AnalysisPartition>) => {
    const clauses: string[] = [];
    if (partition?.period && /^\d{8}$/.test(partition.period)) {
      clauses.push(`snapshot_date = ${sqlLiteral(partition.period)}`);
    } else if (partition?.period && /^\d{6}$/.test(partition.period)) {
      clauses.push(`_billing_period = ${sqlLiteral(partition.period)}`);
    }
    if (partition?.source_connection_id != null) {
      clauses.push(`source_connection_id = ${partition.source_connection_id}`);
    }
    if (partition?.subaccount_id) {
      clauses.push(`SubAccountId = ${sqlLiteral(partition.subaccount_id)}`);
    } else if (partition?.subaccount) {
      clauses.push(`SubAccountName = ${sqlLiteral(partition.subaccount)}`);
    }
    const where = clauses.length ? `\nWHERE ${clauses.join(" AND ")}` : "";
    setSelectedIcebergTable(tableName);
    setSql(`SELECT * FROM iceberg.${layer}.${tableName}${where}\nLIMIT 10`);
  };

  const togglePartitionNode = (key: string) => {
    setOpenPartitionNodes(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const icebergTableGroups = useMemo(
    () => groupIcebergTables(tables[layer] || []),
    [tables, layer],
  );

  const renderPartitionTree = (tableName: string, partitions: AnalysisPartition[]) => {
    const totals = (rows: AnalysisPartition[]) => ({
      records: rows.reduce((sum, row) => sum + row.record_count, 0),
      files: rows.reduce((sum, row) => sum + row.file_count, 0),
    });
    const byPeriod = new Map<string, AnalysisPartition[]>();
    partitions.forEach((row) => {
      const key = row.period || "—";
      const bucket = byPeriod.get(key) || [];
      bucket.push(row);
      byPeriod.set(key, bucket);
    });
    const periods = [...byPeriod.keys()].sort((a, b) => b.localeCompare(a));
    const hasAccount = partitions.some((row) => row.subaccount_id || row.subaccount);

    const rowBar = (label: string, meta: string, records: number, files: number, nodeKey: string | null, onUse: () => void, depth: number) => (
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, pl: depth * 2, py: 0.25 }}>
        {nodeKey ? (
          <Button size="small" variant="outlined" onClick={() => togglePartitionNode(nodeKey)} sx={{ minWidth: 28, width: 28, height: 28, p: 0 }} aria-label={openPartitionNodes.has(nodeKey) ? "Recolher" : "Expandir"}>
            {openPartitionNodes.has(nodeKey) ? <Remove fontSize="small" /> : <Add fontSize="small" />}
          </Button>
        ) : <Box sx={{ width: 28 }} />}
        <Typography variant="body2" sx={{ flex: 1, overflowWrap: "anywhere" }}>{label}</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>{meta}</Typography>
        <Typography variant="caption" sx={{ minWidth: 72, textAlign: "right" }}>{records.toLocaleString()} reg.</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 56, textAlign: "right" }}>{files.toLocaleString()} arq.</Typography>
        <Button size="small" onClick={onUse}>Usar</Button>
      </Box>
    );

    return (
      <Stack spacing={0.25}>
        {periods.map((period) => {
          const periodRows = byPeriod.get(period) || [];
          const periodKey = `${tableName}|${period}`;
          const periodOpen = openPartitionNodes.has(periodKey);
          const periodTotals = totals(periodRows);
          const bySource = new Map<string, AnalysisPartition[]>();
          periodRows.forEach((row) => {
            const sourceKey = row.source_label || (row.source_connection_id != null ? `conexão ${row.source_connection_id}` : "sem fonte");
            const bucket = bySource.get(sourceKey) || [];
            bucket.push(row);
            bySource.set(sourceKey, bucket);
          });
          return (
            <Box key={periodKey}>
              {rowBar(period, periodRows[0]?.period_kind || "período", periodTotals.records, periodTotals.files, periodKey, () => useIcebergSlice(tableName, { period }), 0)}
              {periodOpen && [...bySource.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([source, sourceRows]) => {
                const sourceKey = `${periodKey}|${source}`;
                const sourceOpen = openPartitionNodes.has(sourceKey);
                const sourceTotals = totals(sourceRows);
                const sample = sourceRows[0];
                return (
                  <Box key={sourceKey}>
                    {rowBar(source, "fonte", sourceTotals.records, sourceTotals.files, hasAccount ? sourceKey : null, () => useIcebergSlice(tableName, { period, source_connection_id: sample?.source_connection_id }), 1)}
                    {hasAccount && sourceOpen && sourceRows
                      .slice()
                      .sort((a, b) => (a.subaccount_id || a.subaccount || "").localeCompare(b.subaccount_id || b.subaccount || ""))
                      .map((row) => {
                        const account = row.subaccount_id || row.subaccount || "—";
                        return (
                          <Box key={`${sourceKey}|${account}`}>
                            {rowBar(account, row.subaccount_id ? "SubAccountId" : "conta", row.record_count, row.file_count, null, () => useIcebergSlice(tableName, row), 2)}
                          </Box>
                        );
                      })}
                  </Box>
                );
              })}
            </Box>
          );
        })}
      </Stack>
    );
  };

  const renderIcebergMember = (member: IcebergTableMember) => {
    const detail = tablePartitionDetails[member.tableName];
    return (
      <Paper key={member.tableName} variant="outlined" sx={{ p: 1, bgcolor: "action.hover" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <Typography variant="caption" sx={{ flex: 1, minWidth: 220, overflowWrap: "anywhere", fontFamily: "monospace" }}>
            {member.period ? `${member.period} · ${member.dimension} · ID ${member.scopeId}` : member.tableName}
          </Typography>
          <Button size="small" onClick={() => useIcebergSlice(member.tableName)}>Usar tabela</Button>
        </Box>
        <Box sx={{ mt: 1 }}>
          {!detail && partitionDetailsLoading.has(member.tableName) ? <CircularProgress size={18} /> : detail?.partitioned ? (
            detail.partitions.length ? renderPartitionTree(member.tableName, detail.partitions) : <Typography variant="caption" color="text.secondary">Nenhuma partição Iceberg.</Typography>
          ) : detail ? (
            <Alert severity="info" sx={{ py: 0 }}>Esta tabela física não tem partição Iceberg interna. {member.period ? `O período ${member.period} e o escopo estão codificados no nome; ` : ""}{detail.record_count?.toLocaleString() ?? 0} registros em {detail.file_count ?? 0} arquivo(s).
              <Button size="small" sx={{ ml: 1 }} onClick={() => useIcebergSlice(member.tableName)}>Usar tabela</Button>
            </Alert>
          ) : !partitionDetailsLoading.has(member.tableName) ? (
            <Alert severity="error" sx={{ py: 0 }}>Não foi possível carregar as partições. Recolha e expanda de novo.</Alert>
          ) : null}
        </Box>
      </Paper>
    );
  };

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

  const exportQueryResult = () => {
    if (!result) return;

    const matrix = [
      result.columns,
      ...filteredRows.map((row) => result.columns.map((column) => {
        const value = row[column];
        if (value === null || value === undefined) return "";
        if (typeof value === "object") return JSON.stringify(value);
        return value;
      })),
    ];
    const resultSheet = XLSX.utils.aoa_to_sheet(matrix);
    resultSheet["!views"] = [{ state: "frozen", ySplit: 1, topLeftCell: "A2", activePane: "bottomLeft" }];
    resultSheet["!cols"] = result.columns.map((column) => ({
      wch: Math.min(60, Math.max(12, column.length + 2)),
    }));

    const querySheet = XLSX.utils.aoa_to_sheet([
      ["SQL executado"],
      [executedSql],
      [],
      ["Exportado em", new Date().toLocaleString("pt-BR")],
      ["Registros exportados", filteredRows.length],
    ]);
    querySheet["!cols"] = [{ wch: 24 }, { wch: 24 }];

    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, resultSheet, "Resultado");
    XLSX.utils.book_append_sheet(workbook, querySheet, "Consulta");

    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    XLSX.writeFile(workbook, `resultado-query-${timestamp}.xlsx`);
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
  const goldTablesCount = useMemo(() => (tables.gold || []).length, [tables]);
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
          <Tab label={`Staging (${stagingTables.length})`} value="staging" icon={<DeleteSweep />} iconPosition="start" sx={{ minHeight: 42 }} />
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
            <Tabs value={layer} onChange={(_, v) => { setLayer(v); setSelectedIcebergTable(""); setPartitionRows([]); setResult(null); setError(""); }}>
              <Tab label={`Bronze (${(tables.bronze || []).length})`} value="bronze" icon={<Storage />} iconPosition="start" />
              <Tab label={`Silver (${(tables.silver || []).length})`} value="silver" icon={<Tune />} iconPosition="start" />
              <Tab label={`Gold (${(tables.gold || []).length})`} value="gold" icon={<AttachMoney />} iconPosition="start" />
            </Tabs>
          </Paper>

          {layer === "gold" && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5, flexWrap: "wrap" }}>
                <SchemaIcon color="primary" fontSize="small" />
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Gold — tabelas pré-agregadas (dest_dm_cost_*)</Typography>
                <Chip label={`${goldTablesCount} tabela(s)`} size="small" color="primary" variant="outlined" />
              </Box>
              {goldTablesCount === 0 ? (
                <Alert severity="info" sx={{ mb: 2 }}>Nenhuma tabela Gold gerada ainda. Ela é criada automaticamente ao final do reprocess do destino MODELO FOCUS.</Alert>
              ) : (
                <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
                  {(tables.gold || []).map((t) => (
                    <Chip key={t} label={t} size="small" icon={<SchemaIcon />} variant={selectedIcebergTable === t ? "filled" : "outlined"}
                      color={selectedIcebergTable === t ? "primary" : "default"}
                      onClick={() => void selectIcebergTable(t)} sx={{ cursor: "pointer" }}
                      onDelete={() => void removeGoldTable(t)} deleteIcon={<Delete fontSize="small" />} />
                  ))}
                </Box>
              )}
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
                Clique em uma tabela para montar a query e depois em <b>Run</b> — mesmo comportamento da camada Gold. As cubos Gold são pré-agregadas (mensal) com medidas <b>quantity</b>, <b>realizado</b> e <b>billedcost</b>.
              </Typography>
            </>
          )}
          {layer !== "gold" && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5, flexWrap: "wrap" }}>
                <SchemaIcon color="primary" fontSize="small" />
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>{layer === "bronze" ? "Bronze" : "Silver"} — grupos e tabelas</Typography>
                <Chip label={`${(tables[layer] || []).length} tabela(s) física(s)`} size="small" color="primary" variant="outlined" />
                <Chip label={`${icebergTableGroups.length} grupo(s)`} size="small" variant="outlined" />
              </Box>
              <Stack spacing={0.75} sx={{ mb: 2 }}>
                {icebergTableGroups.map((group) => {
                  const expanded = expandedTableGroups.has(group.name);
                  const isFamily = group.members.length > 1;
                  const toggleGroup = () => {
                    const opening = !expandedTableGroups.has(group.name);
                    setExpandedTableGroups(current => {
                      const next = new Set(current);
                      if (next.has(group.name)) next.delete(group.name); else next.add(group.name);
                      return next;
                    });
                    if (opening) group.members.forEach((member) => { void loadTablePartitions(member.tableName); });
                  };
                  return (
                    <Paper key={group.name} variant="outlined" sx={{ borderRadius: 1.5, overflow: "hidden" }}>
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 1.25, py: 0.75 }}>
                        <Button size="small" variant="outlined" onClick={toggleGroup} sx={{ minWidth: 34, width: 34, height: 32, p: 0 }} aria-label={`${expanded ? "Recolher" : "Expandir"} ${group.name}`}>
                          {expanded ? <Remove fontSize="small" /> : <Add fontSize="small" />}
                        </Button>
                        <Typography variant="body2" sx={{ fontWeight: 600, flex: 1, overflowWrap: "anywhere" }}>{group.name}</Typography>
                        <Chip size="small" variant="outlined" label={isFamily ? `${group.members.length} tabelas · períodos no nome` : "1 tabela"} />
                      </Box>
                      {expanded && (
                        <Stack spacing={0.5} sx={{ px: 2, pb: 1.25, pt: 0.25 }}>
                          {isFamily
                            ? Object.entries(group.members.reduce<Record<string, IcebergTableMember[]>>((byPeriod, member) => {
                                const period = member.period || "Outros";
                                (byPeriod[period] ||= []).push(member);
                                return byPeriod;
                              }, {})).sort(([a], [b]) => b.localeCompare(a)).map(([period, members]) => (
                                <Accordion key={period} disableGutters variant="outlined" sx={{ borderRadius: 1.5 }}>
                                  <AccordionSummary expandIcon={<ExpandMore />}>
                                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{period} <Typography component="span" variant="caption" color="text.secondary">· {members.length} tabela(s)</Typography></Typography>
                                  </AccordionSummary>
                                  <AccordionDetails><Stack spacing={0.5}>{members.map(renderIcebergMember)}</Stack></AccordionDetails>
                                </Accordion>
                              ))
                            : group.members.map(renderIcebergMember)}
                        </Stack>
                      )}
                    </Paper>
                  );
                })}
              </Stack>
            </>
          )}
          {layer === "gold" && selectedIcebergTable && (
            <Accordion disableGutters sx={{ mb: 2, border: "1px solid", borderColor: "divider", borderRadius: "8px !important" }}>
              <AccordionSummary expandIcon={<ExpandMore />}><Typography variant="body2" sx={{ fontWeight: 600 }}>Partições de {selectedIcebergTable}</Typography></AccordionSummary>
              <AccordionDetails>
                {partitionsLoading ? <CircularProgress size={20} /> : partitionRows.length ? (
                  <Table size="small"><TableHead><TableRow><TableCell>Dia / período (UTC quando diário)</TableCell><TableCell>Tipo</TableCell><TableCell align="right">Registros</TableCell><TableCell align="right">Arquivos</TableCell></TableRow></TableHead>
                    <TableBody>{partitionRows.map((p) => <TableRow key={`${p.period}-${p.period_kind}`}><TableCell>{p.period || "—"}</TableCell><TableCell>{p.period_kind}</TableCell><TableCell align="right">{p.record_count.toLocaleString()}</TableCell><TableCell align="right">{p.file_count.toLocaleString()}</TableCell></TableRow>)}</TableBody></Table>
                ) : <Typography variant="body2" color="text.secondary">Nenhuma partição registrada.</Typography>}
              </AccordionDetails>
            </Accordion>
          )}

          <Paper elevation={0} sx={{ p: 2, mb: 2, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Box sx={{ display: "flex", gap: 1, mb: 1, flexWrap: "wrap" }}>
              {((layer === "bronze" || layer === "silver")
                ? (selectedIcebergTable ? [
                    { label: "Amostra da tabela selecionada", sql: `SELECT * FROM iceberg.${layer}.${selectedIcebergTable} LIMIT 10` },
                    { label: "Contagem por período", sql: `SELECT _billing_period, COUNT(*) AS rows FROM iceberg.${layer}.${selectedIcebergTable} GROUP BY _billing_period ORDER BY _billing_period DESC` },
                  ] : [])
                : layer === "gold"
                  ? (selectedIcebergTable ? (PREBUILT.gold || []).map(q => ({
                      ...q,
                      sql: q.sql.split("iceberg.gold.dest_modelo_focus").join(`iceberg.gold.${selectedIcebergTable}`),
                    })) : [])
                  : (PREBUILT[layer] || [])).map((q) => (
                <Button key={q.label} size="small" variant="outlined" onClick={() => setPrebuilt(q.sql)}>{q.label}</Button>
              ))}
            </Box>
            <Box sx={{ display: "flex", gap: 1 }}>
              <TextField fullWidth multiline minRows={2} maxRows={6} size="small"
                placeholder={layer === "gold" ? "SELECT * FROM iceberg.gold.dest_dm_cost_<code> LIMIT 10" : "SELECT * FROM iceberg." + layer + "." + ((tables[layer] || [])[0] || "...") + " LIMIT 10"}
                value={sql} onChange={(e) => setSql(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && e.metaKey && executeSql()}
                sx={{ fontFamily: "monospace", "& textarea": { fontSize: "0.85rem" } }} />
              <Button variant="contained" startIcon={<PlayArrow />} onClick={() => executeSql()}
                disabled={loading || !sql.trim()} sx={{ minWidth: 100 }}>Run</Button>
              <Button
                variant="outlined"
                startIcon={<TableChart />}
                onClick={exportQueryResult}
                disabled={!result || filteredRows.length === 0}
                sx={{ minWidth: 160 }}
              >
                Exportar Excel
              </Button>
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
      {/* TAB: STAGING MAINTENANCE                                         */}
      {/* ================================================================ */}
      {tab === "staging" && (
        <>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 2, mb: 2, flexWrap: "wrap" }}>
            <Box>
              <Typography variant="h6">Tabelas temporárias de reprocessamento</Typography>
              <Typography variant="body2" color="text.secondary">
                Stagings órfãs podem ser removidas sem alterar a Gold publicada. Tabelas de destinos com reprocessamento ativo ficam protegidas.
              </Typography>
            </Box>
            <Button
              size="small"
              variant="outlined"
              startIcon={stagingLoading ? <CircularProgress size={16} /> : <Refresh />}
              disabled={stagingLoading}
              onClick={async () => {
                setStagingLoading(true); setError("");
                try { setStagingTables(await getAnalysisStagingTables()); }
                catch (e: unknown) { setError(formatApiError(e, "Falha ao listar tabelas staging")); }
                finally { setStagingLoading(false); }
              }}
            >
              Atualizar
            </Button>
          </Box>

          {stagingTables.length === 0 ? (
            <Alert severity="success">Nenhuma tabela staging pendente de limpeza.</Alert>
          ) : (
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Tabela</TableCell>
                    <TableCell>Destino</TableCell>
                    <TableCell>Criada em</TableCell>
                    <TableCell align="right">Linhas</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell align="right">Ação</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {stagingTables.map((item) => (
                    <TableRow key={item.table_name} hover>
                      <TableCell sx={{ fontFamily: "monospace", fontSize: "0.78rem" }}>{item.table_name}</TableCell>
                      <TableCell sx={{ fontFamily: "monospace", fontSize: "0.78rem" }}>{item.base_table}</TableCell>
                      <TableCell>{new Date(item.created_at).toLocaleString("pt-BR")}</TableCell>
                      <TableCell align="right">{item.row_count == null ? "—" : item.row_count.toLocaleString("pt-BR")}</TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          label={item.protected ? "Reprocessamento ativo" : "Órfã"}
                          color={item.protected ? "warning" : "default"}
                          variant="outlined"
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Button
                          size="small"
                          color="error"
                          startIcon={<Delete />}
                          disabled={stagingLoading || item.protected}
                          onClick={() => void removeStagingTable(item)}
                        >
                          Excluir
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
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
              onOpenQuery={query => {
                setLayer("gold");
                setSql(query);
                setResult(null);
                setExecutedSql("");
                setError("");
                setTab("iceberg");
              }}
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
