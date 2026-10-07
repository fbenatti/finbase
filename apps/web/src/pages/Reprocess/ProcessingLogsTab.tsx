import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Chip, Dialog, DialogContent, DialogTitle, Divider, IconButton, MenuItem,
  Stack, TextField, Typography,
} from "@mui/material";
import { Close } from "@mui/icons-material";
import { DataGrid, GridColDef, GridPaginationModel } from "@mui/x-data-grid";
import { format } from "date-fns";
import { getProcessingLog, getProcessingLogs, ProcessingLog } from "../../services/api";

const PROCESS_LABELS: Record<string, string> = {
  extraction: "Extração", reprocess: "Reprocessamento de dados", reclassification: "Reclassificação",
  bronze_audit: "Auditoria Bronze", gold_reprocess: "Materialização Gold",
  shared_cost: "Custo compartilhado", forecast: "Forecast", forecast_accuracy: "Acurácia de Forecast",
  ai_cost_relationship_reprocess: "Relacionamentos de custo IA", backup: "Backup",
};
const STATUS_COLOR: Record<string, "success" | "error" | "warning" | "info" | "default"> = {
  COMPLETED: "success", COMPLETED_WITH_ERRORS: "warning", FAILED: "error", CANCELLED: "warning", RUNNING: "info", QUEUED: "default",
};
const dateLabel = (value?: string | null) => value ? format(new Date(value), "dd/MM/yyyy, HH:mm:ss") : "—";
const text = (value: unknown) => value == null || value === "" ? "—" : Array.isArray(value) ? value.join(", ") : typeof value === "object" ? JSON.stringify(value) : String(value);
const sourceLabel = (value: Record<string, unknown>) => {
  const names = value.connection_names as string[] | undefined;
  const ids = value.connection_ids as number[] | undefined;
  if (names?.length) return names.join(", ");
  if (ids?.length) return ids.map(id => `Conexão #${id}`).join(", ");
  return text(value);
};
const destinationLabel = (value: Record<string, unknown>) => text(value.destination_name || value.table || value.tables || value.namespace || value.destination_table_id || value);
const json = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

function Field({ label, value }: { label: string; value: unknown }) {
  return <Box sx={{ minWidth: 180, flex: "1 1 180px" }}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>{text(value)}</Typography></Box>;
}

export default function ProcessingLogsTab() {
  const [rows, setRows] = useState<ProcessingLog[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState<GridPaginationModel>({ page: 0, pageSize: 25 });
  const [processType, setProcessType] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ProcessingLog | null>(null);
  const [detail, setDetail] = useState<ProcessingLog | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await getProcessingLogs({
        limit: pagination.pageSize, offset: pagination.page * pagination.pageSize,
        process_type: processType || undefined, status: status || undefined, search: search || undefined,
      });
      setRows(response.items); setTotal(response.total);
    } catch (e) { setError(e instanceof Error ? e.message : "Falha ao carregar logs"); }
    finally { setLoading(false); }
  }, [pagination, processType, status, search]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    getProcessingLog(selected.id).then(setDetail).catch(e => setError(e instanceof Error ? e.message : "Falha ao carregar detalhe"));
  }, [selected]);

  const columns = useMemo<GridColDef<ProcessingLog>[]>(() => [
    { field: "process_type", headerName: "Tipo", minWidth: 190, flex: 1, valueGetter: (_, row) => PROCESS_LABELS[row.process_type] || row.process_type },
    { field: "status", headerName: "Status", width: 145, renderCell: ({ value }) => <Chip size="small" label={value} color={STATUS_COLOR[String(value)] || "default"} /> },
    { field: "source", headerName: "Origem", minWidth: 180, flex: 1, valueGetter: (_, row) => sourceLabel(row.source) },
    { field: "destination", headerName: "Destino", minWidth: 180, flex: 1, valueGetter: (_, row) => destinationLabel(row.destination) },
    { field: "partitions", headerName: "Partição", minWidth: 130, valueGetter: (_, row) => text(row.partitions) },
    { field: "rows", headerName: "Linhas destino", width: 130, valueGetter: (_, row) => row.counts?.rows_destination ?? 0, valueFormatter: value => Number(value || 0).toLocaleString("pt-BR") },
    { field: "started_at", headerName: "Início", minWidth: 165, valueFormatter: value => dateLabel(value as string) },
    { field: "finished_at", headerName: "Fim", minWidth: 165, valueFormatter: value => dateLabel(value as string) },
  ], []);

  return <Stack spacing={1.5}>
    {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
      <TextField size="small" label="Buscar" value={search} onChange={e => { setSearch(e.target.value); setPagination(p => ({ ...p, page: 0 })); }} sx={{ minWidth: 220 }} />
      <TextField select size="small" label="Tipo" value={processType} onChange={e => { setProcessType(e.target.value); setPagination(p => ({ ...p, page: 0 })); }} sx={{ minWidth: 230 }}>
        <MenuItem value="">Todos os tipos</MenuItem>{Object.entries(PROCESS_LABELS).map(([key, label]) => <MenuItem key={key} value={key}>{label}</MenuItem>)}
      </TextField>
      <TextField select size="small" label="Status" value={status} onChange={e => { setStatus(e.target.value); setPagination(p => ({ ...p, page: 0 })); }} sx={{ minWidth: 170 }}>
        <MenuItem value="">Todos os status</MenuItem>{["QUEUED", "RUNNING", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].map(value => <MenuItem key={value} value={value}>{value}</MenuItem>)}
      </TextField>
    </Stack>
    <Typography variant="caption" color="text.secondary">Selecione uma execução para ver parâmetros, eventos e erros. O conteúdo dos dados permanece na tabela de destino quando o processo o publica.</Typography>
    <Box sx={{ height: 560, width: "100%" }}>
      <DataGrid rows={rows} columns={columns} loading={loading} rowCount={total} paginationMode="server" paginationModel={pagination} onPaginationModelChange={setPagination} pageSizeOptions={[10, 25, 50, 100]} onRowClick={({ row }) => setSelected(row)} disableRowSelectionOnClick sx={{ "& .MuiDataGrid-row": { cursor: "pointer" }, "& .MuiDataGrid-columnHeaders": { bgcolor: "#f8fafc" } }} />
    </Box>
    <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} fullWidth maxWidth="md">
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        {detail ? PROCESS_LABELS[detail.process_type] || detail.process_type : "Detalhe da execução"} · #{selected?.id}
        {detail && <Chip size="small" label={detail.status} color={STATUS_COLOR[detail.status] || "default"} />}
        <IconButton aria-label="Fechar" onClick={() => setSelected(null)} sx={{ ml: "auto" }}><Close /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {detail && <Stack spacing={2}>
          {detail.error && <Alert severity="error">{detail.error}</Alert>}
          <Typography variant="subtitle2">Resumo</Typography>
          <Stack direction="row" gap={2} flexWrap="wrap">
            <Field label="Origem" value={detail.source} /><Field label="Destino" value={detail.destination} /><Field label="Partições / períodos" value={detail.partitions} />
            <Field label="Início" value={dateLabel(detail.started_at)} /><Field label="Fim" value={dateLabel(detail.finished_at)} /><Field label="Fase" value={detail.phase} />
          </Stack>
          <Divider /><Typography variant="subtitle2">Contagens</Typography>
          <Stack direction="row" gap={2} flexWrap="wrap">{Object.entries(detail.counts || {}).map(([key, value]) => <Field key={key} label={key} value={Number(value).toLocaleString("pt-BR")} />)}</Stack>
          <Divider /><Typography variant="subtitle2">Parâmetros</Typography><Box component="pre" sx={{ m: 0, p: 1.5, bgcolor: "grey.50", borderRadius: 1, overflow: "auto", fontSize: 12 }}>{json(detail.parameters)}</Box>
          <Divider /><Typography variant="subtitle2">Eventos e detalhes do processamento</Typography><Box component="pre" sx={{ m: 0, p: 1.5, bgcolor: "grey.50", borderRadius: 1, overflow: "auto", maxHeight: 360, fontSize: 12 }}>{json(detail.details)}</Box>
        </Stack>}
      </DialogContent>
    </Dialog>
  </Stack>;
}
