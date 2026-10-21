import { useState } from "react";
import { Alert, Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { formatApiError, reprocessAICostRelationships } from "../../services/api";

export default function ReprocessAICostRelationshipsTab() {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const start = async () => {
    setRunning(true); setError("");
    try {
      const result = await reprocessAICostRelationships();
      if (result.warnings?.length) {
        setError(`Fila aceita, mas ${result.warnings.length} regra(s) têm campo(s) FOCUS inválido(s) e serão puladas: ${result.warnings.join("; ")}`);
      }
    }
    catch (e) { setError(formatApiError(e, "Não foi possível enfileirar o reprocessamento")); }
    finally { setRunning(false); }
  };
  return <Stack spacing={2}>
    <Typography variant="h6">Relacionamentos de custo IA</Typography>
    <Typography color="text.secondary">Lê cada período de custos uma única vez, aplica as regras e publica uma base consolidada para cada relacionamento ativo. Os painéis consultam essas bases.</Typography>
    {error && <Alert severity="warning">{error}</Alert>}
    <div><Button variant="contained" startIcon={running ? <CircularProgress size={16} color="inherit" /> : <Refresh />} disabled={running} onClick={start}>Reprocessar todos os relacionamentos</Button></div>
  </Stack>;
}
