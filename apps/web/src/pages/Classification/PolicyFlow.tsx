import { useMemo, useState, type CSSProperties } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Chip, Dialog, DialogContent, DialogTitle, IconButton, Typography } from "@mui/material";
import { Close, ExpandMore } from "@mui/icons-material";
import { Background, BackgroundVariant, Controls, MarkerType, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AzureTenantPolicy } from "../../services/api";
import {
  groupSubscriptionPolicies,
  POLICY_FLOW_LAYOUT,
  scopeModesLabel,
  tagsPreview,
  targetLabel,
  targetOf,
  type FlowPolicy,
} from "./PolicyFlowGraph";

type Detail = { title: string; body: string; policies?: FlowPolicy[]; exception?: string };
const style = (border: string): CSSProperties => ({ border: `1px solid ${border}`, borderRadius: 10, background: "#fff", padding: 10, width: 210, fontSize: 12, lineHeight: 1.35, boxShadow: "0 2px 6px rgba(15,23,42,.10)" });
const label = (title: string, detail: string, color: string) => <Box><Typography fontWeight={700} fontSize={12} color={color}>{title}</Typography><Typography fontSize={11}>{detail}</Typography></Box>;
const edge = (id: string, source: string, target: string, color: string, animated = true): Edge => ({
  id, source, target, animated, markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: color },
});

function groupDetail(subscription: string, policies: FlowPolicy[]): Detail {
  const primary = policies[0];
  return {
    title: `Policy #${primary.rule_id}: ${primary.rule_name}`,
    body: [
      `Alvo: ${targetOf(primary)}`,
      `Assignment: /subscriptions/${subscription}`,
      `Modo: ${scopeModesLabel(policies)}`,
      `Evidência GOLD: ${policies.map((item) => item.evidence_count).join(" / ")} linha(s)`,
    ].join("\n"),
    policies,
  };
}

export default function PolicyFlow({ tenant }: { tenant?: AzureTenantPolicy }) {
  const policies = useMemo<FlowPolicy[]>(() => !tenant ? [] : (["resource_groups", "resources"] as const).flatMap((kind) => (tenant.layers?.[kind] || []).map((policy) => ({ ...policy, kind }))), [tenant]);
  const policiesBySubscription = useMemo(() => {
    const ids = [...new Set(policies.flatMap((policy) => policy.scope_subscriptions))].sort();
    return ids.map((id) => ({ id, name: tenant?.subscription_catalog?.find((entry) => entry.ids.includes(id))?.name || id, policies: policies.filter((policy) => policy.scope_subscriptions.includes(id)) }));
  }, [policies, tenant]);
  const [activeNode, setActiveNode] = useState("project");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [dialogPolicies, setDialogPolicies] = useState<FlowPolicy[]>([]);
  const graph = useMemo(() => {
    const details = new Map<string, Detail>();
    if (!tenant || !policies.length) return { nodes: [] as Node[], edges: [] as Edge[], details };
    const subscriptions = [...new Set(policies.flatMap((policy) => policy.scope_subscriptions))].sort();
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    let row = 0;
    const projectId = "project";
    nodes.push({ id: projectId, position: { x: 0, y: 0 }, data: { label: label("Projeto selecionado", tenant.department_name || "Todos os projetos", "#155e75") }, style: style("#0891b2") });
    details.set(projectId, { title: `Projeto: ${tenant.department_name || "Todos os projetos"}`, body: "Selecione uma caixa filha para abrir o detalhe da subscription, policy ou exclusão." });

    subscriptions.forEach((subscription) => {
      const name = tenant.subscription_catalog?.find((entry) => entry.ids.includes(subscription))?.name || subscription;
      const subscriptionPolicies = policies.filter((policy) => policy.scope_subscriptions.includes(subscription));
      const subId = `sub-${subscription}`;
      const subY = row;
      nodes.push({ id: subId, position: { x: 260, y: subY }, data: { label: label("Subscription Azure", `${name}\n${subscriptionPolicies.length} policies`, "#1d4ed8") }, style: style("#2563eb") });
      edges.push(edge(`${projectId}-${subId}`, projectId, subId, "#2563eb"));
      details.set(subId, { title: `Subscription: ${name}`, body: `GUID: ${subscription}\nPolicies aplicadas: ${subscriptionPolicies.length}`, policies: subscriptionPolicies });

      let policyY = subY;
      groupSubscriptionPolicies(subscription, subscriptionPolicies).forEach((group, groupIndex) => {
        const primary = group.policies[0];
        const groupId = `${subscription}-group-${group.key}-${groupIndex}`;
        const y = policyY;
        const ruleId = `rule-${groupId}`;
        const tagsId = `tags-${groupId}`;
        const assignmentId = `assignment-${groupId}`;
        const detail = groupDetail(subscription, group.policies);

        nodes.push({ id: ruleId, position: { x: 520, y }, data: { label: label(`Regra #${primary.rule_id} · p${primary.priority}`, primary.rule_name, "#6d28d9") }, style: style("#7c3aed") });
        edges.push(edge(`${subId}-${ruleId}`, subId, ruleId, "#7c3aed"));

        if (group.merged) {
          const { mergedTargetYOffset } = POLICY_FLOW_LAYOUT;
          const targetNodes = group.policies.map((item, targetIndex) => {
            const targetId = `target-${groupId}-${item.kind}`;
            const targetY = y + (targetIndex === 0 ? -mergedTargetYOffset : mergedTargetYOffset);
            nodes.push({ id: targetId, position: { x: 780, y: targetY }, data: { label: label(targetLabel(item), targetOf(item), "#b45309") }, style: style("#d97706") });
            edges.push(edge(`${ruleId}-${targetId}`, ruleId, targetId, "#d97706"));
            return targetId;
          });
          const preview = tagsPreview(primary.tags);
          nodes.push({ id: tagsId, position: { x: 1040, y }, data: { label: label("Tags da policy", `${preview || "Sem tags"}\nAll (RG) + Indexed (recursos)`, "#047857") }, style: style("#059669") });
          targetNodes.forEach((targetId) => edges.push(edge(`${targetId}-${tagsId}`, targetId, tagsId, "#059669")));
        } else {
          const item = primary;
          const targetId = `target-${groupId}`;
          nodes.push({ id: targetId, position: { x: 780, y }, data: { label: label(targetLabel(item), targetOf(item), "#b45309") }, style: style("#d97706") });
          edges.push(edge(`${ruleId}-${targetId}`, ruleId, targetId, "#d97706"));
          const preview = tagsPreview(item.tags);
          const policyExplanation = item.kind === "resource_groups" ? "Policy All\nTag no RG" : "Policy Indexed\nTag nos recursos";
          nodes.push({ id: tagsId, position: { x: 1040, y }, data: { label: label("Policy definition", `${policyExplanation}\n${preview || "Sem tags"}`, "#047857") }, style: style("#059669") });
          edges.push(edge(`${targetId}-${tagsId}`, targetId, tagsId, "#059669"));
        }

        nodes.push({ id: assignmentId, position: { x: 1300, y }, data: { label: label("Policy aplicada", `Subscription: ${name}\nRemediation + Tag Contributor`, "#047857") }, style: style("#059669") });
        edges.push(edge(`${tagsId}-${assignmentId}`, tagsId, assignmentId, "#059669"));
        const linkedNodes = group.merged
          ? [ruleId, tagsId, assignmentId, ...group.policies.map((item) => `target-${groupId}-${item.kind}`)]
          : [ruleId, `target-${groupId}`, tagsId, assignmentId];
        linkedNodes.forEach((nodeId) => details.set(nodeId, detail));

        const exclusionStartY = group.merged
          ? POLICY_FLOW_LAYOUT.mergedExclusionStartOffset
          : POLICY_FLOW_LAYOUT.exclusionStartOffset;
        primary.exclusions.forEach((exception, exceptionIndex) => {
          const exceptionId = `exception-${groupId}-${exceptionIndex}`;
          nodes.push({ id: exceptionId, position: { x: 520, y: y + exclusionStartY + exceptionIndex * POLICY_FLOW_LAYOUT.exclusionRowStep }, data: { label: label(`Exclusão #${exception.rule_id}`, exception.rule_name, "#be123c") }, style: style("#e11d48") });
          const targets = group.merged
            ? group.policies.map((item) => `target-${groupId}-${item.kind}`)
            : [`target-${groupId}`];
          targets.forEach((targetId) => {
            edges.push({
              id: `${exceptionId}-${targetId}`,
              source: exceptionId,
              target: targetId,
              label: "não aplicar",
              markerEnd: { type: MarkerType.ArrowClosed },
              style: { stroke: "#e11d48" },
              labelStyle: { fill: "#be123c", fontSize: 11 },
            });
          });
          details.set(exceptionId, {
            title: `Exclusão: #${exception.rule_id} ${exception.rule_name}`,
            body: `Esta exclusão impede a policy #${primary.rule_id} (${primary.rule_name}) de aplicar tags onde a regra Prisma/prioritária venceu.\nScope: /subscriptions/${subscription}`,
            policies: group.policies,
            exception: exception.rule_name,
          });
        });

        const baseRowHeight = group.merged
          ? POLICY_FLOW_LAYOUT.mergedRowHeight
          : POLICY_FLOW_LAYOUT.nonMergedRowHeight;
        policyY += baseRowHeight + primary.exclusions.length * POLICY_FLOW_LAYOUT.exclusionRowStep;
      });
      row = policyY + POLICY_FLOW_LAYOUT.subscriptionGroupGap;
    });
    return { nodes, edges, details };
  }, [policies, tenant]);

  const detail = graph.details.get(activeNode) || graph.details.get("project");
  const openNodeDetails = (nodeId: string) => {
    setActiveNode(nodeId);
    const nodeDetail = graph.details.get(nodeId);
    const subscription = policiesBySubscription.find((group) => `sub-${group.id}` === nodeId);
    setDialogPolicies(nodeDetail?.policies || subscription?.policies || []);
    setDetailsOpen(true);
  };

  if (!tenant || !policies.length) return <Alert severity="info">Gere as policies para visualizar o drilldown completo.</Alert>;
  return <Box>
    <Typography variant="h6" fontWeight={700} sx={{ mb: .25 }}>Projeto: {tenant.department_name || "Todos os projetos"}</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Workflow resumido: projeto → subscription → regra → escopos RG/recursos → tags compartilhadas → assignment. Use zoom e o minimapa para navegar; o drilldown abaixo mantém o detalhe auditável por policy.</Typography>
    <Box sx={{ height: 690, border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "hidden" }}><ReactFlow nodes={graph.nodes} edges={graph.edges} defaultViewport={{ x: 60, y: 60, zoom: 0.72 }} nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, node) => openNodeDetails(node.id)}><Background variant={BackgroundVariant.Dots} gap={18} size={1} /><Controls showInteractive={false} /><MiniMap zoomable pannable nodeColor="#94a3b8" /></ReactFlow></Box>
    <Dialog open={detailsOpen} onClose={() => setDetailsOpen(false)} fullWidth maxWidth="lg">
      <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>{detail?.title}<IconButton onClick={() => setDetailsOpen(false)} aria-label="Fechar detalhes"><Close /></IconButton></DialogTitle>
      <DialogContent dividers><Typography component="pre" variant="body2" sx={{ mt: 0, whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{detail?.body}</Typography>{dialogPolicies.map((policy, index) => <Box key={`${policy.rule_id}-${policy.kind}-${index}`} sx={{ mt: 2, p: 2, borderLeft: "4px solid", borderColor: policy.kind === "resource_groups" ? "primary.main" : "success.main", bgcolor: "#f8fafc" }}><Typography fontWeight={700}>#{policy.rule_id} {policy.rule_name} · {policy.kind === "resource_groups" ? "All / Resource Group" : "Indexed / recursos"}</Typography><Typography variant="body2">Alvo: {targetOf(policy)}</Typography><Typography variant="body2">Assignment: /subscriptions/{policy.scope_subscriptions.join(", ")}</Typography><Typography variant="body2">Evidência GOLD: {policy.evidence_count} linha(s)</Typography><Typography variant="body2" sx={{ mt: 1 }} fontWeight={700}>Tags implementadas</Typography><Box sx={{ mt: .5, display: "flex", gap: .5, flexWrap: "wrap" }}>{Object.entries(policy.tags || {}).map(([key, value]) => <Chip key={key} size="small" color="success" label={`${key}=${value}`} />)}</Box><Typography variant="body2" sx={{ mt: 1 }}>Exclusões: {policy.exclusions.length ? policy.exclusions.map((exception) => `#${exception.rule_id} ${exception.rule_name}${exception.observed?.resource_group_name ? ` (${exception.observed.resource_group_name})` : exception.observed?.resource_id ? ` (${exception.observed.resource_id})` : ""}`).join(" · ") : "nenhuma"}</Typography></Box>)}</DialogContent>
    </Dialog>
    <Box sx={{ mt: 1, display: "flex", gap: .75, flexWrap: "wrap" }}><Chip size="small" color="primary" label={`${new Set(policies.flatMap((policy) => policy.scope_subscriptions)).size} subscriptions`} /><Chip size="small" color="success" label={`${policies.length} policies aplicadas`} /><Chip size="small" color="error" label={`${policies.reduce((total, policy) => total + policy.exclusions.length, 0)} exclusões`} /></Box>
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" fontWeight={700}>Drilldown dos dados gerados</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.25 }}>Detalhe auditável por policy (inclui as duas camadas All/Indexed quando aplicável).</Typography>
      {policiesBySubscription.map((group) => <Accordion key={group.id} disableGutters sx={{ mb: 1, border: "1px solid", borderColor: "divider", "&:before": { display: "none" } }}>
        <AccordionSummary expandIcon={<ExpandMore />}><Box><Typography fontWeight={700}>Subscription: {group.name}</Typography><Typography variant="caption" color="text.secondary">{group.id} · {group.policies.length} policy(s)</Typography></Box></AccordionSummary>
        <AccordionDetails sx={{ pt: 0 }}>{group.policies.map((policy, index) => <Box key={`${policy.rule_id}-${policy.kind}-${index}`} sx={{ mt: 1.25, pl: 2, borderLeft: "3px solid", borderColor: policy.kind === "resource_groups" ? "primary.main" : "success.main" }}>
          <Typography variant="body2" fontWeight={700}>└─ Regra #{policy.rule_id} · {policy.rule_name} · prioridade {policy.priority}</Typography>
          <Typography variant="body2">   ├─ Alvo: {targetOf(policy)}</Typography>
          <Typography variant="body2">   ├─ Policy: {policy.kind === "resource_groups" ? "modo All para Resource Group" : "modo Indexed para recursos"}</Typography>
          <Typography variant="body2">   ├─ Assignment: /subscriptions/{group.id}</Typography>
          <Typography variant="body2">   ├─ Evidência GOLD: {policy.evidence_count} linha(s)</Typography>
          <Typography variant="body2">   ├─ Tags:</Typography>
          <Box sx={{ ml: 3, mt: .5, display: "flex", flexWrap: "wrap", gap: .5 }}>{Object.entries(policy.tags || {}).map(([key, value]) => <Chip key={key} size="small" color="success" variant="outlined" label={`${key}=${value}`} />)}</Box>
          <Typography variant="body2" sx={{ mt: .75 }}>   └─ Exclusões: {policy.exclusions.length ? policy.exclusions.map((exception) => `#${exception.rule_id} ${exception.rule_name}${exception.observed?.resource_group_name ? ` (${exception.observed.resource_group_name})` : exception.observed?.resource_id ? ` (${exception.observed.resource_id})` : ""}`).join(" · ") : "nenhuma"}</Typography>
        </Box>)}</AccordionDetails>
      </Accordion>)}
    </Box>
  </Box>;
}
