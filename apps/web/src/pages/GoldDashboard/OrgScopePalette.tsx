import { useEffect, useMemo, useRef, useState } from "react";
import { Box, ButtonBase, Dialog, IconButton, InputBase, Stack, Typography } from "@mui/material";
import { BusinessOutlined, ChevronRightOutlined, HistoryOutlined, KeyboardReturnOutlined, SearchOutlined, StarBorderOutlined, StarOutlined } from "@mui/icons-material";
import { Department, GoldQueryFilter, queryDimonTable } from "../../services/api";

// Escopo como paleta de comando (estilo Raycast/Linear): um campo na barra que abre
// uma busca em toda a estrutura organizacional. Mostra caminho, nivel e custo do
// periodo; guarda recentes e favoritos no navegador. Atalho: Cmd/Ctrl + K.
const BRAND = "#0F62FE";
const RECENT_KEY = "openfinops.scopeRecent";
const FAVORITE_KEY = "openfinops.scopeFavorites";
const brl = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const readList = (key: string): string[] => { try { return JSON.parse(window.localStorage.getItem(key) || "[]"); } catch { return []; } };
const writeList = (key: string, list: string[]) => { try { window.localStorage.setItem(key, JSON.stringify(list)); } catch { /* ignore */ } };

type Entry = { node: Department; path: string[]; key: string };

export default function OrgScopePalette({ tree, path, levelLabels, onSelect, onClear, table, periodFilters, measure }: {
  tree: Department[];
  path: string[];
  levelLabels: string[];
  onSelect: (node: Department, path: string[], depth: number) => void;
  onClear: () => void;
  table: string;
  periodFilters: GoldQueryFilter[];
  measure: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [costs, setCosts] = useState<Record<string, number>>({});
  const [recent, setRecent] = useState<string[]>(() => readList(RECENT_KEY));
  const [favorites, setFavorites] = useState<string[]>(() => readList(FAVORITE_KEY));
  const listRef = useRef<HTMLDivElement>(null);

  const entries = useMemo(() => {
    const out: Entry[] = [];
    const walk = (nodes: Department[], parent: string[]) => nodes.filter((n) => n.active).forEach((node) => {
      const p = [...parent, node.name];
      out.push({ node, path: p, key: p.join(" > ") });
      walk(node.children || [], p);
    });
    walk(tree, []);
    return out;
  }, [tree]);
  const byKey = useMemo(() => new Map(entries.map((e) => [e.key, e])), [entries]);

  // Atalho global Cmd/Ctrl + K.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setOpen(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Custo de cada no: uma consulta com todos os niveis, somada por prefixo do caminho.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const levels = [1, 2, 3, 4, 5, 6].map((n) => `hierarquia_${n}`);
    queryDimonTable({ table, rows: levels, measures: [{ column: measure, aggregation: "SUM" }], filters: periodFilters, row_limit: 10000, sort_column: null, sort_direction: "desc" })
      .then((result) => {
        if (!alive) return;
        const next: Record<string, number> = {};
        result.rows.forEach((row) => {
          const value = Number(row[measure] ?? 0);
          const parts: string[] = [];
          for (const level of levels) {
            const name = row[level];
            if (name == null || name === "") break;
            parts.push(String(name));
            const key = parts.join(" > ");
            next[key] = (next[key] ?? 0) + value;
          }
        });
        setCosts(next);
      })
      .catch(() => { if (alive) setCosts({}); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, table, measure, JSON.stringify(periodFilters)]);

  useEffect(() => { if (open) { setQuery(""); setCursor(0); } }, [open]);

  const currentKey = path.join(" > ");
  const sections = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("pt-BR");
    if (term) {
      const words = term.split(/\s+/);
      const hits = entries
        .filter((e) => words.every((w) => e.key.toLocaleLowerCase("pt-BR").includes(w)))
        .map((e) => ({ e, nameHit: e.node.name.toLocaleLowerCase("pt-BR").includes(words[0]) }))
        .sort((a, z) => Number(z.nameHit) - Number(a.nameHit) || (costs[z.e.key] ?? 0) - (costs[a.e.key] ?? 0))
        .slice(0, 40)
        .map((x) => x.e);
      return [{ title: `Resultados (${hits.length})`, items: hits }];
    }
    const out: Array<{ title: string; items: Entry[] }> = [];
    const fav = favorites.map((k) => byKey.get(k)).filter(Boolean) as Entry[];
    if (fav.length) out.push({ title: "Favoritos", items: fav });
    const rec = recent.filter((k) => !favorites.includes(k)).map((k) => byKey.get(k)).filter(Boolean).slice(0, 5) as Entry[];
    if (rec.length) out.push({ title: "Recentes", items: rec });
    const here = currentKey ? byKey.get(currentKey) : undefined;
    const children = (here ? (here.node.children || []) : tree).filter((n) => n.active)
      .map((n) => byKey.get(here ? `${currentKey} > ${n.name}` : n.name)).filter(Boolean) as Entry[];
    children.sort((a, z) => (costs[z.key] ?? 0) - (costs[a.key] ?? 0));
    if (children.length) out.push({ title: here ? `Dentro de ${here.node.name}` : "Estrutura", items: children });
    if (here && path.length > 1) {
      const parent = byKey.get(path.slice(0, -1).join(" > "));
      if (parent) out.push({ title: "Subir", items: [parent] });
    }
    return out;
  }, [query, entries, costs, favorites, recent, byKey, currentKey, tree, path]);

  const flat = sections.flatMap((s) => s.items);
  useEffect(() => { setCursor((c) => Math.min(c, Math.max(0, flat.length - 1))); }, [flat.length]);
  useEffect(() => { listRef.current?.querySelector(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" }); }, [cursor]);

  const apply = (entry: Entry) => {
    onSelect(entry.node, entry.path, entry.path.length - 1);
    const next = [entry.key, ...recent.filter((k) => k !== entry.key)].slice(0, 8);
    setRecent(next); writeList(RECENT_KEY, next);
    setOpen(false);
  };
  const toggleFavorite = (key: string) => {
    const next = favorites.includes(key) ? favorites.filter((k) => k !== key) : [...favorites, key];
    setFavorites(next); writeList(FAVORITE_KEY, next);
  };
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") { event.preventDefault(); setCursor((c) => Math.min(flat.length - 1, c + 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (event.key === "Enter" && flat[cursor]) { event.preventDefault(); apply(flat[cursor]); }
  };

  const isMac = typeof navigator !== "undefined" && /mac/i.test(navigator.platform);
  const total = costs[path[0] ?? ""] ?? Object.entries(costs).filter(([k]) => !k.includes(" > ")).reduce((s, [, v]) => s + v, 0);
  let index = -1;

  return (
    <>
      <ButtonBase
        onClick={() => setOpen(true)}
        sx={{ width: "100%", height: 34, px: 1.25, gap: 1, justifyContent: "flex-start", borderRadius: "10px", border: "1px solid #D0D9E6", bgcolor: "#fff", "&:hover": { borderColor: BRAND } }}
      >
        <SearchOutlined sx={{ fontSize: 18, color: "#667085" }} />
        <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0, textAlign: "left", color: "#344054" }}>
          {path.length ? (
            <>
              <Box component="span" sx={{ color: "#98A2B3" }}>{path.slice(0, -1).join(" › ")}{path.length > 1 ? " › " : ""}</Box>
              <Box component="span" sx={{ fontWeight: 700, color: BRAND }}>{path[path.length - 1]}</Box>
            </>
          ) : "Toda a nuvem · buscar escopo"}
        </Typography>
        <Box sx={{ px: 0.75, py: 0.1, borderRadius: "6px", border: "1px solid #E4E7EC", bgcolor: "#F9FAFB", fontSize: 11, fontWeight: 700, color: "#667085", flexShrink: 0 }}>{isMac ? "⌘K" : "Ctrl K"}</Box>
      </ButtonBase>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        fullWidth
        maxWidth="sm"
        PaperProps={{ sx: { borderRadius: "16px", position: "absolute", top: "10vh", m: 0, width: "min(680px, calc(100vw - 32px))", overflow: "hidden", boxShadow: "0 24px 64px rgba(16,24,40,.24)" } }}
      >
        <Stack direction="row" alignItems="center" gap={1.25} sx={{ px: 2, py: 1.5, borderBottom: "1px solid #EEF2F6" }}>
          <SearchOutlined sx={{ color: "#667085" }} />
          <InputBase autoFocus fullWidth value={query} onChange={(e) => { setQuery(e.target.value); setCursor(0); }} onKeyDown={onKeyDown} placeholder="Busque segmento, plataforma, diretoria, área ou projeto…" sx={{ fontSize: 16 }} />
          {path.length > 0 && <ButtonBase onClick={() => { onClear(); setOpen(false); }} sx={{ px: 1, py: 0.5, borderRadius: "8px", fontSize: 12, fontWeight: 700, color: BRAND, whiteSpace: "nowrap", "&:hover": { bgcolor: "rgba(15,98,254,.06)" } }}>Toda a nuvem</ButtonBase>}
        </Stack>

        <Box ref={listRef} sx={{ maxHeight: "56vh", overflowY: "auto", py: 0.75 }}>
          {sections.map((section) => (
            <Box key={section.title} sx={{ px: 1, pb: 0.5 }}>
              <Stack direction="row" alignItems="center" gap={0.75} sx={{ px: 1.25, pt: 1, pb: 0.5, color: "#98A2B3" }}>
                {section.title === "Recentes" && <HistoryOutlined sx={{ fontSize: 14 }} />}
                {section.title === "Favoritos" && <StarOutlined sx={{ fontSize: 14 }} />}
                <Typography variant="caption" fontWeight={800} sx={{ textTransform: "uppercase", letterSpacing: 0.5 }}>{section.title}</Typography>
              </Stack>
              {section.items.map((entry) => {
                index += 1;
                const i = index;
                const active = i === cursor;
                const cost = costs[entry.key];
                const share = total > 0 && cost ? Math.min(1, Math.max(0, cost) / total) : 0;
                const fav = favorites.includes(entry.key);
                const hasChildren = (entry.node.children || []).some((c) => c.active);
                return (
                  <Stack
                    key={`${section.title}-${entry.key}`}
                    data-index={i}
                    direction="row"
                    alignItems="center"
                    gap={1.25}
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => apply(entry)}
                    sx={{ px: 1.25, py: 1, borderRadius: "10px", cursor: "pointer", bgcolor: active ? "rgba(15,98,254,.07)" : "transparent", borderLeft: `3px solid ${active ? BRAND : "transparent"}` }}
                  >
                    <Box sx={{ width: 30, height: 30, borderRadius: "8px", display: "grid", placeItems: "center", bgcolor: entry.key === currentKey ? BRAND : "#F2F4F7", color: entry.key === currentKey ? "#fff" : "#667085", flexShrink: 0 }}>
                      <BusinessOutlined sx={{ fontSize: 16 }} />
                    </Box>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Stack direction="row" alignItems="center" gap={0.75}>
                        <Typography variant="body2" fontWeight={700} sx={{ wordBreak: "break-word" }}>{entry.node.name}</Typography>
                        <Typography variant="caption" sx={{ px: 0.6, borderRadius: "5px", bgcolor: "#F2F4F7", color: "#667085", fontWeight: 700, flexShrink: 0 }}>{levelLabels[entry.path.length - 1] ?? `Nível ${entry.path.length}`}</Typography>
                      </Stack>
                      {entry.path.length > 1 && <Typography variant="caption" color="text.secondary" noWrap display="block" title={entry.path.slice(0, -1).join(" › ")}>{entry.path.slice(0, -1).join(" › ")}</Typography>}
                    </Box>
                    <Box sx={{ width: 96, flexShrink: 0, textAlign: "right" }}>
                      {cost != null && <Typography variant="body2" fontWeight={800}>{brl(cost)}</Typography>}
                      {share > 0 && <Box sx={{ height: 3, mt: 0.4, borderRadius: 2, bgcolor: "#EEF2F7", overflow: "hidden" }}><Box sx={{ width: `${share * 100}%`, height: "100%", bgcolor: BRAND, opacity: 0.6, ml: "auto" }} /></Box>}
                    </Box>
                    <IconButton size="small" aria-label={fav ? "Remover dos favoritos" : "Favoritar"} onClick={(e) => { e.stopPropagation(); toggleFavorite(entry.key); }} sx={{ color: fav ? "#F79009" : "#D0D5DD" }}>
                      {fav ? <StarOutlined fontSize="small" /> : <StarBorderOutlined fontSize="small" />}
                    </IconButton>
                    {hasChildren && !query && <ChevronRightOutlined fontSize="small" sx={{ color: "#D0D5DD" }} />}
                  </Stack>
                );
              })}
            </Box>
          ))}
          {!flat.length && <Typography variant="body2" color="text.secondary" sx={{ px: 3, py: 4, textAlign: "center" }}>Nenhum nível encontrado para “{query}”.</Typography>}
        </Box>

        <Stack direction="row" gap={2} sx={{ px: 2, py: 1, borderTop: "1px solid #EEF2F6", bgcolor: "#FAFBFD", color: "#98A2B3" }}>
          <Typography variant="caption">↑↓ navegar</Typography>
          <Stack direction="row" alignItems="center" gap={0.5}><KeyboardReturnOutlined sx={{ fontSize: 13 }} /><Typography variant="caption">aplicar escopo</Typography></Stack>
          <Typography variant="caption">esc fechar</Typography>
          <Box sx={{ flex: 1 }} />
          <Typography variant="caption">Custo do período selecionado</Typography>
        </Stack>
      </Dialog>
    </>
  );
}
