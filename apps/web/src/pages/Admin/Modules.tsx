import { moduleDisplayName } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { Alert, Accordion, AccordionDetails, AccordionSummary, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from "@mui/material";
import { ExpandMore } from "@mui/icons-material";
import { useAuth } from "../../auth/AuthContext";
import { useNavigation } from "../../navigation/NavigationContext";
import type { NavigationModule } from "../../navigation/types";
import { api, formatApiError } from "../../services/api";

interface Customer { id: number; name: string; slug: string }
interface Member { user_id: number; username: string; customer_id: number }
const slugFor = (name: string) => name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export default function Modules() {
  const { hasPerm } = useAuth();
  const navigation = useNavigation();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [customerId, setCustomerId] = useState<number | "">(navigation.customerId || "");
  const [items, setItems] = useState<NavigationModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.get<Customer[]>("/modules/customers"), api.get<Member[]>("/modules/members")])
      .then(([c, m]) => { if (!cancelled) { setCustomers(c.data); setMembers(m.data); setCustomerId(value => value || navigation.customerId || c.data[0]?.id || ""); } })
      .catch(e => { if (!cancelled) { setError(formatApiError(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [navigation.customerId]);
  useEffect(() => {
    if (!customerId) return;
    let cancelled = false;
    setLoading(true);
    api.get<NavigationModule[]>(`/modules/customers/${customerId}`).then(r => { if (!cancelled) setItems(r.data); })
      .catch(e => { if (!cancelled) { setItems([]); setError(formatApiError(e)); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [customerId, revision]);
  const toggle = async (item: NavigationModule) => {
    setBusy(true); setError("");
    try { await api.put(`/modules/customers/${customerId}/${item.code}`, { enabled: !item.configured_enabled }); setRevision(n => n + 1); await navigation.reload(); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const createCustomer = async () => {
    setBusy(true); setError("");
    try { const r = await api.post<Customer>("/modules/customers", { name, slug }); setCustomers(all => [...all, r.data]); setCustomerId(r.data.id); setCreateOpen(false); setName(""); setSlug(""); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const assignMember = async (member: Member, value: number) => {
    setBusy(true); setError("");
    try { await api.put(`/modules/members/${member.user_id}`, { customer_id: value }); setMembers(all => all.map(m => m.user_id === member.user_id ? { ...m, customer_id: value } : m)); await navigation.reload(); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const canUpdate = hasPerm("modules", "update");
  const visible = items.filter(item => `${item.label} ${item.path} ${item.code}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <Stack spacing={2}>
    <Box><Typography variant="h4">Módulos</Typography><Typography color="text.secondary" mt={1}>Defina os menus disponíveis para cada cliente. Desativar um grupo também bloqueia suas páginas. As permissões de cada usuário continuam sendo aplicadas.</Typography></Box>
    {error && <Alert severity="error">{error}</Alert>}
    <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
      <TextField select label="Cliente" value={customerId} onChange={e => setCustomerId(Number(e.target.value))} disabled={busy} sx={{ minWidth: 240 }}>{customers.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}</TextField>
      <TextField label="Buscar módulo ou rota" value={query} onChange={e => setQuery(e.target.value)} sx={{ flex: 1 }} />
      {hasPerm("modules", "create") && <Button variant="outlined" onClick={() => setCreateOpen(true)} disabled={busy}>Novo cliente</Button>}
    </Stack>
    <Alert severity="info">Novos menus cadastrados no catálogo do projeto entram automaticamente nesta lista. Cada página tem sua própria rota e identificação de permissão.</Alert>
    {loading ? <Box sx={{ p: 4, textAlign: "center" }}><CircularProgress /></Box> : <Paper variant="outlined" sx={{ overflowX: "auto" }}><Table size="small"><TableHead><TableRow><TableCell>Módulo / menu</TableCell><TableCell>Rota</TableCell><TableCell>Permissão</TableCell><TableCell>Disponibilidade</TableCell><TableCell align="right">Ativo para o cliente</TableCell></TableRow></TableHead><TableBody>
      {visible.map(item => <TableRow key={item.code} sx={!item.parent_code ? { bgcolor: "action.hover" } : undefined}>
        <TableCell sx={{ pl: item.parent_code ? 4 : 2, fontWeight: item.parent_code ? 400 : 700 }}>{item.label}{item.visible === false && <Chip size="small" label="Fora da navegação" sx={{ ml: 1 }} />}{item.required && <Chip size="small" label="Essencial" sx={{ ml: 1 }} />}</TableCell>
        <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{item.path}</TableCell>
        <TableCell><Typography variant="caption">{item.permission_module ? `${moduleDisplayName(item.permission_module)} · ${item.permission_scope}` : "Grupo de módulos"}</Typography></TableCell>
        <TableCell>{item.enabled ? "Disponível" : item.configured_enabled ? "Bloqueado pelo grupo ou ambiente" : "Desativado"}</TableCell>
        <TableCell align="right"><Tooltip title={item.required ? "A administração de módulos permanece acessível." : "Ativar ou desativar para este cliente"}><span><Switch checked={item.configured_enabled} disabled={!canUpdate || item.required || busy} onChange={() => void toggle(item)} inputProps={{ "aria-label": `Disponibilizar ${item.label}` }} /></span></Tooltip></TableCell>
      </TableRow>)}
      {!visible.length && <TableRow><TableCell colSpan={5}>Nenhum módulo encontrado.</TableCell></TableRow>}
    </TableBody></Table></Paper>}
    <Accordion><AccordionSummary expandIcon={<ExpandMore />}><Typography>Vínculo de usuários aos clientes</Typography></AccordionSummary><AccordionDetails><Typography color="text.secondary" mb={2}>O cliente é identificado pelo usuário autenticado. Usuários sem vínculo específico usam o cliente padrão.</Typography><Stack spacing={2}>{members.map(m => <Stack key={m.user_id} direction="row" spacing={2} alignItems="center"><Typography sx={{ minWidth: 160 }}>{m.username}</Typography><TextField select size="small" label="Cliente do usuário" value={m.customer_id} disabled={!canUpdate || busy} onChange={e => void assignMember(m, Number(e.target.value))} sx={{ minWidth: 240 }}>{customers.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}</TextField></Stack>)}</Stack></AccordionDetails></Accordion>
    <Dialog open={createOpen} onClose={() => !busy && setCreateOpen(false)} fullWidth maxWidth="xs"><DialogTitle>Novo cliente</DialogTitle><DialogContent><Stack spacing={2} pt={1}><TextField label="Nome" value={name} onChange={e => { setName(e.target.value); setSlug(slugFor(e.target.value)); }} /><TextField label="Identificador" value={slug} onChange={e => setSlug(e.target.value)} helperText="Letras minúsculas, números e hífens" /></Stack></DialogContent><DialogActions><Button onClick={() => setCreateOpen(false)} disabled={busy}>Cancelar</Button><Button onClick={() => void createCustomer()} disabled={busy || !name.trim() || !slug.trim()} variant="contained">Criar cliente</Button></DialogActions></Dialog>
  </Stack>;
}
