import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Box, CircularProgress } from "@mui/material";
import { lazy, Suspense } from "react";
import type { ReactElement } from "react";
import Layout from "./layout/Layout";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import ChangePassword from "./auth/ChangePassword";
const Login = lazy(() => import("./pages/Login/Login"));
const Connections = lazy(() => import("./pages/Connections/Connections"));
const Explorer = lazy(() => import("./pages/Explorer/Explorer"));
const Mappings = lazy(() => import("./pages/Mappings/Mappings"));
const Governance = lazy(() => import("./pages/Governance/Governance"));
const Destinations = lazy(() => import("./pages/Destinations/Destinations"));
const Extraction = lazy(() => import("./pages/Extraction/Extraction"));
const ExtractionHistory = lazy(() => import("./pages/Extraction/ExtractionHistory"));
const Analysis = lazy(() => import("./pages/Analysis/Analysis"));
const PipelineBuilder = lazy(() => import("./pages/Pipeline/PipelineBuilder"));
const Relationship = lazy(() => import("./pages/Relationship/Relationship"));
const Reprocess = lazy(() => import("./pages/Reprocess/Reprocess"));
const Forecast = lazy(() => import("./pages/Forecast/Forecast"));
const ForecastHistory = lazy(() => import("./pages/Forecast/ForecastHistory"));
const ForecastAccuracy = lazy(() => import("./pages/ForecastAccuracy/ForecastAccuracy"));
const SharedCostHistory = lazy(() => import("./pages/Reprocess/SharedCostHistory"));
const CloneRules = lazy(() => import("./pages/CloneRules/CloneRules"));
const Tables = lazy(() => import("./pages/Tables/Tables"));
const DashboardsIA = lazy(() => import("./pages/Dashboards/DashboardsIA"));
const GitHubCopilotDashboard = lazy(() => import("./pages/GitHubCopilotDashboard/GitHubCopilotDashboard"));
const FinancialPlanning = lazy(() => import("./pages/Planning/FinancialPlanning"));
const Savings = lazy(() => import("./pages/Savings/Savings"));
const AzurePtuOps = lazy(() => import("./pages/AzurePTU/AzurePtuOps"));
const GoldDashboard = lazy(() => import("./pages/GoldDashboard/GoldDashboard"));
const GoldDashboardV2 = lazy(() => import("./pages/GoldDashboard/GoldDashboardV2"));
const Admin = lazy(() => import("./pages/Admin/Admin"));
const SsoProviders = lazy(() => import("./pages/Admin/SsoProviders"));
const Audit = lazy(() => import("./pages/Admin/Audit"));
const Ideas = lazy(() => import("./pages/Ideas/Ideas"));
const Anomalies = lazy(() => import("./pages/Anomalies/Anomalies"));
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
      <Suspense fallback={<Box sx={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}><CircularProgress /></Box>}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Navigate to="/gold-dashboard" replace />} />
        <Route path="connections" element={<Connections />} />
        <Route path="explorer/:connectionId" element={<Explorer />} />
        <Route path="departments" element={<Navigate to="/governance?tab=organization" replace />} />
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
        <Route path="clone-rules" element={<Navigate to="/governance?tab=rules&subtab=clone-rules" replace />} />
        <Route path="mappings" element={<Mappings />} />
        <Route path="governance" element={<Governance />} />
        <Route path="financial-contracts" element={<Navigate to="/financial-planning?tab=contratos-financeiros" replace />} />
        <Route path="financial-contracts-dashboard" element={<Navigate to="/financial-planning?tab=contratos-financeiros" replace />} />
        <Route path="rules" element={<Navigate to="/governance?tab=rules" replace />} />
        <Route path="classifications" element={<Navigate to="/governance?tab=classification" replace />} />
        <Route path="extraction" element={<Navigate to="/reprocess?tab=extraction" replace />} />
        <Route path="schedules" element={<Navigate to="/reprocess?tab=schedules" replace />} />
        <Route path="analysis" element={<Analysis />} />
        <Route path="expected-view" element={<Navigate to="/gold-dashboard?tab=visao-executiva" replace />} />
        <Route path="dashboards-ia" element={<DashboardsIA />} />
        <Route path="gold-dashboard" element={<GoldDashboard />} />
        <Route path="gold-dashboard-v2" element={<GoldDashboardV2 />} />
        {/* Rota legada: mantém links antigos vivos sem reintroduzir o nome no código. */}
        <Route path="gold-dashboard" element={<Navigate to="/gold-dashboard" replace />} />
        <Route path="visoes-consumo-ia" element={<AzurePtuOps />} />
        <Route path="azure-ptu-ops" element={<Navigate to="/visoes-consumo-ia" replace />} />
        <Route path="reservations" element={<Navigate to="/governance?tab=reservas" replace />} />
        <Route path="github-copilot" element={<GitHubCopilotDashboard />} />
        <Route path="ai-cost-origin" element={<Navigate to="/governance?tab=cost-relationship" replace />} />
        <Route path="pipeline" element={<PipelineBuilder />} />
        <Route path="reprocess" element={<Reprocess />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="forecast-accuracy" element={<ForecastAccuracy />} />
        <Route path="anomalies" element={<Anomalies />} />
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
      </Suspense>
    </AuthProvider>
  );
}

export default App;
