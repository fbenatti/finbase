import { useState } from "react";
import { Alert, Box, Button, Stack, TextField, Typography } from "@mui/material";
import type { AnomalyConfig } from "../../services/anomaliesApi";
import { updateAnomalyConfig } from "../../services/anomaliesApi";

type Props = {
  config: AnomalyConfig;
  onSaved: () => void;
  busy: boolean;
  setBusy: (v: boolean) => void;
};

export default function AnomaliesSettingsTab({ config, onSaved, busy, setBusy }: Props) {
  const [lag, setLag] = useState(String(config.data_lag_days ?? 2));
  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        A detecção é disparada pelo agendamento de coleta (opção &quot;Executar detecção de anomalias ao final do pipeline&quot;).
        Motor: <strong>{config.detection_engine ?? "v2"}</strong>.
      </Alert>
      <Stack spacing={2} maxWidth={480}>
        <TextField
          label="Atraso da fonte (dias)"
          type="number"
          size="small"
          value={lag}
          onChange={(e) => setLag(e.target.value)}
        />
        <Button
          variant="contained"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await updateAnomalyConfig(config.id, { data_lag_days: Number(lag) });
              onSaved();
            } finally {
              setBusy(false);
            }
          }}
        >
          Salvar
        </Button>
        <Typography variant="body2" color="text.secondary">
          Janelas, materialidade, severidade e roteamento: próxima iteração do formulário completo (valores atuais no banco).
        </Typography>
      </Stack>
    </Box>
  );
}
