import { useEffect, useMemo, useState } from "react";
import { Box, ButtonBase, CircularProgress, InputAdornment, Menu, Stack, TextField, Typography } from "@mui/material";
import { AddOutlined, BusinessOutlined, CheckOutlined, ChevronRightOutlined, CloseOutlined, ExpandMoreOutlined, SearchOutlined } from "@mui/icons-material";
import { Department, GoldQueryFilter, queryDimonTable } from "../../services/api";

// Escopo como trilha: um chip por nivel da estrutura organizacional. Cada chip abre
// so as opcoes daquele nivel (irmaos), com o custo do periodo; o chip "+" desce um nivel.
const BRAND = "#0F62FE";
const brl = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);

export default function OrgScopeTrail({ tree, path, levelLabels, onSelect, onClear, table, periodFilters, measure }: {
  tree: Department[];
  path: string[];
  levelLabels: string[];
  onSelect: (node: Department, path: string[], depth: number) => void;
  onClear: () => void;
  table: string;
  periodFilters: GoldQueryFilter[];
  measure: string;
}) {
  const [menu, setMenu] = useState<{ depth: number; anchor: HTMLElement } | null>(null);
  const [search, setSearch] = useState("");
  const [costs, setCosts] = useState<Record<string, number>>({});
  const [loadingCosts, setLoadingCosts] = useState(false);

  // Nos de cada nivel ao longo da trilha atual.
  const levels = useMemo(() => {
    const out: Department[][] = [];
    let current = tree.filter((node) => node.active);
    for (let depth = 0; depth <= path.length; depth += 1) {
      out.push(current);
      const node = current.find((item) => item.name === path[depth]);
      if (!node) break;
      current = (node.children || []).filter((child) => child.active);
    }
    return out;
  }, [tree, path]);

  const options = menu ? levels[menu.depth] ?? [] : [];

  useEffect(() => {
    if (!menu) return;
    let alive = true;
    setSearch("");
    setCosts({});
    setLoadingCosts(true);
    const column = `organograma_nivel_${menu.depth + 1}`;
    const parent = path.slice(0, menu.depth).map((name, i) => ({ column: `organograma_nivel_${i + 1}`, operator: "IN" as const, values: [name] }));
    queryDimonTable({ table, rows: [column], measures: [{ column: measure, aggregation: "SUM" }], filters: [...periodFilters, ...parent], row_limit: 1000, sort_column: null, sort_direction: "desc" })
      .then((result) => {
        if (!alive) return;
        const next: Record<string, number> = {};
        result.rows.forEach((row) => { next[String(row[column] ?? "")] = Number(row[measure] ?? 0); });
        setCosts(next);
      })
      .catch(() => { if (alive) setCosts({}); })
      .finally(() => { if (alive) setLoadingCosts(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [menu?.depth, menu?.anchor]);

  const visible = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("pt-BR");
    const list = options.filter((node) => !term || node.name.toLocaleLowerCase("pt-BR").includes(term));
    return [...list].sort((a, z) => (costs[z.name] ?? 0) - (costs[a.name] ?? 0) || a.name.localeCompare(z.name, "pt-BR"));
  }, [options, search, costs]);
  const total = Object.values(costs).reduce((sum, value) => sum + Math.max(value, 0), 0);

  const choose = (node: Department) => {
    if (!menu) return;
    const nextPath = [...path.slice(0, menu.depth), node.name];
    onSelect(node, nextPath, menu.depth);
    setMenu(null);
  };

  const removeLast = () => {
    if (path.length <= 1) { onClear(); return; }
    const parentPath = path.slice(0, -1);
    let current = tree;
    let node: Department | undefined;
    parentPath.forEach((name) => { node = current.find((item) => item.name === name); current = node?.children || []; });
    if (node) onSelect(node, parentPath, parentPath.length - 1);
  };

  const canGoDeeper = (levels[path.length] ?? []).length > 0;
  const chipSx = (active: boolean) => ({
    height: 34, px: 1.25, borderRadius: "10px", gap: 0.5, maxWidth: 260,
    border: `1px solid ${active ? BRAND : "#D0D9E6"}`, bgcolor: active ? "rgba(15, 98, 254, 0.08)" : "#fff",
    color: active ? BRAND : "#344054", fontSize: 13, fontWeight: active ? 700 : 600,
    transition: "background-color .15s, border-color .15s", "&:hover": { borderColor: BRAND, bgcolor: "rgba(15, 98, 254, 0.05)" },
  });

  return (
    <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.5} sx={{ minWidth: 0 }}>
      <BusinessOutlined fontSize="small" sx={{ color: "#667085", mr: 0.25 }} />
      {path.length === 0 && (
        <ButtonBase onClick={(event) => setMenu({ depth: 0, anchor: event.currentTarget })} sx={chipSx(false)}>
          Toda a nuvem <ExpandMoreOutlined sx={{ fontSize: 18 }} />
        </ButtonBase>
      )}
      {path.map((name, depth) => (
        <Stack key={`${depth}-${name}`} direction="row" alignItems="center" gap={0.5}>
          {depth > 0 && <ChevronRightOutlined sx={{ fontSize: 16, color: "#98A2B3" }} />}
          <ButtonBase onClick={(event) => setMenu({ depth, anchor: event.currentTarget })} sx={chipSx(depth === path.length - 1)} title={`${levelLabels[depth] ?? `Nível ${depth + 1}`}: ${name}`}>
            <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</Box>
            <ExpandMoreOutlined sx={{ fontSize: 18, flexShrink: 0 }} />
          </ButtonBase>
          {depth === path.length - 1 && (
            <ButtonBase aria-label="Subir um nível" onClick={removeLast} sx={{ width: 22, height: 22, borderRadius: "50%", color: "#667085", "&:hover": { bgcolor: "#EEF2F7" } }}>
              <CloseOutlined sx={{ fontSize: 15 }} />
            </ButtonBase>
          )}
        </Stack>
      ))}
      {path.length > 0 && canGoDeeper && (
        <ButtonBase onClick={(event) => setMenu({ depth: path.length, anchor: event.currentTarget })} sx={{ ...chipSx(false), borderStyle: "dashed", color: "#667085" }}>
          <AddOutlined sx={{ fontSize: 16 }} /> {levelLabels[path.length] ?? "Nível"}
        </ButtonBase>
      )}

      <Menu
        anchorEl={menu?.anchor}
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
        autoFocus={false}
        PaperProps={{ sx: { mt: 0.5, width: 380, maxWidth: "calc(100vw - 32px)", borderRadius: "14px", border: "1px solid #E1E8F0", boxShadow: "0 16px 44px rgba(24,39,75,.16)" } }}
        MenuListProps={{ sx: { py: 0 } }}
      >
        <Box sx={{ px: 1.5, pt: 1.25, pb: 1, borderBottom: "1px solid #EEF3F8" }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={options.length > 6 ? 1 : 0}>
            <Typography variant="overline" fontWeight={800} color="text.secondary" lineHeight={1.4}>{menu ? levelLabels[menu.depth] ?? `Nível ${menu.depth + 1}` : ""}</Typography>
            {loadingCosts ? <CircularProgress size={12} /> : total > 0 && <Typography variant="caption" color="text.secondary">Total {brl(total)}</Typography>}
          </Stack>
          {options.length > 6 && (
            <TextField
              fullWidth size="small" autoFocus value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
              placeholder="Filtrar"
              InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment>, sx: { borderRadius: "10px" } }}
            />
          )}
        </Box>
        <Box sx={{ maxHeight: 380, overflowY: "auto", p: 0.75 }}>
          {visible.map((node) => {
            const active = menu ? path[menu.depth] === node.name : false;
            const cost = costs[node.name];
            const share = total > 0 && cost ? Math.max(0, cost) / total : 0;
            return (
              <ButtonBase key={node.id} onClick={() => choose(node)} sx={{ display: "block", width: "100%", textAlign: "left", px: 1.25, py: 0.9, borderRadius: "10px", bgcolor: active ? "rgba(15, 98, 254, 0.08)" : "transparent", "&:hover": { bgcolor: active ? "rgba(15, 98, 254, 0.12)" : "#F4F6FA" } }}>
                <Stack direction="row" alignItems="flex-start" gap={1}>
                  <Typography variant="body2" fontWeight={active ? 800 : 600} color={active ? BRAND : "text.primary"} sx={{ flex: 1, minWidth: 0, lineHeight: 1.35, wordBreak: "break-word" }}>{node.name}</Typography>
                  {cost != null && <Typography variant="caption" fontWeight={700} color="text.secondary" sx={{ flexShrink: 0, mt: 0.2 }}>{brl(cost)}</Typography>}
                  {active && <CheckOutlined sx={{ fontSize: 16, color: BRAND, mt: 0.2 }} />}
                </Stack>
                {share > 0 && (
                  <Box sx={{ height: 3, mt: 0.6, borderRadius: 2, bgcolor: "#EEF2F7", overflow: "hidden" }}>
                    <Box sx={{ width: `${share * 100}%`, height: "100%", bgcolor: BRAND, opacity: 0.55 }} />
                  </Box>
                )}
              </ButtonBase>
            );
          })}
          {!visible.length && <Typography variant="body2" color="text.secondary" sx={{ p: 1.5 }}>Nada encontrado.</Typography>}
        </Box>
        {menu?.depth === 0 && path.length > 0 && (
          <Box sx={{ borderTop: "1px solid #EEF3F8", p: 0.75 }}>
            <ButtonBase onClick={() => { onClear(); setMenu(null); }} sx={{ width: "100%", justifyContent: "flex-start", px: 1.25, py: 0.75, borderRadius: "10px", color: BRAND, fontWeight: 700, fontSize: 13 }}>Ver toda a nuvem</ButtonBase>
          </Box>
        )}
      </Menu>
    </Stack>
  );
}
