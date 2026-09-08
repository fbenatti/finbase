import { describe, expect, it } from "vitest";
import {
  canMergePolicies,
  groupSubscriptionPolicies,
  mergeGroupKey,
  type FlowPolicy,
} from "./PolicyFlowGraph";

const subscription = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function policy(kind: FlowPolicy["kind"], overrides: Partial<FlowPolicy> = {}): FlowPolicy {
  return {
    rule_id: 3028,
    rule_name: "FinOps PRO",
    priority: 2000,
    scope_subscriptions: [subscription],
    evidence_count: 10,
    observed: {},
    tags: { projeto: "FinOps", "centro-de-custos": "29TN161700" },
    condition: { field: "name", equals: "rg" },
    exclusions: [{ rule_id: 78, rule_name: "Classifica -> Prisma" }],
    kind,
    ...overrides,
  };
}

describe("groupSubscriptionPolicies", () => {
  it("agrupa RG e recursos da mesma regra em um único caminho", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups"),
      policy("resources"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].merged).toBe(true);
    expect(groups[0].policies.map((item) => item.kind)).toEqual(["resource_groups", "resources"]);
  });

  it("mantém caminhos separados quando tags divergem", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups"),
      policy("resources", { tags: { projeto: "Outro" } }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.every((group) => !group.merged)).toBe(true);
  });

  it("mantém caminhos separados quando exclusões divergem", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups"),
      policy("resources", { exclusions: [] }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.every((group) => !group.merged)).toBe(true);
  });

  it("não mescla quando só uma camada existe", () => {
    const groups = groupSubscriptionPolicies(subscription, [policy("resource_groups")]);
    expect(groups).toHaveLength(1);
    expect(groups[0].merged).toBe(false);
  });
});

describe("canMergePolicies", () => {
  it("exige camadas distintas com metadados equivalentes", () => {
    expect(canMergePolicies(policy("resource_groups"), policy("resources"))).toBe(true);
    expect(canMergePolicies(policy("resource_groups"), policy("resource_groups"))).toBe(false);
    expect(mergeGroupKey(subscription, policy("resource_groups")))
      .toEqual(mergeGroupKey(subscription, policy("resources")));
  });
});
