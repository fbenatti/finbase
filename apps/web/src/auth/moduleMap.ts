/** Mapa de rota → (módulo RBAC, escopo). Escopo null = página usa o módulo
 *  inteiro; escopo definido = "aba" dentro do módulo (ex.: visao-contabil).
 *  Rotas fora do mapa não têm verificação (a API é a autoridade). */
const ROUTE_MODULES: Array<{ prefix: string; module: string; scope?: string | null }> = [
  { prefix: "/admin/audit", module: "audit" },
  { prefix: "/admin", module: "users" },
  { prefix: "/visao-contabil", module: "dimon", scope: "visao-contabil" },
  { prefix: "/connections", module: "connections" },
  { prefix: "/explorer", module: "connections" },
  { prefix: "/reprocess", module: "etl" },
  { prefix: "/pipeline", module: "etl" },
  { prefix: "/extraction", module: "etl" },
  { prefix: "/analysis", module: "analysis" },
  { prefix: "/dimon-dashboard", module: "dimon" },
  { prefix: "/expected-view", module: "analysis" },
  { prefix: "/dashboards-ia", module: "ai_dashboards" },
  { prefix: "/github-copilot", module: "ai_dashboards" },
  { prefix: "/visoes-consumo-ia", module: "azure_ptu" },
  { prefix: "/azure-ptu-ops", module: "azure_ptu" },
  { prefix: "/forecast-accuracy", module: "forecast" },
  { prefix: "/forecast", module: "forecast" },
  { prefix: "/budgets", module: "planning" },
  { prefix: "/departments", module: "planning" },
  { prefix: "/savings", module: "savings" },
  { prefix: "/reservations", module: "savings" },
  { prefix: "/governance", module: "rules" },
  { prefix: "/rules", module: "rules" },
  { prefix: "/classifications", module: "rules" },
  { prefix: "/mappings", module: "rules" },
  { prefix: "/clone-rules", module: "rules" },
  { prefix: "/tables", module: "destinations" },
  { prefix: "/destinations", module: "destinations" },
  { prefix: "/relationship", module: "destinations" },
];

export function moduleAndScopeForPath(pathname: string): { module: string; scope?: string | null } | null {
  for (const entry of ROUTE_MODULES) {
    if (pathname === entry.prefix || pathname.startsWith(entry.prefix + "/")) {
      return { module: entry.module, scope: entry.scope ?? null };
    }
  }
  return null;
}

/** Compat: apenas o módulo (sem escopo). */
export function moduleForPath(pathname: string): string | null {
  return moduleAndScopeForPath(pathname)?.module ?? null;
}
