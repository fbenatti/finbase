import React, { useEffect, useMemo, useState } from "react";
import { Box, Button, Chip, Paper, Stack, Tooltip, Typography } from "@mui/material";
import { AccountBalanceWalletOutlined, CheckCircle, ChevronRightOutlined, GpsFixedOutlined, OpenInNewOutlined, TroubleshootOutlined } from "@mui/icons-material";
import { CircularProgress } from "@mui/material";
import { colors } from "../../theme/tokens";
import type { AnomalyCase } from "../../services/anomaliesApi";
import { type ForecastAccuracyMonth, type ForecastAck, type GoldQueryFilter, formatApiError, getBudgetAccuracy, getForecastAccuracyDelta, queryDimonTable } from "../../services/api";
import { pathSegmentFilters } from "./HierarchicalDecompTree";
import { dayKey, periodKey } from "./costSignals";

// Matriz de desvios: uma linha por tipo de desvio (comportamento, orcamento e
// acuracia da projecao) e uma coluna por mes. A cor de cada celula mostra a
// intensidade do desvio; o clique abre o detalhe do cruzamento ao lado.
type Kind = "behavior" | "budget" | "accuracy";
type MonthRow = { key: string; label: string; actual: number; budget: number; closed: boolean };
type Cell = { value: number; pct?: number | null; text: string; tone: string; strength: number; badge?: "open" | "justified"; empty?: boolean };

const ORANGE = "#E8590C";
const RED = "#D92D20";
const GREEN = "#12B76A";
const BLUE = "#0F62FE";
const PURPLE = "#7A5AF8";

const KINDS: Array<{ kind: Kind; title: string; question: string; color: string; icon: React.ReactNode }> = [
  { kind: "behavior", title: "Comportamento", question: "Gastamos fora do padrão?", color: ORANGE, icon: <TroubleshootOutlined fontSize="small" /> },
  { kind: "budget", title: "Orçamento", question: "Cabe no que foi aprovado?", color: GREEN, icon: <AccountBalanceWalletOutlined fontSize="small" /> },
  { kind: "accuracy", title: "Acurácia da projeção", question: "Acertamos a Forecast?", color: PURPLE, icon: <GpsFixedOutlined fontSize="small" /> },
];

const brl = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const signed = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${brl(Math.abs(value))}`;
const pctText = (value?: number | null) => (value == null ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`);
const alpha = (hex: string, a: number) => `${hex}${Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0")}`;

export default function DeviationMatrix({ months, anomalies, forecastMonths, justifications, onOpenAccuracy, onOpenAnomalies, onOpenBudget, onOpenCase, table, filters, path, departmentId }: {
  months: MonthRow[];
  anomalies: AnomalyCase[];
  forecastMonths: ForecastAccuracyMonth[];
  justifications: ForecastAck[];
  onOpenAccuracy: () => void;
  onOpenAnomalies: () => void;
  onOpenBudget: () => void;
  onOpenCase: (id: number) => void;
  table: string;
  filters: GoldQueryFilter[];
  path: string[];
  departmentId?: number;
}) {
  const [selected, setSelected] = useState<{ kind: Kind; key: string } | null>(null);

  const data = useMemo(() => {
    const behavior: Record<string, { cost: number; count: number; open: number; items: AnomalyCase[] }> = {};
    anomalies.forEach((item) => {
      const key = periodKey(item.period);
      const slot = behavior[key] ?? (behavior[key] = { cost: 0, count: 0, open: 0, items: [] });
      slot.cost += Number(item.anomalous_cost_cloud || 0);
      slot.count += 1;
      if (!item.closed_at) slot.open += 1;
      slot.items.push(item);
    });
    const accuracy: Record<string, ForecastAccuracyMonth> = {};
    forecastMonths.forEach((month) => { accuracy[periodKey(month.target_month)] = month; });
    const acks: Record<string, ForecastAck[]> = {};
    justifications.forEach((ack) => { (acks[periodKey(ack.target_month)] ??= []).push(ack); });

    const cells: Record<Kind, Record<string, Cell>> = { behavior: {}, budget: {}, accuracy: {} };
    const maxBehavior = Math.max(1, ...Object.values(behavior).map((b) => b.cost));
    const budgetDeltas = months.filter((m) => m.closed && m.budget > 0).map((m) => Math.abs(m.actual - m.budget) / m.budget);
    const maxBudget = Math.max(0.05, ...budgetDeltas);
    const maxAccuracy = Math.max(0.05, ...Object.values(accuracy).map((m) => Math.abs(Number(m.variance_pct ?? 0))));

    months.forEach((m) => {
      const b = behavior[m.key];
      cells.behavior[m.key] = b
        ? { value: b.cost, text: brl(b.cost), tone: ORANGE, strength: 0.15 + 0.85 * (b.cost / maxBehavior), badge: b.open ? "open" : undefined }
        : { value: 0, text: "—", tone: ORANGE, strength: 0, empty: true };

      if (m.closed && m.budget > 0) {
        const delta = m.actual - m.budget;
        const pct = delta / m.budget;
        cells.budget[m.key] = { value: delta, pct, text: pctText(pct), tone: delta > 0 ? RED : GREEN, strength: 0.15 + 0.85 * Math.min(1, Math.abs(pct) / maxBudget) };
      } else {
        cells.budget[m.key] = { value: 0, text: "—", tone: GREEN, strength: 0, empty: true };
      }

      const f = accuracy[m.key];
      if (f && f.forecast_value != null && f.actual_value != null) {
        const delta = Number(f.actual_value) - Number(f.forecast_value);
        const pct = f.variance_pct != null ? Number(f.variance_pct) : Number(f.forecast_value) ? delta / Number(f.forecast_value) : 0;
        cells.accuracy[m.key] = { value: delta, pct, text: pctText(pct), tone: delta > 0 ? ORANGE : BLUE, strength: 0.15 + 0.85 * Math.min(1, Math.abs(pct) / maxAccuracy), badge: acks[m.key]?.length ? "justified" : undefined };
      } else {
        cells.accuracy[m.key] = { value: 0, text: "—", tone: PURPLE, strength: 0, empty: true };
      }
    });

    const closedBudget = months.filter((m) => m.closed && m.budget > 0);
    const budgetBase = closedBudget.reduce((s, m) => s + m.budget, 0);
    const budgetDelta = closedBudget.reduce((s, m) => s + m.actual - m.budget, 0);
    const acc = Object.values(accuracy).filter((m) => m.forecast_value != null && m.actual_value != null);
    const accForecast = acc.reduce((s, m) => s + Number(m.forecast_value), 0);
    const accDelta = acc.reduce((s, m) => s + Number(m.actual_value) - Number(m.forecast_value), 0);
    const totals = {
      behavior: { main: brl(Object.values(behavior).reduce((s, b) => s + b.cost, 0)), sub: `${anomalies.length} casos · ${anomalies.filter((a) => !a.closed_at).length} abertos` },
      budget: { main: signed(budgetDelta), sub: budgetBase ? `${pctText(budgetDelta / budgetBase)} vs orçado nos meses fechados` : "Sem orçamento nos meses fechados" },
      accuracy: { main: accForecast ? pctText(accDelta / accForecast) : "—", sub: acc.length ? `${signed(accDelta)} · ${justifications.length} justificativa(s)` : "Sem mês fechado com projeção" },
    };
    return { behavior, accuracy, acks, cells, totals };
  }, [months, anomalies, forecastMonths, justifications]);

  const monthOf = (key: string) => months.find((m) => m.key === key);
  const sel = selected;

  return (
    <Paper variant="outlined" sx={{ p: 2.25, borderRadius: "18px", borderColor: colors.border, boxShadow: "0 6px 20px rgba(24, 39, 75, 0.035)" }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={1} mb={2}>
        <Box>
          <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Matriz de desvios</Typography>
          <Typography variant="caption" color="text.secondary">Três perguntas, mês a mês. Quanto mais forte a cor, maior o desvio. Clique numa célula para ver o detalhe.</Typography>
        </Box>
      </Stack>

      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" }, mb: 2 }}>
        {KINDS.map((k) => (
          <Box key={k.kind} sx={{ p: 1.5, borderRadius: "14px", bgcolor: alpha(k.color, 0.06), border: `1px solid ${alpha(k.color, 0.2)}` }}>
            <Stack direction="row" gap={1} alignItems="center" sx={{ color: k.color }}>{k.icon}<Typography variant="caption" fontWeight={800} sx={{ textTransform: "uppercase", letterSpacing: 0.4 }}>{k.title}</Typography></Stack>
            <Typography variant="h5" fontWeight={800} color={colors.text} mt={0.5}>{data.totals[k.kind].main}</Typography>
            <Typography variant="caption" color="text.secondary">{data.totals[k.kind].sub}</Typography>
          </Box>
        ))}
      </Box>

      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 8fr) minmax(280px, 4fr)" } }}>
        <Box sx={{ overflowX: "auto" }}>
          <Box sx={{ display: "grid", gridTemplateColumns: `190px repeat(${months.length}, minmax(52px, 1fr))`, gap: 0.75, minWidth: 190 + months.length * 58, alignItems: "center" }}>
            <Box />
            {months.map((m) => <Typography key={m.key} variant="caption" color="text.secondary" textAlign="center" fontWeight={700}>{m.label}</Typography>)}
            {KINDS.map((k) => [
              <Stack key={`${k.kind}-label`} direction="row" gap={1} alignItems="center" sx={{ pr: 1 }}>
                <Box sx={{ width: 30, height: 30, borderRadius: "9px", display: "grid", placeItems: "center", bgcolor: alpha(k.color, 0.12), color: k.color, flexShrink: 0 }}>{k.icon}</Box>
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={800} color={colors.text} lineHeight={1.2}>{k.title}</Typography>
                  <Typography variant="caption" color="text.secondary" lineHeight={1.2}>{k.question}</Typography>
                </Box>
              </Stack>,
              ...months.map((m) => {
                const cell = data.cells[k.kind][m.key];
                const active = sel?.kind === k.kind && sel.key === m.key;
                return (
                  <Tooltip key={`${k.kind}-${m.key}`} title={cell.empty ? "Sem dado" : `${k.title} · ${m.label}: ${k.kind === "behavior" ? cell.text : `${signed(cell.value)} (${cell.text})`}`} arrow>
                    <Box
                      onClick={() => !cell.empty && setSelected(active ? null : { kind: k.kind, key: m.key })}
                      sx={{
                        position: "relative", height: 46, borderRadius: "10px", display: "grid", placeItems: "center",
                        cursor: cell.empty ? "default" : "pointer",
                        bgcolor: cell.empty ? "#F4F6FA" : alpha(cell.tone, cell.strength * 0.85),
                        color: cell.strength > 0.55 ? "#FFFFFF" : colors.text,
                        outline: active ? `2px solid ${colors.text}` : "none", outlineOffset: 1,
                        transition: "transform .12s", "&:hover": cell.empty ? undefined : { transform: "scale(1.06)" },
                      }}
                    >
                      <Typography variant="caption" fontWeight={800} sx={{ fontSize: 11 }}>{cell.text}</Typography>
                      {cell.badge === "open" && <Box sx={{ position: "absolute", top: 4, right: 4, width: 7, height: 7, borderRadius: "50%", bgcolor: RED, border: "1.5px solid #fff" }} />}
                      {cell.badge === "justified" && <CheckCircle sx={{ position: "absolute", top: 2, right: 2, fontSize: 13, color: GREEN, bgcolor: "#fff", borderRadius: "50%" }} />}
                    </Box>
                  </Tooltip>
                );
              }),
            ])}
          </Box>
          <Stack direction="row" gap={2} flexWrap="wrap" mt={1.5}>
            {[["Acima do previsto / estouro", RED], ["Abaixo do previsto", BLUE], ["Dentro do orçamento", GREEN], ["Custo fora do padrão", ORANGE]].map(([label, color]) => (
              <Stack key={label} direction="row" gap={0.75} alignItems="center"><Box sx={{ width: 10, height: 10, borderRadius: "3px", bgcolor: color }} /><Typography variant="caption" color="text.secondary">{label}</Typography></Stack>
            ))}
            <Stack direction="row" gap={0.75} alignItems="center"><Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: RED }} /><Typography variant="caption" color="text.secondary">caso aberto</Typography></Stack>
            <Stack direction="row" gap={0.75} alignItems="center"><CheckCircle sx={{ fontSize: 13, color: GREEN }} /><Typography variant="caption" color="text.secondary">desvio justificado</Typography></Stack>
          </Stack>
        </Box>

        <Box sx={{ p: 1.75, borderRadius: "14px", bgcolor: "#FAFBFD", border: `1px solid ${colors.border}`, minHeight: 220 }}>
          {!sel ? (
            <Stack gap={1} height="100%" justifyContent="center">
              <Typography variant="subtitle2" fontWeight={800} color={colors.text}>Como ler</Typography>
              <Typography variant="body2" color="text.secondary"><b>Comportamento</b>: custo dos casos do módulo de desvios e anomalias no mês.</Typography>
              <Typography variant="body2" color="text.secondary"><b>Orçamento</b>: realizado contra o orçado do mês.</Typography>
              <Typography variant="body2" color="text.secondary"><b>Acurácia</b>: realizado contra o projetado no início do mês.</Typography>
            </Stack>
          ) : sel.kind === "behavior" ? (
            <Stack gap={1}>
              <Typography variant="subtitle2" fontWeight={800} color={ORANGE}>Comportamento · {monthOf(sel.key)?.label}</Typography>
              <Typography variant="body2" color="text.secondary">{data.behavior[sel.key]?.count ?? 0} casos, {data.behavior[sel.key]?.open ?? 0} abertos</Typography>
              {(data.behavior[sel.key]?.items ?? []).sort((a, z) => Number(z.anomalous_cost_cloud || 0) - Number(a.anomalous_cost_cloud || 0)).slice(0, 5).map((item) => (
                <Box key={item.id} onClick={() => onOpenCase(item.id)} sx={{ p: 1, borderRadius: "10px", bgcolor: "#fff", border: `1px solid ${colors.border}`, cursor: "pointer", "&:hover": { borderColor: ORANGE } }}>
                  <Stack direction="row" justifyContent="space-between" gap={1}><Typography variant="body2" fontWeight={700} noWrap title={item.service_name ?? ""}>{item.service_name ?? "Sem serviço"}</Typography><Typography variant="body2" fontWeight={800}>{brl(Number(item.anomalous_cost_cloud || 0))}</Typography></Stack>
                  <Typography variant="caption" color="text.secondary" noWrap display="block" title={item.subaccount_name ?? ""}>{item.subaccount_name ?? "Sem subconta"} · {dayKey(item.event_date).split("-").reverse().join("/")}</Typography>
                </Box>
              ))}
              <Button size="small" onClick={onOpenAnomalies} sx={{ alignSelf: "flex-start", textTransform: "none" }}>Abrir casos</Button>
            </Stack>
          ) : sel.kind === "budget" ? (
            <Stack gap={1}>
              <Typography variant="subtitle2" fontWeight={800} color={data.cells.budget[sel.key].tone}>Orçamento · {monthOf(sel.key)?.label}</Typography>
              <Detail label="Realizado" value={brl(monthOf(sel.key)?.actual ?? 0)} />
              <Detail label="Orçado" value={brl(monthOf(sel.key)?.budget ?? 0)} />
              <Detail label="Desvio" value={`${signed(data.cells.budget[sel.key].value)} (${data.cells.budget[sel.key].text})`} strong />
              <Button size="small" onClick={onOpenBudget} sx={{ alignSelf: "flex-start", textTransform: "none" }}>Abrir orçamento</Button>
            </Stack>
          ) : (
            <Stack gap={1}>
              <Typography variant="subtitle2" fontWeight={800} color={PURPLE}>Acurácia da projeção · {monthOf(sel.key)?.label}</Typography>
              <Detail label="Projetado no início do mês" value={brl(Number(data.accuracy[sel.key]?.forecast_value ?? 0))} />
              <Detail label="Realizado" value={brl(Number(data.accuracy[sel.key]?.actual_value ?? 0))} />
              <Detail label="Desvio" value={`${signed(data.cells.accuracy[sel.key].value)} (${data.cells.accuracy[sel.key].text})`} strong />
              {data.accuracy[sel.key]?.accuracy != null && <Detail label="Acurácia" value={`${(Number(data.accuracy[sel.key]?.accuracy) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`} />}
              <Typography variant="caption" fontWeight={800} color="text.secondary" mt={0.5}>JUSTIFICATIVAS</Typography>
              {(data.acks[sel.key] ?? []).length ? [...(data.acks[sel.key] ?? [])].sort((a, z) => Number(z.variance || 0) - Number(a.variance || 0)).slice(0, 4).map((ack) => (
                <Box key={ack.id} sx={{ p: 1, borderRadius: "10px", bgcolor: "#fff", border: `1px solid ${colors.border}` }}>
                  <Stack direction="row" justifyContent="space-between" gap={1}><Typography variant="body2" fontWeight={700} noWrap>{ack.department_name}</Typography>{ack.category && <Chip size="small" label={ack.category} sx={{ height: 18, fontSize: 11 }} />}</Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{ack.reason}</Typography>
                </Box>
              )) : <Typography variant="body2" color="text.secondary">Nenhuma justificativa registrada para este mês.</Typography>}
              <Button size="small" onClick={onOpenAccuracy} sx={{ alignSelf: "flex-start", textTransform: "none" }}>Registrar ou revisar justificativas</Button>
            </Stack>
          )}
        </Box>
      </Box>
      {sel && (
        <DrillPanel
          key={`${sel.kind}-${sel.key}`}
          kind={sel.kind}
          monthKey={sel.key}
          monthLabel={monthOf(sel.key)?.label ?? sel.key}
          runId={data.accuracy[sel.key]?.run_id ?? null}
          anomalies={data.behavior[sel.key]?.items ?? []}
          table={table}
          filters={filters}
          path={path}
          departmentId={departmentId}
          onOpenCase={onOpenCase}
        />
      )}
    </Paper>
  );
}

// Abre o desvio selecionado pelos filhos da estrutura organizacional. Cada clique
// desce um nivel; na projecao, quando nao ha mais filhos, mostra os servicos.
type DrillRow = { name: string; value: number; base?: number; actual?: number; drillable: boolean };

function DrillPanel({ kind, monthKey, monthLabel, runId, anomalies, table, filters, path, departmentId, onOpenCase }: {
  kind: Kind; monthKey: string; monthLabel: string; runId: number | null; anomalies: AnomalyCase[];
  table: string; filters: GoldQueryFilter[]; path: string[]; departmentId?: number; onOpenCase: (id: number) => void;
}) {
  const [drill, setDrill] = useState<string[]>([]);
  const [rows, setRows] = useState<DrillRow[]>([]);
  const [levelLabel, setLevelLabel] = useState("Estrutura");
  const [caseRows, setCaseRows] = useState<AnomalyCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const base = useMemo(() => [...path, ...drill], [path, drill]);
  const meta = KINDS.find((k) => k.kind === kind)!;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setLevelLabel("Estrutura");
    setCaseRows([]);
    const depth = base.length;
    const prefixOk = (levels: string[]) => base.every((name, i) => levels[i] === name);
    const run = async () => {
      if (kind === "budget") {
        const result = await getBudgetAccuracy(Number(monthKey.slice(0, 4)), Number(monthKey.slice(4, 6)), departmentId ?? null, "monthly");
        const groups = new Map<string, DrillRow>();
        result.lines.filter((line) => line.department_levels.length > depth && prefixOk(line.department_levels)).forEach((line) => {
          const name = line.department_levels[depth];
          const row = groups.get(name) ?? { name, value: 0, base: 0, drillable: false };
          row.value += Number(line.actual_value || 0) - Number(line.budget_value || 0);
          row.base = (row.base ?? 0) + Number(line.budget_value || 0);
          if (line.department_levels.length > depth + 1) row.drillable = true;
          groups.set(name, row);
        });
        return { rows: [...groups.values()] };
      }
      if (kind === "accuracy") {
        if (!runId) return { rows: [] };
        const result = await getForecastAccuracyDelta(runId, monthKey, "showback", base.join(" > "));
        return {
          levelLabel: result.level_label,
          rows: result.records.map((item) => ({
            name: item.name,
            value: Number(item.variance || 0),
            base: Number(item.forecast_value || 0),
            actual: Number(item.actual_value || 0),
            drillable: !result.leaf && item.path.length > depth,
          })),
        };
      }
      const column = `hierarquia_${depth + 1}`;
      const scoped = [...filters.filter((f) => !f.column.startsWith("hierarquia_")), ...pathSegmentFilters(base, false)];
      const result = await queryDimonTable({ table, rows: [column, "subaccountname", "servicename"], filters: scoped, row_limit: 10000, sort_column: null, sort_direction: "desc" });
      const childOf = new Map<string, string>();
      result.rows.forEach((row) => {
        const key = `${String(row.subaccountname ?? "").toLowerCase()}|${String(row.servicename ?? "").toLowerCase()}`;
        if (!childOf.has(key)) childOf.set(key, String(row[column] ?? "Sem classificação"));
      });
      const inBase = anomalies.filter((item) => childOf.has(`${String(item.subaccount_name ?? "").toLowerCase()}|${String(item.service_name ?? "").toLowerCase()}`));
      const groups = new Map<string, DrillRow>();
      inBase.forEach((item) => {
        const name = childOf.get(`${String(item.subaccount_name ?? "").toLowerCase()}|${String(item.service_name ?? "").toLowerCase()}`)!;
        const row = groups.get(name) ?? { name, value: 0, drillable: name !== "Sem classificação" && depth < 6 };
        row.value += Number(item.anomalous_cost_cloud || 0);
        groups.set(name, row);
      });
      return { rows: [...groups.values()], cases: inBase };
    };
    run()
      .then((out) => {
        if (!alive) return;
        setRows(out.rows.filter((r) => kind === "accuracy" ? Math.abs(r.base ?? 0) > 0.5 || Math.abs(r.actual ?? 0) > 0.5 : Math.abs(r.value) > 0.5).sort((a, z) => kind === "accuracy" ? Math.abs(z.value) - Math.abs(a.value) : z.value - a.value));
        setLevelLabel((out as { levelLabel?: string }).levelLabel ?? "Estrutura");
        setCaseRows(((out as { cases?: AnomalyCase[] }).cases ?? []).sort((a, z) => Number(z.anomalous_cost_cloud || 0) - Number(a.anomalous_cost_cloud || 0)));
      })
      .catch((reason) => { if (alive) { setRows([]); setError(formatApiError(reason, "Não foi possível abrir este nível.")); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, monthKey, runId, base.join("|")]);

  const max = Math.max(1, ...rows.map((r) => Math.abs(r.value)));
  const toneOf = (value: number) => (kind === "behavior" ? ORANGE : kind === "budget" ? (value > 0 ? RED : GREEN) : value > 0 ? ORANGE : BLUE);
  const fmt = (row: DrillRow) => (kind === "behavior" ? brl(row.value) : `${signed(row.value)}${row.base ? ` (${pctText(row.value / row.base)})` : ""}`);

  return (
    <Box sx={{ mt: 2, p: 2, borderRadius: "14px", border: `1px solid ${alpha(meta.color, 0.35)}`, bgcolor: alpha(meta.color, 0.03) }}>
      <Stack direction="row" alignItems="center" gap={0.25} flexWrap="wrap" mb={1.5}>
        <Box sx={{ color: meta.color, display: "flex", mr: 0.75 }}>{meta.icon}</Box>
        <Typography variant="subtitle2" fontWeight={800} color={colors.text} sx={{ mr: 1 }}>{meta.title} · {monthLabel} · {kind === "accuracy" ? `por ${levelLabel}` : "por estrutura"}</Typography>
        <Button size="small" onClick={() => setDrill([])} sx={{ textTransform: "none", minWidth: 0, px: 0.75, fontWeight: drill.length ? 500 : 800 }}>{path[path.length - 1] ?? "Toda a nuvem"}</Button>
        {drill.map((name, i) => (
          <Stack key={`${name}-${i}`} direction="row" alignItems="center">
            <ChevronRightOutlined sx={{ fontSize: 16, color: "text.disabled" }} />
            <Button size="small" onClick={() => setDrill(drill.slice(0, i + 1))} sx={{ textTransform: "none", minWidth: 0, px: 0.75, fontWeight: i === drill.length - 1 ? 800 : 500 }}>{name}</Button>
          </Stack>
        ))}
        {loading && <CircularProgress size={14} sx={{ ml: 1 }} />}
      </Stack>

      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: caseRows.length ? "minmax(0, 1fr) minmax(0, 1fr)" : "1fr" } }}>
        <Stack gap={0.75}>
          {!loading && !rows.length && <Typography variant="body2" color="text.secondary">{error || "Nenhum filho com desvio neste nível."}</Typography>}
          {rows.map((row) => (
            <Stack
              key={row.name}
              direction="row"
              alignItems="center"
              gap={1.25}
              onClick={() => row.drillable && setDrill([...drill, row.name])}
              sx={{ p: 1, borderRadius: "10px", bgcolor: "#fff", border: `1px solid ${colors.border}`, cursor: row.drillable ? "pointer" : "default", "&:hover": row.drillable ? { borderColor: meta.color } : undefined }}
            >
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Stack direction="row" justifyContent="space-between" gap={1}>
                  <Typography variant="body2" fontWeight={700} sx={{ wordBreak: "break-word" }}>{row.name}</Typography>
                  <Typography variant="body2" fontWeight={800} sx={{ color: toneOf(row.value), flexShrink: 0 }}>{fmt(row)}</Typography>
                </Stack>
                {kind === "accuracy" && <Typography variant="caption" color="text.secondary">Projetado {brl(row.base ?? 0)} · Realizado {brl(row.actual ?? 0)}</Typography>}
                <Box sx={{ height: 5, mt: 0.75, borderRadius: 3, bgcolor: "#EEF2F7", overflow: "hidden" }}>
                  <Box sx={{ width: `${(Math.abs(row.value) / max) * 100}%`, height: "100%", bgcolor: toneOf(row.value), borderRadius: 3 }} />
                </Box>
              </Box>
              {row.drillable && <ChevronRightOutlined fontSize="small" sx={{ color: "text.disabled" }} />}
            </Stack>
          ))}
        </Stack>

        {caseRows.length > 0 && (
          <Stack gap={0.75}>
            <Typography variant="caption" fontWeight={800} color="text.secondary">CASOS NESTE NÍVEL · clique para abrir</Typography>
            {caseRows.slice(0, 12).map((item) => (
              <Stack key={item.id} direction="row" alignItems="center" gap={1} onClick={() => onOpenCase(item.id)} sx={{ p: 1, borderRadius: "10px", bgcolor: "#fff", border: `1px solid ${colors.border}`, cursor: "pointer", "&:hover": { borderColor: ORANGE } }}>
                <Box sx={{ width: 8, height: 8, transform: "rotate(45deg)", bgcolor: item.closed_at ? "#98A2B3" : ORANGE, flexShrink: 0 }} />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={700} noWrap title={item.service_name ?? ""}>{item.service_name ?? "Sem serviço"}</Typography>
                  <Typography variant="caption" color="text.secondary" noWrap display="block" title={item.subaccount_name ?? ""}>{item.subaccount_name ?? "Sem subconta"} · {dayKey(item.event_date).split("-").reverse().join("/")}</Typography>
                </Box>
                <Typography variant="body2" fontWeight={800}>{brl(Number(item.anomalous_cost_cloud || 0))}</Typography>
                <OpenInNewOutlined sx={{ fontSize: 16, color: "text.disabled" }} />
              </Stack>
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  );
}

function Detail({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <Stack direction="row" justifyContent="space-between" gap={1} sx={{ py: 0.5, borderBottom: `1px dashed ${colors.border}` }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="body2" fontWeight={strong ? 800 : 600} color={colors.text}>{value}</Typography>
    </Stack>
  );
}
