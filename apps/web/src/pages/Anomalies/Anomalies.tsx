import { dataDisplayText } from "../../utils/dataPresentation";
/**
 * Página `/anomalies` — gestão de anomalias de custo cloud.
 *
 * Visões: Visão geral · Meus casos / fila FinOps · Casos · Eventos esperados ·
 * Qualidade · Retrospectiva.
 *
 * O detalhe do caso mostra o painel de etapas (responsável, prazo, próxima
 * ação), o gráfico observado/faixa/área anômala, as ações (remediação) e o
 * workflow por comandos explícitos.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, LinearProgress,
  MenuItem, Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs,
  TextField, Tooltip, Typography,
} from "@mui/material";
import AnomalyCaseDrawer, { CAUSE_LABEL, DECISION_LABEL, SEVERITY_COLOR, STATE_LABEL, daysBetween, dt } from "./AnomalyCaseDrawer";
import AnomaliesCasesDataGrid from "./AnomaliesCasesDataGrid";
import AnomaliesImpactTab from "./AnomaliesImpactTab";
import AnomaliesSettingsTab from "./AnomaliesSettingsTab";
import ExpandMoreIcon from "@mui/icons-material/ExpandMoreOutlined";
import CloseIcon from "@mui/icons-material/CloseOutlined";
import RefreshIcon from "@mui/icons-material/RefreshOutlined";
import PlayArrowIcon from "@mui/icons-material/PlayArrowOutlined";
import type {
  AnomalyCase, AnomalyConfig, AnomalyNotification, AnomalyRunSummary, AnomalySeries, CaseSummary, ImpactSummary,
  ExpectedEventFamily, QualityBucket,
} from "../../services/anomaliesApi";
import {
  createAnomalyAction, enqueueAnomalyRun, getAnomalyCase, getAnomalyCaseEvents,
  getAnomalyCaseSeries, getAnomalyMetrics, getExpectedEvents, getQuality,
  listAnomalyCases, listAnomalyConfigs, listAnomalyNotifications, listAnomalyRuns,
  getAnomalyImpactSummary,
  readAnomalyNotification, transitionAnomalyCase, updateAnomalyAction,
  updateAnomalyConfig,
} from "../../services/anomaliesApi";

const OPEN_CASE_STATES = ["NEW", "ASSIGNED", "INVESTIGATING", "DECIDED", "IN_REMEDIATION", "VERIFYING", "RETROSPECTIVE"];
const CASE_SLICE_LABEL: Record<string, string> = {
  __open__: "Em aberto",
  __overdue__: "Atrasados",
  __unassigned__: "Sem dono",
  __confirmed__: "Anomalias validadas",
  __recurrence__: "Famílias recorrentes",
};


const money = (value: number | null | undefined, currency = "BRL") =>
  value === null || value === undefined
    ? "—"
    : value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 2 });

type ExpectedEventFamiliesResult = { items: ExpectedEventFamily[]; total: number; note?: string };

export default function Anomalies() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = Number(searchParams.get("tab"));
  const tab = Number.isInteger(requestedTab) && requestedTab >= 0 && requestedTab <= 7 ? requestedTab : 0;
  const setTab = (next: number) => setSearchParams({ tab: String(next) }, { replace: true });
  const [configs, setConfigs] = useState<AnomalyConfig[]>([]);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [impactSummary, setImpactSummary] = useState<ImpactSummary | null>(null);
  const [retrospectiveItems, setRetrospectiveItems] = useState<AnomalyCase[]>([]);
  const [confirmedItems, setConfirmedItems] = useState<AnomalyCase[]>([]);
  const [latestRun, setLatestRun] = useState<AnomalyRunSummary | null>(null);
  const detectionRunning = latestRun?.status === "QUEUED" || latestRun?.status === "RUNNING";
  const [cases, setCases] = useState<AnomalyCase[]>([]);
  const [total, setTotal] = useState(0);
  const listRequest = useRef(0);
  const [expectedEvents, setExpectedEvents] = useState<ExpectedEventFamiliesResult>({ items: [], total: 0 });
  const [quality, setQuality] = useState<QualityBucket[]>([]);
  const [qualityNote, setQualityNote] = useState<string | null>(null);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Filtros do grid (estado, severidade, responsável) ficam no próprio DataGrid.
  // Aqui só existem os filtros vindos dos indicadores (KPI) do resumo.
  const [stateFilter, setStateFilter] = useState<string>("");
  const [caseSliceFilter, setCaseSliceFilter] = useState<string>("");

  const config = configs[0];
  const currency = config?.currency ?? "BRL";

  const loadAll = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [configList, metrics, runPage] = await Promise.all([
        listAnomalyConfigs(),
        getAnomalyMetrics(),
        listAnomalyRuns(1),
      ]);
      void getAnomalyImpactSummary().then(setImpactSummary).catch(() => setImpactSummary(null));
      setConfigs(configList);
      setSummary(metrics);
      setLatestRun(runPage.items[0] ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível carregar as anomalias");
    } finally {
      setBusy(false);
    }
  }, []);

  const loadExpected = useCallback(async () => {
    try {
      setExpectedEvents(await getExpectedEvents({ limit: 200 }));
      setRemoteError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Não foi possível listar os eventos previstos";
      setExpectedEvents({ items: [], total: 0, note: message.includes("409")
        ? "Nenhuma publicação vigente: rode a detecção para gerar avaliações."
        : message });
      setRemoteError(null);
    }
  }, []);

  const loadQuality = useCallback(async () => {
    try {
      const payload = await getQuality({ limit: 200 });
      setQuality(payload.items);
      setQualityNote(payload.note ?? null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Não foi possível listar a qualidade da carga";
      setQuality([]);
      setQualityNote(message.includes("409")
        ? "Nenhuma publicação vigente: rode a detecção para gerar avaliações."
        : message);
    }
  }, []);

  const loadCases = useCallback(async () => {
    const request = ++listRequest.current;
    setBusy(true);
    try {
      const params: Record<string, unknown> = { limit: 50, order_by: "score" };
      if (stateFilter) params.state = [stateFilter];
      if (caseSliceFilter === "__overdue__") {
        params.overdue_only = true;
        delete params.state;
      }
      if (caseSliceFilter === "__unassigned__") {
        params.unassigned_only = true;
        delete params.state;
      }
      if (caseSliceFilter === "__open__") params.state = OPEN_CASE_STATES;
      if (caseSliceFilter === "__confirmed__") params.decision = ["CONFIRMED_ACTION", "CONFIRMED_ACCEPTED"];
      if (caseSliceFilter === "__recurrence__") params.recurrence_only = true;
      if (["__confirmed__", "__recurrence__"].includes(caseSliceFilter)) delete params.state;
      const page = await listAnomalyCases(params);
      if (request !== listRequest.current) return;
      setCases(page.items);
      setTotal(page.total);
    } catch (err) {
      if (request === listRequest.current) setError(err instanceof Error ? err.message : "Não foi possível listar os casos");
    } finally {
      if (request === listRequest.current) setBusy(false);
    }
  }, [stateFilter, caseSliceFilter]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  useEffect(() => {
    if (!detectionRunning) return;
    let active = true;
    let pending = false;
    const timer = window.setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const page = await listAnomalyRuns(1);
        if (!active) return;
        const run = page.items[0] ?? null;
        setLatestRun(run);
        if (run && run.status !== "QUEUED" && run.status !== "RUNNING") {
          await Promise.all([loadAll(), loadCases()]);
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Não foi possível acompanhar a detecção");
      } finally {
        pending = false;
      }
    }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [detectionRunning, loadAll, loadCases]);

  useEffect(() => {
    if (tab === 3) void loadExpected();
    if (tab === 4) void loadQuality();
    if (tab === 5) {
      void listAnomalyCases({ limit: 200, order_by: "score", state: ["CLOSED"] })
        .then((page) => setRetrospectiveItems(page.items))
        .catch(() => setRetrospectiveItems([]));
    }
    if (tab === 6) {
      void listAnomalyCases({ limit: 200, order_by: "score", decision: ["CONFIRMED_ACTION", "CONFIRMED_ACCEPTED"] })
        .then((page) => setConfirmedItems(page.items))
        .catch(() => setConfirmedItems([]));
    }
  }, [tab, loadExpected, loadQuality]);

  const openCasesFilter = (filter: "overdue" | "unassigned" | "open" | "closed" | "new" | "confirmed" | "recurrence") => {
    setTab(2);
    const state = filter === "closed" ? "CLOSED" : filter === "new" ? "NEW" : "";
    const slice = ({
      overdue: "__overdue__",
      unassigned: "__unassigned__",
      open: "__open__",
      confirmed: "__confirmed__",
      recurrence: "__recurrence__",
    } as Partial<Record<typeof filter, string>>)[filter] ?? "";
    setStateFilter(state);
    setCaseSliceFilter(slice);
  };

  const setCasesView = (state: string) => {
    setTab(2);
    setStateFilter(state);
    setCaseSliceFilter("");
  };

  const [openCaseId, setOpenCaseId] = useState<number | null>(null);
  const openCase = async (id: number) => { setOpenCaseId(id); };

  // Link direto para um caso (?caso=<id>), usado pelo resumo de custos.
  const requestedCase = Number(searchParams.get("caso"));
  useEffect(() => {
    if (Number.isInteger(requestedCase) && requestedCase > 0) void openCase(requestedCase);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedCase]);


  const toggleEnabled = async () => {
    if (!config) return;
    setBusy(true);
    try {
      await updateAnomalyConfig(config.id, { enabled: !config.enabled });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível atualizar a configuração");
    } finally {
      setBusy(false);
    }
  };

  const runNow = async () => {
    if (!config) return;
    setBusy(true);
    try {
      await enqueueAnomalyRun(config.id);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível enfileirar a detecção");
    } finally {
      setBusy(false);
    }
  };

  const retrospectiveCases = useMemo(() => retrospectiveItems, [retrospectiveItems]);

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, maxWidth: 1600, mx: "auto", width: "100%", minWidth: 0, boxSizing: "border-box" }}>
      <Box sx={{ display: "flex", alignItems: { xs: "stretch", md: "center" }, justifyContent: "space-between", gap: 2, mb: 2, flexWrap: "wrap", minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="h5">Anomalias de custo em nuvem</Typography>
          {busy && <CircularProgress size={18} />}
        </Stack>
        <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
          <Button size="small" startIcon={<RefreshIcon />}
                  onClick={() => { void loadAll(); void loadCases(); void loadExpected(); void loadQuality(); }}>
            Atualizar
          </Button>
          {config && (
            <>
              <Chip size="small" color={config.enabled ? "success" : "default"}
                    label={config.enabled ? "Ativa" : "Inativa"} />
              <Button size="small" onClick={toggleEnabled}>{config.enabled ? "Desativar" : "Ativar"}</Button>
              <Button size="small" variant="contained" startIcon={<PlayArrowIcon />} onClick={runNow} disabled={!config.enabled || detectionRunning || busy}>
                Executar detecção
              </Button>
            </>
          )}
        </Stack>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError(null)}>{dataDisplayText(error)}</Alert>}
      {tab === 0 && summary && (
        <Box>
          {impactSummary && <Paper variant="outlined" sx={{ p: 2, mb: 2, borderColor: "success.light", background: "linear-gradient(110deg,#f5fbf8,#fff)" }}>
            <Typography variant="subtitle2">Impacto financeiro dos casos acompanhados</Typography>
            <Typography variant="caption" color="text.secondary">Atualizado com todos os episódios registrados · saving é projeção para casos corrigidos, não economia realizada auditada.</Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mt: 1.5 }}>
              {[
                { label: "Excesso já incorrido", value: money(impactSummary.incurred_excess_cost, currency), note: "Soma estimada dos dias fora da faixa" },
                { label: "Economia projetada / mês", value: money(impactSummary.projected_saving_monthly, currency), note: "Casos corrigidos sozinhos ou com ação" },
                { label: "Economia projetada / ano", value: money(impactSummary.projected_saving_annual, currency), note: "Projeção anual do ritmo recente" },
                { label: "Exposição em aberto / mês", value: money(impactSummary.open_exposure_monthly, currency), note: "Variações em aberto, aceitas ou recorrentes" },
                { label: "Exposição em aberto / ano", value: money(impactSummary.open_exposure_annual, currency), note: "Projeção anual da exposição" },
              ].map((item) => <Paper key={item.label} variant="outlined" sx={{ p: 1.25, minWidth: 180, flex: "1 1 180px", cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" } }} onClick={() => openCasesFilter(item.label.includes("Saving") ? "confirmed" : "open")}>
                <Typography variant="caption" color="text.secondary">{item.label}</Typography>
                <Typography variant="h6">{item.value}</Typography>
                <Typography variant="caption" color="text.secondary">{item.note}</Typography>
              </Paper>)}
            </Stack>
          </Paper>}
          <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 2 }}>
            {[
              { label: "Casos abertos agora", value: summary.new },
              { label: "Em aberto", value: summary.open },
              { label: "Atrasados", value: summary.overdue },
              { label: "Encerrados", value: summary.closed },
              { label: "Sem dono", value: summary.unassigned },
              { label: "Gasto anômalo observado", value: money(summary.confirmed_anomalous_cost_cloud ?? summary.anomalous_cost_cloud, currency) },
              { label: "Tempo até ciência (h)", value: summary.median_acknowledgement_hours ?? "—" },
              { label: "Tempo até resolução (h)", value: summary.median_resolution_hours ?? "—" },
              { label: "Classificados", value: summary.classified },
              { label: "Famílias recorrentes", value: summary.recurrence_families },
            ].map((item) => (
              <Paper
                key={item.label}
                variant="outlined"
                sx={{ p: 1.5, minWidth: 150, cursor: "pointer", transition: "all .15s", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover", transform: "translateY(-1px)" } }}
                onClick={() => {
                  if (item.label === "Em aberto") openCasesFilter("open");
                  else if (item.label === "Encerrados") openCasesFilter("closed");
                  else if (item.label === "Casos abertos agora") openCasesFilter("new");
                  else if (item.label === "Atrasados") openCasesFilter("overdue");
                  else if (item.label === "Sem dono") openCasesFilter("unassigned");
                  else if (item.label === "Famílias recorrentes") openCasesFilter("recurrence");
                  else if (item.label === "Classificados") openCasesFilter("confirmed");
                  else setCasesView("");
                }}
              >
                <Typography variant="caption" color="text.secondary">{item.label}</Typography>
                <Typography variant="h6">{item.value}</Typography>
                {item.label === "Gasto anômalo observado" && <Typography variant="caption" color="text.secondary">Validadas · {summary.event_period_start ?? "sem início"} a {summary.event_period_end ?? "sem dados"}</Typography>}
                {item.label === "Tempo até ciência (h)" && <Typography variant="caption" color="text.secondary">Mediana dos casos já aceitos</Typography>}
                {item.label === "Tempo até resolução (h)" && <Typography variant="caption" color="text.secondary">Mediana dos casos concluídos</Typography>}
                {item.label === "Atrasados" && (
                  <Typography variant="caption" color="text.secondary">Prazo de ciência sem validação</Typography>
                )}
              </Paper>
            ))}
          </Stack>
          <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>Distribuição por situação</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {Object.entries(summary.by_state).map(([state, count]) => (
                <Chip key={state} label={`${STATE_LABEL[state] ?? state}: ${count}`} size="small" clickable onClick={() => setCasesView(state)} />
              ))}
              {Object.keys(summary.by_state).length === 0 && (
                <Typography variant="body2" color="text.secondary">Nenhum caso registrado até agora.</Typography>
              )}
            </Stack>
            <Divider sx={{ my: 1.5 }} />
            <Typography variant="subtitle2" gutterBottom>Decisões</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {Object.entries(summary.decisions).map(([decision, count]) => (
                <Chip key={decision} size="small" clickable onClick={() => openCasesFilter("confirmed")}
                      label={`${DECISION_LABEL[decision] ?? decision}: ${count}`} />
              ))}
            </Stack>
            <Alert severity="info" sx={{ mt: 2 }}>
              {dataDisplayText(summary.notes.join(" · "))}
            </Alert>
          </Paper>
        </Box>
      )}

      {tab === 1 && <AnomaliesImpactTab currency={currency} />}

      {tab === 2 && (
        <Box>
          <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
            <Typography variant="body2" color="text.secondary">{total} caso(s)</Typography>
            {(caseSliceFilter || stateFilter) && <Chip
              size="small"
              label={`Visão: ${caseSliceFilter ? (CASE_SLICE_LABEL[caseSliceFilter] ?? caseSliceFilter) : STATE_LABEL[stateFilter] ?? stateFilter}`}
              onDelete={() => { setCaseSliceFilter(""); setStateFilter(""); }}
            />}
          </Stack>
          <Stack spacing={1} sx={{ display: { xs: "flex", md: "none" } }}>
            {cases.map((item) => (
              <Paper key={item.id} variant="outlined" onClick={() => void openCase(item.id)}
                     sx={{ p: 1.5, borderRadius: 2, cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" } }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                  <Typography variant="subtitle2" fontWeight={700}>{item.event_date} · {STATE_LABEL[item.state] ?? item.state}</Typography>
                  <Chip size="small" color={SEVERITY_COLOR[item.severity] ?? "default"} label={item.severity} />
                </Stack>
                <Typography variant="body2" sx={{ mt: 0.75, overflowWrap: "anywhere" }}>
                  {item.series_dimensions?.skuname || item.series_dimensions?.sku_name || item.series_dimensions?.sku || item.series_dimensions?.skumeter || item.series_dimensions?.servicesubcategory || item.series_dimensions?.servicename || "—"}
                </Typography>
                <Box sx={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 1, mt: 1.25 }}>
                  <Box><Typography variant="caption" color="text.secondary">Observado</Typography><Typography variant="body2" fontWeight={650}>{money(item.observed_cloud, currency)}</Typography></Box>
                  <Box><Typography variant="caption" color="text.secondary">Esperado</Typography><Typography variant="body2" fontWeight={650}>{money(item.expected_cloud, currency)}</Typography></Box>
                  <Box><Typography variant="caption" color="text.secondary">Gasto anômalo</Typography><Typography variant="body2" fontWeight={700} color="error.main">{money(item.anomalous_cost_cloud, currency)}</Typography></Box>
                  <Box><Typography variant="caption" color="text.secondary">Projeção por mês</Typography><Typography variant="body2" fontWeight={650}>{money(item.projected_month_impact, currency)}</Typography></Box>
                </Box>
              </Paper>
            ))}
            {cases.length === 0 && <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="body2" color="text.secondary">Nenhum caso corresponde aos filtros atuais.</Typography></Paper>}
          </Stack>
          <Box sx={{ display: { xs: "none", md: "block" } }}>
            <AnomaliesCasesDataGrid cases={cases} currency={currency} onOpen={(id) => void openCase(id)} />
          </Box>
        </Box>
      )}

      {tab === 6 && (
        <Box>
          <Typography variant="subtitle2" gutterBottom>Anomalias validadas</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Casos classificados como “confirmado com ação” ou “confirmado aceito”. Eventos esperados e casos sem decisão ficam fora desta lista.</Typography>
          <Typography variant="caption" color="text.secondary">{confirmedItems.length} caso(s) · clique em uma linha para abrir a apuração.</Typography>
          <Box sx={{ mt: 1 }}><AnomaliesCasesDataGrid cases={confirmedItems} currency={currency} onOpen={(id) => void openCase(id)} /></Box>
        </Box>
      )}

      {tab === 7 && config && (
        <AnomaliesSettingsTab config={config} onSaved={() => void loadAll()} busy={busy} setBusy={setBusy} />
      )}

      {tab === 3 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Eventos previstos reconhecidos (permanecem na tabela analítica, sem fluxo de tratamento)
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {expectedEvents.total} avaliação(ões) de evento previsto na publicação vigente
            {expectedEvents.note ? ` · ${expectedEvents.note}` : ""}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Família (recorrência)</TableCell>
                <TableCell align="right">Ocorrências</TableCell>
                <TableCell align="right">Séries</TableCell>
                <TableCell align="right">Esperado</TableCell>
                <TableCell align="right">Observado</TableCell>
                <TableCell align="right">Variação</TableCell>
                <TableCell>Meses</TableCell>
                <TableCell>Confiança</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {expectedEvents.items.map((item) => (
                <TableRow key={item.family}>
                  <TableCell>{item.family}</TableCell>
                  <TableCell align="right">{item.occurrences}</TableCell>
                  <TableCell align="right">{item.series_count}</TableCell>
                  <TableCell align="right">{money(item.expected_cloud, currency)}</TableCell>
                  <TableCell align="right">{money(item.observed_cloud, currency)}</TableCell>
                  <TableCell align="right">{money(item.delta_cloud, currency)}</TableCell>
                  <TableCell>{item.months.join(", ")}</TableCell>
                  <TableCell>{item.confidence.join(", ") || "—"}</TableCell>
                </TableRow>
              ))}
              {expectedEvents.items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8}>
                    <Typography variant="body2" color="text.secondary">
                      Nenhum evento previsto publicado.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {tab === 4 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Qualidade do modelo</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Dados parciais, referências reconstruídas, tolerância explícita e ausência de coleta.
            Dado parcial nunca valida recuperação nem encerra caso.
            {qualityNote ? ` ${qualityNote}` : ""}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Código</TableCell>
                <TableCell align="right">Avaliações</TableCell>
                <TableCell align="right">Gasto anômalo</TableCell>
                <TableCell>Períodos</TableCell>
                <TableCell>Estado</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {quality.map((bucket) => (
                <TableRow key={bucket.reason_code}>
                  <TableCell>{bucket.reason_code}</TableCell>
                  <TableCell align="right">{bucket.count}</TableCell>
                  <TableCell align="right">{money(bucket.anomalous_cost_cloud, currency)}</TableCell>
                  <TableCell>{bucket.periods.join(", ") || "—"}</TableCell>
                  <TableCell>{bucket.statuses.join(", ") || "—"}</TableCell>
                </TableRow>
              ))}
              {quality.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5}>
                    <Typography variant="body2" color="text.secondary">
                      Nenhuma limitação registrada nas avaliações publicadas.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {tab === 5 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Retrospectiva</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Casos encerrados para consulta retrospectiva. Clique em uma linha para ver os detalhes e o histórico da investigação.
          </Typography>
          <AnomaliesCasesDataGrid cases={retrospectiveCases} currency={currency} onOpen={openCase} />
        </Paper>
      )}

      <AnomalyCaseDrawer caseId={openCaseId} onClose={() => setOpenCaseId(null)} onChanged={() => { void loadCases(); void loadAll(); }} config={config} />

    </Box>
  );
}
