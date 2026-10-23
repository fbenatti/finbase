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
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "flex-start" }} sx={{ mb: 2, gap: 2 }}>
              <Box>
                <Typography variant="h6">Perfis de Forecast</Typography>
                <Typography variant="body2" color="text.secondary">
                  Cada perfil define como uma base consolidada é projetada. Rode um perfil quando quiser atualizar o Forecast.
                </Typography>
              </Box>
              <Stack direction="row" spacing={1} flexShrink={0}>
                <Button size="small" startIcon={<RefreshOutlined />} onClick={() => { void load(); void loadConfigsGrid(configsQueryRef.current); }}>
                  Atualizar
                </Button>
                <Button size="small" variant="contained" startIcon={<AddOutlined />} onClick={() => setConfigDialog({ open: true, initial: null })}>
                  Novo perfil
                </Button>
              </Stack>
            </Stack>
            <Box sx={{ display: "grid", gap: 1.5, mb: 2, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
              {[
                { label: "Perfis criados", value: configsGridTotal },
                { label: "Em execução", value: runningConfigId !== null ? 1 : 0 },
                { label: "Bases sem perfil de Forecast", value: Math.max(0, tables.length - configs.length) },
              ].map((kpi) => (
                <Paper key={kpi.label} variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD" }}>
                  <Typography variant="caption" color="text.secondary">{kpi.label}</Typography>
                  <Typography variant="h5" fontWeight={700}>{kpi.value}</Typography>
                </Paper>
              ))}
            </Box>
            <TextField
              fullWidth
              size="small"
              placeholder="Buscar por nome, base, medida ou modelo"
              value={configsSearch}
              onChange={(event) => setConfigsSearch(event.target.value)}
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
            {!configsGridLoading && configsGridRows.length === 0 ? (
              <Box sx={{ py: 7 }}>
                <EmptyState
                  message={configsSearch ? "Nenhum perfil corresponde à busca." : "Nenhum perfil cadastrado. Crie um para começar."}
                />
              </Box>
            ) : (
              <ForecastConfigsDataGrid
                rows={configsGridRows}
                total={configsGridTotal}
                loading={configsGridLoading}
                search={configsSearch}
                runningConfigId={runningConfigId}
                onQuery={handleConfigsGridQuery}
                onEdit={(config) => {
                  setConfigDialog({ open: true, initial: config });
                }}
                onRun={(config) => void runConfig(config)}
                onDelete={setDeleteConfigPending}
              />
            )}
          </Paper>
        )}

        {tab === "rules" && (
          <Paper elevation={0} sx={{ p: { xs: 1.5, md: 2 }, borderRadius: 2, bgcolor: "background.paper", border: "1px solid", borderColor: "divider" }}>
            <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "flex-start" }} sx={{ mb: 2, gap: 2 }}>
              <Box>
                <Typography variant="h6">Ajustes do perfil</Typography>
                <Typography variant="body2" color="text.secondary">
                  Cada regra de negócio ajusta o Forecast depois do cálculo matemático: exclui parcelas, troca valores ou suspende o escopo.
                </Typography>
              </Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} flexShrink={0}>
                <FormControl sx={{ minWidth: 260 }}>
                  <InputLabel>Perfil de Forecast</InputLabel>
                  <Select
                    label="Perfil de Forecast"
                    value={rulesConfigId}
                    onChange={(e) => void selectRulesConfig(Number(e.target.value))}
                  >
                    {configs.map((c) => (
                      <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Button
                  size="small"
                  variant="contained"
                  startIcon={<AddOutlined />}
                  disabled={!rulesConfigId}
                  onClick={() => setRuleDialog({ open: true, initial: null })}
                >
                  Nova regra
                </Button>
              </Stack>
            </Stack>
            <Box sx={{ display: "grid", gap: 1.5, mb: 2, gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
              {[
                { label: "Regras do perfil", value: rulesConfigId ? rulesGridTotal : "—" },
                { label: "Ativas nesta página", value: rulesGridRows.filter((r) => r.active).length },
                { label: "Perfil em uso", value: rulesConfig?.name ?? "Nenhum" },
              ].map((kpi) => (
                <Paper key={kpi.label} variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD", minWidth: 0 }}>
                  <Typography variant="caption" color="text.secondary">{kpi.label}</Typography>
                  <Typography variant="h5" fontWeight={700} noWrap>{kpi.value}</Typography>
                </Paper>
              ))}
            </Box>
            {configs.length === 0 ? (
              <Alert severity="info">Nenhum perfil de Forecast. Crie um em Perfis de Forecast.</Alert>
            ) : !rulesConfigId ? (
              <Alert severity="info">Escolha um perfil para ver as regras.</Alert>
            ) : (
              <>
                <TextField
                  fullWidth
                  size="small"
                  placeholder="Buscar por nome, tipo, comportamento ou coluna"
                  value={rulesSearch}
                  onChange={(event) => setRulesSearch(event.target.value)}
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
                {!rulesGridLoading && rulesGridRows.length === 0 ? (
                  <Box sx={{ py: 7 }}>
                    <EmptyState
                      message={rulesSearch ? "Nenhuma regra encontrada na busca." : "Nenhuma regra cadastrada para este perfil."}
                    />
                  </Box>
                ) : (
                  <ForecastRulesDataGrid
                    key={rulesConfigId}
                    rows={rulesGridRows}
                    total={rulesGridTotal}
                    loading={rulesGridLoading}
                    search={rulesSearch}
                    onQuery={handleRulesGridQuery}
                    onEdit={(rule) => setRuleDialog({ open: true, initial: rule })}
                    onDelete={setRulePendingDeletion}
                  />
                )}
              </>
            )}
          </Paper>
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

      {/* Diálogos fora da área com CSS de toolbar compacta (40px) */}
      <ForecastConfigDialog
        open={configDialog.open}
        initial={configDialog.initial}
        tables={tables}
        usedDestinationTableIds={configs.map((item) => item.destination_table_id)}
        onClose={() => setConfigDialog({ open: false, initial: null })}
        onSaved={async (savedName) => {
          setError("");
          setMessage(`Configuração "${savedName}" salva com sucesso.`);
          await load();
          await loadConfigsGrid(configsQueryRef.current);
        }}
      />
      {ruleDialog.open && rulesConfig && (
        <ForecastRuleDialog
          open={ruleDialog.open}
          initial={ruleDialog.initial}
          config={rulesConfig}
          meta={rulesMeta}
          onClose={() => setRuleDialog({ open: false, initial: null })}
          onSaved={async (savedName) => {
            setError("");
            setMessage(`Regra "${savedName}" salva com sucesso.`);
            await loadRulesGrid(rulesConfigId, rulesQueryRef.current);
          }}
        />
      )}

      {/* Confirmações Material — nunca window.confirm */}
      <Dialog open={Boolean(deleteConfigPending)} onClose={() => setDeleteConfigPending(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Remover perfil?</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a excluir a configuração <strong>{deleteConfigPending?.name}</strong>. Execuções já concluídas permanecem no histórico.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeleteConfigPending(null)}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={() => void removeConfig()}>
            Excluir configuração
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(rulePendingDeletion)} onClose={() => setRulePendingDeletion(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Remover regra?</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a excluir a regra <strong>{rulePendingDeletion?.name}</strong>. Esta ação não pode ser desfeita.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setRulePendingDeletion(null)}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={() => void removeRule()}>
            Remover regra
          </Button>
        </DialogActions>
      </Dialog>

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
