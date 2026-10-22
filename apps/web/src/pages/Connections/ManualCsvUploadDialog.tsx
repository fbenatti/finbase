import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material";
import { CloudUpload, Delete, Save } from "./ConnectionIcons";
import { GridColDef } from "@mui/x-data-grid";
import {
  previewCsv, ingestCsv, ingestCsvBatch, getCsvUploadHistory, formatApiError, IngestedBlob,
} from "../../services/api";
import SortableTable from "../../components/SortableTable";

const TYPE_OPTIONS = [
  { value: "TEXT", label: "Texto" },
  { value: "INTEGER", label: "Inteiro" },
  { value: "DOUBLE", label: "Decimal" },
  { value: "DATE", label: "Data" },
  { value: "BOOLEAN", label: "Booleano" },
];

interface CsvColumnPreview {
  name: string;
  inferred_type: string;
  sample_values: unknown[];
  null_count: number;
  total_count: number;
}

interface CsvPreview {
  row_count: number;
  column_count: number;
  columns: CsvColumnPreview[];
  sample_rows: Record<string, unknown>[];
  suggested_billing_period: string | null;
  monthly_grain?: boolean;
  current_month?: boolean;
  warning_message?: string | null;
  skip_gold_realizado?: boolean;
}

interface IngestResult {
  row_count: number;
  billing_period: string;
  table_name: string;
  warning_message?: string | null;
  skip_gold_realizado?: boolean;
}

interface ManualCsvUploadDialogProps {
  open: boolean;
  onClose: () => void;
  connectionId: number;
  connectionName: string;
}

export default function ManualCsvUploadDialog({
  open,
  onClose,
  connectionId,
  connectionName,
}: ManualCsvUploadDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [columnTypes, setColumnTypes] = useState<Record<string, string>>({});
  const [billingPeriod, setBillingPeriod] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<IngestResult | null>(null);
  const [batchResult, setBatchResult] = useState<IngestResult[] | null>(null);
  const [history, setHistory] = useState<IngestedBlob[]>([]);

  const reset = () => {
    setFile(null);
    setPreview(null);
    setColumnTypes({});
    setBillingPeriod("");
    setError("");
    setResult(null);
    setBatchResult(null);
  };

  const loadHistory = () => {
    getCsvUploadHistory(connectionId).then(setHistory).catch(() => {});
  };

  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    loadHistory();
  }, [open, connectionId]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] || null;
    setFile(selected);
    setPreview(null);
    setResult(null);
    setError("");
    setColumnTypes({});
    if (!selected) return;

    setLoading(true);
    try {
      const data = await previewCsv(selected);
      setPreview(data);
      const types: Record<string, string> = {};
      data.columns.forEach((c) => { types[c.name] = c.inferred_type; });
      setColumnTypes(types);
      setBillingPeriod(data.suggested_billing_period || "");
    } catch (e) {
      setError(formatApiError(e, "Não foi possível ler a prévia do CSV"));
    } finally {
      setLoading(false);
    }
  };

  const handleIngest = async () => {
    if (!file || !billingPeriod) {
      setError("Escolha o arquivo e informe a competência de faturamento.");
      return;
    }
    if (!/^\d{6}$/.test(billingPeriod)) {
      setError("A competência deve estar no formato AAAAMM (ex.: 202601).");
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);
    try {
      const overrides = Object.entries(columnTypes).map(([name, data_type]) => ({ name, data_type }));
      const res = await ingestCsv(file, connectionId, billingPeriod, overrides);
      setResult(res);
      setFile(null);
      setPreview(null);
      setColumnTypes({});
    } catch (e) {
      setError(formatApiError(e, "Não foi possível importar o CSV"));
    } finally {
      setLoading(false);
    }
  };

  const handleBatchIngest = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setLoading(true);
    setError("");
    setBatchResult(null);
    try {
      const res = await ingestCsvBatch(files, connectionId);
      setBatchResult(res);
      loadHistory();
    } catch (e) {
      setError(formatApiError(e, "Falha ao carregar vários CSVs"));
    } finally {
      setLoading(false);
    }
    e.target.value = "";
  };

  const previewColumns: GridColDef[] = useMemo(() => {
    if (!preview) return [];
    return preview.columns.map((col) => ({ field: col.name, headerName: col.name, flex: 1, minWidth: 140 }));
  }, [preview]);

  const previewRows = useMemo(() => {
    if (!preview) return [];
    return preview.sample_rows.map((row, idx) => ({ id: idx, ...row }));
  }, [preview]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth>
      <DialogTitle>Carregar arquivos CSV<Typography variant="body2" color="text.secondary">{connectionName}</Typography></DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>

          Carregue relatórios CSV locais diretamente na base de origem. Para carregar vários meses de uma
          vez, use o botão "Carregar vários arquivos"; o período é identificado pelas colunas de data/faturamento
          do CSV (com alternativa pelo nome do arquivo).
        </Typography>

        <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 3 }} alignItems="flex-start">
          <Button
            component="label"
            variant="contained"
            startIcon={<CloudUpload />}
            sx={{ minHeight: 40 }}
            disabled={loading}
          >
            {file ? file.name : "Selecionar arquivo CSV"}
            <input type="file" accept=".csv" hidden onChange={handleFileChange} />
          </Button>

          <Button
            component="label"
            variant="outlined"
            startIcon={<CloudUpload />}
            sx={{ minHeight: 40 }}
            disabled={loading}
          >
            Importar vários arquivos
            <input type="file" accept=".csv" multiple hidden onChange={handleBatchIngest} />
          </Button>

          {file && (
            <Button
              variant="outlined"
              color="error"
              startIcon={<Delete />}
              onClick={reset}
              sx={{ minHeight: 40 }}
            >
              Remover arquivo
            </Button>
          )}
        </Stack>

        {error && <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

        {preview?.warning_message && (
          <Alert severity="warning" sx={{ mb: 3 }}>
            {dataDisplayText(preview.warning_message)}
          </Alert>
        )}

        {result && (
          <Alert severity={result.skip_gold_realizado ? "warning" : "success"} sx={{ mb: 3 }} onClose={() => setResult(null)}>
            <strong>{result.skip_gold_realizado ? "CSV gravado na base de origem (consolidação e previsão bloqueadas)." : "CSV importado com sucesso."}</strong><br />
            {result.row_count.toLocaleString()} linhas gravadas em {result.table_name} para o período {result.billing_period}.
            {result.warning_message && (
              <>
                <br />
                {result.warning_message}
              </>
            )}
          </Alert>
        )}

        {batchResult && batchResult.length > 0 && (
          <Alert
            severity={batchResult.some(r => r.skip_gold_realizado) ? "warning" : "success"}
            sx={{ mb: 3 }}
            onClose={() => setBatchResult(null)}
          >
            <strong>
              {batchResult.length} arquivo(s) gravados na base de origem
              {batchResult.some(r => r.skip_gold_realizado) ? " (alguns com consolidação e previsão bloqueadas)." : "."}
            </strong>
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {batchResult.map((r, i) => (
                <li key={i}>
                  {r.billing_period}: {r.row_count.toLocaleString()} linhas em {r.table_name}
                  {r.skip_gold_realizado && " — não entra em efetivado nem em previsão"}
                  {r.warning_message && !r.skip_gold_realizado && ` — ${r.warning_message}`}
                </li>
              ))}
            </ul>
          </Alert>
        )}

        {preview && (
          <Paper sx={{ p: 3, mb: 3 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h6">
                  Preview: {preview.row_count.toLocaleString()} linhas · {preview.column_count} colunas
                </Typography>
                {preview.monthly_grain && (
                  <Chip label="Granularidade por mês" size="small" color="warning" />
                )}
              </Stack>
              <TextField
                label="Competência de faturamento (AAAAMM)"
                value={billingPeriod}
                onChange={(e) => setBillingPeriod(e.target.value)}
                sx={{ width: 240 }}
                helperText="Identificado pelo CSV; tem prioridade sobre o nome do arquivo"
              />
            </Stack>

            <Typography variant="subtitle2" sx={{ mb: 1 }}>Tipos de dados (ajuste antes de carregar)</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 2 }}>
              {preview.columns.map((col) => (
                <Box key={col.name} sx={{ minWidth: 220, mb: 1 }}>
                  <FormControl fullWidth size="small">
                    <InputLabel>{col.name}</InputLabel>
                    <Select
                      value={columnTypes[col.name] || col.inferred_type}
                      label={col.name}
                      onChange={(e) => setColumnTypes((prev) => ({ ...prev, [col.name]: e.target.value as string }))}
                    >
                      {TYPE_OPTIONS.map((opt) => <MenuItem key={opt.value} value={opt.value}>{opt.label}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <Typography variant="caption" color="text.secondary">
                    {col.null_count > 0 ? `${col.null_count} nulos · ` : ""}inferido: {col.inferred_type}
                  </Typography>
                </Box>
              ))}
            </Stack>

            <Typography variant="subtitle2" sx={{ mb: 1 }}>Prévia dos dados</Typography>
            <Box sx={{ height: 320 }}>
              <DataGrid rows={previewRows} columns={previewColumns} disableRowSelectionOnClick />
            </Box>

            <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2 }}>
              <Button
                variant="contained"
                color="success"
                startIcon={<Save />}
                onClick={handleIngest}
                disabled={loading || !billingPeriod}
              >
                {loading ? "Lendo..." : "Carregar na base de origem"}
              </Button>
            </Stack>
          </Paper>
        )}

        {history.length > 0 && (
          <Box>
            <Typography variant="h6" sx={{ mb: 2 }}>Log de cargas</Typography>
            <SortableTable
              data={history}
              getRowKey={(b) => b.id}
              defaultSortKey="ingested_at"
              defaultSortDir="desc"
              columns={[
                { key: "billing_period", label: "Competência", width: 110 },
                { key: "blob_path", label: "Arquivo" },
                { key: "row_count", label: "Linhas", width: 100, render: (b) => b.row_count?.toLocaleString() || "-" },
                { key: "status", label: "Situação", width: 120, render: (b) => <Chip label={b.status} size="small" color="success" /> },
                { key: "ingested_at", label: "Importado em", width: 170, render: (b) => new Date(b.ingested_at).toLocaleString() },
              ]}
            />
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Fechar</Button>
      </DialogActions>
    </Dialog>
  );
}
