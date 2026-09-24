import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Box, CircularProgress } from "@mui/material";
import type { ReactElement } from "react";
import Layout from "./layout/Layout";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import ChangePassword from "./auth/ChangePassword";
import Login from "./pages/Login/Login";
import Connections from "./pages/Connections/Connections";
import Rightsizing from "./pages/Rightsizing/Rightsizing";
import SavingActions from "./pages/SavingActions/SavingActions";
import FinOpsCenter from "./pages/FinOpsCenter/FinOpsCenter";
import Explorer from "./pages/Explorer/Explorer";
import Mappings from "./pages/Mappings/Mappings";
import Governance from "./pages/Governance/Governance";
import Departments from "./pages/Departments/Departments";
import Destinations from "./pages/Destinations/Destinations";
import Extraction from "./pages/Extraction/Extraction";
import ExtractionHistory from "./pages/Extraction/ExtractionHistory";
import Analysis from "./pages/Analysis/Analysis";
import PipelineBuilder from "./pages/Pipeline/PipelineBuilder";
import Relationship from "./pages/Relationship/Relationship";
import Reprocess from "./pages/Reprocess/Reprocess";
import Forecast from "./pages/Forecast/Forecast";
import ForecastHistory from "./pages/Forecast/ForecastHistory";
import ForecastAccuracy from "./pages/ForecastAccuracy/ForecastAccuracy";
import SharedCostHistory from "./pages/Reprocess/SharedCostHistory";
import CloneRules from "./pages/CloneRules/CloneRules";
import Tables from "./pages/Tables/Tables";
import DashboardsIA from "./pages/Dashboards/DashboardsIA";
import GitHubCopilotDashboard from "./pages/GitHubCopilotDashboard/GitHubCopilotDashboard";
import FinancialPlanning from "./pages/Planning/FinancialPlanning";
import Savings from "./pages/Savings/Savings";
import AzurePtuOps from "./pages/AzurePTU/AzurePtuOps";
import GoldDashboard from "./pages/GoldDashboard/GoldDashboard";
import Admin from "./pages/Admin/Admin";
import SsoProviders from "./pages/Admin/SsoProviders";
import Audit from "./pages/Admin/Audit";
import Ideas from "./pages/Ideas/Ideas";
function RequireAuth({ children }: { children: ReactElement }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <Box sx={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <CircularProgress />
      </Box>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  if (user.must_change_password) {
    return <ChangePassword onChanged={() => window.location.reload()} />;
  }
  return children;
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Navigate to="/connections" replace />} />
        <Route path="connections" element={<Connections />} />
        <Route path="explorer/:connectionId" element={<Explorer />} />
        <Route path="departments" element={<Departments />} />
        <Route path="financial-planning" element={<FinancialPlanning />} />
        <Route path="departments/financial" element={<Navigate to="/financial-planning?tab=orcamento" replace />} />
        <Route path="budgets" element={<Navigate to="/financial-planning?tab=orcamento" replace />} />
        <Route path="manual-forecast" element={<Navigate to="/financial-planning?tab=forecast-manual" replace />} />
        <Route path="contractual-baseline" element={<Navigate to="/financial-planning?tab=base-line-contratual" replace />} />
        <Route path="credit-letter" element={<Navigate to="/financial-planning?tab=carta-credito" replace />} />
        <Route path="budget-defense" element={<Navigate to="/financial-planning?tab=defesa-orcamentaria" replace />} />
        <Route path="financial-dashboard" element={<Navigate to="/financial-planning?tab=dashboard" replace />} />
        <Route path="tables" element={<Tables />} />
        <Route path="destinations" element={<Navigate to="/tables?tab=destinations" replace />} />
        <Route path="relationship" element={<Navigate to="/tables?tab=relationship" replace />} />
        <Route path="clone-rules" element={<Navigate to="/governance?tab=clone-rules" replace />} />
        <Route path="mappings" element={<Mappings />} />
        <Route path="governance" element={<Governance />} />
        <Route path="rightsizing" element={<Rightsizing />} />
        <Route path="saving-actions" element={<SavingActions />} />
        <Route path="finops-center" element={<FinOpsCenter />} />
        <Route path="rules" element={<Navigate to="/governance?tab=rules" replace />} />
        <Route path="classifications" element={<Navigate to="/governance?tab=classification" replace />} />
        <Route path="extraction" element={<Navigate to="/reprocess?tab=extraction" replace />} />
        <Route path="schedules" element={<Navigate to="/reprocess?tab=schedules" replace />} />
        <Route path="analysis" element={<Analysis />} />
        <Route path="expected-view" element={<Navigate to="/gold-dashboard?tab=visao-executiva" replace />} />
        <Route path="dashboards-ia" element={<DashboardsIA />} />
        <Route path="gold-dashboard" element={<GoldDashboard />} />
        {/* Rota legada: mantém links antigos vivos sem reintroduzir o nome no código. */}
        <Route path="gold-dashboard" element={<Navigate to="/gold-dashboard" replace />} />
        <Route path="visoes-consumo-ia" element={<AzurePtuOps />} />
        <Route path="azure-ptu-ops" element={<Navigate to="/visoes-consumo-ia" replace />} />
        <Route path="reservations" element={<Navigate to="/savings?tab=reservas" replace />} />
        <Route path="github-copilot" element={<GitHubCopilotDashboard />} />
        <Route path="ai-cost-origin" element={<Navigate to="/governance?tab=cost-relationship" replace />} />
        <Route path="pipeline" element={<PipelineBuilder />} />
        <Route path="reprocess" element={<Reprocess />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="forecast-accuracy" element={<ForecastAccuracy />} />
        <Route path="savings" element={<Savings />} />
        <Route path="forecast/history/:runId" element={<ForecastHistory />} />
        <Route path="reprocess/shared-cost/history/:runId" element={<SharedCostHistory />} />
        <Route path="extraction/history/:taskId" element={<ExtractionHistory />} />
        <Route path="admin" element={<Admin />} />
        <Route path="admin/sso" element={<SsoProviders />} />
        <Route path="admin/audit" element={<Audit />} />
        <Route path="ideas" element={<Ideas />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}

export default App;
