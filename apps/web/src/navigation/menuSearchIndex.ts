import type { NavigationModule } from "./types";

export const normalizeMenuText = (value: string) => value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLocaleLowerCase("pt-BR").trim();

/** Flat search: every reachable entry matches by its own label, at any submenu level. */
export function searchWorkspaceMenus(items: NavigationModule[], query: string): NavigationModule[] {
  const byCode = new Map(items.map(item => [item.code, item]));
  const words = normalizeMenuText(query).split(/\s+/).filter(Boolean);
  return items.filter(item => {
    if (!isReachable(item, byCode)) return false;
    const text = normalizeMenuText(item.label);
    return words.every(word => text.includes(word));
  });
}

/** Permissions still follow the ancestry: an entry is reachable only when it and every parent are allowed. */
function isReachable(item: NavigationModule, byCode: Map<string, NavigationModule>): boolean {
  const visited = new Set<string>();
  let current: NavigationModule | undefined = item;
  while (current) {
    if (visited.has(current.code) || !current.allowed || !current.enabled || current.visible === false) return false;
    visited.add(current.code);
    if (!current.parent_code) return true;
    current = byCode.get(current.parent_code);
    if (!current) return false;
  }
  return false;
}
