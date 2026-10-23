import { useEffect, useState } from "react";
import { Alert, Box, CircularProgress, Divider, IconButton, ListSubheader, Menu, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableRow, Tooltip, Typography } from "@mui/material";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import PivotChart, { PivotVisualization } from "../../components/PivotChart";
import { DragHandle, More, OpenInFull } from "../../components/FinbaseIcons";
import { RowAction } from "../../components/RowAction";
import { DashboardWidget, DashboardWidgetPosition, PivotResult, PivotView, formatApiError, runSavedReport } from "../../services/api";
import { fieldLabel, friendlyMeasureLabel } from "../../utils/pivotLabels";
import { HEIGHT_OPTIONS, WIDTH_OPTIONS, visualizationFor, widgetPixelHeight } from "./dashboardLayout";

const HEADER_HEIGHT = 44;
const money = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const VISUALIZATION_OPTIONS: { value: PivotVisualization; label: string }[] = [
  { value: "table", label: "Tabela" }, { value: "bar", label: "Barras" }, { value: "line", label: "Linha" },
  { value: "pie", label: "Pizza" }, { value: "indicator", label: "Indicador" },
];

export type LaidOutWidget = DashboardWidget & { position: DashboardWidgetPosition };

interface Props {
  widget: LaidOutWidget;
  report?: PivotView;
  editing: boolean;
  onResize: (position: Partial<DashboardWidgetPosition>) => void;
  onVisualization: (visualization: PivotVisualization) => void;
  onRemove: () => void;
  onOpenReport: (report: PivotView) => void;
}

function valueLabel(name: string, value: unknown) {
  if (value == null || value === "") return "(vazio)";
  if (["_billing_period", "period", "forecast_month"].includes(name.toLowerCase())) {
    const digits = String(value).replace(/\D/g, "");
    if (digits.length >= 6) return `${digits.slice(4, 6)}/${digits.slice(0, 4)}`;
  }
  return String(value);
}

/** Compact table for widgets: one line per group with the first measure's total. */
function WidgetTable({ result }: { result: PivotResult }) {
  const measure = result.measures[0];
  const total = (row: unknown[]) => result.totals?.rows[JSON.stringify(row)]?.[0];
  const rows = result.row_keys || [];
  if (!rows.length || !result.rows.length) {
    const grand = result.totals?.grand?.[0];
    return <Typography sx={{ fontSize: 28, fontWeight: 750, p: 1 }}>{typeof grand === "number" ? money.format(grand) : "—"}</Typography>;
  }
  return <Table size="small" stickyHeader>
    <TableBody>
      <TableRow>
        <TableCell sx={{ fontWeight: 700 }}>{result.rows.map(fieldLabel).join(" › ")}</TableCell>
        <TableCell align="right" sx={{ fontWeight: 700 }}>{measure ? friendlyMeasureLabel(measure) : "Valor"}</TableCell>
      </TableRow>
      {rows.map(row => {
        const value = total(row);
        return <TableRow key={JSON.stringify(row)} hover>
          <TableCell>{row.map((item, index) => valueLabel(result.rows[index], item)).join(" › ")}</TableCell>
          <TableCell align="right">{typeof value === "number" ? money.format(value) : "—"}</TableCell>
        </TableRow>;
      })}
    </TableBody>
  </Table>;
}

export default function DashboardWidgetCard({ widget, report, editing, onResize, onVisualization, onRemove, onOpenReport }: Props) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id: widget.id, disabled: !editing });
  const [result, setResult] = useState<PivotResult | null>(null);
  const [error, setError] = useState("");
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const visualization = visualizationFor(widget.chart_type);
  const reportKey = report ? `${report.id}:${report.updated_at || ""}` : "";

  useEffect(() => {
    if (!report) return;
    let cancelled = false;
    setResult(null);
    setError("");
    runSavedReport(report)
      .then(next => { if (!cancelled) setResult(next); })
      .catch(e => { if (!cancelled) setError(formatApiError(e, "Não foi possível consultar os dados deste relatório")); });
    return () => { cancelled = true; };
  }, [reportKey]);

  const bodyHeight = widgetPixelHeight(widget.position.height) - HEADER_HEIGHT - 16;
  const pick = (action: () => void) => { action(); setMenuAnchor(null); };

  return <Paper
    ref={setNodeRef}
    variant="outlined"
    sx={{
      gridColumn: { xs: "1 / -1", md: `span ${widget.position.width}` },
      gridRow: `span ${widget.position.height}`,
      display: "flex", flexDirection: "column", minWidth: 0, overflow: "hidden",
      transform: CSS.Transform.toString(transform), transition,
      opacity: isDragging ? 0.6 : 1, zIndex: isDragging ? 2 : undefined,
      borderStyle: editing ? "dashed" : "solid", borderColor: editing ? "primary.light" : undefined,
    }}
  >
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ height: HEADER_HEIGHT, px: 1, flexShrink: 0 }}>
      {editing && <IconButton ref={setActivatorNodeRef} size="small" aria-label={`Mover widget ${widget.title}`} sx={{ cursor: "grab", touchAction: "none" }} {...attributes} {...listeners}><DragHandle fontSize="small" /></IconButton>}
      <Typography variant="subtitle2" fontWeight={750} noWrap sx={{ flex: 1, minWidth: 0, pl: editing ? 0 : 0.5 }} title={widget.title}>{widget.title}</Typography>
      {report && !editing && <Tooltip title="Abrir no pivot"><IconButton size="small" aria-label={`Abrir relatório ${widget.title} no pivot`} onClick={() => onOpenReport(report)}><OpenInFull fontSize="small" /></IconButton></Tooltip>}
      {editing && <>
        <IconButton size="small" aria-label={`Ajustar widget ${widget.title}`} onClick={event => setMenuAnchor(event.currentTarget)}><More fontSize="small" /></IconButton>
        <RowAction kind="delete" label={`Remover widget ${widget.title}`} onClick={onRemove} />
      </>}
    </Stack>
    <Divider />
    <Box sx={{ flex: 1, minHeight: 0, p: 1, overflow: visualization === "table" ? "auto" : "hidden" }}>
      {!report && <Alert severity="warning" variant="outlined">O relatório deste widget foi excluído. Remova o widget ou adicione outro relatório.</Alert>}
      {report && error && <Alert severity="error" variant="outlined">{error}</Alert>}
      {report && !error && !result && <Box sx={{ height: "100%", display: "grid", placeItems: "center" }}><CircularProgress size={24} aria-label="Carregando widget" /></Box>}
      {result && (visualization === "table"
        ? <WidgetTable result={result} />
        : <PivotChart embedded result={result} visualization={visualization} height={bodyHeight} />)}
    </Box>
    <Menu anchorEl={menuAnchor} open={!!menuAnchor} onClose={() => setMenuAnchor(null)}>
      <ListSubheader>Visualização</ListSubheader>
      {VISUALIZATION_OPTIONS.map(option => <MenuItem key={option.value} selected={visualization === option.value} onClick={() => pick(() => onVisualization(option.value))}>{option.label}</MenuItem>)}
      <ListSubheader>Largura</ListSubheader>
      {WIDTH_OPTIONS.map(option => <MenuItem key={option.value} selected={widget.position.width === option.value} onClick={() => pick(() => onResize({ width: option.value }))}>{option.label}</MenuItem>)}
      <ListSubheader>Altura</ListSubheader>
      {HEIGHT_OPTIONS.map(option => <MenuItem key={option.value} selected={widget.position.height === option.value} onClick={() => pick(() => onResize({ height: option.value }))}>{option.label}</MenuItem>)}
    </Menu>
  </Paper>;
}
