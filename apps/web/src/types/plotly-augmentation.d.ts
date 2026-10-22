import "plotly.js";

// `base` faz parte da API pública do Plotly para traces de barra (define onde a
// barra começa e exclui o trace do empilhamento do `barmode`), mas está
// ausente em @types/plotly.js.
declare module "plotly.js" {
  interface PlotData {
    base: Datum | Datum[];
    offsetgroup?: string;
  }
}

