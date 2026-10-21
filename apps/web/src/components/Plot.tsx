// Bundle the supported, self-contained Plotly distribution through its factory.
import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-dist-min";
import type PlotComponent from "react-plotly.js";
import { useMemo, type ComponentProps } from "react";
import { useInterfaceTheme } from "../theme/useInterfaceTheme";
import { chartTokens, standardChartData, standardChartLayout, standardChartConfig } from "../theme/chartTheme";
const PlotlyComponent = createPlotlyComponent(Plotly) as typeof PlotComponent;
export default function Plot({ layout, data, config, style, ...props }: ComponentProps<typeof PlotlyComponent>) {
  const theme = useInterfaceTheme();
  const tokens = useMemo(() => chartTokens(theme), [theme]);
  const normalizedData = useMemo(() => standardChartData(data, tokens), [data, tokens]);
  const normalizedLayout = useMemo(() => standardChartLayout(layout, tokens), [layout, tokens]);
  const normalizedConfig = useMemo(() => standardChartConfig(config), [config]);
  return <PlotlyComponent useResizeHandler {...props} data={normalizedData} layout={normalizedLayout} config={normalizedConfig} style={{ width: "100%", ...style }} />;
}
