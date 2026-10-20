import { useState } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Paper, Stack, TextField, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { AnomalyConfig } from "../../services/anomaliesApi";
import { updateAnomalyConfig } from "../../services/anomaliesApi";

type Props = {
  config: AnomalyConfig;
  onSaved: () => void;
  busy: boolean;
  setBusy: (v: boolean) => void;
};

type SettingField = { key: string; label: string; help: string; step?: number; percent?: boolean };
const groups: Array<{ title: string; summary: string; fields: SettingField[] }> = [
  {
    title: "Períodos e linha de base",
    summary: "Define quanto histórico o motor lê, qual janela procura episódios e quais dias usa para calcular o consumo esperado.",
    fields: [
      { key: "detection_lookback_days", label: "Janela de busca de episódios (dias)", help: "Período recente em que o detector procura picos e episódios contínuos." },
      { key: "baseline_level_days", label: "Nível esperado (dias)", help: "Janela anterior ao evento usada para estimar o consumo típico." },
      { key: "baseline_variability_days", label: "Variação e padrão semanal (dias)", help: "Histórico usado para medir dispersão e fatores por dia da semana." },
      { key: "minimum_weekday_samples", label: "Amostras mínimas por dia da semana", help: "Quantidade de ocorrências necessária antes de ajustar o padrão daquele dia." },
      { key: "robust_z_threshold", label: "Multiplicador do limite robusto (z)", help: "Quanto a dispersão MAD deve ser multiplicada para formar a faixa esperada.", step: 0.1 },
      { key: "robust_mad_scale", label: "Escala MAD", help: "Converte a dispersão mediana absoluta para a escala robusta usada pela faixa.", step: 0.01 },
    ],
  },
  {
    title: "Classificação do tipo de gasto",
    summary: "A classificação é automática. Estes limites determinam quando o motor considera um gasto novo, intermitente, contínuo ou compromisso.",
    fields: [
      { key: "classification_window_days", label: "Janela de classificação (dias)", help: "Histórico em que o motor mede dias com consumo para classificar a natureza do gasto." },
      { key: "new_spend_min_history_days", label: "Histórico mínimo para gasto conhecido (dias)", help: "Abaixo deste histórico, o gasto pode ser classificado como novo." },
      { key: "high_confidence_history_days", label: "Histórico para confiança alta (dias)", help: "Séries mais curtas recebem confiança baixa, que reduz a nota." },
      { key: "intermittent_zero_ratio", label: "Dias sem gasto para classificar como intermitente (%)", help: "Fração mínima de dias sem custo dentro da janela de classificação.", step: 1, percent: true },
      { key: "monthly_comparison_periods", label: "Meses comparáveis", help: "Quantidade de períodos anteriores comparados no detector de gasto intermitente." },
      { key: "new_spend_alert_materiality_multiple", label: "Múltiplo para alerta de gasto novo", help: "Um gasto novo precisa atingir este múltiplo da materialidade para virar alerta.", step: 0.1 },
      { key: "monthly_alert_materiality_multiple", label: "Múltiplo para alerta mensal", help: "Excesso mensal acima deste múltiplo da materialidade vira alerta; abaixo fica em observação.", step: 0.1 },
      { key: "commitment_history_days", label: "Histórico de compromissos (dias)", help: "Janela usada para reconhecer compras e compromissos recorrentes." },
      { key: "commitment_date_tolerance_days", label: "Tolerância de data do compromisso (dias)", help: "Variação permitida em torno do dia esperado da cobrança." },
      { key: "commitment_value_tolerance_pct", label: "Tolerância de valor do compromisso (%)", help: "Variação aceitável em torno do valor esperado da cobrança.", step: 1, percent: true },
    ],
  },
  {
    title: "Materialidade, persistência e qualidade dos dados",
    summary: "Evita abrir casos para desvios pequenos, isolados ou causados por carga incompleta.",
    fields: [
      { key: "recovery_confirmation_days", label: "Dias válidos para confirmar retorno", help: "Dias consecutivos dentro da faixa necessários para marcar o desvio como corrigido." },
      { key: "relapse_watch_days", label: "Acompanhamento de reincidência (dias)", help: "Janela para observar se o desvio reaparece após retornar à faixa." },
      { key: "load_quality_lookback_days", label: "Histórico de qualidade da carga (dias)", help: "Dias anteriores usados para estimar completude da coleta." },
      { key: "load_completeness_threshold", label: "Completude mínima da carga (%)", help: "Abaixo desta fração, o sinal perde tier por possível carga parcial.", step: 1, percent: true },
      { key: "persistence_gate_min_days", label: "Persistência mínima para pico (dias)", help: "Picos curtos abaixo do múltiplo reforçado ficam em observação." },
      { key: "persistence_gate_materiality_multiple", label: "Múltiplo reforçado para pico curto", help: "Um pico curto só passa o filtro se o custo superar este múltiplo da materialidade.", step: 0.1 },
      { key: "one_off_spike_max_days", label: "Duração máxima de pico isolado (dias)", help: "Até esta duração, o pico não é projetado como custo mensal recorrente." },
    ],
  },
  {
    title: "Nota, confiança e severidade",
    summary: "A nota mede prioridade operacional, não probabilidade. Os pesos são normalizados e a soma deles define a contribuição relativa.",
    fields: [
      { key: "score_impact_weight_pct", label: "Peso do impacto (%)", help: "Importância do valor excedente/projetado na nota.", step: 1 },
      { key: "score_surprise_weight_pct", label: "Peso da surpresa estatística (%)", help: "Importância de quão distante o custo ficou da faixa esperada.", step: 1 },
      { key: "score_persistence_weight_pct", label: "Peso da persistência (%)", help: "Importância da duração do episódio na nota.", step: 1 },
      { key: "score_impact_log_scale", label: "Escala logarítmica do impacto", help: "Controla quanto impacto financeiro eleva a componente de impacto.", step: 1 },
      { key: "score_impact_saturation", label: "Teto das componentes da nota", help: "Valor máximo de cada componente antes de aplicar os pesos." },
      { key: "score_surprise_points_per_z", label: "Pontos por unidade z", help: "Velocidade com que a surpresa estatística atinge o teto da componente.", step: 1 },
      { key: "score_persistence_saturation_days", label: "Dias para saturar persistência", help: "Duração que leva a componente de persistência ao teto." },
      { key: "score_confidence_high_factor", label: "Fator de confiança alta (%)", help: "Multiplicador final da nota quando o histórico sustenta confiança alta.", step: 1, percent: true },
      { key: "score_confidence_medium_factor", label: "Fator de confiança média (%)", help: "Multiplicador final da nota quando a confiança é média.", step: 1, percent: true },
      { key: "score_confidence_low_factor", label: "Fator de confiança baixa (%)", help: "Multiplicador final da nota quando a confiança é baixa.", step: 1, percent: true },
      { key: "severity_medium_min", label: "Severidade média a partir de (moeda da configuração)", help: "Impacto financeiro projetado mínimo para severidade média.", step: 10 },
      { key: "severity_high_min", label: "Severidade alta a partir de (moeda da configuração)", help: "Impacto financeiro projetado mínimo para severidade alta.", step: 10 },
      { key: "severity_critical_min", label: "Severidade crítica a partir de (moeda da configuração)", help: "Impacto financeiro projetado mínimo para severidade crítica.", step: 10 },
    ],
  },
  {
    title: "Período financeiro projetado",
    summary: "Converte o ritmo de excesso diário em projeções mensais e anuais.",
    fields: [
      { key: "projection_days_month", label: "Dias por mês de projeção", help: "Fator usado para transformar ritmo diário em impacto mensal.", step: 0.1 },
      { key: "projection_days_year", label: "Dias por ano de projeção", help: "Fator usado para transformar ritmo diário em impacto anual.", step: 1 },
      { key: "impact_run_rate_lookback_days", label: "Dias para calcular ritmo recente", help: "Quantidade de dias finais usada na mediana do ritmo diário após o episódio.", step: 1 },
    ],
  },
];

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
      onChange={(event) => changeSetting(field.key, event.target.value, field.percent)}
      helperText={field.help}
      sx={{ minWidth: 240, flex: "1 1 280px" }}
    />;
  };

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        <strong>Como a detecção funciona:</strong> o motor lê uma janela de histórico, classifica automaticamente cada escopo pelo padrão de consumo e compara o realizado com uma linha de base robusta. Apenas sinais que passam materialidade, desvio mínimo, qualidade da carga e nota mínima viram casos. Cada execução guarda um snapshot dos parâmetros usados.
        <br />Motor ativo: <strong>{config.detection_engine ?? "v2"}</strong>. Alterações só entram em vigor na próxima execução.
      </Alert>

      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={700}>Escopo de análise e limites principais</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>O histórico total precisa cobrir a janela de detecção e a linha de base. A materialidade é expressa na moeda desta configuração.</Typography>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} flexWrap="wrap">
          <TextField label="Histórico total analisado (dias)" type="number" size="small" value={historyDays} onChange={(e) => setHistoryDays(e.target.value)} helperText="Período total carregado e avaliado em cada execução." />
          <TextField label="Atraso da fonte (dias)" type="number" size="small" value={lag} onChange={(e) => setLag(e.target.value)} helperText="Desloca a data final para acomodar atrasos da carga." />
          <TextField label="Desvio relativo mínimo (%)" type="number" size="small" value={Number(minimumDeviation) * 100} onChange={(e) => setMinimumDeviation(String(Number(e.target.value) / 100))} helperText="Diferença mínima entre realizado e esperado para investigar." />
          <TextField label="Nota mínima para investigar" type="number" size="small" value={investigateScore} onChange={(e) => setInvestigateScore(e.target.value)} helperText="Alertas abaixo deste score viram sinais em observação, sem abrir caso." />
          <TextField label={`Materialidade diária (${config.currency})`} type="number" size="small" value={dailyMateriality} onChange={(e) => setDailyMateriality(e.target.value)} helperText="Valor mínimo diário para considerar um desvio relevante." />
          <TextField label={`Materialidade mensal (${config.currency})`} type="number" size="small" value={monthlyMateriality} onChange={(e) => setMonthlyMateriality(e.target.value)} helperText="Vazio usa seis vezes a materialidade diária." />
        </Stack>
      </Paper>

      {groups.map((group) => <Accordion key={group.title} disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box><Typography fontWeight={700}>{group.title}</Typography><Typography variant="caption" color="text.secondary">{group.summary}</Typography></Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack direction="row" flexWrap="wrap" gap={1.5}>{group.fields.map(renderField)}</Stack>
        </AccordionDetails>
      </Accordion>)}

      <Stack direction="row" alignItems="center" spacing={2} sx={{ mt: 2 }}>
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
            setError(err instanceof Error ? err.message : "Falha ao salvar as configurações");
          } finally {
            setBusy(false);
          }
        }}>Salvar configurações</Button>
        <Typography variant="caption" color="text.secondary">Versão atual da configuração: {config.version}</Typography>
      </Stack>
      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
    </Box>
  );
}
