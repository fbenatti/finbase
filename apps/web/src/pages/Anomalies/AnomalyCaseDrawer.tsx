/**
 * Painel lateral do caso de anomalia. Usado no portal de anomalias e no resumo
 * de custos: qualquer mudanca aqui vale para as duas telas.
 */
import { useEffect, useRef, useState } from "react";
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, LinearProgress,
  MenuItem, Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs,
  TextField, Tooltip, Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMoreOutlined";
import CloseIcon from "@mui/icons-material/CloseOutlined";
import AnomalyCaseScope from "./AnomalyCaseScope";
import type { AnomalyCase, AnomalyConfig, AnomalyNotification, AnomalySeries } from "../../services/anomaliesApi";
import {
  createAnomalyAction, getAnomalyCase, getAnomalyCaseEvents, getAnomalyCaseSeries,
  listAnomalyNotifications, readAnomalyNotification, transitionAnomalyCase, updateAnomalyAction,
} from "../../services/anomaliesApi";

export const STATE_LABEL: Record<string, string> = {
  NEW: "Novo",
  ASSIGNED: "Atribuído",
  INVESTIGATING: "Em apuração",
  DECIDED: "Decidido",
  IN_REMEDIATION: "Em correção",
  VERIFYING: "Em conferência",
  RETROSPECTIVE: "Retrospectiva",
  CLOSED: "Encerrado",
};


export const SEVERITY_COLOR: Record<string, "default" | "info" | "warning" | "error"> = {
  LOW: "default",
  MEDIUM: "info",
  HIGH: "warning",
  CRITICAL: "error",
};

export const DECISION_LABEL: Record<string, string> = {
  EXPECTED: "Esperado",
  DATA_ISSUE: "Falha na carga de dados",
  CONFIRMED_ACTION: "Validado — com ação",
  CONFIRMED_ACCEPTED: "Validado — aceito",
  LOW_PRIORITY_NO_ACTION: "Prioridade baixa, sem ação",
};

export const CAUSE_LABEL: Record<string, string> = {
  LEGITIMATE_GROWTH: "Crescimento esperado",
  EXPECTED_PURCHASE: "Compra ou renovação prevista",
  MISCONFIGURATION: "Configuração incorreta",
  FORGOTTEN_RESOURCE: "Recurso sem uso",
  PRICE_OR_DISCOUNT_CHANGE: "Mudança de tarifa ou desconto",
  DATA_ISSUE: "Falha na carga de dados",
  SECURITY_INCIDENT: "Suspeita de incidente de segurança",
  UNDETERMINED: "Causa não identificada",
};

export const dt = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("pt-BR") : "—";

export const daysBetween = (start: string | null | undefined, end: string | null | undefined) => {
  if (!start) return "—";
  const from = new Date(`${start.slice(0, 10)}T00:00:00Z`).getTime();
  const to = end ? new Date(`${end.slice(0, 10)}T00:00:00Z`).getTime() : Date.now();
  return `${Math.max(1, Math.floor((to - from) / 86_400_000) + 1)} dia(s)`;
};


export function StepPanel({ kase }: { kase: AnomalyCase }) {
  const steps = (kase.steps ?? []).filter((step) => "etapa" in step);
  const next = (kase.steps ?? []).find((step) => "proxima_acao" in step);
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>Andamento do caso</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Etapa</TableCell>
            <TableCell>Quando</TableCell>
            <TableCell>Dono</TableCell>
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
          <strong>Prazo para ciência:</strong> {dt(kase.due_at)}
        </Typography>
        <Typography variant="body2">
          <strong>Ação seguinte:</strong> {next ? String(next.proxima_acao) : "—"}
        </Typography>
        <Typography variant="body2">
          <strong>Dono:</strong> {kase.assignee_user_id ? `usuário ${kase.assignee_user_id}` : "Sem dono"}
        </Typography>
        <Typography variant="body2">
          <strong>Grau de confiança:</strong> {kase.confidence}
        </Typography>
        <Typography variant="body2">
          <strong>Qualidade da carga:</strong> {kase.data_quality}
        </Typography>
      </Stack>
    </Paper>
  );
}

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

export default function AnomalyCaseDrawer({ caseId, onClose, onChanged, config }: {
  caseId: number | null;
  onClose: () => void;
  onChanged?: () => void;
  config?: AnomalyConfig;
}) {
  const [selected, setSelected] = useState<AnomalyCase | null>(null);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [series, setSeries] = useState<AnomalySeries | null>(null);
  const caseRequest = useRef(0);
  const [caseNotifications, setCaseNotifications] = useState<AnomalyNotification[]>([]);
  const [caseTab, setCaseTab] = useState(0);
  const [form, setForm] = useState<TransitionForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currency = config?.currency ?? "BRL";
  const loadCases = async () => { onChanged?.(); };
  const loadAll = async () => { /* a tela de origem recarrega em onChanged */ };

  const openCase = async (id: number) => {
    const request = ++caseRequest.current;
    setBusy(true);
    setSeries(null);
    // Start the expensive Iceberg read immediately, alongside PostgreSQL detail.
    const seriesRequest = getAnomalyCaseSeries(id).catch((): AnomalySeries => ({
      items: [], note: "Série diária não disponível para este caso.",
    }));
    try {
      const [detail, eventPage] = await Promise.all([getAnomalyCase(id), getAnomalyCaseEvents(id)]);
      if (request !== caseRequest.current) return;
      setSelected(detail);
      setEvents(eventPage.items);
      setForm(EMPTY_FORM);
      void loadCaseNotifications(id);
      const loadedSeries = await seriesRequest;
      if (request === caseRequest.current) setSeries(loadedSeries);
    } catch (err) {
      if (request === caseRequest.current) setError(err instanceof Error ? err.message : "Não foi possível abrir o caso");
    } finally {
      if (request === caseRequest.current) setBusy(false);
    }
  };


  useEffect(() => {
    if (caseId) void openCase(caseId);
    else { caseRequest.current += 1; setSelected(null); setSeries(null); setCaseNotifications([]); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const closeCase = () => {
    onClose();
    caseRequest.current += 1;
    setSelected(null);
    setCaseNotifications([]);
    setSeries(null);
    setBusy(false);
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
      const message = err instanceof Error ? err.message : "Não foi possível executar o comando";
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
      setError(err instanceof Error ? err.message : "Não foi possível criar a ação");
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
      setError(err instanceof Error ? err.message : "Não foi possível concluir a ação");
    } finally {
      setBusy(false);
    }
  };

  /** Notificações do caso aberto. Carregadas só ao abrir o caso, não em segundo plano. */
  const loadCaseNotifications = async (caseId: number) => {
    try {
      const page = await listAnomalyNotifications({ limit: 25, case_id: caseId });
      setCaseNotifications(page.items.filter((item) => item.case_id === caseId));
    } catch {
      setCaseNotifications([]);
    }
  };

  const acknowledgeNotification = async (notification: AnomalyNotification) => {
    await readAnomalyNotification(notification.id, true);
    await loadCaseNotifications(notification.case_id);
  };

  return (
    <>
      <Drawer anchor="right" open={Boolean(selected)} onClose={closeCase}
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
                    label={`${({ LOW: "Baixa", MEDIUM: "Média", HIGH: "Alta", CRITICAL: "Crítica" } as Record<string, string>)[selected.severity] ?? selected.severity}`} />
              <Chip size="small" label={STATE_LABEL[selected.state] ?? selected.state} />
              <Box sx={{ flexGrow: 1 }} />
              <IconButton aria-label="Encerrar caso" onClick={closeCase}><CloseIcon /></IconButton>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {[selected.series_dimensions.cloud_origem, (series?.contributor_series_count ?? 1) > 1 ? `${series?.contributor_series_count} séries agregadas` : selected.series_dimensions.subaccountname, selected.series_dimensions.servicesubcategory ?? selected.series_dimensions.servicename].filter(Boolean).join(" · ")}
              {" · "}Cobrança em {new Date(`${selected.event_date}T00:00:00Z`).toLocaleDateString("pt-BR", { timeZone: "UTC" })}
            </Typography>
            <Tabs value={caseTab} onChange={(_, value: number) => setCaseTab(value)} sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}>
              <Tab label="Resumo" />
              <Tab label="Tratar" />
              <Tab label="Acompanhamento" />
            </Tabs>
            {busy && <LinearProgress sx={{ mb: 1 }} />}
            {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError(null)}>{error}</Alert>}

            {eventBeyondCutoff && <Alert severity="warning" sx={{ mb: 2 }}>
              Este evento é de {selected.event_date}, mas os dados disponíveis vão somente até {dataEnd} (atraso configurado da fonte: {config?.data_lag_days ?? 2} dia(s)). Ele ainda não pode ser confirmado como anomalia. A avaliação será feita quando a carga desse dia estiver disponível.
            </Alert>}
            {caseTab === 0 && <>
            {series ? <AnomalyCaseScope key={selected.id} kase={selected} series={series} currency={currency} />
              : <Box sx={{ py: 8, textAlign: "center" }}><CircularProgress size={28} /><Typography color="text.secondary" sx={{ mt: 1 }}>Carregando efetivado e Forecast…</Typography></Box>}

            <Accordion disableGutters sx={{ mt: 2 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>Critérios do detector e regras acionadas</AccordionSummary>
            <AccordionDetails>
            <Typography variant="body2" sx={{ mb: 1 }}>Prioridade: {selected.score.toFixed(0)}/100 · Grau de confiança: {selected.confidence} · Qualidade: {selected.data_quality}</Typography>
            {(selected.explanation?.length ? selected.explanation : []).map((check, index) => (
              <Typography key={index} variant="body2" sx={{ mb: 0.5 }}>
                {String(check.explicacao ?? check.criterio)}
              </Typography>
            ))}
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1 }}>
              {(!selected.explanation?.length ? selected.reason_codes : []).map((code) => (
                <Chip key={code} size="small" variant="outlined" label={code} />
              ))}
              {selected.applied_rule_refs.map((rule, index) => (
                <Tooltip key={index} title={rule.status}>
                  <Chip size="small" label={`regra: ${rule.name}`} />
                </Tooltip>
              ))}
            </Stack>
            </AccordionDetails></Accordion>
            </>}

            {caseTab === 1 && <>
            <Accordion disableGutters defaultExpanded={Boolean(selected.actions?.length)}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>Ações corretivas ({selected.actions?.length ?? 0})</AccordionSummary>
            <AccordionDetails>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Descrição da ação</TableCell>
                  <TableCell>Natureza</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Vencimento</TableCell>
                  <TableCell>Concluída em</TableCell>
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
                      <Typography variant="body2" color="text.secondary">Nenhuma ação corretiva registrada.</Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField size="small" label="Nova ação de correção" fullWidth
                         value={form.action_description}
                         onChange={(event) => setForm({ ...form, action_description: event.target.value })} />
              <Button variant="outlined" onClick={addAction}>Adicionar ação</Button>
            </Stack>
            </AccordionDetails></Accordion>

            <Divider sx={{ my: 2 }} />
            {caseNotifications.length > 0 && (
              <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
                <Typography variant="subtitle2" fontWeight={700}>Alertas enviados sobre este caso</Typography>
                {caseNotifications.map((notification) => (
                  <Stack key={notification.id} direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
                    <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                      <Typography variant="body2" noWrap>{notification.title}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {dt(notification.created_at)}{notification.is_finops_queue ? " · fila FinOps" : ""}
                      </Typography>
                    </Box>
                    {notification.read_at
                      ? <Chip size="small" variant="outlined" label="Ciente" />
                      : <Button size="small" variant="outlined" onClick={() => void acknowledgeNotification(notification)}>Marcar como lida</Button>}
                  </Stack>
                ))}
              </Paper>
            )}
            <Typography id="anomaly-investigation" variant="h6" gutterBottom sx={{ scrollMarginTop: 16 }}>Agir sobre esta variação</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Confirme com a área indicada se houve uma mudança planejada. Registre a investigação antes de decidir a ação.</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1 }}>
              <TextField select size="small" label="Próximo passo" value={form.command}
                         onChange={(event) => setForm({ ...form, command: event.target.value })}
                         sx={{ minWidth: 220 }}>
                {["assign", "acknowledge", "return_to_queue", "start_investigation", "update_investigation",
                  "decide", "redecide", "start_remediation", "cancel_remediation", "start_verification",
                  "fail_verification", "validate_result", "record_retrospective", "close", "reopen"]
                  .map((command) => <MenuItem key={command} value={command}>{({ assign: "Definir responsável", acknowledge: "Marcar como lida", return_to_queue: "Devolver para a fila FinOps", start_investigation: "Começar a investigação", update_investigation: "Anotar investigação", decide: "Anotar decisão", redecide: "Reavaliar decisão", start_remediation: "Começar a correção", cancel_remediation: "Desistir da correção", start_verification: "Checar o resultado", fail_verification: "Anotar falha na checagem", validate_result: "Checar resultado", record_retrospective: "Anotar aprendizado", close: "Concluir caso", reopen: "Reativar caso" } as Record<string, string>)[command]}</MenuItem>)}
              </TextField>
              {form.command === "assign" && (
                <TextField size="small" label="Dono (ID do usuário)" value={form.assignee_user_id}
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
                  <MenuItem value="SUCCESS">Variação eliminada</MenuItem>
                  <MenuItem value="CONFIRMED_ABSENCE">Ausência validada</MenuItem>
                </TextField>
              )}
            </Stack>
            <TextField size="small" fullWidth multiline minRows={2} label="Justificativa ou nota"
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
                  <TextField size="small" fullWidth label="Ação preventiva"
                             value={form.preventive_measure}
                             onChange={(event) => setForm({ ...form, preventive_measure: event.target.value })} />
                  <TextField size="small" fullWidth label="Justificativa da ausência"
                             value={form.no_preventive_measure_reason}
                             onChange={(event) => setForm({ ...form, no_preventive_measure_reason: event.target.value })} />
                </Stack>
              </>
            )}
            <Button variant="contained" onClick={submitCommand} disabled={busy}>
              Salvar ação
            </Button>

            </>}

            {caseTab === 2 && <>
            <Accordion disableGutters>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>Andamento e etapas do caso</AccordionSummary>
              <AccordionDetails><StepPanel kase={selected} /></AccordionDetails>
            </Accordion>

            <Accordion sx={{ mt: 2 }} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>Linha do tempo e auditoria</AccordionSummary>
            <AccordionDetails>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Quando</TableCell>
                  <TableCell>Ação registrada</TableCell>
                  <TableCell>Mudança (de → para)</TableCell>
                  <TableCell>Revisão</TableCell>
                  <TableCell>Observação</TableCell>
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
            </AccordionDetails></Accordion>
            </>}
              </>;
            })()}
          </Box>
        )}
      </Drawer>
    </>
  );
}
