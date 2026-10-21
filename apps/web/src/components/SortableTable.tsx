import { useState, useMemo } from "react";
import {
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Paper, TextField, Box, Typography, TableSortLabel, InputAdornment, Stack,
} from "@mui/material";
import { Search as SearchIcon } from "./FinbaseIcons";

export interface ColumnDef<T> {
  key: string;
  label: string;
  headerRender?: () => React.ReactNode;
  render?: (row: T) => React.ReactNode;
  width?: number | string;
  sortable?: boolean;
  filterable?: boolean;
}

interface SortableTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  getRowKey: (row: T, index: number) => string | number;
  size?: "small" | "medium";
  stickyHeader?: boolean;
  maxHeight?: number | string;
  placeholder?: string;
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  defaultSortKey?: string;
  defaultSortDir?: "asc" | "desc";
}

type SortDir = "asc" | "desc";

export default function SortableTable<T extends object>({
  columns,
  data,
  getRowKey,
  size = "small",
  stickyHeader = false,
  maxHeight,
  placeholder = "Filtrar...",
  searchValue,
  onSearchChange,
  defaultSortKey,
  defaultSortDir = "asc",
}: SortableTableProps<T>) {
  const [search, setSearch] = useState("");
  const effectiveSearch = searchValue ?? search;
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey || null);
  const [sortDir, setSortDir] = useState<SortDir>(defaultSortDir);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      if (sortDir === "asc") setSortDir("desc");
      else if (sortDir === "desc") { setSortKey(null); setSortDir("asc"); }
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const filtered = useMemo(() => {
    if (!effectiveSearch.trim()) return data;
    const q = effectiveSearch.toLowerCase();
    return data.filter((row) =>
      columns.some((col) => {
        if (col.filterable === false) return false;
        const val = (row as Record<string, unknown>)[col.key];
        return val != null && String(val).toLowerCase().includes(q);
      })
    );
  }, [data, effectiveSearch, columns]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    return [...filtered].sort((a, b) => {
      const va = (a as Record<string, unknown>)[sortKey];
      const vb = (b as Record<string, unknown>)[sortKey];

      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;

      let cmp = 0;
      if (typeof va === "number" && typeof vb === "number") {
        cmp = va - vb;
      } else {
        cmp = String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: "base" });
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [filtered, sortKey, sortDir]);

  return (
    <Paper variant="outlined" sx={{ overflow: "hidden", minWidth: 0 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" gap={1} sx={{ p: 2, bgcolor: "background.default", borderBottom: 1, borderColor: "divider" }}>
      <TextField
        size="small"
        label="Buscar registros"
        placeholder={placeholder}
        value={effectiveSearch}
        onChange={(e) => {
          const value = e.target.value;
          if (searchValue === undefined) setSearch(value);
          onSearchChange?.(value);
        }}
        InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon sx={{ color: "text.secondary", fontSize: 20 }} /></InputAdornment> }}
        sx={{ width: { xs: "100%", sm: 320 }, bgcolor: "background.paper" }}
      />
      <Typography variant="caption" color="text.secondary" role="status">
        {filtered.length} de {data.length} registros
      </Typography>
      </Stack>

      <TableContainer sx={{ maxHeight, width: "100%" }}>
        <Table size={size} stickyHeader={stickyHeader}>
          <TableHead>
            <TableRow>
              {columns.map((col) => (
                <TableCell
                  key={col.key}
                  sx={{
                    fontWeight: 600,
                    width: col.width,
                    whiteSpace: "nowrap",
                    cursor: col.sortable !== false ? "pointer" : "default",
                    userSelect: "none",
                  }}
                  sortDirection={sortKey === col.key ? sortDir : false}
                >
                  {col.headerRender ? col.headerRender() : col.sortable !== false ? (
                    <TableSortLabel
                      onClick={() => handleSort(col.key)}
                      active={sortKey === col.key}
                      direction={sortKey === col.key ? sortDir : "asc"}
                    >
                      {col.label}
                    </TableSortLabel>
                  ) : (
                    col.label
                  )}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {sorted.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} align="center" sx={{ py: 4, color: "text.secondary" }}>
                  Nenhum registro encontrado
                </TableCell>
              </TableRow>
            ) : (
              sorted.map((row, i) => (
                <TableRow key={getRowKey(row, i)} hover>
                  {columns.map((col) => (
                    <TableCell
                      key={col.key}
                      sx={{
                        maxWidth: col.width || 250,
                        overflow: col.render ? "visible" : "hidden",
                        textOverflow: col.render ? "clip" : "ellipsis",
                        whiteSpace: col.render ? "normal" : "nowrap",
                        fontSize: "0.875rem",
                        height: 48,
                      }}
                    >
                      {col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key] ?? "")}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>
    </Paper>
  );
}
