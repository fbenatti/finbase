import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Alert, Box, Button, CircularProgress, Paper, Stack, Typography } from "@mui/material";
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

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      {/* Faixa de contexto branca */}
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, mb: 2 }}>
          <Box>
            <Typography variant="h4">{comparison ? "Forecast × Realizado" : "Pivot do snapshot"}</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Execução {runId} · consulta server-side sobre o snapshot publicado.
            </Typography>
          </Box>
          <Button size="small" startIcon={<ArrowBack />} onClick={() => navigate("/projecoes?tab=history")} sx={{ flexShrink: 0 }}>
            Voltar para Forecast
          </Button>
        </Box>
      </Box>

      {/* Área de trabalho */}
      <Box sx={{ p: 3 }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>
        )}
        {loading && !result && (
          <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}>
            <CircularProgress />
          </Box>
        )}

        {metadata && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Box sx={{ mb: 1.5 }}>
              <Typography variant="h6">{comparison ? "Forecast × Realizado" : "Resultado do snapshot"}</Typography>
              <Typography variant="body2" color="text.secondary">
                Dimensões: {metadata.default_rows.join(", ") || "nenhuma"} · período: {metadata.default_columns.join(", ") || "nenhum"}
              </Typography>
            </Box>
            {comparison && result && (
              <Alert severity="info" sx={{ mb: 2 }}>
                WAPE: {result.wape == null ? "em acompanhamento" : (result.wape * 100).toFixed(2) + "%"}
              </Alert>
            )}
            {result && (
              <HistoryGrid
                result={result}
                loading={loading}
                onQuery={(search, offset, filters = {}, sort) => void query(search, offset, filters, sort)}
                measureLabel={comparison ? "Forecast × Realizado" : String(metadata?.snapshot?.measure_column || "Valor")}
              />
            )}
          </Paper>
        )}
      </Box>
    </Box>
  );
}
