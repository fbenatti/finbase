import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, FormControl, Grid, InputLabel, LinearProgress,
  MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, Typography,
} from "@mui/material";
import PictureAsPdfOutlined from "@mui/icons-material/PictureAsPdfOutlined";
import html2canvas from "html2canvas";
import jsPDF from "jspdf";
import { getFinancialContractSettlements, getFinancialContracts } from "../../services/api";
import { colors } from "../../theme/tokens";
import { dataDisplayText } from "../../utils/dataPresentation";

// Uso de compromissos: quanto do valor contratado já foi consumido, quanto resta e
// para onde o consumo caminha até o fim da vigência. O gráfico compara o acumulado
// real com o planejado (orçamento por competência) e com a projeção.
type Contract = { id: number; code: string; name: string; active: boolean; currency?: string; valid_from?: string; valid_until?: string; last_period?: string; run_id?: number; last_settlement_at?: string; status?: string };
type Settlement = Record<string, unknown> & { period?: string; cloud_scope?: string; budget_id?: string };
type Status = "over" | "risk-over" | "risk-under" | "on-track" | "empty";
type Summary = {
  contract: Contract; rows: Settlement[]; months: string[]; latest: string;
  cumPlanned: number[]; cumActual: (number | null)[];
  contracted: number; consumed: number; rate: number; projected: number; status: Status;
};

// Tolerâncias da projeção: acima de 105% do contratado vira risco de estouro;
// abaixo de 85% vira risco de sobra (valor comprometido que não será consumido).
const OVER_TOLERANCE = 1.05;
const UNDER_TOLERANCE = 0.85;
const STATUS: Record<Status, { label: string; color: string }> = {
  over: { label: "Estourado", color: colors.error },
  "risk-over": { label: "Risco de estouro", color: colors.accent },
  "risk-under": { label: "Risco de sobra", color: colors.warning },
  "on-track": { label: "No ritmo", color: colors.success },
  empty: { label: "Sem consumo", color: colors.textMuted },
};
const CARD_SX = { p: 2.25, borderRadius: "18px", borderColor: colors.border, boxShadow: "0 6px 20px rgba(24, 39, 75, 0.035)" };
const MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];

const brl = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
const brlCompact = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const pct = (value: number) => `${(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const numeric = (row: Settlement, key: string) => Number(row[key] ?? 0);
const sumOf = (rows: Settlement[], key: string) => rows.reduce((total, row) => total + numeric(row, key), 0);
const signedColor = (value: number) => value > 0 ? "success.main" : value < 0 ? "error.main" : "text.secondary";

// Competências chegam como AAAAMM; datas de vigência podem vir como AAAA-MM-DD.
const toPeriod = (value?: string | null) => {
  const digits = String(value ?? "").replace(/\D/g, "");
  return digits.length >= 6 ? digits.slice(0, 6) : "";
};
const periodLabel = (period: string) => {
  const month = Number(period.slice(4));
  if (!/^\d{6}$/.test(period) || month < 1 || month > 12) return period || "—";
  return `${MONTHS[month - 1]}/${period.slice(2, 4)}`;
};
const vigencia = (contract: Contract) => {
  const from = toPeriod(contract.valid_from);
  const until = toPeriod(contract.valid_until);
  if (!from && !until) return "—";
  return `${from ? periodLabel(from) : "—"} a ${until ? periodLabel(until) : "—"}`;
};

function monthsBetween(start: string, end: string) {
  const months: string[] = [];
  let year = Number(start.slice(0, 4));
  let month = Number(start.slice(4));
  while (months.length < 240) {
    const key = `${year}${String(month).padStart(2, "0")}`;
    if (key > end) break;
    months.push(key);
    month += 1;
    if (month > 12) { month = 1; year += 1; }
  }
  return months;
}

function summarize(contract: Contract, rows: Settlement[]): Summary {
  const withData = [...new Set(rows.map((row) => String(row.period ?? "")))].filter(Boolean).sort();
  const start = toPeriod(contract.valid_from) || withData[0] || "";
  const end = toPeriod(contract.valid_until) || withData[withData.length - 1] || "";
  const window = start && end && start <= end ? monthsBetween(start, end) : [];
  const months = [...new Set([...window, ...withData])].sort();
  const latest = withData[withData.length - 1] ?? "";

  const rowsIn = (month: string) => rows.filter((row) => row.period === month);
  let plannedTotal = 0;
  let actualTotal = 0;
  const cumPlanned = months.map((month) => {
    plannedTotal += sumOf(rowsIn(month), "commitment");
    return plannedTotal;
  });
  const cumActual = months.map((month) => {
    if (month > latest) return null;
    actualTotal += sumOf(rowsIn(month), "consumption");
    return actualTotal;
  });

  const contracted = plannedTotal;
  const consumed = sumOf(rows, "consumption");
  const recent = withData.slice(-3);
  const rate = recent.length ? recent.reduce((total, month) => total + sumOf(rowsIn(month), "consumption"), 0) / recent.length : 0;
  const remaining = months.filter((month) => month > latest).length;
  const projected = consumed + rate * remaining;
  const projectedRatio = contracted ? projected / contracted : 0;
  const status: Status = !rows.length || !contracted ? "empty"
    : consumed > contracted ? "over"
    : projectedRatio > OVER_TOLERANCE ? "risk-over"
    : projectedRatio < UNDER_TOLERANCE ? "risk-under"
    : "on-track";

  return { contract, rows, months, latest, cumPlanned, cumActual, contracted, consumed, rate, projected, status };
}

function KpiCard({ label, value, detail, ratio, color }: { label: string; value: number; detail: string; ratio: number; color: string }) {
  return <Paper variant="outlined" sx={{ ...CARD_SX, height: "100%" }}>
    <Typography variant="overline" color="text.secondary" fontWeight={700}>{label}</Typography>
    <Typography variant="h4" fontWeight={800} color={colors.text} title={brl(value)} sx={{ lineHeight: 1.1, mt: 0.5 }}>{brlCompact(value)}</Typography>
    <LinearProgress variant="determinate" value={Math.min(Math.max(ratio, 0), 1) * 100} sx={{ my: 1.25, height: 6, borderRadius: 3, bgcolor: colors.tableHead, "& .MuiLinearProgress-bar": { bgcolor: color, borderRadius: 3 } }} />
    <Typography variant="caption" color="text.secondary">{detail}</Typography>
  </Paper>;
}

function BurnDownChart({ summary }: { summary: Summary }) {
  const { months, latest, cumPlanned, cumActual, contracted, consumed, projected } = summary;
  if (!months.length || !contracted) {
    return <Typography color="text.secondary" sx={{ py: 6, textAlign: "center" }}>Sem valor contratado registrado para montar o gráfico.</Typography>;
  }
  const W = 800;
  const H = 300;
  const L = 76;
  const R = 24;
  const T = 16;
  const B = 36;
  const last = months.length - 1;
  const latestIndex = Math.max(months.indexOf(latest), 0);
  const top = Math.max(contracted, projected, consumed) * 1.08;
  const x = (index: number) => L + (last === 0 ? 0.5 : index / last) * (W - L - R);
  const y = (value: number) => T + (1 - value / top) * (H - T - B);
  const plannedLine = cumPlanned.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const actualPoints = cumActual.flatMap((value, index) => value === null ? [] : [`${x(index)},${y(value)}`]);
  const actualLine = actualPoints.join(" ");
  const actualArea = `${actualLine} ${x(latestIndex)},${y(0)} ${x(0)},${y(0)}`;
  const projectionLine = `${x(latestIndex)},${y(consumed)} ${x(last)},${y(projected)}`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => fraction * top);
  const labelStep = Math.max(1, Math.ceil(months.length / 8));

  return <Box>
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Consumo acumulado contra o planejado e a projeção" style={{ width: "100%", height: "auto", display: "block" }}>
      {ticks.map((value) => <g key={value}>
        <line x1={L} x2={W - R} y1={y(value)} y2={y(value)} stroke={colors.border} />
        <text x={L - 8} y={y(value) + 4} textAnchor="end" fontSize={11} fill={colors.textMuted}>{brlCompact(value)}</text>
      </g>)}
      {months.map((month, index) => index % labelStep === 0 && <text key={month} x={x(index)} y={H - 12} textAnchor="middle" fontSize={11} fill={colors.textMuted}>{periodLabel(month)}</text>)}
      {latest && <line x1={x(latestIndex)} x2={x(latestIndex)} y1={T} y2={H - B} stroke={colors.borderStrong} strokeDasharray="4 4" />}
      <polygon points={actualArea} fill={colors.brand} fillOpacity={0.08} />
      <polyline points={plannedLine} fill="none" stroke={colors.textSecondary} strokeWidth={2} strokeDasharray="6 5" />
      <polyline points={projectionLine} fill="none" stroke={colors.accent} strokeWidth={2.5} strokeDasharray="2 5" strokeLinecap="round" />
      <polyline points={actualLine} fill="none" stroke={colors.brand} strokeWidth={3} strokeLinejoin="round" />
      {latest && <circle cx={x(latestIndex)} cy={y(consumed)} r={4.5} fill={colors.brand} />}
      <circle cx={x(last)} cy={y(projected)} r={4.5} fill={colors.accent} />
      <text x={x(last)} y={y(contracted) - 10} textAnchor="end" fontSize={12} fontWeight={700} fill={colors.textSecondary}>Contratado {brlCompact(contracted)}</text>
      <text x={x(last)} y={y(projected) + (projected >= contracted ? -10 : 18)} textAnchor="end" fontSize={12} fontWeight={700} fill={colors.accentDark}>Projeção {brlCompact(projected)}</text>
    </svg>
    <Stack direction="row" spacing={2.5} flexWrap="wrap" sx={{ mt: 1.5 }}>
      <LegendItem label="Consumido acumulado" color={colors.brand} />
      <LegendItem label="Previsto (meta)" color={colors.textSecondary} dashed />
      <LegendItem label="Projeção até o fim" color={colors.accent} dashed />
    </Stack>
  </Box>;
}

function LegendItem({ label, color, dashed }: { label: string; color: string; dashed?: boolean }) {
  return <Stack direction="row" alignItems="center" spacing={1}>
    <Box sx={{ width: 22, borderTop: `3px ${dashed ? "dashed" : "solid"} ${color}` }} />
    <Typography variant="caption" color="text.secondary">{label}</Typography>
  </Stack>;
}

export default function FinancialContractsDashboard() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [rowsById, setRowsById] = useState<Record<number, Settlement[]>>({});
  const [contractId, setContractId] = useState("");
  const [period, setPeriod] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const exportRootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    getFinancialContracts()
      .then(async (items) => {
        const normalized = items as Contract[];
        const entries = await Promise.all(normalized.map((contract) =>
          getFinancialContractSettlements(contract.id).then((rows) => [contract.id, rows as Settlement[]] as const)));
        if (!active) return;
        setContracts(normalized);
        setRowsById(Object.fromEntries(entries) as Record<number, Settlement[]>);
        // Abre sempre com um contrato selecionado para não mostrar zeros enganosos.
        if (normalized[0]) setContractId(String(normalized[0].id));
      })
      .catch((reason) => active && setError(String(reason)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  const summaries = useMemo(() => contracts.map((contract) => summarize(contract, rowsById[contract.id] ?? [])), [contracts, rowsById]);
  const summary = summaries.find((item) => String(item.contract.id) === contractId);
  const settlements = useMemo(() => summary?.rows ?? [], [summary]);
  const periods = useMemo(() => [...new Set(settlements.map((row) => String(row.period ?? "")))].filter(Boolean).sort(), [settlements]);
  useEffect(() => { if (period !== "all" && !periods.includes(period)) setPeriod("all"); }, [period, periods]);

  const selectedRows = useMemo(() => period === "all" ? settlements : settlements.filter((row) => row.period === period), [period, settlements]);
  const latestPeriod = periods[periods.length - 1] ?? "";
  const latestRows = settlements.filter((row) => row.period === latestPeriod);
  const balanceRows = period === "all" ? latestRows : selectedRows;
  const monthly = useMemo(() => periods.map((month) => {
    const rows = settlements.filter((row) => row.period === month);
    return { month, budget: sumOf(rows, "commitment"), consumption: sumOf(rows, "consumption"), realized: sumOf(rows, "realized"), adjustment: sumOf(rows, "contractual_adjustment"), credit: sumOf(rows, "closing_credit"), overage: sumOf(rows, "closing_overage"), settledOverage: sumOf(rows, "overage_resolved") };
  }), [periods, settlements]);
  const visibleMonthly = monthly.filter((item) => period === "all" || item.month === period);
  const atRisk = summaries.filter((item) => item.status === "over" || item.status === "risk-over" || item.status === "risk-under");
  const windowLabel = summary?.months.length ? `${periodLabel(summary.months[0])} a ${periodLabel(summary.months[summary.months.length - 1])}` : "sem período";

  const selectContract = (id: number) => {
    setContractId(String(id));
    setPeriod("all");
  };

  const exportPdf = async () => {
    if (!exportRootRef.current || exporting) return;
    setExporting(true);
    try {
      const canvas = await html2canvas(exportRootRef.current, {
        scale: 1.5,
        backgroundColor: colors.canvas,
        useCORS: true,
        onclone: (documentClone) => {
          documentClone.querySelectorAll<HTMLElement>("[data-pdf-hide]").forEach((element) => { element.style.display = "none"; });
          documentClone.querySelectorAll<HTMLElement>(".MuiTableContainer-root").forEach((element) => { element.style.maxHeight = "none"; element.style.overflow = "visible"; });
        },
      });
      const pdf = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4", compress: true });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 5;
      const ratio = Math.min((pageWidth - margin * 2) / canvas.width, (pageHeight - margin * 2) / canvas.height);
      pdf.addImage(canvas.toDataURL("image/jpeg", 0.9), "JPEG", margin, margin, canvas.width * ratio, canvas.height * ratio);
      pdf.save(`uso-compromissos-${contractId || "contratos"}.pdf`);
    } finally {
      setExporting(false);
    }
  };

  const share = (value: number) => summary?.contracted ? value / summary.contracted : 0;
  const kpis = summary ? (() => {
    const balance = summary.contracted - summary.consumed;
    const gap = summary.projected - summary.contracted;
    const gapLabel = summary.status === "over" || summary.status === "risk-over" ? "Estouro previsto"
      : summary.status === "risk-under" ? "Sobra prevista" : "Desvio previsto";
    return [
      { label: "Valor contratado", value: summary.contracted, detail: `${summary.months.length} competências · ${windowLabel}`, ratio: 1, color: colors.brand },
      { label: "Consumido", value: summary.consumed, detail: `${pct(share(summary.consumed))} do contratado`, ratio: share(summary.consumed), color: colors.info },
      { label: "Saldo a consumir", value: balance, detail: balance < 0 ? "Consumo acima do contratado" : `${pct(share(balance))} ainda disponível`, ratio: share(balance), color: colors.brandLight },
      { label: "Projeção até o fim", value: summary.projected, detail: `Média de ${brlCompact(summary.rate)} por mês nos últimos 3 meses`, ratio: share(summary.projected), color: colors.brandDark },
      { label: gapLabel, value: Math.abs(gap), detail: `${pct(share(Math.abs(gap)))} do contratado`, ratio: share(Math.abs(gap)), color: gap > 0 ? colors.accent : colors.warning },
    ];
  })() : [];

  return <Box ref={exportRootRef} sx={{ p: { xs: 2, md: 4 }, bgcolor: colors.canvas, minHeight: "calc(100vh - 64px)" }}>
    <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} spacing={2} sx={{ mb: 3 }}>
      <Box>
        <Typography variant="overline" color="text.secondary" fontWeight={700}>Meta e previsão · Uso de compromissos</Typography>
        <Typography variant="h4" fontWeight={800} color={colors.text}>Uso de compromissos</Typography>
        <Typography color="text.secondary" sx={{ mt: 0.5 }}>Quanto do valor contratado já foi consumido, quanto resta e para onde o consumo caminha até o fim da vigência.</Typography>
        {summary && <Chip size="small" label={`Período ${windowLabel}`} sx={{ mt: 1.5, fontWeight: 700, bgcolor: colors.brandTint, color: colors.brandDark }} />}
      </Box>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }}>
        <FormControl size="small" sx={{ minWidth: 260 }} disabled={!contracts.length}>
          <InputLabel id="commitment-contract-label">Contrato</InputLabel>
          <Select labelId="commitment-contract-label" label="Contrato" value={contractId} onChange={(event) => selectContract(Number(event.target.value))}>
            {contracts.map((item) => <MenuItem key={item.id} value={String(item.id)}>{item.name}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 150 }} disabled={!periods.length}>
          <InputLabel>Competência</InputLabel>
          <Select value={period} label="Competência" onChange={(event) => setPeriod(event.target.value)}>
            <MenuItem value="all">Todas</MenuItem>{periods.map((item) => <MenuItem key={item} value={item}>{periodLabel(item)}</MenuItem>)}
          </Select>
        </FormControl>
        <Button data-pdf-hide variant="contained" startIcon={<PictureAsPdfOutlined />} onClick={() => void exportPdf()} disabled={exporting} sx={{ whiteSpace: "nowrap" }}>{exporting ? "Gerando PDF..." : "Exportar PDF"}</Button>
      </Stack>
    </Stack>

    {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
    {loading && <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}><CircularProgress size={18} /><Typography variant="body2" color="text.secondary">Carregando consumo dos compromissos...</Typography></Stack>}
    {!loading && atRisk.length > 0 && <Alert severity="warning" sx={{ mb: 2 }}>
      {atRisk.length === 1 ? "Um contrato está fora do ritmo previsto" : `${atRisk.length} contratos estão fora do ritmo previsto`}: {atRisk.map((item) => item.contract.name).join(", ")}.
    </Alert>}

    <Grid container spacing={2} sx={{ mb: 2 }}>
      {kpis.map((item) => <Grid item xs={12} sm={6} lg={2.4} key={item.label}><KpiCard {...item} /></Grid>)}
    </Grid>

    <Grid container spacing={2} sx={{ mb: 2 }}>
      <Grid item xs={12} lg={8}><Paper variant="outlined" sx={{ ...CARD_SX, height: "100%" }}>
        <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Consumo acumulado × planejado</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Efetivado acumulado contra a meta por competência e a projeção até o fim da vigência · valores em BRL</Typography>
        {summary ? <BurnDownChart summary={summary} /> : <Typography color="text.secondary" sx={{ py: 6, textAlign: "center" }}>Nenhum contrato selecionado.</Typography>}
      </Paper></Grid>

      <Grid item xs={12} lg={4}><Paper variant="outlined" sx={{ ...CARD_SX, height: "100%" }}>
        <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Compensação e saldos</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>{period === "all" ? "Todas as competências do contrato" : `Competência ${periodLabel(period)}`}</Typography>
        <Stack divider={<Box sx={{ borderTop: `1px solid ${colors.border}` }} />} spacing={1.25}>
          {[
            { label: "Efetivado contratual", value: sumOf(selectedRows, "realized"), hint: "Valor reconhecido no período" },
            { label: "Ajuste de contrato", value: sumOf(selectedRows, "contractual_adjustment"), hint: "Crédito (+) / excedente bruto (−)" },
            { label: "Crédito apurado", value: sumOf(selectedRows, "credit_generated"), hint: "Gerado nas competências selecionadas" },
            { label: "Crédito utilizado", value: sumOf(selectedRows, "credit_used"), hint: "Compensação de excedente" },
            { label: "Crédito disponível", value: sumOf(latestRows, "closing_credit"), hint: `Saldo final · ${latestPeriod ? periodLabel(latestPeriod) : "sem dados"}` },
            { label: "Excedente em aberto", value: sumOf(balanceRows, "closing_overage"), hint: period === "all" ? `Saldo final · ${latestPeriod ? periodLabel(latestPeriod) : "sem dados"}` : `Saldo · ${periodLabel(period)}` },
          ].map((item) => <Stack key={item.label} direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" fontWeight={600} color={colors.text}>{item.label}</Typography>
              <Typography variant="caption" color="text.secondary">{item.hint}</Typography>
            </Box>
            <Typography variant="body1" fontWeight={700} sx={{ color: signedColor(item.value), whiteSpace: "nowrap" }}>{brl(item.value)}</Typography>
          </Stack>)}
        </Stack>
      </Paper></Grid>
    </Grid>

    <Paper variant="outlined" sx={{ ...CARD_SX, mb: 2 }}>
      <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Contratos</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>Clique em um contrato para detalhar as competências abaixo.</Typography>
      <TableContainer>
        <Table size="small">
          <TableHead><TableRow>
            {["Contrato", "Vigência", "Contratado", "Consumido", "Consumo", "Projeção até o fim", "Situação"].map((label) => <TableCell key={label} align={["Contratado", "Consumido", "Projeção até o fim"].includes(label) ? "right" : "left"} sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{label}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>{summaries.map((item) => {
            const ratio = item.contracted ? item.consumed / item.contracted : 0;
            const status = STATUS[item.status];
            return <TableRow key={item.contract.id} hover selected={String(item.contract.id) === contractId} onClick={() => selectContract(item.contract.id)} sx={{ cursor: "pointer" }}>
              <TableCell sx={{ fontWeight: 600 }}>{item.contract.name}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{vigencia(item.contract)}</TableCell>
              <TableCell align="right">{brl(item.contracted)}</TableCell>
              <TableCell align="right">{brl(item.consumed)}</TableCell>
              <TableCell sx={{ minWidth: 180 }}><Stack direction="row" alignItems="center" spacing={1.25}>
                <LinearProgress variant="determinate" value={Math.min(ratio, 1) * 100} sx={{ flex: 1, height: 8, borderRadius: 4, bgcolor: colors.tableHead, "& .MuiLinearProgress-bar": { bgcolor: status.color, borderRadius: 4 } }} />
                <Typography variant="caption" fontWeight={700} sx={{ width: 52, textAlign: "right" }}>{pct(ratio)}</Typography>
              </Stack></TableCell>
              <TableCell align="right">{brl(item.projected)}</TableCell>
              <TableCell><Chip size="small" variant="outlined" label={status.label} sx={{ fontWeight: 700, color: status.color, borderColor: status.color }} /></TableCell>
            </TableRow>;
          })}</TableBody>
        </Table>
      </TableContainer>
      {!summaries.length && !loading && <Typography color="text.secondary" sx={{ py: 3 }}>Nenhum contrato de compromisso cadastrado.</Typography>}
    </Paper>

    <Paper variant="outlined" sx={{ ...CARD_SX, mb: 2 }}>
      <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Acerto mensal</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>{summary ? summary.contract.name : "Contrato não selecionado"}</Typography>
      <TableContainer sx={{ maxHeight: 420 }}>
        <Table size="small" stickyHeader>
          <TableHead><TableRow>
            {["Competência", "Orçamento", "Consumo", "Efetivado", "Ajuste", "Crédito residual", "Excedente acumulado", "Excedente abatido/acertado"].map((label) => <TableCell key={label} align={label === "Competência" ? "left" : "right"} sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{label}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>{visibleMonthly.map((item) => <TableRow key={item.month} hover>
            <TableCell sx={{ fontWeight: 600 }}>{periodLabel(item.month)}</TableCell>
            <TableCell align="right">{brl(item.budget)}</TableCell>
            <TableCell align="right">{brl(item.consumption)}</TableCell>
            <TableCell align="right">{brl(item.realized)}</TableCell>
            <TableCell align="right" sx={{ color: signedColor(item.adjustment) }}>{brl(item.adjustment)}</TableCell>
            <TableCell align="right" sx={{ color: item.credit > 0 ? "success.main" : "text.secondary" }}>{brl(item.credit)}</TableCell>
            <TableCell align="right" sx={{ color: item.overage < 0 ? "error.main" : "text.secondary" }}>{brl(item.overage)}</TableCell>
            <TableCell align="right" sx={{ color: item.settledOverage < 0 ? "error.main" : "text.secondary" }}>{brl(item.settledOverage)}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </TableContainer>
      {!monthly.length && !loading && <Typography color="text.secondary" sx={{ py: 3 }}>Nenhum acerto mensal publicado para este contrato.</Typography>}
    </Paper>

    <Paper variant="outlined" sx={CARD_SX}>
      <Typography variant="subtitle1" fontWeight={800} color={colors.text}>Detalhamento por nuvem e código de orçamento</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>Competência, nuvem e código de orçamento · execução vigente</Typography>
      <TableContainer sx={{ maxHeight: 460 }}>
        <Table size="small" stickyHeader>
          <TableHead><TableRow>
            {["Competência", "Nuvem", "Código do orçamento", "Orçamento", "Consumo", "Efetivado", "Ajuste", "Crédito +", "Crédito utilizado", "Excedente acumulado", "Excedente abatido/acertado", "Crédito residual", "Execução · origem", "Versões do orçamento"].map((label) => <TableCell key={label} sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{label}</TableCell>)}
          </TableRow></TableHead>
          <TableBody>{selectedRows.map((row, index) => <TableRow key={`${row.period}-${row.cloud_scope}-${row.budget_id}-${index}`} hover>
            <TableCell>{periodLabel(String(row.period ?? "—"))}</TableCell>
            <TableCell>{String(row.cloud_scope ?? "—")}</TableCell>
            <TableCell>{String(row.budget_id ?? "—")}</TableCell>
            <TableCell>{brl(numeric(row, "commitment"))}</TableCell>
            <TableCell>{brl(numeric(row, "consumption"))}</TableCell>
            <TableCell>{brl(numeric(row, "realized"))}</TableCell>
            <TableCell sx={{ color: signedColor(numeric(row, "contractual_adjustment")) }}>{brl(numeric(row, "contractual_adjustment"))}</TableCell>
            <TableCell sx={{ color: numeric(row, "credit_generated") > 0 ? "success.main" : "text.secondary" }}>{brl(numeric(row, "credit_generated"))}</TableCell>
            <TableCell>{brl(numeric(row, "credit_used"))}</TableCell>
            <TableCell sx={{ color: numeric(row, "closing_overage") < 0 ? "error.main" : "text.secondary" }}>{brl(numeric(row, "closing_overage"))}</TableCell>
            <TableCell sx={{ color: numeric(row, "overage_resolved") < 0 ? "error.main" : "text.secondary" }}>{brl(numeric(row, "overage_resolved"))}</TableCell>
            <TableCell sx={{ color: numeric(row, "closing_credit") > 0 ? "success.main" : "text.secondary" }}>{brl(numeric(row, "closing_credit"))}</TableCell>
            <TableCell>{String(row.run_id ?? "—")} · {String(row.run_source ?? "—")}</TableCell>
            <TableCell>{Array.isArray(row.budget_version_ids) ? row.budget_version_ids.map(String).join(", ") : "—"}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </TableContainer>
      {!selectedRows.length && !loading && <Typography color="text.secondary" sx={{ py: 3 }}>Nenhuma linha atende à competência selecionada.</Typography>}
    </Paper>
  </Box>;
}
