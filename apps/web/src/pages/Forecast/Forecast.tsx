import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { Alert, Box, Button, CircularProgress, InputAdornment, Paper, Stack, TextField, Typography } from "@mui/material";
import { AddOutlined, RefreshOutlined, SearchOutlined } from "@mui/icons-material";
import {
  DestinationTable,
  ForecastBusinessRule,
  ForecastConfig,
  ForecastMetadata,
  ForecastRun,
  cancelForecastRun,
  createForecastRun,
  deleteForecastBusinessRule,
  getDestinationTables,
  getForecastBusinessRules,
  getForecastConfigs,
  getForecastMetadata,
  getForecastRunsGrid,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import ForecastHistoryDataGrid from "./ForecastHistoryDataGrid";
import ForecastAccuracy from "../ForecastAccuracy/ForecastAccuracy";
import ForecastConfigForm from "./ForecastConfigDialog";
import ForecastRuleForm from "./ForecastRuleDialog";
import { BEHAVIOR_LABELS } from "./forecastConstants";

type GridQuery = {
  offset: number;
  limit: number;
  sort?: { field: string; direction: "asc" | "desc" };
  filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
};

export default function Forecast() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // When opened at the Forecast section, show configuration first so profiles
  // and business rules are discoverable; the accuracy pages have direct links.
  const requestedTab = searchParams.get("tab");
  const tab = ["accuracy", "config", "rules", "history"].includes(requestedTab || "") ? requestedTab! : "config";
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [configs, setConfigs] = useState<ForecastConfig[]>([]);
  const profile = configs[0] ?? null;
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [runningConfigId, setRunningConfigId] = useState<number | null>(null);

  // ── Regras do único perfil ──
  const [rulesMeta, setRulesMeta] = useState<ForecastMetadata | null>(null);
  const [rules, setRules] = useState<ForecastBusinessRule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [ruleEditor, setRuleEditor] = useState<{ open: boolean; initial: ForecastBusinessRule | null }>({ open: false, initial: null });

  // ── Histórico (server-side grid) ──
  const [gridRows, setGridRows] = useState<ForecastRun[]>([]);
  const [gridTotal, setGridTotal] = useState(0);
  const [gridLoading, setGridLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [pollTick, setPollTick] = useState(0);
  const gridQueryRef = useRef<GridQuery>({ offset: 0, limit: 25, filters: [] });

  // ── Confirmações ──
  const [rulePendingDeletion, setRulePendingDeletion] = useState<ForecastBusinessRule | null>(null);
  const [cancelRunPending, setCancelRunPending] = useState<ForecastRun | null>(null);
  const [cancellingRunId, setCancellingRunId] = useState<number | null>(null);

  const load = async () => {
    try {
      const [t, c] = await Promise.all([getDestinationTables(true), getForecastConfigs()]);
      setTables(t);
      setConfigs(c);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const loadRules = async (config = profile) => {
    if (!config) {
      setRules([]);
      setRulesMeta(null);
      return;
    }
    setRulesLoading(true);
    try {
      const [items, metadata] = await Promise.all([
        getForecastBusinessRules(config.id),
        getForecastMetadata(config.destination_table_id),
      ]);
      setRules(items);
      setRulesMeta(metadata);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar as regras");
    } finally {
      setRulesLoading(false);
    }
  };

  useEffect(() => {
    if (tab === "rules") void loadRules(profile);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, profile?.id]);

  // ── History grid ──
  const loadRuns = async (query: GridQuery) => {
    setGridLoading(true);
    try {
      const data = await getForecastRunsGrid({ ...query, search: search.trim() || undefined });
      setGridRows(data.items);
      setGridTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar as execuções de Forecast");
    } finally {
      setGridLoading(false);
    }
  };

  const handleGridQuery = (query: GridQuery) => {
    gridQueryRef.current = query;
    void loadRuns(query);
  };

  useEffect(() => {
    if (tab !== "history") return;
    gridQueryRef.current = { offset: 0, limit: gridQueryRef.current.limit, filters: [] };
    void loadRuns(gridQueryRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => {
    if (tab !== "history") return;
    const timer = setInterval(() => setPollTick((t) => t + 1), 3000);
    return () => clearInterval(timer);
  }, [tab]);

  // ── Ações ──
  const runConfig = async (config: ForecastConfig) => {
    setRunningConfigId(config.id);
    setError("");
    try {
      const result = await createForecastRun(config.id);
      setMessage("Execução " + result.run_id + " entrou na fila para " + config.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível enfileirar o Forecast");
    } finally {
      setRunningConfigId(null);
    }
  };

  const openHistory = (run: ForecastRun, comparison: boolean) =>
    navigate("/projecoes/execucoes/" + run.id + (comparison ? "?comparison=1" : ""));

  const cancelRun = async (run: ForecastRun) => {
    setCancellingRunId(run.id);
    setError("");
    try {
      await cancelForecastRun(run.id);
      setMessage("Execução " + run.id + " foi cancelada");
      setCancelRunPending(null);
      await load();
      void loadRuns(gridQueryRef.current);
    } catch (e) {
      setError(String(e));
    } finally {
      setCancellingRunId(null);
    }
  };

  const removeRule = async () => {
    if (!profile || !rulePendingDeletion) return;
    try {
      await deleteForecastBusinessRule(rulePendingDeletion.id);
      setRulePendingDeletion(null);
      setMessage("Regra removida");
      await loadRules(profile);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <>
    <Box sx={{ minWidth: 0 }}>
      {/* Faixa de contexto branca */}
      <Box sx={{ mb: 3 }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h4" component="h1">{tab === "rules" ? "Regras de negócio do Forecast" : tab === "history" ? "Histórico do Forecast" : tab === "accuracy" ? "Precisão do Forecast" : "Perfis de Forecast"}</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Forecast de custos sobre as bases consolidadas, com regras de negócio e alocação por intervalo.
          </Typography>
        </Box>
      </Box>

      {/* Área de trabalho com campos simples de 40px */}
      <Box sx={{
        minWidth: 0,
        "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
        "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
      }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>
            {dataDisplayText(error)}
          </Alert>
        )}
        {message && (
          <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage("")}>
            {dataDisplayText(message)}
          </Alert>
        )}

        {tab === "config" && (
          <Stack spacing={2}>
            <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
              <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} sx={{ gap: 2 }}>
                <Box>
                  <Typography variant="h6">Perfil único de Forecast</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Este perfil concentra a base, as medidas e os parâmetros usados em todas as execuções.
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1} flexShrink={0}>
                  <Button size="small" startIcon={<RefreshOutlined />} onClick={() => void load()}>
                    Atualizar
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    disabled={!profile || runningConfigId !== null}
                    startIcon={runningConfigId !== null ? <CircularProgress size={16} /> : undefined}
                    onClick={() => profile && void runConfig(profile)}
                  >
                    Executar Forecast
                  </Button>
                </Stack>
              </Stack>
              <Box sx={{ display: "grid", gap: 1.5, mt: 2, gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
                {[
                  { label: "Perfil", value: profile?.name ?? "Não configurado" },
                  { label: "Base consolidada", value: profile?.table_name ?? tables.find((table) => table.id === profile?.destination_table_id)?.name ?? "—" },
                  { label: "Situação", value: runningConfigId !== null ? "Executando" : profile?.active ? "Ativo" : "Inativo" },
                ].map((item) => (
                  <Paper key={item.label} variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD", minWidth: 0 }}>
                    <Typography variant="caption" color="text.secondary">{item.label}</Typography>
                    <Typography variant="subtitle1" fontWeight={700} noWrap>{item.value}</Typography>
                  </Paper>
                ))}
              </Box>
            </Paper>
            {profile ? (
              <ForecastConfigForm
                key={profile.id}
                initial={profile}
                tables={tables}
                onSaved={async (savedName) => {
                  setError("");
                  setMessage(`Configuração "${savedName}" salva com sucesso.`);
                  await load();
                }}
              />
            ) : (
              <Alert severity="info">O perfil único ainda está sendo inicializado. Atualize a tela em alguns instantes.</Alert>
            )}
          </Stack>
        )}

        {tab === "rules" && (
          <Stack spacing={2}>
            <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
              <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} sx={{ gap: 2 }}>
                <Box>
                  <Typography variant="h6">Regras de negócio do Forecast</Typography>
                  <Typography variant="body2" color="text.secondary">
                    As regras abaixo são aplicadas ao único perfil: {profile?.name ?? "perfil não configurado"}.
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1} flexShrink={0}>
                  <Button size="small" startIcon={<RefreshOutlined />} disabled={!profile || rulesLoading} onClick={() => void loadRules(profile)}>
                    Atualizar
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={<AddOutlined />}
                    disabled={!profile || rulesLoading}
                    onClick={() => setRuleEditor({ open: true, initial: null })}
                  >
                    Nova regra
                  </Button>
                </Stack>
              </Stack>
              <Box sx={{ display: "grid", gap: 1.5, mt: 2, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
                {[
                  { label: "Regras cadastradas", value: rules.length },
                  { label: "Regras ativas", value: rules.filter((rule) => rule.active).length },
                  { label: "Perfil", value: profile?.name ?? "Não configurado" },
                ].map((item) => (
                  <Paper key={item.label} variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD", minWidth: 0 }}>
                    <Typography variant="caption" color="text.secondary">{item.label}</Typography>
                    <Typography variant="subtitle1" fontWeight={700} noWrap>{item.value}</Typography>
                  </Paper>
                ))}
              </Box>
            </Paper>

            {ruleEditor.open && profile && (
              <ForecastRuleForm
                key={ruleEditor.initial?.id ?? "new-rule"}
                open
                initial={ruleEditor.initial}
                config={profile}
                meta={rulesMeta}
                onClose={() => setRuleEditor({ open: false, initial: null })}
                onSaved={async (savedName) => {
                  setError("");
                  setMessage(`Regra "${savedName}" salva com sucesso.`);
                  await loadRules(profile);
                }}
              />
            )}

            {!profile ? (
              <Alert severity="info">O perfil único do Forecast ainda não foi carregado.</Alert>
            ) : rulesLoading ? (
              <Stack direction="row" alignItems="center" spacing={1} sx={{ py: 3 }}>
                <CircularProgress size={18} />
                <Typography variant="body2" color="text.secondary">Carregando regras...</Typography>
              </Stack>
            ) : rules.length === 0 ? (
              <EmptyState message="Nenhuma regra de negócio cadastrada para o perfil." />
            ) : (
              <Stack spacing={1.5}>
                {rules.map((rule) => {
                  const identity = rule.identification_clauses?.length
                    ? rule.identification_clauses.map((clause) => `${clause.column} em ${(clause.values || []).join(", ")}`).join(" OU ")
                    : rule.identification_column
                      ? `${rule.identification_column} = ${(rule.identification_values || []).join(", ") || "—"}`
                      : "—";
                  const scope = Object.entries(rule.scope || {}).map(([key, value]) => `${key}=${value}`).join(", ") || "Todas as séries";
                  const details = [
                    { label: "Identificação", value: identity },
                    { label: "Escopo", value: scope },
                    { label: "Tipo de cobrança", value: rule.charge_type || "—" },
                    { label: "Comportamento", value: BEHAVIOR_LABELS[rule.behavior] || rule.behavior },
                    { label: "Opções do comportamento", value: JSON.stringify(rule.behavior_options || {}) },
                    { label: "Coluna de valor", value: rule.value_column || "—" },
                    { label: "Coluna de referência", value: rule.reference_column || "—" },
                    { label: "Valores de referência", value: (rule.reference_values || []).join(", ") || "—" },
                    { label: "Vigência", value: `${rule.start_period?.length === 6 ? `${rule.start_period.slice(0, 4)}-${rule.start_period.slice(4)}` : rule.start_period} até ${rule.end_period ? (rule.end_period.length === 6 ? `${rule.end_period.slice(0, 4)}-${rule.end_period.slice(4)}` : rule.end_period) : "sem fim"}` },
                    { label: "Crescimento", value: `${(Number(rule.growth_rate || 0) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%` },
                    { label: "Valor substituto", value: rule.replacement_value == null ? "—" : String(rule.replacement_value) },
                    { label: "Cancelar em refund", value: rule.cancel_on_refund ? "Sim" : "Não" },
                    { label: "Prioridade", value: String(rule.priority) },
                    { label: "Versão", value: String(rule.version) },
                  ];
                  return (
                    <Paper key={rule.id} variant="outlined" sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2 }}>
                      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} spacing={1.5} sx={{ mb: 2 }}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Typography variant="subtitle1" fontWeight={700}>{rule.name}</Typography>
                          <Typography component="span" variant="caption" sx={{ color: rule.active ? "success.main" : "text.secondary" }}>
                            {rule.active ? "Ativa" : "Inativa"}
                          </Typography>
                        </Stack>
                        <Stack direction="row" spacing={1}>
                          <Button size="small" onClick={() => setRuleEditor({ open: true, initial: rule })}>Editar</Button>
                          <Button size="small" color="error" onClick={() => setRulePendingDeletion(rule)}>Remover</Button>
                        </Stack>
                      </Stack>
                      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" } }}>
                        {details.map((detail) => (
                          <Box key={detail.label} sx={{ minWidth: 0 }}>
                            <Typography variant="caption" color="text.secondary">{detail.label}</Typography>
                            <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>{detail.value}</Typography>
                          </Box>
                        ))}
                      </Box>
                      {rulePendingDeletion?.id === rule.id && (
                        <Alert severity="warning" sx={{ mt: 2 }} action={
                          <Stack direction="row" spacing={1}>
                            <Button size="small" onClick={() => setRulePendingDeletion(null)}>Cancelar</Button>
                            <Button size="small" color="error" onClick={() => void removeRule()}>Confirmar remoção</Button>
                          </Stack>
                        }>
                          Remover a regra “{rule.name}”?
                        </Alert>
                      )}
                    </Paper>
                  );
                })}
              </Stack>
            )}
          </Stack>
        )}

        {tab === "history" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2, minHeight: 52, mb: 2 }}>
              <Box>
                <Typography variant="h6">Histórico de execuções</Typography>
                <Typography variant="body2" color="text.secondary">
                  Execuções de forecast, linhas geradas e acesso ao snapshot publicado.
                </Typography>
              </Box>
            </Box>
            <TextField
              fullWidth
              size="small"
              placeholder="Buscar por situação, fase, perfil, base ou erro"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchOutlined fontSize="small" />
                    </InputAdornment>
                  ),
                  sx: { height: 40 },
                },
              }}
              sx={{ mb: 2, maxWidth: 520 }}
            />
            {!gridLoading && gridRows.length === 0 ? (
              <Box sx={{ py: 7 }}>
                <EmptyState
                  message={search ? "Nenhuma execução encontrada na busca." : "Nenhuma execução de Forecast registrada."}
                />
              </Box>
            ) : (
              <ForecastHistoryDataGrid
                rows={gridRows}
                total={gridTotal}
                loading={gridLoading}
                search={search}
                pollTick={pollTick}
                cancellingId={cancellingRunId}
                onQuery={handleGridQuery}
                onCancel={setCancelRunPending}
                onOpenResult={openHistory}
              />
            )}
          </Paper>
        )}

        {tab === "accuracy" && <ForecastAccuracy embedded />}
      </Box>
    </Box>

      {/* Confirmações Material — nunca window.confirm */}
      <Dialog open={Boolean(cancelRunPending)} onClose={() => setCancelRunPending(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Interromper esta execução?</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a cancelar a execução <strong>{cancelRunPending?.id}</strong>. Use isso se o forecast estiver travado.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={cancellingRunId !== null} onClick={() => setCancelRunPending(null)}>
            Voltar
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={cancellingRunId !== null}
            startIcon={cancellingRunId !== null ? <CircularProgress size={16} /> : undefined}
            onClick={() => cancelRunPending && void cancelRun(cancelRunPending)}
          >
            {cancellingRunId !== null ? "Interrompendo..." : "Interromper execução"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
