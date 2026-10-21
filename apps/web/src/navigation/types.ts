export interface NavigationModule {
  code: string;
  parent_code: string | null;
  label: string;
  path: string;
  renderer: string;
  view_params: Record<string, string>;
  legacy_path: string | null;
  permission_module: string | null;
  permission_scope: string;
  position: number;
  icon: string;
  required: boolean;
  configured_enabled: boolean;
  enabled: boolean;
  allowed: boolean;
}
export interface NavigationSnapshot {
  customer_id: number;
  customer_name: string;
  items: NavigationModule[];
}
