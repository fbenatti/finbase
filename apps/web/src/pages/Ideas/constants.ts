import type { IdeaImpact, IdeaStatus, IdeaType } from "../../services/ideasApi";

export const STATUS_LABELS: Record<IdeaStatus, string> = {
  NOVA: "Nova ideia",
  EM_VOTACAO: "Em priorização",
  EM_ANALISE: "Em triagem",
  BACKLOG: "Fila de espera",
  EM_IMPLEMENTACAO: "Em desenvolvimento",
  CONCLUIDA_ANALISE: "Pronta para revisão",
  EM_PRODUCAO: "Publicada em produção",
  CONCLUIDA: "Entregue",
  CANCELADA: "Descartada",
  FECHADA: "Encerrada",
};

export const TYPE_LABELS: Record<IdeaType, string> = {
  FEATURE: "Nova funcionalidade",
  PAINEL: "Novo dashboard",
  BUG: "Defeito",
  BUG_CRITICO: "Defeito crítico",
  MELHORIA: "Aprimoramento",
};

export const IMPACT_LABELS: Record<IdeaImpact, string> = {
  BAIXO: "Baixo impacto",
  MEDIO: "Impacto médio",
  ALTO: "Alto impacto",
  CRITICO: "Impacto crítico",
};

export const STATUS_COLOR: Record<IdeaStatus, "default" | "primary" | "success" | "error" | "warning" | "info" | "secondary"> = {
  NOVA: "default",
  EM_VOTACAO: "info",
  EM_ANALISE: "primary",
  BACKLOG: "secondary",
  EM_IMPLEMENTACAO: "warning",
  CONCLUIDA_ANALISE: "warning",
  EM_PRODUCAO: "success",
  CONCLUIDA: "success",
  CANCELADA: "error",
  FECHADA: "default",
};

export const VOTABLE_TYPES: IdeaType[] = ["FEATURE", "PAINEL"];

export const BOARD_STATUSES: IdeaStatus[] = [
  "NOVA", "EM_VOTACAO", "EM_ANALISE", "BACKLOG",
  "EM_IMPLEMENTACAO", "CONCLUIDA_ANALISE", "EM_PRODUCAO", "CONCLUIDA",
];

export const TRANSITIONS: Partial<Record<IdeaStatus, IdeaStatus[]>> = {
  NOVA: ["EM_VOTACAO", "EM_ANALISE"],
  EM_VOTACAO: ["EM_ANALISE"],
  EM_ANALISE: ["BACKLOG", "CANCELADA", "FECHADA"],
  BACKLOG: ["EM_IMPLEMENTACAO"],
  EM_IMPLEMENTACAO: ["CONCLUIDA_ANALISE", "CANCELADA"],
  CONCLUIDA_ANALISE: ["EM_PRODUCAO"],
  EM_PRODUCAO: ["CONCLUIDA"],
};
