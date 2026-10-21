export const DATA_LAYER_STAGES: Record<string, string> = {
  bronze: "origem", silver: "preparacao", gold: "consolidacao",
};

export function dataLayerForStage(stage: string | null): "bronze" | "silver" | "gold" {
  if (stage === "origem" || stage === "bronze") return "bronze";
  if (stage === "preparacao" || stage === "silver") return "silver";
  return "gold";
}

/** Accept saved links, but always expose product names in the address bar. */
export function canonicalExplorerParams(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  const tab = next.get("tab");
  if (tab === "iceberg" || tab === "acervo") {
    next.set("tab", "acervo");
    next.set("etapa", DATA_LAYER_STAGES[dataLayerForStage(next.get("etapa") || next.get("layer"))]);
    next.delete("layer");
  } else if (tab === "staging") {
    next.set("tab", "temporarios");
    next.delete("layer");
    next.delete("etapa");
  }
  return next;
}

export function canonicalOperationsParams(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  if (next.get("tab") === "gold") next.set("tab", "cubos");
  if (next.get("tab") === "accuracy") {
    next.set("tab", "forecast");
    next.set("subtab", "accuracy");
  }
  return next;
}
