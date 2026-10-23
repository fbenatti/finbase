import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { getHierarchyLevelLabels, type HierarchyLevelLabel } from "../services/api";

/**
 * Rótulos dos níveis hierarquia_N vindos dos aliases configurados em
 * Estrutura organizacional. Nenhum painel deve fixar "Diretoria" ou "VP":
 * o papel de cada nível muda de ramo para ramo do organograma.
 *
 * - `hierarchyLabel(column)`: leitura síncrona dos rótulos globais (sem escopo),
 *   para código fora de componente. Use `useHierarchyLabels()` no componente
 *   raiz para re-renderizar quando eles chegam.
 * - `useLevelAliases(path)`: rótulos resolvidos no escopo de um caminho.
 */

// Enquanto a API não responde: o organograma padrão do seed.
const FALLBACK = ["Segmento", "Modelo", "Provedor", "Cliente", "Projeto", "Subprojeto"];
const FALLBACK_PLURAL = ["Segmentos", "Modelos", "Provedores", "Clientes", "Projetos", "Subprojetos"];
const LEVEL_RE = /^hierarquia_(\d+)$/;

let globalLevels: HierarchyLevelLabel[] | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();
const scopedCache = new Map<string, Promise<HierarchyLevelLabel[]>>();

function emit() {
  listeners.forEach((listener) => listener());
}

function ensureGlobal() {
  if (globalLevels || loading) return;
  loading = getHierarchyLevelLabels([])
    .then((data) => { globalLevels = data.levels; emit(); })
    .catch(() => { loading = null; });
}

/** Descarta o cache depois de editar papéis ou regras. */
export function invalidateHierarchyLabels() {
  globalLevels = null;
  loading = null;
  scopedCache.clear();
  ensureGlobal();
  emit();
}

export function hierarchyDepthOf(column: string): number | null {
  const match = LEVEL_RE.exec(column);
  return match ? Number(match[1]) : null;
}

function labelAt(levels: HierarchyLevelLabel[] | null, depth: number, plural = false): string {
  const level = levels?.find((item) => item.depth === depth);
  if (level) return plural ? level.label_plural : level.label;
  return (plural ? FALLBACK_PLURAL : FALLBACK)[depth - 1] ?? `Nível ${depth}`;
}

/** Rótulo global de um nível (1-based). */
export function hierarchyLevelLabel(depth: number, plural = false): string {
  ensureGlobal();
  return labelAt(globalLevels, depth, plural);
}

/** Rótulo global de uma coluna hierarquia_N; `null` para outras colunas. */
export function hierarchyLabel(column: string, plural = false): string | null {
  const depth = hierarchyDepthOf(column);
  return depth === null ? null : hierarchyLevelLabel(depth, plural);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  ensureGlobal();
  return () => { listeners.delete(listener); };
}

/** Re-renderiza o componente quando os rótulos globais chegam ou mudam. */
export function useHierarchyLabels(): HierarchyLevelLabel[] | null {
  return useSyncExternalStore(subscribe, () => globalLevels);
}

function fetchScoped(path: string[]): Promise<HierarchyLevelLabel[]> {
  const key = JSON.stringify(path);
  let request = scopedCache.get(key);
  if (!request) {
    request = getHierarchyLevelLabels(path).then((data) => data.levels);
    request.catch(() => scopedCache.delete(key));
    scopedCache.set(key, request);
  }
  return request;
}

/**
 * Rótulos resolvidos no escopo do caminho (nomes da raiz até o nó atual).
 * Enquanto carrega, usa os rótulos globais.
 */
export function useLevelAliases(path: string[] = []) {
  const global = useHierarchyLabels();
  const key = JSON.stringify(path);
  const [scoped, setScoped] = useState<{ key: string; levels: HierarchyLevelLabel[] } | null>(null);

  useEffect(() => {
    if (!path.length) return;
    let active = true;
    fetchScoped(path).then((levels) => { if (active) setScoped({ key, levels }); }).catch(() => undefined);
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, global]);

  return useMemo(() => {
    const levels = path.length && scoped?.key === key ? scoped.levels : global;
    const levelLabel = (depth: number, plural = false) => labelAt(levels, depth, plural);
    return {
      levels,
      levelLabel,
      columnLabel: (column: string, plural = false) => {
        const depth = hierarchyDepthOf(column);
        return depth === null ? column : levelLabel(depth, plural);
      },
      /** Rótulos por profundidade (índice 0 = nível 1), para os seletores de escopo. */
      labels: (count: number) => Array.from({ length: count }, (_, index) => levelLabel(index + 1)),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, scoped, global]);
}
