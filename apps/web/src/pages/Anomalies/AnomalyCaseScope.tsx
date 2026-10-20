import { useMemo, useState } from "react";
import { Alert, Box, Button, ButtonBase, Chip, Paper, Stack, Typography } from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import AnomalyEvolutionChart from "./AnomalyEvolutionChart";
import type { AnomalyCase, AnomalySeries } from "../../services/anomaliesApi";
import { caseLineageNodes, detailScopeChain, dimensionValue, filterMembers, observedScopeBranches, scopeBranches, scopeTimeline, type ScopeFilter } from "./anomalyInvestigationModel";

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
  const memberFields = new Set(all.flatMap(member => Object.keys(member.dimensions)));
  const forecastFilters = filters.filter(filter => memberFields.has(filter.field));
  const members = filterMembers(all, forecastFilters);
  const timeline = useMemo(() => filters.length ? scopeTimeline(members) : series.items, [series, filters, members]);
  const focus = timeline.find(point => point.event_date === kase.event_date);
  const monthlyCase = kase.detector === "MONTHLY_ACCUMULATED" || kase.event_type === "MONTHLY_ACCUMULATED";
  const actual = monthlyCase ? kase.observed_cloud : focus?.observed_cloud ?? null;
  const expected = monthlyCase ? kase.expected_cloud : focus?.expected_cloud ?? null;
  const delta = monthlyCase ? kase.delta_cloud : actual != null && expected != null ? actual - expected : null;
  const withinBand = !monthlyCase && focus?.above_band === false && delta != null;
  const headline = delta == null
    ? "Custo sem comparação"
    : withinBand
      ? `${money(Math.abs(delta), currency)} em relação à mediana, dentro da faixa`
      : delta > 0
        ? `${money(delta, currency)} ${monthlyCase ? "acima da média dos meses anteriores" : "acima da faixa"}`
        : `${money(Math.abs(delta), currency)} ${delta < 0 ? "abaixo da referência" : "de desvio"}`;
  const relative = delta != null && expected != null && expected > 0 ? delta / expected * 100 : null;
  const hierarchy = detailScopeChain(series.detail_dimensions);
  const detailItems = series.detail_items ?? [];
  const forecastBranches = scopeBranches(members, forecastFilters, hierarchy);
  const sourceBranches = observedScopeBranches(detailItems, filters, hierarchy);
  const sourceOnly = forecastBranches.groups.length === 0 && sourceBranches.groups.length > 0;
  const dimension = forecastBranches.groups.length ? forecastBranches.dimension : sourceBranches.dimension;
  const groups = forecastBranches.groups.length
    ? forecastBranches.groups.map(group => ({ ...group, sourceOnly: false }))
    : sourceBranches.groups.map(group => ({ ...group, delta: null, expected: null, sourceOnly: true }));
  const paths = (all.length ? all : [{ dimensions: kase.series_dimensions }]).map(member => orgPath(member.dimensions));
  const common = paths[0]?.filter((_, index) => paths.every(parts => parts.slice(0, index + 1).join("\0") === paths[0].slice(0, index + 1).join("\0"))) ?? [];
  const lineageNodes = caseLineageNodes(members, series.detail_dimensions, series.detail_items, hierarchy);
  const label = filters.length ? filters[filters.length - 1].value : common[common.length - 1] ?? "Escopo do caso";
  const excessDays = timeline.filter(point => point.event_date <= kase.event_date && point.above_band);
  const latest = [...timeline].reverse().find(point => point.observed_cloud != null && point.event_date <= (series.analysis_data_end ?? kase.event_date));
  const rootPoint = series.items.find(point => point.event_date === kase.event_date);
  const rootDelta = rootPoint?.delta_cloud;
  const leading = groups.find(group => group.delta != null && group.delta > 0);
  const positiveTotal = groups.reduce((sum, group) => sum + Math.max(0, group.delta ?? 0), 0);
  const share = (value: number) => positiveTotal > 0 ? Math.max(0, value) / positiveTotal * 100 : 0;
  const forecastScopeLabel = forecastFilters.length ? forecastFilters[forecastFilters.length - 1].value : "total do caso";
  const rawDetailSelected = filters.some(filter => !memberFields.has(filter.field));
  const selectedDetailItems = (series.detail_items ?? []).filter(item => filters.every(filter => dimensionValue({ dimensions: item.dimensions } as typeof all[number], filter.field) === filter.value));
  const selectedSeries: AnomalySeries = { ...series, items: timeline,
    contributor_series: members.map(member => ({ series_id: member.series_id, dimensions: member.dimensions })),
    contributor_series_count: members.length || series.contributor_series_count };
  const safeToDrill = members.length > 0 && members.every(member => Boolean(member.items?.length));

  return <Box>
    <Paper variant="outlined" sx={{ mt: 2, p: { xs: 2, sm: 2.5 }, borderRadius: 3, borderColor: "#dce5ef", background: "linear-gradient(120deg,#fff,#f7faff)" }}>
      <Typography variant="overline" color="text.secondary">{monthlyCase ? "Acumulado do mês até" : "O que aconteceu"} · {day(kase.event_date)}</Typography>
      <Typography sx={{ mt: .5, fontSize: { xs: 25, sm: 30 }, lineHeight: 1.2, fontWeight: 800, letterSpacing: "-.03em", color: delta != null && delta > 0 && !withinBand ? "#c53d4b" : "#203047" }}>
        {headline}
      </Typography>
      <Typography sx={{ mt: 1, fontWeight: 600, color: "#34445a" }}>{label}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>
        {monthlyCase ? "Média dos meses anteriores" : "Mediana recente"} <strong>{money(expected, currency)}</strong> → realizado <strong>{money(actual, currency)}</strong>{relative != null && ` · ${relative > 0 ? "+" : ""}${pct(relative)}`}
      </Typography>
      {expected != null && actual != null && expected >= 0 && actual > 0 && delta != null && delta > 1 && !withinBand && <Box sx={{ mt: 1.5 }}>
        <Box sx={{ height: 10, borderRadius: 2, overflow: "hidden", display: "flex", bgcolor: "#e6edf6" }} aria-label="Composição do realizado entre forecast e desvio">
          <Box sx={{ width: `${Math.min(100, expected / actual * 100)}%`, bgcolor: "#4e81c5" }} />
          {delta != null && delta > 0 && <Box sx={{ flex: 1, bgcolor: "#db525e" }} />}
        </Box>
        <Stack direction="row" justifyContent="space-between" sx={{ mt: .5 }}><Typography variant="caption" color="#4e81c5">Previsto</Typography><Typography variant="caption" color="#c53d4b">Desvio positivo</Typography></Stack>
      </Box>}
      {!monthlyCase && focus?.above_band && excessDays.length > 0 && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Primeiro excesso: {day(excessDays[0].event_date)} · {excessDays.length} {excessDays.length === 1 ? "dia" : "dias"} acima da faixa até {day(kase.event_date)}</Typography>}
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
      <Typography variant="body2" color="text.secondary" sx={{ mt: .25 }}>Localize o salto pelo organograma, subconta, grupo de recursos e produto.</Typography>
      <Stack direction="row" gap={.75} flexWrap="wrap" alignItems="center" sx={{ my: 1.5 }}>
        <Chip size="small" label="Total do caso" color={filters.length ? "default" : "primary"} onClick={() => setFilters([])} />
        {filters.map((filter, index) => <Box key={filter.field} sx={{ display: "flex", alignItems: "center", gap: .75 }}><Typography color="text.secondary">›</Typography><Chip size="small" label={`${hierarchy.find(item => item.field === filter.field)?.label ?? filter.field}: ${filter.value}`} color={index === filters.length - 1 ? "primary" : "default"} onClick={() => setFilters(filters.slice(0, index + 1))} /></Box>)}
      </Stack>
      {lineageNodes.length > 0 && <Box sx={{ mb: 1.5 }}>
        {/** IDs técnicos e dimensões de baixo valor foram retirados do caminho em detailScopeChain. */}
        <Stack direction="row" justifyContent="space-between" alignItems="center" gap={1}>
          <Typography variant="caption" fontWeight={700} color="text.secondary">CAMINHO DO GASTO</Typography>
          <Chip size="small" variant="outlined" label={filters.length ? `Forecast no nível: ${forecastScopeLabel}` : "Forecast e desvio neste ramo"} sx={{ maxWidth: "70%", "& .MuiChip-label": { overflow: "hidden", textOverflow: "ellipsis" } }} />
        </Stack>
        <Box sx={{ mt: 1, pl: 1.5, position: "relative", "&:before": { content: '""', position: "absolute", left: 5, top: 10, bottom: 10, width: 2, bgcolor: "#dce5ef" } }}>
          {lineageNodes.map((node, index) => {
            const terminal = index === lineageNodes.length - 1;
            return <Stack key={node.field} direction="row" alignItems="stretch" gap={1.25} sx={{ position: "relative", mb: index === lineageNodes.length - 1 ? 0 : .75 }}>
              <Box sx={{ zIndex: 1, flex: "0 0 10px", width: 10, height: 10, mt: 1.5, ml: -.25, borderRadius: "50%", border: "2px solid", borderColor: terminal ? "#536fb2" : "#aab8ca", bgcolor: terminal ? "#536fb2" : "#fff" }} />
              <Box sx={{ minWidth: 0, flex: 1, display: "flex", flexWrap: "wrap", alignItems: "baseline", columnGap: 1, rowGap: .25, px: 1.25, py: .8, border: "1px solid", borderColor: terminal ? "#cbd5f0" : "#e4eaf1", borderRadius: 1.5, bgcolor: terminal ? "#f2f4fc" : "#fff" }}>
                <Typography variant="caption" color="text.secondary" sx={{ minWidth: { xs: 96, sm: 128 } }}>{node.label}</Typography>
                <Typography variant="body2" fontWeight={terminal ? 750 : 600} sx={{ minWidth: 0, flex: 1, overflowWrap: "anywhere", color: terminal ? "#344878" : "#26364a" }}>{node.value}</Typography>
                {terminal && <Chip size="small" color="primary" variant="outlined" label="grão avaliado" sx={{ height: 21 }} />}
              </Box>
            </Stack>;
          })}
        </Box>
        {!groups.length && !rawDetailSelected && <Typography variant="caption" color="text.secondary" sx={{ display: "block", ml: 3, mt: .75 }}>A comparação do desvio termina no ramo destacado; o detalhamento abaixo representa custo observado e não divide o forecast por SKU.</Typography>}
      </Box>}
      {detailItems.length > 0 && actual != null && (() => {
        const detailTotal = detailItems.reduce((sum, item) => sum + (item.observed_cloud ?? 0), 0);
        const coverage = Math.abs(actual) > 0 ? Math.min(100, Math.abs(detailTotal / actual) * 100) : 100;
        return <Alert severity={coverage >= 99 ? "info" : "warning"} sx={{ mb: 1.5, py: 0 }}>
          Detalhamento disponível: <strong>{money(detailTotal, currency)}</strong> de <strong>{money(actual, currency)}</strong> observados ({pct(coverage)}).{coverage < 99 && " O restante ainda não está atribuído a recursos; os valores abaixo não representam o total do caso."}
        </Alert>;
      })()}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(160px,.7fr) 2fr" }, gap: { xs: 1.5, md: 3 }, alignItems: "center" }}>
        <Box sx={{ p: 2, bgcolor: "#f1f5fb", border: "1px solid #d6e2f1", borderRadius: 2 }}>
          <Typography variant="caption" color="text.secondary">{filters.length ? "Ramo selecionado" : "Desvio total"}</Typography>
          <Typography fontWeight={750} sx={{ mt: .5, overflowWrap: "anywhere" }}>{label}</Typography>
          <Typography sx={{ mt: 1, fontSize: 24, fontWeight: 800, color: "#c53d4b" }}>{delta != null && delta > 0 ? "+" : ""}{money(delta, currency)}</Typography>
          {rawDetailSelected && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: .5 }}>Desvio calculado em {forecastScopeLabel}; detalhe abaixo é custo observado no dia do evento.</Typography>}
          {filters.length > 0 && rootDelta != null && rootDelta > 0 && delta != null && <Typography variant="caption" color="text.secondary">{pct(delta / rootDelta * 100)} do desvio líquido do caso</Typography>}
        </Box>
        <Box sx={{ pl: { xs: 1.5, md: 2.5 }, borderLeft: "2px solid #dce5ef", minWidth: 0 }}>
          {dimension && <Typography variant="overline" color="text.secondary">{dimension.label}</Typography>}
          {groups.map((group, index) => <ButtonBase key={group.value} disabled={sourceOnly ? !(series.detail_items?.length) : !safeToDrill} onClick={() => setFilters([...filters, { field: dimension!.field, value: group.value }])} sx={{ display: "block", width: "100%", textAlign: "left", p: 1.5, my: 1, border: "1px solid #dce5ef", borderRadius: 2, position: "relative", "&:before": { content: '""', position: "absolute", width: { xs: 12, md: 22 }, height: 2, bgcolor: "#dce5ef", left: { xs: -13, md: -23 }, top: "50%" }, "&:hover": { bgcolor: "#f5f8fd", borderColor: "#8caee1" } }}>
            <Stack direction="row" justifyContent="space-between" alignItems="start" spacing={1}>
              <Typography variant="body2" fontWeight={750}>{group.value}</Typography>
              <Typography fontWeight={800} sx={{ color: group.sourceOnly ? "#315e91" : group.delta != null && group.delta > 0 ? branchColors[index % branchColors.length] : "#087e6e", whiteSpace: "nowrap" }}>{group.sourceOnly ? money(group.actual, currency) : <>{group.delta != null && group.delta > 0 ? "+" : ""}{money(group.delta, currency)}</>}</Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary">{group.sourceOnly ? "Custo observado na data do evento" : <>Previsto {money(group.expected, currency)} → realizado {money(group.actual, currency)}</>}</Typography>
            <Box sx={{ height: 5, bgcolor: "#edf1f6", borderRadius: 1, mt: 1 }}><Box sx={{ width: `${group.sourceOnly ? Math.min(100, Math.abs(group.actual ?? 0) / Math.max(1, Math.abs(actual ?? 0)) * 100) : share(group.delta ?? 0)}%`, height: "100%", borderRadius: 1, bgcolor: branchColors[index % branchColors.length] }} /></Box>
            <Stack direction="row" justifyContent="space-between" sx={{ mt: .5 }}><Typography variant="caption" color="text.secondary">{group.sourceOnly ? "Forecast não desce a este nível" : group.delta == null ? "Comparação indisponível" : group.delta < 0 ? "Reduz o desvio líquido" : `${pct(share(group.delta))} dos aumentos`}</Typography><ArrowForwardIcon sx={{ fontSize: 16, color: "#6685ac" }} /></Stack>
          </ButtonBase>)}
          {!groups.length && rawDetailSelected && selectedDetailItems.length > 0 && <Stack spacing={.75} sx={{ mt: 1 }}>
            {selectedDetailItems.slice(0, 20).map((item, index) => {
              const namedDetails = (series.detail_dimensions ?? []).map(dimension => ({ label: dimension.label, value: item.dimensions[dimension.key] })).filter(detail => Boolean(detail.value));
              const title = namedDetails.find(detail => /^recurso$/i.test(detail.label))?.value
                ?? namedDetails.find(detail => /sku|medidor|serviço/i.test(detail.label))?.value
                ?? namedDetails.find(detail => /resource group/i.test(detail.label))?.value
                ?? namedDetails[0]?.value ?? "Consumo sem dimensão descritiva";
              const supportingDetails = namedDetails.map(detail => {
                if (detail.label === "ID do recurso") {
                  const parts = String(detail.value).split("/").filter(Boolean);
                  return `ID: ${parts[parts.length - 1] ?? detail.value}`;
                }
                return `${detail.label}: ${detail.value}`;
              }).join(" · ");
              return <Box key={`${index}-${String(title)}`} sx={{ display: "flex", justifyContent: "space-between", gap: 2, py: 1, borderBottom: "1px solid #edf1f6" }}>
              <Box sx={{ minWidth: 0 }}><Typography variant="body2" fontWeight={700} sx={{ overflowWrap: "anywhere" }}>{title}</Typography><Typography variant="caption" color="text.secondary">{supportingDetails}</Typography></Box>
              <Typography variant="body2" fontWeight={750} sx={{ whiteSpace: "nowrap" }}>{money(item.observed_cloud, currency)}</Typography>
            </Box>;
            })}
            {selectedDetailItems.length > 20 && <Typography variant="caption" color="text.secondary">Exibindo os 20 maiores detalhes de {selectedDetailItems.length} itens.</Typography>}
          </Stack>}
          {!groups.length && !rawDetailSelected && series.detail_dimensions?.length === 0 && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, p: 1, bgcolor: "#f7f9fc", borderRadius: 1 }}>{monthlyCase ? "A comparação é o total do mês deste escopo. Não há abertura por resource group ou SKU nesta avaliação." : "A origem desta avaliação não disponibilizou dimensões abaixo do escopo do caso."}</Typography>}
          {!all.length && !monthlyCase && <Typography variant="body2" color="text.secondary">A decomposição exige as séries contribuintes desta avaliação. O total continua disponível abaixo.</Typography>}
        </Box>
      </Box>
    </Paper>
    {rawDetailSelected && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.5 }}>O custo detalhado é observado na data do evento. Forecast e linha do tempo continuam no nível {forecastScopeLabel}, a granularidade que a previsão sustenta.</Typography>}
    <Typography variant="subtitle2" sx={{ mt: 2.5 }}>Evolução · {forecastScopeLabel}</Typography>
    <AnomalyEvolutionChart key={`${kase.id}-${JSON.stringify(filters)}`} series={selectedSeries} kase={kase} currency={currency} />
  </Box>;
}
