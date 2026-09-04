/** Mapa de rota → alvos RBAC. Cada alvo = {module, scope}; o usuário pode
 *  acessar a rota se tiver read em QUALQUER um dos alvos (menu e guard).
 *  scope null = permissão de módulo inteiro; scope definido = "aba"/área.
 *  Rotas fora do mapa não têm verificação (a API é a autoridade). */
type PermTarget = { module: string; scope?: string | null };

const ROUTE_TARGETS: Array<{ prefix: string; targets: PermTarget[] }> = [
  { prefix: "/admin/audit", targets: [{ module: "audit" }] },
  { prefix: "/admin/sso", targets: [{ module: "auth" }] },
  { prefix: "/admin", targets: [{ module: "users" }] },
  { prefix: "/connections", targets: [{ module: "connections" }] },
  { prefix: "/explorer", targets: [{ module: "connections" }] },
  { prefix: "/reprocess", targets: [{ module: "etl" }] },
  { prefix: "/pipeline", targets: [{ module: "etl" }] },
  { prefix: "/extraction", targets: [{ module: "etl" }] },
  { prefix: "/analysis", targets: [{ module: "analysis" }] },
  { prefix: "/dimon-dashboard", targets: [{ module: "dimon" }] },
  { prefix: "/expected-view", targets: [{ module: "analysis" }] },
  { prefix: "/dashboards-ia", targets: [{ module: "ai_dashboards" }] },
  { prefix: "/github-copilot", targets: [{ module: "ai_dashboards" }] },
  { prefix: "/visoes-consumo-ia", targets: [{ module: "azure_ptu" }] },
  { prefix: "/azure-ptu-ops", targets: [{ module: "azure_ptu" }] },
  { prefix: "/forecast-accuracy", targets: [{ module: "forecast" }] },
  { prefix: "/forecast", targets: [{ module: "forecast" }] },
  { prefix: "/budgets", targets: [{ module: "planning" }] },
  { prefix: "/departments", targets: [{ module: "planning" }] },
  { prefix: "/savings", targets: [{ module: "savings" }] },
  { prefix: "/reservations", targets: [{ module: "savings" }] },
  {
    prefix: "/governance",
    targets: [
      { module: "rules" },
      { module: "azure_tag_policy" },
      { module: "dimon", scope: "visao-contabil" },
      { module: "forecast", scope: "visao-contabil" },
    ],
  },
  { prefix: "/rules", targets: [{ module: "rules" }] },
  { prefix: "/classifications", targets: [{ module: "rules" }] },
  { prefix: "/mappings", targets: [{ module: "rules" }] },
  { prefix: "/clone-rules", targets: [{ module: "rules" }] },
  { prefix: "/tables", targets: [{ module: "destinations" }] },
  { prefix: "/destinations", targets: [{ module: "destinations" }] },
  { prefix: "/relationship", targets: [{ module: "destinations" }] },
];

export function permissionTargetsForPath(pathname: string): PermTarget[] | null {
  for (const entry of ROUTE_TARGETS) {
    if (pathname === entry.prefix || pathname.startsWith(entry.prefix + "/")) {
      return entry.targets;
    }
  }
  return null;
}
