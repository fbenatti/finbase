import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Collapse,
  FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Select,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { ExpandLess, ExpandMore, Refresh } from "@mui/icons-material";
import {
  AzureTagExecutiveDashboard as DashboardData,
  AzureTagExecutiveResource,
  AzureTenant,
  getAzureTagExecutiveDashboard,
  getAzureTagExecutiveResources,
  getAzureTenants,
  getTaskStatus,
  queueAzureTagExecutiveRefresh,
} from "../../services/api";

const fmt = (value: number) => new Intl.NumberFormat("pt-BR").format(value);
const statusLabel = (resource: AzureTagExecutiveResource) => {
  if (resource.taggable === false) return "Não tageável";
  if (resource.taggable === null) return "Não classificado";
  if (resource.tag_status === "complete") return "Completo";
  if (resource.tag_status === "partial") return "Parcial";
  return "Sem tags";
};

export default function AzureTagExecutiveDashboard() {
  const [tenants, setTenants] = useState<AzureTenant[]>([]);
  const [connectionId, setConnectionId] = useState<number | "">("");
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [resources, setResources] = useState<Record<string, AzureTagExecutiveResource[]>>({});
  const [tokens, setTokens] = useState<Record<string, string | null>>({});
  const [resourceLoading, setResourceLoading] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");

  useEffect(() => {
    getAzureTenants().then((items) => {
      setTenants(items);
      const preferred = items.find((item) => item.code === "azure-ti") ?? items[0];
      if (preferred) setConnectionId(preferred.connection_id);
    }).catch((cause) => setError(cause?.response?.data?.detail || "Não foi possível carregar conexões Azure."));
  }, []);

  const loadDashboard = useCallback(async () => {
    if (connectionId === "") return null;
    setLoading(true);
    setError("");
    setExpanded(null);
    setResources({});
    setTokens({});
    try {
      const result = await getAzureTagExecutiveDashboard(connectionId);
      setData(result);
      return result;
    } catch (cause: any) {
      setData(null);
      setError(cause?.response?.data?.detail || "Falha ao consultar o inventário atual do Azure.");
      return null;
    } finally {
      setLoading(false);
    }
  }, [connectionId]);

  useEffect(() => { if (connectionId !== "") void loadDashboard(); }, [connectionId, loadDashboard]);

  const totals = useMemo(() => (data?.subscriptions ?? []).reduce((acc, item) => {
    acc.resources += item.total_resources;
    acc.groups += item.resource_groups;
    acc.taggable += item.taggable_resources + item.taggable_resource_groups;
    acc.complete += item.fully_tagged;
    acc.nonTaggable += item.non_taggable_resources + item.non_taggable_resource_groups;
    return acc;
  }, { resources: 0, groups: 0, taggable: 0, complete: 0, nonTaggable: 0 }), [data]);
  const globalAdherence = totals.taggable > 0 && data?.tag_keys.length
    ? Math.round((totals.complete / totals.taggable) * 1000) / 10
    : null;

  const fetchResources = async (subscriptionId: string, next = false) => {
    if (connectionId === "") return;
    setResourceLoading(subscriptionId);
    try {
      const page = await getAzureTagExecutiveResources(connectionId, subscriptionId, next ? tokens[subscriptionId] : null);
      setResources((current) => ({ ...current, [subscriptionId]: next ? [...(current[subscriptionId] ?? []), ...page.resources] : page.resources }));
      setTokens((current) => ({ ...current, [subscriptionId]: page.next_page_token }));
    } catch (cause: any) {
      setError(cause?.response?.data?.detail || `Falha ao carregar recursos da subscription ${subscriptionId}.`);
    } finally {
      setResourceLoading(null);
    }
  };

  const toggleSubscription = (subscriptionId: string) => {
    if (expanded === subscriptionId) { setExpanded(null); return; }
    setExpanded(subscriptionId);
    if (!resources[subscriptionId]) void fetchResources(subscriptionId);
  };

  const refreshInventory = async () => {
    if (connectionId === "") return;
    setRefreshing(true);
    setError("");
    setRefreshMessage("Enfileirando coleta Resource Graph para gravar o snapshot na Bronze…");
    try {
      const queued = await queueAzureTagExecutiveRefresh(connectionId);
      const terminal = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"]);
      let task = await getTaskStatus(queued.task_id);
      for (let attempt = 0; !terminal.has(task.status) && attempt < 180; attempt += 1) {
        setRefreshMessage(task.phase || "Coletando inventário Azure e atualizando a Bronze…");
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        task = await getTaskStatus(queued.task_id);
      }
      if (task.status === "COMPLETED") {
        setRefreshMessage("Coleta concluída. Lendo o snapshot mais recente da Bronze…");
        const updated = await loadDashboard();
        setRefreshMessage(`Snapshot Bronze atualizado${updated?.snapshot_date ? ` (${updated.snapshot_date})` : ""}.`);
      } else if (terminal.has(task.status)) {
        throw new Error(task.errors?.join("; ") || task.phase || `A coleta terminou com status ${task.status}.`);
      } else {
        throw new Error(`A coleta continua em andamento (task ${queued.task_id}). Consulte o histórico de extrações para acompanhar.`);
      }
    } catch (cause: any) {
      setError(cause?.response?.data?.detail || cause?.message || "Não foi possível atualizar o inventário Azure.");
      setRefreshMessage("");
    } finally {
      setRefreshing(false);
    }
  };

  return <Box>
    <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", md: "center" }} spacing={2} sx={{ mb: 2 }}>
      <Box>
        <Typography variant="h5" fontWeight={700}>Dashboard Executivo de Tags</Typography>
        <Typography variant="body2" color="text.secondary">Aderência calculada pelas tags do último snapshot Resource Graph armazenado na Bronze.</Typography>
      </Box>
      <Stack direction="row" spacing={1}>
        <FormControl size="small" sx={{ minWidth: 250 }}>
          <InputLabel>Conexão Azure</InputLabel>
          <Select label="Conexão Azure" value={connectionId} onChange={(event) => setConnectionId(event.target.value as number)}>
            {tenants.map((tenant) => <MenuItem key={tenant.connection_id} value={tenant.connection_id}>{tenant.name} ({tenant.code})</MenuItem>)}
          </Select>
        </FormControl>
        <Button variant="contained" startIcon={refreshing ? <CircularProgress size={16} color="inherit" /> : <Refresh />} onClick={() => void refreshInventory()} disabled={loading || refreshing || connectionId === ""}>
          {refreshing ? "Coletando…" : "Atualizar inventário Azure"}
        </Button>
      </Stack>
    </Stack>

    {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
    {refreshMessage && <Alert severity={refreshing ? "info" : "success"} sx={{ mb: 2 }}>{refreshMessage}</Alert>}
    {data && <Alert severity={data.snapshot_date ? "info" : "warning"} sx={{ mb: 2 }}>
      {data.snapshot_date ? `Snapshot de inventário: ${data.snapshot_date}.` : "Ainda não existe snapshot do Resource Graph na Bronze para esta conexão. Use “Atualizar inventário Azure” para coletar."}
    </Alert>}
    {data && <Alert severity="info" sx={{ mb: 2 }}>
      Aderência = percentual dos itens tageáveis com todas as chaves {data.tag_keys.map((key) => <Chip key={key} label={key} size="small" sx={{ mx: 0.3 }} />)} gerenciadas preenchidas. Este indicador verifica presença; a comparação dos valores esperados aparece nos detalhes de cada recurso. {data.generated_at ? `Bronze gravada em ${new Date(data.generated_at).toLocaleString("pt-BR")}.` : "Sem coleta registrada."}
    </Alert>}
    {data && <Alert severity="info" icon={false} sx={{ mb: 2 }}>
      <Typography variant="body2" fontWeight={700}>Tags esperadas cadastradas</Typography>
      <Typography variant="body2">
        {data.tag_mappings?.length
          ? data.tag_mappings.map((mapping) => `${mapping.tag_key} ← ${mapping.focus_field}`).join(" · ")
          : "Nenhum mapeamento de tag ativo."}
        {" · Os valores esperados por recurso vêm da classificação mais recente na Silver."}
      </Typography>
    </Alert>}

    {loading && <LinearProgress sx={{ mb: 2 }} />}
    {data && <>
      <Card variant="outlined" sx={{ mb: 2, borderLeft: "4px solid", borderLeftColor: globalAdherence === null ? "grey.400" : globalAdherence >= 90 ? "success.main" : globalAdherence >= 60 ? "warning.main" : "error.main" }}>
        <CardContent sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2, flexWrap: "wrap", p: "16px !important" }}>
          <Box>
            <Typography variant="overline" color="text.secondary" fontWeight={700}>Aderência global às tags</Typography>
            <Typography variant="body2" color="text.secondary">{data.connection_name} · Todas as subscriptions no snapshot atual</Typography>
          </Box>
          <Stack direction="row" spacing={2} alignItems="center">
            <Typography variant="h4" fontWeight={800} color={globalAdherence === null ? "text.secondary" : globalAdherence >= 90 ? "success.main" : globalAdherence >= 60 ? "warning.main" : "error.main"}>
              {globalAdherence === null ? "—" : `${globalAdherence.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`}
            </Typography>
            <Box>
              <Typography variant="body2" fontWeight={700}>{fmt(totals.complete)} / {fmt(totals.taggable)}</Typography>
              <Typography variant="caption" color="text.secondary">recursos tageáveis com todas as tags</Typography>
            </Box>
          </Stack>
        </CardContent>
      </Card>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2,1fr)", md: "repeat(5,1fr)" }, gap: 1.5, mb: 3 }}>
        {[
          ["Subscriptions", data.subscriptions.length], ["Recursos", totals.resources], ["Resource Groups", totals.groups],
          ["Tageáveis", totals.taggable], ["Não tageáveis", totals.nonTaggable],
        ].map(([label, value]) => <Card key={String(label)} variant="outlined"><CardContent sx={{ p: "14px !important" }}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="h5" fontWeight={700}>{fmt(Number(value))}</Typography></CardContent></Card>)}
      </Box>
      <Stack spacing={1.5}>
        {data.subscriptions.map((item) => {
          const isOpen = expanded === item.subscription_id;
          const adherence = item.adherence_percent;
          return <Card key={item.subscription_id} variant="outlined" sx={{ borderLeft: "4px solid", borderLeftColor: adherence === null ? "grey.400" : adherence >= 90 ? "success.main" : adherence >= 60 ? "warning.main" : "error.main", overflow: "hidden" }}>
            <Box role="button" tabIndex={0} onClick={() => toggleSubscription(item.subscription_id)} onKeyDown={(event) => { if (event.key === "Enter") toggleSubscription(item.subscription_id); }} sx={{ display: "grid", gridTemplateColumns: { xs: "1fr auto", md: "minmax(230px,1.3fr) minmax(180px,1fr) minmax(300px,2fr) auto" }, gap: 2, alignItems: "center", p: 2, cursor: "pointer" }}>
              <Box><Typography fontWeight={700}>{item.name}</Typography><Typography variant="caption" color="text.secondary">{item.subscription_id}</Typography></Box>
              <Stack direction="row" alignItems="center" spacing={1.5}>
                {adherence === null ? <Chip label="Sem base tageável" size="small" /> : <><Box sx={{ position: "relative", display: "inline-flex" }}><CircularProgress variant="determinate" value={100} size={52} thickness={5} sx={{ color: "action.hover" }} /><CircularProgress variant="determinate" value={adherence} size={52} thickness={5} sx={{ position: "absolute", left: 0, color: adherence >= 90 ? "success.main" : adherence >= 60 ? "warning.main" : "error.main" }} /><Typography variant="caption" sx={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontWeight: 700 }}>{adherence}%</Typography></Box><Box><Typography variant="caption" color="text.secondary">Aderência</Typography><Typography variant="body2">{fmt(item.fully_tagged)} / {fmt(item.taggable_items)}</Typography></Box></>}
              </Stack>
              <Stack direction="row" gap={0.7} flexWrap="wrap">
                <Chip size="small" label={`${fmt(item.total_resources)} recursos`} />
                <Chip size="small" label={`${fmt(item.resource_groups)} RGs`} />
                <Chip size="small" color="success" variant="outlined" label={`${fmt(item.taggable_resources + item.taggable_resource_groups)} tageáveis`} />
                <Chip size="small" color="warning" variant="outlined" label={`${fmt(item.non_taggable_resources + item.non_taggable_resource_groups)} não tageáveis`} />
              </Stack>
              <IconButton size="small" aria-label={isOpen ? "Recolher recursos" : "Ver recursos"}>{isOpen ? <ExpandLess /> : <ExpandMore />}</IconButton>
            </Box>
            {item.inventory_error && <Alert severity="error" sx={{ mx: 2, mb: 2 }}>{item.inventory_error}</Alert>}
            {item.capability_warnings.length > 0 && <Alert severity="warning" sx={{ mx: 2, mb: 2 }}>Classificação de suporte a tags incompleta: {item.capability_warnings.join("; ")}</Alert>}
            <Collapse in={isOpen}>
              <CardContent sx={{ pt: 0 }}>
                {resourceLoading === item.subscription_id && <LinearProgress sx={{ mb: 1 }} />}
                <Box sx={{ overflowX: "auto" }}><Table size="small"><TableHead><TableRow><TableCell>Recurso / Resource Group</TableCell><TableCell>Tipo</TableCell><TableCell>Grupo</TableCell><TableCell>Status</TableCell><TableCell>Tags presentes no recurso</TableCell></TableRow></TableHead><TableBody>
                  {(resources[item.subscription_id] ?? []).map((resource) => {
                    const actualTags = resource.tags && typeof resource.tags === "object" ? resource.tags : {};
                    const matching = new Set((resource.matching_tags ?? []).map((key) => key.toLocaleLowerCase()));
                    const managed = new Set((resource.managed_tag_keys ?? []).map((key) => key.toLocaleLowerCase()));
                    const expected = resource.expected_tags ?? {};
                    return <TableRow key={resource.resource_id} hover>
                      <TableCell sx={{ maxWidth: 380 }}><Typography variant="body2" noWrap title={resource.resource_name}>{resource.resource_name}</Typography></TableCell>
                      <TableCell>{resource.is_resource_group ? "Resource Group" : resource.resource_type}</TableCell>
                      <TableCell>{resource.is_resource_group ? "—" : resource.resource_group || "—"}</TableCell>
                      <TableCell><Chip size="small" label={statusLabel(resource)} color={resource.taggable === false ? "default" : resource.taggable === null ? "warning" : resource.tag_status === "complete" ? "success" : "error"} />{resource.tag_support_reason && <Typography variant="caption" display="block" color="text.secondary">{resource.tag_support_reason}</Typography>}</TableCell>
                      <TableCell sx={{ minWidth: 300 }}>
                        {Object.keys(actualTags).length ? <Stack direction="row" gap={0.5} flexWrap="wrap">
                          {Object.entries(actualTags).map(([key, value]) => {
                            const folded = key.toLocaleLowerCase();
                            const matched = matching.has(folded);
                            const managedKey = managed.has(folded);
                            return <Chip key={key} size="small" variant={matched ? "filled" : "outlined"}
                              color={matched ? "success" : managedKey ? "info" : "default"}
                              label={`${key}=${String(value)}`} title={expected[key] ? `Esperado: ${expected[key]}` : undefined} />;
                          })}
                        </Stack> : <Typography variant="caption" color="text.secondary">Nenhuma tag presente</Typography>}
                        {Object.keys(expected).length > 0
                          ? <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 0.5 }}>
                              Esperado{resource.expected_tags_source === "resource_group" ? " pelo Resource Group" : " pela classificação"}: {Object.entries(expected).map(([key, value]) => `${key}=${value}`).join(" · ")}
                            </Typography>
                          : <Typography variant="caption" display="block" color={resource.expected_tags_ambiguous ? "warning.main" : "text.secondary"} sx={{ mt: 0.5 }}>
                              {resource.expected_tags_ambiguous ? "Mais de uma classificação possível na Silver para este Resource Group." : "Expectativa não encontrada na Silver mais recente."}
                            </Typography>}
                      </TableCell>
                    </TableRow>;
                  })}
                </TableBody></Table></Box>
                {tokens[item.subscription_id] && <Button size="small" sx={{ mt: 1 }} onClick={() => void fetchResources(item.subscription_id, true)} disabled={resourceLoading === item.subscription_id}>Carregar mais</Button>}
                {resources[item.subscription_id]?.length === 0 && resourceLoading !== item.subscription_id && <Typography color="text.secondary" sx={{ py: 2 }}>Nenhum recurso retornado nesta subscription.</Typography>}
              </CardContent>
            </Collapse>
          </Card>;
        })}
      </Stack>
      {data.subscriptions.length === 0 && !loading && <Alert severity="info">Nenhuma subscription acessível nesta conexão.</Alert>}
    </>}
  </Box>;
}
