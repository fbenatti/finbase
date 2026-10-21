import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState } from "react";
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
  ToggleButton,
  ToggleButtonGroup,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Add } from "@mui/icons-material";
import Plot from "@/components/Plot";
import {
  BudgetAccuracyDetail,
  BudgetAccuracyLine,
  BudgetAccuracyResponse,
  createBudgetAccuracyAck,
  getBudgetAccuracy,
  getBudgetAccuracyDetail,
  getBudgetAccuracyAcks,
  Department,
  getDepartments,
  getForecastConfigs,
} from "../../services/api";
import AcksWidget from "./AcksWidget";

const money = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});
const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const monthNames = [
  "Jan",
  "Fev",
  "Mar",
  "Abr",
  "Mai",
  "Jun",
  "Jul",
  "Ago",
  "Set",
  "Out",
  "Nov",
  "Dez",
];

function findDepartmentId(
  nodes: Department[],
  path: string,
): number | undefined {
  const expected = path.split(" > ");
  const visit = (
    items: Department[],
    parentPath: string[],
  ): number | undefined => {
    for (const item of items) {
      const nextPath = [...parentPath, item.name];
      if (nextPath.join(" > ") === path) return item.id;
      const found = visit(item.children || [], nextPath);
      if (found) return found;
    }
    return undefined;
  };
  return expected.length ? visit(nodes, []) : undefined;
}

type Direction = "underestimated" | "overestimated";

function BudgetRows({
  rows,
  direction,
  expanded,
  onToggle,
  onOpen,
}: {
  rows: BudgetAccuracyLine[];
  direction: Direction;
  expanded: boolean;
  onToggle: () => void;
  onOpen: (line: BudgetAccuracyLine) => void;
}) {
  const ordered = useMemo(() => {
    const sorted = [...rows].sort((a, b) =>
      direction === "underestimated"
        ? b.variance - a.variance
        : a.variance - b.variance,
    );
    return expanded ? sorted : sorted.slice(0, 5);
  }, [rows, direction, expanded]);
  return (
    <Box sx={{ mb: 2 }}>
      <Typography
        variant="subtitle2"
        fontWeight={800}
        sx={{
          color: direction === "underestimated" ? "#DC2626" : "#16A34A",
          mb: 0.5,
        }}
      >
        {expanded
          ? direction === "underestimated"
            ? "Subestimou (gastou mais)"
            : "Superestimou (gastou menos)"
          : direction === "underestimated"
            ? "Top 5 subestimou (gastou mais)"
            : "Top 5 superestimou (gastou menos)"}
      </Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell />
              <TableCell>Projeto</TableCell>
              <TableCell align="right">Orçamento aprovado</TableCell>
              <TableCell align="right">Realizado</TableCell>
              <TableCell align="right">Desvio</TableCell>
              <TableCell>Status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {ordered.map((line) => {
              const name =
                line.department_levels[line.department_levels.length - 1] ||
                "Sem projeto";
              const projected =
                (line.comparison_value ?? line.actual_value) || 0;
              const noActual = line.status === "sem_realizado";
              const valid = noActual
                ? false
                : direction === "underestimated"
                  ? line.variance > 0
                  : line.variance < 0;
              return (
                <TableRow key={line.department_id} hover>
                  <TableCell>
                    <Button
                      size="small"
                      aria-label={`Abrir detalhe de ${name}`}
                      onClick={() => onOpen(line)}
                    >
                      <Add />
                    </Button>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontWeight={700}>
                      {name}
                    </Typography>
                    <Tooltip title={line.department_levels.join(" > ")}>
                      <Chip
                        size="small"
                        variant="outlined"
                        label={line.department_levels.join(" > ")}
                        sx={{ mt: 0.25, maxWidth: 360 }}
                      />
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    {money.format(line.budget_value)}
                  </TableCell>
                  <TableCell align="right">
                    {money.format(line.actual_value)}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{
                      fontWeight: 700,
                      color: line.variance > 0 ? "#DC2626" : "#16A34A",
                    }}
                  >
                    {money.format(line.variance)}
                  </TableCell>
                  <TableCell align="right">{percent(line.variance_pct)}</TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      color={
                        noActual
                          ? "default"
                          : valid
                            ? direction === "underestimated"
                              ? "error"
                              : "success"
                            : "default"
                      }
                      label={
                        noActual
                          ? "Sem realizado"
                          : valid
                            ? direction === "underestimated"
                              ? "Acima do orçamento"
                              : "Abaixo do orçamento"
                            : "Sem consumo"
                      }
                    />
                  </TableCell>
                </TableRow>
              );
            })}
            {!ordered.length && (
              <TableRow>
                <TableCell colSpan={7} align="center">
                  Nenhum projeto encontrado.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
      {rows.length > 5 && (
        <Button size="small" sx={{ mt: 0.5 }} onClick={onToggle}>
          {expanded ? "Ver menos" : "Ver tudo"}
        </Button>
      )}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 0.25 }}
      >
        {direction === "underestimated"
          ? "Ordenado do menor desvio para o maior, incluindo projetos com orçamento sem realizado."
          : "Ordenado do maior valor economizado para o menor."}
      </Typography>
    </Box>
  );
}

function BudgetPanel({ department }: { department?: string }) {
  const now = new Date();
  const [cutoffDays, setCutoffDays] = useState(2);
  const referenceMonth = new Date(
    now.getFullYear(),
    now.getMonth() - (now.getDate() <= cutoffDays ? cutoffDays : 1),
    1,
  );
  const [year, setYear] = useState(referenceMonth.getFullYear());
  const [period, setPeriod] = useState<"monthly" | "annual">("monthly");
  const [month, setMonth] = useState(referenceMonth.getMonth() + 1);
  const [data, setData] = useState<BudgetAccuracyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [underExpanded, setUnderExpanded] = useState(false);
  const [overExpanded, setOverExpanded] = useState(false);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [detail, setDetail] = useState<BudgetAccuracyDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailLine, setDetailLine] = useState<BudgetAccuracyLine | null>(null);
  const [detailTab, setDetailTab] = useState(0);
  const [showOtherSubcategories, setShowOtherSubcategories] = useState(false);
  const [ackCategory, setAckCategory] = useState("Pico sazonal");
  const [ackReason, setAckReason] = useState("");
  const [savingAck, setSavingAck] = useState(false);

  useEffect(() => {
    getForecastConfigs()
      .then((configs) => {
        const active = configs.find((config) => config.active);
        if (active) setCutoffDays(Math.max(0, Number(active.cutoff_days ?? 2)));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const reference = new Date(
      now.getFullYear(),
      now.getMonth() - (now.getDate() <= cutoffDays ? cutoffDays : 1),
      1,
    );
    setYear(reference.getFullYear());
    setMonth(reference.getMonth() + 1);
  }, [cutoffDays]);

  useEffect(() => {
    getDepartments()
      .then(setDepartments)
      .catch(() => setDepartments([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getBudgetAccuracy(
      year,
      period === "monthly" ? month : null,
      findDepartmentId(departments, department || ""),
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
  }, [year, period, month, department, departments]);

  const rows = data?.lines || [];
  const underRows = rows.filter(
    (line) => line.status !== "sem_realizado" && line.variance > 0,
  );
  const overRows = rows.filter(
    (line) => line.status !== "sem_realizado" && line.variance < 0,
  );
  const totals = data?.totals;
  const openDetail = async (line: BudgetAccuracyLine) => {
    setDetailLine(line);
    setDetailTab(0);
    setShowOtherSubcategories(false);
    setDetailLoading(true);
    try {
      setDetail(await getBudgetAccuracyDetail(year, line.department_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDetailLoading(false);
    }
  };
  const detailBudget =
    detail?.monthly.reduce((sum, item) => sum + item.budget_value, 0) || 0;
  const detailActual =
    detail?.monthly.reduce((sum, item) => sum + item.actual_value, 0) || 0;
  const detailForecast =
    detail?.monthly.reduce((sum, item) => sum + item.forecast_value, 0) || 0;
  const detailProjected = detailActual + detailForecast;
  const detailVariance = detailBudget - detailProjected;
  const saveAck = async () => {
    if (!detailLine || !ackReason.trim()) return;
    setSavingAck(true);
    try {
      await createBudgetAccuracyAck({
        target_month: `${year}${String(month).padStart(2, "0")}`,
        planning_version_id: data?.version?.id,
        department_id: detailLine.department_id,
        department: detailLine.department_levels.join(" > "),
        budget_value: detailLine.budget_value,
        actual_value: detailLine.actual_value,
        category: ackCategory,
        reason: ackReason.trim(),
      });
      setAckReason("");
      setDetailTab(0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSavingAck(false);
    }
  };
  return (
    <Stack spacing={1.5}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={1}
      >
        <Box>
          <Typography variant="h6">Acurácia de orçamento</Typography>
          <Typography variant="body2" color="text.secondary">
            Valor realizado comparado ao orçamento aprovado do projeto.
          </Typography>
        </Box>
        <Stack direction="row" gap={1}>
          <FormControl size="small" sx={{ minWidth: 100 }}>
            <InputLabel>Ano</InputLabel>
            <Select
              label="Ano"
              value={year}
              onChange={(event) => setYear(Number(event.target.value))}
            >
              {[2025, 2026, 2027].map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 125 }}>
            <InputLabel>Período</InputLabel>
            <Select
              label="Período"
              value={period}
              onChange={(event) =>
                setPeriod(event.target.value as "monthly" | "annual")
              }
            >
              <MenuItem value="annual">Ano</MenuItem>
              <MenuItem value="monthly">Mês</MenuItem>
            </Select>
          </FormControl>
          {period === "monthly" && (
            <FormControl size="small" sx={{ minWidth: 105 }}>
              <InputLabel>Mês</InputLabel>
              <Select
                label="Mês"
                value={month}
                onChange={(event) => setMonth(Number(event.target.value))}
              >
                {monthNames.map((name, index) => (
                  <MenuItem key={name} value={index + 1}>
                    {name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
        </Stack>
      </Stack>
      {error && <Alert severity="error">{dataDisplayText(error)}</Alert>}
      {loading ? (
        <Box sx={{ py: 3, display: "flex", justifyContent: "center" }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <>
          {totals && (
            <Stack
              direction={{ xs: "column", sm: "row" }}
              gap={1}
              sx={{ mb: 1 }}
            >
              <Chip label={`Orçamento: ${money.format(totals.budget_value)}`} />
              <Chip
                label={`${period === "annual" ? "Realizado + Forecast" : "Realizado"}: ${money.format(totals.comparison_value ?? totals.actual_value)}`}
                color="primary"
              />
              <Chip
                label={`Desvio: ${money.format(totals.variance)}`}
                color={totals.variance < 0 ? "error" : "success"}
              />
            </Stack>
          )}
          <BudgetRows
            rows={underRows}
            direction="underestimated"
            expanded={underExpanded}
            onToggle={() => setUnderExpanded((value) => !value)}
            onOpen={(line) => void openDetail(line)}
          />
          <BudgetRows
            rows={overRows}
            direction="overestimated"
            expanded={overExpanded}
            onToggle={() => setOverExpanded((value) => !value)}
            onOpen={(line) => void openDetail(line)}
          />
          <Dialog
            open={Boolean(detail) || detailLoading}
            onClose={() => {
              setDetail(null);
              setDetailLine(null);
            }}
            maxWidth="xl"
            fullWidth
            PaperProps={{ sx: { minHeight: "86vh" } }}
          >
            <DialogTitle>
              {detail?.department_levels[detail.department_levels.length - 1] ||
                "Detalhe do projeto"}{" "}
              · visão anual {year}
            </DialogTitle>
            <DialogContent>
              {detailLoading ? (
                <Box sx={{ display: "flex", justifyContent: "center", p: 6 }}>
                  <CircularProgress />
                </Box>
              ) : (
                detail && (
                  <Stack spacing={2}>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      gap={1}
                      flexWrap="wrap"
                    >
                      <Chip
                        label={`Orçamento anual: ${money.format(detailBudget)}`}
                      />
                      <Chip
                        color="primary"
                        label={`Realizado + Forecast: ${money.format(detailProjected)}`}
                      />
                      <Chip
                        label={`Realizado: ${money.format(detailActual)}`}
                      />
                      <Chip
                        label={`Forecast: ${money.format(detailForecast)}`}
                      />
                      <Chip
                        color={detailVariance < 0 ? "error" : "success"}
                        label={`Variação: ${money.format(detailVariance)}`}
                      />
                      <Chip
                        color={detailVariance < 0 ? "error" : "success"}
                        label={
                          detailVariance < 0
                            ? "Fora do orçamento"
                            : "Dentro do orçamento"
                        }
                      />
                    </Stack>
                    <ToggleButtonGroup exclusive size="small" value={detailTab} onChange={(_, value: number | null) => { if (value !== null) setDetailTab(value); }}>
                      <ToggleButton value={0}>Visão anual</ToggleButton>
                      <ToggleButton value={1}>Registrar desvio</ToggleButton>
                    </ToggleButtonGroup>
                    {detailTab === 0 ? (
                      <>
                        <Box
                          sx={{ width: "100%", height: 360, minHeight: 360 }}
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
                              margin: { l: 70, r: 20, t: 20, b: 55 },
                              yaxis: { tickprefix: "R$ ", tickformat: ".2s" },
                              legend: { orientation: "h" },
                            }}
                            config={{ displayModeBar: false, responsive: true }}
                            style={{ width: "100%", height: "100%" }}
                            useResizeHandler
                          />
                        </Box>
                        <Box
                          sx={{ width: "100%", height: 500, minHeight: 500 }}
                        >
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            justifyContent="space-between"
                            alignItems={{ sm: "center" }}
                            gap={1}
                          >
                            <Box>
                              <Typography variant="h6">
                                Composição do consumo mensal por serviço
                              </Typography>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                Top 5 anual do projeto; ative Outros para exibir
                                o restante.
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
                          <Typography variant="body2" color="text.secondary">
                            Top 5 anual do projeto, com a opção de detalhar
                            outras categorias na tela de Acurácia de Orçamento.
                          </Typography>
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
                              })),
                              ...(showOtherSubcategories
                                ? [
                                    {
                                      x: detail.monthly.map((item) =>
                                        item.period.slice(4),
                                      ),
                                      y: detail.others_monthly,
                                      type: "bar" as const,
                                      name: "Outros",
                                      marker: { color: "#94A3B8" },
                                    },
                                  ]
                                : []),
                            ]}
                            layout={{
                              height: 440,
                              barmode: "stack",
                              margin: { l: 75, r: 20, t: 30, b: 100 },
                              yaxis: { tickprefix: "R$ ", tickformat: ".2s" },
                              legend: {
                                orientation: "h",
                                x: 0,
                                y: -0.25,
                                yanchor: "top",
                              },
                            }}
                            config={{ displayModeBar: false, responsive: true }}
                            style={{ width: "100%", height: "100%" }}
                            useResizeHandler
                          />
                        </Box>
                      </>
                    ) : (
                      <Stack spacing={2} sx={{ maxWidth: 720, py: 2 }}>
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
                            {[
                              "Pico sazonal",
                              "Projeto novo",
                              "Migração",
                              "Erro de planejamento",
                              "Outro",
                            ].map((value) => (
                              <MenuItem key={value} value={value}>
                                {value}
                              </MenuItem>
                            ))}
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
                          onClick={() => void saveAck()}
                          disabled={savingAck || !ackReason.trim()}
                          sx={{ alignSelf: "flex-start" }}
                        >
                          {savingAck
                            ? "Salvando..."
                            : "Registrar reconhecimento"}
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
        </>
      )}
    </Stack>
  );
}

export default function AccuracyWidgetV2({
  department,
  dimensionFilters = {},
}: {
  department?: string;
  dimensionFilters?: Record<string, string[]>;
}) {
  const [tab, setTab] = useState(0);
  if (Object.values(dimensionFilters).some((values) => values.length)) {
    return <Paper elevation={0} sx={{ p: 2, borderRadius: 2, border: "1px solid", borderColor: "divider" }}>
      <Typography variant="h6" sx={{ mb: 1 }}>Acurácia</Typography>
      <ToggleButtonGroup exclusive size="small" value={tab} onChange={(_, value: number | null) => { if (value !== null) setTab(value); }} sx={{ mb: 2 }}>
        <ToggleButton value={0}>Acurácia de projeções</ToggleButton>
        <ToggleButton value={1}>Acurácia do orçamento</ToggleButton>
      </ToggleButtonGroup>
      <Alert severity="info">Acurácia indisponível para este filtro dimensional: os snapshots atuais não guardam esta dimensão.</Alert>
    </Paper>;
  }
  return (
    <Paper
      elevation={0}
      sx={{
        p: 2,
        borderRadius: 2,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <ToggleButtonGroup exclusive size="small" value={tab} onChange={(_, value: number | null) => { if (value !== null) setTab(value); }} sx={{ mb: 2 }}>
        <ToggleButton value={0}>Acurácia de projeções</ToggleButton>
        <ToggleButton value={1}>Acurácia do orçamento</ToggleButton>
      </ToggleButtonGroup>
      {tab === 0 ? (
        <AcksWidget department={department} hideManualHighlights />
      ) : (
        <BudgetPanel department={department} />
      )}
    </Paper>
  );
}
