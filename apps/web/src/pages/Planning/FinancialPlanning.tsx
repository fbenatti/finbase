import { Box, Typography } from "@mui/material";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import FinancialDashboard from "./FinancialDashboard";
import BudgetAccuracy from "./BudgetAccuracy";
import Planning from "./Planning";
import VisaoContabil, { EMPTY_FILTERS } from "../GoldDashboard/VisaoContabil";
import FinancialContractsDashboard from "../FinancialContracts/FinancialContractsDashboard";
import ContractStoryDashboard from "./ContractStoryDashboard";
import DollarEvolution from "./DollarEvolution";

const TABS = [
  { code: "dashboard", label: "Dashboard Executivo", title: "Dashboard Executivo", help: "Histórico consolidado do planejamento financeiro." },
  { code: "orcamento", label: "Cadastro Orçamentário", title: "Orçamento", help: "Valores aprovados e carregados por departamento.", budget: true },
  { code: "acuracia-orcamento", label: "Acurácia de Orçamento", title: "Acurácia de Orçamento", help: "Orçamento vigente contra o realizado por mês." },
  { code: "forecast-manual", label: "Forecast Manual", title: "Forecast Manual", help: "Projeções mensais inseridas manualmente." },
  // Temporariamente oculto na interface; implementação e rota permanecem disponíveis.
  // { code: "base-line-contratual", label: "Base Line Contratual", title: "Base Line Contratual", help: "Referência financeira contratual por departamento." },
  { code: "carta-credito", label: "Carta de Credito", title: "Carta de Credito", help: "Créditos planejados por departamento." },
  { code: "defesa-orcamentaria", label: "Defesa Orçamentária", title: "Defesa Orçamentária", help: "Propostas, aprovações, cortes e revisões narradas." },
  { code: "visao-contabil", label: "Pedidos SAP", title: "Pedidos SAP", help: "Pedidos SAP e evolução do consumo e câmbio." },
] as const;

export default function FinancialPlanning() {
  const [searchParams] = useSearchParams();
  const legacyCommitmentTab = searchParams.get("tab") === "contratos-financeiros"
    || searchParams.get("dashboard") === "visao-financeira-contratos"
    || searchParams.get("dashboard") === "contrato-com-compromisso"
    || (["contract", "contrato"].includes(searchParams.get("tab") || "") && searchParams.get("contract") === "compromisso");
  const legacyStoryTab = ["contract", "contrato"].includes(searchParams.get("tab") || "") && !legacyCommitmentTab;
  const requested = legacyCommitmentTab || legacyStoryTab ? "dashboard" : searchParams.get("tab") || "dashboard";
  const active = TABS.find(tab => tab.code === requested) || TABS[0];
  const executiveSubtab = legacyCommitmentTab ? "compromisso" : legacyStoryTab ? "historia-contrato" : searchParams.get("executive") || "orcamento";

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box sx={{ m: 3 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          {active.code === "dashboard" && executiveSubtab === "historia-contrato" ? "História dos contratos" : active.code === "dashboard" && executiveSubtab === "compromisso" ? "Consumo de compromissos" : active.title}
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 2 }}>{active.help}</Typography>
        {active.code === "acuracia-orcamento" ? (
          <BudgetAccuracy />
        ) : active.code === "visao-contabil" ? (
          <VisaoContabil table="dest_dm_cost_visao_completa" filters={EMPTY_FILTERS} filterKey="financial-planning" dollarEvolution={<DollarEvolution />} />
        ) : active.code === "dashboard" ? (
          <>
            {executiveSubtab === "historia-contrato" ? <ContractStoryDashboard /> : executiveSubtab === "compromisso" ? <FinancialContractsDashboard /> : <FinancialDashboard />}
          </>
        ) : (
          <Planning type={active.code} title={active.title} help={active.help} budget={"budget" in active && active.budget} />
        )}
      </Box>
    </Box>
  );
}
