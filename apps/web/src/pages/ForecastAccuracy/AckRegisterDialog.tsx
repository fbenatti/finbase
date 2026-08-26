import { useEffect, useMemo, useState, type ReactElement } from "react";
import {
  Box, Button, Collapse, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton,
  InputLabel, ListItemButton, ListItemText, MenuItem, Select, Stack, TextField, Typography,
} from "@mui/material";
import { ChevronRight, ExpandMore } from "@mui/icons-material";
import { Department, createForecastAck, getDepartments } from "../../services/api";

const ACK_CATEGORIES = ["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"];

export default function AckRegisterDialog({
  open, onClose, onSaved, defaultDepartment,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (monthKey: string, departmentName: string) => void;
  defaultDepartment: string;
}) {
  const [organization, setOrganization] = useState<Department[]>([]);
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [dept, setDept] = useState(defaultDepartment);
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [category, setCategory] = useState(ACK_CATEGORIES[0]);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setDept(defaultDepartment);
    setDate(new Date().toISOString().slice(0, 10));
    setCategory(ACK_CATEGORIES[0]);
    setReason("");
    setError("");
    if (organization.length === 0) {
      getDepartments().then(setOrganization).catch(() => setOrganization([]));
    }
    // Expande o caminho do departamento padrão no organograma.
    if (defaultDepartment && organization.length > 0) {
      const parts = defaultDepartment.split(" > ").map(p => p.trim());
      const expandPath = (nodes: Department[], depth: number) => {
        if (depth >= parts.length) return;
        const match = nodes.find(n => n.name === parts[depth]);
        if (!match) return;
        setExpandedNodes(prev => new Set(prev).add(match.id));
        expandPath(match.children || [], depth + 1);
      };
      expandPath(organization, 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaultDepartment]);

  const byId = useMemo(() => {
    const map = new Map<number, Department>();
    const collect = (nodes: Department[]) => nodes.forEach(n => { map.set(n.id, n); collect(n.children || []); });
    collect(organization);
    return map;
  }, [organization]);

  const buildPath = (node: Department): string => {
    const parts: string[] = [];
    let current: Department | undefined = node;
    const seen = new Set<number>();
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      parts.unshift(current.name);
      current = current.parent_id != null ? byId.get(current.parent_id) : undefined;
    }
    return parts.join(" > ");
  };

  const toggleNode = (id: number) => {
    setExpandedNodes(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const renderNode = (node: Department, depth = 0): ReactElement => {
    const children = (node.children || []).filter(child => child.active);
    const expanded = expandedNodes.has(node.id);
    return (
      <Box key={node.id}>
        <ListItemButton
          onClick={() => setDept(buildPath(node))}
          sx={{ pl: 1 + depth * 2, py: 0.25, bgcolor: dept === buildPath(node) ? "action.selected" : undefined }}
        >
          <Box sx={{ width: 28, display: "flex", justifyContent: "center" }}>
            {children.length > 0 && (
              <IconButton size="small" onClick={event => { event.stopPropagation(); toggleNode(node.id); }}>
                {expanded ? <ExpandMore fontSize="small" /> : <ChevronRight fontSize="small" />}
              </IconButton>
            )}
          </Box>
          <ListItemText primary={node.name} primaryTypographyProps={{ variant: "body2", fontWeight: depth < 3 ? 700 : 400 }} />
        </ListItemButton>
        <Collapse in={expanded} timeout="auto" unmountOnExit>
          {children.map(child => renderNode(child, depth + 1))}
        </Collapse>
      </Box>
    );
  };

  const save = async () => {
    if (!dept.trim() || !reason.trim()) return;
    setSaving(true);
    setError("");
    try {
      await createForecastAck({ department: dept.trim(), category, reason: reason.trim(), registered_at: date });
      onSaved(date.slice(0, 7).replace("-", ""), dept.split(" > ").pop() || "");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Cadastrar item de desvio</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Relacione o item a um departamento do organograma e informe a data do registro. A data define o mês
          (mesma regra do mês realizado — YYYYMM) e pode ser futura.
        </Typography>
        {error && <Typography variant="body2" color="error" sx={{ mb: 1 }}>{error}</Typography>}
        <Typography variant="caption" color="text.secondary" fontWeight={700}>Departamento</Typography>
        <Box sx={{ maxHeight: 220, overflowY: "auto", border: "1px solid", borderColor: "divider", borderRadius: 1, mb: 2, mt: 0.5 }}>
          {organization.filter(d => d.active).map(node => renderNode(node))}
        </Box>
        <TextField
          fullWidth label="Departamento selecionado" value={dept} size="small" sx={{ mb: 2 }}
          InputProps={{ readOnly: true }}
        />
        <TextField
          fullWidth label="Data do registro" type="date" sx={{ mb: 2 }}
          value={date}
          onChange={e => setDate(e.target.value)}
          InputLabelProps={{ shrink: true }}
          helperText="O mês do item é derivado desta data (YYYYMM)."
        />
        <FormControl fullWidth sx={{ mb: 2 }}>
          <InputLabel>Categoria</InputLabel>
          <Select label="Categoria" value={category} onChange={e => setCategory(e.target.value)}>
            {ACK_CATEGORIES.map(cat => <MenuItem key={cat} value={cat}>{cat}</MenuItem>)}
          </Select>
        </FormControl>
        <TextField
          fullWidth multiline minRows={3} label="Motivo do desvio"
          value={reason} onChange={e => setReason(e.target.value)}
          placeholder="Ex.: projeto novo contratado após o fechamento do forecast..."
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={saving || !dept.trim() || !reason.trim()} onClick={() => void save()}>
          {saving ? "Salvando..." : "Cadastrar"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
