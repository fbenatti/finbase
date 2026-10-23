import { describe, expect, it } from "vitest";
import { chartTypeFor, layoutWidgets, visualizationFor, widgetPixelHeight } from "./dashboardLayout";

describe("dashboard layout", () => {
  it("orders widgets by saved order and fills missing or invalid sizes", () => {
    const widgets = layoutWidgets([
      { id: 1, position: { order: 2, width: 12, height: 4 } },
      { id: 2, position: {} },
      { id: 3, position: { order: 0, width: 5, height: 9 } },
    ]);
    expect(widgets.map(widget => widget.id)).toEqual([3, 1, 2]);
    expect(widgets.map(widget => widget.position.order)).toEqual([0, 1, 2]);
    expect(widgets[0].position).toMatchObject({ width: 6, height: 3 });
    expect(widgets[1].position).toMatchObject({ width: 12, height: 4 });
  });

  it("maps pivot visualizations to dashboard chart types and back", () => {
    expect(chartTypeFor("indicator")).toBe("kpi");
    expect(visualizationFor("kpi")).toBe("indicator");
    expect(visualizationFor("table")).toBe("table");
    expect(visualizationFor("stacked_bar")).toBe("bar");
    expect(visualizationFor("heatmap")).toBe("bar");
  });

  it("includes the gaps a widget spans", () => {
    expect(widgetPixelHeight(1)).toBe(112);
    expect(widgetPixelHeight(3)).toBe(3 * 112 + 2 * 16);
  });
});
