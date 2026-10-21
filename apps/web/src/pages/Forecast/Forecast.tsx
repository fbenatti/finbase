import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { Alert, Box, Button, CircularProgress, FormControl, IconButton, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material";
import { Add, Refresh, Search } from "@mui/icons-material";
import {
  DestinationTable,
  ForecastBusinessRule,
  ForecastConfig,
  ForecastMetadata,
  ForecastRun,
  cancelForecastRun,
  createForecastRun,
  deleteForecastBusinessRule,
  deleteForecastConfig,
  getDestinationTables,
  getForecastBusinessRulesGrid,
  getForecastConfigs,
  getForecastConfigsGrid,
  getForecastMetadata,
  getForecastRunsGrid,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import ForecastConfigsDataGrid from "./ForecastConfigsDataGrid";
import ForecastRulesDataGrid from "./ForecastRulesDataGrid";
import ForecastHistoryDataGrid from "./ForecastHistoryDataGrid";
import ForecastAccuracy from "../ForecastAccuracy/ForecastAccuracy";
import ForecastConfigDialog from "./ForecastConfigDialog";
import ForecastRuleDialog from "./ForecastRuleDialog";

type GridQuery = {
  offset: number;
  limit: number;
  sort?: { field: string; direction: "asc" | "desc" };
  filters: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
};

export default function Forecast() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Acurácia is the primary Forecast view; an explicit URL tab still wins.
  const requestedTab = searchParams.get("tab");
  const tab = ["accuracy", "config", "rules", "history"].includes(requestedTab || "") ? requestedTab! : "accuracy";
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [configs, setConfigs] = useState<ForecastConfig[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // ── Configurações (server-side grid) ──
  const [configsGridRows, setConfigsGridRows] = useState<ForecastConfig[]>([]);
  const [configsGridTotal, setConfigsGridTotal] = useState(0);
  const [configsGridLoading, setConfigsGridLoading] = useState(false);
  const [configsSearch, setConfigsSearch] = useState("");
  const configsQueryRef = useRef<GridQuery>({ offset: 0, limit: 10, filters: [] });
  const [runningConfigId, setRunningConfigId] = useState<number | null>(null);

  // ── Regras (server-side grid, por configuração) ──
  const [rulesConfigId, setRulesConfigId] = useState<number | "">("");
  const [rulesConfig, setRulesConfig] = useState<ForecastConfig | null>(null);
  const [rulesMeta, setRulesMeta] = useState<ForecastMetadata | null>(null);
  const [rulesGridRows, setRulesGridRows] = useState<ForecastBusinessRule[]>([]);
  const [rulesGridTotal, setRulesGridTotal] = useState(0);
  const [rulesGridLoading, setRulesGridLoading] = useState(false);
  const [rulesSearch, setRulesSearch] = useState("");
  const rulesQueryRef = useRef<GridQuery>({ offset: 0, limit: 10, filters: [] });

  // ── Histórico (server-side grid) ──
  const [gridRows, setGridRows] = useState<ForecastRun[]>([]);
  const [gridTotal, setGridTotal] = useState(0);
  const [gridLoading, setGridLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [pollTick, setPollTick] = useState(0);
  const gridQueryRef = useRef<GridQuery>({ offset: 0, limit: 25, filters: [] });

  // ── Diálogos ──
  const [configDialog, setConfigDialog] = useState<{ open: boolean; initial: ForecastConfig | null }>({ open: false, initial: null });
  const [ruleDialog, setRuleDialog] = useState<{ open: boolean; initial: ForecastBusinessRule | null }>({ open: false, initial: null });
  const [deleteConfigPending, setDeleteConfigPending] = useState<ForecastConfig | null>(null);
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

  // ── Configs grid ──
  const loadConfigsGrid = async (query: GridQuery) => {
    setConfigsGridLoading(true);
    try {
      const data = await getForecastConfigsGrid({ ...query, search: configsSearch.trim() || undefined });
      setConfigsGridRows(data.items);
      setConfigsGridTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao carregar configurações");
    } finally {
      setConfigsGridLoading(false);
    }
  };

  const handleConfigsGridQuery = (query: GridQuery) => {
    configsQueryRef.current = query;
    void loadConfigsGrid(query);
  };

  useEffect(() => {
    if (tab !== "config") return;
    configsQueryRef.current = { offset: 0, limit: configsQueryRef.current.limit, filters: [] };
    void loadConfigsGrid(configsQueryRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // ── Rules grid ──
  // The config id is passed explicitly: setRulesConfigId is async, so reading
  // it from state inside loadRulesGrid would use a stale value (empty on the
  // first entry to the tab) and skip the load.
  const loadRulesGrid = async (configId: number | "", query: GridQuery) => {
    if (!configId) {
      setRulesGridRows([]);
      setRulesGridTotal(0);
      return;
    }
    setRulesGridLoading(true);
    try {
      const data = await getForecastBusinessRulesGrid(configId, { ...query, search: rulesSearch.trim() || undefined });
      setRulesGridRows(data.items);
      setRulesGridTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao carregar regras");
    } finally {
      setRulesGridLoading(false);
    }
  };

  const handleRulesGridQuery = (query: GridQuery) => {
    rulesQueryRef.current = query;
    void loadRulesGrid(rulesConfigId, query);
  };

  const selectRulesConfig = async (id: number) => {
    setRulesConfigId(id);
    rulesQueryRef.current = { offset: 0, limit: rulesQueryRef.current.limit, filters: [] };
    const cfg = configs.find((c) => c.id === id) || null;
    setRulesConfig(cfg);
    if (cfg) {
      try {
        setRulesMeta(await getForecastMetadata(cfg.destination_table_id));
      } catch {
        setRulesMeta(null);
      }
    } else {
      setRulesMeta(null);
    }
    void loadRulesGrid(id, rulesQueryRef.current);
  };

  // Auto-select the first configuration when entering the rules tab.
  useEffect(() => {
    if (tab !== "rules") return;
    if (!rulesConfigId && configs.length > 0) {
      void selectRulesConfig(configs[0].id);
    } else if (!rulesConfigId) {
      setRulesGridRows([]);
      setRulesGridTotal(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, configs]);

  // If the selected configuration disappears, reset the selection.
  useEffect(() => {
    if (tab !== "rules") return;
    if (rulesConfigId && !configs.some((c) => c.id === rulesConfigId)) {
      setRulesConfigId("");
      setRulesConfig(null);
      setRulesMeta(null);
      setRulesGridRows([]);
      setRulesGridTotal(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, configs, rulesConfigId]);

  // ── History grid ──
  const loadRuns = async (query: GridQuery) => {
    setGridLoading(true);
    try {
      const data = await getForecastRunsGrid({ ...query, search: search.trim() || undefined });
      setGridRows(data.items);
      setGridTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao carregar execuções de forecast");
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
      setMessage("Execução " + result.run_id + " enfileirada para " + config.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao enfileirar o forecast");
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
      setMessage("Execução " + run.id + " cancelada");
      setCancelRunPending(null);
      await load();
      void loadRuns(gridQueryRef.current);
    } catch (e) {
      setError(String(e));
    } finally {
      setCancellingRunId(null);
    }
  };

  const removeConfig = async () => {
    if (!deleteConfigPending) return;
    try {
      await deleteForecastConfig(deleteConfigPending.id);
      setDeleteConfigPending(null);
      setMessage("Configuração excluída");
      await load();
      void loadConfigsGrid(configsQueryRef.current);
    } catch (e) {
      setError(String(e));
    }
  };

  const removeRule = async () => {
    if (!rulesConfigId || !rulePendingDeletion) return;
    try {
      await deleteForecastBusinessRule(rulePendingDeletion.id);
      setRulePendingDeletion(null);
      setMessage("Regra excluída");
      void loadRulesGrid(rulesConfigId, rulesQueryRef.current);
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
          <Typography variant="h4" component="h1">{tab === "rules" ? "Regras de projeção" : tab === "history" ? "Histórico de projeções" : tab === "accuracy" ? "Acurácia das projeções" : "Configuração e execução"}</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Projeções de custo sobre as bases consolidadas, com regras de negócio e rateio por intervalo.
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
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2, minHeight: 52, gap: 2, flexWrap: "wrap" }}>
              <Box>
                <Typography variant="h6">Configurações e execução</Typography>
                <Typography variant="body2" color="text.secondary">
                  Perfis de projeção por base consolidada e execução do forecast.
                </Typography>
              </Box>
              <Stack direction="row" spacing={1} flexShrink={0}>
                <Button size="small" startIcon={<Refresh />} onClick={() => { void load(); void loadConfigsGrid(configsQueryRef.current); }}>
                  Atualizar
                </Button>
                <Button size="small" variant="contained" startIcon={<Add />} onClick={() => setConfigDialog({ open: true, initial: null })}>
                  Novo Forecast
                </Button>
              </Stack>
            </Stack>
            <TextField
              fullWidth
              size="small"
              placeholder="Buscar configurações (nome, tabela, medida ou modelo)"
              value={configsSearch}
              onChange={(event) => setConfigsSearch(event.target.value)}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <Search fontSize="small" />
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
                  message={configsSearch ? "Nenhuma configuração corresponde à busca." : "Nenhuma configuração cadastrada. Crie uma para começar."}
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
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2, minHeight: 52, gap: 2, flexWrap: "wrap" }}>
              <Box>
                <Typography variant="h6">Regras de negócio do Forecast</Typography>
                <Typography variant="body2" color="text.secondary">
                  Regras versionadas aplicadas após a projeção matemática.
                </Typography>
              </Box>
              <Stack direction="row" spacing={1} alignItems="center" flexShrink={0}>
                <FormControl sx={{ minWidth: 260 }}>
                  <InputLabel>Configuração</InputLabel>
                  <Select
                    label="Configuração"
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
                  startIcon={<Add />}
                  disabled={!rulesConfigId}
                  onClick={() => setRuleDialog({ open: true, initial: null })}
                >
                  Adicionar regra
                </Button>
              </Stack>
            </Stack>
            {configs.length === 0 ? (
              <Alert severity="info">Nenhuma configuração de forecast. Crie uma em Configurações e execução.</Alert>
            ) : !rulesConfigId ? (
              <Alert severity="info">Selecione uma configuração para ver suas regras.</Alert>
            ) : (
              <>
                <TextField
                  fullWidth
                  size="small"
                  placeholder="Buscar regras (nome, tipo, comportamento ou coluna)"
                  value={rulesSearch}
                  onChange={(event) => setRulesSearch(event.target.value)}
                  slotProps={{
                    input: {
                      startAdornment: (
                        <InputAdornment position="start">
                          <Search fontSize="small" />
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
                      message={rulesSearch ? "Nenhuma regra corresponde à busca." : "Nenhuma regra cadastrada para esta configuração."}
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
                <Typography variant="h6">Histórico e resultados</Typography>
                <Typography variant="body2" color="text.secondary">
                  Execuções de forecast, linhas geradas e acesso ao snapshot publicado.
                </Typography>
              </Box>
            </Box>
            <TextField
              fullWidth
              size="small"
              placeholder="Buscar execuções (status, fase, configuração, tabela ou erro)"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <Search fontSize="small" />
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
                  message={search ? "Nenhuma execução corresponde à busca." : "Nenhuma execução de forecast registrada."}
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
        <DialogTitle>Excluir configuração?</DialogTitle>
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
        <DialogTitle>Excluir regra?</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a excluir a regra <strong>{rulePendingDeletion?.name}</strong>. Esta ação não pode ser desfeita.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setRulePendingDeletion(null)}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={() => void removeRule()}>
            Excluir regra
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(cancelRunPending)} onClose={() => setCancelRunPending(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Cancelar execução?</DialogTitle>
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
            {cancellingRunId !== null ? "Cancelando..." : "Cancelar execução"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
