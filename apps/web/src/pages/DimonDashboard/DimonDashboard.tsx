import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  Box, Typography, Paper, Grid, Chip, Alert, CircularProgress, Button,
  FormControl, InputLabel, Select, MenuItem, Tabs, Tab, Stack, TextField,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Divider, Tooltip, LinearProgress,
} from "@mui/material";
import { Refresh, FileDownload, ContentCopy, FilterAltOff, Search } from "@mui/icons-material";
import Plot from "react-plotly.js";
// Wrapper: react-plotly.js Data/DataTitle typings são excessivamente rígidos
// para traces com name/text customizados; mantém o restante tipado.
function Chart(props: any) {
  return <Plot {...props} />;
}
import {
  queryDimonTable, getDimonInfo, getAnalysisTables,
  DimonQueryFilter, DimonQueryMeasure, DimonQueryResult, DimonInfo,
  formatApiError,
} from "../../services/api";

// ─────────────────────────────────────────────────────────────────────
// Constantes e formatadores
// ─────────────────────────────────────────────────────────────────────
const DEFAULT_TABLE = "dest_dm_cost_visao_completa";
const PURPLE = "#660099", ORANGE = "#ff7a00", BLUE = "#155eef",
      GREEN = "#10b981", RED = "#ef4444", YELLOW = "#f59e0b", INK = "#0f172a", MUT = "#64748b";

const DIM_LABELS: Record<string, string> = {
  _billing_period: "Mês",
  cloud_origem: "Cloud",
  departamento_nivel_3: "VP",
  departamento_nivel_4: "Diretoria",
  departamento_nivel_5: "Departamento",
  departamento_nivel_6: "Unidade",
  departamento: "Área",
  centro_custo: "Centro de Custo",
  ambiente: "Ambiente",
  servicecategory: "Categoria",
  servicename: "Serviço",
  servicesubcategory: "Subcategoria",
  skumeter: "SKU",
  subaccountname: "Subconta",
  regionname: "Região",
  chargecategory: "Tipo de Carga",
};

const MEASURE_LABELS: Record<string, string> = {
  realizado: "Realizado (BRL)",
  billedcost: "Billed Cost (USD)",
  quantity: "Quantidade",
  count: "Registros",
};

const COMPACT_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });
const FULL_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const COMPACT_USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const FULL_USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const COMPACT_NUM = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });

function money(v: unknown, currency: "BRL" | "USD" = "BRL"): string {
  const n = Number(v ?? 0);
  return currency === "BRL" ? COMPACT_BRL.format(n) : COMPACT_USD.format(n);
}
function moneyFull(v: unknown, currency: "BRL" | "USD" = "BRL"): string {
  const n = Number(v ?? 0);
  return currency === "BRL" ? FULL_BRL.format(n) : FULL_USD.format(n);
}
function num(v: unknown): string { return COMPACT_NUM.format(Number(v ?? 0)); }
function pct(v: unknown, digits = 1): string {
  const n = Number(v ?? 0);
  return `${n.toLocaleString("pt-BR", { maximumFractionDigits: digits })}%`;
}
function esc(s: unknown): string { return String(s ?? "Não informado"); }

function monthLabel(m: string): string {
  const t = String(m ?? "");
  const match = t.match(/^(\d{4})(\d{2})$/);
  if (!match) return t || "—";
  const months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
  return `${months[Number(match[2]) - 1]}/${match[1].slice(2)}`;
}
function monthLong(m: string): string {
  const t = String(m ?? "");
  const match = t.match(/^(\d{4})(\d{2})$/);
  if (!match) return t;
  const months = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  return `${months[Number(match[2]) - 1]}/${match[1]}`;
}

// ─────────────────────────────────────────────────────────────────────
// Tipos e helpers de consulta
// ─────────────────────────────────────────────────────────────────────
type QueryDef = {
  id: string;
  rows?: string[];
  measures?: DimonQueryMeasure[];
  filters?: DimonQueryFilter[];
  row_limit?: number;
  sort_column?: string | null;
  sort_direction?: "asc" | "desc";
};

type Filters = {
  months: string[];   // [] = todos
  clouds: string[];
  dirs: string[];
  depts: string[];
  servicos: string[];
  cats: string[];
};

const EMPTY_FILTERS: Filters = { months: [], clouds: [], dirs: [], depts: [], servicos: [], cats: [] };

function filtersOf(f: Filters, extra: DimonQueryFilter[] = []): DimonQueryFilter[] {
  const out: DimonQueryFilter[] = [...extra];
  if (f.months.length) out.push({ column: "_billing_period", operator: "IN", values: f.months });
  if (f.clouds.length) out.push({ column: "cloud_origem", operator: "IN", values: f.clouds });
  if (f.dirs.length) out.push({ column: "departamento_nivel_4", operator: "IN", values: f.dirs });
  if (f.depts.length) out.push({ column: "departamento_nivel_5", operator: "IN", values: f.depts });
  if (f.servicos.length) out.push({ column: "servicename", operator: "IN", values: f.servicos });
  if (f.cats.length) out.push({ column: "servicecategory", operator: "IN", values: f.cats });
  return out;
}

function useTabQueries(table: string, defs: QueryDef[], key: string) {
  const [results, setResults] = useState<Record<string, DimonQueryResult | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    Promise.all(
      defs.map(d => queryDimonTable({
        table,
        rows: d.rows ?? [],
        measures: d.measures,
        filters: d.filters ?? [],
        row_limit: d.row_limit ?? 500,
        sort_column: d.sort_column ?? null,
        sort_direction: d.sort_direction ?? "desc",
      }).catch(() => null)),
    ).then(list => {
      if (!alive) return;
      const m: Record<string, DimonQueryResult | null> = {};
      defs.forEach((d, i) => { m[d.id] = list[i]; });
      setResults(m);
    }).catch(e => { if (alive) setError(formatApiError(e, "Falha ao consultar a Dimon")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, key]);
  return { results, loading, error };
}

function rowsOf(r: DimonQueryResult | null | undefined): Record<string, any>[] {
  return r?.rows ?? [];
}
function sumOf(r: DimonQueryResult | null | undefined, col: string): number {
  return (r?.rows ?? []).reduce((s, row) => s + Number(row[col] ?? 0), 0);
}

// ─────────────────────────────────────────────────────────────────────
// Componentes de apresentação
// ─────────────────────────────────────────────────────────────────────
function KpiCard({ label, value, sub, color, full }: { label: string; value: string; sub?: string; color?: string; full?: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.8, height: "100%", position: "relative", overflow: "hidden" }}>
      <Box sx={{ position: "absolute", right: -18, top: -18, width: 74, height: 74, borderRadius: 99, background: color || PURPLE, opacity: 0.12 }} />
      <Typography variant="caption" sx={{ color: MUT, fontWeight: 900, textTransform: "uppercase", letterSpacing: 0.6 }}>{label}</Typography>
      <Tooltip title={full || value}><Typography variant="h6" sx={{ mt: 0.5, fontWeight: 950, whiteSpace: "nowrap", color: INK }}>{value}</Typography></Tooltip>
      {sub && <Typography variant="caption" sx={{ color: MUT, fontWeight: 750 }}>{sub}</Typography>}
    </Paper>
  );
}

function RankBars({ title, rows, measure, currency = "BRL", limit = 8, color = PURPLE, unit }: {
  title: string; rows: Record<string, any>[]; measure: string; currency?: "BRL" | "USD";
  limit?: number; color?: string; unit?: string;
}) {
  const items = (rows || []).filter(r => r[measure] !== null && r[measure] !== undefined).slice(0, limit);
  if (!items.length) return <Paper variant="outlined" sx={{ p: 2, height: "100%" }}><Typography variant="subtitle2" fontWeight={900}>{title}</Typography><Typography variant="caption" color="text.secondary">Sem dados para o escopo.</Typography></Paper>;
  const max = Math.max(...items.map(r => Math.abs(Number(r[measure]) || 0)), 1);
  const total = items.reduce((s, r) => s + Math.abs(Number(r[measure]) || 0), 0);
  return (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Typography variant="subtitle2" fontWeight={900} mb={1}>{title}</Typography>
      <Stack spacing={1}>
        {items.map((r, i) => {
          const val = Number(r[measure]) || 0;
          const label = Object.keys(r).find(k => k !== measure && k !== "_billing_period") || "item";
          const w = Math.max(2, Math.min(100, Math.abs(val) / max * 100));
          return (
            <Box key={i}>
              <Stack direction="row" justifyContent="space-between" sx={{ fontSize: 12, fontWeight: 800 }}>
                <Tooltip title={esc(r[label])}><Typography variant="caption" sx={{ fontWeight: 850, maxWidth: "62%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{i + 1}. {esc(r[label])}</Typography></Tooltip>
                <Typography variant="caption" sx={{ fontWeight: 900, whiteSpace: "nowrap" }}>{unit ? `${num(val)} ${unit}` : money(val, currency)} · {pct(Math.abs(val) / (total || 1) * 100, 0)}</Typography>
              </Stack>
              <Box sx={{ height: 9, borderRadius: 99, background: "#e2e8f0", overflow: "hidden", mt: 0.4 }}>
                <Box sx={{ height: "100%", width: `${w}%`, borderRadius: 99, background: `linear-gradient(90deg, ${color}, ${color}cc)` }} />
              </Box>
            </Box>
          );
        })}
      </Stack>
    </Paper>
  );
}

function ChartCard({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Typography variant="subtitle2" fontWeight={900} mb={0.5}>{title}</Typography>
      {children}
      {note && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>{note}</Typography>}
    </Paper>
  );
}

function LoadingBlock({ loading, children }: { loading: boolean; children: React.ReactNode }) {
  if (loading) return <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}><CircularProgress size={26} /></Box>;
  return <>{children}</>;
}

function monthlySeries(r: DimonQueryResult | null | undefined, measure: string) {
  return rowsOf(r)
    .map(row => ({ month: String(row._billing_period || row.period || ""), value: Number(row[measure] ?? 0) }))
    .sort((a, b) => a.month.localeCompare(b.month));
}

// ─────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────
type TabId = "geral" | "evolucao" | "organizacao" | "servicos" | "investigacao" | "drilldown" | "tabela";

export default function DimonDashboard() {
  const [table, setTable] = useState(DEFAULT_TABLE);
  const [availableTables, setAvailableTables] = useState<string[]>([]);
  const [info, setInfo] = useState<DimonInfo | null>(null);
  const [tab, setTab] = useState<TabId>("geral");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [monthsAll, setMonthsAll] = useState<string[]>([]);
  const [periodPreset, setPeriodPreset] = useState("all");
  const [options, setOptions] = useState<Record<string, string[]>>({});
  const [error, setError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  // ── carrega tabelas disponíveis (namespace dimon) ──
  useEffect(() => {
    getAnalysisTables().then(t => {
      const dimonTables = t.dimon || [];
      setAvailableTables(dimonTables);
      if (dimonTables.length && !dimonTables.includes(table)) setTable(dimonTables.includes(DEFAULT_TABLE) ? DEFAULT_TABLE : dimonTables[0]);
    }).catch(() => setAvailableTables([]));
  }, []);

  // ── info da tabela ──
  useEffect(() => {
    let alive = true;
    setInfo(null);
    getDimonInfo(table).then(i => { if (alive) { setInfo(i); setMonthsAll(i.periods || []); } })
      .catch(e => { if (alive) setError(formatApiError(e, "Falha ao carregar info da Dimon")); });
    return () => { alive = false; };
  }, [table]);

  // ── opções de filtro (distinct por dimensão) ──
  useEffect(() => {
    if (!table) return;
    let alive = true;
    const dims = ["cloud_origem", "departamento_nivel_4", "departamento_nivel_5", "servicename", "servicecategory"];
    Promise.all(dims.map(d => queryDimonTable({ table, rows: [d], measures: [{ column: "realizado", aggregation: "SUM" }], row_limit: 500 }).catch(() => null)))
      .then(list => {
        if (!alive) return;
        const next: Record<string, string[]> = {};
        dims.forEach((d, i) => { next[d] = (list[i]?.rows ?? []).map(r => esc(r[d])).filter(v => v !== "Não informado"); });
        setOptions(next);
      });
    return () => { alive = false; };
  }, [table]);

  const applyPreset = (preset: string) => {
    setPeriodPreset(preset);
    if (preset === "all") { setFilters(f => ({ ...f, months: [] })); return; }
    const n = Number(preset.replace("last", ""));
    const sorted = [...(info?.periods || monthsAll)].sort().reverse();
    setFilters(f => ({ ...f, months: sorted.slice(0, n) }));
  };

  const filterKey = JSON.stringify([filters, tab, refreshTick]);
  const setFilter = (key: keyof Filters, values: string[]) => setFilters(f => ({ ...f, [key]: values }));

  const clearFilters = () => { setFilters(EMPTY_FILTERS); setPeriodPreset("all"); };

  const [copied, setCopied] = useState(false);
  const copyEmail = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };

  const exportRows = (rows: Record<string, any>[], filename: string) => {
    if (!rows.length) return;
    const headers = Object.keys(rows[0]);
    const lines = [headers.join(";"), ...rows.map(r => headers.map(h => `"${String(r[h] ?? "").replace(/"/g, '""')}"`).join(";"))];
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = filename; a.style.display = "none";
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2} mb={2}>
        <Box>
          <Typography variant="h4">FinOps Intelligence</Typography>
          <Typography color="text.secondary">Cockpit executivo pré-agregado (mensal) · Fonte: <b>{table}</b> · {info ? `${info.row_count.toLocaleString("pt-BR")} linhas agregadas · ${info.period_count} competências` : "carregando…"}</Typography>
          {!availableTables.includes(DEFAULT_TABLE) && (
            <Alert severity="info" sx={{ mt: 1, fontSize: 12 }}>
              A tabela <b>{DEFAULT_TABLE}</b> ainda não foi gerada. Ela é criada automaticamente ao reprocessar o destino <b>MODELO FOCUS</b>. Selecionando outra Dimon existente você vê o mesmo dashboard com os dados disponíveis.
            </Alert>
          )}
        </Box>
        <Stack direction="row" gap={1} alignItems="center">
          <FormControl size="small" sx={{ minWidth: 260 }}>
            <InputLabel>Tabela Dimon</InputLabel>
            <Select label="Tabela Dimon" value={table} onChange={e => setTable(e.target.value)}>
              {[...new Set([DEFAULT_TABLE, ...availableTables])].map(t => <MenuItem key={t} value={t}>{t}</MenuItem>)}
            </Select>
          </FormControl>
          <Button size="small" variant="outlined" startIcon={<Refresh />} onClick={() => setRefreshTick(t => t + 1)}>Atualizar</Button>
        </Stack>
      </Stack>
      </Box>
      <Box sx={{ p: 3 }}>
        <Paper elevation={0} sx={{ p: 2.5, borderRadius: 2, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {/* ── Filtros globais ── */}
      <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
        <Stack direction="row" flexWrap="wrap" gap={1.2} alignItems="center">
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Período</InputLabel>
            <Select label="Período" value={periodPreset} onChange={e => applyPreset(e.target.value)}>
              <MenuItem value="all">Todos os meses</MenuItem>
              <MenuItem value="last1">Último mês</MenuItem>
              <MenuItem value="last3">Últimos 3 meses</MenuItem>
              <MenuItem value="last6">Últimos 6 meses</MenuItem>
              <MenuItem value="last12">Últimos 12 meses</MenuItem>
            </Select>
          </FormControl>
          {(["cloud_origem", "departamento_nivel_4", "departamento_nivel_5", "servicename", "servicecategory"] as const).map(dim => (
            <FormControl key={dim} size="small" sx={{ minWidth: 180 }}>
              <InputLabel>{DIM_LABELS[dim] || dim}</InputLabel>
              <Select multiple label={DIM_LABELS[dim] || dim}
                value={filters[dim === "cloud_origem" ? "clouds" : dim === "departamento_nivel_4" ? "dirs" : dim === "departamento_nivel_5" ? "depts" : dim === "servicename" ? "servicos" : "cats"]}
                onChange={e => setFilter(dim === "cloud_origem" ? "clouds" : dim === "departamento_nivel_4" ? "dirs" : dim === "departamento_nivel_5" ? "depts" : dim === "servicename" ? "servicos" : "cats", e.target.value as string[])}
                renderValue={sel => sel.length ? `${sel.length} selecionado(s)` : "Todos"}>
                {(options[dim] || []).map(v => <MenuItem key={v} value={v}>{v}</MenuItem>)}
              </Select>
            </FormControl>
          ))}
          <Button size="small" startIcon={<FilterAltOff />} onClick={clearFilters}>Limpar</Button>
        </Stack>
        {filters.months.length > 0 && (
          <Stack direction="row" gap={0.5} flexWrap="wrap" mt={1}>
            {filters.months.map(m => <Chip key={m} size="small" label={monthLabel(m)} onDelete={() => setFilter("months", filters.months.filter(x => x !== m))} />)}
          </Stack>
        )}
      </Paper>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons="auto" sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}>
        <Tab value="geral" label="Visão Geral" />
        <Tab value="evolucao" label="Evolução" />
        <Tab value="organizacao" label="Organização" />
        <Tab value="servicos" label="Serviços & Regiões" />
        <Tab value="investigacao" label="Investigação" />
        <Tab value="drilldown" label="Drilldown" />
        <Tab value="tabela" label="Tabela & Export" />
      </Tabs>

      {tab === "geral" && <VisaoGeral table={table} filters={filters} filterKey={filterKey} />}
      {tab === "evolucao" && <Evolucao table={table} filters={filters} filterKey={filterKey} />}
      {tab === "organizacao" && <Organizacao table={table} filters={filters} filterKey={filterKey} />}
      {tab === "servicos" && <Servicos table={table} filters={filters} filterKey={filterKey} />}
      {tab === "investigacao" && <Investigacao table={table} filters={filters} filterKey={filterKey} />}
      {tab === "drilldown" && <Drilldown table={table} filters={filters} filterKey={filterKey} />}
      {tab === "tabela" && <TabelaTab table={table} filters={filters} filterKey={filterKey} exportRows={exportRows} setFilters={setFilters} options={options} periodPreset={periodPreset} applyPreset={applyPreset} />}
        </Paper>
      </Box>
    </Box>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 1 — Visão Geral (capa executiva + KPIs)
// ═══════════════════════════════════════════════════════════════════
function VisaoGeral({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const [copied, setCopied] = useState(false);
  const copyEmail = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };
  const defs = useMemo<QueryDef[]>(() => [
    { id: "totais", measures: [{ column: "realizado", aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }, { column: "*", aggregation: "COUNT" }], filters: filtersOf(filters) },
    { id: "mensal", rows: ["_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 60 },
    { id: "dirs", rows: ["departamento_nivel_4"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "clouds", rows: ["cloud_origem"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "servicos", rows: ["servicename"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "categorias", rows: ["servicecategory"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 8 },
  ], [filterKey]);
  const { results, loading } = useTabQueries(table, defs, filterKey);

  const totalRows = rowsOf(results.totais);
  const total = {
    realizado: sumOf(results.totais, "realizado"),
    billedcost: sumOf(results.totais, "billedcost"),
    quantity: sumOf(results.totais, "quantity"),
    count: sumOf(results.totais, "count"),
  };
  const mensal = monthlySeries(results.mensal, "realizado");
  const latest = mensal[mensal.length - 1];
  const previous = mensal[mensal.length - 2];
  const mom = latest && previous && previous.value ? ((latest.value - previous.value) / Math.abs(previous.value)) * 100 : null;

  const dirRows = rowsOf(results.dirs);
  const topDir = dirRows[0];
  const dirTotal = dirRows.reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0);
  const top5Share = dirTotal ? dirRows.slice(0, 5).reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0) / dirTotal * 100 : 0;
  const topCloud = rowsOf(results.clouds)[0];
  const topServico = rowsOf(results.servicos)[0];

  const health = Math.round(Math.max(0, Math.min(100,
    35 + (mom !== null && mom <= 5 ? 20 : mom !== null && mom <= 15 ? 10 : 5)
    + (top5Share > 0 && top5Share < 70 ? 20 : 8)
    + (total.count ? 15 : 0)
    + 10,
  )));
  const healthStatus = health >= 85 ? { label: "CONTROLADO", color: GREEN } : health >= 70 ? { label: "MONITORAR", color: YELLOW } : { label: "AÇÃO", color: RED };

  const email = useMemo(() => {
    const period = filters.months.length ? `competência(s) ${filters.months.map(monthLong).join(", ")}` : "todo o período disponível";
    return [
      `Assunto: Resumo executivo FinOps — ${period}`,
      "",
      `Realizado (BRL): ${moneyFull(total.realizado)} | Billed Cost (USD): ${moneyFull(total.billedcost, "USD")} | Quantidade: ${num(total.quantity)}`,
      `Mês mais recente (${latest ? monthLong(latest.month) : "—"}): ${latest ? moneyFull(latest.value) : "—"}${mom !== null ? ` (${mom >= 0 ? "+" : ""}${pct(mom)} vs mês anterior)` : ""}`,
      `Top diretoria: ${topDir ? esc(topDir.departamento_nivel_4) : "—"} (${topDir ? money(topDir.realizado) : "—"}) · Concentração top 5: ${pct(top5Share)}`,
      `Top cloud: ${topCloud ? esc(topCloud.cloud_origem) : "—"} · Top serviço: ${topServico ? esc(topServico.servicename) : "—"}`,
      `Health Score: ${health}/100 (${healthStatus.label}).`,
      "",
      "Gerado automaticamente pelo Dashboard FinOps (Dimon).",
    ].join("\n");
  }, [total, mensal, mom, topDir, topCloud, topServico, top5Share, health, healthStatus, filters.months, latest]);

  return (
    <LoadingBlock loading={loading}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Realizado (BRL)" value={money(total.realizado)} sub="Soma da medida realizado" color={PURPLE} full={moneyFull(total.realizado)} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Billed Cost (USD)" value={money(total.billedcost, "USD")} sub="Moeda de billing" color={ORANGE} full={moneyFull(total.billedcost, "USD")} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Quantidade" value={num(total.quantity)} sub="Soma de ConsumedQuantity" color={BLUE} full={total.quantity.toLocaleString("pt-BR")} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Linhas agregadas" value={num(total.count)} sub="Registros da Dimon" color={GREEN} full={total.count.toLocaleString("pt-BR")} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Health Score" value={`${health}/100`} sub={healthStatus.label} color={healthStatus.color} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="MoM último mês" value={mom === null ? "—" : `${mom >= 0 ? "+" : ""}${pct(mom)}`} sub={latest ? monthLabel(latest.month) : "sem histórico"} color={mom !== null && mom > 10 ? RED : GREEN} /></Grid>
      </Grid>

      <Grid container spacing={1.5}>
        <Grid item xs={12} md={7}>
          <ChartCard title="Evolução mensal — Realizado vs Billed Cost" note="Valores somados da Dimon para o escopo filtrado.">
            <Chart
              data={[
                { x: mensal.map(r => monthLabel(r.month)), y: mensal.map(r => r.value), type: "bar", name: "Realizado (BRL)", marker: { color: PURPLE } },
                { x: mensal.map(r => monthLabel(r.month)), y: monthlySeries(results.mensal, "billedcost").map(r => r.value), type: "scatter", mode: "lines+markers", name: "Billed Cost (USD)", yaxis: "y2", line: { color: ORANGE, width: 3 } },
              ]}
              layout={{ height: 320, margin: { l: 56, r: 56, t: 8, b: 34 }, barmode: "group", legend: { orientation: "h", y: -0.22 }, yaxis: { title: { text: "BRL" }, gridcolor: "#eef0f6" }, yaxis2: { title: { text: "USD" }, overlaying: "y", side: "right", showgrid: false } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <ChartCard title="Mix por Categoria de Carga">
            <Chart
              data={[{ labels: rowsOf(results.categorias).map(r => esc(r.servicecategory)), values: rowsOf(results.categorias).map(r => Number(r.realizado) || 0), type: "pie", hole: 0.55, marker: { colors: [PURPLE, ORANGE, BLUE, GREEN, YELLOW, RED, "#0f766e", "#7c3aed"] } }]}
              layout={{ height: 300, margin: { l: 8, r: 8, t: 8, b: 8 }, showlegend: true, legend: { orientation: "h", y: -0.25, font: { size: 10 } } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <RankBars title="Top Diretorias" rows={dirRows} measure="realizado" limit={8} color={PURPLE} />
        </Grid>
        <Grid item xs={12} md={3.5}>
          <RankBars title="Top Clouds" rows={rowsOf(results.clouds)} measure="realizado" limit={6} color={BLUE} />
        </Grid>
        <Grid item xs={12} md={3.5}>
          <RankBars title="Top Serviços" rows={rowsOf(results.servicos)} measure="realizado" limit={6} color={ORANGE} />
        </Grid>
        <Grid item xs={12}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
              <Typography variant="subtitle2" fontWeight={900}>E-mail executivo (auto-gerado)</Typography>
              <Button size="small" startIcon={<ContentCopy />} onClick={() => copyEmail(email)}>{copied ? "Copiado!" : "Copiar"}</Button>
            </Stack>
            <TextField value={email} multiline minRows={6} fullWidth size="small" sx={{ fontFamily: "monospace", fontSize: 12 }} />
          </Paper>
        </Grid>
      </Grid>
    </LoadingBlock>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 2 — Evolução (mensal + drivers + run rate)
// ═══════════════════════════════════════════════════════════════════
function Evolucao({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const [dim, setDim] = useState("cloud_origem");
  const defs = useMemo<QueryDef[]>(() => [
    { id: "mensal", rows: ["_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 60 },
    { id: "drivers", rows: [dim], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 400 },
  ], [filterKey, dim]);
  const { results, loading } = useTabQueries(table, defs, filterKey + dim);

  const mensal = monthlySeries(results.mensal, "realizado");
  const billed = monthlySeries(results.mensal, "billedcost");
  const qty = monthlySeries(results.mensal, "quantity");

  // Waterfall MoM: últimos 2 meses por dimensão
  const drivers = rowsOf(results.drivers);
  const monthsPresent = [...new Set(drivers.map(r => String(r._billing_period)))].sort();
  const lastTwo = monthsPresent.slice(-2);
  const byDim = new Map<string, Record<string, number>>();
  drivers.forEach(r => {
    const key = esc(r[dim]);
    if (!byDim.has(key)) byDim.set(key, {});
    byDim.get(key)![String(r._billing_period)] = Number(r.realizado) || 0;
  });
  const cur = lastTwo[1], prev = lastTwo[0];
  const deltas = [...byDim.entries()].map(([key, m]) => ({
    key, current: m[cur] || 0, previous: m[prev] || 0, delta: (m[cur] || 0) - (m[prev] || 0),
  })).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const curTotal = deltas.reduce((s, d) => s + d.current, 0);
  const prevTotal = deltas.reduce((s, d) => s + d.previous, 0);
  const totalDelta = curTotal - prevTotal;

  const runRateAvg = mensal.length ? mensal.reduce((s, r) => s + r.value, 0) / mensal.length : 0;
  const annualized = runRateAvg * 12;
  const last3 = mensal.slice(-3).reduce((s, r) => s + r.value, 0) / Math.max(1, Math.min(3, mensal.length));
  const projected = annualized + (last3 - runRateAvg) * 12;

  return (
    <LoadingBlock loading={loading}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={6} md={3}><KpiCard label="Média mensal (BRL)" value={money(runRateAvg)} full={moneyFull(runRateAvg)} color={PURPLE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Run Rate anualizado" value={money(annualized)} full={moneyFull(annualized)} color={BLUE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Projeção anual (ritmo 3m)" value={money(projected)} full={moneyFull(projected)} color={ORANGE} sub="Média 3m × 12" /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Variação último mês" value={`${totalDelta >= 0 ? "+" : ""}${money(totalDelta)}`} sub={`${cur ? monthLabel(cur) : "—"} vs ${prev ? monthLabel(prev) : "—"}`} color={totalDelta > 0 ? RED : GREEN} /></Grid>
      </Grid>

      <Grid container spacing={1.5}>
        <Grid item xs={12}>
          <ChartCard title="Realizado (BRL), Billed Cost (USD) e Quantidade por mês">
            <Chart
              data={[
                { x: mensal.map(r => monthLabel(r.month)), y: mensal.map(r => r.value), type: "bar", name: "Realizado (BRL)", marker: { color: PURPLE } },
                { x: billed.map(r => monthLabel(r.month)), y: billed.map(r => r.value), type: "scatter", mode: "lines", name: "Billed Cost (USD)", line: { color: ORANGE, width: 3 } },
                { x: qty.map(r => monthLabel(r.month)), y: qty.map(r => r.value), type: "scatter", mode: "lines+markers", name: "Quantidade", yaxis: "y2", line: { color: GREEN, width: 2, dash: "dot" } },
              ]}
              layout={{ height: 340, margin: { l: 56, r: 56, t: 8, b: 34 }, legend: { orientation: "h", y: -0.22 }, yaxis: { title: { text: "Valor" }, gridcolor: "#eef0f6" }, yaxis2: { title: { text: "Quantidade" }, overlaying: "y", side: "right", showgrid: false } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={7}>
          <ChartCard title={`What Changed? — variação ${cur ? monthLabel(cur) : "—"} vs ${prev ? monthLabel(prev) : "—"} por ${DIM_LABELS[dim] || dim}`} note="Maiores contribuintes absolutos da variação. Não presume causalidade operacional.">
            <FormControl size="small" sx={{ mb: 1, minWidth: 180 }}>
              <Select value={dim} onChange={e => setDim(e.target.value)}>
                {["cloud_origem", "departamento_nivel_4", "departamento_nivel_5", "servicename", "servicecategory", "regionname", "subaccountname"].map(d => <MenuItem key={d} value={d}>{DIM_LABELS[d] || d}</MenuItem>)}
              </Select>
            </FormControl>
            <Chart
              data={[{
                x: deltas.slice(0, 12).map(d => d.key.length > 34 ? d.key.slice(0, 33) + "…" : d.key),
                y: deltas.slice(0, 12).map(d => d.delta),
                type: "bar",
                marker: { color: deltas.slice(0, 12).map(d => d.delta >= 0 ? RED : GREEN) },
                text: deltas.slice(0, 12).map(d => `${d.delta >= 0 ? "+" : ""}${money(d.delta)}`),
                textposition: "outside",
              }]}
              layout={{ height: 320, margin: { l: 56, r: 24, t: 8, b: 90 }, xaxis: { tickangle: -35 }, yaxis: { title: { text: "Δ BRL" }, gridcolor: "#eef0f6" } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <ChartCard title="Contribuição para a variação" note={`Variação total: ${totalDelta >= 0 ? "+" : ""}${money(totalDelta)} (${prevTotal ? pct(totalDelta / Math.abs(prevTotal) * 100) : "—"} vs mês anterior)`}>
            <Stack spacing={1}>
              {deltas.slice(0, 10).map((d, i) => (
                <Box key={i}>
                  <Stack direction="row" justifyContent="space-between" sx={{ fontSize: 12, fontWeight: 800 }}>
                    <Tooltip title={d.key}><Typography variant="caption" sx={{ fontWeight: 850, maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.key}</Typography></Tooltip>
                    <Typography variant="caption" sx={{ fontWeight: 900 }}>{d.delta >= 0 ? "+" : ""}{money(d.delta)} · {totalDelta ? pct(Math.abs(d.delta) / Math.abs(totalDelta) * 100, 0) : "—"} do Δ</Typography>
                  </Stack>
                  <Box sx={{ height: 9, borderRadius: 99, background: "#e2e8f0", overflow: "hidden", mt: 0.4 }}>
                    <Box sx={{ height: "100%", width: `${Math.max(2, Math.min(100, Math.abs(d.delta) / (Math.max(...deltas.slice(0, 10).map(x => Math.abs(x.delta)), 1)) * 100))}%`, borderRadius: 99, background: d.delta >= 0 ? RED : GREEN }} />
                  </Box>
                </Box>
              ))}
              {!deltas.length && <Typography variant="caption" color="text.secondary">Sem dois meses consecutivos para comparar.</Typography>}
            </Stack>
          </ChartCard>
        </Grid>
      </Grid>
    </LoadingBlock>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 3 — Organização (rankings + hierarquia + governança)
// ═══════════════════════════════════════════════════════════════════
function Organizacao({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const defs = useMemo<QueryDef[]>(() => [
    { id: "n3", rows: ["departamento_nivel_3"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "n4", rows: ["departamento_nivel_4"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "n5", rows: ["departamento_nivel_5"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "dept", rows: ["departamento"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "cc", rows: ["centro_custo"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "cloud", rows: ["cloud_origem"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "hier", rows: ["departamento_nivel_1", "departamento_nivel_2", "departamento_nivel_3", "departamento_nivel_4", "departamento_nivel_5"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 200 },
    { id: "gov", rows: ["departamento_nivel_4"], measures: [{ column: "realizado", aggregation: "SUM" }, { column: "*", aggregation: "COUNT" }], filters: filtersOf(filters), row_limit: 100 },
  ], [filterKey]);
  const { results, loading } = useTabQueries(table, defs, filterKey);

  const hierRows = rowsOf(results.hier);
  const govRows = rowsOf(results.gov);
  const govTotal = govRows.reduce((s, r) => s + Number(r.realizado || 0), 0);
  const govClassified = govRows.filter(r => r.departamento_nivel_4).reduce((s, r) => s + Number(r.realizado || 0), 0);
  const govShare = govTotal ? govClassified / govTotal * 100 : 0;

  return (
    <LoadingBlock loading={loading}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={6} md={3}><KpiCard label="Governança (Diretoria)" value={pct(govShare)} sub="% do Realizado classificado" color={govShare >= 90 ? GREEN : govShare >= 70 ? YELLOW : RED} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Diretorias com custo" value={String(govRows.length)} color={PURPLE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Níveis hierárquicos" value={hierRows.length ? "5" : "—"} sub="nivel_1 .. nivel_5" color={BLUE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Concentração Top 5 Dir." value={govRows.length ? pct(govRows.slice(0, 5).reduce((s, r) => s + Number(r.realizado || 0), 0) / (govTotal || 1) * 100) : "—"} color={ORANGE} /></Grid>
      </Grid>
      <Grid container spacing={1.5}>
        <Grid item xs={12} md={4}><RankBars title="VP (Nível 3)" rows={rowsOf(results.n3)} measure="realizado" limit={10} color={PURPLE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Diretorias (Nível 4)" rows={rowsOf(results.n4)} measure="realizado" limit={12} color={BLUE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Departamentos (Nível 5)" rows={rowsOf(results.n5)} measure="realizado" limit={12} color={GREEN} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Áreas (departamento)" rows={rowsOf(results.dept)} measure="realizado" limit={12} color={ORANGE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Centros de Custo" rows={rowsOf(results.cc)} measure="realizado" limit={10} color={YELLOW} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Clouds" rows={rowsOf(results.cloud)} measure="realizado" limit={10} color="#7c3aed" /></Grid>
        <Grid item xs={12}>
          <ChartCard title="Hierarquia organizacional — custo por caminho (níveis 1→5)" note="Top 200 caminhos por Realizado; use os filtros globais para estreitar.">
            <TableContainer sx={{ maxHeight: 460 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    {["Nível 1", "Nível 2", "Nível 3 (VP)", "Nível 4 (Diretoria)", "Nível 5 (Departamento)", "Realizado"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {hierRows.map((r, i) => (
                    <TableRow key={i} hover>
                      <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_1)}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_2)}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_3)}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_4)}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_5)}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900, whiteSpace: "nowrap" }}>{money(r.realizado)}</TableCell>
                    </TableRow>
                  ))}
                  {!hierRows.length && <TableRow><TableCell colSpan={6} align="center" sx={{ color: "text.secondary" }}>Sem dados para o escopo.</TableCell></TableRow>}
                </TableBody>
              </Table>
            </TableContainer>
          </ChartCard>
        </Grid>
      </Grid>
    </LoadingBlock>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 4 — Serviços & Regiões
// ═══════════════════════════════════════════════════════════════════
function Servicos({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const [heatDim, setHeatDim] = useState("departamento_nivel_4");
  const defs = useMemo<QueryDef[]>(() => [
    { id: "serv", rows: ["servicename"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "cat", rows: ["servicecategory"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "sub", rows: ["servicesubcategory"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "sku", rows: ["skumeter"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "reg", rows: ["regionname"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "subacct", rows: ["subaccountname"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "charge", rows: ["chargecategory"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 8 },
    { id: "heat", rows: [heatDim, "_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 800 },
  ], [filterKey, heatDim]);
  const { results, loading } = useTabQueries(table, defs, filterKey + heatDim);

  const heatRows = rowsOf(results.heat);
  const heatDims = [...new Set(heatRows.map(r => esc(r[heatDim])))].slice(0, 14);
  const heatMonths = [...new Set(heatRows.map(r => String(r._billing_period)))].sort();
  const z = heatDims.map(d => heatMonths.map(m => {
    const hit = heatRows.find(r => esc(r[heatDim]) === d && String(r._billing_period) === m);
    return hit ? Number(hit.realizado) || 0 : null;
  }));
  const maxHeat = Math.max(...z.flat().filter(v => v !== null).map(Number), 1);

  return (
    <LoadingBlock loading={loading}>
      <Grid container spacing={1.5}>
        <Grid item xs={12} md={4}><RankBars title="Top Serviços" rows={rowsOf(results.serv)} measure="realizado" limit={12} color={PURPLE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Top Categorias" rows={rowsOf(results.cat)} measure="realizado" limit={12} color={BLUE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Top Subcategorias" rows={rowsOf(results.sub)} measure="realizado" limit={12} color={GREEN} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Top SKUs (skumeter)" rows={rowsOf(results.sku)} measure="realizado" limit={12} color={ORANGE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Top Regiões" rows={rowsOf(results.reg)} measure="realizado" limit={12} color={YELLOW} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Top Subcontas" rows={rowsOf(results.subacct)} measure="realizado" limit={10} color="#7c3aed" /></Grid>
        <Grid item xs={12} md={4}>
          <ChartCard title="Mix por Tipo de Carga">
            <Chart
              data={[{ labels: rowsOf(results.charge).map(r => esc(r.chargecategory)), values: rowsOf(results.charge).map(r => Number(r.realizado) || 0), type: "pie", hole: 0.5, marker: { colors: [PURPLE, ORANGE, BLUE, GREEN, YELLOW, RED, "#0f766e"] } }]}
              layout={{ height: 320, margin: { l: 8, r: 8, t: 8, b: 8 }, legend: { orientation: "h", y: -0.3, font: { size: 10 } } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={8}>
          <ChartCard title={`Heatmap ${DIM_LABELS[heatDim] || heatDim} × Mês — Realizado (BRL)`} note="Top 14 valores da dimensão; célula = soma mensal no escopo filtrado.">
            <FormControl size="small" sx={{ mb: 1, minWidth: 180 }}>
              <Select value={heatDim} onChange={e => setHeatDim(e.target.value)}>
                {["departamento_nivel_4", "cloud_origem", "servicename", "servicecategory", "regionname", "subaccountname"].map(d => <MenuItem key={d} value={d}>{DIM_LABELS[d] || d}</MenuItem>)}
              </Select>
            </FormControl>
            <Chart
              data={[{ z, x: heatMonths.map(monthLabel), y: heatDims, type: "heatmap", colorscale: [[0, "#f3e8ff"], [0.5, PURPLE], [1, "#2b003d"]], hoverongaps: false, text: (z.map(row => row.map(v => v === null ? "sem dados" : money(v))) as string[][]) as any, texttemplate: "%{text}", hovertemplate: "%{y} · %{x}<br>%{text}<extra></extra>" }]}
              layout={{ height: 380, margin: { l: 130, r: 16, t: 8, b: 40 }, xaxis: { tickangle: -35 } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
      </Grid>
    </LoadingBlock>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 5 — Investigação (attention, desvios, anomalias, pareto, aceleração)
// ═══════════════════════════════════════════════════════════════════
function Investigacao({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const [dim, setDim] = useState("servicename");
  const defs = useMemo<QueryDef[]>(() => [
    { id: "serie", rows: [dim, "_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 2000 },
    { id: "cloud", rows: ["cloud_origem", "_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 400 },
    { id: "dirs", rows: ["departamento_nivel_4", "_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 600 },
    { id: "pareto", rows: [dim], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 300 },
  ], [filterKey, dim]);
  const { results, loading } = useTabQueries(table, defs, filterKey + dim);

  // Séries mensais por item da dimensão
  const series = useMemo(() => {
    const map = new Map<string, Map<string, number>>();
    rowsOf(results.serie).forEach(r => {
      const key = esc(r[dim]);
      if (!map.has(key)) map.set(key, new Map());
      map.get(key)!.set(String(r._billing_period), Number(r.realizado) || 0);
    });
    return [...map.entries()].map(([key, months]) => {
      const keys = [...months.keys()].sort();
      const values = keys.map(k => months.get(k) || 0);
      const last = values[values.length - 1] || 0;
      const prev = values[values.length - 2] || 0;
      const delta = last - prev;
      const mom = prev ? (delta / Math.abs(prev)) * 100 : null;
      const hist = values.slice(0, -1);
      const mean = hist.length ? hist.reduce((a, b) => a + b, 0) / hist.length : 0;
      const sd = hist.length > 1 ? Math.sqrt(hist.reduce((a, b) => a + (b - mean) ** 2, 0) / hist.length) : 0;
      const z = sd ? (last - mean) / sd : 0;
      const accel = values.length >= 3 ? (values[values.length - 1] - values[values.length - 2]) - (values[values.length - 2] - values[values.length - 3]) : null;
      return { key, last, prev, delta, mom, mean, sd, z, accel, values, keys };
    }).filter(x => x.last !== 0 || x.prev !== 0);
  }, [results.serie, dim]);

  const sortedByAbs = [...series].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 12);
  const topGrow = [...series].filter(x => x.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 8);
  const anomalies = [...series].filter(x => Math.abs(x.z) >= 2).sort((a, b) => Math.abs(b.z) - Math.abs(a.z)).slice(0, 8);
  const accelerated = [...series].filter(x => x.accel !== null && x.accel > 0).sort((a, b) => b.accel! - a.accel!).slice(0, 8);

  // Pareto
  const paretoRows = rowsOf(results.pareto);
  const paretoTotal = paretoRows.reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0);
  let cum = 0, paretoCount = 0;
  for (const r of paretoRows) {
    if (paretoTotal && cum / paretoTotal >= 0.8) break;
    cum += Math.abs(Number(r.realizado) || 0);
    paretoCount++;
  }

  // Attention items
  const attention = useMemo(() => {
    const items: Array<{ sev: "P1" | "P2" | "P3"; title: string; detail: string; color: string }> = [];
    if (topGrow[0] && topGrow[0].mom !== null && Math.abs(topGrow[0].mom) >= 15) {
      items.push({ sev: Math.abs(topGrow[0].mom) >= 25 ? "P1" : "P2", title: `Crescimento material: ${topGrow[0].key}`, detail: `${DIM_LABELS[dim] || dim} cresceu ${pct(topGrow[0].mom)} no último mês (${money(topGrow[0].delta)}).`, color: Math.abs(topGrow[0].mom) >= 25 ? RED : YELLOW });
    }
    if (anomalies[0]) {
      items.push({ sev: Math.abs(anomalies[0].z) >= 3 ? "P1" : "P2", title: `Anomalia estatística: ${anomalies[0].key}`, detail: `Z-score ${anomalies[0].z.toFixed(1)} vs histórico (último mês ${money(anomalies[0].last)}).`, color: RED });
    }
    if (accelerated[0]) {
      items.push({ sev: "P2", title: `Aceleração de custo: ${accelerated[0].key}`, detail: `Aceleração de ${money(accelerated[0].accel!)} entre os últimos 3 meses.`, color: YELLOW });
    }
    const dirSeries = rowsOf(results.dirs);
    const dirTotal = dirSeries.reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0);
    if (dirTotal) {
      const top5 = dirSeries.slice(0, 5).reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0);
      const conc = top5 / dirTotal * 100;
      if (conc > 70) items.push({ sev: "P2", title: "Concentração financeira", detail: `Top 5 diretorias respondem por ${pct(conc)} do custo.`, color: YELLOW });
    }
    if (paretoCount > 0 && paretoCount <= Math.max(8, Math.round(paretoRows.length * 0.15))) {
      items.push({ sev: "P3", title: "Pareto 80/20", detail: `${paretoCount} de ${paretoRows.length} itens de ${DIM_LABELS[dim] || dim} respondem por 80% do custo.`, color: GREEN });
    }
    return items.sort((a, b) => a.sev.localeCompare(b.sev)).slice(0, 10);
  }, [topGrow, anomalies, accelerated, results.dirs, paretoCount, paretoRows.length, dim]);

  return (
    <LoadingBlock loading={loading}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={12} md={4}>
          <Paper variant="outlined" sx={{ p: 2, height: "100%", background: "linear-gradient(135deg,#2b003d,#660099)", color: "#fff" }}>
            <Typography variant="subtitle2" fontWeight={900} mb={1}>Executive Attention Center</Typography>
            {attention.length === 0 && <Typography variant="caption">Nenhum sinal relevante no escopo filtrado.</Typography>}
            <Stack spacing={1}>
              {attention.map((a, i) => (
                <Box key={i} sx={{ borderLeft: `5px solid ${a.color}`, background: "rgba(255,255,255,.12)", borderRadius: 1.5, p: 1.2 }}>
                  <Typography variant="caption" sx={{ fontWeight: 950, display: "flex", alignItems: "center", gap: 0.6 }}>
                    <Chip size="small" label={a.sev} sx={{ background: a.color, color: "#fff", fontWeight: 950, height: 18, fontSize: 10 }} /> {a.title}
                  </Typography>
                  <Typography variant="caption" sx={{ color: "#ede9fe", display: "block", mt: 0.3 }}>{a.detail}</Typography>
                </Box>
              ))}
            </Stack>
          </Paper>
        </Grid>
        <Grid item xs={12} md={8}>
          <ChartCard title={`Top Desvios MoM por ${DIM_LABELS[dim] || dim}`} note="Variação absoluta do último mês vs anterior.">
            <FormControl size="small" sx={{ mb: 1, minWidth: 180 }}>
              <Select value={dim} onChange={e => setDim(e.target.value)}>
                {["servicename", "cloud_origem", "departamento_nivel_4", "departamento_nivel_5", "servicecategory", "regionname", "subaccountname"].map(d => <MenuItem key={d} value={d}>{DIM_LABELS[d] || d}</MenuItem>)}
              </Select>
            </FormControl>
            <Chart
              data={[{
                x: sortedByAbs.map(d => d.key.length > 34 ? d.key.slice(0, 33) + "…" : d.key),
                y: sortedByAbs.map(d => d.delta),
                type: "bar",
                marker: { color: sortedByAbs.map(d => d.delta >= 0 ? RED : GREEN) },
                text: sortedByAbs.map(d => `${d.delta >= 0 ? "+" : ""}${money(d.delta)}`),
                textposition: "outside",
              }]}
              layout={{ height: 320, margin: { l: 56, r: 24, t: 8, b: 100 }, xaxis: { tickangle: -40 }, yaxis: { title: { text: "Δ BRL" }, gridcolor: "#eef0f6" } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={6}>
          <ChartCard title="Anomalias (z-score do último mês vs histórico)" note="|z| ≥ 2 indica variação fora do padrão histórico do item.">
            <TableContainer>
              <Table size="small">
                <TableHead><TableRow>{["Item", "Último mês", "Histórico (média)", "Z-score", "Status"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}</TableRow></TableHead>
                <TableBody>
                  {anomalies.map((a, i) => (
                    <TableRow key={i} hover>
                      <TableCell sx={{ fontSize: 12 }}>{esc(a.key)}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{money(a.last)}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{money(a.mean)}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900, color: Math.abs(a.z) >= 3 ? RED : YELLOW }}>{a.z.toFixed(2)}</TableCell>
                      <TableCell><Chip size="small" label={Math.abs(a.z) >= 3 ? "Crítico" : "Atenção"} sx={{ background: Math.abs(a.z) >= 3 ? RED : YELLOW, color: "#fff", fontSize: 10, fontWeight: 900 }} /></TableCell>
                    </TableRow>
                  ))}
                  {!anomalies.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary" }}>Nenhuma anomalia detectada (ou histórico insuficiente).</TableCell></TableRow>}
                </TableBody>
              </Table>
            </TableContainer>
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={6}>
          <Grid container spacing={1.5}>
            <Grid item xs={12}>
              <ChartCard title={`Pareto 80/20 — ${DIM_LABELS[dim] || dim}`}>
                <Chart
                  data={[
                    { x: paretoRows.slice(0, 30).map(r => esc(r[dim])), y: paretoRows.slice(0, 30).map(r => Number(r.realizado) || 0), type: "bar", name: "Realizado", marker: { color: PURPLE } },
                  ]}
                  layout={{ height: 260, margin: { l: 56, r: 20, t: 8, b: 100 }, xaxis: { tickangle: -45, tickfont: { size: 9 } }, yaxis: { title: { text: "BRL" }, gridcolor: "#eef0f6" } }}
                  config={{ displayModeBar: false }} style={{ width: "100%" }} />
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  <b>{paretoCount}</b> de {paretoRows.length} itens concentram ≥ 80% do custo ({pct(paretoTotal ? cum / paretoTotal * 100 : 0, 0)} acumulado).
                </Typography>
              </ChartCard>
            </Grid>
            <Grid item xs={12}>
              <ChartCard title="Aceleração de custo (últimos 3 meses)" note="Diferença das diferenças mensais — aceleração real de crescimento.">
                <Stack spacing={1}>
                  {accelerated.slice(0, 6).map((a, i) => (
                    <Stack key={i} direction="row" justifyContent="space-between" sx={{ fontSize: 12, fontWeight: 800 }}>
                      <Typography variant="caption" sx={{ fontWeight: 850, maxWidth: "70%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{esc(a.key)}</Typography>
                      <Typography variant="caption" sx={{ fontWeight: 900, color: RED }}>+{money(a.accel!)}</Typography>
                    </Stack>
                  ))}
                  {!accelerated.length && <Typography variant="caption" color="text.secondary">Sem aceleração relevante no escopo.</Typography>}
                </Stack>
              </ChartCard>
            </Grid>
          </Grid>
        </Grid>
      </Grid>
    </LoadingBlock>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 6 — Drilldown (navegação hierárquica)
// ═══════════════════════════════════════════════════════════════════
const DRILL_CHAIN: Array<{ field: string; label: string }> = [
  { field: "cloud_origem", label: "Cloud" },
  { field: "departamento_nivel_4", label: "Diretoria" },
  { field: "departamento_nivel_5", label: "Departamento" },
  { field: "servicename", label: "Serviço" },
  { field: "servicecategory", label: "Categoria" },
  { field: "regionname", label: "Região" },
  { field: "subaccountname", label: "Subconta" },
];

function Drilldown({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const [path, setPath] = useState<Array<{ field: string; value: string }>>([]);
  const level = path.length;
  const next = DRILL_CHAIN[level];

  const pathFilters = useMemo<DimonQueryFilter[]>(() => path.map(p => ({ column: p.field, operator: "IN", values: [p.value] })), [path]);

  const defs = useMemo<QueryDef[]>(() => {
    if (!next) return [];
    const filtersLocal = filtersOf(filters, pathFilters);
    return [
      { id: "children", rows: [next.field], measures: [{ column: "realizado", aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }], filters: filtersLocal, row_limit: 300 },
      { id: "months", rows: ["_billing_period"], measures: [{ column: "realizado", aggregation: "SUM" }], filters: filtersLocal, row_limit: 60 },
    ];
  }, [filterKey, path, next]);
  const { results, loading } = useTabQueries(table, defs, filterKey + JSON.stringify(path));

  const children = rowsOf(results.children);
  const childTotal = children.reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0);
  const top5Share = childTotal ? children.slice(0, 5).reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0) / childTotal * 100 : 0;
  const months = monthlySeries(results.months, "realizado");

  return (
    <Grid container spacing={1.5}>
      <Grid item xs={12}>
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Stack direction="row" alignItems="center" gap={0.6} flexWrap="wrap">
            <Chip label="Portfólio" size="small" onClick={() => setPath([])} color={level === 0 ? "primary" : "default"} />
            {path.map((p, i) => (
              <Box key={i} display="flex" alignItems="center" gap={0.6}>
                <Typography sx={{ color: MUT, fontWeight: 950 }}>→</Typography>
                <Chip size="small" label={`${DRILL_CHAIN[i].label}: ${p.value}`} color={i === path.length - 1 ? "primary" : "default"} onDelete={() => setPath(path.slice(0, i))} />
              </Box>
            ))}
            {level > 0 && <Button size="small" onClick={() => setPath(path.slice(0, -1))}>Voltar</Button>}
          </Stack>
        </Paper>
      </Grid>
      {!next ? (
        <Grid item xs={12}>
          <Alert severity="info">Drilldown completo — fim da cadeia. Use os filtros globais ou a aba Tabela para auditar.</Alert>
        </Grid>
      ) : (
        <>
          <Grid item xs={12} md={7}>
            <LoadingBlock loading={loading}>
              <RankBars title={`${DRILL_CHAIN[level].label} — filhos (Top 20)`} rows={children} measure="realizado" limit={20} color={PURPLE} />
            </LoadingBlock>
          </Grid>
          <Grid item xs={12} md={5}>
            <LoadingBlock loading={loading}>
              <ChartCard title="Evolução do escopo selecionado">
                <Chart
                  data={[{ x: months.map(r => monthLabel(r.month)), y: months.map(r => r.value), type: "bar", marker: { color: BLUE } }]}
                  layout={{ height: 280, margin: { l: 56, r: 20, t: 8, b: 34 }, yaxis: { title: { text: "BRL" }, gridcolor: "#eef0f6" } }}
                  config={{ displayModeBar: false }} style={{ width: "100%" }} />
                <Stack direction="row" spacing={2} mt={1}>
                  <Typography variant="caption" fontWeight={900}>Total: {money(childTotal)}</Typography>
                  <Typography variant="caption" fontWeight={900}>Top 5: {pct(top5Share)}</Typography>
                  <Typography variant="caption" fontWeight={900}>Itens: {children.length}</Typography>
                </Stack>
              </ChartCard>
            </LoadingBlock>
          </Grid>
          <Grid item xs={12}>
            <LoadingBlock loading={loading}>
              <TableContainer>
                <Table size="small">
                  <TableHead><TableRow>
                    {[DRILL_CHAIN[level].label, "Realizado", "Billed Cost", "Quantidade", "% do nível"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
                  </TableRow></TableHead>
                  <TableBody>
                    {children.slice(0, 30).map((r, i) => {
                      const share = childTotal ? Math.abs(Number(r.realizado) || 0) / childTotal * 100 : 0;
                      return (
                        <TableRow key={i} hover sx={{ cursor: "pointer" }} onClick={() => setPath([...path, { field: next.field, value: esc(r[next.field]) }])}>
                          <TableCell sx={{ fontSize: 12 }}>{esc(r[next.field])}</TableCell>
                          <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{money(r.realizado)}</TableCell>
                          <TableCell sx={{ fontSize: 12 }}>{money(r.billedcost, "USD")}</TableCell>
                          <TableCell sx={{ fontSize: 12 }}>{num(r.quantity)}</TableCell>
                          <TableCell sx={{ fontSize: 12 }}>{pct(share)}</TableCell>
                        </TableRow>
                      );
                    })}
                    {!children.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary" }}>Sem dados para o escopo.</TableCell></TableRow>}
                  </TableBody>
                </Table>
              </TableContainer>
            </LoadingBlock>
          </Grid>
        </>
      )}
    </Grid>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Aba 7 — Tabela & Export
// ═══════════════════════════════════════════════════════════════════
function TabelaTab({ table, filters, filterKey, exportRows, setFilters, options, periodPreset, applyPreset }: {
  table: string; filters: Filters; filterKey: string;
  exportRows: (rows: Record<string, any>[], filename: string) => void;
  setFilters: React.Dispatch<React.SetStateAction<Filters>>;
  options: Record<string, string[]>;
  periodPreset: string;
  applyPreset: (p: string) => void;
}) {
  const [query, setQuery] = useState("");
  const defs = useMemo<QueryDef[]>(() => [
    { id: "grid", rows: ["_billing_period", "cloud_origem", "departamento_nivel_4", "departamento_nivel_5", "servicename", "servicecategory", "regionname"], measures: [{ column: "quantity", aggregation: "SUM" }, { column: "realizado", aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 500 },
  ], [filterKey]);
  const { results, loading } = useTabQueries(table, defs, filterKey);

  const rows = useMemo(() => {
    const all = rowsOf(results.grid);
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter(r => Object.values(r).some(v => String(v ?? "").toLowerCase().includes(q)));
  }, [results.grid, query]);

  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1} mb={1.5}>
        <Stack direction="row" gap={1} alignItems="center">
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Período</InputLabel>
            <Select label="Período" value={periodPreset} onChange={e => applyPreset(e.target.value)}>
              <MenuItem value="all">Todos os meses</MenuItem>
              <MenuItem value="last1">Último mês</MenuItem>
              <MenuItem value="last3">Últimos 3 meses</MenuItem>
              <MenuItem value="last6">Últimos 6 meses</MenuItem>
              <MenuItem value="last12">Últimos 12 meses</MenuItem>
            </Select>
          </FormControl>
          <TextField size="small" placeholder="Buscar na tabela…" value={query} onChange={e => setQuery(e.target.value)} InputProps={{ startAdornment: <Search fontSize="small" sx={{ mr: 0.5, color: MUT }} /> }} />
        </Stack>
        <Button size="small" variant="contained" startIcon={<FileDownload />} onClick={() => exportRows(rows, `dimon_${table}.csv`)}>Exportar CSV</Button>
      </Stack>
      <LoadingBlock loading={loading}>
        <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 560 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                {["Mês", "Cloud", "Diretoria", "Departamento", "Serviço", "Categoria", "Região", "Quantidade", "Realizado", "Billed Cost"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11, whiteSpace: "nowrap" }}>{h}</TableCell>)}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r, i) => (
                <TableRow key={i} hover>
                  <TableCell sx={{ fontSize: 12 }}>{monthLabel(String(r._billing_period))}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{esc(r.cloud_origem)}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_4)}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{esc(r.departamento_nivel_5)}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{esc(r.servicename)}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{esc(r.servicecategory)}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{esc(r.regionname)}</TableCell>
                  <TableCell sx={{ fontSize: 12, whiteSpace: "nowrap" }}>{num(r.quantity)}</TableCell>
                  <TableCell sx={{ fontSize: 12, fontWeight: 900, whiteSpace: "nowrap" }}>{money(r.realizado)}</TableCell>
                  <TableCell sx={{ fontSize: 12, whiteSpace: "nowrap" }}>{money(r.billedcost, "USD")}</TableCell>
                </TableRow>
              ))}
              {!rows.length && <TableRow><TableCell colSpan={10} align="center" sx={{ color: "text.secondary", py: 3 }}>Sem dados para o escopo filtrado.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </TableContainer>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>Exibindo {rows.length} de {results.grid?.row_count ?? 0} linhas (top 500 por Realizado). A Dimon já é pré-agregada mensal — aqui você audita a granularidade agregada.</Typography>
      </LoadingBlock>
    </Box>
  );
}
