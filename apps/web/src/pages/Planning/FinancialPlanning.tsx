import { Box, Tab, Tabs, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import FinancialDashboard from "./FinancialDashboard";
import BudgetAccuracy from "./BudgetAccuracy";
import Planning from "./Planning";
import VisaoContabil, { EMPTY_FILTERS } from "../GoldDashboard/VisaoContabil";
import FinancialContractsDashboard from "../FinancialContracts/FinancialContractsDashboard";
import ContractStoryDashboard from "./ContractStoryDashboard";
import DollarEvolution from "./DollarEvolution";
import { Alert } from "@mui/material";

const TABS = [
  { code: "dashboard", label: "Dashboard Executivo", title: "Dashboard Executivo", help: "Histórico consolidado do planejamento financeiro." },
  { code: "orcamento", label: "Cadastro Orçamentário", title: "Orçamento", help: "Valores aprovados e carregados por departamento.", budget: true },
  { code: "acuracia-orcamento", label: "Acurácia de Orçamento", title: "Acurácia de Orçamento", help: "Orçamento vigente contra o realizado por mês." },
  { code: "forecast-manual", label: "Forecast Manual", title: "Forecast Manual", help: "Projeções mensais inseridas manualmente." },
  // Temporariamente oculto na interface; implementação e rota permanecem disponíveis.
  // { code: "base-line-contratual", label: "Base Line Contratual", title: "Base Line Contratual", help: "Referência financeira contratual por departamento." },
  { code: "carta-credito", label: "Carta de Credito", title: "Carta de Credito", help: "Créditos planejados por departamento." },
  { code: "defesa-orcamentaria", label: "Defesa Orçamentária", title: "Defesa Orçamentária", help: "Propostas, aprovações, cortes e revisões narradas." },
  { code: "visao-contabil", label: "Visão Contábil", title: "Visão Contábil", help: "Conciliação entre realizado e provisão contábil." },
  { code: "pedidos-sap", label: "Pedidos SAP", title: "Pedidos SAP", help: "Acompanhamento de pedidos SAP." },
  { code: "evolucao-dolar", label: "Evolução do Dólar", title: "Evolução do Dólar", help: "Evolução mensal do dólar e do consumo Azure." },
] as const;

export default function FinancialPlanning() {
  const [searchParams, setSearchParams] = useSearchParams();
  const legacyCommitmentTab = searchParams.get("tab") === "contratos-financeiros"
    || searchParams.get("dashboard") === "visao-financeira-contratos"
    || searchParams.get("dashboard") === "contrato-com-compromisso"
    || (["contract", "contrato"].includes(searchParams.get("tab") || "") && searchParams.get("contract") === "compromisso");
  const legacyStoryTab = ["contract", "contrato"].includes(searchParams.get("tab") || "") && !legacyCommitmentTab;
  const requested = legacyCommitmentTab || legacyStoryTab ? "dashboard" : searchParams.get("tab") || "dashboard";
  const active = TABS.find(tab => tab.code === requested) || TABS[0];
  const executiveSubtab = legacyCommitmentTab ? "compromisso" : legacyStoryTab ? "historia-contrato" : searchParams.get("executive") || "orcamento";

  const changeTab = (_event: React.SyntheticEvent, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", value);
    next.delete("dashboard");
    next.delete("contract");
    next.delete("executive");
    setSearchParams(next, { replace: true });
  };

  const changeExecutiveSubtab = (_event: React.SyntheticEvent, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", "dashboard");
    next.set("executive", value);
    next.delete("dashboard");
    next.delete("contract");
    setSearchParams(next, { replace: true });
  };

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h4">Financeiro</Typography>
        <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
          Cadastros, movimentações e histórico executivo do planejamento financeiro.
        </Typography>
        <Tabs
          value={active.code}
          onChange={changeTab}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="Módulos do planejamento financeiro"
        >
          {TABS.map(tab => <Tab key={tab.code} value={tab.code} label={tab.label} />)}
        </Tabs>
      </Box>
      <Box sx={{ m: 3 }}>
        {active.code === "acuracia-orcamento" ? (
          <BudgetAccuracy />
        ) : active.code === "visao-contabil" ? (
          <VisaoContabil table="dest_dm_cost_visao_completa" filters={EMPTY_FILTERS} filterKey="financial-planning" />
        ) : active.code === "evolucao-dolar" ? (
          <DollarEvolution />
        ) : active.code === "pedidos-sap" ? (
          <Alert severity="info">A aba está pronta, mas ainda não há uma fonte de pedidos SAP conectada para exibir os dados.</Alert>
        ) : active.code === "dashboard" ? (
          <>
            <Tabs value={executiveSubtab} onChange={changeExecutiveSubtab} aria-label="Visões do Dashboard Executivo" sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}>
              <Tab value="orcamento" label="Histórico do Orçamento" />
              <Tab value="historia-contrato" label="História do Contrato" />
              <Tab value="compromisso" label="Histórico Consumo Compromisso" />
            </Tabs>
            {executiveSubtab === "historia-contrato" ? <ContractStoryDashboard /> : executiveSubtab === "compromisso" ? <FinancialContractsDashboard /> : <FinancialDashboard />}
          </>
        ) : (
          <Planning type={active.code} title={active.title} help={active.help} budget={"budget" in active && active.budget} />
        )}
      </Box>
    </Box>
  );
}
