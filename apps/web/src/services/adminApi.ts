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
export const listRoles = () => api.get<AdminRole[]>("/roles").then((r) => r.data);
export const createRole = (body: { name: string; description?: string }) =>
  api.post<AdminRole>("/roles", body).then((r) => r.data);
export const updateRole = (id: number, body: { name?: string; description?: string }) =>
  api.put(`/roles/${id}`, body).then((r) => r.data);
export const deleteRole = (id: number) => api.delete(`/roles/${id}`).then((r) => r.data);
export const getRole = (id: number) => api.get<AdminRole>(`/roles/${id}`).then((r) => r.data);
export const setRolePermissions = (id: number, items: Array<{ module: string; action: string }>) =>
  api.put(`/roles/${id}/permissions`, items).then((r) => r.data);
export const addInheritance = (roleId: number, parentId: number) =>
  api.post(`/roles/${roleId}/inherits/${parentId}`).then((r) => r.data);
export const removeInheritance = (roleId: number, parentId: number) =>
  api.delete(`/roles/${roleId}/inherits/${parentId}`).then((r) => r.data);
export const getRoleTree = (id: number) => api.get(`/roles/${id}/tree`).then((r) => r.data);

// Groups
export const listGroups = () => api.get<AdminGroup[]>("/groups").then((r) => r.data);
export const createGroup = (body: { name: string; description?: string }) =>
  api.post<AdminGroup>("/groups", body).then((r) => r.data);
export const updateGroup = (id: number, body: { name?: string; description?: string }) =>
  api.put(`/groups/${id}`, body).then((r) => r.data);
export const deleteGroup = (id: number) => api.delete(`/groups/${id}`).then((r) => r.data);
export const addGroupMember = (groupId: number, userId: number) =>
  api.post(`/groups/${groupId}/members`, { user_id: userId }).then((r) => r.data);
export const removeGroupMember = (groupId: number, userId: number) =>
  api.delete(`/groups/${groupId}/members/${userId}`).then((r) => r.data);
export const addGroupRole = (groupId: number, roleId: number) =>
  api.post(`/groups/${groupId}/roles`, { role_id: roleId }).then((r) => r.data);
export const removeGroupRole = (groupId: number, roleId: number) =>
  api.delete(`/groups/${groupId}/roles/${roleId}`).then((r) => r.data);

// Users
export const listUsers = () => api.get<AdminUser[]>("/users").then((r) => r.data);
export const createUser = (body: { username: string; full_name?: string; email?: string; password?: string; role_ids?: number[] }) =>
  api.post<AdminUser>("/users", body).then((r) => r.data);
export const updateUser = (id: number, body: Partial<Pick<AdminUser, "full_name" | "email" | "is_active">>) =>
  api.put(`/users/${id}`, body).then((r) => r.data);
export const resetUserPassword = (id: number, newPassword?: string) =>
  api.post<{ ok: boolean; temporary_password?: string }>(`/users/${id}/reset-password`, { new_password: newPassword }).then((r) => r.data);
export const setUserRoles = (id: number, roleIds: number[]) =>
  api.put(`/users/${id}/roles`, { role_ids: roleIds }).then((r) => r.data);
export const getEffectivePermissions = (id: number) =>
  api.get<EffectivePermissions>(`/users/${id}/effective-permissions`).then((r) => r.data);
export const listGrants = (userId: number) =>
  api.get<AdminGrant[]>(`/users/${userId}/grants`).then((r) => r.data);
export const createGrant = (userId: number, body: { module: string; action: string; reason: string; expires_at?: string | null }) =>
  api.post<AdminGrant>(`/users/${userId}/grants`, body).then((r) => r.data);
export const deleteGrant = (grantId: number) =>
  api.delete(`/users/grants/${grantId}`).then((r) => r.data);
