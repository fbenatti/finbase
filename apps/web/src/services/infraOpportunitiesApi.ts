import { api } from "./api";

/** Tamanho neutro (vCPU/GB); `ocpu` e afins são unidades do provedor, só para exibição. */
export type FlexConfig = { sku: string; vcpus: number; memory_gb: number; ocpu?: number | null; memory_sized_from_telemetry?: boolean };

export type MetricSource = { id: number; code: string; name: string; connector_type: string; provider: string | null };

export async function listMetricSources(): Promise<MetricSource[]> {
  const { data } = await api.get("/custos/metricas-infra/oportunidades/fontes");
  return data.items;
}

export type SeriesStats = {
  samples: number;
  expected_buckets: number;
  coverage: number;
  days_observed: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  average: number | null;
  max: number | null;
  daily: { day: string; p95: number | null; max: number; average: number }[];
  quantile_method: string;
  percentile_basis: string;
};

export type OpportunityRun = {
  id: number;
  connection_id: number;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  engine_version: string;
  policy_version: string;
  /** Revisão da política gravada usada na execução (null = padrão do motor). */
  policy_revision: number | null;
  policy_aggressiveness: RightsizingAggressiveness | null;
  policy_params: RightsizingPolicyParams | null;
  window_start_utc: string;
  window_end_utc: string;
  resolution_seconds: number;
  resources_analyzed: number;
  opportunities_count: number;
  total_monthly_saving: number | null;
  avg_monthly_saving: number | null;
  currency: string | null;
  readiness_counts: Record<string, number>;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type Opportunity = {
  id: number;
  run_id: number;
  connection_id: number;
  provider: string;
  resource_id: string;
  resource_name: string | null;
  action_type: "RIGHTSIZE_DOWN" | "RIGHTSIZE_UP" | "KEEP";
  readiness_status: string;
  state: "NEW" | "ACCEPTED" | "IMPLEMENTED" | "DISMISSED";
  confidence: "HIGH" | "MEDIUM" | "LOW";
  from_config: FlexConfig | null;
  to_config: FlexConfig | null;
  current_monthly_cost: number | null;
  target_monthly_cost: number | null;
  monthly_saving: number | null;
  annual_saving: number | null;
  currency: string | null;
  cpu_p50: number | null;
  cpu_p95: number | null;
  cpu_p99: number | null;
  cpu_max: number | null;
  memory_p50: number | null;
  memory_p95: number | null;
  memory_p99: number | null;
  memory_max: number | null;
  coverage: number | null;
  risks: string[];
  state_note: string | null;
  state_changed_at: string | null;
  created_at: string | null;
  inputs_hash: string;
  engine_version: string;
  /** Picos recorrentes: saving extra condicionado a limitar/reagendar o job. */
  conditional_action_type: "SCHEDULE_OR_CAP_JOB" | null;
  conditional_monthly_saving: number | null;
  conditional_state: Opportunity["state"] | null;
  conditional_state_note: string | null;
  conditional_to_config: FlexConfig | null;
  conditional_confidence: Opportunity["confidence"] | null;
  recurring_peaks_label: string | null;
};

export type RecurringPeakWindow = {
  hours: number[];
  /** 0 = segunda … 6 = domingo; null = todos os dias. */
  weekdays: number[] | null;
  start: string;
  end: string;
  days_with_peak: number;
  days_observed: number;
  day_ratio: number;
  typical_duration_minutes: number;
  typical_peak: number;
  cpu_time_share: number;
};

export type RecurringPeaks = {
  detection: {
    detected: boolean;
    timezone: string;
    windows: RecurringPeakWindow[];
    threshold?: number;
    peak_time_share?: number;
    episode_time_share?: number;
    cpu_time_share?: number;
    label?: string;
    reason?: string;
    note?: string;
  };
  conditional: {
    action_type: "SCHEDULE_OR_CAP_JOB";
    readiness_status: string;
    confidence: Opportunity["confidence"];
    to_config: FlexConfig;
    sizing_basis: { cpu: string; memory: string; required_vcpus: number; required_memory_gb: number };
    cpu_without_recurring_peaks: { p95?: number; p99?: number; max?: number; max_p99?: number; max_p995?: number };
    projected_cpu_peak_p995: number;
    target_monthly_cost: number | null;
    monthly_saving: number | null;
    incremental_monthly_saving: number | null;
    baseline_vcpus_p95: number;
    suggested_cap_vcpus: number;
    suggested_cpu_quota_percent: number;
    estimated_job_minutes_capped: number;
    suggested_actions: string[];
    rationale: string[];
  } | null;
};

export type OpportunityDetail = Opportunity & {
  rationale: string[];
  evidence: {
    cpu: SeriesStats | null;
    memory: SeriesStats | null;
    capacity: Record<string, unknown>;
    /** Base do preço de cada provedor: preços unitários, fórmula e origem. */
    prices: {
      currency?: string | null;
      billing_period?: string;
      unit_prices?: Record<string, number>;
      meters?: Record<string, string>;
      formula?: string;
      source?: string;
    } | null;
    policy: Record<string, number | string>;
    projected_utilization?: Record<string, number>;
    recurring_peaks?: RecurringPeaks;
  };
  history: {
    run_id: number;
    finished_at: string | null;
    action_type: string;
    to_config: FlexConfig | null;
    monthly_saving: number | null;
    readiness_status: string;
    window_start_utc: string | null;
    window_end_utc: string | null;
  }[];
};

export type OpportunitySummary = {
  run: OpportunityRun | null;
  completed_runs: number;
  avg_saving_per_run: number | null;
  currency?: string | null;
  resources_analyzed?: number;
  opportunities?: number;
  projected_saving_monthly?: number;
  projected_saving_annual?: number;
  implemented_saving_monthly?: number;
  accepted_saving_monthly?: number;
  dismissed_saving_monthly?: number;
  conditional_opportunities?: number;
  conditional_saving_monthly?: number;
  conditional_pending_saving_monthly?: number;
  conditional_accepted_saving_monthly?: number;
  avg_saving_per_opportunity?: number | null;
  analyzed_monthly_cost?: number;
  saving_ratio?: number | null;
  by_action?: Record<string, number>;
  by_state?: Record<string, number>;
  by_readiness?: Record<string, number>;
  by_confidence?: Record<string, number>;
};

export type OpportunitySeries = {
  resolution_seconds: number;
  window_start_utc: string;
  window_end_utc: string;
  from_config: FlexConfig | null;
  to_config: FlexConfig | null;
  points: { t: string; cpu_avg?: number | null; cpu_max?: number | null; memory_avg?: number | null; memory_max?: number | null }[];
};

/** Perfil horário (UTC) de cada recurso da execução: P95 das médias e pico de cada hora. */
export type HourlyProfile = {
  opportunity_id: number;
  resource_id: string;
  cpu_p95: (number | null)[];
  cpu_peak: (number | null)[];
  cpu_hours: number;
  memory_p95: (number | null)[];
  memory_peak: (number | null)[];
  memory_hours: number;
};

export type HourlyProfiles = {
  run_id: number | null;
  window_start_utc?: string;
  window_end_utc?: string;
  resolution_seconds?: number;
  /** Horas da janela em UTC, "YYYY-MM-DDTHH", alinhadas com as listas de cada recurso. */
  hours: string[];
  items: HourlyProfile[];
};

const BASE = "/custos/metricas-infra/oportunidades";

export async function getHourlyProfiles(connectionId: number, runId?: number): Promise<HourlyProfiles> {
  const { data } = await api.get(`${BASE}/perfis-horarios`, { params: { connection_id: connectionId, run_id: runId }, timeout: 200_000 });
  return data;
}

export async function getOpportunitySeries(id: number, resolutionSeconds: number): Promise<OpportunitySeries> {
  const { data } = await api.get(`${BASE}/${id}/serie`, { params: { resolution_seconds: resolutionSeconds } });
  return data;
}

export async function getOpportunitySummary(connectionId: number): Promise<OpportunitySummary> {
  const { data } = await api.get(`${BASE}/resumo`, { params: { connection_id: connectionId } });
  return data;
}

export async function listOpportunities(connectionId: number, runId?: number): Promise<{ run_id: number | null; items: Opportunity[] }> {
  const { data } = await api.get(BASE, { params: { connection_id: connectionId, run_id: runId } });
  return data;
}

export async function listOpportunityRuns(connectionId: number): Promise<{ items: OpportunityRun[] }> {
  const { data } = await api.get(`${BASE}/execucoes`, { params: { connection_id: connectionId } });
  return data;
}

export async function getOpportunity(id: number): Promise<OpportunityDetail> {
  const { data } = await api.get(`${BASE}/${id}`);
  return data;
}

export async function runOpportunityEngine(connectionId: number, days: number, resolutionSeconds: number): Promise<OpportunityRun> {
  const { data } = await api.post(`${BASE}/executar`, { connection_id: connectionId, days, resolution_seconds: resolutionSeconds }, { timeout: 300_000 });
  return data;
}

export async function setOpportunityState(id: number, state: Opportunity["state"], note?: string): Promise<OpportunityDetail> {
  const { data } = await api.post(`${BASE}/${id}/estado`, { state, note: note || null });
  return data;
}

export type RightsizingAggressiveness = "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE" | "CUSTOM";

export type RightsizingPolicyParams = {
  cpu_target_p95: number;
  cpu_target_p99: number;
  memory_target_p95: number;
  memory_target_p99: number;
  cpu_peak_quantile: number;
  cpu_peak_limit: number;
  memory_peak_limit: number;
  min_change_ratio: number;
  min_days: number;
  min_coverage: number;
  high_confidence_coverage: number;
};

export type RightsizingPolicy = {
  policy_version: string;
  revision: number | null;
  aggressiveness: RightsizingAggressiveness;
  params: RightsizingPolicyParams;
  note: string | null;
  updated_by: string | null;
  updated_at: string | null;
  is_default: boolean;
  presets: Record<Exclude<RightsizingAggressiveness, "CUSTOM">, RightsizingPolicyParams>;
  peak_quantiles: number[];
};

const POLICY = "/custos/metricas-infra/politica-rightsizing";

export async function getRightsizingPolicy(): Promise<RightsizingPolicy> {
  const { data } = await api.get(POLICY);
  return data;
}

export async function updateRightsizingPolicy(
  aggressiveness: RightsizingAggressiveness, params: RightsizingPolicyParams, note?: string,
): Promise<RightsizingPolicy> {
  const { data } = await api.put(POLICY, { aggressiveness, params, note: note || null });
  return data;
}

export async function setConditionalState(id: number, state: Opportunity["state"], note?: string): Promise<OpportunityDetail> {
  const { data } = await api.post(`${BASE}/${id}/estado-condicional`, { state, note: note || null });
  return data;
}
