import type { Theme } from "@mui/material/styles";
import type { Data, Layout, Config } from "plotly.js";
import { chartSemantic, chartSeries, colors } from "./tokens";

export const CHART_PALETTE = [...chartSeries];
export interface ChartTokens { family: string; text: string; muted: string; border: string; paper: string; primary: string }
export const chartTokens = (theme: Theme): ChartTokens => ({ family: theme.typography.fontFamily!, text: theme.palette.text.primary, muted: theme.palette.text.secondary, border: theme.palette.divider, paper: theme.palette.background.paper, primary: theme.palette.primary.main });
const normalized = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const SEQUENTIAL_SCALE = [[0, colors.brandTint], [0.3, "#A5B4FC"], [0.65, colors.brandLight], [1, colors.brandDark]];
export function chartColor(value: string, tokens: ChartTokens): string {
  const lower = value.toLowerCase();
  if (["#5A6378", "#5A6378", "#6b7280", "#8A93A8", "#9ca3af", colors.textMuted.toLowerCase()].includes(lower)) return tokens.muted;
  if (lower === "red") return chartSemantic.negative;
  if (lower === "green") return chartSemantic.positive;
  return value;
}
function seriesColor(name: unknown, tokens: ChartTokens) {
  const label = normalized(String(name || ""));
  if (/^(realizado|consumo|actual|observado)\b/.test(label)) return tokens.primary;
  if (/^(orcamento|meta|budget)\b/.test(label)) return chartSemantic.budget;
  if (/^(forecast|projetado|projecao|previsto|previsao)\b/.test(label)) return chartSemantic.forecast;
  if (/^(erro|falha|fora do orcamento|acima do orcamento)\b/.test(label)) return chartSemantic.negative;
  return undefined;
}
const styleColors = (style: any, tokens: ChartTokens): any => {
  if (!style || typeof style !== "object" || Array.isArray(style)) return style;
  return Object.fromEntries(Object.entries(style).map(([key, value]) => {
    if (["color", "fillcolor", "bgcolor", "bordercolor"].includes(key) && typeof value === "string") return [key, chartColor(value, tokens)];
    if (["color", "colors"].includes(key) && Array.isArray(value)) return [key, value.map(v => typeof v === "string" ? chartColor(v, tokens) : v)];
    if (key === "font" || key.endsWith("font")) return [key, { ...(value as object), family: tokens.family }];
    return [key, styleColors(value, tokens)];
  }));
};
export function standardChartData(data: Data[], tokens: ChartTokens): Data[] {
  return data.map(source => {
    const trace: any = { ...source };
    for (const key of ["marker", "line", "error_x", "error_y", "colorbar", "insidetextfont", "outsidetextfont", "textfont"]) if (trace[key]) trace[key] = styleColors(trace[key], tokens);
    for (const key of ["fillcolor", "decreasing", "increasing", "totals"]) if (typeof trace[key] === "string") trace[key] = chartColor(trace[key], tokens); else if (trace[key]) trace[key] = styleColors(trace[key], tokens);
    const color = seriesColor(trace.name, tokens);
    const categoryColors = Array.isArray(trace.marker?.color) || Array.isArray(trace.marker?.colors);
    if (color && !categoryColors && [undefined, "bar", "scatter", "scattergl", "histogram", "box", "violin"].includes(trace.type)) {
      trace.marker = { ...trace.marker, color };
      if ([undefined, "scatter", "scattergl"].includes(trace.type)) trace.line = { width: 2.5, ...trace.line, color };
    }
    if (typeof trace.colorscale === "string" && /^(Purples|Blues|Viridis|Plasma|Rainbow)$/i.test(trace.colorscale)) trace.colorscale = SEQUENTIAL_SCALE;
    // Explicit continuous/diverging scales carry quantitative meaning; keep their lightness and stops.
    if (Array.isArray((source as any).colorscale)) trace.colorscale = (source as any).colorscale;
    for (const key of ["textfont", "insidetextfont", "outsidetextfont"]) if (trace[key]) trace[key] = { ...trace[key], family: tokens.family };
    if (trace.colorbar) trace.colorbar = { ...trace.colorbar, tickfont: { ...trace.colorbar.tickfont, family: tokens.family, size: 11, color: tokens.muted } };
    return trace;
  });
}
const styledTitle = (title: any, tokens: ChartTokens) => title ? { ...(typeof title === "string" ? { text: title } : title), font: { ...(typeof title === "object" ? title.font : {}), family: tokens.family, color: tokens.text, size: 14 } } : title;
export function standardChartLayout(layout: Partial<Layout> = {}, tokens: ChartTokens): Partial<Layout> {
  const result: any = {
    autosize: true, margin: { l: 64, r: 24, t: 32, b: 72 },
    ...layout, colorway: [tokens.primary, ...CHART_PALETTE.slice(1)],
    separators: ",.", paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    font: { ...layout.font, family: tokens.family, color: tokens.muted, size: 12 },
    title: styledTitle(layout.title, tokens),
    hoverlabel: { ...layout.hoverlabel, bgcolor: tokens.paper, bordercolor: tokens.border, font: { family: tokens.family, size: 12, color: tokens.text } },
    legend: { orientation: "h", x: 0, xanchor: "left", y: -0.22, yanchor: "top", ...layout.legend, bgcolor: "transparent", borderwidth: 0, font: { ...layout.legend?.font, family: tokens.family, color: tokens.muted, size: 12 } },
  };
  for (const key of new Set(["xaxis", "yaxis", ...Object.keys(layout).filter(k => /^[xy]axis\d*$/.test(k))])) {
    const axis: any = (layout as any)[key] || {};
    result[key] = { automargin: true, zerolinewidth: 1, ...axis, title: styledTitle(axis.title, tokens),
      gridcolor: tokens.border, gridwidth: 1, linecolor: tokens.border, zerolinecolor: tokens.border,
      tickfont: { ...axis.tickfont, family: tokens.family, size: 11, color: tokens.muted },
    };
  }
  if (layout.annotations) result.annotations = layout.annotations.map(annotation => ({ ...annotation, font: { ...annotation.font, family: tokens.family } }));
  if (layout.margin) result.margin = { l: 64, r: 24, t: 32, b: 72, ...layout.margin };
  return result;
}
export function standardChartConfig(config: Partial<Config> = {}): Partial<Config> {
  return { displayModeBar: false, scrollZoom: false, ...config, displaylogo: false, responsive: true };
}
