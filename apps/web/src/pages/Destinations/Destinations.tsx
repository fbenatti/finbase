import { useState, useEffect, useCallback } from "react";
import {
  Box, Typography, Button, IconButton, Chip, Dialog, DialogTitle, DialogContent,
  DialogActions, TextField, Select, MenuItem, FormControl, InputLabel, Alert,
  CircularProgress, Paper,
} from "@mui/material";
import { Add, Edit, Delete, TableChart, Refresh } from "@mui/icons-material";
import {
  getDestinationTables, getDestinationTable, getDestinationColumns,
  addDestinationColumn, deleteDestinationColumn, createDestinationTable,
  getSourceDestMappings,
  DestinationTable, DestinationColumn, DestinationTableDetail, SourceDestinationMapping,
  getConnections, SourceConnection,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable from "../../components/SortableTable";

const DATA_TYPES = ["TEXT", "VARCHAR(50)", "VARCHAR(100)", "VARCHAR(200)", "NUMERIC", "DATE", "TIMESTAMPTZ", "JSONB", "BOOLEAN"];

export default function Destinations({ embedded }: { embedded?: boolean } = {}) {
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [selectedTable, setSelectedTable] = useState<DestinationTableDetail | null>(null);
  const [mappings, setMappings] = useState<SourceDestinationMapping[]>([]);
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // New column dialog
  const [colDialogOpen, setColDialogOpen] = useState(false);
  const [newCol, setNewCol] = useState({ column_name: "", data_type: "TEXT", is_required: false });

  // New table dialog
  const [tableDialogOpen, setTableDialogOpen] = useState(false);
  const [newTable, setNewTable] = useState({ name: "", description: "" });

  useEffect(() => {
    getConnections().then(setConnections).catch(() => {});
  }, []);

  const loadTables = useCallback(async () => {
    setLoading(true);
    try {
      setTables(await getDestinationTables(false));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { loadTables(); }, [loadTables]);

  const selectTable = async (table: DestinationTable) => {
    try {
      const detail = await getDestinationTable(table.id);
      setSelectedTable(detail);
      setMappings(await getSourceDestMappings(undefined, table.id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load table detail");
    }
  };

  const doAddColumn = async () => {
    if (!selectedTable || !newCol.column_name) return;
    try {
      await addDestinationColumn(selectedTable.id, newCol);
      setColDialogOpen(false);
      setNewCol({ column_name: "", data_type: "TEXT", is_required: false });
      selectTable(selectedTable);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to add column");
    }
  };

  const doDeleteColumn = async (col: DestinationColumn) => {
    if (!confirm(`Remover coluna "${col.column_name}"?`)) return;
    try {
      await deleteDestinationColumn(col.id);
      if (selectedTable) selectTable(selectedTable);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete column");
    }
  };

  const doCreateTable = async () => {
    if (!newTable.name) return;
    try {
      await createDestinationTable(newTable);
      setTableDialogOpen(false);
      setNewTable({ name: "", description: "" });
      loadTables();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create table");
    }
  };

  // Count mappings by source
  const mappingSummary = new Map<number, number>();
  mappings.forEach((m) => {
    mappingSummary.set(m.source_connection_id, (mappingSummary.get(m.source_connection_id) || 0) + 1);
  });

  if (loading) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  return (
    <Box sx={{ display: "flex", gap: 3, height: "calc(100vh - 100px)" }}>
      {/* Left: Table List */}
      <Paper sx={{ width: 300, p: 2, overflow: "auto", flexShrink: 0 }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
          <Typography variant="h6" sx={{ fontSize: "1rem" }}>Tabelas de Destino</Typography>
          <IconButton size="small" onClick={() => setTableDialogOpen(true)}><Add /></IconButton>
        </Box>

        {tables.length === 0 ? (
          <EmptyState message="Nenhuma tabela criada." />
        ) : (
          tables.map((t) => (
            <Paper
              key={t.id}
              sx={{
                p: 1.5, mb: 1, cursor: "pointer",
                bgcolor: selectedTable?.id === t.id ? "#e3f2fd" : "#fff",
                border: selectedTable?.id === t.id ? "2px solid #1976d2" : "1px solid #ddd",
              }}
              onClick={() => selectTable(t)}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <TableChart sx={{ fontSize: 18, color: "#1976d2" }} />
                <Typography variant="body2" fontWeight={600}>{t.name}</Typography>
                {!t.active && <Chip label="Inativo" size="small" color="default" />}
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                {t.physical_name}
              </Typography>
            </Paper>
          ))
        )}

        <Button size="small" startIcon={<Refresh />} onClick={loadTables} sx={{ mt: 1 }} fullWidth>
          Atualizar
        </Button>
      </Paper>

      {/* Right: Table Detail */}
      <Box sx={{ flexGrow: 1, overflow: "auto" }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

        {!selectedTable ? (
          <EmptyState message="Selecione uma tabela de destino para ver suas colunas e mapeamentos." />
        ) : (
          <>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
              <Box>
                <Typography variant="h5">{selectedTable.name}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {selectedTable.description || selectedTable.physical_name}
                </Typography>
              </Box>
              <Button variant="contained" startIcon={<Add />} onClick={() => setColDialogOpen(true)}>
                Nova Coluna
              </Button>
            </Box>

            {/* Columns */}
            <Typography variant="subtitle1" sx={{ mb: 1 }}>
              Colunas ({selectedTable.columns.length})
            </Typography>
            <Box sx={{ mb: 4 }}>
              {selectedTable.columns.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  Nenhuma coluna definida.
                </Typography>
              ) : (
                selectedTable.columns.map((col) => (
                  <Paper key={col.id} sx={{ p: 1, mb: 0.5, display: "flex", alignItems: "center", gap: 1 }}>
                    <Chip label={col.data_type} size="small" color="primary" variant="outlined" sx={{ minWidth: 70 }} />
                    <Typography variant="body2" sx={{ flexGrow: 1, fontFamily: "monospace" }}>
                      {col.column_name}
                    </Typography>
                    {col.is_required && <Chip label="Obrigatório" size="small" color="warning" />}
                    <Typography variant="caption" color="text.secondary">pos={col.position}</Typography>
                    <IconButton size="small" color="error" onClick={() => doDeleteColumn(col)}>
                      <Delete fontSize="small" />
                    </IconButton>
                  </Paper>
                ))
              )}
            </Box>

            {/* Mappings summary */}
            <Typography variant="subtitle1" sx={{ mb: 1 }}>
              Fontes Mapeadas ({mappings.length} mapeamentos)
            </Typography>
            <Box sx={{ mb: 3 }}>
              {mappingSummary.size === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  Nenhuma fonte mapeada para esta tabela. Use o Mapeamento de Colunas para criar os relacionamentos.
                </Typography>
              ) : (
                Array.from(mappingSummary.entries()).map(([connId, count]) => {
                  const conn = connections.find((c) => c.id === connId);
                  return (
                    <Chip
                      key={connId}
                      label={`${conn?.name || `Conn #${connId}`}: ${count} colunas`}
                      sx={{ mr: 1, mb: 1 }}
                      icon={<TableChart />}
                    />
                  );
                })
              )}
            </Box>
          </>
        )}
      </Box>

      {/* Add Column Dialog */}
      <Dialog open={colDialogOpen} onClose={() => setColDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Nova Coluna</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome da Coluna" value={newCol.column_name}
              onChange={(e) => setNewCol({ ...newCol, column_name: e.target.value })} />
            <FormControl fullWidth>
              <InputLabel>Tipo</InputLabel>
              <Select value={newCol.data_type} label="Tipo"
                onChange={(e) => setNewCol({ ...newCol, data_type: e.target.value })}>
                {DATA_TYPES.map((dt) => <MenuItem key={dt} value={dt}>{dt}</MenuItem>)}
              </Select>
            </FormControl>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setColDialogOpen(false)}>Cancelar</Button>
          <Button variant="contained" onClick={doAddColumn} disabled={!newCol.column_name}>Adicionar</Button>
        </DialogActions>
      </Dialog>

      {/* Add Table Dialog */}
      <Dialog open={tableDialogOpen} onClose={() => setTableDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Nova Tabela de Destino</DialogTitle>
        <DialogContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
            <TextField label="Nome" value={newTable.name}
              onChange={(e) => setNewTable({ ...newTable, name: e.target.value })}
              helperText="Ex: MODELO_FOCUS, MODELO_GCP" />
            <TextField label="Descrição (opcional)" value={newTable.description}
              onChange={(e) => setNewTable({ ...newTable, description: e.target.value })}
              multiline rows={2} />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTableDialogOpen(false)}>Cancelar</Button>
          <Button variant="contained" onClick={doCreateTable} disabled={!newTable.name}>Criar</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
