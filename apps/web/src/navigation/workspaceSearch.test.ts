import { describe, expect, it } from "vitest";
import type { NavigationModule } from "./types";
import { searchWorkspaceMenus } from "./menuSearchIndex";
const item = (code: string, label: string, parent_code: string | null = null, extra: Partial<NavigationModule> = {}): NavigationModule => ({
  code, label, parent_code, path: `/${code}`, renderer: "ModuleIndex", view_params: {}, legacy_path: null,
  permission_module: null, permission_scope: "", position: 1, icon: "", required: false,
  configured_enabled: true, enabled: true, allowed: true, visible: true, ...extra,
});
const catalog = [item("planning", "Planejamento"), item("budget", "Orçamentos", "planning"), item("story", "História dos contratos", "planning"), item("data", "Dados e operação"), item("sources", "Fontes de dados", "data")];
describe("workspace menu search", () => {
  it("ignores accents, case and extra whitespace", () => expect(searchWorkspaceMenus(catalog, "  ORCAMENTO  ").map(x => x.code)).toEqual(["budget"]));
  it("matches the group and submenu together", () => expect(searchWorkspaceMenus(catalog, "planejamento contrato").map(x => x.code)).toEqual(["story"]));
  it("includes children when their group matches", () => expect(searchWorkspaceMenus(catalog, "planejamento").map(x => x.code)).toEqual(["planning", "budget", "story"]));
  it("returns no results for unmatched words", () => expect(searchWorkspaceMenus(catalog, "inexistente")).toEqual([]));
  it("excludes forbidden, disabled and hidden entries", () => expect(searchWorkspaceMenus([item("a", "A", null, { allowed: false }), item("b", "B", null, { enabled: false }), item("c", "C", null, { visible: false })], "")).toEqual([]));
  it("excludes children of inaccessible ancestors", () => expect(searchWorkspaceMenus([item("a", "A", null, { allowed: false }), item("b", "B", "a")], "b")).toEqual([]));
  it("rejects missing ancestors and cycles", () => expect(searchWorkspaceMenus([item("a", "A", "b"), item("b", "B", "a"), item("c", "C", "missing")], "")).toEqual([]));
  it("registers new catalog entries without a separate search index", () => expect(searchWorkspaceMenus([...catalog, item("new", "Novo relatório", "data")], "novo").map(x => x.code)).toEqual(["new"]));
  it("keeps the catalog order and restores it when cleared", () => expect(searchWorkspaceMenus(catalog, "")).toEqual(catalog));
});
