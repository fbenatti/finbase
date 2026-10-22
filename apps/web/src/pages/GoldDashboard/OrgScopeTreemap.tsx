import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, ButtonBase, CircularProgress, Dialog, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { AccountTreeOutlined, CheckOutlined, ChevronRightOutlined, CloseOutlined } from "@mui/icons-material";
import { Department, GoldQueryFilter, queryDimonTable } from "../../services/api";

// Escopo como mapa de blocos: cada filho do nivel atual e um bloco do tamanho do
// custo no periodo, colorido pela variacao do ultimo mes contra o anterior.
// Clique entra no bloco; "Aplicar" fixa o escopo navegado.
const BRAND = "#0F62FE";
const brl = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const pct = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}%`;

type Item = { node: Department; cost: number; mom: number | null };
type Rect = { item: Item; x: number; y: number; w: number; h: number };

// Squarified treemap (Bruls et al.), em coordenadas de 0 a 1.
function squarify(items: Item[], x: number, y: number, w: number, h: number): Rect[] {
  const total = items.reduce((s, i) => s + i.cost, 0);
  if (!items.length || total <= 0) return [];
  const scale = (w * h) / total;
  const areas = items.map((item) => ({ item, area: item.cost * scale }));
  const out: Rect[] = [];
  let rest = areas;
  let rx = x, ry = y, rw = w, rh = h;
  const worst = (row: typeof areas, side: number) => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    const max = Math.max(...row.map((r) => r.area));
    const min = Math.min(...row.map((r) => r.area));
    return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
  };
  while (rest.length) {
    const side = Math.min(rw, rh);
    let row = [rest[0]];
    let i = 1;
    while (i < rest.length && worst([...row, rest[i]], side) <= worst(row, side)) { row = [...row, rest[i]]; i += 1; }
    const sum = row.reduce((s, r) => s + r.area, 0);
    if (rw >= rh) {
      const colW = sum / rh;
      let cy = ry;
      row.forEach((r) => { const hh = r.area / colW; out.push({ item: r.item, x: rx, y: cy, w: colW, h: hh }); cy += hh; });
      rx += colW; rw -= colW;
    } else {
      const rowH = sum / rw;
      let cx = rx;
      row.forEach((r) => { const ww = r.area / rowH; out.push({ item: r.item, x: cx, y: ry, w: ww, h: rowH }); cx += ww; });
      ry += rowH; rh -= rowH;
    }
    rest = rest.slice(i);
  }
  return out;
}

function tone(mom: number | null) {
  if (mom == null) return { bg: "#E8EEF9", fg: "#1D2939" };
  const t = Math.min(1, Math.abs(mom) / 0.3);
  if (mom > 0.02) return { bg: `rgba(217, 45, 32, ${0.18 + 0.62 * t})`, fg: t > 0.55 ? "#fff" : "#1D2939" };
  if (mom < -0.02) return { bg: `rgba(18, 183, 106, ${0.18 + 0.62 * t})`, fg: t > 0.55 ? "#fff" : "#1D2939" };
  return { bg: "#E8EEF9", fg: "#1D2939" };
}

export default function OrgScopeTreemap({ tree, path, levelLabels, onSelect, onClear, table, months, measure }: {
  tree: Department[];
  path: string[];
  levelLabels: string[];
  onSelect: (node: Department, path: string[], depth: number) => void;
  onClear: () => void;
  table: string;
  months: string[];
  measure: string;
}) {
  const [open, setOpen] = useState(false);
  const [browse, setBrowse] = useState<string[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (open) setBrowse(path); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const nodeAt = (p: string[]) => {
    let level = tree.filter((n) => n.active);
    let node: Department | undefined;
    for (const name of p) {
      node = level.find((n) => n.name === name);
      if (!node) return undefined;
      level = (node.children || []).filter((c) => c.active);
    }
    return node;
  };
  const current = nodeAt(browse);
  const children = useMemo(() => (browse.length ? (current?.children || []) : tree).filter((n) => n.active), [browse, current, tree]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    const depth = browse.length;
    const column = `hierarquia_${depth + 1}`;
    const parent: GoldQueryFilter[] = browse.map((name, i) => ({ column: `hierarquia_${i + 1}`, operator: "IN", values: [name] }));
    const sorted = [...months].sort();
    const last = sorted[sorted.length - 1];
    const prev = sorted[sorted.length - 2];
    const period: GoldQueryFilter[] = sorted.length ? [{ column: "_billing_period", operator: "IN", values: sorted }] : [];
    queryDimonTable({ table, rows: [column, "_billing_period"], measures: [{ column: measure, aggregation: "SUM" }], filters: [...period, ...parent], row_limit: 10000, sort_column: null, sort_direction: "desc" })
      .then((result) => {
        if (!alive) return;
        const cost = new Map<string, number>();
        const lastBy = new Map<string, number>();
        const prevBy = new Map<string, number>();
        result.rows.forEach((row) => {
          const name = String(row[column] ?? "");
          const value = Number(row[measure] ?? 0);
          const key = String(row._billing_period ?? "").replace("-", "").slice(0, 6);
          cost.set(name, (cost.get(name) ?? 0) + value);
          if (key === last) lastBy.set(name, (lastBy.get(name) ?? 0) + value);
          if (key === prev) prevBy.set(name, (prevBy.get(name) ?? 0) + value);
        });
        setItems(children
          .map((node) => {
            const p = prevBy.get(node.name) ?? 0;
            const l = lastBy.get(node.name) ?? 0;
            return { node, cost: Math.max(0, cost.get(node.name) ?? 0), mom: p > 0 ? (l - p) / p : null };
          })
          .filter((i) => i.cost > 0)
          .sort((a, z) => z.cost - a.cost));
      })
      .catch(() => { if (alive) setItems([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, browse.join("|"), table, measure, months.join(",")]);

  const rects = useMemo(() => squarify(items, 0, 0, 1.6, 1), [items]);
  const total = items.reduce((s, i) => s + i.cost, 0);
  const empty = children.filter((c) => !items.some((i) => i.node.id === c.id));

  const apply = (p: string[]) => {
    const node = nodeAt(p);
    if (!p.length) onClear();
    else if (node) onSelect(node, p, p.length - 1);
    setOpen(false);
  };

  return (
    <>
      <ButtonBase onClick={() => setOpen(true)} sx={{ width: "100%", height: 34, px: 1.25, gap: 1, justifyContent: "flex-start", borderRadius: "10px", border: "1px solid #D0D9E6", bgcolor: "#fff", "&:hover": { borderColor: BRAND } }}>
        <AccountTreeOutlined sx={{ fontSize: 18, color: "#667085" }} />
        <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0, textAlign: "left" }}>
          <Box component="span" sx={{ color: "#667085" }}>Escopo: </Box>
          {path.length ? <><Box component="span" sx={{ color: "#98A2B3" }}>{path.slice(0, -1).join(" › ")}{path.length > 1 ? " › " : ""}</Box><Box component="span" sx={{ fontWeight: 700, color: BRAND }}>{path[path.length - 1]}</Box></> : <Box component="span" sx={{ fontWeight: 700 }}>Toda a nuvem</Box>}
        </Typography>
      </ButtonBase>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth={false} PaperProps={{ sx: { width: "min(1080px, calc(100vw - 32px))", borderRadius: "18px", overflow: "hidden" } }}>
        <Stack direction="row" alignItems="center" gap={1} sx={{ px: 2.5, pt: 2, pb: 1.25 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle1" fontWeight={800}>Escolha o escopo</Typography>
            <Typography variant="caption" color="text.secondary">Tamanho = custo no período. Cor = último mês contra o anterior (vermelho subiu, verde caiu). Clique num bloco para entrar.</Typography>
          </Box>
          <IconButton onClick={() => setOpen(false)} aria-label="Fechar"><CloseOutlined /></IconButton>
        </Stack>

        <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.25} sx={{ px: 2, pb: 1.25 }}>
          <Button size="small" onClick={() => setBrowse([])} sx={{ textTransform: "none", minWidth: 0, fontWeight: browse.length ? 500 : 800 }}>Toda a nuvem</Button>
          {browse.map((name, i) => (
            <Stack key={`${i}-${name}`} direction="row" alignItems="center">
              <ChevronRightOutlined sx={{ fontSize: 16, color: "text.disabled" }} />
              <Button size="small" onClick={() => setBrowse(browse.slice(0, i + 1))} sx={{ textTransform: "none", minWidth: 0, fontWeight: i === browse.length - 1 ? 800 : 500 }}>{name}</Button>
            </Stack>
          ))}
          {loading && <CircularProgress size={14} sx={{ ml: 1 }} />}
        </Stack>

        <Box sx={{ px: 2 }}>
          <Box ref={boxRef} sx={{ position: "relative", width: "100%", aspectRatio: "1.6 / 1", maxHeight: "58vh", borderRadius: "12px", overflow: "hidden", bgcolor: "#F4F6FA" }}>
            {!loading && !rects.length && (
              <Stack sx={{ position: "absolute", inset: 0 }} alignItems="center" justifyContent="center">
                <Typography variant="body2" color="text.secondary">{children.length ? "Sem custo no período para os filhos deste nível." : "Este nível não tem filhos. Use Aplicar para fixar o escopo."}</Typography>
              </Stack>
            )}
            {rects.map((r) => {
              const c = tone(r.item.mom);
              const big = r.w * r.h > 0.04;
              const hasChildren = (r.item.node.children || []).some((n) => n.active);
              const label = `${r.item.node.name} · ${brl(r.item.cost)} · ${(r.item.cost / total * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% do total${r.item.mom != null ? ` · ${pct(r.item.mom)} no mês` : ""}`;
              return (
                <Tooltip key={r.item.node.id} title={label} arrow followCursor>
                  <ButtonBase
                    onClick={() => (hasChildren ? setBrowse([...browse, r.item.node.name]) : apply([...browse, r.item.node.name]))}
                    sx={{
                      position: "absolute", left: `${(r.x / 1.6) * 100}%`, top: `${r.y * 100}%`, width: `${(r.w / 1.6) * 100}%`, height: `${r.h * 100}%`,
                      p: 0.5, boxSizing: "border-box",
                    }}
                  >
                    <Box sx={{ width: "100%", height: "100%", borderRadius: "10px", bgcolor: c.bg, color: c.fg, p: big ? 1.25 : 0.5, display: "flex", flexDirection: "column", alignItems: "flex-start", justifyContent: "space-between", textAlign: "left", overflow: "hidden", transition: "filter .15s, box-shadow .15s", "&:hover": { filter: "brightness(0.95)", boxShadow: `inset 0 0 0 2px ${BRAND}` } }}>
                      <Typography sx={{ fontSize: big ? 14 : 11, fontWeight: 800, lineHeight: 1.2, wordBreak: "break-word" }}>{r.item.node.name}</Typography>
                      {big && (
                        <Stack direction="row" alignItems="baseline" gap={1} flexWrap="wrap">
                          <Typography sx={{ fontSize: 18, fontWeight: 800 }}>{brl(r.item.cost)}</Typography>
                          {r.item.mom != null && <Typography sx={{ fontSize: 12, fontWeight: 700, opacity: 0.85 }}>{pct(r.item.mom)}</Typography>}
                          {hasChildren && <ChevronRightOutlined sx={{ fontSize: 16, opacity: 0.7 }} />}
                        </Stack>
                      )}
                    </Box>
                  </ButtonBase>
                </Tooltip>
              );
            })}
          </Box>
          {empty.length > 0 && (
            <Typography variant="caption" color="text.secondary" display="block" mt={1}>
              Sem custo no período: {empty.slice(0, 8).map((n) => n.name).join(", ")}{empty.length > 8 ? ` e mais ${empty.length - 8}` : ""}
            </Typography>
          )}
        </Box>

        <Stack direction="row" alignItems="center" gap={1.5} sx={{ px: 2.5, py: 1.5, mt: 1, borderTop: "1px solid #EEF2F6", bgcolor: "#FAFBFD" }}>
          <Stack direction="row" alignItems="center" gap={1} sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary">{levelLabels[browse.length] ? `Mostrando: ${levelLabels[browse.length]}` : ""}</Typography>
            <Typography variant="caption" fontWeight={700}>Total {brl(total)}</Typography>
          </Stack>
          <Button onClick={() => setOpen(false)} sx={{ textTransform: "none" }}>Cancelar</Button>
          <Button variant="contained" disableElevation startIcon={<CheckOutlined />} onClick={() => apply(browse)} sx={{ textTransform: "none", borderRadius: "10px" }}>
            Aplicar {browse.length ? browse[browse.length - 1] : "Toda a nuvem"}
          </Button>
        </Stack>
      </Dialog>
    </>
  );
}
