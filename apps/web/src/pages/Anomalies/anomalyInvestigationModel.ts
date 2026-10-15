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
export const detailScopeChain = (details: Array<{ key: string; label: string }> = []) => {
  const base = [
    { field: "cloud_origem", label: "Cloud / conexão" },
    ...Array.from({ length: 7 }, (_, index) => ({ field: `organograma_nivel_${index + 1}`, label: `Organograma · nível ${index + 1}` })),
    { field: "subaccountname", label: "Assinatura / subconta" }, { field: "centro_custo", label: "Centro de custo" },
    { field: "servicename", label: "Serviço" }, { field: "servicesubcategory", label: "Categoria do serviço" },
    { field: "skumeter", label: "SKU / medidor" }, { field: "pricingcategory", label: "Categoria de preço" },
    { field: "commitmentdiscounttype", label: "Modalidade" },
  ];
  const custom = details.map(item => ({ field: item.key, label: item.label }));
  const unseen = custom.filter(item => !base.some(candidate => candidate.field === item.field));
  const infrastructure = unseen.filter(item => ["subscription", "subscription_id", "resource_group", "gcp_project", "region"].includes(item.field));
  const serviceDetails = unseen.filter(item => !infrastructure.includes(item));
  const all = [...base.slice(0, 10), ...infrastructure, ...base.slice(10, 13), ...serviceDetails, ...base.slice(13)];
  return all.filter((item, index) => all.findIndex(candidate => candidate.field === item.field) === index);
};
export const filterMembers = (members: Member[], filters: ScopeFilter[]) => members.filter(member => filters.every(filter => dimensionValue(member, filter.field) === filter.value));
export function totalMetric(members: Member[], key: "observed_cloud" | "expected_cloud" | "delta_cloud") {
  return members.length && members.every(member => member[key] != null) ? members.reduce((sum, member) => sum + member[key]!, 0) : null;
}
export function scopeBranches(members: Member[], filters: ScopeFilter[], chain = scopeChain) {
  const start = filters.length ? chain.findIndex(item => item.field === filters[filters.length - 1].field) + 1 : 0;
  // Shared ancestors stay in the breadcrumb. Branch at the first distinct value.
  const dimension = chain.slice(start).find(item => new Set(members.map(member => dimensionValue(member, item.field))).size > 1);
  if (!dimension) return { dimension: null, groups: [] };
  const groups = [...new Set(members.map(member => dimensionValue(member, dimension.field)))].map(value => {
    const children = members.filter(member => dimensionValue(member, dimension.field) === value);
    return { value, members: children, delta: totalMetric(children, "delta_cloud"), expected: totalMetric(children, "expected_cloud"), actual: totalMetric(children, "observed_cloud") };
  }).sort((a, b) => (b.delta ?? -Infinity) - (a.delta ?? -Infinity));
  return { dimension, groups };
}
export function observedScopeBranches(items: NonNullable<AnomalySeries["detail_items"]>, filters: ScopeFilter[], chain: Array<{ field: string; label: string }>) {
  const rows = items.filter(item => filters.every(filter => dimensionValue({ dimensions: item.dimensions } as Member, filter.field) === filter.value));
  const start = filters.length ? chain.findIndex(item => item.field === filters[filters.length - 1].field) + 1 : 0;
  const dimension = chain.slice(start).find(item => new Set(rows.map(row => String(row.dimensions[item.field] || "Não informado"))).size > 1);
  if (!dimension) return { dimension: null, groups: [] };
  const groups = [...new Set(rows.map(row => String(row.dimensions[dimension.field] || "Não informado")))].map(value => {
    const selected = rows.filter(row => String(row.dimensions[dimension.field] || "Não informado") === value);
    return { value, members: selected, actual: selected.every(row => row.observed_cloud != null) ? selected.reduce((sum, row) => sum + row.observed_cloud!, 0) : null };
  }).sort((a, b) => Math.abs(b.actual ?? 0) - Math.abs(a.actual ?? 0));
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
