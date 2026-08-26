import { useEffect, useState } from "react";
import { Alert, Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { AICostRelationshipHistoryItem, formatApiError, getAICostRelationshipHistory, reprocessAICostRelationships } from "../../services/api";

export default function ReprocessAICostRelationshipsTab() {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<AICostRelationshipHistoryItem[]>([]);
  const load = () => getAICostRelationshipHistory().then(setHistory).catch(() => setHistory([]));
  useEffect(() => { load(); }, []);
  const start = async () => {
    setRunning(true); setError("");
    try {
      const result = await reprocessAICostRelationships();
      if (result.warnings?.length) {
        setError(`Fila aceita, mas ${result.warnings.length} regra(s) têm campo(s) FOCUS inválido(s) e serão puladas: ${result.warnings.join("; ")}`);
      }
      await load();
    }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };
  return <Stack spacing={2}>
    <Typography variant="h6">Relacionamentos de custo IA</Typography>
    <Typography color="text.secondary">Lê cada período da FOCUS uma única vez, classifica os matches e publica uma Gold individual para cada relacionamento ativo. Os dashboards consultam apenas essas tabelas materializadas.</Typography>
    {error && <Alert severity="warning">{error}</Alert>}
    <div><Button variant="contained" startIcon={running ? <CircularProgress size={16} color="inherit" /> : <Refresh />} disabled={running} onClick={start}>Reprocessar todos os relacionamentos</Button></div>
    <Typography variant="subtitle2">Histórico recente</Typography>
    {history.length === 0 ? <Typography color="text.secondary">Nenhuma execução registrada.</Typography> : history.map(item => {
      const failed = item.result_json?.failed || [];
      const warnings = item.result_json?.warnings || [];
      const hasRuleErrors = failed.length > 0;
      return <Box key={item.id}>
        <Alert severity={item.status === "FAILED" ? "error" : item.status === "COMPLETED" ? (hasRuleErrors ? "warning" : "success") : "info"}>
          #{item.id} · {item.status} · {item.phase || "Sem detalhe"}{item.rows_destination != null ? ` · ${item.rows_destination} linhas` : ""}
        </Alert>
        {(failed.length > 0 || warnings.length > 0) && <Box sx={{ mt: 0.5, ml: 2 }}>
          {failed.map((f, index) => <Typography key={`f-${index}`} variant="caption" display="block" color="error.main">
            ✕ {f.name || `Regra #${f.link_id}`}: {f.error || "erro"}
          </Typography>)}
          {warnings.map((w, index) => <Typography key={`w-${index}`} variant="caption" display="block" color="text.secondary">
            ⚠ {w}
          </Typography>)}
        </Box>}
      </Box>;
    })}
  </Stack>;
}
