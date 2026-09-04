import React, { useEffect, useMemo, useState } from "react";
import {
  Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, IconButton, InputLabel, MenuItem, Paper, Select, Stack, Tab, Tabs,
  TextField, Tooltip, Typography, Alert, Switch, FormControlLabel, Grid,
} from "@mui/material";
import { Add as AddIcon, Delete as DeleteIcon, Refresh as RefreshIcon, LockReset as LockResetIcon } from "@mui/icons-material";
import {
  AdminGroup, AdminRole, AdminUser, AdminGrant, EffectivePermissions,
  listRoles, createRole, deleteRole, getRole, setRolePermissions, addInheritance, removeInheritance, getRoleTree,
  listGroups, createGroup, deleteGroup, addGroupMember, removeGroupMember, addGroupRole, removeGroupRole,
  listUsers, createUser, updateUser, resetUserPassword, setUserRoles, getEffectivePermissions, listGrants, createGrant, deleteGrant,
} from "../../services/adminApi";

const ACTIONS = ["read", "create", "update", "delete"] as const;

function useErrorAlert() {
  const [error, setError] = useState<string | null>(null);
  const show = (e: any) => {
    const d = e?.response?.data?.detail;
    setError(typeof d === "string" ? d : d ? JSON.stringify(d) : String(e));
  };
  return [error, setError, show] as const;
}

// ── Roles ────────────────────────────────────────────────────────────────────
function RolesTab() {
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [selected, setSelected] = useState<AdminRole | null>(null);
  const [perms, setPerms] = useState<Record<string, Set<string>>>({});
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [tree, setTree] = useState<any>(null);
  const [newRole, setNewRole] = useState("");
  const [error, setError, show] = useErrorAlert();
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => listRoles().then(setRoles).catch(show);
  useEffect(() => { load(); }, []);

  const openRole = async (r: AdminRole) => {
    try {
      const full = await getRole(r.id);
      setSelected(full);
      const map: Record<string, Set<string>> = {};
      full.permissions?.forEach((p) => {
        (map[p.module] = map[p.module] || new Set()).add(p.action);
      });
      setPerms(map);
      const sc: Record<string, string> = {};
      full.permissions?.forEach((p) => {
        if (p.scope) sc[p.module] = sc[p.module] || p.scope;
      });
      setScopes(sc);
      setTree(await getRoleTree(r.id));
      setError(null); setMsg(null);
    } catch (e) { show(e); }
  };

  const modules = useMemo(() => {
    const set = new Set<string>();
    Object.keys(perms).forEach((m) => set.add(m));
    return Array.from(set).sort();
  }, [perms]);

  const toggle = (module: string, action: string) => {
    setPerms((prev) => {
      const next: Record<string, Set<string>> = {};
      Object.entries(prev).forEach(([m, s]) => (next[m] = new Set(s)));
      const set = next[module] || new Set<string>();
      // UX: marcar qualquer ação marca read automaticamente
      if (!set.has(action)) {
        set.add(action);
        if (action !== "read") set.add("read");
      } else {
        set.delete(action);
      }
      if (set.size === 0) delete next[module]; else next[module] = set;
      return next;
    });
  };

  const savePerms = async () => {
    if (!selected) return;
    const items = Object.entries(perms).flatMap(([module, set]) =>
      Array.from(set).map((action) => ({ module, action, scope: scopes[module] || null }))
    );
    try {
      await setRolePermissions(selected.id, items);
      setMsg("Permissões salvas");
      load();
    } catch (e) { show(e); }
  };

  const addModule = (module: string) => {
    if (!module || perms[module]) return;
    setPerms((prev) => ({ ...prev, [module]: new Set(["read"]) }));
  };

  const inherit = async (parentId: number) => {
    if (!selected) return;
    try { await addInheritance(selected.id, parentId); await openRole(selected); setMsg("Herança adicionada"); }
    catch (e) { show(e); }
  };
  const uninherit = async (parentId: number) => {
    if (!selected) return;
    try { await removeInheritance(selected.id, parentId); await openRole(selected); }
    catch (e) { show(e); }
  };

  const TreeNode = ({ node, depth }: { node: any; depth: number }) => (
    <Box sx={{ ml: depth * 2 }}>
      <Typography variant="body2">↳ {node.name}{node.cycle ? " (ciclo!)" : ""}</Typography>
      {node.parents?.map((p: any) => <TreeNode key={p.id} node={p} depth={depth + 1} />)}
    </Box>
  );

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {msg && <Alert severity="success">{msg}</Alert>}
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField size="small" label="Nova role" value={newRole} onChange={(e) => setNewRole(e.target.value)} />
        <Button variant="contained" startIcon={<AddIcon />} onClick={async () => {
          try { await createRole({ name: newRole }); setNewRole(""); load(); } catch (e) { show(e); }
        }}>Criar</Button>
        <IconButton onClick={load}><RefreshIcon /></IconButton>
      </Stack>
      <Stack direction="row" spacing={1} flexWrap="wrap">
        {roles.map((r) => (
          <Chip key={r.id} label={r.name} onClick={() => openRole(r)} color={selected?.id === r.id ? "primary" : "default"}
            onDelete={r.is_system ? undefined : async () => { try { await deleteRole(r.id); setSelected(null); load(); } catch (e) { show(e); } }} />
        ))}
      </Stack>
      {selected && (
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="h6">{selected.name} {selected.is_system ? "(sistema)" : ""}</Typography>
            <Button variant="contained" onClick={savePerms}>Salvar permissões</Button>
          </Stack>
          <Grid container spacing={1} sx={{ mb: 1 }}>
            <Grid item xs={2}><Typography variant="caption">Módulo</Typography></Grid>
            <Grid item xs={1}><Typography variant="caption">Escopo</Typography></Grid>
            {ACTIONS.map((a) => <Grid key={a} item xs={2}><Typography variant="caption">{a}</Typography></Grid>)}
          </Grid>
          {modules.map((m) => (
            <Grid container spacing={1} key={m} alignItems="center">
              <Grid item xs={2}><Typography variant="body2">{m}</Typography></Grid>
              <Grid item xs={1}>
                <TextField size="small" variant="standard" placeholder="escopo" title="Escopo (ex.: visao-contabil); vazio = módulo inteiro"
                  value={scopes[m] || ""} onChange={(e) => setScopes((prev) => ({ ...prev, [m]: e.target.value.trim() }))} />
              </Grid>
              {ACTIONS.map((a) => (
                <Grid key={a} item xs={2}>
                  <Checkbox size="small" checked={perms[m]?.has(a) ?? false} onChange={() => toggle(m, a)} />
                </Grid>
              ))}
            </Grid>
          ))}
          <FormControl size="small" sx={{ mt: 2, minWidth: 200 }}>
            <InputLabel>Adicionar módulo</InputLabel>
            <Select label="Adicionar módulo" value="" onChange={(e) => addModule(e.target.value)}>
              {["connections","rules","etl","analysis","forecast","planning","reservations","savings","periods","destinations","schema","fact","csv_upload","clone_rules","dimon","ai_copilot","ai_dashboards","ai_provider_dashboards","azure_ptu","azure_resource_graph","azure_tag_policy","oci_operational","scheduled_shutdown","auth","users","groups","roles","audit"]
                .filter((m) => !perms[m]).map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
            </Select>
          </FormControl>
          <Box sx={{ mt: 3 }}>
            <Typography variant="subtitle2">Herança (herda de):</Typography>
            <Stack direction="row" spacing={1} sx={{ my: 1 }} flexWrap="wrap">
              {selected.inherits_from?.map((p) => (
                <Chip key={p.id} label={p.name} onDelete={() => uninherit(p.id)} />
              ))}
              {selected.inherits_from?.length === 0 && <Typography variant="body2" color="text.secondary">nenhuma</Typography>}
            </Stack>
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel>Herdar de…</InputLabel>
              <Select label="Herdar de…" value="" onChange={(e) => inherit(Number(e.target.value))}>
                {roles.filter((r) => r.id !== selected.id && !selected.inherits_from?.some((p) => p.id === r.id))
                  .map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
              </Select>
            </FormControl>
            <Box sx={{ mt: 2 }}>
              <Typography variant="subtitle2">Árvore resolvida:</Typography>
              {tree && <TreeNode node={tree} depth={0} />}
            </Box>
          </Box>
        </Paper>
      )}
    </Stack>
  );
}

// ── Groups ───────────────────────────────────────────────────────────────────
function GroupsTab() {
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selected, setSelected] = useState<AdminGroup | null>(null);
  const [newGroup, setNewGroup] = useState("");
  const [error, setError, show] = useErrorAlert();

  const load = () => {
    listGroups().then((gs) => { setGroups(gs); if (selected) setSelected(gs.find((g) => g.id === selected.id) ?? null); }).catch(show);
    listRoles().then(setRoles).catch(show);
    listUsers().then(setUsers).catch(show);
  };
  useEffect(() => { load(); }, []);

  const call = async (fn: () => Promise<any>) => { try { await fn(); load(); setError(null); } catch (e) { show(e); } };

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" spacing={1}>
        <TextField size="small" label="Novo grupo" value={newGroup} onChange={(e) => setNewGroup(e.target.value)} />
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => call(async () => { await createGroup({ name: newGroup }); setNewGroup(""); })}>Criar</Button>
        <IconButton onClick={load}><RefreshIcon /></IconButton>
      </Stack>
      <Stack direction="row" spacing={1} flexWrap="wrap">
        {groups.map((g) => (
          <Chip key={g.id} label={g.name} color={selected?.id === g.id ? "primary" : "default"}
            onClick={() => setSelected(g)}
            onDelete={() => call(() => deleteGroup(g.id))} />
        ))}
      </Stack>
      {selected && (
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>{selected.name}</Typography>
          <Typography variant="subtitle2">Membros</Typography>
          <Stack direction="row" spacing={1} sx={{ my: 1 }} flexWrap="wrap">
            {selected.members.map((m) => (
              <Chip key={m.id} label={m.username} onDelete={() => call(() => removeGroupMember(selected.id, m.id))} />
            ))}
          </Stack>
          <FormControl size="small" sx={{ minWidth: 200, mb: 2 }}>
            <InputLabel>Adicionar membro</InputLabel>
            <Select label="Adicionar membro" value="" onChange={(e) => call(() => addGroupMember(selected.id, Number(e.target.value)))}>
              {users.filter((u) => !selected.members.some((m) => m.id === u.id))
                .map((u) => <MenuItem key={u.id} value={u.id}>{u.username}</MenuItem>)}
            </Select>
          </FormControl>
          <Typography variant="subtitle2">Roles do grupo</Typography>
          <Stack direction="row" spacing={1} sx={{ my: 1 }} flexWrap="wrap">
            {selected.roles.map((r) => (
              <Chip key={r.id} label={r.name} onDelete={() => call(() => removeGroupRole(selected.id, r.id))} />
            ))}
          </Stack>
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel>Associar role</InputLabel>
            <Select label="Associar role" value="" onChange={(e) => call(() => addGroupRole(selected.id, Number(e.target.value)))}>
              {roles.filter((r) => !selected.roles.some((gr) => gr.id === r.id))
                .map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
            </Select>
          </FormControl>
        </Paper>
      )}
    </Stack>
  );
}

// ── Users ────────────────────────────────────────────────────────────────────
function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [grants, setGrants] = useState<AdminGrant[]>([]);
  const [eff, setEff] = useState<EffectivePermissions | null>(null);
  const [newUser, setNewUser] = useState("");
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const [resetDialog, setResetDialog] = useState<AdminUser | null>(null);
  const [newPass, setNewPass] = useState("");
  const [resetError, setResetError] = useState<string | null>(null);
  const [grantModule, setGrantModule] = useState("rules");
  const [grantAction, setGrantAction] = useState<string>("create");
  const [grantReason, setGrantReason] = useState("");
  const [error, setError, show] = useErrorAlert();

  const load = () => {
    listUsers().then((us) => { setUsers(us); if (selected) setSelected(us.find((u) => u.id === selected.id) ?? null); }).catch(show);
    listRoles().then(setRoles).catch(show);
  };
  useEffect(() => { load(); }, []);

  const openUser = async (u: AdminUser) => {
    setSelected(u); setTempPassword(null);
    try {
      setGrants(await listGrants(u.id));
      setEff(await getEffectivePermissions(u.id));
      setError(null);
    } catch (e) { show(e); }
  };

  const call = async (fn: () => Promise<any>) => { try { await fn(); load(); if (selected) openUser(selected); setError(null); } catch (e) { show(e); } };

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {tempPassword && <Alert severity="info">Senha temporária de {selected?.username}: <b>{tempPassword}</b></Alert>}
      <Stack direction="row" spacing={1}>
        <TextField size="small" label="Novo usuário" value={newUser} onChange={(e) => setNewUser(e.target.value)} />
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => call(async () => {
          const created = await createUser({ username: newUser });
          setNewUser("");
          setTempPassword(created.temporary_password ?? null);
        })}>Criar</Button>
        <IconButton onClick={load}><RefreshIcon /></IconButton>
      </Stack>
      <Stack direction="row" spacing={1} flexWrap="wrap">
        {users.map((u) => (
          <Chip key={u.id} label={u.username} color={selected?.id === u.id ? "primary" : "default"}
            variant={u.is_active ? "filled" : "outlined"}
            onClick={() => openUser(u)} />
        ))}
      </Stack>
      {selected && (
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="h6">{selected.username} <Typography component="span" variant="caption">({selected.full_name ?? ""})</Typography></Typography>
            <Stack direction="row" spacing={1}>
              <Tooltip title="Resetar senha">
                <IconButton onClick={() => { setResetDialog(selected); setNewPass(""); setResetError(null); }}><LockResetIcon /></IconButton>
              </Tooltip>
              <FormControlLabel control={
                <Switch checked={selected.is_active} onChange={(e) => call(() => updateUser(selected.id, { is_active: e.target.checked }))} />
              } label="Ativo" />
            </Stack>
          </Stack>
          <Typography variant="subtitle2">Roles diretas</Typography>
          <Stack direction="row" spacing={1} sx={{ my: 1 }} flexWrap="wrap">
            {selected.roles.map((r) => (
              <Chip key={r.id} label={r.name}
                onDelete={() => call(() => setUserRoles(selected.id, selected.roles.filter((x) => x.id !== r.id).map((x) => x.id)))} />
            ))}
          </Stack>
          <FormControl size="small" sx={{ minWidth: 200, mb: 2 }}>
            <InputLabel>Adicionar role</InputLabel>
            <Select label="Adicionar role" value="" onChange={(e) => call(() => setUserRoles(selected.id, [...selected.roles.map((r) => r.id), Number(e.target.value)]))}>
              {roles.filter((r) => !selected.roles.some((sr) => sr.id === r.id))
                .map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
            </Select>
          </FormControl>
          <Typography variant="subtitle2">Grants diretos</Typography>
          <Stack direction="row" spacing={1} sx={{ my: 1 }} flexWrap="wrap">
            {grants.map((g) => (
              <Chip key={g.id} label={`${g.module}:${g.action}`} onDelete={() => call(() => deleteGrant(g.id))}
                title={g.reason} />
            ))}
            {grants.length === 0 && <Typography variant="body2" color="text.secondary">nenhum</Typography>}
          </Stack>
          <Stack direction="row" spacing={1} sx={{ mb: 2 }} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Módulo</InputLabel>
              <Select label="Módulo" value={grantModule} onChange={(e) => setGrantModule(e.target.value)}>
                {["connections","rules","etl","analysis","forecast","planning","reservations","savings","periods","destinations","schema","fact","csv_upload","clone_rules","dimon","ai_copilot","ai_dashboards","ai_provider_dashboards","azure_ptu","azure_resource_graph","azure_tag_policy","oci_operational","scheduled_shutdown","auth","users","groups","roles","audit"].map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 110 }}>
              <InputLabel>Ação</InputLabel>
              <Select label="Ação" value={grantAction} onChange={(e) => setGrantAction(e.target.value)}>
                {ACTIONS.map((a) => <MenuItem key={a} value={a}>{a}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField size="small" label="Motivo (obrigatório)" value={grantReason} onChange={(e) => setGrantReason(e.target.value)} sx={{ minWidth: 240 }} />
            <Button variant="outlined" disabled={!grantReason.trim()} onClick={() => call(async () => {
              await createGrant(selected.id, { module: grantModule, action: grantAction, reason: grantReason });
              setGrantReason("");
            })}>Conceder</Button>
          </Stack>
          {eff && (
            <>
              <Typography variant="subtitle2">Permissões efetivas ({eff.permissions.length})</Typography>
              <Box sx={{ mt: 1 }}>
                {eff.origins.map((o, i) => (
                  <Typography key={i} variant="body2">
                    <b>{o.role}</b> — <Typography component="span" color="text.secondary">{o.origin}</Typography>
                  </Typography>
                ))}
              </Box>
            </>
          )}
          <Dialog open={resetDialog !== null} onClose={() => setResetDialog(null)} maxWidth="xs" fullWidth>
            <DialogTitle>Resetar senha de {resetDialog?.username}</DialogTitle>
            <DialogContent>
              {tempPassword && <Alert severity="info" sx={{ mb: 1 }}>Senha temporária: <b>{tempPassword}</b></Alert>}
              {resetError && <Alert severity="error" sx={{ mb: 1 }}>{resetError}</Alert>}
              <TextField size="small" fullWidth label="Nova senha (opcional)"
                helperText="Deixe vazio para gerar temporária. Mín. 10 caracteres, maiúscula, minúscula e dígito."
                value={newPass} onChange={(e) => setNewPass(e.target.value)}
                type="password" autoComplete="new-password" />
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setResetDialog(null)}>Cancelar</Button>
              <Button variant="outlined" disabled={!!newPass} onClick={async () => {
                try {
                  const r = await resetUserPassword(resetDialog!.id);
                  if (r.temporary_password) setTempPassword(r.temporary_password);
                  setResetDialog(null); setResetError(null);
                } catch (e: any) {
                  setResetError(e?.response?.data?.detail || "Falha ao resetar");
                }
              }}>Gerar temporária</Button>
              <Button variant="contained" disabled={!newPass.trim()} onClick={async () => {
                try {
                  await resetUserPassword(resetDialog!.id, newPass);
                  setTempPassword(null);
                  setResetDialog(null); setNewPass(""); setResetError(null);
                  load();
                } catch (e: any) {
                  setResetError(e?.response?.data?.detail || "Falha ao definir senha");
                }
              }}>Definir senha</Button>
            </DialogActions>
          </Dialog>
        </Paper>
      )}
    </Stack>
  );
}

export default function Admin() {
  const [tab, setTab] = useState(0);
  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 2 }}>Administração</Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Usuários" />
        <Tab label="Grupos" />
        <Tab label="Roles" />
      </Tabs>
      {tab === 0 && <UsersTab />}
      {tab === 1 && <GroupsTab />}
      {tab === 2 && <RolesTab />}
    </Box>
  );
}
