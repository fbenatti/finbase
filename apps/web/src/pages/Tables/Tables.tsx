import { Box, Paper, Tab, Tabs, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import Destinations from "../Destinations/Destinations";
import CloneRules from "../CloneRules/CloneRules";
import Relationship from "../Relationship/Relationship";
import Dimon from "../Dimon/Dimon";

const TABS = [
  { id: "destinations", label: "Tabelas de destino" },
  { id: "clone-rules", label: "Regras de clonagem" },
  { id: "relationship", label: "Relacionamentos" },
  { id: "cost-relationship", label: "Camada Dimon de Custo" },
] as const;

export default function Tables() {
  const [params, setParams] = useSearchParams();
  // Legacy tab id redirect: ai-cost-relationship -> cost-relationship.
  const rawTab = params.get("tab");
  const normalized = rawTab === "ai-cost-relationship" ? "cost-relationship" : rawTab;
  const activeTab = TABS.some((t) => t.id === normalized) ? normalized! : "destinations";

  const setTab = (next: string) => {
    const updated = new URLSearchParams(params);
    updated.set("tab", next);
    setParams(updated, { replace: true });
  };

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box
        sx={{
          bgcolor: "background.paper",
          px: { xs: 2, md: 4 },
          pt: 3,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          Tabelas
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Gerencie tabelas de destino, regras de clonagem e mapeamentos de relacionamentos em um só lugar.
        </Typography>
        <Tabs
          value={activeTab}
          onChange={(_, value) => setTab(value)}
          variant="scrollable"
          allowScrollButtonsMobile
        >
          {TABS.map((item) => (
            <Tab key={item.id} value={item.id} label={item.label} />
          ))}
        </Tabs>
      </Box>

      <Box
        sx={{
          bgcolor: "#f8fafc",
          p: { xs: 2, md: 4 },
          minHeight: "calc(100vh - 150px)",
        }}
      >
        <Paper
          variant="outlined"
          sx={{ p: { xs: 1.5, md: 2.5 }, bgcolor: "background.paper" }}
        >
          {activeTab === "destinations" && <Destinations embedded />}
          {activeTab === "clone-rules" && <CloneRules embedded />}
          {activeTab === "relationship" && <Relationship embedded />}
          {activeTab === "cost-relationship" && <Dimon embedded />}
        </Paper>
      </Box>
    </Box>
  );
}
