import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Divider, IconButton, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from "@mui/material";
import { AddOutlined, ChevronRightOutlined, DeleteOutlined, EditOutlined, WarningAmberOutlined } from "@mui/icons-material";
import DepartmentTreePicker, { flattenDepartments } from "../../components/DepartmentTreePicker";
import {
  type Department,
  type HierarchyLevelAlias,
  type HierarchyLevelLabel,
  type HierarchyRole,
  deleteHierarchyLevelAlias,
  deleteHierarchyRole,
  formatApiError,
  getHierarchyLevelAliases,
  getHierarchyLevelLabels,
  getHierarchyRoles,
  saveHierarchyLevelAlias,
  saveHierarchyRole,
} from "../../services/api";
import { invalidateHierarchyLabels } from "../../utils/hierarchyLabels";

const emptyRole = { id: undefined as number | undefined, label: "", label_plural: "" };

/**
 * Papéis (Provedor, Cliente, Diretoria...) e regras "neste ramo, o nível N é o
 * papel X". Os painéis leem os rótulos daqui, então um organograma novo só
 * precisa de regras — nenhum ajuste de dashboard.
 */
export default function LevelAliasesDialog({ open, onClose, departments }: {
  open: boolean;
  onClose: () => void;
  departments: Department[];
}) {
  const [roles, setRoles] = useState<HierarchyRole[]>([]);
  const [aliases, setAliases] = useState<HierarchyLevelAlias[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [roleForm, setRoleForm] = useState(emptyRole);
  const [ruleScope, setRuleScope] = useState<number | null>(null);
  const [ruleDepth, setRuleDepth] = useState(1);
  const [ruleRole, setRuleRole] = useState<number | "">("");
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [preview, setPreview] = useState<HierarchyLevelLabel[]>([]);

  const flat = useMemo(() => flattenDepartments(departments), [departments]);
  const depthOf = (id: number | null) => (id == null ? 0 : (flat.find((item) => item.id === id)?.path.split(" > ").length ?? 0));
  const maxDepth = useMemo(() => Math.max(6, ...flat.map((item) => item.path.split(" > ").length)), [flat]);
  const roleLabel = (id: number) => roles.find((role) => role.id === id)?.label ?? `#${id}`;

  const load = async () => {
    setLoading(true);
    try {
      const [nextRoles, nextAliases] = await Promise.all([getHierarchyRoles(), getHierarchyLevelAliases()]);
      setRoles(nextRoles);
      setAliases(nextAliases);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) { setError(""); load(); }
  }, [open]);

  const previewPath = useMemo(() => {
    const node = flat.find((item) => item.id === previewId);
    return node ? node.path.split(" > ") : [];
  }, [flat, previewId]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    getHierarchyLevelLabels(previewPath)
      // Sem nó, a cadeia inteira; com nó, até o nível logo abaixo dele.
      .then((data) => { if (active) setPreview(data.levels.slice(0, previewPath.length ? previewPath.length + 1 : maxDepth)); })
      .catch(() => { if (active) setPreview([]); });
    return () => { active = false; };
  }, [open, previewPath, aliases, roles, maxDepth]);

  const run = async (action: () => Promise<unknown>) => {
    setError("");
    try {
      await action();
      invalidateHierarchyLabels();
      await load();
    } catch (err) {
      setError(formatApiError(err));
    }
  };

  const submitRole = () => run(async () => {
    await saveHierarchyRole({ label: roleForm.label, label_plural: roleForm.label_plural || null }, roleForm.id);
    setRoleForm(emptyRole);
  });

  const submitRule = () => run(async () => {
    if (ruleRole === "") return;
    const existing = aliases.find((alias) => alias.scope_department_id === ruleScope && alias.depth === ruleDepth);
    await saveHierarchyLevelAlias({ scope_department_id: ruleScope, depth: ruleDepth, role_id: ruleRole }, existing?.id);
    setRuleRole("");
  });

  const minDepth = Math.max(1, depthOf(ruleScope));
  const depthOptions = Array.from({ length: maxDepth - minDepth + 1 }, (_, index) => minDepth + index);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Aliases de níveis</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Diga o que cada nível significa em cada ramo da estrutura. Os painéis usam esses nomes
          (por exemplo, &quot;Por Cliente&quot;) e mudam sozinhos quando as regras mudam.
        </Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        {loading && !roles.length ? <CircularProgress sx={{ display: "block", mx: "auto", my: 4 }} /> : (
          <Stack spacing={3}>
            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Pré-visualização</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Escolha um nó para ver como os níveis aparecem nos painéis naquele ramo.</Typography>
              <DepartmentTreePicker departments={departments} value={previewId} onChange={setPreviewId} label="Nó" placeholder="Toda a hierarquia" allowClear clearLabel="Toda a hierarquia" />
              <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.5} sx={{ mt: 1.5 }}>
                {preview.map((level, index) => (
                  <Stack key={level.depth} direction="row" alignItems="center" gap={0.5}>
                    {index > 0 && <ChevronRightOutlined fontSize="small" color="disabled" />}
                    <Tooltip title={level.source === "escopo" ? `Regra do ramo ${level.scope ?? ""}` : level.source === "global" ? "Regra global" : "Sem regra"}>
                      <Chip
                        size="small"
                        variant={level.source === "escopo" ? "filled" : "outlined"}
                        color={level.source === "padrao" ? "default" : "primary"}
                        icon={level.mixed ? <WarningAmberOutlined /> : undefined}
                        label={`${level.depth} · ${level.label}${previewPath[index] ? `: ${previewPath[index]}` : ""}`}
                      />
                    </Tooltip>
                  </Stack>
                ))}
              </Stack>
              {preview.some((level) => level.mixed) && (
                <Typography variant="caption" color="warning.main" sx={{ display: "block", mt: 1 }}>
                  Os filhos deste nó têm papéis diferentes; o painel usa a regra do ramo para nomear o próximo nível.
                </Typography>
              )}
            </Box>

            <Divider />

            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Regras por escopo</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Sem escopo, a regra vale para toda a hierarquia. Com escopo, vale só dentro daquele ramo e vence a regra global.
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Escopo</TableCell>
                    <TableCell width={80}>Nível</TableCell>
                    <TableCell>Papel</TableCell>
                    <TableCell width={48} />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {aliases.map((alias) => (
                    <TableRow key={alias.id}>
                      <TableCell>{alias.scope_path.length ? alias.scope_path.join(" › ") : <Typography variant="body2" color="text.secondary">Toda a hierarquia</Typography>}</TableCell>
                      <TableCell>{alias.depth}</TableCell>
                      <TableCell>{roleLabel(alias.role_id)}</TableCell>
                      <TableCell>
                        <IconButton size="small" aria-label="Remover regra" onClick={() => run(() => deleteHierarchyLevelAlias(alias.id))}><DeleteOutlined fontSize="small" /></IconButton>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!aliases.length && <TableRow><TableCell colSpan={4} align="center">Nenhuma regra. Os painéis mostram &quot;Nível N&quot;.</TableCell></TableRow>}
                </TableBody>
              </Table>
              <Stack direction={{ xs: "column", sm: "row" }} gap={1} alignItems={{ sm: "flex-start" }} sx={{ mt: 1.5 }}>
                <Box sx={{ flex: 2, minWidth: 0 }}>
                  <DepartmentTreePicker
                    departments={departments}
                    value={ruleScope}
                    onChange={(id) => { setRuleScope(id); setRuleDepth(Math.max(1, depthOf(id) + 1)); }}
                    label="Escopo"
                    placeholder="Toda a hierarquia"
                    allowClear
                    clearLabel="Toda a hierarquia"
                  />
                </Box>
                <TextField select size="small" label="Nível" value={ruleDepth} onChange={(event) => setRuleDepth(Number(event.target.value))} sx={{ width: { sm: 100 } }}>
                  {depthOptions.map((depth) => <MenuItem key={depth} value={depth}>{depth}</MenuItem>)}
                </TextField>
                <TextField select size="small" label="Papel" value={ruleRole} onChange={(event) => setRuleRole(Number(event.target.value))} sx={{ flex: 1, minWidth: 160 }}>
                  {roles.map((role) => <MenuItem key={role.id} value={role.id}>{role.label}</MenuItem>)}
                </TextField>
                <Button variant="outlined" startIcon={<AddOutlined />} disabled={ruleRole === ""} onClick={submitRule} sx={{ height: 40 }}>
                  {aliases.some((alias) => alias.scope_department_id === ruleScope && alias.depth === ruleDepth) ? "Substituir" : "Adicionar"}
                </Button>
              </Stack>
            </Box>

            <Divider />

            <Box>
              <Typography variant="subtitle1" fontWeight={700}>Papéis</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Catálogo de nomes que as regras podem usar. O plural aparece em títulos como &quot;Top 5 clientes&quot;.</Typography>
              <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mb: 1.5 }}>
                {roles.map((role) => (
                  <Chip
                    key={role.id}
                    label={role.label_plural ? `${role.label} / ${role.label_plural}` : role.label}
                    onClick={() => setRoleForm({ id: role.id, label: role.label, label_plural: role.label_plural ?? "" })}
                    onDelete={() => run(() => deleteHierarchyRole(role.id))}
                    deleteIcon={<Tooltip title="Excluir papel"><DeleteOutlined /></Tooltip>}
                    color={roleForm.id === role.id ? "primary" : "default"}
                    variant={roleForm.id === role.id ? "filled" : "outlined"}
                  />
                ))}
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} gap={1}>
                <TextField size="small" label="Nome" value={roleForm.label} onChange={(event) => setRoleForm({ ...roleForm, label: event.target.value })} sx={{ flex: 1 }} />
                <TextField size="small" label="Plural" value={roleForm.label_plural} onChange={(event) => setRoleForm({ ...roleForm, label_plural: event.target.value })} sx={{ flex: 1 }} />
                <Button variant="outlined" startIcon={roleForm.id ? <EditOutlined /> : <AddOutlined />} disabled={!roleForm.label.trim()} onClick={submitRole} sx={{ height: 40 }}>
                  {roleForm.id ? "Salvar" : "Adicionar"}
                </Button>
                {roleForm.id && <Button onClick={() => setRoleForm(emptyRole)} sx={{ height: 40 }}>Cancelar</Button>}
              </Stack>
            </Box>
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Fechar</Button>
      </DialogActions>
    </Dialog>
  );
}
