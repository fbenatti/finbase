import { api } from "./api";

export type AnomalyConfig = {
  id: number;
  name: string;
  description: string | null;
  forecast_config_id: number | null;
  measure_column: string;
  currency: string;
  profile: string;
  daily_quantile: number;
  min_relative_deviation: number;
  investigate_score: number;
  monthly_tolerance_pct: number;
  materiality_by_currency: Record<string, number>;
  history_days: number;
  calibration_days: number;
  horizon_days: number;
  data_lag_days: number;
  family_keys: string[];
  routing_rules: unknown[];
  sla_hours: Record<string, number>;
  notification_enabled: boolean;
  enabled: boolean;
  version: number;
  source_namespace: string;
  destination_physical_name: string;
  detection_engine?: string;
  materiality_monthly?: number | null;
};

export type ImpactSummary = {
  detected: number;
  by_deviation_status: Record<string, number>;
  incurred_excess_cost: number;
  projected_saving_monthly: number;
  projected_saving_annual: number;
  open_exposure_monthly: number;
  open_exposure_annual: number;
};

export type AnomalyCase = {
  id: number;
  case_key: string;
  state: string;
  severity: string;
  score: number;
  confidence: string;
  event_type: string;
  event_family: string;
  event_date: string;
  period: string;
  source_billing_period: string | null;
  series_id: string;
  series_dimensions: Record<string, string | null>;
  observed_cloud: number | null;
  expected_cloud: number | null;
  lower_cloud: number | null;
  upper_cloud: number | null;
  delta_cloud: number | null;
  anomalous_cost_cloud: number;
  confirmed_anomalous_cost_cloud?: number;
  event_period_start?: string | null;
  event_period_end?: string | null;
  evaluation_grain: string;
  current_reference_id: string | null;
  current_assessment_id: string | null;
  reference_kind: string | null;
  reason_codes: string[];
  applied_rule_refs: Array<{ name: string; status: string; detail?: string | null }>;
  data_quality: string;
  assignee_user_id: number | null;
  suggested_owner_id?: number | null;
  cloud?: string | null;
  subaccount_name?: string | null;
  resource_group?: string | null;
  service_name?: string | null;
  region?: string | null;
  detector?: string | null;
  deviation_status?: string | null;
  projected_month_impact?: number | null;
  impact_to_date?: number | null;
  explanation?: Array<Record<string, unknown>> | null;
  routing_rule_id: string | null;
  due_at: string | null;
  acknowledged_at: string | null;
  investigation_started_at: string | null;
  cause_category: string | null;
  cause_hypothesis: string | null;
  decision: string | null;
  decision_note: string | null;
  decided_at: string | null;
  remediation_started_at: string | null;
  verification_started_at: string | null;
  verification_succeeded_at: string | null;
  valid_days_streak: number;
  retrospective_at: string | null;
  learning: string | null;
  preventive_measure: string | null;
  no_preventive_measure_reason: string | null;
  closed_at: string | null;
  reopen_count: number;
  case_version: number;
  first_detected_at: string | null;
  steps?: Array<Record<string, unknown>>;
  actions?: AnomalyAction[];
};

export type AnomalyAction = {
  id: number;
  case_id: number;
  action_type: string;
  description: string;
  owner_user_id: number | null;
  status: string;
  due_at: string | null;
  executed_at: string | null;
  evidence: string | null;
  external_reference: string | null;
  mandatory: boolean;
};

export type AnomalyNotification = {
  id: number;
  case_id: number;
  kind: string;
  title: string;
  body: string | null;
  severity: string;
  is_finops_queue: boolean;
  recipient_user_id: number | null;
  created_at: string | null;
  read_at: string | null;
  acknowledged_at: string | null;
};

export type CaseSummary = {
  total: number;
  by_state: Record<string, number>;
  by_severity: Record<string, number>;
  decisions: Record<string, number>;
  new: number;
  open: number;
  overdue: number;
  closed: number;
  unassigned: number;
  anomalous_cost_cloud: number;
  median_acknowledgement_hours: number | null;
  median_resolution_hours: number | null;
  classified: number;
  recurrence_families: number;
  notes: string[];
};

export async function listAnomalyConfigs(): Promise<AnomalyConfig[]> {
  const { data } = await api.get("/anomalies/configs");
  return data;
}

export async function updateAnomalyConfig(id: number, payload: Record<string, unknown>) {
  const { data } = await api.put(`/anomalies/configs/${id}`, payload);
  return data;
}

export async function enqueueAnomalyRun(id: number) {
  const { data } = await api.post(`/anomalies/configs/${id}/run`, { trigger_type: "MANUAL" });
  return data;
}

export type AnomalyRunSummary = {
  id: number;
  anomaly_config_id: number;
  status: string;
  phase: string | null;
  error: string | null;
  cases_created: number;
  cases_updated: number;
  rows_written: number;
};

export async function listAnomalyRuns(limit = 20): Promise<{ items: AnomalyRunSummary[] }> {
  const { data } = await api.get("/anomalies/runs", { params: { limit } });
  return data;
}

export async function getAnomalyCoverage() {
  const { data } = await api.get("/anomalies/coverage");
  return data;
}

export async function getAnomalyMetrics(): Promise<CaseSummary> {
  const { data } = await api.get("/anomalies/metrics");
  return data;
}

export async function listAnomalyCases(params: Record<string, unknown>) {
  const { data } = await api.get("/anomalies/cases", { params });
  return data as { items: AnomalyCase[]; total: number; limit: number; offset: number };
}

export async function getAnomalyCase(id: number): Promise<AnomalyCase> {
  const { data } = await api.get(`/anomalies/cases/${id}`);
  return data;
}

export async function getAnomalyCaseEvents(id: number) {
  const { data } = await api.get(`/anomalies/cases/${id}/events`);
  return data as { items: Array<Record<string, unknown>> };
}

export type AnomalySeriesPoint = {
  event_date: string;
  weekday: number;
  reference_month: string | null;
  reference_id: string | null;
  reference_kind: string | null;
  coverage_kind: string | null;
  distribution_method: string;
  expected_cloud: number | null;
  lower_cloud: number | null;
  upper_cloud: number | null;
  observed_cloud: number | null;
  delta_cloud: number | null;
  anomalous_cost_cloud: number | null;
  above_band: boolean | null;
  comparison_supported?: boolean;
  is_future?: boolean;
  raw_observed_cloud?: number | null;
  forecast_run_id?: number | null;
  forecast_cutoff?: string | null;
  phase?: "HISTORY" | "COMPARISON" | "FORECAST";
};

export type AnomalySeries = {
  items: AnomalySeriesPoint[];
  context_items?: Array<{
    event_date: string;
    observed_cloud: number | null;
    is_event_day: boolean;
  }>;
  context_start?: string;
  context_end?: string;
  context_event_date?: string;
  months?: string[];
  data_cutoff?: string;
  analysis_data_end?: string;
  horizon_end?: string;
  forecast_start?: string | null;
  forecast_end?: string;
  evaluation_grain?: string;
  contributor_series_count?: number;
  contributor_series?: Array<{ series_id: string; dimensions: Record<string, string | null> }>;
  contributor_breakdown?: Array<{
    series_id: string;
    dimensions: Record<string, string | null>;
    event_date: string;
    observed_cloud: number | null;
    expected_cloud: number | null;
    delta_cloud: number | null;
    items?: AnomalySeriesPoint[];
  }>;
  detail_dimensions?: Array<{ key: string; label: string }>;
  detail_items?: Array<{ dimensions: Record<string, string | null>; observed_cloud: number | null }>;
  scope_verified?: boolean;
  warnings?: string[];
  reference?: {
    reference_id: string;
    forecast_run_id: number | null;
    data_cutoff: string;
    generated_at: string | null;
    reference_kind: string;
    coverage_kind: string;
    monthly_forecast_cloud: number | null;
  } | null;
  reference_table?: string;
  series_dimensions?: Record<string, string | null>;
  distribution_methods?: string[];
  band_methods?: string[];
  confidence?: string[];
  note?: string;
};

export async function getAnomalyCaseSeries(id: number, horizonDays = 15): Promise<AnomalySeries> {
  const { data } = await api.get(`/anomalies/cases/${id}/series`, {
    params: { horizon_days: horizonDays },
  });
  return data;
}

export type ExpectedEventFamily = {
  family: string;
  occurrences: number;
  series_count: number;
  expected_cloud: number;
  observed_cloud: number;
  delta_cloud: number;
  months: string[];
  last_event_date: string | null;
  confidence: string[];
  reason_codes: string[];
};

export type QualityBucket = {
  reason_code: string;
  count: number;
  anomalous_cost_cloud: number;
  periods: string[];
  statuses: string[];
};

export async function getExpectedEvents(params: Record<string, unknown> = {}) {
  const { data } = await api.get("/anomalies/expected-events", { params });
  return data as {
    items: ExpectedEventFamily[];
    total: number;
    note?: string;
    assessment_table?: string;
  };
}

export async function getQuality(params: Record<string, unknown> = {}) {
  const { data } = await api.get("/anomalies/quality", { params });
  return data as {
    items: QualityBucket[];
    sampled?: number;
    note?: string;
    assessment_table?: string;
  };
}

export async function getAnomalyContributors(id: number) {
  const { data } = await api.get(`/anomalies/cases/${id}/contributors`);
  return data as { items: Array<Record<string, unknown>>; note?: string };
}

export async function transitionAnomalyCase(id: number, payload: Record<string, unknown>) {
  const { data } = await api.post(`/anomalies/cases/${id}/transition`, payload);
  return data as AnomalyCase;
}

export async function createAnomalyAction(caseId: number, payload: Record<string, unknown>) {
  const { data } = await api.post(`/anomalies/cases/${caseId}/actions`, payload);
  return data as AnomalyAction;
}

export async function updateAnomalyAction(actionId: number, payload: Record<string, unknown>) {
  const { data } = await api.put(`/anomalies/actions/${actionId}`, payload);
  return data as AnomalyAction;
}

export async function getAnomalyImpactSummary(configId?: number) {
  const { data } = await api.get("/anomalies/impact/summary", { params: configId ? { config_id: configId } : {} });
  return data as ImpactSummary;
}

export async function getAnomalyHealth() {
  const { data } = await api.get("/anomalies/health");
  return data as Record<string, unknown>;
}

export async function listAnomalyNotifications(params: Record<string, unknown>) {
  const { data } = await api.get("/anomalies/notifications", { params });
  return data as { items: AnomalyNotification[]; total: number; unread: number };
}

export async function readAnomalyNotification(id: number, acknowledge = false) {
  const { data } = await api.post(`/anomalies/notifications/${id}/read`, null, {
    params: { acknowledge },
  });
  return data;
}
