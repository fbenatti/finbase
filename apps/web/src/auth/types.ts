export interface Permission {
  module: string;
  action: "read" | "create" | "update" | "delete";
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

export function userHasPerm(user: AuthUser | null, module: string, action: string): boolean {
  if (!user) return false;
  return user.permissions.some((p) => p.module === module && p.action === action);
}
