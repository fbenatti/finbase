import { useConfirmation } from "../../context/ConfirmationContext";
import { Dialog, DialogTitle, DialogContent, DialogActions } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState } from "react";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { Box, Typography, Paper, Grid, Alert, CircularProgress, LinearProgress, Button, Checkbox, ListItemText, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Chip, Tooltip, TextField, FormControl, InputLabel, Select, MenuItem, IconButton, Stack } from "@mui/material";
import { RefreshOutlined, AddOutlined, DeleteOutlined, EditOutlined } from "@mui/icons-material";
import Plot from "@/components/Plot";
// Wrapper: typings do react-plotly são rígidos para títulos/textos customizados.
function Chart(props: any) {
  return <Plot {...props} />;
}
import {
  getShutdownSavings, listShutdownExceptions,
  saveShutdownException, deleteShutdownException, createExceptionFromDelta, getScheduleDelta, getTagDelta, getOpportunity, getSavingsMonthly, getOpportunitySubscriptions,
  getPadroes, getPadroesPeriodos, getPadroesRecursos, getSavingOrganograma, getVmDailyUsage, PadroesResponse, PadraoRecursosResponse, SavingOrganogramaResponse, VmDailyUsageResponse,
  SavingsFilter, VmMonthSaving, ShutdownExceptionRule, ScheduleDeltaResponse, TagDeltaResponse, OpportunityResponse, SavingsMonthlyRow, OpportunitySubscription,
  formatApiError,
} from "../../services/api";
import { colors } from "../../theme/tokens";

const PURPLE = colors.brandDark, GREEN = "#10b981", RED = "#ef4444", BLUE = colors.info, ORANGE = "#ff7a00";
const COMPACT_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });
const FULL_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const COMPACT_NUM = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });

function money(v: unknown) { return COMPACT_BRL.format(Number(v ?? 0)); }
function moneyFull(v: unknown) { return FULL_BRL.format(Number(v ?? 0)); }
function num(v: unknown) { return COMPACT_NUM.format(Number(v ?? 0)); }
function pct(v: unknown) { return `${Number(v ?? 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`; }
function esc(s: unknown) { return String(s ?? "Sem classificação"); }
function duration(v: unknown) {
  const minutes = Math.max(0, Math.round(Number(v ?? 0) * 60));
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (!hours) return `${remainingMinutes} min`;
  return remainingMinutes ? `${hours} h ${remainingMinutes} min` : `${hours} h`;
}

function monthLabel(m: string) {
  const t = String(m ?? "");
  const match = t.match(/^(\d{4})(\d{2})$/);
  if (!match) return t;
  const months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
  return `${months[Number(match[2]) - 1]}/${match[1].slice(2)}`;
}

// Lógica usada para classificar cada VM num padrão (dimensionada sobre o
// histórico mensal de horas faturadas — BilledCost/h por hora on-demand).
const PADRAO_EXPLICACAO: Record<string, string> = {
  DESLIGUE_PROGRAMADO:
    "Horas faturadas estáveis entre 100–450h/mês (≈15h/dia em dias úteis) com desvio padrão < 80h, ≥5 dias materiais (≥4h/dia) nos últimos 14 dias do mês, uptime <75% na janela e no mês, histórico ≥3 meses e ciclo de vida estável. Confirma desligue recorrente via billing.",
  CICLO_CURTO:
    "Menos de 5 dias relevantes (≥4h faturadas) nos últimos 14 dias do mês — consumo efêmero ou VM que nasceu/morreu no período, sem padrão de desligue recorrente.",
  AKS_GERENCIADO:
    "Nó de AKS gerenciado (resource group MC_AKS*) — não é candidato a desligue isolado de VM.",
  LIGADA_24x7:
    "Uptime ≥75% na janela diária de 14 dias ou no mês (ou média mensal >700h) — VM praticamente sempre ligada, sem padrão de desligue.",
  VM_DESLIGADA:
    "Média de horas faturadas < 50h/mês com desvio padrão < 50h — a VM fica praticamente sempre desligada (não é um desligue pontual, parece abandonada ou desativada).",
  USO_IRREGULAR:
    "Desvio padrão de horas > 150h/mês — as horas variam muito de um mês para outro (uso esporádico/manual, sem padrão de schedule).",
  POUCO_HISTORICO:
    "Menos de 3 meses de histórico (60+ dias) ou ciclo de vida recente (nascimento/morte na janela) — ainda não é possível afirmar um padrão de desligue.",
  ANALISAR:
    "Não se enquadra nos padrões acima — horas entre os limiares ou comportamento misto. Requer revisão manual.",
  SEM_PADRAO: "Sem classificação definida.",
};

export default function DesligueProgramado({ embedded }: { embedded?: boolean }) {
  const confirm = useConfirmation();
  const [searchParams] = useSearchParams();
  const tab = ({ dashboard: 0, governance: 1, exceptions: 2 } as Record<string, number>)[searchParams.get("subview") || "dashboard"] ?? 0;
  const [data, setData] = useState<VmMonthSaving[]>([]);
  const [periods, setPeriods] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [exceptions, setExceptions] = useState<ShutdownExceptionRule[]>([]);
  const [exDialogOpen, setExDialogOpen] = useState(false);
  const [editingEx, setEditingEx] = useState<ShutdownExceptionRule | null>(null);
  const [delta, setDelta] = useState<ScheduleDeltaResponse | null>(null);
  const [tagDelta, setTagDelta] = useState<TagDeltaResponse | null>(null);
  const [opp, setOpp] = useState<OpportunityResponse | null>(null);
  const [oppSubs, setOppSubs] = useState<OpportunitySubscription[]>([]);
  const [oppSelectedSubs, setOppSelectedSubs] = useState<string[]>([]);
  const [oppTypes, setOppTypes] = useState<string[]>(["VM", "VMSS"]);
  const [oppLoading, setOppLoading] = useState(false);
  const [monthly, setMonthly] = useState<SavingsMonthlyRow[]>([]);
  const [padroes, setPadroes] = useState<PadroesResponse | null>(null);
  const [padraoPeriod, setPadraoPeriod] = useState<string>("");
  const [padroesPeriodos, setPadroesPeriodos] = useState<string[]>([]);
  const [savingDept, setSavingDept] = useState<SavingOrganogramaResponse | null>(null);
  const [padraoSelected, setPadraoSelected] = useState<string | null>(null);
  const [padraoRecursos, setPadraoRecursos] = useState<PadraoRecursosResponse | null>(null);
  const [padraoRecursosLoading, setPadraoRecursosLoading] = useState(false);
  const [usageDialog, setUsageDialog] = useState<{ resource: string; data: VmDailyUsageResponse | null; loading: boolean; error: string } | null>(null);

  const [filter, setFilter] = useState<SavingsFilter>({});
  const [refreshTick, setRefreshTick] = useState(0);
  const [summary, setSummary] = useState<{ total_saving_brl: number; total_saved_hours: number; vm_count: number; new_vm_count: number; vm_with_shutdown: number } | null>(null);

  useEffect(() => {
    setLoading(true);
    setError("");
    getShutdownSavings(filter)
      .then((res) => {
        setData(res.rows); setPeriods(res.billing_periods);
        setSummary({ total_saving_brl: res.total_saving_brl, total_saved_hours: res.total_saved_hours, vm_count: res.vm_count, new_vm_count: res.new_vm_count, vm_with_shutdown: res.vm_with_shutdown });
      })
      .catch((e) => setError(formatApiError(e, "Não foi possível carregar o saving")))
      .finally(() => setLoading(false));
  }, [filter, refreshTick]);

  useEffect(() => {
    loadExceptions();
  }, []);

  useEffect(() => {
    getScheduleDelta().then(setDelta).catch(() => setDelta(null));
    // tag-delta desativado: a tag shutdown=auto não existe no billing (regra
    // snapshot_tags_automacao foi desativada) — não consultar 823k linhas.
    setTagDelta(null);
    getSavingsMonthly().then(setMonthly).catch(() => setMonthly([]));
    getPadroesPeriodos().then((ps) => setPadroesPeriodos(ps.map(p => p.period))).catch(() => setPadroesPeriodos([]));
    getPadroes(padraoPeriod || undefined).then((res) => {
      setPadroes(res);
      setPadraoSelected(null);
      setPadraoRecursos(null);
    }).catch(() => setPadroes(null));
    getSavingOrganograma(padraoPeriod || undefined).then(setSavingDept).catch(() => setSavingDept(null));
  }, [refreshTick, padraoPeriod]);

  const loadOpportunity = () => {
    setOppLoading(true);
    getOpportunity({ subscriptions: oppSelectedSubs, resource_groups: oppTypes })
      .then(setOpp)
      .catch(() => setOpp(null))
      .finally(() => setOppLoading(false));
  };

  // Carrega as subscriptions e pré-seleciona as com hml/dev/hmp.
  useEffect(() => {
    getOpportunitySubscriptions()
      .then((res) => {
        setOppSubs(res.subscriptions);
        const preset = res.subscriptions.filter(s => /hml|dev|hmp/i.test(s.name)).map(s => s.name);
        setOppSelectedSubs(preset);
      })
      .catch(() => setOppSubs([]));
  }, [refreshTick]);

  const loadExceptions = () => {
    listShutdownExceptions().then(setExceptions).catch(() => setExceptions([]));
  };

  const totals = useMemo(() => {
    // ECONOMIA ESTIMADA e HORAS usam o total ACUMULADO do gráfico mensal
    // (todos os meses da Gold) para bater com as barras.
    const monthlySaving = monthly.reduce((s, r) => s + r.saving_brl, 0);
    const monthlyHours = monthly.reduce((s, r) => s + r.saved_hours, 0);
    const totalExceptionCost = data.reduce((s, r) => s + (r.exception_cost_brl || 0), 0);
    const automated = data.filter(r => r.has_automation).length;
    return {
      totalSaving: monthly.length ? monthlySaving : (summary?.total_saving_brl ?? data.reduce((s, r) => s + r.saving_brl, 0)),
      totalSavedHours: monthly.length ? monthlyHours : (summary?.total_saved_hours ?? data.reduce((s, r) => s + r.saved_hours, 0)),
      totalExceptionCost,
      vmCount: summary?.vm_with_shutdown ?? new Set(data.filter(r => r.saved_hours > 0).map(r => r.resource_id).filter(Boolean)).size,
      automated,
    };
  }, [data, summary, monthly]);

  const topVMs = useMemo(() => {
    return [...data].sort((a, b) => b.saving_brl - a.saving_brl).slice(0, 10);
  }, [data]);

  const byPeriod = useMemo(() => {
    const map = new Map<string, number>();
    data.forEach(r => { map.set(r.billing_period, (map.get(r.billing_period) || 0) + r.saving_brl); });
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [data]);

  const openNewException = () => {
    setEditingEx({ name: "", scope_type: "resource", is_active: true });
    setExDialogOpen(true);
  };

  const saveException = async (rule: ShutdownExceptionRule) => {
    await saveShutdownException(rule);
    setExDialogOpen(false);
    setEditingEx(null);
    loadExceptions();
  };

  const removeException = async (id: number) => {
    if (!(await confirm("Remover regra de exceção?", { title: "Excluir exceção?", confirmLabel: "Excluir", danger: true }))) return;
    await deleteShutdownException(id);
    loadExceptions();
  };

  const selectPadrao = (p: string) => {
    const period = padroes?.billing_period || "";
    setPadraoSelected(p);
    setPadraoRecursosLoading(true);
    setPadraoRecursos(null);
    getPadroesRecursos(period, p)
      .then(setPadraoRecursos)
      .catch(() => setPadraoRecursos(null))
      .finally(() => setPadraoRecursosLoading(false));
  };

  const registerDeltaException = async (r: { resource_id?: string | null; vm?: string | null; connection_code?: string | null; subscription_id?: string | null }) => {
    if (!r.resource_id) return;
    if (!(await confirm("Registrar este recurso como exceção (saiu do schedule de desligue)?", { title: "Criar exceção", confirmLabel: "Criar exceção" }))) return;
    try {
      await createExceptionFromDelta({
        resource_id: r.resource_id,
        resource_name: r.vm || undefined,
        connection_code: r.connection_code || undefined,
        subscription_name: r.subscription_id || undefined,
        reason: "Saiu da programação de desligamento",
      });
      loadExceptions();
      setRefreshTick(t => t + 1);
    } catch (e) {
      setError(formatApiError(e, "Não foi possível registrar a exceção"));
    }
  };

  const openUsageDialog = (resource: string) => {
    setUsageDialog({ resource, data: null, loading: true, error: "" });
    getVmDailyUsage(resource, 30)
      .then((data) => setUsageDialog({ resource, data, loading: false, error: "" }))
      .catch((e) => setUsageDialog({ resource, data: null, loading: false, error: formatApiError(e, "Não foi possível carregar o consumo") }));
  };

  // Mês realizado (fechado) = anterior ao atual. O dialog de consumo sempre
  // exibe o realizado — ex.: em ago/2026 mostra julho/2026.
  const realizadoPeriod = (() => {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth() + 1; // 1-12
    const pm = m === 1 ? 12 : m - 1;
    const py = m === 1 ? y - 1 : y;
    return `${py}${String(pm).padStart(2, "0")}`;
  })();

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}

      {tab === 0 && (
        <>
          <Grid container spacing={1.5} mb={2}>
            <Grid item xs={6} md={2.4}>
              <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>ECONOMIA ESTIMADA</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: PURPLE }}>{money(totals.totalSaving)}</Typography>
                <Typography variant="caption">{moneyFull(totals.totalSaving)}{monthly.length ? ` · acumulado ${monthLabel(monthly[0].billing_period)}–${monthLabel(monthly[monthly.length - 1].billing_period)}` : ""}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={2.4}>
              <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>HORAS EVITADAS</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: GREEN }}>{num(totals.totalSavedHours)} h</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={2.4}>
              <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>VMs ÚNICAS COM DESLIGAMENTO</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: BLUE }}>{totals.vmCount}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={2.4}>
              <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>COM AUTOMAÇÃO ATIVA</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: ORANGE }}>{totals.automated}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={2.4}>
              <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>CUSTO EM EXCEÇÃO</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: RED }}>{money(totals.totalExceptionCost)}</Typography>
                <Typography variant="caption">{moneyFull(totals.totalExceptionCost)}</Typography>
              </Paper>
            </Grid>
          </Grid>

          <Grid container spacing={2}>
            <Grid item xs={12} md={12}>
              <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
                <Stack direction="row" spacing={2} alignItems="center" mb={1}>
                  <Typography variant="subtitle2" fontWeight={900}>
                    Padrões identificados {padroes?.billing_period ? `· ${monthLabel(padroes.billing_period)}` : ""}
                  </Typography>
                  <FormControl size="small" sx={{ minWidth: 140 }}>
                    <InputLabel>Competência</InputLabel>
                    <Select
                      value={padraoPeriod}
                      label="Competência"
                      onChange={(e) => setPadraoPeriod(e.target.value)}
                    >
                      <MenuItem value="">Último mês disponível</MenuItem>
                      {padroesPeriodos.map((p) => (
                        <MenuItem key={p} value={p}>{monthLabel(p)}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Stack>
                <Alert severity="info" sx={{ mb: 1.5, py: 0.5 }} icon={false}>
                  VMs efêmeras (<strong>CICLO_CURTO</strong>, <strong>AKS (gerenciado)</strong>) e{" "}
                  <strong>ligadas 24x7 (sem parada)</strong> não aparecem nesta lista. Os totais de saving consideram apenas{" "}
                  <strong>desligamento confirmado</strong> — escopo de subscriptions configurável na Origem base consolidada da regra de consolidação.
                </Alert>
                {!padroes || padroes.rows.length === 0 ? (
                  <Typography variant="caption" color="text.secondary">Sem padrões. Atualize os dados em Dados e operação → Materialização de modelos.</Typography>
                ) : (
                  <>
                    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
                      {padroes.rows.map((p) => (
                        <Tooltip key={p.padrao} title={PADRAO_EXPLICACAO[p.padrao] || PADRAO_EXPLICACAO.SEM_PADRAO} arrow placement="top">
                          <Box
                            onClick={() => selectPadrao(p.padrao)}
                            sx={{
                              border: p.desligue_programado ? `1.5px solid ${GREEN}` : "1px solid #E1E8F0",
                              borderRadius: 2, px: 1.5, py: 1, bgcolor: p.desligue_programado ? "#f0fdf4" : colors.raised,
                              cursor: "pointer",
                              outline: padraoSelected === p.padrao ? `2px solid ${PURPLE}` : "none",
                              "&:hover": { boxShadow: 1 },
                            }}
                          >
                            <Typography variant="caption" sx={{ fontWeight: 900, color: p.desligue_programado ? GREEN : colors.textSecondary }}>
                              {p.padrao.replace(/_/g, " ")}
                            </Typography>
                            <Typography variant="body2" sx={{ fontWeight: 950 }}>{num(p.resources)} VMs</Typography>
                            <Typography variant="caption" sx={{ color: colors.textSecondary }}>{num(p.saved_hours)}h · {money(p.saving_brl)}</Typography>
                          </Box>
                        </Tooltip>
                      ))}
                    </Box>
                    {padraoSelected && (
                      <Box mt={1.5}>
                        <Typography variant="subtitle1" fontWeight={700} sx={{ color: PURPLE, mb: 1 }}>
                          Recursos · {padraoSelected.replace(/_/g, " ")} · {padraoRecursos?.rows.length ?? 0} VMs
                        </Typography>
                        {padraoRecursosLoading ? (
                          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 0.5 }}>
                            <CircularProgress size={16} /> <Typography variant="caption">Buscando…</Typography>
                          </Box>
                        ) : padraoRecursos && padraoRecursos.rows.length > 0 ? (
                          <TableContainer>
                            <Table size="small">
                              <TableHead>
                                <TableRow>
                                  <TableCell />
                                  <TableCell>Recurso</TableCell>
                                  <TableCell align="right">Saving (BRL)</TableCell>
                                </TableRow>
                              </TableHead>
                              <TableBody>
                                {padraoRecursos.rows.map((r) => (
                                  <TableRow key={r.resource_name} hover sx={{ cursor: "pointer" }} onClick={() => openUsageDialog(r.resource_name)}>
                                    <TableCell>
                                      <IconButton size="small" onClick={(e) => { e.stopPropagation(); openUsageDialog(r.resource_name); }} aria-label={`Consumo de ${r.resource_name}`}>
                                        <AddOutlined fontSize="small" />
                                      </IconButton>
                                    </TableCell>
                                    <TableCell>
                                      <Typography variant="body2" fontWeight={600} sx={{ color: BLUE, textDecoration: "underline" }}>{esc(r.resource_name)}</Typography>
                                    </TableCell>
                                    <TableCell align="right" sx={{ fontWeight: 600 }}>{money(r.saving_brl)}</TableCell>
                                  </TableRow>
                                ))}
                              </TableBody>
                            </Table>
                          </TableContainer>
                        ) : (
                          <Typography variant="caption" color="text.secondary">Nenhum recurso nesse padrão.</Typography>
                        )}
                      </Box>
                    )}
                  </>
                )}
              </Paper>
            </Grid>
            <Grid item xs={12} md={6}>
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={900} mb={1}>Saving apurado por mês (barras)</Typography>
                {monthly.length === 0 ? (
                  <Typography variant="caption" color="text.secondary">Sem dados disponíveis.</Typography>
                ) : (
                  <Chart
                    data={[{
                      type: "bar",
                      x: monthly.map(r => monthLabel(r.billing_period)),
                      y: monthly.map(r => r.saving_brl),
                      marker: { color: PURPLE },
                      text: monthly.map(r => money(r.saving_brl)),
                      textposition: "outside",
                      hovertemplate: "%{x}<br>%{y:,.2f}<extra></extra>",
                    }]}
                    layout={{
                      height: 300,
                      margin: { l: 60, r: 16, t: 8, b: 40 },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      font: { family: "Inter, system-ui, sans-serif", size: 12, color: colors.text },
                      xaxis: { title: "Mês", gridcolor: colors.border },
                      yaxis: { title: "Saving (BRL)", gridcolor: colors.border, tickformat: ".2s" },
                    }}
                    config={{ displayModeBar: false }}
                    style={{ width: "100%" }}
                  />
                )}
              </Paper>
            </Grid>
            <Grid item xs={12} md={6}>
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={900} mb={1}>VMs com maior saving</Typography>
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>VM</TableCell>
                        <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Saving apurado</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {topVMs.map((r, i) => (
                        <TableRow key={i} hover>
                          <TableCell sx={{ fontSize: 12 }}>{esc(r.resource_name || r.resource_id)}</TableCell>
                          <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{money(r.saving_brl)}</TableCell>
                        </TableRow>
                      ))}
                      {!topVMs.length && <TableRow><TableCell colSpan={2} align="center" sx={{ color: "text.secondary" }}>Sem dados disponíveis.</TableCell></TableRow>}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Paper>
            </Grid>
            <Grid item xs={12} md={12}>
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={900} mb={1}>
                  Saving por Departamento {savingDept?.billing_period ? `· ${monthLabel(savingDept.billing_period)}` : ""}
                </Typography>
                {!savingDept || savingDept.rows.length === 0 ? (
                  <Typography variant="caption" color="text.secondary">Sem dados disponíveis.</Typography>
                ) : (
                  <Chart
                    data={[{
                      type: "bar",
                      orientation: "h",
                      y: savingDept.rows.slice(0, 10).map(r => r.organograma_nivel_4),
                      x: savingDept.rows.slice(0, 10).map(r => r.saving_brl),
                      marker: { color: PURPLE },
                      hovertemplate: "%{y}<br>%{x:,.2f} · %{customdata}<extra></extra>",
                      customdata: savingDept.rows.slice(0, 10).map(r => `${r.organograma_nivel_5} · ${r.vms} VMs`),
                    }]}
                    layout={{
                      height: Math.max(240, Math.min(savingDept.rows.slice(0, 10).length, 10) * 40),
                      margin: { l: 200, r: 20, t: 8, b: 40 },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      font: { family: "Inter, system-ui, sans-serif", size: 12, color: colors.text },
                      xaxis: { title: "Saving (BRL)", gridcolor: colors.border, tickformat: ".2s", zeroline: false },
                      yaxis: { gridcolor: colors.border, automargin: true, zeroline: false },
                      showlegend: false,
                    }}
                    config={{ displayModeBar: false }}
                    style={{ width: "100%" }}
                  />
                )}
              </Paper>
            </Grid>
          </Grid>
        </>
      )}

      {tab === 1 && (
        <>
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
            <Typography variant="subtitle2" fontWeight={900}>
              Governança do Desligue — schedules
              {delta?.latest_snapshot ? ` (${delta.latest_snapshot}${delta.previous_snapshot ? ` vs ${delta.previous_snapshot}` : " — inventário, aguardando 2ª coleta"})` : ""}
            </Typography>
            <Button size="small" startIcon={<RefreshOutlined />} onClick={() => setRefreshTick(t => t + 1)}>Recarregar dados</Button>
          </Box>
          <Grid container spacing={1.5} mb={2}>
            <Grid item xs={6} md={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>SAÍRAM DA PROGRAMAÇÃO</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: RED }}>{delta?.total_removed ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>DESABILITADAS</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: "#f59e0b" }}>{delta?.total_disabled ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>ENTRARAM NA PROGRAMAÇÃO</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: GREEN }}>{delta?.total_added ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={6} md={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>CUSTO DESDE A PARADA</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: RED }}>{money(delta?.total_cost_since_removal ?? 0)}</Typography>
              </Paper>
            </Grid>
          </Grid>
          {!delta || !delta.rows.length ? (
            <Typography color="text.secondary" variant="body2">
              Nenhum schedule coletado ainda. Rode a coleta do Azure Automation (MODELO AUTOMATION SCHEDULE) para ver o inventário; com 2 coletas o delta D vs D-1 aparece.
            </Typography>
          ) : (
            <TableContainer sx={{ maxHeight: 480 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Programação / VM</TableCell>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Estado</TableCell>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Custo desde a saída</TableCell>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Exceção</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {delta.rows.filter(r => r.change_type !== "INALTERADO").map((r, i) => (
                    <TableRow key={i} hover>
                      <TableCell sx={{ fontSize: 12 }}>{r.schedule_name || r.vm || r.resource_id}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>
                        <Chip
                          size="small"
                          label={r.change_type === "ATUAL" ? (r.enabled ? "ATIVO" : "INATIVO") : r.change_type}
                          color={r.change_type === "REMOVIDO" ? "error" : r.change_type === "DESABILITADO" || (r.change_type === "ATUAL" && !r.enabled) ? "warning" : r.change_type === "ADICIONADO" ? "success" : "default"}
                        />
                      </TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{r.cost_since_removal ? money(r.cost_since_removal) : "—"}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>
                        {r.is_exception ? (
                          <Chip size="small" color="primary" label="Exceção gravada" />
                        ) : (r.change_type === "REMOVIDO" || r.change_type === "DESABILITADO" || (r.change_type === "ATUAL" && !r.enabled)) ? (
                          <Button
                            size="small"
                            variant="outlined"
                            color="error"
                            startIcon={<AddOutlined />}
                            onClick={() => registerDeltaException(r)}
                          >
                            Criar exceção
                          </Button>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                  {!delta.rows.some(r => r.change_type !== "INALTERADO") && (
                    <TableRow><TableCell colSpan={4} align="center" sx={{ color: "text.secondary" }}>Nenhuma mudança entre os inventários.</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>

        <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
          <Typography variant="subtitle2" fontWeight={900} mb={1}>

            Alterações nas tags de desligamento automático
            {tagDelta?.latest_snapshot ? ` — ${tagDelta.latest_snapshot}${tagDelta.previous_snapshot ? ` vs ${tagDelta.previous_snapshot}` : " (estado atual — aguardando 2ª coleta)"}` : ""}
          </Typography>
          {(tagDelta?.total_resources ?? 0) > 0 && (tagDelta?.total_with_tag ?? 0) === 0 && (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              A tag <b>shutdown=auto</b> não foi encontrada em nenhum recurso do billing FOCUS. A decisão de
              desligue é controlada por <b>schedules do Azure Automation</b> (aba Inventário/Governança), não por tags.
            </Alert>
          )}
          <Grid container spacing={1.5} mb={1.5}>
            <Grid item xs={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>RECURSOS NO INVENTÁRIO</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: BLUE }}>{tagDelta?.total_resources ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>COM shutdown=auto</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: GREEN }}>{tagDelta?.total_with_tag ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>DEIXARAM A AUTOMAÇÃO</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: RED }}>{tagDelta?.total_exited ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={3}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>CUSTO DESDE A PARADA</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: RED }}>{money(tagDelta?.total_cost_since_removal ?? 0)}</Typography>
              </Paper>
            </Grid>
          </Grid>
          {!tagDelta || !tagDelta.rows.length ? (
            <Typography color="text.secondary" variant="body2">
              {tagDelta?.previous_snapshot
                ? "Nenhuma mudança de tag entre os inventários."
                : "Ainda só existe 1 inventário — a comparação diária aparecerá após a 2ª coleta e consolidação dos dados. O estado atual mostra os recursos monitorados."}
            </Typography>
          ) : (
            <TableContainer sx={{ maxHeight: 360 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Recurso</TableCell>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Mudança</TableCell>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Custo desde a saída</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tagDelta.rows.map((r, i) => (
                    <TableRow key={i} hover>
                      <TableCell sx={{ fontSize: 12 }}>{r.resource_name || r.resource_id}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>
                        <Chip size="small" color={r.change_type === "SAIU_DA_AUTOMACAO" ? "error" : "success"} label={r.change_type} />
                      </TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{r.cost_since_removal ? money(r.cost_since_removal) : "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>

        <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1 }}>
            <Typography variant="subtitle2" fontWeight={900}>Oportunidade de saving — VMs/VMSS desligáveis</Typography>
            <Button size="small" variant="contained" startIcon={<RefreshOutlined />} onClick={loadOpportunity}>Buscar</Button>
          </Box>
          <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap", mb: 1.5, alignItems: "center" }}>
            <FormControl size="small" sx={{ minWidth: 380, maxWidth: 560 }}>
              <InputLabel>Assinaturas (pré-marcadas hml/dev/hmp)</InputLabel>
              <Select
                multiple
                value={oppSelectedSubs}
                label="Assinaturas (pré-marcadas hml/dev/hmp)"
                onChange={(e) => setOppSelectedSubs(e.target.value as string[])}
                renderValue={(sel) => `${(sel as string[]).length} subscription(s) selecionada(s)`}
                MenuProps={{ PaperProps: { style: { maxHeight: 320 } } }}
              >
                {oppSubs.map(s => (
                  <MenuItem key={s.name} value={s.name}>
                    <Checkbox checked={oppSelectedSubs.includes(s.name)} />
                    <ListItemText primary={s.name} secondary={`${s.resources} recursos`} />
                  </MenuItem>
                ))}
                {!oppSubs.length && <MenuItem disabled><em>Carregando assinaturas…</em></MenuItem>}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel>Tipo do recurso</InputLabel>
              <Select
                multiple
                value={oppTypes}
                label="Tipo do recurso"
                onChange={(e) => setOppTypes(e.target.value as string[])}
                renderValue={(sel) => (sel as string[]).join(", ") || "Todos"}
              >
                <MenuItem value="VM">VMs (virtualMachines)</MenuItem>
                <MenuItem value="VMSS">VMSS (virtualMachineScaleSets)</MenuItem>
              </Select>
            </FormControl>
          </Box>
          <Grid container spacing={1.5} mb={1.5}>
            <Grid item xs={4}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>OPORTUNIDADE NO TOTAL</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: PURPLE }}>{money(opp?.total_opportunity_brl ?? 0)}</Typography>
                <Typography variant="caption">{opp?.billing_period || ""}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={4}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>VMs</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: BLUE }}>{opp?.vm_count ?? 0}</Typography>
              </Paper>
            </Grid>
            <Grid item xs={4}>
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="caption" sx={{ color: colors.textSecondary, fontWeight: 900 }}>VMSS</Typography>
                <Typography variant="h6" sx={{ fontWeight: 950, color: ORANGE }}>{opp?.vmss_count ?? 0}</Typography>
              </Paper>
            </Grid>
          </Grid>
          {oppLoading ? (
            <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}><CircularProgress size={24} /></Box>
          ) : !opp || !opp.rows.length ? (
            <Typography color="text.secondary" variant="body2">

              Nenhum recurso encontrado com os filtros. Atualize os dados de economia para o mês corrente.
            </Typography>
          ) : (
            <TableContainer sx={{ maxHeight: 420 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Recurso</TableCell>
                    <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Oportunidade</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {opp.rows.map((r, i) => (
                    <TableRow key={i} hover>
                      <TableCell sx={{ fontSize: 12 }}>{r.resource_name || r.resource_id}</TableCell>
                      <TableCell sx={{ fontSize: 12, fontWeight: 900 }}>{money(r.opportunity_brl)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
        </>
      )}

      {tab === 2 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
            <Typography variant="subtitle2" fontWeight={900}>Exceções cadastradas</Typography>
            <Button size="small" variant="contained" startIcon={<AddOutlined />} onClick={openNewException}>Criar exceção</Button>
          </Box>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Nome da regra</TableCell>
                  <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Abrangência</TableCell>
                  <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Período de vigência</TableCell>
                  <TableCell sx={{ fontWeight: 900, fontSize: 11 }}>Em uso</TableCell>
                  <TableCell sx={{ fontWeight: 900, fontSize: 11 }}></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {exceptions.map((ex) => (
                  <TableRow key={ex.id} hover>
                    <TableCell sx={{ fontSize: 12 }}>{ex.name}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{ex.scope_type}: {esc(ex.scope_value || ex.tag_key)}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{esc(ex.valid_from)} → {esc(ex.valid_until)}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{ex.is_active ? "Sim" : "Não"}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>
                      <IconButton size="small" onClick={() => { setEditingEx(ex); setExDialogOpen(true); }}><EditOutlined fontSize="small" /></IconButton>
                      <IconButton size="small" color="error" onClick={() => ex.id && removeException(ex.id)}><DeleteOutlined fontSize="small" /></IconButton>
                    </TableCell>
                  </TableRow>
                ))}
                {!exceptions.length && <TableRow><TableCell colSpan={5} align="center" sx={{ color: "text.secondary" }}>Nenhuma exceção registrada.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      <ExceptionDialog open={exDialogOpen} rule={editingEx} onClose={() => setExDialogOpen(false)} onSave={saveException} />

      <Dialog open={usageDialog !== null} onClose={() => setUsageDialog(null)} maxWidth="lg" fullWidth>
        {usageDialog && (
          <>
            <DialogTitle>
              <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap">
                <Box>
                  <Typography variant="h6">Consumo diário — {usageDialog.resource}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {monthLabel(realizadoPeriod)} (efetivado) · horas faturadas da VM dia a dia (base consolidada FOCUS)
                  </Typography>
                </Box>
                <Button size="small" onClick={() => setUsageDialog(null)}>Fechar</Button>
              </Stack>
            </DialogTitle>
            <DialogContent dividers>
              {usageDialog.loading && <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />}
              {usageDialog.error && <Alert severity="error" sx={{ mb: 1.5 }}>{dataDisplayText(usageDialog.error)}</Alert>}
              {usageDialog.data?.is_recently_born && (
                <Alert severity="warning" sx={{ mb: 1.5 }}>
                  VM recém-aparecida na janela de 30 dias — classificada como <strong>POUCO_HISTORICO</strong> até estabilizar o ciclo de vida.
                </Alert>
              )}
              {usageDialog.data?.is_recently_died && !usageDialog.data?.is_recently_born && (
                <Alert severity="warning" sx={{ mb: 1.5 }}>
                  Último consumo faturado antes do fim da janela — pode indicar encerramento recente ou desligue prolongado no período.
                </Alert>
              )}
              {usageDialog.data && usageDialog.data.rows.length > 0 && (
                <>
                  {usageDialog.data.padrao && (
                    <Alert
                      severity={usageDialog.data.is_desligue_programado ? "success" : "info"}
                      sx={{ mb: 1.5 }}
                    >
                      Padrão: <strong>{usageDialog.data.padrao}</strong>
                      {" — "}
                      {PADRAO_EXPLICACAO[usageDialog.data.padrao] || PADRAO_EXPLICACAO.SEM_PADRAO}
                    </Alert>
                  )}
                  <Stack direction="row" spacing={3} sx={{ mb: 2 }} flexWrap="wrap">
                    <Box>
                      <Typography variant="caption" color="text.secondary">Horas faturadas (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{duration(usageDialog.data.total_hours)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Custo total (mês)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{money(usageDialog.data.total_cost_brl)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Dias relevantes (14d, ≥4h)</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>
                        {usageDialog.data.active_days ?? 0} / {usageDialog.data.min_active_days ?? 5} mín.
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Disponibilidade na janela de 14 dias</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{pct((usageDialog.data.window_uptime_pct ?? 0) * 100)}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Disponibilidade mensal</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>
                        {usageDialog.data.monthly_uptime_pct != null ? pct(usageDialog.data.monthly_uptime_pct * 100) : "—"}
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">Dias com uso</Typography>
                      <Typography variant="subtitle1" fontWeight={600}>{usageDialog.data.rows.length}</Typography>
                    </Box>
                  </Stack>
                  <Chart
                    data={[{
                      type: "bar",
                      name: "Perfil de horas do dia",
                      x: usageDialog.data.rows.map(r => r.day),
                      y: usageDialog.data.rows.map(r => r.hours),
                      customdata: usageDialog.data.rows.map(r => Math.round(r.hours * 60)),
                      marker: { color: PURPLE },
                      hovertemplate: "%{x}<br>%{y:.1f} h (%{customdata} min)<extra></extra>",
                    }]}
                    layout={{
                      autosize: true,
                      height: 380,
                      margin: { l: 70, r: 20, t: 40, b: 50 },
                      paper_bgcolor: "transparent",
                      plot_bgcolor: "transparent",
                      xaxis: { title: { text: "Dia" }, dtick: 2 },
                      yaxis: { title: { text: "Horas cobradas" }, gridcolor: colors.border, zeroline: false },
                      showlegend: true,
                      legend: { orientation: "h", y: -0.18 },
                    }}
                    config={{ displayModeBar: true, responsive: true }}
                    style={{ width: "100%" }}
                    useResizeHandler
                  />
                  <Box sx={{ mt: 3 }}>
                    <Alert severity="info" icon={false} sx={{ mb: 2, py: 0.5 }}>
                      Janela de validação: <strong>{usageDialog.data.validation_start} → {usageDialog.data.validation_end}</strong>.
                      Dias materiais = dias com ≥{usageDialog.data.min_active_hours_per_day ?? 4}h faturadas.
                      Desligue confirmado exige ≥{usageDialog.data.min_active_days ?? 5} dias materiais e uptime &lt;{pct((usageDialog.data.max_uptime_pct ?? 0.75) * 100)}.
                    </Alert>
                    {(() => {
                      const dias = usageDialog.data.rows.filter((r) => {
                        const vs = usageDialog.data?.validation_start;
                        const ve = usageDialog.data?.validation_end;
                        if (!vs || !ve) return true;
                        return r.day >= vs && r.day <= ve;
                      });
                      const minH = usageDialog.data.min_active_hours_per_day ?? 4;
                      const ligados = dias.filter(r => r.hours >= minH);
                      const desligados = dias.filter(r => r.hours < minH);
                      return (
                        <>
                          <Typography variant="subtitle1" fontWeight={700} sx={{ color: GREEN, mb: 1 }}>
                            Dias ligados (≥4h) · {ligados.length}
                          </Typography>
                          {ligados.length > 0 ? (
                            <TableContainer>
                              <Table size="small">
                                <TableHead>
                                  <TableRow>
                                    <TableCell>Dia</TableCell>
                                    <TableCell align="right">Horas cobradas</TableCell>
                                    <TableCell align="right">Custo (BRL)</TableCell>
                                    <TableCell align="right">Custo por hora</TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {ligados.map((r) => (
                                    <TableRow key={r.day} hover>
                                      <TableCell sx={{ fontWeight: 600 }}>{r.day}</TableCell>
                                      <TableCell align="right" sx={{ fontWeight: 600 }}>{duration(r.hours)}</TableCell>
                                      <TableCell align="right">{money(r.cost_brl)}</TableCell>
                                      <TableCell align="right">{r.hours > 0 ? money(r.cost_brl / r.hours) : "—"}</TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </TableContainer>
                          ) : (
                            <Typography variant="body2" color="text.secondary">Nenhum dia com uso ≥4h no mês.</Typography>
                          )}

                          <Typography variant="subtitle1" fontWeight={700} sx={{ color: RED, mt: 2, mb: 1 }}>
                            Dias desligados (&lt;4h) · {desligados.length}
                          </Typography>
                          {desligados.length > 0 ? (
                            <TableContainer>
                              <Table size="small">
                                <TableHead>
                                  <TableRow>
                                    <TableCell>Dia</TableCell>
                                    <TableCell align="right">Horas cobradas</TableCell>
                                    <TableCell align="right">Custo (BRL)</TableCell>
                                    <TableCell align="right">Custo por hora</TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {desligados.map((r) => {
                                    const ds = new Date(r.day + "T12:00:00").getDay();
                                    const weekend = ds === 0 || ds === 6;
                                    return (
                                      <TableRow key={r.day} hover>
                                        <TableCell sx={{ fontWeight: 600 }}>{r.day}{weekend ? " (fim de semana)" : ""}</TableCell>
                                        <TableCell align="right" sx={{ fontWeight: 600 }}>{duration(r.hours)}</TableCell>
                                        <TableCell align="right">{money(r.cost_brl)}</TableCell>
                                        <TableCell align="right">{r.hours > 0 ? money(r.cost_brl / r.hours) : "—"}</TableCell>
                                      </TableRow>
                                    );
                                  })}
                                </TableBody>
                              </Table>
                            </TableContainer>
                          ) : (
                            <Typography variant="body2" color="text.secondary">Nenhum dia desligado na competência.</Typography>
                          )}
                        </>
                      );
                    })()}
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                    Dias com horas &lt; 4h indicam VM desligada (sem consumo relevante). Quedas recorrentes no fim de semana/fora do
                    horário confirmam desligue programado ativo; dias com 24h ≈ ligada o dia inteiro.
                  </Typography>
                </>
              )}
              {usageDialog.data && usageDialog.data.rows.length === 0 && !usageDialog.error && (
                <Typography color="text.secondary" variant="body2">Sem consumo no mês efetivado ({monthLabel(realizadoPeriod)}).</Typography>
              )}
            </DialogContent>
          </>
        )}
      </Dialog>
    </Box>
  );
}

function ExceptionDialog({ open, rule, onClose, onSave }: {
  open: boolean; rule: ShutdownExceptionRule | null; onClose: () => void; onSave: (r: ShutdownExceptionRule) => void;
}) {
  const [form, setForm] = useState<ShutdownExceptionRule>(rule || { name: "", scope_type: "resource" });
  useEffect(() => { if (rule) setForm({ ...rule }); }, [rule]);
  if (!rule) return null;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{rule.id ? "Alterar exceção" : "Criar exceção"}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
          <TextField label="Nome da regra" value={form.name || ""} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          <TextField label="Finalidade" value={form.description || ""} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} multiline />
          <FormControl>
            <InputLabel>Tipo de abrangência</InputLabel>
            <Select value={form.scope_type} label="Tipo de abrangência" onChange={e => setForm(f => ({ ...f, scope_type: e.target.value }))}>
              <MenuItem value="resource">ResourceId</MenuItem>
              <MenuItem value="resource_group">Grupo de recursos</MenuItem>
              <MenuItem value="subscription">Assinatura</MenuItem>
              <MenuItem value="tag">Tag de recurso</MenuItem>
            </Select>
          </FormControl>
          {form.scope_type === "tag" ? (
            <>
              <TextField label="Chave da tag" value={form.tag_key || ""} onChange={e => setForm(f => ({ ...f, tag_key: e.target.value }))} />
              <TextField label="Valor da tag" value={form.tag_value || ""} onChange={e => setForm(f => ({ ...f, tag_value: e.target.value }))} />
            </>
          ) : (
            <TextField label="Valor da abrangência" value={form.scope_value || ""} onChange={e => setForm(f => ({ ...f, scope_value: e.target.value }))} />
          )}
          <TextField label="Vigência inicial (AAAAMM)" value={form.valid_from || ""} onChange={e => setForm(f => ({ ...f, valid_from: e.target.value }))} />
          <TextField label="Vigência final (AAAAMM)" value={form.valid_until || ""} onChange={e => setForm(f => ({ ...f, valid_until: e.target.value }))} />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" onClick={() => onSave(form)} disabled={!form.name}>Salvar</Button>
      </DialogActions>
    </Dialog>
  );
}
