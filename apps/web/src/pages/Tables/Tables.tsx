import { Box, Paper, Typography } from "@mui/material";
import { Navigate } from "react-router-dom";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import Destinations from "../Destinations/Destinations";
import Relationship from "../Relationship/Relationship";

const TABS = [
  { id: "destinations", label: "Tabelas de destino" },
  { id: "relationship", label: "Relacionamentos" },
] as const;

export default function Tables() {
  const [params] = useSearchParams();
  // Legacy tab id redirect: ai-cost-relationship -> cost-relationship.
  const rawTab = params.get("tab");
  // Itens de governança foram movidos para a área de Governança. Preserve links antigos.
  if (rawTab === "clone-rules" || rawTab === "cost-relationship" || rawTab === "ai-cost-relationship") {
    const governanceTab = rawTab === "clone-rules" ? "clone-rules" : "cost-relationship";
    return <Navigate to={governanceTab === "clone-rules" ? "/governanca/compromissos" : `/governanca?tab=${governanceTab}`} replace />;
  }
  const normalized = rawTab === "ai-cost-relationship" ? "cost-relationship" : rawTab;
  const activeTab = TABS.some((t) => t.id === normalized) ? normalized! : "destinations";

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
          Gerencie tabelas de destino e mapeamentos de relacionamentos em um só lugar.
        </Typography>
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
          {activeTab === "relationship" && <Relationship embedded />}
        </Paper>
      </Box>
    </Box>
  );
}
