import { useEffect, useState } from "react";
import { Alert, Button, CircularProgress, Stack, Typography } from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { formatApiError, getAICostRelationshipHistory, reprocessAICostRelationships } from "../../services/api";

export default function ReprocessAICostRelationshipsTab() {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<Array<any>>([]);
  const load = () => getAICostRelationshipHistory().then(setHistory).catch(() => setHistory([]));
  useEffect(() => { load(); }, []);
  const start = async () => {
    setRunning(true); setError("");
    try { await reprocessAICostRelationships(); await load(); }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };
  return <Stack spacing={2}>
    <Typography variant="h6">Relacionamentos de custo IA</Typography>
    <Typography color="text.secondary">Lê cada período da FOCUS uma única vez, classifica os matches e publica uma Gold individual para cada relacionamento ativo. Os dashboards consultam apenas essas tabelas materializadas.</Typography>
    {error && <Alert severity="error">{error}</Alert>}
    <div><Button variant="contained" startIcon={running ? <CircularProgress size={16} color="inherit" /> : <Refresh />} disabled={running} onClick={start}>Reprocessar todos os relacionamentos</Button></div>
    <Typography variant="subtitle2">Histórico recente</Typography>
    {history.length === 0 ? <Typography color="text.secondary">Nenhuma execução registrada.</Typography> : history.map(item =>
      <Alert key={item.id} severity={item.status === "FAILED" ? "error" : item.status === "COMPLETED" ? "success" : "info"}>
        #{item.id} · {item.status} · {item.phase || "Sem detalhe"}{item.rows_destination != null ? ` · ${item.rows_destination} linhas` : ""}
      </Alert>)}
  </Stack>;
}
