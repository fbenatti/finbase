import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Collapse,
  FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Select,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { ExpandLess, ExpandMore, History, PictureAsPdfOutlined, Refresh } from "@mui/icons-material";
import html2canvas from "html2canvas";
import jsPDF from "jspdf";
import {
  AzureTagExecutiveDashboard as DashboardData,
  AzureTagExecutiveResource,
  AzureTagPolicyTimeline,
  AzureTenant,
  getAzureTagExecutiveDashboard,
  getAzureTagExecutiveResources,
  getAzureTagPolicyTimeline,
  getAzureTenants,
  getTaskStatus,
  queueAzureTagExecutiveRefresh,
} from "../../services/api";

const fmt = (value: number) => new Intl.NumberFormat("pt-BR").format(value);
const adherenceLevel = (value: number | null) => {
  if (value === null) return { label: "Sem dado", color: "#94a3b8", background: "#f1f5f9" };
  if (value < 25) return { label: "0–24%", color: "#dc2626", background: "#fef2f2" };
  if (value < 50) return { label: "25–49%", color: "#ea580c", background: "#fff7ed" };
  if (value < 75) return { label: "50–74%", color: "#ca8a04", background: "#fefce8" };
  return { label: "75–100%", color: "#16a34a", background: "#f0fdf4" };
};
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
  const [exporting, setExporting] = useState(false);
  const [timeline, setTimeline] = useState<AzureTagPolicyTimeline | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineSubscription, setTimelineSubscription] = useState("");
  const [timelineOpen, setTimelineOpen] = useState(false);
  const exportRootRef = useRef<HTMLDivElement>(null);

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

  const loadTimeline = useCallback(async () => {
    if (connectionId === "") return;
    setTimelineLoading(true);
    try {
      const result = await getAzureTagPolicyTimeline(connectionId);
      setTimeline(result);
    } catch (cause: any) {
      setError(cause?.response?.data?.detail || "Não foi possível carregar o histórico de tags e policies.");
    } finally {
      setTimelineLoading(false);
    }
  }, [connectionId]);

  useEffect(() => {
    setTimeline(null);
    setTimelineSubscription("");
    setTimelineOpen(false);
  }, [connectionId]);

  useEffect(() => {
    if (timelineOpen && connectionId !== "" && !timeline) void loadTimeline();
  }, [timelineOpen, connectionId, timeline, loadTimeline]);

  const timelinePoints = useMemo(() => (timeline?.snapshots ?? []).map((snapshot) => {
    const selected = timelineSubscription
      ? snapshot.subscriptions.find((item) => item.subscription_id === timelineSubscription)
      : null;
    return {
      date: snapshot.snapshot_date,
      label: new Date(`${snapshot.snapshot_date}T00:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" }),
      global: snapshot.global.adherence_percent,
      value: timelineSubscription ? selected?.adherence_percent ?? null : snapshot.global.adherence_percent,
    };
  }), [timeline, timelineSubscription]);

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
  const globalCardColor = globalAdherence === null ? "grey.500" : globalAdherence >= 90 ? "success.main" : globalAdherence >= 60 ? "warning.main" : "error.main";
  const validTimelinePoints = timelinePoints.filter((point) => point.value !== null);
  const firstTimelinePoint = validTimelinePoints[0] ?? null;
  const lastTimelinePoint = validTimelinePoints[validTimelinePoints.length - 1] ?? null;
  const firstTimelineValue = firstTimelinePoint?.value ?? null;
  const lastTimelineValue = lastTimelinePoint?.value ?? null;
  const timelineDelta = firstTimelineValue !== null && lastTimelineValue !== null ? lastTimelineValue - firstTimelineValue : null;
  const timelineSubscriptionName = timeline?.subscriptions.find((item) => item.subscription_id === timelineSubscription)?.name;
  const timelineMetricLabel = timelineSubscriptionName ? `Aderência mais recente · ${timelineSubscriptionName}` : "Aderência mais recente · global";

  const exportPdf = async () => {
    if (!exportRootRef.current || exporting) return;
    setExporting(true);
    try {
      const canvas = await html2canvas(exportRootRef.current, {
        scale: 1.5,
        backgroundColor: "#ffffff",
        useCORS: true,
        onclone: (documentClone) => {
          documentClone.querySelectorAll<HTMLElement>("[data-pdf-hide]").forEach((element) => { element.style.display = "none"; });
          documentClone.querySelectorAll<HTMLElement>(".MuiCollapse-root").forEach((element) => { element.style.height = "auto"; element.style.visibility = "visible"; });
          documentClone.querySelectorAll<HTMLElement>(".MuiCollapse-hidden").forEach((element) => { element.style.display = "none"; });
          documentClone.querySelectorAll<HTMLElement>("table").forEach((element) => { element.style.width = "100%"; });
        },
      });
      const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4", compress: true });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 5;
      const imageWidth = pageWidth - margin * 2;
      const imageHeight = canvas.height * imageWidth / canvas.width;
      const pageCanvas = document.createElement("canvas");
      const pageContext = pageCanvas.getContext("2d");
      if (!pageContext) throw new Error("Não foi possível preparar as páginas do PDF.");
      const pagePixelHeight = Math.floor(canvas.width * (pageHeight - margin * 2) / imageWidth);
      pageCanvas.width = canvas.width;
      pageCanvas.height = pagePixelHeight;
      let offset = 0;
      while (offset < canvas.height) {
        const sliceHeight = Math.min(pagePixelHeight, canvas.height - offset);
        pageContext.clearRect(0, 0, pageCanvas.width, pageCanvas.height);
        pageContext.drawImage(canvas, 0, offset, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight);
        if (offset > 0) pdf.addPage();
        pdf.addImage(pageCanvas.toDataURL("image/jpeg", 0.9), "JPEG", margin, margin, imageWidth, sliceHeight * imageWidth / canvas.width);
        offset += sliceHeight;
      }
      void imageHeight;
      pdf.save(`dashboard-executivo-tags-${data?.connection_code || "azure"}.pdf`);
    } finally {
      setExporting(false);
    }
  };

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
    setRefreshMessage("Enfileirando coleta de inventário para atualizar a base de origem…");
    try {
      const queued = await queueAzureTagExecutiveRefresh(connectionId);
      const terminal = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"]);
      let task = await getTaskStatus(queued.task_id);
      for (let attempt = 0; !terminal.has(task.status) && attempt < 180; attempt += 1) {
        setRefreshMessage(task.phase || "Coletando inventário Azure e atualizando a base de origem…");
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        task = await getTaskStatus(queued.task_id);
      }
      if (task.status === "COMPLETED") {
        setRefreshMessage("Coleta concluída. Lendo o inventário mais recente…");
        const updated = await loadDashboard();
        setRefreshMessage(`Inventário atualizado${updated?.snapshot_date ? ` (${updated.snapshot_date})` : ""}.`);
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

  return <Box ref={exportRootRef}>
    <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", md: "center" }} spacing={2} sx={{ mb: 2 }}>
      <Box>
        <Typography variant="h5" fontWeight={700}>Dashboard Executivo de Tags</Typography>
        <Typography variant="body2" color="text.secondary">Aderência calculada pelas tags do último inventário coletado.</Typography>
      </Box>
      <Stack direction="row" spacing={1}>
        <FormControl data-pdf-hide size="small" sx={{ minWidth: 250 }}>
          <InputLabel>Conexão Azure</InputLabel>
          <Select label="Conexão Azure" value={connectionId} onChange={(event) => setConnectionId(event.target.value as number)}>
            {tenants.map((tenant) => <MenuItem key={tenant.connection_id} value={tenant.connection_id}>{tenant.name} ({tenant.code})</MenuItem>)}
          </Select>
        </FormControl>
        <Button data-pdf-hide variant="outlined" startIcon={<PictureAsPdfOutlined />} onClick={() => void exportPdf()} disabled={exporting || loading || !data}>{exporting ? "Gerando PDF…" : "Exportar PDF"}</Button>
        <Button data-pdf-hide variant="contained" startIcon={refreshing ? <CircularProgress size={16} color="inherit" /> : <Refresh />} onClick={() => void refreshInventory()} disabled={loading || refreshing || connectionId === ""}>
          {refreshing ? "Coletando…" : "Atualizar inventário Azure"}
        </Button>
      </Stack>
    </Stack>

    {error && <Alert severity="error" sx={{ mb: 2, whiteSpace: "pre-line" }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
    {refreshMessage && <Alert severity={refreshing ? "info" : "success"} sx={{ mb: 2 }}>{dataDisplayText(refreshMessage)}</Alert>}
    {loading && <LinearProgress sx={{ mb: 2 }} />}
    {data && <>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2,minmax(0,1fr))", sm: "repeat(3,minmax(0,1fr))", lg: "repeat(6,minmax(0,1fr))" }, gap: 1.5, mb: 1.5 }}>
        {[
          ["Aderência global", globalAdherence === null ? "—" : `${globalAdherence.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`],
          ["Subscriptions", data.subscriptions.length], ["Recursos", totals.resources], ["Resource Groups", totals.groups],
          ["Tageáveis", totals.taggable], ["Não tageáveis", totals.nonTaggable],
        ].map(([label, value]) => label === "Aderência global" ? <Card
          key="global-adherence"
          variant="outlined"
          role="button"
          tabIndex={0}
          aria-expanded={timelineOpen}
          aria-label={`${timelineOpen ? "Ocultar" : "Ver"} timeline de aderência global`}
          onClick={() => setTimelineOpen((open) => !open)}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setTimelineOpen((open) => !open); } }}
          sx={{ minWidth: 0, cursor: "pointer", borderLeft: "4px solid", borderLeftColor: globalCardColor, bgcolor: timelineOpen ? "action.hover" : "background.paper", transition: "background-color 160ms ease, transform 160ms ease", "&:hover": { bgcolor: "action.hover", transform: "translateY(-2px)" }, "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: 2 } }}
        >
          <CardContent sx={{ p: { xs: "10px !important", md: "14px !important" }, minWidth: 0 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={0.5}>
              <Typography variant="caption" color="text.secondary" noWrap>Aderência global</Typography>
              <History fontSize="small" color="action" />
            </Stack>
            <Typography variant="h5" fontWeight={700} noWrap color={globalCardColor}>{value}</Typography>
            <Typography variant="caption" display="block" color="text.secondary" noWrap>{fmt(totals.complete)} / {fmt(totals.taggable)} tageáveis · {timelineOpen ? "Ocultar evolução" : "Ver evolução"}</Typography>
          </CardContent>
        </Card> : <Card key={String(label)} variant="outlined" sx={{ minWidth: 0 }}><CardContent sx={{ p: { xs: "10px !important", md: "14px !important" }, minWidth: 0 }}><Typography variant="caption" color="text.secondary" noWrap display="block">{label}</Typography><Typography variant="h5" fontWeight={700} noWrap>{fmt(Number(value))}</Typography></CardContent></Card>)}
      </Box>
      <Collapse in={timelineOpen} unmountOnExit>
        <Card variant="outlined" sx={{ mb: 3, overflow: "hidden", background: "linear-gradient(135deg, #ffffff 0%, #f5f8ff 65%, #f0fdfa 100%)" }}>
          <CardContent sx={{ p: { xs: 2, md: 3 } }}>
            <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", md: "center" }} spacing={2} sx={{ mb: 2.5 }}>
              <Box>
                <Typography variant="overline" color="primary.main" fontWeight={800} letterSpacing={1.1}>HISTÓRICO DE ADERÊNCIA</Typography>
                <Typography variant="h5" fontWeight={750}>Evolução das tags</Typography>
                <Typography variant="body2" color="text.secondary">Snapshots de inventário preservados desde 12/10/2026</Typography>
              </Box>
              <Stack direction="row" spacing={1} alignItems="center">
                <FormControl size="small" sx={{ minWidth: { xs: 210, sm: 260 }, bgcolor: "background.paper" }}>
                  <InputLabel>Comparar subscription</InputLabel>
                  <Select label="Comparar subscription" value={timelineSubscription} onChange={(event) => setTimelineSubscription(event.target.value)}>
                    <MenuItem value="">Somente consolidado</MenuItem>
                    {(timeline?.subscriptions ?? []).map((item) => <MenuItem key={item.subscription_id} value={item.subscription_id}>{item.name}</MenuItem>)}
                  </Select>
                </FormControl>
                <IconButton data-pdf-hide aria-label="Ocultar timeline" onClick={() => setTimelineOpen(false)}><ExpandLess /></IconButton>
              </Stack>
            </Stack>
            {timelineLoading && <LinearProgress sx={{ mb: 2, borderRadius: 2 }} />}
            {timeline && timelinePoints.length > 0 && <>
              <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
                {[25, 50, 75, 100].map((threshold) => {
                  const level = adherenceLevel(threshold - 1);
                  return <Stack key={threshold} direction="row" spacing={0.6} alignItems="center">
                    <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: level.color }} />
                    <Typography variant="caption" color="text.secondary">{level.label}</Typography>
                  </Stack>;
                })}
              </Stack>
              <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(3, minmax(0,1fr))" }, gap: 1.25, mb: 2.5 }}>
                <Box sx={{ p: 1.6, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="caption" color="text.secondary">{timelineMetricLabel.toLocaleUpperCase("pt-BR")}</Typography>
                  <Typography variant="h4" fontWeight={800} color={adherenceLevel(lastTimelineValue).color}>{lastTimelineValue === null ? "—" : `${lastTimelineValue.toLocaleString("pt-BR", { minimumFractionDigits: 1 })}%`}</Typography>
                  <Typography variant="caption" color="text.secondary">{lastTimelinePoint?.label ?? "Sem medição"}</Typography>
                </Box>
                <Box sx={{ p: 1.6, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="caption" color="text.secondary">VARIAÇÃO NO PERÍODO</Typography>
                  <Typography variant="h4" fontWeight={800} color={timelineDelta === null ? "text.primary" : timelineDelta >= 0 ? "success.main" : "error.main"}>{timelineDelta === null ? "—" : `${timelineDelta >= 0 ? "+" : ""}${timelineDelta.toLocaleString("pt-BR", { minimumFractionDigits: 1 })} p.p.`}</Typography>
                  <Typography variant="caption" color="text.secondary">desde {firstTimelinePoint?.label ?? "Sem medição"}</Typography>
                </Box>
                <Box sx={{ p: 1.6, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
                  <Typography variant="caption" color="text.secondary">MEDIÇÕES DE INVENTÁRIO</Typography>
                  <Typography variant="h4" fontWeight={800}>{fmt(validTimelinePoints.length)}</Typography>
                  <Typography variant="caption" color="text.secondary">{firstTimelinePoint?.label ?? "—"} – {lastTimelinePoint?.label ?? "—"}</Typography>
                </Box>
              </Box>
              <Box sx={{ position: "relative", px: { xs: 0.5, sm: 2 }, py: 2.5, overflowX: "auto" }}>
                <Box sx={{ minWidth: Math.max(560, timelinePoints.length * 155), position: "relative" }}>
                  <Box sx={{ position: "absolute", left: 24, right: 24, top: 18, height: 6, borderRadius: 4, bgcolor: "#dbe5f3" }} />
                  <Box sx={{ position: "absolute", left: 24, width: timelinePoints.length > 1 ? `calc((100% - 48px) * ${(timelinePoints.length - 1) / timelinePoints.length})` : 0, top: 18, height: 6, borderRadius: 4, background: "linear-gradient(90deg, #2563eb, #06b6d4)" }} />
                  <Box sx={{ display: "grid", gridTemplateColumns: `repeat(${timelinePoints.length}, minmax(130px, 1fr))`, position: "relative" }}>
                    {timelinePoints.map((point, index) => {
                      const previousPoint = timelinePoints.slice(0, index).reverse().find((candidate) => candidate.value !== null);
                      const delta = point.value !== null && previousPoint?.value !== null && previousPoint
                        ? point.value - previousPoint.value : null;
                      const level = adherenceLevel(point.value);
                      return <Box key={point.date} sx={{ textAlign: "center", px: 0.75 }}>
                        <Box sx={{ mx: "auto", width: 42, height: 42, borderRadius: "50%", display: "grid", placeItems: "center", bgcolor: level.background, border: "4px solid", borderColor: level.color, boxShadow: `0 4px 14px ${level.color}33`, position: "relative", zIndex: 1 }}>
                          <Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: level.color }} />
                        </Box>
                        <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 1.2 }}>{point.label}</Typography>
                        <Typography variant="h6" fontWeight={800} color={level.color}>{point.value === null ? "—" : `${point.value.toLocaleString("pt-BR", { minimumFractionDigits: 1 })}%`}</Typography>
                        <Typography variant="caption" display="block" color={delta === null ? "text.secondary" : delta >= 0 ? "success.main" : "error.main"} fontWeight={700}>
                          {index === 0 ? "primeiro snapshot" : delta === null ? "sem comparação" : `${delta >= 0 ? "↑" : "↓"} ${Math.abs(delta).toLocaleString("pt-BR", { minimumFractionDigits: 1 })} p.p.`}
                        </Typography>
                        {timelineSubscription && point.value !== null && point.global !== null && <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 0.4 }}>global {point.global.toLocaleString("pt-BR", { minimumFractionDigits: 1 })}%</Typography>}
                      </Box>;
                    })}
                  </Box>
                </Box>
              </Box>
              {timelineSubscriptionName && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}><Box component="span" sx={{ color: "#0891b2", fontWeight: 800 }}>●</Box> {timelineSubscriptionName} · todos os indicadores e marcos mostram esta subscription; aderência global aparece abaixo de cada marco.</Typography>}
            </>}
            {timeline && timelinePoints.length === 0 && !timelineLoading && <Alert severity="info">Ainda não existem snapshots de inventário desde 12/10/2026.</Alert>}
            {!timeline && !timelineLoading && <Typography variant="body2" color="text.secondary">Abrindo histórico…</Typography>}
          </CardContent>
        </Card>
      </Collapse>
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
              <IconButton data-pdf-hide size="small" aria-label={isOpen ? "Recolher recursos" : "Ver recursos"}>{isOpen ? <ExpandLess /> : <ExpandMore />}</IconButton>
            </Box>
            {item.inventory_error && <Alert severity="error" sx={{ mx: 2, mb: 2 }}>{dataDisplayText(item.inventory_error)}</Alert>}
            {item.capability_warnings.length > 0 && <Alert severity="warning" sx={{ mx: 2, mb: 2 }}>Classificação de suporte a tags incompleta: {dataDisplayText(item.capability_warnings.join("; "))}</Alert>}
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
                              {resource.expected_tags_ambiguous ? "Mais de uma classificação possível na base preparada para este Resource Group." : "Expectativa não encontrada na base preparada mais recente."}
                            </Typography>}
                      </TableCell>
                    </TableRow>;
                  })}
                </TableBody></Table></Box>
                {tokens[item.subscription_id] && <Button data-pdf-hide size="small" sx={{ mt: 1 }} onClick={() => void fetchResources(item.subscription_id, true)} disabled={resourceLoading === item.subscription_id}>Carregar mais</Button>}
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
