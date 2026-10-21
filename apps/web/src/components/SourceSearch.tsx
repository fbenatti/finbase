import { useEffect, useState } from "react";
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { Search } from "@mui/icons-material";
import { DestinationAnalysis, PivotFilter, PivotMetadata, getPivotMetadata, searchPivotTable, LakehouseSearchResult } from "../services/api";

export default function SourceSearch({ destinations, initialTableId }: { destinations: DestinationAnalysis[]; initialTableId?: number }) {
  const [tableId, setTableId] = useState(initialTableId || destinations[0]?.id || 0);
  const [meta, setMeta] = useState<PivotMetadata | null>(null);
  const [term, setTerm] = useState("");
  const [field, setField] = useState("");
  const [filters, setFilters] = useState<PivotFilter[]>([]);
  const [result, setResult] = useState<LakehouseSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { if (tableId) getPivotMetadata(tableId).then(setMeta).catch(e => setError(e.message)); }, [tableId]);
  const addFilter = () => { if (field && !filters.some(f => f.column === field)) setFilters([...filters, { column: field, values: [], operator: "IN" }]); };
  const search = async () => { if (!term.trim()) return; setLoading(true); setError(""); try { setResult(await searchPivotTable(tableId, term, 100, 0, filters)); } catch (e: unknown) { setError(e instanceof Error ? e.message : "Falha na busca"); } finally { setLoading(false); } };

  return <Box>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Paper sx={{ p: 2, mb: 2 }}>
      <Typography variant="h6">Busca na Fonte de Dados</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Pesquise uma string em todas as colunas textuais da base consolidada, aplicando filtros condicionais antes da busca.</Typography>
      <Stack direction="row" gap={1.5} flexWrap="wrap" alignItems="center">
        <FormControl size="small" sx={{ minWidth: 250 }}><InputLabel>base consolidada</InputLabel><Select value={tableId} label="base consolidada" onChange={e => { setTableId(Number(e.target.value)); setResult(null); }}>{destinations.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}</Select></FormControl>
        <TextField size="small" label="Texto para buscar" placeholder="marketplace" value={term} onChange={e => setTerm(e.target.value)} onKeyDown={e => e.key === "Enter" && search()} sx={{ minWidth: 280 }} />
        <Button variant="contained" startIcon={<Search />} onClick={search} disabled={loading || !term.trim()}>{loading ? "Buscando..." : "Buscar"}</Button>
      </Stack>
      <Stack direction="row" gap={1} alignItems="center" sx={{ mt: 2, flexWrap: "wrap" }}>
        <FormControl size="small" sx={{ minWidth: 260 }}><InputLabel>Adicionar condição</InputLabel><Select value={field} label="Adicionar condição" onChange={e => setField(e.target.value)}>{meta?.columns.filter(c => !c.numeric).map(c => <MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</Select></FormControl>
        <Button onClick={addFilter} disabled={!field}>Adicionar filtro</Button>
        {filters.map(filter => <Stack key={filter.column} direction="row" gap={.5} alignItems="center"><Chip label={filter.column} onDelete={() => setFilters(filters.filter(f => f.column !== filter.column))} /><Select size="small" value={filter.operator || "IN"} onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, operator: e.target.value as PivotFilter["operator"] } : f))}><MenuItem value="IN">É igual a</MenuItem><MenuItem value="NOT_IN">Não é</MenuItem><MenuItem value="CONTAINS">Contém</MenuItem><MenuItem value="NOT_CONTAINS">Não contém</MenuItem></Select><TextField size="small" placeholder="valor" value={filter.values.join(", ")} onChange={e => setFilters(filters.map(f => f.column === filter.column ? { ...f, values: e.target.value.split(",").map(v => v.trim()).filter(Boolean) } : f))} /></Stack>)}
      </Stack>
    </Paper>
    {result && <Paper sx={{ p: 2 }}><Stack direction="row" justifyContent="space-between"><Typography variant="subtitle1">Resultados para “{term}”</Typography><Chip label={`${result.total} exibidos (máximo 100)`} size="small" /></Stack><Typography variant="caption" color="text.secondary">A busca verificou {result.searched_columns?.length || 0} colunas textuais. As colunas encontradas ficam destacadas.</Typography><TableContainer sx={{ maxHeight: "65vh", mt: 1 }}><Table stickyHeader size="small"><TableHead><TableRow>{result.columns.map(c => <TableCell key={c}>{c}</TableCell>)}</TableRow></TableHead><TableBody>{result.rows.map((row, i) => <TableRow key={i}>{result.columns.map(c => <TableCell key={c} sx={result.matched_columns[i]?.includes(c) ? { bgcolor: "#fff3cd", fontWeight: 700 } : undefined}>{String(row[c] ?? "")}</TableCell>)}</TableRow>)}</TableBody></Table></TableContainer></Paper>}
  </Box>;
}
