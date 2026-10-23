import { api } from "./api";

export type CommitmentProfile = "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE";

export interface CommitmentRun {
  id: number;
  connection_id: number;
  provider: string;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  window_start_utc: string | null;
  window_end_utc: string | null;
  keys_analyzed: number;
  recommendations_count: number;
  total_monthly_saving: number | null;
  currency: string | null;
  readiness_counts: Record<string, number>;
  error_message: string | null;
  finished_at: string | null;
}

export interface CommitmentConnection {
  id: number;
  code: string;
  name: string;
  provider: string;
  latest_run: CommitmentRun | null;
}

export interface CommitmentRecommendation {
  id: number;
  run_id: number;
  provider: string;
  instrument: string;
  instrument_label: string;
  purchase_path: "self_service" | "negotiated";
  process_note: string | null;
  profile: CommitmentProfile | "NONE";
  action_type: "COMMIT" | "NO_ACTION";
  readiness_status: string;
  state: "NEW" | "APPROVED" | "REJECTED" | "PURCHASED" | "REALIZED";
  confidence: string;
  term_months: number | null;
  payment_option: string | null;
  discount_pct: number | null;
  commit_cost_hour: number | null;
  expected_utilization: number | null;
  monthly_saving: number | null;
  term_saving: number | null;
  upfront_cost: number | null;
  npv: number | null;
  saving_if_usage_drops: number | null;
  currency: string | null;
  risks: string[];
  rationale: string[];
}

const BASE = "/custos/reservas/motor";

export async function listCommitmentConnections(): Promise<CommitmentConnection[]> {
  return (await api.get(`${BASE}/conexoes`)).data.items;
}

export async function runCommitmentEngine(connectionId: number): Promise<CommitmentRun> {
  return (await api.post(`${BASE}/execucoes`, { connection_id: connectionId })).data;
}

export async function listCommitmentRecommendations(connectionId: number): Promise<{
  run_id: number | null;
  items: CommitmentRecommendation[];
}> {
  return (await api.get(`${BASE}/recomendacoes`, { params: { connection_id: connectionId } })).data;
}

export async function setCommitmentRecommendationState(
  id: number,
  state: "NEW" | "APPROVED" | "REJECTED",
): Promise<CommitmentRecommendation> {
  return (await api.patch(`${BASE}/recomendacoes/${id}/estado`, { state })).data;
}
