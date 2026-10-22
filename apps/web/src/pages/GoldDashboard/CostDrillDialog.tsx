import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Breadcrumbs, Button, CircularProgress, Dialog, DialogContent, DialogTitle, IconButton, Link, Paper, Stack, Typography } from "@mui/material";
import { ChevronRightOutlined, CloseOutlined } from "@mui/icons-material";
import Plot from "@/components/Plot";
import { colors } from "../../theme/tokens";
import { type GoldQueryFilter, formatApiError, queryDimonTable } from "../../services/api";
import type { AnomalyCase } from "../../services/anomaliesApi";

// "Onde esta o custo?": decomposicao com treemap, ranking com variacao contra o periodo
// anterior e sinais de anomalia. Os dados so sao buscados quando o dialog abre, um nivel
// por vez, e a consulta do periodo anterior roda em paralelo com a atual.
type Mode = "hierarchy" | "servicename" | "regionname" | "subaccountname";
type Row = { name: string; value: number; prev: number | null };

const MODE_LABELS: Record<Mode, string> = { hierarchy: "Hierarquia", servicename: "Serviço", regionname: "Região", subaccountname: "Subconta" };
const LEVEL_LABELS = ["Segmento", "Modelo", "VP", "Diretoria", "Projeto", "Unidade"];
const ANOMALY_FIELD: Partial<Record<Mode, "service_name" | "region" | "subaccount_name">> = {
  servicename: "service_name",
  regionname: "region",
  subaccountname: "subaccount_name",
};
const UNKNOWN = "Sem classificação";
const MAX_LEVEL = 6;
const TREEMAP_LIMIT = 30;

const brlCompact = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const signedBrl = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${brlCompact(Math.abs(value))}`;
const percent = (value: number) => `${(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const signedPct = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${percent(Math.abs(value))}`;
const valueOf = (row: Record<string, any>, column: string) => Number(row[column] ?? 0);

// Meses do periodo anterior, no mesmo formato da lista de meses do filtro atual.
function previousPeriodFilters(filters: GoldQueryFilter[]): GoldQueryFilter[] | null {
  const billing = filters.find((filter) => filter.column === "_billing_period");
  if (!billing?.values?.length) return null;
  const values = billing.values.map(String).sort();
  const dashed = values[0].includes("-");
  const toIndex = (value: string) => {
    const match = /^(\d{4})-?(\d{2})$/.exec(value);
    return match ? Number(match[1]) * 12 + Number(match[2]) - 1 : NaN;
  };
  const first = toIndex(values[0]);
  if (Number.isNaN(first)) return null;
  const count = values.length;
  const previous = Array.from({ length: count }, (_, k) => first - count + k).map((index) => {
    const year = Math.floor(index / 12);
    const month = String((index % 12) + 1).padStart(2, "0");
    return dashed ? `${year}-${month}` : `${year}${month}`;
  });
  return [
    ...filters.filter((filter) => filter.column !== "_billing_period"),
    { column: "_billing_period", operator: "IN", values: previous } as GoldQueryFilter,
  ];
}

export default function CostDrillDialog({ open, onClose, table, measureColumn, filters, basePath, anomalies }: {
  open: boolean;
  onClose: () => void;
  table: string;
  measureColumn: "realizado" | "realizado_cloud";
  filters: GoldQueryFilter[];
  basePath: string[];
  anomalies: AnomalyCase[];
}) {
  const [mode, setMode] = useState<Mode>("hierarchy");
  const [drill, setDrill] = useState<string[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const basePathKey = JSON.stringify(basePath);
  const level = basePath.length + drill.length + 1;
  const canDrill = mode === "hierarchy" && level <= MAX_LEVEL;
  const filtersKey = JSON.stringify(filters);
  const drillKey = JSON.stringify(drill);

  useEffect(() => {
    if (open) setDrill([]);
  }, [open, basePathKey]);

  // Carrega somente quando o dialog esta aberto; atual e periodo anterior em paralelo.
  useEffect(() => {
    if (!open) return;
    if (mode === "hierarchy" && level > MAX_LEVEL) { setRows([]); return; }
    let alive = true;
    setLoading(true);
    setError("");
    const column = mode === "hierarchy" ? `hierarquia_${level}` : mode;
    const drillFilters: GoldQueryFilter[] = mode === "hierarchy"
      ? drill.map((name, index) => ({ column: `hierarquia_${basePath.length + index + 1}`, operator: "IN", values: [name] }) as GoldQueryFilter)
      : [];
    const previousFilters = previousPeriodFilters(filters);
    const request = (scoped: GoldQueryFilter[]) => queryDimonTable({
      table,
      rows: [column],
      measures: [{ column: measureColumn, aggregation: "SUM" }],
      filters: scoped,
      row_limit: 200,
      sort_column: null,
      sort_direction: "desc",
    });
    Promise.all([
      request([...filters, ...drillFilters]),
      previousFilters ? request([...previousFilters, ...drillFilters]) : Promise.resolve(null),
    ])
      .then(([current, previous]) => {
        if (!alive) return;
        const previousByName = new Map<string, number>();
        previous?.rows.forEach((row) => previousByName.set(String(row[column] ?? UNKNOWN), valueOf(row, measureColumn)));
        setRows(current.rows
          .map((row) => {
            const name = String(row[column] ?? UNKNOWN);
            return { name, value: valueOf(row, measureColumn), prev: previous ? previousByName.get(name) ?? 0 : null };
          })
          .sort((a, z) => z.value - a.value));
      })
      .catch((reason) => { if (alive) setError(formatApiError(reason, "Não foi possível carregar a decomposição")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, table, measureColumn, filtersKey, drillKey, basePathKey, level, mode]);

  const anomalyCounts = useMemo(() => {
    const field = ANOMALY_FIELD[mode];
    const counts = new Map<string, number>();
    if (!field) return counts;
    anomalies.forEach((item) => {
      const name = String(item[field] ?? UNKNOWN);
      counts.set(name, (counts.get(name) ?? 0) + 1);
    });
    return counts;
  }, [anomalies, mode]);

  // Textos de destaque (Pareto e maior aumento), calculados uma vez por consulta.
  const insight = useMemo(() => {
    const total = rows.reduce((sum, row) => sum + row.value, 0);
    if (!rows.length || total <= 0) return [] as string[];
    const top3 = rows.slice(0, 3);
    const top3Share = top3.reduce((sum, row) => sum + row.value, 0) / total;
    let accumulated = 0;
    let to80 = 0;
    for (const row of rows) {
      accumulated += row.value;
      to80 += 1;
      if (accumulated >= total * 0.8) break;
    }
    const lines = [
      `Os ${top3.length} maiores itens concentram ${percent(top3Share)} do custo.`,
      `${to80} de ${rows.length} itens explicam 80% do custo.`,
    ];
    const rise = rows
      .filter((row) => row.prev != null)
      .map((row) => ({ name: row.name, delta: row.value - (row.prev ?? 0), prev: row.prev ?? 0 }))
      .sort((a, z) => z.delta - a.delta)[0];
    if (rise && rise.delta > 0) {
      const pct = rise.prev ? rise.delta / rise.prev : null;
      lines.push(`O maior aumento foi ${rise.name} (${signedBrl(rise.delta)}${pct != null ? `, ${signedPct(pct)}` : ""}) frente ao período anterior.`);
    }
    return lines;
  }, [rows]);

  const total = rows.reduce((sum, row) => sum + row.value, 0);
  const treemapRows = rows.slice(0, TREEMAP_LIMIT);
  const restValue = rows.slice(TREEMAP_LIMIT).reduce((sum, row) => sum + row.value, 0);
  const treemapLabels = [...treemapRows.map((row) => row.name), ...(restValue > 0 ? ["Outros"] : [])];
  const treemapValues = [...treemapRows.map((row) => Math.max(row.value, 0)), ...(restValue > 0 ? [restValue] : [])];
  const rootLabel = basePath.length ? basePath.join(" > ") : "Todos";

  const drillInto = (name: string) => {
    if (!canDrill || name === UNKNOWN || name === "Outros" || level >= MAX_LEVEL) return;
    setDrill([...drill, name]);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 2 }}>
        <Box>
          <Typography variant="h6" fontWeight={800} color={colors.text}>Onde está o custo?</Typography>
          <Typography variant="caption" color="text.secondary">Decomposição do custo no escopo e período atuais</Typography>
        </Box>
        <IconButton aria-label="Fechar" onClick={onClose}><CloseOutlined /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        <Stack direction="row" gap={1} flexWrap="wrap" mb={2}>
          {(Object.keys(MODE_LABELS) as Mode[]).map((item) => (
            <Button
              key={item}
              size="small"
              variant={item === mode ? "contained" : "outlined"}
              onClick={() => { setMode(item); setDrill([]); }}
              sx={{ textTransform: "none", borderRadius: "12px", bgcolor: item === mode ? colors.brandDark : undefined }}
            >
              {MODE_LABELS[item]}
            </Button>
          ))}
        </Stack>

        {mode === "hierarchy" && (
          <Breadcrumbs sx={{ mb: 2 }}>
            {drill.length ? (
              <Link component="button" underline="hover" color="inherit" onClick={() => setDrill([])}>{rootLabel}</Link>
            ) : (
              <Typography color="text.primary">{rootLabel}</Typography>
            )}
            {drill.map((name, index) => index === drill.length - 1 ? (
              <Typography key={`${index}-${name}`} color="text.primary">{name}</Typography>
            ) : (
              <Link key={`${index}-${name}`} component="button" underline="hover" color="inherit" onClick={() => setDrill(drill.slice(0, index + 1))}>{name}</Link>
            ))}
          </Breadcrumbs>
        )}

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {insight.length > 0 && (
          <Paper variant="outlined" sx={{ p: 2, mb: 2, borderRadius: "14px", borderLeft: `4px solid ${colors.brand}`, borderColor: colors.border }}>
            <Typography variant="overline" color="text.secondary" fontWeight={800}>Leitura rápida</Typography>
            {insight.map((line) => <Typography key={line} variant="body2" color={colors.text}>{line}</Typography>)}
          </Paper>
        )}

        {mode === "hierarchy" && !canDrill ? (
          <Typography variant="body2" color="text.secondary">Este já é o último nível da hierarquia.</Typography>
        ) : loading ? (
          <Box sx={{ py: 8, textAlign: "center" }}><CircularProgress size={24} /></Box>
        ) : !rows.length ? (
          <Typography variant="body2" color="text.secondary">Sem custo neste recorte.</Typography>
        ) : (
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1.15fr) minmax(0, 1fr)" } }}>
            <Box>
              <Typography variant="caption" color="text.secondary" display="block" mb={0.5}>
                {mode === "hierarchy" ? `${LEVEL_LABELS[level - 1]} · clique para descer um nível` : MODE_LABELS[mode]}
              </Typography>
              <Plot
                data={[{
                  type: "treemap",
                  labels: treemapLabels,
                  parents: treemapLabels.map(() => ""),
                  values: treemapValues,
                  branchvalues: "total",
                  textinfo: "label+percent root",
                  hovertemplate: "%{label}<br>%{value:,.0f}<br>%{percentRoot:.1%}<extra></extra>",
                  marker: { colors: treemapValues, colorscale: [[0, "#DCE8FF"], [1, colors.brandDark]], showscale: false, line: { color: "#FFFFFF", width: 2 } },
                } as any]}
                layout={{ height: 380, margin: { l: 0, r: 0, t: 0, b: 0 } } as any}
                config={{ displayModeBar: false }}
                style={{ height: 380 }}
                onClick={(event: any) => { const label = event?.points?.[0]?.label; if (typeof label === "string") drillInto(label); }}
              />
            </Box>

            <Stack gap={0.5} sx={{ minWidth: 0 }}>
              <Stack direction="row" gap={1} sx={{ px: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>Item</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ width: 96, textAlign: "right" }}>Custo</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ width: 150, textAlign: "right" }}>Vs. período anterior</Typography>
              </Stack>
              {rows.slice(0, 12).map((row) => {
                const delta = row.prev != null ? row.value - row.prev : null;
                const deltaPct = row.prev ? (delta ?? 0) / row.prev : null;
                const flags = anomalyCounts.get(row.name) ?? 0;
                const clickable = canDrill && row.name !== UNKNOWN;
                return (
                  <Stack
                    key={row.name}
                    onClick={clickable ? () => drillInto(row.name) : undefined}
                    sx={{ px: 1, py: 0.75, borderRadius: 1, cursor: clickable ? "pointer" : "default", "&:hover": { bgcolor: clickable ? colors.rowHover : undefined } }}
                  >
                    <Stack direction="row" alignItems="center" gap={1}>
                      <Typography variant="body2" noWrap title={row.name} sx={{ flex: 1, minWidth: 0 }}>{row.name}</Typography>
                      <Typography variant="body2" fontWeight={700} sx={{ width: 96, textAlign: "right" }}>{brlCompact(row.value)}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ width: 40, textAlign: "right" }}>{total ? percent(row.value / total) : "—"}</Typography>
                      <Box sx={{ width: 18 }}>{clickable && <ChevronRightOutlined fontSize="small" sx={{ color: colors.textMuted }} />}</Box>
                    </Stack>
                    <Stack direction="row" alignItems="center" gap={1} mt={0.5}>
                      <Box sx={{ flex: 1, height: 6, bgcolor: colors.tableHead, borderRadius: 3, overflow: "hidden" }}>
                        <Box sx={{ width: `${total ? (row.value / total) * 100 : 0}%`, height: "100%", bgcolor: colors.brand, borderRadius: 3 }} />
                      </Box>
                      {delta != null && (
                        <Typography variant="caption" fontWeight={700} sx={{ width: 150, textAlign: "right", color: delta > 0 ? colors.accentDark : delta < 0 ? colors.brandDark : colors.textSecondary }}>
                          {signedBrl(delta)}{deltaPct != null ? ` (${signedPct(deltaPct)})` : ""}
                        </Typography>
                      )}
                      {flags > 0 && (
                        <Typography variant="caption" fontWeight={700} sx={{ color: colors.accentDark, whiteSpace: "nowrap" }}>◆ {flags} anomalia{flags > 1 ? "s" : ""}</Typography>
                      )}
                    </Stack>
                  </Stack>
                );
              })}
              <Typography variant="caption" color="text.secondary" sx={{ px: 1, pt: 1 }}>
                Anomalias aparecem para serviço, região e subconta. Orçamento é por departamento e não entra nesta decomposição.
              </Typography>
            </Stack>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
}
