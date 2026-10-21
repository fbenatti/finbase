import { dataDisplayText } from "../../utils/dataPresentation";
import {
  Alert, Box, Card, CardContent, Chip, Grid, Stack, Tooltip, Typography,
} from "@mui/material";
import { widgetHelp } from "./AzurePtuOpsReport";
import { tokensLabel } from "./AzurePtuFormat";

/**
 * Resumo executivo financeiro do Azure PTU Ops alimentado pela Gold FOCUS
 * (dest_modelo_focus / dest_modelo_focus_planning). Compartilhado entre a
 * aba "Financeiro (Gold)" da tela e a seção financeira do relatório PDF.
 *
 * Contrato do payload (backend -> `costs` no ops-overview):
 *   costs.current.totals/projects  -> KPIs do mês de referência
 *     reservation_units/cost       -> reserva Microsoft.Capacity (100 PTUs)
 *     ondemand_units/cost          -> on-demand Provisioned Throughput Units (100 PTUs, 1/Month);
 *                                    estimado pelo último realizado quando o mês não recebeu a compra
 *   tokenData / aiDemand           -> tokens dos últimos 30 dias (custo/milhão)
 * O consumo diário/horário (1/Hour) não é retornado — já está na compra mensal.
 */

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const brl2 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 });
const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const n = (v: any) => (v == null ? "—" : fmt.format(Number(v)));
const money = (v: any) => (v == null || isNaN(Number(v)) ? "—" : brl.format(Number(v)));
const money2 = (v: any) => (v == null || isNaN(Number(v)) ? "—" : brl2.format(Number(v)));
const pct = (v: any) => (v == null || isNaN(Number(v)) ? "—" : `${fmt.format(Number(v))}%`);

const PROJECT_COLORS: Record<string, string> = {
  "Vivo Mais": "#2563EB",
  "Aura Brain": "#7C3AED",
};
const PROJECT_KEY_LABEL: Record<string, string> = {
  "vivo-mais": "Vivo Mais",
  "aura-brain": "Aura Brain",
};

function Kpi({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  const help = widgetHelp[label] || "Indicador financeiro calculado a partir da base consolidada FOCUS no mês de referência.";
  return (
    <Tooltip title={<Typography variant="body2" sx={{ maxWidth: 340, p: 0.5 }}>{help}</Typography>} arrow placement="top" enterDelay={250}>
      <Card sx={{ height: "100%", borderTop: "3px solid #0EA5E9", cursor: "help" }}><CardContent>
        <Typography variant="body2" color="text.secondary">{label} ⓘ</Typography>
        <Typography variant="h6">{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
      </CardContent></Card>
    </Tooltip>
  );
}

export function PtuFinanceViews({ costs, tokenData, aiDemand, provisioned }: {
  costs: any;
  tokenData?: any;
  aiDemand?: any;
  provisioned?: { total_ptus?: number; deployment_count?: number };
}) {
  if (!costs || costs.available === false) {
    return <Alert severity="warning">Dados financeiros indisponíveis{dataDisplayText(costs?.error ? `: ${costs.error}` : "")}. A aba operacional continua completa; os custos PTU não foram carregados.</Alert>;
  }
  if (costs.empty) {
    return <Alert severity="info">Não foram encontrados registros consolidados para os períodos consultados. Verifique a atualização dos dados de custos.</Alert>;
  }

  const current = costs.current || {};
  const totals = current.totals || {};
  const projects = current.projects || {};
  const projectsList: string[] = (costs.projects && costs.projects.length ? costs.projects : Object.keys(projects));
  const notes = costs.notes || [];

  const ondemandEstimated = totals.ondemand_estimated === true;
  const reservationReferenced = totals.reservation_referenced === true;
  const ptuMonthlyCost = (Number(totals.reservation_cost) || 0) + (Number(totals.ondemand_cost) || 0);
  const totalUnits = (Number(totals.reservation_units) || 0) + (Number(totals.ondemand_units) || 0);
  const reservaUnitPrice = totals.reservation_units ? (Number(totals.reservation_cost) / Number(totals.reservation_units)) : null;
  const ondemandUnitPrice = totals.ondemand_units ? (Number(totals.ondemand_cost) / Number(totals.ondemand_units)) : null;
  const deltaPct = (reservaUnitPrice != null && ondemandUnitPrice != null)
    ? ((ondemandUnitPrice - reservaUnitPrice) / reservaUnitPrice) * 100
    : null;

  // ── Tokens dos últimos 30 dias e custo por milhão de tokens ─────────────
  // A compra de PTU é mensal; o custo/milhão usa SEMPRE os últimos 30 dias
  // (backend envia tokens_last_30d), independente da janela selecionada.
  const last30d = tokenData?.tokens_last_30d;
  const seriesTokens = (tokenData?.series || []).reduce((s: number, r: any) => s + Number(r.total_tokens || 0), 0);
  const totalTokens = Number(last30d?.total || aiDemand?.total_tokens || seriesTokens || 0);
  const tokensByProject: Record<string, number> = {};
  const last30dByProject: Record<string, any> = last30d?.by_project || {};
  if (Object.keys(last30dByProject).length) {
    Object.entries(last30dByProject).forEach(([k, v]) => {
      const key = PROJECT_KEY_LABEL[k] || k || "—";
      tokensByProject[key] = (tokensByProject[key] || 0) + Number(v || 0);
    });
  } else {
    (tokenData?.deployment_rankings || []).forEach((r: any) => {
      const key = PROJECT_KEY_LABEL[r.project_key] || r.project_key || "—";
      tokensByProject[key] = (tokensByProject[key] || 0) + Number(r.input_tokens || 0) + Number(r.output_tokens || 0);
    });
  }
  const cpm = (cost: number, tokens: number) => (tokens > 0 ? (cost / tokens) * 1e6 : null);
  const cpmTotal = cpm(ptuMonthlyCost, totalTokens);
  const cpmByProject = (project: string) => {
    const p = projects[project] || {};
    const cost = project === "Vivo Mais" ? Number(p.reservation_cost || 0) : Number(p.ondemand_cost || 0);
    return cpm(cost, tokensByProject[project] || 0);
  };
  const byProjectCurrent = (project: string) => projects[project] || {};

  return (
    <Stack gap={2.5}>
      <Alert severity="info" sx={{ mb: 0.5 }}>
        <b>Fonte:</b> base consolidada FOCUS <code>{costs.table}</code> (+ planejamento) · mês de referência: <b>{costs.current_label}</b> · janela de faturamento intersectada: {costs.window_label}.
        A capacidade PTU é faturada como <b>reserva</b> (<i>Microsoft.Capacity</i>) e <b>on-demand</b> (<i>Provisioned Throughput Units · 1/Month</i>), classificadas no <b>organograma_nivel_5</b>. Valores em BRL (realizado; previsto quando marcado).
        As <b>unidades faturadas</b> (reserva + on-demand) <b>não são a capacidade provisionada física</b> — o inventário Azure mostra os PTUs realmente configurados nos deployments ({provisioned?.total_ptus != null ? `${n(provisioned.total_ptus)} PTUs em ${n(provisioned.deployment_count)} deployments` : "ver KPIs operacionais"}).
      </Alert>
      {notes.map((note: string, i: number) => <Alert key={i} severity="warning" sx={{ mb: 0.5 }}>{note}</Alert>)}

      <Typography variant="h6" gutterBottom>Financeiro — {costs.current_label}</Typography>

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Custo PTU mensal" value={money(ptuMonthlyCost)} hint={`reserva ${money(totals.reservation_cost)}${reservationReferenced ? " (última compra)" : ""} + on-demand ${money(totals.ondemand_cost)}${ondemandEstimated ? " (previsto)" : ""}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="PTUs (reserva + on-demand)" value={`${n(totalUnits)} PTUs`} hint={`reserva ${n(totals.reservation_units)}${reservationReferenced ? " (última compra)" : ""} · on-demand ${n(totals.ondemand_units)}${ondemandEstimated ? " (previsto)" : ""}${provisioned?.total_ptus != null ? ` · provisionado: ${n(provisioned.total_ptus)} PTUs (${n(provisioned.deployment_count)} deployments)` : ""}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="On-demand vs reserva" value={deltaPct == null ? "—" : `${deltaPct >= 0 ? "+" : ""}${pct(deltaPct)}`} hint={deltaPct == null ? "sem dados de on-demand" : `on-demand ${pct(deltaPct)} sobre a reserva · reserva ${money(reservaUnitPrice)}/PTU · on-demand ${money(ondemandUnitPrice)}/PTU${ondemandEstimated ? " (on-demand previsto)" : ""}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Custo por milhão de tokens" value={cpmTotal == null ? "—" : `${money2(cpmTotal)}/mi`} hint={`PTU reserva+on-demand ${money(ptuMonthlyCost)} ÷ ${tokensLabel(totalTokens)} tokens (últimos 30 dias)`} /></Grid>
      </Grid>

      <Box>
        <Typography variant="h6" gutterBottom>Resumo por projeto — {costs.current_label}</Typography>
        <Grid container spacing={1.5}>
          {projectsList.map((project: string) => {
            const p = byProjectCurrent(project);
            const isVivo = project === "Vivo Mais";
            const cpmP = cpmByProject(project);
            return (
              <Grid item xs={12} sm={6} key={project}>
                <Card className="azure-ptu-print-card"><CardContent>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="subtitle1" sx={{ fontWeight: 700, color: PROJECT_COLORS[project] }}>{project}</Typography>
                    <Chip size="small" label={p.department || "—"} />
                  </Stack>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {isVivo
                      ? <>Reserva: <b>{n(p.reservation_units)} PTUs</b> · <b>{money(p.reservation_cost)}</b>{p.reservation_referenced ? ` (${p.reservation_reference_note})` : ""}</>
                      : <>On-demand: <b>{p.ondemand_units == null ? "não lançado" : `${n(p.ondemand_units)} PTUs · ${money(p.ondemand_cost)}${p.ondemand_estimated ? " (previsto)" : ""}`}</b></>}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    Custo/milhão de tokens: <b>{cpmP == null ? "—" : `${money2(cpmP)}/mi`}</b> · tokens (30d): {tokensLabel(tokensByProject[project] || 0)}
                  </Typography>
                </CardContent></Card>
              </Grid>
            );
          })}
        </Grid>
      </Box>
    </Stack>
  );
}
