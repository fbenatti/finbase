import { Alert, Typography } from "@mui/material";

type Coverage = {
  days_with_data?: number;
  expected_days?: number;
  coverage_pct?: number;
  missing_days?: number;
  missing_day_samples?: string[];
};

type Props = {
  coverage?: Coverage | null;
  period?: { coverage_pct?: number; sample_hours?: number; expected_hours?: number };
};

export default function CoverageWarning({ coverage, period }: Props) {
  const missing = coverage?.missing_days ?? 0;
  const periodGap = period?.expected_hours && period?.sample_hours
    ? period.expected_hours - period.sample_hours
    : 0;

  if (!missing && periodGap <= 0) return null;

  return (
    <Alert severity="warning" sx={{ mb: 2 }}>
      {missing > 0 && (
        <Typography variant="body2">
          Cobertura incompleta na base consolidada: {coverage?.days_with_data}/{coverage?.expected_days} dias
          ({coverage?.coverage_pct}%).
          {coverage?.missing_day_samples?.length
            ? ` Ex.: ${coverage.missing_day_samples.slice(0, 5).join(", ")}`
            : ""}
        </Typography>
      )}
      {periodGap > 0 && (
        <Typography variant="body2">
          Amostras horárias: {period?.sample_hours}/{period?.expected_hours} ({period?.coverage_pct}%).
        </Typography>
      )}
    </Alert>
  );
}
