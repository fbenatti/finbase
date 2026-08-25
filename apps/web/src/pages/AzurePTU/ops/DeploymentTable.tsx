import {
  Chip, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import RiskBand from "./RiskBand";

const num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 });

const PROJECT_LABELS: Record<string, string> = {
  "vivo-mais": "Vivo Mais",
  "aura-brain": "Aura Brain",
};

type Deployment = {
  deployment_name: string;
  project_key?: string;
  model_name?: string;
  region?: string;
  ptus?: number;
  utilization_avg?: number;
  utilization_max?: number;
  utilization_p95?: number;
  utilization_peak_label?: string;
  hours_at_or_above_80?: number;
  hours_at_or_above_95?: number;
  rate_limited_count?: number;
  input_tpm?: number;
  output_tpm?: number;
  utilization_band?: string;
  risk_score?: number;
};

type Props = {
  deployments: Deployment[];
  emptyMessage?: string;
};

export default function DeploymentTable({ deployments, emptyMessage }: Props) {
  const sorted = [...deployments].sort(
    (a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0) || (b.utilization_max ?? 0) - (a.utilization_max ?? 0),
  );

  if (!sorted.length) {
    return <Typography color="text.secondary">{emptyMessage || "sem dados no recorte"}</Typography>;
  }

  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          {["Projeto", "Deployment", "PTUs", "Média", "Pico", "≥80h", "≥95h", "429", "Faixa"].map(h => (
            <TableCell key={h}>{h}</TableCell>
          ))}
        </TableRow>
      </TableHead>
      <TableBody>
        {sorted.map(d => (
          <TableRow key={d.deployment_name}>
            <TableCell>{PROJECT_LABELS[d.project_key || ""] || d.project_key || "—"}</TableCell>
            <TableCell>
              <Typography variant="body2">{d.deployment_name}</Typography>
              <Typography variant="caption" color="text.secondary">
                {d.model_name || "—"} · {d.region || "—"}
              </Typography>
            </TableCell>
            <TableCell>{d.ptus ?? "—"}</TableCell>
            <TableCell>{d.utilization_avg != null ? `${num.format(d.utilization_avg)}%` : "—"}</TableCell>
            <TableCell>
              {d.utilization_max != null ? `${num.format(d.utilization_max)}%` : "—"}
              {d.utilization_peak_label && (
                <Typography variant="caption" display="block" color="text.secondary">
                  {d.utilization_peak_label}
                </Typography>
              )}
            </TableCell>
            <TableCell>{d.hours_at_or_above_80 ?? "—"}</TableCell>
            <TableCell>{d.hours_at_or_above_95 ?? "—"}</TableCell>
            <TableCell>
              {d.rate_limited_count ? (
                <Chip size="small" color="error" label={num.format(d.rate_limited_count)} />
              ) : "—"}
            </TableCell>
            <TableCell><RiskBand band={d.utilization_band} /></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function DeploymentRanking({ deployments }: { deployments: Deployment[] }) {
  const sorted = [...deployments].sort(
    (a, b) => ((b as any).total_tokens ?? (b as any).input_tokens ?? 0) - ((a as any).total_tokens ?? (a as any).input_tokens ?? 0),
  ).slice(0, 10);

  if (!sorted.length) return <Typography color="text.secondary">sem dados no recorte</Typography>;

  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          {["#", "Deployment", "Input", "Output", "Utilização"].map(h => <TableCell key={h}>{h}</TableCell>)}
        </TableRow>
      </TableHead>
      <TableBody>
        {sorted.map((d, i) => (
          <TableRow key={d.deployment_name}>
            <TableCell>{i + 1}</TableCell>
            <TableCell>{d.deployment_name}</TableCell>
            <TableCell>{num.format((d as any).input_tokens || 0)}</TableCell>
            <TableCell>{num.format((d as any).output_tokens || 0)}</TableCell>
            <TableCell>{d.utilization_avg != null ? `${num.format(d.utilization_avg)}%` : "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
