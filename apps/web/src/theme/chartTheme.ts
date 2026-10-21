import type { Theme } from "@mui/material/styles";
import type { Data, Layout, Config } from "plotly.js";

export const CHART_COLORS = {
  blue: "#0f62fe", teal: "#009d9a", purple: "#6929c4", pink: "#ee538b",
  cyan: "#1192e8", red: "#da1e28", green: "#198038", yellow: "#b28600",
};
export const CHART_PALETTE = [CHART_COLORS.blue, CHART_COLORS.teal, CHART_COLORS.purple, CHART_COLORS.pink, CHART_COLORS.cyan, "#005d5d", "#9f1853", "#002d9c"];
export interface ChartTokens { family: string; text: string; muted: string; border: string; paper: string; primary: string }
export const chartTokens = (theme: Theme): ChartTokens => ({ family: theme.typography.fontFamily!, text: theme.palette.text.primary, muted: theme.palette.text.secondary, border: theme.palette.divider, paper: theme.palette.background.paper, primary: theme.palette.primary.main });
const normalized = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const legacyColors: Record<string, string> = {
  "#2563eb": CHART_COLORS.blue, "#3b82f6": CHART_COLORS.blue, "#306ce5": CHART_COLORS.blue, "#0078d4": CHART_COLORS.blue,
  "#22c55e": CHART_COLORS.green, "#2e7d32": CHART_COLORS.green, "#059669": CHART_COLORS.green, "#10b981": CHART_COLORS.green,
  "#dc2626": CHART_COLORS.red, "#ef4444": CHART_COLORS.red, "#d32f2f": CHART_COLORS.red, "#f44336": CHART_COLORS.red,
  "#f2a900": CHART_COLORS.yellow, "#f59e0b": CHART_COLORS.yellow, "#d97706": CHART_COLORS.yellow, "#ff9800": CHART_COLORS.yellow,
  "#7c3aed": CHART_COLORS.purple, "#5b00cb": CHART_COLORS.purple, "#8b5cf6": CHART_COLORS.purple, "#660099": CHART_COLORS.purple,
  "#0f766e": CHART_COLORS.teal, "#14b8a6": CHART_COLORS.teal, "#168f83": CHART_COLORS.teal,
};
const exactColors = new Set(Object.values(CHART_COLORS).concat(CHART_PALETTE));
function paletteColor(key: string) {
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return CHART_PALETTE[hash % CHART_PALETTE.length];
}
export function chartColor(value: string, tokens: ChartTokens): string {
  const lower = value.toLowerCase();
  if (["transparent", "white", "black", "#fff", "#ffffff", "#000", "#000000"].includes(lower)) return value;
  if (["#64748b", "#617386", "#6b7280", "#94a3b8", "#9ca3af"].includes(lower)) return tokens.muted;
  if (lower === "red") return CHART_COLORS.red;
  if (lower === "green") return CHART_COLORS.green;
  const rgba = lower.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([.\d]+))?\s*\)$/);
  if (rgba) {
    const hex = "#" + rgba.slice(1, 4).map(n => Number(n).toString(16).padStart(2, "0")).join("");
    const mapped = chartColor(hex, tokens);
    if (!/^#[0-9a-f]{6}$/i.test(mapped)) return value;
    return `rgba(${parseInt(mapped.slice(1, 3), 16)},${parseInt(mapped.slice(3, 5), 16)},${parseInt(mapped.slice(5, 7), 16)},${rgba[4] ?? 1})`;
  }
  if (legacyColors[lower]) return legacyColors[lower];
  if (exactColors.has(lower) || lower === tokens.primary.toLowerCase()) return lower;
  return /^#[0-9a-f]{6}$/i.test(value) ? paletteColor(lower) : value;
}
function seriesColor(name: unknown, tokens: ChartTokens) {
  const label = normalized(String(name || ""));
  if (/^(realizado|consumo|actual|observado)\b/.test(label)) return tokens.primary;
  if (/^(orcamento|meta|budget)\b/.test(label)) return CHART_COLORS.purple;
  if (/^(forecast|projetado|projecao|previsto|previsao)\b/.test(label)) return CHART_COLORS.teal;
  if (/^(erro|falha|fora do orcamento|acima do orcamento)\b/.test(label)) return CHART_COLORS.red;
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
    if (typeof trace.colorscale === "string" && /^(Purples|Blues|Viridis|Plasma|Rainbow)$/i.test(trace.colorscale)) trace.colorscale = [[0, "#f4f4f4"], [0.3, "#a6c8ff"], [0.65, "#4589ff"], [1, "#0043ce"]];
    // Explicit continuous/diverging scales carry quantitative meaning; keep their lightness and stops.
    if (Array.isArray((source as any).colorscale)) trace.colorscale = (source as any).colorscale.map(([stop, color]: [number, string]) => [stop, legacyColors[color.toLowerCase()] || color]);
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
