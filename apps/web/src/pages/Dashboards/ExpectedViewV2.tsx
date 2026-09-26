import { useEffect, useMemo, useState, type ReactElement } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Plot from "@/components/Plot";
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  FormControl,
  FormControlLabel,
  Grid,
  IconButton,
  InputLabel,
  Link,
  List,
  ListItemButton,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { ChevronRight, ExpandMore } from "@mui/icons-material";
import {
  Department,
  formatApiError,
  getDepartments,
  getExpectedView,
  ExpectedViewDriver,
  ExpectedViewRow,
  GoldQueryFilter,
  GoldQueryResult,
  queryDimonTable,
  queryGoldDailyConsumption,
} from "../../services/api";
import AccuracyWidget from "./BudgetAccuracyWidgetV2";
import ConsumptionDrillDialog from "../GoldDashboard/ConsumptionDrillDialog";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const monthNames = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const monthLong = (period: string) => `${monthNames[Number(period.slice(4, 6)) - 1] || period} ${period.slice(0, 4)}`;
const compactMoney = (value: number) => Math.abs(value) >= 1_000_000
  ? `R$ ${(value / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MM`
  : `R$ ${money.format(value)}`;
type ChartGranularity = "accumulated" | "monthly" | "daily";
const FILTER_PALETTE = ["#6416dc", "#9c4dff", "#3b82f6", "#0f9f83", "#ec8b13", "#d9468d", "#53627d", "#00a8cc", "#a33bd1", "#6f9b20"];
const flattenDepartments = (
  nodes: Department[],
  parents: string[] = [],
): Array<Department & { path: string }> =>
  nodes.flatMap((node) => {
    const path = [...parents, node.name];
    return [
      { ...node, path: path.join(" > ") },
      ...flattenDepartments(node.children || [], path),
    ];
  });

export default function ExpectedViewV2({
  embedded = false,
  director: externalDirector,
  departmentId: externalDepartmentId,
  directorView = false,
  table,
  footer,
  periodFrom,
  periodTo,
  scopeKey,
  dimensionFilters = {},
  organizationNodeIds = [],
  includeSharedCost: externalSharedCost,
  groupByOptions = [],
  chartHasType = false,
  chartFilters = [],
  chartFilterGroups = [],
}: {
  embedded?: boolean;
  director?: string;
  departmentId?: number;
  directorView?: boolean;
  table?: string;
  footer?: ReactElement;
  periodFrom?: string;
  periodTo?: string;
  scopeKey?: string;
  dimensionFilters?: Record<string, string[]>;
  organizationNodeIds?: number[];
  includeSharedCost?: boolean;
  groupByOptions?: string[];
  chartHasType?: boolean;
  chartFilters?: GoldQueryFilter[];
  chartFilterGroups?: GoldQueryFilter[][];
} = {}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [rows, setRows] = useState<ExpectedViewRow[]>([]);
  const [drivers, setDrivers] = useState<ExpectedViewDriver[]>([]);
  const [budgetAvailable, setBudgetAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [director, setDirector] = useState(externalDirector ?? "Consumo Cloud");
  const [departmentOptions, setDepartmentOptions] = useState<
    Array<Department & { path: string }>
  >([]);
  const [departmentId, setDepartmentId] = useState<number | undefined>(
    externalDepartmentId,
  );
  // Filtro global de departamento: quando o pai (FinOps Intelligence) muda o
  // escopo, sincroniza e recarrega os dados.
  // O filtro global também pode voltar para "Todos" (undefined). Nesse caso
  // é importante limpar o escopo local, senão Variância e Ack ficam presos
  // ao último nó selecionado na árvore.
  useEffect(() => {
    if (externalDirector !== undefined) setDirector(externalDirector);
  }, [externalDirector]);
  useEffect(() => {
    setDepartmentId(externalDepartmentId);
  }, [externalDepartmentId]);
  const [organizationLoaded, setOrganizationLoaded] = useState(false);
  const [organization, setOrganization] = useState<Department[]>([]);
  const [organizationAnchor, setOrganizationAnchor] =
    useState<HTMLElement | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [periodFilter, setPeriodFilter] = useState("current");
  const [chartGranularity, setChartGranularity] = useState<ChartGranularity>("accumulated");
  const [groupBy, setGroupBy] = useState("");
  const orderedGroupByOptions = useMemo(() => [...groupByOptions].sort((a, b) => {
    const label = (field: string) => field.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());
    return label(a).localeCompare(label(b), "pt-BR", { sensitivity: "base" });
  }), [groupByOptions]);
  const [groupedGold, setGroupedGold] = useState<GoldQueryResult | null>(null);
  const [dailyGold, setDailyGold] = useState<{ available: boolean; rows: Array<{ day: string; realizado: number; [column: string]: unknown }> } | null>(null);
  const [chartQueryLoading, setChartQueryLoading] = useState(false);
  const [chartQueryError, setChartQueryError] = useState("");
  const cumulative = chartGranularity === "accumulated";
  const [includeSharedCost, setIncludeSharedCost] = useState(
    params.get("shared") === "1",
  );
  useEffect(() => {
    if (externalSharedCost !== undefined) setIncludeSharedCost(externalSharedCost);
  }, [externalSharedCost]);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [drillOpen, setDrillOpen] = useState(false);
  const [drillPeriod, setDrillPeriod] = useState<string | undefined>();
  const [drillTipo, setDrillTipo] = useState<"realizado" | "forecast">(
    "realizado",
  );
  const rangeDays = useMemo(() => {
    if (!periodFrom || !periodTo) return 0;
    const start = new Date(Number(periodFrom.slice(0, 4)), Number(periodFrom.slice(4, 6)) - 1, 1);
    const end = new Date(Number(periodTo.slice(0, 4)), Number(periodTo.slice(4, 6)), 0);
    return Math.floor((end.getTime() - start.getTime()) / 86400000) + 1;
  }, [periodFrom, periodTo]);
  useEffect(() => {
    if (chartGranularity === "daily" && rangeDays > 45) setChartGranularity("monthly");
  }, [chartGranularity, rangeDays]);
  useEffect(() => {
    let cancelled = false;
    getDepartments()
      .then((departments: Department[]) => {
        if (cancelled) return;
        const flat = flattenDepartments(departments).filter(
          (department) => department.active,
        );
        setDepartmentOptions(flat);
        setOrganization(departments);
        const cloud = flat.find(
          (department) =>
            department.name === "Consumo Cloud" &&
            department.parent_id === null,
        );
        if (!embedded && cloud) {
          setDepartmentId(cloud.id);
          setDirector(cloud.path);
          setExpandedNodes(new Set([cloud.id]));
        }
      })
      .catch(() => {
        // The current director remains usable if the hierarchy is temporarily unavailable.
      })
      .finally(() => {
        if (!cancelled) setOrganizationLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    // Wait for the tree before querying.  Previously the page sent one broad
    // request during mount and a second request after resolving Consumo Cloud,
    // duplicating a costly seven-level Trino aggregation.
    if (!organizationLoaded) return;
    const controller = new AbortController();
    // Several controlled filters settle during mount. Debounce them so only
    // the final state reaches Trino; cancellation also stops an in-flight
    // request when the user changes the selection again.
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      const selectedId = directorView
        ? undefined
        : departmentOptions.find((department) => department.path === director)
            ?.id;
      // "Todos os períodos" envia year=0: o backend remove o recorte de ano e
      // traz todo o realizado + forecast disponível (inclusive meses futuros).
      const year = periodFrom && periodTo
        ? (periodFrom.slice(0, 4) === periodTo.slice(0, 4) ? Number(periodFrom.slice(0, 4)) : 0)
        : periodFilter === "all"
          ? 0
          : periodFilter === "current"
            ? new Date().getFullYear()
            : Number(periodFilter);
      getExpectedView(
        director,
        year,
        departmentId ?? selectedId,
        controller.signal,
        includeSharedCost,
        directorView,
        table,
        { monthFrom: periodFrom, monthTo: periodTo, departmentIds: organizationNodeIds, dimensionFilters },
      )
        .then((result) => {
          setRows(result.rows.filter((row) => (!periodFrom || row.period >= periodFrom) && (!periodTo || row.period <= periodTo)));
          setDrivers(result.drivers);
          setBudgetAvailable(result.budget_available !== false);
        })
        .catch((e) => {
          if (
            (e as { code?: string }).code === "ERR_CANCELED" ||
            controller.signal.aborted
          )
            return;
          setError(
            formatApiError(e, "Não foi possível carregar a Visão Esperada"),
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    director,
    departmentId,
    organizationLoaded,
    periodFilter,
    reloadNonce,
    includeSharedCost,
    directorView,
    periodFrom,
    periodTo,
    scopeKey,
    organizationNodeIds,
    dimensionFilters,
  ]);
  useEffect(() => {
    if (!embedded || !table) return;
    const controller = new AbortController();
    let active = true;
    setChartQueryError("");
    setGroupedGold(null);
    setDailyGold(null);
    if (chartGranularity === "daily" || groupBy) setChartQueryLoading(true);
    else {
      setGroupedGold(null);
      setDailyGold(null);
      setChartQueryLoading(false);
      return;
    }
    const query = chartGranularity === "daily"
      ? queryGoldDailyConsumption({ table, filters: chartFilters, filter_groups: chartFilterGroups, group_by: groupBy || null, row_limit: 10000 })
      : queryDimonTable({ table, rows: [groupBy, "_billing_period", ...(chartHasType ? ["tipo"] : [])], measures: [{ column: "realizado", aggregation: "SUM" }], filters: chartFilters, filter_groups: chartFilterGroups, row_limit: 10000 });
    query.then((result) => {
      if (!active) return;
      if (chartGranularity === "daily") setDailyGold(result as { available: boolean; rows: Array<{ day: string; realizado: number; [column: string]: unknown }> });
      else setGroupedGold(result as GoldQueryResult);
    }).catch((reason: unknown) => {
      if (active) setChartQueryError(formatApiError(reason, "Não foi possível carregar a série do gráfico"));
    }).finally(() => { if (active) setChartQueryLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [embedded, table, chartGranularity, groupBy, chartFilters, chartFilterGroups, scopeKey, chartHasType]);
  const years = [...new Set(rows.map((row) => row.period.slice(0, 4)))].sort();
  const filteredRows = periodFrom && periodTo
    ? rows.filter((row) => row.period >= periodFrom && row.period <= periodTo)
    : periodFilter === "all"
      ? rows
      : periodFilter === "current"
        ? rows.filter((row) =>
            row.period.startsWith(String(new Date().getFullYear())),
          )
        : rows.filter((row) => row.period.startsWith(periodFilter));
  const toDate = (period: string) =>
    period.length === 6
      ? `${period.slice(0, 4)}-${period.slice(4, 6)}-01`
      : period;
  // The current (open) month carries both partial realized and the
  // remaining-days forecast (baseline × dias restantes). Those two never
  // overlap, so the forecast must be stacked on top of the realized bar —
  // otherwise the current month shows only the partial spend and the
  // remaining projection disappears from the chart, cards and cumulative.
  const now = new Date();
  const currentMonthKey = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}`;
  const isCurrentMonth = (row: ExpectedViewRow) =>
    row.period === currentMonthKey;
  const sharedOn = includeSharedCost;
  const sharedOf = (row: ExpectedViewRow) => Number(row.shared_value || 0);
  const receivedOf = (row: ExpectedViewRow) => Number(row.shared_received || 0);
  const givenOf = (row: ExpectedViewRow) => Number(row.shared_given || 0);
  const consumptionOf = (row: ExpectedViewRow) => {
    if (row.actual_value == null) return 0;
    return Math.max(
      0,
      Number(row.actual_value || 0) + (sharedOn ? givenOf(row) : 0),
    );
  };
  const realizedOf = (row: ExpectedViewRow) =>
    row.actual_value == null
      ? 0
      : Number(row.actual_value || 0) + (sharedOn ? sharedOf(row) : 0);
  const forecastManualOf = (row: ExpectedViewRow) =>
    Number(row.forecast_manual_value || 0);
  const expectedOf = (row: ExpectedViewRow) =>
    row.actual_value != null
      ? Number(row.actual_value || 0) +
        (isCurrentMonth(row) ? Number(row.forecast_value || 0) : 0) +
        forecastManualOf(row) +
        (sharedOn ? sharedOf(row) : 0)
      : Number(row.forecast_value ?? 0) +
        forecastManualOf(row) +
        (sharedOn ? sharedOf(row) : 0);
  const labels = filteredRows.map((row) => toDate(row.period));
  const actual = filteredRows.reduce((sum, row) => sum + realizedOf(row), 0);
  const budget = filteredRows.reduce(
    (sum, row) => sum + Number(row.budget_value || 0),
    0,
  );
  const forecast = filteredRows.reduce((sum, row) => sum + expectedOf(row), 0);
  const variance = budget - forecast;
  const forecastOnly = forecast - actual;
  const realizedBudgetPct = budgetAvailable && budget > 0 ? `${(actual / budget * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "Sem orçamento";
  const actualEnd = filteredRows.reduce(
    (last, row, index) => (row.actual_value == null ? last : index),
    -1,
  );
  // Mês corrente aberto: no acumulado, a linha de Realizado termina no último
  // mês FECHADO (julho) e o Forecast assume a partir do mês corrente tratando
  // o mês inteiro como forecast — a transição vira um passo mensal normal,
  // sem ponto duplicado nem quebra.
  const currentOpen =
    actualEnd >= 0 &&
    isCurrentMonth(filteredRows[actualEnd]) &&
    Number(filteredRows[actualEnd].forecast_value || 0) > 0;
  const realizedEnd = currentOpen ? actualEnd - 1 : actualEnd;
  const consumptionCumulative = filteredRows.map((_, index) =>
    index <= realizedEnd
      ? filteredRows
          .slice(0, index + 1)
          .reduce((sum, item) => sum + consumptionOf(item), 0)
      : null,
  );
  const actualCumulative = filteredRows.map((_, index) =>
    index <= realizedEnd
      ? filteredRows
          .slice(0, index + 1)
          .reduce((sum, item) => sum + realizedOf(item), 0)
      : null,
  );
  const forecastCumulative = filteredRows.map((_, index) => {
    if (index < actualEnd) return null;
    return filteredRows
      .slice(0, index + 1)
      .reduce((sum, item) => sum + expectedOf(item), 0);
  });
  const forecastStart = actualEnd >= 0 ? actualEnd : 0;
  // Ponte: a linha de Forecast parte do ÚLTIMO PONTO do Realizado (último mês
  // fechado) para não deixar buraco entre a amarela e a verde no eixo x; o mês
  // corrente entra no acumulado como forecast (realizado parcial + restante).
  const bridge =
    currentOpen &&
    realizedEnd >= 0 &&
    consumptionCumulative[realizedEnd] != null
      ? { x: labels[realizedEnd], y: consumptionCumulative[realizedEnd] }
      : null;
  const forecastLabels = bridge
    ? [bridge.x, ...labels.slice(forecastStart)]
    : labels.slice(forecastStart);
  const forecastFilled = bridge
    ? [bridge.y, ...forecastCumulative.slice(forecastStart)]
    : forecastCumulative.slice(forecastStart);
  const budgetCumulative = filteredRows.map((_, index) =>
    filteredRows
      .slice(0, index + 1)
      .reduce((sum, item) => sum + Number(item.budget_value || 0), 0),
  );
  // Future months carry the full-month forecast; the current (open) month
  // carries the remaining-days forecast, stacked on top of its partial
  // realized. Closed months have no meaningful forecast (zeroed by the engine).
  const forecastBars = filteredRows.map((row) =>
    row.actual_value == null || isCurrentMonth(row)
      ? Math.max(
          0,
          Number(row.forecast_value || 0) + (sharedOn ? givenOf(row) : 0),
        )
      : 0,
  );
  // Forecast manual is its own stacked layer on top of the Gold forecast.
  const forecastManualBars = filteredRows.map((row) =>
    row.actual_value == null ? Math.max(0, forecastManualOf(row)) : 0,
  );
  const forecastManualCumulative = filteredRows.map((_, index) => {
    if (index < forecastStart) return null;
    return filteredRows
      .slice(0, index + 1)
      .reduce(
        (sum, item) =>
          sum + (item.actual_value == null ? forecastManualOf(item) : 0),
        0,
      );
  });
  const forecastManualFilled = bridge
    ? [0, ...forecastManualCumulative.slice(forecastStart)]
    : forecastManualCumulative.slice(forecastStart);
  const receivedOnActual = filteredRows.map((row) =>
    sharedOn && row.actual_value != null ? Math.max(0, receivedOf(row)) : 0,
  );
  const receivedOnForecast = filteredRows.map((row) =>
    sharedOn && row.actual_value == null ? Math.max(0, receivedOf(row)) : 0,
  );
  const sharedLine = { color: "#EA580C", width: 3 };
  const sharedFill = "rgba(234,88,12,.28)";
  const forecastBaseCumulative = filteredRows.map((_, index) => {
    if (index < actualEnd) return null;
    return filteredRows.slice(0, index + 1).reduce((sum, item) => {
      if (item.actual_value != null)
        return (
          sum +
          consumptionOf(item) +
          (isCurrentMonth(item) ? Number(item.forecast_value || 0) : 0)
        );
      return (
        sum + Number(item.forecast_value || 0) + (sharedOn ? givenOf(item) : 0)
      );
    }, 0);
  });
  const forecastBaseFilled = bridge
    ? [bridge.y, ...forecastBaseCumulative.slice(forecastStart)]
    : forecastBaseCumulative.slice(forecastStart);
  const chartData: any[] = cumulative
    ? [
        {
          x: labels,
          y: consumptionCumulative,
          type: "scatter",
          mode: "lines+markers",
          name: "Realizado",
          fill: "tozeroy",
          fillcolor: "rgba(242,169,0,.22)",
          line: { color: "#F2A900", width: 3 },
          marker: { size: 6 },
        },
        ...(sharedOn
          ? [
              {
                x: labels,
                y: actualCumulative,
                type: "scatter",
                mode: "lines+markers",
                name: "Custo compartilhado",
                legendgroup: "shared",
                fill: "tonexty",
                fillcolor: sharedFill,
                line: sharedLine,
                marker: { size: 6 },
              },
            ]
          : []),
        ...(budgetAvailable ? [{
          x: labels,
          y: budgetCumulative,
          type: "scatter",
          mode: "lines+markers",
          name: "Orçamento",
          line: { color: "#64748B", width: 3 },
          marker: { size: 5 },
        }] : []),
        ...(sharedOn
          ? [
              {
                x: forecastLabels,
                y: forecastBaseFilled,
                type: "scatter",
                mode: "lines",
                name: "Forecast base",
                showlegend: false,
                hoverinfo: "skip",
                fill: "tozeroy",
                fillcolor: "rgba(34,197,94,.18)",
                line: { color: "#22C55E", width: 0 },
              },
              {
                x: forecastLabels,
                y: forecastFilled,
                type: "scatter",
                mode: "lines+markers",
                name: "Custo compartilhado",
                legendgroup: "shared",
                showlegend: false,
                fill: "tonexty",
                fillcolor: sharedFill,
                line: sharedLine,
                marker: { size: 6, color: "#EA580C" },
              },
            ]
          : []),
        {
          x: forecastLabels,
          y: forecastFilled,
          type: "scatter",
          mode: "lines+markers",
          name: "Forecast",
          fill: sharedOn ? "none" : "tozeroy",
          fillcolor: "rgba(34,197,94,.22)",
          line: { color: "#22C55E", width: 3 },
          marker: { size: 6 },
        },
        {
          x: forecastLabels,
          y: forecastManualFilled,
          type: "scatter",
          mode: "lines+markers",
          name: "Forecast manual",
          fill: "tonexty",
          fillcolor: "rgba(134,239,172,.30)",
          line: { color: "#4ADE80", width: 2 },
          marker: { size: 5, color: "#86EFAC" },
        },
      ]
    : [
        {
          x: labels,
          y: filteredRows.map(consumptionOf),
          type: "bar",
          name: "Realizado",
          marker: { color: "#F2A900" },
        },
        ...(sharedOn
          ? [
              {
                x: labels,
                y: receivedOnActual,
                type: "bar",
                name: "Custo compartilhado",
                legendgroup: "shared",
                marker: { color: "#EA580C" },
              },
            ]
          : []),
        {
          x: labels,
          y: forecastBars,
          type: "bar",
          name: "Forecast",
          marker: { color: "#22C55E" },
        },
        {
          x: labels,
          y: forecastManualBars,
          type: "bar",
          name: "Forecast manual",
          marker: { color: "#86EFAC" },
        },
        ...(sharedOn
          ? [
              {
                x: labels,
                y: receivedOnForecast,
                type: "bar",
                name: "Custo compartilhado",
                legendgroup: "shared",
                showlegend: false,
                marker: { color: "#EA580C" },
              },
            ]
          : []),
        ...(budgetAvailable ? [{
          x: labels,
          y: filteredRows.map((row) => Number(row.budget_value || 0)),
          type: "scatter",
          mode: "lines+markers",
          name: "Orçamento",
          line: { color: "#64748B", width: 3 },
          marker: { size: 6 },
        }] : []),
      ];
  const groupedRows = groupedGold?.rows || [];
  const dailyRows = dailyGold?.rows || [];
  // Trino returns source column names with their physical casing. Gold
  // metadata and some logical fields are lowercase, so resolve row values
  // case-insensitively instead of silently treating every row as missing.
  const groupedField = (row: Record<string, unknown>, field: string) => {
    if (field in row) return row[field];
    const key = Object.keys(row).find((candidate) => candidate.toLowerCase() === field.toLowerCase());
    return key ? row[key] : undefined;
  };
  const groupedLabels = chartGranularity === "daily"
    ? [...new Set(dailyRows.map((row) => String(groupedField(row, "day"))))].sort()
    : filteredRows.map((row) => row.period);
  const groupedSource = chartGranularity === "daily" ? dailyRows : groupedRows;
  const groupKeyField = chartGranularity === "daily" ? "day" : "_billing_period";
  const groupNames = groupBy
    ? [...new Set(groupedSource.map((row) => String(groupedField(row, groupBy) ?? "Não informado")))].sort((a, b) => {
        const sum = (name: string) => groupedSource.filter((row) => String(groupedField(row, groupBy) ?? "Não informado") === name && String(groupedField(row, "tipo") ?? "").toLowerCase() !== "forecast").reduce((total, row) => total + Number(groupedField(row, "realizado") || 0), 0);
        return sum(b) - sum(a);
      })
    : ["Realizado"];
  const visibleGroups = groupNames.slice(0, 6);
  const rowValues = (name: string, period: string, kind: "actual" | "forecast") => groupedSource
    .filter((row) => {
      const rowType = String(groupedField(row, "tipo") ?? "").trim().toLowerCase();
      const matchesKind = kind === "forecast" ? rowType === "forecast" : rowType !== "forecast" && rowType !== "rateio";
      return String(groupedField(row, groupKeyField)) === period && (!groupBy || String(groupedField(row, groupBy) ?? "Não informado") === name) && matchesKind;
    })
    .reduce((total, row) => total + Number(groupedField(row, "realizado") || 0), 0);
  const forecastEligible = (period: string) => {
    const expectedRow = filteredRows.find((row) => row.period === period);
    return Boolean(expectedRow && (expectedRow.actual_value == null || isCurrentMonth(expectedRow)));
  };
  const groupTraces: any[] = visibleGroups.flatMap<any>((name, index) => {
    const color = FILTER_PALETTE[index % FILTER_PALETTE.length];
    const forecastColor = color;
    const actualValues = groupedLabels.map((period) => rowValues(name, period, "actual"));
    const forecastValues = groupedLabels.map((period) => forecastEligible(period) ? rowValues(name, period, "forecast") : 0);
    if (chartGranularity === "accumulated") {
      const actualCumulativeByMonth = actualValues.map((_, i) => i <= realizedEnd ? actualValues.slice(0, i + 1).reduce((sum, value) => sum + value, 0) : null);
      const forecastCumulativeByMonth = actualValues.map((_, i) => groupedLabels.slice(0, i + 1).reduce((sum, period, j) => sum + actualValues[j] + forecastValues[j], 0));
      const projectedValues = forecastCumulativeByMonth.map((value, i) => i < forecastStart ? null : value);
      return [
        { x: groupedLabels, y: actualCumulativeByMonth, type: "scatter", mode: "lines+markers", name: `${name} · Realizado`, fill: "tozeroy", fillcolor: `${color}22`, line: { color, width: 2.5 }, marker: { size: 5 } },
        { x: groupedLabels, y: projectedValues, type: "scatter", mode: "lines+markers", name: `${name} · Forecast`, fill: "tozeroy", fillcolor: `${forecastColor}18`, line: { color: forecastColor, width: 2.5, dash: "dash" }, marker: { size: 5 } },
      ];
    }
    return [
      { x: groupedLabels, y: actualValues, type: "bar", name: `${name} · Realizado`, marker: { color } },
      { x: groupedLabels, y: forecastValues, type: "bar", name: `${name} · Forecast`, marker: { color: forecastColor, pattern: { shape: "/" } } },
    ];
  });
  const dimensionTraces = groupTraces;
  const groupedBudgetTrace = groupBy && chartGranularity !== "daily" && budgetAvailable
    ? [{
        x: groupedLabels,
        y: cumulative ? budgetCumulative : filteredRows.map((row) => Number(row.budget_value || 0)),
        type: "scatter",
        mode: "lines+markers",
        name: "Orçamento",
        line: { color: "#64748B", width: 3 },
        marker: { size: 5 },
      }]
    : [];
  const chartDataForRender = chartGranularity === "daily"
    ? dimensionTraces
    : groupBy
      ? [...dimensionTraces, ...groupedBudgetTrace]
      : chartData;
  const chartLayout: any = {
    autosize: true,
    barmode: "stack",
    margin: { l: 82, r: 24, t: 20, b: 82 },
    hovermode: "x unified",
    xaxis: {
      type: "date",
      tickformat: "%b %Y",
      dtick: "M1",
      title: { text: "Período", standoff: 18 },
      side: "bottom",
      showticklabels: true,
      tickangle: -35,
    },
    yaxis: {
      title: {
        text: cumulative ? "Valor acumulado (R$)" : "Valor mensal (R$)",
        standoff: 12,
      },
      side: "left",
      showticklabels: true,
      tickprefix: "R$ ",
      tickformat: ".2s",
      zeroline: true,
      rangemode: "tozero",
    },
    legend: { orientation: "h", y: -0.28 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
  };
  const chartLayoutForRender = {
    ...chartLayout,
    barmode: chartGranularity === "daily" || groupBy ? "stack" : chartLayout.barmode,
    xaxis: {
      ...chartLayout.xaxis,
      ...(chartGranularity === "daily"
        ? { type: "date", tickformat: "%d/%m", dtick: undefined, tickangle: -35 }
        : groupBy
          ? { type: "category", tickformat: undefined, dtick: undefined }
          : {}),
    },
  };
  const sortedDrivers = drivers
    .filter((driver) => Math.abs(driver.variance) > 0.005)
    .sort((a, b) => Math.abs(b.variance) - Math.abs(a.variance));
  const leadingDrivers = sortedDrivers.slice(0, 8);
  const otherDrivers = sortedDrivers
    .slice(8)
    .reduce((total, item) => total + item.variance, 0);
  // Waterfall: Orçamento → drivers → Forecast. Cada driver.variance = orçamento −
  // forecast; o passo relativo precisa ser (forecast − orçamento), senão o total
  // vira 2×orçamento − forecast e inverte a leitura quando forecast > orçamento.
  const driverStep = (variance: number) => -variance;
  const waterfallLabels = [
    "Orçamento",
    ...leadingDrivers.map((item) => item.name),
    ...(sortedDrivers.length > 8 ? ["Outros"] : []),
    "Forecast",
  ];
  const waterfallValues = [
    budget,
    ...leadingDrivers.map((item) => driverStep(item.variance)),
    ...(sortedDrivers.length > 8 ? [driverStep(otherDrivers)] : []),
    0,
  ];
  const waterfallMeasures = [
    "absolute",
    ...leadingDrivers.map(() => "relative"),
    ...(sortedDrivers.length > 8 ? ["relative"] : []),
    "total",
  ];
  const stackedDrivers = [...drivers]
    .filter(
      (driver) =>
        Math.abs(Number(driver.actual_value || 0)) +
          Math.abs(Number(driver.shared_value || 0)) >
        0.005,
    )
    .sort(
      (a, b) =>
        Number(b.actual_value || 0) +
        Number(b.shared_value || 0) -
        (Number(a.actual_value || 0) + Number(a.shared_value || 0)),
    )
    .slice(0, 8);
  const waterfallData: any[] =
    sharedOn && stackedDrivers.length > 0
      ? [
          {
            type: "bar",
            x: stackedDrivers.map((item) => item.name),
            y: stackedDrivers.map((item) => Number(item.actual_value || 0)),
            name: "Realizado",
            marker: { color: "#F2A900" },
          },
          {
            type: "bar",
            x: stackedDrivers.map((item) => item.name),
            y: stackedDrivers.map((item) => Number(item.shared_value || 0)),
            name: "Custo compartilhado",
            marker: { color: "#EA580C" },
          },
        ]
      : [
          {
            type: "waterfall",
            x: waterfallLabels,
            y: waterfallValues,
            measure: waterfallMeasures,
            connector: { line: { color: "#9CA3AF" } },
            increasing: { marker: { color: "#EF4444" } },
            decreasing: { marker: { color: "#16A34A" } },
            totals: { marker: { color: "#64748B" } },
            name: "Drivers da variação",
          },
        ];
  const pathParts = director.split(" > ").filter(Boolean);
  const childLevel = Math.min(pathParts.length + 1, 7);
  const waterfallLayout: any =
    sharedOn && stackedDrivers.length > 0
      ? {
          autosize: true,
          barmode: "stack",
          hovermode: "x unified",
          margin: { l: 82, r: 20, t: 25, b: 120 },
          yaxis: {
            title: { text: "Consumo e rateio", standoff: 12 },
            side: "left",
            showticklabels: true,
            tickprefix: "$",
            tickformat: ".2s",
          },
          xaxis: {
            type: "category",
            title: {
              text: `Departamento nível ${childLevel} · clique para abrir`,
              standoff: 18,
            },
            side: "bottom",
            showticklabels: true,
            tickangle: -35,
            automargin: true,
          },
          legend: { orientation: "h", y: -0.28 },
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
        }
      : {
          autosize: true,
          margin: { l: 82, r: 20, t: 25, b: 120 },
          yaxis: {
            title: { text: "Variação", standoff: 12 },
            side: "left",
            showticklabels: true,
            tickprefix: "$",
            tickformat: ".2s",
          },
          xaxis: {
            type: "category",
            title: {
              text: `Departamento nível ${childLevel} · clique para abrir`,
              standoff: 18,
            },
            side: "bottom",
            showticklabels: true,
            tickangle: -35,
            automargin: true,
          },
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
        };
  const card = (
    label: string,
    value: number | string,
    color: string,
    onOpen?: () => void,
    caption?: string,
  ) => (
    <Card
      onClick={onOpen}
      sx={{
        flex: 1,
        minWidth: 190,
        border: "1px solid #e2e7f0",
        borderRadius: "16px",
        borderTop: `3px solid ${color}`,
        boxShadow: "0 8px 24px rgba(24, 39, 75, 0.045)",
        cursor: onOpen ? "pointer" : "default",
        "&:hover": onOpen ? { boxShadow: "0 12px 28px rgba(24, 39, 75, 0.10)", transform: "translateY(-1px)" } : undefined,
        transition: "box-shadow .16s ease, transform .16s ease",
      }}
    >
      <CardContent>
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h5" fontWeight={700} title={typeof value === "number" ? money.format(value) : undefined}>
          {typeof value === "number" ? compactMoney(value) : value}
        </Typography>
        <Typography variant="caption">
          {caption ??
            (onOpen ? "Clique para ver o detalhe do consumo" : "Current Year")}
        </Typography>
      </CardContent>
    </Card>
  );
  const toggleNode = (id: number) =>
    setExpandedNodes((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  const expandAncestors = (department: Department) => {
    setExpandedNodes((previous) => {
      const next = new Set(previous);
      let current: (Department & { path: string }) | undefined =
        departmentOptions.find((item) => item.id === department.id);
      while (current) {
        next.add(current.id);
        current = current.parent_id
          ? departmentOptions.find((item) => item.id === current!.parent_id)
          : undefined;
      }
      return next;
    });
  };
  const chooseDepartment = (department: Department) => {
    const path =
      flattenDepartments(organization).find((item) => item.id === department.id)
        ?.path || department.name;
    setDepartmentId(department.id);
    setDirector(path);
    expandAncestors(department);
    setOrganizationAnchor(null);
  };
  const choosePath = (path: string) => {
    const found = departmentOptions.find((item) => item.path === path);
    if (found) chooseDepartment(found);
  };
  const openActualDetails = () => {
    const year =
      periodFilter === "all"
        ? ""
        : periodFilter === "current"
          ? String(new Date().getFullYear())
          : periodFilter;
    const query = new URLSearchParams({
      tab: "pivot",
      measure: "realizado",
      path: director,
      tipo: "realizado",
    });
    if (year) query.set("year", year);
    navigate(`/analysis?${query.toString()}`);
  };
  // Clique no gráfico: abre o diálogo de decomposição (estado próprio, sem
  // alterar filtros da página). O mês clicado restringe só o diálogo.
  const onMainChartClick = (event: any) => {
    const x = event?.points?.[0]?.x;
    let period: string | undefined;
    const isoMonth = String(x ?? "").match(/^(\d{4})-(\d{2})/);
    if (isoMonth) {
      // Não use new Date('YYYY-MM-DD'): a data é interpretada como UTC e,
      // em UTC−03, 2026-08-01 vira 31/jul antes de getMonth(). O eixo já
      // informa a competência em ISO, portanto basta preservar ano e mês.
      period = `${isoMonth[1]}${isoMonth[2]}`;
    }
    const traceName = String(
      event?.points?.[0]?.data?.name ||
        event?.points?.[0]?.fullData?.name ||
        "",
    ).toLowerCase();
    setDrillTipo(traceName.includes("forecast") ? "forecast" : "realizado");
    setDrillPeriod(period);
    setDrillOpen(true);
  };
  const openDriver = (name: string) => {
    if (
      !name ||
      [
        "Orçamento",
        "Forecast",
        "Outros",
        "Realizado",
        "Custo compartilhado",
      ].includes(name)
    )
      return;
    const found = departmentOptions.find(
      (item) => item.path === [...pathParts, name].join(" > ") && item.active,
    );
    if (found) chooseDepartment(found);
  };

  const renderOrganizationNode = (
    department: Department,
    depth = 0,
  ): ReactElement => {
    const children = (department.children || []).filter(
      (child) => child.active,
    );
    const expanded = expandedNodes.has(department.id);
    return (
      <Box key={department.id}>
        <ListItemButton
          selected={department.id === departmentId}
          onClick={() => chooseDepartment(department)}
          sx={{ pl: 1 + depth * 2, py: 0.25 }}
        >
          <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>
            {children.length > 0 && (
              <IconButton
                size="small"
                onClick={(event) => {
                  event.stopPropagation();
                  toggleNode(department.id);
                }}
              >
                {expanded ? (
                  <ExpandMore fontSize="small" />
                ) : (
                  <ChevronRight fontSize="small" />
                )}
              </IconButton>
            )}
          </Box>
          <ListItemText
            primary={department.name}
            primaryTypographyProps={{
              variant: "body2",
              fontWeight: depth < 3 ? 700 : 400,
            }}
          />
        </ListItemButton>
        <Collapse in={expanded} timeout="auto" unmountOnExit>
          {children.map((child) => renderOrganizationNode(child, depth + 1))}
        </Collapse>
      </Box>
    );
  };
  const workspaceSx: Record<string, unknown> = {
    "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
    "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": {
      transform: "translate(14px, 11px) scale(1)",
    },
  };
  const body = (
    <>
      <Breadcrumbs
        separator=">"
        sx={{ mb: 1.5, "& .MuiBreadcrumbs-separator": { mx: 0.5 } }}
      >
        <Typography color="text.secondary">Visão esperada</Typography>
        <Typography color="text.secondary">Variância de Forecast</Typography>
        {pathParts.map((name, index) => {
          const path = pathParts.slice(0, index + 1).join(" > ");
          const last = index === pathParts.length - 1;
          return last ? (
            <Typography key={path} color="text.primary" fontWeight={600}>
              {name}
            </Typography>
          ) : (
            <Link
              key={path}
              component="button"
              underline="hover"
              color="inherit"
              onClick={() => choosePath(path)}
            >
              {name}
            </Link>
          );
        })}
      </Breadcrumbs>

      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={1}
        mb={2}
      >
        <Typography variant="subtitle2" color="text.secondary">
          Variância de forecast
        </Typography>
        <Stack direction="row" gap={1} flexWrap="wrap">
          {!embedded && (
            <>
              <Button
                variant="outlined"
                size="small"
                onClick={(event) => setOrganizationAnchor(event.currentTarget)}
                sx={{
                  minWidth: 320,
                  justifyContent: "flex-start",
                  textTransform: "none",
                }}
              >
                {director || "Consumo Cloud"}
              </Button>
              <Menu
                anchorEl={organizationAnchor}
                open={Boolean(organizationAnchor)}
                onClose={() => setOrganizationAnchor(null)}
                PaperProps={{ sx: { width: 430, maxHeight: 520 } }}
              >
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", px: 2, pt: 1 }}
                >
                  Selecione um nível para consolidar todos os seus filhos
                </Typography>
                <List dense disablePadding>
                  {organization
                    .filter((department) => department.active)
                    .map((department) => renderOrganizationNode(department))}
                </List>
              </Menu>
            </>
          )}
          {!periodFrom && <FormControl sx={{ minWidth: 170 }}>
            <InputLabel>Período</InputLabel>
            <Select
              label="Período"
              value={periodFilter}
              onChange={(e) => setPeriodFilter(String(e.target.value))}
            >
              <MenuItem value="current">Ano atual</MenuItem>
              <MenuItem value="all">Todos os períodos</MenuItem>
              {years.map((year) => (
                <MenuItem key={year} value={year}>
                  {year}
                </MenuItem>
              ))}
            </Select>
          </FormControl>}
          {externalSharedCost === undefined && <FormControlLabel
            control={
              <Switch
                checked={includeSharedCost}
                onChange={(_, checked) => setIncludeSharedCost(checked)}
              />
            }
            label="Incluir custo compartilhado"
          />}
        </Stack>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Forecast = realizado dos meses fechados + no mês corrente, realizado
        parcial com o forecast do restante empilhado + forecast dos meses
        futuros. Variação = orçamento menos forecast. Com o interruptor ligado,
        o custo compartilhado aparece empilhado em laranja sobre o consumo real
        — não mistura as duas barras. O card Realizado soma os dois.
      </Alert>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
          <Button
            size="small"
            sx={{ ml: 1 }}
            onClick={() => setReloadNonce((n) => n + 1)}
          >
            Tentar de novo
          </Button>
        </Alert>
      )}

      {loading ? (
        <Stack alignItems="center" mt={6} gap={1}>
          <CircularProgress />
          <Typography color="text.secondary">
            Consultando realizado e forecast no Trino…
          </Typography>
        </Stack>
      ) : (
        <>
          {!budgetAvailable && <Alert severity="info" sx={{ mb: 2 }}>Orçamento e forecast manual indisponíveis para filtros dimensionais: o planejamento não tem granularidade nessa coluna. A tela omite esses valores em vez de reutilizar totais globais.</Alert>}
          <Stack direction="row" gap={2} flexWrap="wrap" mb={2}>
            {card("Realizado", actual, "#F2A900", openActualDetails)}
            {card("Forecast", forecastOnly, "#7C3AED", undefined, `Realizado / orçamento: ${realizedBudgetPct} · horizonte até ${filteredRows[filteredRows.length - 1] ? monthLong(filteredRows[filteredRows.length - 1].period) : "—"}`)}
            {card("Orçamento", budgetAvailable ? budget : "Sem orçamento para este filtro", "#64748B")}
            {card(
              "Total projetado",
              forecast,
              "#16A34A",
              undefined,
              "Realizado + Forecast",
            )}
            {card("Variação", budgetAvailable ? variance : "Sem base", variance >= 0 ? "#16A34A" : "#EF4444")}
          </Stack>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <Paper
                elevation={0}
                sx={{
                  p: 2,
                  borderRadius: "18px",
                  bgcolor: "#fff",
                  border: "1px solid #e2e7f0",
                  boxShadow: "0 8px 24px rgba(24, 39, 75, 0.045)",
                }}
              >
                <Stack direction={{ xs: "column", lg: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", lg: "center" }} gap={1}>
                  <Box>
                    <Typography variant="h6">
                      Gasto ao longo do tempo
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {chartGranularity === "daily"
                        ? "Diário · consumo realizado agrupado pela data real disponível na fonte"
                        : cumulative
                        ? sharedOn
                          ? "Acumulado · realizado, custo compartilhado por cima, orçamento e forecast"
                          : "Acumulado · Realizado, orçamento e forecast"
                        : sharedOn
                          ? "Mensal · consumo real e custo compartilhado empilhados; forecast e orçamento"
                          : "Mensal · Realizado e forecast empilhados (mês corrente: parcial + restante); orçamento em linha"}{" "}
                      · clique para decompor o consumo
                    </Typography>
                  </Box>
                  <Stack direction="row" gap={1} flexWrap="wrap" justifyContent={{ xs: "flex-start", lg: "flex-end" }}>
                    <FormControl size="small" sx={{ minWidth: 190 }}>
                      <InputLabel>Agrupar por</InputLabel>
                      <Select label="Agrupar por" value={groupBy} onChange={(event) => setGroupBy(String(event.target.value))}>
                        <MenuItem value="">Nenhum</MenuItem>
                        {orderedGroupByOptions.map((field) => <MenuItem key={field} value={field}>{field.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase())}</MenuItem>)}
                      </Select>
                    </FormControl>
                    <FormControl size="small" sx={{ minWidth: 170 }}>
                      <InputLabel>Granularidade</InputLabel>
                      <Select label="Granularidade" value={chartGranularity} onChange={(event) => setChartGranularity(event.target.value as ChartGranularity)}>
                        <MenuItem value="accumulated">Acumulado</MenuItem>
                        <MenuItem value="monthly">Mensal</MenuItem>
                        <MenuItem value="daily" disabled={rangeDays > 45}>Diário · até 45 dias</MenuItem>
                      </Select>
                    </FormControl>
                  </Stack>
                </Stack>
                {chartQueryError && <Alert severity="error" sx={{ mt: 1 }}>{chartQueryError}</Alert>}
                {chartGranularity === "daily" && dailyGold?.available === false
                  ? <Alert severity="info" sx={{ mt: 1 }}>A tabela Gold deste dashboard só tem competência mensal; não há coluna com a data real do consumo para montar o gráfico diário. É necessário incluir essa data na tabela Gold a partir da fonte de origem.</Alert>
                  : chartQueryLoading
                    ? <Stack alignItems="center" justifyContent="center" sx={{ height: 420 }} direction="row" gap={1}><CircularProgress size={22} /><Typography color="text.secondary">Carregando série no Trino…</Typography></Stack>
                    : chartGranularity === "daily" && !dailyGold?.rows.length
                      ? <Alert severity="info" sx={{ mt: 1 }}>Sem dados diários disponíveis neste período.</Alert>
                      : groupBy && chartGranularity !== "daily" && groupedGold && !groupedGold.rows.length
                        ? <Alert severity="info" sx={{ mt: 1 }}>Sem dados realizados para agrupar por esta coluna no período selecionado.</Alert>
                        : <Plot
                  useResizeHandler
                  style={{ width: "100%", height: 420, cursor: "pointer" }}
                  data={chartDataForRender}
                  layout={chartLayoutForRender}
                  config={{ responsive: true, displaylogo: false }}
                  onClick={groupBy || chartGranularity === "daily" ? undefined : onMainChartClick}
                />}
              </Paper>
            </Grid>
          </Grid>

          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12} md={6}>
              <Paper elevation={0} sx={{ p: 2, height: "100%", borderRadius: "18px", bgcolor: "#fff", border: "1px solid #e2e7f0", boxShadow: "0 8px 24px rgba(24, 39, 75, 0.045)" }}>
                <Typography variant="h6">{sharedOn ? "Rateio por departamento" : "Drivers da variação"}</Typography>
                <Typography variant="caption" color="text.secondary">{sharedOn ? "Laranja empilhado no filho que recebe o custo · clique para abrir" : "Filhos do departamento selecionado · clique na barra para abrir o subnível"}</Typography>
                <Plot useResizeHandler style={{ width: "100%", height: 360, cursor: "pointer" }} data={waterfallData} layout={{ ...waterfallLayout, height: 360 }} config={{ responsive: true, displaylogo: false }} onClick={(event) => openDriver(String(event.points?.[0]?.x ?? ""))} />
              </Paper>
            </Grid>
            <Grid item xs={12} md={6}>
              <Paper variant="outlined" sx={{ p: 2, height: "100%", borderRadius: "18px", borderColor: "#e2e7f0", boxShadow: "0 8px 24px rgba(24, 39, 75, 0.045)" }}>
                <Typography variant="overline" color="primary" fontWeight={800} sx={{ letterSpacing: ".08em" }}>TENDÊNCIA</Typography>
                <Typography variant="h6" fontWeight={750} sx={{ color: "#111b4e" }}>Tendência mensal · Forecast × Realizado</Typography>
                <Typography variant="caption" color="text.secondary">Consumo realizado e projeção por competência; competências futuras sem dados permanecem ausentes.</Typography>
                <Plot useResizeHandler style={{ width: "100%", height: 320 }} data={[
                  { x: filteredRows.filter((row) => row.actual_value != null).map((row) => monthLong(row.period)), y: filteredRows.filter((row) => row.actual_value != null).map((row) => Number(row.actual_value || 0)), type: "scatter", mode: "lines+markers", name: "Realizado", line: { color: "#6416dc", width: 3 }, marker: { color: "#6416dc", size: 7 } },
                  { x: filteredRows.filter((row) => row.forecast_value != null).map((row) => monthLong(row.period)), y: filteredRows.filter((row) => row.forecast_value != null).map((row) => Number(row.forecast_value || 0)), type: "scatter", mode: "lines+markers", name: "Forecast", line: { color: "#9c4dff", width: 3, dash: "dash" }, marker: { color: "#9c4dff", size: 7 } },
                ] as any} layout={{ height: 320, autosize: true, margin: { l: 75, r: 20, t: 15, b: 45 }, yaxis: { tickprefix: "R$ ", separatethousands: true, gridcolor: "#edf0f6" }, xaxis: { gridcolor: "#fff" }, legend: { orientation: "h" }, paper_bgcolor: "transparent", plot_bgcolor: "transparent" }} config={{ responsive: true, displaylogo: false }} />
              </Paper>
            </Grid>
          </Grid>

          {footer}
        </>
      )}

      {!loading && filteredRows.length > 0 && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
        >
          Orçamento anual carregado: {money.format(budget)}
        </Typography>
      )}
      <ConsumptionDrillDialog
        open={drillOpen}
        onClose={() => setDrillOpen(false)}
        table={table}
        directorPath={director}
        directorView={directorView}
        period={drillPeriod}
        tipo={drillTipo}
      />
    </>
  );
  const acksBox = (
    <Box sx={{ mt: 2 }}>
      <AccuracyWidget department={director} dimensionFilters={dimensionFilters} />
    </Box>
  );
  return embedded ? (
    <Box sx={workspaceSx}>
      {body}
      {acksBox}
    </Box>
  ) : (
    <Box
      sx={{
        mx: -3,
        mt: -3,
        pb: 3,
        minHeight: "calc(100vh - 64px)",
        bgcolor: "#f8fafc",
      }}
    >
      <Box
        sx={{
          px: 3,
          pt: 3,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ mb: 2 }}>
          <Typography variant="h4">Visão executiva</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Realizado × forecast × orçamento por departamento, com rateio de
            custo compartilhado.
          </Typography>
        </Box>
      </Box>
      <Box sx={{ p: 3, ...workspaceSx }}>
        <Paper
          elevation={0}
          sx={{
            p: { xs: 2, md: 2.5 },
            borderRadius: "18px",
            bgcolor: "#fff",
            border: "1px solid #e2e7f0",
            boxShadow: "0 8px 24px rgba(24, 39, 75, 0.045)",
          }}
        >
          {body}
        </Paper>
        {acksBox}
      </Box>
    </Box>
  );
}
