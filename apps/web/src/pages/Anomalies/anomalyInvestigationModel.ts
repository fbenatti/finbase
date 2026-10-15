import type { AnomalySeries, AnomalySeriesPoint } from "../../services/anomaliesApi";

export type Member = NonNullable<AnomalySeries["contributor_breakdown"]>[number];
export type ScopeFilter = { field: string; value: string };
export const dimensionValue = (member: Member, field: string) => member.dimensions[field] || "Não informado";
export const scopeChain = [
  ...Array.from({ length: 7 }, (_, index) => ({ field: `organograma_nivel_${index + 1}`, label: index === 4 ? "Projeto / nível 5" : `Organograma · nível ${index + 1}` })),
  { field: "subaccountname", label: "Conta" },
  { field: "centro_custo", label: "Centro de custo" },
  { field: "servicesubcategory", label: "SKU / categoria do serviço" },
];
export const filterMembers = (members: Member[], filters: ScopeFilter[]) => members.filter(member => filters.every(filter => dimensionValue(member, filter.field) === filter.value));
export function totalMetric(members: Member[], key: "observed_cloud" | "expected_cloud" | "delta_cloud") {
  return members.length && members.every(member => member[key] != null) ? members.reduce((sum, member) => sum + member[key]!, 0) : null;
}
export function scopeBranches(members: Member[], filters: ScopeFilter[]) {
  const start = filters.length ? scopeChain.findIndex(item => item.field === filters[filters.length - 1].field) + 1 : 0;
  // Shared ancestors stay in the breadcrumb. Branch at the first distinct value.
  const dimension = scopeChain.slice(start).find(item => new Set(members.map(member => dimensionValue(member, item.field))).size > 1);
  if (!dimension) return { dimension: null, groups: [] };
  const groups = [...new Set(members.map(member => dimensionValue(member, dimension.field)))].map(value => {
    const children = members.filter(member => dimensionValue(member, dimension.field) === value);
    return { value, members: children, delta: totalMetric(children, "delta_cloud"), expected: totalMetric(children, "expected_cloud"), actual: totalMetric(children, "observed_cloud") };
  }).sort((a, b) => (b.delta ?? -Infinity) - (a.delta ?? -Infinity));
  return { dimension, groups };
}
export function scopeTimeline(members: Member[]): AnomalySeriesPoint[] {
  if (!members.length || members.some(member => !member.items?.length)) return [];
  const maps = members.map(member => new Map(member.items!.map(point => [point.event_date, point])));
  const dates = [...new Set(members.flatMap(member => member.items!.map(point => point.event_date)))].sort();
  return dates.map(day => {
    const points = maps.map(map => map.get(day));
    const base = points.find((point): point is AnomalySeriesPoint => Boolean(point))!;
    const sum = (key: "observed_cloud" | "expected_cloud" | "lower_cloud" | "upper_cloud") => points.every(point => point?.[key] != null) ? points.reduce((value, point) => value + point![key]!, 0) : null;
    const actual = sum("observed_cloud"), expected = sum("expected_cloud"), upper = sum("upper_cloud");
    return { ...base, observed_cloud: actual, expected_cloud: expected, lower_cloud: sum("lower_cloud"), upper_cloud: upper,
      delta_cloud: actual != null && expected != null ? actual - expected : null,
      anomalous_cost_cloud: actual != null && upper != null ? Math.max(0, actual - upper) : null,
      above_band: actual != null && upper != null ? actual > upper : null };
  });
}
