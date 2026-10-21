// Use the factory entry point so Vite bundles the supported, self-contained
// Plotly distribution. The package default imports plotly.js/dist/plotly,
// which is a CommonJS-only dependency and leaks an unresolved module
// specifier into a browser production bundle.
import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-dist-min";
import type PlotComponent from "react-plotly.js";

const PlotlyComponent = createPlotlyComponent(Plotly) as typeof PlotComponent;
const Plot = ({ layout, ...props }: ComponentProps<typeof PlotlyComponent>) => (
  <PlotlyComponent
    {...props}
    layout={{
      ...layout,
      font: {
        ...layout?.font,
        family: '"Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
        color: layout?.font?.color ?? "#617386",
      },
      paper_bgcolor: layout?.paper_bgcolor ?? "transparent",
      plot_bgcolor: layout?.plot_bgcolor ?? "transparent",
    }}
  />
);

export default Plot;
import type { ComponentProps } from "react";
