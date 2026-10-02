import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Card, CardContent, CircularProgress, FormControl, Grid, InputLabel,
  MenuItem, Select, Stack, Tab, Tabs, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { getFinancialContractSettlements, getFinancialContracts, getFinancialContractsOverview } from "../../services/api";

type Contract = { id: number; code: string; name: string; active: boolean; currency?: string; valid_from?: string; valid_until?: string; last_period?: string; run_id?: number; last_settlement_at?: string; status?: string };
type Settlement = Record<string, unknown> & { period?: string; cloud_scope?: string; budget_id?: string };
const amount = (value: unknown) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value ?? 0));
const numeric = (row: Settlement, key: string) => Number(row[key] ?? 0);
const periodLabel = (period: string) => period.length === 6 ? `${period.slice(4)}/${period.slice(0, 4)}` : period;
const dateLabel = (value?: string) => {
  if (!value) return "";
  const normalized = /^\d{4}-\d{2}$/.test(value) ? `${value}-01` : value;
  const date = new Date(`${normalized.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("pt-BR");
};

function Metric({ label, value, detail, accent }: { label: string; value: string; detail?: string; accent: string }) {
  return <Card variant="outlined" sx={{ height: "100%", borderRadius: 3, borderTop: `3px solid ${accent}` }}><CardContent>
    <Typography color="text.secondary" variant="body2">{label}</Typography>
    <Typography variant="h5" fontWeight={700} sx={{ mt: 1 }}>{value}</Typography>
    {detail && <Typography variant="caption" color="text.secondary">{detail}</Typography>}
  </CardContent></Card>;
}

export default function FinancialContractsDashboard() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [overview, setOverview] = useState<Record<string, unknown>[]>([]);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [contractId, setContractId] = useState("");
  const [period, setPeriod] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([getFinancialContracts(), getFinancialContractsOverview()])
      .then(([items, summaries]) => {
        if (!active) return;
        const normalized = items as Contract[];
        setContracts(normalized);
        setOverview(summaries);
        // The overview is intentionally aggregated and may not expose the
        // run status. Select the first available contract so the dashboard
        // never opens with an empty contract filter and misleading zeroes.
        const firstAvailable = normalized[0];
        if (firstAvailable) setContractId(String(firstAvailable.id));
      })
      .catch((reason) => active && setError(String(reason)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    if (!contractId) { setSettlements([]); return () => { active = false; }; }
    setLoading(true);
    getFinancialContractSettlements(Number(contractId))
      .then((items) => active && setSettlements(items as Settlement[]))
      .catch((reason) => active && setError(String(reason)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [contractId]);

  const periods = useMemo(() => [...new Set(settlements.map((row) => String(row.period ?? "")))].filter(Boolean).sort(), [settlements]);
  useEffect(() => { if (period !== "all" && !periods.includes(period)) setPeriod("all"); }, [period, periods]);
  const selectedRows = useMemo(() => period === "all" ? settlements : settlements.filter((row) => row.period === period), [period, settlements]);
  const latestPeriod = periods[periods.length - 1] ?? "";
  const latestRows = settlements.filter((row) => row.period === latestPeriod);
  const balanceRows = period === "all" ? latestRows : selectedRows;
  const sum = (rows: Settlement[], key: string) => rows.reduce((total, row) => total + numeric(row, key), 0);
  const monthly = useMemo(() => periods.map((month) => {
    const rows = settlements.filter((row) => row.period === month);
    return { month, budget: sum(rows, "commitment"), consumption: sum(rows, "consumption"), realized: sum(rows, "realized"), adjustment: sum(rows, "contractual_adjustment"), credit: sum(rows, "closing_credit"), overage: sum(rows, "closing_overage"), settledOverage: sum(rows, "overage_resolved") };
  }), [periods, settlements]);
  const chartMonths = monthly.slice(-12);
  const chartMax = Math.max(1, ...chartMonths.flatMap((item) => [item.budget, item.consumption]));
  const currentContract = contracts.find((item) => String(item.id) === contractId);
  const currentSummary = overview.find((item) => String(item.contract_id) === contractId);

  return <Box sx={{ p: { xs: 2, md: 4 }, bgcolor: "#f5f7fb", minHeight: "calc(100vh - 64px)" }}>
    <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} spacing={2} sx={{ mb: 3 }}>
      <Box><Typography variant="h4" fontWeight={750}>Histórico de consumo de compromissos contratuais</Typography>
        <Typography color="text.secondary" sx={{ mt: 0.5 }}>Acompanhe orçamento, consumo e liquidação mês a mês.</Typography></Box>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ minWidth: { sm: 380 } }}>
        <FormControl size="small" sx={{ minWidth: 150 }} disabled={!periods.length}><InputLabel>Competência</InputLabel><Select value={period} label="Competência" onChange={(event) => setPeriod(event.target.value)}>
          <MenuItem value="all">Todas</MenuItem>{periods.map((item) => <MenuItem key={item} value={item}>{periodLabel(item)}</MenuItem>)}
        </Select></FormControl>
      </Stack>
    </Stack>

    <Tabs value={contractId} onChange={(_, value) => { setContractId(value); setPeriod("all"); }} variant="scrollable" scrollButtons="auto" sx={{ mb: 3 }} aria-label="Contratos">{contracts.map((item) => <Tab key={item.id} value={String(item.id)} label={item.name} />)}</Tabs>

    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    {loading && <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}><CircularProgress size={18} /><Typography variant="body2" color="text.secondary">Carregando liquidações...</Typography></Stack>}
    {currentContract && <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
      <Typography variant="body2" fontWeight={600}>{currentContract.active ? "Contrato ativo" : "Contrato inativo"}</Typography>
      <Typography variant="body2" color="text.secondary">· Vigência {dateLabel(currentContract.valid_from) || "—"}{currentContract.valid_until ? ` – ${dateLabel(currentContract.valid_until)}` : " em diante"}</Typography>
      <Typography variant="body2" color="text.secondary">· Run {String(currentSummary?.run_id ?? "—")} · {currentSummary?.last_settlement_at ? new Date(String(currentSummary.last_settlement_at)).toLocaleString("pt-BR") : "sem liquidação"}</Typography>
    </Stack>}

    <Grid container spacing={2} sx={{ mb: 3 }}>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Orçamento do período" value={amount(sum(selectedRows, "commitment"))} detail={period === "all" ? "Soma das competências exibidas" : periodLabel(period)} accent="#2563eb" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Consumo realizado" value={amount(sum(selectedRows, "consumption"))} detail="Consumo apurado na Silver" accent="#0891b2" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Realizado contratual" value={amount(sum(selectedRows, "realized"))} detail="Valor reconhecido no pool" accent="#7c3aed" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Saldo de crédito" value={amount(sum(latestRows, "closing_credit"))} detail={`Saldo final · ${latestPeriod ? periodLabel(latestPeriod) : "sem dados"}`} accent="#059669" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Ajuste contratual" value={amount(sum(selectedRows, "contractual_adjustment"))} detail="Crédito (+) / overage bruto (−)" accent="#d97706" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Crédito gerado" value={amount(sum(selectedRows, "credit_generated"))} detail="Competências selecionadas" accent="#16a34a" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Crédito consumido" value={amount(sum(selectedRows, "credit_used"))} detail="Compensação de overage" accent="#4f46e5" /></Grid>
      <Grid item xs={12} sm={6} lg={3}><Metric label="Saldo de overage" value={amount(sum(balanceRows, "closing_overage"))} detail={period === "all" ? `Saldo final · ${latestPeriod ? periodLabel(latestPeriod) : "sem dados"}` : `Saldo · ${periodLabel(period)}`} accent="#dc2626" /></Grid>
    </Grid>

    <Grid container spacing={2}>
      <Grid item xs={12} lg={5}><Card variant="outlined" sx={{ height: "100%", borderRadius: 3 }}><CardContent>
        <Typography variant="h6" fontWeight={700}>Orçamento × consumo</Typography><Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Últimas 12 competências · valores em {currentContract?.currency ?? "BRL"}</Typography>
        {!chartMonths.length ? <Typography color="text.secondary" sx={{ py: 4 }}>Sem dados mensais para exibir.</Typography> : <Stack spacing={1.2}>
          {chartMonths.map((item) => <Box key={item.month} sx={{ display: "grid", gridTemplateColumns: "48px 1fr", gap: 1, alignItems: "center" }}>
            <Typography variant="caption" color="text.secondary">{periodLabel(item.month)}</Typography><Stack spacing={0.5}>
              <Box title={`Orçamento ${amount(item.budget)}`} sx={{ height: 9, width: `${Math.max(1, item.budget / chartMax * 100)}%`, bgcolor: "#93c5fd", borderRadius: 5 }} />
              <Box title={`Consumo ${amount(item.consumption)}`} sx={{ height: 9, width: `${Math.max(1, item.consumption / chartMax * 100)}%`, bgcolor: item.consumption > item.budget ? "#f87171" : "#2dd4bf", borderRadius: 5 }} />
            </Stack>
          </Box>)}
          <Stack direction="row" spacing={2} sx={{ pt: 1 }}><Typography variant="caption">🔹 Orçamento</Typography><Typography variant="caption">🟢 Consumo dentro do orçamento</Typography><Typography variant="caption">🔴 Consumo acima</Typography></Stack>
        </Stack>}
      </CardContent></Card></Grid>
      <Grid item xs={12} lg={7}><Card variant="outlined" sx={{ height: "100%", borderRadius: 3 }}><CardContent>
        <Typography variant="h6" fontWeight={700} sx={{ mb: 1 }}>Fechamento mensal</Typography>
        <TableContainer sx={{ maxHeight: 560 }}><Table size="small" stickyHeader><TableHead><TableRow>
          {["Competência", "Orçamento", "Consumo", "Realizado", "Ajuste", "Crédito final", "Overage acumulado", "Overage abatido/liquidado"].map((label) => <TableCell key={label} align={label === "Competência" ? "left" : "right"} sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{label}</TableCell>)}
        </TableRow></TableHead><TableBody>{monthly.filter((item) => period === "all" || item.month === period).map((item) => <TableRow key={item.month} hover>
          <TableCell sx={{ fontWeight: 600 }}>{periodLabel(item.month)}</TableCell><TableCell align="right">{amount(item.budget)}</TableCell><TableCell align="right">{amount(item.consumption)}</TableCell><TableCell align="right">{amount(item.realized)}</TableCell>
          <TableCell align="right" sx={{ color: item.adjustment > 0 ? "success.main" : item.adjustment < 0 ? "error.main" : "text.secondary" }}>{amount(item.adjustment)}</TableCell><TableCell align="right" sx={{ color: item.credit > 0 ? "success.main" : "text.secondary" }}>{amount(item.credit)}</TableCell><TableCell align="right" sx={{ color: item.overage < 0 ? "error.main" : "text.secondary" }}>{amount(item.overage)}</TableCell><TableCell align="right" sx={{ color: item.settledOverage < 0 ? "error.main" : "text.secondary" }}>{amount(item.settledOverage)}</TableCell>
        </TableRow>)}</TableBody></Table></TableContainer>
        {!monthly.length && !loading && <Typography color="text.secondary" sx={{ py: 3 }}>Nenhuma liquidação mensal publicada para este contrato.</Typography>}
      </CardContent></Card></Grid>

      <Grid item xs={12}><Card variant="outlined" sx={{ borderRadius: 3 }}><CardContent>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" sx={{ mb: 1 }}><Box><Typography variant="h6" fontWeight={700}>Detalhamento por pool</Typography><Typography variant="body2" color="text.secondary">Competência, cloud e ID Orçamento · run vigente</Typography></Box><Typography variant="caption" color="text.secondary" sx={{ alignSelf: "center" }}>Versões de orçamento incluídas em cada linha</Typography></Stack>
        <TableContainer sx={{ maxHeight: 460 }}><Table size="small" stickyHeader><TableHead><TableRow>
          {["Competência", "Cloud", "ID Orçamento", "Orçamento", "Consumo", "Realizado", "Ajuste", "Crédito +", "Crédito usado", "Overage acumulado", "Overage abatido/liquidado", "Crédito final", "Run / origem", "Versão orçamento"].map((label) => <TableCell key={label} sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{label}</TableCell>)}
        </TableRow></TableHead><TableBody>{selectedRows.map((row, index) => <TableRow key={`${row.period}-${row.cloud_scope}-${row.budget_id}-${index}`} hover>
          <TableCell>{periodLabel(String(row.period ?? "—"))}</TableCell><TableCell>{String(row.cloud_scope ?? "—")}</TableCell><TableCell>{String(row.budget_id ?? "—")}</TableCell><TableCell>{amount(row.commitment)}</TableCell><TableCell>{amount(row.consumption)}</TableCell><TableCell>{amount(row.realized)}</TableCell><TableCell sx={{ color: Number(row.contractual_adjustment ?? 0) > 0 ? "success.main" : Number(row.contractual_adjustment ?? 0) < 0 ? "error.main" : "text.secondary" }}>{amount(row.contractual_adjustment)}</TableCell><TableCell sx={{ color: Number(row.credit_generated ?? 0) > 0 ? "success.main" : "text.secondary" }}>{amount(row.credit_generated)}</TableCell><TableCell>{amount(row.credit_used)}</TableCell><TableCell sx={{ color: Number(row.closing_overage ?? 0) < 0 ? "error.main" : "text.secondary" }}>{amount(row.closing_overage)}</TableCell><TableCell sx={{ color: Number(row.overage_resolved ?? 0) < 0 ? "error.main" : "text.secondary" }}>{amount(row.overage_resolved)}</TableCell><TableCell sx={{ color: Number(row.closing_credit ?? 0) > 0 ? "success.main" : "text.secondary" }}>{amount(row.closing_credit)}</TableCell><TableCell>{String(row.run_id ?? "—")} · {String(row.run_source ?? "—")}</TableCell><TableCell>{Array.isArray(row.budget_version_ids) ? row.budget_version_ids.map(String).join(", ") : "—"}</TableCell>
        </TableRow>)}</TableBody></Table></TableContainer>
        {!selectedRows.length && !loading && <Typography color="text.secondary" sx={{ py: 3 }}>Nenhum pool para os filtros selecionados.</Typography>}
      </CardContent></Card></Grid>
    </Grid>
  </Box>;
}
