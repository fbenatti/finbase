// Use the factory entry point so Vite bundles the supported, self-contained
// Plotly distribution. The package default imports plotly.js/dist/plotly,
// which is a CommonJS-only dependency and leaks an unresolved module
// specifier into a browser production bundle.
import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-dist-min";
import type PlotComponent from "react-plotly.js";

const Plot = createPlotlyComponent(Plotly) as typeof PlotComponent;

export default Plot;
