import type { Opportunity } from "../../services/infraOpportunitiesApi";

export const ACTION_LABEL: Record<Opportunity["action_type"], string> = {
  RIGHTSIZE_DOWN: "Reduzir tamanho",
  RIGHTSIZE_UP: "Aumentar tamanho",
  KEEP: "Manter",
};

export const ACTION_COLOR: Record<Opportunity["action_type"], "success" | "warning" | "default"> = {
  RIGHTSIZE_DOWN: "success",
  RIGHTSIZE_UP: "warning",
  KEEP: "default",
};

export const STATE_LABEL: Record<Opportunity["state"], string> = {
  NEW: "Nova",
  ACCEPTED: "Aceita",
  IMPLEMENTED: "Executada",
  DISMISSED: "Descartada",
};

export const CONFIDENCE_LABEL: Record<Opportunity["confidence"], string> = { HIGH: "alta", MEDIUM: "média", LOW: "baixa" };

export const CONFIDENCE_COLOR: Record<Opportunity["confidence"], "success" | "info" | "default"> = {
  HIGH: "success",
  MEDIUM: "info",
  LOW: "default",
};

export const READINESS_LABEL: Record<string, string> = {
  READY_FOR_CAPACITY_ANALYSIS: "Pronta para análise",
  INCOMPLETE_TELEMETRY: "Telemetria incompleta",
  MISSING_CAPACITY: "Sem capacidade comprovada",
  MISSING_COMPARABLE_PRICE: "Sem preço comparável",
};

export const money = (value: number | null | undefined, currency = "BRL") =>
  value == null ? "—" : value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 2 });

export const pct = (value: number | null | undefined) => (value == null ? "—" : `${value.toFixed(1)}%`);

export const sizeLabel = (config: { ocpus: number; memory_gb: number } | null | undefined) =>
  config ? `${config.ocpus} OCPU · ${config.memory_gb} GB` : "—";
