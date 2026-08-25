import { Chip } from "@mui/material";

const BAND_COLORS: Record<string, "default" | "success" | "warning" | "error"> = {
  "0-30": "warning",
  "30-80": "success",
  "80-95": "warning",
  "95-100": "error",
  low: "success",
  medium: "warning",
  high: "error",
  critical: "error",
};

type Props = {
  band?: string | null;
  label?: string;
};

export default function RiskBand({ band, label }: Props) {
  if (!band) return <Chip size="small" label={label || "—"} />;
  return (
    <Chip
      size="small"
      color={BAND_COLORS[band] || "default"}
      label={label || band}
    />
  );
}
