import { useEffect, useMemo, useState } from "react";
import { type AnomalyCase, listAnomalyCases } from "../../services/anomaliesApi";
import {
  type BudgetAccuracyResponse,
  type ForecastAccuracyMonth,
  type ForecastAck,
  type GoldQueryFilter,
  getBudgetAccuracy,
  getExecutiveManualAcks,
  getForecastAccuracyMonths,
  queryDimonTable,
} from "../../services/api";

// Sinais de anomalia, orcamento e projecao para a analise de custos.
// As tres consultas saem em paralelo e nenhuma espera a outra.
// Anomalias so existem por periodo, servico, regiao, cloud e subconta; orcamento
// aceita o departamento; projecao aceita o caminho da hierarquia.
export const periodKey = (period: unknown) => String(period ?? "").replace("-", "").slice(0, 6);
export const dayKey = (day: unknown) => String(day ?? "").slice(0, 10);

const ANOMALY_FIELD: Record<string, "service_name" | "region" | "cloud" | "subaccount_name"> = {
  servicename: "service_name",
  regionname: "region",
  cloud_origem: "cloud",
  subaccountname: "subaccount_name",
};

export type CostSignalScope = { dateFrom: string; dateTo: string; filters: GoldQueryFilter[] };

export type CostSignals = {
  loading: boolean;
  anomalies: AnomalyCase[];
  anomalyByMonth: Record<string, number>;
  anomalyCost: number;
  openAnomalies: number;
  budget: BudgetAccuracyResponse["totals"] | null;
  budgetYear: number;
  forecastMonth: string | null;
  forecastMonths: ForecastAccuracyMonth[];
  forecastError: string;
  justifications: ForecastAck[];
};

function matchesFilters(item: AnomalyCase, filters: GoldQueryFilter[]) {
  return filters.every((filter) => {
    const field = ANOMALY_FIELD[filter.column];
    if (!field || !filter.values?.length) return true;
    const value = String(item[field] ?? "").toLowerCase();
    return filter.values.some((candidate) => String(candidate).toLowerCase() === value);
  });
}

// Anomalias nao trazem a estrutura organizacional. Para segui-la, buscamos os pares
// subconta + servico que existem no escopo filtrado e mantemos so as anomalias deles.
const scopeKeyOf = (subaccount: unknown, service: unknown) => `${String(subaccount ?? "").toLowerCase()}|${String(service ?? "").toLowerCase()}`;

async function scopePairs(table: string | undefined, filters: GoldQueryFilter[], path: string[]): Promise<Set<string> | null> {
  const scoped = path.length > 0 || filters.some((filter) => filter.column !== "_billing_period" && !ANOMALY_FIELD[filter.column]);
  if (!table || !scoped) return null;
  const result = await queryDimonTable({ table, rows: ["subaccountname", "servicename"], filters, row_limit: 10000, sort_column: null, sort_direction: "desc" });
  return new Set(result.rows.map((row) => scopeKeyOf(row.subaccountname, row.servicename)));
}

export function useCostSignals(scope: CostSignalScope, departmentId: number | undefined, path: string[], table?: string, refreshKey = 0): CostSignals {
  const [anomalies, setAnomalies] = useState<AnomalyCase[]>([]);
  const [budget, setBudget] = useState<BudgetAccuracyResponse["totals"] | null>(null);
  const [forecastMonths, setForecastMonths] = useState<ForecastAccuracyMonth[]>([]);
  const [forecastError, setForecastError] = useState("");
  const [justifications, setJustifications] = useState<ForecastAck[]>([]);
  const [loading, setLoading] = useState(true);

  const departmentPath = path.length ? path.join(" > ") : undefined;
  const budgetYear = Number(scope.dateTo.slice(0, 4));
  const scopeKey = JSON.stringify([scope, departmentId, departmentPath, table, refreshKey]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setForecastError("");
    const from = periodKey(scope.dateFrom);
    const to = periodKey(scope.dateTo);
    const inRange = (period: unknown) => {
      const key = periodKey(period);
      return key >= from && key <= to;
    };

    Promise.allSettled([
      listAnomalyCases({ limit: 200, order_by: "score" }),
      getBudgetAccuracy(budgetYear, null, departmentId ?? null, "annual"),
      getForecastAccuracyMonths(undefined, departmentPath, undefined, true),
      getExecutiveManualAcks(from, to, departmentPath ? [departmentPath] : []),
      scopePairs(table, scope.filters, path),
    ]).then(([anomalyResult, budgetResult, monthsResult, acksResult, pairsResult]) => {
      if (!alive) return;
      setAnomalies(anomalyResult.status === "fulfilled"
        ? anomalyResult.value.items.filter((item) => {
          if (!inRange(item.period) || !matchesFilters(item, scope.filters)) return false;
          const pairs = pairsResult.status === "fulfilled" ? pairsResult.value : null;
          return !pairs || pairs.has(scopeKeyOf(item.subaccount_name, item.service_name));
        })
        : []);
      setBudget(budgetResult.status === "fulfilled" ? budgetResult.value.totals : null);
      const months = monthsResult.status === "fulfilled"
        ? monthsResult.value.filter((month) => month.closed && inRange(month.target_month))
        : [];
      setForecastError(monthsResult.status === "rejected" ? "Não foi possível carregar a acurácia de forecast. Use Recarregar dados para tentar novamente." : "");
      setForecastMonths([...months].sort((a, z) => periodKey(a.target_month).localeCompare(periodKey(z.target_month))));
      setJustifications(acksResult.status === "fulfilled"
        ? acksResult.value.items.filter((ack) => ack.active !== false).sort((a, z) => Math.abs(Number(z.variance || 0)) - Math.abs(Number(a.variance || 0)))
        : []);
      setLoading(false);
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  const anomalyByMonth = useMemo(() => anomalies.reduce<Record<string, number>>((acc, item) => {
    const key = periodKey(item.period);
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {}), [anomalies]);

  const anomalyCost = useMemo(() => anomalies.reduce((sum, item) => sum + Number(item.anomalous_cost_cloud || 0), 0), [anomalies]);
  const openAnomalies = useMemo(() => anomalies.filter((item) => !item.closed_at).length, [anomalies]);
  const forecastMonth = forecastMonths.length ? forecastMonths[forecastMonths.length - 1].target_month : null;

  return { loading, anomalies, anomalyByMonth, anomalyCost, openAnomalies, budget, budgetYear, forecastMonth, forecastMonths, forecastError, justifications };
}
