/**
 * Página de oportunidades de infraestrutura — rightsizing de VMs OCI Flex.
 *
 * Mesma organização do portal de anomalias: impacto financeiro e indicadores
 * clicáveis, grade de oportunidades (recurso, de/para, saving) e painel lateral
 * com evidência, decisão e histórico. O motor lê somente a Silver de métricas,
 * a capacidade OCI e o preço efetivo do billing; não altera a nuvem.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Alert, Box, Button, Chip, CircularProgress, FormControl, InputLabel, MenuItem, Paper, Select, Stack,
  Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Typography,
} from "@mui/material";
import { type GridColDef } from "@mui/x-data-grid";
import RefreshIcon from "@mui/icons-material/RefreshOutlined";
import PlayArrowIcon from "@mui/icons-material/PlayArrowOutlined";
import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { getConnections, type SourceConnection } from "../../services/api";
import type { Opportunity, OpportunityRun, OpportunitySummary } from "../../services/infraOpportunitiesApi";
import {
  getOpportunitySummary, listOpportunities, listOpportunityRuns, runOpportunityEngine,
} from "../../services/infraOpportunitiesApi";
import OpportunityDrawer from "./OpportunityDrawer";
import {
  ACTION_COLOR, ACTION_LABEL, CONFIDENCE_COLOR, CONFIDENCE_LABEL, READINESS_LABEL, STATE_LABEL, money, pct, sizeLabel,
} from "./opportunityLabels";

const TABS = ["Visão geral", "Oportunidades", "Execuções do motor"];

export default function InfrastructureOpportunities() {
  // A aba inicial pode vir do menu (view_params); a troca de aba é local.
  const [searchParams] = useSearchParams();
  const requestedTab = Number(searchParams.get("tab"));
  const [tab, setTab] = useState(
    Number.isInteger(requestedTab) && requestedTab >= 0 && requestedTab < TABS.length ? requestedTab : 0,
  );

  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [days, setDays] = useState(7);
  const [resolution, setResolution] = useState(300);
  const [summary, setSummary] = useState<OpportunitySummary | null>(null);
  const [items, setItems] = useState<Opportunity[]>([]);
  const [runs, setRuns] = useState<OpportunityRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState<string>("");
  const [stateFilter, setStateFilter] = useState<string>("");
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    getConnections().then((list) => {
      const oci = list.filter((item) => item.connector_type === "oci");
      setConnections(oci);
      const enabled = oci.find((item) => (item.config_json?.infrastructure_metrics as { enabled?: boolean } | undefined)?.enabled);
      setConnectionId((current) => current ?? (enabled ?? oci[0])?.id ?? null);
    }).catch(() => setError("Não foi possível carregar as conexões OCI."));
  }, []);

  const load = useCallback(async () => {
    if (connectionId == null) return;
    setBusy(true);
    setError(null);
    try {
      const [summaryData, list, runList] = await Promise.all([
        getOpportunitySummary(connectionId), listOpportunities(connectionId), listOpportunityRuns(connectionId),
      ]);
      setSummary(summaryData);
      setItems(list.items);
      setRuns(runList.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível carregar as oportunidades");
    } finally {
      setBusy(false);
    }
  }, [connectionId]);

  useEffect(() => { void load(); }, [load]);

  const runNow = async () => {
    if (connectionId == null) return;
    setRunning(true);
    setError(null);
    setMessage(null);
    try {
      const run = await runOpportunityEngine(connectionId, days, resolution);
      if (run.status === "FAILED") setError(`A análise falhou: ${run.error_message ?? "erro desconhecido"}`);
      else setMessage(`Análise #${run.id} concluída: ${run.resources_analyzed} recurso(s), ${run.opportunities_count} oportunidade(s), ${money(run.total_monthly_saving, run.currency ?? "BRL")}/mês.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível executar o motor");
    } finally {
      setRunning(false);
    }
  };

  const currency = summary?.currency ?? "BRL";
  const visibleItems = useMemo(() => items.filter((item) =>
    (!actionFilter || item.action_type === actionFilter) && (!stateFilter || item.state === stateFilter)), [items, actionFilter, stateFilter]);
  const openList = (filters: { action?: string; state?: string }) => {
    setActionFilter(filters.action ?? "");
    setStateFilter(filters.state ?? "");
    setTab(1);
  };

  const columns: GridColDef<Opportunity>[] = [
    { field: "action_type", headerName: "Ação", width: 150, renderCell: ({ row }) => <Chip size="small" color={ACTION_COLOR[row.action_type]} label={ACTION_LABEL[row.action_type]} /> },
    { field: "resource_name", headerName: "Recurso", flex: 1, minWidth: 180 },
    { field: "shape", headerName: "Shape", width: 170, valueGetter: (_, row) => row.from_config?.shape ?? "—" },
    { field: "from_config", headerName: "De", width: 140, valueGetter: (_, row) => sizeLabel(row.from_config) },
    { field: "to_config", headerName: "Para", width: 140, valueGetter: (_, row) => sizeLabel(row.to_config) },
    { field: "cpu_p95", headerName: "CPU P95", width: 90, valueFormatter: (value) => pct(value as number | null) },
    { field: "memory_p95", headerName: "Mem. P95", width: 95, valueFormatter: (value) => pct(value as number | null) },
    { field: "current_monthly_cost", headerName: "Custo / mês", width: 120, valueFormatter: (value) => money(value as number | null, currency) },
    {
      field: "monthly_saving", headerName: "Saving / mês", width: 130,
      renderCell: ({ row }) => <Typography variant="body2" fontWeight={700} color={(row.monthly_saving ?? 0) > 0 ? "success.main" : (row.monthly_saving ?? 0) < 0 ? "error.main" : undefined}>{money(row.monthly_saving, currency)}</Typography>,
    },
    { field: "confidence", headerName: "Confiança", width: 100, renderCell: ({ row }) => <Chip size="small" variant="outlined" color={CONFIDENCE_COLOR[row.confidence]} label={CONFIDENCE_LABEL[row.confidence]} /> },
    { field: "readiness_status", headerName: "Prontidão", width: 170, valueFormatter: (value) => READINESS_LABEL[String(value)] ?? String(value) },
    { field: "state", headerName: "Estado", width: 110, valueFormatter: (value) => STATE_LABEL[value as Opportunity["state"]] ?? String(value) },
  ];

  const run = summary?.run;
  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, maxWidth: 1600, mx: "auto", width: "100%", minWidth: 0, boxSizing: "border-box" }}>
      <Box sx={{ display: "flex", alignItems: { xs: "stretch", md: "center" }, justifyContent: "space-between", gap: 2, mb: 2, flexWrap: "wrap" }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="h5">Oportunidades de infraestrutura</Typography>
          {(busy || running) && <CircularProgress size={18} />}
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel>Conexão OCI</InputLabel>
            <Select label="Conexão OCI" value={connectionId ?? ""} onChange={(event) => setConnectionId(Number(event.target.value))}>
              {connections.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Janela</InputLabel>
            <Select label="Janela" value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {[7, 14, 30, 60].map((value) => <MenuItem key={value} value={value}>{value} dias</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Base do motor</InputLabel>
            <Select label="Base do motor" value={resolution} onChange={(event) => setResolution(Number(event.target.value))}>
              <MenuItem value={300}>Buckets de 5 min</MenuItem>
              <MenuItem value={60}>Buckets de 1 min</MenuItem>
            </Select>
          </FormControl>
          <Button size="small" startIcon={<RefreshIcon />} onClick={() => void load()}>Atualizar</Button>
          <Button size="small" variant="contained" startIcon={<PlayArrowIcon />} disabled={connectionId == null || running} onClick={() => void runNow()}>
            {running ? "Analisando…" : "Executar análise"}
          </Button>
        </Stack>
      </Box>

      <Tabs value={tab} onChange={(_, next) => setTab(next)} sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}>
        {TABS.map((label) => <Tab key={label} label={label} />)}
      </Tabs>

      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError(null)}>{dataDisplayText(error)}</Alert>}
      {message && <Alert severity="success" sx={{ mb: 1 }} onClose={() => setMessage(null)}>{message}</Alert>}
      {!busy && summary && !run && <Alert severity="info" sx={{ mb: 2 }}>Nenhuma análise concluída para esta conexão. Clique em “Executar análise” para rodar o motor sobre a Silver de métricas.</Alert>}

      {tab === 0 && run && summary && <Box>
        <Paper variant="outlined" sx={{ p: 2, mb: 2, borderColor: "success.light", background: "linear-gradient(110deg,#f5fbf8,#fff)" }}>
          <Typography variant="subtitle2">Impacto financeiro das oportunidades</Typography>
          <Typography variant="caption" color="text.secondary">
            Análise #{run.id} · janela {run.window_start_utc.slice(0, 10)} a {run.window_end_utc.slice(0, 10)} (UTC) · base de {run.resolution_seconds / 60} min · saving projetado com o preço efetivo do billing de cada recurso, não economia realizada auditada.
          </Typography>
          <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
            {[
              { label: "Economia projetada / mês", value: money(summary.projected_saving_monthly, currency), note: "Reduções prontas, não descartadas", go: { action: "RIGHTSIZE_DOWN" } },
              { label: "Economia projetada / ano", value: money(summary.projected_saving_annual, currency), note: "12 × a projeção mensal", go: { action: "RIGHTSIZE_DOWN" } },
              { label: "Saving médio por oportunidade", value: money(summary.avg_saving_per_opportunity, currency), note: "Média mensal da última execução", go: { action: "RIGHTSIZE_DOWN" } },
              { label: "Saving médio por execução", value: money(summary.avg_saving_per_run, currency), note: `Média de ${summary.completed_runs} execução(ões) concluída(s)`, go: null },
              { label: "Já executado / mês", value: money(summary.implemented_saving_monthly, currency), note: "Oportunidades marcadas como executadas", go: { state: "IMPLEMENTED" } },
              { label: "Custo analisado / mês", value: money(summary.analyzed_monthly_cost, currency), note: summary.saving_ratio != null ? `${(summary.saving_ratio * 100).toFixed(0)}% de redução possível` : "", go: {} },
            ].map((item) => <Paper key={item.label} variant="outlined"
                                    onClick={() => (item.go ? openList(item.go) : setTab(2))}
                                    sx={{ p: 1.25, minWidth: 180, flex: "1 1 180px", cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" } }}>
              <Typography variant="caption" color="text.secondary">{item.label}</Typography>
              <Typography variant="h6">{item.value}</Typography>
              <Typography variant="caption" color="text.secondary">{item.note}</Typography>
            </Paper>)}
          </Stack>
        </Paper>

        <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
          {[
            { label: "Recursos analisados", value: summary.resources_analyzed ?? 0, go: {} },
            { label: "Oportunidades", value: summary.opportunities ?? 0, go: { action: "RIGHTSIZE_DOWN" } },
            { label: "Para aumentar", value: summary.by_action?.RIGHTSIZE_UP ?? 0, go: { action: "RIGHTSIZE_UP" } },
            { label: "Bem dimensionados", value: summary.by_action?.KEEP ?? 0, go: { action: "KEEP" } },
            { label: "Novas", value: summary.by_state?.NEW ?? 0, go: { state: "NEW" } },
            { label: "Aceitas", value: summary.by_state?.ACCEPTED ?? 0, go: { state: "ACCEPTED" } },
            { label: "Executadas", value: summary.by_state?.IMPLEMENTED ?? 0, go: { state: "IMPLEMENTED" } },
            { label: "Descartadas", value: summary.by_state?.DISMISSED ?? 0, go: { state: "DISMISSED" } },
          ].map((item) => <Paper key={item.label} variant="outlined" onClick={() => openList(item.go)}
                                  sx={{ p: 1.5, minWidth: 150, cursor: "pointer", transition: "all .15s", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover", transform: "translateY(-1px)" } }}>
            <Typography variant="caption" color="text.secondary">{item.label}</Typography>
            <Typography variant="h6">{item.value}</Typography>
          </Paper>)}
        </Stack>

        <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Maiores oportunidades</Typography>
          <Table size="small">
            <TableHead><TableRow>
              <TableCell>Recurso</TableCell><TableCell>Ação</TableCell><TableCell>De</TableCell><TableCell>Para</TableCell>
              <TableCell align="right">CPU P95</TableCell><TableCell align="right">Mem. P95</TableCell><TableCell align="right">Saving / mês</TableCell>
            </TableRow></TableHead>
            <TableBody>
              {items.filter((item) => item.action_type !== "KEEP").slice(0, 5).map((item) => (
                <TableRow key={item.id} hover sx={{ cursor: "pointer" }} onClick={() => setOpenId(item.id)}>
                  <TableCell>{item.resource_name}</TableCell>
                  <TableCell><Chip size="small" color={ACTION_COLOR[item.action_type]} label={ACTION_LABEL[item.action_type]} /></TableCell>
                  <TableCell>{sizeLabel(item.from_config)}</TableCell>
                  <TableCell>{sizeLabel(item.to_config)}</TableCell>
                  <TableCell align="right">{pct(item.cpu_p95)}</TableCell>
                  <TableCell align="right">{pct(item.memory_p95)}</TableCell>
                  <TableCell align="right"><Typography variant="body2" fontWeight={700} color={(item.monthly_saving ?? 0) > 0 ? "success.main" : "error.main"}>{money(item.monthly_saving, currency)}</Typography></TableCell>
                </TableRow>
              ))}
              {items.every((item) => item.action_type === "KEEP") && <TableRow><TableCell colSpan={7}><Typography variant="body2" color="text.secondary">Todos os recursos analisados estão bem dimensionados.</Typography></TableCell></TableRow>}
            </TableBody>
          </Table>
          <Alert severity="info" sx={{ mt: 2 }}>
            Política {run.policy_version}: CPU P95 ≤ 70% e P99 ≤ 90%; memória P95 ≤ 80% e P99 ≤ 90% no tamanho recomendado.
            Reduções menores que 15% são ignoradas. Prontidão: {Object.entries(summary.by_readiness ?? {}).map(([key, count]) => `${READINESS_LABEL[key] ?? key}: ${count}`).join(" · ") || "—"}.
          </Alert>
        </Paper>
      </Box>}

      {tab === 1 && <Box>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
          <Typography variant="body2" color="text.secondary">{visibleItems.length} recurso(s) na análise #{run?.id ?? "—"}</Typography>
          {actionFilter && <Chip size="small" label={`Ação: ${ACTION_LABEL[actionFilter as Opportunity["action_type"]] ?? actionFilter}`} onDelete={() => setActionFilter("")} />}
          {stateFilter && <Chip size="small" label={`Estado: ${STATE_LABEL[stateFilter as Opportunity["state"]] ?? stateFilter}`} onDelete={() => setStateFilter("")} />}
        </Stack>
        <DataGrid autoHeight rows={visibleItems} columns={columns} disableRowSelectionOnClick
                  onRowClick={(params) => setOpenId(Number(params.id))}
                  pageSizeOptions={[50, 100]} initialState={{ pagination: { paginationModel: { pageSize: 50 } } }}
                  sx={{ border: 0, "& .MuiDataGrid-row": { cursor: "pointer" } }} />
      </Box>}

      {tab === 2 && <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Execuções do motor</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Cada execução congela a janela, lê a Silver de métricas, a capacidade OCI e o preço efetivo do billing, e grava o resultado com hash dos insumos.
        </Typography>
        <Table size="small">
          <TableHead><TableRow>
            <TableCell>#</TableCell><TableCell>Situação</TableCell><TableCell>Concluída em</TableCell><TableCell>Janela (UTC)</TableCell><TableCell>Base</TableCell>
            <TableCell align="right">Recursos</TableCell><TableCell align="right">Oportunidades</TableCell>
            <TableCell align="right">Saving total / mês</TableCell><TableCell align="right">Saving médio / oportunidade</TableCell><TableCell>Motor</TableCell>
          </TableRow></TableHead>
          <TableBody>
            {runs.map((item) => <TableRow key={item.id}>
              <TableCell>{item.id}</TableCell>
              <TableCell><Chip size="small" color={item.status === "COMPLETED" ? "success" : item.status === "FAILED" ? "error" : "default"} label={item.status === "COMPLETED" ? "Concluída" : item.status === "FAILED" ? "Falhou" : "Em execução"} title={item.error_message ?? undefined} /></TableCell>
              <TableCell>{item.finished_at ? new Date(item.finished_at).toLocaleString("pt-BR") : "—"}</TableCell>
              <TableCell>{item.window_start_utc.slice(0, 10)} → {item.window_end_utc.slice(0, 10)}</TableCell>
              <TableCell>{item.resolution_seconds / 60} min</TableCell>
              <TableCell align="right">{item.resources_analyzed}</TableCell>
              <TableCell align="right">{item.opportunities_count}</TableCell>
              <TableCell align="right">{money(item.total_monthly_saving, item.currency ?? currency)}</TableCell>
              <TableCell align="right">{money(item.avg_monthly_saving, item.currency ?? currency)}</TableCell>
              <TableCell>{item.engine_version}</TableCell>
            </TableRow>)}
            {runs.length === 0 && <TableRow><TableCell colSpan={10}><Typography variant="body2" color="text.secondary">Nenhuma execução registrada.</Typography></TableCell></TableRow>}
          </TableBody>
        </Table>
      </Paper>}

      <OpportunityDrawer opportunityId={openId} onClose={() => setOpenId(null)} onChanged={() => void load()} />
    </Box>
  );
}
