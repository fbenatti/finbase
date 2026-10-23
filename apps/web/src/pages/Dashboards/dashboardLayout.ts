import type { DashboardChartType, DashboardWidget, DashboardWidgetPosition } from "../../services/api";
import type { PivotVisualization } from "../../components/PivotChart";

export const GRID_COLUMNS = 12;
export const ROW_HEIGHT = 112;
export const GRID_GAP = 16;

export const WIDTH_OPTIONS = [
  { value: 4, label: "1/3 da largura" },
  { value: 6, label: "Metade" },
  { value: 8, label: "2/3 da largura" },
  { value: 12, label: "Largura inteira" },
];
export const HEIGHT_OPTIONS = [
  { value: 2, label: "Baixo" },
  { value: 3, label: "Médio" },
  { value: 4, label: "Alto" },
];
const DEFAULT_POSITION = { width: 6, height: 3 };

// The pivot saves a visualization; the dashboard API stores a chart type.
const TO_CHART: Record<PivotVisualization, DashboardChartType> = { table: "table", bar: "bar", line: "line", pie: "pie", indicator: "kpi" };
const FROM_CHART: Record<string, PivotVisualization> = { table: "table", bar: "bar", stacked_bar: "bar", line: "line", area: "line", pie: "pie", donut: "pie", kpi: "indicator" };

export const chartTypeFor = (visualization: PivotVisualization): DashboardChartType => TO_CHART[visualization];
export const visualizationFor = (chartType: string): PivotVisualization => FROM_CHART[chartType] || "bar";

const clamp = (value: unknown, allowed: number[], fallback: number) =>
  typeof value === "number" && allowed.includes(value) ? value : fallback;

/** Widgets in display order with a complete, valid position. */
export function layoutWidgets<T extends Pick<DashboardWidget, "id" | "position">>(widgets: T[]): Array<T & { position: DashboardWidgetPosition }> {
  return widgets
    .map((widget, index) => ({ widget, index, order: typeof widget.position?.order === "number" ? widget.position.order : Number.MAX_SAFE_INTEGER }))
    .sort((a, b) => a.order - b.order || a.index - b.index)
    .map(({ widget }, order) => ({
      ...widget,
      position: {
        order,
        width: clamp(widget.position?.width, WIDTH_OPTIONS.map(item => item.value), DEFAULT_POSITION.width),
        height: clamp(widget.position?.height, HEIGHT_OPTIONS.map(item => item.value), DEFAULT_POSITION.height),
      },
    }));
}

/** Pixel height available to a widget's content for a given number of grid rows. */
export const widgetPixelHeight = (rows: number) => rows * ROW_HEIGHT + (rows - 1) * GRID_GAP;

export const newWidgetPosition = (order: number): DashboardWidgetPosition => ({ order, ...DEFAULT_POSITION });
