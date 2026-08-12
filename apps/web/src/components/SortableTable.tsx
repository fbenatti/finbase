import { useState, useMemo } from "react";
import {
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Paper, TextField, Box, Typography, TableSortLabel,
} from "@mui/material";
import { Search as SearchIcon } from "@mui/icons-material";

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
    <Box>
      <TextField
        size="small"
        placeholder={placeholder}
        value={effectiveSearch}
        onChange={(e) => {
          const value = e.target.value;
          if (searchValue === undefined) setSearch(value);
          onSearchChange?.(value);
        }}
        InputProps={{ startAdornment: <SearchIcon sx={{ mr: 0.5, color: "text.secondary", fontSize: 18 }} /> }}
        sx={{ mb: 1, minWidth: 280 }}
      />
      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
        {filtered.length} de {data.length} registros
      </Typography>

      <TableContainer component={Paper} sx={{ maxHeight }}>
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
                  onClick={() => {
                    if (col.sortable !== false) handleSort(col.key);
                  }}
                >
                  {col.headerRender ? col.headerRender() : col.sortable !== false ? (
                    <TableSortLabel
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
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontSize: size === "small" ? "0.8rem" : undefined,
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
    </Box>
  );
}
