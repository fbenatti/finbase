import { Box, Button, Paper, Stack, Typography } from "@mui/material";
import type { AnomalyCase, AnomalySeries } from "../../services/anomaliesApi";

const money = (value: number | null, currency: string) => value == null ? "—" : value.toLocaleString("pt-BR", { style: "currency", currency });
const path = (dimensions: Record<string, string | null>) => Array.from({ length: 7 }, (_, i) => dimensions[`organograma_nivel_${i + 1}`]).filter((value): value is string => Boolean(value));

export default function AnomalyCaseScope({ kase, series, currency, breakdown = false }: {
  kase: AnomalyCase; series: AnomalySeries; currency: string; breakdown?: boolean;
}) {
  const members = series.contributor_series ?? [{ series_id: kase.series_id, dimensions: kase.series_dimensions }];
  const paths = members.map(member => path(member.dimensions));
  const common = paths[0].filter((value, index) => paths.every(parts => parts.slice(0, index + 1).join("\0") === paths[0].slice(0, index + 1).join("\0")));
  const rows = [...(series.contributor_breakdown ?? [])].sort((a, b) => (b.delta_cloud ?? -Infinity) - (a.delta_cloud ?? -Infinity));
  const lead = rows.find(row => row.delta_cloud != null && row.delta_cloud > 0);
  if (!breakdown) return <Box sx={{ my: 2 }}>
    <Typography variant="overline" color="text.secondary">Área afetada</Typography>
    <Typography sx={{ fontWeight: 700, fontSize: 17 }}>{common.at(-1) ?? "Organograma não informado"}</Typography>
    {common.length > 1 && <Typography variant="caption" color="text.secondary">{common.slice(0, -1).join(" › ")}</Typography>}
    <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }} justifyContent="space-between" sx={{ mt: 1.5, p: 1.5, bgcolor: "#f1f6fc", borderRadius: 2 }}>
      <Box>
        <Typography variant="body2" fontWeight={700}>{lead ? `Maior desvio: ${lead.dimensions.subaccountname ?? "Conta não informada"} · +${money(lead.delta_cloud, currency)}` : "Identifique a origem e confirme se o aumento era esperado"}</Typography>
        <Typography variant="body2" color="text.secondary">{lead ? path(lead.dimensions).at(-1) ?? "Organograma não informado" : "Consulte o consumo e a referência de forecast abaixo."}</Typography>
      </Box>
      <Button variant="contained" size="small" onClick={() => document.getElementById("anomaly-investigation")?.scrollIntoView({ behavior: "smooth", block: "start" })}>Tratar caso</Button>
    </Stack>
    <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: .75 }}>Responsável: {kase.assignee_user_id ? `usuário ${kase.assignee_user_id}` : "fila FinOps"}{kase.due_at && ` · Prazo de ciência: ${new Date(kase.due_at).toLocaleString("pt-BR")}`}</Typography>
  </Box>;

  return <Paper variant="outlined" sx={{ mt: 2, p: 2, borderRadius: 3 }}>
    <Typography fontWeight={700}>Onde está o desvio?</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>Por conta e caminho organizacional, na data da avaliação. Priorize os maiores desvios positivos.</Typography>
    {(rows.length ? rows : members.map(member => ({ ...member, observed_cloud: null, expected_cloud: null, delta_cloud: null }))).map(row => <Box key={row.series_id} sx={{ py: 1.5, borderTop: "1px solid #e8edf3" }}>
      <Stack direction="row" justifyContent="space-between" spacing={2}>
        <Box>
          <Typography variant="body2" fontWeight={700}>{row.dimensions.subaccountname ?? "Conta não informada"} · {path(row.dimensions).at(-1) ?? "Organograma não informado"}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>{path(row.dimensions).slice(0, -1).join(" › ") || "Sem caminho organizacional"}</Typography>
          <Typography variant="caption" color="text.secondary">Centro de custo: {row.dimensions.centro_custo ?? "não informado"}</Typography>
        </Box>
        <Typography sx={{ fontWeight: 750, color: (row.delta_cloud ?? 0) > 0 ? "#cf4141" : "text.primary", whiteSpace: "nowrap" }}>{row.delta_cloud != null && row.delta_cloud > 0 ? "+" : ""}{money(row.delta_cloud, currency)}</Typography>
      </Stack>
      <Typography variant="body2" sx={{ mt: .75 }}>Previsto {money(row.expected_cloud, currency)} → realizado {money(row.observed_cloud, currency)}</Typography>
    </Box>)}
    <Typography variant="caption" color="text.secondary">O organograma localiza o gasto; não comprova a causa do aumento nem identifica um recurso individual.</Typography>
  </Paper>;
}
