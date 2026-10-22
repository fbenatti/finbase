import { api } from "./api";

export type AclOperation = "read" | "create" | "write" | "delete";
export type AclResourceType = "screen" | "table" | "field" | "rule";
export type ScopeType = "hierarchy" | "budget" | "cost_center";
export type MatchMode = "exact" | "subtree";

export interface AccessScope {
  id: number;
  scope_type: ScopeType;
  value: string;
  label: string | null;
  node_ref: string | null;
}

export interface AclCondition {
  column: string;
  scope_type: ScopeType;
  match_mode: MatchMode;
}

export interface AccessAcl {
  id: number;
  role_id: number;
  resource_type: AclResourceType;
  resource: string;
  operation: AclOperation;
  effect: "allow" | "deny";
  conditions: AclCondition[];
}

export interface AccessDecision {
  allowed: boolean;
  reason: string;
}

export interface AclInput {
  resource_type: AclResourceType;
  resource: string;
  operation: AclOperation;
  effect: "allow" | "deny";
  conditions: AclCondition[];
}

export const listScopes = () => api.get<AccessScope[]>("/administracao/acessos/escopos").then((r) => r.data);
export const createScope = (body: { scope_type: ScopeType; value: string; label?: string }) =>
  api.post<AccessScope>("/administracao/acessos/escopos", body).then((r) => r.data);
export const listRoleAcls = (roleId: number) =>
  api.get<AccessAcl[]>(`/administracao/acessos/perfis/${roleId}/listas-acesso`).then((r) => r.data);
export const createRoleAcl = (roleId: number, body: AclInput) =>
  api.post<AccessAcl>(`/administracao/acessos/perfis/${roleId}/listas-acesso`, body).then((r) => r.data);
export const deleteAcl = (aclId: number) => api.delete(`/administracao/acessos/listas-acesso/${aclId}`);
export const grantUserScope = (userId: number, body: { scope_id: number; match_mode: MatchMode; reason: string }) =>
  api.post(`/administracao/acessos/usuarios/${userId}/escopos`, body).then((r) => r.data);
export const simulateDecision = (
  userId: number,
  params: { operation: AclOperation; resource_type: AclResourceType; resource: string },
) => api.get<AccessDecision>(`/administracao/acessos/usuarios/${userId}/decisao`, { params }).then((r) => r.data);
