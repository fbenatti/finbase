import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "./layout/Layout";
import Connections from "./pages/Connections/Connections";
import Explorer from "./pages/Explorer/Explorer";
import Mappings from "./pages/Mappings/Mappings";
import Rules from "./pages/Rules/Rules";
import Classification from "./pages/Classification/Classification";
import Departments from "./pages/Departments/Departments";
import Destinations from "./pages/Destinations/Destinations";
import Extraction from "./pages/Extraction/Extraction";
import Analysis from "./pages/Analysis/Analysis";
import PipelineBuilder from "./pages/Pipeline/PipelineBuilder";
import Relationship from "./pages/Relationship/Relationship";
import Reprocess from "./pages/Reprocess/Reprocess";
import Forecast from "./pages/Forecast/Forecast";
import ForecastHistory from "./pages/Forecast/ForecastHistory";
import CloneRules from "./pages/CloneRules/CloneRules";
import Dashboards from "./pages/Dashboards/Dashboards";
import ExpectedView from "./pages/Dashboards/ExpectedView";
import FinancialTypes from "./pages/FinancialTypes/FinancialTypes";
import Planning from "./pages/Planning/Planning";
import Savings from "./pages/Savings/Savings";
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
        <Route path="financial-types" element={<FinancialTypes />} />
        <Route path="destinations" element={<Destinations />} />
        <Route path="relationship" element={<Relationship />} />
        <Route path="mappings" element={<Mappings />} />
        <Route path="rules" element={<Rules />} />
        <Route path="clone-rules" element={<CloneRules />} />
        <Route path="classifications" element={<Classification />} />
        <Route path="extraction" element={<Extraction />} />
        <Route path="analysis" element={<Analysis />} />
        <Route path="dashboards" element={<Dashboards />} />
        <Route path="expected-view" element={<ExpectedView />} />
        <Route path="pipeline" element={<PipelineBuilder />} />
        <Route path="reprocess" element={<Reprocess />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="savings" element={<Savings />} />
        <Route path="forecast/history/:runId" element={<ForecastHistory />} />
        <Route path="dashboards" element={<Dashboards />} />
      </Route>
    </Routes>
  );
}

export default App;
