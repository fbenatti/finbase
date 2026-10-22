import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Collapse, Divider,
  FormControlLabel, IconButton, Link, Stack, TextField, Tooltip, Typography, Paper,
} from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  PlayArrow as PlayArrowIcon,
  Refresh as RefreshIcon,
  Validate as ScienceIcon,
} from "./ConnectionIcons";
import {
  AzureEnvironmentGroup,
  AzureSourceItem,
  collectAzureEnvironment,
  getAzureSources,
  refreshAzurePriceSheet,
  testAzureSource,
  updateAzureSourceSelection,
} from "../../services/api";

/**
 * Seleção das fontes Azure por ambiente.
 *
 * O operador escolhe **um ambiente** (tenant/credencial) e liga as fontes que
 * quer coletar; a carga full da Bronze sai daqui. Cada fonte continua sendo uma
 * conexão interna porque o pipeline vincula a Bronze ao `code` — mas isso não
 * aparece como dezenas de cards para configurar.
 */

const STATE_LABEL: Record<string, { label: string; color: "success" | "warning" | "error" | "default" }> = {
  AVAILABLE: { label: "Acessível", color: "success" },
  PARTIAL: { label: "Parcial", color: "warning" },
  NOT_CONFIGURED: { label: "Sem credenciais", color: "default" },
  UNAVAILABLE: { label: "Inacessível", color: "warning" },
  ERROR: { label: "Erro", color: "error" },
};

function formatWhen(value?: string | null): string {
  if (!value) return "nunca";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "nunca";
  return date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function SourceRow({
  source,
  busy,
  onToggle,
  onTest,
  onRefreshDedicated,
  onWindow,
}: {
  source: AzureSourceItem;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
  onTest: () => void;
  onRefreshDedicated: () => void;
  onWindow: (days: number) => void;
}) {
  const state = STATE_LABEL[source.state] || STATE_LABEL.NOT_CONFIGURED;
  const planned = source.implementation_status === "planned";
  const [open, setOpen] = useState(false);
  const [windowDays, setWindowDays] = useState(String(source.window_days ?? ""));

  useEffect(() => { setWindowDays(String(source.window_days ?? "")); }, [source.window_days]);

  return (
    <Box sx={{ borderBottom: 1, borderColor: "divider", py: 1 }}>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <Tooltip title={planned ? "Fonte prevista: o coletor ainda não existe" : source.independent_connection ? "Conexão independente: não depende de outro ambiente" : "Ativar ou pausar a coleta"}>
          <span>
            <Checkbox
              size="small"
              inputProps={{ "aria-label": `Coletar ${source.display_name}` }}
              checked={source.enabled}
              disabled={busy || planned || source.independent_connection || source.collection_mode === "dedicated"}
              onChange={(e) => onToggle(e.target.checked)}
            />
          </span>
        </Tooltip>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
            <Typography variant="body2" fontWeight={600}>{source.display_name}</Typography>
            <Chip size="small" label={state.label} color={state.color} variant="outlined" />
            {source.is_cost_source && <Chip size="small" label="Dados de custo" color="info" variant="outlined" />}
            {source.independent_connection && <Chip size="small" label="Conexão própria" color="info" variant="outlined" />}
            {planned && <Chip size="small" label="Em construção" variant="outlined" />}
            {source.running_tasks > 0 && <Chip size="small" label="Coleta em andamento" color="primary" />}
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block">
            {source.rows != null ? `${source.rows.toLocaleString("pt-BR")} registros` : "Sem registros coletados"}
            {source.last_billing_period ? ` · ${source.last_billing_period}` : ""}
          </Typography>
        </Box>
        <Tooltip title="Testar acesso à fonte">
          <span>
            <IconButton aria-label={`Testar acesso de ${source.display_name}`} size="small" onClick={onTest} disabled={busy || planned}>
              <ScienceIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        {source.collection_mode === "dedicated" && (
          <Tooltip title="Recarregar os dados desta fonte">
            <span>
              <IconButton aria-label={`Recarregar ${source.display_name}`} size="small" onClick={onRefreshDedicated} disabled={busy}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        )}
        <IconButton aria-label={`Ver detalhes de ${source.display_name}`} aria-expanded={open} size="small" onClick={() => setOpen((v) => !v)}>
          <ExpandMoreIcon fontSize="small" sx={{ transform: open ? "rotate(180deg)" : "none" }} />
        </IconButton>
      </Stack>

      <Collapse in={open}>
        <Stack spacing={1} sx={{ pl: { xs: 1, sm: 5 }, pt: 1, pb: 1 }}>
          <Typography variant="caption" color="text.secondary">Identificador: {source.code} · Origem: {source.bronze_table}</Typography>
          {source.description && (
            <Typography variant="caption" color="text.secondary">{source.description}</Typography>
          )}
          <Typography variant="caption" color="text.secondary">
            Último teste: {source.last_test_status || "—"} em {formatWhen(source.last_tested_at)}
            {source.last_ingested_at ? ` · última coleta: ${formatWhen(source.last_ingested_at)}` : ""}
          </Typography>
          {source.required_permission && (
            <Typography variant="caption" color="text.secondary">
              Permissão exigida: {source.required_permission}
            </Typography>
          )}
          {source.api_reference && (
            <Typography variant="caption" color="text.secondary">API: {source.api_reference}</Typography>
          )}
          {source.silver_target && (
            <Typography variant="caption" color="text.secondary">Destino detalhado: {source.silver_target}</Typography>
          )}
          {source.window_days != null && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ pt: 0.5 }}>
              <TextField
                size="small" label="Período (dias)" type="number" value={windowDays}
                onChange={(e) => setWindowDays(e.target.value)}
                onBlur={() => {
                  const days = Number(windowDays);
                  if (Number.isFinite(days) && days >= 1 && days <= 90 && days !== source.window_days) onWindow(days);
                }}
                inputProps={{ min: 1, max: 90 }}
                sx={{ width: 140 }}
              />
              <Typography variant="caption" color="text.secondary">De 1 a 90 dias (piloto: 15)</Typography>
            </Stack>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}

function EnvironmentCard({
  environment,
  onChanged,
}: {
  environment: AzureEnvironmentGroup;
  onChanged: () => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<{ severity: "success" | "error" | "info"; text: string } | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [expanded, setExpanded] = useState(true);

  const enabledSources = useMemo(
    () => environment.sources.filter((s) => s.enabled && s.implementation_status !== "planned" && s.collection_mode !== "dedicated"),
    [environment.sources],
  );

  const toggle = useCallback(async (source: AzureSourceItem, enabled: boolean) => {
    setBusyId(source.connection_id);
    try {
      await updateAzureSourceSelection(source.connection_id, source.source_key ?? "", { enabled });
      onChanged();
    } catch (error) {
      setMessage({ severity: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusyId(null);
    }
  }, [onChanged]);

  const changeWindow = useCallback(async (source: AzureSourceItem, days: number) => {
    setBusyId(source.connection_id);
    try {
      await updateAzureSourceSelection(source.connection_id, source.source_key ?? "", { window_days: days });
      setMessage({ severity: "success", text: `Janela de ${source.display_name} ajustada para ${days} dias.` });
      onChanged();
    } catch (error) {
      setMessage({ severity: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusyId(null);
    }
  }, [onChanged]);

  const test = useCallback(async (source: AzureSourceItem) => {
    setBusyId(source.connection_id);
    setMessage({ severity: "info", text: `Validando ${source.display_name}…` });
    try {
      const result = await testAzureSource(source.connection_id);
      setMessage({
        severity: result.state === "ERROR" ? "error" : "success",
        text: `${source.display_name}: ${result.message}${result.capability ? ` · ${JSON.stringify(result.capability)}` : ""}`,
      });
      onChanged();
    } catch (error) {
      setMessage({ severity: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusyId(null);
    }
  }, [onChanged]);

  const collectAll = useCallback(async () => {
    setCollecting(true);
    setMessage({ severity: "info", text: `Preparando a coleta completa do ambiente ${environment.environment_slug}…` });
    try {
      const result = await collectAzureEnvironment(environment.environment_slug);
      setMessage({ severity: "success", text: result.message });
      onChanged();
    } catch (error) {
      setMessage({ severity: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setCollecting(false);
    }
  }, [environment.environment_slug, onChanged]);

  const refreshDedicated = useCallback(async (source: AzureSourceItem) => {
    setBusyId(source.connection_id);
    setMessage({ severity: "info", text: `Preparando atualização independente de ${source.display_name}…` });
    try {
      const task = await refreshAzurePriceSheet();
      setMessage({ severity: "success", text: task.message || `Recarga agendada na conexão ${task.source || "azure-pricesheet"} (tarefa ${task.task_id}).` });
      onChanged();
    } catch (error) {
      setMessage({ severity: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusyId(null);
    }
  }, [onChanged]);

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <Typography variant="subtitle1" fontWeight={700}>
          {environment.environment_slug}
        </Typography>
        {environment.cloud_names.map((name) => <Chip key={name} size="small" label={name} />)}
        {environment.sources.some((source) => source.independent_connection) && (
          <Chip size="small" label="credenciais e coleta exclusivas" color="info" variant="outlined" />
        )}
        {environment.credential_profiles.map((profile) => (
          <Chip key={profile} size="small" variant="outlined" label={`perfil: ${profile}`} />
        ))}
        <Box sx={{ flex: 1 }} />
        {enabledSources.length > 0 && (
          <>
            <Chip size="small" label={`${environment.enabled_count}/${environment.source_count} ativas`} />
            <Tooltip title="Agenda a coleta de todas as fontes ativas deste ambiente">
              <span>
                <Button
                  size="small" variant="contained" startIcon={collecting ? <CircularProgress size={14} /> : <PlayArrowIcon />}
                  onClick={collectAll} disabled={collecting}
                >
                  Coletar fontes ({enabledSources.length})
                </Button>
              </span>
            </Tooltip>
          </>
        )}
        <IconButton aria-label={`Mostrar fontes do ambiente ${environment.environment_slug}`} aria-expanded={expanded} size="small" onClick={() => setExpanded((v) => !v)}>
          <ExpandMoreIcon fontSize="small" sx={{ transform: expanded ? "rotate(180deg)" : "none" }} />
        </IconButton>
      </Stack>

      <Typography variant="caption" color="text.secondary">
        Fontes compartilhadas usam as credenciais do ambiente; fontes independentes preservam conexão,
        credenciais e fluxo de coleta próprios.
      </Typography>

      {message && <Alert severity={message.severity} sx={{ mt: 1 }} onClose={() => setMessage(null)}>{dataDisplayText(message.text)}</Alert>}

      <Collapse in={expanded}>
        <Box sx={{ mt: 1 }}>
          {environment.sources.map((source) => (
            <SourceRow
              key={source.connection_id}
              source={source}
              busy={busyId === source.connection_id}
              onToggle={(enabled) => toggle(source, enabled)}
              onTest={() => test(source)}
              onRefreshDedicated={() => void refreshDedicated(source)}
              onWindow={(days) => changeWindow(source, days)}
            />
          ))}
        </Box>
      </Collapse>
    </Paper>
  );
}

export default function AzureSourcesPanel({ environment, connectionId }: { environment?: string; connectionId?: number }) {
  const [groups, setGroups] = useState<AzureEnvironmentGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getAzureSources(environment, connectionId);
      setGroups(data.environments || []);
      setError(null);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setError(`Falha ao consultar GET /azure-sources/: ${detail}`);
    } finally {
      setLoading(false);
    }
  }, [environment, connectionId]);

  useEffect(() => { void load(); }, [load]);

  const totals = useMemo(() => {
    const sources = groups.flatMap((g) => g.sources);
    return {
      environments: groups.length,
      sources: sources.length,
      enabled: sources.filter((s) => s.enabled).length,
      collectable: sources.filter((s) => s.enabled && s.implementation_status !== "planned").length,
    };
  }, [groups]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, py: 2 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">Buscando fontes Azure…</Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error" action={<IconButton aria-label="Recarregar fontes Azure" size="small" onClick={() => void load()}><RefreshIcon fontSize="small" /></IconButton>}>
        Não foi possível carregar as fontes Azure: {dataDisplayText(error)}
      </Alert>
    );
  }

  if (groups.length === 0) {
    return (
      <Alert severity="info">
        Nenhuma fonte Azure cadastrada. Crie conexões Azure; fontes do mesmo ambiente podem compartilhar
        a credencial, enquanto fontes com caminho de ingestão independente devem ter conexão própria.
      </Alert>
    );
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="subtitle2">
          Fontes de coleta Azure
        </Typography>
        <Chip size="small" label={`${totals.environments} ambiente(s) mapeado(s)`} />
        <Chip size="small" label={`${totals.collectable} prontas para coleta`} color="success" variant="outlined" />
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Revalidar o estado das fontes">
          <IconButton aria-label="Recarregar fontes Azure" size="small" onClick={() => void load()}><RefreshIcon fontSize="small" /></IconButton>
        </Tooltip>
      </Stack>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1.5 }}>

        Habilite as fontes que devem participar da coleta do ambiente. Conexões independentes mantêm sua ação de coleta
        e base de origem próprias. Desabilitar uma fonte não apaga o histórico já coletado.
      </Typography>

      {groups.map((group) => (
        <EnvironmentCard key={group.environment_slug} environment={group} onChanged={() => void load()} />
      ))}

      <Divider sx={{ my: 1 }} />
      <Typography variant="caption" color="text.secondary">
        Fontes marcadas como <b>prevista</b> aparecem na lista para dar visibilidade ao roadmap, mas
        ainda não estão disponíveis para coleta. Veja{" "}
        <Link href="/dados/explorador/destinos" underline="hover">Explorador de tabelas</Link> para inspecionar as tabelas de origem, de staging e consolidadas.
      </Typography>
    </Box>
  );
}
