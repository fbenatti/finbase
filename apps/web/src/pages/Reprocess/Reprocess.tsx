import { dataDisplayText } from "../../utils/dataPresentation";
import { Box, Paper, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import { canonicalOperationsParams } from "../../utils/dataNavigation";
import Extraction from "../Extraction/Extraction";
import SchedulesPanel from "../Extraction/SchedulesPanel";
import ReprocessDataTab from "./ReprocessDataTab";
import ReprocessForecastTab from "./ReprocessForecastTab";
import ReprocessSharedCostTab from "./ReprocessSharedCostTab";
import ReprocessGoldTab from "./ReprocessGoldTab";
import ProcessingLogsTab from "./ProcessingLogsTab";
import { useEffect, useState } from "react";
import { getConnections, SourceConnection, formatApiError } from "../../services/api";
import { Alert } from "@mui/material";

export default function Reprocess() {
  const [params, setParams] = useSearchParams();
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [schedulesError, setSchedulesError] = useState("");
  const requestedTab = params.get("tab");
  const legacyAccuracyTab = requestedTab === "accuracy";
  const normalizedTab = legacyAccuracyTab ? "forecast" : requestedTab === "cubos" ? "gold" : requestedTab;
  const allowedTabs = ["data", "forecast", "shared", "gold", "extraction", "schedules", "logs"];
  const tab = allowedTabs.includes(normalizedTab || "") ? normalizedTab! : "data";

  useEffect(() => {
    const next = canonicalOperationsParams(params);
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [params, setParams]);

  useEffect(() => {
    if (tab === "schedules") {
      getConnections()
        .then(setConnections)
        .catch((e) => setSchedulesError(formatApiError(e, "Falha ao carregar conexões")));
    }
  }, [tab]);

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box sx={{ bgcolor: "background.paper", px: { xs: 2, md: 4 }, pt: 3, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Reprocessar</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        
        Extração, consolidação, projeções e custo compartilhado no mesmo lugar. Acompanhe as execuções no sino de notificações.
        </Typography>
      </Box>
      <Box sx={{ bgcolor: "#f8fafc", p: { xs: 2, md: 4 }, minHeight: "calc(100vh - 150px)" }}>
        <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 }, bgcolor: "background.paper" }}>
          {tab === "data" && <ReprocessDataTab />}
          {tab === "forecast" && <ReprocessForecastTab />}
          {tab === "shared" && <ReprocessSharedCostTab />}
          {tab === "gold" && <ReprocessGoldTab />}
          {tab === "logs" && <ProcessingLogsTab />}
          {tab === "extraction" && <Extraction embedded />}
          {tab === "schedules" && (
            <>
              <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>Agendamentos</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Coleta automática diária de dados de cloud e ferramentas. Cada agendamento pode cobrir várias
                conexões e dispara a extração na fila do worker.
              </Typography>
              {schedulesError && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setSchedulesError("")}>{dataDisplayText(schedulesError)}</Alert>}
              <SchedulesPanel connections={connections} />
            </>
          )}
        </Paper>
      </Box>
    </Box>
  );
}
