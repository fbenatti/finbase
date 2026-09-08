import type { AzurePolicyPlanItem } from "../../services/api";

export type FlowPolicyKind = "resource_groups" | "resources";
export type FlowPolicy = AzurePolicyPlanItem & { kind: FlowPolicyKind };

export function targetOf(policy: FlowPolicy): string {
  return policy.observed.resource_group_name || policy.observed.resource_id || "Subscription completa";
}

export function targetLabel(policy: FlowPolicy): string {
  if (policy.observed.resource_id) return "ResourceId específico";
  if (policy.observed.resource_group_name) {
    return policy.kind === "resource_groups" ? "Resource Group" : "Recursos dentro do RG";
  }
  return policy.kind === "resource_groups" ? "Resource Groups da subscription" : "Recursos da subscription";
}

function observedKey(policy: FlowPolicy): string {
  return policy.observed.resource_group_name || policy.observed.resource_id || "subscription";
}

function exclusionsKey(exclusions: FlowPolicy["exclusions"]): string {
  return exclusions.map((entry) => entry.rule_id).sort((a, b) => a - b).join(",");
}

function tagsKey(tags: Record<string, string> | undefined): string {
  const keys = Object.keys(tags || {}).sort();
  return JSON.stringify(tags || {}, keys);
}

export function mergeGroupKey(subscription: string, policy: FlowPolicy): string {
  return `${subscription}|${policy.rule_id}|${observedKey(policy)}|${tagsKey(policy.tags)}|${exclusionsKey(policy.exclusions)}`;
}

export function canMergePolicies(a: FlowPolicy, b: FlowPolicy): boolean {
  return (
    a.rule_id === b.rule_id
    && a.rule_name === b.rule_name
    && a.priority === b.priority
    && observedKey(a) === observedKey(b)
    && tagsKey(a.tags) === tagsKey(b.tags)
    && exclusionsKey(a.exclusions) === exclusionsKey(b.exclusions)
    && a.kind !== b.kind
  );
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
    const items = [...(buckets.get(key) ?? [policy])].sort((a, b) => {
      if (a.kind === b.kind) return 0;
      return a.kind === "resource_groups" ? -1 : 1;
    });
    const hasBothLayers = items.some((item) => item.kind === "resource_groups")
      && items.some((item) => item.kind === "resources");
    const rgPolicy = items.find((item) => item.kind === "resource_groups");
    const resourcePolicy = items.find((item) => item.kind === "resources");
    const merged = Boolean(
      hasBothLayers && rgPolicy && resourcePolicy && canMergePolicies(rgPolicy, resourcePolicy),
    );
    groups.push({ key, policies: merged ? items : [policy], merged });
  }
  return groups;
}

export function tagsPreview(tags: Record<string, string> | undefined): string {
  return Object.entries(tags || {}).map(([key, value]) => `${key}=${value}`).join("\n");
}

export function scopeModesLabel(policies: FlowPolicy[]): string {
  const modes = policies.map((policy) => (
    policy.kind === "resource_groups" ? "All (Resource Group)" : "Indexed (recursos)"
  ));
  return [...new Set(modes)].join(" + ");
}

/** Vertical layout constants for the PolicyFlow organogram (React Flow). */
export const POLICY_FLOW_LAYOUT = {
  /** Y offset from rule center for RG (−) and Resources (+) in merged groups. */
  mergedTargetYOffset: 90,
  /** Base row height when RG and Resources share one rule node. */
  mergedRowHeight: 260,
  /** Base row height for a single (non-merged) policy path. */
  nonMergedRowHeight: 230,
  /** First exclusion node Y offset below rule center (non-merged). */
  exclusionStartOffset: 100,
  /** First exclusion node Y offset below rule center (merged; below Resources target). */
  mergedExclusionStartOffset: 170,
  /** Vertical step between stacked exclusion nodes. */
  exclusionRowStep: 105,
  /** Gap between subscription groups. */
  subscriptionGroupGap: 80,
} as const;
