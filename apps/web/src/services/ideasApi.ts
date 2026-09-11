import { api } from "./api";

export type IdeaType = "FEATURE" | "PAINEL" | "BUG" | "BUG_CRITICO" | "MELHORIA";
export type IdeaImpact = "BAIXO" | "MEDIO" | "ALTO" | "CRITICO";
export type IdeaDifficulty = "XS" | "S" | "M" | "L" | "XL";
export type IdeaStatus =
  | "NOVA" | "EM_VOTACAO" | "EM_ANALISE" | "BACKLOG" | "EM_IMPLEMENTACAO"
  | "CONCLUIDA_ANALISE" | "EM_PRODUCAO" | "CONCLUIDA" | "CANCELADA" | "FECHADA";

export interface IdeaUser {
  id: number;
  username: string | null;
  full_name: string | null;
}

export interface Idea {
  id: number;
  title: string;
  description: string | null;
  idea_type: IdeaType;
  impact: IdeaImpact;
  difficulty: IdeaDifficulty;
  due_date: string | null;
  status: IdeaStatus;
  author_id: number;
  author: IdeaUser;
  votes_count: number;
  priority_score: number;
  status_entered_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  assignees: IdeaUser[];
  dwell?: Record<string, number>;
  time_to_start_days?: number | null;
  time_in_impl_days?: number | null;
  time_to_prod_days?: number | null;
  days_in_current_status?: number;
}

export interface IdeaComment {
  id: number;
  body: string;
  author_id: number;
  author: IdeaUser;
  created_at: string | null;
}

export interface IdeaTimeline {
  idea_id: number;
  status: IdeaStatus;
  intervals: Array<{
    id: number;
    status: IdeaStatus;
    from_status: string | null;
    entered_at: string;
    exited_at: string | null;
    duration_seconds: number;
    duration_days: number;
  }>;
  metrics: {
    dwell: Record<string, number>;
    time_to_start_days: number | null;
    time_in_impl_days: number | null;
    time_to_prod_days: number | null;
    days_in_current_status: number;
  };
}

export interface IdeasDashboard {
  kpis: {
    implemented: number;
    bugs_fixed: number;
    bugs_critical_fixed: number;
    features_shipped: number;
    panels_shipped: number;
    avg_time_to_start_days: number | null;
    p50_time_to_start_days: number | null;
    avg_time_in_impl_days: number | null;
    p50_time_in_impl_days: number | null;
    avg_time_to_prod_days: number | null;
    p50_time_to_prod_days: number | null;
    wip: number;
    cancelled: number;
    closed: number;
  };
  dwell_by_status: Array<{ status: string; avg_days: number | null; p50_days: number | null; count: number }>;
  stage_times_by_type: Array<{ type: string; time_to_start: number | null; time_in_impl: number | null; time_to_prod: number | null }>;
  stage_times_by_difficulty: Array<{ difficulty: string; time_to_start: number | null; time_in_impl: number | null; time_to_prod: number | null }>;
  monthly_shipped: Array<{ month: string; by_type: Record<string, number> }>;
  funnel: Array<{ status: string; count: number }>;
  aging_now: Array<{ idea_id: number; title: string; status: string; days_in_current_status: number }>;
  shipped_rows: Idea[];
  wip_rows: Idea[];
  top_voted_open: Idea[];
}

export interface IdeaBoard {
  columns: Array<{ status: IdeaStatus; ideas: Idea[] }>;
}

export interface IdeaCreateInput {
  title: string;
  description?: string;
  idea_type: IdeaType;
  impact?: IdeaImpact;
  difficulty?: IdeaDifficulty;
  due_date?: string;
}

export const listIdeas = (params?: Record<string, string | number>) =>
  api.get<Idea[]>("/ideas", { params }).then((r) => r.data);

export const getIdea = (id: number) => api.get<Idea>(`/ideas/${id}`).then((r) => r.data);

export const createIdea = (body: IdeaCreateInput) =>
  api.post<Idea>("/ideas", body).then((r) => r.data);

export const patchIdea = (id: number, body: Partial<IdeaCreateInput>) =>
  api.patch<Idea>(`/ideas/${id}`, body).then((r) => r.data);

export const deleteIdea = (id: number) => api.delete(`/ideas/${id}`);

export const transitionIdea = (id: number, status: IdeaStatus) =>
  api.post<Idea>(`/ideas/${id}/transition`, { status }).then((r) => r.data);

export const getIdeaTimeline = (id: number) =>
  api.get<IdeaTimeline>(`/ideas/${id}/timeline`).then((r) => r.data);

export const voteIdea = (id: number) => api.post<Idea>(`/ideas/${id}/vote`).then((r) => r.data);

export const unvoteIdea = (id: number) => api.delete<Idea>(`/ideas/${id}/vote`).then((r) => r.data);

export const listIdeaComments = (id: number) =>
  api.get<IdeaComment[]>(`/ideas/${id}/comments`).then((r) => r.data);

export const addIdeaComment = (id: number, body: string) =>
  api.post<IdeaComment>(`/ideas/${id}/comments`, { body }).then((r) => r.data);

export const setIdeaAssignees = (id: number, userIds: number[]) =>
  api.put<Idea>(`/ideas/${id}/assignees`, { user_ids: userIds }).then((r) => r.data);

export const getIdeasBoard = () => api.get<IdeaBoard>("/ideas/board").then((r) => r.data);

export const getIdeasDashboard = (from?: string, to?: string) =>
  api.get<IdeasDashboard>("/ideas/dashboard", { params: { from, to } }).then((r) => r.data);
