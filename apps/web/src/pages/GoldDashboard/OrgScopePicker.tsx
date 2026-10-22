import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, InputAdornment, Popover, Stack, TextField, Typography } from "@mui/material";
import { ChevronRightOutlined, CheckOutlined, SearchOutlined } from "@mui/icons-material";
import { Department } from "../../services/api";

// Seletor de escopo em colunas (estilo Finder): cada coluna e um nivel da estrutura
// organizacional. Clicar num item abre os filhos na coluna seguinte; "Aplicar"
// usa o ultimo item escolhido. A busca lista os caminhos completos que batem.
type Pick = { node: Department; path: string[]; depth: number };

const VISIBLE_COLUMNS = 3;

export default function OrgScopePicker({ anchorEl, onClose, tree, currentPath, levelLabels, onSelect, onClear }: {
  anchorEl: HTMLElement | null;
  onClose: () => void;
  tree: Department[];
  currentPath: string[];
  levelLabels: string[];
  onSelect: (node: Department, path: string[], depth: number) => void;
  onClear: () => void;
}) {
  const [trail, setTrail] = useState<Pick[]>([]);
  const [search, setSearch] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const open = Boolean(anchorEl);

  const roots = useMemo(() => tree.filter((node) => node.active), [tree]);

  // Ao abrir, reconstroi a trilha a partir do escopo atual.
  useEffect(() => {
    if (!open) return;
    setSearch("");
    const next: Pick[] = [];
    let level = roots;
    currentPath.forEach((name, depth) => {
      const node = level.find((item) => item.name === name);
      if (!node) return;
      next.push({ node, path: [...currentPath.slice(0, depth), name], depth });
      level = (node.children || []).filter((child) => child.active);
    });
    setTrail(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    scroller.current?.scrollTo({ left: scroller.current.scrollWidth, behavior: "smooth" });
  }, [trail.length]);

  const columns = useMemo(() => {
    const out: Department[][] = [roots];
    trail.forEach((pick) => {
      const children = (pick.node.children || []).filter((child) => child.active);
      if (children.length) out.push(children);
    });
    return out.slice(0, trail.length + 1);
  }, [roots, trail]);

  const results = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("pt-BR");
    if (!term) return [];
    const out: Pick[] = [];
    const walk = (nodes: Department[], parent: string[]) => nodes.filter((node) => node.active).forEach((node) => {
      const path = [...parent, node.name];
      if (node.name.toLocaleLowerCase("pt-BR").includes(term)) out.push({ node, path, depth: path.length - 1 });
      walk(node.children || [], path);
    });
    walk(roots, []);
    return out.slice(0, 60);
  }, [roots, search]);

  const choose = (depth: number, node: Department) => {
    const parent = trail.slice(0, depth);
    const path = [...parent.map((pick) => pick.node.name), node.name];
    setTrail([...parent, { node, path, depth }]);
  };

  const last = trail[trail.length - 1];
  const apply = (pick?: Pick) => {
    if (!pick) return;
    onSelect(pick.node, pick.path, pick.depth);
    onClose();
  };

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      PaperProps={{ sx: { mt: 0.5, width: { xs: "calc(100vw - 32px)", md: 960 }, maxWidth: "calc(100vw - 32px)", borderRadius: "14px", border: "1px solid #E1E8F0", boxShadow: "0 16px 44px rgba(24,39,75,.16)", overflow: "hidden" } }}
    >
      <Box sx={{ p: 1.5, borderBottom: "1px solid #EEF3F8" }}>
        <TextField
          fullWidth
          size="small"
          autoFocus
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Buscar área, diretoria, plataforma…"
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment>, sx: { borderRadius: "10px" } }}
        />
        {!search.trim() && (
          <Stack direction="row" alignItems="center" gap={0.25} flexWrap="wrap" mt={1} sx={{ minHeight: 24 }}>
            <Button size="small" onClick={() => setTrail([])} sx={{ textTransform: "none", minWidth: 0, px: 0.75, fontWeight: trail.length ? 500 : 700 }}>Toda a nuvem</Button>
            {trail.map((pick, i) => (
              <Stack key={pick.node.id} direction="row" alignItems="center">
                <ChevronRightOutlined sx={{ fontSize: 16, color: "text.disabled" }} />
                <Button size="small" onClick={() => setTrail(trail.slice(0, i + 1))} sx={{ textTransform: "none", minWidth: 0, px: 0.75, fontWeight: i === trail.length - 1 ? 700 : 500 }}>{pick.node.name}</Button>
              </Stack>
            ))}
          </Stack>
        )}
      </Box>

      {search.trim() ? (
        <Box sx={{ maxHeight: 360, overflowY: "auto", p: 0.75 }}>
          {results.length ? results.map((pick) => (
            <Box key={pick.node.id} onClick={() => apply(pick)} sx={{ px: 1.25, py: 0.9, borderRadius: "10px", cursor: "pointer", "&:hover": { bgcolor: "rgba(15, 98, 254, 0.06)" } }}>
              <Typography variant="body2" fontWeight={700}>{pick.node.name}</Typography>
              <Typography variant="caption" color="text.secondary" noWrap display="block">{levelLabels[pick.depth] ?? `Nível ${pick.depth + 1}`} · {pick.path.join(" › ")}</Typography>
            </Box>
          )) : <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>Nenhum nível encontrado.</Typography>}
        </Box>
      ) : (
        <Box ref={scroller} sx={{ display: "flex", overflowX: "auto", height: 380 }}>
          {columns.map((items, depth) => depth < columns.length - VISIBLE_COLUMNS ? null : (
            <Box key={depth} sx={{ flex: 1, minWidth: 260, borderRight: "1px solid #EEF3F8", display: "flex", flexDirection: "column", "&:last-of-type": { borderRight: 0 } }}>
              <Typography variant="overline" color="text.secondary" fontWeight={800} sx={{ px: 1.5, pt: 1, lineHeight: 1.6 }}>{levelLabels[depth] ?? `Nível ${depth + 1}`}</Typography>
              <Box sx={{ overflowY: "auto", flex: 1, px: 0.75, pb: 0.75 }}>
                {items.map((node) => {
                  const selected = trail[depth]?.node.id === node.id;
                  const hasChildren = (node.children || []).some((child) => child.active);
                  return (
                    <Stack
                      key={node.id}
                      direction="row"
                      alignItems="flex-start"
                      gap={0.5}
                      onClick={() => choose(depth, node)}
                      onDoubleClick={() => apply({ node, path: [...trail.slice(0, depth).map((pick) => pick.node.name), node.name], depth })}
                      sx={{ px: 1, py: 0.75, borderRadius: "8px", cursor: "pointer", bgcolor: selected ? "rgba(15, 98, 254, 0.1)" : "transparent", color: selected ? "#0F62FE" : "text.primary", "&:hover": { bgcolor: selected ? "rgba(15, 98, 254, 0.14)" : "rgba(24, 39, 75, 0.04)" } }}
                    >
                      <Typography variant="body2" fontWeight={selected ? 700 : 500} sx={{ flex: 1, minWidth: 0, lineHeight: 1.35, wordBreak: "break-word" }}>{node.name}</Typography>
                      {hasChildren && <ChevronRightOutlined sx={{ fontSize: 18, color: selected ? "#0F62FE" : "text.disabled" }} />}
                    </Stack>
                  );
                })}
              </Box>
            </Box>
          ))}
        </Box>
      )}

      <Stack direction="row" alignItems="center" gap={1} sx={{ px: 1.5, py: 1, borderTop: "1px solid #EEF3F8", bgcolor: "#FAFBFD" }}>
        <Typography variant="caption" color="text.secondary" title={last?.path.join(" › ")} sx={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {search.trim() ? "Clique num resultado para aplicar." : last ? `Escopo: ${last.path.join(" › ")}` : "Clique para navegar; duplo clique aplica direto."}
        </Typography>
        <Button size="small" onClick={() => { onClear(); onClose(); }} sx={{ textTransform: "none" }}>Toda a nuvem</Button>
        <Button size="small" variant="contained" disableElevation startIcon={<CheckOutlined />} disabled={!last} onClick={() => apply(last)} sx={{ textTransform: "none", borderRadius: "8px" }}>Aplicar</Button>
      </Stack>
    </Popover>
  );
}
