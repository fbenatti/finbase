import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useNotifications } from "../../context/NotificationContext";
import { Alert, Box, Button, CircularProgress, Typography } from "@mui/material";
import { Refresh } from "@mui/icons-material";
import {
  SharedCostConfig, formatApiError, getSharedCosts,
  reprocessSharedCost,
} from "../../services/api";

export default function ReprocessSharedCostTab() {
  const navigate = useNavigate();
  const notifications = useNotifications();
  const [configs, setConfigs] = useState<SharedCostConfig[]>([]);
  const [error, setError] = useState("");
  const [startMessage, setStartMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const loadConfigs = async () => {
    setConfigs(await getSharedCosts());
  };

  const load = async () => {
    setError("");
    try {
      await loadConfigs();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível carregar o custo compartilhado"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const enabled = configs.filter(config => config.enabled);

  const run = async () => {
    setRunning(true);
    setError("");
    setStartMessage("");
    try {
      const result = await reprocessSharedCost();
      setStartMessage(result.message + " (task " + result.task_id + "). Acompanhe o andamento no ícone de notificações.");
      await notifications.addTaskById(result.task_id, { kind: "shared_cost", title: "Custo compartilhado" });
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível reprocessar o custo compartilhado"));
    } finally {
      setRunning(false);
    }
  };


  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {startMessage && <Alert severity="info" sx={{ mb: 2 }} onClose={() => setStartMessage("")}>{dataDisplayText(startMessage)}</Alert>}
      <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
        <Button
          variant="contained"
          startIcon={running ? <CircularProgress size={16} /> : <Refresh />}
          disabled={running || enabled.length === 0}
          onClick={() => void run()}
        >
          Reprocessar custo compartilhado
        </Button>
        <Button variant="outlined" onClick={() => navigate("/custos/resumo?shared=1")}>
          Abrir Visão Esperada com custo compartilhado
        </Button>
        <Button variant="text" onClick={() => navigate("/departments")}>Configurar departamentos</Button>
        <Button variant="text" onClick={() => void load()}>Atualizar</Button>
      </Box>
      {loading && <Typography color="text.secondary">Carregando custos compartilhados…</Typography>}
      {!loading && enabled.length === 0 && (
        <Typography color="text.secondary">
          Nenhuma configuração ativa de custo compartilhado. Em Departamentos, edite o nó doador, ligue Custo compartilhado e salve.
        </Typography>
      )}
    </Box>
  );
}
