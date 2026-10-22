/**
 * Painel de anomalias (`/alertas/painel`, aba 0 da página Anomalias).
 *
 * Mostra só o que o sistema de anomalias produz hoje:
 * - KPIs de `/anomalies/metrics` (abertas, críticas, tempo de resolução) e
 *   impacto de `/anomalies/impact/summary`;
 * - tendência diária, serviço e nó da hierarquia, calculados sobre a lista de
 *   casos de `/anomalies/cases` (até 200, por prioridade);
 * - lista priorizada por impacto, com abertura da investigação do caso.
 *
 * Impacto em R$ em outras telas (saving, exposição) fica na aba Impacto.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import Plot from "@/components/Plot";
import { listAnomalyCases, type AnomalyCase, type CaseSummary, type ImpactSummary } from "../../services/anomaliesApi";
import { colors } from "../../theme/tokens";
import { dataDisplayText } from "../../utils/dataPresentation";

const STATE_LABEL: Record<string, string> = {
  NEW: "Novo",
  ASSIGNED: "Atribuído",
  INVESTIGATING: "Em apuração",
  DECIDED: "Decidido",
  IN_REMEDIATION: "Em correção",
  VERIFYING: "Em conferência",
  RETROSPECTIVE: "Retrospectiva",
  CLOSED: "Encerrado",
};

const SEVERITY_COLOR: Record<string, "default" | "info" | "warning" | "error"> = {
  LOW: "default",
  MEDIUM: "info",
  HIGH: "warning",
  CRITICAL: "error",
};

const SEVERITY_LABEL: Record<string, string> = {
  LOW: "Baixa",
  MEDIUM: "Média",
  HIGH: "Alta",
  CRITICAL: "Crítica",
};

const TOP_GROUPS = 8;
const PRIORITY_ROWS = 10;

const money = (value: number, currency: string) =>
  value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 0 });

const hoursLabel = (hours: number | null | undefined) => {
  if (hours === null || hours === undefined) return "—";
  if (hours >= 48) return `${(hours / 24).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} dias`;
  if (hours >= 1) return `${hours.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} h`;
  return `${Math.round(hours * 60)} min`;
};

const shortDate = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString("pt-BR") : "—";

type Group = { label: string; impact: number; count: number };

/** Agrupa por chave, ordena por impacto e reúne a cauda em "Outros". */
function groupByImpact(cases: AnomalyCase[], keyOf: (kase: AnomalyCase) => string): Group[] {
  const groups = new Map<string, Group>();
  for (const kase of cases) {
    const label = keyOf(kase);
    const group = groups.get(label) ?? { label, impact: 0, count: 0 };
    group.impact += kase.anomalous_cost_cloud || 0;
    group.count += 1;
    groups.set(label, group);
  }
  const sorted = [...groups.values()].sort((a, b) => b.impact - a.impact);
  const head = sorted.slice(0, TOP_GROUPS);
  const tail = sorted.slice(TOP_GROUPS);
  if (tail.length) {
    head.push({
      label: `Outros (${tail.length})`,
      impact: tail.reduce((sum, group) => sum + group.impact, 0),
      count: tail.reduce((sum, group) => sum + group.count, 0),
    });
  }
  return head;
}

const grid = (columns: string) => ({
  display: "grid",
  gap: 2,
  gridTemplateColumns: { xs: "1fr", md: columns },
});

const chartLayout = (height: number) => ({
  height,
  margin: { l: 56, r: 16, t: 8, b: 36 },
  showlegend: false,
  bargap: 0.35,
});

type Props = {
  summary: CaseSummary;
  impactSummary: ImpactSummary | null;
  currency: string;
  onOpenCase: (id: number) => void;
  onOpenFilter: (filter: "open" | "overdue") => void;
  onOpenState: (state: string) => void;
};

export default function AnomaliesPainel({ summary, impactSummary, currency, onOpenCase, onOpenFilter, onOpenState }: Props) {
  const [cases, setCases] = useState<AnomalyCase[]>([]);
  const [sampleTotal, setSampleTotal] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Recarrega sempre que o resumo muda (botão Atualizar ou fim de detecção).
  useEffect(() => {
    let active = true;
    listAnomalyCases({ limit: 200, order_by: "score" })
      .then((page) => {
        if (!active) return;
        setCases(page.items);
        setSampleTotal(page.total);
        setLoadError(null);
      })
      .catch((err) => {
        if (active) setLoadError(err instanceof Error ? err.message : "Não foi possível carregar os casos");
      });
    return () => { active = false; };
  }, [summary]);

  const daily = useMemo(() => {
    const days = new Map<string, { date: string; count: number; impact: number }>();
    for (const kase of cases) {
      const date = kase.event_date.slice(0, 10);
      const day = days.get(date) ?? { date, count: 0, impact: 0 };
      day.count += 1;
      day.impact += kase.anomalous_cost_cloud || 0;
      days.set(date, day);
    }
    return [...days.values()].sort((a, b) => a.date.localeCompare(b.date));
  }, [cases]);

  const byService = useMemo(
    () => groupByImpact(cases, (kase) => kase.service_name || "Serviço não informado"),
    [cases],
  );
  const byNode = useMemo(
    () => groupByImpact(cases, (kase) => [kase.cloud, kase.subaccount_name].filter(Boolean).join(" › ") || "Nó não informado"),
    [cases],
  );
  const priority = useMemo(
    () => cases
      .filter((kase) => kase.state !== "CLOSED")
      .sort((a, b) => (b.anomalous_cost_cloud || 0) - (a.anomalous_cost_cloud || 0))
      .slice(0, PRIORITY_ROWS),
    [cases],
  );

  const kpis = [
    {
      label: "Abertas",
      value: String(summary.open),
      note: `${summary.new} novas · ${summary.overdue} com prazo vencido`,
      onClick: () => onOpenFilter("open"),
    },
    {
      label: "Críticas",
      value: String(summary.by_severity?.CRITICAL ?? 0),
      note: `de ${summary.total} casos registrados`,
      onClick: () => onOpenFilter("open"),
    },
    {
      label: "Impacto em R$",
      value: money(impactSummary?.incurred_excess_cost ?? summary.anomalous_cost_cloud, currency),
      note: "excesso estimado acima da faixa esperada",
      onClick: undefined,
    },
    {
      label: "Tempo para resolver",
      value: hoursLabel(summary.median_resolution_hours),
      note: "mediana dos casos encerrados",
      onClick: undefined,
    },
  ];

  const sampleNote = sampleTotal > cases.length
    ? `Base dos gráficos e da lista: ${cases.length} de ${sampleTotal} casos, por prioridade.`
    : null;

  return (
    <Stack spacing={2}>
      <Box sx={grid("repeat(4, minmax(0, 1fr))")}>
        {kpis.map((kpi) => (
          <Paper
            key={kpi.label}
            variant="outlined"
            onClick={kpi.onClick}
            sx={{ p: 2, minWidth: 0, cursor: kpi.onClick ? "pointer" : "default", "&:hover": kpi.onClick ? { borderColor: "primary.main" } : undefined }}
          >
            <Typography variant="overline" color="text.secondary">{kpi.label}</Typography>
            <Typography variant="h5" sx={{ fontVariantNumeric: "tabular-nums" }}>{kpi.value}</Typography>
            <Typography variant="caption" color="text.secondary">{kpi.note}</Typography>
          </Paper>
        ))}
      </Box>

      {loadError && <Alert severity="warning">{dataDisplayText(loadError)}</Alert>}
      {sampleNote && <Typography variant="caption" color="text.secondary">{sampleNote}</Typography>}

      <Box sx={grid("repeat(2, minmax(0, 1fr))")}>
        <Paper variant="outlined" sx={{ p: 2, minWidth: 0 }}>
          <Typography variant="subtitle2">Casos por dia</Typography>
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>Data do evento</Typography>
          {daily.length === 0 ? <Typography variant="body2" color="text.secondary">Sem casos para mostrar.</Typography> : (
            <Plot
              data={[{ type: "bar", x: daily.map((d) => d.date), y: daily.map((d) => d.count), marker: { color: colors.brand }, hovertemplate: "%{x}<br>%{y} caso(s)<extra></extra>" }]}
              layout={{ ...chartLayout(240), yaxis: { rangemode: "tozero" } }}
            />
          )}
        </Paper>
        <Paper variant="outlined" sx={{ p: 2, minWidth: 0 }}>
          <Typography variant="subtitle2">Impacto por dia</Typography>
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>Custo anômalo observado ({currency})</Typography>
          {daily.length === 0 ? <Typography variant="body2" color="text.secondary">Sem impacto para mostrar.</Typography> : (
            <Plot
              data={[{ type: "bar", x: daily.map((d) => d.date), y: daily.map((d) => d.impact), marker: { color: colors.brandLight }, hovertemplate: "%{x}<br>%{y:,.0f}<extra></extra>" }]}
              layout={{ ...chartLayout(240), yaxis: { rangemode: "tozero" } }}
            />
          )}
        </Paper>
      </Box>

      <Box sx={grid("repeat(2, minmax(0, 1fr))")}>
        <Paper variant="outlined" sx={{ p: 2, minWidth: 0 }}>
          <Typography variant="subtitle2">Impacto por serviço</Typography>
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>Top {TOP_GROUPS} serviços</Typography>
          {byService.length === 0 ? <Typography variant="body2" color="text.secondary">Sem casos para mostrar.</Typography> : (
            <Plot
              data={[{ type: "bar", orientation: "h", y: byService.map((g) => g.label), x: byService.map((g) => g.impact), marker: { color: colors.brand }, hovertemplate: "%{y}<br>%{x:,.0f}<extra></extra>" }]}
              layout={{ ...chartLayout(Math.max(180, byService.length * 30 + 40)), margin: { l: 160, r: 16, t: 8, b: 36 }, yaxis: { automargin: true, autorange: "reversed" } }}
            />
          )}
        </Paper>
        <Paper variant="outlined" sx={{ p: 2, minWidth: 0 }}>
          <Typography variant="subtitle2">Impacto por nó da estrutura</Typography>
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>Cloud › subconta</Typography>
          {byNode.length === 0 ? <Typography variant="body2" color="text.secondary">Sem casos para mostrar.</Typography> : (
            <Plot
              data={[{ type: "bar", orientation: "h", y: byNode.map((g) => g.label), x: byNode.map((g) => g.impact), marker: { color: colors.brand }, hovertemplate: "%{y}<br>%{x:,.0f}<extra></extra>" }]}
              layout={{ ...chartLayout(Math.max(180, byNode.length * 30 + 40)), margin: { l: 160, r: 16, t: 8, b: 36 }, yaxis: { automargin: true, autorange: "reversed" } }}
            />
          )}
        </Paper>
      </Box>

      <Paper variant="outlined" sx={{ p: 2, minWidth: 0 }}>
        <Typography variant="subtitle2">Casos priorizados por impacto</Typography>
        <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>Casos não encerrados, do maior para o menor impacto.</Typography>
        {priority.length === 0 ? <Typography variant="body2" color="text.secondary">Nenhum caso em aberto.</Typography> : (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Severidade</TableCell>
                  <TableCell>Serviço</TableCell>
                  <TableCell>Nó</TableCell>
                  <TableCell align="right">Impacto</TableCell>
                  <TableCell>Situação</TableCell>
                  <TableCell>Prazo de ciência</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {priority.map((kase) => (
                  <TableRow key={kase.id} hover>
                    <TableCell>
                      <Chip size="small" color={SEVERITY_COLOR[kase.severity] ?? "default"} label={SEVERITY_LABEL[kase.severity] ?? kase.severity} />
                    </TableCell>
                    <TableCell>
                      {kase.service_name || "Serviço não informado"}
                      <Typography variant="caption" color="text.secondary" display="block">{kase.event_type}</Typography>
                    </TableCell>
                    <TableCell>{[kase.cloud, kase.subaccount_name].filter(Boolean).join(" › ") || "—"}</TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{money(kase.anomalous_cost_cloud || 0, currency)}</TableCell>
                    <TableCell>{STATE_LABEL[kase.state] ?? kase.state}</TableCell>
                    <TableCell>{shortDate(kase.due_at)}</TableCell>
                    <TableCell align="right">
                      <Button size="small" onClick={() => onOpenCase(kase.id)}>Abrir investigação</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Situação dos casos</Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {Object.entries(summary.by_state).map(([state, count]) => (
            <Chip key={state} size="small" clickable onClick={() => onOpenState(state)} label={`${STATE_LABEL[state] ?? state}: ${count}`} />
          ))}
          {Object.keys(summary.by_state).length === 0 && (
            <Typography variant="body2" color="text.secondary">Nenhum caso registrado até agora.</Typography>
          )}
        </Stack>
        <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1.5 }}>
          {dataDisplayText(summary.notes.join(" · "))}
        </Typography>
      </Paper>
    </Stack>
  );
}
