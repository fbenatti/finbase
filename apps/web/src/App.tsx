import { Alert, Button } from "@mui/material";
import { NavigationProvider, useNavigation } from "./navigation/NavigationContext";
import { ModulePage, CatalogLegacyRedirect, DefaultModule } from "./navigation/ModulePage";
import { resolveCatalogRoute, widgetSearch } from "./navigation/routes";
import { Routes, Route, Navigate, useLocation, useParams } from "react-router-dom";
import { Box, CircularProgress } from "@mui/material";
import { lazy, Suspense } from "react";
import type { ReactElement } from "react";
import Layout from "./layout/Layout";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { NotificationProvider } from "./context/NotificationContext";
import ChangePassword from "./auth/ChangePassword";
const Modules = lazy(() => import("./pages/Admin/Modules"));
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
const FinancialPlanning = lazy(() => import("./pages/Planning/FinancialPlanning"));
const Savings = lazy(() => import("./pages/Savings/Savings"));
const GoldDashboard = lazy(() => import("./pages/GoldDashboard/GoldDashboard"));
const GoldDashboardV2 = lazy(() => import("./pages/GoldDashboard/GoldDashboardV2"));
const Admin = lazy(() => import("./pages/Admin/Admin"));
const SsoProviders = lazy(() => import("./pages/Admin/SsoProviders"));
const Audit = lazy(() => import("./pages/Admin/Audit"));
const Ideas = lazy(() => import("./pages/Ideas/Ideas"));
const Anomalies = lazy(() => import("./pages/Anomalies/Anomalies"));
const Connectors = lazy(() => import("./pages/Admin/Connectors"));
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
    return <Navigate to="/entrar" replace state={{ from: location }} />;
  }
  if (user.must_change_password) {
    return <ChangePassword onChanged={() => window.location.reload()} />;
  }
  return children;
}

/** Caminhos antigos "/projecoes/acuracia/..." seguem para a mesma aba em "/projecoes/precisao/...". */
function ForecastAccuracyLegacyRedirect() {
  const location = useLocation();
  const rest = location.pathname.slice("/projecoes/acuracia".length).replace(/\/+$/, "") || "/visao";
  return <Navigate to={{ pathname: `/projecoes/precisao${rest}`, search: location.search, hash: location.hash }} replace />;
}

function LegacyRedirect({ to }: { to: string }) {
  const location = useLocation();
  const [pathname, fixedSearch = ""] = to.split("?");
  const params = new URLSearchParams(fixedSearch);
  new URLSearchParams(location.search).forEach((value, key) => {
    if (!params.has(key)) params.append(key, value);
  });
  const { items } = useNavigation();
  const entry = resolveCatalogRoute(items, pathname, params);
  const targetSearch = entry ? widgetSearch(params) : params;
  return <Navigate to={{ pathname: entry?.path || pathname, search: targetSearch.size ? `?${targetSearch}` : "", hash: location.hash }} replace />;
}

function LegacyForecastHistory() {
  const { runId = "" } = useParams();
  return <LegacyRedirect to={`/projecoes/execucoes/${runId}`} />;
}

function LegacySharedCostHistory() {
  const { runId = "" } = useParams();
  return <LegacyRedirect to={`/operacoes/custos-compartilhados/${runId}`} />;
}

function LegacyExtractionHistory() {
  const { taskId = "" } = useParams();
  return <LegacyRedirect to={`/operacoes/extracoes/${taskId}/historico`} />;
}

function LegacyExplorer() {
  const { connectionId = "" } = useParams();
  return <LegacyRedirect to={`/dados/fontes/${connectionId}/explorar`} />;
}

const CATALOG_RENDERERS = { Modules, Connectors, GoldDashboard, Anomalies, FinancialPlanning, ForecastAccuracy, Forecast, Savings, Connections, Tables, PipelineBuilder, Reprocess, Analysis, Governance, Admin, Audit, SsoProviders };

function ApplicationRoutes() {
  const { user } = useAuth();
  const navigation = useNavigation();
  if (user && navigation.loading) return <Box sx={{ p: 6, textAlign: "center" }}><CircularProgress /></Box>;
  if (user && navigation.error) return <Box sx={{ p: 4 }}><Alert severity="error">{navigation.error}</Alert><Button onClick={() => void navigation.reload()}>Tentar novamente</Button></Box>;
  return (
      <Suspense fallback={<Box sx={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}><CircularProgress /></Box>}>
      <Routes>
        <Route path="/entrar" element={<Login />} />
        <Route path="/login" element={<LegacyRedirect to="/entrar" />} />
        <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<DefaultModule />} />
        {navigation.items.map(item => <Route key={item.code} path={item.path.slice(1)} element={<ModulePage item={item} renderers={CATALOG_RENDERERS} />} />)}

        {/* Rotas canônicas finbase.cloud */}
        <Route path="custos" element={<CatalogLegacyRedirect />} />
        <Route path="custos/avancado" element={<GoldDashboardV2 />} />
        <Route path="alertas" element={<CatalogLegacyRedirect />} />
        <Route path="alertas/visao-geral" element={<LegacyRedirect to="/alertas/painel" />} />
        <Route path="planejamento" element={<CatalogLegacyRedirect />} />
        <Route path="projecoes" element={<CatalogLegacyRedirect />} />
        <Route path="projecoes/acuracia/*" element={<ForecastAccuracyLegacyRedirect />} />
        <Route path="planejamento/acuracia-do-orcamento" element={<LegacyRedirect to="/planejamento/aderencia-orcamento" />} />
        <Route path="operacoes/acuracia" element={<LegacyRedirect to="/operacoes/precisao" />} />
        <Route path="projecoes/execucoes/:runId" element={<ForecastHistory />} />
        <Route path="otimizacao" element={<CatalogLegacyRedirect />} />
        <Route path="dados/fontes/:connectionId/explorar" element={<Explorer />} />
        <Route path="dados/mapeamentos" element={<Mappings />} />
        <Route path="dados/catalogo" element={<CatalogLegacyRedirect />} />
        <Route path="dados/explorador" element={<CatalogLegacyRedirect />} />
        <Route path="operacoes/execucoes" element={<CatalogLegacyRedirect />} />
        <Route path="operacoes/custos-compartilhados/:runId" element={<SharedCostHistory />} />
        <Route path="operacoes/extracoes/:taskId/historico" element={<ExtractionHistory />} />
        <Route path="governanca" element={<CatalogLegacyRedirect />} />
        <Route path="administracao/acessos" element={<CatalogLegacyRedirect />} />
        <Route path="iniciativas" element={<Ideas />} />

        {/* Aliases antigos: preservam favoritos e links existentes. */}
        <Route path="connections" element={<LegacyRedirect to="/dados/fontes" />} />
        <Route path="explorer/:connectionId" element={<LegacyExplorer />} />
        <Route path="mappings" element={<LegacyRedirect to="/dados/mapeamentos" />} />
        <Route path="tables" element={<LegacyRedirect to="/dados/catalogo" />} />
        <Route path="analysis" element={<LegacyRedirect to="/dados/explorador" />} />
        <Route path="pipeline" element={<LegacyRedirect to="/dados/fluxos" />} />
        <Route path="reprocess" element={<LegacyRedirect to="/operacoes/execucoes" />} />
        <Route path="reprocess/shared-cost/history/:runId" element={<LegacySharedCostHistory />} />
        <Route path="extraction/history/:taskId" element={<LegacyExtractionHistory />} />
        <Route path="governance" element={<LegacyRedirect to="/governanca" />} />
        <Route path="admin" element={<LegacyRedirect to="/administracao/acessos" />} />
        <Route path="admin/sso" element={<LegacyRedirect to="/administracao/identidade" />} />
        <Route path="admin/audit" element={<LegacyRedirect to="/administracao/auditoria" />} />
        <Route path="ideas" element={<LegacyRedirect to="/iniciativas" />} />
        <Route path="anomalies" element={<LegacyRedirect to="/alertas" />} />
        <Route path="gold-dashboard" element={<LegacyRedirect to="/custos" />} />
        <Route path="gold-dashboard-v2" element={<LegacyRedirect to="/custos/avancado" />} />
        <Route path="financial-planning" element={<LegacyRedirect to="/planejamento" />} />
        <Route path="forecast" element={<LegacyRedirect to="/projecoes" />} />
        <Route path="forecast-accuracy" element={<LegacyRedirect to="/projecoes/precisao/visao" />} />
        <Route path="forecast/history/:runId" element={<LegacyForecastHistory />} />
        <Route path="savings" element={<LegacyRedirect to="/otimizacao" />} />
        <Route path="departments" element={<LegacyRedirect to="/governanca?tab=organization" />} />
        <Route path="departments/financial" element={<LegacyRedirect to="/planejamento?tab=orcamento" />} />
        <Route path="budgets" element={<LegacyRedirect to="/planejamento?tab=orcamento" />} />
        <Route path="manual-forecast" element={<LegacyRedirect to="/planejamento?tab=forecast-manual" />} />
        <Route path="contractual-baseline" element={<LegacyRedirect to="/planejamento?tab=base-line-contratual" />} />
        <Route path="credit-letter" element={<LegacyRedirect to="/planejamento?tab=carta-credito" />} />
        <Route path="budget-defense" element={<LegacyRedirect to="/planejamento?tab=defesa-orcamentaria" />} />
        <Route path="financial-dashboard" element={<LegacyRedirect to="/planejamento?tab=dashboard" />} />
        <Route path="destinations" element={<LegacyRedirect to="/dados/catalogo?tab=destinations" />} />
        <Route path="relationship" element={<LegacyRedirect to="/dados/catalogo?tab=relationship" />} />
        <Route path="clone-rules" element={<LegacyRedirect to="/governanca?tab=rules&subtab=clone-rules" />} />
        <Route path="financial-contracts" element={<LegacyRedirect to="/governanca?tab=financial-contracts" />} />
        <Route path="financial-contracts-dashboard" element={<LegacyRedirect to="/governanca?tab=financial-contracts" />} />
        <Route path="rules" element={<LegacyRedirect to="/governanca?tab=rules" />} />
        <Route path="classifications" element={<LegacyRedirect to="/governanca?tab=classification" />} />
        <Route path="extraction" element={<LegacyRedirect to="/operacoes/execucoes?tab=extraction" />} />
        <Route path="schedules" element={<LegacyRedirect to="/operacoes/execucoes?tab=schedules" />} />
        <Route path="expected-view" element={<LegacyRedirect to="/custos?tab=visao-executiva" />} />
        <Route path="reservations" element={<LegacyRedirect to="/governanca?tab=reservas" />} />
        <Route path="*" element={<DefaultModule />} />
        </Route>
      </Routes>
      </Suspense>
  );
}

function App() {
  return <AuthProvider><NotificationProvider><NavigationProvider><ApplicationRoutes /></NavigationProvider></NotificationProvider></AuthProvider>;
}

export default App;
