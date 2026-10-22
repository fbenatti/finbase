import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Box, Typography, Paper, Grid, Chip, Alert, CircularProgress, Button,
  FormControl, InputLabel, Select, MenuItem, Stack, TextField, Autocomplete, Popover,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Divider, Tooltip, LinearProgress, Menu, List, ListItemButton, ListItemText, Collapse, IconButton,
  FormControlLabel, Switch,
} from "@mui/material";
import { RefreshOutlined, ContentCopyOutlined, FilterAltOffOutlined, ExpandMoreOutlined, ChevronRightOutlined, BusinessOutlined, FileDownloadOutlined, CalendarMonthOutlined, AddOutlined, CloseOutlined } from "@mui/icons-material";
import Plot from "@/components/Plot";
// Wrapper: react-plotly.js Data/DataTitle typings são excessivamente rígidos
// para traces com name/text customizados; mantém o restante tipado.
function Chart(props: any) {
  return <Plot {...props} />;
}
import {
  queryDimonTable, getDimonInfo, getAnalysisTables, getDepartments,
  GoldQueryFilter, GoldQueryMeasure, GoldQueryResult, GoldInfo,
  formatApiError, Department, getGoldFilterOptions,
  getForecastAccuracyMonths, runForecastHistoryPivot,
  ForecastAccuracyMonth, HistoryFilter, 
} from "../../services/api";
import CostSummary from "./CostSummary";
import OrgScopeDrawer from "./OrgScopeDrawer";
import HierarchicalDecompTree, { buildDecompChain, type DecompPathStep } from "./HierarchicalDecompTree";
import ConsumptionDrillDialog from "./ConsumptionDrillDialog";
import * as XLSX from "xlsx";
import { ORG } from "../../constants/orgLabels";

// ─────────────────────────────────────────────────────────────────────
// Constantes e formatadores
// ─────────────────────────────────────────────────────────────────────
const DEFAULT_TABLE = "dest_dm_cost_visao_completa";
const EXECUTIVE_DEPARTMENT_STORAGE_KEY = "openfinops.executiveDepartmentId";
const EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY = "openfinops.executiveDirectorView";
const MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const toMonthValue = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
const toDateValue = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const shiftMonth = (value: string, delta: number) => { const [year, month] = value.split("-").map(Number); return toMonthValue(new Date(year, month - 1 + delta, 1)); };
const monthStart = (month: string) => `${month}-01`;
const monthEnd = (month: string) => { const [year, value] = month.split("-").map(Number); return toDateValue(new Date(year, value, 0)); };
const labelDateRange = (from: string, to: string) => `${new Date(`${from}T00:00:00`).toLocaleDateString("pt-BR")} – ${new Date(`${to}T00:00:00`).toLocaleDateString("pt-BR")}`;
const monthsInDateRange = (from: string, to: string) => monthsBetween(from.slice(0, 7), to.slice(0, 7));
const monthsBetween = (from: string, to: string) => {
  const [fromYear, fromMonth] = from.split("-").map(Number);
  const [toYear, toMonth] = to.split("-").map(Number);
  const months: string[] = [];
  if (!fromYear || !toYear || from > to) return months;
  for (let year = fromYear, month = fromMonth; year < toYear || (year === toYear && month <= toMonth);) {
    months.push(`${year}${String(month).padStart(2, "0")}`);
    if (++month > 12) { year++; month = 1; }
  }
  return months;
};
const labelDimension = (column: string) => ({ cloud_origem: "Cloud", organograma_nivel_1: "Segmento", organograma_nivel_2: "Modelo", organograma_nivel_3: "VP", organograma_nivel_4: "Diretoria", organograma_nivel_5: "Projeto", organograma_nivel_6: "SubProjeto", servicename: "Serviço", servicecategory: "Família de serviço", regionname: "Região", subaccountname: "Subconta" }[column] || column.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase()));
const sourceDimensionLabel = (column: string) => column.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());

/** Hierarquia da Visão Diretoria: nivel_1 → 2 → 4 → 3 → 5 → 6 */
const VISAO_DIRETORIA_LEVELS = [
  "organograma_nivel_1",
  "organograma_nivel_2",
  "organograma_nivel_4",
  "organograma_nivel_3",
  "organograma_nivel_5",
  "organograma_nivel_6",
] as const;

/** Ordem de exibição do caminho legado (árvore linear → visão diretoria) */
const VISAO_DIRETORIA_PATH_ORDER = [1, 2, 4, 3, 5, 6] as const;

/** Profundidade na árvore → coluna organograma_nivel_N (Visão Diretoria: 1,2,4,3,5,6). */
const VISAO_DIRETORIA_DEPTH_TO_NIVEL = [1, 2, 4, 3, 5, 6] as const;

function pathSegmentFilters(path: string[], visaoDiretoria: boolean): GoldQueryFilter[] {
  const out: GoldQueryFilter[] = [];
  path.forEach((segment, depth) => {
    if (!segment?.trim()) return;
    const nivel = visaoDiretoria
      ? (VISAO_DIRETORIA_DEPTH_TO_NIVEL[depth] ?? depth + 1)
      : depth + 1;
    out.push({ column: `organograma_nivel_${nivel}`, operator: "IN", values: [segment] });
  });
  return out;
}

function formatDirectorPath(path: string[]): string {
  return VISAO_DIRETORIA_PATH_ORDER
    .map(nivel => {
      const depth = VISAO_DIRETORIA_DEPTH_TO_NIVEL.indexOf(nivel);
      return depth >= 0 && depth < path.length ? path[depth] : "";
    })
    .filter(Boolean)
    .join(" > ");
}

function displayOrgPath(f: Filters): string {
  if (!f.path.length) return ORG.allNodes;
  return f.path.join(" > ");
}

/**
 * Reorganiza a árvore existente para 1 → 2 → 4 → 3 → 5 → 6.
 * Em cada nó de nível 2, a Diretoria passa a ser pai do VP ao qual
 * pertencia; os demais níveis continuam abaixo do VP.
 */
function swapDirectorTreeLevels(nodes: Department[], depth = 0): Department[] {
  return nodes.map(node => {
    const activeChildren = (node.children || []).filter(child => child.active);
    if (depth !== 1) {
      return { ...node, children: swapDirectorTreeLevels(activeChildren, depth + 1) };
    }

    // A mesma Diretoria pode existir em várias clouds. Agrupe pelo nome para
    // que ela apareça uma única vez e exponha todas as clouds como filhas.
    const directorsByName = new Map<string, Department>();
    activeChildren.forEach(level3 => {
      (level3.children || []).filter(child => child.active).forEach(level4 => {
        const key = level4.name.trim().toLocaleLowerCase("pt-BR");
        const cloudNode: Department = {
          ...level3,
          parent_id: level4.id,
          children: swapDirectorTreeLevels(
            (level4.children || []).filter(child => child.active),
            4,
          ),
        };
        const existing = directorsByName.get(key);
        if (existing) {
          existing.children.push(cloudNode);
        } else {
          directorsByName.set(key, { ...level4, children: [cloudNode] });
        }
      });
    });
    return { ...node, children: Array.from(directorsByName.values()) };
  });
}

function nivelNum(field: string): number {
  const m = field.match(/^organograma_nivel_(\d+)$/);
  return m ? Number(m[1]) : 0;
}
const PURPLE = "#0B3FA8", ORANGE = "#ff7a00", BLUE = "#0EA5C6",
      GREEN = "#10b981", RED = "#ef4444", YELLOW = "#f59e0b", INK = "#162B3A", MUT = "#526777";

const DIM_LABELS: Record<string, string> = {
  _billing_period: "Mês",
  cloud_origem: "Cloud",
  organograma_nivel_1: "Segmento",
  organograma_nivel_2: "Modelo",
  organograma_nivel_3: "VP",
  organograma_nivel_4: "Diretoria",
  organograma_nivel_5: ORG.level5,
  organograma_nivel_6: "Unidade",
  organograma: "Área",
  centro_custo: "Centro de custo",
  ambiente: "Ambiente",
  servicecategory: "Categoria",
  servicename: "Serviço",
  servicesubcategory: "Subcategoria",
  skumeter: "SKU",
  subaccountname: "Subconta",
  regionname: "Região",
  chargecategory: "Tipo de carga",
};

const DIRECTOR_VIEW_LABELS: Record<string, string> = {
  organograma_nivel_1: "Segmento",
  organograma_nivel_2: "Modelo",
  organograma_nivel_4: "Diretoria",
  organograma_nivel_3: "Cloud",
  organograma_nivel_5: "Projeto",
  organograma_nivel_6: "SubProjeto",
};

function directorNivelLabel(depth: number): string | null {
  const nivel = VISAO_DIRETORIA_DEPTH_TO_NIVEL[depth];
  if (!nivel) return null;
  return DIRECTOR_VIEW_LABELS[`organograma_nivel_${nivel}`] || `Nível ${nivel}`;
}

function visaoDiretoriaChain(): Array<{ field: string; label: string }> {
  return VISAO_DIRETORIA_LEVELS.map(field => ({ field, label: DIRECTOR_VIEW_LABELS[field] || field }));
}

function analysisDimOptions(visaoDiretoria: boolean): string[] {
  const dept = visaoDiretoria ? [...VISAO_DIRETORIA_LEVELS] : ["organograma_nivel_4", "organograma_nivel_5"];
  return ["cloud_origem", ...dept, "servicename", "servicecategory", "regionname", "subaccountname"];
}

function investigationDimOptions(visaoDiretoria: boolean): string[] {
  const dept = visaoDiretoria ? [...VISAO_DIRETORIA_LEVELS] : ["organograma_nivel_4", "organograma_nivel_5"];
  return ["servicename", "cloud_origem", ...dept, "servicecategory", "regionname", "subaccountname"];
}

function heatmapDimOptions(visaoDiretoria: boolean): string[] {
  const dept = visaoDiretoria ? [...VISAO_DIRETORIA_LEVELS] : ["organograma_nivel_4"];
  return [...dept, "cloud_origem", "servicename", "servicecategory", "regionname", "subaccountname"];
}

const MEASURE_LABELS: Record<string, string> = {
  efetivado: "Efetivado (BRL)",
  billedcost: "Custo faturado (USD)",
  quantity: "Volume consumido",
  count: "Registros",
};

const COMPACT_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });
const FULL_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const COMPACT_USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const FULL_USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const COMPACT_NUM = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });

function money(v: unknown, currency: "BRL" | "USD" = "BRL"): string {
  const n = Number(v ?? 0);
  const symbol = currency === "BRL" ? "R$" : "US$";
  if (Math.abs(n) >= 1_000_000) return `${symbol} ${(n / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MM`;
  if (Math.abs(n) >= 1_000) return `${symbol} ${(n / 1_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} K`;
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
function esc(s: unknown): string { return String(s ?? "Sem classificação"); }
/** Compact BRL (mesmo formato de money, para valores monetários do cockpit de decisão). */
function compact(v: unknown): string { return money(v, "BRL"); }

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
  measures?: GoldQueryMeasure[];
  filters?: GoldQueryFilter[];
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
  // Escopo global de departamento: caminho da árvore (ex.: Consumo Cloud > Showback > Azure)
  // e o id do departamento selecionado (quando for um nó real do organograma).
  path: string[];
  deptId: number | null;
  /** Agrupa por nivel_1 → 2 → 4 → 3 → 5 → 6 em todas as abas */
  visaoDiretoria: boolean;
  // Filtros adicionais por coluna (ex.: contexto aplicado pela árvore de decomposição
  // em dimensões que não são níveis de departamento — servicename, servicecategory, ...).
  extra: Record<string, string[]>;
  consumoFaturado: boolean;
};

const EMPTY_FILTERS: Filters = { months: [], clouds: [], dirs: [], depts: [], servicos: [], cats: [], path: [], deptId: null, visaoDiretoria: false, extra: {}, consumoFaturado: true };
const measureFor = (f: Filters) => f.consumoFaturado ? "realizado" : "realizado_cloud";

function filtersOf(f: Filters, extra: GoldQueryFilter[] = []): GoldQueryFilter[] {
  const out: GoldQueryFilter[] = [...extra];
  if (f.months.length) out.push({ column: "_billing_period", operator: "IN", values: f.months });
  if (f.clouds.length) out.push({ column: "cloud_origem", operator: "IN", values: f.clouds });
  if (f.dirs.length) out.push({ column: "organograma_nivel_4", operator: "IN", values: f.dirs });
  if (f.depts.length) out.push({ column: "organograma_nivel_5", operator: "IN", values: f.depts });
  if (f.servicos.length) out.push({ column: "servicename", operator: "IN", values: f.servicos });
  if (f.cats.length) out.push({ column: "servicecategory", operator: "IN", values: f.cats });
  if (f.path.length) out.push(...pathSegmentFilters(f.path, f.visaoDiretoria));
  // Filtros extras por coluna (contexto da árvore de decomposição, etc.).
  if (f.extra) {
    Object.entries(f.extra).forEach(([column, values]) => {
      if (values && values.length) out.push({ column, operator: "IN", values });
    });
  }
  return out;
}

function useTabQueries(table: string, defs: QueryDef[], key: string) {
  const [results, setResults] = useState<Record<string, GoldQueryResult | null>>({});
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
      })),
    ).then(list => {
      if (!alive) return;
      const m: Record<string, GoldQueryResult | null> = {};
      defs.forEach((d, i) => {
        const result = list[i];
        // Keep presentation components stable while allowing the selected
        // financial measure to change underneath them.
        if (result && d.measures) result.rows = result.rows.map(row => {
          const next = { ...row };
          d.measures?.forEach(measure => {
            if (measure.column !== "realizado" && measure.column !== "realizado_cloud" && measure.column !== "*") next.realizado = next[measure.column];
            if (measure.column === "realizado_cloud") next.realizado = next.realizado_cloud;
          });
          return next;
        });
        m[d.id] = result;
      });
      setResults(m);
    }).catch(e => { if (alive) setError(formatApiError(e, "Não foi possível consultar a base consolidada")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, key]);
  return { results, loading, error };
}

function rowsOf(r: GoldQueryResult | null | undefined): Record<string, any>[] {
  return r?.rows ?? [];
}
function sumOf(r: GoldQueryResult | null | undefined, col: string): number {
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
  if (!items.length) return <Paper variant="outlined" sx={{ p: 2, height: "100%" }}><Typography variant="subtitle2" fontWeight={900}>{title}</Typography><Typography variant="caption" color="text.secondary">Sem dados para o recorte selecionado.</Typography></Paper>;
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
              <Box sx={{ height: 9, borderRadius: 99, background: "#E1E8F0", overflow: "hidden", mt: 0.4 }}>
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

function LoadingBlock({ loading, error, children }: { loading: boolean; error?: string; children: React.ReactNode }) {
  if (loading) return <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}><CircularProgress size={26} /></Box>;
  if (error) return <Alert severity="error">{dataDisplayText(error)}</Alert>;
  return <>{children}</>;
}

function DataQuality({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const [service, setService] = useState("");
  const base = filtersOf(filters, [{ column: "tipo", operator: "IN", values: ["realizado"] }]);
  const defs = useMemo<QueryDef[]>(() => [{ id: "services", rows: ["servicename"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: base, row_limit: 500 }, { id: "consumers", rows: ["organograma_nivel_4", "organograma_nivel_5"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: service ? [...base, { column: "servicename", operator: "IN", values: [service] }] : base, row_limit: 1000 }], [filterKey, service]);
  const { results, loading, error } = useTabQueries(table, defs, "data-quality-" + filterKey + service);
  const services = rowsOf(results.services); const consumers = rowsOf(results.consumers);
  return <Box><Typography variant="h5" sx={{ fontWeight: 950, mb: .5 }}>Qualidade dos dados</Typography><Typography color="text.secondary" sx={{ mb: 2 }}>Escolha um serviço para ver os projetos e as diretorias que o consomem, com o valor efetivado neste recorte.</Typography><SectionPanel title="Consumidores por serviço" subtitle="Os filtros globais de período, cloud e organização continuam aplicados." right={<FormControl size="small" sx={{ minWidth: 280 }}><InputLabel>Serviço</InputLabel><Select label="Serviço" value={service} onChange={e => setService(e.target.value)}><MenuItem value="">Todos os serviços</MenuItem>{services.map(r => <MenuItem key={String(r.servicename)} value={String(r.servicename)}>{esc(r.servicename)} · {money(r.realizado)}</MenuItem>)}</Select></FormControl>}><LoadingBlock loading={loading} error={error}><TableContainer><Table size="small"><TableHead><TableRow>{["Diretoria", "Projeto", "Efetivado", "% do total do serviço"].map(h => <TableCell key={h} sx={{ fontWeight: 900 }}>{h}</TableCell>)}</TableRow></TableHead><TableBody>{consumers.sort((a, b) => Number(b.realizado || 0) - Number(a.realizado || 0)).map((r, i) => <TableRow key={i}><TableCell>{esc(r.organograma_nivel_4)}</TableCell><TableCell>{esc(r.organograma_nivel_5)}</TableCell><TableCell sx={{ fontWeight: 900 }}>{money(r.realizado)}</TableCell><TableCell>{pct(Number(r.realizado || 0) / Math.max(1, consumers.reduce((s, x) => s + Number(x.realizado || 0), 0)) * 100)}</TableCell></TableRow>)}{!consumers.length && <TableRow><TableCell colSpan={4} align="center">Nenhum consumidor no recorte selecionado.</TableCell></TableRow>}</TableBody></Table></TableContainer></LoadingBlock></SectionPanel></Box>;
}

function monthlySeries(r: GoldQueryResult | null | undefined, measure: string) {
  return rowsOf(r)
    .map(row => ({ month: String(row._billing_period || row.period || ""), value: Number(row[measure] ?? 0) }))
    .sort((a, b) => a.month.localeCompare(b.month));
}

// ─────────────────────────────────────────────────────────────────────
// Página principal
// ─────────────────────────────────────────────────────────────────────
type TabId = "visao-executiva" | "geral" | "evolucao" | "organizacao" | "servicos" | "investigacao" | "decisao" | "drilldown" | "data-quality";

export default function GoldDashboard() {
  const [params] = useSearchParams();
  const [table, setTable] = useState(DEFAULT_TABLE);
  const [availableTables, setAvailableTables] = useState<string[]>([]);
  const [info, setInfo] = useState<GoldInfo | null>(null);
  // A aba pode vir da URL (?tab=...) — padrão: Visão Executiva.
  const urlTab = params.get("tab");
  const tab: TabId = urlTab && (["visao-executiva", "geral", "evolucao", "organizacao", "servicos", "investigacao", "decisao", "drilldown", "data-quality"] as TabId[]).includes(urlTab as TabId)
    ? urlTab as TabId
    : "visao-executiva";
  const [filters, setFilters] = useState<Filters>(() => ({
    ...EMPTY_FILTERS,
    months: monthsBetween(`${new Date().getFullYear()}-01`, `${new Date().getFullYear()}-12`),
    visaoDiretoria: false,
  }));
  const [monthsAll, setMonthsAll] = useState<string[]>([]);
  const currentYear = new Date().getFullYear();
  const [dateFrom, setDateFrom] = useState(`${currentYear}-01-01`);
  const [dateTo, setDateTo] = useState(`${currentYear}-12-31`);
  const [dateAnchor, setDateAnchor] = useState<HTMLElement | null>(null);
  const [draftFrom, setDraftFrom] = useState(`${currentYear}-01-01`);
  const [draftTo, setDraftTo] = useState(`${currentYear}-12-31`);
  const [filterMenuAnchor, setFilterMenuAnchor] = useState<HTMLElement | null>(null);
  const [includeSharedCost, setIncludeSharedCost] = useState(false);
  const [error, setError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  // Árvore do organograma para o filtro global de departamento.
  const [organization, setOrganization] = useState<Department[]>([]);
  const [organizationAnchor, setOrganizationAnchor] = useState<HTMLElement | null>(null);
  const [organizationSearch, setOrganizationSearch] = useState("");
  const organizationSearchInput = useRef<HTMLInputElement>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [highlightsDepartment, setHighlightsDepartment] = useState("");
  const [consumptionDrill, setConsumptionDrill] = useState<{ open: boolean; tipo: "realizado" | "forecast" | "ambos"; period?: string }>({ open: false, tipo: "realizado" });
  const filterDimensionOptions = useMemo(() => [...new Set([...(info?.dimensions || []), ...(info?.columns.filter((column) => column.type !== "measure").map((column) => column.name) || [])])]
    .filter((column) => !["_billing_period", "tipo"].includes(column.toLowerCase()))
    .sort((a, b) => sourceDimensionLabel(a).localeCompare(sourceDimensionLabel(b), "pt-BR", { sensitivity: "base" })), [info]);
  const groupByOptions = useMemo(() => filterDimensionOptions
    .filter((column) => !["period", "source connection id", "rateio medida"].includes(column.toLowerCase().replace(/_/g, " ")))
    .sort((a, b) => sourceDimensionLabel(a).localeCompare(sourceDimensionLabel(b), "pt-BR", { sensitivity: "base" })), [filterDimensionOptions]);
  useEffect(() => {
    if (!organizationAnchor) return;
    const timer = window.setTimeout(() => organizationSearchInput.current?.focus(), 120);
    return () => window.clearTimeout(timer);
  }, [organizationAnchor]);
  const displayedOrganization = useMemo(
    () => filters.visaoDiretoria ? swapDirectorTreeLevels(organization) : organization,
    [organization, filters.visaoDiretoria],
  );
  const filteredOrganization = useMemo(() => {
    const term = organizationSearch.trim().toLocaleLowerCase("pt-BR");
    if (!term) return displayedOrganization;

    const matchingIds = new Set<number>();
    const findMatches = (nodes: Department[], parentPath: string[] = []) => {
      nodes.filter(node => node.active).forEach(node => {
        const path = [...parentPath, node.name];
        if (path.join(" ").toLocaleLowerCase("pt-BR").includes(term)) matchingIds.add(node.id);
        findMatches(node.children || [], path);
      });
    };
    findMatches(displayedOrganization);

    const includeSubtree = (node: Department): Department => ({
      ...node,
      children: (node.children || []).filter(child => child.active).map(includeSubtree),
    });
    const filterTree = (nodes: Department[]): Department[] => nodes.flatMap(node => {
      if (!node.active) return [];
      if (matchingIds.has(node.id)) return [includeSubtree(node)];
      const children = filterTree(node.children || []);
      return children.length ? [{ ...node, children }] : [];
    });
    return filterTree(displayedOrganization);
  }, [displayedOrganization, organizationSearch]);

  useEffect(() => {
    getDepartments().then(departments => {
      setOrganization(departments);
      const savedId = Number(window.localStorage.getItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY));
      if (!Number.isInteger(savedId) || savedId <= 0) return;

      const findDepartment = (nodes: Department[], parentPath: string[] = []): { department: Department; path: string[] } | undefined => {
        for (const department of nodes) {
          const path = [...parentPath, department.name];
          if (department.id === savedId && department.active) return { department, path };
          const found = findDepartment(department.children || [], path);
          if (found) return found;
        }
        return undefined;
      };
      const tree = window.localStorage.getItem(EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY) === "1"
        ? swapDirectorTreeLevels(departments)
        : departments;
      const savedDepartment = findDepartment(tree);
      if (savedDepartment) {
        const path = window.localStorage.getItem(EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY) === "1"
          ? formatDirectorPath(savedDepartment.path).split(" > ").filter(Boolean)
          : savedDepartment.path;
        setFilters(current => ({ ...current, path, deptId: savedDepartment.department.id }));
        setHighlightsDepartment(path.join(" > "));
      } else {
        // Do not keep a stale reference after an organogram change.
        window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
      }
    }).catch(() => setOrganization([]));
  }, []);

  const toggleOrgNode = (id: number) => setExpandedNodes(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  // The organization endpoint is a nested tree.  Carry its path while
  // rendering instead of trying to find parents only among root nodes: the
  // latter truncated selections such as Azure > DIR DADOS..., causing the
  // executive widgets to query a different (empty) scope.
  const renderOrgNode = (node: Department, depth = 0, parentPath: string[] = [], forceExpanded = false): React.ReactNode => {
    const children = (node.children || []).filter(child => child.active);
    const expanded = forceExpanded || expandedNodes.has(node.id);
    const path = [...parentPath, node.name];
    return (
      <Box key={node.id}>
        <ListItemButton
          sx={{ pl: 1 + depth * 2, py: 0.375, mb: 0.25, borderRadius: 1 }}
          onClick={() => {
            const groupedDirector = filters.visaoDiretoria && depth === 2;
            setHighlightsDepartment(path.join(" > "));
            setFilters(f => ({ ...f, path, deptId: groupedDirector ? null : node.id }));
            if (groupedDirector) window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
            else window.localStorage.setItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY, String(node.id));
            setOrganizationAnchor(null);
            setOrganizationSearch("");
          }}
        >
          <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>
            {children.length > 0 && (
              <IconButton size="small" onClick={event => { event.stopPropagation(); toggleOrgNode(node.id); }}>
                {expanded ? <ExpandMoreOutlined fontSize="small" /> : <ChevronRightOutlined fontSize="small" />}
              </IconButton>
            )}
          </Box>
          <ListItemText
            primary={node.name}
            secondary={filters.visaoDiretoria ? directorNivelLabel(depth) : undefined}
            sx={{ my: 0 }}
            primaryTypographyProps={{
              variant: "body2",
              fontWeight: children.length > 0 ? 700 : 400,
              sx: { lineHeight: 1.35 },
            }}
            secondaryTypographyProps={{ variant: "caption", color: "secondary" }}
          />
        </ListItemButton>
        <Collapse in={expanded} timeout="auto" unmountOnExit>
          {children.map(child => renderOrgNode(child, depth + 1, path, forceExpanded))}
        </Collapse>
      </Box>
    );
  };
  const globalDirector = filters.path.length ? filters.path.join(" > ") : undefined;


  // ── carrega tabelas disponíveis (namespace gold) ──
  useEffect(() => {
    getAnalysisTables().then(t => {
      const goldTables = t.gold || [];
      setAvailableTables(goldTables);
      if (goldTables.length && !goldTables.includes(table)) setTable(goldTables.includes(DEFAULT_TABLE) ? DEFAULT_TABLE : goldTables[0]);
    }).catch(() => setAvailableTables([]));
  }, []);

  // ── info da tabela ──
  useEffect(() => {
    let alive = true;
    setInfo(null);
    getDimonInfo(table).then(i => { if (alive) { setInfo(i); setMonthsAll(i.periods || []); } })
      .catch(e => { if (alive) setError(formatApiError(e, "Não foi possível carregar as informações da base consolidada")); });
    return () => { alive = false; };
  }, [table]);

  const applyPreset = (preset: "this_month" | "last_month" | "last_3" | "last_6" | "last_12" | "this_year" | "last_year") => {
    const now = new Date(); const current = toMonthValue(now); const year = now.getFullYear();
    const ranges: Record<typeof preset, [string, string]> = {
      this_month: [current, current], last_month: [shiftMonth(current, -1), shiftMonth(current, -1)],
      last_3: [shiftMonth(current, -2), current], last_6: [shiftMonth(current, -5), current],
      last_12: [shiftMonth(current, -11), current], this_year: [`${year}-01`, `${year}-12`],
      last_year: [`${year - 1}-01`, `${year - 1}-12`],
    };
    const [fromMonth, toMonth] = ranges[preset];
    const from = monthStart(fromMonth); const to = monthEnd(toMonth);
    setDateFrom(from); setDateTo(to); setFilters((currentFilters) => ({ ...currentFilters, months: monthsInDateRange(from, to) })); setDateAnchor(null);
  };

  const filterKey = JSON.stringify([filters, tab, refreshTick, measureFor(filters)]);
  const executiveScope = useMemo(() => ({
    monthFrom: dateFrom.slice(0, 7).replace("-", ""),
    monthTo: dateTo.slice(0, 7).replace("-", ""),
    dateFrom,
    dateTo,
    dimensionFilters: Object.fromEntries(Object.entries(filters.extra).filter(([, values]) => values.length > 0)),
    filters: filtersOf(filters),
  }), [dateFrom, dateTo, filters]);
  const setFilter = (key: keyof Filters, values: string[]) => setFilters(f => ({ ...f, [key]: values }));

  const clearFilters = () => {
    window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
    window.localStorage.removeItem(EXECUTIVE_DIRECTOR_VIEW_STORAGE_KEY);
    setFilters(EMPTY_FILTERS);
    const from = `${currentYear}-01-01`; const to = `${currentYear}-12-31`;
    setDateFrom(from); setDateTo(to); setFilters({ ...EMPTY_FILTERS, months: monthsInDateRange(from, to) });
  };

  const [copied, setCopied] = useState(false);
  const copyEmail = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#FAFBFD" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2} mb={2}>
        <Box>
          <Typography variant="h4">FinBase</Typography>
        </Box>
        <Stack direction="row" gap={1} alignItems="center">
          <Button size="small" variant="outlined" startIcon={<RefreshOutlined />} onClick={() => { setRefreshTick(t => t + 1); }}>Recarregar dados</Button>
        </Stack>
      </Stack>
      </Box>
      <Box sx={{ p: 3 }}>
        <Paper elevation={0} sx={{ p: 2.5, borderRadius: 2, border: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}

      {/* ── Filtros globais ── */}
      <Paper variant="outlined" sx={{ px: 1.5, py: 1, mb: 2, borderRadius: "14px", borderColor: "#E1E8F0" }}>
        <Stack direction="row" flexWrap="wrap" gap={1} alignItems="center">
          <Popover open={Boolean(dateAnchor)} anchorEl={dateAnchor} onClose={() => setDateAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "left" }} transformOrigin={{ vertical: "top", horizontal: "left" }} PaperProps={{ sx: { mt: .5, borderRadius: "14px", border: "1px solid #E1E8F0", boxShadow: "0 16px 44px rgba(24,39,75,.16)" } }}>
            <Stack direction={{ xs: "column", sm: "row" }} sx={{ p: 2 }} gap={2}>
              <Box sx={{ minWidth: 180 }}><Typography variant="overline" color="text.secondary" fontWeight={800}>Atalhos de competência</Typography><Stack spacing={.25} mt={.5}>
                <Button size="small" onClick={() => applyPreset("this_month")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Mês em curso</Button>
                <Button size="small" onClick={() => applyPreset("last_month")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Mês passado</Button>
                <Button size="small" onClick={() => applyPreset("last_3")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Últimos três meses</Button>
                <Button size="small" onClick={() => applyPreset("last_6")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Últimos seis meses</Button>
                <Button size="small" onClick={() => applyPreset("last_12")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Últimos doze meses</Button>
                <Button size="small" onClick={() => applyPreset("this_year")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Ano corrente</Button>
                <Button size="small" onClick={() => applyPreset("last_year")} sx={{ justifyContent: "flex-start", textTransform: "none" }}>Exercício anterior</Button>
              </Stack></Box>
              <Box sx={{ minWidth: { sm: 330 }, borderLeft: { sm: "1px solid #EEF3F8" }, pl: { sm: 2 } }}><Typography variant="overline" color="text.secondary" fontWeight={800}>Intervalo personalizado</Typography><Stack direction={{ xs: "column", sm: "row" }} gap={1} mt={1}><TextField size="small" type="date" label="Data inicial" value={draftFrom} onChange={(event) => setDraftFrom(event.target.value)} InputLabelProps={{ shrink: true }} /><TextField size="small" type="date" label="Data final" value={draftTo} onChange={(event) => setDraftTo(event.target.value)} InputLabelProps={{ shrink: true }} /></Stack>{draftFrom > draftTo && <Typography variant="caption" color="error" display="block" mt={.5}>A data inicial deve ser anterior ou igual à data final.</Typography>}<Stack direction="row" justifyContent="flex-end" gap={1} mt={2}><Button size="small" onClick={() => setDateAnchor(null)}>Cancelar</Button><Button size="small" variant="contained" disabled={!draftFrom || !draftTo || draftFrom > draftTo} onClick={() => { setDateFrom(draftFrom); setDateTo(draftTo); setFilters((current) => ({ ...current, months: monthsInDateRange(draftFrom, draftTo) })); setDateAnchor(null); }} sx={{ bgcolor: "#0B3FA8" }}>Aplicar</Button></Stack></Box>
            </Stack>
          </Popover>
          <Box sx={{ minWidth: 0, flex: "1 1 320px" }}>
            <OrgScopeDrawer
              tree={displayedOrganization}
              path={filters.path}
              levelLabels={filters.visaoDiretoria ? [0, 1, 2, 3, 4, 5].map((depth) => directorNivelLabel(depth) || `Nível ${depth + 1}`) : ["Segmento", "Modelo", "Plataforma", "Diretoria", ORG.level5, "Unidade"]}
              table={table}
              measure={measureFor(filters)}
              months={filters.months}
              onSelect={(node, path, depth) => {
                const groupedDirector = filters.visaoDiretoria && depth === 2;
                setHighlightsDepartment(path.join(" > "));
                setFilters(f => ({ ...f, path, deptId: groupedDirector ? null : node.id }));
                if (groupedDirector) window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
                else window.localStorage.setItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY, String(node.id));
              }}
              onClear={() => {
                setHighlightsDepartment("");
                setFilters(f => ({ ...f, path: [], deptId: null }));
                window.localStorage.removeItem(EXECUTIVE_DEPARTMENT_STORAGE_KEY);
              }}
            />
          </Box>
          <Tooltip title="Filtra por qualquer dimensão FOCUS. Valores da mesma dimensão somam (OU); dimensões diferentes se combinam (E).">
            <Button size="small" variant="outlined" startIcon={<AddOutlined />} onClick={(event) => setFilterMenuAnchor(event.currentTarget)} sx={{ height: 34, borderRadius: "10px", textTransform: "none" }}>Filtro</Button>
          </Tooltip>
          <FormControlLabel control={<Switch size="small" checked={includeSharedCost} onChange={(_, checked) => setIncludeSharedCost(checked)} />} label={<Typography variant="body2">Incluir custo rateado</Typography>} sx={{ ml: 0.5, mr: 0 }} />
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" startIcon={<FilterAltOffOutlined />} onClick={() => { setHighlightsDepartment(""); setIncludeSharedCost(false); clearFilters(); }} sx={{ textTransform: "none" }}>Limpar</Button>
        <Box>
          <Popover
            anchorEl={filterMenuAnchor}
            open={Boolean(filterMenuAnchor)}
            onClose={() => setFilterMenuAnchor(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
            transformOrigin={{ vertical: "top", horizontal: "left" }}
              slotProps={{ paper: { sx: { p: 1.25, width: 380, maxWidth: "calc(100vw - 32px)", borderRadius: "12px", overflow: "visible" } } }}
          >
            <Typography variant="caption" color="text.secondary" sx={{ px: .5, pb: .5, display: "block" }}>CAMPOS FILTRÁVEIS DO MODELO</Typography>
            <Autocomplete
              autoHighlight
              open
              disablePortal
              options={filterDimensionOptions.filter((column) => !filters.extra[column])}
              getOptionLabel={sourceDimensionLabel}
              onChange={(_, column) => { if (column) { setFilters((current) => ({ ...current, extra: { ...current.extra, [column]: [] } })); setFilterMenuAnchor(null); } }}
              noOptionsText={!info ? "Carregando estrutura da base consolidada…" : "Todos os campos já foram incluídos"}
              slotProps={{
                popper: { sx: { zIndex: (theme) => theme.zIndex.modal + 1 } },
                listbox: { sx: { maxHeight: 280, overflowY: "auto" } },
              }}
              renderInput={(params) => <TextField {...params} autoFocus size="small" placeholder="Procurar coluna" />}
            />
          </Popover>
        </Box>
        </Stack>
        {Object.entries(filters.extra).length > 0 && <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 340px), 380px))", gap: 1, width: "100%", mt: 1, alignItems: "start" }}>
          {Object.entries(filters.extra).map(([column, values]) => <Stack key={column} direction="row" alignItems="flex-start" gap={.5} sx={{ minWidth: 0 }}><Box sx={{ minWidth: 0, flex: 1 }}><CascadeDimensionFilter column={column} label={sourceDimensionLabel(column)} table={table} value={values} filters={filtersOf(filters).filter((filter) => filter.column !== column)} onChange={(nextValues) => setFilters((current) => ({ ...current, extra: { ...current.extra, [column]: nextValues } }))} /></Box><Chip size="small" color={values.length ? "success" : "default"} variant={values.length ? "filled" : "outlined"} label={values.length ? `${values.length} aplicado${values.length === 1 ? "" : "s"}` : "Sem filtro"} sx={{ mt: 1, flexShrink: 0 }} /><IconButton size="small" aria-label={`Remover filtro ${sourceDimensionLabel(column)}`} onClick={() => setFilters((current) => { const extra = { ...current.extra }; delete extra[column]; return { ...current, extra }; })}><CloseOutlined fontSize="small" /></IconButton></Stack>)}
        </Box>}
      </Paper>

      {tab === "visao-executiva" && (
        <CostSummary
          table={table}
          director={globalDirector || "Consumo de nuvem"}
          departmentId={filters.deptId ?? undefined}
          path={filters.path}
          includeSharedCost={includeSharedCost}
          measureColumn={measureFor(filters)}
          scope={executiveScope}
          onDateRangeChange={(from, to) => {
            setDateFrom(from);
            setDateTo(to);
            setFilters(current => ({ ...current, months: monthsInDateRange(from, to) }));
          }}
        />
      )}
      {tab === "geral" && <VisaoGeral table={table} filters={filters} filterKey={filterKey} />}
      {tab === "evolucao" && <Evolucao table={table} filters={filters} filterKey={filterKey} />}
      {tab === "organizacao" && <Organizacao table={table} filters={filters} filterKey={filterKey} />}
      {tab === "servicos" && <Servicos table={table} filters={filters} filterKey={filterKey} />}
      {tab === "investigacao" && <Investigacao table={table} filters={filters} filterKey={filterKey} />}
      {tab === "decisao" && <Decisao table={table} filters={filters} filterKey={filterKey} setFilters={setFilters} />}
      {tab === "drilldown" && <Drilldown table={table} filters={filters} filterKey={filterKey} />}
      {tab === "data-quality" && <DataQuality table={table} filters={filters} filterKey={filterKey} />}
        </Paper>
      </Box>
      <ConsumptionDrillDialog
        open={consumptionDrill.open}
        onClose={() => setConsumptionDrill(current => ({ ...current, open: false }))}
        table={table}
        directorPath={filters.path.join(" > ")}
        directorView={filters.visaoDiretoria}
        period={consumptionDrill.period}
        tipo={consumptionDrill.tipo}
        measureColumn={measureFor(filters)}
        key={JSON.stringify([filters, consumptionDrill.tipo, consumptionDrill.period])}
        globalFilters={filtersOf(filters).filter(filter => filter.column !== "_billing_period")}
      />
    </Box>
  );
}

function CascadeDimensionFilter({ column, label, table, value, filters, onChange }: {
  column: string; label: string; table: string; value: string[]; filters: GoldQueryFilter[]; onChange: (values: string[]) => void;
}) {
  const [options, setOptions] = useState<string[]>(value);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [optionsError, setOptionsError] = useState("");
  const contextKey = JSON.stringify([column, filters, search]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoadingOptions(true);
    setOptionsError("");
    const timer = window.setTimeout(() => getGoldFilterOptions({ table, column, filters, search, limit: 100 })
      .then((result) => { if (active) setOptions([...new Set([...value, ...result.values])]); })
      .catch((error) => {
        if (active) {
          setOptions(value);
          setOptionsError(formatApiError(error, `Não foi possível carregar opções de ${label}.`));
        }
      })
      .finally(() => { if (active) setLoadingOptions(false); }), 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [contextKey, open]);
  return <Autocomplete
    multiple
    freeSolo
    selectOnFocus
    clearOnBlur
    handleHomeEndKeys
    size="small"
    open={open}
    onOpen={() => setOpen(true)}
    onClose={() => setOpen(false)}
    loading={loadingOptions}
    loadingText="Carregando valores…"
    noOptionsText={optionsError || (search ? "Nada encontrado. Digite outro termo para incluir um valor." : "Nenhum valor encontrado.")}
    options={options}
    value={value}
    onChange={(_, values) => onChange(values.map((item) => String(item).trim()).filter(Boolean))}
    onInputChange={(_, text) => setSearch(text)}
    filterOptions={(items, state) => items.filter((item) => item.toLocaleLowerCase().includes(state.inputValue.toLocaleLowerCase()))}
    renderTags={(selected, getTagProps) => <>
      {selected.slice(0, 1).map((item, index) => <Chip {...getTagProps({ index })} key={`${item}-${index}`} label={item} size="small" sx={{ maxWidth: 155 }} />)}
      {selected.length > 1 && <Chip size="small" label={`+${selected.length - 1}`} title={selected.slice(1).join(", ")} />}
    </>}
    renderInput={(params) => <TextField {...params} label={label} placeholder={value.length ? "Digite ou escolha valores" : "Escolha ou digite para buscar"} />}
  />;
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
    { id: "totais", measures: [{ column: measureFor(filters), aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }, { column: "*", aggregation: "COUNT" }], filters: filtersOf(filters) },
    { id: "mensal", rows: ["_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 60 },
    { id: "dirs", rows: [filters.visaoDiretoria ? VISAO_DIRETORIA_LEVELS[0] : "organograma_nivel_4"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "clouds", rows: ["cloud_origem"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "servicos", rows: ["servicename"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "categorias", rows: ["servicecategory"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 8 },
  ], [filterKey]);
  const { results, loading, error } = useTabQueries(table, defs, filterKey);

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

  const dirField = filters.visaoDiretoria ? VISAO_DIRETORIA_LEVELS[0] : "organograma_nivel_4";
  const dirLabel = DIM_LABELS[dirField] || dirField;

  const email = useMemo(() => {
    const period = filters.months.length ? `competência(s) ${filters.months.map(monthLong).join(", ")}` : "todo o histórico disponível";
    return [
      `Assunto: Resumo executivo FinOps — ${period}`,
      "",
      `Efetivado (BRL): ${moneyFull(total.realizado)} | Custo faturado (USD): ${moneyFull(total.billedcost, "USD")} | Volume consumido: ${num(total.quantity)}`,
      `Mês mais recente (${latest ? monthLong(latest.month) : "—"}): ${latest ? moneyFull(latest.value) : "—"}${mom !== null ? ` (${mom >= 0 ? "+" : ""}${pct(mom)} vs mês anterior)` : ""}`,
      `Top ${dirLabel.toLowerCase()}: ${topDir ? esc(topDir[dirField]) : "—"} (${topDir ? money(topDir.realizado) : "—"}) · Concentração top 5: ${pct(top5Share)}`,
      `Top cloud: ${topCloud ? esc(topCloud.cloud_origem) : "—"} · Top serviço: ${topServico ? esc(topServico.servicename) : "—"}`,
      `Health Score: ${health}/100 (${healthStatus.label}).`,
      "",
      "Gerado automaticamente pelo painel FinOps (base consolidada).",
    ].join("\n");
  }, [total, mensal, mom, topDir, topCloud, topServico, top5Share, health, healthStatus, filters.months, latest, dirField, dirLabel]);

  return (
    <LoadingBlock loading={loading} error={error}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Efetivado (BRL)" value={money(total.realizado)} sub="Soma da medida efetivado" color={PURPLE} full={moneyFull(total.realizado)} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Custo faturado (USD)" value={money(total.billedcost, "USD")} sub="Moeda de billing" color={ORANGE} full={moneyFull(total.billedcost, "USD")} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Volume consumido" value={num(total.quantity)} sub="Total de ConsumedQuantity" color={BLUE} full={total.quantity.toLocaleString("pt-BR")} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Linhas agrupadas" value={num(total.count)} sub="Linhas da base consolidada" color={GREEN} full={total.count.toLocaleString("pt-BR")} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Índice de saúde financeira" value={`${health}/100`} sub={healthStatus.label} color={healthStatus.color} /></Grid>
        <Grid item xs={6} sm={4} md={2}><KpiCard label="Variação do mês" value={mom === null ? "—" : `${mom >= 0 ? "+" : ""}${pct(mom)}`} sub={latest ? monthLabel(latest.month) : "sem histórico"} color={mom !== null && mom > 10 ? RED : GREEN} /></Grid>
      </Grid>

      <Grid container spacing={1.5}>
        <Grid item xs={12} md={7}>
          <ChartCard title="Evolução mensal: efetivado e custo faturado" note="Valores somados da base consolidada para o recorte filtrado.">
            <Chart
              data={[
                { x: mensal.map(r => monthLabel(r.month)), y: mensal.map(r => r.value), type: "bar", name: "Efetivado (BRL)", marker: { color: PURPLE } },
                { x: mensal.map(r => monthLabel(r.month)), y: monthlySeries(results.mensal, "billedcost").map(r => r.value), type: "scatter", mode: "lines+markers", name: "Custo faturado (USD)", yaxis: "y2", line: { color: ORANGE, width: 3 } },
              ]}
              layout={{ height: 320, margin: { l: 56, r: 56, t: 8, b: 34 }, barmode: "group", legend: { orientation: "h", y: -0.22 }, yaxis: { title: { text: "BRL" }, gridcolor: "#EEF3F8" }, yaxis2: { title: { text: "USD" }, overlaying: "y", side: "right", showgrid: false } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <ChartCard title="Peso das categorias de carga">
            <Chart
              data={[{ labels: rowsOf(results.categorias).map(r => esc(r.servicecategory)), values: rowsOf(results.categorias).map(r => Number(r.realizado) || 0), type: "pie", hole: 0.55, marker: { colors: [PURPLE, ORANGE, BLUE, GREEN, YELLOW, RED, "#0F62FE", "#64748B"] } }]}
              layout={{ height: 300, margin: { l: 8, r: 8, t: 8, b: 8 }, showlegend: true, legend: { orientation: "h", y: -0.25, font: { size: 10 } } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <RankBars title={`Top ${dirLabel}`} rows={dirRows} measure="realizado" limit={8} color={PURPLE} />
        </Grid>
        <Grid item xs={12} md={3.5}>
          <RankBars title="Principais nuvens" rows={rowsOf(results.clouds)} measure="realizado" limit={6} color={BLUE} />
        </Grid>
        <Grid item xs={12} md={3.5}>
          <RankBars title="Serviços de maior custo" rows={rowsOf(results.servicos)} measure="realizado" limit={6} color={ORANGE} />
        </Grid>
        <Grid item xs={12}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
              <Typography variant="subtitle2" fontWeight={900}>Resumo executivo para e-mail (automático)</Typography>
              <Button size="small" startIcon={<ContentCopyOutlined />} onClick={() => copyEmail(email)}>{copied ? "Copiado!" : "Copiar"}</Button>
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
  const dimOptions = useMemo(() => analysisDimOptions(filters.visaoDiretoria), [filters.visaoDiretoria]);
  const [dim, setDim] = useState("cloud_origem");
  useEffect(() => {
    if (filters.visaoDiretoria) setDim(VISAO_DIRETORIA_LEVELS[0]);
  }, [filters.visaoDiretoria]);
  const defs = useMemo<QueryDef[]>(() => [
    { id: "mensal", rows: ["_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 60 },
    { id: "drivers", rows: [dim], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 400 },
  ], [filterKey, dim]);
  const { results, loading, error } = useTabQueries(table, defs, filterKey + dim);

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
    <LoadingBlock loading={loading} error={error}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={6} md={3}><KpiCard label="Média por mês (BRL)" value={money(runRateAvg)} full={moneyFull(runRateAvg)} color={PURPLE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Anualizado no ritmo atual" value={money(annualized)} full={moneyFull(annualized)} color={BLUE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Projeção anual (ritmo dos últimos 3 meses)" value={money(projected)} full={moneyFull(projected)} color={ORANGE} sub="Média de 3 meses × 12" /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Variação do último mês" value={`${totalDelta >= 0 ? "+" : ""}${money(totalDelta)}`} sub={`${cur ? monthLabel(cur) : "—"} vs ${prev ? monthLabel(prev) : "—"}`} color={totalDelta > 0 ? RED : GREEN} /></Grid>
      </Grid>

      <Grid container spacing={1.5}>
        <Grid item xs={12}>
          <ChartCard title="Efetivado (BRL), Custo faturado (USD) e Volume por mês">
            <Chart
              data={[
                { x: mensal.map(r => monthLabel(r.month)), y: mensal.map(r => r.value), type: "bar", name: "Efetivado (BRL)", marker: { color: PURPLE } },
                { x: billed.map(r => monthLabel(r.month)), y: billed.map(r => r.value), type: "scatter", mode: "lines", name: "Custo faturado (USD)", line: { color: ORANGE, width: 3 } },
                { x: qty.map(r => monthLabel(r.month)), y: qty.map(r => r.value), type: "scatter", mode: "lines+markers", name: "Volume consumido", yaxis: "y2", line: { color: GREEN, width: 2, dash: "dot" } },
              ]}
              layout={{ height: 340, margin: { l: 56, r: 56, t: 8, b: 34 }, legend: { orientation: "h", y: -0.22 }, yaxis: { title: { text: "Valor" }, gridcolor: "#EEF3F8" }, yaxis2: { title: { text: "Volume consumido" }, overlaying: "y", side: "right", showgrid: false } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={7}>
          <ChartCard title={`O que mudou? — variação ${cur ? monthLabel(cur) : "—"} vs ${prev ? monthLabel(prev) : "—"} por ${DIM_LABELS[dim] || dim}`} note="Maiores contribuidores absolutos da variação. Não presume causa operacional.">
            <FormControl size="small" sx={{ mb: 1, minWidth: 180 }}>
              <Select value={dim} onChange={e => setDim(e.target.value)}>
                {dimOptions.map(d => <MenuItem key={d} value={d}>{DIM_LABELS[d] || d}</MenuItem>)}
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
              layout={{ height: 320, margin: { l: 56, r: 24, t: 8, b: 90 }, xaxis: { tickangle: -35 }, yaxis: { title: { text: "Δ BRL" }, gridcolor: "#EEF3F8" } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={5}>
          <ChartCard title="Peso de cada item na variação" note={`Variação consolidada: ${totalDelta >= 0 ? "+" : ""}${money(totalDelta)} (${prevTotal ? pct(totalDelta / Math.abs(prevTotal) * 100) : "—"} vs mês anterior)`}>
            <Stack spacing={1}>
              {deltas.slice(0, 10).map((d, i) => (
                <Box key={i}>
                  <Stack direction="row" justifyContent="space-between" sx={{ fontSize: 12, fontWeight: 800 }}>
                    <Tooltip title={d.key}><Typography variant="caption" sx={{ fontWeight: 850, maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.key}</Typography></Tooltip>
                    <Typography variant="caption" sx={{ fontWeight: 900 }}>{d.delta >= 0 ? "+" : ""}{money(d.delta)} · {totalDelta ? pct(Math.abs(d.delta) / Math.abs(totalDelta) * 100, 0) : "—"} do Δ</Typography>
                  </Stack>
                  <Box sx={{ height: 9, borderRadius: 99, background: "#E1E8F0", overflow: "hidden", mt: 0.4 }}>
                    <Box sx={{ height: "100%", width: `${Math.max(2, Math.min(100, Math.abs(d.delta) / (Math.max(...deltas.slice(0, 10).map(x => Math.abs(x.delta)), 1)) * 100))}%`, borderRadius: 99, background: d.delta >= 0 ? RED : GREEN }} />
                  </Box>
                </Box>
              ))}
              {!deltas.length && <Typography variant="caption" color="text.secondary">São necessários dois meses seguidos para comparar.</Typography>}
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
  const hierLevels = filters.visaoDiretoria ? [...VISAO_DIRETORIA_LEVELS] : ["organograma_nivel_1", "organograma_nivel_2", "organograma_nivel_3", "organograma_nivel_4", "organograma_nivel_5"];
  const defs = useMemo<QueryDef[]>(() => {
    if (filters.visaoDiretoria) {
      return [
        { id: "n1", rows: ["organograma_nivel_1"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
        { id: "n2", rows: ["organograma_nivel_2"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
        { id: "n4", rows: ["organograma_nivel_4"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
        { id: "n3", rows: ["organograma_nivel_3"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
        { id: "n5", rows: ["organograma_nivel_5"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
        { id: "n6", rows: ["organograma_nivel_6"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
        { id: "dept", rows: ["organograma"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
        { id: "cc", rows: ["centro_custo"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
        { id: "cloud", rows: ["cloud_origem"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
        { id: "hier", rows: [...VISAO_DIRETORIA_LEVELS], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 200 },
        { id: "gov", rows: ["organograma_nivel_4"], measures: [{ column: measureFor(filters), aggregation: "SUM" }, { column: "*", aggregation: "COUNT" }], filters: filtersOf(filters), row_limit: 100 },
      ];
    }
    return [
      { id: "n3", rows: ["organograma_nivel_3"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
      { id: "n4", rows: ["organograma_nivel_4"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
      { id: "n5", rows: ["organograma_nivel_5"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
      { id: "dept", rows: ["organograma"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
      { id: "cc", rows: ["centro_custo"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
      { id: "cloud", rows: ["cloud_origem"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
      { id: "hier", rows: ["organograma_nivel_1", "organograma_nivel_2", "organograma_nivel_3", "organograma_nivel_4", "organograma_nivel_5"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 200 },
      { id: "gov", rows: ["organograma_nivel_4"], measures: [{ column: measureFor(filters), aggregation: "SUM" }, { column: "*", aggregation: "COUNT" }], filters: filtersOf(filters), row_limit: 100 },
    ];
  }, [filterKey, filters.visaoDiretoria]);
  const { results, loading, error } = useTabQueries(table, defs, filterKey);

  const hierRows = rowsOf(results.hier);
  const govRows = rowsOf(results.gov);
  const govTotal = govRows.reduce((s, r) => s + Number(r.realizado || 0), 0);
  const govClassified = govRows.filter(r => r.organograma_nivel_4).reduce((s, r) => s + Number(r.realizado || 0), 0);
  const govShare = govTotal ? govClassified / govTotal * 100 : 0;

  return (
    <LoadingBlock loading={loading} error={error}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={6} md={3}><KpiCard label="Governança (nível diretoria)" value={pct(govShare)} sub="% do efetivado classificado" color={govShare >= 90 ? GREEN : govShare >= 70 ? YELLOW : RED} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Diretorias com custo registrado" value={String(govRows.length)} color={PURPLE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Níveis da estrutura" value={hierRows.length ? String(hierLevels.length) : "—"} sub={filters.visaoDiretoria ? `Visão Diretoria (${ORG.directorViewLevels})` : "nivel_1 .. nivel_5"} color={BLUE} /></Grid>
        <Grid item xs={6} md={3}><KpiCard label="Peso das 5 maiores diretorias" value={govRows.length ? pct(govRows.slice(0, 5).reduce((s, r) => s + Number(r.realizado || 0), 0) / (govTotal || 1) * 100) : "—"} color={ORANGE} /></Grid>
      </Grid>
      <Grid container spacing={1.5}>
        {filters.visaoDiretoria ? (
          <>
            {VISAO_DIRETORIA_LEVELS.map((field, i) => {
              const ids = ["n1", "n2", "n4", "n3", "n5", "n6"];
              const colors = [PURPLE, BLUE, "#64748B", GREEN, ORANGE, YELLOW];
              return (
                <Grid item xs={12} sm={6} md={4} key={field}>
                  <RankBars
                    title={`${DIM_LABELS[field]} (Nível ${nivelNum(field)})`}
                    rows={rowsOf(results[ids[i]])}
                    measure="realizado"
                    limit={12}
                    color={colors[i]}
                  />
                </Grid>
              );
            })}
          </>
        ) : (
          <>
            <Grid item xs={12} md={4}><RankBars title="VP (nível 3)" rows={rowsOf(results.n3)} measure="realizado" limit={10} color={PURPLE} /></Grid>
            <Grid item xs={12} md={4}><RankBars title="Diretorias (nível 4)" rows={rowsOf(results.n4)} measure="realizado" limit={12} color={BLUE} /></Grid>
            <Grid item xs={12} md={4}><RankBars title={`${ORG.level5} (Nível 5)`} rows={rowsOf(results.n5)} measure="realizado" limit={12} color={GREEN} /></Grid>
          </>
        )}
        <Grid item xs={12} md={4}><RankBars title="Áreas (legado)" rows={rowsOf(results.dept)} measure="realizado" limit={12} color={ORANGE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Centros de custo" rows={rowsOf(results.cc)} measure="realizado" limit={10} color={YELLOW} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Nuvens" rows={rowsOf(results.cloud)} measure="realizado" limit={10} color="#64748B" /></Grid>
        <Grid item xs={12}>
          <ChartCard
            title={filters.visaoDiretoria ? `Estrutura organizacional · visão diretoria — custo por caminho (${ORG.directorViewLevels})` : "Estrutura organizacional — custo por caminho (níveis 1 a 5)"}
            note="Top 200 caminhos por efetivado; use os filtros globais para reduzir."
          >
            <TableContainer sx={{ maxHeight: 460 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    {hierLevels.map(level => (
                      <TableCell key={level} sx={{ fontWeight: 900, fontSize: 11 }}>{DIM_LABELS[level] || level}</TableCell>
                    ))}
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Efetivado</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {hierRows.map((r, i) => (
                    <TableRow key={i} hover>
                      {hierLevels.map(level => (
                        <TableCell key={level} sx={{ fontSize: 12 }}>{esc(r[level])}</TableCell>
                      ))}
                      <TableCell sx={{ fontSize: 12, fontWeight: 900, whiteSpace: "nowrap" }}>{money(r.realizado)}</TableCell>
                    </TableRow>
                  ))}
                  {!hierRows.length && <TableRow><TableCell colSpan={hierLevels.length + 1} align="center" sx={{ color: "text.secondary" }}>Sem dados para o recorte selecionado.</TableCell></TableRow>}
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
  const heatOptions = useMemo(() => heatmapDimOptions(filters.visaoDiretoria), [filters.visaoDiretoria]);
  const [heatDim, setHeatDim] = useState("organograma_nivel_4");
  useEffect(() => {
    if (filters.visaoDiretoria) setHeatDim(VISAO_DIRETORIA_LEVELS[0]);
  }, [filters.visaoDiretoria]);
  const defs = useMemo<QueryDef[]>(() => [
    { id: "serv", rows: ["servicename"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "cat", rows: ["servicecategory"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "sub", rows: ["servicesubcategory"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "sku", rows: ["skumeter"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "reg", rows: ["regionname"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 12 },
    { id: "subacct", rows: ["subaccountname"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 10 },
    { id: "charge", rows: ["chargecategory"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 8 },
    { id: "heat", rows: [heatDim, "_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 800 },
  ], [filterKey, heatDim]);
  const { results, loading, error } = useTabQueries(table, defs, filterKey + heatDim);

  const heatRows = rowsOf(results.heat);
  const heatDims = [...new Set(heatRows.map(r => esc(r[heatDim])))].slice(0, 14);
  const heatMonths = [...new Set(heatRows.map(r => String(r._billing_period)))].sort();
  const z = heatDims.map(d => heatMonths.map(m => {
    const hit = heatRows.find(r => esc(r[heatDim]) === d && String(r._billing_period) === m);
    return hit ? Number(hit.realizado) || 0 : null;
  }));
  const maxHeat = Math.max(...z.flat().filter(v => v !== null).map(Number), 1);

  return (
    <LoadingBlock loading={loading} error={error}>
      <Grid container spacing={1.5}>
        <Grid item xs={12} md={4}><RankBars title="Serviços de maior custo" rows={rowsOf(results.serv)} measure="realizado" limit={12} color={PURPLE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Categorias de maior custo" rows={rowsOf(results.cat)} measure="realizado" limit={12} color={BLUE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Subcategorias de maior custo" rows={rowsOf(results.sub)} measure="realizado" limit={12} color={GREEN} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="SKUs de maior custo" rows={rowsOf(results.sku)} measure="realizado" limit={12} color={ORANGE} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Regiões de maior custo" rows={rowsOf(results.reg)} measure="realizado" limit={12} color={YELLOW} /></Grid>
        <Grid item xs={12} md={4}><RankBars title="Subcontas de maior custo" rows={rowsOf(results.subacct)} measure="realizado" limit={10} color="#64748B" /></Grid>
        <Grid item xs={12} md={4}>
          <ChartCard title="Peso dos tipos de carga">
            <Chart
              data={[{ labels: rowsOf(results.charge).map(r => esc(r.chargecategory)), values: rowsOf(results.charge).map(r => Number(r.realizado) || 0), type: "pie", hole: 0.5, marker: { colors: [PURPLE, ORANGE, BLUE, GREEN, YELLOW, RED, "#0F62FE"] } }]}
              layout={{ height: 320, margin: { l: 8, r: 8, t: 8, b: 8 }, legend: { orientation: "h", y: -0.3, font: { size: 10 } } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={8}>
          <ChartCard title={`Heatmap ${DIM_LABELS[heatDim] || heatDim} × Mês — Efetivado (BRL)`} note="Top 14 valores da dimensão; célula = soma mensal neste recorte.">
            <FormControl size="small" sx={{ mb: 1, minWidth: 180 }}>
              <Select value={heatDim} onChange={e => setHeatDim(e.target.value)}>
                {heatOptions.map(d => <MenuItem key={d} value={d}>{DIM_LABELS[d] || d}</MenuItem>)}
              </Select>
            </FormControl>
            <Chart
              data={[{ z, x: heatMonths.map(monthLabel), y: heatDims, type: "heatmap", colorscale: [[0, "#EEF4FF"], [0.5, PURPLE], [1, "#2b003d"]], hoverongaps: false, text: (z.map(row => row.map(v => v === null ? "sem dados" : money(v))) as string[][]) as any, texttemplate: "%{text}", hovertemplate: "%{y} · %{x}<br>%{text}<extra></extra>" }]}
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
  const dimOptions = useMemo(() => investigationDimOptions(filters.visaoDiretoria), [filters.visaoDiretoria]);
  const [dim, setDim] = useState("servicename");
  useEffect(() => {
    if (filters.visaoDiretoria) setDim(VISAO_DIRETORIA_LEVELS[0]);
  }, [filters.visaoDiretoria]);
  const defs = useMemo<QueryDef[]>(() => [
    { id: "serie", rows: [dim, "_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 2000 },
    { id: "cloud", rows: ["cloud_origem", "_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 400 },
    { id: "dirs", rows: [filters.visaoDiretoria ? VISAO_DIRETORIA_LEVELS[0] : "organograma_nivel_4", "_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 600 },
    { id: "pareto", rows: [dim], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersOf(filters), row_limit: 300 },
  ], [filterKey, dim, filters.visaoDiretoria]);
  const { results, loading, error } = useTabQueries(table, defs, filterKey + dim);

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
      if (conc > 70) items.push({ sev: "P2", title: "Concentração financeira", detail: `Top 5 ${(filters.visaoDiretoria ? DIM_LABELS[VISAO_DIRETORIA_LEVELS[0]] : "diretorias").toLowerCase()} respondem por ${pct(conc)} do custo.`, color: YELLOW });
    }
    if (paretoCount > 0 && paretoCount <= Math.max(8, Math.round(paretoRows.length * 0.15))) {
      items.push({ sev: "P3", title: "Pareto 80/20", detail: `${paretoCount} de ${paretoRows.length} itens de ${DIM_LABELS[dim] || dim} respondem por 80% do custo.`, color: GREEN });
    }
    return items.sort((a, b) => a.sev.localeCompare(b.sev)).slice(0, 10);
  }, [topGrow, anomalies, accelerated, results.dirs, paretoCount, paretoRows.length, dim, filters.visaoDiretoria]);

  return (
    <LoadingBlock loading={loading} error={error}>
      <Grid container spacing={1.5} mb={1.5}>
        <Grid item xs={12} md={4}>
          <Paper variant="outlined" sx={{ p: 2, height: "100%", background: "linear-gradient(135deg,#2b003d,#0B3FA8)", color: "#fff" }}>
            <Typography variant="subtitle2" fontWeight={900} mb={1}>Onde olhar primeiro</Typography>
            {attention.length === 0 && <Typography variant="caption">Nenhum sinal relevante no recorte.</Typography>}
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
          <ChartCard title={`Top Desvios MoM por ${DIM_LABELS[dim] || dim}`} note="Variação absoluta do último mês contra o anterior.">
            <FormControl size="small" sx={{ mb: 1, minWidth: 180 }}>
              <Select value={dim} onChange={e => setDim(e.target.value)}>
                {dimOptions.map(d => <MenuItem key={d} value={d}>{DIM_LABELS[d] || d}</MenuItem>)}
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
              layout={{ height: 320, margin: { l: 56, r: 24, t: 8, b: 100 }, xaxis: { tickangle: -40 }, yaxis: { title: { text: "Δ BRL" }, gridcolor: "#EEF3F8" } }}
              config={{ displayModeBar: false }} style={{ width: "100%" }} />
          </ChartCard>
        </Grid>
        <Grid item xs={12} md={6}>
          <ChartCard title="Anomalias (desvio do último mês em relação ao histórico)" note="|z| ≥ 2 indica variação fora do padrão histórico do item.">
            <TableContainer>
              <Table size="small">
                <TableHead><TableRow>{["Item", "Mês mais recente", "Histórico (média)", "Z-score", "Status"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}</TableRow></TableHead>
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
                  {!anomalies.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary" }}>Nada fora do padrão (ou histórico insuficiente).</TableCell></TableRow>}
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
                    { x: paretoRows.slice(0, 30).map(r => esc(r[dim])), y: paretoRows.slice(0, 30).map(r => Number(r.realizado) || 0), type: "bar", name: "Efetivado", marker: { color: PURPLE } },
                  ]}
                  layout={{ height: 260, margin: { l: 56, r: 20, t: 8, b: 100 }, xaxis: { tickangle: -45, tickfont: { size: 9 } }, yaxis: { title: { text: "BRL" }, gridcolor: "#EEF3F8" } }}
                  config={{ displayModeBar: false }} style={{ width: "100%" }} />
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  <b>{paretoCount}</b> de {paretoRows.length} itens concentram ≥ 80% do custo ({pct(paretoTotal ? cum / paretoTotal * 100 : 0, 0)} acumulado).
                </Typography>
              </ChartCard>
            </Grid>
            <Grid item xs={12}>
              <ChartCard title="Ritmo de crescimento acelerado (3 meses)" note="Diferença das variações mensais — aceleração real de crescimento.">
                <Stack spacing={1}>
                  {accelerated.slice(0, 6).map((a, i) => (
                    <Stack key={i} direction="row" justifyContent="space-between" sx={{ fontSize: 12, fontWeight: 800 }}>
                      <Typography variant="caption" sx={{ fontWeight: 850, maxWidth: "70%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{esc(a.key)}</Typography>
                      <Typography variant="caption" sx={{ fontWeight: 900, color: RED }}>+{money(a.accel!)}</Typography>
                    </Stack>
                  ))}
                  {!accelerated.length && <Typography variant="caption" color="text.secondary">Nenhuma aceleração relevante no recorte.</Typography>}
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
// ═══════════════════════════════════════════════════════════════════
// Aba 7 — Decisão (Decision Intelligence)
// Widgets portados do cockpit de referência (ABRIR_FINOPS.html):
// Executive Pulse, Risk Index, Waterfall executivo,
// Ponte financeira: realizado, previsão e gap, Attention, Cost Drivers, Anomaly, Forecast Bias,
// v42 (Run Rate/Reliability/Opportunity), Governance/Data Quality,
// Comparator e Decomposição hierárquica.
// ═══════════════════════════════════════════════════════════════════

// Dimensões disponíveis para os widgets de decisão (colunas válidas da Gold).
const DECISAO_DIMS_BASE: Array<{ field: string; label: string }> = [
  { field: "cloud_origem", label: "Cloud" },
  { field: "organograma_nivel_3", label: "Plataforma" },
  { field: "organograma_nivel_4", label: "Diretoria" },
  { field: "organograma_nivel_5", label: ORG.level5 },
  { field: "servicename", label: "Serviço" },
  { field: "servicecategory", label: "Categoria" },
  { field: "subaccountname", label: "Subconta" },
];

function buildDecisaoDims(visaoDiretoria: boolean): Array<{ field: string; label: string }> {
  if (!visaoDiretoria) return DECISAO_DIMS_BASE;
  return [
    { field: "cloud_origem", label: "Cloud" },
    ...visaoDiretoriaChain(),
    { field: "servicename", label: "Serviço" },
    { field: "servicecategory", label: "Categoria" },
    { field: "subaccountname", label: "Subconta" },
  ];
}

// Cadeia da decomposição hierárquica: ver buildDecompChain em HierarchicalDecompTree.

type DimRow = Record<string, any>;

function isMissingValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  const s = String(v).trim();
  if (!s) return true;
  const low = s.toLowerCase();
  return low === "não informado" || low === "nao informado" || low === "n/a" || low === "-" || low === "none" || low === "null";
}

/** Séries mensais {chave → {mês → valor}} restritas a um tipo (realizado/forecast). */
function monthlyByKey(rows: DimRow[], keyField: string, tipo: string): Map<string, Map<string, number>> {
  const map = new Map<string, Map<string, number>>();
  for (const r of rows) {
    if (String(r.tipo ?? "") !== tipo) continue;
    const k = String(r[keyField] ?? "Sem classificação");
    const m = String(r._billing_period ?? "");
    if (!m) continue;
    if (!map.has(k)) map.set(k, new Map());
    const mm = map.get(k)!;
    mm.set(m, (mm.get(m) || 0) + Number(r.realizado ?? 0));
  }
  return map;
}

function monthTotals(rows: DimRow[], tipo: string): Map<string, number> {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (String(r.tipo ?? "") !== tipo) continue;
    const k = String(r._billing_period ?? "");
    if (!k) continue;
    m.set(k, (m.get(k) || 0) + Number(r.realizado ?? 0));
  }
  return m;
}

function severityChip(sev: "P1" | "P2" | "P3") {
  const color = sev === "P1" ? RED : sev === "P2" ? YELLOW : GREEN;
  return <Box component="span" sx={{ display: "inline-block", px: 0.8, py: 0.2, borderRadius: 99, bgcolor: color, color: "#fff", fontSize: 10, fontWeight: 950, lineHeight: 1.4 }}>{sev}</Box>;
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

function Decisao({ table, filters, filterKey, setFilters }: {
  table: string; filters: Filters; filterKey: string;
  setFilters: React.Dispatch<React.SetStateAction<Filters>>;
}) {
  const decisaoDims = useMemo(() => buildDecisaoDims(filters.visaoDiretoria), [filters.visaoDiretoria]);
  const decompChain = useMemo(() => buildDecompChain(filters.visaoDiretoria), [filters.visaoDiretoria]);
  const govDeptLevels = useMemo(
    () => (filters.visaoDiretoria
      ? VISAO_DIRETORIA_LEVELS.map((field, index) => ({ id: `gov${index + 1}`, field, label: DIM_LABELS[field] || field }))
      : [
        { id: "gov1", field: "organograma_nivel_3", label: "Plataforma" },
        { id: "gov2", field: "organograma_nivel_4", label: "Diretoria" },
      ]),
    [filters.visaoDiretoria],
  );
  const [dim, setDim] = useState("organograma_nivel_4");
  const [compA, setCompA] = useState("");
  const [compB, setCompB] = useState("");
  useEffect(() => {
    if (filters.visaoDiretoria) {
      setDim(VISAO_DIRETORIA_LEVELS[0]);
    }
  }, [filters.visaoDiretoria]);
  const dimLabel = decisaoDims.find(d => d.field === dim)?.label || dim;

  // ── consultas base (independentes da árvore) ──
  const baseDefs = useMemo<QueryDef[]>(() => {
    const global = filtersOf(filters);
    const realiz = [...global, { column: "tipo", operator: "IN" as const, values: ["realizado"] }];
    const govDefs: QueryDef[] = [
      ...govDeptLevels.map(g => ({ id: g.id, rows: [g.field], measures: [{ column: measureFor(filters), aggregation: "SUM" as const }], filters: realiz, row_limit: 8000 })),
      { id: "govServ", rows: ["servicename"], measures: [{ column: measureFor(filters), aggregation: "SUM" as const }], filters: realiz, row_limit: 8000 },
      { id: "govCat", rows: ["servicecategory"], measures: [{ column: measureFor(filters), aggregation: "SUM" as const }], filters: realiz, row_limit: 8000 },
      { id: "govSub", rows: ["subaccountname"], measures: [{ column: measureFor(filters), aggregation: "SUM" as const }], filters: realiz, row_limit: 8000 },
      { id: "govCloud", rows: ["cloud_origem"], measures: [{ column: measureFor(filters), aggregation: "SUM" as const }], filters: realiz, row_limit: 8000 },
    ];
    return [
      { id: "pulse", rows: ["_billing_period", "tipo"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: global, row_limit: 400 },
      { id: "dim", rows: [dim, "_billing_period", "tipo"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: global, row_limit: 10000 },
      { id: "dimCloud", rows: ["cloud_origem", "_billing_period", "tipo"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: global, row_limit: 2000 },
      { id: "dimServ", rows: ["servicename", "_billing_period", "tipo"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: global, row_limit: 10000 },
      ...govDefs,
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, dim, filters.visaoDiretoria, govDeptLevels]);
  const { results: base, loading: baseLoading, error: baseError } = useTabQueries(table, baseDefs, "decisao-base-" + filterKey + dim);

  // ── séries mensais globais ──
  const pulseRows = rowsOf(base.pulse);
  const realizByMonth = useMemo(() => monthTotals(pulseRows, "realizado"), [base.pulse]);
  const fcByMonth = useMemo(() => monthTotals(pulseRows, "forecast"), [base.pulse]);
  const realizMonths = useMemo(() => [...realizByMonth.keys()].sort(), [realizByMonth]);
  const latestMonth = realizMonths[realizMonths.length - 1];
  const prevMonth = realizMonths[realizMonths.length - 2];
  const latestReal = latestMonth ? (realizByMonth.get(latestMonth) || 0) : 0;
  const prevReal = prevMonth ? (realizByMonth.get(prevMonth) || 0) : 0;
  const momPct = prevReal ? ((latestReal - prevReal) / Math.abs(prevReal)) * 100 : null;
  const fcMonths = useMemo(() => [...fcByMonth.keys()].sort(), [fcByMonth]);
  const latestFc = fcMonths.length ? (fcByMonth.get(fcMonths[fcMonths.length - 1]) || 0) : 0;
  const gap = latestFc - latestReal;
  const gapPct = latestReal ? (gap / Math.abs(latestReal)) * 100 : 0;
  let globalAcc: number | null = null;
  for (let i = realizMonths.length - 1; i >= 0; i--) {
    const fc = fcByMonth.get(realizMonths[i]) || 0;
    if (fc > 0) { globalAcc = Math.max(0, 100 - (Math.abs(fc - (realizByMonth.get(realizMonths[i]) || 0)) / Math.abs(fc)) * 100); break; }
  }

  const dimRowsAll = rowsOf(base.dim);
  const cloudRowsAll = rowsOf(base.dimCloud);
  const servRowsAll = rowsOf(base.dimServ);

  // ── concentração Top 5 (dimensão selecionada, última competência) ──
  const top5Pct = useMemo(() => {
    if (!latestMonth) return 0;
    const byKey = monthlyByKey(dimRowsAll, dim, "realizado");
    const vals = [...byKey.values()].map(m => m.get(latestMonth) || 0).sort((a, b) => Math.abs(b) - Math.abs(a));
    const total = vals.reduce((s, v) => s + Math.abs(v), 0);
    return total ? (vals.slice(0, 5).reduce((s, v) => s + Math.abs(v), 0) / total) * 100 : 0;
  }, [dimRowsAll, dim, latestMonth]);

  // ── governança / qualidade de dados ──
  const govDims = useMemo(() => [
    ...govDeptLevels,
    { id: "govServ", field: "servicename", label: "Serviço" },
    { id: "govCat", field: "servicecategory", label: "Categoria" },
    { id: "govSub", field: "subaccountname", label: "Subconta" },
    { id: "govCloud", field: "cloud_origem", label: "Cloud" },
  ], [govDeptLevels]);
  const gov = useMemo(() => {
    const rowsPer = govDims.map(g => {
      const rows = rowsOf(base[g.id as keyof typeof base] as GoldQueryResult | null | undefined);
      let spend = 0, missingSpend = 0, missingRows = 0, cnt = 0;
      for (const r of rows) {
        const v = Math.abs(Number(r.realizado || 0));
        spend += v; cnt++;
        if (isMissingValue(r[g.field])) { missingSpend += v; missingRows++; }
      }
      return { ...g, spend, missingSpend, missingRows, spendPct: spend ? (missingSpend / spend) * 100 : 0, rowPct: cnt ? (missingRows / cnt) * 100 : 0 };
    });
    const score = 100 - rowsPer.reduce((s, x) => s + x.spendPct, 0) / Math.max(1, rowsPer.length);
    return { score, rowsPer: [...rowsPer].sort((a, b) => b.spendPct - a.spendPct) };
  }, [base, govDims]);
  const govScore = gov.score;

  // ── custo de IA (heurística por nome de serviço) ──
  const aiCost = useMemo(() => {
    let s = 0;
    for (const r of rowsOf(base.govServ)) {
      if (/openai|gpt|copilot|machine learning|cognitive|bedrock|anthropic|azure ai/i.test(String(r.servicename || ""))) s += Math.abs(Number(r.realizado || 0));
    }
    return s;
  }, [base.govServ]);

  // ── risk index ──
  const riskIndex = Math.round((100 - (globalAcc ?? 100)) * 0.4 + (100 - govScore) * 0.25 + top5Pct * 0.2 + (aiCost > 0 ? 15 : 5));
  const riskStatus = riskIndex > 60 ? "red" : riskIndex > 30 ? "yellow" : "green";
  const riskHex = riskStatus === "red" ? RED : riskStatus === "yellow" ? YELLOW : GREEN;
  const riskLabel = riskStatus === "red" ? "AÇÃO URGENTE" : riskStatus === "yellow" ? "MONITORAR" : "CONTROLADO";
  const riskShort = riskStatus === "red" ? "ALTO" : riskStatus === "yellow" ? "MÉDIO" : "BAIXO";

  // ── prioritization matrix ──
  const matrix = useMemo(() => {
    if (!latestMonth) return { pts: [], mc: 0, mg: 0 };
    const byKey = monthlyByKey(dimRowsAll, dim, "realizado");
    const pts: Array<{ key: string; cost: number; growth: number; cur: number }> = [];
    for (const [key, months] of byKey) {
      const cur = months.get(latestMonth) || 0;
      const prev = prevMonth ? (months.get(prevMonth) || 0) : 0;
      pts.push({ key, cost: Math.abs(cur), growth: prev ? ((cur - prev) / Math.abs(prev)) * 100 : 0, cur });
    }
    pts.sort((a, b) => b.cost - a.cost);
    const costs = pts.map(p => p.cost).sort((a, b) => a - b);
    const growths = pts.map(p => p.growth).sort((a, b) => a - b);
    const med = (a: number[]) => (a.length ? a[Math.floor(a.length / 2)] : 0);
    return { pts: pts.slice(0, 30), mc: med(costs), mg: med(growths) };
  }, [dimRowsAll, dim, latestMonth, prevMonth]);
  const qColor = (cost: number, growth: number) =>
    cost >= matrix.mc && growth >= matrix.mg ? "#dc2626" :
    cost < matrix.mc && growth >= matrix.mg ? "#d97706" :
    cost >= matrix.mc ? "#0F62FE" : "#526777";

  // ── waterfall executivo de variação ──
  const waterfall = useMemo(() => {
    if (!latestMonth || !prevMonth) return [];
    const byKey = monthlyByKey(dimRowsAll, dim, "realizado");
    const deltas: Array<{ key: string; delta: number }> = [];
    for (const [key, months] of byKey) {
      const cur = months.get(latestMonth) || 0;
      const prev = months.get(prevMonth) || 0;
      const d = cur - prev;
      if (Math.abs(d) > 1e-9) deltas.push({ key, delta: d });
    }
    deltas.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    const top = deltas.slice(0, 7);
    const topSum = top.reduce((s, x) => s + x.delta, 0);
    const totalDelta = latestReal - prevReal;
    const other = totalDelta - topSum;
    const steps: Array<{ key: string; value: number; kind: "total" | "inc" | "dec"; contrib?: number }> = [
      { key: prevMonth, value: prevReal, kind: "total" },
      ...top.map(x => ({ key: x.key, value: x.delta, kind: (x.delta >= 0 ? "inc" : "dec") as "inc" | "dec", contrib: totalDelta ? (x.delta / totalDelta) * 100 : 0 })),
    ];
    if (Math.abs(other) > 1e-9) steps.push({ key: "Demais motores", value: other, kind: other >= 0 ? "inc" : "dec" });
    steps.push({ key: latestMonth, value: latestReal, kind: "total" });
    return steps;
  }, [dimRowsAll, dim, latestMonth, prevMonth, latestReal, prevReal]);

  // ── attention ──
  const attention = useMemo(() => {
    const items: Array<{ sev: "P1" | "P2" | "P3"; title: string; desc: string; impact: string }> = [];
    if (globalAcc != null && globalAcc < 85) items.push({ sev: globalAcc < 70 ? "P1" : "P2", title: "Acerto da previsão", desc: `Confiabilidade global abaixo de 85% (${pct(globalAcc)}).`, impact: pct(globalAcc) });
    if (momPct != null && Math.abs(momPct) >= 15) items.push({ sev: Math.abs(momPct) >= 25 ? "P1" : "P2", title: "Mudança mensal material", desc: `Última competência (${monthLabel(latestMonth)}) variou ${pct(momPct)} contra a anterior.`, impact: compact(Math.abs(latestReal - prevReal)) });
    if (top5Pct > 70) items.push({ sev: "P2", title: "Concentração Financeira", desc: `Top 5 de ${dimLabel} concentram ${pct(top5Pct)} do custo.`, impact: "Top 5" });
    if (govScore < 90) items.push({ sev: govScore < 75 ? "P1" : "P2", title: "Governance Completeness", desc: "Classificação incompleta reduz rastreabilidade executiva.", impact: pct(govScore) });
    const totalDelta = latestReal - prevReal;
    const growth = waterfall.find(s => s.kind === "inc" && s.contrib != null && s.contrib > 35 && s.key !== "Demais motores");
    if (growth && totalDelta > 0) items.push({ sev: (growth.contrib || 0) > 55 ? "P1" : "P2", title: `Driver de crescimento: ${growth.key}`, desc: `${dimLabel} responde por ${pct(growth.contrib || 0)} do aumento mensal.`, impact: compact(growth.value) });
    return items.sort((a, b) => a.sev.localeCompare(b.sev)).slice(0, 10);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dimRowsAll, dim, latestMonth, prevMonth, top5Pct, govScore, globalAcc, momPct, waterfall, latestReal, prevReal]);

  // ── cost drivers ──
  function driversOf(rows: DimRow[], field: string) {
    const byKey = monthlyByKey(rows, field, "realizado");
    const items: Array<{ key: string; cur: number; prev: number; delta: number; share: number; contrib: number }> = [];
    for (const [key, months] of byKey) {
      const cur = months.get(latestMonth) || 0;
      const prev = prevMonth ? (months.get(prevMonth) || 0) : 0;
      items.push({ key, cur, prev, delta: cur - prev, share: 0, contrib: 0 });
    }
    items.sort((a, b) => Math.abs(b.cur) - Math.abs(a.cur));
    const totalCur = items.reduce((s, x) => s + Math.abs(x.cur), 0) || 1;
    const totalDelta = items.reduce((s, x) => s + x.delta, 0);
    return items.slice(0, 7).map(x => ({ ...x, share: (Math.abs(x.cur) / totalCur) * 100, contrib: totalDelta ? (x.delta / totalDelta) * 100 : 0 }));
  }
  const driversDim = useMemo(() => driversOf(dimRowsAll, dim), [dimRowsAll, dim, latestMonth, prevMonth]);
  const driversCloud = useMemo(() => driversOf(cloudRowsAll, "cloud_origem"), [cloudRowsAll, latestMonth, prevMonth]);

  // ── anomalia & aceleração ──
  const anomaly = useMemo(() => {
    if (!latestMonth) return { anomalyList: [], accelList: [] };
    const byKey = monthlyByKey(dimRowsAll, dim, "realizado");
    const all: Array<{ key: string; z: number; current: number; acceleration: number }> = [];
    for (const [key, months] of byKey) {
      const series = [...months.entries()].sort((a, b) => a[0].localeCompare(b[0]));
      if (series.length < 4) continue;
      const hist = series.slice(0, -1).map(x => x[1]);
      const mean = hist.reduce((s, v) => s + v, 0) / hist.length;
      const std = Math.sqrt(hist.reduce((s, v) => s + (v - mean) ** 2, 0) / hist.length) || 0;
      const last = series[series.length - 1][1];
      const z = std ? (last - mean) / std : 0;
      const m1 = last, m2 = series[series.length - 2]?.[1] ?? m1, m3 = series[series.length - 3]?.[1] ?? m2;
      all.push({ key, z, current: last, acceleration: (m1 - m2) - (m2 - m3) });
    }
    return {
      anomalyList: all.filter(x => Math.abs(x.z) >= 2).sort((a, b) => Math.abs(b.z) - Math.abs(a.z)).slice(0, 10),
      accelList: [...all].sort((a, b) => Math.abs(b.acceleration) - Math.abs(a.acceleration)).slice(0, 8),
    };
  }, [dimRowsAll, dim, latestMonth, prevMonth]);

  // ── forecast bias & reliability ──
  const biasRows = useMemo(() => {
    const real = monthlyByKey(dimRowsAll, dim, "realizado");
    const fc = monthlyByKey(dimRowsAll, dim, "forecast");
    const out: Array<{ key: string; accuracy: number; bias: number; over: number; under: number; absErr: number; n: number }> = [];
    for (const [key, fcMonths] of fc) {
      const realMonths = real.get(key);
      if (!realMonths) continue;
      let accSum = 0, biasSum = 0, over = 0, under = 0, absErr = 0, n = 0;
      for (const [m, fcv] of fcMonths) {
        const rev = realMonths.get(m);
        if (rev == null || fcv === 0) continue;
        const err = fcv - rev;
        accSum += Math.max(0, 100 - (Math.abs(err) / Math.abs(fcv)) * 100);
        biasSum += err / Math.abs(fcv);
        if (err >= 0) over++; else under++;
        absErr += Math.abs(err);
        n++;
      }
      if (n) out.push({ key, accuracy: accSum / n, bias: (biasSum / n) * 100, over, under, absErr, n });
    }
    return out.sort((a, b) => a.accuracy - b.accuracy).slice(0, 10);
  }, [dimRowsAll, dim]);

  // ── opportunity gap (forecast - realizado por entidade) ──
  function oppOf(rows: DimRow[], field: string) {
    const real = monthlyByKey(rows, field, "realizado");
    const fc = monthlyByKey(rows, field, "forecast");
    const out: Array<{ key: string; gap: number; forecast: number }> = [];
    for (const [key, fcMonths] of fc) {
      const realMonths = real.get(key);
      if (!realMonths) continue;
      let sr = 0, sf = 0;
      for (const [m, v] of realMonths) if (fcMonths.has(m)) sr += v;
      for (const [m, v] of fcMonths) if (realMonths.has(m)) sf += v;
      out.push({ key, gap: sf - sr, forecast: sf });
    }
    return out.filter(x => x.gap > 0).sort((a, b) => b.gap - a.gap).slice(0, 8);
  }
  const oppDim = useMemo(() => oppOf(dimRowsAll, dim), [dimRowsAll, dim]);
  const oppServ = useMemo(() => oppOf(servRowsAll, "servicename"), [servRowsAll]);

  // ── run rate ──
  const runRate = useMemo(() => {
    const vals = realizMonths.map(m => realizByMonth.get(m) || 0);
    const avg = vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : 0;
    return { avgReal: avg, annualized: avg * 12, meses: vals.length };
  }, [realizMonths, realizByMonth]);

  // ── comparator ──
  const entities = useMemo(() => {
    if (!latestMonth) return [];
    const byKey = monthlyByKey(dimRowsAll, dim, "realizado");
    return [...byKey.entries()]
      .map(([key, months]) => ({ key, latest: months.get(latestMonth) || 0, series: [...months.entries()].sort((a, b) => a[0].localeCompare(b[0])) }))
      .sort((a, b) => b.latest - a.latest)
      .slice(0, 30);
  }, [dimRowsAll, dim, latestMonth]);
  const aKey = compA || entities[0]?.key || "";
  const bKey = compB || (entities[1] && entities[1].key !== aKey ? entities[1].key : entities[2]?.key || entities[0]?.key || "") || "";
  const entA = entities.find(e => e.key === aKey);
  const entB = entities.find(e => e.key === bKey);
  const compMetric = (e: { key: string; latest: number; series: Array<[string, number]> } | undefined) => {
    if (!e) return null;
    const last = e.series[e.series.length - 1];
    const prev = e.series[e.series.length - 2];
    const mom = prev ? ((last[1] - prev[1]) / Math.abs(prev[1])) * 100 : null;
    const share = latestReal ? (Math.abs(last[1]) / Math.abs(latestReal)) * 100 : 0;
    return { mom, share };
  };
  const mA = compMetric(entA);
  const mB = compMetric(entB);

  const applyTreeContext = (treePath: DecompPathStep[]) => {
    const extra: Record<string, string[]> = {};
    const path: string[] = [];
    for (const p of treePath) {
      const m = p.field.match(/^organograma_nivel_(\d+)$/);
      if (m) path[Number(m[1]) - 1] = p.value;
      else extra[p.field] = [p.value];
    }
    setFilters(f => ({ ...f, path: path.filter(Boolean), deptId: null, extra }));
  };

  // ── ações do risk board ──
  const actions: Array<[string, string, string, string]> = [];
  if (Math.abs(gapPct) > 10) actions.push(["Previsão x efetivado", compact(gap), `Desvio de ${pct(gapPct)}`, "Revisar premissas e maiores desvios."]);
  if (top5Pct > 50) actions.push(["Peso das 5 maiores", pct(top5Pct), "Dependência de custo relevante", "Priorizar a governança dos itens de maior peso."]);
  if (aiCost > 0) actions.push(["Custo de IA", compact(aiCost), "Consumo de IA mapeado", "Validar retorno e tendência."]);

  const bridgeSteps = [
    { key: "Efetivado", value: latestReal, kind: "total" as const },
    { key: "Previsão (Δ)", value: latestFc - latestReal, kind: (latestFc - latestReal >= 0 ? "inc" : "dec") as "inc" | "dec" },
    { key: "Forecast", value: latestFc, kind: "total" as const },
  ];

  const riskCards = [
    ["Acerto da previsão", globalAcc != null ? pct(globalAcc) : "N/A", globalAcc == null ? "green" : globalAcc < 85 ? "red" : globalAcc < 95 ? "yellow" : "green"],
    ["Cobertura de governança", pct(govScore), govScore < 70 ? "red" : govScore < 90 ? "yellow" : "green"],
    ["Concentração de custo", pct(top5Pct), top5Pct > 70 ? "red" : top5Pct > 50 ? "yellow" : "green"],
    ["Custo de IA", aiCost > 0 ? compact(aiCost) : "—", aiCost > 0 ? "yellow" : "green"],
  ] as Array<[string, string, "red" | "yellow" | "green"]>;
  const riskColorOf = (c: "red" | "yellow" | "green") => (c === "red" ? RED : c === "yellow" ? YELLOW : GREEN);

  return (
    <Box>
      <LoadingBlock loading={baseLoading} error={baseError}>
        <Stack direction="row" flexWrap="wrap" gap={1.2} alignItems="center" sx={{ mb: 2 }}>
          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel>Eixo de análise</InputLabel>
            <Select label="Eixo de análise" value={dim} onChange={e => { setDim(e.target.value); setCompA(""); setCompB(""); }}>
              {decisaoDims.map(d => <MenuItem key={d.field} value={d.field}>{d.label}</MenuItem>)}
            </Select>
          </FormControl>
          <Typography variant="caption" sx={{ color: MUT, fontWeight: 800 }}>
            Escopo: {displayOrgPath(filters) === ORG.allNodes ? "Portfólio inteiro" : displayOrgPath(filters)}{Object.keys(filters.extra).length ? " · contexto da árvore" : ""} · {monthLabel(latestMonth) || "—"} vs {monthLabel(prevMonth) || "—"}
          </Typography>
        </Stack>

        {!latestMonth ? (
          <Alert severity="info">Não há meses com efetivado neste recorte. Ajuste os filtros de período ou hierarquia, ou clique em “Limpar”.</Alert>
        ) : (
        <>
        {/* 01 · Executive Pulse */}
        <SectionPanel title="Posição financeira agora" subtitle={`Executive Pulse · Efetivado ${monthLabel(latestMonth) || "—"} · Forecast horizonte ${monthLabel(fcMonths[fcMonths.length - 1]) || "—"}`}>
          <Grid container spacing={1.5}>
            <Grid item xs={6} sm={4} md={2}><KpiCard label="Efetivado" value={compact(latestReal)} sub={`${monthLabel(latestMonth)} · ${momPct == null ? "sem base" : `${momPct >= 0 ? "+" : ""}${pct(momPct)} MoM`}`} color={PURPLE} full={moneyFull(latestReal)} /></Grid>
            <Grid item xs={6} sm={4} md={2}><KpiCard label="Previsão" value={compact(latestFc)} sub={`${monthLabel(fcMonths[fcMonths.length - 1]) || "—"}`} color={ORANGE} full={moneyFull(latestFc)} /></Grid>
            <Grid item xs={6} sm={4} md={2}><KpiCard label="Desvio da previsão" value={`${gap >= 0 ? "+" : ""}${compact(gap)}`} sub={latestReal ? `${gapPct >= 0 ? "+" : ""}${pct(gapPct)} do efetivado` : "sem base"} color={gap >= 0 ? RED : GREEN} full={moneyFull(gap)} /></Grid>
            <Grid item xs={6} sm={4} md={2}><KpiCard label="Tendência por mês" value={momPct == null ? "N/A" : `${momPct >= 0 ? "+" : ""}${pct(momPct)}`} sub="última vs anterior" color={BLUE} /></Grid>
            <Grid item xs={6} sm={4} md={2}><KpiCard label="Peso das 5 maiores" value={pct(top5Pct)} sub={dimLabel} color="#6EA8FE" /></Grid>
            <Grid item xs={6} sm={4} md={2}><KpiCard label="Acerto da previsão" value={globalAcc != null ? pct(globalAcc) : "N/A"} sub="global (último mês com ambos)" color={globalAcc != null && globalAcc < 85 ? RED : GREEN} /></Grid>
          </Grid>
        </SectionPanel>

        {/* 02 · FinOps Risk Index + Executive Action Center */}
        <SectionPanel title="Índice de risco e próximos passos" subtitle="Ponderação: confiabilidade 40% · governança 25% · concentração 20% · IA 15%">
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={4}>
              <Box sx={{ borderRadius: 2, p: 2, color: "#fff", background: `radial-gradient(circle at 50% 40%, #6EA8FE 0%, #0B3FA8 40%, #162B3A 90%)`, textAlign: "center" }}>
                <Typography variant="caption" sx={{ fontWeight: 900, opacity: 0.85 }}>Índice de risco FinOps</Typography>
                <Typography variant="h3" sx={{ fontWeight: 950, mt: 0.5 }}>{riskIndex}<Typography component="span" variant="body2" sx={{ fontWeight: 900, opacity: 0.7 }}>/100</Typography></Typography>
                <Box component="span" sx={{ display: "inline-block", mt: 1, px: 1.4, py: 0.4, borderRadius: 99, bgcolor: riskHex, fontWeight: 950, fontSize: 12 }}>{riskShort} · {riskLabel}</Box>
              </Box>
            </Grid>
            <Grid item xs={12} md={8}>
              <Grid container spacing={1}>
                {riskCards.map((c, i) => (
                  <Grid item xs={6} sm={3} key={i}>
                    <Paper variant="outlined" sx={{ p: 1.4, borderTop: `3px solid ${riskColorOf(c[2])}` }}>
                      <Typography variant="caption" sx={{ fontWeight: 950, color: MUT, textTransform: "uppercase", fontSize: 10 }}>{c[0]}</Typography>
                      <Typography variant="h6" sx={{ fontWeight: 950, color: INK }}>{c[1]}</Typography>
                    </Paper>
                  </Grid>
                ))}
              </Grid>
              <Box sx={{ mt: 1 }}>
                {actions.length ? actions.slice(0, 3).map((a, i) => (
                  <Box key={i} sx={{ display: "flex", gap: 1, alignItems: "flex-start", borderLeft: `4px solid ${i === 0 ? RED : YELLOW}`, bgcolor: "#FAFBFD", borderRadius: 1, p: 1, mb: 0.6 }}>
                    <Box component="span" sx={{ bgcolor: i === 0 ? RED : YELLOW, color: "#fff", borderRadius: 99, px: 0.9, py: 0.15, fontSize: 10, fontWeight: 950, whiteSpace: "nowrap" }}>{i === 0 ? "AÇÃO URGENTE" : "MONITORAR"}</Box>
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 950, color: INK }}>{a[0]} · {a[1]}</Typography>
                      <Typography variant="caption" sx={{ color: MUT, fontWeight: 700 }}>{a[2]} — <b>Ação sugerida:</b> {a[3]}</Typography>
                    </Box>
                  </Box>
                )) : <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 800 }}>Nenhum alerta crítico no recorte.</Typography>}
              </Box>
            </Grid>
          </Grid>
        </SectionPanel>

        {/* 03 · O que merece atenção agora? */}
        <SectionPanel title="Onde agir primeiro" subtitle="Sinais priorizados: P1 (agir já) a P3 (monitorar)">
          <Stack spacing={0.8}>
            {attention.length ? attention.map((a, i) => {
              const sevColor = a.sev === "P1" ? RED : a.sev === "P2" ? YELLOW : GREEN;
              return (
                <Box key={i} sx={{ display: "flex", gap: 1, alignItems: "flex-start", border: `1px solid ${sevColor}55`, borderRadius: 1.5, p: 1, bgcolor: `${sevColor}0d` }}>
                  {severityChip(a.sev)}
                  <Box flex={1}>
                    <Typography variant="body2" sx={{ fontWeight: 900, color: INK }}>{a.title}</Typography>
                    <Typography variant="caption" sx={{ color: MUT, fontWeight: 700 }}>{a.desc}</Typography>
                  </Box>
                  <Typography variant="body2" sx={{ fontWeight: 950, color: sevColor, whiteSpace: "nowrap" }}>{a.impact}</Typography>
                </Box>
              );
            }) : <Typography variant="body2" color="text.secondary">Nenhum alerta material no recorte.</Typography>}
          </Stack>
        </SectionPanel>

        {/* 05 · Ponte de variação do custo */}
        <SectionPanel title="Ponte da variação do custo" subtitle={`${monthLabel(prevMonth)} → ${monthLabel(latestMonth)} por ${dimLabel} (top drivers por variação absoluta)`}>
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={8}>
              {waterfall.length < 2 ? <Typography variant="body2" color="text.secondary">Nenhuma variação no recorte.</Typography> : (
                <Chart
                  data={[{
                    type: "waterfall",
                    x: waterfall.map(s => s.key),
                    y: waterfall.map(s => s.value),
                    measure: waterfall.map(s => (s.kind === "total" ? "absolute" : "relative")),
                    increasing: { marker: { color: "#d92d20" } },
                    decreasing: { marker: { color: "#0b9f6e" } },
                    totals: { marker: { color: "#162B3A" } },
                    connector: { line: { color: "#C5D2DE", dash: "dot" } },
                    text: waterfall.map(s => compact(s.value)),
                    textposition: "outside",
                    customdata: waterfall.map(s => s.contrib != null ? `Peso: ${pct(s.contrib)} do MoM` : ""),
                    hovertemplate: "%{x}<br>%{y}<br>%{customdata}<extra></extra>",
                  }]}
                  layout={{ height: 360, margin: { l: 66, r: 20, t: 20, b: 110 }, xaxis: { tickangle: -30, automargin: true }, yaxis: { title: { text: "BRL" }, gridcolor: "#EEF3F8" }, showlegend: false }}
                  config={{ displayModeBar: false }} style={{ width: "100%" }} />
              )}
              <Typography variant="caption" sx={{ color: MUT, fontWeight: 700 }}>“Outros” agrega a diferença entre os 7 maiores motores e a variação total. Não é uma categoria da fonte.</Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Stack spacing={1}>
                <KpiCard label="Competência anterior" value={compact(prevReal)} sub={monthLabel(prevMonth)} color={INK} full={moneyFull(prevReal)} />
                <KpiCard label="Saldo da variação" value={`${momPct == null ? "—" : `${momPct >= 0 ? "+" : ""}${compact(latestReal - prevReal)} (${momPct >= 0 ? "+" : ""}${pct(momPct)})`}`} sub="MoM" color={momPct != null && momPct >= 0 ? RED : GREEN} full={moneyFull(latestReal - prevReal)} />
                <KpiCard label="Competência atual" value={compact(latestReal)} sub={monthLabel(latestMonth)} color={PURPLE} full={moneyFull(latestReal)} />
              </Stack>
            </Grid>
          </Grid>
        </SectionPanel>

        {/* 06 · Ponte financeira: realizado, previsão e gap */}
        <SectionPanel title="Ponte financeira: efetivado, previsão e desvio" subtitle="Efetivado → previsão → desvio, neste recorte">
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={8}>
              <Chart
                data={[{
                  type: "waterfall",
                  x: bridgeSteps.map(s => s.key),
                  y: bridgeSteps.map(s => s.value),
                  measure: bridgeSteps.map(s => (s.kind === "total" ? "absolute" : "relative")),
                  increasing: { marker: { color: ORANGE } },
                  decreasing: { marker: { color: RED } },
                  totals: { marker: { color: INK } },
                  connector: { line: { color: "#C5D2DE", dash: "dot" } },
                  text: bridgeSteps.map(s => compact(s.value)),
                  textposition: "outside",
                }]}
                layout={{ height: 300, margin: { l: 66, r: 20, t: 20, b: 40 }, yaxis: { title: { text: "BRL" }, gridcolor: "#EEF3F8" }, showlegend: false }}
                config={{ displayModeBar: false }} style={{ width: "100%" }} />
            </Grid>
            <Grid item xs={12} md={4}>
              <Stack spacing={1}>
                <KpiCard label="Desvio da previsão" value={`${gap >= 0 ? "+" : ""}${compact(gap)}`} sub={`${gapPct >= 0 ? "+" : ""}${pct(gapPct)} do efetivado`} color={gap >= 0 ? RED : GREEN} full={moneyFull(gap)} />
                <KpiCard label="Ritmo de referência" value={compact(runRate.annualized)} sub={`média ${compact(runRate.avgReal)}/mês × 12 · ${runRate.meses} mês(es) efetivado`} color={BLUE} full={moneyFull(runRate.annualized)} />
              </Stack>
            </Grid>
          </Grid>
        </SectionPanel>

        {/* 07 · Principais motores do custo */}
        <SectionPanel title="Motores de maior custo" subtitle={`Maiores motores por ${dimLabel} e por nuvem — custo, participação, Δ e peso na variação mensal`}>
          <Grid container spacing={1.5}>
            {[
              { title: dimLabel, rows: driversDim },
              { title: "Cloud", rows: driversCloud },
            ].map((card, ci) => (
              <Grid item xs={12} md={6} key={ci}>
                <TableContainer>
                  <Table size="small">
                    <TableHead><TableRow>
                      {[card.title, "Custo", "Share", "Δ", "Contribuição"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
                    </TableRow></TableHead>
                    <TableBody>
                      {card.rows.map((x, i) => (
                        <TableRow key={i}>
                          <TableCell sx={{ fontSize: 12, fontWeight: 850 }}>{x.key}</TableCell>
                          <TableCell sx={{ fontSize: 12, fontWeight: 900, whiteSpace: "nowrap" }}>{money(x.cur)}</TableCell>
                          <TableCell sx={{ fontSize: 12 }}>{pct(x.share)}</TableCell>
                          <TableCell sx={{ fontSize: 12, fontWeight: 900, color: x.delta >= 0 ? RED : GREEN, whiteSpace: "nowrap" }}>{x.delta >= 0 ? "+" : ""}{compact(x.delta)}</TableCell>
                          <TableCell sx={{ fontSize: 12, color: MUT }}>{pct(x.contrib)}</TableCell>
                        </TableRow>
                      ))}
                      {!card.rows.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary", py: 2 }}>Nenhum dado.</TableCell></TableRow>}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Grid>
            ))}
          </Grid>
        </SectionPanel>

        {/* 08 · Anomalias e aceleração do custo */}
        <SectionPanel title="Desvios e aceleração de custo" subtitle={`Detecção simples por ${dimLabel}: |Z| ≥ 2 vs histórico anterior; aceleração = variação da velocidade nos últimos 3 meses`}>
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={6}>
              <Typography variant="subtitle2" sx={{ fontWeight: 950, mb: 0.5 }}>Desvios (|Z| ≥ 2)</Typography>
              <Table size="small">
                <TableHead><TableRow><TableCell sx={{ fontWeight: 900, fontSize: 11 }}>{dimLabel}</TableCell><TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Z</TableCell><TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Custo do mês</TableCell></TableRow></TableHead>
                <TableBody>
                  {anomaly.anomalyList.map((x, i) => (
                    <TableRow key={i}>
                      <TableCell sx={{ fontSize: 12, fontWeight: 850 }}>{x.key}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 950, color: Math.abs(x.z) >= 2.5 ? RED : YELLOW }}>Z {x.z.toFixed(1)}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{money(x.current)}</TableCell>
                    </TableRow>
                  ))}
                  {!anomaly.anomalyList.length && <TableRow><TableCell colSpan={3} align="center" sx={{ color: "text.secondary", py: 2 }}>Nenhum desvio relevante.</TableCell></TableRow>}
                </TableBody>
              </Table>
            </Grid>
            <Grid item xs={12} md={6}>
              <Typography variant="subtitle2" sx={{ fontWeight: 950, mb: 0.5 }}>Aceleração de custo (maiores mudanças)</Typography>
              <Table size="small">
                <TableHead><TableRow><TableCell sx={{ fontWeight: 900, fontSize: 11 }}>{dimLabel}</TableCell><TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Aceleração em BRL</TableCell></TableRow></TableHead>
                <TableBody>
                  {anomaly.accelList.map((x, i) => (
                    <TableRow key={i}>
                      <TableCell sx={{ fontSize: 12, fontWeight: 850 }}>{x.key}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900, color: x.acceleration >= 0 ? RED : GREEN }}>{x.acceleration >= 0 ? "+" : ""}{compact(x.acceleration)}</TableCell>
                    </TableRow>
                  ))}
                  {!anomaly.accelList.length && <TableRow><TableCell colSpan={2} align="center" sx={{ color: "text.secondary", py: 2 }}>Séries insuficientes para análise.</TableCell></TableRow>}
                </TableBody>
              </Table>
            </Grid>
          </Grid>
        </SectionPanel>

        {/* 09 · Viés e confiabilidade da previsão */}
        <SectionPanel title="Viés e acerto da previsão" subtitle={`Ranking por ${dimLabel}: acerto médio (previsão x efetivado), viés e erro acumulado`}>
          <Grid container spacing={1.5} sx={{ mb: 1.5 }}>
            <Grid item xs={6} sm={3}><KpiCard label="Itens avaliados" value={String(biasRows.length)} sub="com pares previsão/efetivado" color={BLUE} /></Grid>
            <Grid item xs={6} sm={3}><KpiCard label="Pior acerto" value={biasRows[0] ? pct(biasRows[0].accuracy) : "—"} sub={biasRows[0]?.key || "N/A"} color={biasRows[0] && biasRows[0].accuracy < 70 ? RED : GREEN} /></Grid>
            <Grid item xs={6} sm={3}><KpiCard label="Viés (pior caso)" value={biasRows[0] ? `${biasRows[0].bias >= 0 ? "+" : ""}${pct(biasRows[0].bias)}` : "—"} sub="+ over / − under forecast" color={ORANGE} /></Grid>
            <Grid item xs={6} sm={3}><KpiCard label="Erro absoluto (maior caso)" value={biasRows[0] ? compact(biasRows[0].absErr) : "—"} sub={biasRows[0]?.key || "N/A"} color={PURPLE} /></Grid>
          </Grid>
          <TableContainer>
            <Table size="small">
              <TableHead><TableRow>
                {[dimLabel, "Acerto", "Viés médio", "Over", "Under", "Erro em R$"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
              </TableRow></TableHead>
              <TableBody>
                {biasRows.map((x, i) => (
                  <TableRow key={i}>
                    <TableCell sx={{ fontSize: 12, fontWeight: 850 }}>{x.key}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}><Box component="span" sx={{ px: 0.8, py: 0.15, borderRadius: 99, bgcolor: x.accuracy < 70 ? RED : x.accuracy < 90 ? YELLOW : GREEN, color: "#fff", fontSize: 11, fontWeight: 950 }}>{pct(x.accuracy)}</Box></TableCell>
                    <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{x.bias >= 0 ? "+" : ""}{pct(x.bias)}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{x.over}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{x.under}</TableCell>
                    <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{compact(x.absErr)}</TableCell>
                  </TableRow>
                ))}
                {!biasRows.length && <TableRow><TableCell colSpan={6} align="center" sx={{ color: "text.secondary", py: 2 }}>Não há pares previsão/efetivado suficientes por {dimLabel.toLowerCase()} no histórico.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </TableContainer>
        </SectionPanel>

        {/* 10 · v42: Forecast Reliability / Opportunity Gap / Run Rate */}
        <SectionPanel title="Prioridade preliminar de análise" subtitle="Acerto, desvios positivos da previsão e ritmo anualizado — indicação para direcionar investigação, não economia garantida">
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={6}>
              <Typography variant="subtitle2" sx={{ fontWeight: 950, mb: 0.5 }}>Maiores gaps por {dimLabel}</Typography>
              {oppDim.length ? oppDim.map((x, i) => (
                <Box key={i} sx={{ mb: 0.6 }}>
                  <Stack direction="row" justifyContent="space-between"><Typography variant="caption" sx={{ fontWeight: 900 }}>{i + 1}. {x.key}</Typography><Typography variant="caption" sx={{ fontWeight: 950, color: ORANGE }}>Gap {compact(x.gap)}</Typography></Stack>
                  <Box sx={{ height: 8, borderRadius: 99, bgcolor: "#EEF3F8", overflow: "hidden" }}><Box sx={{ height: "100%", width: `${Math.max(2, Math.min(100, (Math.abs(x.gap) / Math.max(1, Math.abs(oppDim[0].gap))) * 100))}%`, bgcolor: i < 3 ? ORANGE : PURPLE }} /></Box>
                </Box>
              )) : <Typography variant="body2" color="text.secondary">Nenhum desvio positivo no recorte.</Typography>}
              <Typography variant="subtitle2" sx={{ fontWeight: 950, mb: 0.5, mt: 2 }}>Maiores desvios por serviço</Typography>
              {oppServ.length ? oppServ.map((x, i) => (
                <Stack key={i} direction="row" justifyContent="space-between"><Typography variant="caption" sx={{ fontWeight: 900 }}>{i + 1}. {x.key}</Typography><Typography variant="caption" sx={{ fontWeight: 950, color: BLUE }}>Gap {compact(x.gap)}</Typography></Stack>
              )) : <Typography variant="body2" color="text.secondary">Nenhum desvio positivo por serviço.</Typography>}
            </Grid>
            <Grid item xs={12} md={6}>
              <Typography variant="subtitle2" sx={{ fontWeight: 950, mb: 0.5 }}>Acerto da previsão (piores)</Typography>
              {biasRows.slice(0, 5).map((x, i) => (
                <Box key={i} sx={{ mb: 0.6 }}>
                  <Stack direction="row" justifyContent="space-between"><Typography variant="caption" sx={{ fontWeight: 900 }}>{i + 1}. {x.key}</Typography><Typography variant="caption" sx={{ fontWeight: 950, color: x.accuracy < 70 ? RED : x.accuracy < 90 ? YELLOW : GREEN }}>{pct(x.accuracy)} · erro {compact(x.absErr)}</Typography></Stack>
                  <Box sx={{ height: 8, borderRadius: 99, bgcolor: "#EEF3F8", overflow: "hidden" }}><Box sx={{ height: "100%", width: `${Math.max(2, Math.min(100, 100 - x.accuracy))}%`, bgcolor: x.accuracy < 70 ? RED : x.accuracy < 90 ? YELLOW : GREEN }} /></Box>
                </Box>
              ))}
              {!biasRows.length && <Typography variant="body2" color="text.secondary">Sem pares de previsão e efetivado.</Typography>}
              <Box sx={{ mt: 1.5 }}>
                <KpiCard label="Ritmo de referência" value={compact(runRate.annualized)} sub={`média ${compact(runRate.avgReal)}/mês × 12`} color={BLUE} full={moneyFull(runRate.annualized)} />
              </Box>
            </Grid>
          </Grid>
        </SectionPanel>

        {/* 11 · Governance / Data Quality */}
        <SectionPanel title="Completude da governança e qualidade dos dados" subtitle="Completude ponderada pelo custo efetivado — registros ausentes (nulos/vazios) reduzem o score proporcionalmente à materialidade">
          <Grid container spacing={1.5} sx={{ mb: 1.5 }}>
            <Grid item xs={6} sm={3}><KpiCard label="Índice de qualidade de dados" value={pct(Math.max(0, govScore))} sub="média de completude ponderada por custo" color={govScore >= 90 ? GREEN : govScore >= 80 ? YELLOW : RED} /></Grid>
            <Grid item xs={6} sm={3}><KpiCard label="Pior eixo" value={gov.rowsPer[0]?.label || "—"} sub={gov.rowsPer[0] ? `${pct(gov.rowsPer[0].spendPct)} do custo sem classificação` : ""} color={gov.rowsPer[0] && gov.rowsPer[0].spendPct >= 20 ? RED : YELLOW} /></Grid>
            <Grid item xs={6} sm={3}><KpiCard label="Custo exposto (pior caso)" value={compact(gov.rowsPer[0]?.missingSpend || 0)} sub="no pior eixo de qualidade" color={ORANGE} full={moneyFull(gov.rowsPer[0]?.missingSpend || 0)} /></Grid>
            <Grid item xs={6} sm={3}><KpiCard label="Critérios em alerta" value={String(gov.rowsPer.filter(x => x.spendPct >= 20).length)} sub="eixos com ≥20% do custo sem classificação" color={gov.rowsPer.filter(x => x.spendPct >= 20).length ? RED : GREEN} /></Grid>
          </Grid>
          <TableContainer>
            <Table size="small">
              <TableHead><TableRow>
                {["Eixo", "Severidade", "% de linhas", "% do custo", "Custo relevante", "Materialidade"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
              </TableRow></TableHead>
              <TableBody>
                {gov.rowsPer.map((x, i) => {
                  const sev = x.spendPct >= 20 ? "critical" : x.spendPct >= 5 ? "high" : x.spendPct >= 1 ? "medium" : "low";
                  const sevColor = sev === "critical" ? RED : sev === "high" ? YELLOW : sev === "medium" ? ORANGE : GREEN;
                  return (
                    <TableRow key={i}>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{x.label}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}><Box component="span" sx={{ px: 0.8, py: 0.15, borderRadius: 99, bgcolor: sevColor, color: "#fff", fontSize: 10, fontWeight: 950 }}>{sev.toUpperCase()}</Box></TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{pct(x.rowPct)}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{pct(x.spendPct)}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{money(x.missingSpend)}</TableCell>
                      <TableCell sx={{ minWidth: 120 }}><Box sx={{ height: 8, borderRadius: 99, bgcolor: "#EEF3F8", overflow: "hidden" }}><Box sx={{ height: "100%", width: `${Math.min(100, x.spendPct)}%`, bgcolor: sevColor }} /></Box></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </SectionPanel>

        {/* 12 · Comparator */}
        <SectionPanel title="Compare dois itens no mesmo recorte" subtitle={`Escolha duas entidades de ${dimLabel} e compare trajetória, participação e variação mensal`}
          right={
            <Stack direction="row" gap={1} flexWrap="wrap">
              <FormControl size="small" sx={{ minWidth: 200 }}><InputLabel>Item A</InputLabel>
                <Select label="Item A" value={aKey} onChange={e => setCompA(e.target.value)}>
                  {entities.map(e => <MenuItem key={e.key} value={e.key}>{e.key}</MenuItem>)}
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 200 }}><InputLabel>Item B</InputLabel>
                <Select label="Item B" value={bKey} onChange={e => setCompB(e.target.value)}>
                  {entities.map(e => <MenuItem key={e.key} value={e.key}>{e.key}</MenuItem>)}
                </Select>
              </FormControl>
            </Stack>
          }>
          <Grid container spacing={1.5}>
            <Grid item xs={12} md={4}>
              <Stack spacing={1}>
                <KpiCard label={aKey || "A"} value={entA ? compact(entA.latest) : "—"} sub={mA ? `${mA.mom == null ? "sem base" : `${mA.mom >= 0 ? "▲ " : "▼ "}${pct(Math.abs(mA.mom))} MoM`} · share ${pct(mA.share)}` : ""} color="#0B3FA8" full={entA ? moneyFull(entA.latest) : ""} />
                <KpiCard label={bKey || "B"} value={entB ? compact(entB.latest) : "—"} sub={mB ? `${mB.mom == null ? "sem base" : `${mB.mom >= 0 ? "▲ " : "▼ "}${pct(Math.abs(mB.mom))} MoM`} · share ${pct(mB.share)}` : ""} color="#0EA5C6" full={entB ? moneyFull(entB.latest) : ""} />
                <Typography variant="caption" sx={{ color: MUT, fontWeight: 700 }}>A comparação mostra trajetória e participação — não afirma eficiência ou desperdício apenas pela diferença de custo.</Typography>
              </Stack>
            </Grid>
            <Grid item xs={12} md={8}>
              {entA && entB ? (
                <Chart
                  data={[
                    { x: entA.series.map(s => monthLabel(s[0])), y: entA.series.map(s => s[1]), type: "scatter", mode: "lines+markers", name: aKey, line: { color: "#0B3FA8", width: 3 }, marker: { size: 4 } },
                    { x: entB.series.map(s => monthLabel(s[0])), y: entB.series.map(s => s[1]), type: "scatter", mode: "lines+markers", name: bKey, line: { color: "#0EA5C6", width: 3 }, marker: { size: 4 } },
                  ]}
                  layout={{ height: 300, margin: { l: 60, r: 20, t: 12, b: 36 }, yaxis: { title: { text: "BRL" }, gridcolor: "#EEF3F8" }, legend: { orientation: "h", y: 1.05 } }}
                  config={{ displayModeBar: false }} style={{ width: "100%" }} />
              ) : <Typography variant="body2" color="text.secondary">Nenhum item disponível no recorte.</Typography>}
            </Grid>
          </Grid>
        </SectionPanel>

        {/* 13 · Decomposição hierárquica (v233 — árvore de fluxo interativa) */}
        <SectionPanel title="Decomposição pela estrutura organizacional" subtitle={`Avance do custo total para ${decompChain.map(c => c.label).join(" → ")}. Cada seleção recalcula os filhos; a espessura das conexões é o share do custo no nó pai.`}>
          <HierarchicalDecompTree
            table={table}
            baseFilters={filtersOf(filters)}
            chain={decompChain}
            queryKey={"decisao-tree-" + filterKey}
            measureColumn={measureFor(filters)}
            onApplyContext={applyTreeContext}
            shareCaption="participação no total"
          />
          <Typography variant="caption" sx={{ color: MUT, fontWeight: 700, display: "block", mt: 1 }}>“Outros” abre a lista dos demais filhos; cada item pode ser selecionado para continuar a decomposição. “Aplicar contexto” transforma a trilha em filtros globais do cockpit.</Typography>
        </SectionPanel>

        </>
        )}
      </LoadingBlock>
    </Box>
  );
}

/** Converte os filtros globais do cockpit em filtros do pivot do snapshot de forecast. */
function pivotFiltersFrom(filters: Filters): Record<string, HistoryFilter> {
  const out: Record<string, HistoryFilter> = {};
  if (filters.clouds.length) out.cloud_origem = { operator: "in", value: filters.clouds };
  if (filters.dirs.length) out.organograma_nivel_4 = { operator: "in", value: filters.dirs };
  if (filters.depts.length) out.organograma_nivel_5 = { operator: "in", value: filters.depts };
  if (filters.servicos.length) out.servicename = { operator: "in", value: filters.servicos };
  if (filters.cats.length) out.servicecategory = { operator: "in", value: filters.cats };
  if (filters.path.length) {
    pathSegmentFilters(filters.path, filters.visaoDiretoria).forEach(f => {
      out[f.column] = { operator: "in", value: f.values.map(String) };
    });
  }
  Object.entries(filters.extra || {}).forEach(([col, vals]) => { if (vals && vals.length) out[col] = { operator: "in", value: vals }; });
  return out;
}

const DRILL_CHAIN_BASE: Array<{ field: string; label: string }> = [
  { field: "cloud_origem", label: "Cloud" },
  { field: "organograma_nivel_4", label: "Diretoria" },
  { field: "organograma_nivel_5", label: ORG.level5 },
  { field: "servicename", label: "Serviço" },
  { field: "servicecategory", label: "Categoria" },
  { field: "regionname", label: "Região" },
  { field: "subaccountname", label: "Subconta" },
];

function buildDrillChain(visaoDiretoria: boolean): Array<{ field: string; label: string }> {
  if (!visaoDiretoria) return DRILL_CHAIN_BASE;
  return [
    ...visaoDiretoriaChain(),
    { field: "servicename", label: "Serviço" },
    { field: "servicecategory", label: "Categoria" },
    { field: "regionname", label: "Região" },
    { field: "subaccountname", label: "Subconta" },
  ];
}

function Drilldown({ table, filters, filterKey }: { table: string; filters: Filters; filterKey: string }) {
  const drillChain = useMemo(() => buildDrillChain(filters.visaoDiretoria), [filters.visaoDiretoria]);
  const [path, setPath] = useState<Array<{ field: string; value: string }>>([]);
  useEffect(() => { setPath([]); }, [filters.visaoDiretoria]);
  const level = path.length;
  const next = drillChain[level];

  const pathFilters = useMemo<GoldQueryFilter[]>(() => path.map(p => ({ column: p.field, operator: "IN", values: [p.value] })), [path]);

  const defs = useMemo<QueryDef[]>(() => {
    if (!next) return [];
    const filtersLocal = filtersOf(filters, pathFilters);
    return [
      { id: "children", rows: [next.field], measures: [{ column: measureFor(filters), aggregation: "SUM" }, { column: "billedcost", aggregation: "SUM" }, { column: "quantity", aggregation: "SUM" }], filters: filtersLocal, row_limit: 300 },
      { id: "months", rows: ["_billing_period"], measures: [{ column: measureFor(filters), aggregation: "SUM" }], filters: filtersLocal, row_limit: 60 },
    ];
  }, [filterKey, path, next]);
  const { results, loading, error } = useTabQueries(table, defs, filterKey + JSON.stringify(path));

  const children = rowsOf(results.children);
  const childTotal = children.reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0);
  const top5Share = childTotal ? children.slice(0, 5).reduce((s, r) => s + Math.abs(Number(r.realizado) || 0), 0) / childTotal * 100 : 0;
  const months = monthlySeries(results.months, "realizado");

  return (
    <Grid container spacing={1.5}>
      {error && <Grid item xs={12}><Alert severity="error">{dataDisplayText(error)}</Alert></Grid>}
      <Grid item xs={12}>
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Stack direction="row" alignItems="center" gap={0.6} flexWrap="wrap">
            <Chip label="Visão total" size="small" onClick={() => setPath([])} color={level === 0 ? "primary" : "default"} />
            {path.map((p, i) => (
              <Box key={i} display="flex" alignItems="center" gap={0.6}>
                <Typography sx={{ color: MUT, fontWeight: 950 }}>→</Typography>
                <Chip size="small" label={`${drillChain[i].label}: ${p.value}`} color={i === path.length - 1 ? "primary" : "default"} onDelete={() => setPath(path.slice(0, i))} />
              </Box>
            ))}
            {level > 0 && <Button size="small" onClick={() => setPath(path.slice(0, -1))}>Voltar</Button>}
          </Stack>
        </Paper>
      </Grid>
      {!next ? (
        <Grid item xs={12}>
          <Alert severity="info">Fim da trilha. Use os filtros globais ou a aba Tabela para conferir.</Alert>
        </Grid>
      ) : (
        <>
          <Grid item xs={12} md={7}>
            <LoadingBlock loading={loading} error={error}>
              <RankBars title={`${drillChain[level].label} — filhos (Top 20)`} rows={children} measure="realizado" limit={20} color={PURPLE} />
            </LoadingBlock>
          </Grid>
          <Grid item xs={12} md={5}>
            <LoadingBlock loading={loading} error={error}>
              <ChartCard title="Evolução do recorte escolhido">
                <Chart
                  data={[{ x: months.map(r => monthLabel(r.month)), y: months.map(r => r.value), type: "bar", marker: { color: BLUE } }]}
                  layout={{ height: 280, margin: { l: 56, r: 20, t: 8, b: 34 }, yaxis: { title: { text: "BRL" }, gridcolor: "#EEF3F8" } }}
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
            <LoadingBlock loading={loading} error={error}>
              <TableContainer>
                <Table size="small">
                  <TableHead><TableRow>
                    {[drillChain[level].label, "Efetivado", "BilledCost", "Volume consumido", "% do nível pai"].map(h => <TableCell key={h} sx={{ fontWeight: 900, fontSize: 11 }}>{h}</TableCell>)}
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
                    {!children.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary" }}>Sem dados para o recorte selecionado.</TableCell></TableRow>}
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
