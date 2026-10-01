import { Box, Tab, Tabs, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import FinancialDashboard from "./FinancialDashboard";
import BudgetAccuracy from "./BudgetAccuracy";
import Planning from "./Planning";
import VisaoContabil, { EMPTY_FILTERS } from "../GoldDashboard/VisaoContabil";
import FinancialContractsDashboard from "../FinancialContracts/FinancialContractsDashboard";
import ContractStoryDashboard from "./ContractStoryDashboard";

const TABS = [
  { code: "orcamento", label: "Cadastro Orçamentário", title: "Orçamento", help: "Valores aprovados e carregados por departamento.", budget: true },
  { code: "acuracia-orcamento", label: "Acurácia de Orçamento", title: "Acurácia de Orçamento", help: "Orçamento vigente contra o realizado por mês." },
  { code: "forecast-manual", label: "Forecast Manual", title: "Forecast Manual", help: "Projeções mensais inseridas manualmente." },
  // Temporariamente oculto na interface; implementação e rota permanecem disponíveis.
  // { code: "base-line-contratual", label: "Base Line Contratual", title: "Base Line Contratual", help: "Referência financeira contratual por departamento." },
  { code: "carta-credito", label: "Carta de Credito", title: "Carta de Credito", help: "Créditos planejados por departamento." },
  { code: "defesa-orcamentaria", label: "Defesa Orçamentária", title: "Defesa Orçamentária", help: "Propostas, aprovações, cortes e revisões narradas." },
  { code: "visao-contabil", label: "Visão Contábil", title: "Visão Contábil", help: "Conciliação entre realizado e provisão contábil." },
  { code: "dashboard", label: "Dashboard Executivo", title: "Dashboard Executivo", help: "Histórico consolidado do planejamento financeiro." },
] as const;

export default function FinancialPlanning() {
  const [searchParams, setSearchParams] = useSearchParams();
  const legacyCommitmentTab = searchParams.get("tab") === "contratos-financeiros";
  const legacyStoryTab = searchParams.get("tab") === "contract";
  const requested = legacyCommitmentTab || legacyStoryTab ? "dashboard" : searchParams.get("tab") || "orcamento";
  const active = TABS.find(tab => tab.code === requested) || TABS[0];
  const dashboardSubtab = legacyCommitmentTab || searchParams.get("dashboard") === "visao-financeira-contratos"
    ? "visao-financeira-contratos"
    : legacyStoryTab || searchParams.get("dashboard") === "contrato"
      ? "contrato"
      : "orcamento";

  const changeTab = (_event: React.SyntheticEvent, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", value);
    next.delete("dashboard");
    setSearchParams(next, { replace: true });
  };

  const changeDashboardSubtab = (_event: React.SyntheticEvent, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", "dashboard");
    if (value !== "orcamento") next.set("dashboard", value);
    else next.delete("dashboard");
    setSearchParams(next, { replace: true });
  };

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h4">Planejamento Financeiro</Typography>
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
        ) : active.code === "dashboard" ? (
          <>
            <Tabs value={dashboardSubtab} onChange={changeDashboardSubtab} aria-label="Dashboards executivos" sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}>
              <Tab value="orcamento" label="Orçamento" />
              <Tab value="visao-financeira-contratos" label="Visão financeira dos contratos" />
              <Tab value="contrato" label="Contrato" />
            </Tabs>
            {dashboardSubtab === "visao-financeira-contratos" ? <FinancialContractsDashboard /> : dashboardSubtab === "contrato" ? <ContractStoryDashboard /> : <FinancialDashboard />}
          </>
        ) : (
          <Planning type={active.code} title={active.title} help={active.help} budget={"budget" in active && active.budget} />
        )}
      </Box>
    </Box>
  );
}
