import { useMemo, useState } from "react";
import { Box, Button, ButtonBase, Chip, Paper, Stack, Typography } from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import AnomalyEvolutionChart from "./AnomalyEvolutionChart";
import type { AnomalyCase, AnomalySeries } from "../../services/anomaliesApi";
import { dimensionValue, filterMembers, scopeBranches, scopeTimeline, type ScopeFilter } from "./anomalyInvestigationModel";

const money = (value: number | null | undefined, currency: string) => value == null ? "—" : value.toLocaleString("pt-BR", { style: "currency", currency });
const pct = (value: number) => `${value.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const day = (value: string) => new Date(`${value.slice(0, 10)}T00:00:00Z`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", timeZone: "UTC" }).replace(/\.$/, "");
const orgPath = (dimensions: Record<string, string | null>) => Array.from({ length: 7 }, (_, i) => dimensions[`organograma_nivel_${i + 1}`]).filter((value): value is string => Boolean(value));
const branchColors = ["#db525e", "#e59051", "#9b719e", "#6299b1"];

export default function AnomalyCaseScope({ kase, series, currency }: {
  kase: AnomalyCase; series: AnomalySeries; currency: string;
}) {
  const [filters, setFilters] = useState<ScopeFilter[]>([]);
  const all = series.contributor_breakdown ?? [];
  const members = filterMembers(all, filters);
  const timeline = useMemo(() => filters.length ? scopeTimeline(members) : series.items, [series, filters, members]);
  const focus = timeline.find(point => point.event_date === kase.event_date);
  const actual = focus?.observed_cloud ?? null, expected = focus?.expected_cloud ?? null;
  const delta = actual != null && expected != null ? actual - expected : null;
  const relative = delta != null && expected != null && expected > 0 ? delta / expected * 100 : null;
  const { dimension, groups } = scopeBranches(members, filters);
  const paths = (all.length ? all : [{ dimensions: kase.series_dimensions }]).map(member => orgPath(member.dimensions));
  const common = paths[0]?.filter((_, index) => paths.every(parts => parts.slice(0, index + 1).join("\0") === paths[0].slice(0, index + 1).join("\0"))) ?? [];
  const label = filters.length ? filters[filters.length - 1].value : common[common.length - 1] ?? "Escopo do caso";
  const excessDays = timeline.filter(point => point.event_date <= kase.event_date && point.above_band);
  const latest = [...timeline].reverse().find(point => point.observed_cloud != null && point.event_date <= (series.analysis_data_end ?? kase.event_date));
  const rootPoint = series.items.find(point => point.event_date === kase.event_date);
  const rootDelta = rootPoint?.delta_cloud;
  const leading = groups.find(group => group.delta != null && group.delta > 0);
  const positiveTotal = groups.reduce((sum, group) => sum + Math.max(0, group.delta ?? 0), 0);
  const share = (value: number) => positiveTotal > 0 ? Math.max(0, value) / positiveTotal * 100 : 0;
  const selectedSeries: AnomalySeries = { ...series, items: timeline,
    contributor_series: members.map(member => ({ series_id: member.series_id, dimensions: member.dimensions })),
    contributor_series_count: members.length || series.contributor_series_count };
  const safeToDrill = members.length > 0 && members.every(member => Boolean(member.items?.length));

  return <Box>
    <Paper variant="outlined" sx={{ mt: 2, p: { xs: 2, sm: 2.5 }, borderRadius: 3, borderColor: "#dce5ef", background: "linear-gradient(120deg,#fff,#f7faff)" }}>
      <Typography variant="overline" color="text.secondary">O que aconteceu · {day(kase.event_date)}</Typography>
      <Typography sx={{ mt: .5, fontSize: { xs: 25, sm: 30 }, lineHeight: 1.2, fontWeight: 800, letterSpacing: "-.03em", color: delta != null && delta > 0 ? "#c53d4b" : "#203047" }}>
        {delta == null ? "Custo sem forecast comparável" : delta > 0 ? `${money(delta, currency)} acima do forecast` : `${money(Math.abs(delta), currency)} ${delta < 0 ? "abaixo do forecast" : "de desvio"}`}
      </Typography>
      <Typography sx={{ mt: 1, fontWeight: 600, color: "#34445a" }}>{label}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>
        Previsto <strong>{money(expected, currency)}</strong> → realizado <strong>{money(actual, currency)}</strong>{relative != null && ` · ${relative > 0 ? "+" : ""}${pct(relative)}`}
      </Typography>
      {expected != null && actual != null && expected >= 0 && actual > 0 && <Box sx={{ mt: 1.5 }}>
        <Box sx={{ height: 10, borderRadius: 2, overflow: "hidden", display: "flex", bgcolor: "#e6edf6" }} aria-label="Composição do realizado entre forecast e desvio">
          <Box sx={{ width: `${Math.min(100, expected / actual * 100)}%`, bgcolor: "#4e81c5" }} />
          {delta != null && delta > 0 && <Box sx={{ flex: 1, bgcolor: "#db525e" }} />}
        </Box>
        <Stack direction="row" justifyContent="space-between" sx={{ mt: .5 }}><Typography variant="caption" color="#4e81c5">Previsto</Typography><Typography variant="caption" color="#c53d4b">Desvio positivo</Typography></Stack>
      </Box>}
      {excessDays.length > 0 && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Primeiro excesso: {day(excessDays[0].event_date)} · {excessDays.length} {excessDays.length === 1 ? "dia" : "dias"} acima da faixa até {day(kase.event_date)}</Typography>}
      {latest && latest.event_date > kase.event_date && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: .75 }}>Último dado ({day(latest.event_date)}): {money(latest.observed_cloud, currency)}{latest.above_band === true ? " · acima da faixa" : latest.above_band === false ? " · não supera o limite de alerta" : " · sem comparação disponível"}</Typography>}
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} spacing={1.5} sx={{ mt: 2, pt: 1.5, borderTop: "1px solid #e2e8f0" }}>
        <Box>
          <Typography variant="body2" fontWeight={700}>Investigue primeiro {leading ? leading.value : label}</Typography>
          <Typography variant="caption" color="text.secondary">{leading ? `${pct(share(leading.delta ?? 0))} dos aumentos · ` : ""}{kase.cause_hypothesis || "Confirme com a área se a mudança foi planejada."}</Typography>
        </Box>
        <Button variant="contained" size="small" sx={{ flexShrink: 0 }} onClick={() => document.getElementById("anomaly-investigation")?.scrollIntoView({ behavior: "smooth", block: "start" })}>Registrar investigação</Button>
      </Stack>
    </Paper>

    <Paper variant="outlined" sx={{ mt: 2, p: { xs: 2, sm: 2.5 }, borderRadius: 3 }}>
      <Typography sx={{ fontWeight: 750, fontSize: 17 }}>De onde veio o desvio?</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: .25 }}>Abra um ramo para acompanhar somente aquele projeto ou conta.</Typography>
      <Stack direction="row" gap={.75} flexWrap="wrap" alignItems="center" sx={{ my: 1.5 }}>
        <Chip size="small" label="Total do caso" color={filters.length ? "default" : "primary"} onClick={() => setFilters([])} />
        {filters.map((filter, index) => <Box key={filter.field} sx={{ display: "flex", alignItems: "center", gap: .75 }}><Typography color="text.secondary">›</Typography><Chip size="small" label={filter.value} color={index === filters.length - 1 ? "primary" : "default"} onClick={() => setFilters(filters.slice(0, index + 1))} /></Box>)}
      </Stack>
      {common.length > 0 && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>{common.join(" › ")}</Typography>}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(160px,.7fr) 2fr" }, gap: { xs: 1.5, md: 3 }, alignItems: "center" }}>
        <Box sx={{ p: 2, bgcolor: "#f1f5fb", border: "1px solid #d6e2f1", borderRadius: 2 }}>
          <Typography variant="caption" color="text.secondary">{filters.length ? "Ramo selecionado" : "Desvio total"}</Typography>
          <Typography fontWeight={750} sx={{ mt: .5, overflowWrap: "anywhere" }}>{label}</Typography>
          <Typography sx={{ mt: 1, fontSize: 24, fontWeight: 800, color: "#c53d4b" }}>{delta != null && delta > 0 ? "+" : ""}{money(delta, currency)}</Typography>
          {filters.length > 0 && rootDelta != null && rootDelta > 0 && delta != null && <Typography variant="caption" color="text.secondary">{pct(delta / rootDelta * 100)} do desvio líquido do caso</Typography>}
        </Box>
        <Box sx={{ pl: { xs: 1.5, md: 2.5 }, borderLeft: "2px solid #dce5ef", minWidth: 0 }}>
          {dimension && <Typography variant="overline" color="text.secondary">{dimension.label}</Typography>}
          {groups.map((group, index) => <ButtonBase key={group.value} disabled={!safeToDrill} onClick={() => setFilters([...filters, { field: dimension!.field, value: group.value }])} sx={{ display: "block", width: "100%", textAlign: "left", p: 1.5, my: 1, border: "1px solid #dce5ef", borderRadius: 2, position: "relative", "&:before": { content: '""', position: "absolute", width: { xs: 12, md: 22 }, height: 2, bgcolor: "#dce5ef", left: { xs: -13, md: -23 }, top: "50%" }, "&:hover": { bgcolor: "#f5f8fd", borderColor: "#8caee1" } }}>
            <Stack direction="row" justifyContent="space-between" alignItems="start" spacing={1}>
              <Typography variant="body2" fontWeight={750}>{group.value}</Typography>
              <Typography fontWeight={800} sx={{ color: group.delta != null && group.delta > 0 ? branchColors[index % branchColors.length] : "#087e6e", whiteSpace: "nowrap" }}>{group.delta != null && group.delta > 0 ? "+" : ""}{money(group.delta, currency)}</Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary">Previsto {money(group.expected, currency)} → realizado {money(group.actual, currency)}</Typography>
            <Box sx={{ height: 5, bgcolor: "#edf1f6", borderRadius: 1, mt: 1 }}><Box sx={{ width: `${share(group.delta ?? 0)}%`, height: "100%", borderRadius: 1, bgcolor: branchColors[index % branchColors.length] }} /></Box>
            <Stack direction="row" justifyContent="space-between" sx={{ mt: .5 }}><Typography variant="caption" color="text.secondary">{group.delta == null ? "Comparação indisponível" : group.delta < 0 ? "Reduz o desvio líquido" : `${pct(share(group.delta))} dos aumentos`}</Typography><ArrowForwardIcon sx={{ fontSize: 16, color: "#6685ac" }} /></Stack>
          </ButtonBase>)}
          {!groups.length && members.map(member => <Box key={member.series_id} sx={{ py: 1 }}>
            <Typography variant="body2" fontWeight={700}>{dimensionValue(member, "subaccountname")}</Typography>
            <Typography variant="body2" color="text.secondary">{member.dimensions.servicesubcategory ?? member.dimensions.servicename ?? "Serviço não informado"}</Typography>
            <Typography variant="caption" color="text.secondary">Centro de custo: {member.dimensions.centro_custo ?? "não informado"} · {member.dimensions.commitmentdiscounttype || "Modalidade não informada"}</Typography>
          </Box>)}
          {!all.length && <Typography variant="body2" color="text.secondary">A decomposição exige as séries contribuintes desta avaliação. O total continua disponível abaixo.</Typography>}
        </Box>
      </Box>
    </Paper>
    <Typography variant="subtitle2" sx={{ mt: 2.5 }}>Evolução · {filters.length ? label : "total do caso"}</Typography>
    <AnomalyEvolutionChart key={`${kase.id}-${JSON.stringify(filters)}`} series={selectedSeries} kase={kase} currency={currency} />
  </Box>;
}
