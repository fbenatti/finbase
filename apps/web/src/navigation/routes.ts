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
  const candidates = items.filter(item => item.legacy_path === path);
  return candidates.filter(item => Object.entries(item.view_params).every(([key, value]) => params.get(key) === value))
    .sort((a, b) => Object.keys(b.view_params).length - Object.keys(a.view_params).length)[0] || candidates[0];
}

export function resolveViewChange(items: NavigationModule[], current: NavigationModule, params: URLSearchParams): NavigationModule {
  const candidates = items.filter(item => item.legacy_path && item.legacy_path === current.legacy_path);
  return candidates.filter(item => Object.entries(item.view_params).every(([key, value]) => params.get(key) === value))
    .sort((a, b) => Object.keys(b.view_params).length - Object.keys(a.view_params).length)[0] || current;
}
