// `base` faz parte da API pública do Plotly para traces de barra (define onde a
// barra começa e exclui o trace do empilhamento do `barmode`), mas está
// ausente em @types/plotly.js.
declare module "plotly.js" {
  interface PlotData {
    base: Datum | Datum[];
  }
}

declare module "plotly.js-dist-min" {
  const Plotly: any;
  export default Plotly;
}

declare module "react-plotly.js/factory" {
  function createPlotlyComponent(plotly: any): any;
  export default createPlotlyComponent;
}
