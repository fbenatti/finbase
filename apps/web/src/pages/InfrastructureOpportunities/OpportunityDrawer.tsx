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
import RecurringPeaksPanel from "./RecurringPeaksPanel";
import { getOpportunity, setOpportunityState } from "../../services/infraOpportunitiesApi";
import {
  ACTION_COLOR, ACTION_LABEL, CONFIDENCE_COLOR, CONFIDENCE_LABEL, READINESS_LABEL, STATE_LABEL, money, pct, sizeLabel,
} from "./opportunityLabels";

const UNIT_PRICE_LABEL: Record<string, string> = { ocpu_hour: "OCPU-h", memory_gb_hour: "GB-h", vcpu_hour: "vCPU-h", sku_hour: "hora" };

const unitPrice = (value: number, currency: string) =>
  value.toLocaleString("pt-BR", { style: "currency", currency, minimumFractionDigits: 4, maximumFractionDigits: 4 });

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
  const peaks = evidence?.recurring_peaks?.detection.detected ? evidence.recurring_peaks : null;

  return <Drawer anchor="right" open={opportunityId != null} onClose={onClose}
                 PaperProps={{ sx: { width: { xs: "100%", md: "min(1360px, 94vw)" }, p: { xs: 2, md: 3 } } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="overline" color="text.secondary">Oportunidade de rightsizing · {(item?.provider ?? "").toUpperCase() || "—"}</Typography>
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
            <Typography variant="h6">{from ? `${from.vcpus} vCPU · ${from.memory_gb} GB` : "—"}</Typography>
            <Typography variant="body2" color="text.secondary">{from?.sku ?? "—"}{from?.ocpu ? ` · ${from.ocpu} OCPU` : ""}</Typography>
            <Typography variant="body2" fontWeight={650}>{money(item.current_monthly_cost, currency)} / mês</Typography>
          </Box>
          <ArrowForwardIcon color="action" />
          <Box>
            <Typography variant="caption" color="text.secondary">Recomendado</Typography>
            <Typography variant="h6" color={item.action_type === "RIGHTSIZE_DOWN" ? "success.main" : undefined}>{to ? `${to.vcpus} vCPU · ${to.memory_gb} GB` : "—"}</Typography>
            <Typography variant="body2" color="text.secondary">{to?.sku ?? "—"}{to?.ocpu ? ` · ${to.ocpu} OCPU` : ""}</Typography>
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
          Preço {evidence.prices.billing_period ? `efetivo (${evidence.prices.billing_period})` : "comparável"}: {Object.entries(evidence.prices.unit_prices ?? {}).map(([unit, value]) => `${unitPrice(value, currency)} por ${UNIT_PRICE_LABEL[unit] ?? unit}`).join(" · ")} · {evidence.prices.formula ?? "preço por hora"} × 730 h/mês · {evidence.prices.source ?? ""}
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

      {peaks && <RecurringPeaksPanel key={item.id} item={item} peaks={peaks} currency={currency}
                                     onChanged={(next) => { setItem({ ...item, ...next }); onChanged(); }} />}

      <Box>
        <Typography variant="h6" sx={{ mb: 0.5 }}>De / para do consumo</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          Como o recurso está hoje e como ficaria no tamanho recomendado, com a mesma demanda observada na janela da análise.
        </Typography>
        <OpportunityCharts
          opportunityId={item.id} from={from ?? null} to={to ?? null}
          recurringWindows={peaks?.detection.windows}
          coverage={item.coverage}
          daysObserved={Math.min(evidence?.cpu?.days_observed ?? 0, evidence?.memory?.days_observed ?? evidence?.cpu?.days_observed ?? 0) || null}
          targets={{
            cpu: Number(evidence?.policy?.cpu_target_p95 ?? 0.7), memory: Number(evidence?.policy?.memory_target_p95 ?? 0.8),
            cpuP99: Number(evidence?.policy?.cpu_target_p99 ?? 0.9), memoryP99: Number(evidence?.policy?.memory_target_p99 ?? 0.9),
          }}
        />
      </Box>

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
            <TableCell>{sizeLabel(row.to_config)}</TableCell>
            <TableCell align="right">{money(row.monthly_saving, currency)}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </Paper>
    </Stack>}
  </Drawer>;
}
