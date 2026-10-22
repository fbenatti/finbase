import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useNotifications } from "../../context/NotificationContext";
import { Alert, Box, Button, Chip, CircularProgress, IconButton, Paper, Stack, Tooltip, Typography } from "@mui/material";
import { RefreshOutlined, OpenInNewOutlined, SettingsOutlined } from "@mui/icons-material";
import {
  SharedCostConfig, formatApiError, getSharedCosts,
  reprocessSharedCost,
} from "../../services/api";

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: "#FAFBFD", minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" fontWeight={700}>{value}</Typography>
    </Paper>
  );
}

export default function ReprocessSharedCostTab() {
  const navigate = useNavigate();
  const notifications = useNotifications();
  const [configs, setConfigs] = useState<SharedCostConfig[]>([]);
  const [error, setError] = useState("");
  const [startMessage, setStartMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const load = async () => {
    setError("");
    try {
      setConfigs(await getSharedCosts());
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível carregar o rateio de custo compartilhado"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const enabled = configs.filter(config => config.enabled);
  const disabledCount = configs.length - enabled.length;
  const targetCount = enabled.reduce((total, config) => total + (config.targets?.length ?? 0), 0);

  const run = async () => {
    setRunning(true);
    setError("");
    setStartMessage("");
    try {
      const result = await reprocessSharedCost();
      setStartMessage(`${result.message} (tarefa ${result.task_id}). Acompanhe no ícone de notificações.`);
      await notifications.addTaskById(result.task_id, { kind: "shared_cost", title: "Custo compartilhado" });
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível reprocessar o rateio"));
    } finally {
      setRunning(false);
    }
  };

  return (
    <Stack spacing={2.5}>
      {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {startMessage && <Alert severity="info" onClose={() => setStartMessage("")}>{dataDisplayText(startMessage)}</Alert>}

      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} spacing={2}>
        <Box>
          <Typography variant="subtitle1" fontWeight={700}>Rateio do custo compartilhado</Typography>
          <Typography variant="body2" color="text.secondary">
            Departamentos doadores repassam parte do custo aos receptores. Recalcule depois de mudar um rateio.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="contained" startIcon={running ? <CircularProgress size={16} color="inherit" /> : <RefreshOutlined />}
            disabled={running || enabled.length === 0} onClick={() => void run()}>
            Recalcular rateio
          </Button>
          <Button variant="outlined" startIcon={<OpenInNewOutlined />} onClick={() => navigate("/custos/resumo?shared=1")}>
            Ver visão esperada
          </Button>
          <Tooltip title="Configurar unidades">
            <IconButton aria-label="Configurar unidades" onClick={() => navigate("/departments")}><SettingsOutlined /></IconButton>
          </Tooltip>
          <Tooltip title="Recarregar dados">
            <IconButton aria-label="Recarregar dados" onClick={() => { setLoading(true); void load(); }}><RefreshOutlined /></IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(3, minmax(0, 1fr))" } }}>
        <Kpi label="Rateios ativos" value={enabled.length} />
        <Kpi label="Destinos do repasse" value={targetCount} />
        <Kpi label="Rateios desativados" value={disabledCount} />
      </Box>

      {loading && <Typography color="text.secondary">Carregando rateios…</Typography>}

      {!loading && enabled.length === 0 && (
        <Paper variant="outlined" sx={{ p: 3, borderRadius: 2, textAlign: "center" }}>
          <Typography fontWeight={600}>Nenhum rateio em uso</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Em Departamentos, edite o nó doador, ative o custo compartilhado e salve.
          </Typography>
          <Button variant="outlined" onClick={() => navigate("/departments")}>Abrir unidades</Button>
        </Paper>
      )}

      {enabled.length > 0 && (
        <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: "repeat(2, minmax(0, 1fr))" } }}>
          {enabled.map(config => (
            <Paper key={config.department_id} variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
                <Box sx={{ minWidth: 0 }}>
                  <Typography fontWeight={700} noWrap>{config.name || `Departamento ${config.department_id}`}</Typography>
                  {config.path && <Typography variant="caption" color="text.secondary" noWrap component="p">{config.path}</Typography>}
                </Box>
                <Chip size="small" color="success" variant="outlined" label="Ativo" />
              </Stack>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
                <Chip size="small" variant="outlined" label={String(config.allocation_type)} />
                <Chip size="small" variant="outlined" label={String(config.mode)} />
                <Chip size="small" variant="outlined" label={`${config.targets?.length ?? 0} destino(s)`} />
              </Stack>
            </Paper>
          ))}
        </Box>
      )}
    </Stack>
  );
}
