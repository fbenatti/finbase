import { useEffect, useState } from "react";
import { Box, Paper, Typography, Alert } from "@mui/material";
import { getConnections, SourceConnection, formatApiError } from "../../services/api";
import SchedulesPanel from "./SchedulesPanel";

/** Página dedicada de Agendamentos (coleta automática cloud + ferramentas). */
export default function SchedulesPage() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    getConnections()
      .then(setConnections)
      .catch((e) => setError(formatApiError(e, "Falha ao carregar conexões")));
  }, []);

  return (
    <Box>
      <Typography variant="h4" sx={{ mb: 2 }}>Agendamentos</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Coleta automática diária de dados de cloud e ferramentas. Cada agendamento pode cobrir várias
        conexões e dispara a extração na fila do worker.
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 } }}>
        <SchedulesPanel connections={connections} />
      </Paper>
    </Box>
  );
}
