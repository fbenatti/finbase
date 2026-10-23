/**
 * Picos recorrentes: horários em que a CPU dispara quase todo dia (cron,
 * backup, ETL) e quanto economizaria limitar ou reagendar esse job.
 *
 * A economia é condicionada: depende de uma mudança no job, por isso tem
 * decisão própria e só entra na economia projetada depois de aceita.
 */
import { useState } from "react";
import {
  Alert, Box, Button, Chip, Divider, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForwardOutlined";
import type { FlexConfig, Opportunity, OpportunityDetail, RecurringPeaks } from "../../services/infraOpportunitiesApi";
import { setConditionalState } from "../../services/infraOpportunitiesApi";
import { CONFIDENCE_COLOR, CONFIDENCE_LABEL, STATE_LABEL, money, pct, sizeLabel } from "./opportunityLabels";

export const PEAK_SLOT_COLOR = "#7b3fe4";
const WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

type Props = {
  item: OpportunityDetail;
  peaks: RecurringPeaks;
  currency: string;
  onChanged: (item: OpportunityDetail) => void;
};

function SizeBox({ title, config, cost, currency, highlight }: { title: string; config: FlexConfig | null | undefined; cost: number | null; currency: string; highlight?: boolean }) {
  return <Box>
    <Typography variant="caption" color="text.secondary">{title}</Typography>
    <Typography variant="subtitle1" fontWeight={700} sx={{ color: highlight ? PEAK_SLOT_COLOR : undefined }}>
      {config ? `${config.vcpus} vCPU · ${config.memory_gb} GB` : "—"}
    </Typography>
    <Typography variant="body2" color="text.secondary">{money(cost, currency)} / mês</Typography>
  </Box>;
}

export default function RecurringPeaksPanel({ item, peaks, currency, onChanged }: Props) {
  const [note, setNote] = useState(item.conditional_state_note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { detection, conditional } = peaks;

  const transition = async (state: Opportunity["state"]) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await setConditionalState(item.id, state, note));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível registrar a decisão");
    } finally { setBusy(false); }
  };

  const minutes = detection.windows.reduce((total, window) => total + window.typical_duration_minutes, 0);
  const state = item.conditional_state ?? "NEW";
  return <Paper variant="outlined" sx={{ p: 2, borderTop: `3px solid ${PEAK_SLOT_COLOR}` }}>
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Typography variant="subtitle1" fontWeight={700}>Picos recorrentes</Typography>
      {conditional && <>
        <Chip size="small" sx={{ bgcolor: PEAK_SLOT_COLOR, color: "#fff" }} label="Condicionada a ajuste de job" />
        <Chip size="small" variant="outlined" label={STATE_LABEL[state]} />
        <Chip size="small" variant="outlined" color={CONFIDENCE_COLOR[conditional.confidence]} label={`Confiança ${CONFIDENCE_LABEL[conditional.confidence]}`} />
      </>}
    </Stack>
    <Typography variant="body2" sx={{ mt: 1 }}>
      A CPU dispara nos mesmos horários quase todo dia: <b>{detection.label}</b> (horário de {detection.timezone === "America/Sao_Paulo" ? "Brasília" : detection.timezone}).
      São ~{minutes.toLocaleString("pt-BR")} min por dia, {((detection.cpu_time_share ?? 0) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% do tempo de CPU da janela —
      mas são eles que seguram o tamanho da VM. Os horários estão contornados em roxo no mapa de picos abaixo.
    </Typography>

    <Table size="small" sx={{ mt: 1.5 }}>
      <TableHead><TableRow>
        <TableCell>Horário típico</TableCell><TableCell>Quando</TableCell><TableCell align="right">Dias com o pico</TableCell>
        <TableCell align="right">Duração típica</TableCell><TableCell align="right">Pico típico</TableCell><TableCell align="right">Tempo de CPU</TableCell>
      </TableRow></TableHead>
      <TableBody>{detection.windows.map((window) => <TableRow key={`${window.start}-${window.weekdays?.join() ?? "all"}`}>
        <TableCell sx={{ fontWeight: 650 }}>{window.start}–{window.end}</TableCell>
        <TableCell>{window.weekdays ? window.weekdays.map((day) => WEEKDAYS[day]).join(", ") : "todo dia"}</TableCell>
        <TableCell align="right">{window.days_with_peak} de {window.days_observed}</TableCell>
        <TableCell align="right">{window.typical_duration_minutes.toLocaleString("pt-BR")} min</TableCell>
        <TableCell align="right">{pct(window.typical_peak)}</TableCell>
        <TableCell align="right">{(window.cpu_time_share * 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%</TableCell>
      </TableRow>)}</TableBody>
    </Table>

    {!conditional && <Alert severity="info" sx={{ mt: 1.5 }}>{detection.note ?? "Sem economia adicional ao limitar ou reagendar esses picos."}</Alert>}

    {conditional && <>
      <Divider sx={{ my: 2 }} />
      <Typography variant="subtitle2" gutterBottom>Se o job for limitado ou reagendado</Typography>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr auto 1fr auto 1fr" }, alignItems: "center", gap: 2 }}>
        <SizeBox title="Atual" config={item.from_config} cost={item.current_monthly_cost} currency={currency} />
        <ArrowForwardIcon color="action" sx={{ display: { xs: "none", sm: "block" } }} />
        <SizeBox title="Recomendado hoje (com os picos)" config={item.to_config} cost={item.target_monthly_cost} currency={currency} />
        <ArrowForwardIcon color="action" sx={{ display: { xs: "none", sm: "block" } }} />
        <SizeBox title="Sem os picos recorrentes" config={conditional.to_config} cost={conditional.target_monthly_cost} currency={currency} highlight />
      </Box>
      <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
        <Box>
          <Typography variant="caption" color="text.secondary">Economia condicionada / mês</Typography>
          <Typography variant="h6" sx={{ color: PEAK_SLOT_COLOR }}>+ {money(conditional.incremental_monthly_saving, currency)}</Typography>
          <Typography variant="caption" color="text.secondary">além da recomendação atual</Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">Total frente ao tamanho atual</Typography>
          <Typography variant="h6">{money(conditional.monthly_saving, currency)}</Typography>
          <Typography variant="caption" color="text.secondary">{sizeLabel(item.from_config)} → {sizeLabel(conditional.to_config)}</Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">P99,5 dos picos sem o job</Typography>
          <Typography variant="h6">{pct(conditional.cpu_without_recurring_peaks.max_p995 ?? null)}</Typography>
          <Typography variant="caption" color="text.secondary">{pct(conditional.projected_cpu_peak_p995)} no novo tamanho</Typography>
        </Box>
      </Stack>
      <Alert severity="warning" variant="outlined" sx={{ mt: 1.5 }}>
        Economia condicionada: só vale se o job for limitado ou reagendado. Não entra na economia projetada da página até ser aceita aqui.
      </Alert>

      <Typography variant="subtitle2" sx={{ mt: 2 }} gutterBottom>Ações sugeridas</Typography>
      <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 2.5 }}>
        {conditional.suggested_actions.map((line) => <Typography component="li" variant="body2" key={line}>{line}</Typography>)}
      </Stack>
      <Box component="pre" sx={{ mt: 1, p: 1.25, bgcolor: "action.hover", borderRadius: 1, fontSize: 12, overflowX: "auto", whiteSpace: "pre" }}>
        {`# limitar o serviço do job (systemd)\nsudo systemctl set-property <servico-do-job>.service CPUQuota=${conditional.suggested_cpu_quota_percent}%\n# ou rodar o cron com limite e prioridade baixa\nsystemd-run --scope -p CPUQuota=${conditional.suggested_cpu_quota_percent}% nice -n 10 <comando-do-job>`}
      </Box>
      <Typography variant="caption" color="text.secondary">
        Com ~{conditional.suggested_cap_vcpus.toLocaleString("pt-BR")} vCPU o job levaria até ~{conditional.estimated_job_minutes_capped.toLocaleString("pt-BR")} min por dia em vez de ~{minutes.toLocaleString("pt-BR")} min
        (o restante da carga usa ~{conditional.baseline_vcpus_p95.toLocaleString("pt-BR")} vCPU no P95).
      </Typography>

      <Typography variant="subtitle2" sx={{ mt: 2 }} gutterBottom>Por que</Typography>
      <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 2.5 }}>
        {conditional.rationale.map((line) => <Typography component="li" variant="body2" key={line}>{line}</Typography>)}
      </Stack>

      <Typography variant="subtitle2" sx={{ mt: 2 }} gutterBottom>Decisão sobre o ajuste do job</Typography>
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
      <TextField size="small" fullWidth multiline minRows={2} label="Observação (ex.: job, responsável, nova janela)" value={note} onChange={(event) => setNote(event.target.value)} />
      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
        <Button size="small" variant="contained" sx={{ bgcolor: PEAK_SLOT_COLOR }} disabled={busy || state === "ACCEPTED"} onClick={() => void transition("ACCEPTED")}>Aceitar ajuste do job</Button>
        <Button size="small" variant="contained" color="success" disabled={busy || state === "IMPLEMENTED"} onClick={() => void transition("IMPLEMENTED")}>Marcar como executado</Button>
        <Button size="small" color="inherit" disabled={busy || state === "DISMISSED"} onClick={() => void transition("DISMISSED")}>Descartar</Button>
        {state !== "NEW" && <Button size="small" disabled={busy} onClick={() => void transition("NEW")}>Reabrir</Button>}
      </Stack>
    </>}
  </Paper>;
}
