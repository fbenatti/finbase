import Plot from "@/components/Plot";
import {
  Alert, Box, Card, CardContent, Chip, Grid, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { PtuFinanceViews } from "./AzurePtuFinance";
import { tokensLabel } from "./AzurePtuFormat";

/** Descrições ("dados de info") de cada indicador — usadas nos tooltips da tela e no relatório PDF. */
export const widgetHelp: Record<string, string> = {
  "PTUs provisionados": "Quantidade de Provisioned Throughput Units atribuídas aos deployments no inventário Azure. É capacidade configurada, não volume de tokens.",
  "Utilização média ponderada": "Média da utilização Azure Monitor ponderada pela quantidade de PTUs de cada deployment. 100% representa o limite provisionado; valores acima de 100% são tratados como anomalia de medição/agregação.",
  "Capacidade não utilizada": "Percentual complementar da utilização média: 100% menos a utilização válida observada. Não é economia financeira.",
  "Risco operacional": "Classificação baseada em horas observadas acima de 80% e 95%, utilização média e eventos de qualidade. É um sinal operacional, não uma previsão de incidente.",
  "Tokens totais": "Soma de TokenTransaction no período filtrado. Quando essa métrica não existe, é calculada como input + output.",
  "Input tokens": "Tokens de entrada/processados no prompt. Uma participação alta normalmente indica contexto ou prompts grandes em relação ao texto gerado.",
  "Output tokens": "Tokens gerados pelo modelo na resposta.",
  "Tokens por request": "Tokens totais divididos pelo número de requests observados. Indisponível quando o Azure não retorna contagem de requests compatível.",
  "Taxa de 429": "Percentual de requests que receberam HTTP 429 (rate limit/throttling). Zero significa que nenhum 429 foi observado no recorte, não que nunca possa ocorrer.",
  "Disponibilidade": "Percentual estimado de disponibilidade das chamadas Azure OpenAI. 97,1% significa que cerca de 2,9% não foram consideradas disponíveis pela métrica Azure; não é sinônimo de taxa de 429.",
  "TTFT médio": "Time to First Token: tempo até o primeiro token da resposta. Mede a espera inicial percebida pelo usuário.",
  "TBT médio": "Time Between Tokens: intervalo normalizado entre tokens durante a geração. Mede a cadência de streaming após o primeiro token.",
  "Horas ≥80%": "Quantidade de horas em que o pico válido de utilização atingiu pelo menos 80%. É uma contagem observada no período filtrado.",
  "Horas ≥95%": "Quantidade de horas em que o pico válido atingiu pelo menos 95%. É evidência de proximidade do limite, não confirmação de indisponibilidade ou custo adicional.",
  "Pico observado": "Maior utilização válida encontrada nas agregações horárias do período. Não é P95 bruto; o base consolidada histórico não contém toda a distribuição de pontos de minuto.",
  "Cache match": "Percentual de tokens de contexto atendidos pelo cache, quando o deployment expõe essa métrica. Maior valor pode indicar reaproveitamento de contexto.",
  "Custo PTU mensal": "Soma do custo da reserva de capacidade (Microsoft.Capacity) e da PTU on-demand (Provisioned Throughput Units · 1/Month), no mês de referência da base consolidada FOCUS. Quando o on-demand do mês corrente ainda não foi lançado, usa o forecast (ou o último realizado) marcado como previsto.",
  "PTUs (reserva + on-demand)": "Unidades FATURADAS de capacidade PTU no mês de referência: reserva (Microsoft.Capacity · Purchase) + on-demand (Provisioned Throughput Units · 1/Month). O on-demand só é contabilizado para projetos com linha realizada na base consolidada (ex.: Aura Brain 100 PTUs/mês); quando o mês corrente ainda não foi lançado, usa o forecast marcado como previsto. Projetos cobertos pela reserva (ex.: Vivo Mais) não recebem on-demand fabricado. ATENÇÃO: não é a capacidade física — o inventário Azure (dest_azure_ptu_capacity) mostra os PTUs realmente configurados nos deployments (KPIs operacionais).",
  "On-demand vs reserva": "Diferença percentual entre o custo por PTU on-demand e o custo por PTU da reserva no mês de referência. O delta esperado entre reserva e on-demand é de no máximo ~20%. Quando o on-demand do mês corrente não foi lançado, usa o forecast (ou o último realizado) marcado como previsto.",
  "Custo por milhão de tokens": "Custo da capacidade PTU (reserva + on-demand) no mês de referência dividido pelos tokens consumidos nos últimos 30 dias (Azure Monitor), multiplicado por 1 milhão. É o custo efetivo de cada milhão de tokens dado o gasto fixo mensal de capacidade.",
};

/** Escala de verde para heatmaps (portal e PDF): uso baixo = verde fraco, uso alto = verde forte. */
export const HEATMAP_GREEN_SCALE: [number, string][] = [
  [0, "#E7F7E7"],
  [0.2, "#BCE8BC"],
  [0.4, "#84D184"],
  [0.6, "#4CAF50"],
  [0.8, "#1E7A2E"],
  [1, "#0B4D14"],
];

const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const n = (v: any) => (v == null ? "—" : fmt.format(Number(v)));
const pct = (v: any) => (v == null ? "—" : `${fmt.format(Number(v))}%`);
const labels: Record<string, string> = { "vivo-mais": "Vivo Mais", "aura-brain": "Aura Brain" };
const riskColor = (r?: string) => (r === "critical" ? "error" : r === "high" ? "warning" : r === "medium" ? "info" : "success");
const riskLabel = (r?: string) => (r === "critical" ? "Saturação" : r === "high" ? "Atenção" : r === "medium" ? "Monitorar" : "Normal");
const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

export type AzurePtuOpsReportMeta = {
  periodId: string;
  periodLabel: string;
  projectLabel: string;
  grainLabel: string;
  exportedAt: string;
};

function KpiCard({ label, value, hint, color, note }: { label: string; value: React.ReactNode; hint?: string; color?: string; note?: string }) {
  return (
    <Grid item xs={12} sm={6} md={3}>
      <Card className="azure-ptu-print-card"><CardContent>
        <Typography variant="body2" color="text.secondary">{label}</Typography>
        <Typography variant="h6" sx={color ? { color } : undefined}>{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
        <Typography variant="caption" component="div" className="azure-ptu-kpi-note">ⓘ {note || widgetHelp[label] || "Indicador operacional calculado a partir das métricas Azure Monitor no período selecionado."}</Typography>
      </CardContent></Card>
    </Grid>
  );
}

function Section({ index, title, subtitle, children }: { index: string; title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <Box className="azure-ptu-ops-section">
      <Box className="azure-ptu-ops-section-head">
        <Chip size="small" color="primary" label={index} />
        <Typography variant="h5">{title}</Typography>
      </Box>
      {subtitle && <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>{subtitle}</Typography>}
      {children}
    </Box>
  );
}

function ChartCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <Card className="azure-ptu-print-card" sx={{ mb: 2 }}><CardContent>
      <Typography variant="h6" gutterBottom>{title}</Typography>
      {subtitle && <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{subtitle}</Typography>}
      {children}
    </CardContent></Card>
  );
}

export function AzurePtuOpsReport({ data, meta }: { data: any; meta: AzurePtuOpsReportMeta }) {
  const s = data.executive_summary || {}, c = s.capacity_decision || {}, d = s.ai_demand || {}, q = s.reliability_experience || {};
  const token = data.token_consumption || {}, risk = data.capacity_risk || {}, periodInfo = data.period || {}, coverage = data.data_coverage || {};
  const series = token.series || data.token_series || [], tokenHeatmap = data.token_heatmap || [], util = risk.utilization_series || data.utilization_series || [];
  const deps = risk.deployment_table || data.deployment_utilization || [];
  const total = Number(d.total_tokens || 0), input = Number(d.input_tokens || 0), output = Number(d.output_tokens || 0);
  const sourceLabel = data.data_source === "gold" ? "Base consolidada" : data.data_source === "stale" ? "Dados parciais" : "Dados parciais";
  const mixReserved = data?.kpis?.reserved_ptus ?? s?.capacity_decision?.reserved_ptus;
  const mixOnDemand = data?.kpis?.ondemand_ptus ?? s?.capacity_decision?.ondemand_ptus;

  const metric = (field: string, value: any, suffix = "") =>
    data.metric_availability?.[field]?.available === false ? "Não coletada" : value == null ? "—" : `${suffix === " ms" ? fmt.format(Number(value)) : n(value)}${suffix}`;

  const layout = (title: string, suffix = "") => ({
    autosize: true, margin: { l: 58, r: 20, t: 25, b: 55 }, hovermode: "x unified",
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    xaxis: { title: { text: "Período" } }, yaxis: { title: { text: title }, rangemode: "tozero", ticksuffix: suffix },
    legend: { orientation: "h", y: -0.2 },
  } as any);

  const plot = (data: any[], l: any, height: number, key: string) => (
    <Plot data={data} layout={l} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%", height }} useResizeHandler key={key} />
  );

  const tokenConsumptionChart = plot([
    { x: series.map((r: any) => r.timestamp), y: series.map((r: any) => r.input_tokens), name: "Input", type: "bar" },
    { x: series.map((r: any) => r.timestamp), y: series.map((r: any) => r.output_tokens), name: "Output", type: "bar" },
    { x: series.map((r: any) => r.timestamp), y: series.map((r: any) => r.total_tokens), name: "Total", type: "scatter", mode: "lines", line: { color: "#7C3AED" } },
  ], { ...layout("Tokens"), barmode: "stack" }, 300, "token-consumption");

  const utilizationChart = plot([
    { x: util.map((r: any) => r.timestamp), y: util.map((r: any) => r.utilization_avg), name: "Média ponderada", type: "scatter", mode: "lines", line: { color: "#2563EB" } },
    { x: util.map((r: any) => r.timestamp), y: util.map((r: any) => r.utilization_max), name: "Pico observado", type: "scatter", mode: "lines", line: { color: "#DC2626", dash: "dot" } },
  ], {
    ...layout("Utilização", "%"),
    shapes: [
      { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 80, y1: 80, line: { color: "#F59E0B", dash: "dot" } },
      { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 95, y1: 95, line: { color: "#DC2626", dash: "dash" } },
    ],
  }, 320, "utilization");

  const tokenHeatmapChart = tokenHeatmap.length > 0 ? plot([{
    z: Array.from({ length: 7 }, (_, day) => Array.from({ length: 24 }, (_, hour) => {
      const cell = tokenHeatmap.find((x: any) => x.weekday === day && x.hour === hour);
      return cell ? cell.total_tokens / Math.max(1, cell.samples) : null;
    })),
    x: Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}h`),
    y: WEEKDAYS, type: "heatmap", colorscale: HEATMAP_GREEN_SCALE as any, zmin: 0,
    colorbar: { title: { text: "tokens/h" } },
    hovertemplate: "%{y} %{x}: %{z:.0f} tokens/h<extra></extra>",
  }], { autosize: true, margin: { l: 55, r: 20, t: 20, b: 40 }, paper_bgcolor: "transparent" }, 300, "token-heatmap")
    : <Alert severity="info">Sem dados horários suficientes para o heatmap de consumo.</Alert>;

  const utilHeatmapChart = risk.heatmap_available ? plot([{
    z: risk.heatmap.map((x: any) => x.utilization_avg),
    x: risk.heatmap.map((x: any) => x.hour),
    y: risk.heatmap.map((x: any) => x.weekday),
    type: "heatmap", colorscale: HEATMAP_GREEN_SCALE as any, zmin: 0, zmax: 100,
    colorbar: { title: { text: "uso %" } },
  }], { autosize: true, margin: { l: 50, r: 20, t: 20, b: 40 }, paper_bgcolor: "transparent" }, 280, "util-heatmap") : null;

  return (
    <Box className="azure-ptu-ops-report">
      {/* ── Cabeçalho do relatório ─────────────────────────────────────── */}
      <Box className="azure-ptu-print-meta">
        <h1>Azure PTU — Relatório Operacional Completo</h1>
        <p><strong>Período:</strong> {meta.periodLabel} · <strong>Projeto:</strong> {meta.projectLabel} · <strong>Granularidade:</strong> {meta.grainLabel} · <strong>Fonte:</strong> {sourceLabel}</p>
        <p><strong>Recorte:</strong> {periodInfo.start || "—"} → {periodInfo.end || "—"} · última amostra: {periodInfo.last_sample || "—"} · cobertura: {pct(periodInfo.coverage_pct ?? coverage.coverage_pct)}{periodInfo.sample_unit ? ` (${periodInfo.sample_count}/${periodInfo.expected_count} ${periodInfo.sample_unit === "hour" ? "horas" : periodInfo.sample_unit === "day" ? "dias" : "meses"})` : ""} · {c.deployment_count ?? deps.length} deployments / {n(c.total_ptus ?? data.kpis?.total_ptus)} PTUs</p>
        {mixReserved != null && <p><strong>Composição de capacidade:</strong> Reserva: {n(mixReserved)} PTUs · OnDemand: {n(mixOnDemand)} PTUs</p>}
        <p><strong>Exportado em:</strong> {meta.exportedAt}</p>
        <p style={{ marginTop: 8, fontStyle: "italic" }}>Relatório consolidado com todos os painéis da tela Azure PTU — Operação: resumo executivo (inclui financeiro base consolidada), consumo de tokens, capacidade/operação e recomendações. Todos os valores refletem exclusivamente o período e os filtros selecionados.</p>
      </Box>

      {coverage.complete === false && <Alert severity="warning" sx={{ mb: 2 }}>Cobertura parcial: {coverage.days_with_data}/{coverage.expected_days} dias; lacunas não significam consumo zero.</Alert>}

      {/* ── 01 · Resumo executivo ─────────────────────────────────────── */}
      <Section index="01" title="Resumo executivo" subtitle="Visão consolidada de capacidade, demanda, experiência e financeiro no período selecionado.">
        <PtuFinanceViews costs={data.costs} tokenData={data.token_consumption} aiDemand={data.executive_summary?.ai_demand} />
        <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>Capacidade e decisão</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Utilização PTU é a capacidade consumida dividida pela capacidade provisionada. 100% significa que o deployment atingiu o limite observado; acima de 100% é tratado como anomalia de métrica/rollup e não como “overage” faturável.</Typography>
        <Grid container spacing={1.5} sx={{ mb: 1.5 }}>
          <KpiCard label="PTUs provisionados" value={n(c.total_ptus ?? data.kpis?.total_ptus)} hint={`${c.deployment_count ?? deps.length} deployments`} />
          <KpiCard label="Utilização média ponderada" value={pct(c.utilization_weighted_avg ?? data.kpis?.utilization_avg)} hint="ponderada pelos PTUs" color="#2563EB" />
          <KpiCard label="Capacidade não utilizada" value={pct((c.unused_capacity_pct ?? data.kpis?.unused_capacity_pct) == null ? null : Number(c.unused_capacity_pct ?? data.kpis?.unused_capacity_pct) * 100)} hint="média do período" />
          <KpiCard label="Risco operacional" value={<Chip size="small" color={riskColor(c.risk_level) as any} label={riskLabel(c.risk_level)} />} hint={`${c.hours_at_or_above_80 || 0}h ≥80% · ${c.hours_at_or_above_95 || 0}h ≥95%`} />
        </Grid>
        <Alert severity={riskColor(c.risk_level) as any} sx={{ mb: 2 }}><b>{riskLabel(c.risk_level)}:</b> {s.narrative || "Sem dados suficientes para narrativa automática."}</Alert>

        <Typography variant="h6" sx={{ mt: 1.5, mb: 1 }}>Demanda de IA no período selecionado</Typography>
        <Grid container spacing={1.5} sx={{ mb: 1.5 }}>
          <KpiCard label="Tokens totais" value={tokensLabel(total)} hint="Azure Monitor" />
          <KpiCard label="Input tokens" value={tokensLabel(input)} hint={total ? `${pct(input / total * 100)} do total` : "—"} />
          <KpiCard label="Output tokens" value={tokensLabel(output)} hint={total ? `${pct(output / total * 100)} do total` : "—"} />
          <KpiCard label="Tokens por request" value={n(d.tokens_per_request)} hint={s.period_comparison?.tokens_delta_pct == null ? "comparativo indisponível" : `${s.period_comparison.tokens_delta_pct >= 0 ? "+" : ""}${n(s.period_comparison.tokens_delta_pct)}% vs período anterior`} />
        </Grid>

        <Typography variant="h6" sx={{ mt: 1.5, mb: 1 }}>Confiabilidade e experiência</Typography>
        <Grid container spacing={1.5}>
          <KpiCard label="Taxa de 429" value={q.throttle_rate_pct == null ? "—" : `${n(q.throttle_rate_pct)}%`} hint={`${n(q.rate_limited_count)} respostas`} color="#DC2626" />
          <KpiCard label="Disponibilidade" value={metric("availability_rate_avg", q.availability_rate_avg, "%")} hint="chamadas sem erro de servidor" />
          <KpiCard label="TTFT médio" value={metric("normalized_ttft_avg_ms", q.normalized_ttft_avg_ms, " ms")} hint="tempo até primeiro token" />
          <KpiCard label="TBT médio" value={metric("normalized_tbt_avg_ms", q.normalized_tbt_avg_ms, " ms")} hint="tempo entre tokens" />
        </Grid>
      </Section>

      {/* ── 02 · Consumo de tokens ────────────────────────────────────── */}
      <Section index="02" title="Consumo de tokens" subtitle="Input e output são somas das métricas Azure Monitor no período filtrado; não representam custo.">
        <ChartCard title="Consumo de tokens — evolução no período">{tokenConsumptionChart}</ChartCard>
        <ChartCard title="Heatmap de consumo por dia e hora" subtitle="Tokens totais médios por janela do Azure Monitor. Verde fraco indica pouco consumo; verde forte indica pico de consumo.">{tokenHeatmapChart}</ChartCard>
        <Card className="azure-ptu-print-card"><CardContent>
          <Typography variant="h6" gutterBottom>Ranking por deployment</Typography>
          <Table size="small">
            <TableHead><TableRow>{["Projeto", "Deployment", "Modelo / versão", "PTUs", "Tokens", "% total", "Requests", "Tokens/request", "Utilização"].map(h => <TableCell key={h}>{h}</TableCell>)}</TableRow></TableHead>
            <TableBody>{(token.deployment_rankings || []).map((x: any) => {
              const tokens = Number(x.input_tokens || 0) + Number(x.output_tokens || 0);
              const requests = Number(x.request_count || 0);
              return <TableRow key={x.deployment_name}>
                <TableCell>{labels[x.project_key] || x.project_key || "—"}</TableCell>
                <TableCell>{x.deployment_name}</TableCell>
                <TableCell>{x.model_name || "—"}{x.model_version ? ` / ${x.model_version}` : ""}</TableCell>
                <TableCell>{n(x.ptus)}</TableCell>
                <TableCell>{tokensLabel(tokens)}</TableCell>
                <TableCell>{total ? pct(tokens / total * 100) : "—"}</TableCell>
                <TableCell>{n(requests || null)}</TableCell>
                <TableCell>{requests ? n(tokens / requests) : "—"}</TableCell>
                <TableCell>{pct(x.utilization_avg)}</TableCell>
              </TableRow>;
            })}</TableBody>
          </Table>
        </CardContent></Card>
      </Section>

      {/* ── 03 · Capacidade e operação ────────────────────────────────── */}
      <Section index="03" title="Capacidade e operação" subtitle="As horas ≥80% e ≥95% são contagens observadas no base consolidada. O pico acima de 100% é mostrado como anomalia de medição/agregação até ser validado; não deve ser interpretado como cobrança extra. Possíveis ações técnicas incluem revisar distribuição de tráfego, versão/modelo, cache e arquitetura RAG — sempre após confirmar a qualidade do dado.">
        <Grid container spacing={1.5} sx={{ mb: 1.5 }}>
          <KpiCard label="Horas ≥80%" value={n(risk.hours_at_or_above_80)} hint="zona de atenção" />
          <KpiCard label="Horas ≥95%" value={n(risk.hours_at_or_above_95)} hint="zona crítica observada" color="#DC2626" />
          <KpiCard label="Pico observado" value={pct(c.utilization_peak)} hint={c.utilization_peak_label || "pico horário observado"} />
          <KpiCard label="Cache match" value={metric("context_cache_match_rate_avg", q.context_cache_match_rate_avg, "%")} hint="quando suportado" />
        </Grid>
        <ChartCard title="Utilização — média e pico observado">{utilizationChart}</ChartCard>
        <Card className="azure-ptu-print-card"><CardContent>
          <Typography variant="h6" gutterBottom>Deployments ordenados por risco</Typography>
          <Table size="small">
            <TableHead><TableRow>{["Projeto", "Deployment", "Modelo / região", "SKU", "PTUs", "Tokens", "Requests", "≥80%", "≥95%", "Pico RPM", "429", "TTR", "TTLB", "Faixa"].map(h => <TableCell key={h}>{h}</TableCell>)}</TableRow></TableHead>
            <TableBody>{deps.map((x: any) => <TableRow key={x.deployment_name}>
              <TableCell>{labels[x.project_key] || x.project_key || "—"}</TableCell>
              <TableCell>{x.deployment_name}</TableCell>
              <TableCell>{x.model_name || "—"}{x.region ? ` / ${x.region}` : ""}</TableCell>
              <TableCell>{x.sku_name || "—"}</TableCell>
              <TableCell>{n(x.ptus)}</TableCell>
              <TableCell>{tokensLabel(Number(x.input_tokens || 0) + Number(x.output_tokens || 0))}</TableCell>
              <TableCell>{n(x.request_count || null)}</TableCell>
              <TableCell>{n(x.hours_at_or_above_80 ?? x.hours_80)}</TableCell>
              <TableCell>{n(x.hours_at_or_above_95 ?? x.hours_95)}</TableCell>
              <TableCell>{n(x.request_peak_per_minute)}</TableCell>
              <TableCell>{x.throttle_rate_pct == null ? "—" : `${n(x.throttle_rate_pct)}%`}</TableCell>
              <TableCell>{x.time_to_response_avg_ms == null ? "—" : `${n(x.time_to_response_avg_ms)} ms`}</TableCell>
              <TableCell>{x.time_to_last_byte_avg_ms == null ? "—" : `${n(x.time_to_last_byte_avg_ms)} ms`}</TableCell>
              <TableCell><Chip size="small" color={x.utilization_band === "95-100" ? "error" : x.utilization_band === "80-95" ? "warning" : "success"} label={x.utilization_band || "—"} /></TableCell>
            </TableRow>)}</TableBody>
          </Table>
        </CardContent></Card>
        {utilHeatmapChart && <ChartCard title="Heatmap de utilização" subtitle="Utilização média por dia e hora. Verde fraco indica uso baixo; verde forte indica uso próximo do limite.">{utilHeatmapChart}</ChartCard>}
      </Section>

      {/* ── 04 · Recomendações operacionais ───────────────────────────── */}
      <Section index="04" title="Recomendações operacionais" subtitle="Modelos mais novos, otimização de prompts, cache e RAG podem ser alternativas técnicas, mas esta tela não mede qualidade da resposta, tamanho do contexto ou acurácia. Use os deployments sinalizados como candidatos e valide a mudança com testes de qualidade, latência e consumo antes de substituir um modelo.">
        <Stack gap={1.5}>
          {(data.recommendations || []).map((x: any) => <Alert key={`${x.code}-${x.period}`} severity={x.severity === "high" ? "error" : x.severity === "medium" ? "warning" : "info"}>
            <Typography fontWeight={700}>{x.code}</Typography>
            <Typography variant="body2">{x.evidence}</Typography>
            <Typography variant="body2">Impacto: {x.impact} · Confiança: {x.confidence == null ? "—" : `${Math.round(x.confidence * 100)}%`}</Typography>
            <Typography variant="body2">Ação: {x.action}</Typography>
            {x.affected_deployments?.length > 0 && <Typography variant="caption">Deployments: {x.affected_deployments.join(", ")}</Typography>}
          </Alert>)}
          {!(data.recommendations || []).length && <Alert severity="success">Nenhuma condição de risco foi detectada no recorte atual.</Alert>}
        </Stack>
      </Section>

      {/* ── 05 · Notas metodológicas ──────────────────────────────────── */}
      <Section index="05" title="Notas metodológicas" subtitle="Definições e ressalvas sobre cada indicador deste relatório.">
        <Stack gap={1}>
          {Object.entries(widgetHelp).map(([label, help]) => (
            <Typography key={label} variant="body2"><strong>{label}:</strong> {help}</Typography>
          ))}
          <Typography variant="body2" sx={{ mt: 1 }}><strong>Horas ≥80%/≥95%:</strong>  horas observadas que cruzaram esses limiares; são fatos do recorte, não previsão nem confirmação de cobrança.</Typography>
          <Typography variant="body2"><strong>Heatmaps:</strong> a escala de verde vai de uso baixo (verde fraco) a uso alto (verde forte), aplicada igualmente no portal e neste relatório.</Typography>
          <Typography variant="body2"><strong>Financeiro:</strong> valores em BRL (realizado) da base consolidada FOCUS. Reserva = Microsoft.Capacity (100 PTUs/mês, Vivo Mais); on-demand = Provisioned Throughput Units · 1/Month (100 PTUs/mês, Aura Brain); uso horário da capacidade = meter Provisioned Managed (1/Hour, informativo). Classificação por organograma_nivel_5 (projeto).</Typography>
        </Stack>
      </Section>

      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 3, borderTop: "1px solid #e5e7eb", pt: 1.5 }}>
        Documento gerado automaticamente pela plataforma FinOps em {meta.exportedAt}. Filtros aplicados: período {meta.periodLabel}, projeto {meta.projectLabel}, granularidade {meta.grainLabel}. Dados: {sourceLabel}.
      </Typography>
    </Box>
  );
}
