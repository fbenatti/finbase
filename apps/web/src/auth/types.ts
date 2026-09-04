export interface Permission {
  module: string;
  action: "read" | "create" | "update" | "delete";
  scope?: string | null;
}

export interface AuthUser {
  id: number;
  username: string;
  email?: string | null;
  full_name?: string | null;
  auth_provider?: string;
  must_change_password: boolean;
  permissions: Permission[];
}

/**
 * Semântica igual ao backend (core/rbac.has_permission):
 * - sem scope (undefined/null) → exige permissão de módulo inteiro (perm.scope null);
 * - com scope → vale permissão de módulo inteiro OU do escopo exato.
 */
export function userHasPerm(user: AuthUser | null, module: string, action: string, scope?: string | null): boolean {
  if (!user) return false;
  return user.permissions.some((p) => {
    if (p.module !== module || p.action !== action) return false;
    if (scope === undefined || scope === null) return p.scope == null;
    return p.scope == null || p.scope === scope;
  });
}
