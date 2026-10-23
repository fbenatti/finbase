import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useState } from "react";
import { useNotifications } from "../../context/NotificationContext";
import {
  Alert, Box, Button, CircularProgress, Paper, Stack, Typography,
} from "@mui/material";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { PlayArrowOutlined } from "@mui/icons-material";
import {
  ForecastConfig, createForecastRun, getForecastConfigs,
} from "../../services/api";
import ReprocessAccuracyTab from "./ReprocessAccuracyTab";

export default function ReprocessForecastTab() {
  const [params] = useSearchParams();
  const subtab = params.get("subtab") === "accuracy" ? "accuracy" : "forecast";
  const [configs, setConfigs] = useState<ForecastConfig[]>([]);
  const [error, setError] = useState("");
  const [runningConfig, setRunningConfig] = useState<number | null>(null);
  const notifications = useNotifications();

  const load = useCallback(async () => {
    try {
      const nextConfigs = await getForecastConfigs();
      setConfigs(nextConfigs);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar o Forecast");
    }
  }, []);

  useEffect(() => {
    if (subtab === "forecast") void load();
  }, [subtab, load]);

  const run = async (configId: number) => {
    setRunningConfig(configId);
    setError("");
    try {
      const result = await createForecastRun(configId);
      await notifications.addTaskById(result.task_id, { kind: "forecast", title: "Forecast" });
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível enviar o Forecast para a fila");
    } finally {
      setRunningConfig(null);
    }
  };

  return (
    <Stack spacing={2}>
      {subtab === "accuracy" ? <ReprocessAccuracyTab /> : <>
      <Box>
        <Typography variant="h6">Execuções do Forecast</Typography>
        <Typography variant="body2" color="text.secondary">
          Reprocessa o forecast da base consolidada. Depois, o worker publica o rateio de custo compartilhado
          e enfileira a materialização da <strong>base consolidada de visão total</strong> (efetivado + forecast + rateio).
          O andamento aparece nas notificações.
        </Typography>
      </Box>

      {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      {configs.length === 0 ? (
        <Typography color="text.secondary">Nenhuma configuração de Forecast. Crie em Perfis de Forecast.</Typography>
      ) : (
        <Stack spacing={1}>
          {configs.map(config => (
            <Paper key={config.id} variant="outlined" sx={{ p: 1.5 }}>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={600} noWrap>{config.name}</Typography>
                  <Typography variant="caption" color="text.secondary" noWrap>
                    {config.measure_column} · {config.horizon_months} meses de horizonte
                  </Typography>
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={runningConfig === config.id ? <CircularProgress size={16} /> : <PlayArrowOutlined />}
                  disabled={runningConfig === config.id}
                  onClick={() => void run(config.id)}
                  sx={{ alignSelf: { sm: "center" } }}
                >
                  Reprocessar forecast
                </Button>
              </Stack>
            </Paper>
          ))}
        </Stack>
      )}
      </>}
    </Stack>
  );
}
