import { describe, expect, it } from "vitest";
import type { Data, Layout } from "plotly.js";
import { createAppTheme } from "./theme";
import { chartTokens, CHART_COLORS, standardChartData, standardChartLayout, standardChartConfig } from "./chartTheme";
const carbon = chartTokens(createAppTheme("carbon"));
describe("shared chart theme", () => {
  it("adapts typography when the interface layout changes", () => {
    expect(standardChartLayout({}, carbon).font?.family).toContain("IBM Plex Sans");
    expect(standardChartLayout({}, chartTokens(createAppTheme("finbase"))).font?.family).toContain("Inter");
  });
  it("preserves financial values, dates, base and chart stacking", () => {
    const data = [{ type: "bar", name: "Realizado", y: [0, -12, 33], x: ["Jan", "Fev", "Mar"], base: [0, 3, 5] }] as Data[];
    const copy = JSON.stringify(data);
    const result: any = standardChartData(data, carbon)[0];
    expect(result.y).toEqual([0, -12, 33]); expect(result.base).toEqual([0, 3, 5]); expect(JSON.stringify(data)).toBe(copy);
    expect(standardChartLayout({ barmode: "relative" }, carbon).barmode).toBe("relative");
  });
  it("uses consistent colors for actual, budget and forecast series", () => {
    const colors = standardChartData(["Realizado", "Orçamento vigente", "Forecast manual"].map(name => ({ type: "bar", name })), carbon).map((trace: any) => trace.marker.color);
    expect(colors).toEqual([carbon.primary, CHART_COLORS.purple, CHART_COLORS.teal]);
  });
  it("preserves category and numeric color arrays instead of assigning a single series color", () => {
    const result: any = standardChartData([{ type: "bar", name: "Realizado", marker: { color: ["#dc2626", "#22c55e"] } }, { type: "scatter", marker: { color: [1, 3, 5] } }], carbon);
    expect(result[0].marker.color).toEqual([CHART_COLORS.red, CHART_COLORS.green]); expect(result[1].marker.color).toEqual([1, 3, 5]);
  });
  it("preserves logarithmic axes, ranges and number formats on both y axes", () => {
    const layout: Partial<Layout> = { yaxis: { type: "log", range: [0, 5], tickprefix: "R$ ", tickformat: ".2s" }, yaxis2: { overlaying: "y", side: "right", ticksuffix: "%" } };
    const result = standardChartLayout(layout, carbon);
    expect(result.yaxis).toMatchObject({ type: "log", range: [0, 5], tickprefix: "R$ ", tickformat: ".2s" });
    expect(result.yaxis2).toMatchObject({ overlaying: "y", side: "right", ticksuffix: "%", gridcolor: carbon.border });
  });
  it("preserves custom annotations, legend placement and interactions", () => {
    const layout: Partial<Layout> = { annotations: [{ text: "Limite", x: 8, y: 10 }], legend: { orientation: "v", x: 1.1 } };
    expect(standardChartLayout(layout, carbon).annotations?.[0]).toMatchObject({ text: "Limite", x: 8, y: 10 });
    expect(standardChartLayout(layout, carbon).legend).toMatchObject({ orientation: "v", x: 1.1 });
    expect(standardChartConfig({ scrollZoom: true, displayModeBar: true })).toMatchObject({ scrollZoom: true, displayModeBar: true, responsive: true, displaylogo: false });
  });
  it("does not mutate the original layout or trace styles", () => {
    const layout: Partial<Layout> = { font: { family: "Arial" }, yaxis: { title: { text: "Custo" } } }; const copy = JSON.stringify(layout);
    standardChartLayout(layout, carbon); expect(JSON.stringify(layout)).toBe(copy);
  });
});
