import { Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { Box, Paper, Tab, Tabs, Typography } from "@mui/material";
import Rules from "../Rules/Rules";
import { ClassificationRulesTab, TagsTab } from "../Classification/Classification";
import CloneRules from "../CloneRules/CloneRules";
import Gold from "../GoldMarts/GoldMarts";
import FinancialContracts from "./FinancialContracts";
import Departments from "../Departments/Departments";

const TABS = [
  { id: "organization", label: "Organograma", perms: [{ module: "planning", scope: null }] },
  { id: "classification", label: "Classificar projetos", perms: [{ module: "rules", scope: null }] },
  { id: "rules", label: "Regras financeiras", perms: [{ module: "rules", scope: null }] },
  { id: "financial-contracts", label: "Contratos financeiros", perms: [{ module: "rules", scope: null }] },
  { id: "clone-rules", label: "Reservas e Savings Plans", perms: [{ module: "reservations", scope: null }] },
  { id: "cost-relationship", label: "Geração de Cubos", perms: [{ module: "gold", scope: null }] },
  { id: "tags", label: "Tags Automatizadas", perms: [{ module: "azure_tag_policy", scope: null }] },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Governance() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const requested = searchParams.get("tab");
  if (requested === "desligue" || requested === "orfaos") {
    return <Navigate to={`/savings?tab=${requested}`} replace />;
  }
  const allowedTabs = TABS.filter((item) => item.perms.some((p) => hasPerm(p.module, "read", p.scope)));
  const fallback = allowedTabs[0]?.id ?? null;
  const tab: TabId | null =
    requested && TABS.some((item) => item.id === requested && allowedTabs.some((a) => a.id === requested))
      ? (requested as TabId)
      : fallback;

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
            Regras de negócio, reservas e Savings Plans, classificação organizacional e políticas de tags.
          </Typography>
        </Box>
        <Tabs value={tab} onChange={(_, value: TabId) => setTab(value)} aria-label="Governança" sx={{ mb: 0, minHeight: 42 }}>
          {allowedTabs.map((item) => (
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
        {!tab && (
          <Typography color="text.secondary" sx={{ py: 6, textAlign: "center" }}>
            Você não tem permissão de leitura para nenhuma aba desta página. Solicite acesso ao administrador.
          </Typography>
        )}
        {tab === "organization" && <Departments embedded />}
        {tab === "rules" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Rules embedded />
          </Paper>
        )}
        {tab === "financial-contracts" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <FinancialContracts />
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
            <Gold embedded />
          </Paper>
        )}
        {tab === "tags" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <TagsTab />
          </Paper>
        )}
      </Box>
    </Box>
  );
}
