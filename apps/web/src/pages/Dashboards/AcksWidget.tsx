import { useEffect, useState } from "react";
import {
  Alert, Box, Chip, CircularProgress, FormControl, InputLabel, MenuItem, Paper, Select, Stack, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Typography,
} from "@mui/material";
import { ForecastAck, getForecastAcks } from "../../services/api";

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

export default function AcksWidget({ department }: { department?: string }) {
  const [month, setMonth] = useState(previousMonth());
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const options = lastMonths(13);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getForecastAcks(month, department || undefined)
      .then(result => { if (!cancelled) setAcks(result.items); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [month, department]);

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
      {loading ? (
        <Box sx={{ py: 3, display: "flex", justifyContent: "center" }}><CircularProgress size={24} /></Box>
      ) : acks.length === 0 ? (
        <Typography variant="body2" color="text.secondary">Nenhum Ack registrado para {prettyMonth(month)}.</Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Departamento</TableCell>
                <TableCell>Motivo</TableCell>
                <TableCell align="center">Categoria</TableCell>
                <TableCell align="right">Projetado</TableCell>
                <TableCell align="right">Realizado</TableCell>
                <TableCell align="right">Delta</TableCell>
                <TableCell align="right">Ack em</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {acks.map(ack => (
                <TableRow key={ack.id} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>{ack.department_name}</Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ display: "block", maxWidth: 260 }}>
                      {ack.department_path.join(" > ")}
                    </Typography>
                  </TableCell>
                  <TableCell><Typography variant="body2">{ack.reason}</Typography></TableCell>
                  <TableCell align="center"><Chip size="small" label={ack.category} variant="outlined" /></TableCell>
                  <TableCell align="right">{money.format(Number(ack.forecast_value || 0))}</TableCell>
                  <TableCell align="right">{money.format(Number(ack.actual_value || 0))}</TableCell>
                  <TableCell align="right" sx={{ color: Number(ack.variance || 0) >= 0 ? "#DC2626" : "#16A34A", fontWeight: 600 }}>
                    {money.format(Number(ack.variance || 0))}
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="caption" color="text.secondary">
                      {ack.created_at ? new Date(ack.created_at).toLocaleDateString("pt-BR") : "—"}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
}
