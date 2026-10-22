import { Box, Paper, Stack, Tab, Tabs, Typography } from "@mui/material";
import { Navigate } from "react-router-dom";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import Destinations from "../Destinations/Destinations";
import Relationship from "../Relationship/Relationship";

const TABS = [
  { id: "destinations", label: "Tabelas de saída" },
  { id: "relationship", label: "Relações entre tabelas" },
] as const;

export default function Tables() {
  const [params, setParams] = useSearchParams();
  // Legacy tab id redirect: ai-cost-relationship -> cost-relationship.
  const rawTab = params.get("tab");
  // Itens de governança foram movidos para a área de Governança. Preserve links antigos.
  if (rawTab === "clone-rules" || rawTab === "cost-relationship" || rawTab === "ai-cost-relationship") {
    const governanceTab = rawTab === "clone-rules" ? "clone-rules" : "cost-relationship";
    return <Navigate to={governanceTab === "clone-rules" ? "/governanca/compromissos" : `/governanca?tab=${governanceTab}`} replace />;
  }
  const normalized = rawTab === "ai-cost-relationship" ? "cost-relationship" : rawTab;
  const activeTab = TABS.some((t) => t.id === normalized) ? normalized! : "destinations";

  const selectTab = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", id);
    setParams(next, { replace: true });
  };

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h6">Catálogo de tabelas e colunas</Typography>
        <Typography variant="body2" color="text.secondary">
          Tabelas de saída e as relações que ligam cada origem a elas.
        </Typography>
      </Box>

      <Paper variant="outlined">
        <Tabs value={activeTab} onChange={(_, id) => selectTab(id)} sx={{ px: 2, borderBottom: 1, borderColor: "divider" }}>
          {TABS.map((tab) => <Tab key={tab.id} value={tab.id} label={tab.label} />)}
        </Tabs>
        <Box sx={{ p: { xs: 1.5, md: 2.5 } }}>
          {activeTab === "destinations" && <Destinations embedded />}
          {activeTab === "relationship" && <Relationship embedded />}
        </Box>
      </Paper>
    </Stack>
  );
}
