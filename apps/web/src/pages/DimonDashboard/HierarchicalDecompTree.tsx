import { useEffect, useId, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import {
  DimonQueryFilter, DimonQueryResult, formatApiError, queryDimonTable,
} from "../../services/api";
import { ORG } from "../../constants/orgLabels";

export type DecompChainItem = { field: string; label: string };
export type DecompPathStep = { field: string; value: string };

const COMPACT_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });
const PURPLE = "#5b00cb";

const VISAO_DIRETORIA_LEVELS = [
  "departamento_nivel_1",
  "departamento_nivel_2",
  "departamento_nivel_4",
  "departamento_nivel_3",
  "departamento_nivel_5",
  "departamento_nivel_6",
] as const;

const VISAO_DIRETORIA_DEPTH_TO_NIVEL = [1, 2, 4, 3, 5, 6] as const;

const DIRECTOR_VIEW_LABELS: Record<string, string> = {
  departamento_nivel_1: "Segmento",
  departamento_nivel_2: "Modelo",
  departamento_nivel_4: "Diretoria",
  departamento_nivel_3: "Cloud",
  departamento_nivel_5: "Projeto",
  departamento_nivel_6: "SubProjeto",
};

/** Raiz do organograma na UI — não corresponde a departamento_nivel_1 nos dados Dimon. */
const DIRECTOR_PATH_ROOT_PREFIXES = ["Consumo Cloud"] as const;

const DEFAULT_CHILD_LIMIT = 7;
export const EMPTY_DIMENSION_LABEL = "Não informado";
const MAX_EMPTY_ORG_SKIP = 6;

/** Remove prefixo de raiz da árvore para alinhar profundidade → departamento_nivel_N. */
export function normalizeDirectorPath(path: string[], visaoDiretoria: boolean): string[] {
  if (!visaoDiretoria || path.length === 0) return path;
  if (path.length === 1 && DIRECTOR_PATH_ROOT_PREFIXES.includes(path[0] as typeof DIRECTOR_PATH_ROOT_PREFIXES[number])) {
    return [];
  }
  if (DIRECTOR_PATH_ROOT_PREFIXES.includes(path[0] as typeof DIRECTOR_PATH_ROOT_PREFIXES[number])) {
    return path.slice(1);
  }
  return path;
}

export function isOrgField(field: string): boolean {
  return field.startsWith("departamento_nivel_");
}

/** Nível org cujo único filho agrega todo o custo em valor ausente — deve ser pulado. */
export function isEmptyOrgLevel(field: string, children: Array<{ key: string; __other?: boolean }>): boolean {
  if (!isOrgField(field)) return false;
  if (children.length !== 1 || children[0].__other) return false;
  return children[0].key === EMPTY_DIMENSION_LABEL;
}

type TreeChildAgg = { key: string; cost: number; mom: number | null; __other?: boolean };

function aggregateTreeChildren(rows: Record<string, unknown>[], field: string, filterRowTypes: boolean): Map<string, { cost: number; byMonth: Map<string, number> }> {
  const map = new Map<string, { cost: number; byMonth: Map<string, number> }>();
  for (const r of rows) {
    const k = String(r[field] ?? EMPTY_DIMENSION_LABEL);
    if (!map.has(k)) map.set(k, { cost: 0, byMonth: new Map() });
    const o = map.get(k)!;
    const v = Number(r.realizado || 0);
    // No diálogo de consumo o filtro SQL já selecionou somente realizado.
    // Não dependa do nome retornado da coluna ``tipo`` (que pode preservar a
    // capitalização física da Dimon), pois isso descartava todas as linhas.
    const isRealized = ["", "realizado"].includes(String(r.tipo ?? r.Tipo ?? "").trim().toLowerCase());
    if (!filterRowTypes || isRealized) {
      o.cost += v;
      const m = String(r._billing_period || "");
      if (m) o.byMonth.set(m, (o.byMonth.get(m) || 0) + v);
    }
  }
  return map;
}

function rankTreeChildren(map: Map<string, { cost: number; byMonth: Map<string, number> }>, field: string): TreeChildAgg[] {
  const items: TreeChildAgg[] = [...map.entries()]
    .map(([key, o]) => {
      const months = [...o.byMonth.keys()].sort();
      const cur = months.length ? o.byMonth.get(months[months.length - 1]) || 0 : 0;
      const prev = months.length > 1 ? o.byMonth.get(months[months.length - 2]) || 0 : 0;
      return { key, cost: o.cost, mom: prev ? ((cur - prev) / Math.abs(prev)) * 100 : null };
    });
  // Toda a decomposição é uma visão de consumo: a ordem precisa ser
  // materialidade (maior → menor), inclusive para Serviço e SKU.
  items.sort((a, b) => Math.abs(b.cost) - Math.abs(a.cost));
  const limit = DEFAULT_CHILD_LIMIT;
  const top = items.slice(0, limit);
  const rest = items.slice(limit);
  if (rest.length) top.push({ key: "Outros", cost: rest.reduce((s, x) => s + x.cost, 0), mom: null, __other: true });
  return top;
}

/** Quantos níveis do organograma executivo já estão cobertos pelo escopo filtrado. */
export function executiveChainStartIndex(path: string[], visaoDiretoria: boolean): number {
  const normalized = normalizeDirectorPath(path, visaoDiretoria);
  if (!visaoDiretoria) return normalized.length;
  return Math.min(normalized.length, VISAO_DIRETORIA_LEVELS.length);
}

function pct(v: unknown, digits = 1): string {
  return `${Number(v ?? 0).toLocaleString("pt-BR", { maximumFractionDigits: digits })}%`;
}
function compact(v: unknown): string {
  return COMPACT_BRL.format(Number(v ?? 0));
}
function rowsOf(r: DimonQueryResult | null | undefined): Record<string, unknown>[] {
  return (r?.rows ?? []) as Record<string, unknown>[];
}

export function pathSegmentFilters(path: string[], visaoDiretoria: boolean): DimonQueryFilter[] {
  const out: DimonQueryFilter[] = [];
  const scoped = normalizeDirectorPath(path, visaoDiretoria);
  scoped.forEach((segment, depth) => {
    if (!segment?.trim()) return;
    const nivel = visaoDiretoria
      ? (VISAO_DIRETORIA_DEPTH_TO_NIVEL[depth] ?? depth + 1)
      : depth + 1;
    out.push({ column: `departamento_nivel_${nivel}`, operator: "IN", values: [segment] });
  });
  return out;
}

/** Cadeia da aba Decisão (inclui ServiceSubcategory e skumeter). */
export function buildDecompChain(visaoDiretoria: boolean): DecompChainItem[] {
  const org: DecompChainItem[] = visaoDiretoria
    ? VISAO_DIRETORIA_LEVELS.map(field => ({ field, label: DIRECTOR_VIEW_LABELS[field] || field }))
    : [
      { field: "departamento_nivel_1", label: "Segmento" },
      { field: "departamento_nivel_2", label: "Modelo" },
      { field: "departamento_nivel_3", label: "Plataforma" },
      { field: "departamento_nivel_4", label: "Diretoria" },
      { field: "departamento_nivel_5", label: ORG.level5 },
    ];
  return [
    { field: "cloud_origem", label: "Cloud" },
    ...org,
    { field: "servicename", label: "Serviço" },
    { field: "servicesubcategory", label: "ServiceSubcategory" },
    { field: "skumeter", label: "Sub Serviço (SKU name)" },
    { field: "subaccountname", label: "Subconta" },
  ];
}

/** Cadeia da visão executiva: organograma a partir do escopo já filtrado, até ServiceSubcategory e skumeter. */
export function buildExecutiveDecompChain(visaoDiretoria: boolean, scopedPathDepth = 0): DecompChainItem[] {
  const org: DecompChainItem[] = visaoDiretoria
    ? VISAO_DIRETORIA_LEVELS.map(field => ({ field, label: DIRECTOR_VIEW_LABELS[field] || field }))
    : [
      { field: "departamento_nivel_1", label: "Segmento" },
      { field: "departamento_nivel_2", label: "Modelo" },
      { field: "departamento_nivel_3", label: "Plataforma" },
      { field: "departamento_nivel_4", label: "Diretoria" },
      { field: "departamento_nivel_5", label: ORG.level5 },
      { field: "departamento_nivel_6", label: "Unidade" },
    ];
  const orgTail = org.slice(Math.max(0, scopedPathDepth));
  return [
    ...orgTail,
    { field: "servicename", label: "Serviço" },
    { field: "servicesubcategory", label: "ServiceSubcategory" },
    { field: "skumeter", label: "Sub Serviço (SKU name)" },
  ];
}

export default function HierarchicalDecompTree({
  table,
  baseFilters,
  chain,
  queryKey,
  onApplyContext,
  shareCaption = "participação no contexto atual",
  filterRowTypes = true,
}: {
  table: string;
  baseFilters: DimonQueryFilter[];
  chain: DecompChainItem[];
  queryKey: string;
  onApplyContext?: (path: DecompPathStep[]) => void;
  shareCaption?: string;
  /** False quando baseFilters já restringe o tipo no servidor. */
  filterRowTypes?: boolean;
}) {
  const svgId = useId().replace(/:/g, "");
  const [treePath, setTreePath] = useState<DecompPathStep[]>([]);
  const [treeMetric, setTreeMetric] = useState<"cost" | "share" | "mom">("cost");
  const [results, setResults] = useState<Record<string, DimonQueryResult | null>>({});
  const [emptyLevelSkip, setEmptyLevelSkip] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setTreePath([]);
  }, [queryKey]);

  const treeStart = treePath.length;
  const effectiveTreeIndex = treeStart + emptyLevelSkip;
  const treePathKey = treePath.map(p => `${p.field}=${p.value}`).join("|");
  const chainKey = chain.map(c => c.field).join(",");
  useEffect(() => {
    setEmptyLevelSkip(0);
  }, [treePathKey, chainKey]);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    const pathFilters = (n: number) => treePath.slice(0, n).map(p => ({
      column: p.field, operator: "IN" as const, values: [p.value],
    }));
    const defs: Array<{
      id: string;
      rows?: string[];
      filters: DimonQueryFilter[];
      row_limit: number;
    }> = [];
    for (let i = 0; i <= treeStart; i++) {
      defs.push({ id: `spine${i}`, filters: [...baseFilters, ...pathFilters(i)], row_limit: 5 });
    }
    // Rankings complementares do nó atual. Eles recebem exatamente os mesmos
    // filtros do caminho escolhido na árvore (período, tipo e escopo).
    defs.push(
      { id: "topSubcategory", rows: ["servicesubcategory"], filters: [...baseFilters, ...pathFilters(treeStart)], row_limit: 10 },
      { id: "topSku", rows: ["skumeter"], filters: [...baseFilters, ...pathFilters(treeStart)], row_limit: 10 },
    );
    const load = async () => {
      const spineList = await Promise.all(
        defs.map(d => queryDimonTable({
          table,
          rows: d.rows ?? [],
          measures: [{ column: "realizado", aggregation: "SUM" }],
          filters: d.filters,
          row_limit: d.row_limit,
        })),
      );
      if (!alive) return;
      const m: Record<string, DimonQueryResult | null> = {};
      defs.forEach((d, i) => { m[d.id] = spineList[i]; });

      let skip = 0;
      const selectedField = treePath[treePath.length - 1]?.field;
      if (treeStart < chain.length) {
        for (; skip < MAX_EMPTY_ORG_SKIP && treeStart + skip < chain.length; skip++) {
          const idx = treeStart + skip;
          const field = chain[idx].field;
          // A seleção pode já ter sido gravada com o mesmo campo do próximo
          // nível (por exemplo, duas entradas de Serviço). Não consulte esse
          // campo novamente: o resultado precisa ser do ServiceSubcategory.
          if (selectedField && field === selectedField) continue;
          const treeResult = await queryDimonTable({
            table,
            rows: [field, "_billing_period", "tipo"],
            measures: [{ column: "realizado", aggregation: "SUM" }],
            filters: [...baseFilters, ...pathFilters(treeStart)],
            row_limit: 10000,
          });
          if (!alive) return;
          const children = rankTreeChildren(aggregateTreeChildren(rowsOf(treeResult), field, filterRowTypes), field);
          m[`tree${treeStart}`] = treeResult;
          if (!isEmptyOrgLevel(field, children)) {
            setEmptyLevelSkip(skip);
            break;
          }
          if (treeStart + skip + 1 >= chain.length) setEmptyLevelSkip(skip);
        }
      }
      setResults(m);
    };
    load().catch(e => {
      if (alive) setError(formatApiError(e, "Falha ao consultar a decomposição"));
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, queryKey, treePathKey, chainKey]);

  const spineCosts = useMemo(() => {
    const arr: number[] = [];
    for (let i = 0; i <= treeStart; i++) {
      arr.push(rowsOf(results[`spine${i}`]).reduce((s, r) => s + Number(r.realizado || 0), 0));
    }
    return arr;
  }, [results, treeStart]);
  const totalAll = spineCosts[0] || 0;
  const nodeTotal = spineCosts[treeStart] || 0;
  const pathNodes = useMemo(() => {
    const out: Array<{ label: string; dim: string; cost: number }> = [{ label: "TOTAL", dim: "TOTAL", cost: totalAll }];
    treePath.forEach((p, i) => {
      out.push({ label: p.value, dim: chain.find(c => c.field === p.field)?.label || p.field, cost: spineCosts[i + 1] || 0 });
    });
    return out;
  }, [treePath, spineCosts, totalAll, chain]);
  const treeChildren = useMemo(() => {
    if (effectiveTreeIndex >= chain.length) return [];
    const rows = rowsOf(results[`tree${treeStart}`]);
    const field = chain[effectiveTreeIndex].field;
    return rankTreeChildren(aggregateTreeChildren(rows, field, filterRowTypes), field);
  }, [results, treeStart, effectiveTreeIndex, chain, filterRowTypes]);
  const nextField = effectiveTreeIndex < chain.length ? chain[effectiveTreeIndex].field : null;
  const nextLabel = effectiveTreeIndex < chain.length ? chain[effectiveTreeIndex].label : null;
  const topSubcategories = rowsOf(results.topSubcategory);
  const topSkus = rowsOf(results.topSku);
  const topShare = treeChildren.length && nodeTotal ? (Math.abs(treeChildren[0].cost) / Math.abs(nodeTotal)) * 100 : 0;
  const top3Share = nodeTotal ? (treeChildren.slice(0, 3).reduce((s, x) => s + Math.abs(x.cost), 0) / Math.abs(nodeTotal)) * 100 : 0;
  const shareTotal = totalAll ? (Math.abs(nodeTotal) / Math.abs(totalAll)) * 100 : 0;
  const treeMetricText = (item: { cost: number; mom: number | null }, parentTotal: number) => {
    if (treeMetric === "share") return pct(parentTotal ? (Math.abs(item.cost) / Math.abs(parentTotal)) * 100 : 0);
    if (treeMetric === "mom") return item.mom == null ? "N/A" : `${item.mom >= 0 ? "+" : ""}${pct(item.mom)}`;
    return compact(item.cost || 0);
  };
  const selectTreeNode = (idx: number, field: string, value: string) => {
    setTreePath([...treePath.slice(0, idx), { field, value }]);
  };

  return (
    <Box>
      <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center" justifyContent="flex-end" sx={{ mb: 1 }}>
        <Box sx={{ display: "inline-flex", border: "1px solid #d8dce7", borderRadius: 1.5, p: 0.3, bgcolor: "#f8fafc" }}>
          {(["cost", "share", "mom"] as const).map(m => (
            <Button key={m} size="small" onClick={() => setTreeMetric(m)}
              sx={{ minWidth: 0, px: 1.2, py: 0.4, borderRadius: 1.2, fontWeight: 950, fontSize: 11, textTransform: "none", color: treeMetric === m ? "#fff" : "#667085", bgcolor: treeMetric === m ? "#111827" : "transparent", "&:hover": { bgcolor: treeMetric === m ? "#111827" : "#eef0f4" } }}>
              {m === "cost" ? "Custo" : m === "share" ? "Share" : "MoM"}
            </Button>
          ))}
        </Box>
        <Button size="small" variant="outlined" onClick={() => setTreePath([])}>Resetar</Button>
        {onApplyContext && treePath.length > 0 && (
          <Button size="small" variant="contained" onClick={() => onApplyContext(treePath)}>Aplicar contexto</Button>
        )}
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
      <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2.5, overflow: "hidden", bgcolor: "#fff", position: "relative" }}>
        {loading && (
          <Stack alignItems="center" justifyContent="center" sx={{ position: "absolute", inset: 0, bgcolor: "rgba(255,255,255,.65)", zIndex: 2 }}>
            <CircularProgress size={28} />
          </Stack>
        )}
        <Box sx={{ display: "flex", justifyContent: "space-between", gap: 1.5, alignItems: "flex-start", p: 1.6, borderBottom: "1px solid", borderColor: "divider", background: "linear-gradient(135deg, rgba(91,0,203,.06), rgba(110,0,167,.015))", flexWrap: "wrap" }}>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography variant="caption" sx={{ display: "block", fontSize: 9, fontWeight: 950, textTransform: "uppercase", letterSpacing: 1, color: "#6b7280", mb: 0.6 }}>Caminho de investigação</Typography>
            <Stack direction="row" gap={0.5} alignItems="center" flexWrap="wrap">
              <Chip size="small" label="TOTAL" onClick={() => setTreePath([])} color={treePath.length === 0 ? "primary" : "default"} sx={{ fontWeight: 950 }} />
              {treePath.map((p, i) => (
                <Box key={`${p.field}-${i}`} display="flex" alignItems="center" gap={0.5}>
                  <Typography sx={{ color: "#8b5cf6", fontWeight: 950 }}>›</Typography>
                  <Chip size="small" label={`${chain.find(c => c.field === p.field)?.label || p.field}: ${p.value}`} color={i === treePath.length - 1 ? "primary" : "default"} onClick={() => setTreePath(treePath.slice(0, i + 1))} sx={{ fontWeight: 900, maxWidth: 240 }} />
                </Box>
              ))}
            </Stack>
          </Box>
        </Box>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4,1fr)" }, gap: 1, p: 1.6, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, p: 1.2, minWidth: 0 }}>
            <Typography variant="caption" sx={{ display: "block", fontSize: 9, fontWeight: 950, textTransform: "uppercase", letterSpacing: 0.65, color: "#667085" }}>Custo no nó atual</Typography>
            <Typography variant="h6" sx={{ mt: 0.4, fontWeight: 950, color: "#111827", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{compact(nodeTotal)}</Typography>
            <Typography variant="caption" sx={{ color: "#667085", fontWeight: 800, display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{treePath.length ? treePath[treePath.length - 1].value : "TOTAL"}</Typography>
          </Box>
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, p: 1.2, minWidth: 0 }}>
            <Typography variant="caption" sx={{ display: "block", fontSize: 9, fontWeight: 950, textTransform: "uppercase", letterSpacing: 0.65, color: "#667085" }}>Share do total</Typography>
            <Typography variant="h6" sx={{ mt: 0.4, fontWeight: 950, color: "#111827" }}>{pct(shareTotal)}</Typography>
            <Typography variant="caption" sx={{ color: "#667085", fontWeight: 800 }}>{shareCaption}</Typography>
          </Box>
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, p: 1.2, minWidth: 0 }}>
            <Typography variant="caption" sx={{ display: "block", fontSize: 9, fontWeight: 950, textTransform: "uppercase", letterSpacing: 0.65, color: "#667085" }}>Maior filho</Typography>
            <Typography variant="h6" sx={{ mt: 0.4, fontWeight: 950, color: "#111827", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{treeChildren[0] ? treeChildren[0].key : "N/A"}</Typography>
            <Typography variant="caption" sx={{ color: "#667085", fontWeight: 800 }}>{treeChildren[0] ? `${pct(topShare)} do nó atual` : "sem próximo nível"}</Typography>
          </Box>
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, p: 1.2, minWidth: 0 }}>
            <Typography variant="caption" sx={{ display: "block", fontSize: 9, fontWeight: 950, textTransform: "uppercase", letterSpacing: 0.65, color: "#667085" }}>Concentração Top 3</Typography>
            <Typography variant="h6" sx={{ mt: 0.4, fontWeight: 950, color: "#111827" }}>{pct(top3Share)}</Typography>
            <Typography variant="caption" sx={{ color: "#667085", fontWeight: 800 }}>{nextLabel || "fim da hierarquia"}</Typography>
          </Box>
        </Box>
        <Box sx={{ overflow: "auto", px: 1, pt: 1, background: "radial-gradient(circle at 10% 0%, rgba(91,0,203,.045), transparent 30%), linear-gradient(180deg,#fff,#fbfcff)" }}>
          {(() => {
            const nodeW = 205, nodeH = 62, stageGap = 54, left = 28, top = 50;
            const pathCount = pathNodes.length;
            const branchX = left + pathCount * (nodeW + stageGap);
            const childGap = 74;
            const childCount = Math.max(treeChildren.length, 1);
            const H = Math.max(420, top + childCount * childGap + 40);
            const W = Math.max(1060, branchX + nodeW + 80);
            const pathY = Math.max(96, H / 2 - 31);
            const barGrad = `v233BarGradient-${svgId}`;
            const pathGrad = `v233PathGradient-${svgId}`;
            const spineLinks = pathNodes.slice(1).map((n, i) => {
              const px = left + i * (nodeW + stageGap) + nodeW, py = pathY + nodeH / 2;
              const cx = left + (i + 1) * (nodeW + stageGap), cy = pathY + nodeH / 2;
              const parent = pathNodes[i].cost;
              const shr = parent ? (Math.abs(n.cost) / Math.abs(parent)) * 100 : 0;
              const sw = Math.max(3, Math.min(16, 3 + shr / 8));
              return (
                <path key={`sl${i}`} d={`M ${px} ${py} C ${px + 25} ${py}, ${cx - 25} ${cy}, ${cx} ${cy}`} stroke={`url(#${pathGrad})`} strokeWidth={sw} fill="none" strokeLinecap="round" opacity={0.88}>
                  <title>{`${pct(shr)} do pai`}</title>
                </path>
              );
            });
            const branchLinks = treeChildren.map((it, idx) => {
              const y = top + idx * childGap, cy = y + nodeH / 2;
              const share = nodeTotal ? (Math.abs(it.cost) / Math.abs(nodeTotal)) * 100 : 0;
              const sw = Math.max(2, Math.min(15, 2 + share / 7));
              const col = it.mom == null ? "#7c3aed" : it.mom >= 0 ? "#dc2626" : "#059669";
              return (
                <path key={`bl${idx}`} d={`M ${left + (pathCount - 1) * (nodeW + stageGap) + nodeW} ${pathY + nodeH / 2} C ${left + (pathCount - 1) * (nodeW + stageGap) + nodeW + 34} ${pathY + nodeH / 2}, ${branchX - 34} ${cy}, ${branchX} ${cy}`} stroke={col} strokeWidth={sw} fill="none" strokeLinecap="round" opacity={0.42}>
                  <title>{`${it.key} · ${pct(share)} do nó atual`}</title>
                </path>
              );
            });
            const emptyBranch = effectiveTreeIndex >= chain.length && (
              <text x={branchX} y={pathY + 20} fontSize={12} fontWeight={850} fill="#94a3b8">Fim da hierarquia disponível</text>
            );
            return (
              <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Árvore interativa de decomposição de custo" style={{ width: "100%", minWidth: 1050, height: "auto", display: "block" }}>
                <defs>
                  <linearGradient id={barGrad} x1="0" x2="1"><stop offset="0%" stopColor="#7c3aed" /><stop offset="100%" stopColor={PURPLE} /></linearGradient>
                  <linearGradient id={pathGrad} x1="0" x2="1"><stop offset="0%" stopColor="#a78bfa" /><stop offset="100%" stopColor={PURPLE} /></linearGradient>
                </defs>
                {pathNodes.map((n, i) => (
                  <text key={`st${i}`} x={left + i * (nodeW + stageGap)} y={28} fontSize={9} fontWeight={950} letterSpacing={0.8} fill="#7c3aed">{n.dim}</text>
                ))}
                {nextLabel && <text x={branchX} y={28} fontSize={9} fontWeight={950} letterSpacing={0.8} fill="#7c3aed">{nextLabel}</text>}
                {spineLinks}
                {branchLinks}
                {pathNodes.map((n, i) => {
                  const x = left + i * (nodeW + stageGap), y = pathY;
                  const parent = i ? pathNodes[i - 1].cost : totalAll;
                  const shr = i === 0 ? 100 : parent ? (Math.abs(n.cost) / Math.abs(parent)) * 100 : 0;
                  const click = i === 0 ? () => setTreePath([]) : () => setTreePath(treePath.slice(0, i));
                  return (
                    <g key={`pn${i}`} className="node" style={{ cursor: "pointer" }} onClick={click}>
                      <rect className="nodeBg" x={x} y={y} width={nodeW} height={nodeH} rx={14} fill={i === 0 ? "#160d35" : "#f5f0ff"} stroke={i === 0 ? PURPLE : "#8b5cf6"} strokeWidth={i === 0 ? 1 : 1.4} />
                      <text x={x + 13} y={y + 21} fontSize={11} fontWeight={900} fill={i === 0 ? "#fff" : "#1f2937"}>{String(n.label).slice(0, 28)}</text>
                      <text x={x + 13} y={y + 41} fontSize={10} fontWeight={800} fill={i === 0 ? "#d8ccff" : "#64748b"}>{compact(n.cost)}</text>
                      <text x={x + nodeW - 12} y={y + 41} fontSize={10} fontWeight={950} textAnchor="end" fill={i === 0 ? "#c4b5fd" : PURPLE}>{pct(shr)}</text>
                      <title>{`${n.label} · ${compact(n.cost)} · ${pct(shr)} do pai`}</title>
                    </g>
                  );
                })}
                {treeChildren.map((it, idx) => {
                  const x = branchX, y = top + idx * childGap;
                  const share = nodeTotal ? (Math.abs(it.cost) / Math.abs(nodeTotal)) * 100 : 0;
                  const barW = Math.max(3, (nodeW - 26) * Math.min(1, share / Math.max(topShare, 1)));
                  const clickable = !it.__other && it.key !== EMPTY_DIMENSION_LABEL;
                  const metric = treeMetricText(it, nodeTotal);
                  const metricColor = treeMetric === "mom" && it.mom != null ? (it.mom >= 0 ? "#dc2626" : "#059669") : PURPLE;
                  return (
                    <g key={`cn${idx}`} className="node" style={{ cursor: clickable ? "pointer" : "default" }} onClick={clickable && nextField ? () => selectTreeNode(treeStart, nextField, it.key) : undefined}>
                      <rect x={x} y={y} width={nodeW} height={nodeH} rx={14} fill="#fff" stroke="#e5e7eb" strokeWidth={1} />
                      <text x={x + 13} y={y + 20} fontSize={11} fontWeight={900} fill="#1f2937">{String(it.key).slice(0, 28)}</text>
                      <text x={x + 13} y={y + 40} fontSize={10} fontWeight={800} fill="#64748b">{compact(it.cost)}</text>
                      <text x={x + nodeW - 12} y={y + 40} fontSize={10} fontWeight={950} textAnchor="end" fill={metricColor}>{metric}</text>
                      <rect x={x + 13} y={y + 50} width={nodeW - 26} height={5} rx={3} fill="#eef2f7" />
                      <rect x={x + 13} y={y + 50} width={barW} height={5} rx={3} fill={`url(#${barGrad})`} />
                      <title>{`${it.key} · custo ${compact(it.cost)} · share ${pct(share)}${it.mom == null ? "" : ` · MoM ${it.mom >= 0 ? "+" : ""}${pct(it.mom)}`}`}</title>
                    </g>
                  );
                })}
                {emptyBranch}
              </svg>
            );
          })()}
        </Box>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 1.2, p: 1.6, borderTop: "1px solid", borderColor: "divider", bgcolor: "#fcfcfd" }}>
          {[
            { title: "Top 10 ServiceSubcategory", field: "servicesubcategory", rows: topSubcategories },
            { title: "Top 10 SKU name", field: "skumeter", rows: topSkus },
          ].map(({ title, field, rows }) => (
            <Box key={field} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, p: 1.2, minWidth: 0 }}>
              <Typography variant="caption" sx={{ display: "block", fontSize: 10, fontWeight: 950, textTransform: "uppercase", letterSpacing: 0.65, color: "#475467", mb: 0.8 }}>{title}</Typography>
              <Stack spacing={0.55}>
                {rows.map((row, index) => {
                  const value = Number(row.realizado || 0);
                  const label = String(row[field] ?? EMPTY_DIMENSION_LABEL);
                  const share = nodeTotal ? Math.abs(value) / Math.abs(nodeTotal) * 100 : 0;
                  return (
                    <Box key={`${field}-${label}-${index}`} sx={{ display: "grid", gridTemplateColumns: "22px minmax(0, 1fr) auto", gap: 0.7, alignItems: "center" }}>
                      <Typography variant="caption" sx={{ color: "#667085", fontWeight: 900 }}>{index + 1}</Typography>
                      <Box sx={{ minWidth: 0 }}>
                        <Typography variant="caption" title={label} sx={{ display: "block", fontWeight: 800, color: "#344054", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</Typography>
                        <Box sx={{ mt: 0.25, height: 3, borderRadius: 99, bgcolor: "#eaecf0", overflow: "hidden" }}><Box sx={{ height: "100%", width: `${Math.min(100, share)}%`, bgcolor: "#7c3aed" }} /></Box>
                      </Box>
                      <Typography variant="caption" sx={{ color: "#344054", fontWeight: 950, whiteSpace: "nowrap" }}>{compact(value)}</Typography>
                    </Box>
                  );
                })}
                {!loading && rows.length === 0 && <Typography variant="caption" color="text.secondary">Sem dados no contexto atual.</Typography>}
              </Stack>
            </Box>
          ))}
        </Box>
        <Stack direction="row" justifyContent="space-between" gap={1.5} alignItems="center" flexWrap="wrap" sx={{ px: 1.6, py: 1.2, color: "#667085", fontSize: 10, fontWeight: 750 }}>
          <Box><Typography component="span" sx={{ color: "#344054", fontWeight: 900 }}>Como ler:</Typography> a espessura das conexões representa o share do custo no nó pai. Clique em um filho para aprofundar até ServiceSubcategory e skumeter.</Box>
          <Stack direction="row" gap={1.5} alignItems="center">
            <Box><Box component="span" sx={{ display: "inline-block", width: 9, height: 9, borderRadius: 99, bgcolor: "#7c3aed", mr: 0.6 }} />estrutura / share</Box>
            <Box><Box component="span" sx={{ display: "inline-block", width: 9, height: 9, borderRadius: 99, bgcolor: "#dc2626", mr: 0.6 }} />MoM positivo</Box>
            <Box><Box component="span" sx={{ display: "inline-block", width: 9, height: 9, borderRadius: 99, bgcolor: "#059669", mr: 0.6 }} />MoM negativo</Box>
          </Stack>
        </Stack>
      </Box>
    </Box>
  );
}
