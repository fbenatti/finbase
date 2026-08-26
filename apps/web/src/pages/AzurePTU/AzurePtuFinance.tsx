import {
  Alert, Box, Card, CardContent, Chip, Grid, Stack, Tooltip, Typography,
} from "@mui/material";
import { widgetHelp } from "./AzurePtuOpsReport";

/**
 * Resumo executivo financeiro do Azure PTU Ops alimentado pela Gold FOCUS
 * (dest_modelo_focus / dest_modelo_focus_planning). Compartilhado entre a
 * aba "Financeiro (Gold)" da tela e a seção financeira do relatório PDF.
 *
 * Contrato do payload (backend -> `costs` no ops-overview):
 *   costs.current.totals/projects  -> KPIs do mês de referência
 *     reservation_units/cost       -> reserva Microsoft.Capacity (100 PTUs)
 *     ondemand_units/cost          -> on-demand Provisioned Throughput Units (100 PTUs, 1/Month)
 *     ptu_usage_hours              -> horas provisionadas consumidas (1/Hour)
 *   tokenData / aiDemand           -> tokens dos últimos 30 dias (custo/milhão)
 */

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const brl2 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 });
const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const fmtCompact = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const n = (v: any) => (v == null ? "—" : fmt.format(Number(v)));
const money = (v: any) => (v == null || isNaN(Number(v)) ? "—" : brl.format(Number(v)));
const money2 = (v: any) => (v == null || isNaN(Number(v)) ? "—" : brl2.format(Number(v)));
const pct = (v: any) => (v == null || isNaN(Number(v)) ? "—" : `${fmt.format(Number(v))}%`);

/** Formata volumes grandes em unidades compactas: 1 Mi, 1 Bi, 1 Tr (mín. 2 casas). */
const tokensLabel = (v: any) => {
  const value = Number(v || 0);
  if (!isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${fmtCompact.format(value / 1e12)} Tr`;
  if (abs >= 1e9) return `${fmtCompact.format(value / 1e9)} Bi`;
  if (abs >= 1e6) return `${fmtCompact.format(value / 1e6)} Mi`;
  return fmtCompact.format(value);
};

const PROJECT_COLORS: Record<string, string> = {
  "Vivo Mais": "#2563EB",
  "Aura Brain": "#7C3AED",
};
const PROJECT_KEY_LABEL: Record<string, string> = {
  "vivo-mais": "Vivo Mais",
  "aura-brain": "Aura Brain",
};

function Kpi({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  const help = widgetHelp[label] || "Indicador financeiro calculado a partir da Gold FOCUS no mês de referência.";
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

export function PtuFinanceViews({ costs, tokenData, aiDemand }: { costs: any; tokenData?: any; aiDemand?: any }) {
  if (!costs || costs.available === false) {
    return <Alert severity="warning">Enriquecimento financeiro (Gold) indisponível{costs?.error ? `: ${costs.error}` : ""}. A aba operacional continua completa; os custos PTU não foram carregados.</Alert>;
  }
  if (costs.empty) {
    return <Alert severity="info">A Gold não retornou linhas para os períodos consultados. Verifique se a materialização do FOCUS está atualizada.</Alert>;
  }

  const current = costs.current || {};
  const totals = current.totals || {};
  const projects = current.projects || {};
  const projectsList: string[] = (costs.projects && costs.projects.length ? costs.projects : Object.keys(projects));
  const notes = costs.notes || [];

  const ondemandEstimated = totals.ondemand_estimated === true;
  const ptuMonthlyCost = (Number(totals.reservation_cost) || 0) + (Number(totals.ondemand_cost) || 0);
  const totalUnits = (Number(totals.reservation_units) || 0) + (Number(totals.ondemand_units) || 0);
  const reservaUnitPrice = totals.reservation_units ? (Number(totals.reservation_cost) / Number(totals.reservation_units)) : null;
  const ondemandUnitPrice = totals.ondemand_units ? (Number(totals.ondemand_cost) / Number(totals.ondemand_units)) : null;
  const deltaPct = (reservaUnitPrice != null && ondemandUnitPrice != null)
    ? ((ondemandUnitPrice - reservaUnitPrice) / reservaUnitPrice) * 100
    : null;

  // ── Tokens dos últimos 30 dias e custo por milhão de tokens ─────────────
  const seriesTokens = (tokenData?.series || []).reduce((s: number, r: any) => s + Number(r.total_tokens || 0), 0);
  const totalTokens = Number(aiDemand?.total_tokens || seriesTokens || 0);
  const tokensByProject: Record<string, number> = {};
  (tokenData?.deployment_rankings || []).forEach((r: any) => {
    const key = PROJECT_KEY_LABEL[r.project_key] || r.project_key || "—";
    tokensByProject[key] = (tokensByProject[key] || 0) + Number(r.input_tokens || 0) + Number(r.output_tokens || 0);
  });
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
        <b>Fonte:</b> Gold FOCUS <code>{costs.table}</code> (+ planejamento) · mês de referência: <b>{costs.current_label}</b> · janela de faturamento intersectada: {costs.window_label}.
        A capacidade PTU é separada em <b>reserva</b> (<i>Microsoft.Capacity</i>, 100 PTUs — Vivo Mais) e <b>on-demand</b> (<i>Provisioned Throughput Units · 1/Month</i>, 100 PTUs — Aura Brain), classificadas no <b>departamento_nivel_5</b>. Valores em BRL (realizado).
      </Alert>
      {notes.map((note: string, i: number) => <Alert key={i} severity="warning" sx={{ mb: 0.5 }}>{note}</Alert>)}

      <Typography variant="h6" gutterBottom>Resumo executivo — {costs.current_label}</Typography>

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Custo PTU mensal" value={money(ptuMonthlyCost)} hint={`reserva ${money(totals.reservation_cost)} + on-demand ${money(totals.ondemand_cost)}${ondemandEstimated ? " (previsto)" : ""}`} /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="PTUs (reserva + on-demand)" value={`${n(totalUnits)} PTUs`} hint={`reserva ${n(totals.reservation_units)} · on-demand ${n(totals.ondemand_units)}${ondemandEstimated ? " (previsto)" : ""}`} /></Grid>
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
                      ? <>Reserva: <b>{n(p.reservation_units)} PTUs</b> · <b>{money(p.reservation_cost)}</b></>
                      : <>On-demand: <b>{p.ondemand_units == null ? "não lançado" : `${n(p.ondemand_units)} PTUs · ${money(p.ondemand_cost)}${p.ondemand_estimated ? " (previsto)" : ""}`}</b></>}
                    {p.ptu_usage_hours ? <> · Uso da capacidade: {n(p.ptu_usage_hours)} h</> : null}
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
