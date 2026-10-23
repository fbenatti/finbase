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
  { code: "dashboard", label: "Painel executivo", title: "Painel executivo", help: "Histórico consolidado do planejamento financeiro." },
  { code: "orcamento", label: "Cadastro de metas", title: "Orçamentos", help: "Valores aprovados e carregados por unidade.", budget: true },
  { code: "aderencia-orcamento", label: "Aderência à meta", title: "Aderência à meta", help: "Meta vigente contra o efetivado por mês." },
  { code: "forecast-manual", label: "Forecasts manuais", title: "Forecasts manuais", help: "Forecasts mensais inseridas manualmente." },
  // Temporariamente oculto na interface; implementação e rota permanecem disponíveis.
  // { code: "base-line-contratual", label: "Base Line Contratual", title: "Base Line Contratual", help: "Referência financeira contratual por departamento." },
  { code: "carta-credito", label: "Carta de crédito", title: "Carta de crédito", help: "Créditos planejados por unidade." },
  { code: "defesa-orcamentaria", label: "Defesa de meta", title: "Defesa de meta", help: "Propostas, aprovações, cortes e revisões em texto." },
  { code: "visao-contabil", label: "Pedidos SAP", title: "Pedidos SAP", help: "Pedidos SAP e evolução do consumo e do câmbio." },
] as const;

export default function FinancialPlanning() {
  const [searchParams] = useSearchParams();
  const legacyCommitmentTab = searchParams.get("tab") === "contratos-financeiros"
    || searchParams.get("dashboard") === "visao-financeira-contratos"
    || searchParams.get("dashboard") === "contrato-com-compromisso"
    || (["contract", "contrato"].includes(searchParams.get("tab") || "") && searchParams.get("contract") === "compromisso");
  const legacyStoryTab = ["contract", "contrato"].includes(searchParams.get("tab") || "") && !legacyCommitmentTab;
  const requestedTab = searchParams.get("tab");
  const requested = legacyCommitmentTab || legacyStoryTab ? "dashboard" : requestedTab || "dashboard";
  const active = TABS.find(tab => tab.code === requested) || TABS[0];
  const executiveSubtab = legacyCommitmentTab ? "compromisso" : legacyStoryTab ? "historia-contrato" : searchParams.get("executive") || "orcamento";

  return (
    <Box sx={{ minWidth: 0 }}>
      <Box>
        {active.code === "visao-contabil" && <><Typography variant="h4" sx={{ mb: 0.5 }}>
          {active.title}
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 2 }}>{active.help}</Typography></>}
        {active.code === "aderencia-orcamento" ? (
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
