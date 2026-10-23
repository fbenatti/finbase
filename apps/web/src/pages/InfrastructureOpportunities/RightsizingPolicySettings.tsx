/**
 * Parâmetros do motor de rightsizing de infraestrutura.
 *
 * A agressividade preenche os parâmetros detalhados; editar um campo torna a
 * política personalizada. Cada gravação cria uma revisão nova (quem e quando),
 * usada pela próxima execução em /otimizacao/rightsizing-infra.
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import {
  Alert, Box, Button, Chip, FormControl, FormHelperText, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack,
  TextField, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import { formatApiError } from "../../services/api";
import type { RightsizingAggressiveness, RightsizingPolicy, RightsizingPolicyParams } from "../../services/infraOpportunitiesApi";
import { getRightsizingPolicy, updateRightsizingPolicy } from "../../services/infraOpportunitiesApi";
import { AGGRESSIVENESS_LABEL } from "./opportunityLabels";

type Key = keyof RightsizingPolicyParams;
type PolicyField = {
  key: Key; label: string; help: string; unit: string;
  /** Converte o valor guardado para o exibido e de volta. */
  toView: (value: number) => number; fromView: (value: number) => number;
};

const percent = { unit: "%", toView: (v: number) => Math.round(v * 1000) / 10, fromView: (v: number) => v / 100 };
const headroom = { unit: "%", toView: (v: number) => Math.round((1 - v) * 1000) / 10, fromView: (v: number) => 1 - v / 100 };
const days = { unit: "dias", toView: (v: number) => v, fromView: (v: number) => Math.round(v) };

const AGGRESSIVENESS_HELP: Record<RightsizingAggressiveness, string> = {
  CONSERVATIVE: "Mais folga e mais evidência antes de reduzir. Menos economia, risco menor de lentidão.",
  BALANCED: "Padrão do motor: folga moderada sobre o uso típico e os picos.",
  AGGRESSIVE: "Tamanhos mais justos ao uso medido. Mais economia, com menos folga para picos e crescimento.",
  CUSTOM: "Valores ajustados manualmente, diferentes dos três perfis.",
};

const GROUPS: { title: string; summary: string; fields: PolicyField[] }[] = [
  {
    title: "Uso típico (percentis das médias de 5 min)",
    summary: "Quanto do novo tamanho o uso medido pode ocupar. Alvos mais altos geram máquinas menores.",
    fields: [
      { key: "cpu_target_p95", label: "Alvo de CPU no P95", ...percent, help: "Uso de CPU que 95% do tempo deve ficar abaixo no novo tamanho. Mais alto = mais economia, menos folga." },
      { key: "cpu_target_p99", label: "Alvo de CPU no P99", ...percent, help: "Limite para os momentos mais carregados (1% do tempo). Mais alto = mais economia, mais risco de lentidão." },
      { key: "memory_target_p95", label: "Alvo de memória no P95", ...percent, help: "Uso de memória que 95% do tempo deve respeitar no novo tamanho. Mais alto = menos memória sobrando." },
      { key: "memory_target_p99", label: "Alvo de memória no P99", ...percent, help: "Limite de memória nos momentos de maior uso. Mais alto = mais economia, mais risco de falta de memória." },
    ],
  },
  {
    title: "Picos",
    summary: "Os picos também dimensionam: o novo tamanho precisa comportá-los.",
    fields: [
      { key: "cpu_peak_limit", label: "Limite de CPU nos picos", ...percent, help: "Quanto da capacidade os picos podem ocupar. Acima de 100% aceita fila de CPU nos picos: mais economia, mais lentidão." },
      { key: "memory_peak_limit", label: "Folga de memória sobre o maior pico", ...headroom, help: "Memória reservada além do maior pico observado. Menos folga = mais economia, mais risco de falta de memória." },
    ],
  },
  {
    title: "Estabilidade e evidência",
    summary: "Quando o motor muda o tamanho e quanto dado ele exige para recomendar.",
    fields: [
      { key: "min_change_ratio", label: "Redução mínima para recomendar", ...percent, help: "Reduções menores mantêm o tamanho atual. Mais baixo = mais recomendações pequenas; mais alto = menos trocas por ruído." },
      { key: "min_days", label: "Dias mínimos de dados", ...days, help: "Abaixo disto a análise fica como telemetria incompleta, sem saving. Precisa caber na janela escolhida na execução." },
      { key: "min_coverage", label: "Cobertura mínima da telemetria", ...percent, help: "Fração mínima dos intervalos com dados. Mais baixo aceita séries com falhas: mais recomendações, menos certeza." },
      { key: "high_confidence_coverage", label: "Cobertura para confiança alta", ...percent, help: "A partir desta cobertura (e sem pico acima da capacidade) a confiança é alta. Não muda o tamanho, só a confiança." },
    ],
  },
];

const PEAK_QUANTILE_LABEL: Record<string, string> = {
  "99": "P99 dos picos — até 1% do tempo acima",
  "99.5": "P99,5 dos picos — até 0,5% do tempo acima",
  "99.9": "P99,9 dos picos — até 0,1% do tempo acima",
  "100": "Maior pico — todo pico precisa caber",
};

const fieldGrid = { display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" }, gap: 2 } as const;

function classify(policy: RightsizingPolicy, params: RightsizingPolicyParams): RightsizingAggressiveness {
  const match = (Object.entries(policy.presets) as [RightsizingAggressiveness, RightsizingPolicyParams][])
    .find(([, preset]) => (Object.keys(preset) as Key[]).every((key) => Math.abs(preset[key] - params[key]) < 1e-9));
  return match ? match[0] : "CUSTOM";
}

export default function RightsizingPolicySettings() {
  const [policy, setPolicy] = useState<RightsizingPolicy | null>(null);
  const [params, setParams] = useState<RightsizingPolicyParams | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    getRightsizingPolicy().then((data) => { setPolicy(data); setParams(data.params); })
      .catch((err) => {
        // Sem acesso a métricas de infraestrutura a seção simplesmente não aparece.
        if (axios.isAxiosError(err) && err.response?.status === 403) setForbidden(true);
        else setError(formatApiError(err, "Não foi possível carregar a política de rightsizing"));
      });
  }, []);

  const aggressiveness = useMemo(() => (policy && params ? classify(policy, params) : "BALANCED"), [policy, params]);
  const dirty = !!policy && !!params && (Object.keys(params) as Key[]).some((key) => params[key] !== policy.params[key]);

  const save = async () => {
    if (!params) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await updateRightsizingPolicy(aggressiveness, params, note.trim());
      setPolicy(saved);
      setParams(saved.params);
      setNote("");
      setMessage(`Revisão ${saved.revision} gravada (${AGGRESSIVENESS_LABEL[saved.aggressiveness]}). Execute a análise em Rightsizing de infraestrutura para aplicá-la.`);
    } catch (err) {
      setError(formatApiError(err, "Não foi possível gravar a política"));
    } finally {
      setBusy(false);
    }
  };

  if (forbidden) return null;

  const renderField = (field: PolicyField) => {
    if (!params) return null;
    return <TextField
      key={field.key}
      label={field.label}
      type="number"
      size="small"
      value={field.toView(params[field.key])}
      inputProps={{ step: field.unit === "dias" ? 1 : 0.5 }}
      InputProps={{ endAdornment: <InputAdornment position="end">{field.unit}</InputAdornment> }}
      onChange={(event) => {
        if (event.target.value === "") return;
        const parsed = Number(event.target.value);
        if (Number.isFinite(parsed)) setParams({ ...params, [field.key]: field.fromView(parsed) });
      }}
      helperText={field.help}
    />;
  };

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
        <Typography variant="subtitle1" fontWeight={700} sx={{ flex: 1, minWidth: 240 }}>Parâmetros de rightsizing de infraestrutura</Typography>
        {policy && <Chip size="small" variant="outlined" label={policy.is_default ? "Padrão do motor (sem revisão gravada)" : `Revisão ${policy.revision}`} />}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Definem quanto o motor reduz ou aumenta cada máquina a partir de CPU e memória medidas. Valem para a próxima execução da análise;
        cada execução guarda a revisão e os valores usados.
        {policy && !policy.is_default && policy.updated_at && ` Última alteração por ${policy.updated_by ?? "—"} em ${new Date(policy.updated_at).toLocaleString("pt-BR")}.`}
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage(null)}>{message}</Alert>}

      {policy && params && <Stack spacing={2.5}>
        <Box>
          <Typography variant="subtitle2" gutterBottom>Agressividade</Typography>
          <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
            <ToggleButtonGroup exclusive size="small" color="primary" value={aggressiveness === "CUSTOM" ? null : aggressiveness}
                               onChange={(_, next: RightsizingAggressiveness | null) => { if (next && next !== "CUSTOM") setParams({ ...policy.presets[next] }); }}>
              {(["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const).map((name) => <ToggleButton key={name} value={name}>{AGGRESSIVENESS_LABEL[name]}</ToggleButton>)}
            </ToggleButtonGroup>
            {aggressiveness === "CUSTOM" && <Chip color="warning" variant="outlined" label="Personalizado" />}
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.75 }}>{AGGRESSIVENESS_HELP[aggressiveness]}</Typography>
        </Box>

        {GROUPS.map((group) => <Box key={group.title}>
          <Typography variant="subtitle2">{group.title}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>{group.summary}</Typography>
          <Box sx={fieldGrid}>
            {group.title === "Picos" && <FormControl size="small">
              <InputLabel>Picos de CPU considerados</InputLabel>
              <Select label="Picos de CPU considerados" value={String(params.cpu_peak_quantile)}
                      onChange={(event) => setParams({ ...params, cpu_peak_quantile: Number(event.target.value) })}>
                {policy.peak_quantiles.map((q) => <MenuItem key={q} value={String(q)}>{PEAK_QUANTILE_LABEL[String(q)] ?? `P${q}`}</MenuItem>)}
              </Select>
              <FormHelperText>Quais picos o novo tamanho precisa comportar. Ignorar mais picos raros = mais economia, mais momentos de fila.</FormHelperText>
            </FormControl>}
            {group.fields.map(renderField)}
          </Box>
        </Box>)}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ xs: "stretch", sm: "center" }}>
          <TextField size="small" label="Motivo da alteração (opcional)" value={note} onChange={(event) => setNote(event.target.value.slice(0, 500))} sx={{ flex: 1 }} />
          <Button variant="outlined" disabled={busy || !dirty} onClick={() => setParams(policy.params)}>Descartar</Button>
          <Button variant="contained" disabled={busy || !dirty} onClick={() => void save()}>Gravar política</Button>
        </Stack>
      </Stack>}
    </Paper>
  );
}
