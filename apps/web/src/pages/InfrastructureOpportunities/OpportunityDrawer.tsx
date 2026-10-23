/**
 * Painel lateral da oportunidade: recurso, de/para da ação, evidência de
 * consumo (P95/P99/pico), preço efetivo usado, histórico de execuções e decisão.
 */
import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, Paper, Stack,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/CloseOutlined";
import ArrowForwardIcon from "@mui/icons-material/ArrowForwardOutlined";
import type { OpportunityDetail, SeriesStats } from "../../services/infraOpportunitiesApi";
import OpportunityCharts from "./OpportunityCharts";
import { getOpportunity, setOpportunityState } from "../../services/infraOpportunitiesApi";
import {
  ACTION_COLOR, ACTION_LABEL, CONFIDENCE_COLOR, CONFIDENCE_LABEL, READINESS_LABEL, STATE_LABEL, money, pct,
} from "./opportunityLabels";

const unitPrice = (value: number, currency: string) =>
  value.toLocaleString("pt-BR", { style: "currency", currency, minimumFractionDigits: 4, maximumFractionDigits: 4 });

function DailyP95Chart({ cpu, memory }: { cpu: SeriesStats | null; memory: SeriesStats | null }) {
  const days = cpu?.daily ?? [];
  if (days.length < 2) return <Typography variant="body2" color="text.secondary">Série diária insuficiente para o gráfico.</Typography>;
  const memoryByDay = new Map((memory?.daily ?? []).map((item) => [item.day, item]));
  const x = (index: number) => 40 + index * 700 / (days.length - 1);
  const y = (value: number) => 150 - Math.min(value, 100) * 1.3;
  const line = (values: (number | null | undefined)[]) =>
    values.map((value, index) => (value == null ? null : `${x(index)},${y(value)}`)).filter(Boolean).join(" ");
  return <svg viewBox="0 0 760 190" role="img" aria-label="P95 diário de CPU e memória" style={{ width: "100%", height: 200 }}>
    {[0, 25, 50, 75, 100].map((tick) => <g key={tick}>
      <line x1="40" x2="740" y1={y(tick)} y2={y(tick)} stroke="#e4e9ed" />
      <text x="34" y={y(tick) + 4} textAnchor="end" fontSize="10" fill="#66747d">{tick}%</text>
    </g>)}
    <polyline points={line(days.map((item) => item.max))} fill="none" stroke="#f0a35e" strokeWidth="1.5" strokeDasharray="4 3" />
    <polyline points={line(days.map((item) => item.p95))} fill="none" stroke="#087f8c" strokeWidth="2.5" />
    <polyline points={line(days.map((item) => memoryByDay.get(item.day)?.p95))} fill="none" stroke="#6b4fbb" strokeWidth="2.5" />
    {days.map((item, index) => <text key={item.day} x={x(index)} y="172" textAnchor="middle" fontSize="10" fill="#66747d">{item.day.slice(5)}</text>)}
    <g fontSize="11">
      <rect x="40" y="180" width="10" height="3" fill="#087f8c" /><text x="54" y="185" fill="#33424b">CPU P95</text>
      <rect x="120" y="180" width="10" height="3" fill="#6b4fbb" /><text x="134" y="185" fill="#33424b">Memória P95</text>
      <rect x="230" y="180" width="10" height="3" fill="#f0a35e" /><text x="244" y="185" fill="#33424b">CPU máx. das médias de 1m</text>
    </g>
  </svg>;
}

function MetricRow({ label, stats, projected95, projected99 }: { label: string; stats: SeriesStats | null; projected95?: number; projected99?: number }) {
  if (!stats) return <TableRow><TableCell>{label}</TableCell><TableCell colSpan={6}>Sem telemetria</TableCell></TableRow>;
  return <TableRow>
    <TableCell>{label}</TableCell>
    <TableCell align="right">{pct(stats.p50)}</TableCell>
    <TableCell align="right">{pct(stats.p95)}</TableCell>
    <TableCell align="right">{pct(stats.p99)}</TableCell>
    <TableCell align="right">{pct(stats.max)}</TableCell>
    <TableCell align="right">{projected95 == null ? "—" : `${pct(projected95)} / ${pct(projected99)}`}</TableCell>
    <TableCell align="right">{(stats.coverage * 100).toFixed(1)}% · {stats.days_observed}d</TableCell>
  </TableRow>;
}

type Props = { opportunityId: number | null; onClose: () => void; onChanged: () => void };

export default function OpportunityDrawer({ opportunityId, onClose, onChanged }: Props) {
  const [item, setItem] = useState<OpportunityDetail | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (opportunityId == null) { setItem(null); return; }
    let active = true;
    setItem(null); setError(null);
    getOpportunity(opportunityId)
      .then((data) => { if (active) { setItem(data); setNote(data.state_note ?? ""); } })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Não foi possível abrir a oportunidade"); });
    return () => { active = false; };
  }, [opportunityId]);

  const transition = async (state: OpportunityDetail["state"]) => {
    if (!item) return;
    setBusy(true);
    try {
      setItem({ ...item, ...(await setOpportunityState(item.id, state, note)) });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível registrar a decisão");
    } finally { setBusy(false); }
  };

  const evidence = item?.evidence;
  const projected = evidence?.projected_utilization ?? {};
  const currency = item?.currency ?? "BRL";
  const from = item?.from_config;
  const to = item?.to_config;

  return <Drawer anchor="right" open={opportunityId != null} onClose={onClose}
                 PaperProps={{ sx: { width: { xs: "100%", md: 1040 }, p: { xs: 2, md: 3 } } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="overline" color="text.secondary">Oportunidade de rightsizing · Oracle Cloud</Typography>
        <Typography variant="h6" sx={{ overflowWrap: "anywhere" }}>{item?.resource_name ?? "Carregando…"}</Typography>
        {item && <Typography variant="caption" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>{item.resource_id}</Typography>}
      </Box>
      <IconButton onClick={onClose} aria-label="Fechar"><CloseIcon /></IconButton>
    </Stack>
    {error && <Alert severity="error" sx={{ my: 1 }}>{error}</Alert>}
    {!item && !error && <Box sx={{ display: "grid", placeItems: "center", py: 8 }}><CircularProgress size={28} /></Box>}
    {item && <Stack spacing={2} sx={{ mt: 1.5 }}>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Chip size="small" color={ACTION_COLOR[item.action_type]} label={ACTION_LABEL[item.action_type]} />
        <Chip size="small" variant="outlined" label={STATE_LABEL[item.state]} />
        <Chip size="small" color={CONFIDENCE_COLOR[item.confidence]} label={`Confiança ${CONFIDENCE_LABEL[item.confidence]}`} />
        <Chip size="small" variant="outlined" label={READINESS_LABEL[item.readiness_status] ?? item.readiness_status} />
      </Stack>

      <Paper variant="outlined" sx={{ p: 2, borderTop: "3px solid #087f8c" }}>
        <Typography variant="subtitle2" gutterBottom>De / para da ação</Typography>
        <Box sx={{ display: "grid", gridTemplateColumns: "1fr auto 1fr", alignItems: "center", gap: 2 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">Atual</Typography>
            <Typography variant="h6">{from ? `${from.ocpus} OCPU · ${from.memory_gb} GB` : "—"}</Typography>
            <Typography variant="body2" color="text.secondary">{from?.shape ?? "—"} · {from?.vcpus ?? "—"} vCPU</Typography>
            <Typography variant="body2" fontWeight={650}>{money(item.current_monthly_cost, currency)} / mês</Typography>
          </Box>
          <ArrowForwardIcon color="action" />
          <Box>
            <Typography variant="caption" color="text.secondary">Recomendado</Typography>
            <Typography variant="h6" color={item.action_type === "RIGHTSIZE_DOWN" ? "success.main" : undefined}>{to ? `${to.ocpus} OCPU · ${to.memory_gb} GB` : "—"}</Typography>
            <Typography variant="body2" color="text.secondary">{to?.shape ?? "—"} · {to?.vcpus ?? "—"} vCPU</Typography>
            <Typography variant="body2" fontWeight={650}>{money(item.target_monthly_cost, currency)} / mês</Typography>
          </Box>
        </Box>
        <Divider sx={{ my: 1.5 }} />
        <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
          <Box><Typography variant="caption" color="text.secondary">Saving / mês</Typography><Typography variant="h6" color={(item.monthly_saving ?? 0) >= 0 ? "success.main" : "error.main"}>{money(item.monthly_saving, currency)}</Typography></Box>
          <Box><Typography variant="caption" color="text.secondary">Saving / ano</Typography><Typography variant="h6">{money(item.annual_saving, currency)}</Typography></Box>
          <Box><Typography variant="caption" color="text.secondary">Redução do custo do recurso</Typography><Typography variant="h6">{item.current_monthly_cost && item.monthly_saving != null ? `${(item.monthly_saving / item.current_monthly_cost * 100).toFixed(0)}%` : "—"}</Typography></Box>
        </Stack>
        {evidence?.prices && <Typography variant="caption" color="text.secondary">
          Preço efetivo do billing ({evidence.prices.billing_period}): {unitPrice(evidence.prices.ocpu_hour, currency)}/OCPU-h e {unitPrice(evidence.prices.memory_gb_hour, currency)}/GB-h · 730 h/mês · {evidence.prices.meters?.ocpu_hour} / {evidence.prices.meters?.memory_gb_hour}
        </Typography>}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2">Consumo na janela analisada</Typography>
        <Typography variant="caption" color="text.secondary">Percentis lineares sobre as médias dos buckets de {((evidence?.cpu as { resolution_seconds?: number } | null)?.resolution_seconds ?? 60) / 60} min ({Object.entries((evidence?.cpu as { origins?: Record<string, number> } | null)?.origins ?? { NATIVE: 0 }).map(([origin, count]) => `${origin === "NATIVE" ? "nativo OCI" : "rollup local do 1 min"}${count ? `: ${count.toLocaleString("pt-BR")}` : ""}`).join(" · ")}) · pico = máximo nativo · “No alvo” = utilização projetada após a ação (P95 / P99)</Typography>
        <Table size="small" sx={{ mt: 1 }}>
          <TableHead><TableRow>
            <TableCell>Métrica</TableCell><TableCell align="right">P50</TableCell><TableCell align="right">P95</TableCell>
            <TableCell align="right">P99</TableCell><TableCell align="right">Pico</TableCell><TableCell align="right">No alvo</TableCell><TableCell align="right">Cobertura</TableCell>
          </TableRow></TableHead>
          <TableBody>
            <MetricRow label="CPU" stats={evidence?.cpu ?? null} projected95={projected.cpu_p95} projected99={projected.cpu_p99} />
            <MetricRow label="Memória" stats={evidence?.memory ?? null} projected95={projected.memory_p95} projected99={projected.memory_p99} />
          </TableBody>
        </Table>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2">De / para do consumo</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
          Como a VM está hoje e como ficaria no tamanho recomendado, com a mesma demanda observada na janela.
        </Typography>
        <OpportunityCharts opportunityId={item.id} from={from ?? null} to={to ?? null}
                           targets={{ cpu: Number(evidence?.policy?.cpu_target_p95 ?? 0.7), memory: Number(evidence?.policy?.memory_target_p95 ?? 0.8) }} />
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Evolução diária</Typography>
        <DailyP95Chart cpu={evidence?.cpu ?? null} memory={evidence?.memory ?? null} />
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Por que o motor recomendou</Typography>
        <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 2.5 }}>
          {item.rationale.map((line) => <Typography component="li" variant="body2" key={line}>{line}</Typography>)}
        </Stack>
        {item.risks.map((risk) => <Alert key={risk} severity="warning" sx={{ mt: 1 }}>{risk}</Alert>)}
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, overflowWrap: "anywhere" }}>
          Motor {item.engine_version} · política {String(evidence?.policy?.version ?? "—")} · hash dos insumos {item.inputs_hash.slice(0, 16)}…
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Decisão</Typography>
        <TextField size="small" fullWidth multiline minRows={2} label="Observação" value={note} onChange={(event) => setNote(event.target.value)} />
        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
          <Button size="small" variant="contained" disabled={busy || item.state === "ACCEPTED"} onClick={() => void transition("ACCEPTED")}>Aceitar</Button>
          <Button size="small" variant="contained" color="success" disabled={busy || item.state === "IMPLEMENTED"} onClick={() => void transition("IMPLEMENTED")}>Marcar como executada</Button>
          <Button size="small" color="inherit" disabled={busy || item.state === "DISMISSED"} onClick={() => void transition("DISMISSED")}>Descartar</Button>
          {item.state !== "NEW" && <Button size="small" disabled={busy} onClick={() => void transition("NEW")}>Reabrir</Button>}
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Histórico nas execuções do motor</Typography>
        <Table size="small">
          <TableHead><TableRow><TableCell>Execução</TableCell><TableCell>Janela</TableCell><TableCell>Ação</TableCell><TableCell>Para</TableCell><TableCell align="right">Saving / mês</TableCell></TableRow></TableHead>
          <TableBody>{item.history.map((row) => <TableRow key={row.run_id}>
            <TableCell>#{row.run_id}</TableCell>
            <TableCell>{row.window_start_utc?.slice(0, 10)} → {row.window_end_utc?.slice(0, 10)}</TableCell>
            <TableCell>{ACTION_LABEL[row.action_type as keyof typeof ACTION_LABEL] ?? row.action_type}</TableCell>
            <TableCell>{row.to_config ? `${row.to_config.ocpus} OCPU · ${row.to_config.memory_gb} GB` : "—"}</TableCell>
            <TableCell align="right">{money(row.monthly_saving, currency)}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </Paper>
    </Stack>}
  </Drawer>;
}
