import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "./layout/Layout";
import Connections from "./pages/Connections/Connections";
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
import ExpectedView from "./pages/Dashboards/ExpectedView";
import DashboardsIA from "./pages/Dashboards/DashboardsIA";
import GitHubCopilotDashboard from "./pages/GitHubCopilotDashboard/GitHubCopilotDashboard";
import Planning from "./pages/Planning/Planning";
import Savings from "./pages/Savings/Savings";
import AzurePTU from "./pages/AzurePTU/AzurePTU";
import AzurePtuOps from "./pages/AzurePTU/AzurePtuOps";
function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/connections" replace />} />
        <Route path="connections" element={<Connections />} />
        <Route path="explorer/:connectionId" element={<Explorer />} />
        <Route path="departments" element={<Departments />} />
        <Route path="departments/financial" element={<Planning />} />
        <Route path="budgets" element={<Planning budgetMode />} />
        <Route path="tables" element={<Tables />} />
        <Route path="destinations" element={<Navigate to="/tables?tab=destinations" replace />} />
        <Route path="relationship" element={<Navigate to="/tables?tab=relationship" replace />} />
        <Route path="clone-rules" element={<Navigate to="/tables?tab=clone-rules" replace />} />
        <Route path="mappings" element={<Mappings />} />
        <Route path="governance" element={<Governance />} />
        <Route path="rules" element={<Navigate to="/governance?tab=rules" replace />} />
        <Route path="classifications" element={<Navigate to="/governance?tab=classification" replace />} />
        <Route path="extraction" element={<Navigate to="/reprocess?tab=extraction" replace />} />
        <Route path="analysis" element={<Analysis />} />
        <Route path="expected-view" element={<ExpectedView />} />
        <Route path="dashboards-ia" element={<DashboardsIA />} />
        <Route path="azure-ptu" element={<AzurePTU />} />
        <Route path="azure-ptu-ops" element={<AzurePtuOps />} />
        <Route path="github-copilot" element={<GitHubCopilotDashboard />} />
        <Route path="ai-cost-origin" element={<Navigate to="/tables?tab=ai-cost-relationship" replace />} />
        <Route path="pipeline" element={<PipelineBuilder />} />
        <Route path="reprocess" element={<Reprocess />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="forecast-accuracy" element={<ForecastAccuracy />} />
        <Route path="savings" element={<Savings />} />
        <Route path="forecast/history/:runId" element={<ForecastHistory />} />
        <Route path="reprocess/shared-cost/history/:runId" element={<SharedCostHistory />} />
        <Route path="extraction/history/:taskId" element={<ExtractionHistory />} />
      </Route>
    </Routes>
  );
}

export default App;
