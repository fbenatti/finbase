import { Dialog, DialogTitle, DialogContent, DialogActions } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect, useCallback, useMemo } from "react";
import {
  Box, Typography, Button, IconButton, Chip, TextField, Select, MenuItem, FormControl, InputLabel,
  Alert, CircularProgress, Paper, Stack, Drawer, Tabs, Tab, InputAdornment, ToggleButton,
  ToggleButtonGroup, Table, TableHead, TableBody, TableRow, TableCell, Tooltip, Divider,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import {
  AddOutlined, DeleteOutlined, TableChartOutlined, RefreshOutlined, SearchOutlined, CloseOutlined,
  ViewListOutlined, GridViewOutlined, HubOutlined, ArrowForwardOutlined, StorageOutlined,
} from "@mui/icons-material";
import {
  getDestinationTables, getDestinationTable,
  addDestinationColumn, deleteDestinationColumn, createDestinationTable,
  getSourceDestMappings,
  DestinationTable, DestinationColumn, DestinationTableDetail, SourceDestinationMapping,
  getConnections, SourceConnection,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import DestinationPartitionSettings from "../../components/DestinationPartitionSettings";

const DATA_TYPES = ["TEXT", "VARCHAR(50)", "VARCHAR(100)", "VARCHAR(200)", "NUMERIC", "DATE", "TIMESTAMPTZ", "JSONB", "BOOLEAN"];

type StatusFilter = "all" | "active" | "inactive";
type ViewMode = "list" | "cards";
type DetailTab = "fields" | "mappings" | "diagram" | "partition";

interface TableRowModel {
  table: DestinationTable;
  fieldCount: number | null;
  mappingCount: number;
  sourceIds: number[];
}

const fmtDate = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
};

const partitionLabel = (t: DestinationTable) => {
  const spec = t.partition_spec_json;
  if (!spec) return "Sem partição";
  return spec.grain === "snapshot_date" ? "Por data de foto" : "Por período de cobrança";
};

const typeFamily = (dt: string) => {
  const t = dt.toUpperCase();
  if (t.startsWith("NUMERIC") || t.includes("INT") || t.includes("DECIMAL")) return "número";
  if (t.startsWith("DATE") || t.startsWith("TIMESTAMP")) return "data";
  if (t.startsWith("BOOL")) return "lógico";
  if (t.startsWith("JSON")) return "estrutura";
  return "texto";
};

function Kpi({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.75, flex: "1 1 160px", minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 600, lineHeight: 1.3 }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </Paper>
  );
}

/** Diagrama origem → tabela de destino. Origens à esquerda, tabelas à direita, curvas com nº de campos. */
function RelationDiagram({
  links, sources, targets, highlightTarget, onSelectTarget,
}: {
  links: { sourceId: number; targetId: number; count: number }[];
  sources: { id: number; label: string; sub?: string }[];
  targets: { id: number; label: string; sub?: string }[];
  highlightTarget?: number | null;
  onSelectTarget?: (id: number) => void;
}) {
  const theme = useTheme();
  const rowH = 52;
  const nodeW = 230;
  const width = 760;
  const height = Math.max(sources.length, targets.length, 1) * rowH + 24;
  const yOf = (i: number, n: number) => 12 + (height - 24 - n * rowH) / 2 + i * rowH + rowH / 2;
  const sIdx = new Map(sources.map((s, i) => [s.id, i]));
  const tIdx = new Map(targets.map((t, i) => [t.id, i]));
  const maxCount = Math.max(1, ...links.map((l) => l.count));
  const primary = theme.palette.primary.main;
  const muted = theme.palette.text.secondary;
  const border = theme.palette.divider;
  const paper = theme.palette.background.paper;

  if (links.length === 0) {
    return <EmptyState message="Nenhuma origem vinculada ainda. Crie os vínculos na aba Relações entre tabelas." />;
  }

  const node = (x: number, y: number, label: string, sub: string | undefined, active: boolean, key: string, onClick?: () => void) => (
    <g key={key} transform={`translate(${x},${y - 20})`} style={{ cursor: onClick ? "pointer" : "default" }} onClick={onClick}>
      <rect width={nodeW} height={40} rx={8} fill={active ? alpha(primary, 0.08) : paper} stroke={active ? primary : border} />
      <text x={12} y={17} fontSize={12} fontWeight={600} fill={theme.palette.text.primary}>
        {label.length > 30 ? `${label.slice(0, 29)}…` : label}
      </text>
      {sub && <text x={12} y={31} fontSize={10} fill={muted} fontFamily="monospace">{sub.length > 34 ? `${sub.slice(0, 33)}…` : sub}</text>}
    </g>
  );

  return (
    <Box sx={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ minWidth: 560, display: "block" }} role="img" aria-label="Diagrama de relações entre origens e tabelas de destino">
        {links.map((l) => {
          const si = sIdx.get(l.sourceId); const ti = tIdx.get(l.targetId);
          if (si === undefined || ti === undefined) return null;
          const y1 = yOf(si, sources.length); const y2 = yOf(ti, targets.length);
          const x1 = nodeW; const x2 = width - nodeW;
          const mid = (x1 + x2) / 2;
          const active = highlightTarget == null || highlightTarget === l.targetId;
          return (
            <g key={`${l.sourceId}-${l.targetId}`}>
              <path d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none"
                stroke={active ? primary : border} strokeOpacity={active ? 0.55 : 0.6}
                strokeWidth={1.5 + (l.count / maxCount) * 4} />
              <g transform={`translate(${mid},${(y1 + y2) / 2})`}>
                <rect x={-16} y={-9} width={32} height={18} rx={9} fill={paper} stroke={active ? primary : border} />
                <text textAnchor="middle" y={4} fontSize={10} fontWeight={600} fill={active ? primary : muted}>{l.count}</text>
              </g>
            </g>
          );
        })}
        {sources.map((s, i) => node(0, yOf(i, sources.length), s.label, s.sub, false, `s${s.id}`))}
        {targets.map((t, i) => node(width - nodeW, yOf(i, targets.length), t.label, t.sub, highlightTarget === t.id,
          `t${t.id}`, onSelectTarget ? () => onSelectTarget(t.id) : undefined))}
      </svg>
      <Stack direction="row" justifyContent="space-between" sx={{ px: 0.5, mt: 0.5 }}>
        <Typography variant="caption" color="text.secondary">Origens (conexões)</Typography>
        <Typography variant="caption" color="text.secondary">Número na linha = campos vinculados</Typography>
        <Typography variant="caption" color="text.secondary">Tabelas de destino</Typography>
      </Stack>
    </Box>
  );
}

export default function Destinations({ embedded: _embedded }: { embedded?: boolean } = {}) {
  const theme = useTheme();
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [fieldCounts, setFieldCounts] = useState<Map<number, number>>(new Map());
  const [allMappings, setAllMappings] = useState<SourceDestinationMapping[]>([]);
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [view, setView] = useState<ViewMode>("list");

  const [selectedTable, setSelectedTable] = useState<DestinationTableDetail | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("fields");
  const [fieldSearch, setFieldSearch] = useState("");

  const [colDialogOpen, setColDialogOpen] = useState(false);
  const [newCol, setNewCol] = useState({ column_name: "", data_type: "TEXT", is_required: false });
  const [tableDialogOpen, setTableDialogOpen] = useState(false);
  const [newTable, setNewTable] = useState({ name: "", description: "" });

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [tbls, maps, conns] = await Promise.all([
        getDestinationTables(false),
        getSourceDestMappings().catch(() => [] as SourceDestinationMapping[]),
        getConnections().catch(() => [] as SourceConnection[]),
      ]);
      setTables(tbls);
      setAllMappings(maps);
      setConnections(conns);
      // Contagem de campos por tabela (catálogo pequeno: uma chamada por tabela, em paralelo).
      const details = await Promise.all(tbls.map((t) => getDestinationTable(t.id).catch(() => null)));
      const counts = new Map<number, number>();
      details.forEach((d) => { if (d) counts.set(d.id, d.columns.length); });
      setFieldCounts(counts);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar o catálogo");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const connById = useMemo(() => new Map(connections.map((c) => [c.id, c])), [connections]);

  const rows: TableRowModel[] = useMemo(() => tables.map((t) => {
    const maps = allMappings.filter((m) => m.destination_table_id === t.id);
    return {
      table: t,
      fieldCount: fieldCounts.has(t.id) ? fieldCounts.get(t.id)! : null,
      mappingCount: maps.length,
      sourceIds: Array.from(new Set(maps.map((m) => m.source_connection_id))),
    };
  }), [tables, allMappings, fieldCounts]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter(({ table, sourceIds }) => {
      if (status === "active" && !table.active) return false;
      if (status === "inactive" && table.active) return false;
      if (!q) return true;
      const sourceNames = sourceIds.map((id) => connById.get(id)?.name || "").join(" ");
      return `${table.name} ${table.physical_name} ${table.description || ""} ${sourceNames}`.toLowerCase().includes(q);
    });
  }, [rows, search, status, connById]);

  const totals = useMemo(() => ({
    tables: tables.length,
    active: tables.filter((t) => t.active).length,
    fields: Array.from(fieldCounts.values()).reduce((a, b) => a + b, 0),
    mappings: allMappings.length,
    sources: new Set(allMappings.map((m) => m.source_connection_id)).size,
    orphan: rows.filter((r) => r.mappingCount === 0).length,
  }), [tables, fieldCounts, allMappings, rows]);

  const openTable = async (tableId: number, tab: DetailTab = "fields") => {
    try {
      setSelectedTable(await getDestinationTable(tableId));
      setDetailTab(tab);
      setFieldSearch("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar a tabela");
    }
  };

  const refreshSelected = async () => {
    if (!selectedTable) return;
    const detail = await getDestinationTable(selectedTable.id);
    setSelectedTable(detail);
    setFieldCounts((prev) => new Map(prev).set(detail.id, detail.columns.length));
  };

  const doAddColumn = async () => {
    if (!selectedTable || !newCol.column_name) return;
    try {
      await addDestinationColumn(selectedTable.id, newCol);
      setColDialogOpen(false);
      setNewCol({ column_name: "", data_type: "TEXT", is_required: false });
      await refreshSelected();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível adicionar o campo");
    }
  };

  const doDeleteColumn = async (col: DestinationColumn) => {
    if (!confirm(`Remover o campo "${col.column_name}"?`)) return;
    try {
      await deleteDestinationColumn(col.id);
      await refreshSelected();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível remover o campo");
    }
  };

  const doCreateTable = async () => {
    if (!newTable.name) return;
    try {
      await createDestinationTable(newTable);
      setTableDialogOpen(false);
      setNewTable({ name: "", description: "" });
      loadAll();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível criar a tabela");
    }
  };

  // Diagrama geral: todas as origens → tabelas visíveis no filtro.
  const overview = useMemo(() => {
    const visible = new Set(filtered.map((r) => r.table.id));
    const linkMap = new Map<string, { sourceId: number; targetId: number; count: number }>();
    allMappings.forEach((m) => {
      if (!visible.has(m.destination_table_id)) return;
      const k = `${m.source_connection_id}-${m.destination_table_id}`;
      const cur = linkMap.get(k) || { sourceId: m.source_connection_id, targetId: m.destination_table_id, count: 0 };
      cur.count += 1; linkMap.set(k, cur);
    });
    const links = Array.from(linkMap.values());
    const srcIds = Array.from(new Set(links.map((l) => l.sourceId)));
    const tgtIds = Array.from(new Set(links.map((l) => l.targetId)));
    return {
      links,
      sources: srcIds.map((id) => ({ id, label: connById.get(id)?.name || `Conexão nº ${id}`, sub: connById.get(id)?.connector_type })),
      targets: tgtIds.map((id) => {
        const t = tables.find((x) => x.id === id);
        return { id, label: t?.name || `Tabela nº ${id}`, sub: t?.physical_name };
      }),
    };
  }, [filtered, allMappings, connById, tables]);

  const selectedMappings = useMemo(
    () => (selectedTable ? allMappings.filter((m) => m.destination_table_id === selectedTable.id) : []),
    [selectedTable, allMappings],
  );
  const mappedDestCols = useMemo(() => new Set(selectedMappings.map((m) => m.destination_column)), [selectedMappings]);

  const selectedDiagram = useMemo(() => {
    if (!selectedTable) return null;
    const counts = new Map<number, number>();
    selectedMappings.forEach((m) => counts.set(m.source_connection_id, (counts.get(m.source_connection_id) || 0) + 1));
    return {
      links: Array.from(counts.entries()).map(([sourceId, count]) => ({ sourceId, targetId: selectedTable.id, count })),
      sources: Array.from(counts.keys()).map((id) => ({ id, label: connById.get(id)?.name || `Conexão nº ${id}`, sub: connById.get(id)?.connector_type })),
      targets: [{ id: selectedTable.id, label: selectedTable.name, sub: selectedTable.physical_name }],
    };
  }, [selectedTable, selectedMappings, connById]);

  const sourceChips = (ids: number[], max = 2) => (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {ids.length === 0 && <Typography variant="caption" color="text.disabled">Sem origem</Typography>}
      {ids.slice(0, max).map((id) => (
        <Chip key={id} size="small" variant="outlined" label={connById.get(id)?.name || `Conexão nº ${id}`} sx={{ maxWidth: 160 }} />
      ))}
      {ids.length > max && <Chip size="small" label={`+${ids.length - max}`} />}
    </Stack>
  );

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  const visibleFields = selectedTable
    ? selectedTable.columns.filter((c) => c.column_name.toLowerCase().includes(fieldSearch.trim().toLowerCase()))
    : [];

  return (
    <Stack spacing={2.5}>
      {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      {/* Indicadores do catálogo */}
      <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
        <Kpi label="Tabelas de saída" value={totals.tables} hint={`${totals.active} em uso`} />
        <Kpi label="Campos catalogados" value={totals.fields.toLocaleString("pt-BR")} />
        <Kpi label="Origens vinculadas" value={totals.sources} hint="conexões que alimentam o catálogo" />
        <Kpi label="Vínculos de campo" value={totals.mappings.toLocaleString("pt-BR")} hint={totals.orphan ? `${totals.orphan} tabela(s) sem origem` : "todas as tabelas têm origem"} />
      </Stack>

      {/* Barra de ferramentas */}
      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "center" }}>
        <TextField
          size="small" placeholder="Buscar por tabela, nome físico ou origem" value={search}
          onChange={(e) => setSearch(e.target.value)} sx={{ flex: 1, minWidth: 220 }}
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment> }}
        />
        <ToggleButtonGroup size="small" exclusive value={status} onChange={(_, v) => v && setStatus(v)}>
          <ToggleButton value="all">Todas</ToggleButton>
          <ToggleButton value="active">Em uso</ToggleButton>
          <ToggleButton value="inactive">Fora de uso</ToggleButton>
        </ToggleButtonGroup>
        <ToggleButtonGroup size="small" exclusive value={view} onChange={(_, v) => v && setView(v)}>
          <ToggleButton value="list" aria-label="Ver em lista"><ViewListOutlined fontSize="small" /></ToggleButton>
          <ToggleButton value="cards" aria-label="Ver em cartões"><GridViewOutlined fontSize="small" /></ToggleButton>
        </ToggleButtonGroup>
        <Tooltip title="Atualizar"><IconButton onClick={loadAll}><RefreshOutlined /></IconButton></Tooltip>
        <Button variant="contained" startIcon={<AddOutlined />} onClick={() => setTableDialogOpen(true)}>Nova tabela</Button>
      </Stack>

      {/* Lista / cartões */}
      {filtered.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 4 }}>
          <EmptyState message={tables.length === 0 ? "Nenhuma tabela de destino criada." : "Nenhuma tabela corresponde à busca."} />
        </Paper>
      ) : view === "list" ? (
        <Paper variant="outlined" sx={{ overflowX: "auto" }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Tabela</TableCell>
                <TableCell align="right">Campos</TableCell>
                <TableCell>Origens</TableCell>
                <TableCell align="right">Vínculos</TableCell>
                <TableCell>Partição</TableCell>
                <TableCell>Atualizada em</TableCell>
                <TableCell>Situação</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map(({ table: t, fieldCount, mappingCount, sourceIds }) => (
                <TableRow key={t.id} hover selected={selectedTable?.id === t.id} onClick={() => openTable(t.id)} sx={{ cursor: "pointer" }}>
                  <TableCell sx={{ minWidth: 240 }}>
                    <Stack direction="row" spacing={1.25} alignItems="center">
                      <Box sx={{ width: 32, height: 32, borderRadius: 1, display: "grid", placeItems: "center", bgcolor: alpha(theme.palette.primary.main, 0.08), color: "primary.main", flexShrink: 0 }}>
                        <TableChartOutlined fontSize="small" />
                      </Box>
                      <Box sx={{ minWidth: 0 }}>
                        <Typography variant="body2" fontWeight={600} noWrap>{t.name}</Typography>
                        <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }} noWrap>{t.physical_name}</Typography>
                      </Box>
                    </Stack>
                  </TableCell>
                  <TableCell align="right">{fieldCount ?? "—"}</TableCell>
                  <TableCell sx={{ minWidth: 180 }}>{sourceChips(sourceIds)}</TableCell>
                  <TableCell align="right">{mappingCount}</TableCell>
                  <TableCell><Typography variant="caption">{partitionLabel(t)}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{fmtDate(t.updated_at)}</Typography></TableCell>
                  <TableCell>
                    <Chip size="small" label={t.active ? "Em uso" : "Fora de uso"} color={t.active ? "success" : "default"} variant="outlined" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      ) : (
        <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(3, 1fr)", xl: "repeat(4, 1fr)" } }}>
          {filtered.map(({ table: t, fieldCount, mappingCount, sourceIds }) => (
            <Paper key={t.id} variant="outlined" onClick={() => openTable(t.id)}
              sx={{ p: 2, cursor: "pointer", display: "flex", flexDirection: "column", gap: 1.25, transition: "border-color .15s, box-shadow .15s",
                borderColor: selectedTable?.id === t.id ? "primary.main" : "divider",
                "&:hover": { borderColor: "primary.main", boxShadow: `0 2px 10px ${alpha(theme.palette.primary.main, 0.12)}` } }}>
              <Stack direction="row" spacing={1} alignItems="flex-start">
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="subtitle2" noWrap>{t.name}</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }} noWrap component="div">{t.physical_name}</Typography>
                </Box>
                <Chip size="small" label={t.active ? "Em uso" : "Fora de uso"} color={t.active ? "success" : "default"} variant="outlined" />
              </Stack>
              {t.description && <Typography variant="body2" color="text.secondary" sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{t.description}</Typography>}
              <Stack direction="row" spacing={2} divider={<Divider orientation="vertical" flexItem />}>
                <Box><Typography variant="caption" color="text.secondary">Campos</Typography><Typography fontWeight={600}>{fieldCount ?? "—"}</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Origens</Typography><Typography fontWeight={600}>{sourceIds.length}</Typography></Box>
                <Box><Typography variant="caption" color="text.secondary">Vínculos</Typography><Typography fontWeight={600}>{mappingCount}</Typography></Box>
              </Stack>
              {sourceChips(sourceIds, 3)}
              <Typography variant="caption" color="text.secondary" sx={{ mt: "auto" }}>{partitionLabel(t)} · atualizada em {fmtDate(t.updated_at)}</Typography>
            </Paper>
          ))}
        </Box>
      )}

      {/* Mapa de relações */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
          <HubOutlined fontSize="small" color="primary" />
          <Typography variant="subtitle2">Mapa de relações</Typography>
          <Typography variant="caption" color="text.secondary">De qual origem vem cada tabela de saída. Clique numa tabela para abrir o detalhe.</Typography>
        </Stack>
        <RelationDiagram {...overview} highlightTarget={selectedTable?.id ?? null} onSelectTarget={(id) => openTable(id, "diagram")} />
      </Paper>

      {/* Detalhe da tabela */}
      <Drawer anchor="right" open={!!selectedTable} onClose={() => setSelectedTable(null)}
        PaperProps={{ sx: { width: { xs: "100%", md: 720 }, maxWidth: "100%" } }}>
        {selectedTable && (
          <Stack sx={{ height: "100%" }}>
            <Box sx={{ p: 2.5, borderBottom: 1, borderColor: "divider" }}>
              <Stack direction="row" spacing={1.5} alignItems="flex-start">
                <Box sx={{ width: 40, height: 40, borderRadius: 1, display: "grid", placeItems: "center", bgcolor: alpha(theme.palette.primary.main, 0.08), color: "primary.main", flexShrink: 0 }}>
                  <StorageOutlined />
                </Box>
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="h6" noWrap>{selectedTable.name}</Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ fontFamily: "monospace" }}>{selectedTable.physical_name}</Typography>
                  {selectedTable.description && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{selectedTable.description}</Typography>}
                </Box>
                <IconButton aria-label="Fechar" onClick={() => setSelectedTable(null)}><CloseOutlined /></IconButton>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
                <Chip size="small" label={`${selectedTable.columns.length} campos`} />
                <Chip size="small" label={`${selectedMappings.length} vínculos`} />
                <Chip size="small" label={`${selectedDiagram?.sources.length ?? 0} origens`} />
                <Chip size="small" variant="outlined" label={partitionLabel(selectedTable)} />
                <Chip size="small" variant="outlined" label={`Atualizada em ${fmtDate(selectedTable.updated_at)}`} />
              </Stack>
            </Box>
            <Tabs value={detailTab} onChange={(_, v) => setDetailTab(v)} sx={{ px: 2, borderBottom: 1, borderColor: "divider" }} variant="scrollable">
              <Tab value="fields" label="Campos" />
              <Tab value="mappings" label="Mapeamentos" />
              <Tab value="diagram" label="Relações" />
              <Tab value="partition" label="Partição" />
            </Tabs>
            <Box sx={{ p: 2.5, overflow: "auto", flex: 1 }}>
              {detailTab === "fields" && (
                <Stack spacing={1.5}>
                  <Stack direction="row" spacing={1}>
                    <TextField size="small" placeholder="Buscar campo" value={fieldSearch} onChange={(e) => setFieldSearch(e.target.value)} sx={{ flex: 1 }}
                      InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment> }} />
                    <Button variant="contained" startIcon={<AddOutlined />} onClick={() => setColDialogOpen(true)}>Novo campo</Button>
                  </Stack>
                  {visibleFields.length === 0 ? (
                    <EmptyState message={selectedTable.columns.length === 0 ? "Nenhum campo definido." : "Nenhum campo corresponde à busca."} />
                  ) : (
                    <Paper variant="outlined">
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell width={48}>#</TableCell>
                            <TableCell>Campo</TableCell>
                            <TableCell>Tipo</TableCell>
                            <TableCell>Regras</TableCell>
                            <TableCell width={48} />
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {visibleFields.map((col) => (
                            <TableRow key={col.id} hover>
                              <TableCell><Typography variant="caption" color="text.secondary">{col.position}</Typography></TableCell>
                              <TableCell sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{col.column_name}</TableCell>
                              <TableCell>
                                <Tooltip title={typeFamily(col.data_type)}><Chip size="small" variant="outlined" label={col.data_type} /></Tooltip>
                              </TableCell>
                              <TableCell>
                                <Stack direction="row" spacing={0.5}>
                                  {col.is_required && <Chip size="small" color="warning" variant="outlined" label="Não nulo" />}
                                  {mappedDestCols.has(col.column_name)
                                    ? <Chip size="small" color="primary" variant="outlined" label="Vinculado" />
                                    : <Chip size="small" variant="outlined" label="Sem origem" />}
                                </Stack>
                              </TableCell>
                              <TableCell>
                                <IconButton size="small" color="error" aria-label={`Remover ${col.column_name}`} onClick={() => doDeleteColumn(col)}>
                                  <DeleteOutlined fontSize="small" />
                                </IconButton>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </Paper>
                  )}
                </Stack>
              )}

              {detailTab === "mappings" && (
                selectedMappings.length === 0 ? (
                  <EmptyState message="Nenhum campo de origem vinculado a esta tabela. Use a aba Relações entre tabelas para criar os vínculos." />
                ) : (
                  <Paper variant="outlined">
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Origem</TableCell>
                          <TableCell>Campo de origem</TableCell>
                          <TableCell width={24} />
                          <TableCell>Campo de destino</TableCell>
                          <TableCell>Transformação</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {selectedMappings.map((m) => (
                          <TableRow key={m.id} hover sx={{ opacity: m.active ? 1 : 0.5 }}>
                            <TableCell><Typography variant="caption">{connById.get(m.source_connection_id)?.name || `Conexão nº ${m.source_connection_id}`}</Typography></TableCell>
                            <TableCell sx={{ fontFamily: "monospace", fontSize: 12, overflowWrap: "anywhere" }}>{m.source_column}</TableCell>
                            <TableCell><ArrowForwardOutlined fontSize="inherit" color="action" /></TableCell>
                            <TableCell sx={{ fontFamily: "monospace", fontSize: 12, overflowWrap: "anywhere" }}>{m.destination_column}</TableCell>
                            <TableCell><Chip size="small" variant="outlined" label={(m.transform_type || "RENAME").toUpperCase() === "RENAME" ? "Cópia direta" : m.transform_type} /></TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </Paper>
                )
              )}

              {detailTab === "diagram" && selectedDiagram && <RelationDiagram {...selectedDiagram} highlightTarget={selectedTable.id} />}

              {detailTab === "partition" && (
                <DestinationPartitionSettings
                  destinationTableId={selectedTable.id}
                  columnNames={selectedTable.columns.map((c) => c.column_name)}
                  onSaved={() => { refreshSelected(); loadAll(); }}
                />
              )}
            </Box>
          </Stack>
        )}
      </Drawer>

      {/* Dialog: novo campo */}
      <Dialog open={colDialogOpen} onClose={() => setColDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Novo campo</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome do campo" value={newCol.column_name}
              onChange={(e) => setNewCol({ ...newCol, column_name: e.target.value })} />
            <FormControl fullWidth>
              <InputLabel>Tipo de dado</InputLabel>
              <Select value={newCol.data_type} label="Tipo de dado"
                onChange={(e) => setNewCol({ ...newCol, data_type: e.target.value })}>
                {DATA_TYPES.map((dt) => <MenuItem key={dt} value={dt}>{dt}</MenuItem>)}
              </Select>
            </FormControl>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setColDialogOpen(false)}>Cancelar</Button>
          <Button variant="contained" onClick={doAddColumn} disabled={!newCol.column_name}>Incluir</Button>
        </DialogActions>
      </Dialog>

      {/* Dialog: nova tabela */}
      <Dialog open={tableDialogOpen} onClose={() => setTableDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Criar tabela de destino</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome da tabela" value={newTable.name}
              onChange={(e) => setNewTable({ ...newTable, name: e.target.value })}
              helperText="Ex.: MODELO_FOCUS, MODELO_GCP" />
            <TextField label="Finalidade (opcional)" value={newTable.description}
              onChange={(e) => setNewTable({ ...newTable, description: e.target.value })}
              multiline rows={2} />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTableDialogOpen(false)}>Cancelar</Button>
          <Button variant="contained" onClick={doCreateTable} disabled={!newTable.name}>Criar tabela</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
