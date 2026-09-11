import type { IdeaImpact, IdeaStatus, IdeaType } from "../../services/ideasApi";

export const STATUS_LABELS: Record<IdeaStatus, string> = {
  NOVA: "Nova",
  EM_VOTACAO: "Em votação",
  EM_ANALISE: "Em análise",
  BACKLOG: "Backlog",
  EM_IMPLEMENTACAO: "Em implementação",
  CONCLUIDA_ANALISE: "Concluída p/ análise",
  EM_PRODUCAO: "Em produção",
  CONCLUIDA: "Concluída",
  CANCELADA: "Cancelada",
  FECHADA: "Fechada",
};

export const TYPE_LABELS: Record<IdeaType, string> = {
  FEATURE: "Nova feature",
  PAINEL: "Novo painel",
  BUG: "Bug",
  BUG_CRITICO: "Bug crítico",
  MELHORIA: "Melhoria",
};

export const IMPACT_LABELS: Record<IdeaImpact, string> = {
  BAIXO: "Baixo",
  MEDIO: "Médio",
  ALTO: "Alto",
  CRITICO: "Crítico",
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
