import type { NavigationModule } from "./types";

export const normalizeMenuText = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR").trim();

/** Search only the catalog allowed for this user, including its visible ancestry. */
export function searchWorkspaceMenus(items: NavigationModule[], query: string): NavigationModule[] {
  const byCode = new Map(items.map(item => [item.code, item]));
  const words = normalizeMenuText(query).split(/\s+/).filter(Boolean);
  return items.filter(item => {
    const labels: string[] = [];
    const visited = new Set<string>();
    let current: NavigationModule | undefined = item;
    while (current) {
      if (visited.has(current.code) || !current.allowed || !current.enabled || current.visible === false) return false;
      visited.add(current.code);
      labels.unshift(current.label);
      if (!current.parent_code) break;
      current = byCode.get(current.parent_code);
      if (!current) return false;
    }
    const text = normalizeMenuText(labels.join(" "));
    return words.every(word => text.includes(word));
  });
}
