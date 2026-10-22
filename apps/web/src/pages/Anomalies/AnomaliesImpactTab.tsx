import { useEffect, useState } from "react";
import { Paper, Stack, Typography } from "@mui/material";
import { getAnomalyImpactSummary, type ImpactSummary } from "../../services/anomaliesApi";

const money = (v: number, currency = "BRL") =>
  v.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 0 });

export default function AnomaliesImpactTab({ currency }: { currency: string }) {
  const [data, setData] = useState<ImpactSummary | null>(null);
  useEffect(() => {
    void getAnomalyImpactSummary().then(setData).catch(() => setData(null));
  }, []);
  if (!data) {
    return <Typography color="text.secondary">Calculando impacto…</Typography>;
  }
  const items = [
    { label: "Anomalias detectadas", value: data.detected },
    { label: "Custo já incorrido", value: money(data.incurred_excess_cost, currency) },
    { label: "Economia projetada / mês", value: money(data.projected_saving_monthly, currency) },
    { label: "Economia projetada / ano", value: money(data.projected_saving_annual, currency) },
    { label: "Exposição sem tratamento (mês)", value: money(data.open_exposure_monthly, currency) },
    { label: "Exposição sem tratamento (ano)", value: money(data.open_exposure_annual, currency) },
  ];
  return (
    <Stack direction="row" spacing={2} flexWrap="wrap">
      {items.map((item) => (
        <Paper key={item.label} variant="outlined" sx={{ p: 1.5, minWidth: 180 }}>
          <Typography variant="caption" color="text.secondary">{item.label}</Typography>
          <Typography variant="h6">{item.value}</Typography>
        </Paper>
      ))}
    </Stack>
  );
}
