import { ReactNode, useMemo } from "react";
import { Box, Paper, Stack, Typography } from "@mui/material";
import Plot from "./Plot";
import type { PivotResult } from "../services/api";
import { fieldLabel, friendlyMeasureLabel } from "../utils/pivotLabels";

// Saved with the pivot config (``config.visualization``) so a saved report can
// later be rendered as a dashboard widget from the same PivotResult.
export type PivotVisualization = "table" | "bar" | "line" | "pie" | "indicator";

interface Props {
  result: PivotResult;
  visualization: PivotVisualization;
  height?: number;
  onCellClick?: (row: unknown[], column: unknown[]) => void;
  /** Inside a dashboard widget: no card, no caption, tighter margins. */
  embedded?: boolean;
}

const money = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
const key = (values: unknown[]) => JSON.stringify(values);
const PIE_SLICES = 10;
const TICK_CHARS = 22;
const shortTick = (label: string) => label.length > TICK_CHARS ? `${label.slice(0, TICK_CHARS - 1)}…` : label;

function valueLabel(name: string | undefined, value: unknown) {
  if (value == null || value === "") return "(vazio)";
  const lower = (name || "").toLowerCase();
  if (["_billing_period", "period", "forecast_month"].includes(lower)) {
    const digits = String(value).replace(/\D/g, "");
    if (digits.length >= 6) return `${digits.slice(4, 6)}/${digits.slice(0, 4)}`;
  }
  return String(value);
}
function Frame({ embedded, padding, children }: { embedded: boolean; padding: number; children: ReactNode }) {
  return embedded
    ? <Box sx={{ height: "100%" }}>{children}</Box>
    : <Paper variant="outlined" sx={{ p: padding, mb: 2 }}>{children}</Paper>;
}
const keyLabel = (values: unknown[], names: string[]) => values.map((v, i) => valueLabel(names[i], v)).join(" › ");

export default function PivotChart({ result, visualization, height = 380, onCellClick, embedded = false }: Props) {
  const margin = embedded ? { l: 48, r: 12, t: 8, b: 40 } : undefined;
  const measure = result.measures[0];
  const measureName = measure ? friendlyMeasureLabel(measure) : "Valor";
  const rowKeys = result.row_keys || [];
  const colKeys = result.column_keys?.length ? result.column_keys : [[]];
  const cells = useMemo(() => new Map((result.cells || []).map(cell => [`${key(cell.row)}|${key(cell.column)}`, cell.values])), [result]);
  const rowTotal = (row: unknown[]) => {
    const total = result.totals?.rows[key(row)]?.[0];
    if (typeof total === "number") return total;
    return colKeys.reduce<number>((sum, col) => sum + (cells.get(`${key(row)}|${key(col)}`)?.[0] ?? 0), 0);
  };

  if (visualization === "table" || !measure) return null;

  if (visualization === "indicator") {
    const grand = result.totals?.grand?.[0] ?? rowKeys.reduce<number>((sum, row) => sum + rowTotal(row), 0);
    return <Frame embedded={embedded} padding={3}>
      <Typography variant="overline" color="text.secondary" fontWeight={750}>{measureName}</Typography>
      <Typography sx={{ fontSize: embedded ? 36 : 44, fontWeight: 750, letterSpacing: "-0.03em", lineHeight: 1.1 }}>{typeof grand === "number" ? money.format(grand) : "—"}</Typography>
      <Typography variant="body2" color="text.secondary" mt={0.5}>{result.record_count.toLocaleString("pt-BR")} registros considerados</Typography>
    </Frame>;
  }

  const categories = rowKeys.map(row => rowKeys.length && result.rows.length ? keyLabel(row, result.rows) : "Total");
  const xTitle = result.rows.map(fieldLabel).join(" › ");

  if (visualization === "pie") {
    const ranked = rowKeys.map((row, index) => ({ row, label: categories[index], value: rowTotal(row) })).filter(item => item.value > 0).sort((a, b) => b.value - a.value);
    const top = ranked.slice(0, PIE_SLICES);
    const rest = ranked.slice(PIE_SLICES).reduce((sum, item) => sum + item.value, 0);
    const labels = [...top.map(item => item.label), ...(rest > 0 ? ["Outros"] : [])];
    const values = [...top.map(item => item.value), ...(rest > 0 ? [rest] : [])];
    return <Frame embedded={embedded} padding={1.5}>
      <Plot
        data={[{ type: "pie", labels, values, hole: 0.45, sort: false, textinfo: "percent", hovertemplate: "%{label}<br>%{value:,.2f}<extra></extra>", customdata: top.map((_, index) => index) } as any]}
        layout={{ height, margin: { l: 16, r: 16, t: 16, b: 16 }, legend: { orientation: "v" } }}
        onClick={(event: any) => { const index = event.points?.[0]?.customdata; if (onCellClick && typeof index === "number" && top[index]) onCellClick(top[index].row, []); }}
      />
      {!embedded && ranked.length > PIE_SLICES && <Typography variant="caption" color="text.secondary">Mostrando os {PIE_SLICES} maiores; o restante está somado em “Outros”.</Typography>}
    </Frame>;
  }

  const stacked = result.columns.length > 0;
  const traces = colKeys.map(col => ({
    type: visualization === "line" ? "scatter" : "bar",
    mode: visualization === "line" ? "lines+markers" : undefined,
    name: stacked ? keyLabel(col, result.columns) : measureName,
    x: categories,
    y: rowKeys.map(row => cells.get(`${key(row)}|${key(col)}`)?.[0] ?? null),
    customdata: rowKeys.map((_, index) => index),
    hovertemplate: "%{x}<br>%{y:,.2f}<extra>%{fullData.name}</extra>",
    meta: col,
  }));
  return <Frame embedded={embedded} padding={1.5}>
    {!embedded && <Stack direction="row" justifyContent="space-between" alignItems="baseline" px={1}>
      <Typography variant="subtitle2" fontWeight={750}>{measureName}{xTitle ? ` por ${xTitle}` : ""}</Typography>
      {stacked && <Typography variant="caption" color="text.secondary">Empilhado por {result.columns.map(fieldLabel).join(" › ")}</Typography>}
    </Stack>}
    <Box>
      <Plot
        data={traces as any}
        layout={{ height, ...(margin && { margin }), barmode: stacked ? "stack" : "group", showlegend: stacked, legend: { orientation: "h", y: -0.25 }, xaxis: { type: "category", automargin: true, tickangle: categories.length > 6 ? -35 : 0, tickvals: categories, ticktext: categories.map(shortTick) }, yaxis: { tickformat: "~s", automargin: true }, hovermode: "closest" }}
        onClick={(event: any) => {
          const point = event.points?.[0];
          const index = point?.customdata;
          if (onCellClick && typeof index === "number" && rowKeys[index]) onCellClick(rowKeys[index], (point.data?.meta as unknown[]) || []);
        }}
      />
    </Box>
    {!embedded && rowKeys.length > 60 && <Typography variant="caption" color="text.secondary" px={1}>{rowKeys.length} grupos. Use um filtro ou um agrupamento menos detalhado para ler melhor o gráfico ({compact.format(rowKeys.length)} barras).</Typography>}
  </Frame>;
}
