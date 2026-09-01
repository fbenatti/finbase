import { useEffect, useMemo, useRef, useState } from "react";
import Plot from "react-plotly.js";
import {
  Alert, Box, Card, CardContent, Chip, CircularProgress, FormControl, Grid,
  InputLabel, MenuItem, Paper, Select, Stack, Tab, Tabs, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import {
  getAIProviderOverview,
  type AIProviderId,
  type AIProviderOverviewResult,
} from "../../services/api";

const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });

const n = (v: any) => v == null ? "—" : fmt.format(Number(v));
const pct = (v: any) => v == null ? "—" : `${fmt.format(Number(v))}%`;

/** Números grandes sumarizados: 1.234.567 -> "1,2 Mi"; 174.899.376 -> "174,9 Mi". */
const compact = (v: any) => {
  const x = Number(v || 0);
  const abs = Math.abs(x);
  if (abs >= 1e9) return (x / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " Bi";
  if (abs >= 1e6) return (x / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " Mi";
  if (abs >= 1e4) return (x / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " K";
  return Math.round(x).toLocaleString("pt-BR");
};

const MODEL_PALETTE = ["#6366F1", "#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#06B6D4", "#EF4444", "#84CC16", "#A855F7", "#0EA5E9"];

const statusColor = (s?: string) => {
  const v = (s || "").toUpperCase();
  if (v === "ATIVO" || v === "ACTIVE") return "success";
  if (v.startsWith("INATIVO")) return "warning";
  return "default";
};

const statusLabel = (s?: string) => {
  const v = (s || "").toUpperCase();
  if (v === "NUNCA_USOU") return "Nunca usou";
  if (v === "ATIVO" || v === "ACTIVE") return "Ativo";
  if (v === "INATIVO_30D") return "Inativo 30d";
  if (v === "INATIVO_60D") return "Inativo 60d";
  if (v === "INATIVO_90D") return "Inativo 90d";
  return v || "—";
};

const fmtDate = (v?: string | null) => {
  if (!v) return "—";
  return String(v).slice(0, 10);
};

const PERIODS = [
  { id: 30, label: "Últimos 30 dias" },
  { id: 90, label: "Últimos 90 dias" },
  { id: 180, label: "Últimos 180 dias" },
  { id: 730, label: "Todo o período" },
] as const;

const HELP: Record<string, string> = {
  "Tokens totais": "Soma de input + output no recorte. Na fonte normalizada, TotalTokens espelha InputTokens; por isso o total executivo usa input+output.",
  "Input tokens": "Soma de tokens de entrada (prompt) no período selecionado.",
  "Output tokens": "Soma de tokens de saída (resposta) no período selecionado.",
  "Requests": "Número total de chamadas/requests registradas no período.",
  "Custo total": "Soma do custo normalizado (AllocatedCost > realizado > BilledCost) no recorte. Para o Claude é a alocação da assinatura; para o NotebookLM é o billing GCP.",
  "Linhas de consumo": "Total de linhas de consumo no recorte, independente de ter tokens ou custo.",
  "Usuários no consumo": "E-mails distintos presentes na tabela de consumo no recorte.",
  "Licenças atribuídas": "E-mails distintos no snapshot mais recente de dest_licencas_ia para o provedor.",
  "Licenças ativas": "Licenças com status diferente de revoked/cancelled/suspended no snapshot.",
  "Logaram": "Usuários com LastActivityAt preenchido — evidência de logon/uso na ferramenta.",
  "Nunca usaram": "Usuários com ActivityStatus = NUNCA_USOU no snapshot de licenças.",
  "Tokens/request": "Tokens médios (input+output) por request no recorte.",
  "Tokens/usuário": "Tokens médios (input+output) por usuário ativo no consumo no recorte.",
  "Custo/1M tokens": "Custo total × 1.000.000 ÷ tokens totais do recorte.",
  "Inativos ≥30d": "Usuários com licença cuja última atividade foi há 30 dias ou mais.",
  "Custo médio/dia": "Custo total do recorte dividido pelos dias com dados de consumo.",
  "Custo por linha": "Custo total dividido pelo número de linhas de consumo (billing GCP).",
};

function Kpi({ label, value, hint, color }: { label: string; value: React.ReactNode; hint?: string; color?: string }) {
  const help = HELP[label] || "Indicador calculado a partir das tabelas Gold dest_licencas_ia / dest_consumo_ia no recorte selecionado.";
  return (
    <Tooltip title={<Typography variant="body2" sx={{ maxWidth: 320, p: .5 }}>{help}</Typography>} arrow placement="top" enterDelay={200}>
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

/** Funil de adoção em barras (mais legível que o funnel do Plotly). */
function AdoptionFunnel({ funnel }: { funnel: { assigned: number; logged_in: number; used: number; healthy: number } }) {
  const stages = [
    { key: "assigned", label: "Licenças atribuídas", value: funnel.assigned, color: "#64748B", desc: "assinaturas no snapshot" },
    { key: "logged_in", label: "Com logon na ferramenta", value: funnel.logged_in, color: "#2563EB", desc: "LastActivityAt preenchido" },
    { key: "used", label: "Com uso registrado", value: funnel.used, color: "#7C3AED", desc: "atividade além de NUNCA_USOU" },
    { key: "healthy", label: "Uso saudável (ATIVO)", value: funnel.healthy, color: "#16A34A", desc: "status ATIVO no snapshot" },
  ] as const;
  const max = Math.max(1, stages[0].value);
  return (
    <Stack gap={1.75} sx={{ mt: 1 }}>
      {stages.map((s, i) => {
        const width = Math.max(s.value > 0 ? 3 : 0, Math.round(s.value / max * 100));
        const share = max ? Math.round(s.value / max * 100) : 0;
        const prev = i > 0 ? stages[i - 1].value : null;
        const conv = prev != null && prev > 0 ? Math.round(s.value / prev * 100) : null;
        return (
          <Box key={s.key}>
            <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 0.5 }}>
              <Typography variant="body2" fontWeight={600}>{s.label}</Typography>
              <Typography variant="body2">
                <b>{n(s.value)}</b>{" "}
                <Box component="span" color="text.secondary" fontWeight={400}>
                  ({share}% das atribuídas{conv != null ? ` · ${conv}% da anterior` : ""})
                </Box>
              </Typography>
            </Stack>
            <Box sx={{ position: "relative", height: 24, bgcolor: "#eef2f7", borderRadius: 1.5, overflow: "hidden" }}>
              <Box sx={{ width: `${width}%`, height: "100%", bgcolor: s.color, borderRadius: 1.5, transition: "width .4s ease" }} />
            </Box>
            <Typography variant="caption" color="text.secondary">{s.desc}</Typography>
          </Box>
        );
      })}
    </Stack>
  );
}

export default function ProviderIaDashboard({ provider }: { provider: AIProviderId }) {
  const [data, setData] = useState<AIProviderOverviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [days, setDays] = useState<number>(90);
  const [search, setSearch] = useState("");
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    const c = new AbortController();
    abort.current?.abort();
    abort.current = c;
    setError(null);
    setData(null);
    getAIProviderOverview({ provider, days }, c.signal)
      .then(x => { if (!c.signal.aborted) setData(x); })
      .catch(e => { if (e?.code !== "ERR_CANCELED" && !c.signal.aborted) setError(e?.response?.data?.detail || "Falha ao consultar o dashboard do provedor"); });
    return () => c.abort();
  }, [provider, days]);

  const isTokens = data?.primary_metric === "tokens";
  const k = data?.kpis;
  const series = data?.daily_series || [];
  const models = data?.model_breakdown || [];
  const ranking = data?.user_ranking || [];
  const licenses = data?.license_table || [];
  const funnel = data?.adoption_funnel;

  const filteredRanking = useMemo(
    () => ranking.filter(u => !search || u.user_email.toLowerCase().includes(search.toLowerCase())),
    [ranking, search],
  );
  const filteredLicenses = useMemo(
    () => licenses.filter(u => !search || (u.user_email || "").toLowerCase().includes(search.toLowerCase())),
    [licenses, search],
  );

  if (error && !data) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Box p={5} textAlign="center"><CircularProgress /></Box>;

  const dates = series.map(r => r.date);
  const costPerDay = k && series.length ? k.total_cost / series.length : 0;

  return (
    <Stack gap={2.5}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h4" gutterBottom>{data.provider_label}</Typography>
          <Typography color="text.secondary" gutterBottom>
            Visão executiva e técnica a partir das tabelas Gold normalizadas (dest_licencas_ia / dest_consumo_ia).
            {isTokens
              ? " Métrica primária: tokens por usuário/modelo/dia; custo alocado quando disponível."
              : " Métrica primária: custo (billing GCP) por serviço; adoção por licença."}
          </Typography>
          <Stack direction="row" gap={1} flexWrap="wrap">
            <Chip size="small" label={`Recorte: ${data.period.date_from} → ${data.period.date_to}`} />
            <Chip size="small" label={`Snapshot licenças: ${data.period.license_period || "—"}`} />
            <Chip size="small" label={`Fonte: Gold Iceberg (${data.period.period_count} períodos)`} color={isTokens ? "primary" : "success"} variant="outlined" />
          </Stack>
        </Box>
        <FormControl size="small" sx={{ minWidth: 185 }}>
          <InputLabel>Período</InputLabel>
          <Select label="Período" value={days} onChange={e => setDays(Number(e.target.value))}>
            {PERIODS.map(p => <MenuItem value={p.id} key={p.id}>{p.label}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {(data.data_quality.notes || []).map((note, i) => (
        <Alert key={i} severity="info" sx={{ alignItems: "flex-start" }}>{note}</Alert>
      ))}

      <Grid container spacing={2}>
        {isTokens ? (
          <>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Tokens totais" value={compact(k?.total_tokens)} hint={`${compact(k?.source_total_tokens)} TotalTokens na fonte`} color="#D97757" /></Grid>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Input tokens" value={compact(k?.input_tokens)} hint={k?.total_tokens ? pct((k.input_tokens || 0) / k.total_tokens * 100) : "—"} /></Grid>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Output tokens" value={compact(k?.output_tokens)} hint={k?.total_tokens ? pct((k.output_tokens || 0) / k.total_tokens * 100) : "—"} /></Grid>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Requests" value={compact(k?.total_requests)} hint={`${n(k?.tokens_per_request)} tokens/request`} /></Grid>
          </>
        ) : (
          <>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Custo total" value={money.format(k?.total_cost || 0)} hint="AllocatedCost > realizado > BilledCost" color="#34A853" /></Grid>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Linhas de consumo" value={compact(data.data_quality.consumption_rows)} hint="linhas billing GCP" /></Grid>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Requests" value={compact(k?.total_requests)} hint="linhas com request registrado" /></Grid>
            <Grid item xs={12} sm={6} md={3}><Kpi label="Custo médio/dia" value={money.format(costPerDay)} hint={`${series.length} dias com dados`} /></Grid>
          </>
        )}
        {isTokens && (
          <Grid item xs={12} sm={6} md={3}><Kpi label="Custo total" value={money.format(k?.total_cost || 0)} hint={k?.total_cost ? "AllocatedCost (alocação da assinatura)" : "sem custo no recorte"} color="#34A853" /></Grid>
        )}
        <Grid item xs={12} sm={6} md={3}><Kpi label="Licenças atribuídas" value={n(k?.total_licenses)} hint={`${n(k?.active_licenses)} ativas`} color="#2563EB" /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Logaram" value={n(k?.logged_in)} hint={k?.total_licenses ? `${pct((k.logged_in || 0) / k.total_licenses * 100)} das licenças` : "—"} color="#16A34A" /></Grid>
        <Grid item xs={12} sm={6} md={3}><Kpi label="Nunca usaram" value={n(k?.never_used)} hint={`${n(k?.inactive_30d)} inativos ≥30d`} color="#DC2626" /></Grid>
        {isTokens && <Grid item xs={12} sm={6} md={3}><Kpi label="Usuários no consumo" value={n(k?.consumption_users)} hint={`${n(k?.tokens_per_user)} tokens/usuário`} /></Grid>}
      </Grid>

      <Tabs value={tab} onChange={(_, v) => setTab(v)}>
        <Tab label="Visão executiva" />
        <Tab label="Visão técnica (usuários e licenças)" />
      </Tabs>

      {tab === 0 && (
        <Stack gap={2.5}>
          <Grid container spacing={2}>
            <Grid item xs={12} md={5}>
              <Card><CardContent>
                <Typography variant="h6">Funil de adoção</Typography>
                <Typography variant="body2" color="text.secondary">Atribuídas → com logon → com uso → uso saudável (ATIVO).</Typography>
                {funnel && <AdoptionFunnel funnel={funnel} />}
              </CardContent></Card>
            </Grid>
            <Grid item xs={12} md={7}>
              <Card><CardContent>
                <Typography variant="h6">{isTokens ? "Consumo de tokens por dia" : "Custo por dia"}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {isTokens
                    ? "Barras: input/output empilhados · linha roxa: total (input+output) · linha verde: custo (eixo direito)."
                    : "Barras: custo por dia · linha azul: requests (eixo direito)."}
                </Typography>
                {isTokens ? (
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 330 }}
                    data={[
                      { x: dates, y: series.map(r => r.input_tokens), name: "Input", type: "bar", marker: { color: "#93C5FD" } },
                      { x: dates, y: series.map(r => r.output_tokens), name: "Output", type: "bar", marker: { color: "#6366F1" } },
                      { x: dates, y: series.map(r => r.input_tokens + r.output_tokens), name: "Total", type: "scatter", mode: "lines", line: { color: "#7C3AED", width: 2 }, yaxis: "y" },
                      { x: dates, y: series.map(r => r.cost), name: "Custo", type: "scatter", mode: "lines", line: { color: "#16A34A", width: 2, dash: "dot" }, yaxis: "y2" },
                    ]}
                    layout={{
                      autosize: true, margin: { l: 64, r: 64, t: 25, b: 60 }, hovermode: "x unified",
                      xaxis: { title: { text: "Data" }, type: "category" },
                      yaxis: { title: { text: "Tokens" }, rangemode: "tozero" },
                      yaxis2: { title: { text: "Custo (R$)" }, overlaying: "y", side: "right", showgrid: false, tickprefix: "R$" },
                      barmode: "stack",
                      legend: { orientation: "h", y: -0.28 },
                      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                  />
                ) : (
                  <Plot
                    useResizeHandler
                    style={{ width: "100%", height: 330 }}
                    data={[
                      { x: dates, y: series.map(r => r.cost), name: "Custo", type: "bar", marker: { color: "#4ADE80" } },
                      { x: dates, y: series.map(r => r.requests), name: "Requests", type: "scatter", mode: "lines", line: { color: "#2563EB", width: 2 }, yaxis: "y2" },
                    ]}
                    layout={{
                      autosize: true, margin: { l: 64, r: 64, t: 25, b: 60 }, hovermode: "x unified",
                      xaxis: { title: { text: "Data" }, type: "category" },
                      yaxis: { title: { text: "Custo (R$)" }, rangemode: "tozero", tickprefix: "R$" },
                      yaxis2: { title: { text: "Requests" }, overlaying: "y", side: "right", showgrid: false },
                      legend: { orientation: "h", y: -0.28 },
                      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                  />
                )}
              </CardContent></Card>
            </Grid>
          </Grid>

          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              <Card><CardContent>
                <Typography variant="h6">{isTokens ? "Tokens por modelo" : "Custo por serviço"}</Typography>
                <Typography variant="body2" color="text.secondary">Top 10 por métrica primária no recorte.</Typography>
                <Plot
                  useResizeHandler
                  style={{ width: "100%", height: 340 }}
                  data={[{
                    type: "bar",
                    orientation: "h",
                    y: models.slice(0, 10).map(m => m.model_name.length > 46 ? m.model_name.slice(0, 46) + "…" : m.model_name).reverse(),
                    x: models.slice(0, 10).map(m => isTokens ? m.total_tokens : m.cost).reverse(),
                    marker: { color: models.slice(0, 10).map((_, i) => MODEL_PALETTE[i % MODEL_PALETTE.length]).reverse() },
                    text: models.slice(0, 10).map(m => isTokens ? compact(m.total_tokens) : money.format(m.cost)).reverse(),
                    textposition: "outside",
                  }]}
                  layout={{
                    autosize: true, margin: { l: 290, r: 90, t: 20, b: 50 },
                    xaxis: { title: { text: isTokens ? "Tokens" : "Custo (R$)" }, tickformat: isTokens ? ".2s" : ",.2s" },
                    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                />
              </CardContent></Card>
            </Grid>
            <Grid item xs={12} md={6}>
              <Card><CardContent>
                <Typography variant="h6">Detalhe por modelo / serviço</Typography>
                <Table size="small" sx={{ maxHeight: 340, overflow: "auto", display: "block" }}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{isTokens ? "Modelo" : "Serviço"}</TableCell>
                      <TableCell align="right">{isTokens ? "Tokens" : "Custo"}</TableCell>
                      <TableCell align="right">Requests</TableCell>
                      <TableCell align="right">% do recorte</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {models.slice(0, 20).map(m => (
                      <TableRow key={m.model_name + m.record_type} hover>
                        <TableCell sx={{ maxWidth: 260 }}>{m.model_name}{m.record_type ? <Box component="span" sx={{ color: "text.secondary" }}> · {m.record_type}</Box> : null}</TableCell>
                        <TableCell align="right">{isTokens ? compact(m.total_tokens) : money.format(m.cost)}</TableCell>
                        <TableCell align="right">{compact(m.requests)}</TableCell>
                        <TableCell align="right">{pct(m.share_pct)}</TableCell>
                      </TableRow>
                    ))}
                    {models.length === 0 && <TableRow><TableCell colSpan={4}><Typography color="text.secondary">Sem dados no recorte.</Typography></TableCell></TableRow>}
                  </TableBody>
                </Table>
              </CardContent></Card>
            </Grid>
          </Grid>

          <Grid container spacing={2}>
            {isTokens ? (
              <>
                <Grid item xs={12} md={4}><Kpi label="Tokens/request" value={n(k?.tokens_per_request)} hint="input+output médios por request" /></Grid>
                <Grid item xs={12} md={4}><Kpi label="Tokens/usuário" value={n(k?.tokens_per_user)} hint="média sobre usuários com consumo" /></Grid>
              </>
            ) : (
              <>
                <Grid item xs={12} md={4}><Kpi label="Custo médio/dia" value={money.format(costPerDay)} hint={`${series.length} dias com dados`} /></Grid>
                <Grid item xs={12} md={4}><Kpi label="Custo por linha" value={money.format(data.data_quality.consumption_rows ? (k?.total_cost || 0) / data.data_quality.consumption_rows : 0)} hint="billing GCP por linha de consumo" /></Grid>
              </>
            )}
            <Grid item xs={12} md={4}><Kpi label="Inativos ≥30d" value={n(k?.inactive_30d)} hint={`≥60d: ${n(k?.inactive_60d)} · ≥90d: ${n(k?.inactive_90d)}`} color="#F59E0B" /></Grid>
          </Grid>
        </Stack>
      )}

      {tab === 1 && (
        <Stack gap={2.5}>
          {data.data_quality.consumption_but_license_never > 0 && (
            <Alert severity="warning">
              <b>{data.data_quality.consumption_but_license_never} usuário(s)</b> têm consumo de tokens no recorte, mas a
              licença marca <b>"Nunca usou"</b>. O <i>LastActivityAt</i> do snapshot de licença só cobre atividade recente
              (janela do snapshot em {data.period.license_period}); consumo em períodos anteriores fica de fora.
              Ex.: usuários que usaram em jun/jul aparecem como "nunca usou" no snapshot de ago — <b>o consumo de tokens
              é a evidência de uso mais confiável</b>.
            </Alert>
          )}
          {data.data_quality.consumption_without_license > 0 && (
            <Alert severity="warning">
              <b>{data.data_quality.consumption_without_license} usuário(s)</b> consumiram tokens sem licença atribuída
              no snapshot — verifique atribuição/remoção de assentos no provedor.
            </Alert>
          )}
          <Alert severity={isTokens ? "info" : "warning"}>
            {isTokens
              ? "Ranking de consumo cruza dest_consumo_ia (tokens por usuário) com dest_licencas_ia (logon/atividade). Usuários sem tokens no recorte aparecem com consumo zero."
              : "Para NotebookLM o consumo não traz e-mail/tokens por usuário (é billing GCP). O ranking abaixo é de adoção: logon e atividade de licença (dest_licencas_ia)."}
          </Alert>
          <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
            <Typography variant="h6">{isTokens ? `Consumo por usuário (${filteredRanking.length})` : `Adoção por usuário (${filteredRanking.length})`}</Typography>
            <TextField size="small" label="Buscar por e-mail" value={search} onChange={e => setSearch(e.target.value)} sx={{ minWidth: 280 }} />
          </Stack>
          <Paper elevation={0} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "auto", maxHeight: 460 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>Usuário</TableCell>
                  {isTokens ? (<><TableCell align="right">Input</TableCell><TableCell align="right">Output</TableCell><TableCell align="right">Tokens</TableCell><TableCell align="right">Requests</TableCell></>) : <TableCell align="right">Custo</TableCell>}
                  <TableCell>Atividade</TableCell>
                  <TableCell>Logou?</TableCell>
                  <TableCell>Última atividade</TableCell>
                  <TableCell align="right">Dias inativo</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredRanking.slice(0, 200).map(u => (
                  <TableRow key={u.user_email} hover>
                    <TableCell><Typography variant="body2">{u.user_email}</Typography>{u.user_name ? <Typography variant="caption" color="text.secondary">{u.user_name}</Typography> : null}</TableCell>
                    {isTokens ? (<>
                      <TableCell align="right">{compact(u.input_tokens)}</TableCell>
                      <TableCell align="right">{compact(u.output_tokens)}</TableCell>
                      <TableCell align="right"><b>{compact(u.tokens)}</b></TableCell>
                      <TableCell align="right">{compact(u.requests)}</TableCell>
                    </>) : <TableCell align="right">{money.format(u.cost)}</TableCell>}
                    <TableCell>
                      <Stack direction="row" gap={0.5} flexWrap="wrap">
                        {u.has_license
                          ? <Chip size="small" color={statusColor(u.activity_status) as any} label={statusLabel(u.activity_status)} />
                          : <Chip size="small" color="default" variant="outlined" label="Sem licença" />}
                        {u.has_consumption && (u.activity_status || "").toUpperCase() === "NUNCA_USOU" && (
                          <Chip size="small" color="warning" variant="outlined" label="Uso no consumo ⚠" />
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell>{u.logged_in ? <Chip size="small" color="success" variant="outlined" label="Sim" /> : <Chip size="small" color="default" variant="outlined" label="Não" />}</TableCell>
                    <TableCell>{fmtDate(u.last_activity_at)}</TableCell>
                    <TableCell align="right">{u.days_since_activity == null ? "—" : n(u.days_since_activity)}</TableCell>
                  </TableRow>
                ))}
                {filteredRanking.length === 0 && <TableRow><TableCell colSpan={8}><Typography color="text.secondary">Nenhum usuário encontrado.</Typography></TableCell></TableRow>}
              </TableBody>
            </Table>
          </Paper>

          <Typography variant="h6">Licenças — snapshot {data.period.license_period || ""} ({filteredLicenses.length})</Typography>
          <Paper elevation={0} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "auto", maxHeight: 460 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>Usuário</TableCell>
                  <TableCell>Status licença</TableCell>
                  <TableCell>Atividade</TableCell>
                  <TableCell>Logou?</TableCell>
                  <TableCell>Última atividade</TableCell>
                  <TableCell align="right">Dias inativo</TableCell>
                  <TableCell>SKU</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredLicenses.map(u => (
                  <TableRow key={u.user_email + u.license_status} hover>
                    <TableCell><Typography variant="body2">{u.user_email}</Typography>{u.user_name ? <Typography variant="caption" color="text.secondary">{u.user_name}</Typography> : null}</TableCell>
                    <TableCell><Chip size="small" color={u.license_status === "active" ? "success" : "default"} variant="outlined" label={u.license_status || "—"} /></TableCell>
                    <TableCell><Chip size="small" color={statusColor(u.activity_status) as any} label={statusLabel(u.activity_status)} /></TableCell>
                    <TableCell>{u.logged_in ? <Chip size="small" color="success" variant="outlined" label="Sim" /> : <Chip size="small" color="default" variant="outlined" label="Não" />}</TableCell>
                    <TableCell>{fmtDate(u.last_activity_at)}</TableCell>
                    <TableCell align="right">{u.days_since_activity == null ? "—" : n(u.days_since_activity)}</TableCell>
                    <TableCell>{u.license_sku || "—"}</TableCell>
                  </TableRow>
                ))}
                {filteredLicenses.length === 0 && <TableRow><TableCell colSpan={7}><Typography color="text.secondary">Nenhuma licença encontrada.</Typography></TableCell></TableRow>}
              </TableBody>
            </Table>
          </Paper>
        </Stack>
      )}
    </Stack>
  );
}
