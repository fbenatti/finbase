import { describe, expect, it } from "vitest";
import type { Data, Layout } from "plotly.js";
import { createAppTheme } from "./theme";
import { chartTokens, standardChartData, standardChartLayout, standardChartConfig } from "./chartTheme";
import { chartSemantic, colors as tokenColors, fontFamily } from "./tokens";
const tokens = chartTokens(createAppTheme());
describe("shared chart theme", () => {
  it("uses the application font tokens for every chart", () => {
    expect(standardChartLayout({}, tokens).font?.family).toBe(fontFamily.body);
  });
  it("preserves financial values, dates, base and chart stacking", () => {
    const data = [{ type: "bar", name: "Realizado", y: [0, -12, 33], x: ["Jan", "Fev", "Mar"], base: [0, 3, 5] }] as Data[];
    const copy = JSON.stringify(data);
    const result: any = standardChartData(data, tokens)[0];
    expect(result.y).toEqual([0, -12, 33]); expect(result.base).toEqual([0, 3, 5]); expect(JSON.stringify(data)).toBe(copy);
    expect(standardChartLayout({ barmode: "relative" }, tokens).barmode).toBe("relative");
  });
  it("uses consistent colors for actual, budget and forecast series", () => {
    const colors = standardChartData(["Realizado", "Orçamento vigente", "Forecast manual"].map(name => ({ type: "bar", name })), tokens).map((trace: any) => trace.marker.color);
    expect(colors).toEqual([tokens.primary, chartSemantic.budget, chartSemantic.forecast]);
  });
  it("preserves category and numeric color arrays instead of assigning a single series color", () => {
    const result: any = standardChartData([{ type: "bar", name: "Realizado", marker: { color: ["#dc2626", "#22c55e"] } }, { type: "scatter", marker: { color: [1, 3, 5] } }], tokens);
    expect(result[0].marker.color).toEqual(["#dc2626", "#22c55e"]); expect(result[1].marker.color).toEqual([1, 3, 5]);
  });
  it("preserves logarithmic axes, ranges and number formats on both y axes", () => {
    const layout: Partial<Layout> = { yaxis: { type: "log", range: [0, 5], tickprefix: "R$ ", tickformat: ".2s" }, yaxis2: { overlaying: "y", side: "right", ticksuffix: "%" } };
    const result = standardChartLayout(layout, tokens);
    expect(result.yaxis).toMatchObject({ type: "log", range: [0, 5], tickprefix: "R$ ", tickformat: ".2s" });
    expect(result.yaxis2).toMatchObject({ overlaying: "y", side: "right", ticksuffix: "%", gridcolor: tokens.border });
  });
  it("preserves custom annotations, legend placement and interactions", () => {
    const layout: Partial<Layout> = { annotations: [{ text: "Limite", x: 8, y: 10 }], legend: { orientation: "v", x: 1.1 } };
    expect(standardChartLayout(layout, tokens).annotations?.[0]).toMatchObject({ text: "Limite", x: 8, y: 10 });
    expect(standardChartLayout(layout, tokens).legend).toMatchObject({ orientation: "v", x: 1.1 });
    expect(standardChartConfig({ scrollZoom: true, displayModeBar: true })).toMatchObject({ scrollZoom: true, displayModeBar: true, responsive: true, displaylogo: false });
  });
  it("keeps sequential heatmap scales ordered and custom diverging stops intact", () => {
    const data: any = standardChartData([{ type: "heatmap", colorscale: "Purples" }, { type: "heatmap", colorscale: [[0, "#ffeeee"], [0.5, "#ffffff"], [1, "#004400"]] }], tokens);
    expect(data[0].colorscale).toEqual([[0, tokenColors.brandTint], [0.3, "#A5B4FC"], [0.65, tokenColors.brandLight], [1, tokenColors.brandDark]]);
    expect(data[1].colorscale).toEqual([[0, "#ffeeee"], [0.5, "#ffffff"], [1, "#004400"]]);
  });
  it("does not mutate the original layout or trace styles", () => {
    const layout: Partial<Layout> = { font: { family: "Arial" }, yaxis: { title: { text: "Custo" } } }; const copy = JSON.stringify(layout);
    standardChartLayout(layout, tokens); expect(JSON.stringify(layout)).toBe(copy);
  });
});
