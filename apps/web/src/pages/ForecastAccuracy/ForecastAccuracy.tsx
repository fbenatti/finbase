import { dataDisplayText } from "../../utils/dataPresentation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Plot from "@/components/Plot";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Alert, Box, Breadcrumbs, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, Grid, IconButton,
  InputLabel, LinearProgress, Link, MenuItem,
  Paper, Select, Stack, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, TextField, Typography,
} from "@mui/material";
import { Add, ArrowBack, Remove } from "@mui/icons-material";
import DepartmentTreePicker, { flattenDepartments } from "../../components/DepartmentTreePicker";
import DesviosTab from "./DesviosTab";
import AcksRegisteredTab from "./AcksRegisteredTab";
import {
  Department, ForecastAccuracyMonth, ForecastAccuracyDimensions, ForecastAccuracyDelta, ForecastAccuracyDeltaRow,
  ForecastAccuracyDeltaSeries, ForecastAck, ForecastDesvioSubcat,
  createForecastAck,
  getDepartments, getForecastAccuracyMonths, getForecastAccuracyDimensions, getForecastAccuracyDelta,
  getForecastAccuracyDeltaSeries, getForecastAccuracyDesviosSubcategories, getForecastAcks,
} from "../../services/api";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const money2 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const monthLabel = (month: string) => month.length === 6
  ? `${month.slice(0, 4)}-${month.slice(4, 6)}`
  : month;
const MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const prettyMonth = (month: string) => month.length === 6
  ? `${MONTH_NAMES[Number(month.slice(4, 6)) - 1]}/${month.slice(2, 4)}`
  : month;
const DIM_LABELS: Record<string, string> = {
  serviceprovidername: "Cloud",
  cloud_origem: "Cloud Origem",
  subaccountname: "SubAccountName",
};

function KpiCard({ label, value, hint, color }: { label: string; value: string; hint?: string; color?: string }) {
  return (
    <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider", height: "100%" }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ mt: 0.5, fontWeight: 600, color: color || "text.primary" }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </Paper>
  );
}

export default function ForecastAccuracy({ embedded = false }: { embedded?: boolean }) {
  const [params, setParams] = useSearchParams();
  const requestedTab = params.get("aba");
  const tab = requestedTab === "desvios" || requestedTab === "acks" ? requestedTab : "visao";
  const [months, setMonths] = useState<ForecastAccuracyMonth[]>([]);
  const [summary, setSummary] = useState<ForecastAccuracyMonth | null>(null);
  const [dimensions, setDimensions] = useState<ForecastAccuracyDimensions | null>(null);
  const [deltaData, setDeltaData] = useState<ForecastAccuracyDelta | null>(null);
  const [selectedMonth, setSelectedMonth] = useState(params.get("mes") ?? "");
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [selectedDim, setSelectedDim] = useState(params.get("dim") ?? "cloud_origem");
  const [department, setDepartmentState] = useState(params.get("dept") ?? "");
  const departmentRef = useRef(department);
  const [organization, setOrganization] = useState<Department[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dimsLoading, setDimsLoading] = useState(false);
  const [deltaLoading, setDeltaLoading] = useState(false);
  // No último nível os ofensores abrem o detalhe por ServiceSubcategory
  // (mesmo comportamento da aba Desvios).
  const [offenderSubcats, setOffenderSubcats] = useState<Record<string, ForecastDesvioSubcat[]>>({});
  const [offenderSubcatOpen, setOffenderSubcatOpen] = useState<string | null>(null);
  const [offenderSubcatLoading, setOffenderSubcatLoading] = useState(false);
  // Dialog com o gráfico acumulado do período para um ServiceSubcategory.
  const [seriesDialog, setSeriesDialog] = useState<{ subcategory: string; data: ForecastAccuracyDeltaSeries | null; loading: boolean; error: string } | null>(null);
  // Acks (motivos de desvio) do mês selecionado — exibidos no Top 5 ofensores.
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [ackTarget, setAckTarget] = useState<ForecastAccuracyDeltaRow | null>(null);
  const [ackCategory, setAckCategory] = useState("Pico sazonal");
  const [ackReason, setAckReason] = useState("");
  const [savingAck, setSavingAck] = useState(false);
  const openSeriesDialog = (row: ForecastAccuracyDeltaRow) => {
    if (selectedRun == null) return;
    const subcategory = row.subcategory || row.name;
    setSeriesDialog({ subcategory, data: null, loading: true, error: "" });
    getForecastAccuracyDeltaSeries(selectedRun, selectedMonth || undefined, department || undefined, subcategory)
      .then(data => setSeriesDialog({ subcategory, data, loading: false, error: "" }))
      .catch(e => setSeriesDialog({ subcategory, data: null, loading: false, error: e instanceof Error ? e.message : String(e) }));
  };

  // Filtros persistidos na URL: trocar mês/departamento nunca perde o outro,
  // mesmo que a página remonte ou seja recarregada.
  const setDepartment = (path: string) => {
    departmentRef.current = path;
    setDepartmentState(path);
    const next = new URLSearchParams(params);
    if (path) next.set("dept", path); else next.delete("dept");
    setParams(next, { replace: true });
  };
  const setMonthAndRun = (month: string, runId: number | null, entry: ForecastAccuracyMonth | null) => {
    setSelectedMonth(month);
    setSelectedRun(runId);
    if (entry) setSummary(entry);
    const next = new URLSearchParams(params);
    if (month) next.set("mes", month); else next.delete("mes");
    setParams(next, { replace: true });
  };
  const setDim = (dim: string) => {
    setSelectedDim(dim);
    const next = new URLSearchParams(params);
    if (dim) next.set("dim", dim); else next.delete("dim");
    setParams(next, { replace: true });
  };

  // Carrega a árvore de departamentos (como no ExpectedView) e define o default,
  // respeitando o departamento já persistido na URL.
  useEffect(() => {
    let cancelled = false;
    getDepartments().then((departments: Department[]) => {
      if (cancelled) return;
      setOrganization(departments);
      const flat = flattenDepartments(departments).filter(d => d.active);
      const cloud = flat.find(d => d.name === "Consumo Cloud" && d.parent_id === null);
      if (cloud && !department) {
        setDepartment(cloud.path);
      }
    }).catch(() => { /* hierarquia indisponível: fica "Todos" */ })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A visão (tendência + KPIs por mês) segue o departamento selecionado.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getForecastAccuracyMonths(undefined, department || undefined, "all")
      .then((monthsList) => {
        if (cancelled) return;
        setMonths(monthsList);
        // Mês padrão: o último mês antes do corrente (ex.: julho quando estamos em agosto).
        const now = new Date();
        const currentYm = now.getFullYear() * 100 + (now.getMonth() + 1);
        const prevYm = currentYm % 100 === 1
          ? (Math.floor(currentYm / 100) - 1) * 100 + 12
          : currentYm - 1;
        const prev = String(prevYm);
        const still = monthsList.find(item => item.target_month === selectedMonth);
        const next = still
          || monthsList.find(item => item.target_month === prev)
          || monthsList[monthsList.length - 1]
          || monthsList[0];
        if (next) setMonthAndRun(next.target_month, next.run_id, next);
        else setMonthAndRun("", null, null);
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [department]);

  // Detalhe por dimensão + ofensores do desvio (nível abaixo do departamento).
  useEffect(() => {
    if (selectedRun == null || tab !== "visao") return;
    let cancelled = false;
    setError("");
    setDimsLoading(true);
    setDeltaLoading(true);
    setOffenderSubcats({});
    setOffenderSubcatOpen(null);
    Promise.all([
      getForecastAccuracyDimensions(selectedRun, selectedMonth || undefined, selectedDim, department || undefined, "all"),
      getForecastAccuracyDelta(selectedRun, selectedMonth || undefined, "all", department || undefined),
    ])
      .then(([dimsData, delta]) => {
        if (cancelled) return;
        setDimensions(dimsData);
        setDeltaData(delta);
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) { setDimsLoading(false); setDeltaLoading(false); } });
    return () => { cancelled = true; };
  }, [selectedRun, selectedMonth, selectedDim, department, tab]);

  // Carrega os Acks do mês selecionado (motivos de desvio) para o Top 5.
  useEffect(() => {
    if (!selectedMonth) { setAcks([]); return; }
    let cancelled = false;
    getForecastAcks(selectedMonth, department || undefined)
      .then(result => { if (!cancelled) setAcks(result.items); })
      .catch(() => { if (!cancelled) setAcks([]); });
    return () => { cancelled = true; };
  }, [selectedMonth, department]);

  // Motivos de desvio que se aplicam a uma linha do Top 5: Ack exato no caminho
  // da linha ou Ack de um descendente (ex.: linha "DIR DADOS..." mostra o Ack do
  // projeto "Invoice" abaixo dela). Merge direto com os desvios identificados.
  const acksForRow = (item: ForecastAccuracyDeltaRow): ForecastAck[] => {
    if (acks.length === 0 || !deltaData) return [];
    const rowPath = item.path && item.path.length
      ? item.path.join(" > ")
      : [deltaData.department, item.name].filter(Boolean).join(" > ");
    const exact = acks.filter(ack => ack.department_path.join(" > ") === rowPath);
    const descendants = acks.filter(ack => ack.department_path.join(" > ").startsWith(rowPath + " > "));
    const ancestors = acks.filter(ack => rowPath.startsWith(ack.department_path.join(" > ") + " > "));
    return [...exact, ...descendants, ...ancestors].filter((ack, index, all) => all.findIndex(item => item.id === ack.id) === index);
  };

  const projectPathForRow = (item: ForecastAccuracyDeltaRow): string => {
    const parts = item.path?.length
      ? item.path
      : [...pathParts, item.name].filter(Boolean);
    // Organograma padrão: Segmento > Modelo > Cloud > Diretoria > Projeto.
    return parts.length >= 5 ? parts.slice(0, 5).join(" > ") : "";
  };

  const saveAck = async () => {
    if (!ackTarget || selectedRun == null || !ackReason.trim()) return;
    const path = projectPathForRow(ackTarget);
    if (!path) return;
    setSavingAck(true);
    try {
      await createForecastAck({
        run_id: selectedRun,
        target_month: selectedMonth,
        department: path,
        category: ackCategory,
        reason: ackReason.trim(),
      });
      const result = await getForecastAcks(selectedMonth, department || undefined);
      setAcks(result.items);
      setAckTarget(null);
      setAckReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao salvar Ack");
    } finally {
      setSavingAck(false);
    }
  };

  const goBackOffender = () => {
    if (offenderSubcatOpen) {
      setOffenderSubcatOpen(null);
      return;
    }
    const parent = pathParts.slice(0, -1).join(" > ");
    setDepartment(parent);
  };

  const handleMonthChange = (month: string) => {
    const match = months.find(item => item.target_month === month);
    setMonthAndRun(month, match ? match.run_id : null, match || null);
  };

  const chooseDepartment = (path: string) => {
    setDepartment(path);
  };

  // Navega um nível abaixo: clicar num ofensor estende o caminho do departamento.
  // Usa o caminho completo retornado pelo backend (nunca só o nome isolado).
  // Quando não há mais nível de departamento para detalhar (nível 7, ou a linha
  // é "Não classificado" — folha sem filhos), abre o breakdown por
  // ServiceSubcategory, igual à aba Desvios.
  const isOffenderLeaf = (row: ForecastAccuracyDeltaRow) => {
    if (!deltaData) return false;
    const name = String(row.name || "").trim();
    const isNullRow = name === "" || /n[aã]o\s*classificado/i.test(name);
    return deltaData.level >= 7 || isNullRow;
  };
  const toggleOffenderSubcats = (row: ForecastAccuracyDeltaRow) => {
    const full = row.path && row.path.length ? row.path.join(" > ") : [deltaData?.department, row.name].filter(Boolean).join(" > ");
    setOffenderSubcatOpen(current => {
      const next = current === full ? null : full;
      if (next && selectedRun != null && !offenderSubcats[next]) {
        setOffenderSubcatLoading(true);
        getForecastAccuracyDesviosSubcategories(selectedRun, selectedMonth || undefined, next, department || undefined)
          .then(result => setOffenderSubcats(prev => ({ ...prev, [next]: result.records })))
          .catch(e => setError(e instanceof Error ? e.message : String(e)))
          .finally(() => setOffenderSubcatLoading(false));
      }
      return next;
    });
  };
  const drillOffender = (row: ForecastAccuracyDeltaRow) => {
    if (!deltaData) return;
    if (deltaData.leaf) return; // já é o breakdown por ServiceSubcategory — nada mais abaixo.
    if (isOffenderLeaf(row)) {
      toggleOffenderSubcats(row);
      return;
    }
    const full = row.path && row.path.length ? row.path.join(" > ") : [deltaData.department, row.name].filter(Boolean).join(" > ");
    setDepartment(full);
  };

  const buildPath = (node: Department): string => {
    const parts: string[] = [];
    const byId = new Map<number, Department>();
    const collect = (nodes: Department[]) => nodes.forEach(n => { byId.set(n.id, n); collect(n.children || []); });
    collect(organization);
    let current: Department | undefined = node;
    const seen = new Set<number>();
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      parts.unshift(current.name);
      current = current.parent_id != null ? byId.get(current.parent_id) : undefined;
    }
    return parts.join(" > ");
  };

  const pathParts = (department || "").split(" > ").filter(Boolean);
  const choosePath = (path: string) => setDepartment(path);

  const chartData = useMemo(() => {
    if (!dimensions) return [];
    const records = dimensions.records.slice(0, 15);
    return [
      {
        type: "bar" as const,
        name: "Projetado",
        x: records.map(item => item.dimension_value),
        y: records.map(item => Number(item.forecast_value || 0)),
        marker: { color: "#2563eb" },
      },
      {
        type: "bar" as const,
        name: "Realizado",
        x: records.map(item => item.dimension_value),
        y: records.map(item => Number(item.actual_value || 0)),
        marker: { color: "#16A34A" },
      },
    ];
  }, [dimensions]);

  const chartLayout = useMemo(() => ({
    autosize: true,
    margin: { l: 70, r: 20, t: 30, b: 110 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    barmode: "group" as const,
    xaxis: { title: { text: DIM_LABELS[dimensions?.dimension || ""] || dimensions?.dimension || "Dimensão" }, tickangle: -35 },
    yaxis: { title: { text: "Valor (R$)" }, tickformat: ".2s" },
    showlegend: true,
    legend: { orientation: "h" as const, y: -0.28 },
  }), [dimensions]);

  const accuracyColor = (value: number | null | undefined) => {
    if (value == null) return "text.primary";
    if (value >= 0.9) return "#16A34A";
    if (value >= 0.7) return "#D97706";
    return "#DC2626";
  };

  return (
    <Box sx={embedded
      ? { pb: 3 }
      : { mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider", borderRadius: embedded ? "12px 12px 0 0" : 0 }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, mb: 2, flexWrap: "wrap" }}>
          <Box>
            <Typography variant={embedded ? "h6" : "h4"}>{embedded ? "Acurácia" : "Acurácia de Forecast"}</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              O que foi projetado no início do mês × o que realmente foi realizado.
            </Typography>
          </Box>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ alignItems: { md: "flex-end" }, flexWrap: "wrap", width: "100%" }}>
            <Box sx={{ width: { xs: "100%", md: 360 }, mr: "auto" }}>
              <DepartmentTreePicker
                departments={organization.filter(node => node.active)}
                value={flattenDepartments(organization).find(node => node.path === department)?.id ?? null}
                onChange={id => chooseDepartment(id == null ? "" : flattenDepartments(organization).find(node => node.id === id)?.path || "")}
                allowClear
                clearLabel="Todos"
                label="Organograma"
                menuHint="Selecione um nível para consolidar todos os seus filhos."
              />
            </Box>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel id="accuracy-month-label">Mês realizado</InputLabel>
              <Select
                labelId="accuracy-month-label"
                label="Mês realizado"
                value={selectedMonth}
                onChange={event => handleMonthChange(event.target.value)}
              >
                {months.map(item => (
                  <MenuItem key={item.run_id} value={item.target_month}>
                    {prettyMonth(item.target_month)} {item.closed ? "" : "(em curso)"}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 180 }}>
              <InputLabel id="accuracy-dim-label">Dimensão</InputLabel>
              <Select
                labelId="accuracy-dim-label"
                label="Dimensão"
                value={selectedDim}
                onChange={event => setDim(event.target.value)}
              >
                <MenuItem value="serviceprovidername">Cloud</MenuItem>
                <MenuItem value="cloud_origem">Cloud Origem</MenuItem>
                <MenuItem value="subaccountname">SubAccountName</MenuItem>
              </Select>
            </FormControl>
          </Stack>
        </Box>

        {pathParts.length > 0 && (
          <Breadcrumbs separator=">" sx={{ pb: 2, "& .MuiBreadcrumbs-separator": { mx: 0.5 } }}>
            <Typography color="text.secondary">Acurácia</Typography>
            {pathParts.map((name, index) => {
              const path = pathParts.slice(0, index + 1).join(" > ");
              const last = index === pathParts.length - 1;
              return last
                ? <Typography key={path} color="text.primary" fontWeight={600}>{name}</Typography>
                : <Link key={path} component="button" underline="hover" color="inherit" onClick={() => choosePath(path)}>{name}</Link>;
            })}
          </Breadcrumbs>
        )}
      </Box>

      <Box sx={{ p: 3 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
        {tab === "desvios" ? (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
            <DesviosTab runId={selectedRun} targetMonth={selectedMonth} department={department} />
          </Paper>
        ) : tab === "acks" ? (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
            <AcksRegisteredTab defaultMonth={selectedMonth ? `${selectedMonth.slice(0, 4)}-${selectedMonth.slice(4, 6)}-01` : ""} department={department} />
          </Paper>
        ) : (
        <>
        {loading && summary && <LinearProgress sx={{ mb: 2, borderRadius: 1 }} />}
        {loading && !summary && (
          <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}><CircularProgress /></Box>
        )}

        {summary && (
          <>
            <Stack direction="row" spacing={1} sx={{ mb: 2, alignItems: "center", flexWrap: "wrap" }}>
              <Typography variant="h6">Snapshot do simulado</Typography>
              <Chip size="small" color={summary.closed ? "success" : "warning"} label={summary.closed ? "Mês fechado" : "Em acompanhamento"} />
              <Chip size="small" variant="outlined" label={`simulado em ${monthLabel(summary.simulated_as_of)}`} />
              <Chip size="small" variant="outlined" label={`cutoff ${monthLabel(summary.cutoff_date)}`} />
              <Chip size="small" variant="outlined" label={`run #${summary.run_id} · ${summary.result_table}`} />
              {department && <Chip size="small" variant="outlined" label={`departamento: ${department}`} />}
            </Stack>

            <Grid container spacing={2} sx={{ mb: 2 }}>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Projetado (mês)" value={money.format(Number(summary.forecast_value || 0))} hint="estimativa do snapshot" /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Realizado" value={money.format(Number(summary.actual_value || 0))} hint={summary.closed ? "mês fechado" : "parcial até hoje"} /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Variação (R$)" value={money.format(Number(summary.variance || 0))} color={Number(summary.variance || 0) >= 0 ? "#DC2626" : "#16A34A"} /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Variação (%)" value={pct(summary.variance_pct)} color={Number(summary.variance_pct || 0) >= 0 ? "#DC2626" : "#16A34A"} /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="Acurácia" value={pct(summary.accuracy)} color={accuracyColor(summary.accuracy)} hint="1 − WAPE" /></Grid>
              <Grid item xs={6} sm={4} md={2}><KpiCard label="WAPE" value={summary.wape == null ? "—" : money2.format(Number(summary.wape))} hint={`bias: ${pct(summary.bias)}`} /></Grid>
            </Grid>

            <Grid container spacing={2}>
              <Grid item xs={12} lg={7}>
                <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="h6" sx={{ mb: 1 }}>Projetado × Realizado</Typography>
                  <Box sx={{ height: 340 }}>
                    <Plot
                      data={chartData}
                      layout={chartLayout}
                      style={{ width: "100%", height: "100%" }}
                      useResizeHandler
                      config={{ displayModeBar: false, responsive: true }}
                    />
                  </Box>
                </Paper>
              </Grid>
              <Grid item xs={12} lg={5}>
                <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="h6" sx={{ mb: 1 }}>Evolução da acurácia</Typography>
                  {months.length > 0 ? (
                    <TableContainer>
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>Mês</TableCell>
                            <TableCell align="right">Projetado</TableCell>
                            <TableCell align="right">Realizado</TableCell>
                            <TableCell align="right">Acurácia</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {months.map(item => (
                            <TableRow key={item.target_month}>
                              <TableCell>{prettyMonth(item.target_month)}</TableCell>
                              <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
                              <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
                              <TableCell align="right" sx={{ color: accuracyColor(item.accuracy) }}>{pct(item.accuracy)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  ) : (
                    <Typography variant="body2" color="text.secondary">Sem simulados para este departamento/visão.</Typography>
                  )}
                </Paper>
              </Grid>
            </Grid>

            <Paper elevation={0} sx={{ mt: 2, p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                <Stack direction="row" alignItems="flex-start" gap={1}>
                  {(department || offenderSubcatOpen) && (
                    <IconButton size="small" onClick={goBackOffender} aria-label="Voltar um nível" title="Voltar um nível">
                      <ArrowBack fontSize="small" />
                    </IconButton>
                  )}
                  <Box>
                  <Typography variant="h6">Principais desvios</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Nível abaixo do departamento selecionado — {deltaData?.level_label || "—"} · top 10 por |delta|.
                    {department ? ` Filtrado por: ${department}.` : " Sem filtro de departamento."}
                    {deltaData && !deltaData.leaf && deltaData.records.some(r => !isOffenderLeaf(r)) && " Clique em uma linha para navegar um nível abaixo."}
                    {deltaData && !deltaData.leaf && deltaData.records.some(isOffenderLeaf) && " Clique em uma linha para ver o detalhe por ServiceSubcategory."}
                  </Typography>
                  </Box>
                </Stack>
                {deltaLoading && <Chip size="small" label="carregando…" color="info" variant="outlined" />}
              </Stack>
              {deltaLoading && <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />}
              {deltaData && deltaData.records.length > 0 ? (
                <>
                  <TableContainer>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>{deltaData.level_label}</TableCell>
                          <TableCell align="right">Projetado</TableCell>
                          <TableCell align="right">Realizado</TableCell>
                          <TableCell align="right">Delta (R$)</TableCell>
                          <TableCell align="right">Delta (%)</TableCell>
                          <TableCell align="right">Acurácia</TableCell>
                          <TableCell>Ack</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {deltaData.records.slice(0, 10).map(item => {
                          const rowPath = item.path && item.path.length ? item.path.join(" > ") : [deltaData.department, item.name].filter(Boolean).join(" > ");
                          const subcatOpen = offenderSubcatOpen === rowPath;
                          const subcats = offenderSubcats[rowPath];
                          const rowAcks = acksForRow(item);
                          return (
                            <Fragment key={item.name}>
                              <TableRow
                                hover
                                onClick={() => drillOffender(item)}
                                sx={{ cursor: "pointer" }}
                              >
                                <TableCell>
                                  <Stack direction="row" alignItems="center" gap={1}>
                                    {deltaData.leaf && (
                                      <IconButton
                                        size="small"
                                        onClick={event => { event.stopPropagation(); openSeriesDialog(item); }}
                                        aria-label={`Gráfico do período de ${item.name}`}
                                        title="Gráfico do período (Projetado × Realizado dia a dia)"
                                      >
                                        <Add fontSize="small" />
                                      </IconButton>
                                    )}
                                    <Typography variant="body2" fontWeight={600}>{item.name}</Typography>
                                    {deltaData.leaf || isOffenderLeaf(item)
                                      ? <Chip size="small" label="ServiceSubcategory" variant="outlined" />
                                      : <Chip size="small" label="detalhar" variant="outlined" />}
                                  </Stack>
                                </TableCell>
                                <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
                                <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
                                <TableCell align="right" sx={{ color: Number(item.variance || 0) >= 0 ? "#DC2626" : "#16A34A", fontWeight: 600 }}>
                                  {money.format(Number(item.variance || 0))}
                                </TableCell>
                                <TableCell align="right">{pct(item.variance_pct)}</TableCell>
                                <TableCell align="right" sx={{ color: accuracyColor(item.accuracy) }}>{pct(item.accuracy)}</TableCell>
                                <TableCell>
                                  {rowAcks.length === 0 ? (
                                    projectPathForRow(item) ? (
                                      <Button
                                        size="small"
                                        variant="contained"
                                        onClick={event => { event.stopPropagation(); setAckTarget(item); }}
                                      >
                                        Ack
                                      </Button>
                                    ) : <Typography variant="caption" color="text.disabled">Disponível no nível Projeto</Typography>
                                  ) : (
                                    <Stack spacing={0.5} sx={{ maxWidth: 360 }}>
                                      <Chip size="small" color="info" label={`✓ Ack${rowAcks.length > 1 ? ` (${rowAcks.length})` : ""}: ${rowAcks[0].category}`} />
                                      {rowAcks.slice(0, 3).map(ack => (
                                        <Typography key={ack.id} variant="caption" color="text.secondary" sx={{ display: "block" }}>
                                          {ack.reason}
                                        </Typography>
                                      ))}
                                      {rowAcks.length > 3 && (
                                        <Typography variant="caption" color="text.secondary">+{rowAcks.length - 3} acks…</Typography>
                                      )}
                                    </Stack>
                                  )}
                                </TableCell>
                              </TableRow>
                              {isOffenderLeaf(item) && subcatOpen && (
                                <TableRow>
                                  <TableCell colSpan={7} sx={{ bgcolor: "#f8fafc" }}>
                                    {offenderSubcatLoading && !subcats ? (
                                      <LinearProgress sx={{ m: 1 }} />
                                    ) : subcats && subcats.length > 0 ? (
                                      <Table size="small">
                                        <TableHead>
                                          <TableRow>
                                            <TableCell>ServiceSubcategory</TableCell>
                                            <TableCell align="right">Projetado</TableCell>
                                            <TableCell align="right">Realizado</TableCell>
                                            <TableCell align="right">Delta (R$)</TableCell>
                                            <TableCell align="right">Acurácia</TableCell>
                                          </TableRow>
                                        </TableHead>
                                        <TableBody>
                                          {subcats.slice(0, 10).map(sub => (
                                            <TableRow key={sub.subcategory}>
                                              <TableCell><Typography variant="body2">{sub.subcategory}</Typography></TableCell>
                                              <TableCell align="right">{money.format(Number(sub.forecast_value || 0))}</TableCell>
                                              <TableCell align="right">{money.format(Number(sub.actual_value || 0))}</TableCell>
                                              <TableCell align="right" sx={{ color: Number(sub.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>
                                                {money.format(Number(sub.variance || 0))}
                                              </TableCell>
                                              <TableCell align="right">{pct(sub.accuracy)}</TableCell>
                                            </TableRow>
                                          ))}
                                        </TableBody>
                                      </Table>
                                    ) : (
                                      <Typography variant="body2" color="text.secondary">Sem subcategorias para este nível.</Typography>
                                    )}
                                  </TableCell>
                                </TableRow>
                              )}
                            </Fragment>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </TableContainer>
                  <Stack direction="row" spacing={2} alignItems="center" sx={{ mt: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      <Box component="span" sx={{ color: "#DC2626" }}>vermelho = subestimou</Box> (realizado acima do projetado) ·{" "}
                      <Box component="span" sx={{ color: "#16A34A" }}>verde = superestimou</Box> (projetado acima do realizado).
                    </Typography>
                  </Stack>
                </>
              ) : (
                <Typography variant="body2" color="text.secondary">Sem dados neste nível para os filtros atuais.</Typography>
              )}
            </Paper>

            <Dialog open={Boolean(ackTarget)} onClose={() => !savingAck && setAckTarget(null)} maxWidth="sm" fullWidth>
              <DialogTitle>Registrar Ack — Projeto {ackTarget ? projectPathForRow(ackTarget).split(" > ").slice(-1)[0] : ""}</DialogTitle>
              <DialogContent>
                <Stack gap={2} sx={{ mt: 1 }}>
                  <FormControl fullWidth>
                    <InputLabel>Categoria</InputLabel>
                    <Select value={ackCategory} label="Categoria" onChange={event => setAckCategory(event.target.value)}>
                      {["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"].map(category => (
                        <MenuItem key={category} value={category}>{category}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <TextField
                    label="Motivo do desvio"
                    value={ackReason}
                    onChange={event => setAckReason(event.target.value)}
                    multiline
                    minRows={3}
                    required
                  />
                </Stack>
              </DialogContent>
              <DialogActions>
                <Button onClick={() => setAckTarget(null)} disabled={savingAck}>Cancelar</Button>
                <Button variant="contained" onClick={saveAck} disabled={savingAck || !ackReason.trim()}>
                  {savingAck ? "Salvando…" : "Salvar Ack"}
                </Button>
              </DialogActions>
            </Dialog>

            <Paper elevation={0} sx={{ mt: 2, p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="h6">Detalhe por {DIM_LABELS[dimensions?.dimension || ""] || dimensions?.dimension}</Typography>
                {dimsLoading && <Chip size="small" label="carregando…" color="info" variant="outlined" />}
              </Stack>
              {dimsLoading && <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />}
              {dimensions && dimensions.records.length > 0 ? (
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{DIM_LABELS[dimensions.dimension] || dimensions.dimension}</TableCell>
                        <TableCell align="right">Projetado</TableCell>
                        <TableCell align="right">Realizado</TableCell>
                        <TableCell align="right">Variação (R$)</TableCell>
                        <TableCell align="right">Variação (%)</TableCell>
                        <TableCell align="right">Acurácia</TableCell>
                        <TableCell align="right">WAPE</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {dimensions.records.map(item => (
                        <TableRow key={item.dimension_value} hover>
                          <TableCell>{item.dimension_value}</TableCell>
                          <TableCell align="right">{money.format(Number(item.forecast_value || 0))}</TableCell>
                          <TableCell align="right">{money.format(Number(item.actual_value || 0))}</TableCell>
                          <TableCell align="right" sx={{ color: Number(item.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>
                            {money.format(Number(item.variance || 0))}
                          </TableCell>
                          <TableCell align="right">{pct(item.variance_pct)}</TableCell>
                          <TableCell align="right" sx={{ color: accuracyColor(item.accuracy) }}>{pct(item.accuracy)}</TableCell>
                          <TableCell align="right">{item.wape == null ? "—" : money2.format(Number(item.wape))}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              ) : (
                <Typography variant="body2" color="text.secondary">Sem linhas para esta dimensão.</Typography>
              )}
            </Paper>
          </>
        )}
        {!loading && months.length === 0 && !error && (
          <Alert severity="info">
            Nenhum simulado de acurácia encontrado. Crie um com
            <Box component="code" sx={{ mx: 1 }}>POST /forecast/runs?config_id=&lt;id&gt;&amp;simulated_as_of=YYYY-MM-DD</Box>
            ou na aba <b>Acurácia de Forecast</b> de Reprocessar.
          </Alert>
        )}
        {!loading && months.length > 0 && !error && !months.some(m => Number(m.forecast_value) || Number(m.actual_value)) && (
          <Alert severity="warning">
            Sem dados de acurácia para o departamento selecionado
            {department ? ` (${department})` : ""}. Selecione outro departamento ou <b>Todos</b>.
          </Alert>
        )}
        </>)}

      <Dialog open={seriesDialog !== null} onClose={() => setSeriesDialog(null)} maxWidth="lg" fullWidth>
        {seriesDialog && (
          <>
            <DialogTitle>
              <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap">
                <Box>
                  <Typography variant="h6">Período — {seriesDialog.subcategory}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {prettyMonth(seriesDialog.data?.target_month || selectedMonth)} · {seriesDialog.data?.department || department || "Todos os departamentos"}
                  </Typography>
                </Box>
                <Button size="small" onClick={() => setSeriesDialog(null)}>Fechar</Button>
              </Stack>
            </DialogTitle>
            <DialogContent dividers>
              {seriesDialog.loading && <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />}
              {seriesDialog.error && <Alert severity="error" sx={{ mb: 1.5 }}>{dataDisplayText(seriesDialog.error)}</Alert>}
              {seriesDialog.data && seriesDialog.data.points.length > 0 && (
                <>
                  <Stack direction="row" spacing={3} sx={{ mb: 2 }} flexWrap="wrap">
                    <Box>
                      <Typography variant="caption" color="text.secondary">Projetado (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{money.format(seriesDialog.data.forecast_total)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Realizado (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{money.format(seriesDialog.data.actual_total)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Delta</Typography>
                      <Typography variant="subtitle1" fontWeight={600} sx={{ color: Number(seriesDialog.data.variance || 0) >= 0 ? "#DC2626" : "#16A34A" }}>
                        {money.format(seriesDialog.data.variance)} ({pct(seriesDialog.data.variance_pct)})
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Acurácia</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{pct(seriesDialog.data.accuracy)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Desvio começou a crescer</Typography>
                      <Typography variant="subtitle1" fontWeight={600} sx={{ color: seriesDialog.data.daily_deviation_day != null ? "#DC2626" : "#16A34A" }}>
                        {seriesDialog.data.daily_deviation_day != null ? `dia ${seriesDialog.data.daily_deviation_day}` : "não houve (redução)"}
                      </Typography>
                    </Box>
                  </Stack>
                  <Plot
                    data={[
                      {
                        type: "bar",
                        name: "Realizado (dentro do rate diário)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => Math.min(p.actual, p.forecast_rate)),
                        marker: { color: "#16A34A" },
                      },
                      {
                        type: "bar",
                        name: "Excesso do dia (desvio)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => Math.max(0, p.actual - p.forecast_rate)),
                        marker: { color: "#DC2626" },
                      },
                      {
                        type: "scatter",
                        mode: "lines",
                        name: "Projetado (rate diário)",
                        x: seriesDialog.data.points.map(p => p.day),
                        y: seriesDialog.data.points.map(p => p.forecast_rate),
                        line: { color: "#2563eb", dash: "dash", width: 2 },
                      },
                      ...(seriesDialog.data.daily_deviation_day != null ? [{
                        type: "scatter" as const,
                        mode: "lines" as const,
                        name: "Desvio começou",
                        x: [seriesDialog.data.daily_deviation_day, seriesDialog.data.daily_deviation_day],
                        y: [0, Math.max(...seriesDialog.data.points.map(p => p.actual))],
                        line: { color: "#DC2626", dash: "dot" as const, width: 1.5 },
                        hovertemplate: "Desvio começou a crescer — dia %{x}<extra></extra>",
                      }] : []),
                    ]}
                    layout={{
                      autosize: true,
                      height: 420,
                      margin: { l: 70, r: 20, t: 40, b: 50 },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      barmode: "stack",
                      xaxis: { title: { text: "Dia do mês" }, dtick: 1 },
                      yaxis: { title: { text: "Valor diário (R$)" }, tickformat: ".2s" },
                      showlegend: true,
                      legend: { orientation: "h", y: -0.18 },
                    }}
                    config={{ displayModeBar: true, responsive: true }}
                    style={{ width: "100%" }}
                    useResizeHandler
                  />
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
                    Consumo diário vs rate projetado (total mensal ÷ dias). A partir do dia{" "}
                    {seriesDialog.data.daily_deviation_day ?? "—"} o realizado do dia passa a ultrapassar o
                    rate projetado — é quando o desvio começa a crescer (excesso empilhado em vermelho).
                  </Typography>
                </>
              )}
            </DialogContent>
          </>
        )}
      </Dialog>
      </Box>
    </Box>
  );
}
