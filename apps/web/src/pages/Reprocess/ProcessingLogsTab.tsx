import { Dialog, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Chip, Divider, IconButton, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { Close, ExpandMore } from "../../components/FinbaseIcons";
import { GridColDef, GridPaginationModel } from "@mui/x-data-grid";
import { format } from "date-fns";
import { useNavigate } from "react-router-dom";
import { cancelEtl, getProcessingLog, getProcessingLogs, ProcessingLog } from "../../services/api";

const PROCESS_LABELS: Record<string, string> = {
  extraction: "Coleta", reprocess: "Recarga de dados", reclassification: "Reclassificação de projetos",
  bronze_audit: "Auditoria da base de origem", gold_reprocess: "Consolidação de bases",
  shared_cost: "Rateio de custo compartilhado", forecast: "Forecast", forecast_accuracy: "Precisão de Forecast",
  ai_cost_relationship_reprocess: "Relações de custo (IA)", backup: "Backup",
};
const STATUS_COLOR: Record<string, "success" | "error" | "warning" | "info" | "default"> = {
  COMPLETED: "success", COMPLETED_WITH_ERRORS: "warning", FAILED: "error", CANCELLED: "warning", RUNNING: "info", QUEUED: "default",
};
const dateLabel = (value?: string | null) => value ? format(new Date(value), "dd/MM/yyyy, HH:mm:ss") : "—";
const text = (value: unknown) => dataDisplayText(value == null || value === "" ? "—" : Array.isArray(value) ? value.join(", ") : typeof value === "object" ? JSON.stringify(value) : String(value));
const FIELD_LABELS: Record<string, string> = {
  at: "Data e horário", backfill_days: "Dias analisados", billing_periods: "Competências", blobs: "Arquivos tratados",
  bronze_audit: "Auditoria da base de origem", bronze_audit_task_id: "Código da auditoria da base de origem", connection_codes: "Códigos das fontes",
  connection_ids: "Códigos das fontes", connection_names: "Fontes", destination_table_id: "Código do destino",
  destination_name: "Tabela de saída", error: "Erro", errors: "Erros", event_log: "Eventos", exception_type: "Categoria do erro",
  input_rows: "Linhas recebidas", level: "Camada", message: "Mensagem", output_rows: "Registros processados",
  period: "Competência", publish_by_partition: "Gravar por partição", queue_group: "Grupo na fila",
  queue_position: "Ordem na fila", queue_total: "Tarefas aguardando", replace_existing: "Sobrescrever dados existentes",
  result: "Resultado", rows_destination: "Registros gravados no destino", rows_normalized: "Registros normalizados",
  rows_raw: "Linhas da origem", snapshot_dates: "Datas-base", table: "Tabela", task_id: "Código da tarefa",
  billing_period: "Competência de cobrança", blob_id: "Código do arquivo", connection_id: "Código da fonte", rule_ids: "Códigos das regras",
};
const fieldLabel = (key: string) => FIELD_LABELS[key] || dataDisplayText(key.replace(/_/g, " ").replace(/\b\w/g, char => char.toLocaleUpperCase("pt-BR")));
const displayValue = (value: unknown, key?: string) => {
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (key === "at" && typeof value === "string") return dateLabel(value);
  return text(value);
};
const sourceLabel = (value: Record<string, unknown>) => {
  if (value.label) return text(value.label);
  const names = value.connection_names as string[] | undefined;
  const ids = value.connection_ids as number[] | undefined;
  if (names?.length) return text(names);
  if (ids?.length) return ids.map(id => `Fonte nº ${id}`).join(", ");
  return text(value);
};
const destinationLabel = (value: Record<string, unknown>) => text(value.label || value.destination_name || value.table || value.tables || value.namespace || value.destination_table_id || value);

function Field({ label, value }: { label: string; value: unknown }) {
  return <Box sx={{ minWidth: 180, flex: "1 1 180px", p: 1.25, bgcolor: "grey.50", border: "1px solid", borderColor: "divider", borderRadius: 1.5 }}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="body2" fontWeight={600} sx={{ overflowWrap: "anywhere", mt: 0.25 }}>{text(value)}</Typography></Box>;
}

function StructuredValue({ value, field }: { value: unknown; field?: string }) {
  if (value == null || value === "") return <Typography variant="body2" color="text.secondary">—</Typography>;
  if (Array.isArray(value)) {
    if (!value.length) return <Typography variant="body2" color="text.secondary">Nenhum registro</Typography>;
    return <Stack spacing={0.75}>
      {value.map((item, index) => <Box key={`${field || "item"}-${index}`} sx={item && typeof item === "object" ? { pl: 1.25, borderLeft: "2px solid", borderColor: "divider" } : undefined}>
        {item && typeof item === "object" ? <StructuredValue value={item} /> : <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>{displayValue(item, field)}</Typography>}
      </Box>)}
    </Stack>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <Typography variant="body2" color="text.secondary">Sem dados disponíveis</Typography>;
    return <Stack spacing={0} sx={{ width: "100%" }}>
      {entries.map(([key, nested]) => <Box key={key} sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "minmax(155px, 0.32fr) 1fr" }, gap: { xs: 0.25, sm: 1.5 }, alignItems: "start", py: 0.85, borderBottom: "1px solid", borderColor: "divider", "&:last-child": { borderBottom: 0 } }}>
        <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ pt: 0.15 }}>{fieldLabel(key)}</Typography>
        <Box sx={{ minWidth: 0 }}><StructuredValue value={nested} field={key} /></Box>
      </Box>)}
    </Stack>;
  }
  return <Typography variant="body2" sx={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{displayValue(value, field)}</Typography>;
}

function StructuredPanel({ value }: { value: unknown }) {
  return <Box sx={{ px: 1.5, py: 0.5, bgcolor: "background.paper", border: "1px solid", borderColor: "divider", borderRadius: 1.5 }}><StructuredValue value={value} /></Box>;
}

export default function ProcessingLogsTab() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<ProcessingLog[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState<GridPaginationModel>({ page: 0, pageSize: 50 });
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
    } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível carregar os logs"); }
    finally { setLoading(false); }
  }, [pagination, processType, status, search]);
  useEffect(() => { void load(); }, [load]);
  const hasActiveRows = rows.some(row => row.status === "QUEUED" || row.status === "RUNNING");
  useEffect(() => {
    if (!hasActiveRows) return;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveRows, load]);
  const selectedId = selected?.id;
  const selectedStatus = selectedId != null && detail?.id === selectedId ? detail.status : selected?.status;
  useEffect(() => {
    if (selectedId == null) { setDetail(null); return; }
    let cancelled = false;
    const refresh = () => getProcessingLog(selectedId).then(value => { if (!cancelled) setDetail(value); }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : "Não foi possível carregar o detalhe"); });
    void refresh();
    const active = selectedStatus === "QUEUED" || selectedStatus === "RUNNING";
    const timer = active ? window.setInterval(() => { void refresh(); }, 5000) : undefined;
    return () => { cancelled = true; if (timer !== undefined) window.clearInterval(timer); };
  }, [selectedId, selectedStatus]);

  const columns = useMemo<GridColDef<ProcessingLog>[]>(() => [
    { field: "process_type", headerName: "Tipo", minWidth: 190, flex: 1, valueGetter: (_, row) => PROCESS_LABELS[row.process_type] || row.process_type },
    { field: "status", headerName: "Status", width: 145, renderCell: ({ value }) => <Chip size="small" label={value} color={STATUS_COLOR[String(value)] || "default"} /> },
    { field: "source", headerName: "Origem", minWidth: 180, flex: 1, valueGetter: (_, row) => sourceLabel(row.source) },
    { field: "destination", headerName: "Destino", minWidth: 180, flex: 1, valueGetter: (_, row) => destinationLabel(row.destination) },
    { field: "partitions", headerName: "Partição", minWidth: 130, valueGetter: (_, row) => text(row.partitions) },
    { field: "rows_source", headerName: "Registros de origem", width: 125, valueGetter: (_, row) => row.counts?.rows_source ?? 0, valueFormatter: value => Number(value || 0).toLocaleString("pt-BR") },
    { field: "rows", headerName: "Registros de destino", width: 130, valueGetter: (_, row) => row.counts?.rows_destination ?? 0, valueFormatter: value => Number(value || 0).toLocaleString("pt-BR") },
    { field: "started_at", headerName: "Início", minWidth: 165, valueFormatter: value => dateLabel(value as string) },
    { field: "finished_at", headerName: "Fim", minWidth: 165, valueFormatter: value => dateLabel(value as string) },
  ], []);
  const detailData = detail?.details || {};
  const events = Array.isArray(detailData.event_log)
    ? detailData.event_log.filter((event): event is Record<string, unknown> => Boolean(event) && typeof event === "object")
    : [];
  const otherDetails = Object.fromEntries(Object.entries(detailData).filter(([key]) => key !== "event_log").map(([key, value]) => {
    if (key !== "result" || !value || typeof value !== "object" || Array.isArray(value)) return [key, value];
    const { event_log: _duplicateEvents, ...result } = value as Record<string, unknown>;
    return [key, result];
  }));

  return <Stack spacing={1.5}>
    <Box>
      <Typography variant="h6">Logs de execução</Typography>
      <Typography variant="body2" color="text.secondary">Escolha uma execução para ver parâmetros, eventos e erros. Os dados permanecem na tabela de destino quando o processo a publica.</Typography>
    </Box>
    {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
        <TextField size="small" label="Buscar" value={search} onChange={e => { setSearch(e.target.value); setPagination(p => ({ ...p, page: 0 })); }} sx={{ minWidth: 220 }} />
        <TextField select size="small" label="Tipo" value={processType} onChange={e => { setProcessType(e.target.value); setPagination(p => ({ ...p, page: 0 })); }} sx={{ minWidth: 230 }}>
          <MenuItem value="">Todos os tipos de execução</MenuItem>{Object.entries(PROCESS_LABELS).map(([key, label]) => <MenuItem key={key} value={key}>{label}</MenuItem>)}
        </TextField>
        <TextField select size="small" label="Status" value={status} onChange={e => { setStatus(e.target.value); setPagination(p => ({ ...p, page: 0 })); }} sx={{ minWidth: 170 }}>
          <MenuItem value="">Todos os status</MenuItem>{["QUEUED", "RUNNING", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].map(value => <MenuItem key={value} value={value}>{value}</MenuItem>)}
        </TextField>
      </Stack>
    </Paper>
    <Box sx={{ height: 560, width: "100%" }}>
      <DataGrid rows={rows} columns={columns} loading={loading} rowCount={total} paginationMode="server" paginationModel={pagination} onPaginationModelChange={setPagination} pageSizeOptions={[50, 100, 500, 1000]} onRowClick={({ row }) => setSelected(row)} disableRowSelectionOnClick sx={{ "& .MuiDataGrid-row": { cursor: "pointer" }, "& .MuiDataGrid-columnHeaders": { bgcolor: "background.default" } }} />
    </Box>
    <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} fullWidth maxWidth="lg">
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        {detail ? PROCESS_LABELS[detail.process_type] || detail.process_type : "Detalhamento da execução"} · #{selected?.id}
        {detail && <Chip size="small" label={detail.status} color={STATUS_COLOR[detail.status] || "default"} />}
        <IconButton aria-label="Fechar" onClick={() => setSelected(null)} sx={{ ml: "auto" }}><Close /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {detail && <Stack spacing={2}>
          {detail.error && <Alert severity="error">{dataDisplayText(detail.error)}</Alert>}
          <Typography variant="subtitle2">Visão geral</Typography>
          <Stack direction="row" gap={2} flexWrap="wrap">
            <Field label="Fonte" value={sourceLabel(detail.source)} /><Field label="Destino final" value={destinationLabel(detail.destination)} /><Field label="Partições / competências" value={detail.partitions} />
            <Field label="Começo" value={dateLabel(detail.started_at)} /><Field label="Término" value={dateLabel(detail.finished_at)} /><Field label="Etapa" value={detail.phase} />
          </Stack>
          <Divider /><Typography variant="subtitle2">Contagens de linhas</Typography>
          <Stack direction="row" gap={2} flexWrap="wrap">{Object.entries(detail.counts || {}).map(([key, value]) => <Field key={key} label={fieldLabel(key)} value={Number(value).toLocaleString("pt-BR")} />)}</Stack>
          <Divider /><Typography variant="subtitle2">Configuração da execução</Typography>
          <StructuredPanel value={detail.parameters} />
          <Divider /><Typography variant="subtitle2">Linha do tempo de eventos</Typography>
          {events.length ? <Stack spacing={0} sx={{ position: "relative", pl: 2.5, "&:before": { content: '""', position: "absolute", left: 7, top: 8, bottom: 8, width: 2, bgcolor: "divider" } }}>
            {events.map((event, index) => {
              const level = String(event.level || "INFO").toUpperCase();
              const color = level === "ERROR" ? "error" : level === "WARNING" || level === "WARN" ? "warning" : "info";
              return <Box key={`${String(event.at || "event")}-${index}`} sx={{ position: "relative", pb: index === events.length - 1 ? 0 : 1.25, "&:before": { content: '""', position: "absolute", width: 10, height: 10, borderRadius: "50%", bgcolor: `${color}.main`, left: -20, top: 5, boxShadow: "0 0 0 3px white" } }}>
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
                  <Typography variant="caption" color="text.secondary" sx={{ minWidth: 145 }}>{dateLabel(String(event.at || ""))}</Typography>
                  <Chip size="small" label={level === "INFO" ? "Informação" : level === "ERROR" ? "Erro" : level === "WARNING" || level === "WARN" ? "Atenção" : level} color={color} sx={{ alignSelf: { xs: "flex-start", sm: "center" } }} />
                  <Typography variant="body2" fontWeight={600} sx={{ flex: 1 }}>{displayValue(event.message)}</Typography>
                </Stack>
                {event.details != null && Object.keys(event.details as object || {}).length > 0 && <Accordion disableGutters elevation={0} sx={{ mt: 0.25, ml: { sm: 18 }, bgcolor: "transparent", "&:before": { display: "none" } }}>
                  <AccordionSummary expandIcon={<ExpandMore />} sx={{ minHeight: 30, px: 0, "& .MuiAccordionSummary-content": { my: 0.25 } }}><Typography variant="caption" color="primary.main">Ver detalhes deste evento</Typography></AccordionSummary>
                  <AccordionDetails sx={{ px: 0, pt: 0 }}><StructuredPanel value={event.details} /></AccordionDetails>
                </Accordion>}
              </Box>;
            })}
          </Stack> : <Typography variant="body2" color="text.secondary">Nenhum outro evento foi anotado nesta execução.</Typography>}
          {Object.keys(otherDetails).length > 0 && <><Divider /><Accordion disableGutters elevation={0} sx={{ "&:before": { display: "none" } }}><AccordionSummary expandIcon={<ExpandMore />}><Typography variant="subtitle2">Resultado e informações complementares</Typography></AccordionSummary><AccordionDetails sx={{ pt: 0 }}><StructuredPanel value={otherDetails} /></AccordionDetails></Accordion></>}
          {detail.etl_task_id && detail.process_type === "extraction" && <Box><Divider sx={{ mb: 1 }} /><Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Os registros de origem desta extração podem ser consultados na visualização especializada.</Typography><Chip clickable label="Abrir dados de origem" onClick={() => navigate(`/operacoes/extracoes/${detail.etl_task_id}`)} /></Box>}
          {detail.etl_task_id && ["QUEUED", "RUNNING"].includes(detail.status) && <Chip clickable color="error" label="Interromper execução" onClick={async () => { await cancelEtl(detail.etl_task_id!); await load(); setSelected(null); }} sx={{ alignSelf: "flex-start" }} />}
        </Stack>}
      </DialogContent>
    </Dialog>
  </Stack>;
}
