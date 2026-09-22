import { useEffect, useMemo, useRef, useState } from "react";
import Plot from "@/components/Plot";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, Grid,
  Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip, Typography,
} from "@mui/material";
import { getProjectCostDashboard } from "../../services/api";

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const brl2 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 });
const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const n = (v: any) => (v == null || isNaN(Number(v)) ? "—" : fmt.format(Number(v)));
const money = (v: any) => (v == null || isNaN(Number(v)) ? "—" : brl.format(Number(v)));
const money2 = (v: any) => (v == null || isNaN(Number(v)) ? "—" : brl2.format(Number(v)));
const pct = (v: any) => (v == null || isNaN(Number(v)) ? "—" : `${fmt.format(Number(v))}%`);
const periodLabel = (period?: string) => {
  if (!period || period.length !== 6) return period || "—";
  return new Intl.DateTimeFormat("pt-BR", { month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${period.slice(0, 4)}-${period.slice(4, 6)}-01T00:00:00Z`))
    .replace(" de ", "/").replace(".", "");
};

/** Números grandes sumarizados: 1.234.567 -> "1,2 Mi". */
const compact = (v: any) => {
  const x = Number(v || 0);
  const abs = Math.abs(x);
  if (abs >= 1e9) return (x / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " Bi";
  if (abs >= 1e6) return (x / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " Mi";
  if (abs >= 1e4) return (x / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " K";
  return Math.round(x).toLocaleString("pt-BR");
};

const CLASS_COLORS: Record<string, string> = {
  ptu: "#7C3AED",
  tokens_ondemand: "#0EA5E9",
  ia_service: "#EC4899",
  infra: "#F59E0B",
};
const CLASS_LABELS: Record<string, string> = {
  ptu: "PTUs (compra mensal)",
  tokens_ondemand: "Tokens on-demand",
  ia_service: "Serviços de IA",
  infra: "Infraestrutura",
};
const IA_PALETTE = ["#EC4899", "#8B5CF6", "#06B6D4", "#10B981", "#F97316", "#84CC16", "#14B8A6", "#F43F5E", "#A855F7", "#64748B"];
const PROJECT_ACCENT: Record<string, string> = {
  "aura-brain": "#7C3AED",
  "vivo-mais": "#2563EB",
};

const HELP: Record<string, string> = {
  "Custo total": "Mês de referência = realizado + forecast do mês corrente: quando a compra mensal de PTU ainda não foi lançada na Gold, entra o valor previsto (estimado pelo último realizado). BRL, líquido de devoluções (Returns).",
  "PTUs (compra mensal)": "Compra mensal de capacidade PTU — reserva (Purchase) ou on-demand (Provisioned Throughput Units, 1/Month). Vale uma única fonte por mês: realizada se existir, senão estimada pelo último realizado da compra. O consumo diário/horário (Provisioned Managed*, 1/Hour) já está incluído na compra e não é exibido.",
  "Tokens on-demand": "Meters PAYG de tokens da Azure OpenAI clássicos (gpt 4o, gpt 4.1, embeddings). Modelos novos (GPT-5, Deepseek, Claude) têm card próprio em Serviços de IA.",
  "Serviços de IA": "Serviços relacionados a IA com card próprio na fatura Azure: Azure Document Intelligence, Claude (Anthropic), Azure OpenAI GPT-5/Media/Reasoning, Deepseek, Azure Speech etc. Foram removidos de Infraestrutura e de Tokens on-demand.",
  "Infraestrutura": "Todo o restante: VMs, Cosmos DB, discos, rede, Service Bus, Log Analytics etc., detalhado por servicesubcategory (sem serviços de IA).",
  "Infra compartilhada (rateio)": "Custo de infraestrutura repassado ao projeto. O Vivo Mais não recebe rateio: consome a infra do Aura Brain, mas a divisão não é feita por não existir forma eficiente (a infra não é específica do Vivo Mais e uma estrutura dedicada seria inviável).",
  "Custo previsto (forecast)": "Total planejado (planning) do mês de referência, exibido como total no chip — o planejamento não tem granularidade por componente. Bruto (sem abater as devoluções do espelho, que espelham o consumo horário já excluído do realizado).",
  "Bilhões de tokens": "Bilhões de tokens atribuídos ao projeto na Gold diária de Azure PTU, somando input e output no mesmo mês do custo.",
  "Custo por MM de tokens": "Custo total do projeto dividido pelos milhões de tokens atribuídos no mesmo período. Inclui toda a composição de custo do projeto, não apenas meters de tokens.",
};

function Kpi({ label, value, hint, color }: { label: string; value: React.ReactNode; hint?: string; color?: string }) {
  const help = HELP[label] || "Indicador executivo calculado a partir da Gold específica do projeto.";
  return (
    <Tooltip title={<Typography variant="body2" sx={{ maxWidth: 340, p: 0.5 }}>{help}</Typography>} arrow placement="top" enterDelay={250}>
      <Card sx={{ height: "100%", borderTop: color ? `3px solid ${color}` : undefined, cursor: "help" }}>
        <CardContent>
          <Typography variant="body2" color="text.secondary">{label} ⓘ</Typography>
          <Typography variant="h6">{value}</Typography>
          {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
        </CardContent>
      </Card>
    </Tooltip>
  );
}

export default function ProjectCostDashboard({ project }: { project: "aura-brain" | "vivo-mais" }) {
  const isVivo = project === "vivo-mais";
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    const c = new AbortController();
    abort.current = c;
    setError(null);
    getProjectCostDashboard({ project_key: project, period_range: "current_year" }, c.signal)
      .then(x => { if (!c.signal.aborted) setData(x); })
      .catch(e => {
        if (e?.code !== "ERR_CANCELED") setError(e?.response?.data?.detail || "Falha ao consultar o custo do projeto.");
      });
    return () => c.abort();
  }, [project]);

  const cur = data?.current || {};
  const realized = cur.realizado || {};
  const classes = realized.classes || {};
  const pcts = realized.pct || {};
  const monthly: any[] = data?.monthly || [];
  const infra: any[] = cur.infra_by_subcategory || [];
  const iaServices: any[] = cur.ia_services_by_subcategory || [];
  const subaccounts: any[] = cur.subaccounts || [];
  const notes: string[] = data?.notes || [];
  const total = realized.total;
  const realizedTotal = realized.realizado_total ?? Number(total);
  const ptuSource: "realizado" | "estimado" = cur.ptu_source === "estimado" ? "estimado" : "realizado";
  const ptuEstimateNote = cur.ptu_estimate_note || "último realizado";
  const tokenEfficiency = cur.token_efficiency || {};
  const efficiencyEstimated = Boolean(tokenEfficiency.estimated);

  // Famílias de IA na ordem de importância do mês corrente (cores estáveis).
  const iaFamilies = useMemo(() => {
    const ordered: string[] = (cur.ia_services_by_subcategory || []).map((r: any) => r.family);
    const set = new Set<string>(ordered);
    monthly.forEach((m: any) => Object.keys(m.realizado?.ia_services || {}).forEach((k: string) => set.add(k)));
    return [...set];
  }, [monthly, cur]);

  // O gráfico cobre toda a janela selecionada (ano atual / todo o período): as
  // barras coloridas são o realizado por classe (stack nativo do Plotly). A
  // barra cinza é o forecast TOTAL em overlay — o planejamento não tem
  // granularidade por componente, então o forecast é uma barra única.
  // Não usar `base` manual nas classes: com `base` o Plotly reporta y como
  // base+segmento, e Infraestrutura (último da pilha) vira o total.
  const series = useMemo(() => ({
    x: monthly.map(m => m.label),
    forecast: monthly.map(m => m.forecast ?? null),
    traces: [
      { name: CLASS_LABELS.ptu, values: monthly.map(m => m.realizado?.ptu ?? null), color: CLASS_COLORS.ptu },
      { name: CLASS_LABELS.tokens_ondemand, values: monthly.map(m => m.realizado?.tokens_ondemand ?? null), color: CLASS_COLORS.tokens_ondemand },
      ...iaFamilies.map((fam, i) => ({
        name: fam,
        values: monthly.map(m => m.realizado?.ia_services?.[fam] ?? null),
        color: IA_PALETTE[i % IA_PALETTE.length],
      })),
      { name: CLASS_LABELS.infra, values: monthly.map(m => m.realizado?.infra ?? null), color: CLASS_COLORS.infra },
    ],
  }), [monthly, iaFamilies]);

  const infraTop = infra.slice(0, 12);
  // Barra horizontal do Plotly: o 1º item fica embaixo — inverte para o mais
  // caro aparecer no topo (a tabela mantém a ordem decrescente).
  const infraTopAsc = [...infraTop].reverse();
  const infraTotal = infra.reduce((s: number, r: any) => s + Number(r.realized || 0), 0);
  const subTotal = subaccounts.reduce((s: number, r: any) => s + Number(r.realized || 0), 0);

  return (
    <Stack gap={2.5}>
      <Typography variant="h4">{data?.project_label || (isVivo ? "Vivo Mais" : "Aura Brain")}</Typography>

      {isVivo && (
        <Alert severity="info" sx={{ borderRadius: 2 }}>
          <Typography fontWeight={700} gutterBottom>Por que o Vivo Mais não recebe custo compartilhado de infraestrutura?</Typography>
          <Typography variant="body2" paragraph sx={{ mb: 1 }}>
            O Vivo Mais <b>consome recursos do Aura Brain</b> (capacidade PTU e infraestrutura da mesma subconta),
            mas <b>não recebe rateio de infraestrutura</b>: todo o custo do projeto vive nas subcontas
            <code> AURA BRAIN - PRO/DEV</code>, e a infraestrutura do Aura Brain não é específica do Vivo Mais.
            Não existe uma forma eficiente de dividir esse custo, e criar uma estrutura dedicada somente para o
            Vivo Mais seria inviável. Por isso o custo exibido é apenas o que é diretamente atribuível:
            <b> PTUs (reserva)</b> e <b>tokens on-demand</b>.
          </Typography>
          <Chip size="small" label={`Infra compartilhada (rateio): R$ 0,00`} color="info" variant="outlined" />
        </Alert>
      )}

      {error && <Alert severity="error">{error}</Alert>}
      {!error && !data && <Box p={5} textAlign="center"><CircularProgress /></Box>}
      {!error && data?.available === false && <Alert severity="warning">{data.error || "Dados indisponíveis."}</Alert>}
      {!error && data?.empty && <Alert severity="info">Nenhum mês com custo realizado na Gold para este projeto.</Alert>}

      {data?.available && !data?.empty && (
        <>
          <Card sx={{ color: "white", background: `linear-gradient(120deg, ${PROJECT_ACCENT[project]}, #0F172A)` }}>
            <CardContent>
              <Stack direction="row" alignItems="center" gap={1}>
                <Typography variant="overline" sx={{ opacity: 0.8 }}>Eficiência econômica de IA</Typography>
                {efficiencyEstimated && <Chip size="small" label="run-rate estimado" sx={{ bgcolor: "rgba(255,255,255,.18)", color: "white" }} />}
              </Stack>
              <Grid container spacing={2} mt={0.25}>
                <Grid item xs={12} md={4}><Typography variant="body2" sx={{ opacity: 0.8 }}>Custo total do projeto</Typography><Typography variant="h4">{money(tokenEfficiency.project_cost ?? total)}</Typography><Typography variant="caption" sx={{ opacity: 0.75 }}>{periodLabel(tokenEfficiency.cost_period || cur.period)}</Typography></Grid>
                <Grid item xs={12} md={4}><Typography variant="body2" sx={{ opacity: 0.8 }}>Bilhões de tokens</Typography><Typography variant="h4">{tokenEfficiency.total_tokens ? n(Number(tokenEfficiency.total_tokens) / 1_000_000_000) : "—"}</Typography><Typography variant="caption" sx={{ opacity: 0.75 }}>input + output · últimos 30 dias{tokenEfficiency.token_date_from && tokenEfficiency.token_date_to ? ` (${tokenEfficiency.token_date_from} a ${tokenEfficiency.token_date_to})` : ""}</Typography></Grid>
                <Grid item xs={12} md={4}><Typography variant="body2" sx={{ opacity: 0.8 }}>Custo por MM de tokens</Typography><Typography variant="h4">{money2(tokenEfficiency.cost_per_million_tokens)}</Typography><Typography variant="caption" sx={{ opacity: 0.75 }}>custo mensal ÷ milhões de tokens</Typography></Grid>
              </Grid>
            </CardContent>
          </Card>
          {tokenEfficiency.note && <Alert severity="info">{tokenEfficiency.note}</Alert>}
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={isVivo ? 4 : 3}>
              <Kpi label="Custo total" value={money(total)} hint={`${cur.label} · último realizado: R$ ${compact(realizedTotal)}${cur.mom_pct == null ? "" : ` · ${cur.mom_pct >= 0 ? "+" : ""}${n(cur.mom_pct)}% vs ${cur.prev_period_label}`}`} color={PROJECT_ACCENT[project]} />
            </Grid>
            <Grid item xs={12} sm={6} md={isVivo ? 4 : 3}>
              <Kpi label="PTUs (compra mensal)" value={<>{money(classes.ptu)} {ptuSource === "estimado" && <Chip size="small" color="warning" label="estimado" sx={{ ml: 0.5 }} />}</>} hint={`${pct(pcts.ptu)} do total · ${ptuSource === "estimado" ? `compra ainda não lançada na Gold — estimado pelo ${ptuEstimateNote}` : isVivo ? "reserva (Purchase) realizada" : "on-demand mensal realizado"}`} color={CLASS_COLORS.ptu} />
            </Grid>
            <Grid item xs={12} sm={6} md={isVivo ? 4 : 3}>
              <Kpi label="Tokens on-demand" value={money(classes.tokens_ondemand)} hint={`${pct(pcts.tokens_ondemand)} do total · meters PAYG clássicos (GPT-5, Claude e outros têm card próprio)`} color={CLASS_COLORS.tokens_ondemand} />
            </Grid>
            {!isVivo && (
              <Grid item xs={12} sm={6} md={3}>
                <Kpi label="Infraestrutura" value={money(classes.infra)} hint={`${pct(pcts.infra)} do total · ${infra.length} subcategorias · sem serviços de IA`} color={CLASS_COLORS.infra} />
              </Grid>
            )}
          </Grid>

          <Card>
            <CardContent>
              <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
                <Typography variant="h6">Evolução mensal — realizado por classe e forecast</Typography>
                <Chip size="small" label={`Último realizado: ${cur.label}`} />
              </Stack>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Barras coloridas = custo realizado por classe. A barra cinza representa a projeção do mês, exibida apenas nesta evolução; KPIs e detalhes usam sempre o último mês realizado fechado.
              </Typography>
              <Plot
                data={[
                  { x: series.x, y: series.forecast, base: 0, name: "Forecast (projeção)", type: "bar", marker: { color: "rgba(15, 23, 42, 0.18)" }, hovertemplate: "%{y:,.0f}<extra>Forecast (projeção)</extra>" },
                  ...series.traces.map(t => ({
                    x: series.x,
                    y: t.values,
                    name: t.name,
                    type: "bar" as const,
                    marker: { color: t.color },
                    hovertemplate: `%{y:,.0f}<extra>${t.name}</extra>`,
                  })),
                ]}
                layout={{
                  autosize: true, barmode: "stack", hovermode: "x unified",
                  margin: { l: 70, r: 20, t: 20, b: 50 },
                  paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                  yaxis: { title: { text: "R$" }, rangemode: "tozero", tickprefix: "R$ " },
                  legend: { orientation: "h", y: -0.25 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: 340 }}
                useResizeHandler
              />
            </CardContent>
          </Card>

          {iaServices.length > 0 && (
            <Card>
              <CardContent>
                <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
                  <Typography variant="h6">Serviços de IA — último realizado fechado ({cur.detail_label || "—"})</Typography>
                  <Chip size="small" label={`Total: ${money(iaServices.reduce((s: number, r: any) => s + Number(r.realized || 0), 0))}`} />
                </Stack>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  Linhas de consumo relacionadas a IA na fatura Azure, com card próprio — removidas de Infraestrutura e de Tokens on-demand. Detalhe do mês anterior ao de referência ({cur.label}), quando o realizado está fechado; barras no gráfico acima.
                </Typography>
                <Grid container spacing={1.5}>
                  {iaServices.map((r: any, i: number) => (
                    <Grid item xs={12} sm={6} md={4} lg={3} key={r.family}>
                      <Tooltip title={<Typography variant="body2" sx={{ maxWidth: 300, p: 0.5 }}>{r.family} na fatura Azure · {pct(r.pct)} do total de serviços de IA em {cur.label}.</Typography>} arrow placement="top" enterDelay={250}>
                        <Card sx={{ height: "100%", borderTop: `3px solid ${IA_PALETTE[i % IA_PALETTE.length]}`, cursor: "help" }}>
                          <CardContent>
                            <Typography variant="body2" color="text.secondary">{r.family} ⓘ</Typography>
                            <Typography variant="h6">{money(r.realized)}</Typography>
                            <Typography variant="caption" color="text.secondary">{pct(r.pct)} dos serviços de IA</Typography>
                          </CardContent>
                        </Card>
                      </Tooltip>
                    </Grid>
                  ))}
                </Grid>
              </CardContent>
            </Card>
          )}

          {!isVivo && infra.length > 0 && (
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>Infraestrutura por subcategoria de serviço — último realizado fechado ({cur.detail_label || "—"})</Typography>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                  Detalhe do custo de infra por <code>servicesubcategory</code> no último mês realizado ({cur.label}).
                </Typography>
                <Grid container spacing={2} alignItems="stretch">
                  <Grid item xs={12} md={6}>
                    <Plot
                      data={[{
                        x: infraTopAsc.map(r => r.realized),
                        y: infraTopAsc.map(r => r.servicesubcategory),
                        type: "bar",
                        orientation: "h",
                        marker: { color: "#F59E0B" },
                        hovertemplate: "%{y}: R$ %{x:,.0f}<extra></extra>",
                      }]}
                      layout={{
                        autosize: true,
                        margin: { l: 210, r: 20, t: 10, b: 30 },
                        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                        xaxis: { title: { text: "R$" }, tickprefix: "R$ " },
                        yaxis: { automargin: true },
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", height: 360 }}
                      useResizeHandler
                    />
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          {["Subcategoria de serviço", "Custo", "% infra", "% acum."].map(h => <TableCell key={h} sx={{ fontWeight: 700 }}>{h}</TableCell>)}
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {infra.slice(0, 15).map((r: any, i: number) => {
                          const cum = infra.slice(0, i + 1).reduce((s: number, x: any) => s + Number(x.realized || 0), 0);
                          return (
                            <TableRow key={r.servicesubcategory}>
                              <TableCell>{r.servicesubcategory}</TableCell>
                              <TableCell>{money(r.realized)}</TableCell>
                              <TableCell>{pct(r.pct)}</TableCell>
                              <TableCell>{infraTotal ? pct((cum / infraTotal) * 100) : "—"}</TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                    {infra.length > 15 && <Typography variant="caption" color="text.secondary">Exibindo top 15 de {infra.length} subcategorias.</Typography>}
                  </Grid>
                </Grid>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Distribuição por subconta — último realizado fechado ({cur.detail_label || "—"})</Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Valores do último mês realizado ({cur.label}).
                {isVivo
                  ? " Todo o custo do Vivo Mais vive nas subcontas do Aura Brain — o que sustenta a decisão de não ratear infra."
                  : " Custo realizado do Aura Brain (sem consumo diário/horário de PTU, que já está na compra mensal)."}
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    {["Subconta", "Custo", "% do projeto"].map(h => <TableCell key={h} sx={{ fontWeight: 700 }}>{h}</TableCell>)}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {subaccounts.map((s: any) => (
                    <TableRow key={s.subaccountname}>
                      <TableCell><code>{s.subaccountname}</code></TableCell>
                      <TableCell>{money(s.realized)}</TableCell>
                      <TableCell>{subTotal ? pct((Number(s.realized) / subTotal) * 100) : "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {notes.map((note: string, i: number) => (
            <Alert key={i} severity="info" sx={{ borderRadius: 2 }}>{note}</Alert>
          ))}
        </>
      )}
    </Stack>
  );
}
