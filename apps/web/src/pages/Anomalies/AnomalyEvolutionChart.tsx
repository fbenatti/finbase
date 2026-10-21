import { dataDisplayText } from "../../utils/dataPresentation";
import { useMemo, useState } from "react";
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import Plot from "@/components/Plot";
import type { AnomalyCase, AnomalySeries, AnomalySeriesPoint } from "../../services/anomaliesApi";

const colors = { actual: "#149683", history: "#94a3b8", excess: "#ed5959", forecast: "#306ce5", band: "rgba(48,108,229,0.09)" };
const money = (value: number | null | undefined, currency: string) => value == null ? "—"
  : value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 2 });
const dateLabel = (day: string | null | undefined) => {
  if (!day) return "—";
  return new Date(`${day.slice(0, 10)}T00:00:00Z`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", timeZone: "UTC" });
};
const dayOffset = (day: string | null | undefined, offset: number) => {
  if (!day) return "";
  const value = new Date(`${day.slice(0, 10)}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + offset);
  return value.toISOString().slice(0, 10);
};

export default function AnomalyEvolutionChart({ series, kase, currency }: {
  series: AnomalySeries; kase: AnomalyCase; currency: string;
}) {
  const [historyDays, setHistoryDays] = useState(14);
  const [selectedDay, setSelectedDay] = useState(kase.event_date);
  const monthly = series.evaluation_grain === "MONTHLY_EVENT" || kase.evaluation_grain === "MONTHLY_EVENT";
  const historicalReference = series.reference?.reference_kind === "V2_BASELINE";
  const expectedLabel = historicalReference ? "Referência histórica" : "Forecast";
  const currencySymbol = new Intl.NumberFormat("pt-BR", { style: "currency", currency }).formatToParts(0).find((part) => part.type === "currency")?.value ?? currency;
  const dataEnd = series.analysis_data_end ?? series.data_cutoff ?? kase.event_date;
  const points = useMemo(() => {
    // Defesa para publicações antigas: a mesma data nunca ocupa várias barras.
    const days = new Map<string, AnomalySeriesPoint>();
    for (const point of series.items) days.set(point.event_date, {
      ...point, observed_cloud: point.event_date > dataEnd ? null : point.observed_cloud,
    });
    const start = monthly ? series.context_start ?? kase.event_date : dayOffset(kase.event_date, -historyDays);
    return [...days.values()].filter((point) => point.event_date >= start).sort((a, b) => a.event_date.localeCompare(b.event_date));
  }, [series, kase.event_date, dataEnd, historyDays, monthly]);
  const focus = points.find((point) => point.event_date === selectedDay);
  const expected = focus?.expected_cloud ?? null;
  const actual = focus?.observed_cloud ?? null;
  const delta = actual != null && expected != null ? actual - expected : null;
  const deltaPct = delta != null && expected != null && expected !== 0 ? delta / Math.abs(expected) * 100 : null;
  const comparable = points.filter((point) => point.expected_cloud != null);
  const future = comparable.filter((point) => point.event_date > dataEnd);
  const x = points.map((point) => point.event_date);
  const tooltip = points.map((point) => [
    dateLabel(point.event_date), money(point.observed_cloud, currency), money(point.expected_cloud, currency),
    money(point.delta_cloud, currency), money(point.anomalous_cost_cloud, currency), money(point.upper_cloud, currency),
    point.forecast_run_id == null ? "sem referência" : `#${point.forecast_run_id}`,
  ]);
  const positiveStack = (point: AnomalySeriesPoint) => point.observed_cloud != null && point.observed_cloud >= 0 && point.expected_cloud != null && point.expected_cloud >= 0;
  const firstExcess = points.find((point) => point.above_band && point.event_date <= dataEnd);
  const reference = series.reference;
  const warnings = series.warnings?.length ? series.warnings : series.note ? [series.note] : [];
  const canCompare = expected != null;

  return <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2.5 }, borderRadius: 3, borderColor: "#e2e8f0", mt: 1.5 }}>
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1.5} alignItems={{ sm: "center" }}>
      <Box>
        <Typography variant="h6" sx={{ fontWeight: 750, letterSpacing: "-.025em" }}>{monthly ? "Compras e forecast do mês" : `Realizado × ${expectedLabel.toLowerCase()}`}</Typography>
        <Typography variant="body2" color="text.secondary">{monthly ? "Acumulado nas datas de cobrança" : "O topo da barra é o realizado. O vermelho mostra quanto passou do valor esperado."}</Typography>
      </Box>
      {!monthly && <ToggleButtonGroup exclusive size="small" value={historyDays} onChange={(_, value: number | null) => { if (value) setHistoryDays(value); }} aria-label="Histórico antes do evento">
        <ToggleButton value={14}>14 dias antes</ToggleButton><ToggleButton value={30}>30 dias antes</ToggleButton>
      </ToggleButtonGroup>}
    </Stack>

    {warnings.map((warning) => <Alert severity="warning" key={warning} sx={{ mt: 2 }}>{dataDisplayText(warning)}</Alert>)}

    {monthly && reference && reference.coverage_kind !== "FULL_MONTH" && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
      Saldo registrado no forecast em {dateLabel(reference.data_cutoff)}: {money(reference.monthly_forecast_cloud, currency)}. A expectativa total anterior à compra não foi preservada nesta referência.
    </Typography>}
    <Box sx={{ mt: 2.5, mb: .5 }}>
      <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: ".06em" }}>
        {monthly ? "Acumulado em" : selectedDay > dataEnd ? "Projeção para" : "Comparação em"} {dateLabel(selectedDay)}
      </Typography>
      <Typography variant="body1" sx={{ fontWeight: 600, mt: .25 }}>
        {!focus ? "A data deste evento está fora do período elegível para análise." : actual == null && expected == null ? "Não há realizado nem referência comparável nesta data." : actual == null ? `${expectedLabel} de ${money(expected, currency)} · realizado ainda indisponível.`
          : !canCompare ? `Realizado de ${money(actual, currency)} · sem referência comparável nesta data.`
            : <>{money(actual, currency)} realizados para {money(expected, currency)} previstos
              {delta != null && delta > 0 && <Box component="span" sx={{ color: "#cf4141" }}> · +{money(delta, currency)}{deltaPct == null ? "" : ` (+${deltaPct.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%)`}</Box>}
              {delta != null && delta < 0 && <Box component="span" sx={{ color: "#087e6e" }}> · {money(Math.abs(delta), currency)} abaixo do forecast</Box>}
            </>}
      </Typography>
    </Box>
    {points.length > 0 ? <Box sx={{ mt: 1, width: "100%", height: 410, minHeight: 410, minWidth: 0 }} aria-label="Gráfico único de realizado e forecast com desvio empilhado">
      <Plot
        data={[
          { type: "scatter", mode: "lines", x, y: points.map((point) => point.lower_cloud), line: { width: 0 }, hoverinfo: "skip", showlegend: false, connectgaps: false },
          { type: "scatter", mode: "lines", name: "Faixa tolerada", x, y: points.map((point) => point.upper_cloud), line: { width: 0 }, fill: "tonexty", fillcolor: colors.band, hoverinfo: "skip", showlegend: false, connectgaps: false },
          { type: "bar", name: "Realizado antes do desvio", x, y: points.map((point) => point.event_date < kase.event_date && point.expected_cloud == null ? point.observed_cloud : null), marker: { color: colors.actual }, customdata: tooltip, hovertemplate: "Realizado antes do desvio: %{customdata[1]}<br>Forecast comparável: %{customdata[2]}<extra></extra>", showlegend: points.some((point) => point.event_date < kase.event_date && point.expected_cloud == null && point.observed_cloud != null) },
          { type: "bar", name: "Realizado sem comparação", x, y: points.map((point) => point.event_date >= kase.event_date && point.expected_cloud == null ? point.observed_cloud : null), marker: { color: colors.history }, customdata: tooltip, hovertemplate: "Realizado: %{customdata[1]}<br>Forecast comparável: %{customdata[2]}<extra></extra>", showlegend: points.some((point) => point.event_date >= kase.event_date && point.expected_cloud == null && point.observed_cloud != null) },
          { type: "bar", name: "Realizado até o forecast", x, y: points.map((point) => point.expected_cloud == null ? null : positiveStack(point) ? Math.min(point.observed_cloud!, point.expected_cloud!) : point.observed_cloud), marker: { color: colors.actual }, customdata: tooltip, hovertemplate: "Realizado: %{customdata[1]}<extra></extra>", showlegend: comparable.length > 0 },
          { type: "bar", name: "Desvio sobre o forecast", x, y: points.map((point) => positiveStack(point) ? Math.max(0, point.observed_cloud! - point.expected_cloud!) : null), marker: { color: colors.excess }, customdata: tooltip, hovertemplate: "Desvio: %{customdata[3]}<br>Acima da faixa: %{customdata[4]}<extra></extra>", showlegend: comparable.length > 0 },
          { type: "scatter", mode: "lines", name: expectedLabel, x, y: points.map((point) => point.expected_cloud), line: { color: colors.forecast, dash: "dash", width: 2.5 }, customdata: tooltip, hovertemplate: historicalReference ? "Referência histórica: %{customdata[2]}<extra></extra>" : "Forecast: %{customdata[2]}<br>Execução %{customdata[6]}<extra></extra>", showlegend: comparable.length > 0, connectgaps: false },
          { type: "scatter", mode: "lines", name: "Limite tolerado", x, y: points.map((point) => point.upper_cloud), line: { color: "#9aafd3", dash: "dot", width: 1 }, customdata: tooltip, hovertemplate: "Limite tolerado: %{customdata[5]}<extra></extra>", showlegend: comparable.length > 0, connectgaps: false },
        ]}
        layout={{
          autosize: true, height: 410, barmode: "relative", bargap: .25,
          margin: { l: 72, r: 18, t: 50, b: 85 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { family: "inherit", color: "#64748b", size: 11 },
          xaxis: { type: "date", range: x.length ? [`${dayOffset(x[0], -1)}T12:00:00Z`, `${x[x.length - 1]}T12:00:00Z`] : undefined, tickformat: "%d/%m", hoverformat: "%d/%m/%Y", nticks: 9, showgrid: false, zeroline: false, fixedrange: true },
          yaxis: { rangemode: "tozero", tickprefix: `${currencySymbol} `, tickformat: ",.0f", gridcolor: "#e8edf3", zerolinecolor: "#cbd5e1", fixedrange: true },
          hovermode: "x unified", separators: ",.", showlegend: true,
          legend: { orientation: "h", y: -.22, x: 0, font: { size: 11 }, traceorder: "normal" },
          shapes: [
            ...(future.length ? [{ type: "rect" as const, xref: "x" as const, yref: "paper" as const, x0: `${dataEnd}T12:00:00Z`, x1: `${dayOffset(x[x.length - 1], 1)}T00:00:00Z`, y0: 0, y1: 1, fillcolor: "rgba(48,108,229,0.045)", line: { width: 0 }, layer: "below" as const }] : []),
            { type: "line", xref: "x", yref: "paper", x0: `${dataEnd}T12:00:00Z`, x1: `${dataEnd}T12:00:00Z`, y0: 0, y1: 1, line: { color: "#94a3b8", width: 1, dash: "dot" } },
            ...(kase.event_date <= dataEnd ? [{ type: "line" as const, xref: "x" as const, yref: "paper" as const, x0: kase.event_date, x1: kase.event_date, y0: 0, y1: 1, line: { color: "#d64c4c", width: 1, dash: "dot" as const } }] : []),
          ],
          annotations: [
            ...(kase.event_date <= dataEnd ? [{ x: kase.event_date, y: 1.04, xref: "x" as const, yref: "paper" as const, text: `Avaliação · ${dateLabel(kase.event_date)}`, showarrow: false, xanchor: "right" as const, font: { color: "#c83f3f", size: 11 } }] : []),
            ...(future.length ? [{ x: future[Math.floor(future.length / 2)].event_date, y: 1.04, xref: "x" as const, yref: "paper" as const, text: "Forecast →", showarrow: false, font: { color: colors.forecast, size: 12 } }] : []),
          ],
        }}
        onClick={(event) => {
          const day = String(event.points[0]?.x ?? "").slice(0, 10);
          if (points.some((point) => point.event_date === day)) setSelectedDay(day);
        }}
        config={{ displayModeBar: false, responsive: true, locale: "pt-BR" }}
        style={{ width: "100%", height: "100%" }} useResizeHandler
      />
    </Box> : <Alert severity="info" sx={{ mt: 2 }}>Não há dados disponíveis para desenhar esta série.</Alert>}

    <Typography variant="caption" color="text.secondary">Realizado até {dateLabel(dataEnd)}{future.length > 0 && ` · Forecast até ${dateLabel(future[future.length - 1].event_date)}`} · Clique em uma data para comparar.</Typography>
    <Accordion elevation={0} disableGutters sx={{ mt: 1, "&:before": { display: "none" } }}>
    <AccordionSummary sx={{ px: 0, minHeight: 32 }}>
      <Typography variant="caption" color="text.secondary">Como esta comparação foi calculada</Typography>
    </AccordionSummary><AccordionDetails sx={{ px: 0 }}>
    <Typography variant="body2" sx={{ mb: 1 }}>Acima do limite tolerado nesta data: {money(focus?.anomalous_cost_cloud, currency)}. O vermelho compara com o forecast central; apenas a parcela acima do limite é custo anômalo.</Typography>
    <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} alignItems="center" sx={{ mt: .5 }}>
      <Chip size="small" variant="outlined" label={`Realizado até ${dateLabel(dataEnd)}`} />
      {future.length > 0 && <Chip size="small" variant="outlined" sx={{ color: colors.forecast, borderColor: "#c7d7fa" }} label={`Forecast até ${dateLabel(future[future.length - 1].event_date)}`} />}
      {firstExcess && <Chip size="small" variant="outlined" label={`Primeiro excesso na janela: ${dateLabel(firstExcess.event_date)}`} />}
      {reference?.forecast_run_id != null && <Button size="small" endIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />} href={`/projecoes/execucoes/${reference.forecast_run_id}`} target="_blank" rel="noreferrer">Forecast #{reference.forecast_run_id}</Button>}
    </Stack>
    <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: "block", lineHeight: 1.6 }}>
      {monthly ? "Barras nas datas de cobrança; acumulado reiniciado a cada mês. " : "Verde mostra o realizado anterior ao desvio como contexto histórico; esses dias podem não ter forecast comparável. Cinza indica realizado sem comparação a partir da data do desvio. "}
      {comparable.length > 0 ? "Vermelho é o desvio positivo; custo anômalo considera somente a parcela acima do limite tolerado." : "Sem referência completa, o gráfico mostra o realizado sem quantificar desvio."} Dias sem coleta ficam vazios.
      {future.length > 0 && " A área azul mostra a projeção, sem criar realizado futuro."} Clique em uma data para ver os valores.
      {(series.contributor_series_count ?? 1) > 1 && " Realizado e forecast somam o mesmo conjunto de séries do caso."}
      {reference?.data_cutoff && <> Referência congelada com corte em {dateLabel(reference.data_cutoff)}.</>}
      {historicalReference && " O desvio em andamento não é usado para recalcular a referência do episódio."}
    </Typography>
    </AccordionDetails></Accordion>
  </Paper>;
}
