import { Card, CardContent, Typography } from "@mui/material";

type Props = {
  label: string;
  value: string;
  hint?: string;
  unavailable?: boolean;
};

export default function KpiCard({ label, value, hint, unavailable }: Props) {
  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Typography color="text.secondary" variant="body2">{label}</Typography>
        <Typography variant="h6" color={unavailable ? "text.disabled" : "text.primary"}>
          {unavailable ? "—" : value}
        </Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary">
            {unavailable ? "métrica não suportada" : hint}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}
