import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { moduleDisplayName } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { Alert, Accordion, AccordionDetails, AccordionSummary, Box, Button, Chip, CircularProgress, IconButton, MenuItem, Paper, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from "@mui/material";
import { EditOutlined, ExpandMoreOutlined } from "@mui/icons-material";
import { useAuth } from "../../auth/AuthContext";
import { useNavigation } from "../../navigation/NavigationContext";
import type { NavigationModule } from "../../navigation/types";
import { api, formatApiError } from "../../services/api";

interface Customer { id: number; name: string; slug: string }
interface Member { user_id: number; username: string; customer_id: number }
const slugFor = (name: string) => name.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const matches = (item: NavigationModule, query: string) => `${item.label} ${item.default_label ?? ""} ${item.path} ${item.code}`.toLocaleLowerCase().includes(query.toLocaleLowerCase());

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
  const [renaming, setRenaming] = useState<NavigationModule | null>(null);
  const [draft, setDraft] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.get<Customer[]>("/administracao/modulos/clientes"), api.get<Member[]>("/administracao/modulos/membros")])
      .then(([c, m]) => { if (!cancelled) { setCustomers(c.data); setMembers(m.data); setCustomerId(value => value || navigation.customerId || c.data[0]?.id || ""); } })
      .catch(e => { if (!cancelled) { setError(formatApiError(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [navigation.customerId]);
  useEffect(() => {
    if (!customerId) return;
    let cancelled = false;
    setLoading(true);
    api.get<NavigationModule[]>(`/administracao/modulos/clientes/${customerId}`).then(r => { if (!cancelled) setItems(r.data); })
      .catch(e => { if (!cancelled) { setItems([]); setError(formatApiError(e)); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [customerId, revision]);
  const toggle = async (item: NavigationModule) => {
    setBusy(true); setError("");
    try { await api.put(`/administracao/modulos/clientes/${customerId}/${item.code}`, { enabled: !item.configured_enabled }); setRevision(n => n + 1); await navigation.reload(); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const saveLabel = async (label: string | null) => {
    if (!renaming) return;
    setBusy(true); setError("");
    try { await api.put(`/administracao/modulos/catalogo/${renaming.code}`, { label }); setRenaming(null); setRevision(n => n + 1); await navigation.reload(); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const createCustomer = async () => {
    setBusy(true); setError("");
    try { const r = await api.post<Customer>("/administracao/modulos/clientes", { name, slug }); setCustomers(all => [...all, r.data]); setCustomerId(r.data.id); setCreateOpen(false); setName(""); setSlug(""); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const assignMember = async (member: Member, value: number) => {
    setBusy(true); setError("");
    try { await api.put(`/administracao/modulos/membros/${member.user_id}`, { customer_id: value }); setMembers(all => all.map(m => m.user_id === member.user_id ? { ...m, customer_id: value } : m)); await navigation.reload(); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const canUpdate = hasPerm("modules", "update");
  const groups = items.filter(item => !item.parent_code).sort((a, b) => a.position - b.position);
  const childrenOf = (group: NavigationModule) => items.filter(item => item.parent_code === group.code).sort((a, b) => a.position - b.position);
  const shown = (group: NavigationModule) => {
    const children = childrenOf(group).filter(child => matches(child, query));
    return matches(group, query) || children.length ? children : null;
  };
  return <Stack spacing={2}>
    <Box><Typography variant="h4">Módulos</Typography><Typography color="text.secondary" mt={1}>Defina quais grupos e telas cada cliente enxerga. Desativar um grupo bloqueia também as telas dele. As permissões de cada usuário continuam valendo.</Typography></Box>
    {error && <Alert severity="error">{error}</Alert>}
    <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
      <TextField select label="Cliente" value={customerId} onChange={e => setCustomerId(Number(e.target.value))} disabled={busy} sx={{ minWidth: 240 }}>{customers.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}</TextField>
      <TextField label="Buscar tela ou rota" value={query} onChange={e => setQuery(e.target.value)} sx={{ flex: 1 }} />
      {hasPerm("modules", "create") && <Button variant="outlined" onClick={() => setCreateOpen(true)} disabled={busy}>Novo cliente</Button>}
    </Stack>
    <Alert severity="info">Novas telas do catálogo entram automaticamente nesta lista. Renomear uma tela atualiza o nome no menu e na navegação de todos os clientes.</Alert>
    {loading ? <Box sx={{ p: 4, textAlign: "center" }}><CircularProgress /></Box> : <>
      {groups.map(group => {
        const children = shown(group);
        if (!children) return null;
        return <Accordion key={group.code} defaultExpanded disableGutters variant="outlined">
          <AccordionSummary expandIcon={<ExpandMoreOutlined />}>
            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ width: "100%", pr: 1 }}>
              <Typography fontWeight={700} sx={{ flex: 1 }}>{group.label}</Typography>
              {group.custom_label && <Chip size="small" variant="outlined" label={`Nome original: ${group.default_label}`} />}
              <Typography variant="caption" color="text.secondary">{children.length} {children.length === 1 ? "tela" : "telas"}</Typography>
              <Tooltip title={canUpdate ? "Renomear grupo" : "Sem permissão para renomear"}><span onClick={e => e.stopPropagation()}><IconButton size="small" aria-label={`Renomear ${group.label}`} disabled={!canUpdate || busy} onClick={() => { setRenaming(group); setDraft(group.label); }}><EditOutlined fontSize="small" /></IconButton></span></Tooltip>
              <Tooltip title={group.required ? "A administração de módulos permanece acessível." : "Disponibilizar ou bloquear o grupo inteiro para este cliente"}><span onClick={e => e.stopPropagation()}><Switch checked={group.configured_enabled} disabled={!canUpdate || group.required || busy} onChange={() => void toggle(group)} inputProps={{ "aria-label": `Disponibilizar ${group.label}` }} /></span></Tooltip>
            </Stack>
          </AccordionSummary>
          <AccordionDetails sx={{ p: 0 }}>
            <Paper variant="outlined" sx={{ overflowX: "auto", border: 0 }}><Table size="small" sx={{ minWidth: 820, tableLayout: "fixed" }}><TableHead><TableRow><TableCell sx={{ width: "26%" }}>Tela</TableCell><TableCell sx={{ width: "27%" }}>Rota</TableCell><TableCell sx={{ width: "24%" }}>Permissão</TableCell><TableCell sx={{ width: "15%" }}>Situação</TableCell><TableCell align="right" sx={{ width: "8%" }}>Ativa</TableCell></TableRow></TableHead><TableBody>
              {children.map(item => <TableRow key={item.code}>
                <TableCell sx={{ pl: 3 }}>
                  <Stack direction="row" alignItems="center" spacing={0.5}>
                    <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>{item.label}</Typography>
                    <Tooltip title={canUpdate ? "Renomear tela" : "Sem permissão para renomear"}><span><IconButton size="small" aria-label={`Renomear ${item.label}`} disabled={!canUpdate || busy} onClick={() => { setRenaming(item); setDraft(item.label); }}><EditOutlined sx={{ fontSize: 16 }} /></IconButton></span></Tooltip>
                  </Stack>
                  {item.custom_label && <Typography variant="caption" color="text.secondary">Nome original: {item.default_label}</Typography>}
                  {item.visible === false && <Chip size="small" label="Fora da navegação" sx={{ mt: 0.5 }} />}
                </TableCell>
                <TableCell sx={{ fontFamily: "monospace", fontSize: 12, overflowWrap: "anywhere" }}>{item.path}</TableCell>
                <TableCell><Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>{item.permission_module ? `${moduleDisplayName(item.permission_module)} · ${item.permission_scope}` : "Sem permissão própria"}</Typography></TableCell>
                <TableCell><Typography variant="caption">{item.enabled ? "Disponível" : item.configured_enabled ? "Bloqueada pelo grupo" : "Desativada"}</Typography></TableCell>
                <TableCell align="right"><Tooltip title={item.required ? "A administração de módulos permanece acessível." : "Ativar ou desativar para este cliente"}><span><Switch checked={item.configured_enabled} disabled={!canUpdate || item.required || busy} onChange={() => void toggle(item)} inputProps={{ "aria-label": `Disponibilizar ${item.label}` }} /></span></Tooltip></TableCell>
              </TableRow>)}
            </TableBody></Table></Paper>
          </AccordionDetails>
        </Accordion>;
      })}
      {!groups.some(group => shown(group)) && <Paper variant="outlined" sx={{ p: 3, textAlign: "center" }}><Typography color="text.secondary">Nenhuma tela encontrada para essa busca.</Typography></Paper>}
    </>}
    <Accordion><AccordionSummary expandIcon={<ExpandMoreOutlined />}><Typography>Vínculo de usuários aos clientes</Typography></AccordionSummary><AccordionDetails><Typography color="text.secondary" mb={2}>O cliente é identificado pelo usuário autenticado. Usuários sem vínculo específico usam o cliente padrão.</Typography><Stack spacing={2}>{members.map(m => <Stack key={m.user_id} direction="row" spacing={2} alignItems="center"><Typography sx={{ minWidth: 160 }}>{m.username}</Typography><TextField select size="small" label="Cliente do usuário" value={m.customer_id} disabled={!canUpdate || busy} onChange={e => void assignMember(m, Number(e.target.value))} sx={{ minWidth: 240 }}>{customers.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}</TextField></Stack>)}</Stack></AccordionDetails></Accordion>
    <Dialog open={Boolean(renaming)} onClose={() => !busy && setRenaming(null)} fullWidth maxWidth="xs"><DialogTitle>Renomear</DialogTitle><DialogContent><Stack spacing={2} pt={1}><TextField autoFocus label="Nome exibido no menu" value={draft} onChange={e => setDraft(e.target.value)} helperText={renaming?.default_label ? `Nome original: ${renaming.default_label}` : undefined} inputProps={{ maxLength: 160 }} /></Stack></DialogContent><DialogActions>{renaming?.custom_label && <Button onClick={() => void saveLabel(null)} disabled={busy}>Restaurar nome original</Button>}<Button onClick={() => setRenaming(null)} disabled={busy}>Cancelar</Button><Button variant="contained" onClick={() => void saveLabel(draft.trim())} disabled={busy || draft.trim().length < 2}>Salvar nome</Button></DialogActions></Dialog>
    <Dialog open={createOpen} onClose={() => !busy && setCreateOpen(false)} fullWidth maxWidth="xs"><DialogTitle>Novo cliente</DialogTitle><DialogContent><Stack spacing={2} pt={1}><TextField label="Nome" value={name} onChange={e => { setName(e.target.value); setSlug(slugFor(e.target.value)); }} /><TextField label="Identificador" value={slug} onChange={e => setSlug(e.target.value)} helperText="Letras minúsculas, números e hífens" /></Stack></DialogContent><DialogActions><Button onClick={() => setCreateOpen(false)} disabled={busy}>Cancelar</Button><Button onClick={() => void createCustomer()} disabled={busy || !name.trim() || !slug.trim()} variant="contained">Criar cliente</Button></DialogActions></Dialog>
  </Stack>;
}
