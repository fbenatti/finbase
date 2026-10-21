import { describe, expect, it } from "vitest";
import { resolveCatalogRoute, resolveViewChange, routeViewParams, widgetSearch } from "./routes";
import type { NavigationModule } from "./types";
const item = (code: string, path: string, params: Record<string, string>, allowed = true): NavigationModule => ({ code, path, view_params: params, allowed, parent_code: "section_planejamento", label: code, renderer: "FinancialPlanning", legacy_path: "/planejamento", permission_module: "planning", permission_scope: code, position: 1, icon: "HistoryOutlined", required: false, configured_enabled: allowed, enabled: allowed });
const budget = item("budget", "/planejamento/historico-do-orcamento", { tab: "dashboard", executive: "orcamento" });
const story = item("story", "/planejamento/historia-dos-contratos", { tab: "dashboard", executive: "historia-contrato" }, false);
const items = [budget, story];
describe("catalog page routes", () => {
  it("redirects the original contract history URL to its own registered route", () => {
    const params = new URLSearchParams("tab=dashboard&executive=historia-contrato&mes=202610");
    expect(resolveCatalogRoute(items, "/planejamento", params)?.path).toBe(story.path);
    expect(widgetSearch(params).toString()).toBe("mes=202610");
  });
  it("does not redirect disabled pages to another page to bypass access checks", () => {
    expect(resolveCatalogRoute(items, story.path, new URLSearchParams())?.allowed).toBe(false);
  });
  it("prevents query parameters from selecting a different or hidden view", () => {
    const params = routeViewParams(budget, new URLSearchParams("tab=dashboard&executive=historia-contrato&contract=compromisso&dashboard=visao-financeira-contratos&mes=202610"));
    expect(params.get("executive")).toBe("orcamento");
    expect(params.has("contract")).toBe(false);
    expect(params.has("dashboard")).toBe(false);
    expect(params.get("mes")).toBe("202610");
  });
  it("retains the page and the widget filters when changing a month", () => {
    const params = routeViewParams(story, new URLSearchParams("mes=202610"));
    expect(resolveViewChange(items, story, params).path).toBe(story.path);
    expect(widgetSearch(params).toString()).toBe("mes=202610");
  });
  it("translates view changes into page routes", () => {
    expect(resolveViewChange(items, budget, new URLSearchParams(story.view_params)).path).toBe(story.path);
  });
});

describe("older favorites", () => {
  it("keeps the original engine-based explorer favorites at the same layer", () => {
    const prepared = { ...budget, code: "prepared", path: "/dados/explorador/preparados", legacy_path: "/dados/explorador", view_params: { tab: "acervo", etapa: "preparacao" } };
    const source = { ...prepared, code: "source", path: "/dados/explorador/fontes", view_params: { tab: "fontes" } };
    expect(resolveCatalogRoute([source, prepared], "/dados/explorador", new URLSearchParams("tab=iceberg&layer=silver"))?.path).toBe(prepared.path);
  });
  it("keeps the financial contract alias on the commitment page", () => {
    const commitment = { ...story, code: "commitment", path: "/planejamento/consumo-de-compromissos", view_params: { tab: "dashboard", executive: "compromisso" } };
    expect(resolveCatalogRoute([budget, story, commitment], "/planejamento", new URLSearchParams("tab=contratos-financeiros"))?.path).toBe(commitment.path);
  });
});
