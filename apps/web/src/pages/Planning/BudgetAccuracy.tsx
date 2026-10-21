import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import Plot from "@/components/Plot";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  TextField,
  Typography,
} from "@mui/material";
import { Add } from "@mui/icons-material";
import {
  BudgetAccuracyAck,
  BudgetAccuracyDetail,
  BudgetAccuracyLine,
  BudgetAccuracyResponse,
  Department,
  createBudgetAccuracyAck,
  getBudgetAccuracy,
  getBudgetAccuracyAcks,
  getBudgetAccuracyDetail,
  getDepartments,
} from "../../services/api";
import DepartmentTreePicker from "../../components/DepartmentTreePicker";

const money = (value: number) =>
  new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0,
  }).format(value || 0);
const percent = (value: number | null) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const months = [
  "Janeiro",
  "Fevereiro",
  "Março",
  "Abril",
  "Maio",
  "Junho",
  "Julho",
  "Agosto",
  "Setembro",
  "Outubro",
  "Novembro",
  "Dezembro",
];
const statusLabels = {
  dentro_orcamento: "Dentro do orçamento",
  fora_orcamento: "Fora do orçamento",
  sem_orcamento: "Sem orçamento",
  sem_realizado: "Sem realizado",
} as const;
const statusColors = {
  dentro_orcamento: "success",
  fora_orcamento: "error",
  sem_orcamento: "default",
  sem_realizado: "warning",
} as const;

function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <Paper
      variant="outlined"
      sx={{
        p: 2,
        flex: "1 1 210px",
        borderTop: "3px solid",
        borderTopColor: color || "primary.main",
      }}
    >
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h5" fontWeight={700} sx={{ mt: 0.5 }}>
        {value}
      </Typography>
    </Paper>
  );
}

export default function BudgetAccuracy() {
  const [params, setParams] = useState(
    () => new URLSearchParams(window.location.search),
  );
  const [year, setYear] = useState(
    Number(params.get("ano") || new Date().getFullYear()),
  );
  const [period, setPeriod] = useState<"monthly" | "annual">(
    params.get("periodo") === "annual" ? "annual" : "monthly",
  );
  const [month, setMonth] = useState(
    Number(params.get("mes")?.slice(-2) || new Date().getMonth() || 1),
  );
  const [departmentId, setDepartmentId] = useState<number | null>(
    Number(params.get("organograma")) || Number(params.get("departamento")) || null,
  );
  const [departments, setDepartments] = useState<Department[]>([]);
  const [data, setData] = useState<BudgetAccuracyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [acks, setAcks] = useState<BudgetAccuracyAck[]>([]);
  const [ackTarget, setAckTarget] = useState<BudgetAccuracyLine | null>(null);
  const [ackCategory, setAckCategory] = useState("Outro");
  const [ackReason, setAckReason] = useState("");
  const [savingAck, setSavingAck] = useState(false);
  const [detail, setDetail] = useState<BudgetAccuracyDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showOtherSubcategories, setShowOtherSubcategories] = useState(false);
  const [detailLine, setDetailLine] = useState<BudgetAccuracyLine | null>(null);
  const [detailTab, setDetailTab] = useState(0);

  const updateUrl = (next: URLSearchParams) => {
    next.set("tab", "acuracia-orcamento");
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}?${next.toString()}`,
    );
    setParams(next);
  };
  const changeYear = (value: number) => {
    setYear(value);
    const next = new URLSearchParams(params);
    next.set("ano", String(value));
    updateUrl(next);
  };
  const changeMonth = (value: number) => {
    setMonth(value);
    const next = new URLSearchParams(params);
    next.set("mes", `${year}${String(value).padStart(2, "0")}`);
    updateUrl(next);
  };
  const changePeriod = (value: "monthly" | "annual") => {
    setPeriod(value);
    const next = new URLSearchParams(params);
    next.set("periodo", value);
    updateUrl(next);
  };
  const changeDepartment = (value: number | null) => {
    setDepartmentId(value);
    const next = new URLSearchParams(params);
    if (value) next.set("organograma", String(value));
    else next.delete("organograma");
    updateUrl(next);
  };

  useEffect(() => {
    getDepartments()
      .then(setDepartments)
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getBudgetAccuracy(
      year,
      period === "monthly" ? month : null,
      departmentId,
      period,
    )
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((reason) => {
        if (!cancelled)
          setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [year, month, departmentId, period]);
  useEffect(() => {
    const targetMonth =
      period === "monthly"
        ? `${year}${String(month).padStart(2, "0")}`
        : undefined;
    getBudgetAccuracyAcks(
      targetMonth,
      period === "monthly" ? departmentId : undefined,
    )
      .then((result) =>
        setAcks(
          result.items.filter((item) =>
            item.target_month.startsWith(String(year)),
          ),
        ),
      )
      .catch(() => setAcks([]));
  }, [year, month, departmentId, period]);
  const openDetail = async (line: BudgetAccuracyLine) => {
    setShowOtherSubcategories(false);
    setDetailLine(line);
    setDetailTab(0);
    setDetailLoading(true);
    try {
      setDetail(await getBudgetAccuracyDetail(year, line.department_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDetailLoading(false);
    }
  };
  useEffect(() => {
    if (!detail) return undefined;
    const resizePlots = () => window.dispatchEvent(new Event("resize"));
    const frame = window.requestAnimationFrame(resizePlots);
    const timer = window.setTimeout(resizePlots, 300);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [detail, detailTab, showOtherSubcategories]);
  const saveAck = async () => {
    const target = ackTarget || detailLine;
    if (!target || !ackReason.trim()) return;
    setSavingAck(true);
    try {
      await createBudgetAccuracyAck({
        target_month: `${year}${String(month).padStart(2, "0")}`,
        planning_version_id: data?.version?.id,
        department_id: target.department_id,
        department: target.department_levels.join(" > "),
        budget_value: target.budget_value,
        actual_value: target.actual_value,
        category: ackCategory,
        reason: ackReason,
      });
      const refreshed = await getBudgetAccuracyAcks(
        `${year}${String(month).padStart(2, "0")}`,
        departmentId,
      );
      setAcks(refreshed.items);
      setAckTarget(null);
      setAckReason("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSavingAck(false);
    }
  };

  const totals = data?.totals;
  const detailBudget =
    detail?.monthly.reduce((total, item) => total + item.budget_value, 0) || 0;
  const detailActual =
    detail?.monthly.reduce((total, item) => total + item.actual_value, 0) || 0;
  const detailForecast =
    detail?.monthly.reduce((total, item) => total + item.forecast_value, 0) ||
    0;
  const detailProjected = detailActual + detailForecast;
  const detailVariance = detailBudget - detailProjected;
  const detailWithinBudget = detailVariance >= 0;
  const outsideCount =
    data?.lines.filter((line) => line.status === "fora_orcamento").length || 0;
  const insideCount =
    data?.lines.filter((line) => line.status === "dentro_orcamento").length ||
    0;
  return (
    <Box
      sx={{
        mx: -3,
        mt: -3,
        p: 3,
        minHeight: "calc(100vh - 64px)",
        bgcolor: "#f8fafc",
      }}
    >
      <Typography variant="h4">Acurácia de Orçamento</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        {period === "annual"
          ? "Visão anual: soma de Forecast + Realizado comparada ao orçamento anual por departamento."
          : "Visão mensal do orçamento vigente contra o realizado por departamento."}
      </Typography>
      <Paper
        variant="outlined"
        sx={{ p: 2, mb: 2, bgcolor: "#fff", width: "fit-content", maxWidth: "100%" }}
      >
        <Stack
          direction="column"
          gap={2}
          alignItems="flex-start"
        >
          <Stack
            direction={{ xs: "column", sm: "row" }}
            gap={2}
            alignItems={{ sm: "center" }}
          >
            <FormControl size="small" sx={{ minWidth: 130 }}>
              <InputLabel>Ano</InputLabel>
              <Select
                label="Ano"
                value={String(year)}
                onChange={(event) => changeYear(Number(event.target.value))}
              >
                {[2025, 2026, 2027].map((value) => (
                  <MenuItem key={value} value={String(value)}>
                    {value}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel>Visão</InputLabel>
              <Select
                label="Visão"
                value={period}
                onChange={(event) =>
                  changePeriod(event.target.value as "monthly" | "annual")
                }
              >
                <MenuItem value="monthly">Mês</MenuItem>
                <MenuItem value="annual">Ano</MenuItem>
              </Select>
            </FormControl>
            {period === "monthly" && (
              <FormControl size="small" sx={{ minWidth: 150 }}>
                <InputLabel>Mês</InputLabel>
                <Select
                  label="Mês"
                  value={String(month)}
                  onChange={(event) => changeMonth(Number(event.target.value))}
                >
                  {months.map((label, index) => (
                    <MenuItem key={label} value={String(index + 1)}>
                      {label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
          </Stack>
          <Box sx={{ width: "100%", maxWidth: 420 }}>
            <DepartmentTreePicker
              departments={departments}
              value={departmentId}
              onChange={changeDepartment}
              allowClear
              clearLabel="Toda a organização"
              label="Organograma"
            />
          </Box>
        </Stack>
      </Paper>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {dataDisplayText(error)}
        </Alert>
      )}
      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", p: 8 }}>
          <CircularProgress />
        </Box>
      ) : (
        <>
          <Stack direction={{ xs: "column", md: "row" }} gap={2} sx={{ mb: 2 }}>
            <Metric
              label="Orçamento vigente"
              value={money(totals?.budget_value || 0)}
              color="#2563eb"
            />
            {period === "annual" ? (
              <Metric
                label="Forecast + Realizado"
                value={money(totals?.comparison_value || 0)}
                color="#0f766e"
              />
            ) : (
              <Metric
                label="Realizado"
                value={money(totals?.actual_value || 0)}
                color="#0f766e"
              />
            )}
            {period === "annual" && (
              <Metric
                label="Forecast"
                value={money(totals?.forecast_value || 0)}
                color="#7c3aed"
              />
            )}
            <Metric
              label={period === "annual" ? "Variação anual" : "Desvio mensal"}
              value={money(totals?.variance || 0)}
              color={
                totals &&
                (period === "annual"
                  ? totals.variance < 0
                  : totals.variance > 0)
                  ? "#dc2626"
                  : "#059669"
              }
            />
            <Metric
              label="Fora do orçamento"
              value={String(outsideCount)}
              color="#dc2626"
            />
            <Metric
              label="Dentro do orçamento"
              value={String(insideCount)}
              color="#059669"
            />
          </Stack>
          <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              justifyContent="space-between"
              gap={1}
            >
              <Box>
                <Typography variant="h6">
                  {period === "annual"
                    ? `Ano de ${year}`
                    : `${months[month - 1]} de ${year}`}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {period === "annual"
                    ? "Soma dos 12 meses: Forecast + Realizado comparados ao orçamento anual."
                    : "Realizado mensal comparado ao orçamento do mês."}{" "}
                  Desvio: {percent(totals?.variance_pct ?? null)} · consumo:{" "}
                  {percent(totals?.consumed_pct ?? null)}
                </Typography>
              </Box>
              <Typography variant="body2" color="text.secondary">
                Versão:{" "}
                {data?.version
                  ? `v${data.version.version} · ${data.version.title || "sem título"}`
                  : "Nenhuma versão vigente"}
              </Typography>
            </Stack>
          </Paper>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell />
                  <TableCell>Departamento</TableCell>
                  <TableCell align="right">
                    {period === "annual"
                      ? "Orçamento anual"
                      : "Orçamento do mês"}
                  </TableCell>
                  <TableCell align="right">
                    {period === "annual" ? "Forecast + Realizado" : "Realizado"}
                  </TableCell>
                  {period === "annual" && (
                    <TableCell align="right">Forecast</TableCell>
                  )}
                  <TableCell align="right">Desvio</TableCell>
                  <TableCell align="right">Desvio %</TableCell>
                  <TableCell align="right">Consumido</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Reconhecer desvio</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {!data?.lines.length ? (
                  <TableRow>
                    <TableCell colSpan={period === "annual" ? 10 : 9}>
                      <Typography
                        color="text.secondary"
                        sx={{ py: 3, textAlign: "center" }}
                      >
                        Nenhuma linha de orçamento encontrada para este período.
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : (
                  data.lines.map((line) => {
                    const ack = acks.find(
                      (item) => item.department_id === line.department_id,
                    );
                    return (
                      <TableRow key={line.department_id} hover>
                        <TableCell>
                          <Button
                            size="small"
                            onClick={() => void openDetail(line)}
                            aria-label={`Abrir detalhe de ${line.department_levels[line.department_levels.length - 1]}`}
                          >
                            <Add />
                          </Button>
                        </TableCell>
                        <TableCell>
                          <Typography fontWeight={600}>
                            {
                              line.department_levels[
                                line.department_levels.length - 1
                              ]
                            }
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {line.department_levels.join(" › ")}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          {money(line.budget_value)}
                        </TableCell>
                        <TableCell align="right">
                          {money(
                            period === "annual"
                              ? line.comparison_value || 0
                              : line.actual_value,
                          )}
                        </TableCell>
                        {period === "annual" && (
                          <TableCell align="right">
                            {money(line.forecast_value || 0)}
                          </TableCell>
                        )}
                        <TableCell
                          align="right"
                          sx={{
                            color:
                              period === "annual"
                                ? line.variance < 0
                                  ? "error.main"
                                  : "success.main"
                                : line.variance > 0
                                  ? "error.main"
                                  : "success.main",
                            fontWeight: 600,
                          }}
                        >
                          {money(line.variance)}
                        </TableCell>
                        <TableCell align="right">
                          {percent(line.variance_pct)}
                        </TableCell>
                        <TableCell align="right">
                          {percent(line.consumed_pct)}
                        </TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            color={statusColors[line.status]}
                            label={statusLabels[line.status]}
                          />
                        </TableCell>
                        <TableCell>
                          {ack ? (
                            <Stack spacing={0.25}>
                              <Chip
                                size="small"
                                color="info"
                                label={ack.category}
                              />
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                {ack.reason}
                              </Typography>
                            </Stack>
                          ) : (
                            <Button
                              size="small"
                              onClick={() => setAckTarget(line)}
                            >
                              Reconhecer desvio
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </TableContainer>
          <Dialog
            open={Boolean(detail) || detailLoading}
            onClose={() => {
              setDetail(null);
              setDetailLine(null);
            }}
            maxWidth="xl"
            fullWidth
            PaperProps={{ sx: { minHeight: "88vh" } }}
          >
            <DialogTitle>
              {detail?.department_levels[detail.department_levels.length - 1] ||
                "Detalhe do projeto"}{" "}
              · visão anual {year}
            </DialogTitle>
            <DialogContent sx={{ overflowX: "hidden" }}>
              {detailLoading ? (
                <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
                  <CircularProgress />
                </Box>
              ) : (
                detail && (
                  <Stack spacing={2}>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      gap={1.5}
                      flexWrap="wrap"
                    >
                      <Metric
                        label="Orçamento anual"
                        value={money(detailBudget)}
                        color="#2563eb"
                      />
                      <Metric
                        label="Realizado + Forecast"
                        value={money(detailProjected)}
                        color="#0f766e"
                      />
                      <Metric
                        label="Realizado"
                        value={money(detailActual)}
                        color="#f2a900"
                      />
                      <Metric
                        label="Forecast"
                        value={money(detailForecast)}
                        color="#7c3aed"
                      />
                      <Metric
                        label="Variação"
                        value={money(detailVariance)}
                        color={detailWithinBudget ? "#059669" : "#dc2626"}
                      />
                      <Metric
                        label="Status"
                        value={
                          detailWithinBudget
                            ? "Dentro do orçamento"
                            : "Fora do orçamento"
                        }
                        color={detailWithinBudget ? "#059669" : "#dc2626"}
                      />
                    </Stack>
                    <ToggleButtonGroup exclusive size="small" value={detailTab} onChange={(_, value: number | null) => { if (value !== null) setDetailTab(value); }}>
                      <ToggleButton value={0}>Visão anual</ToggleButton>
                      <ToggleButton value={1}>Registrar desvio</ToggleButton>
                    </ToggleButtonGroup>
                    {detailTab === 0 ? (
                      <>
                        <Typography variant="body2" color="text.secondary">
                          Evolução mensal: orçamento como linha; realizado e
                          forecast como barras empilhadas. No mês corrente, a
                          altura representa a projeção total.
                        </Typography>
                        <Box
                          sx={{
                            width: "100%",
                            height: 360,
                            minHeight: 360,
                            minWidth: 0,
                            overflow: "hidden",
                          }}
                        >
                          <Plot
                            data={[
                              {
                                x: detail.monthly.map((item) =>
                                  item.period.slice(4),
                                ),
                                y: detail.monthly.map(
                                  (item) => item.actual_value,
                                ),
                                type: "bar",
                                name: "Realizado",
                                marker: { color: "#F2A900" },
                              },
                              {
                                x: detail.monthly.map((item) =>
                                  item.period.slice(4),
                                ),
                                y: detail.monthly.map(
                                  (item) => item.forecast_value,
                                ),
                                type: "bar",
                                name: "Forecast",
                                marker: { color: "#22C55E" },
                              },
                              {
                                x: detail.monthly.map((item) =>
                                  item.period.slice(4),
                                ),
                                y: detail.monthly.map(
                                  (item) => item.budget_value,
                                ),
                                type: "scatter",
                                mode: "lines+markers",
                                name: "Orçamento",
                                line: { color: "#64748B", width: 3 },
                              },
                            ]}
                            layout={{
                              height: 360,
                              barmode: "stack",
                              margin: { l: 70, r: 20, t: 20, b: 45 },
                              paper_bgcolor: "transparent",
                              plot_bgcolor: "transparent",
                              yaxis: { tickprefix: "R$ ", tickformat: ".2s" },
                              legend: { orientation: "h" },
                            }}
                            config={{ displayModeBar: false, responsive: true }}
                            style={{ width: "100%", height: "100%" }}
                            useResizeHandler
                          />
                        </Box>
                        <Box>
                          <Stack
                            direction={{ xs: "column", md: "row" }}
                            justifyContent="space-between"
                            alignItems={{ md: "center" }}
                            gap={1}
                            sx={{ mb: 0.5 }}
                          >
                            <Box>
                              <Typography variant="h6">
                                Composição do consumo mensal por serviço
                              </Typography>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                Barras empilhadas mostram a evolução do consumo
                                total e a participação do top 5 em cada mês.
                              </Typography>
                            </Box>
                            <FormControlLabel
                              control={
                                <Switch
                                  size="small"
                                  checked={showOtherSubcategories}
                                  onChange={(event) =>
                                    setShowOtherSubcategories(
                                      event.target.checked,
                                    )
                                  }
                                />
                              }
                              label="Incluir Outros"
                            />
                          </Stack>
                          <Box
                            sx={{
                              width: "100%",
                              height: 440,
                              minHeight: 440,
                              minWidth: 0,
                              overflow: "hidden",
                            }}
                          >
                            <Plot
                              data={[
                                ...detail.subcategories.map((item, index) => ({
                                  x: detail.monthly.map((point) =>
                                    point.period.slice(4),
                                  ),
                                  y: item.monthly_values,
                                  type: "bar",
                                  name: item.name,
                                  marker: {
                                    color: [
                                      "#2563EB",
                                      "#0F766E",
                                      "#F2A900",
                                      "#7C3AED",
                                      "#DC2626",
                                    ][index],
                                  },
                                  hovertemplate: `${item.name}<br>%{x}: R$ %{y:,.0f}<extra></extra>`,
                                })),
                                ...(showOtherSubcategories
                                  ? [
                                      {
                                        x: detail.monthly.map((item) =>
                                          item.period.slice(4),
                                        ),
                                        y: detail.others_monthly,
                                        type: "bar",
                                        name: "Outros",
                                        marker: { color: "#94A3B8" },
                                        hovertemplate:
                                          "Outros<br>%{x}: R$ %{y:,.0f}<extra></extra>",
                                      },
                                    ]
                                  : []),
                              ]}
                              layout={{
                                height: 440,
                                barmode: "stack",
                                margin: { l: 75, r: 20, t: 20, b: 105 },
                                paper_bgcolor: "transparent",
                                plot_bgcolor: "transparent",
                                hovermode: "x unified",
                                yaxis: {
                                  tickprefix: "R$ ",
                                  tickformat: ".2s",
                                  title: "Consumo mensal",
                                },
                                legend: {
                                  orientation: "h",
                                  x: 0,
                                  y: -0.25,
                                  yanchor: "top",
                                },
                              }}
                              config={{
                                displayModeBar: false,
                                responsive: true,
                              }}
                              style={{ width: "100%", height: "100%" }}
                              useResizeHandler
                            />
                          </Box>
                        </Box>
                      </>
                    ) : (
                      <Stack spacing={2} sx={{ py: 2, maxWidth: 720 }}>
                        <Typography variant="body2" color="text.secondary">
                          Registre a categoria e o motivo do desvio para este
                          projeto.
                        </Typography>
                        <FormControl fullWidth>
                          <InputLabel>Categoria</InputLabel>
                          <Select
                            label="Categoria"
                            value={ackCategory}
                            onChange={(event) =>
                              setAckCategory(String(event.target.value))
                            }
                          >
                            <MenuItem value="Pico sazonal">
                              Pico sazonal
                            </MenuItem>
                            <MenuItem value="Projeto novo">
                              Projeto novo
                            </MenuItem>
                            <MenuItem value="Migração">Migração</MenuItem>
                            <MenuItem value="Erro de planejamento">
                              Erro de planejamento
                            </MenuItem>
                            <MenuItem value="Outro">Outro</MenuItem>
                          </Select>
                        </FormControl>
                        <TextField
                          label="Motivo do desvio"
                          value={ackReason}
                          onChange={(event) => setAckReason(event.target.value)}
                          multiline
                          minRows={4}
                          fullWidth
                        />
                        <Button
                          variant="contained"
                          color="warning"
                          onClick={saveAck}
                          disabled={savingAck || !ackReason.trim()}
                          sx={{ alignSelf: "flex-start" }}
                        >
                          Registrar reconhecimento
                        </Button>
                      </Stack>
                    )}
                  </Stack>
                )
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setDetail(null)}>Fechar</Button>
            </DialogActions>
          </Dialog>
          <Dialog
            open={Boolean(ackTarget)}
            onClose={() => setAckTarget(null)}
            maxWidth="sm"
            fullWidth
          >
            <DialogTitle>Justificar desvio de orçamento</DialogTitle>
            <DialogContent>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {ackTarget?.department_levels.join(" › ")}
              </Typography>
              <Stack gap={2}>
                <FormControl fullWidth>
                  <InputLabel>Categoria</InputLabel>
                  <Select
                    label="Categoria"
                    value={ackCategory}
                    onChange={(event) =>
                      setAckCategory(String(event.target.value))
                    }
                  >
                    <MenuItem value="Pico sazonal">Pico sazonal</MenuItem>
                    <MenuItem value="Projeto novo">Projeto novo</MenuItem>
                    <MenuItem value="Migração">Migração</MenuItem>
                    <MenuItem value="Erro de planejamento">
                      Erro de planejamento
                    </MenuItem>
                    <MenuItem value="Outro">Outro</MenuItem>
                  </Select>
                </FormControl>
                <TextField
                  label="Motivo do desvio"
                  value={ackReason}
                  onChange={(event) => setAckReason(event.target.value)}
                  multiline
                  minRows={3}
                  fullWidth
                />
              </Stack>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setAckTarget(null)}>Cancelar</Button>
              <Button
                variant="contained"
                onClick={saveAck}
                disabled={savingAck || !ackReason.trim()}
              >
                Registrar ACK
              </Button>
            </DialogActions>
          </Dialog>
        </>
      )}
    </Box>
  );
}
