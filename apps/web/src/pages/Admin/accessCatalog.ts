/** Telas do produto, com a rota e o módulo RBAC que as libera.
 *  Usado pela matriz de perfis e pelo acesso efetivo de cada pessoa.
 *  Telas que compartilham um módulo (ex.: as de Operações) são liberadas juntas. */
export interface AccessScreen {
  route: string;
  label: string;
  module: string;
}

export interface AccessArea {
  area: string;
  screens: AccessScreen[];
}

export const ACCESS_AREAS: AccessArea[] = [
  {
    area: "Dados",
    screens: [
      { route: "/dados/fontes", label: "Fontes", module: "connections" },
      { route: "/dados/catalogo", label: "Catálogo de tabelas", module: "destinations" },
      { route: "/dados/explorador", label: "Explorador", module: "analysis" },
      { route: "/dados/fluxos", label: "Pipeline de dados", module: "etl" },
      { route: "/dados/mapeamentos", label: "Mapeamentos", module: "rules" },
    ],
  },
  {
    area: "Operações",
    screens: [
      { route: "/operacoes/dados", label: "Reprocessar dados", module: "etl" },
      { route: "/operacoes/execucoes", label: "Execuções", module: "etl" },
      { route: "/operacoes/custos-compartilhados", label: "Custo compartilhado", module: "etl" },
      { route: "/operacoes/extracoes", label: "Extrações", module: "etl" },
    ],
  },
  {
    area: "Projeções",
    screens: [
      { route: "/projecoes", label: "Projeções", module: "forecast" },
      { route: "/projecoes/precisao", label: "Precisão das previsões", module: "forecast" },
    ],
  },
  {
    area: "Custos e planejamento",
    screens: [
      { route: "/custos", label: "Custos", module: "gold" },
      { route: "/planejamento", label: "Planejamento", module: "planning" },
      { route: "/otimizacao", label: "Otimização", module: "savings" },
      { route: "/reservations", label: "Reservas", module: "reservations" },
    ],
  },
  {
    area: "Governança",
    screens: [
      { route: "/governanca?tab=organization", label: "Hierarquia", module: "planning" },
      { route: "/governanca?tab=rules", label: "Regras e classificação", module: "rules" },
      { route: "/governanca?tab=tags", label: "Política de tags", module: "azure_tag_policy" },
    ],
  },
  {
    area: "Outros",
    screens: [
      { route: "/iniciativas", label: "Iniciativas", module: "ideas" },
      { route: "/alertas", label: "Alertas", module: "anomalies" },
      { route: "/avisos", label: "Avisos do sistema (sino e alertas de tarefas)", module: "notifications" },
    ],
  },
  {
    area: "Administração",
    screens: [
      { route: "/administracao/acessos", label: "Pessoas, grupos e perfis", module: "users" },
      { route: "/administracao/identidade", label: "Identidade (SSO)", module: "auth" },
      { route: "/administracao/auditoria", label: "Auditoria", module: "audit" },
    ],
  },
];

export const ALL_SCREENS: AccessScreen[] = ACCESS_AREAS.flatMap((a) => a.screens);

export const CATALOG_MODULES = new Set(ALL_SCREENS.map((s) => s.module));

export const EDIT_ACTIONS = ["create", "update", "delete"] as const;

export const ACTION_LABELS: Record<string, string> = {
  read: "ver",
  create: "criar",
  update: "editar",
  delete: "excluir",
};
