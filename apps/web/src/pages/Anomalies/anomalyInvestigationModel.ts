import type { AnomalySeries, AnomalySeriesPoint } from "../../services/anomaliesApi";

export type Member = NonNullable<AnomalySeries["contributor_breakdown"]>[number];
export type ScopeFilter = { field: string; value: string };
export const dimensionValue = (member: Member, field: string) => member.dimensions[field] || "Não informado";
export const scopeChain = [
  ...Array.from({ length: 7 }, (_, index) => ({ field: `organograma_nivel_${index + 1}`, label: index === 4 ? "Projeto / nível 5" : `Organograma · nível ${index + 1}` })),
  { field: "subaccountname", label: "Assinatura / subconta" },
  { field: "resourcegroupname", label: "Resource group" }, { field: "resource_group", label: "Resource group" },
  { field: "servicesubcategory", label: "Produto / família" }, { field: "skumeter", label: "SKU / medidor" },
];
export const detailScopeChain = (details: Array<{ key: string; label: string }> = []) => {
  const base = [
    ...Array.from({ length: 7 }, (_, index) => ({ field: `organograma_nivel_${index + 1}`, label: `Organograma · nível ${index + 1}` })),
    { field: "subaccountname", label: "Assinatura / subconta" },
    { field: "resourcegroupname", label: "Resource group" }, { field: "resource_group", label: "Resource group" },
    { field: "gcp_project", label: "Projeto GCP" },
    { field: "servicesubcategory", label: "Produto / família" }, { field: "skumeter", label: "SKU / medidor" },
  ];
  const hiddenFields = new Set(["subscription", "subscription_id", "resource", "resource_name", "resource_id", "region"]);
  const hiddenLabels = /^(id\s+da\s+assinatura|assinatura\s+id|subscription(?:\s+id)?|recurso(?:\s+id)?|id\s+do\s+recurso|nome\s+do\s+recurso|resource(?:\s+id|\s+name)?|regi[aã]o|region)$/i;
  const custom = details
    .filter(item => !hiddenFields.has(item.key.toLowerCase()) && !hiddenLabels.test(item.label.trim()))
    .map(item => ({ field: item.key, label: item.label }));
  const noise = /^(centro.?custo|cost.?center|servicename|service.?name|pricingcategory|commitmentdiscounttype)$/i;
  const unseen = custom.filter(item => !base.some(candidate => candidate.field === item.field) && !noise.test(item.field) && !noise.test(item.label.replace(/\s/g, "")));
  const sku = base.find(item => item.field === "skumeter");
  const product = base.find(item => item.field === "servicesubcategory");
  const path = base.filter(item => item !== sku && item !== product);
  const all = [...path, ...unseen, ...(product ? [product] : []), ...(sku ? [sku] : [])];
  return all.filter((item, index) => all.findIndex(candidate => candidate.field === item.field) === index);
};
export function caseLineageNodes(
  members: Member[],
  detailDimensions: Array<{ key: string; label: string }> = [],
  detailItems: Array<{ dimensions: Record<string, string | null> }> = [],
  chain = detailScopeChain(detailDimensions),
) {
  const detailFields = new Set(detailDimensions.map(item => item.key));
  return chain.flatMap(item => {
    const values = detailFields.has(item.field)
      ? detailItems.map(row => row.dimensions[item.field])
      : members.map(member => member.dimensions[item.field]);
    const unique = [...new Set(values.filter((value): value is string => Boolean(value)))];
    return unique.length === 1 ? [{ ...item, value: unique[0] }] : [];
  });
}
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
