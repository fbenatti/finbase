import React, { useEffect, useMemo, useState } from "react";
import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText, moduleDisplayName } from "../../utils/dataPresentation";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Alert, Box, Button, Chip, FormControl, FormControlLabel, IconButton, InputLabel, MenuItem, Paper, Select,
  Stack, Switch, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, ToggleButton,
  ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import { AddOutlined as AddIcon, RefreshOutlined as RefreshIcon, LockResetOutlined as LockResetIcon } from "@mui/icons-material";
import {
  AdminGroup, AdminRole, AdminUser, AdminGrant, EffectivePermissions,
  listRoles, createRole, deleteRole, getRole, setRolePermissions, addInheritance, removeInheritance, getRoleTree,
  listGroups, createGroup, deleteGroup, addGroupMember, removeGroupMember, addGroupRole, removeGroupRole,
  listUsers, createUser, updateUser, resetUserPassword, setUserRoles, getEffectivePermissions, listGrants, createGrant, deleteGrant,
} from "../../services/adminApi";
import { ACCESS_AREAS, ACTION_LABELS, ALL_SCREENS, CATALOG_MODULES, EDIT_ACTIONS } from "./accessCatalog";
import { RowAction } from "../../components/RowAction";
import { AccessControlTab } from "./AccessControlTab";

type PermMap = Record<string, Set<string>>;
type Level = "none" | "view" | "edit";

const TABS = [
  { value: "roles", label: "1 · Papéis", hint: "Defina, tela a tela, o que cada papel pode ver ou editar." },
  { value: "groups", label: "2 · Grupos de acesso", hint: "Reúna usuários e vincule papéis a eles em um só lugar." },
  { value: "users", label: "3 · Usuários", hint: "Atribua papéis, ative ou desative acessos e confira o acesso final." },
  { value: "acl", label: "4 · Permissões avançadas", hint: "Defina ACLs por papel, escopos de estrutura e orçamento, e simule o acesso de um usuário." },
];

function toPermMap(items: Array<{ module: string; action: string }> = []): PermMap {
  const map: PermMap = {};
  items.forEach((p) => { (map[p.module] = map[p.module] || new Set()).add(p.action); });
  return map;
}

function useErrorAlert() {
  const [error, setError] = useState<string | null>(null);
  const show = (e: any) => {
    const d = e?.response?.data?.detail;
    setError(typeof d === "string" ? d : d ? JSON.stringify(d) : String(e));
  };
  return [error, setError, show] as const;
}

/** Lista à esquerda, detalhe à direita. Em telas pequenas, empilha. */
function MasterDetail({ list, detail }: { list: React.ReactNode; detail: React.ReactNode }) {
  return (
    <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "minmax(300px, 4fr) 7fr" }, alignItems: "start" }}>
      <Paper variant="outlined" sx={{ overflow: "hidden" }}>{list}</Paper>
      <Paper variant="outlined" sx={{ p: 2.5, minHeight: 320 }}>{detail}</Paper>
    </Box>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Box sx={{ py: 2, borderTop: 1, borderColor: "divider" }}>
      <Typography variant="subtitle2" fontWeight={700}>{title}</Typography>
      {hint && <Typography variant="caption" color="text.secondary" component="p">{hint}</Typography>}
      <Box sx={{ mt: 1.5 }}>{children}</Box>
    </Box>
  );
}

function EmptyDetail({ text }: { text: string }) {
  return <Typography variant="body2" color="text.secondary">{text}</Typography>;
}

function ListHeader({ children }: { children: React.ReactNode }) {
  return (
    <Box sx={{ p: 2, borderBottom: 1, borderColor: "divider", bgcolor: "background.paper" }}>
      {children}
    </Box>
  );
}

// ── Perfis ───────────────────────────────────────────────────────────────────
function RolesTab() {
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<PermMap>({});
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [inherits, setInherits] = useState<Array<{ id: number; name: string }>>([]);
  const [tree, setTree] = useState<any>(null);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError, show] = useErrorAlert();

  const selected = roles.find((r) => r.id === selectedId) ?? null;

  const load = () => listRoles().then(setRoles).catch(show);
  useEffect(() => { load(); }, []);

  const openRole = async (id: number) => {
    try {
      const full = await getRole(id);
      const sc: Record<string, string> = {};
      full.permissions?.forEach((p) => { if (p.scope) sc[p.module] = sc[p.module] || p.scope; });
      setSelectedId(id);
      setDraft(toPermMap(full.permissions));
      setScopes(sc);
      setInherits(full.inherits_from ?? []);
      setTree(await getRoleTree(id));
      setDirty(false); setMsg(null); setError(null);
    } catch (e) { show(e); }
  };

  const updateModule = (module: string, mutate: (set: Set<string>) => void) => {
    setDraft((prev) => {
      const set = new Set(prev[module] ?? []);
      mutate(set);
      const next = { ...prev };
      if (set.size === 0) delete next[module]; else next[module] = set;
      return next;
    });
    setDirty(true);
  };

  const setLevel = (module: string, level: Level) => updateModule(module, (set) => {
    set.clear();
    if (level !== "none") set.add("read");
    if (level === "edit") EDIT_ACTIONS.forEach((a) => set.add(a));
  });

  const levelOf = (module: string): Level => {
    const set = draft[module];
    if (!set?.has("read")) return "none";
    return EDIT_ACTIONS.some((a) => set.has(a)) ? "edit" : "view";
  };

  const save = async () => {
    if (selectedId === null) return;
    const items = Object.entries(draft).flatMap(([module, set]) =>
      Array.from(set).map((action) => ({ module, action, scope: scopes[module] || null })));
    try {
      await setRolePermissions(selectedId, items);
      setDirty(false); setMsg("Papel salvo");
      load();
    } catch (e) { show(e); }
  };

  const remove = async () => {
    if (!selected) return;
    try { await deleteRole(selected.id); setSelectedId(null); load(); }
    catch (e) { show(e); }
  };

  const inherit = async (parentId: number) => {
    if (selectedId === null) return;
    try { await addInheritance(selectedId, parentId); await openRole(selectedId); setMsg("Herança de papel adicionada"); }
    catch (e) { show(e); }
  };
  const uninherit = async (parentId: number) => {
    if (selectedId === null) return;
    try { await removeInheritance(selectedId, parentId); await openRole(selectedId); }
    catch (e) { show(e); }
  };

  const TreeNode = ({ node, depth }: { node: any; depth: number }) => (
    <Box sx={{ ml: depth * 2 }}>
      <Typography variant="body2">↳ {node.name}{node.cycle ? " (ciclo!)" : ""}</Typography>
      {node.parents?.map((p: any) => <TreeNode key={p.id} node={p} depth={depth + 1} />)}
    </Box>
  );

  const extraModules = Object.keys(draft).filter((m) => !CATALOG_MODULES.has(m));

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{dataDisplayText(error)}</Alert>}
      {msg && !dirty && <Alert severity="success" onClose={() => setMsg(null)}>{dataDisplayText(msg)}</Alert>}
      <MasterDetail
        list={
          <>
            <ListHeader>
              <Stack direction="row" spacing={1}>
                <TextField fullWidth placeholder="Nome do novo papel" value={name} onChange={(e) => setName(e.target.value)} />
                <Button variant="contained" startIcon={<AddIcon />} disabled={!name.trim()} onClick={async () => {
                  try { await createRole({ name: name.trim() }); setName(""); load(); } catch (e) { show(e); }
                }}>Incluir</Button>
                <Tooltip title="Recarregar"><IconButton onClick={load}><RefreshIcon /></IconButton></Tooltip>
              </Stack>
            </ListHeader>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Papel</TableCell>
                  <TableCell>Tipo</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {roles.map((r) => (
                  <TableRow key={r.id} hover selected={r.id === selectedId} onClick={() => openRole(r.id)} sx={{ cursor: "pointer" }}>
                    <TableCell>{r.name}</TableCell>
                    <TableCell>
                      <Chip size="small" variant="outlined" label={r.is_system ? "Sistema" : "Personalizado"} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        }
        detail={
          !selected ? <EmptyDetail text="Selecione um papel para ver e editar o que ele libera." /> : (
            <>
              <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={2}>
                <Box>
                  <Typography variant="h6">{selected.name}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {selected.is_system ? "Papel de sistema. Pode ser ajustado, mas não removido." : "Papel personalizado."}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                  {!selected.is_system && (
                    <Tooltip title="Remover papel"><RowAction kind="delete" onClick={remove} /></Tooltip>
                  )}
                  <Button variant="contained" disabled={!dirty} onClick={save}>Salvar papel</Button>
                </Stack>
              </Stack>

              <Section title="Telas liberadas" hint="Defina Sem acesso, Leitura ou Edição para cada tela. Telas que dividem um módulo mudam juntas.">
                <Stack spacing={2}>
                  {ACCESS_AREAS.map((area) => (
                    <Box key={area.area}>
                      <Typography variant="overline" color="text.secondary">{area.area}</Typography>
                      <Stack spacing={1}>
                        {area.screens.map((screen) => {
                          const shared = ALL_SCREENS.filter((s) => s.module === screen.module).length;
                          return (
                            <Stack key={screen.route} direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" spacing={1}
                              sx={{ py: 0.75, px: 1.5, borderRadius: 1, bgcolor: "#FAFBFD", border: 1, borderColor: "divider" }}>
                              <Box>
                                <Typography variant="body2" fontWeight={600}>{screen.label}</Typography>
                                <Typography variant="caption" color="text.secondary">
                                  {screen.route}{shared > 1 ? ` · divide o módulo com ${shared - 1} tela(s)` : ""}
                                </Typography>
                              </Box>
                              <ToggleButtonGroup size="small" exclusive value={levelOf(screen.module)}
                                onChange={(_, v: Level | null) => v && setLevel(screen.module, v)}>
                                <ToggleButton value="none">Sem acesso</ToggleButton>
                                <ToggleButton value="view">Ver</ToggleButton>
                                <ToggleButton value="edit">Edição</ToggleButton>
                              </ToggleButtonGroup>
                            </Stack>
                          );
                        })}
                      </Stack>
                    </Box>
                  ))}
                </Stack>
                {extraModules.length > 0 && (
                  <Box sx={{ mt: 2 }}>
                    <Typography variant="caption" color="text.secondary" component="p">
                      Módulos sem tela no catálogo (mantidos como estão):
                    </Typography>
                    <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap">
                      {extraModules.map((m) => (
                        <Chip key={m} size="small" label={moduleDisplayName(m)} onDelete={() => setLevel(m, "none")} />
                      ))}
                    </Stack>
                  </Box>
                )}
              </Section>

              <Section title="Herda acessos de" hint="Os acessos dos papéis herdados valem junto com os deste papel.">
                <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1.5 }}>
                  {inherits.map((p) => <Chip key={p.id} label={p.name} onDelete={() => uninherit(p.id)} />)}
                  {inherits.length === 0 && <Typography variant="body2" color="text.secondary">Nenhum papel herdado.</Typography>}
                </Stack>
                <FormControl size="small" sx={{ minWidth: 240 }}>
                  <InputLabel>Adicionar papel herdado</InputLabel>
                  <Select label="Adicionar papel herdado" value="" onChange={(e) => inherit(Number(e.target.value))}>
                    {roles.filter((r) => r.id !== selected.id && !inherits.some((p) => p.id === r.id))
                      .map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                  </Select>
                </FormControl>
                {tree && <Box sx={{ mt: 2 }}><TreeNode node={tree} depth={0} /></Box>}
              </Section>
            </>
          )
        }
      />
    </Stack>
  );
}

// ── Grupos ───────────────────────────────────────────────────────────────────
function GroupsTab() {
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [error, setError, show] = useErrorAlert();

  const selected = groups.find((g) => g.id === selectedId) ?? null;

  const load = () => {
    listGroups().then(setGroups).catch(show);
    listRoles().then(setRoles).catch(show);
    listUsers().then(setUsers).catch(show);
  };
  useEffect(() => { load(); }, []);

  const call = async (fn: () => Promise<unknown>) => {
    try { await fn(); setError(null); load(); } catch (e) { show(e); }
  };

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{dataDisplayText(error)}</Alert>}
      <MasterDetail
        list={
          <>
            <ListHeader>
              <Stack direction="row" spacing={1}>
                <TextField fullWidth placeholder="Nome do novo grupo de acesso" value={name} onChange={(e) => setName(e.target.value)} />
                <Button variant="contained" startIcon={<AddIcon />} disabled={!name.trim()} onClick={() => call(async () => {
                  await createGroup({ name: name.trim() }); setName("");
                })}>Incluir</Button>
                <Tooltip title="Recarregar"><IconButton onClick={load}><RefreshIcon /></IconButton></Tooltip>
              </Stack>
            </ListHeader>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Nome do grupo</TableCell>
                  <TableCell align="right">Usuários</TableCell>
                  <TableCell align="right">Papéis</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {groups.map((g) => (
                  <TableRow key={g.id} hover selected={g.id === selectedId} onClick={() => setSelectedId(g.id)} sx={{ cursor: "pointer" }}>
                    <TableCell>{g.name}</TableCell>
                    <TableCell align="right">{g.members.length}</TableCell>
                    <TableCell align="right">{g.roles.length}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        }
        detail={
          !selected ? <EmptyDetail text="Selecione um grupo para gerenciar usuários e papéis." /> : (
            <>
              <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
                <Typography variant="h6">{selected.name}</Typography>
                <Tooltip title="Remover grupo">
                  <RowAction kind="delete" onClick={() => call(async () => { await deleteGroup(selected.id); setSelectedId(null); })} />
                </Tooltip>
              </Stack>

              <Section title="Usuários" hint="Quem entra no grupo herda os papéis dele.">
                <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1.5 }}>
                  {selected.members.map((m) => (
                    <Chip key={m.id} label={m.username} onDelete={() => call(() => removeGroupMember(selected.id, m.id))} />
                  ))}
                  {selected.members.length === 0 && <Typography variant="body2" color="text.secondary">Nenhum usuário.</Typography>}
                </Stack>
                <FormControl size="small" sx={{ minWidth: 240 }}>
                  <InputLabel>Adicionar usuário</InputLabel>
                  <Select label="Adicionar usuário" value="" onChange={(e) => call(() => addGroupMember(selected.id, Number(e.target.value)))}>
                    {users.filter((u) => !selected.members.some((m) => m.id === u.id))
                      .map((u) => <MenuItem key={u.id} value={u.id}>{u.username}</MenuItem>)}
                  </Select>
                </FormControl>
              </Section>

              <Section title="Papéis do grupo">
                <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1.5 }}>
                  {selected.roles.map((r) => (
                    <Chip key={r.id} label={r.name} onDelete={() => call(() => removeGroupRole(selected.id, r.id))} />
                  ))}
                  {selected.roles.length === 0 && <Typography variant="body2" color="text.secondary">Nenhum papel.</Typography>}
                </Stack>
                <FormControl size="small" sx={{ minWidth: 240 }}>
                  <InputLabel>Adicionar papel</InputLabel>
                  <Select label="Adicionar papel" value="" onChange={(e) => call(() => addGroupRole(selected.id, Number(e.target.value)))}>
                    {roles.filter((r) => !selected.roles.some((gr) => gr.id === r.id))
                      .map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                  </Select>
                </FormControl>
              </Section>
            </>
          )
        }
      />
    </Stack>
  );
}

// ── Usuários ──────────────────────────────────────────────────────────────────
function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [grants, setGrants] = useState<AdminGrant[]>([]);
  const [eff, setEff] = useState<EffectivePermissions | null>(null);
  const [query, setQuery] = useState("");
  const [newUser, setNewUser] = useState("");
  const [tempPassword, setTempPassword] = useState<{ username: string; password: string } | null>(null);
  const [resetUser, setResetUser] = useState<AdminUser | null>(null);
  const [newPass, setNewPass] = useState("");
  const [resetError, setResetError] = useState<string | null>(null);
  const [exceptionRoute, setExceptionRoute] = useState("");
  const [exceptionLevel, setExceptionLevel] = useState<"view" | "edit">("view");
  const [exceptionReason, setExceptionReason] = useState("");
  const [error, setError, show] = useErrorAlert();

  const selected = users.find((u) => u.id === selectedId) ?? null;

  const loadUsers = () => listUsers().then(setUsers);
  const loadDetail = async (id: number) => {
    try {
      const [g, e] = await Promise.all([listGrants(id), getEffectivePermissions(id)]);
      setGrants(g); setEff(e); setError(null);
    } catch (e) { show(e); }
  };

  useEffect(() => {
    loadUsers().catch(show);
    listRoles().then(setRoles).catch(show);
  }, []);

  const openUser = (id: number) => {
    setSelectedId(id); setTempPassword(null); setExceptionReason("");
    loadDetail(id);
  };

  /** Após qualquer mudança: recarrega a lista e o acesso da pessoa selecionada. */
  const change = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setError(null);
      await loadUsers();
      if (selectedId !== null) await loadDetail(selectedId);
    } catch (e) { show(e); }
  };

  const filtered = users.filter((u) =>
    `${u.username} ${u.full_name ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));

  const readableModules = useMemo(
    () => new Set((eff?.permissions ?? []).filter((p) => p.action === "read").map((p) => p.module)),
    [eff],
  );
  const editableModules = useMemo(
    () => new Set((eff?.permissions ?? []).filter((p) => EDIT_ACTIONS.includes(p.action as any)).map((p) => p.module)),
    [eff],
  );

  const addException = () => {
    const screen = ALL_SCREENS.find((s) => s.route === exceptionRoute);
    if (selectedId === null || !screen) return;
    const actions = exceptionLevel === "view" ? ["read"] : ["read", ...EDIT_ACTIONS];
    change(async () => {
      await Promise.all(actions.map((action) =>
        createGrant(selectedId, { module: screen.module, action, reason: exceptionReason.trim() })));
      setExceptionReason(""); setExceptionRoute("");
    });
  };

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{dataDisplayText(error)}</Alert>}
      {tempPassword && (
        <Alert severity="info" onClose={() => setTempPassword(null)}>
          Senha temporária de <b>{tempPassword.username}</b>: <b>{tempPassword.password}</b>
        </Alert>
      )}
      <MasterDetail
        list={
          <>
            <ListHeader>
              <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
                <TextField fullWidth placeholder="Nome de usuário (login)" value={newUser} onChange={(e) => setNewUser(e.target.value)} />
                <Button variant="contained" startIcon={<AddIcon />} disabled={!newUser.trim()} onClick={() => change(async () => {
                  const created = await createUser({ username: newUser.trim() });
                  setNewUser("");
                  if (created.temporary_password) setTempPassword({ username: created.username, password: created.temporary_password });
                })}>Incluir</Button>
              </Stack>
              <Stack direction="row" spacing={1}>
                <TextField fullWidth placeholder="Buscar usuário" value={query} onChange={(e) => setQuery(e.target.value)} />
                <Tooltip title="Recarregar"><IconButton onClick={() => loadUsers().catch(show)}><RefreshIcon /></IconButton></Tooltip>
              </Stack>
            </ListHeader>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Usuário</TableCell>
                  <TableCell>Papéis</TableCell>
                  <TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((u) => (
                  <TableRow key={u.id} hover selected={u.id === selectedId} onClick={() => openUser(u.id)} sx={{ cursor: "pointer" }}>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{u.username}</Typography>
                      {u.full_name && <Typography variant="caption" color="text.secondary">{u.full_name}</Typography>}
                    </TableCell>
                    <TableCell>{u.roles.length}</TableCell>
                    <TableCell>
                      <Chip size="small" label={u.is_active ? "Ativo" : "Inativo"} color={u.is_active ? "success" : "default"} variant="outlined" />
                    </TableCell>
                  </TableRow>
                ))}
                {filtered.length === 0 && (
                  <TableRow><TableCell colSpan={3}><Typography variant="body2" color="text.secondary">Nenhum usuário encontrado.</Typography></TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </>
        }
        detail={
          !selected ? <EmptyDetail text="Selecione um usuário para ver papéis, exceções e o acesso final." /> : (
            <>
              <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={2}>
                <Box>
                  <Typography variant="h6">{selected.username}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {[selected.full_name, selected.email].filter(Boolean).join(" · ") || "Sem nome ou e-mail cadastrados"}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Tooltip title="Redefinir senha">
                    <IconButton onClick={() => { setResetUser(selected); setNewPass(""); setResetError(null); }}><LockResetIcon /></IconButton>
                  </Tooltip>
                  <FormControlLabel label="Habilitado" control={
                    <Switch checked={selected.is_active} onChange={(e) => change(() => updateUser(selected.id, { is_active: e.target.checked }))} />
                  } />
                </Stack>
              </Stack>

              <Section title="Papéis" hint="Os papéis definem as telas que o usuário enxerga.">
                <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 1.5 }}>
                  {selected.roles.map((r) => (
                    <Chip key={r.id} label={r.name}
                      onDelete={() => change(() => setUserRoles(selected.id, selected.roles.filter((x) => x.id !== r.id).map((x) => x.id)))} />
                  ))}
                  {selected.roles.length === 0 && <Typography variant="body2" color="text.secondary">Nenhum papel atribuído.</Typography>}
                </Stack>
                <FormControl size="small" sx={{ minWidth: 240 }}>
                  <InputLabel>Adicionar papel</InputLabel>
                  <Select label="Adicionar papel" value="" onChange={(e) => change(() =>
                    setUserRoles(selected.id, [...selected.roles.map((r) => r.id), Number(e.target.value)]))}>
                    {roles.filter((r) => !selected.roles.some((sr) => sr.id === r.id))
                      .map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                  </Select>
                </FormControl>
              </Section>

              <Section title="Telas que o usuário acessa" hint="Resultado final de papéis, grupos e exceções.">
                {ACCESS_AREAS.map((area) => {
                  const visible = area.screens.filter((s) => readableModules.has(s.module));
                  if (visible.length === 0) return null;
                  return (
                    <Box key={area.area} sx={{ mb: 1.5 }}>
                      <Typography variant="overline" color="text.secondary">{area.area}</Typography>
                      <Stack direction="row" spacing={1} flexWrap="wrap">
                        {visible.map((s) => (
                          <Chip key={s.route} size="small" variant="outlined"
                            color={editableModules.has(s.module) ? "primary" : "default"}
                            label={`${s.label}${editableModules.has(s.module) ? " · editar" : ""}`} />
                        ))}
                      </Stack>
                    </Box>
                  );
                })}
                {eff && readableModules.size === 0 && <Typography variant="body2" color="text.secondary">Nenhuma tela liberada.</Typography>}
                {eff && eff.origins.length > 0 && (
                  <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
                    Origem: {eff.origins.map((o) => `${o.role} (${o.origin})`).join(" · ")}
                  </Typography>
                )}
              </Section>

              <Section title="Exceções individuais" hint="Libera uma tela só para este usuário. Exige um motivo, que fica registrado.">
                <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mb: 1.5 }}>
                  <FormControl size="small" sx={{ minWidth: 240 }}>
                    <InputLabel>Tela</InputLabel>
                    <Select label="Tela" value={exceptionRoute} onChange={(e) => setExceptionRoute(e.target.value)}>
                      {ALL_SCREENS.map((s) => <MenuItem key={s.route} value={s.route}>{s.label}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <ToggleButtonGroup size="small" exclusive value={exceptionLevel}
                    onChange={(_, v: "view" | "edit" | null) => v && setExceptionLevel(v)}>
                    <ToggleButton value="view">Ver</ToggleButton>
                    <ToggleButton value="edit">Edição</ToggleButton>
                  </ToggleButtonGroup>
                  <TextField fullWidth label="Motivo (obrigatório)" value={exceptionReason} onChange={(e) => setExceptionReason(e.target.value)} />
                  <Button variant="outlined" disabled={!exceptionRoute || !exceptionReason.trim()} onClick={addException}>Conceder</Button>
                </Stack>
                <Stack direction="row" spacing={1} flexWrap="wrap">
                  {grants.map((g) => (
                    <Chip key={g.id} size="small" color="warning" variant="outlined"
                      title={g.reason}
                      label={`${moduleDisplayName(g.module)} · ${ACTION_LABELS[g.action] ?? g.action}`}
                      onDelete={() => change(() => deleteGrant(g.id))} />
                  ))}
                  {grants.length === 0 && <Typography variant="body2" color="text.secondary">Nenhuma exceção.</Typography>}
                </Stack>
              </Section>
            </>
          )
        }
      />

      <Dialog open={resetUser !== null} onClose={() => setResetUser(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Redefinir senha de {resetUser?.username}</DialogTitle>
        <DialogContent>
          {resetError && <Alert severity="error" sx={{ mb: 1 }}>{dataDisplayText(resetError)}</Alert>}
          <TextField size="small" fullWidth label="Senha nova (opcional)"
            helperText="Deixe em branco para gerar uma senha temporária. Mín. 10 caracteres, com maiúscula, minúscula e número."
            value={newPass} onChange={(e) => setNewPass(e.target.value)}
            type="password" autoComplete="new-password" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setResetUser(null)}>Cancelar</Button>
          <Button variant="outlined" disabled={!!newPass} onClick={async () => {
            try {
              const r = await resetUserPassword(resetUser!.id);
              if (r.temporary_password) setTempPassword({ username: resetUser!.username, password: r.temporary_password });
              setResetUser(null); setResetError(null);
            } catch (e: any) {
              setResetError(e?.response?.data?.detail || "Não foi possível redefinir a senha");
            }
          }}>Gerar senha temporária</Button>
          <Button variant="contained" disabled={!newPass.trim()} onClick={async () => {
            try {
              await resetUserPassword(resetUser!.id, newPass);
              setTempPassword(null);
              setResetUser(null); setNewPass(""); setResetError(null);
            } catch (e: any) {
              setResetError(e?.response?.data?.detail || "Não foi possível definir a senha");
            }
          }}>Definir nova senha</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

export default function Admin() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") || "users";
  const current = TABS.find((t) => t.value === requested) ?? TABS[2];

  const select = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    setParams(next);
  };

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#FAFBFD" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>Usuários e acessos</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Papéis definem as telas. Grupos reúnem papéis. Usuários recebem papéis e grupos, e o acesso final aparece em cada um.
        </Typography>
        <Tabs value={current.value} onChange={(_, v) => select(v)}>
          {TABS.map((t) => <Tab key={t.value} value={t.value} label={t.label} />)}
        </Tabs>
      </Box>
      <Box sx={{ p: 3 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{current.hint}</Typography>
        {current.value === "users" && <UsersTab />}
        {current.value === "groups" && <GroupsTab />}
        {current.value === "acl" && <AccessControlTab />}
        {current.value === "roles" && <RolesTab />}
      </Box>
    </Box>
  );
}
