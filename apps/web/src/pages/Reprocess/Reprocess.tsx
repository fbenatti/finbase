import { Box, Paper, Tab, Tabs, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import Extraction from "../Extraction/Extraction";
import ReprocessDataTab from "./ReprocessDataTab";
import ReprocessForecastTab from "./ReprocessForecastTab";
import ReprocessSharedCostTab from "./ReprocessSharedCostTab";
import ReprocessTasksPanel from "./ReprocessTasksPanel";

const TABS = [
  { id: "data", label: "Reprocessar dados" },
  { id: "forecast", label: "Forecast" },
  { id: "shared", label: "Custo compartilhado" },
  { id: "extraction", label: "Extração & Monitoramento" },
] as const;

export default function Reprocess() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some(item => item.id === params.get("tab")) ? params.get("tab")! : "data";

  const setTab = (next: string) => {
    const updated = new URLSearchParams(params);
    updated.set("tab", next);
    if (next !== "extraction") updated.delete("extTab");
    setParams(updated, { replace: true });
  };

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box sx={{ bgcolor: "background.paper", px: { xs: 2, md: 4 }, pt: 3, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Reprocessar</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Extração, Gold, forecast e custo compartilhado no mesmo lugar, com as tasks da fila visíveis acima de cada ação.
        </Typography>
        <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" allowScrollButtonsMobile>
          {TABS.map(item => <Tab key={item.id} value={item.id} label={item.label} />)}
        </Tabs>
      </Box>
      <Box sx={{ bgcolor: "#f8fafc", p: { xs: 2, md: 4 }, minHeight: "calc(100vh - 150px)" }}>
        <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 }, bgcolor: "background.paper" }}>
          <ReprocessTasksPanel />
          {tab === "data" && <ReprocessDataTab />}
          {tab === "forecast" && <ReprocessForecastTab />}
          {tab === "shared" && <ReprocessSharedCostTab />}
          {tab === "extraction" && <Extraction embedded />}
        </Paper>
      </Box>
    </Box>
  );
}
