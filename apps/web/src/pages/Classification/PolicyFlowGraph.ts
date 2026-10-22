import type { AzurePolicyPlanItem } from "../../services/api";

export type FlowPolicyKind = "resource_groups" | "resources";
export type FlowPolicy = AzurePolicyPlanItem & { kind: FlowPolicyKind };

export function targetOf(policy: FlowPolicy, subscriptionName?: string): string {
  if (policy.observed.resource_group_name) return policy.observed.resource_group_name;
  if (policy.observed.resource_id) return policy.observed.resource_id;
  return subscriptionName || "Assinatura completa";
}

export function assignmentScopePath(subscription: string, policy: FlowPolicy): string {
  if (policy.assignment_scope_kind === "resource_group" && policy.assignment_resource_group) {
    return `/subscriptions/${subscription}/resourceGroups/${policy.assignment_resource_group}`;
  }
  return `/subscriptions/${subscription}`;
}

export function targetLabel(policy: FlowPolicy, subscriptionName?: string): string {
  if (policy.observed.resource_id) return "ResourceId informado";
  if (policy.observed.resource_group_name) {
    return policy.kind === "resource_groups" ? "Grupo de recursos" : "Recursos dentro do grupo";
  }
  return policy.kind === "resource_groups"
    ? `Grupo de recursoss · ${subscriptionName || "subscription"}`
    : `Recursos · ${subscriptionName || "subscription"}`;
}

export function observedKey(policy: FlowPolicy): string {
  return policy.observed.resource_group_name || policy.observed.resource_id || "subscription";
}

export function uniqueExclusions(exclusions: FlowPolicy["exclusions"]): FlowPolicy["exclusions"] {
  const seen = new Set<number>();
  const unique: FlowPolicy["exclusions"] = [];
  for (const entry of exclusions) {
    if (seen.has(entry.rule_id)) continue;
    seen.add(entry.rule_id);
    unique.push(entry);
  }
  return unique;
}

export function groupExclusions(policies: FlowPolicy[]): FlowPolicy["exclusions"] {
  return uniqueExclusions(policies.flatMap((policy) => policy.exclusions));
}

function exclusionsKey(exclusions: FlowPolicy["exclusions"]): string {
  return [...new Set(exclusions.map((entry) => entry.rule_id))].sort((a, b) => a - b).join(",");
}

function tagsKey(tags: Record<string, string> | undefined): string {
  const keys = Object.keys(tags || {}).sort();
  return JSON.stringify(tags || {}, keys);
}

export function mergeGroupKey(subscription: string, policy: FlowPolicy): string {
  // All/Indexed can legitimately have different concrete exclusions (for
  // example, a ResourceId exception only applies to Indexed). They still share
  // the same business rule and tags, so the workflow shows one decision and
  // branches only at its RG/resource targets.
  return `${subscription}|${policy.rule_id}|${policy.rule_name}|${policy.priority}|${tagsKey(policy.tags)}`;
}

export function canMergePolicies(a: FlowPolicy, b: FlowPolicy): boolean {
  return (
    a.rule_id === b.rule_id
    && a.rule_name === b.rule_name
    && a.priority === b.priority
    && tagsKey(a.tags) === tagsKey(b.tags)
    && exclusionsKey(a.exclusions) === exclusionsKey(b.exclusions)
    && a.kind !== b.kind
  );
}

export type PolicyFlowTarget = {
  key: string;
  title: string;
  detail: string;
  policies: FlowPolicy[];
};

export function groupFlowTargets(policies: FlowPolicy[], subscriptionName?: string): PolicyFlowTarget[] {
  const buckets = new Map<string, FlowPolicy[]>();
  const order: string[] = [];
  for (const policy of policies) {
    const key = observedKey(policy);
    if (!buckets.has(key)) order.push(key);
    const list = buckets.get(key) ?? [];
    list.push(policy);
    buckets.set(key, list);
  }

  return order.map((key) => {
    const items = [...(buckets.get(key) ?? [])].sort((a, b) => {
      if (a.kind === b.kind) return 0;
      return a.kind === "resource_groups" ? -1 : 1;
    });
    const sample = items[0];
    const hasBoth = items.some((item) => item.kind === "resource_groups")
      && items.some((item) => item.kind === "resources");
    const name = targetOf(sample, subscriptionName);
    if (hasBoth) {
      return { key, title: name, detail: "All (RG) + Indexed (recursos)", policies: items };
    }
    return { key, title: targetLabel(sample, subscriptionName), detail: name, policies: items };
  });
}

export type PolicyFlowGroup = {
  key: string;
  policies: FlowPolicy[];
  merged: boolean;
};

export function groupSubscriptionPolicies(subscription: string, policies: FlowPolicy[]): PolicyFlowGroup[] {
  const buckets = new Map<string, FlowPolicy[]>();
  for (const policy of policies) {
    const key = mergeGroupKey(subscription, policy);
    const list = buckets.get(key) ?? [];
    list.push(policy);
    buckets.set(key, list);
  }

  const groups: PolicyFlowGroup[] = [];
  const seen = new Set<string>();
  for (const policy of policies) {
    const key = mergeGroupKey(subscription, policy);
    if (seen.has(key)) continue;
    seen.add(key);
    const items = [...(buckets.get(key) ?? [policy])];
    const hasBothLayers = items.some((item) => item.kind === "resource_groups")
      && items.some((item) => item.kind === "resources");
    groups.push({ key, policies: items, merged: hasBothLayers });
  }
  return groups;
}

export function tagsPreview(tags: Record<string, string> | undefined): string {
  return Object.entries(tags || {}).map(([key, value]) => `${key}=${value}`).join("\n");
}

export function scopeModesLabel(policies: FlowPolicy[]): string {
  const modes = policies.map((policy) => (
    policy.kind === "resource_groups" ? "All (Grupo de recursos)" : "Indexed (recursos)"
  ));
  return [...new Set(modes)].join(" + ");
}

/** Vertical layout constants for the PolicyFlow organogram (React Flow). */
export const POLICY_FLOW_LAYOUT = {
  /** Vertical gap between stacked orange target nodes. */
  targetStackStep: 110,
  /** Extra height after the target stack before the next group (tags/assignment sit at stack center). */
  baseRowHeight: 230,
  /** First exclusion node Y offset below the last target. */
  exclusionBelowTargetsOffset: 100,
  /** Vertical step between stacked exclusion nodes. */
  exclusionRowStep: 105,
  /** Gap between subscription groups. */
  subscriptionGroupGap: 80,
} as const;
