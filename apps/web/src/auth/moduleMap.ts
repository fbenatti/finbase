/** Mapa de rota → alvos RBAC. Cada alvo = {module, scope}; o usuário pode
 *  acessar a rota se tiver read em QUALQUER um dos alvos (menu e guard).
 *  scope null = permissão de módulo inteiro; scope definido = "aba"/área.
 *  Rotas fora do mapa não têm verificação (a API é a autoridade). */
type PermTarget = { module: string; scope?: string | null };

const ROUTE_TARGETS: Array<{ prefix: string; targets: PermTarget[] }> = [
  { prefix: "/iniciativas", targets: [{ module: "ideas" }] },
  { prefix: "/alertas", targets: [{ module: "anomalies" }] },
  { prefix: "/administracao/auditoria", targets: [{ module: "audit" }] },
  { prefix: "/administracao/identidade", targets: [{ module: "auth" }] },
  { prefix: "/administracao/acessos", targets: [{ module: "users" }] },
  { prefix: "/dados/fontes", targets: [{ module: "connections" }] },
  { prefix: "/dados/catalogo", targets: [{ module: "destinations" }] },
  { prefix: "/dados/explorador", targets: [{ module: "analysis" }] },
  { prefix: "/dados/fluxos", targets: [{ module: "etl" }] },
  { prefix: "/dados/mapeamentos", targets: [{ module: "rules" }] },
  { prefix: "/operacoes/execucoes", targets: [{ module: "etl" }] },
  { prefix: "/operacoes/custos-compartilhados", targets: [{ module: "etl" }] },
  { prefix: "/operacoes/extracoes", targets: [{ module: "etl" }] },
  { prefix: "/custos", targets: [{ module: "gold" }] },
  { prefix: "/planejamento", targets: [{ module: "planning" }] },
  { prefix: "/projecoes/acuracia", targets: [{ module: "forecast" }] },
  { prefix: "/projecoes", targets: [{ module: "forecast" }] },
  { prefix: "/otimizacao", targets: [{ module: "savings" }] },
  {
    prefix: "/governanca",
    targets: [
      { module: "planning" },
      { module: "rules" },
      { module: "reservations" },
      { module: "azure_tag_policy" },
      { module: "gold" },
    ],
  },
  { prefix: "/ideas", targets: [{ module: "ideas" }] },
  { prefix: "/anomalies", targets: [{ module: "anomalies" }] },
  { prefix: "/admin/audit", targets: [{ module: "audit" }] },
  { prefix: "/admin/sso", targets: [{ module: "auth" }] },
  { prefix: "/admin", targets: [{ module: "users" }] },
  { prefix: "/connections", targets: [{ module: "connections" }] },
  { prefix: "/explorer", targets: [{ module: "connections" }] },
  { prefix: "/reprocess", targets: [{ module: "etl" }] },
  { prefix: "/pipeline", targets: [{ module: "etl" }] },
  { prefix: "/extraction", targets: [{ module: "etl" }] },
  { prefix: "/analysis", targets: [{ module: "analysis" }] },
  { prefix: "/gold-dashboard", targets: [{ module: "gold" }] },
  { prefix: "/expected-view", targets: [{ module: "gold" }] },
  { prefix: "/azure-ptu-ops", targets: [{ module: "azure_ptu" }] },
  { prefix: "/forecast-accuracy", targets: [{ module: "forecast" }] },
  { prefix: "/forecast", targets: [{ module: "forecast" }] },
  { prefix: "/financial-planning", targets: [{ module: "planning" }] },
  { prefix: "/budgets", targets: [{ module: "planning" }] },
  { prefix: "/manual-forecast", targets: [{ module: "planning" }] },
  { prefix: "/contractual-baseline", targets: [{ module: "planning" }] },
  { prefix: "/credit-letter", targets: [{ module: "planning" }] },
  { prefix: "/budget-defense", targets: [{ module: "planning" }] },
  { prefix: "/financial-dashboard", targets: [{ module: "planning" }] },
  { prefix: "/departments", targets: [{ module: "planning" }] },
  { prefix: "/savings", targets: [{ module: "savings" }] },
  { prefix: "/reservations", targets: [{ module: "reservations" }] },
  {
    prefix: "/governance",
    targets: [
      { module: "planning" },
      { module: "rules" },
      { module: "reservations" },
      { module: "azure_tag_policy" },
    ],
  },
  { prefix: "/financial-contracts", targets: [{ module: "rules" }] },
  { prefix: "/financial-contracts-dashboard", targets: [{ module: "rules" }] },
  { prefix: "/rules", targets: [{ module: "rules" }] },
  { prefix: "/classifications", targets: [{ module: "rules" }] },
  { prefix: "/mappings", targets: [{ module: "rules" }] },
  { prefix: "/clone-rules", targets: [{ module: "reservations" }] },
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

export function permissionTargetsForLocation(pathname: string, search: string): PermTarget[] | null {
  const params = new URLSearchParams(search);
  if (pathname === "/administracao/acessos" || pathname === "/admin") {
    const module = params.get("tab") === "groups" ? "groups" : params.get("tab") === "roles" ? "roles" : "users";
    return [{ module }];
  }
  if (pathname === "/governanca" || pathname === "/governance") {
    if (params.get("tab") === "rules" && params.get("subtab") === "clone-rules") {
      return [{ module: "reservations" }];
    }
    const moduleByTab: Record<string, string> = {
      organization: "planning",
      classification: "rules",
      rules: "rules",
      "financial-contracts": "rules",
      "cost-relationship": "gold",
      reservas: "reservations",
      tags: "azure_tag_policy",
    };
    const module = moduleByTab[params.get("tab") || ""];
    return module ? [{ module }] : permissionTargetsForPath(pathname);
  }
  return permissionTargetsForPath(pathname);
}
