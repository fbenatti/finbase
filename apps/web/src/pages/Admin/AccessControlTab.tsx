import React, { useEffect, useState } from "react";
import { Alert, Box, Button, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { listRoles, listUsers, type AdminRole, type AdminUser } from "../../services/adminApi";
import {
  createRoleAcl, createScope, deleteAcl, grantUserScope, listRoleAcls, listScopes, simulateDecision,
  type AccessAcl, type AccessScope, type AclOperation, type AclResourceType, type MatchMode, type ScopeType,
} from "../../services/accessApi";

const RESOURCE_TYPES: Array<{ value: AclResourceType; label: string }> = [
  { value: "screen", label: "Tela" },
  { value: "table", label: "Tabela" },
  { value: "field", label: "Campo" },
  { value: "rule", label: "Regra" },
];
const OPERATIONS: Array<{ value: AclOperation; label: string }> = [
  { value: "read", label: "Ler" },
  { value: "create", label: "Criar" },
  { value: "write", label: "Editar" },
  { value: "delete", label: "Excluir" },
];
const SCOPE_TYPES: Array<{ value: ScopeType; label: string }> = [
  { value: "hierarchy", label: "Hierarquia" },
  { value: "budget", label: "Orçamento" },
  { value: "cost_center", label: "Centro de custo" },
];

function errorText(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : "Não foi possível concluir a ação.";
}

export function AccessControlTab() {
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [scopes, setScopes] = useState<AccessScope[]>([]);
  const [roleId, setRoleId] = useState<number | "">("");
  const [acls, setAcls] = useState<AccessAcl[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [aclForm, setAclForm] = useState({
    resource_type: "table" as AclResourceType, resource: "", operation: "read" as AclOperation,
    effect: "allow" as "allow" | "deny", column: "", scope_type: "hierarchy" as ScopeType, match_mode: "subtree" as MatchMode,
  });
  const [scopeForm, setScopeForm] = useState({ scope_type: "hierarchy" as ScopeType, value: "", label: "" });
  const [grantForm, setGrantForm] = useState({ user_id: "" as number | "", scope_id: "" as number | "", match_mode: "subtree" as MatchMode, reason: "" });
  const [simForm, setSimForm] = useState({ user_id: "" as number | "", operation: "read" as AclOperation, resource_type: "table" as AclResourceType, resource: "" });
  const [simResult, setSimResult] = useState<string | null>(null);

  const guard = (fn: () => Promise<void>) => fn().catch((e) => setError(errorText(e)));

  const reloadScopes = () => listScopes().then(setScopes);
  const reloadAcls = (id: number) => listRoleAcls(id).then(setAcls);

  useEffect(() => {
    guard(async () => {
      const [r, u] = await Promise.all([listRoles(), listUsers()]);
      setRoles(r);
      setUsers(u);
      await reloadScopes();
    });
  }, []);

  useEffect(() => {
    if (roleId !== "") guard(() => reloadAcls(roleId));
    else setAcls([]);
  }, [roleId]);

  const addAcl = () => {
    const rid = roleId;
    if (rid === "") return;
    const conditions = aclForm.column
      ? [{ column: aclForm.column, scope_type: aclForm.scope_type, match_mode: aclForm.match_mode }]
      : [];
    guard(async () => {
      await createRoleAcl(rid, {
        resource_type: aclForm.resource_type, resource: aclForm.resource.trim(), operation: aclForm.operation,
        effect: aclForm.effect, conditions,
      });
      setAclForm({ ...aclForm, resource: "", column: "" });
      await reloadAcls(rid);
    });
  };

  const removeAcl = (id: number) => guard(async () => {
    const rid = roleId;
    await deleteAcl(id);
    if (rid !== "") await reloadAcls(rid);
  });

  const addScope = () => guard(async () => {
    await createScope({ scope_type: scopeForm.scope_type, value: scopeForm.value.trim(), label: scopeForm.label || undefined });
    setScopeForm({ ...scopeForm, value: "", label: "" });
    await reloadScopes();
  });

  const addGrant = () => {
    const { user_id: userId, scope_id: scopeId } = grantForm;
    if (userId === "" || scopeId === "") return;
    guard(async () => {
      await grantUserScope(userId, { scope_id: scopeId, match_mode: grantForm.match_mode, reason: grantForm.reason });
      setGrantForm({ ...grantForm, reason: "" });
    });
  };

  const runSimulation = () => {
    const userId = simForm.user_id;
    if (userId === "") return;
    guard(async () => {
      const result = await simulateDecision(userId, {
        operation: simForm.operation, resource_type: simForm.resource_type, resource: simForm.resource.trim(),
      });
      setSimResult(`${result.allowed ? "Permitido" : "Negado"}: ${result.reason}`);
    });
  };

  return (
    <Stack spacing={3}>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6">ACLs por perfil</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Cada regra vale para um perfil. Uma regra de negação vence qualquer permissão. Uma condição limita as linhas ao escopo da pessoa.
        </Typography>
        <TextField select fullWidth size="small" label="Perfil" value={roleId} onChange={(e) => setRoleId(e.target.value === "" ? "" : Number(e.target.value))} sx={{ mb: 2 }}>
          {roles.map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
        </TextField>
        {roleId !== "" && (
          <>
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Tipo</TableCell><TableCell>Recurso</TableCell><TableCell>Operação</TableCell>
                    <TableCell>Efeito</TableCell><TableCell>Condição</TableCell><TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {acls.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell>{RESOURCE_TYPES.find((t) => t.value === a.resource_type)?.label}</TableCell>
                      <TableCell>{a.resource}</TableCell>
                      <TableCell>{OPERATIONS.find((o) => o.value === a.operation)?.label}</TableCell>
                      <TableCell>{a.effect === "deny" ? "Negar" : "Permitir"}</TableCell>
                      <TableCell>{a.conditions.map((c) => `${c.column} (${c.scope_type}, ${c.match_mode})`).join("; ") || "—"}</TableCell>
                      <TableCell align="right"><Button size="small" color="error" onClick={() => removeAcl(a.id)}>Remover</Button></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
            <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 2, flexWrap: "wrap" }}>
              <TextField select size="small" label="Tipo" value={aclForm.resource_type} onChange={(e) => setAclForm({ ...aclForm, resource_type: e.target.value as AclResourceType })}>
                {RESOURCE_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
              </TextField>
              <TextField size="small" label="Recurso" placeholder="fact_cost ou /planejamento" value={aclForm.resource} onChange={(e) => setAclForm({ ...aclForm, resource: e.target.value })} />
              <TextField select size="small" label="Operação" value={aclForm.operation} onChange={(e) => setAclForm({ ...aclForm, operation: e.target.value as AclOperation })}>
                {OPERATIONS.map((o) => <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>)}
              </TextField>
              <TextField select size="small" label="Efeito" value={aclForm.effect} onChange={(e) => setAclForm({ ...aclForm, effect: e.target.value as "allow" | "deny" })}>
                <MenuItem value="allow">Permitir</MenuItem>
                <MenuItem value="deny">Negar</MenuItem>
              </TextField>
              <TextField size="small" label="Coluna da linha (opcional)" placeholder="department_id" value={aclForm.column} onChange={(e) => setAclForm({ ...aclForm, column: e.target.value })} />
              <TextField select size="small" label="Escopo" value={aclForm.scope_type} onChange={(e) => setAclForm({ ...aclForm, scope_type: e.target.value as ScopeType })}>
                {SCOPE_TYPES.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
              <Button variant="contained" onClick={addAcl} disabled={!aclForm.resource.trim()}>Adicionar regra</Button>
            </Stack>
          </>
        )}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6">Escopos</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Nós de hierarquia ou unidades de orçamento. Uma pessoa recebe um escopo com prazo e motivo.
        </Typography>
        <Box sx={{ overflowX: "auto" }}>
          <Table size="small">
            <TableHead><TableRow><TableCell>Tipo</TableCell><TableCell>Valor</TableCell><TableCell>Descrição</TableCell></TableRow></TableHead>
            <TableBody>
              {scopes.map((s) => (
                <TableRow key={s.id}>
                  <TableCell>{SCOPE_TYPES.find((t) => t.value === s.scope_type)?.label}</TableCell>
                  <TableCell>{s.value}</TableCell>
                  <TableCell>{s.label || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 2 }}>
          <TextField select size="small" label="Tipo" value={scopeForm.scope_type} onChange={(e) => setScopeForm({ ...scopeForm, scope_type: e.target.value as ScopeType })}>
            {SCOPE_TYPES.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Valor (id ou código)" value={scopeForm.value} onChange={(e) => setScopeForm({ ...scopeForm, value: e.target.value })} />
          <TextField size="small" label="Descrição" value={scopeForm.label} onChange={(e) => setScopeForm({ ...scopeForm, label: e.target.value })} />
          <Button variant="outlined" onClick={addScope} disabled={!scopeForm.value.trim()}>Criar escopo</Button>
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6">Conceder escopo a uma pessoa</Typography>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 1, flexWrap: "wrap" }}>
          <TextField select size="small" label="Pessoa" value={grantForm.user_id} onChange={(e) => setGrantForm({ ...grantForm, user_id: Number(e.target.value) })} sx={{ minWidth: 200 }}>
            {users.map((u) => <MenuItem key={u.id} value={u.id}>{u.username}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Escopo" value={grantForm.scope_id} onChange={(e) => setGrantForm({ ...grantForm, scope_id: Number(e.target.value) })} sx={{ minWidth: 220 }}>
            {scopes.map((s) => <MenuItem key={s.id} value={s.id}>{`${s.scope_type}: ${s.value}`}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Alcance" value={grantForm.match_mode} onChange={(e) => setGrantForm({ ...grantForm, match_mode: e.target.value as MatchMode })}>
            <MenuItem value="subtree">Nó e descendentes</MenuItem>
            <MenuItem value="exact">Só o nó</MenuItem>
          </TextField>
          <TextField size="small" label="Motivo (obrigatório)" value={grantForm.reason} onChange={(e) => setGrantForm({ ...grantForm, reason: e.target.value })} />
          <Button variant="outlined" onClick={addGrant} disabled={grantForm.user_id === "" || grantForm.scope_id === "" || grantForm.reason.trim().length < 3}>Conceder</Button>
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6">Ver como</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Confira o que uma pessoa pode fazer, com o motivo da decisão.
        </Typography>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ flexWrap: "wrap" }}>
          <TextField select size="small" label="Pessoa" value={simForm.user_id} onChange={(e) => setSimForm({ ...simForm, user_id: Number(e.target.value) })} sx={{ minWidth: 200 }}>
            {users.map((u) => <MenuItem key={u.id} value={u.id}>{u.username}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Tipo" value={simForm.resource_type} onChange={(e) => setSimForm({ ...simForm, resource_type: e.target.value as AclResourceType })}>
            {RESOURCE_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Recurso" value={simForm.resource} onChange={(e) => setSimForm({ ...simForm, resource: e.target.value })} />
          <TextField select size="small" label="Operação" value={simForm.operation} onChange={(e) => setSimForm({ ...simForm, operation: e.target.value as AclOperation })}>
            {OPERATIONS.map((o) => <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>)}
          </TextField>
          <Button variant="outlined" onClick={runSimulation} disabled={simForm.user_id === "" || !simForm.resource.trim()}>Simular</Button>
        </Stack>
        {simResult && <Typography sx={{ mt: 2 }}>{simResult}</Typography>}
      </Paper>
    </Stack>
  );
}
