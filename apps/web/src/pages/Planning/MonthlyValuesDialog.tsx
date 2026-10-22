import { Fragment, useState } from "react";
import {
  Alert, Box, Button, InputAdornment, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { colors, radius } from "../../theme/tokens";

const MONTH_NAMES = [
  "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
];
const QUARTERS = [
  { label: "1º trimestre", months: [0, 1, 2] },
  { label: "2º trimestre", months: [3, 4, 5] },
  { label: "3º trimestre", months: [6, 7, 8] },
  { label: "4º trimestre", months: [9, 10, 11] },
];
const ALL_MONTHS = MONTH_NAMES.map((_, index) => index);

export type MonthlyValuesDialogProps = {
  rowName: string;
  costCenter: string | null;
  year: number;
  currentValues: number[];
  onClose: () => void;
  onApply: (values: number[]) => void;
};

const roundCents = (value: number) => Math.round(value * 100) / 100 || 0;

const sumAt = (list: number[], indices: number[]) =>
  indices.reduce((total, index) => total + Number(list[index] ?? 0), 0);

const formatBRL = (value: number) =>
  roundCents(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

const formatAmountInput = (value: number) =>
  roundCents(value).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const formatVariation = (value: number) => `${roundCents(value) > 0 ? "+" : ""}${formatBRL(value)}`;

const formatPercent = (value: number | null) =>
  value === null
    ? "—"
    : `${value > 0 ? "+" : ""}${value.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;

// Aceita "1.234,56", "1234.56", "1.234" (milhar) e "-500". Retorna null quando o texto não é número.
const parseAmount = (text: string): number | null => {
  const compact = text.trim().replace(/\s/g, "").replace(/^R\$/, "").replace(/%$/, "");
  if (compact === "") return 0;
  let normalized = compact;
  if (compact.includes(",")) normalized = compact.replace(/\./g, "").replace(",", ".");
  else if (/^-?\d{1,3}(\.\d{3})+$/.test(compact)) normalized = compact.replace(/\./g, "");
  return /^-?\d+(\.\d+)?$/.test(normalized) ? Number(normalized) : null;
};

const variationColor = (value: number) => {
  if (roundCents(value) > 0) return colors.warning;
  if (roundCents(value) < 0) return colors.success;
  return colors.textSecondary;
};

function SummaryCard({ label, value, hint, emphasis, color }: {
  label: string;
  value: string;
  hint?: string;
  emphasis?: boolean;
  color?: string;
}) {
  return (
    <Box sx={{
      p: 1.5,
      borderRadius: `${radius.surface}px`,
      border: "1px solid",
      borderColor: emphasis ? colors.brand : colors.border,
      bgcolor: emphasis ? colors.brandTint : colors.paper,
    }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6" fontWeight={800} color={color ?? "text.primary"} sx={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </Box>
  );
}

export default function MonthlyValuesDialog({
  rowName,
  costCenter,
  year,
  currentValues,
  onClose,
  onApply,
}: MonthlyValuesDialogProps) {
  const [draft, setDraft] = useState<string[]>(() => ALL_MONTHS.map((index) => formatAmountInput(Number(currentValues[index] ?? 0))));
  const [totalInput, setTotalInput] = useState(() => formatAmountInput(sumAt(currentValues, ALL_MONTHS)));
  const [percentInput, setPercentInput] = useState("");

  const parsed = draft.map((text) => parseAmount(text));
  const values = parsed.map((value) => value ?? 0);
  const currentAt = (index: number) => Number(currentValues[index] ?? 0);
  const changed = values.map((value, index) => roundCents(value) !== roundCents(currentAt(index)));
  const changedCount = changed.filter(Boolean).length;
  const invalidCount = parsed.filter((value) => value === null).length;
  const negativeCount = values.filter((value, index) => parsed[index] !== null && value < 0).length;
  const canApply = changedCount > 0 && invalidCount === 0 && negativeCount === 0;
  const percentValue = parseAmount(percentInput);

  const originalTotal = sumAt(currentValues, ALL_MONTHS);
  const editedTotal = sumAt(values, ALL_MONTHS);
  const difference = editedTotal - originalTotal;
  const differencePct = originalTotal !== 0 ? (difference / originalTotal) * 100 : null;

  const setAll = (next: number[]) => setDraft(next.map((value) => formatAmountInput(value)));
  const updateMonth = (index: number, text: string) =>
    setDraft((previous) => previous.map((value, i) => (i === index ? text : value)));
  const formatMonth = (index: number) => {
    const value = parseAmount(draft[index]);
    if (value !== null) updateMonth(index, formatAmountInput(value));
  };

  const distributeEvenly = () => {
    const total = parseAmount(totalInput);
    if (total === null) return;
    // Trabalha em centavos para que a soma dos meses feche exatamente o total.
    const cents = Math.round(total * 100);
    const base = Math.floor(cents / 12);
    const extra = cents - base * 12;
    setAll(ALL_MONTHS.map((index) => (base + (index >= 12 - extra ? 1 : 0)) / 100));
  };
  const adjustByPercent = () => {
    if (percentValue === null) return;
    setAll(values.map((value) => value * (1 + percentValue / 100)));
  };
  const clearAll = () => setAll(ALL_MONTHS.map(() => 0));
  const restoreCurrent = () => setAll(ALL_MONTHS.map(currentAt));

  const apply = () => {
    if (canApply) onApply(values.map(roundCents));
  };

  const variationCells = (current: number, next: number, invalid: boolean) => {
    const diff = next - current;
    const pct = current !== 0 ? (diff / current) * 100 : null;
    return (
      <>
        <TableCell align="right" sx={{ color: invalid ? "text.secondary" : variationColor(diff), whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
          {invalid ? "—" : formatVariation(diff)}
        </TableCell>
        <TableCell align="right" sx={{ color: invalid ? "text.secondary" : variationColor(diff), whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
          {invalid ? "—" : formatPercent(pct)}
        </TableCell>
      </>
    );
  };

  const monthRow = (index: number) => {
    const invalid = parsed[index] === null;
    const negative = !invalid && values[index] < 0;
    return (
      <TableRow key={index} sx={{ bgcolor: changed[index] ? colors.brandTint : undefined }}>
        <TableCell sx={{ fontWeight: changed[index] ? 700 : 400 }}>{MONTH_NAMES[index]} {year}</TableCell>
        <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{formatBRL(currentAt(index))}</TableCell>
        <TableCell align="right" sx={{ minWidth: 150 }}>
          <TextField
            size="small"
            fullWidth
            value={draft[index]}
            error={invalid || negative}
            onChange={(event) => updateMonth(index, event.target.value)}
            onBlur={() => formatMonth(index)}
            slotProps={{
              htmlInput: {
                "aria-label": `Novo valor de ${MONTH_NAMES[index]}`,
                inputMode: "decimal",
                style: { textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 600 },
              },
            }}
          />
        </TableCell>
        {variationCells(currentAt(index), values[index], invalid)}
      </TableRow>
    );
  };

  const subtotalRow = (label: string, indices: number[], background: string) => {
    const current = sumAt(currentValues, indices);
    const next = sumAt(values, indices);
    return (
      <TableRow sx={{ bgcolor: background, "& .MuiTableCell-root": { fontWeight: 700 } }}>
        <TableCell>{label}</TableCell>
        <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{formatBRL(current)}</TableCell>
        <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{formatBRL(next)}</TableCell>
        {variationCells(current, next, invalidCount > 0)}
      </TableRow>
    );
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="body2" color="text.secondary">Editar valores mensais</Typography>
        <Typography variant="h6" fontWeight={800}>{rowName}</Typography>
        <Typography variant="body2" color="text.secondary">
          Altere somente os valores próprios desta linha. Os totais dos níveis superiores são recalculados automaticamente.
        </Typography>
      </DialogTitle>
      <DialogContent dividers sx={{ py: 2.5 }}>
        {(negativeCount > 0 || invalidCount > 0) && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {negativeCount > 0 && "Valores negativos não são aceitos. "}
            {invalidCount > 0 && "Há campos com valor inválido. "}
            Corrija os campos destacados para aplicar.
          </Alert>
        )}

        <Box sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "repeat(3, minmax(0, 1fr))" },
          gap: 1.5,
          mb: 2.5,
        }}>
          <SummaryCard label={`Total original · ${year}`} value={formatBRL(originalTotal)} />
          <SummaryCard label="Total editado" value={formatBRL(editedTotal)} emphasis color={colors.brand} />
          <SummaryCard
            label="Diferença"
            value={formatVariation(difference)}
            hint={formatPercent(differencePct)}
            color={variationColor(difference)}
          />
        </Box>

        <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "flex-end" }} sx={{ mb: 2.5 }}>
          <Stack direction="row" spacing={1} alignItems="flex-end" sx={{ flex: 1 }}>
            <TextField
              size="small"
              label="Total anual"
              value={totalInput}
              onChange={(event) => setTotalInput(event.target.value)}
              slotProps={{ input: { startAdornment: <InputAdornment position="start">R$</InputAdornment> } }}
              sx={{ flex: 1 }}
            />
            <Button variant="outlined" onClick={distributeEvenly} disabled={parseAmount(totalInput) === null}>
              Distribuir igualmente
            </Button>
          </Stack>
          <Stack direction="row" spacing={1} alignItems="flex-end" sx={{ flex: 1 }}>
            <TextField
              size="small"
              label="Ajuste"
              value={percentInput}
              onChange={(event) => setPercentInput(event.target.value)}
              placeholder="Ex.: 5 ou -3"
              slotProps={{ input: { endAdornment: <InputAdornment position="end">%</InputAdornment> } }}
              sx={{ flex: 1 }}
            />
            <Button variant="outlined" onClick={adjustByPercent} disabled={percentValue === null || percentInput.trim() === ""}>
              Aplicar %
            </Button>
          </Stack>
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" color="error" onClick={clearAll}>Zerar</Button>
            <Button variant="text" onClick={restoreCurrent} disabled={changedCount === 0}>Restaurar atuais</Button>
          </Stack>
        </Stack>

        <TableContainer>
          <Table size="small" aria-label={`Distribuição mensal de ${rowName} em ${year}`}>
            <TableHead>
              <TableRow sx={{ "& .MuiTableCell-root": { fontWeight: 700, color: colors.textSecondary, bgcolor: colors.tableHead } }}>
                <TableCell>Mês</TableCell>
                <TableCell align="right">Valor atual</TableCell>
                <TableCell align="right">Novo valor</TableCell>
                <TableCell align="right">Variação (R$)</TableCell>
                <TableCell align="right">Variação (%)</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {QUARTERS.map((quarter) => (
                <Fragment key={quarter.label}>
                  <TableRow>
                    <TableCell colSpan={5} sx={{ py: 0.75, color: colors.textSecondary, fontWeight: 700, bgcolor: colors.raised }}>
                      {quarter.label}
                    </TableCell>
                  </TableRow>
                  {quarter.months.map((index) => monthRow(index))}
                  {subtotalRow(`Subtotal ${quarter.label}`, quarter.months, colors.raised)}
                </Fragment>
              ))}
              {subtotalRow(`Total de ${year}`, ALL_MONTHS, colors.tableHead)}
            </TableBody>
          </Table>
        </TableContainer>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2, justifyContent: "space-between" }}>
        <Typography variant="body2" color="text.secondary">
          {changedCount === 0 ? "Nenhuma alteração" : `${changedCount} ${changedCount === 1 ? "mês alterado" : "meses alterados"}`}
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="contained" onClick={apply} disabled={!canApply}>
            Aplicar
          </Button>
        </Stack>
      </DialogActions>
    </Dialog>
  );
}
