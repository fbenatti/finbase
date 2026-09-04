import { useMemo, useState, type ReactElement } from "react";
import {
  Box, Button, Typography, Menu, List, ListItemButton, ListItemText, Collapse, IconButton,
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
}: DepartmentTreePickerProps) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

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
    setAnchor(null);
  };

  const renderNode = (department: Department, depth = 0): ReactElement | null => {
    if (blockedIds.has(department.id)) return null;
    const children = (department.children || []).filter((child) => !blockedIds.has(child.id));
    const isExpanded = expanded.has(department.id);

    return (
      <Box key={department.id}>
        <ListItemButton
          selected={department.id === value}
          onClick={() => chooseDepartment(department)}
          sx={{ pl: 1 + depth * 2, py: 0.25 }}
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
            primaryTypographyProps={{ variant: "body2", fontWeight: depth < 3 ? 700 : 400 }}
          />
        </ListItemButton>
        <Collapse in={isExpanded} timeout="auto" unmountOnExit>
          {children.map((child) => renderNode(child, depth + 1))}
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
        PaperProps={{ sx: { width: 430, maxHeight: 520 } }}
      >
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", px: 2, pt: 1 }}>
          Selecione o nível na hierarquia. O caminho completo aparece no botão.
        </Typography>
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
          {departments.map((department) => renderNode(department))}
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
