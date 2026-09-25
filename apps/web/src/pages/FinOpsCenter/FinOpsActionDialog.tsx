import { useEffect, useMemo, useState } from "react";
import Plot from "@/components/Plot";
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogContent,
  DialogTitle, Divider, FormControl, InputLabel, MenuItem, Select, Stack,
  LinearProgress, Paper, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import {
  FinOpsActionDetail, FinOpsFamilyOption, FinOpsNamespaceDetail,
  FinOpsWorkerDetail, getFinopsActionDetail, getFinopsJevReview, getFinopsNamespaceDetail, getFinopsWorkerDetail,
} from "../../services/api";

function Chart(props: any) { return <Plot {...props} />; }

function withTimeGaps<T>(
  rows: T[], timestamp: (row: T) => string | null | undefined,
  value: (row: T) => number | null, stepMs = 60 * 60 * 1000,
) {
  const ordered = rows.filter((row) => timestamp(row)).sort((a, b) => String(timestamp(a)).localeCompare(String(timestamp(b))));
  const x: string[] = [];
  const y: Array<number | null> = [];
  let previousMs: number | null = null;
  for (const row of ordered) {
    const stamp = String(timestamp(row));
    const currentMs = Date.parse(stamp);
    if (previousMs != null && currentMs - previousMs > stepMs * 1.5) {
      x.push(new Date(previousMs + stepMs).toISOString());
      y.push(null);
    }
    x.push(stamp);
    y.push(value(row));
    previousMs = currentMs;
  }
  return { x, y };
}

const brl = (value: unknown) => value == null
  ? "Sem preço comparável"
  : Number(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 });

function optionName(option: FinOpsFamilyOption) {
  return `${option.vendor} · ${option.sku}`;
}

const metricNames: Record<string, string> = {
  "Percentage CPU": "CPU utilizada",
  "Available Memory Percentage": "Memória disponível",
  "Available Memory Bytes": "Memória disponível",
  "Disk Read Operations/Sec": "IOPS de leitura (VM agregada)",
  "Disk Write Operations/Sec": "IOPS de gravação (VM agregada)",
  "Disk Read Bytes": "Dados lidos (VM agregada)",
  "Disk Write Bytes": "Dados gravados (VM agregada)",
  "Network In Total": "Tráfego de entrada",
  "Network Out Total": "Tráfego de saída",
};
function profileValue(value: number | null, metric: string, unit?: string | null) {
  if (value == null) return "—";
  let n = Number(value);
  let displayUnit = unit || "";
  if (metric === "Available Memory Bytes") { n /= 1024 ** 3; displayUnit = "GiB"; }
  else if (displayUnit.toLowerCase().includes("bytespersecond")) { n /= 1024 ** 2; displayUnit = "MiB/s"; }
  else if (displayUnit.toLowerCase().includes("countpersecond")) displayUnit = "IOPS";
  else if (displayUnit.toLowerCase() === "percent") displayUnit = "%";
  return `${n.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}${displayUnit ? ` ${displayUnit}` : ""}`;
}

function AksTopologyPanel({
  topology, action, targetSku, targetCost, targetSaving, onOpenPool, onOpenNamespace, onOpenWorker, onShowNamespaceStatus,
}: {
  topology: NonNullable<FinOpsActionDetail["aks_topology"]>;
  action: FinOpsActionDetail["action"];
  targetSku: string;
  targetCost: number | null | undefined;
  targetSaving: number | null | undefined;
  onOpenPool: () => void;
  onOpenNamespace: (namespace: string) => void;
  onOpenWorker: (worker: string) => void;
  onShowNamespaceStatus: (reason: string) => void;
}) {
  if (topology.status !== "AVAILABLE") {
    return <Alert severity="warning">Não foi possível montar a topologia AKS deste VMSS. {topology.reason || "Confira as referências disponíveis no Silver."}</Alert>;
  }
  const workers = topology.workers ?? 0;
  const totalCost = action.cost_from_current;
  const savingPerWorker = workers > 0 && targetSaving != null ? targetSaving / workers : null;
  const projectedPerWorker = workers > 0 && targetCost != null ? targetCost / workers : null;

  return (
    <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 }, borderRadius: 2 }}>
      <Stack spacing={2.5}>
        <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" spacing={1}>
          <Box>
            <Typography variant="h6" fontWeight={700}>Topologia AKS e plano por node pool</Typography>
            <Typography variant="body2" color="text.secondary">Este VMSS representa o pool {topology.pool_name}; a mudança de SKU é avaliada para o pool inteiro.</Typography>
          </Box>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Chip label={`${workers} workers no pool`} color="primary" />
            <Chip label={`${topology.total_workers ?? 0} workers no cluster`} variant="outlined" />
            <Chip label={`${topology.pools?.length ?? 0} pools`} variant="outlined" />
          </Stack>
        </Stack>

        <Box sx={{ p: 1.75, border: "1px solid", borderColor: "divider", bgcolor: "action.hover", borderRadius: 1.5 }}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} justifyContent="space-between">
            <Box><Typography variant="caption" color="text.secondary">Cluster gerenciado</Typography><Typography fontWeight={700}>{topology.cluster_name || "AKS"}</Typography><Typography variant="caption" color="text.secondary">{topology.cluster_resource_id}</Typography></Box>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
              <Chip size="small" label={`Região ${topology.region || action.region_id || "—"}`} />
              <Chip size="small" label={`Kubernetes ${topology.kubernetes_version || "versão não coletada"}`} />
              {topology.os_type && <Chip size="small" label={`SO ${topology.os_type}`} />}
              <Chip size="small" label={`Snapshot ${topology.snapshot_date || "—"}`} />
            </Stack>
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
            {topology.orchestrator || "Orquestrador não coletado"}
            {topology.vmss_resource_group ? ` · Resource group ${topology.vmss_resource_group}` : ""}
            {topology.vmss_inventory_snapshot ? ` · Inventário VMSS ${new Date(topology.vmss_inventory_snapshot).toLocaleDateString("pt-BR")}` : ""}
          </Typography>
        </Box>

        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Cobertura do Container Insights</Typography>
          {(topology.container_coverage || []).length ? (topology.container_coverage || []).map((row, index) => (
            <Alert key={`${row.snapshot_date}-${index}`} severity={row.coverage_status === "AVAILABLE" ? "success" : "warning"} sx={{ mt: 1 }}>
              {row.coverage_status || "Estado não informado"} · {row.snapshot_date || "snapshot sem data"}{row.coverage_reason ? ` · ${row.coverage_reason}` : ""}
            </Alert>
          )) : <Alert severity="info" sx={{ mt: 1 }}>Não há snapshot de cobertura neste período.</Alert>}
        </Box>

        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Inventário diário de pods e nodes</Typography>
          {(topology.container_inventory || []).length ? <Table size="small"><TableHead><TableRow><TableCell>Snapshot</TableCell><TableCell>Tipo</TableCell><TableCell>Namespace / nó</TableCell><TableCell align="right">Pods</TableCell><TableCell align="right">Containers</TableCell><TableCell align="right">Nodes</TableCell><TableCell>Kubernetes</TableCell></TableRow></TableHead><TableBody>
            {(topology.container_inventory || []).map((row, index) => <TableRow key={`${row.snapshot_date}-${row.record_kind}-${row.namespace}-${row.computer}-${index}`}>
              <TableCell>{row.snapshot_date || "—"}</TableCell><TableCell>{row.record_kind || "—"}</TableCell><TableCell>{[row.namespace, row.computer].filter(Boolean).join(" · ") || "—"}</TableCell><TableCell align="right">{row.pods ?? "—"}</TableCell><TableCell align="right">{row.containers ?? "—"}</TableCell><TableCell align="right">{row.nodes ?? "—"}</TableCell><TableCell>{row.kubernetes_version || "—"}</TableCell>
            </TableRow>)}
          </TableBody></Table> : <Alert severity="info" sx={{ mt: 1 }}>Sem linhas de inventário de pods ou nodes neste recorte.</Alert>}
        </Box>

        <Box sx={{ display: "flex", justifyContent: "center", height: 22 }}><Box sx={{ borderLeft: "2px solid", borderColor: "primary.light" }} /></Box>
        <Stack direction={{ xs: "column", lg: "row" }} spacing={1.5} useFlexGap flexWrap="wrap" aria-label="Pools e workers AKS">
          {(topology.pools || []).map((pool) => {
            const selectedPool = pool.name.toLowerCase() === (topology.pool_name || "").toLowerCase();
            return (
              <Box key={`${pool.name}-${pool.sku}`} onClick={selectedPool ? onOpenPool : undefined} role={selectedPool ? "button" : undefined} tabIndex={selectedPool ? 0 : undefined} onKeyDown={selectedPool ? (event) => { if (event.key === "Enter" || event.key === " ") onOpenPool(); } : undefined} sx={{ flex: "1 1 240px", minWidth: 220, p: 1.75, border: "2px solid", borderColor: selectedPool ? "primary.main" : "divider", borderRadius: 1.5, bgcolor: selectedPool ? "primary.50" : "background.paper", cursor: selectedPool ? "pointer" : "default", "&:hover": selectedPool ? { boxShadow: 3 } : undefined }}>
                <Stack direction="row" justifyContent="space-between" spacing={1}>
                  <Box><Typography fontWeight={700}>{pool.name}</Typography><Typography variant="body2">{pool.mode || "Modo não coletado"} · {pool.sku || "SKU não coletado"}</Typography></Box>
                  {selectedPool && <Chip size="small" color="primary" label="Em análise" />}
                </Stack>
                {selectedPool && <Button size="small" sx={{ mt: 0.5, px: 0, textTransform: "none" }} onClick={(event) => { event.stopPropagation(); onOpenPool(); }}>Abrir monitoramento do pool →</Button>}
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                  {pool.workers} workers · peso de alocação por capacidade {(pool.allocation_weight * 100).toFixed(1)}%
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1.25 }}>
                  {selectedPool && Array.from(new Set((topology.container_metrics || []).map((row) => row.computer).filter((name): name is string => Boolean(name)))).map((worker) => <Button key={worker} size="small" variant="outlined" onClick={(event) => { event.stopPropagation(); onOpenWorker(worker); }}>{worker}</Button>)}
                  {selectedPool && !(topology.container_metrics || []).some((row) => row.computer) && <Typography variant="caption" color="text.secondary">Nomes dos nós não disponíveis na coleta atual</Typography>}
                </Stack>
              </Box>
            );
          })}
        </Stack>
        <Typography variant="caption" color="text.secondary">O peso mostrado distribui a capacidade entre pools; não representa uma medição de custo por pool. Origem: resource_allocation e resource_inventory no Silver.</Typography>

        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Workers e consumo dos workloads</Typography>
          {(topology.container_metrics || []).some((row) => row.computer) ? <Typography variant="body2" color="text.secondary">Selecione um nó para consultar o histórico de métricas e os namespaces observados nele.</Typography> : <Alert severity="warning" sx={{ mt: 1 }}>Ainda não há workers identificados nem métricas de workload para este cluster. O Container Insights está {topology.container_insights_status === "AVAILABLE" ? "habilitado, mas sem linhas por nó" : "indisponível"}: {topology.container_insights_reason || "sem cobertura registrada no Silver"}</Alert>}
        </Box>

        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Namespaces e workloads coletados</Typography>
          {(() => {
            const namespaceRows = (topology.container_metrics || []).filter((row) => row.namespace);
            const allocations = topology.namespace_allocations || [];
            const namespaces = Array.from(new Set([
              ...namespaceRows.map((row) => row.namespace!),
              ...allocations.map((row) => row.namespace).filter((namespace): namespace is string => Boolean(namespace)),
            ]));
            return namespaces.length ? (
              <Table size="small">
                <TableHead><TableRow><TableCell>Namespace</TableCell><TableCell align="right">Pods</TableCell><TableCell align="right">Containers</TableCell><TableCell align="right">Nodes</TableCell><TableCell align="right">Capacidade alocada</TableCell><TableCell>Snapshot</TableCell></TableRow></TableHead>
                <TableBody>{namespaces.map((namespace) => {
                  const rows = namespaceRows.filter((row) => row.namespace === namespace);
                  const latest = rows[rows.length - 1];
                  const allocation = allocations.find((row) => row.namespace === namespace);
                  const maxOf = (key: "pods" | "containers" | "nodes") => Math.max(0, ...rows.map((row) => Number(row[key] || 0)));
                  return <TableRow key={namespace} hover onClick={() => onOpenNamespace(namespace)} sx={{ cursor: "pointer" }}>
                    <TableCell><Button size="small" sx={{ textTransform: "none", fontWeight: 700, p: 0, minWidth: 0 }}>{namespace}</Button><Typography variant="caption" display="block" color="text.secondary">Abrir métricas e histórico →</Typography></TableCell>
                    <TableCell align="right">{maxOf("pods") || "—"}</TableCell><TableCell align="right">{maxOf("containers") || "—"}</TableCell><TableCell align="right">{maxOf("nodes") || "—"}</TableCell><TableCell align="right">{allocation?.allocation_weight == null ? "—" : `${(Number(allocation.allocation_weight) * 100).toFixed(1)}%`}</TableCell><TableCell>{latest?.snapshot_date || allocation?.snapshot_date || "—"}</TableCell>
                  </TableRow>;
                })}</TableBody>
              </Table>
            ) : <Alert severity="warning" action={<Button color="inherit" size="small" onClick={() => onShowNamespaceStatus(`Cluster ${topology.cluster_name || topology.cluster_resource_id}: não há linhas de namespace em container_metric_series nem resource_allocation no Silver. ${topology.container_insights_reason || "Configure e execute a coleta de namespaces via Managed Prometheus para disponibilizar CPU/memória e alocação por namespace."}`)}>Ver status da coleta</Button>}>
              Nenhum namespace disponível para clicar neste cluster. A lista aparece quando o Silver recebe dados por namespace.
            </Alert>;
          })()}
        </Box>

        <Divider />
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>De/Para do pool selecionado</Typography>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ mt: 1 }}>
            <Box sx={{ flex: 1, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}><Typography variant="caption" color="text.secondary">Pool atual · {workers} workers</Typography><Typography fontWeight={700}>{action.sku_from} → {targetSku}</Typography><Typography variant="body2">Custo observado na base do cenário {brl(totalCost)} · {brl(workers ? (totalCost ?? 0) / workers : null)} por worker equivalente</Typography></Box>
            <Box sx={{ flex: 1, p: 1.5, borderRadius: 1, bgcolor: "success.50" }}><Typography variant="caption" color="text.secondary">Cenário escolhido · mesma contagem de workers</Typography><Typography fontWeight={700}>{workers} × {targetSku}</Typography><Typography variant="body2">Custo projetado sobre essa base {brl(targetCost)} · {brl(projectedPerWorker)} por worker equivalente</Typography><Typography variant="body2" color={savingPerWorker != null && savingPerWorker > 0 ? "success.main" : "text.secondary"}>Saving estimado nessa base {brl(targetSaving)} no pool · {brl(savingPerWorker)} por worker equivalente</Typography></Box>
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>O rateio por worker divide o custo do pool pela contagem declarada; é uma aproximação uniforme, não uma cobrança medida individualmente.</Typography>
        </Box>

        <Alert severity={topology.container_insights_status === "AVAILABLE" ? "success" : "warning"}>
          <Typography fontWeight={700}>Dados de pods e carga por worker: {topology.container_insights_status === "AVAILABLE" ? "disponíveis" : "indisponíveis"}</Typography>
          <Typography variant="body2">{topology.container_insights_reason || "Container Insights não tem cobertura registrada para este cluster."}</Typography>
          {topology.container_insights_status !== "AVAILABLE" && <Typography variant="body2">A recomendação usa as métricas agregadas do VMSS. O Silver não permite confirmar se cada worker e workload cabe no tamanho menor; valide requests/limits, PDB, quota e capacidade de surge em uma migração controlada.</Typography>}
        </Alert>
        <Typography variant="caption" color="text.secondary">Para pools AKS VMSS, a troca de tamanho é uma migração do node pool: crie o pool de destino, mova workloads com cordon/drain e remova o pool antigo após validar a aplicação. <a href="https://learn.microsoft.com/en-us/azure/aks/aks-virtual-machine-sizes" target="_blank" rel="noreferrer">Documentação Microsoft sobre tamanhos de VM no AKS</a>.</Typography>
      </Stack>
    </Paper>
  );
}

function PoolMonitoringDialog({
  open, detail, onClose, onOpenWorker,
}: { open: boolean; detail: FinOpsActionDetail | null; onClose: () => void; onOpenWorker: (worker: string) => void }) {
  const topology = detail?.aks_topology;
  const action = detail?.action;
  const metricGroups = new Map<string, typeof detail extends null ? never : NonNullable<FinOpsActionDetail["series"]>>();
  for (const row of detail?.series || []) {
    if (!metricGroups.has(row.metric)) metricGroups.set(row.metric, [] as any);
    (metricGroups.get(row.metric) as any[]).push(row);
  }
  type ContainerRows = NonNullable<NonNullable<FinOpsActionDetail["aks_topology"]>["container_metrics"]>;
  const workerMap = new Map<string, ContainerRows>();
  for (const row of topology?.container_metrics || []) {
    if (!row.computer) continue;
    if (!workerMap.has(row.computer)) workerMap.set(row.computer, []);
    workerMap.get(row.computer)!.push(row);
  }
  const workers = Array.from(workerMap.entries());
  const allocationCount = topology?.namespace_allocations?.length || 0;
  const hasWorkloadMetrics = (topology?.container_metrics || []).some((row) => row.record_kind === "performance" && Boolean(row.computer && row.namespace && row.metric_name));
  const riskSignals = [
    { label: "CPU e memória dos workloads", state: hasWorkloadMetrics ? "Observado" : "Sem série por workload", evidence: hasWorkloadMetrics ? "Série disponível para comparar pressão de consumo." : "O gráfico atual é agregado no VMSS ou não há telemetria de workload." },
    { label: "Pods distribuídos entre workers", state: workers.length ? "Parcial" : "Sem dados", evidence: workers.length ? `${workers.length} nó(s) identificados; a distribuição depende das linhas por namespace e nó.` : "Não há workers identificados pela telemetria do cluster." },
    { label: "Requests e limits de CPU/RAM", state: allocationCount ? "Parcial" : "Sem dados", evidence: allocationCount ? "Há alocação por namespace; ainda não há requests/limits por workload nesta visão." : "Não há snapshots de alocação por namespace." },
    { label: "Réplicas disponíveis e PDB", state: "Não coletado", evidence: "Necessário para estimar interrupção durante cordon/drain." },
    { label: "Afinidade, tolerations e volumes", state: "Não coletado", evidence: "Necessário para confirmar se o workload pode agendar no pool de destino e mover dados persistentes." },
    { label: "Capacidade livre no pool de destino", state: "Não validado", evidence: "A topologia lista pools e contagens, mas não prova headroom após a migração." },
  ];
  const riskKnown = hasWorkloadMetrics && workers.length > 0 && allocationCount > 0;
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="xl">
    <DialogTitle>Monitoramento do pool · {topology?.pool_name || action?.resource_name || "AKS"}</DialogTitle>
    <DialogContent dividers>
      {!detail || !topology ? <Alert severity="info">Carregando os dados de monitoramento do pool.</Alert> : <Stack spacing={2.5}>
        <Alert severity={(detail.metric_window?.observed_days ?? 0) < detail.metric_window.requested_days ? "warning" : "info"}>
          Histórico UTC {detail.metric_window.from_day}–{detail.metric_window.to_day}: {detail.metric_window.observed_days ?? 0} de {detail.metric_window.requested_days} dias com amostras. Séries agregadas por hora; dias sem amostras ficam como lacunas.
        </Alert>
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
          <Typography variant="overline" color="text.secondary">Visão agregada do node pool</Typography>
          <Typography variant="h6" fontWeight={700}>{topology.cluster_name} · {topology.pool_name}</Typography>
          <Typography variant="body2">{topology.workers ?? 0} workers declarados · {action?.sku_from || "SKU —"} · Região {topology.region || action?.region_id || "—"}</Typography>
          <Typography variant="caption" color="text.secondary">Os gráficos abaixo são as métricas coletadas para o resource_id do VMSS; eles representam o pool agregado.</Typography>
        </Paper>
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Consumo do pool</Typography>
          {metricGroups.size ? <Stack spacing={1.5} sx={{ mt: 1 }}>{Array.from(metricGroups.entries()).map(([metric, rows]) => {
            const sorted = [...rows].sort((a, b) => a.bucket_start_utc.localeCompare(b.bucket_start_utc));
            const unit = sorted.find((row) => row.metric_unit)?.metric_unit || "";
            const points = withTimeGaps(sorted, (row) => row.bucket_start_utc, (row) => Number(row.value));
            const trace = { type: "scatter", mode: "lines", connectgaps: false, name: metricNames[metric] || metric, x: points.x, y: points.y, line: { width: 1.6 }, hovertemplate: `%{x}<br>%{y:.3f}${unit ? ` ${unit}` : ""}<extra>${metricNames[metric] || metric}</extra>` };
            return <Paper key={metric} variant="outlined" sx={{ p: 1.5, borderRadius: 1.5 }}><Typography variant="body2" fontWeight={700}>{metricNames[metric] || metric}{unit ? ` · ${unit}` : ""}</Typography><Chart data={[trace]} layout={{ autosize: true, height: 260, margin: { l: 65, r: 20, t: 12, b: 55 }, xaxis: { title: "Horário da coleta" }, yaxis: { title: unit || "Valor coletado", rangemode: "tozero" }, showlegend: false }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} /></Paper>;
          })}</Stack> : <Alert severity="info" sx={{ mt: 1 }}>Não há séries agregadas de consumo para este pool no período consultado.</Alert>}
        </Box>
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Risco de migração dos workloads</Typography>
          <Alert severity={riskKnown ? "warning" : "info"} sx={{ mt: 1, mb: 1.5 }}>
            <Typography fontWeight={700}>{riskKnown ? "Avaliação parcial · faltam sinais de segurança operacional" : "Risco indeterminado · cobertura insuficiente"}</Typography>
            <Typography variant="body2">A avaliação considera consumo, distribuição dos pods, requests/limits, réplicas/PDB, regras de agendamento, volumes e capacidade do destino. Dados ausentes não são tratados como risco baixo; nenhum score confiável pode ser calculado enquanto faltarem os sinais críticos.</Typography>
          </Alert>
          <Table size="small"><TableHead><TableRow><TableCell>Fator de risco</TableCell><TableCell>Estado</TableCell><TableCell>Evidência / validação necessária</TableCell></TableRow></TableHead><TableBody>{riskSignals.map((signal) => <TableRow key={signal.label}><TableCell><Typography fontWeight={600}>{signal.label}</Typography></TableCell><TableCell><Chip size="small" label={signal.state} color={signal.state === "Observado" ? "success" : signal.state === "Parcial" ? "warning" : "default"} variant="outlined" /></TableCell><TableCell>{signal.evidence}</TableCell></TableRow>)}</TableBody></Table>
        </Box>
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Servidores e workloads observados</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>A grade usa nomes de nó (`computer`) e métricas que chegaram pela telemetria AKS. Clique em um servidor para abrir seu histórico.</Typography>
          {workers.length ? <Table size="small"><TableHead><TableRow><TableCell>Servidor / nó</TableCell><TableCell>Namespaces observados</TableCell><TableCell>Métricas disponíveis</TableCell><TableCell align="right">Pods</TableCell><TableCell align="right">Containers</TableCell><TableCell>Última coleta</TableCell><TableCell /></TableRow></TableHead><TableBody>{workers.map(([worker, rows]) => {
            const namespaces = Array.from(new Set(rows.map((row) => row.namespace).filter(Boolean)));
            const metrics = Array.from(new Set(rows.map((row) => row.metric_name).filter(Boolean)));
            const dates = rows.map((row) => row.snapshot_date || "").sort();
            return <TableRow key={worker} hover><TableCell><Typography fontWeight={700}>{worker}</Typography></TableCell><TableCell>{namespaces.join(", ") || "—"}</TableCell><TableCell>{metrics.join(", ") || rows.map((row) => row.record_kind).filter(Boolean).join(", ") || "—"}</TableCell><TableCell align="right">{Math.max(0, ...rows.map((row) => Number(row.pods || 0))) || "—"}</TableCell><TableCell align="right">{Math.max(0, ...rows.map((row) => Number(row.containers || 0))) || "—"}</TableCell><TableCell>{dates[dates.length - 1] || "—"}</TableCell><TableCell><Button size="small" onClick={() => onOpenWorker(worker)}>Ver consumo</Button></TableCell></TableRow>;
          })}</TableBody></Table> : <Alert severity="warning">Não há servidores identificados na telemetria deste cluster. {topology.container_insights_reason || "A coleta de Container Insights está indisponível ou ainda não registrou nós."}</Alert>}
        </Box>
      </Stack>}
    </DialogContent>
  </Dialog>;
}

function WorkerDetailDialog({
  open, loading, error, detail, onClose,
}: { open: boolean; loading: boolean; error: string | null; detail: FinOpsWorkerDetail | null; onClose: () => void }) {
  const rows = detail?.series || [];
  const groups = new Map<string, Map<string, number[]>>();
  for (const row of rows) {
    if (row.metric_average == null || !row.snapshot_date || !row.metric_name) continue;
    const name = [row.namespace, row.metric_name].filter(Boolean).join(" · ");
    if (!groups.has(name)) groups.set(name, new Map());
    const dates = groups.get(name)!;
    if (!dates.has(row.snapshot_date)) dates.set(row.snapshot_date, []);
    dates.get(row.snapshot_date)!.push(Number(row.metric_average));
  }
  const traces = Array.from(groups.entries()).map(([name, dates]) => {
    const points = Array.from(dates.entries()).sort(([a], [b]) => a.localeCompare(b));
    const gapAware = withTimeGaps(points.map(([timestamp, values]) => ({ timestamp, value: values.reduce((sum, value) => sum + value, 0) / values.length })), (point) => point.timestamp, (point) => point.value);
    return { type: "scatter", mode: "lines+markers", connectgaps: false, name, x: gapAware.x, y: gapAware.y, hovertemplate: "%{x}<br>%{y:.3f}<extra>%{fullData.name}</extra>" };
  });
  const namespaces = Array.from(new Set(rows.map((row) => row.namespace).filter(Boolean)));
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="lg">
    <DialogTitle>{detail?.worker ? `Worker · ${detail.worker}` : "Consumo do worker"}</DialogTitle>
    <DialogContent dividers>
      {loading && <CircularProgress sx={{ display: "block", mx: "auto", my: 5 }} />}
      {error && <Alert severity="warning">{error}</Alert>}
      {detail && !loading && <Stack spacing={2.5}>
        <Alert severity={(detail.metric_window?.observed_days ?? 0) < detail.metric_window.requested_days ? "warning" : "info"}>
          Histórico UTC {detail.metric_window.from_day}–{detail.metric_window.to_day}: {detail.metric_window.observed_days ?? 0} de {detail.metric_window.requested_days} dias com amostras. Dias ausentes permanecem lacunas.
        </Alert>
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
          <Typography variant="overline" color="text.secondary">Nó do cluster</Typography>
          <Typography variant="h6" fontWeight={700}>{detail.worker}</Typography>
          <Typography variant="body2">Cluster {detail.cluster_name || "—"} · Região {detail.region || "—"} · Kubernetes {detail.kubernetes_version || "—"}</Typography>
          <Typography variant="caption" color="text.secondary">Vínculo pelo resource_id do cluster e nome do nó coletado (`computer`).</Typography>
        </Paper>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          <Paper variant="outlined" sx={{ p: 1.75, flex: 1, borderRadius: 2 }}><Typography variant="caption" color="text.secondary">Workloads / namespaces observados</Typography><Typography variant="h4" fontWeight={700}>{namespaces.length}</Typography></Paper>
          <Paper variant="outlined" sx={{ p: 1.75, flex: 1, borderRadius: 2 }}><Typography variant="caption" color="text.secondary">Séries numéricas coletadas</Typography><Typography variant="h4" fontWeight={700}>{traces.length}</Typography></Paper>
          <Paper variant="outlined" sx={{ p: 1.75, flex: 1, borderRadius: 2 }}><Typography variant="caption" color="text.secondary">Snapshots disponíveis</Typography><Typography variant="h4" fontWeight={700}>{new Set(rows.map((row) => row.snapshot_date).filter(Boolean)).size}</Typography></Paper>
        </Stack>
        <Box><Typography variant="subtitle1" fontWeight={700}>Consumo por workload</Typography>
          {traces.length ? <Stack spacing={1.5} sx={{ mt: 1 }}>{traces.map((trace) => <Paper key={trace.name} variant="outlined" sx={{ p: 1.5, borderRadius: 1.5 }}><Typography variant="body2" fontWeight={700}>{trace.name}</Typography><Chart data={[trace]} layout={{ autosize: true, height: 290, margin: { l: 60, r: 20, t: 12, b: 55 }, xaxis: { title: "Snapshot" }, yaxis: { title: "Média coletada", rangemode: "tozero" }, showlegend: false }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} /></Paper>)}</Stack> : <Alert severity="info" sx={{ mt: 1 }}>Há identificação deste nó, mas o Silver não contém métricas numéricas de consumo por workload para o período consultado.</Alert>}
        </Box>
        <Box><Typography variant="subtitle1" fontWeight={700}>Amostras coletadas</Typography><Table size="small"><TableHead><TableRow><TableCell>Data</TableCell><TableCell>Namespace</TableCell><TableCell>Métrica</TableCell><TableCell align="right">Média</TableCell><TableCell align="right">Pods</TableCell><TableCell align="right">Containers</TableCell></TableRow></TableHead><TableBody>{rows.map((row, i) => <TableRow key={`${row.snapshot_date}-${row.namespace}-${row.metric_name}-${i}`}><TableCell>{row.snapshot_date || "—"}</TableCell><TableCell>{row.namespace || "—"}</TableCell><TableCell>{row.metric_name || row.record_kind || "—"}</TableCell><TableCell align="right">{row.metric_average == null ? "—" : Number(row.metric_average).toLocaleString("pt-BR", { maximumFractionDigits: 3 })}</TableCell><TableCell align="right">{row.pods ?? "—"}</TableCell><TableCell align="right">{row.containers ?? "—"}</TableCell></TableRow>)}</TableBody></Table></Box>
      </Stack>}
    </DialogContent>
  </Dialog>;
}

function NamespaceDetailDialog({
  open, loading, error, detail, onClose,
}: {
  open: boolean; loading: boolean; error: string | null;
  detail: FinOpsNamespaceDetail | null; onClose: () => void;
}) {
  const namespaceRows = detail?.series || [];
  const snapshotDates = namespaceRows.map((row) => row.snapshot_date || "").sort();
  const latestSnapshot = snapshotDates[snapshotDates.length - 1] || "";
  const latestRows = namespaceRows.filter((row) => (row.snapshot_date || "") === latestSnapshot);
  const maxOf = (key: "pods" | "containers" | "nodes") => Math.max(0, ...latestRows.map((row) => Number(row[key] || 0)));
  const metricGroups = new Map<string, Map<string, number[]>>();
  for (const row of namespaceRows) {
    if (!row.metric_name || row.metric_average == null || !row.snapshot_date) continue;
    if (!metricGroups.has(row.metric_name)) metricGroups.set(row.metric_name, new Map());
    const dates = metricGroups.get(row.metric_name)!;
    if (!dates.has(row.snapshot_date)) dates.set(row.snapshot_date, []);
    dates.get(row.snapshot_date)!.push(Number(row.metric_average));
  }
  const chartData = Array.from(metricGroups.entries()).map(([metric, dates]) => {
    const points = Array.from(dates.entries()).sort(([a], [b]) => a.localeCompare(b));
    const gapAware = withTimeGaps(points.map(([timestamp, values]) => ({ timestamp, value: values.reduce((sum, value) => sum + value, 0) / values.length })), (point) => point.timestamp, (point) => point.value);
    return {
      type: "scatter", mode: "lines+markers", connectgaps: false, name: metric,
      x: gapAware.x,
      y: gapAware.y,
      hovertemplate: "%{x}<br>%{y:.3f}<extra>%{fullData.name}</extra>",
    };
  });
  const allocationPoints = (detail?.allocations || []).filter((row) => row.snapshot_date && row.allocation_weight != null)
    .sort((a, b) => String(a.snapshot_date).localeCompare(String(b.snapshot_date)));
  const allocationChart = allocationPoints.length ? [{
    type: "scatter", mode: "lines+markers", name: "Capacidade alocada",
    x: allocationPoints.map((row) => row.snapshot_date),
    y: allocationPoints.map((row) => Number(row.allocation_weight) * 100),
  }] : [];
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="lg">
      <DialogTitle>{detail?.namespace ? `Namespace · ${detail.namespace}` : "Dados por namespace"}</DialogTitle>
      <DialogContent dividers>
        {loading && <CircularProgress sx={{ display: "block", mx: "auto", my: 5 }} />}
        {error && <Alert severity="warning">{error}</Alert>}
        {detail && !loading && (
          <Stack spacing={2.5}>
            <Alert severity={(detail.metric_window?.observed_days ?? 0) < detail.metric_window.requested_days ? "warning" : "info"}>
              Histórico UTC {detail.metric_window.from_day}–{detail.metric_window.to_day}: {detail.metric_window.observed_days ?? 0} de {detail.metric_window.requested_days} dias com amostras. Dias ausentes permanecem lacunas.
            </Alert>
            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="overline" color="text.secondary">Contexto do workload</Typography>
              <Typography variant="h6" fontWeight={700}>{detail.namespace}</Typography>
              <Typography variant="body2">Cluster {detail.cluster_name || "—"} · Região {detail.region || "—"} · Kubernetes {detail.kubernetes_version || "—"}</Typography>
              <Typography variant="caption" color="text.secondary">Vínculo pelo resource_id do cluster · {detail.cluster_resource_id}</Typography>
            </Paper>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              {([["Pods", maxOf("pods")], ["Containers", maxOf("containers")], ["Nodes", maxOf("nodes")]] as const).map(([label, value]) => (
                <Paper key={label} variant="outlined" sx={{ p: 1.75, flex: 1, borderRadius: 2 }}>
                  <Typography variant="caption" color="text.secondary">{label} no snapshot mais recente</Typography>
                  <Typography variant="h4" fontWeight={700}>{value || "—"}</Typography>
                </Paper>
              ))}
              <Paper variant="outlined" sx={{ p: 1.75, flex: 1, borderRadius: 2 }}>
                <Typography variant="caption" color="text.secondary">Snapshots disponíveis</Typography>
                <Typography variant="h4" fontWeight={700}>{new Set(namespaceRows.map((row) => row.snapshot_date).filter(Boolean)).size}</Typography>
              </Paper>
            </Stack>
            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Histórico de métricas</Typography>
              {chartData.length ? <Stack spacing={1.5}>{chartData.map((trace) => <Paper key={trace.name} variant="outlined" sx={{ p: 1.5, borderRadius: 1.5 }}><Typography variant="body2" fontWeight={700}>{trace.name}</Typography><Chart data={[trace]} layout={{ autosize: true, height: 290, margin: { l: 60, r: 20, t: 12, b: 55 }, xaxis: { title: "Snapshot" }, yaxis: { title: "Média coletada", rangemode: "tozero" }, showlegend: false }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} /></Paper>)}</Stack> : <Alert severity="info">O Silver não tem série numérica de consumo para este namespace. Se houver alocação, o gráfico de capacidade reservada aparece abaixo, identificado separadamente.</Alert>}
              <Typography variant="caption" color="text.secondary">Cada métrica tem gráfico e escala próprios. Cada ponto é a média informada no snapshot; a resolução temporal depende da frequência de ingestão do Silver.</Typography>
            </Box>
            {allocationChart.length > 0 && <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 1.5 }}>
              <Typography variant="subtitle1" fontWeight={700}>Capacidade reservada atribuída</Typography>
              <Chart data={allocationChart} layout={{ autosize: true, height: 290, margin: { l: 60, r: 20, t: 12, b: 55 }, xaxis: { title: "Snapshot" }, yaxis: { title: "Parcela da capacidade (%)", rangemode: "tozero" }, showlegend: false }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} />
              <Typography variant="caption" color="text.secondary">O percentual usa a base de alocação coletada (por exemplo, CPU requisitada); representa capacidade reservada, não consumo medido do namespace.</Typography>
            </Paper>}
            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Amostras do namespace</Typography>
              <Table size="small"><TableHead><TableRow><TableCell>Data</TableCell><TableCell>Métrica</TableCell><TableCell align="right">Média</TableCell><TableCell>Worker</TableCell><TableCell align="right">Amostras</TableCell></TableRow></TableHead>
                <TableBody>{namespaceRows.map((row, index) => <TableRow key={`${row.snapshot_date}-${row.metric_name}-${row.computer}-${index}`}>
                  <TableCell>{row.snapshot_date || "—"}</TableCell><TableCell>{row.metric_name || row.record_kind || "—"}</TableCell><TableCell align="right">{row.metric_average == null ? "—" : Number(row.metric_average).toLocaleString("pt-BR", { maximumFractionDigits: 3 })}</TableCell><TableCell>{row.computer || "—"}</TableCell><TableCell align="right">{row.samples?.toLocaleString("pt-BR") || "—"}</TableCell>
                </TableRow>)}</TableBody>
              </Table>
            </Box>
          </Stack>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function FinOpsActionDialog({
  open, resourceId, onClose,
}: { open: boolean; resourceId: string | null; onClose: () => void }) {
  const [detail, setDetail] = useState<FinOpsActionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSku, setSelectedSku] = useState("");
  const [jevBusy, setJevBusy] = useState(false);
  const [confirmJev, setConfirmJev] = useState(false);
  const [jevError, setJevError] = useState<string | null>(null);
  const [jevResult, setJevResult] = useState<Awaited<ReturnType<typeof getFinopsJevReview>> | null>(null);
  const [namespaceOpen, setNamespaceOpen] = useState(false);
  const [namespaceLoading, setNamespaceLoading] = useState(false);
  const [namespaceError, setNamespaceError] = useState<string | null>(null);
  const [namespaceDetail, setNamespaceDetail] = useState<FinOpsNamespaceDetail | null>(null);
  const [workerOpen, setWorkerOpen] = useState(false);
  const [workerLoading, setWorkerLoading] = useState(false);
  const [workerError, setWorkerError] = useState<string | null>(null);
  const [workerDetail, setWorkerDetail] = useState<FinOpsWorkerDetail | null>(null);
  const [poolOpen, setPoolOpen] = useState(false);

  useEffect(() => {
    if (!open || !resourceId) return;
    let active = true;
    setLoading(true); setError(null); setDetail(null); setJevResult(null); setJevError(null); setConfirmJev(false);
    getFinopsActionDetail(resourceId)
      .then((data) => { if (active) { setDetail(data); setSelectedSku(data.action.sku_to || data.action.sku_from || ""); } })
      .catch((e) => { if (active) setError(e?.response?.data?.detail || "Não foi possível carregar os dados do recurso."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, resourceId]);

  const selected = useMemo(() => {
    if (!detail) return null;
    return detail.family_options.find((option) => option.sku === selectedSku) ?? null;
  }, [detail, selectedSku]);
  const action = detail?.action;
  const targetVcpus = selected?.vcpus ?? action?.vcpus_to ?? action?.vcpus_from ?? 0;
  const targetMemory = selected?.memory_gb ?? action?.memory_gb_to ?? action?.memory_gb_from ?? 0;
  const targetSku = selected?.sku ?? action?.sku_to ?? action?.sku_from ?? "";
  const projectedCost = selected ? selected.cost_projected : action?.cost_to_estimated;
  const saving = selected ? selected.saving : action?.saving_monthly;
  const scenarioRows = useMemo(() => {
    if (!detail) return [];
    const deterministicSku = detail.action.sku_to;
    const rows: Array<{
      sku: string; vendor: string; vcpus: number | null; memory_gb: number | null;
      cost_projected: number | null; saving: number | null; is_deterministic_target: boolean;
    }> = deterministicSku ? [{
      sku: deterministicSku, vendor: detail.action.vendor_to || "Determinístico",
      vcpus: detail.action.vcpus_to, memory_gb: detail.action.memory_gb_to,
      cost_projected: detail.action.cost_to_estimated, saving: detail.action.saving_monthly,
      is_deterministic_target: true,
    }] : [];
    const existing = new Set(rows.map((row) => row.sku));
    for (const option of detail.family_options) {
      if (existing.has(option.sku)) continue;
      existing.add(option.sku);
      rows.push({ ...option, is_deterministic_target: false });
    }
    return rows;
  }, [detail]);
  const cpuRows = (detail?.series ?? []).filter((row) => row.metric === "Percentage CPU");
  const cpuObservedLine = withTimeGaps(cpuRows, (row) => row.bucket_start_utc, (row) => row.value);
  const cpuProjectedLine = withTimeGaps(cpuRows, (row) => row.bucket_start_utc, (row) => targetVcpus > 0 && action?.vcpus_from
    ? Math.min(100, row.value * action.vcpus_from / targetVcpus) : row.value);
  const cpuChart = cpuRows.length ? [{
    type: "scatter", mode: "lines", connectgaps: false, name: "CPU observada",
    x: cpuObservedLine.x, y: cpuObservedLine.y,
    line: { color: "#2563eb", width: 1.5 },
  }, {
    type: "scatter", mode: "lines", connectgaps: false, name: `Estimativa em ${targetSku}`,
    x: cpuProjectedLine.x, y: cpuProjectedLine.y,
    line: { color: "#10b981", width: 2, dash: "dash" },
  }] : [];
  const availableMemory = detail?.profiles.find((profile) => profile.metric === "Available Memory Bytes");
  const memoryUsed = action?.memory_gb_from != null && availableMemory?.value_min != null
    ? Math.max(0, action.memory_gb_from - Number(availableMemory.value_min) / 1024 ** 3) : null;

  const consultarJev = async () => {
    if (!resourceId) return;
    setJevBusy(true); setJevResult(null); setJevError(null);
    try { setJevResult(await getFinopsJevReview(resourceId)); }
    catch (e: any) { setJevError(e?.response?.data?.detail || "Falha ao consultar o Jev."); }
    finally { setJevBusy(false); }
  };

  const abrirNamespace = async (namespace: string) => {
    if (!resourceId) return;
    setNamespaceOpen(true); setNamespaceLoading(true); setNamespaceError(null); setNamespaceDetail(null);
    try { setNamespaceDetail(await getFinopsNamespaceDetail(resourceId, namespace)); }
    catch (e: any) { setNamespaceError(e?.response?.data?.detail || "Não foi possível carregar os dados desse namespace."); }
    finally { setNamespaceLoading(false); }
  };

  const abrirWorker = async (worker: string) => {
    if (!resourceId) return;
    setWorkerOpen(true); setWorkerLoading(true); setWorkerError(null); setWorkerDetail(null);
    try { setWorkerDetail(await getFinopsWorkerDetail(resourceId, worker)); }
    catch (e: any) { setWorkerError(e?.response?.data?.detail || "Não foi possível carregar o consumo deste worker."); }
    finally { setWorkerLoading(false); }
  };

  const jevAnswers = jevResult?.answers as any;
  const recommendation = jevAnswers?.answers?.recommendation ?? jevAnswers?.recommendation;
  const recommendationChoice = recommendation?.choice;
  const probabilities = recommendation?.probabilities as Record<string, number> | undefined;

  return (
    <>
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="lg">
      <DialogTitle>Detalhe do recurso · {action?.resource_name || resourceId || ""}</DialogTitle>
      <DialogContent dividers>
        {loading && <CircularProgress sx={{ display: "block", mx: "auto", my: 5 }} />}
        {error && <Alert severity="error">{error}</Alert>}
        {detail && action && (
          <Stack spacing={2.5}>
            <Alert severity={detail.recommendation_profile_status === "STALE" || (detail.metric_window.observed_days ?? 0) < detail.metric_window.requested_days ? "warning" : "info"}>
              Série 4P UTC {detail.metric_window.from_day}–{detail.metric_window.to_day}: {detail.metric_window.observed_days ?? 0} de {detail.metric_window.requested_days} dias com amostras; lacunas não são preenchidas com zero. {detail.recommendation_profile_status === "STALE" ? "A recomendação ainda não foi recalculada após a observação mais recente." : "O gráfico é calculado diretamente da série diária."}
            </Alert>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Chip label={`Região ${action.region_id || "—"}`} />
              <Chip label={`P95 CPU ${action.cpu_p95 == null ? "—" : `${Number(action.cpu_p95).toFixed(1)}%`}`} />
              <Chip label={`${action.observed_days} dias observados`} />
              <Chip label={`Veredito ${action.verdict}`} color={action.verdict === "VALID" ? "success" : "warning"} />
              <Chip label={`Revisão ${action.status_revisao}`} />
              {action.family_change && <Chip color="info" label={`Mudança de família: ${action.vendor_from} → ${action.vendor_to}`} />}
            </Stack>

            {detail.aks_topology && <AksTopologyPanel
              topology={detail.aks_topology} action={action} targetSku={targetSku} targetCost={projectedCost} targetSaving={saving}
              onOpenPool={() => setPoolOpen(true)}
              onOpenNamespace={(namespace) => void abrirNamespace(namespace)}
              onOpenWorker={(worker) => void abrirWorker(worker)}
              onShowNamespaceStatus={(reason) => { setNamespaceOpen(true); setNamespaceLoading(false); setNamespaceDetail(null); setNamespaceError(reason); }}
            />}

            <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
              <Box sx={{ flex: 1, p: 2, bgcolor: "action.hover", borderRadius: 1 }}>
                <Typography variant="caption" color="text.secondary">Configuração atual</Typography>
                <Typography variant="h6">{action.sku_from}</Typography>
                <Typography>{action.vcpus_from ?? "—"} vCPU · {action.memory_gb_from ?? "—"} GB</Typography>
                <Typography>Custo observado: {brl(action.cost_from_current)}</Typography>
              </Box>
              <Box sx={{ flex: 1, p: 2, bgcolor: "success.50", borderRadius: 1 }}>
                <Typography variant="caption" color="text.secondary">Cenário selecionado</Typography>
                <FormControl fullWidth size="small" sx={{ mt: 1 }}>
                  <InputLabel id="sku-target-label">SKU para comparar</InputLabel>
                  <Select labelId="sku-target-label" label="SKU para comparar" value={selectedSku} onChange={(event) => { setSelectedSku(event.target.value); setJevResult(null); setJevError(null); }}>
                    <MenuItem value={action.sku_to}>{action.sku_to} · {action.family_change ? `${action.vendor_from} → ${action.vendor_to}` : "mesma família"}{action.cost_to_estimated != null ? ` · custo ${brl(action.cost_to_estimated)} · saving ${brl(action.saving_monthly)}` : " · sem custo estimado"}</MenuItem>
                    {detail.family_options.filter((option) => option.sku !== action.sku_to).map((option) => (
                      <MenuItem key={option.sku} value={option.sku}>{optionName(option)} · {option.vcpus} vCPU · {option.memory_gb} GB{option.cost_projected != null ? ` · custo ${brl(option.cost_projected)} · saving ${brl(option.saving)}${option.price_comparable ? " · tarifa comparável" : " · projeção proporcional"}` : " · sem valor estimado"}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Typography sx={{ mt: 1 }}>{targetVcpus || "—"} vCPU · {targetMemory || "—"} GB</Typography>
                <Typography>Custo estimado na janela: {brl(projectedCost)}</Typography>
                <Typography color={saving != null && saving > 0 ? "success.main" : "text.secondary"}>
                  Economia estimada: {brl(saving)}
                </Typography>
              </Box>
            </Stack>

            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Compare os cenários disponíveis</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                {scenarioRows.length} cenário(s) para comparação. O valor projetado usa tarifa comparável ou, quando explicitado, a proporção da capacidade sobre o custo observado; selecione uma linha para atualizar os gráficos e o De/Para.
              </Typography>
              <Table size="small">
                <TableHead><TableRow><TableCell>Opção</TableCell><TableCell>De → Para</TableCell><TableCell align="right">vCPU · RAM</TableCell><TableCell align="right">Valor projetado</TableCell><TableCell align="right">Saving estimado</TableCell><TableCell /></TableRow></TableHead>
                <TableBody>
                  {scenarioRows.map((row) => (
                    <TableRow key={row.sku} hover selected={selectedSku === row.sku} sx={{ cursor: "pointer" }} onClick={() => { setSelectedSku(row.sku); setJevResult(null); setJevError(null); }}>
                      <TableCell>
                        <Typography variant="body2" fontWeight={700}>{row.vendor} · {row.sku}</Typography>
                        <Typography variant="caption" color="text.secondary">{row.is_deterministic_target ? "Veredito determinístico" : "Alternativa de catálogo"}{!row.is_deterministic_target && !detail.family_options.find((option) => option.sku === row.sku)?.price_comparable ? " · proporção da capacidade" : ""}</Typography>
                      </TableCell>
                      <TableCell>{action.sku_from} → {row.sku}</TableCell>
                      <TableCell align="right">{row.vcpus ?? "—"} vCPU · {row.memory_gb ?? "—"} GB</TableCell>
                      <TableCell align="right">{brl(row.cost_projected)}</TableCell>
                      <TableCell align="right" sx={{ color: row.saving != null && row.saving > 0 ? "success.main" : "text.secondary", fontWeight: 700 }}>{brl(row.saving)}</TableCell>
                      <TableCell>{selectedSku === row.sku ? <Chip size="small" color="primary" label="Selecionado" /> : <Button size="small" onClick={(event) => { event.stopPropagation(); setSelectedSku(row.sku); setJevResult(null); setJevError(null); }}>Ver cenário</Button>}</TableCell>
                    </TableRow>
                  ))}
                  {!scenarioRows.length && <TableRow><TableCell colSpan={6}>Não há alternativa com referência de preço compatível para este recurso.</TableCell></TableRow>}
                </TableBody>
              </Table>
            </Box>

            <Alert severity="info">{detail.projection_note} {selected && selected.cost_projected == null ? "Não há tarifa nem base proporcional suficiente para estimar o custo desta alternativa." : selected && !selected.price_comparable ? "Esta alternativa não tem tarifa comparável; o valor é uma projeção proporcional à capacidade e ao custo observado." : ""}</Alert>

            <Box>
              <Typography variant="subtitle1" fontWeight={700}>CPU observada e estimativa após redimensionamento</Typography>
              {cpuChart.length ? (
                <Chart data={cpuChart} layout={{ autosize: true, height: 310, margin: { l: 48, r: 20, t: 20, b: 55 }, yaxis: { title: "CPU (%)", rangemode: "tozero" }, xaxis: { title: "Horário UTC" }, legend: { orientation: "h", y: -0.24 } }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} />
              ) : <Alert severity="warning">A série temporal de CPU não está disponível para este recurso. O resumo por percentis abaixo ainda pode existir.</Alert>}
              <Typography variant="caption" color="text.secondary">A curva verde mantém a carga observada e ajusta a utilização pela razão entre vCPU atual e destino; ela não prevê mudanças de desempenho entre Intel e AMD. O marcador de RAM estima o uso a partir da memória livre mínima observada na janela, não é um percentil de uso.</Typography>
            </Box>

            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Capacidade: atual versus cenário</Typography>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Chart data={[
                    { type: "bar", name: "Atual", x: ["Atual", "Selecionado"], y: [action.vcpus_from ?? 0, targetVcpus], marker: { color: "#2563eb" } },
                  ]} layout={{ autosize: true, height: 250, margin: { l: 50, r: 10, t: 20, b: 40 }, yaxis: { title: "vCPU", rangemode: "tozero" }, showlegend: false }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} />
                </Box>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Chart data={[
                    { type: "bar", name: "Capacidade RAM", x: ["Atual", "Selecionado"], y: [action.memory_gb_from ?? 0, targetMemory], marker: { color: "#10b981" } },
                    ...(memoryUsed == null ? [] : [{ type: "scatter", mode: "markers", name: "RAM usada estimada (mínimo espaço livre)", x: ["Atual", "Selecionado"], y: [memoryUsed, memoryUsed], marker: { color: "#f59e0b", size: 12, symbol: "diamond" } }]),
                  ]} layout={{ autosize: true, height: 250, barmode: "group", margin: { l: 50, r: 10, t: 20, b: 40 }, yaxis: { title: "RAM (GB)", rangemode: "tozero" }, legend: { orientation: "h", y: -0.22 } }} useResizeHandler style={{ width: "100%" }} config={{ displayModeBar: false, responsive: true }} />
                </Box>
              </Stack>
            </Box>

            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Série coletada — perfil e cobertura</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                P50 é a mediana; P95 significa que 95% das amostras ficaram até esse valor; P99 mostra a cauda alta. Cobertura é a fração da janela com dado válido, e amostras é o número de pontos usados.
              </Typography>
              {detail.profiles.length ? (
                <Table size="small">
                  <TableHead><TableRow><TableCell>Métrica / unidade</TableCell><TableCell align="right">Mínimo</TableCell><TableCell align="right">P50 · mediana</TableCell><TableCell align="right">P95</TableCell><TableCell align="right">P99</TableCell><TableCell align="right">Máximo</TableCell><TableCell align="right">Amostras</TableCell><TableCell align="right">Cobertura da janela</TableCell></TableRow></TableHead>
                  <TableBody>{detail.profiles.map((profile) => <TableRow key={profile.metric}><TableCell><Typography variant="body2">{metricNames[profile.metric] || profile.metric}</Typography><Typography variant="caption" color="text.secondary">{profile.metric} · {profile.metric_unit || "unidade não informada"}</Typography></TableCell><TableCell align="right">{profileValue(profile.value_min, profile.metric, profile.metric_unit)}</TableCell><TableCell align="right">{profileValue(profile.p50, profile.metric, profile.metric_unit)}</TableCell><TableCell align="right" sx={{ fontWeight: 700 }}>{profileValue(profile.p95, profile.metric, profile.metric_unit)}</TableCell><TableCell align="right">{profileValue(profile.p99, profile.metric, profile.metric_unit)}</TableCell><TableCell align="right">{profileValue(profile.value_max, profile.metric, profile.metric_unit)}</TableCell><TableCell align="right">{profile.samples?.toLocaleString("pt-BR") ?? "—"}<Typography variant="caption" display="block" color="text.secondary">{profile.observed_days ?? "—"} dias observados</Typography></TableCell><TableCell align="right">{profile.coverage == null ? "—" : `${(Number(profile.coverage) * 100).toFixed(0)}%`}</TableCell></TableRow>)}</TableBody>
                </Table>
              ) : <Alert severity="warning">Não há perfil agregado coletado para este recurso.</Alert>}
            </Box>

            <Divider />
            <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap">
              <Box sx={{ flex: 1, minWidth: 250 }}>
                <Typography variant="subtitle1" fontWeight={700}>Parecer assistivo do Jev</Typography>
                <Typography variant="caption" color="text.secondary">O Jev compara somente opções elegíveis e não aplica alterações nem modifica o veredito determinístico.</Typography>
              </Box>
              <Button variant="contained" onClick={() => setConfirmJev(true)} disabled={jevBusy || (!action.sku_to && detail.family_options.length === 0)}>
                {jevBusy ? "Consultando Jev…" : "Pedir parecer ao Jev"}
              </Button>
            </Stack>
            {confirmJev && (
              <Alert
                severity="warning"
                action={(
                  <Stack direction="row" spacing={1}>
                    <Button size="small" color="inherit" onClick={() => setConfirmJev(false)}>Cancelar</Button>
                    <Button size="small" variant="contained" onClick={() => { setConfirmJev(false); void consultarJev(); }}>Enviar e consultar</Button>
                  </Stack>
                )}
              >
                Ao confirmar, o sistema enviará ao Jev região, SKUs atual/proposto, CPU e memória p95, dias observados, custo comparável e alternativas de catálogo. O identificador e o nome do recurso não são enviados. O parecer e seu hash serão registrados na auditoria; nenhuma VM será alterada. O Jev comparará manter o SKU atual, a ação determinística e as alternativas do catálogo.
              </Alert>
            )}
            {jevBusy && <CircularProgress size={24} />}
            {jevError && <Alert severity="error">{jevError}</Alert>}
            {jevResult && (
              jevResult.status === "OK" ? (
                <Paper variant="outlined" sx={{ overflow: "hidden", borderRadius: 2, borderColor: "primary.light" }}>
                  <Box sx={{ px: 2.5, py: 1.5, bgcolor: "primary.50", borderBottom: "1px solid", borderColor: "divider" }}>
                    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" spacing={1}>
                      <Box><Typography variant="overline" color="primary.main">Jev · parecer assistivo</Typography><Typography variant="h6" fontWeight={700}>Decisão para revisão humana</Typography></Box>
                      <Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color="success" label={`Veredito técnico: ${jevResult.deterministic_verdict}`} /><Chip size="small" variant="outlined" label={jevResult.model || "Modelo Jev"} /></Stack>
                    </Stack>
                  </Box>
                  <Stack spacing={2} sx={{ p: 2.5 }}>
                    <Box sx={{ p: 2, borderRadius: 1.5, bgcolor: "action.hover", borderLeft: "4px solid", borderColor: "primary.main" }}>
                      <Typography variant="caption" color="text.secondary" fontWeight={700}>PERGUNTA ENVIADA AO JEV</Typography>
                      <Typography variant="body1" sx={{ mt: 0.5 }}>Entre manter o SKU atual, seguir a recomendação determinística ou avaliar uma alternativa de catálogo, qual opção deve ser priorizada para revisão humana?</Typography>
                    </Box>
                    <Box sx={{ p: 2.25, borderRadius: 1.5, background: "linear-gradient(120deg, rgba(25,118,210,0.10), rgba(46,125,50,0.08))", border: "1px solid", borderColor: "primary.light" }}>
                      <Typography variant="caption" color="text.secondary" fontWeight={700}>RESPOSTA DO JEV</Typography>
                      <Typography variant="h5" fontWeight={800} sx={{ mt: 0.5, color: "primary.dark" }}>{jevResult.choices?.[recommendationChoice] || recommendationChoice || "Resposta recebida sem opção reconhecida"}</Typography>
                      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: 1.25 }}>
                        {recommendation?.confidence != null && <Chip color="primary" label={`Confiança do parecer ${(Number(recommendation.confidence) * 100).toFixed(0)}%`} />}
                        <Chip variant="outlined" label="Nenhuma alteração aplicada" />
                        <Chip variant="outlined" label="Veredito determinístico preservado" />
                      </Stack>
                    </Box>
                    {probabilities && <Box>
                      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>Como o Jev distribuiu as opções</Typography>
                      <Stack spacing={1}>
                        {Object.entries(probabilities).sort((a, b) => b[1] - a[1]).map(([choice, probability]) => {
                          const percent = Math.max(0, Math.min(100, Number(probability) * 100));
                          const chosen = choice === recommendationChoice;
                          return <Paper key={choice} variant="outlined" sx={{ p: 1.5, borderRadius: 1.5, borderColor: chosen ? "primary.main" : "divider", bgcolor: chosen ? "primary.50" : "background.paper" }}>
                            <Stack direction="row" justifyContent="space-between" spacing={2} sx={{ mb: 0.75 }}>
                              <Typography variant="body2" fontWeight={chosen ? 700 : 500}>{jevResult.choices?.[choice] || choice}{chosen ? " · recomendada" : ""}</Typography>
                              <Typography fontWeight={800}>{percent.toFixed(0)}%</Typography>
                            </Stack>
                            <LinearProgress variant="determinate" value={percent} color={chosen ? "primary" : "inherit"} sx={{ height: 8, borderRadius: 5 }} />
                          </Paper>;
                        })}
                      </Stack>
                    </Box>}
                    <Typography variant="caption" color="text.secondary">Este parecer ajuda a priorizar a revisão. A validade, o preço comparável e a decisão operacional continuam sujeitos às validações exibidas acima.</Typography>
                  </Stack>
                </Paper>
              ) : <Alert severity="warning">Jev indisponível: {jevResult.reason || "sem motivo informado"}</Alert>
            )}

            <Typography variant="caption" color="text.secondary">{detail.family_note}</Typography>
            {action.veto_reason && <Alert severity="warning">Validação determinística: {action.veto_reason}</Alert>}
          </Stack>
        )}
      </DialogContent>
    </Dialog>
    <NamespaceDetailDialog open={namespaceOpen} loading={namespaceLoading} error={namespaceError} detail={namespaceDetail} onClose={() => setNamespaceOpen(false)} />
    <WorkerDetailDialog open={workerOpen} loading={workerLoading} error={workerError} detail={workerDetail} onClose={() => setWorkerOpen(false)} />
    <PoolMonitoringDialog open={poolOpen} detail={detail} onClose={() => setPoolOpen(false)} onOpenWorker={(worker) => void abrirWorker(worker)} />
    </>
  );
}
