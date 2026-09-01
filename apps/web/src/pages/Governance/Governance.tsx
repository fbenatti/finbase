import { Navigate, useSearchParams } from "react-router-dom";
import { Box, Paper, Tab, Tabs, Typography } from "@mui/material";
import Rules from "../Rules/Rules";
import { ClassificationRulesTab, TagsTab } from "../Classification/Classification";
import VisaoContabil, { EMPTY_FILTERS } from "../DimonDashboard/VisaoContabil";
import CloneRules from "../CloneRules/CloneRules";
import Dimon from "../Dimon/Dimon";

const TABS = [
  { id: "rules", label: "Regras financeiras" },
  { id: "classification", label: "Classificar Departamentos" },
  { id: "clone-rules", label: "Regras de clonagem" },
  { id: "cost-relationship", label: "Camada Dimon de Custo" },
  { id: "visao-contabil", label: "Visão Contábil" },
  { id: "tags", label: "Tags Automatizadas" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Governance() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  if (requested === "desligue" || requested === "orfaos") {
    return <Navigate to={`/savings?tab=${requested}`} replace />;
  }
  const tab: TabId = TABS.some((item) => item.id === requested) ? (requested as TabId) : "rules";

  const setTab = (value: TabId) => {
    setSearchParams({ tab: value }, { replace: true });
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      {/* Faixa de contexto branca */}
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h4">Governança</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Regras de negócio, clonagem, classificação organizacional, políticas de tags e visão contábil.
          </Typography>
        </Box>
        <Tabs value={tab} onChange={(_, value: TabId) => setTab(value)} aria-label="Governança" sx={{ mb: 0, minHeight: 42 }}>
          {TABS.map((item) => (
            <Tab key={item.id} value={item.id} label={item.label} sx={{ minHeight: 42 }} />
          ))}
        </Tabs>
      </Box>

      {/* Área de trabalho com campos simples de 40px */}
      <Box sx={{
        p: 3,
        "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
        "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
      }}>
        {tab === "rules" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Rules embedded />
          </Paper>
        )}
        {tab === "classification" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <ClassificationRulesTab />
          </Paper>
        )}
        {tab === "clone-rules" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <CloneRules embedded />
          </Paper>
        )}
        {tab === "cost-relationship" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Dimon embedded />
          </Paper>
        )}
        {tab === "tags" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <TagsTab />
          </Paper>
        )}
        {tab === "visao-contabil" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <VisaoContabil table="dest_dm_cost_visao_completa" filters={EMPTY_FILTERS} filterKey="governance" />
          </Paper>
        )}
      </Box>
    </Box>
  );
}
