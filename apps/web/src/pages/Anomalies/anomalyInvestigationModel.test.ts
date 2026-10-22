import { describe, expect, it } from "vitest";
import { caseLineageNodes, detailScopeChain, filterMembers, scopeBranches, scopeTimeline, type Member } from "./anomalyInvestigationModel";
import type { AnomalySeriesPoint } from "../../services/anomaliesApi";

const point = (day: string, actual: number | null, expected: number | null): AnomalySeriesPoint => ({
  event_date: day, observed_cloud: actual, expected_cloud: expected, lower_cloud: expected, upper_cloud: expected == null ? null : expected + 10,
  weekday: 1, reference_month: "202610", reference_id: "frozen", reference_kind: "PROSPECTIVE", coverage_kind: "REMAINING_MONTH", distribution_method: "UNIFORM",
  delta_cloud: actual != null && expected != null ? actual - expected : null, anomalous_cost_cloud: null, above_band: null,
});
const member = (project: string, actual: number, expected: number, account: string): Member => ({
  series_id: `${project}-${account}`, dimensions: { hierarquia_4: "Diretoria", hierarquia_5: project, subaccountname: account },
  event_date: "2026-10-12", observed_cloud: actual, expected_cloud: expected, delta_cloud: actual - expected,
  items: [point("2026-10-12", actual, expected), point("2026-10-13", null, expected)],
});

describe("decomposição de desvio", () => {
  it("reconcilia aumentos e reduções sem transformar participação em custo duplicado", () => {
    const members = [member("Projeto A", 500, 100, "Prod"), member("Projeto B", 50, 100, "Dev")];
    const { dimension, groups } = scopeBranches(members, []);
    expect(dimension?.field).toBe("hierarquia_5");
    expect(groups.map(group => group.delta)).toEqual([400, -50]);
    expect(groups.reduce((sum, group) => sum + group.delta!, 0)).toBe(350);
    expect(scopeTimeline(members)[0].delta_cloud).toBe(350);
  });
  it("selecionar projeto filtra a mesma conta e o mesmo forecast do gráfico", () => {
    const members = [member("A", 500, 100, "Prod"), member("B", 300, 200, "Dev")];
    const filters = [{ field: "hierarquia_5", value: "B" }];
    const selected = filterMembers(members, filters);
    expect(scopeTimeline(selected)[0]).toMatchObject({ observed_cloud: 300, expected_cloud: 200, delta_cloud: 100, reference_id: "frozen" });
    expect(scopeTimeline(selected)[1].observed_cloud).toBeNull();
  });
  it("mantém a decomposição encadeada até contas distintas de um projeto", () => {
    const selected = [member("A", 500, 100, "Prod"), member("A", 300, 200, "Dev")];
    expect(scopeBranches(selected, [{ field: "hierarquia_5", value: "A" }]).dimension?.field).toBe("subaccountname");
  });
  it("prioriza assinatura e resource group antes de produto e SKU", () => {
    const chain = detailScopeChain();
    const members = [
      { ...member("A", 500, 100, "Prod"), dimensions: { ...member("A", 500, 100, "Prod").dimensions, servicename: "Compute", servicesubcategory: "VM", skumeter: "D4" } },
      { ...member("A", 300, 200, "Dev"), dimensions: { ...member("A", 300, 200, "Dev").dimensions, servicename: "Compute", servicesubcategory: "VM", skumeter: "D8" } },
      { ...member("A", 200, 100, "Prod"), dimensions: { ...member("A", 200, 100, "Prod").dimensions, servicename: "Compute", servicesubcategory: "VM", skumeter: "D8" } },
    ];
    const project = [{ field: "hierarquia_5", value: "A" }];
    expect(scopeBranches(members, project, chain).dimension?.field).toBe("subaccountname");
    expect(scopeBranches(members, [...project, { field: "subaccountname", value: "Prod" }], chain).dimension?.field).toBe("skumeter");
    expect(chain.some(item => item.field === "centro_custo" || item.field === "servicename")).toBe(false);
    expect(chain.findIndex(item => item.field === "resourcegroupname")).toBeGreaterThan(chain.findIndex(item => item.field === "subaccountname"));
  });
  it("inclui resource group da origem na trilha mesmo quando forecast não tem esse grão", () => {
    const dimensions = [{ key: "resource_group", label: "Resource group" }, { key: "resource_id", label: "ID do recurso" }];
    const items = [
      { dimensions: { resource_group: "rg-prod", resource_id: "/subscriptions/x/resourceGroups/rg-prod/db/one" } },
      { dimensions: { resource_group: "rg-prod", resource_id: "/subscriptions/x/resourceGroups/rg-prod/db/two" } },
    ];
    const nodes = caseLineageNodes([member("Projeto A", 500, 100, "Prod")], dimensions, items);
    expect(nodes.find(node => node.field === "resource_group")).toMatchObject({ value: "rg-prod", label: "Resource group" });
    expect(nodes.some(node => node.field === "resource_id")).toBe(false);
  });
  it("não publica forecast ou observado parcial como se fosse o total", () => {
    const left = member("A", 500, 100, "Prod"), right = member("B", 300, 200, "Dev");
    right.items = [point("2026-10-12", null, null)];
    expect(scopeTimeline([left, right])[0]).toMatchObject({ observed_cloud: null, expected_cloud: null, delta_cloud: null });
    expect(scopeTimeline([left, right])[1].expected_cloud).toBeNull();
    expect(scopeTimeline([{ ...left, items: undefined }])).toEqual([]);
  });
});
