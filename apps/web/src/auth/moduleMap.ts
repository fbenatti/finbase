/** Mapa de rota → módulo RBAC (para o guard de leitura). Fallback: sem módulo
 *  = sem verificação (a API é a autoridade e responde 401/403). */
const ROUTE_MODULES: Array<[string, string]> = [
  ["/admin/audit", "audit"],
  ["/admin", "users"],
  ["/connections", "connections"],
  ["/explorer", "connections"],
  ["/reprocess", "etl"],
  ["/pipeline", "etl"],
  ["/extraction", "etl"],
  ["/analysis", "analysis"],
  ["/dimon-dashboard", "dimon"],
  ["/expected-view", "analysis"],
  ["/dashboards-ia", "ai_dashboards"],
  ["/github-copilot", "ai_dashboards"],
  ["/visoes-consumo-ia", "azure_ptu"],
  ["/azure-ptu-ops", "azure_ptu"],
  ["/forecast-accuracy", "forecast"],
  ["/forecast", "forecast"],
  ["/budgets", "planning"],
  ["/departments", "planning"],
  ["/savings", "savings"],
  ["/reservations", "savings"],
  ["/governance", "rules"],
  ["/rules", "rules"],
  ["/classifications", "rules"],
  ["/mappings", "rules"],
  ["/clone-rules", "rules"],
  ["/tables", "destinations"],
  ["/destinations", "destinations"],
  ["/relationship", "destinations"],
];

export function moduleForPath(pathname: string): string | null {
  for (const [prefix, module] of ROUTE_MODULES) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) return module;
  }
  return null;
}
