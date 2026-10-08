import { useMemo, useState, type ReactElement } from "react";
import {
  Box, Button, Checkbox, Typography, Menu, List, ListItemButton, ListItemText, Collapse, IconButton, TextField,
} from "@mui/material";
import { ChevronRight, ExpandMore } from "@mui/icons-material";
import type { Department } from "../services/api";
import { ORG } from "../constants/orgLabels";

export function flattenDepartments(
  nodes: Department[],
  parents: string[] = [],
): Array<Department & { path: string }> {
  return nodes.flatMap((node) => {
    const path = [...parents, node.name];
    return [
      { ...node, path: path.join(" > ") },
      ...flattenDepartments(node.children || [], path),
    ];
  });
}

function ancestorIdsFromFlat(
  flat: Array<Department & { path: string }>,
  targetId: number | null,
): Set<number> {
  if (targetId == null) return new Set();
  const byId = new Map(flat.map((department) => [department.id, department]));
  const ids = new Set<number>();
  let current = byId.get(targetId);
  while (current) {
    ids.add(current.id);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return ids;
}

function ancestorIds(nodes: Department[], targetId: number | null): Set<number> {
  return ancestorIdsFromFlat(flattenDepartments(nodes), targetId);
}

export function descendantIds(node: Department): Set<number> {
  return new Set([node.id, ...(node.children || []).flatMap((child) => [...descendantIds(child)])]);
}

export { ancestorIds };

interface DepartmentTreePickerProps {
  departments: Department[];
  value: number | null;
  onChange: (departmentId: number | null) => void;
  label?: string;
  helperText?: string;
  placeholder?: string;
  allowClear?: boolean;
  clearLabel?: string;
  blockedIds?: Set<number>;
  /** Texto de orientação exibido no topo do menu. */
  menuHint?: string;
  /** Ícone opcional à esquerda do botão (ex.: Business no filtro global). */
  startIcon?: ReactElement;
}

export default function DepartmentTreePicker({
  departments,
  value,
  onChange,
  label = ORG.node,
  helperText,
  placeholder = ORG.selectNode,
  allowClear = false,
  clearLabel = "Nenhum",
  blockedIds = new Set<number>(),
  menuHint = "Selecione o nível na hierarquia. O caminho completo aparece no botão.",
  startIcon,
}: DepartmentTreePickerProps) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");

  const flatDepartments = useMemo(() => flattenDepartments(departments), [departments]);

  const selectedPath = useMemo(() => {
    if (value == null) return placeholder;
    return flatDepartments.find((d) => d.id === value)?.path || placeholder;
  }, [flatDepartments, placeholder, value]);

  const openMenu = (event: React.MouseEvent<HTMLElement>) => {
    setExpanded(ancestorIdsFromFlat(flatDepartments, value));
    setAnchor(event.currentTarget);
  };

  const toggleNode = (id: number) => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const chooseDepartment = (department: Department | null) => {
    onChange(department?.id ?? null);
    setSearch("");
    setAnchor(null);
  };

  const matchedDepartments = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("pt-BR");
    if (!term) return [];
    return flatDepartments.filter((department) =>
      `${department.name} ${department.path}`.toLocaleLowerCase("pt-BR").includes(term),
    ).slice(0, 100);
  }, [flatDepartments, search]);

  const filteredDepartments = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("pt-BR");
    if (!term) return departments;
    const matchingIds = new Set(matchedDepartments.map((department) => department.id));
    const includeSubtree = (node: Department): Department => ({
      ...node,
      children: (node.children || []).map(includeSubtree),
    });
    const filter = (nodes: Department[]): Department[] => nodes.flatMap((node) => {
      if (matchingIds.has(node.id)) return [includeSubtree(node)];
      const children = filter(node.children || []);
      return children.length ? [{ ...node, children }] : [];
    });
    return filter(departments);
  }, [departments, matchedDepartments, search]);

  const renderNode = (department: Department, depth = 0, forceExpanded = false): ReactElement | null => {
    if (blockedIds.has(department.id)) return null;
    const children = (department.children || []).filter((child) => !blockedIds.has(child.id));
    const isExpanded = forceExpanded || expanded.has(department.id);

    return (
      <Box key={department.id}>
        <ListItemButton
          selected={department.id === value}
          onClick={() => chooseDepartment(department)}
          sx={{ pl: 1 + depth * 2, py: 0.125 }}
        >
          <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>
            {children.length > 0 && (
              <IconButton
                size="small"
                tabIndex={-1}
                onClick={(event) => {
                  event.stopPropagation();
                  toggleNode(department.id);
                }}
              >
                {isExpanded ? <ExpandMore fontSize="small" /> : <ChevronRight fontSize="small" />}
              </IconButton>
            )}
          </Box>
          <ListItemText
            primary={department.name}
            sx={{ my: 0 }}
            primaryTypographyProps={{
              variant: "body2",
              fontWeight: depth < 3 ? 700 : 400,
              sx: { lineHeight: 1.25 },
            }}
          />
        </ListItemButton>
        <Collapse in={isExpanded} timeout="auto" unmountOnExit>
          {children.map((child) => renderNode(child, depth + 1, forceExpanded))}
        </Collapse>
      </Box>
    );
  };

  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Button
        variant="outlined"
        fullWidth
        startIcon={startIcon}
        onClick={openMenu}
        sx={{ mt: 0.5, justifyContent: "flex-start", textTransform: "none", textAlign: "left" }}
      >
        {selectedPath}
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        disablePortal
        disableAutoFocusItem
        disableRestoreFocus
        PaperProps={{ sx: { width: { xs: "calc(100vw - 32px)", sm: 560 }, maxWidth: "calc(100vw - 32px)", maxHeight: 520 } }}
      >
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2, pt: 1 }}>
          {menuHint}
        </Typography>
        <Box sx={{ px: 1.5, pt: 1 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Buscar diretoria ou projeto"
          />
        </Box>
        <List dense disablePadding>
          {allowClear && (
            <ListItemButton
              selected={value === null}
              onClick={() => chooseDepartment(null)}
              sx={{ pl: 1, py: 0.25 }}
            >
              <ListItemText
                primary={clearLabel}
                primaryTypographyProps={{ variant: "body2", fontStyle: "italic" }}
              />
            </ListItemButton>
          )}
          {search.trim()
            ? filteredDepartments.map((department) => renderNode(department, 0, true))
            : departments.map((department) => renderNode(department))}
          {search.trim() && matchedDepartments.length === 0 && <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1.5 }}>Nenhum projeto ou diretoria encontrado.</Typography>}
        </List>
      </Menu>
      {helperText && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
          {helperText}
        </Typography>
      )}
    </Box>
  );
}

/** Hierarchical organogram picker with persistent menu and multiple selection. */
export function DepartmentTreeMultiPicker({
  departments,
  values,
  onChange,
  label = ORG.node,
  placeholder = ORG.selectNode,
}: {
  departments: Department[];
  values: number[];
  onChange: (departmentIds: number[]) => void;
  label?: string;
  placeholder?: string;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const flat = useMemo(() => flattenDepartments(departments), [departments]);
  const selected = useMemo(() => new Set(values), [values]);
  const selectedPaths = useMemo(() => flat.filter((item) => selected.has(item.id)), [flat, selected]);
  const expandedBySelection = () => new Set(selectedPaths.flatMap((item) => [...ancestorIdsFromFlat(flat, item.id)]));
  const toggleExpanded = (id: number) => setExpanded((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleSelection = (department: Department) => {
    const item = flat.find((entry) => entry.id === department.id);
    if (!item) return;
    if (selected.has(department.id)) {
      onChange(values.filter((id) => id !== department.id));
      return;
    }
    // A selection represents one hierarchy level: allow several siblings,
    // but changing level replaces the previous selection (including parents).
    const depth = item.path.split(" > ").length;
    const selectedAtSameDepth = selectedPaths.filter(
      (entry) => entry.path.split(" > ").length === depth,
    );
    const next = selectedAtSameDepth.map((entry) => entry.id);
    onChange([...next, department.id]);
  };
  const term = search.trim().toLocaleLowerCase("pt-BR");
  const matches = term ? flat.filter((item) => item.active && `${item.name} ${item.path}`.toLocaleLowerCase("pt-BR").includes(term)).slice(0, 100) : [];
  const renderNode = (department: Department, depth = 0): ReactElement | null => {
    if (!department.active) return null;
    const children = (department.children || []).filter((child) => child.active);
    const isExpanded = expanded.has(department.id);
    return <Box key={department.id}>
      <ListItemButton selected={selected.has(department.id)} onClick={() => toggleSelection(department)} sx={{ pl: 1 + depth * 2, py: 0.25 }}>
        <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>
          {children.length > 0 && <IconButton size="small" tabIndex={-1} onClick={(event) => { event.stopPropagation(); toggleExpanded(department.id); }}>{isExpanded ? <ExpandMore fontSize="small" /> : <ChevronRight fontSize="small" />}</IconButton>}
        </Box>
        <Checkbox size="small" checked={selected.has(department.id)} tabIndex={-1} sx={{ p: .5, mr: .5 }} />
        <ListItemText primary={department.name} primaryTypographyProps={{ variant: "body2", fontWeight: depth < 3 ? 700 : 400 }} />
      </ListItemButton>
      <Collapse in={isExpanded} timeout="auto" unmountOnExit>{children.map((child) => renderNode(child, depth + 1))}</Collapse>
    </Box>;
  };
  const display = selectedPaths.length ? `${selectedPaths.length} nível(is) selecionado(s)` : placeholder;
  return <Box>
    <Typography variant="caption" color="text.secondary">{label}</Typography>
    <Button variant="outlined" fullWidth onClick={(event) => { setExpanded(expandedBySelection()); setAnchor(event.currentTarget); }} sx={{ mt: .5, minHeight: 40, justifyContent: "flex-start", textTransform: "none", textAlign: "left", borderRadius: "12px", color: "#111b4e", borderColor: "#dce2ee" }}>{display}</Button>
    <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => { setAnchor(null); setSearch(""); }} disablePortal PaperProps={{ sx: { width: 460, maxHeight: 560, borderRadius: "14px", border: "1px solid #e2e7f0", boxShadow: "0 16px 44px rgba(24,39,75,.16)" } }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2, pt: 1 }}>Navegue pela hierarquia e marque um ou mais níveis. Marcar um nível pai inclui seus descendentes.</Typography>
      <Box sx={{ px: 1.5, pt: 1 }}><TextField autoFocus fullWidth size="small" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar por nome ou caminho completo" /></Box>
      <List dense disablePadding sx={{ maxHeight: 390, overflow: "auto", mt: .5 }}>
        {term ? matches.map((item) => <ListItemButton key={item.id} selected={selected.has(item.id)} onClick={() => toggleSelection(item)} sx={{ py: .5 }}><Checkbox size="small" checked={selected.has(item.id)} tabIndex={-1} sx={{ p: .5, mr: .75 }} /><ListItemText primary={item.name} secondary={item.path} primaryTypographyProps={{ variant: "body2", fontWeight: 600 }} secondaryTypographyProps={{ variant: "caption", noWrap: true }} /></ListItemButton>) : departments.filter((item) => item.active).map((item) => renderNode(item))}
        {term && matches.length === 0 && <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1.5 }}>Nenhum nível encontrado.</Typography>}
      </List>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", px: 1.5, py: 1, borderTop: "1px solid #edf0f6" }}><Button size="small" color="inherit" onClick={() => onChange([])}>Limpar seleção</Button><Button size="small" variant="contained" onClick={() => { setAnchor(null); setSearch(""); }} sx={{ bgcolor: "#6416dc", "&:hover": { bgcolor: "#5311b8" } }}>Concluir · {values.length}</Button></Box>
    </Menu>
  </Box>;
}
