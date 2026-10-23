import { describe, expect, it } from "vitest";
import { resolveCatalogRoute, resolveViewChange, routeViewParams, widgetSearch } from "./routes";
import type { NavigationModule } from "./types";
const item = (code: string, path: string, params: Record<string, string>, allowed = true): NavigationModule => ({ code, path, view_params: params, allowed, parent_code: "section_planejamento", label: code, renderer: "FinancialPlanning", legacy_path: "/planejamento", permission_module: "planning", permission_scope: code, position: 1, icon: "HistoryOutlined", required: false, configured_enabled: allowed, enabled: allowed });
const budget = item("budget", "/planejamento/historico-do-orcamento", { tab: "dashboard", executive: "orcamento" });
const commitment = item("commitment", "/planejamento/consumo-de-compromissos", { tab: "dashboard", executive: "compromisso" });
const items = [budget, commitment];
describe("catalog page routes", () => {
  it("does not redirect disabled pages to another page to bypass access checks", () => {
    const disabled = { ...commitment, allowed: false };
    expect(resolveCatalogRoute([budget, disabled], disabled.path, new URLSearchParams())?.allowed).toBe(false);
  });
  it("prevents query parameters from selecting a different or hidden view", () => {
    const params = routeViewParams(budget, new URLSearchParams("tab=dashboard&executive=compromisso&contract=compromisso&dashboard=visao-financeira-contratos&mes=202610"));
    expect(params.get("executive")).toBe("orcamento");
    expect(params.has("contract")).toBe(false);
    expect(params.has("dashboard")).toBe(false);
    expect(params.get("mes")).toBe("202610");
  });
  it("retains the page and the widget filters when changing a month", () => {
    const params = routeViewParams(commitment, new URLSearchParams("mes=202610"));
    expect(resolveViewChange(items, commitment, params).path).toBe(commitment.path);
    expect(widgetSearch(params).toString()).toBe("mes=202610");
  });
  it("translates view changes into page routes", () => {
    expect(resolveViewChange(items, budget, new URLSearchParams(commitment.view_params)).path).toBe(commitment.path);
  });
});

describe("older favorites", () => {
  it("keeps the original engine-based explorer favorites at the same layer", () => {
    const prepared = { ...budget, code: "prepared", path: "/dados/explorador/preparados", legacy_path: "/dados/explorador", view_params: { tab: "acervo", etapa: "preparacao" } };
    const source = { ...prepared, code: "source", path: "/dados/explorador/fontes", view_params: { tab: "fontes" } };
    expect(resolveCatalogRoute([source, prepared], "/dados/explorador", new URLSearchParams("tab=iceberg&layer=silver"))?.path).toBe(prepared.path);
  });
  it("keeps the financial contract alias on the commitment page", () => {
    expect(resolveCatalogRoute([budget, commitment], "/planejamento", new URLSearchParams("tab=contratos-financeiros"))?.path).toBe(commitment.path);
  });
});
