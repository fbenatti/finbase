import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useState } from "react";
import { useNotifications } from "../../context/NotificationContext";
import {
  Alert, Box, Button, Card, CardContent, CircularProgress, Stack, Typography,
} from "@mui/material";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import { PlayArrow } from "@mui/icons-material";
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
      setError(e instanceof Error ? e.message : "Não foi possível carregar o forecast");
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
      setError(e instanceof Error ? e.message : "Falha ao enfileirar o forecast");
    } finally {
      setRunningConfig(null);
    }
  };


  return (
    <Box>
      {subtab === "accuracy" ? <ReprocessAccuracyTab /> : <>
      <Alert severity="info" sx={{ mb: 2 }}>
        Reprocessa o forecast da base consolidada configurada. Na sequência, o worker também publica o rateio
        de custo compartilhado e enfileira a materialização do <strong>base consolidada de visão completa</strong>
        (realizado + forecast + rateio) automaticamente. Acompanhe o andamento no sino de notificações
        (canto superior direito).
      </Alert>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6">Configurações</Typography>
          {configs.length === 0 && (
            <Typography color="text.secondary" sx={{ mt: 1 }}>Nenhuma configuração de forecast. Crie em Forecast.</Typography>
          )}
          {configs.map(config => (
            <Stack key={config.id} direction="row" gap={1} alignItems="center" mt={1} flexWrap="wrap">
              <Typography sx={{ flex: 1 }}>
                {config.name} · {config.measure_column} · {config.horizon_months} meses
              </Typography>
              <Button
                size="small"
                variant="contained"
                startIcon={runningConfig === config.id ? <CircularProgress size={16} /> : <PlayArrow />}
                disabled={runningConfig === config.id}
                onClick={() => void run(config.id)}
              >
                Reprocessar forecast
              </Button>
            </Stack>
          ))}
        </CardContent>
      </Card>
      </>}
    </Box>
  );
}
