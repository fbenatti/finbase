import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Alert, Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import HistoryGrid from "./HistoryGrid";
import {
  ForecastHistoryMetadata,
  ForecastHistoryPivot, HistoryFilter,
  compareForecastHistory,
  getForecastHistoryMetadata,
  runForecastHistoryPivot,
} from "../../services/api";

export default function ForecastHistory() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const comparison = params.get("comparison") === "1";
  const [metadata, setMetadata] = useState<ForecastHistoryMetadata | null>(null);
  const [result, setResult] = useState<ForecastHistoryPivot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const query = useCallback(async (search = "", offset = 0, filters: Record<string, HistoryFilter> = {}, sort?: { field: string; direction: "asc" | "desc" }) => {
    if (!runId) return;
    try {
      setLoading(true);
      const meta = metadata || await getForecastHistoryMetadata(Number(runId));
      if (!metadata) setMetadata(meta);
      const request = {
        rows: meta.default_rows,
        columns: meta.default_columns,
        filters,
        row_limit: 100,
        offset,
        search,
        sort_field: sort?.field,
        sort_direction: sort?.direction || "asc",
      };
      const data = comparison
        ? await compareForecastHistory(Number(runId), request)
        : await runForecastHistoryPivot(Number(runId), request);
      setResult(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [comparison, metadata, runId]);

  useEffect(() => { void query(); }, [query]);

  return <Box>
    <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
      <Box>
        <Typography variant="h4">{comparison ? "Forecast × Realizado" : "Pivot do snapshot"}</Typography>
        <Typography color="text.secondary">Execução {runId} · consulta server-side</Typography>
      </Box>
      <Button startIcon={<ArrowBack />} onClick={() => navigate("/forecast?tab=history")}>Voltar para Forecast</Button>
    </Stack>
    {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    {loading && !result && <Stack alignItems="center" py={8}><CircularProgress /><Typography mt={2}>Carregando somente a primeira página…</Typography></Stack>}
    {metadata && <Typography variant="body2" color="text.secondary" mb={1}>
      Dimensões: {metadata.default_rows.join(", ") || "nenhuma"} · período: {metadata.default_columns.join(", ") || "nenhum"}
    </Typography>}
    {result && <>
      {comparison && <Typography mb={1}>WAPE: {result.wape == null ? "em acompanhamento" : `${(result.wape * 100).toFixed(2)}%`}</Typography>}
      <HistoryGrid result={result} loading={loading} onQuery={(search, offset, filters = {}, sort) => void query(search, offset, filters, sort)} measureLabel={comparison ? "Forecast × Realizado" : String(metadata?.snapshot?.measure_column || "Valor")} />
    </>}
  </Box>;
}
