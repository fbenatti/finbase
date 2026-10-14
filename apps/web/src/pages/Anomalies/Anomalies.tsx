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
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, LinearProgress,
  MenuItem, Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs,
  TextField, Tooltip, Typography,
} from "@mui/material";
import AnomalyEvolutionChart from "./AnomalyEvolutionChart";
import CloseIcon from "@mui/icons-material/Close";
import NotificationsIcon from "@mui/icons-material/Notifications";
import RefreshIcon from "@mui/icons-material/Refresh";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import type {
  AnomalyCase, AnomalyConfig, AnomalyNotification, AnomalySeries, CaseSummary,
  ExpectedEventFamily, QualityBucket,
} from "../../services/anomaliesApi";
import {
  createAnomalyAction, enqueueAnomalyRun, getAnomalyCase, getAnomalyCaseEvents,
  getAnomalyCaseSeries, getAnomalyMetrics, getExpectedEvents, getQuality,
  listAnomalyCases, listAnomalyConfigs, listAnomalyNotifications,
  readAnomalyNotification, transitionAnomalyCase, updateAnomalyAction,
  updateAnomalyConfig,
} from "../../services/anomaliesApi";

const STATE_LABEL: Record<string, string> = {
  NEW: "Novo",
  ASSIGNED: "Atribuído",
  INVESTIGATING: "Em investigação",
  DECIDED: "Decidido",
  IN_REMEDIATION: "Em remediação",
  VERIFYING: "Em verificação",
  RETROSPECTIVE: "Retrospectiva",
  CLOSED: "Encerrado",
};

const SEVERITY_COLOR: Record<string, "default" | "info" | "warning" | "error"> = {
  LOW: "default",
  MEDIUM: "info",
  HIGH: "warning",
  CRITICAL: "error",
};

const DECISION_LABEL: Record<string, string> = {
  EXPECTED: "Esperado",
  DATA_ISSUE: "Problema de dados",
  CONFIRMED_ACTION: "Confirmado — com ação",
  CONFIRMED_ACCEPTED: "Confirmado — aceito",
  LOW_PRIORITY_NO_ACTION: "Baixa prioridade, sem ação",
};

const CAUSE_LABEL: Record<string, string> = {
  LEGITIMATE_GROWTH: "Crescimento legítimo",
  EXPECTED_PURCHASE: "Compra/renovação esperada",
  MISCONFIGURATION: "Configuração indevida",
  FORGOTTEN_RESOURCE: "Recurso esquecido",
  PRICE_OR_DISCOUNT_CHANGE: "Mudança de preço/desconto",
  DATA_ISSUE: "Problema de dados",
  SECURITY_INCIDENT: "Possível incidente de segurança",
  UNDETERMINED: "Causa não determinada",
};

const money = (value: number | null | undefined, currency = "BRL") =>
  value === null || value === undefined
    ? "—"
    : value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 2 });

const dt = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("pt-BR") : "—";

function StepPanel({ kase }: { kase: AnomalyCase }) {
  const steps = (kase.steps ?? []).filter((step) => "etapa" in step);
  const next = (kase.steps ?? []).find((step) => "proxima_acao" in step);
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>Etapas do caso</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Etapa</TableCell>
            <TableCell>Quando</TableCell>
            <TableCell>Responsável</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {steps.map((step, index) => (
            <TableRow key={index}>
              <TableCell>{String(step.etapa)}</TableCell>
              <TableCell>{dt(step.quando as string | null)}</TableCell>
              <TableCell>
                {step.responsavel ? `usuário ${step.responsavel}` : "—"}
                {step.decisao ? ` · ${DECISION_LABEL[String(step.decisao)] ?? step.decisao}` : ""}
                {step.acoes_abertas ? ` · ${step.acoes_abertas} ação(ões) aberta(s)` : ""}
                {step.dias_validos ? ` · ${step.dias_validos} dia(s) válido(s)` : ""}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Divider sx={{ my: 1.5 }} />
      <Stack direction="row" spacing={2} flexWrap="wrap">
        <Typography variant="body2">
          <strong>Prazo de ciência:</strong> {dt(kase.due_at)}
        </Typography>
        <Typography variant="body2">
          <strong>Próxima ação:</strong> {next ? String(next.proxima_acao) : "—"}
        </Typography>
        <Typography variant="body2">
          <strong>Responsável:</strong> {kase.assignee_user_id ? `usuário ${kase.assignee_user_id}` : "fila FinOps"}
        </Typography>
        <Typography variant="body2">
          <strong>Confiança:</strong> {kase.confidence}
        </Typography>
        <Typography variant="body2">
          <strong>Qualidade:</strong> {kase.data_quality}
        </Typography>
      </Stack>
    </Paper>
  );
}

type ExpectedEventFamiliesResult = { items: ExpectedEventFamily[]; total: number; note?: string };

type TransitionForm = {
  command: string;
  decision: string;
  cause_category: string;
  note: string;
  evidence: string;
  verification_outcome: string;
  learning: string;
  preventive_measure: string;
  no_preventive_measure_reason: string;
  assignee_user_id: string;
  action_description: string;
};

const EMPTY_FORM: TransitionForm = {
  command: "acknowledge",
  decision: "CONFIRMED_ACCEPTED",
  cause_category: "UNDETERMINED",
  note: "",
  evidence: "",
  verification_outcome: "SUCCESS",
  learning: "",
  preventive_measure: "",
  no_preventive_measure_reason: "",
  assignee_user_id: "",
  action_description: "",
};

export default function Anomalies() {
  const [tab, setTab] = useState(0);
  const [configs, setConfigs] = useState<AnomalyConfig[]>([]);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [cases, setCases] = useState<AnomalyCase[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<AnomalyCase | null>(null);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [series, setSeries] = useState<AnomalySeries | null>(null);
  const [expectedEvents, setExpectedEvents] = useState<ExpectedEventFamiliesResult>({ items: [], total: 0 });
  const [quality, setQuality] = useState<QualityBucket[]>([]);
  const [qualityNote, setQualityNote] = useState<string | null>(null);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<AnomalyNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [form, setForm] = useState<TransitionForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stateFilter, setStateFilter] = useState<string>("");
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [queueOnly, setQueueOnly] = useState(false);
  const [dataLagDraft, setDataLagDraft] = useState("2");

  const config = configs[0];
  const currency = config?.currency ?? "BRL";

  useEffect(() => {
    if (config) setDataLagDraft(String(config.data_lag_days ?? 2));
  }, [config?.id, config?.version, config?.data_lag_days]);

  const loadAll = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [configList, metrics, notificationPage] = await Promise.all([
        listAnomalyConfigs(),
        getAnomalyMetrics(),
        listAnomalyNotifications({ limit: 25 }),
      ]);
      setConfigs(configList);
      setSummary(metrics);
      setNotifications(notificationPage.items);
      setUnread(notificationPage.unread);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao carregar anomalias");
    } finally {
      setBusy(false);
    }
  }, []);

  const loadExpected = useCallback(async () => {
    try {
      setExpectedEvents(await getExpectedEvents({ limit: 200 }));
      setRemoteError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Falha ao listar eventos esperados";
      setExpectedEvents({ items: [], total: 0, note: message.includes("409")
        ? "Nenhuma publicação corrente: execute a detecção para gerar avaliações."
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
      const message = err instanceof Error ? err.message : "Falha ao listar qualidade";
      setQuality([]);
      setQualityNote(message.includes("409")
        ? "Nenhuma publicação corrente: execute a detecção para gerar avaliações."
        : message);
    }
  }, []);

  const loadCases = useCallback(async () => {
    setBusy(true);
    try {
      const params: Record<string, unknown> = { limit: 50, order_by: "score" };
      if (stateFilter) params.state = [stateFilter];
      if (severityFilter) params.severity = [severityFilter];
      if (queueOnly) params.queue_only = true;
      const page = await listAnomalyCases(params);
      setCases(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao listar casos");
    } finally {
      setBusy(false);
    }
  }, [stateFilter, severityFilter, queueOnly]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  useEffect(() => {
    if (tab === 3) void loadExpected();
    if (tab === 4) void loadQuality();
  }, [tab, loadExpected, loadQuality]);

  const openCase = async (id: number) => {
    setBusy(true);
    try {
      const [detail, eventPage] = await Promise.all([getAnomalyCase(id), getAnomalyCaseEvents(id)]);
      setSelected(detail);
      setEvents(eventPage.items);
      setForm(EMPTY_FORM);
      setSeries(null);
      try {
        setSeries(await getAnomalyCaseSeries(id));
      } catch {
        setSeries({ items: [], note: "Série diária indisponível para este caso." });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao abrir o caso");
    } finally {
      setBusy(false);
    }
  };

  const submitCommand = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        command: form.command,
        case_version: selected.case_version,
      };
      if (form.note) payload.note = form.note;
      if (form.evidence) payload.evidence = form.evidence.split("\n").filter(Boolean);
      if (form.command === "decide" || form.command === "redecide") {
        payload.decision = form.decision;
        payload.cause_category = form.cause_category;
      }
      if (form.command === "assign") payload.assignee_user_id = Number(form.assignee_user_id);
      if (form.command === "validate_result") {
        payload.verification_outcome = form.verification_outcome;
        payload.verification_note = form.note || "validado";
      }
      if (form.command === "record_retrospective" || form.command === "close") {
        payload.learning = form.learning;
        if (form.preventive_measure) payload.preventive_measure = form.preventive_measure;
        else payload.no_preventive_measure_reason = form.no_preventive_measure_reason || "sem medida preventiva";
      }
      await transitionAnomalyCase(selected.id, payload);
      await openCase(selected.id);
      await loadCases();
      await loadAll();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Falha no comando";
      setError(message.includes("409") ? `Conflito de versão (409): ${message}` : message);
    } finally {
      setBusy(false);
    }
  };

  const addAction = async () => {
    if (!selected || !form.action_description) return;
    setBusy(true);
    try {
      await createAnomalyAction(selected.id, {
        description: form.action_description,
        action_type: "CORRECTIVE",
        mandatory: true,
      });
      await openCase(selected.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao criar ação");
    } finally {
      setBusy(false);
    }
  };

  const completeAction = async (actionId: number) => {
    if (!selected) return;
    setBusy(true);
    try {
      await updateAnomalyAction(actionId, { status: "COMPLETED" });
      await openCase(selected.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao concluir ação");
    } finally {
      setBusy(false);
    }
  };

  const readNotification = async (id: number) => {
    await readAnomalyNotification(id, true);
    const page = await listAnomalyNotifications({ limit: 25 });
    setNotifications(page.items);
    setUnread(page.unread);
  };

  const toggleEnabled = async () => {
    if (!config) return;
    setBusy(true);
    try {
      await updateAnomalyConfig(config.id, { enabled: !config.enabled });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao atualizar a configuração");
    } finally {
      setBusy(false);
    }
  };

  const saveDataLag = async () => {
    if (!config) return;
    const days = Number(dataLagDraft);
    if (!Number.isInteger(days) || days < 0 || days > 30) {
      setError("O atraso precisa ser um número inteiro entre 0 e 30 dias.");
      return;
    }
    setBusy(true);
    try {
      await updateAnomalyConfig(config.id, { data_lag_days: days });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao salvar atraso da fonte");
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
      setError(err instanceof Error ? err.message : "Falha ao enfileirar a detecção");
    } finally {
      setBusy(false);
    }
  };

  const retrospectiveCases = useMemo(
    () => cases.filter((item) => item.state === "RETROSPECTIVE" || item.state === "CLOSED"),
    [cases],
  );

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, maxWidth: 1600, mx: "auto", width: "100%", minWidth: 0, boxSizing: "border-box" }}>
      <Box sx={{ display: "flex", alignItems: { xs: "stretch", md: "center" }, justifyContent: "space-between", gap: 2, mb: 2, flexWrap: "wrap", minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="h5">Anomalias de custo cloud</Typography>
          {busy && <CircularProgress size={18} />}
        </Stack>
        <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
          <Tooltip title="Caixa de notificações">
            <IconButton onClick={() => setInboxOpen(true)} aria-label="Caixa de notificações">
              <NotificationsIcon />
              {unread > 0 && <Chip size="small" color="error" label={unread} sx={{ ml: -1, mt: -2 }} />}
            </IconButton>
          </Tooltip>
          <Button size="small" startIcon={<RefreshIcon />}
                  onClick={() => { void loadAll(); void loadCases(); void loadExpected(); void loadQuality(); }}>
            Atualizar
          </Button>
          {config && (
            <>
              <Chip size="small" color={config.enabled ? "success" : "default"}
                    label={config.enabled ? "Habilitada" : "Desabilitada"} />
              <Button size="small" onClick={toggleEnabled}>{config.enabled ? "Desabilitar" : "Habilitar"}</Button>
              <Button size="small" variant="contained" startIcon={<PlayArrowIcon />} onClick={runNow} disabled={!config.enabled}>
                Executar detecção
              </Button>
            </>
          )}
        </Stack>
      </Box>

      {config && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Medida <strong>{config.measure_column}</strong> · moeda <strong>{config.currency}</strong> ·
          perfil {config.profile} · quantil {(config.daily_quantile * 100).toFixed(1)}% · materialidade{" "}
          {money(config.materiality_by_currency?.[config.currency] ?? 100, config.currency)} · janela{" "}
          {config.history_days}d (calibração {config.calibration_days}d) · horizonte {config.horizon_days}d · atraso da fonte {config.data_lag_days}d
        </Typography>
      )}
      {config && <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
        <TextField size="small" type="number" label="Atraso da fonte (dias)" value={dataLagDraft}
          inputProps={{ min: 0, max: 30, step: 1 }} onChange={(event) => setDataLagDraft(event.target.value)} sx={{ width: 210 }} />
        <Button size="small" variant="outlined" onClick={saveDataLag} disabled={busy || Number(dataLagDraft) === config.data_lag_days}>Salvar atraso</Button>
        <Typography variant="caption" color="text.secondary">0 = até hoje; 2 = padrão atual D-2</Typography>
      </Stack>}
      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError(null)}>{error}</Alert>}

      <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}>
        <Tab label="Visão geral" />
        <Tab label="Meus casos / fila FinOps" />
        <Tab label="Casos" />
        <Tab label="Eventos esperados" />
        <Tab label="Qualidade" />
        <Tab label="Retrospectiva" />
      </Tabs>

      {tab === 0 && summary && (
        <Box>
          <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 2 }}>
            {[
              { label: "Casos novos", value: summary.new },
              { label: "Abertos", value: summary.open },
              { label: "Vencidos", value: summary.overdue },
              { label: "Encerrados", value: summary.closed },
              { label: "Sem responsável", value: summary.unassigned },
              { label: "Custo anômalo observado", value: money(summary.anomalous_cost_cloud, currency) },
              { label: "Tempo até ciência (h)", value: summary.median_acknowledgement_hours ?? "—" },
              { label: "Tempo até resolução (h)", value: summary.median_resolution_hours ?? "—" },
              { label: "Classificados", value: summary.classified },
              { label: "Famílias reincidentes", value: summary.recurrence_families },
            ].map((item) => (
              <Paper key={item.label} variant="outlined" sx={{ p: 1.5, minWidth: 150 }}>
                <Typography variant="caption" color="text.secondary">{item.label}</Typography>
                <Typography variant="h6">{item.value}</Typography>
              </Paper>
            ))}
          </Stack>
          <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
            <Typography variant="subtitle2" gutterBottom>Distribuição por estado</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {Object.entries(summary.by_state).map(([state, count]) => (
                <Chip key={state} label={`${STATE_LABEL[state] ?? state}: ${count}`} size="small" />
              ))}
              {Object.keys(summary.by_state).length === 0 && (
                <Typography variant="body2" color="text.secondary">Nenhum caso registrado ainda.</Typography>
              )}
            </Stack>
            <Divider sx={{ my: 1.5 }} />
            <Typography variant="subtitle2" gutterBottom>Decisões</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {Object.entries(summary.decisions).map(([decision, count]) => (
                <Chip key={decision} size="small"
                      label={`${DECISION_LABEL[decision] ?? decision}: ${count}`} />
              ))}
            </Stack>
            <Alert severity="info" sx={{ mt: 2 }}>
              {summary.notes.join(" · ")}
            </Alert>
          </Paper>
        </Box>
      )}

      {(tab === 1 || tab === 2) && (
        <Box>
          <Stack direction="row" spacing={2} sx={{ mb: 1 }} flexWrap="wrap">
            <TextField select size="small" label="Estado" value={stateFilter}
                       onChange={(event) => setStateFilter(event.target.value)} sx={{ minWidth: 170 }}>
              <MenuItem value="">Todos</MenuItem>
              {Object.keys(STATE_LABEL).map((state) => (
                <MenuItem key={state} value={state}>{STATE_LABEL[state]}</MenuItem>
              ))}
            </TextField>
            <TextField select size="small" label="Severidade" value={severityFilter}
                       onChange={(event) => setSeverityFilter(event.target.value)} sx={{ minWidth: 150 }}>
              <MenuItem value="">Todas</MenuItem>
              {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((severity) => (
                <MenuItem key={severity} value={severity}>{severity}</MenuItem>
              ))}
            </TextField>
            {tab === 1 && (
              <Button size="small" variant={queueOnly ? "contained" : "outlined"}
                      onClick={() => setQueueOnly((value) => !value)}>
                Somente fila FinOps
              </Button>
            )}
            <Typography variant="body2" sx={{ alignSelf: "center" }}>
              {total} caso(s)
            </Typography>
          </Stack>
          <Stack spacing={1} sx={{ display: { xs: "flex", md: "none" } }}>
            {cases.map((item) => (
              <Paper key={item.id} variant="outlined" onClick={() => void openCase(item.id)}
                     sx={{ p: 1.5, borderRadius: 2, cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" } }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                  <Typography variant="subtitle2" fontWeight={700}>Caso #{item.id} · nota {item.score.toFixed(0)}</Typography>
                  <Chip size="small" color={SEVERITY_COLOR[item.severity] ?? "default"} label={item.severity} />
                </Stack>
                <Typography variant="body2" sx={{ mt: 0.75, overflowWrap: "anywhere" }}>{item.event_family}</Typography>
                <Typography variant="caption" color="text.secondary">{item.event_date} · {STATE_LABEL[item.state] ?? item.state}</Typography>
                <Box sx={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 1, mt: 1.25 }}>
                  <Box><Typography variant="caption" color="text.secondary">Observado</Typography><Typography variant="body2" fontWeight={650}>{money(item.observed_cloud, currency)}</Typography></Box>
                  <Box><Typography variant="caption" color="text.secondary">Esperado</Typography><Typography variant="body2" fontWeight={650}>{money(item.expected_cloud, currency)}</Typography></Box>
                  <Box><Typography variant="caption" color="text.secondary">Custo anômalo</Typography><Typography variant="body2" fontWeight={700} color="error.main">{money(item.anomalous_cost_cloud, currency)}</Typography></Box>
                  <Box><Typography variant="caption" color="text.secondary">Responsável / prazo</Typography><Typography variant="body2">{item.assignee_user_id ?? "Fila FinOps"} · {dt(item.due_at)}</Typography></Box>
                </Box>
              </Paper>
            ))}
            {cases.length === 0 && <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="body2" color="text.secondary">Nenhum caso encontrado com os filtros atuais.</Typography></Paper>}
          </Stack>
          <Paper variant="outlined" sx={{ display: { xs: "none", md: "block" }, overflowX: "auto" }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>#</TableCell>
                  <TableCell>Nota</TableCell>
                  <TableCell>Severidade</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Evento</TableCell>
                  <TableCell>Data</TableCell>
                  <TableCell align="right">Observado</TableCell>
                  <TableCell align="right">Esperado</TableCell>
                  <TableCell align="right">Custo anômalo</TableCell>
                  <TableCell>Responsável</TableCell>
                  <TableCell>Prazo</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {cases.map((item) => (
                  <TableRow key={item.id} hover sx={{ cursor: "pointer" }}
                            onClick={() => void openCase(item.id)}>
                    <TableCell>{item.id}</TableCell>
                    <TableCell>{item.score.toFixed(0)}</TableCell>
                    <TableCell>
                      <Chip size="small" color={SEVERITY_COLOR[item.severity] ?? "default"}
                            label={item.severity} />
                    </TableCell>
                    <TableCell>{STATE_LABEL[item.state] ?? item.state}</TableCell>
                    <TableCell>{item.event_family}</TableCell>
                    <TableCell>{item.event_date}</TableCell>
                    <TableCell align="right">{money(item.observed_cloud, currency)}</TableCell>
                    <TableCell align="right">{money(item.expected_cloud, currency)}</TableCell>
                    <TableCell align="right">{money(item.anomalous_cost_cloud, currency)}</TableCell>
                    <TableCell>{item.assignee_user_id ?? "fila FinOps"}</TableCell>
                    <TableCell>{dt(item.due_at)}</TableCell>
                  </TableRow>
                ))}
                {cases.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={11}>
                      <Typography variant="body2" color="text.secondary">
                        Nenhum caso. Habilite a configuração e execute a detecção.
                      </Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Paper>
        </Box>
      )}

      {tab === 3 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Eventos esperados reconhecidos (permanecem na tabela analítica, sem workflow)
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {expectedEvents.total} avaliação(ões) de evento esperado na publicação corrente
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
                <TableCell align="right">Desvio</TableCell>
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
                      Nenhum evento esperado publicado.
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
          <Typography variant="subtitle2" gutterBottom>Qualidade dos dados</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Dados parciais, referências reconstruídas, tolerância explícita e ausência de coleta.
            Dado parcial nunca confirma recuperação nem encerra caso.
            {qualityNote ? ` ${qualityNote}` : ""}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Código</TableCell>
                <TableCell align="right">Avaliações</TableCell>
                <TableCell align="right">Custo anômalo</TableCell>
                <TableCell>Períodos</TableCell>
                <TableCell>Status</TableCell>
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
                      Nenhuma limitação declarada nas avaliações publicadas.
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
            Causas, decisões e aprendizados registrados. Não se atribui economia causal não comprovada.
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell>
                <TableCell>Causa</TableCell>
                <TableCell>Decisão</TableCell>
                <TableCell>Aprendizado</TableCell>
                <TableCell>Medida preventiva</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {retrospectiveCases.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>#{item.id}</TableCell>
                  <TableCell>{item.cause_category ? CAUSE_LABEL[item.cause_category] ?? item.cause_category : "—"}</TableCell>
                  <TableCell>{item.decision ? DECISION_LABEL[item.decision] ?? item.decision : "—"}</TableCell>
                  <TableCell>{item.learning ?? "—"}</TableCell>
                  <TableCell>{item.preventive_measure ?? item.no_preventive_measure_reason ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      <Drawer anchor="right" open={Boolean(selected)} onClose={() => setSelected(null)}
              PaperProps={{ sx: { width: "min(1100px, 96vw)", p: 2, top: { xs: 56, sm: 64 }, height: { xs: "calc(100dvh - 56px)", sm: "calc(100dvh - 64px)" }, overflowX: "hidden" } }}>
        {selected && (
          <Box>
            {(() => {
              const dataEnd = series?.analysis_data_end ?? series?.data_cutoff;
              const eventBeyondCutoff = Boolean(dataEnd && selected.event_date > dataEnd);
              return <>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>Caso #{selected.id}</Typography>
              <Chip size="small" color={SEVERITY_COLOR[selected.severity] ?? "default"}
                    label={`${({ LOW: "Baixa", MEDIUM: "Média", HIGH: "Alta", CRITICAL: "Crítica" } as Record<string, string>)[selected.severity] ?? selected.severity} · nota ${selected.score.toFixed(0)}`} />
              <Chip size="small" label={STATE_LABEL[selected.state] ?? selected.state} />
              <Chip size="small" label={`Confiança ${({ HIGH: "alta", MEDIUM: "média", LOW: "baixa", UNAVAILABLE: "indisponível" } as Record<string, string>)[selected.confidence] ?? selected.confidence}`} />
              <Box sx={{ flexGrow: 1 }} />
              <IconButton aria-label="Fechar caso" onClick={() => setSelected(null)}><CloseIcon /></IconButton>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {[selected.series_dimensions.cloud_origem, selected.series_dimensions.subaccountname, selected.series_dimensions.servicesubcategory ?? selected.series_dimensions.servicename].filter(Boolean).join(" · ")}
              {" · "}Cobrança em {new Date(`${selected.event_date}T00:00:00Z`).toLocaleDateString("pt-BR", { timeZone: "UTC" })}
            </Typography>
            {busy && <LinearProgress sx={{ mb: 1 }} />}

            {eventBeyondCutoff && <Alert severity="warning" sx={{ mb: 2 }}>
              Este evento é de {selected.event_date}, mas os dados disponíveis vão somente até {dataEnd} (atraso configurado da fonte: {config?.data_lag_days ?? 2} dia(s)). Ele ainda não pode ser confirmado como anomalia. A avaliação será feita quando a carga desse dia estiver disponível.
            </Alert>}
            {series ? <AnomalyEvolutionChart key={selected.id} series={series} kase={selected} currency={currency} />
              : <Box sx={{ py: 8, textAlign: "center" }}><CircularProgress size={28} /><Typography color="text.secondary" sx={{ mt: 1 }}>Carregando realizado e forecast…</Typography></Box>}
            <Divider sx={{ my: 2 }} />
            <StepPanel kase={selected} />

            <Divider sx={{ my: 2 }} />
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1 }}>
              {selected.reason_codes.map((code) => (
                <Chip key={code} size="small" variant="outlined" label={code} />
              ))}
              {selected.applied_rule_refs.map((rule, index) => (
                <Tooltip key={index} title={rule.status}>
                  <Chip size="small" label={`regra: ${rule.name}`} />
                </Tooltip>
              ))}
            </Stack>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Ações (remediação)</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Descrição</TableCell>
                  <TableCell>Tipo</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Prazo</TableCell>
                  <TableCell>Executada</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {(selected.actions ?? []).map((action) => (
                  <TableRow key={action.id}>
                    <TableCell>{action.description}</TableCell>
                    <TableCell>{action.action_type}</TableCell>
                    <TableCell>{action.status}</TableCell>
                    <TableCell>{dt(action.due_at)}</TableCell>
                    <TableCell>{dt(action.executed_at)}</TableCell>
                    <TableCell>
                      {action.status !== "COMPLETED" && (
                        <Button size="small" onClick={() => void completeAction(action.id)}>
                          Concluir
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {(selected.actions ?? []).length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6}>
                      <Typography variant="body2" color="text.secondary">Nenhuma ação cadastrada.</Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField size="small" label="Nova ação corretiva" fullWidth
                         value={form.action_description}
                         onChange={(event) => setForm({ ...form, action_description: event.target.value })} />
              <Button variant="outlined" onClick={addAction}>Adicionar</Button>
            </Stack>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Comando do workflow</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1 }}>
              <TextField select size="small" label="Comando" value={form.command}
                         onChange={(event) => setForm({ ...form, command: event.target.value })}
                         sx={{ minWidth: 220 }}>
                {["assign", "acknowledge", "return_to_queue", "start_investigation", "update_investigation",
                  "decide", "redecide", "start_remediation", "cancel_remediation", "start_verification",
                  "fail_verification", "validate_result", "record_retrospective", "close", "reopen"]
                  .map((command) => <MenuItem key={command} value={command}>{command}</MenuItem>)}
              </TextField>
              {form.command === "assign" && (
                <TextField size="small" label="Responsável (user id)" value={form.assignee_user_id}
                           onChange={(event) => setForm({ ...form, assignee_user_id: event.target.value })} />
              )}
              {(form.command === "decide" || form.command === "redecide") && (
                <>
                  <TextField select size="small" label="Decisão" value={form.decision}
                             onChange={(event) => setForm({ ...form, decision: event.target.value })}
                             sx={{ minWidth: 220 }}>
                    {Object.keys(DECISION_LABEL).map((decision) => (
                      <MenuItem key={decision} value={decision}>{DECISION_LABEL[decision]}</MenuItem>
                    ))}
                  </TextField>
                  <TextField select size="small" label="Causa" value={form.cause_category}
                             onChange={(event) => setForm({ ...form, cause_category: event.target.value })}
                             sx={{ minWidth: 220 }}>
                    {Object.keys(CAUSE_LABEL).map((cause) => (
                      <MenuItem key={cause} value={cause}>{CAUSE_LABEL[cause]}</MenuItem>
                    ))}
                  </TextField>
                </>
              )}
              {form.command === "validate_result" && (
                <TextField select size="small" label="Resultado" value={form.verification_outcome}
                           onChange={(event) => setForm({ ...form, verification_outcome: event.target.value })}>
                  <MenuItem value="SUCCESS">Sucesso</MenuItem>
                  <MenuItem value="CONFIRMED_ABSENCE">Ausência confirmada</MenuItem>
                </TextField>
              )}
            </Stack>
            <TextField size="small" fullWidth multiline minRows={2} label="Justificativa / nota"
                       value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })}
                       sx={{ mb: 1 }} />
            {(form.command === "decide" || form.command === "redecide") && (
              <TextField size="small" fullWidth multiline minRows={2} label="Evidências (uma por linha)"
                         value={form.evidence}
                         onChange={(event) => setForm({ ...form, evidence: event.target.value })}
                         sx={{ mb: 1 }} />
            )}
            {(form.command === "record_retrospective" || form.command === "close") && (
              <>
                <TextField size="small" fullWidth label="Aprendizado" value={form.learning}
                           onChange={(event) => setForm({ ...form, learning: event.target.value })}
                           sx={{ mb: 1 }} />
                <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                  <TextField size="small" fullWidth label="Medida preventiva"
                             value={form.preventive_measure}
                             onChange={(event) => setForm({ ...form, preventive_measure: event.target.value })} />
                  <TextField size="small" fullWidth label="Justificativa de ausência"
                             value={form.no_preventive_measure_reason}
                             onChange={(event) => setForm({ ...form, no_preventive_measure_reason: event.target.value })} />
                </Stack>
              </>
            )}
            <Button variant="contained" onClick={submitCommand} disabled={busy}>
              Aplicar comando
            </Button>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Histórico de eventos</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Quando</TableCell>
                  <TableCell>Evento</TableCell>
                  <TableCell>De → Para</TableCell>
                  <TableCell>Versão</TableCell>
                  <TableCell>Nota</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {events.map((event) => (
                  <TableRow key={String(event.id)}>
                    <TableCell>{dt(event.created_at as string)}</TableCell>
                    <TableCell>{String(event.event_type)}{event.command ? ` (${event.command})` : ""}</TableCell>
                    <TableCell>{String(event.from_state ?? "—")} → {String(event.to_state ?? "—")}</TableCell>
                    <TableCell>{String(event.case_version)}</TableCell>
                    <TableCell>{String(event.note ?? "—")}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
              </>;
            })()}
          </Box>
        )}
      </Drawer>

      <Drawer anchor="right" open={inboxOpen} onClose={() => setInboxOpen(false)}
              PaperProps={{ sx: { width: "min(520px, 96vw)", p: 2 } }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="h6">Notificações</Typography>
          <Chip size="small" color={unread ? "error" : "default"} label={`${unread} não lida(s)`} />
          <Box sx={{ flexGrow: 1 }} />
          <IconButton onClick={() => setInboxOpen(false)}><CloseIcon /></IconButton>
        </Stack>
        <Divider sx={{ my: 1 }} />
        {notifications.map((notification) => (
          <Paper key={notification.id} variant="outlined" sx={{ p: 1, mb: 1,
                  bgcolor: notification.read_at ? "transparent" : "action.hover" }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={notification.kind} />
              {notification.is_finops_queue && <Chip size="small" color="warning" label="fila FinOps" />}
              <Box sx={{ flexGrow: 1 }} />
              <Typography variant="caption">{dt(notification.created_at)}</Typography>
            </Stack>
            <Typography variant="body2" sx={{ mt: 0.5 }}>{notification.title}</Typography>
            {notification.body && (
              <Typography variant="caption" color="text.secondary">{notification.body}</Typography>
            )}
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
              <Button size="small" onClick={() => void readNotification(notification.id)}>
                Assumir / confirmar ciência
              </Button>
              <Button size="small" onClick={() => { setInboxOpen(false); void openCase(notification.case_id); }}>
                Abrir caso
              </Button>
            </Stack>
          </Paper>
        ))}
        {notifications.length === 0 && (
          <Typography variant="body2" color="text.secondary">Nenhuma notificação.</Typography>
        )}
      </Drawer>
    </Box>
  );
}
