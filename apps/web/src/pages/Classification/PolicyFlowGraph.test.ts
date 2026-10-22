import { describe, expect, it } from "vitest";
import {
  canMergePolicies,
  groupExclusions,
  groupFlowTargets,
  groupSubscriptionPolicies,
  mergeGroupKey,
  uniqueExclusions,
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
  it("agrupa All e Indexed da mesma regra mesmo quando canMergePolicies falha", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups", { rule_name: "FinOps PRO All", priority: 2000 }),
      policy("resources", { rule_name: "FinOps PRO Indexed", priority: 2001 }),
    ]);
    expect(canMergePolicies(
      policy("resource_groups", { rule_name: "FinOps PRO All", priority: 2000 }),
      policy("resources", { rule_name: "FinOps PRO Indexed", priority: 2001 }),
    )).toBe(false);
    expect(groups).toHaveLength(1);
    expect(groups[0].merged).toBe(true);
    expect(groups[0].policies.map((item) => item.kind)).toEqual(["resource_groups", "resources"]);
  });

  it("mantém grupos separados quando os rule_ids divergem", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups", { rule_id: 3028 }),
      policy("resources", { rule_id: 4001, rule_name: "Outra" }),
    ]);
    expect(groups).toHaveLength(2);
  });

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

  it("mescla camadas quando a mesma exclusão aparece duplicada em um lado", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups", {
        exclusions: [
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
        ],
      }),
      policy("resources"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].merged).toBe(true);
    expect(groupExclusions(groups[0].policies).map((item) => item.rule_id)).toEqual([78]);
  });

  it("agrupa a mesma regra em um caminho quando os RGs observados divergem", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups", { observed: { resource_group_name: "rg-a" } }),
      policy("resources", { observed: { resource_group_name: "rg-b" } }),
      policy("resource_groups", { observed: { resource_group_name: "rg-c" } }),
      policy("resources", { observed: { resource_group_name: "rg-d" } }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].policies).toHaveLength(4);
    expect(groupFlowTargets(groups[0].policies).map((target) => target.key)).toEqual(["rg-a", "rg-b", "rg-c", "rg-d"]);
    expect(groups[0].merged).toBe(true);
  });

  it("mantém grupos separados quando a mesma regra tem tags diferentes", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups", { observed: { resource_group_name: "rg-a" } }),
      policy("resource_groups", { observed: { resource_group_name: "rg-b" }, tags: { projeto: "Outro" } }),
    ]);
    expect(groups).toHaveLength(2);
  });

  it("deduplica exclusões no nível do grupo com vários RGs", () => {
    const groups = groupSubscriptionPolicies(subscription, [
      policy("resource_groups", { observed: { resource_group_name: "rg-a" } }),
      policy("resources", {
        observed: { resource_group_name: "rg-b" },
        exclusions: [
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
        ],
      }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groupExclusions(groups[0].policies).map((item) => item.rule_id)).toEqual([78]);
  });
});

describe("groupFlowTargets", () => {
  it("colapsa All e Indexed em um alvo quando a subscription é completa", () => {
    const targets = groupFlowTargets([
      policy("resource_groups"),
      policy("resources"),
    ], "Cliente Exemplo");
    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject({
      key: "subscription",
      title: "Cliente Exemplo",
      detail: "All (RG) + Indexed (recursos)",
    });
  });

  it("colapsa All e Indexed em um alvo quando só há um RG observado", () => {
    const targets = groupFlowTargets([
      policy("resource_groups", { observed: { resource_group_name: "rg-a" } }),
      policy("resources", { observed: { resource_group_name: "rg-a" } }),
    ]);
    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject({
      key: "rg-a",
      title: "rg-a",
      detail: "All (RG) + Indexed (recursos)",
    });
  });

  it("colapsa All e Indexed por RG quando há vários RGs observados", () => {
    const targets = groupFlowTargets([
      policy("resource_groups", { observed: { resource_group_name: "rg-a" } }),
      policy("resources", { observed: { resource_group_name: "rg-a" } }),
      policy("resource_groups", { observed: { resource_group_name: "rg-b" } }),
      policy("resources", { observed: { resource_group_name: "rg-b" } }),
      policy("resource_groups", { observed: { resource_group_name: "rg-c" } }),
      policy("resources", { observed: { resource_group_name: "rg-c" } }),
      policy("resource_groups", { observed: { resource_group_name: "rg-d" } }),
      policy("resources", { observed: { resource_group_name: "rg-d" } }),
    ]);
    expect(targets).toHaveLength(4);
    expect(targets.map((target) => target.key)).toEqual(["rg-a", "rg-b", "rg-c", "rg-d"]);
    expect(targets.every((target) => target.detail === "All (RG) + Indexed (recursos)")).toBe(true);
    expect(targets.map((target) => target.title)).toEqual(["rg-a", "rg-b", "rg-c", "rg-d"]);
  });

  it("mostra só a camada presente quando não há All e Indexed juntos", () => {
    const targets = groupFlowTargets([policy("resource_groups")], "Cliente Exemplo");
    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject({
      title: "Resource Groups · Cliente Exemplo",
      detail: "Cliente Exemplo",
    });
  });

  it("separa subscription completa de RGs específicos", () => {
    const targets = groupFlowTargets([
      policy("resource_groups"),
      policy("resource_groups", { observed: { resource_group_name: "rg-a" } }),
    ], "Cliente Exemplo");
    expect(targets.map((target) => target.key)).toEqual(["subscription", "rg-a"]);
    expect(targets[0].detail).toBe("Cliente Exemplo");
  });
});

describe("groupExclusions", () => {
  it("lista uma exclusão quando as duas camadas compartilham o mesmo rule_id", () => {
    const exclusions = groupExclusions([
      policy("resource_groups"),
      policy("resources", {
        exclusions: [
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
        ],
      }),
    ]);
    expect(exclusions).toHaveLength(1);
    expect(exclusions[0].rule_id).toBe(78);
  });

  it("lista duas exclusões quando os rule_ids são distintos", () => {
    const exclusions = groupExclusions([
      policy("resource_groups", {
        exclusions: [
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
          { rule_id: 90, rule_name: "Outra exclusão" },
        ],
      }),
      policy("resources", {
        exclusions: [
          { rule_id: 90, rule_name: "Outra exclusão" },
          { rule_id: 78, rule_name: "Classifica -> Prisma" },
        ],
      }),
    ]);
    expect(exclusions.map((item) => item.rule_id)).toEqual([78, 90]);
  });
});

describe("uniqueExclusions", () => {
  it("colapsa entradas repetidas do mesmo rule_id em um único item", () => {
    expect(uniqueExclusions([
      { rule_id: 78, rule_name: "Classifica -> Prisma" },
      { rule_id: 78, rule_name: "Classifica -> Prisma" },
    ])).toHaveLength(1);
  });
});

describe("canMergePolicies", () => {
  it("exige camadas distintas com metadados equivalentes", () => {
    expect(canMergePolicies(policy("resource_groups"), policy("resources"))).toBe(true);
    expect(canMergePolicies(policy("resource_groups"), policy("resource_groups"))).toBe(false);
    expect(mergeGroupKey(subscription, policy("resource_groups", { observed: { resource_group_name: "rg-a" } })))
      .toEqual(mergeGroupKey(subscription, policy("resources", { observed: { resource_group_name: "rg-b" } })));
  });
});
