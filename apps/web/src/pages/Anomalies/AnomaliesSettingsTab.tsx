import { dataDisplayText } from "../../utils/dataPresentation";
import { useState } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, InputAdornment, Paper, Stack, TextField, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMoreOutlined";
import type { AnomalyConfig } from "../../services/anomaliesApi";
import { updateAnomalyConfig } from "../../services/anomaliesApi";

type Props = {
  config: AnomalyConfig;
  onSaved: () => void;
  busy: boolean;
  setBusy: (v: boolean) => void;
};

/** `unit` aparece como sufixo do campo. `percent` guarda a fração e mostra em %. */
type SettingField = { key: string; label: string; help: string; unit?: string; step?: number; percent?: boolean };
type SettingGroup = { title: string; summary: string; fields: SettingField[] };

const DAYS = "dias";
const MULT = "× materialidade";

const groups: SettingGroup[] = [
  {
    title: "Períodos e referência",
    summary: "Quanto histórico o motor lê, qual janela procura episódios e quais dias formam o consumo esperado.",
    fields: [
      { key: "detection_lookback_days", label: "Janela de busca de episódios", unit: DAYS, help: "Período recente em que o detector busca picos e episódios contínuos." },
      { key: "baseline_level_days", label: "Nível esperado", unit: DAYS, help: "Janela anterior ao evento usada para estimar o gasto típico." },
      { key: "baseline_variability_days", label: "Variação e padrão semanal", unit: DAYS, help: "Histórico usado para medir a dispersão e os fatores por dia da semana." },
      { key: "minimum_weekday_samples", label: "Quantidade mínima de amostras por dia da semana", unit: "ocorrências", help: "Ocorrências necessárias antes de ajustar o padrão daquele dia." },
      { key: "robust_z_threshold", label: "Multiplicador do limite robusto", unit: "z", step: 0.1, help: "Quanto a dispersão MAD é multiplicada para formar a faixa esperada." },
      { key: "robust_mad_scale", label: "Escala MAD (desvio absoluto mediano)", step: 0.01, help: "Converte a dispersão mediana absoluta para a escala robusta da faixa." },
    ],
  },
  {
    title: "Critério de classificação do gasto",
    summary: "A classificação é automática. Estes limites definem quando um gasto é novo, intermitente, contínuo ou compromisso.",
    fields: [
      { key: "classification_window_days", label: "Janela de classificação", unit: DAYS, help: "Histórico em que o motor conta dias com consumo para classificar a natureza do gasto." },
      { key: "new_spend_min_history_days", label: "Histórico mínimo para gasto conhecido", unit: DAYS, help: "Abaixo deste histórico, o gasto pode ser tratado como novo." },
      { key: "high_confidence_history_days", label: "Histórico para confiança alta", unit: DAYS, help: "Séries mais curtas recebem confiança baixa, o que reduz a nota." },
      { key: "intermittent_zero_ratio", label: "Dias sem gasto para intermitente", unit: "%", step: 1, percent: true, help: "Fração mínima de dias sem custo dentro da janela de classificação." },
      { key: "monthly_comparison_periods", label: "Meses de comparação", unit: "meses", help: "Períodos anteriores comparados pelo detector de gasto intermitente." },
      { key: "new_spend_alert_materiality_multiple", label: "Multiplicador para alerta de gasto novo", unit: MULT, step: 0.1, help: "Um gasto novo precisa atingir este multiplicador da materialidade para virar alerta." },
      { key: "monthly_alert_materiality_multiple", label: "Multiplicador para alerta mensal", unit: MULT, step: 0.1, help: "Excesso mensal acima deste múltiplo vira alerta; abaixo fica em observação." },
      { key: "commitment_history_days", label: "Histórico de compromissos", unit: DAYS, help: "Janela usada para identificar compras e compromissos recorrentes." },
      { key: "commitment_date_tolerance_days", label: "Tolerância de data do compromisso", unit: DAYS, help: "Variação aceita em torno do dia previsto da cobrança." },
      { key: "commitment_value_tolerance_pct", label: "Tolerância de valor do compromisso", unit: "%", step: 1, percent: true, help: "Variação aceitável em torno do valor esperado da cobrança." },
    ],
  },
  {
    title: "Materialidade, persistência e qualidade da carga",
    summary: "Impede abrir casos para desvios pequenos, isolados ou causados por carga incompleta.",
    fields: [
      { key: "recovery_confirmation_days", label: "Dias válidos para confirmar a volta ao normal", unit: DAYS, help: "Dias consecutivos dentro da faixa para marcar o desvio como corrigido." },
      { key: "relapse_watch_days", label: "Acompanhamento de reincidência", unit: DAYS, help: "Janela para observar se o desvio reaparece após voltar à faixa." },
      { key: "load_quality_lookback_days", label: "Histórico de qualidade da carga", unit: DAYS, help: "Dias anteriores usados para estimar a completude da coleta." },
      { key: "load_completeness_threshold", label: "Completude mínima da carga", unit: "%", step: 1, percent: true, help: "Abaixo deste valor o sinal perde tier por possível carga parcial." },
      { key: "persistence_gate_min_days", label: "Persistência mínima para pico", unit: DAYS, help: "Picos curtos abaixo do multiplicador reforçado ficam em observação." },
      { key: "persistence_gate_materiality_multiple", label: "Multiplicador reforçado para pico curto", unit: MULT, step: 0.1, help: "Um pico curto só passa se superar este múltiplo da materialidade." },
      { key: "one_off_spike_max_days", label: "Duração máxima de pico isolado", unit: DAYS, help: "Até esta duração, o pico não entra na projeção como custo mensal recorrente." },
    ],
  },
  {
    title: "Score, confiança e gravidade",
    summary: "A nota mede prioridade operacional, não probabilidade. Os pesos são normalizados, e a soma deles define a contribuição relativa.",
    fields: [
      { key: "score_impact_weight_pct", label: "Peso do impacto", unit: "%", step: 1, percent: true, help: "Importância do valor excedente ou projetado na nota." },
      { key: "score_surprise_weight_pct", label: "Peso da surpresa estatística", unit: "%", step: 1, percent: true, help: "Quão distante o custo ficou da faixa esperada." },
      { key: "score_persistence_weight_pct", label: "Peso da persistência", unit: "%", step: 1, percent: true, help: "Importância da duração do episódio na nota." },
      { key: "score_impact_log_scale", label: "Impacto em escala logarítmica", step: 1, help: "Quanto o impacto financeiro eleva a componente de impacto." },
      { key: "score_impact_saturation", label: "Limite dos componentes da nota", help: "Valor máximo de cada componente antes da ponderação." },
      { key: "score_surprise_points_per_z", label: "Pontos por unidade de z-score", unit: "pts/z", step: 1, help: "Velocidade com que a surpresa estatística atinge o limite do componente." },
      { key: "score_persistence_saturation_days", label: "Dias para saturar a persistência", unit: DAYS, help: "Duração que leva o componente de persistência ao limite máximo." },
      { key: "score_confidence_high_factor", label: "Fator de confiança alta", unit: "%", step: 1, percent: true, help: "Multiplicador final da nota quando o histórico sustenta confiança alta." },
      { key: "score_confidence_medium_factor", label: "Fator de confiança média", unit: "%", step: 1, percent: true, help: "Multiplicador final da nota quando a confiança é média." },
      { key: "score_confidence_low_factor", label: "Fator de confiança baixa", unit: "%", step: 1, percent: true, help: "Multiplicador final da nota quando a confiança é baixa." },
      { key: "score_confidence_unknown_factor", label: "Fator de confiança indisponível", unit: "%", step: 1, percent: true, help: "Multiplicador final quando não há classificação de confiança." },
      { key: "severity_medium_min", label: "Gravidade média a partir de", unit: "moeda", step: 10, help: "Impacto financeiro projetado mínimo para gravidade média." },
      { key: "severity_high_min", label: "Gravidade alta a partir de", unit: "moeda", step: 10, help: "Impacto financeiro projetado mínimo para gravidade alta." },
      { key: "severity_critical_min", label: "Gravidade crítica a partir de", unit: "moeda", step: 10, help: "Impacto financeiro projetado mínimo para gravidade crítica." },
    ],
  },
  {
    title: "Período da projeção financeira",
    summary: "Converte o excesso diário em projeções por mês e por ano.",
    fields: [
      { key: "projection_days_month", label: "Dias por mês na projeção", unit: DAYS, step: 0.1, help: "Fator usado para transformar o ritmo diário em impacto mensal." },
      { key: "projection_days_year", label: "Dias por ano na projeção", unit: DAYS, step: 1, help: "Fator usado para transformar o ritmo diário em impacto anual." },
      { key: "impact_run_rate_lookback_days", label: "Dias para calcular o ritmo recente", unit: DAYS, step: 1, help: "Dias finais usados na mediana do ritmo diário após o episódio." },
    ],
  },
];

const STEPS = [
  { title: "Ler o histórico", text: "Carrega a janela de dados configurada, descontando o atraso da fonte." },
  { title: "Classificar o escopo", text: "Define pelo padrão de consumo se cada escopo é novo, intermitente, contínuo ou compromisso." },
  { title: "Comparar com a linha de base", text: "Compara o efetivado com uma linha de base robusta e a faixa esperada." },
  { title: "Filtrar e abrir casos", text: "Só sinais que passam materialidade, desvio mínimo, qualidade da carga e nota mínima viram casos." },
];

const fieldGrid = { display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" }, gap: 2 } as const;

export default function AnomaliesSettingsTab({ config, onSaved, busy, setBusy }: Props) {
  const [lag, setLag] = useState(String(config.data_lag_days ?? 2));
  const [historyDays, setHistoryDays] = useState(String(config.history_days ?? 90));
  const [minimumDeviation, setMinimumDeviation] = useState(String(config.min_relative_deviation ?? 0.2));
  const [investigateScore, setInvestigateScore] = useState(String(config.investigate_score ?? 55));
  const [dailyMateriality, setDailyMateriality] = useState(String(config.materiality_by_currency?.[config.currency] ?? 100));
  const [monthlyMateriality, setMonthlyMateriality] = useState(String(config.materiality_monthly ?? ""));
  const [settings, setSettings] = useState<Record<string, number>>({ ...config.detector_settings });
  const [error, setError] = useState<string | null>(null);

  const changeSetting = (key: string, value: string, percent = false) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    setSettings((current) => ({ ...current, [key]: percent ? parsed / 100 : parsed }));
  };

  const renderField = (field: SettingField) => {
    const stored = settings[field.key] ?? 0;
    const value = field.percent ? stored * 100 : stored;
    return <TextField
      key={field.key}
      label={field.label}
      type="number"
      size="small"
      value={Number.isInteger(value) ? value : Number(value.toFixed(4))}
      inputProps={{ step: field.step ?? 1 }}
      InputProps={field.unit ? { endAdornment: <InputAdornment position="end">{field.unit === "moeda" ? config.currency : field.unit}</InputAdornment> } : undefined}
      onChange={(event) => changeSetting(field.key, event.target.value, field.percent)}
      helperText={field.help}
    />;
  };

  return (
    <Stack spacing={3}>
      <Paper variant="outlined" sx={{ p: 2.5 }}>
        <Typography variant="subtitle1" fontWeight={700}>Como a detecção funciona</Typography>
        <Box sx={{ display: "grid", gap: 2, mt: 1.5, gridTemplateColumns: { xs: "1fr", md: "repeat(4, minmax(0, 1fr))" } }}>
          {STEPS.map((step, index) => (
            <Box key={step.title} sx={{ display: "flex", gap: 1.25 }}>
              <Typography variant="body2" sx={{ fontWeight: 700, color: "primary.main", minWidth: 16 }}>{index + 1}</Typography>
              <Box>
                <Typography variant="body2" fontWeight={600}>{step.title}</Typography>
                <Typography variant="caption" color="text.secondary">{step.text}</Typography>
              </Box>
            </Box>
          ))}
        </Box>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2.5, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 2 }}>
        <Box sx={{ flex: 1, minWidth: 240 }}>
          <Typography variant="subtitle2" fontWeight={700}>Motor de detecção</Typography>
          <Typography variant="caption" color="text.secondary">
            Alterações entram em vigor na próxima execução. Cada execução guarda um snapshot dos parâmetros usados.
          </Typography>
        </Box>
        <Chip color="success" variant="outlined" label={`Motor ativo: ${config.detection_engine ?? "v2"}`} />
        <Chip variant="outlined" label={`Versão da configuração: ${config.version}`} />
      </Paper>

      <Paper variant="outlined" sx={{ p: 2.5 }}>
        <Typography variant="subtitle1" fontWeight={700}>Escopo da análise e limites principais</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          O histórico total precisa cobrir a janela de detecção e a linha de base. A materialidade é expressa na moeda desta configuração.
        </Typography>
        <Box sx={fieldGrid}>
          <TextField label="Histórico total analisado" type="number" value={historyDays} onChange={(e) => setHistoryDays(e.target.value)}
            InputProps={{ endAdornment: <InputAdornment position="end">dias</InputAdornment> }}
            helperText="Período carregado e avaliado em cada execução." />
          <TextField label="Atraso da fonte" type="number" value={lag} onChange={(e) => setLag(e.target.value)}
            InputProps={{ endAdornment: <InputAdornment position="end">dias</InputAdornment> }}
            helperText="Adia a data final para absorver atrasos da carga." />
          <TextField label="Variação relativa mínima" type="number" value={Number(minimumDeviation) * 100} onChange={(e) => setMinimumDeviation(String(Number(e.target.value) / 100))}
            InputProps={{ endAdornment: <InputAdornment position="end">%</InputAdornment> }}
            helperText="Diferença mínima entre efetivado e esperado para apurar." />
          <TextField label="Score mínimo para apurar" type="number" value={investigateScore} onChange={(e) => setInvestigateScore(e.target.value)}
            InputProps={{ endAdornment: <InputAdornment position="end">pts</InputAdornment> }}
            helperText="Alertas abaixo deste score viram sinais de observação, sem abrir caso." />
          <TextField label="Materialidade diária" type="number" value={dailyMateriality} onChange={(e) => setDailyMateriality(e.target.value)}
            InputProps={{ endAdornment: <InputAdornment position="end">{config.currency}</InputAdornment> }}
            helperText="Valor mínimo diário para considerar uma variação relevante." />
          <TextField label="Materialidade mensal" type="number" value={monthlyMateriality} onChange={(e) => setMonthlyMateriality(e.target.value)}
            InputProps={{ endAdornment: <InputAdornment position="end">{config.currency}</InputAdornment> }}
            helperText="Em branco usa seis vezes a materialidade diária." />
        </Box>
      </Paper>

      <Box>
        {groups.map((group) => <Accordion key={group.title} disableGutters variant="outlined" sx={{ "&:not(:last-of-type)": { mb: 1 } }}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Box>
              <Typography fontWeight={700}>{group.title}</Typography>
              <Typography variant="caption" color="text.secondary">{group.summary}</Typography>
            </Box>
          </AccordionSummary>
          <AccordionDetails sx={{ pt: 0, pb: 2.5 }}>
            <Box sx={fieldGrid}>{group.fields.map(renderField)}</Box>
          </AccordionDetails>
        </Accordion>)}
      </Box>

      <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap" useFlexGap>
        <Button variant="contained" disabled={busy} onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            const daily = Number(dailyMateriality);
            const monthly = monthlyMateriality.trim() ? Number(monthlyMateriality) : daily * 6;
            await updateAnomalyConfig(config.id, {
              data_lag_days: Number(lag), history_days: Number(historyDays),
              min_relative_deviation: Number(minimumDeviation), investigate_score: Number(investigateScore),
              materiality_by_currency: { ...config.materiality_by_currency, [config.currency]: daily },
              materiality_monthly: monthly, detector_settings: settings,
            });
            onSaved();
          } catch (err) {
            setError(err instanceof Error ? err.message : "Não foi possível gravar as configurações");
          } finally {
            setBusy(false);
          }
        }}>Gravar configurações</Button>
      </Stack>
      {error && <Alert severity="error">{dataDisplayText(error)}</Alert>}
    </Stack>
  );
}
