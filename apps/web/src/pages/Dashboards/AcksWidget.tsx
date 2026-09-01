import { useEffect, useState } from "react";
import {
  Alert, Box, Chip, CircularProgress, FormControl, InputLabel, MenuItem, Paper, Select, Stack, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Typography,
} from "@mui/material";
import { ForecastAck, ForecastDesvioItem, getForecastAccuracyDesvios, getForecastAccuracyMonths, getForecastAcks } from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const prettyMonth = (month: string) => `${MONTH_NAMES[Number(month.slice(4, 6)) - 1]}/${month.slice(2, 4)}`;

function previousMonth(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1; // 1-12
  const prev = month === 1 ? { y: year - 1, m: 12 } : { y: year, m: month - 1 };
  return `${prev.y}${String(prev.m).padStart(2, "0")}`;
}

function lastMonths(count: number): string[] {
  const out: string[] = [];
  const now = new Date();
  for (let i = 0; i < count; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    out.push(`${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`);
  }
  return out;
}

function severityColor(severity?: string): "info" | "warning" | "error" | "success" {
  switch ((severity || "").toLowerCase()) {
    case "perigo": return "warning";
    case "crítico":
    case "critico": return "error";
    case "sucesso": return "success";
    default: return "info";
  }
}

// Ordem dos highlights: Crítico > Perigo > Informação > Sucesso; empate por data (mais recente primeiro).
const SEVERITY_ORDER: Record<string, number> = {
  "crítico": 0, "critico": 0, "perigo": 1, "informação": 2, "informacao": 2, "sucesso": 3,
};

function DesvioRow({ item, ack }: { item: ForecastDesvioItem; ack: ForecastAck | undefined }) {
  const justified = Boolean(ack && ack.active !== false);
  return (
    <TableRow hover>
      <TableCell>
        <Typography variant="body2" fontWeight={600}>{item.name}</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", maxWidth: 240 }}>
          {item.path.join(" > ")}
        </Typography>
      </TableCell>
      <TableCell>
        {justified ? (
          <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
            <Chip size="small" color="info" label={ack?.category} variant="outlined" />
            <Typography variant="body2" sx={{ maxWidth: 300 }}>{ack?.reason}</Typography>
          </Stack>
        ) : (
          <Chip size="small" color="warning" label="Desvio não justificado" variant="outlined" />
        )}
      </TableCell>
      <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
      <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
      <TableCell align="right" sx={{ color: Number(item.variance || 0) >= 0 ? "#DC2626" : "#16A34A", fontWeight: 600 }}>
        {money.format(Number(item.variance || 0))}
      </TableCell>
    </TableRow>
  );
}

export default function AcksWidget({ department }: { department?: string }) {
  const [month, setMonth] = useState(previousMonth());
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [positive, setPositive] = useState<ForecastDesvioItem[]>([]);
  const [negative, setNegative] = useState<ForecastDesvioItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [desviosWarning, setDesviosWarning] = useState("");
  const options = lastMonths(13);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setDesviosWarning("");
    setPositive([]);
    setNegative([]);
    // Só acks ativos aparecem no dashboard — desativados ficam fora.
    getForecastAcks(month, department || undefined, true)
      .then(result => { if (!cancelled) setAcks(result.items); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    // Top 5 positivos/negativos dos desvios do mês: os sem ack ativo são
    // marcados como "Desvio não justificado" e aparecem junto aos justificados.
    getForecastAccuracyMonths(undefined, department || undefined, "all")
      .then(months => {
        if (cancelled) return;
        const run = months.find(item => item.target_month === month);
        if (!run) {
          if (!cancelled) setDesviosWarning(`Nenhum simulado de acurácia para ${prettyMonth(month)} — sem Top 5 de desvios.`);
          return;
        }
        return getForecastAccuracyDesvios(run.run_id, month, department || undefined)
          .then(desvios => {
            if (cancelled) return;
            setPositive(desvios.positive.slice(0, 5));
            setNegative(desvios.negative.slice(0, 5));
          })
          .catch(e => { if (!cancelled) setDesviosWarning(e instanceof Error ? e.message : String(e)); });
      })
      .catch(e => { if (!cancelled) setDesviosWarning(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [month, department]);

  const ackByPath = new Map<string, ForecastAck>();
  acks.forEach(ack => ackByPath.set(ack.department_path.join(" > "), ack));
  // Registros criados manualmente não possuem run_id (cadastro livre).
  const manualAcks = acks.filter(ack => ack.run_id == null);
  // Highlights ordenados: campo `ordem` (menor primeiro), depois severidade e data.
  const orderedManual = [...manualAcks].sort((a, b) => {
    const oa = Number(a.ordem ?? SEVERITY_ORDER[(a.severity || "").toLowerCase()] ?? 2);
    const ob = Number(b.ordem ?? SEVERITY_ORDER[(b.severity || "").toLowerCase()] ?? 2);
    if (oa !== ob) return oa - ob;
    const sa = SEVERITY_ORDER[(a.severity || "").toLowerCase()] ?? 2;
    const sb = SEVERITY_ORDER[(b.severity || "").toLowerCase()] ?? 2;
    if (sa !== sb) return sa - sb;
    const da = a.registered_at || a.created_at || "";
    const db = b.registered_at || b.created_at || "";
    return da < db ? 1 : da > db ? -1 : 0;
  });

  return (
    <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1, flexWrap: "wrap", gap: 1 }}>
        <Box>
          <Typography variant="h6">Motivos de desvio (Ack)</Typography>
          <Typography variant="body2" color="text.secondary">
            Desvios reconhecidos no forecast — padrão: mês anterior ao atual
            {department ? ` · filtrado por: ${department}` : ""}.
          </Typography>
        </Box>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel id="acks-month-label">Mês</InputLabel>
          <Select labelId="acks-month-label" label="Mês" value={month} onChange={e => setMonth(e.target.value)}>
            {options.map(m => <MenuItem key={m} value={m}>{prettyMonth(m)}{m === previousMonth() ? " (anterior)" : ""}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError("")}>{error}</Alert>}
      {desviosWarning && !error && <Alert severity="warning" sx={{ mb: 1 }} onClose={() => setDesviosWarning("")}>{desviosWarning}</Alert>}
      {loading ? (
        <Box sx={{ py: 3, display: "flex", justifyContent: "center" }}><CircularProgress size={24} /></Box>
      ) : (
        <>
          {/* Top 5 positivos (subestimou) */}
          {positive.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ color: "#DC2626", mb: 0.5 }}>
                Top 5 subestimou (gastou mais)
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Departamento</TableCell>
                      <TableCell>Motivo</TableCell>
                      <TableCell align="right">Projetado</TableCell>
                      <TableCell align="right">Realizado</TableCell>
                      <TableCell align="right">Delta (R$)</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {positive.map(item => (
                      <DesvioRow key={item.path.join(" > ")} item={item} ack={ackByPath.get(item.path.join(" > "))} />
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}
          {/* Top 5 negativos (superestimou) */}
          {negative.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" fontWeight={700} sx={{ color: "#16A34A", mb: 0.5 }}>
                Top 5 superestimou (gastou menos)
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Departamento</TableCell>
                      <TableCell>Motivo</TableCell>
                      <TableCell align="right">Projetado</TableCell>
                      <TableCell align="right">Realizado</TableCell>
                      <TableCell align="right">Delta (R$)</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {negative.map(item => (
                      <DesvioRow key={item.path.join(" > ")} item={item} ack={ackByPath.get(item.path.join(" > "))} />
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}
          {/* Highlights — registros criados manualmente (sem run_id), como alertas ordenados */}
          {orderedManual.length > 0 && (
            <Box>
              <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 0.5 }}>
                Highlights
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
                Registros criados manualmente · ordenados por severidade (Crítico → Sucesso)
              </Typography>
              <Stack spacing={1}>
                {orderedManual.map((ack, index) => (
                  <Alert key={ack.id} severity={severityColor(ack.severity)} sx={{ alignItems: "flex-start" }}>
                    <Typography variant="body2" fontWeight={700}>{index + 1}. {ack.department_name}</Typography>
                    <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.25 }}>
                      <Chip size="small" label={ack.severity || "Informação"} />
                      <Chip size="small" label={ack.category} variant="outlined" />
                    </Stack>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>{ack.department_path.join(" > ")}</Typography>
                    <Typography variant="body2" sx={{ mt: 0.5 }}>{ack.reason}</Typography>
                    {ack.registered_at && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                        {new Date(ack.registered_at).toLocaleDateString("pt-BR")}
                      </Typography>
                    )}
                  </Alert>
                ))}
              </Stack>
            </Box>
          )}
        </>
      )}
    </Paper>
  );
}
