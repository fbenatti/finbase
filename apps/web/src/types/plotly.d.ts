declare module "plotly.js-dist-min" {
  const Plotly: any;
  export default Plotly;
}

declare module "react-plotly.js/factory" {
  function createPlotlyComponent(plotly: any): any;
  export default createPlotlyComponent;
}
