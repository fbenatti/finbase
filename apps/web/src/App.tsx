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

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/connections" replace />} />
        <Route path="connections" element={<Connections />} />
        <Route path="explorer/:connectionId" element={<Explorer />} />
        <Route path="departments" element={<Departments />} />
        <Route path="destinations" element={<Destinations />} />
        <Route path="relationship" element={<Relationship />} />
        <Route path="mappings" element={<Mappings />} />
        <Route path="rules" element={<Rules />} />
        <Route path="classifications" element={<Classification />} />
        <Route path="extraction" element={<Extraction />} />
        <Route path="analysis" element={<Analysis />} />
        <Route path="pipeline" element={<PipelineBuilder />} />
        <Route path="reprocess" element={<Reprocess />} />
      </Route>
    </Routes>
  );
}

export default App;
