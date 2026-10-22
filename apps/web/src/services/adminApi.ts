import { api } from "./api";

export interface AdminRole {
  id: number; name: string; description: string | null; is_system: boolean;
  created_at?: string;
  permissions?: Array<{ module: string; action: string; scope?: string | null }>;
  inherits_from?: Array<{ id: number; name: string }>;
  inherited_by?: Array<{ id: number; name: string }>;
}
export interface AdminGroup {
  id: number; name: string; description: string | null; external_id: string | null;
  created_at?: string;
  members: Array<{ id: number; username: string; full_name: string | null }>;
  roles: Array<{ id: number; name: string }>;
}
export interface AdminUser {
  id: number; username: string; email: string | null; full_name: string | null;
  auth_provider: string; is_active: boolean; must_change_password: boolean;
  last_login_at?: string | null; created_at?: string;
  roles: Array<{ id: number; name: string }>;
  groups: Array<{ id: number; name: string }>;
  temporary_password?: string;
}
export interface AdminGrant {
  id: number; module: string; action: string; reason: string;
  expires_at: string | null; created_at: string; granted_by_id: number | null;
}
export interface EffectivePermissions {
  user_id: number; username: string;
  permissions: Array<{ module: string; action: string; scope?: string | null }>;
  origins: Array<{ role_id: number | null; role: string; origin: string }>;
}

// Roles
export const listRoles = () => api.get<AdminRole[]>("/administracao/perfis").then((r) => r.data);
export const createRole = (body: { name: string; description?: string }) =>
  api.post<AdminRole>("/administracao/perfis", body).then((r) => r.data);
export const updateRole = (id: number, body: { name?: string; description?: string }) =>
  api.put(`/administracao/perfis/${id}`, body).then((r) => r.data);
export const deleteRole = (id: number) => api.delete(`/administracao/perfis/${id}`).then((r) => r.data);
export const getRole = (id: number) => api.get<AdminRole>(`/administracao/perfis/${id}`).then((r) => r.data);
export const setRolePermissions = (id: number, items: Array<{ module: string; action: string }>) =>
  api.put(`/administracao/perfis/${id}/permissoes`, items).then((r) => r.data);
export const addInheritance = (roleId: number, parentId: number) =>
  api.post(`/administracao/perfis/${roleId}/heranca/${parentId}`).then((r) => r.data);
export const removeInheritance = (roleId: number, parentId: number) =>
  api.delete(`/administracao/perfis/${roleId}/heranca/${parentId}`).then((r) => r.data);
export const getRoleTree = (id: number) => api.get(`/administracao/perfis/${id}/arvore`).then((r) => r.data);

// Groups
export const listGroups = () => api.get<AdminGroup[]>("/administracao/grupos").then((r) => r.data);
export const createGroup = (body: { name: string; description?: string }) =>
  api.post<AdminGroup>("/administracao/grupos", body).then((r) => r.data);
export const updateGroup = (id: number, body: { name?: string; description?: string }) =>
  api.put(`/administracao/grupos/${id}`, body).then((r) => r.data);
export const deleteGroup = (id: number) => api.delete(`/administracao/grupos/${id}`).then((r) => r.data);
export const addGroupMember = (groupId: number, userId: number) =>
  api.post(`/administracao/grupos/${groupId}/membros`, { user_id: userId }).then((r) => r.data);
export const removeGroupMember = (groupId: number, userId: number) =>
  api.delete(`/administracao/grupos/${groupId}/membros/${userId}`).then((r) => r.data);
export const addGroupRole = (groupId: number, roleId: number) =>
  api.post(`/administracao/grupos/${groupId}/perfis`, { role_id: roleId }).then((r) => r.data);
export const removeGroupRole = (groupId: number, roleId: number) =>
  api.delete(`/administracao/grupos/${groupId}/perfis/${roleId}`).then((r) => r.data);

// Users
export const listUsers = () => api.get<AdminUser[]>("/administracao/usuarios").then((r) => r.data);
export const createUser = (body: { username: string; full_name?: string; email?: string; password?: string; role_ids?: number[] }) =>
  api.post<AdminUser>("/administracao/usuarios", body).then((r) => r.data);
export const updateUser = (id: number, body: Partial<Pick<AdminUser, "full_name" | "email" | "is_active">>) =>
  api.put(`/administracao/usuarios/${id}`, body).then((r) => r.data);
export const resetUserPassword = (id: number, newPassword?: string) =>
  api.post<{ ok: boolean; temporary_password?: string }>(`/administracao/usuarios/${id}/redefinir-senha`, { new_password: newPassword }).then((r) => r.data);
export const setUserRoles = (id: number, roleIds: number[]) =>
  api.put(`/administracao/usuarios/${id}/perfis`, { role_ids: roleIds }).then((r) => r.data);
export const getEffectivePermissions = (id: number) =>
  api.get<EffectivePermissions>(`/administracao/usuarios/${id}/permissoes-efetivas`).then((r) => r.data);
export const listGrants = (userId: number) =>
  api.get<AdminGrant[]>(`/administracao/usuarios/${userId}/concessoes`).then((r) => r.data);
export const createGrant = (userId: number, body: { module: string; action: string; reason: string; expires_at?: string | null }) =>
  api.post<AdminGrant>(`/administracao/usuarios/${userId}/concessoes`, body).then((r) => r.data);
export const deleteGrant = (grantId: number) =>
  api.delete(`/administracao/usuarios/concessoes/${grantId}`).then((r) => r.data);
