import { canonicalExplorerParams, canonicalOperationsParams } from "../utils/dataNavigation";
import type { NavigationModule } from "./types";

export const VIEW_SELECTOR_KEYS = ["tab", "executive", "subtab", "subview", "aba", "etapa", "layer", "view", "providerTab", "dashboard", "contract"];

export function widgetSearch(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of VIEW_SELECTOR_KEYS) next.delete(key);
  return next;
}

/** Legacy links resolve to a registered page, including disabled pages (which are denied). */
export function resolveCatalogRoute(items: NavigationModule[], path: string, params: URLSearchParams): NavigationModule | undefined {
  const exact = items.find(item => item.path === path);
  if (exact) return exact;
  params = normalizeLegacyParams(path, params);
  const candidates = items.filter(item => item.legacy_path === path);
  return candidates.filter(item => Object.entries(item.view_params).every(([key, value]) => params.get(key) === value))
    .sort((a, b) => Object.keys(b.view_params).length - Object.keys(a.view_params).length)[0] || candidates[0];
}

export function resolveViewChange(items: NavigationModule[], current: NavigationModule, params: URLSearchParams): NavigationModule {
  const candidates = items.filter(item => item.legacy_path && item.legacy_path === current.legacy_path);
  return candidates.filter(item => Object.entries(item.view_params).every(([key, value]) => params.get(key) === value))
    .sort((a, b) => Object.keys(b.view_params).length - Object.keys(a.view_params).length)[0] || current;
}

export function routeViewParams(item: NavigationModule, query: URLSearchParams): URLSearchParams {
  const params = widgetSearch(query);
  for (const [key, value] of Object.entries(item.view_params)) params.set(key, value);
  return params;
}

export function normalizeLegacyParams(path: string, input: URLSearchParams): URLSearchParams {
  if (path === "/dados/explorador") return canonicalExplorerParams(input);
  if (path === "/operacoes/execucoes") return canonicalOperationsParams(input);
  const params = new URLSearchParams(input);
  if (path === "/governanca") {
    if (params.get("tab") === "clone-rules") {
      params.set("tab", "rules"); params.set("subtab", "clone-rules");
    } else if (params.get("tab") === "reservas-legacy") params.set("tab", "reservas");
  }
  if (path === "/planejamento") {
    const tab = params.get("tab");
    const contractTab = tab === "contract" || tab === "contrato";
    if (tab === "contratos-financeiros" || ["visao-financeira-contratos", "contrato-com-compromisso"].includes(params.get("dashboard") || "") || (contractTab && params.get("contract") === "compromisso")) {
      params.set("tab", "dashboard"); params.set("executive", "compromisso");
    }
  }
  return params;
}
