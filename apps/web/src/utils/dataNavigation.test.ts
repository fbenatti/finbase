import { describe, expect, it } from "vitest";
import { canonicalExplorerParams, canonicalOperationsParams, dataLayerForStage } from "./dataNavigation";
import { permissionTargetsForLocation } from "../auth/moduleMap";

describe("data navigation compatibility", () => {
  it.each([
    ["bronze", "origem"], ["silver", "preparacao"], ["gold", "consolidacao"],
  ])("opens a saved %s link at the same data layer", (layer, stage) => {
    const original = new URLSearchParams(`tab=iceberg&layer=${layer}&period=202610`);
    const next = canonicalExplorerParams(original);
    expect(next.get("tab")).toBe("acervo");
    expect(next.get("etapa")).toBe(stage);
    expect(next.has("layer")).toBe(false);
    expect(next.get("period")).toBe("202610");
    expect(dataLayerForStage(next.get("etapa"))).toBe(layer);
    expect(original.get("tab")).toBe("iceberg");
    expect(canonicalExplorerParams(next).toString()).toBe(next.toString());
    expect(permissionTargetsForLocation("/dados/explorador", `?${next}`))
      .toEqual(permissionTargetsForLocation("/dados/explorador", `?${original}`));
  });

  it("defaults missing or invalid stages to consolidated data", () => {
    for (const suffix of ["", "&layer=invalid"]) {
      const next = canonicalExplorerParams(new URLSearchParams(`tab=iceberg${suffix}`));
      expect(next.get("etapa")).toBe("consolidacao");
      expect(dataLayerForStage(next.get("etapa"))).toBe("gold");
    }
  });

  it("keeps the cleanup page accessible through saved links", () => {
    expect(canonicalExplorerParams(new URLSearchParams("tab=staging&layer=bronze")).toString())
      .toBe("tab=temporarios");
  });

  it.each(["fontes", "destino", "pivot", "busca"])("preserves the %s view", tab => {
    const params = new URLSearchParams(`tab=${tab}&q=custo`);
    expect(canonicalExplorerParams(params).toString()).toBe(params.toString());
  });

  it.each(["cubes", "ai-cost"])("preserves the %s operation and its permissions", subtab => {
    const params = new URLSearchParams(`tab=gold&subtab=${subtab}`);
    const next = canonicalOperationsParams(params);
    expect(next.get("tab")).toBe("cubos");
    expect(next.get("subtab")).toBe(subtab);
    expect(canonicalOperationsParams(next).toString()).toBe(next.toString());
    expect(permissionTargetsForLocation("/operacoes/execucoes", `?${next}`))
      .toEqual(permissionTargetsForLocation("/operacoes/execucoes", `?${params}`));
  });

  it("preserves the existing projection accuracy alias", () => {
    expect(canonicalOperationsParams(new URLSearchParams("tab=accuracy&period=202610")).toString())
      .toBe("tab=forecast&period=202610&subtab=accuracy");
  });
});
