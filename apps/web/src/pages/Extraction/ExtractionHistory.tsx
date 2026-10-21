import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ArrowBack, Refresh } from "@mui/icons-material";
import {
  DataGrid,
  GridToolbar,
  GridFilterModel,
  GridPaginationModel,
  GridSortModel,
  GridColDef,
} from "@mui/x-data-grid";
import {
  ExtractionHistoryMetadata,
  ExtractionHistoryQueryResponse,
  HistoryFilter,
  getExtractionHistoryMetadata,
  queryExtractionHistoryData,
} from "../../services/api";

export default function ExtractionHistory() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();

  const [metadata, setMetadata] = useState<ExtractionHistoryMetadata | null>(null);
  const [data, setData] = useState<ExtractionHistoryQueryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(100);
  const [contextMenu, setContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    field: string;
    value: string;
  } | null>(null);

  const fetchData = useCallback(
    async (
      currentSearch = search,
      currentOffset = page * pageSize,
      currentFilters: Record<string, HistoryFilter> = {},
      currentSort?: { field: string; direction: "asc" | "desc" }
    ) => {
      if (!taskId) return;
      try {
        setLoading(true);
        if (!metadata) {
          const meta = await getExtractionHistoryMetadata(Number(taskId));
          setMetadata(meta);
        }

        const res = await queryExtractionHistoryData(Number(taskId), {
          offset: currentOffset,
          limit: pageSize,
          search: currentSearch,
          filters: currentFilters,
          sort_field: currentSort?.field,
          sort_direction: currentSort?.direction || "asc",
        });

        setData(res);
        setError("");
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [metadata, page, pageSize, search, taskId]
  );

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  // Converter colunas para o DataGrid
  const columns: GridColDef[] = (data?.columns || metadata?.columns || []).map((col) => {
    const isNumeric = col.includes("cost") || col.includes("Quantity") || col.includes("Price") || col.includes("size");
    return {
      field: col,
      headerName: col,
      minWidth: 160,
      flex: 1,
      type: isNumeric ? "number" : "string",
      filterable: true,
      sortable: true,
      renderCell: (params) => {
        const val = params.value;
        if (val === null || val === undefined) return <span style={{ color: "#aaa" }}>—</span>;
        if (typeof val === "number") {
          return col.includes("cost") || col.includes("Cost") || col.includes("Price")
            ? val.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 })
            : val.toLocaleString("pt-BR");
        }
        return String(val);
      },
    };
  });

  // Criar rows com ID único
  const rows = (data?.records || []).map((rec, idx) => ({
    id: `${data?.offset || 0}-${idx}`,
    ...rec,
  }));

  const handleFilterChange = (model: GridFilterModel) => {
    setFilterModel(model);
    const filters: Record<string, HistoryFilter> = {};
    for (const item of model.items) {
      if (!item.value && item.value !== 0) continue;
      const operator = item.operator || "equals";
      const value = operator === "isAnyOf"
        ? String(item.value).split(",").map((v) => v.trim()).filter(Boolean)
        : item.value;
      filters[item.field] = { operator, value };
    }
    setPage(0);
    void fetchData(search, 0, filters);
  };

  const handlePaginationChange = (model: GridPaginationModel) => {
    setPage(model.page);
    setPageSize(model.pageSize);
    void fetchData(search, model.page * model.pageSize);
  };

  const handleSortChange = (model: GridSortModel) => {
    setSortModel(model);
    const item = model[0];
    if (!item?.sort) {
      void fetchData(search, 0);
      return;
    }
    void fetchData(search, 0, {}, { field: item.field, direction: item.sort });
  };

  const clearAllFilters = () => {
    setSearch("");
    setFilterModel({ items: [] });
    setSortModel([]);
    setPage(0);
    void fetchData("", 0, {});
  };

  const handleContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = (event.target as HTMLElement).closest(".MuiDataGrid-cell") as HTMLElement | null;
    const dataField = cell?.getAttribute("data-field") || "";
    const value = cell?.textContent?.trim() || "";
    if (dataField && value && value !== "—") {
      setContextMenu({
        mouseX: event.clientX - 2,
        mouseY: event.clientY - 4,
        field: dataField,
        value,
      });
    }
  };

  const handleCloseContextMenu = () => setContextMenu(null);

  const applyContextFilter = (operator: "equals" | "notEquals") => {
    if (!contextMenu) return;
    setPage(0);
    void fetchData(search, 0, {
      [contextMenu.field]: { operator, value: contextMenu.value },
    });
    handleCloseContextMenu();
  };

  const totalRows = data?.total_records || metadata?.total_rows || 0;

  return (
    <Box sx={{ p: 1 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
        <Box>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography variant="h4" fontWeight={600}>
              Dados da Extração #{taskId}
            </Typography>
            {metadata && (
              <Chip
                label={metadata.status}
                color={metadata.status === "COMPLETED" ? "success" : metadata.status === "FAILED" ? "error" : "primary"}
                size="small"
              />
            )}
          </Stack>
          <Typography variant="body2" color="text.secondary" mt={0.5}>
            {metadata ? (
              <>
                <strong>Fonte:</strong> {metadata.connection_name} ({metadata.connection_code}) ·{" "}
                <strong>Tabela Bronze:</strong> <code>{metadata.table_name}</code> ·{" "}
                <strong>Total de Registros:</strong> {totalRows.toLocaleString()}
              </>
            ) : (
              `Execução ${taskId} · consulta server-side no Apache Iceberg`
            )}
          </Typography>
        </Box>
        <Button startIcon={<ArrowBack />} variant="outlined" onClick={() => navigate("/operacoes/execucoes?tab=history")}>
          Voltar para Extrações
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>
          {error}
        </Alert>
      )}

      {/* Barra de Filtros e Busca */}
      <Stack direction="row" gap={1} mb={1} alignItems="center">
        <TextField
          size="small"
          fullWidth
          label="Buscar nos dados da extração"
          placeholder="Busca textual em qualquer coluna (ex: Compute, Network, 202608, BRL...)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void fetchData(search, 0)}
        />
        <Button variant="contained" disabled={loading} onClick={() => void fetchData(search, 0)}>
          Buscar
        </Button>
        <Button variant="outlined" disabled={loading} onClick={clearAllFilters}>
          Limpar Filtros
        </Button>
        <Button
          variant="outlined"
          color="secondary"
          startIcon={<Refresh />}
          disabled={loading}
          onClick={() => void fetchData(search, page * pageSize)}
        >
          Atualizar
        </Button>
      </Stack>

      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        DataGrid server-side · Paginação de {pageSize} linhas · Linhas {(page * pageSize) + 1}–{Math.min((page + 1) * pageSize, totalRows)} de {totalRows.toLocaleString()} · Clique direito em uma célula para filtrar rapidamente.
      </Typography>

      {/* DataGrid */}
      <Box sx={{ height: 620, width: "100%", bgcolor: "background.paper", borderRadius: 1 }} onContextMenu={handleContextMenu}>
        <DataGrid
          rows={rows}
          columns={columns}
          loading={loading}
          pagination
          paginationMode="server"
          filterMode="server"
          sortingMode="server"
          rowCount={totalRows}
          pageSizeOptions={[50, 100, 200]}
          paginationModel={{ page, pageSize }}
          filterModel={filterModel}
          sortModel={sortModel}
          onPaginationModelChange={handlePaginationChange}
          onFilterModelChange={handleFilterChange}
          onSortModelChange={handleSortChange}
          sortingOrder={["asc", "desc"]}
          slots={{ toolbar: GridToolbar }}
          disableRowSelectionOnClick
          density="compact"
          sx={{
            "& .MuiDataGrid-columnHeaders": {
              bgcolor: "grey.100",
              fontWeight: 700,
            },
          }}
        />
      </Box>

      {/* Context Menu para clique direito */}
      <Menu
        open={contextMenu !== null}
        onClose={handleCloseContextMenu}
        anchorReference="anchorPosition"
        anchorPosition={contextMenu ? { top: contextMenu.mouseY, left: contextMenu.mouseX } : undefined}
      >
        <MenuItem onClick={() => applyContextFilter("equals")}>
          Mostrar iguais a “{contextMenu?.value}”
        </MenuItem>
        <MenuItem onClick={() => applyContextFilter("notEquals")}>
          Mostrar diferentes de “{contextMenu?.value}”
        </MenuItem>
      </Menu>
    </Box>
  );
}
