import { useEffect, useMemo, useState } from "react";
import { Box, Button, ButtonBase, CircularProgress, Drawer, IconButton, InputAdornment, Stack, TextField, Typography } from "@mui/material";
import { AccountTreeOutlined, ArrowBackOutlined, CheckOutlined, ChevronRightOutlined, CloseOutlined, SearchOutlined, TrendingDownOutlined, TrendingUpOutlined } from "@mui/icons-material";
import { Department, GoldQueryFilter, queryDimonTable } from "../../services/api";

// Escopo em painel lateral: um card por filho do nivel navegado, com nome inteiro,
// custo do periodo, participacao e variacao do ultimo mes. Clique entra no card;
// "Aplicar" fixa o escopo navegado.
const BRAND = "#0F62FE";
const brl = (value: number) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 }).format(value);
const pct = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;

type Card = { node: Department; cost: number; mom: number | null; kids: number };

export default function OrgScopeDrawer({ tree, path, levelLabels, onSelect, onClear, table, months, measure }: {
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
  const [search, setSearch] = useState("");
  const [stats, setStats] = useState<{ cost: Map<string, number>; last: Map<string, number>; prev: Map<string, number> } | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { if (open) setBrowse(path); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setSearch(""), [browse.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

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
    setStats(null);
    const column = `organograma_nivel_${browse.length + 1}`;
    const parent: GoldQueryFilter[] = browse.map((name, i) => ({ column: `organograma_nivel_${i + 1}`, operator: "IN", values: [name] }));
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
        setStats({ cost, last: lastBy, prev: prevBy });
      })
      .catch(() => { if (alive) setStats(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, browse.join("|"), table, measure, months.join(",")]);

  // Cards vem da arvore da estrutura; se a arvore nao carregou, usa os nomes que a base devolveu.
  const cards = useMemo<Card[]>(() => {
    const source: Department[] = children.length
      ? children
      : [...(stats?.cost.keys() ?? [])].filter(Boolean).map((name, i) => ({ id: -(i + 1), parent_id: null, name, centro_custo: null, id_orcamento: null, ambiente: null, cloud_filter: null, custom_fields: null, active: true, children: [] } as unknown as Department));
    return source.map((node) => {
      const p = stats?.prev.get(node.name) ?? 0;
      const l = stats?.last.get(node.name) ?? 0;
      return { node, cost: stats?.cost.get(node.name) ?? 0, mom: p > 0 ? (l - p) / p : null, kids: (node.children || []).filter((c) => c.active).length };
    }).filter((card) => !stats || Math.abs(card.cost) > 0.5).sort((a, z) => a.node.name.localeCompare(z.node.name, "pt-BR", { sensitivity: "base" }));
  }, [children, stats]);
  const total = cards.reduce((s, c) => s + Math.max(0, c.cost), 0);
  const term = search.trim().toLocaleLowerCase("pt-BR");
  const visible = cards.filter((c) => !term || c.node.name.toLocaleLowerCase("pt-BR").includes(term));

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

      <Drawer anchor="right" open={open} onClose={() => setOpen(false)} PaperProps={{ sx: { width: "min(560px, 100vw)", display: "flex", flexDirection: "column", bgcolor: "#FAFBFD" } }}>
        <Box sx={{ px: 2.5, pt: 2.25, pb: 1.5, bgcolor: "#fff", borderBottom: "1px solid #EEF2F6" }}>
          <Stack direction="row" alignItems="flex-start" gap={1}>
            {browse.length > 0 && <IconButton size="small" onClick={() => setBrowse(browse.slice(0, -1))} aria-label="Voltar um nível" sx={{ mt: 0.25 }}><ArrowBackOutlined fontSize="small" /></IconButton>}
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ textTransform: "uppercase", letterSpacing: 0.5 }}>{browse.length ? levelLabels[browse.length - 1] ?? `Nível ${browse.length}` : "Escopo"}</Typography>
              <Typography variant="h6" fontWeight={800} lineHeight={1.25} sx={{ wordBreak: "break-word" }}>{browse.length ? browse[browse.length - 1] : "Toda a nuvem"}</Typography>
              <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.25} mt={0.5}>
                <ButtonBase onClick={() => setBrowse([])} sx={{ fontSize: 12, color: BRAND, fontWeight: 600, borderRadius: "4px", px: 0.25 }}>Toda a nuvem</ButtonBase>
                {browse.slice(0, -1).map((name, i) => (
                  <Stack key={`${i}-${name}`} direction="row" alignItems="center">
                    <ChevronRightOutlined sx={{ fontSize: 14, color: "#98A2B3" }} />
                    <ButtonBase onClick={() => setBrowse(browse.slice(0, i + 1))} sx={{ fontSize: 12, color: BRAND, fontWeight: 600, borderRadius: "4px", px: 0.25 }}>{name}</ButtonBase>
                  </Stack>
                ))}
              </Stack>
            </Box>
            <IconButton onClick={() => setOpen(false)} aria-label="Fechar"><CloseOutlined /></IconButton>
          </Stack>
          <Stack direction="row" alignItems="center" gap={1.5} mt={1.5}>
            <TextField
              size="small" fullWidth value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={`Filtrar ${(levelLabels[browse.length] ?? "itens").toLowerCase()}`}
              InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment>, sx: { borderRadius: "10px" } }}
            />
            <Box sx={{ textAlign: "right", flexShrink: 0 }}>
              <Typography variant="caption" color="text.secondary" display="block">Total no período</Typography>
              <Typography variant="subtitle2" fontWeight={800}>{loading ? "…" : brl(total)}</Typography>
            </Box>
          </Stack>
        </Box>

        <Box sx={{ flex: 1, overflowY: "auto", p: 2 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1}>
            <Typography variant="caption" fontWeight={800} color="text.secondary" sx={{ textTransform: "uppercase", letterSpacing: 0.5 }}>{levelLabels[browse.length] ?? "Itens"} · {visible.length}</Typography>
            {loading && <CircularProgress size={14} />}
          </Stack>
          {!loading && !children.length && <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>Este nível não tem filhos. Use Aplicar para fixar o escopo.</Typography>}
          <Box sx={{ display: "grid", gap: 1.25, gridTemplateColumns: "1fr" }}>
            {visible.map((card) => {
              const selected = path.join(">") === [...browse, card.node.name].join(">");
              const share = total > 0 ? Math.max(0, card.cost) / total : 0;
              const up = (card.mom ?? 0) > 0.02;
              const down = (card.mom ?? 0) < -0.02;
              return (
                <ButtonBase
                  key={card.node.id}
                  onClick={() => (card.kids ? setBrowse([...browse, card.node.name]) : apply([...browse, card.node.name]))}
                  sx={{ display: "block", textAlign: "left", p: 1.5, borderRadius: "14px", bgcolor: "#fff", border: `1px solid ${selected ? BRAND : "#E4E7EC"}`, boxShadow: selected ? `0 0 0 3px rgba(15,98,254,.12)` : "0 1px 2px rgba(16,24,40,.04)", transition: "border-color .15s, box-shadow .15s, transform .15s", "&:hover": { borderColor: BRAND, boxShadow: "0 8px 20px rgba(16,24,40,.08)", transform: "translateY(-1px)" } }}
                >
                  <Stack direction="row" alignItems="flex-start" gap={0.5}>
                    <Typography variant="body2" fontWeight={800} color="#101828" sx={{ flex: 1, minWidth: 0, lineHeight: 1.3, wordBreak: "break-word" }}>{card.node.name}</Typography>
                    {card.kids > 0 && <ChevronRightOutlined sx={{ fontSize: 18, color: "#98A2B3", flexShrink: 0 }} />}
                  </Stack>
                  <Typography variant="h6" fontWeight={800} color="#101828" mt={1} lineHeight={1}>{brl(card.cost)}</Typography>
                  <Box sx={{ height: 4, mt: 1, borderRadius: 2, bgcolor: "#EEF2F7", overflow: "hidden" }}>
                    <Box sx={{ width: `${share * 100}%`, height: "100%", bgcolor: BRAND, borderRadius: 2 }} />
                  </Box>
                  <Stack direction="row" alignItems="center" justifyContent="space-between" mt={0.75} gap={1}>
                    <Typography variant="caption" color="text.secondary">{(share * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% do total{card.kids ? ` · ${card.kids} ${card.kids === 1 ? "filho" : "filhos"}` : ""}</Typography>
                    {card.mom != null && (
                      <Stack direction="row" alignItems="center" gap={0.25} sx={{ px: 0.75, py: 0.1, borderRadius: "6px", bgcolor: up ? "#FEF3F2" : down ? "#ECFDF3" : "#F2F4F7", color: up ? "#B42318" : down ? "#027A48" : "#475467" }}>
                        {up ? <TrendingUpOutlined sx={{ fontSize: 14 }} /> : down ? <TrendingDownOutlined sx={{ fontSize: 14 }} /> : null}
                        <Typography variant="caption" fontWeight={700}>{pct(card.mom)}</Typography>
                      </Stack>
                    )}
                  </Stack>
                </ButtonBase>
              );
            })}
          </Box>
        </Box>

        <Stack direction="row" alignItems="center" gap={1} sx={{ px: 2.5, py: 1.5, bgcolor: "#fff", borderTop: "1px solid #EEF2F6" }}>
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>Variação: último mês do período contra o anterior.</Typography>
          <Button onClick={() => setOpen(false)} sx={{ textTransform: "none" }}>Cancelar</Button>
          <Button variant="contained" disableElevation startIcon={<CheckOutlined />} onClick={() => apply(browse)} sx={{ textTransform: "none", borderRadius: "10px", maxWidth: 260 }}>
            <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Aplicar {browse.length ? browse[browse.length - 1] : "Toda a nuvem"}</Box>
          </Button>
        </Stack>
      </Drawer>
    </>
  );
}
