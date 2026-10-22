import { useEffect, useMemo, useState } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Box, Paper, Typography, Stack, Alert, Button, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, CircularProgress,
} from "@mui/material";
import { FileDownloadOutlined } from "@mui/icons-material";
import * as XLSX from "xlsx";
import {
  queryDimonTable, runForecastHistoryPivot, getForecastAccuracyMonths,
  ForecastAccuracyMonth, HistoryFilter, GoldQueryFilter,
} from "../../services/api";

// ── Helpers (autocontidos) ──
const PURPLE = "#0B3FA8", ORANGE = "#ff7a00", GREEN = "#10b981", RED = "#ef4444", INK = "#162B3A", MUT = "#526777";
const COMPACT_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });
const FULL_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });

function money(v: unknown): string { return COMPACT_BRL.format(Number(v ?? 0)); }
function compact(v: unknown): string { return money(v); }
function pct(v: unknown, digits = 1): string { return `${Number(v ?? 0).toLocaleString("pt-BR", { maximumFractionDigits: digits })}%`; }
function monthLabel(m: string): string {
  const t = String(m ?? "");
  const match = t.match(/^(\d{4})(\d{2})$/);
  if (!match) return t;
  const months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
  return `${months[Number(match[2]) - 1]}/${match[1].slice(2)}`;
}

export type Filters = {
  months: string[];
  clouds: string[];
  dirs: string[];
  depts: string[];
  servicos: string[];
  cats: string[];
  path: string[];
  deptId: number | null;
  extra: Record<string, string[]>;
};

export const EMPTY_FILTERS: Filters = { months: [], clouds: [], dirs: [], depts: [], servicos: [], cats: [], path: [], deptId: null, extra: {} };

function filtersOf(f: Filters, extra: GoldQueryFilter[] = []): GoldQueryFilter[] {
  const out: GoldQueryFilter[] = [...extra];
  if (f.months.length) out.push({ column: "_billing_period", operator: "IN", values: f.months });
  if (f.clouds.length) out.push({ column: "cloud_origem", operator: "IN", values: f.clouds });
  if (f.dirs.length) out.push({ column: "organograma_nivel_4", operator: "IN", values: f.dirs });
  if (f.depts.length) out.push({ column: "organograma_nivel_5", operator: "IN", values: f.depts });
  if (f.servicos.length) out.push({ column: "servicename", operator: "IN", values: f.servicos });
  if (f.cats.length) out.push({ column: "servicecategory", operator: "IN", values: f.cats });
  if (f.path.length) f.path.forEach((segment, index) => out.push({ column: `organograma_nivel_${index + 1}`, operator: "IN", values: [segment] }));
  Object.entries(f.extra || {}).forEach(([column, values]) => { if (values && values.length) out.push({ column, operator: "IN", values }); });
  return out;
}

function pivotFiltersFrom(filters: Filters): Record<string, HistoryFilter> {
  const out: Record<string, HistoryFilter> = {};
  if (filters.clouds.length) out.cloud_origem = { operator: "in", value: filters.clouds };
  if (filters.dirs.length) out.organograma_nivel_4 = { operator: "in", value: filters.dirs };
  if (filters.depts.length) out.organograma_nivel_5 = { operator: "in", value: filters.depts };
  if (filters.servicos.length) out.servicename = { operator: "in", value: filters.servicos };
  if (filters.cats.length) out.servicecategory = { operator: "in", value: filters.cats };
  if (filters.path.length) filters.path.forEach((seg, i) => { if (seg) out[`organograma_nivel_${i + 1}`] = { operator: "in", value: [seg] }; });
  Object.entries(filters.extra || {}).forEach(([col, vals]) => { if (vals && vals.length) out[col] = { operator: "in", value: vals }; });
  return out;
}

function SectionPanel({ title, subtitle, right, children }: {
  title: string; subtitle?: string; right?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={1} mb={1}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 950, color: INK }}>{title}</Typography>
          {subtitle && <Typography variant="caption" sx={{ color: MUT, fontWeight: 700 }}>{subtitle}</Typography>}
        </Box>
        {right}
      </Stack>
      {children}
    </Paper>
  );
}

function LoadingBlock({ loading, children }: { loading: boolean; children: React.ReactNode }) {
  if (loading) return <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}><CircularProgress size={26} /></Box>;
  return <>{children}</>;
}

// ── Visão Contábil: pares Realizado X → Provisão X+1 (Forecast Accuracy) ──
export default function VisaoContabil({ table, filters, filterKey, dollarEvolution }: { table: string; filters: Filters; filterKey: string; dollarEvolution?: React.ReactNode }) {
  const [searchParams] = useSearchParams();
  const view = searchParams.get("subview") === "evolucao-dolar" ? "evolucao-dolar" : "pedidos-sap";
  const [selectedPair, setSelectedPair] = useState<[string, string] | null>(null);
  const [months, setMonths] = useState<ForecastAccuracyMonth[]>([]);
  const [monthsLoading, setMonthsLoading] = useState(true);
  const [det, setDet] = useState<{
    loading: boolean;
    clouds: Array<{ cloud: string; rows: Array<{ cc: string; real: number; prov: number }>; totalReal: number; totalProv: number }>;
  }>({ loading: false, clouds: [] });

  const dept = filters.path.length ? filters.path.join(" > ") : undefined;

  useEffect(() => {
    let alive = true;
    setMonthsLoading(true);
    getForecastAccuracyMonths(9, dept, "all")
      .then(list => { if (alive) { setMonths(list); setMonthsLoading(false); } })
      .catch(() => { if (alive) setMonthsLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  const pares = useMemo(() => {
    const sorted = [...months].sort((a, b) => a.target_month.localeCompare(b.target_month));
    const out: Array<{ mesReal: string; mesProv: string; realVal: number; provVal: number; runId: number }> = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i], b = sorted[i + 1];
      out.push({ mesReal: a.target_month, mesProv: b.target_month, realVal: a.actual_value || 0, provVal: b.forecast_value || 0, runId: b.run_id });
    }
    return out;
  }, [months]);

  useEffect(() => {
    if (!selectedPair) { setDet({ loading: false, clouds: [] }); return; }
    const [mesReal, mesProv] = selectedPair;
    const run = months.find(m => m.target_month === mesProv);
    if (!run) { setDet({ loading: false, clouds: [] }); return; }
    let alive = true;
    setDet(d => ({ ...d, loading: true }));
    const pvFilters: Record<string, HistoryFilter> = {
      ...pivotFiltersFrom(filters),
      tipo: { operator: "in", value: ["forecast"] },
      forecast_month: { operator: "in", value: [mesProv] },
    };
    Promise.all([
      runForecastHistoryPivot(run.run_id, { rows: ["cloud_origem", "centro_custo"], columns: [], filters: pvFilters, row_limit: 100 }).catch(() => null),
      queryDimonTable({
        table,
        rows: ["cloud_origem", "centro_custo"],
        measures: [{ column: "realizado", aggregation: "SUM" }],
        filters: [...filtersOf(filters), { column: "_billing_period", operator: "IN", values: [mesReal] }, { column: "tipo", operator: "IN", values: ["realizado"] }],
        row_limit: 10000,
      }).catch(() => null),
    ]).then(([pivotRes, goldRes]) => {
      if (!alive) return;
      const map = new Map<string, Map<string, { real: number; prov: number }>>();
      const bump = (cloud: string, cc: string, real: number, prov: number) => {
        if (!map.has(cloud)) map.set(cloud, new Map());
        const ccMap = map.get(cloud)!;
        if (!ccMap.has(cc)) ccMap.set(cc, { real: 0, prov: 0 });
        const o = ccMap.get(cc)!;
        o.real += real; o.prov += prov;
      };
      for (const rec of pivotRes?.records ?? []) {
        bump(String(rec.d0 ?? "Sem classificação"), String(rec.d1 ?? "Sem classificação"), 0, Number(rec.value || 0));
      }
      for (const r of goldRes?.rows ?? []) {
        bump(String(r.cloud_origem ?? "Sem classificação"), String(r.centro_custo ?? "Sem classificação"), Number(r.realizado || 0), 0);
      }
      const clouds = [...map.entries()].map(([cloud, ccMap]) => ({
        cloud,
        rows: [...ccMap.entries()].map(([cc, v]) => ({ cc, ...v })).sort((a, b) => Math.abs(b.real) - Math.abs(a.real)),
        totalReal: [...ccMap.values()].reduce((s, x) => s + x.real, 0),
        totalProv: [...ccMap.values()].reduce((s, x) => s + x.prov, 0),
      })).sort((a, b) => Math.abs(b.totalReal) - Math.abs(a.totalReal));
      setDet({ loading: false, clouds });
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPair, months, filterKey, table, filters]);

  const totalReal = det.clouds.reduce((s, c) => s + c.totalReal, 0);
  const totalProv = det.clouds.reduce((s, c) => s + c.totalProv, 0);

  const exportPedidoSap = () => {
    if (!selectedPair || !det.clouds.length) return;
    const wb = XLSX.utils.book_new();
    const sanitize = (name: string) => String(name || "Cloud").replace(/[\[\]:*?/\\]/g, "_").slice(0, 31);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([
      ["Cloud", "Valor efetivado", "Provisão contábil", "Δ (Provisão − efetivado)", "Provisão % do efetivado"],
      ...det.clouds.map(c => [c.cloud, c.totalReal, c.totalProv, c.totalProv - c.totalReal, c.totalReal ? ((c.totalProv - c.totalReal) / Math.abs(c.totalReal)) * 100 : 0]),
    ]), "Resumo");
    det.clouds.forEach(c => {
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([
        ["Centro de custo", "Valor efetivado", "Provisão contábil", "Δ (Provisão − efetivado)", "Provisão % do efetivado"],
        ...c.rows.map(x => [x.cc, x.real, x.prov, x.prov - x.real, x.real ? (x.prov / Math.abs(x.real)) * 100 : 0]),
        ["TOTAL", c.totalReal, c.totalProv, c.totalProv - c.totalReal, c.totalReal ? ((c.totalProv - c.totalReal) / Math.abs(c.totalReal)) * 100 : 0],
      ]), sanitize(c.cloud));
    });
    XLSX.writeFile(wb, `pedido_sap_${selectedPair[0]}_${selectedPair[1]}.xlsx`);
  };

  return (
    <Box>
      {view === "evolucao-dolar" ? (dollarEvolution ?? <Alert severity="warning">Série histórica indisponível no momento.</Alert>) : <>
      <SectionPanel title="Pedido SAP — efetivado × provisão"
        subtitle="Escolha o par efetivado do mês X → Provisão do mês seguinte. Confira o detalhe por cloud e centro de custo e exporte o pedido SAP em Excel.">
        <LoadingBlock loading={monthsLoading}>
          {pares.length ? (
            <Stack spacing={0.6}>
              {pares.map(p => {
                const active = selectedPair && selectedPair[0] === p.mesReal && selectedPair[1] === p.mesProv;
                return (
                  <Box key={p.mesReal}
                    onClick={() => setSelectedPair(active ? null : [p.mesReal, p.mesProv])}
                    sx={{
                      cursor: "pointer", borderRadius: 1.5, border: "1px solid", px: 1.4, py: 1,
                      borderColor: active ? PURPLE : "divider",
                      bgcolor: active ? "#f5efff" : "background.paper",
                      "&:hover": { borderColor: PURPLE, bgcolor: "#faf7ff" },
                    }}>
                    <Stack direction="row" justifyContent="space-between" flexWrap="wrap" gap={0.5} alignItems="center">
                      <Typography variant="body2" sx={{ fontWeight: 950, color: active ? PURPLE : "#0B3FA8", textDecoration: "underline", textDecorationColor: active ? PURPLE : "#7DB4FF", textUnderlineOffset: 2 }}>
                        Efetivado {monthLabel(p.mesReal)} → Provisão {monthLabel(p.mesProv)}
                      </Typography>
                      <Typography variant="caption" sx={{ color: MUT, fontWeight: 750 }}>
                        Efetivado {compact(p.realVal)} · Provisão {compact(p.provVal)}
                      </Typography>
                    </Stack>
                  </Box>
                );
              })}
            </Stack>
          ) : (
            <Alert severity="info">Nenhum par disponível (efetivado do mês e previsão do mês seguinte). Reprocesse a precisão das previsões (Operações → Precisão das previsões) para gerar os snapshots.</Alert>
          )}
        </LoadingBlock>
      </SectionPanel>

      {selectedPair && (
        <SectionPanel title={`Detalhe ${monthLabel(selectedPair[0])} (Efetivado) × ${monthLabel(selectedPair[1])} (Provisão)`}
          subtitle="Uma visão por nuvem, agrupada por centro de custo — provisão do snapshot imutável do forecast"
          right={
            <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap">
              <Typography variant="caption" sx={{ fontWeight: 900, color: PURPLE }}>Total Efetivado: {compact(totalReal)}</Typography>
              <Typography variant="caption" sx={{ fontWeight: 900, color: ORANGE }}>Total Provisão: {compact(totalProv)}</Typography>
              <Typography variant="caption" sx={{ fontWeight: 900, color: totalProv >= totalReal ? RED : GREEN }}>
                Δ {totalProv >= totalReal ? "+" : ""}{compact(totalProv - totalReal)} ({totalReal ? pct(((totalProv - totalReal) / Math.abs(totalReal)) * 100) : "—"})
              </Typography>
              <Button size="small" variant="contained" startIcon={<FileDownloadOutlined />} onClick={exportPedidoSap} disabled={!det.clouds.length}>
                Export pedido SAP
              </Button>
            </Stack>
          }>
          <LoadingBlock loading={det.loading}>
            {det.clouds.length ? (
              <Stack spacing={1.5}>
                {det.clouds.map(c => (
                  <Paper key={c.cloud} variant="outlined" sx={{ p: 1.5 }}>
                    <Stack direction="row" justifyContent="space-between" flexWrap="wrap" gap={1} sx={{ mb: 1 }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 950, color: INK }}>{c.cloud}</Typography>
                      <Stack direction="row" spacing={2}>
                        <Typography variant="caption" sx={{ fontWeight: 900, color: PURPLE }}>Efetivado {compact(c.totalReal)}</Typography>
                        <Typography variant="caption" sx={{ fontWeight: 900, color: ORANGE }}>Provisão {compact(c.totalProv)}</Typography>
                        <Typography variant="caption" sx={{ fontWeight: 900, color: c.totalProv >= c.totalReal ? RED : GREEN }}>
                          Δ {c.totalProv >= c.totalReal ? "+" : ""}{compact(c.totalProv - c.totalReal)}
                        </Typography>
                      </Stack>
                    </Stack>
                    <TableContainer>
                      <Table size="small">
                        <TableHead><TableRow>
                          {["Centro de custo", "Valor efetivado", "Provisão contábil", "Δ (Provisão − efetivado)", "Provisão % do efetivado"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
                        </TableRow></TableHead>
                        <TableBody>
                          {c.rows.map((x, i) => {
                            const delta = x.prov - x.real;
                            const provPct = x.real ? (x.prov / Math.abs(x.real)) * 100 : 0;
                            return (
                              <TableRow key={i}>
                                <TableCell sx={{ fontSize: 12, fontWeight: 850 }}>{x.cc}</TableCell>
                                <TableCell sx={{ fontSize: 12, fontWeight: 900, whiteSpace: "nowrap" }}>{money(x.real)}</TableCell>
                                <TableCell sx={{ fontSize: 12, fontWeight: 900, color: ORANGE, whiteSpace: "nowrap" }}>{money(x.prov)}</TableCell>
                                <TableCell sx={{ fontSize: 12, fontWeight: 900, color: delta >= 0 ? RED : GREEN, whiteSpace: "nowrap" }}>{delta >= 0 ? "+" : ""}{compact(delta)}</TableCell>
                                <TableCell sx={{ fontSize: 12, fontWeight: 800, color: provPct > 105 ? RED : provPct < 95 ? GREEN : MUT }}>{pct(provPct)}</TableCell>
                              </TableRow>
                            );
                          })}
                          {!c.rows.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary", py: 2 }}>Sem centros de custo para esta nuvem no par selecionado.</TableCell></TableRow>}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  </Paper>
                ))}
              </Stack>
            ) : (
              <Alert severity="info">
                {det.loading ? "Carregando o detalhamento…" : "Nenhum dado para o par selecionado. Se a versão salva da previsão ainda não tiver centro_custo, aguarde o reprocessamento do Forecast Accuracy full."}
              </Alert>
            )}
          </LoadingBlock>
        </SectionPanel>
      )}
      </>}
    </Box>
  );
}
