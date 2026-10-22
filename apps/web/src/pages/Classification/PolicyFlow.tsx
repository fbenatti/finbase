import { Dialog, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { useMemo, useState, type CSSProperties } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Chip, IconButton, Typography } from "@mui/material";
import { CloseOutlined, ExpandMoreOutlined } from "@mui/icons-material";
import { Background, BackgroundVariant, Controls, MarkerType, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AzureTenantPolicy } from "../../services/api";
import {
  assignmentScopePath,
  groupExclusions,
  groupFlowTargets,
  groupSubscriptionPolicies,
  POLICY_FLOW_LAYOUT,
  scopeModesLabel,
  tagsPreview,
  targetOf,
  type FlowPolicy,
} from "./PolicyFlowGraph";

type Detail = { title: string; body: string; policies?: FlowPolicy[]; exception?: string };
const style = (border: string): CSSProperties => ({ border: `1px solid ${border}`, borderRadius: 10, background: "#fff", padding: 10, width: 210, fontSize: 12, lineHeight: 1.35, boxShadow: "0 2px 6px rgba(15,23,42,.10)" });
const label = (title: string, detail: string, color: string) => <Box><Typography fontWeight={700} fontSize={12} color={color}>{title}</Typography><Typography fontSize={11}>{detail}</Typography></Box>;
const edge = (id: string, source: string, target: string, color: string, animated = true): Edge => ({
  id, source, target, animated, markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: color },
});
const exceptionTarget = (exception: { observed?: { resource_group_name?: string; resource_id?: string } }) => {
  const value = exception.observed?.resource_group_name || exception.observed?.resource_id || "escopo da regra";
  return value.length > 54 ? `${value.slice(0, 51)}…` : value;
};
const exclusionLabel = (exception: { rule_id: number; rule_name: string; observed?: { resource_group_name?: string; resource_id?: string } }) =>
  `#${exception.rule_id} ${exception.rule_name} — ${exceptionTarget(exception)}`;

function scopesLabel(policies: FlowPolicy[], subscription: string, subscriptionName: string): string {
  const scopes = [...new Set(policies.map((policy) => assignmentScopePath(subscription, policy)))];
  if (scopes.length === 1 && scopes[0] === `/subscriptions/${subscription}`) {
    return `Subscription: ${subscriptionName}`;
  }
  return scopes.map((scope) => scope.replace(`/subscriptions/${subscription}`, subscriptionName)).join("\n");
}
function ExclusionCards({ exclusions }: { exclusions: FlowPolicy["exclusions"] }) {
  if (!exclusions.length) return <Typography variant="body2" sx={{ mt: 1 }}>Sem exceções</Typography>;
  return <Box sx={{ mt: 1 }}>
    <Typography variant="body2" fontWeight={700}>Exceções</Typography>
    <Box sx={{ mt: .5, display: "grid", gap: .5 }}>
      {exclusions.map((exception, index) => <Box key={`${exception.rule_id}-${index}`} sx={{ px: 1, py: .6, border: "1px solid", borderColor: "error.light", borderRadius: 1, bgcolor: "#fff7f7" }}>
        <Typography variant="caption" color="error.dark" fontWeight={700}>#{exception.rule_id} · {exception.rule_name}</Typography>
        <Typography variant="caption" display="block" color="text.secondary">Não aplicar em: {exceptionTarget(exception)}</Typography>
      </Box>)}
    </Box>
  </Box>;
}

function groupDetail(subscription: string, subscriptionName: string, policies: FlowPolicy[]): Detail {
  const primary = policies[0];
  const targets = [...new Set(policies.map((policy) => targetOf(policy, subscriptionName)))];
  const scopes = [...new Set(policies.map((policy) => assignmentScopePath(subscription, policy)))];
  return {
    title: `Policy #${primary.rule_id}: ${primary.rule_name}`,
    body: [
      `Alvos (${targets.length}): ${targets.join(", ")}`,
      `Assignment: ${scopes.join(", ")}`,
      `Modo: ${scopeModesLabel(policies)}`,
      `Evidência BASE CONSOLIDADA: ${policies.map((item) => item.evidence_count).join(" / ")} linha(s)`,
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
    nodes.push({ id: projectId, position: { x: 0, y: 0 }, data: { label: label("Projeto escolhido", tenant.department_name || "Qualquer projeto", "#0B3FA8") }, style: style("#0EA5C6") });
    details.set(projectId, { title: `Projeto: ${tenant.department_name || "Qualquer projeto"}`, body: "Escolha uma caixa filha para abrir o detalhe da assinatura, policy ou exceção." });

    subscriptions.forEach((subscription) => {
      const name = tenant.subscription_catalog?.find((entry) => entry.ids.includes(subscription))?.name || subscription;
      const subscriptionPolicies = policies.filter((policy) => policy.scope_subscriptions.includes(subscription));
      const subId = `sub-${subscription}`;
      const subY = row;
      nodes.push({ id: subId, position: { x: 260, y: subY }, data: { label: label("Assinatura Azure", `${name}\n${subscriptionPolicies.length} policies`, "#0EA5C6") }, style: style("#0F62FE") });
      edges.push(edge(`${projectId}-${subId}`, projectId, subId, "#0F62FE"));
      details.set(subId, { title: `Subscription: ${name}`, body: `GUID: ${subscription}\nPolicies aplicadas: ${subscriptionPolicies.length}`, policies: subscriptionPolicies });

      let policyY = subY;
      groupSubscriptionPolicies(subscription, subscriptionPolicies).forEach((group, groupIndex) => {
        const primary = group.policies[0];
        const targets = groupFlowTargets(group.policies, name);
        const groupId = `${subscription}-group-${group.key}-${groupIndex}`;
        const y = policyY;
        const stackHeight = Math.max(0, targets.length - 1) * POLICY_FLOW_LAYOUT.targetStackStep;
        const ruleY = y + stackHeight / 2;
        const ruleId = `rule-${groupId}`;
        const tagsId = `tags-${groupId}`;
        const assignmentId = `assignment-${groupId}`;
        const detail = groupDetail(subscription, name, group.policies);

        nodes.push({ id: ruleId, position: { x: 520, y: ruleY }, data: { label: label(`Regra #${primary.rule_id} · p${primary.priority}`, primary.rule_name, "#0B3FA8") }, style: style("#64748B") });
        edges.push(edge(`${subId}-${ruleId}`, subId, ruleId, "#64748B"));

        const targetIds = targets.map((target, targetIndex) => {
          const targetId = `target-${groupId}-${targetIndex}`;
          const targetY = y + targetIndex * POLICY_FLOW_LAYOUT.targetStackStep;
          nodes.push({ id: targetId, position: { x: 780, y: targetY }, data: { label: label(target.title, target.detail, "#b45309") }, style: style("#d97706") });
          edges.push(edge(`${ruleId}-${targetId}`, ruleId, targetId, "#d97706"));
          return targetId;
        });

        const preview = tagsPreview(primary.tags);
        const bothKinds = group.merged
          || (group.policies.some((item) => item.kind === "resource_groups")
            && group.policies.some((item) => item.kind === "resources"));
        const tagsHeading = bothKinds || targets.length > 1 ? "Etiquetas da política" : "Definição da política";
        const tagsDetail = bothKinds
          ? `${preview || "Sem etiquetas"}\nAll (RG) + Indexed (recursos)`
          : `${primary.kind === "resource_groups" ? "Policy All\nTag no RG" : "Policy Indexed\nTag nos recursos"}\n${preview || "Sem etiquetas"}`;
        nodes.push({ id: tagsId, position: { x: 1040, y: ruleY }, data: { label: label(tagsHeading, tagsDetail, "#047857") }, style: style("#059669") });
        targetIds.forEach((targetId) => edges.push(edge(`${targetId}-${tagsId}`, targetId, tagsId, "#059669")));

        nodes.push({ id: assignmentId, position: { x: 1300, y: ruleY }, data: { label: label("Política aplicada", `${scopesLabel(group.policies, subscription, name)}\nRemediation + Tag Contributor`, "#047857") }, style: style("#059669") });
        edges.push(edge(`${tagsId}-${assignmentId}`, tagsId, assignmentId, "#059669"));
        [ruleId, tagsId, assignmentId, ...targetIds].forEach((nodeId) => details.set(nodeId, detail));

        const exclusions = groupExclusions(group.policies);
        const exclusionStartY = y + stackHeight + POLICY_FLOW_LAYOUT.exclusionBelowTargetsOffset;
        exclusions.forEach((exception, exceptionIndex) => {
          const exceptionId = `exception-${groupId}-${exception.rule_id}`;
          nodes.push({ id: exceptionId, position: { x: 520, y: exclusionStartY + exceptionIndex * POLICY_FLOW_LAYOUT.exclusionRowStep }, data: { label: label(`Exceção nº ${exception.rule_id}`, `${exception.rule_name}\n${exceptionTarget(exception)}`, "#be123c") }, style: style("#e11d48") });
          targetIds.forEach((targetId) => {
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
            title: `Exceção: nº ${exception.rule_id} ${exception.rule_name}`,
            body: `Esta exceção impede a policy #${primary.rule_id} (${primary.rule_name}) de aplicar tags onde a regra #${exception.rule_id} (${exception.rule_name}) tem prioridade maior.\nScope: /subscriptions/${subscription}`,
            policies: group.policies,
            exception: exception.rule_name,
          });
        });

        policyY += stackHeight + POLICY_FLOW_LAYOUT.baseRowHeight + exclusions.length * POLICY_FLOW_LAYOUT.exclusionRowStep;
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

  if (!tenant || !policies.length) return <Alert severity="info">Gere as policies para visualizar o detalhamento completo.</Alert>;
  return <Box>
    <Typography variant="h6" fontWeight={700} sx={{ mb: .25 }}>Projeto: {tenant.department_name || "Qualquer projeto"}</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Fluxo resumido: projeto → assinatura → regra → escopos RG/recursos → tags compartilhadas → assignment. Use zoom e o minimapa para navegar; o drilldown abaixo mantém o detalhe auditável por policy.</Typography>
    <Box sx={{ height: 690, border: "1px solid", borderColor: "divider", borderRadius: 2, overflow: "hidden" }}><ReactFlow nodes={graph.nodes} edges={graph.edges} defaultViewport={{ x: 60, y: 60, zoom: 0.72 }} nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, node) => openNodeDetails(node.id)}><Background variant={BackgroundVariant.Dots} gap={18} size={1} /><Controls showInteractive={false} /><MiniMap zoomable pannable nodeColor="#8497A8" /></ReactFlow></Box>
    <Dialog open={detailsOpen} onClose={() => setDetailsOpen(false)} fullWidth maxWidth="lg">
      <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>{detail?.title}<IconButton onClick={() => setDetailsOpen(false)} aria-label="Fechar detalhamento"><CloseOutlined /></IconButton></DialogTitle>
      <DialogContent dividers><Typography component="pre" variant="body2" sx={{ mt: 0, whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{detail?.body}</Typography>{dialogPolicies.map((policy, index) => {
        const subscriptionId = policy.scope_subscriptions[0] || "";
        const subscriptionName = tenant?.subscription_catalog?.find((entry) => entry.ids.includes(subscriptionId))?.name || subscriptionId;
        return <Box key={`${policy.rule_id}-${policy.kind}-${index}`} sx={{ mt: 2, p: 2, borderLeft: "4px solid", borderColor: policy.kind === "resource_groups" ? "primary.main" : "success.main", bgcolor: "#FAFBFD" }}><Typography fontWeight={700}>#{policy.rule_id} {policy.rule_name} · {policy.kind === "resource_groups" ? "Tudo / grupo de recursos" : "Indexed / recursos"}</Typography><Typography variant="body2">Alvo: {targetOf(policy, subscriptionName)}</Typography><Typography variant="body2">Assignment: {assignmentScopePath(subscriptionId, policy)}</Typography><Typography variant="body2">Evidência BASE CONSOLIDADA: {policy.evidence_count} linha(s)</Typography><Typography variant="body2" sx={{ mt: 1 }} fontWeight={700}>Tags implementadas</Typography><Box sx={{ mt: .5, display: "flex", gap: .5, flexWrap: "wrap" }}>{Object.entries(policy.tags || {}).map(([key, value]) => <Chip key={key} size="small" color="success" label={`${key}=${value}`} />)}</Box><ExclusionCards exclusions={policy.exclusions} /></Box>;
      })}</DialogContent>
    </Dialog>
    <Box sx={{ mt: 1, display: "flex", gap: .75, flexWrap: "wrap" }}><Chip size="small" color="primary" label={`${new Set(policies.flatMap((policy) => policy.scope_subscriptions)).size} subscriptions`} /><Chip size="small" color="success" label={`${policies.length} policies aplicadas`} /><Chip size="small" color="error" label={`${policies.reduce((total, policy) => total + policy.exclusions.length, 0)} exceções`} /></Box>
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" fontWeight={700}>Detalhamento dos dados gerados</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.25 }}>Detalhe auditável por policy (inclui as duas camadas All/Indexed quando aplicável).</Typography>
      {policiesBySubscription.map((group) => <Accordion key={group.id} disableGutters sx={{ mb: 1, border: "1px solid", borderColor: "divider", "&:before": { display: "none" } }}>
        <AccordionSummary expandIcon={<ExpandMoreOutlined />}><Box><Typography fontWeight={700}>Subscription: {group.name}</Typography><Typography variant="caption" color="text.secondary">{group.id} · {group.policies.length} policy(s)</Typography></Box></AccordionSummary>
        <AccordionDetails sx={{ pt: 0 }}>{group.policies.map((policy, index) => <Box key={`${policy.rule_id}-${policy.kind}-${index}`} sx={{ mt: 1.25, pl: 2, borderLeft: "3px solid", borderColor: policy.kind === "resource_groups" ? "primary.main" : "success.main" }}>
          <Typography variant="body2" fontWeight={700}>└─ Regra #{policy.rule_id} · {policy.rule_name} · prioridade {policy.priority}</Typography>
          <Typography variant="body2">Alvo: {targetOf(policy, group.name)}</Typography>
          <Typography variant="body2">   ├─ Policy: {policy.kind === "resource_groups" ? "modo All para Resource Group" : "modo Indexed para recursos"}</Typography>
          <Typography variant="body2">   ├─ Assignment: {assignmentScopePath(group.id, policy)}</Typography>
          <Typography variant="body2">   ├─ Evidência BASE CONSOLIDADA: {policy.evidence_count} linha(s)</Typography>
          <Typography variant="body2">   ├─ Tags:</Typography>
          <Box sx={{ ml: 3, mt: .5, display: "flex", flexWrap: "wrap", gap: .5 }}>{Object.entries(policy.tags || {}).map(([key, value]) => <Chip key={key} size="small" color="success" variant="outlined" label={`${key}=${value}`} />)}</Box>
          <ExclusionCards exclusions={policy.exclusions} />
        </Box>)}</AccordionDetails>
      </Accordion>)}
    </Box>
  </Box>;
}
