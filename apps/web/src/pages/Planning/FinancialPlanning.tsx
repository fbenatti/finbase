import { Box, Tab, Tabs, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import FinancialDashboard from "./FinancialDashboard";
import BudgetAccuracy from "./BudgetAccuracy";
import Planning from "./Planning";
import VisaoContabil, { EMPTY_FILTERS } from "../GoldDashboard/VisaoContabil";
import FinancialContractsDashboard from "../FinancialContracts/FinancialContractsDashboard";

const TABS = [
  { code: "orcamento", label: "Orçamento", title: "Orçamento", help: "Valores aprovados e carregados por departamento.", budget: true },
  { code: "acuracia-orcamento", label: "Acurácia de Orçamento", title: "Acurácia de Orçamento", help: "Orçamento vigente contra o realizado por mês." },
  { code: "forecast-manual", label: "Forecast Manual", title: "Forecast Manual", help: "Projeções mensais inseridas manualmente." },
  // Temporariamente oculto na interface; implementação e rota permanecem disponíveis.
  // { code: "base-line-contratual", label: "Base Line Contratual", title: "Base Line Contratual", help: "Referência financeira contratual por departamento." },
  { code: "carta-credito", label: "Carta de Credito", title: "Carta de Credito", help: "Créditos planejados por departamento." },
  { code: "defesa-orcamentaria", label: "Defesa Orçamentária", title: "Defesa Orçamentária", help: "Propostas, aprovações, cortes e revisões narradas." },
  { code: "visao-contabil", label: "Visão Contábil", title: "Visão Contábil", help: "Conciliação entre realizado e provisão contábil." },
  { code: "contratos-financeiros", label: "Contratos Financeiros", title: "Contratos Financeiros", help: "Orçamento, consumo, realizado, créditos e overage por contrato." },
  { code: "dashboard", label: "Dashboard Executivo", title: "Dashboard Executivo", help: "Histórico consolidado do planejamento financeiro." },
] as const;

export default function FinancialPlanning() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab") || "orcamento";
  const active = TABS.find(tab => tab.code === requested) || TABS[0];

  return (
    <Box sx={{ mx: -3, mt: -3 }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h4">Planejamento Financeiro</Typography>
        <Typography color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
          Cadastros, movimentações e histórico executivo do planejamento financeiro.
        </Typography>
        <Tabs
          value={active.code}
          onChange={(_event, value: string) => setSearchParams({ tab: value }, { replace: true })}
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
          <FinancialDashboard />
        ) : active.code === "contratos-financeiros" ? (
          <FinancialContractsDashboard />
        ) : (
          <Planning type={active.code} title={active.title} help={active.help} budget={"budget" in active && active.budget} />
        )}
      </Box>
    </Box>
  );
}
