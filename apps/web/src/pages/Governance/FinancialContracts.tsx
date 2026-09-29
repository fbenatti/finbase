import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, FormControl, InputLabel, ListItemText, MenuItem, OutlinedInput,
  Select, Table, TableBody, TableCell, TableHead, TableRow, TextField,
} from "@mui/material";
import { Add } from "@mui/icons-material";
import {
  createFinancialContract, FinancialContract, FinancialContractScope,
  getConnections, getDepartments, getFinancialContractScopes, getFinancialContracts,
} from "../../services/api";

const blank = { code: "", name: "", valid_from: "202601", valid_until: "202612", currency: "BRL", synthetic_department_id: "", connection_ids: [] as number[], scopes: [] as string[] };

export default function FinancialContracts() {
  const [contracts, setContracts] = useState<FinancialContract[]>([]);
  const [scopes, setScopes] = useState<FinancialContractScope[]>([]);
  const [connections, setConnections] = useState<{ id: number; name: string; code: string }[]>([]);
  const [departments, setDepartments] = useState<{ id: number; name: string; id_orcamento?: string | null }[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(blank);
  const [error, setError] = useState("");

  const load = () => getFinancialContracts().then(setContracts).catch((e) => setError(String(e)));
  useEffect(() => {
    load();
    getFinancialContractScopes().then(setScopes).catch(() => {});
    getConnections().then((rows) => setConnections(rows as typeof connections)).catch(() => {});
    getDepartments(true).then((rows) => setDepartments(rows as typeof departments)).catch(() => {});
  }, []);

  const save = async () => {
    try {
      await createFinancialContract({
        code: form.code, name: form.name, currency: form.currency,
        valid_from: form.valid_from, valid_until: form.valid_until,
        synthetic_department_id: form.synthetic_department_id ? Number(form.synthetic_department_id) : null,
        connection_ids: form.connection_ids,
        budget_scopes: form.scopes.map((key) => {
          const [cloud_scope, budget_id] = key.split("::");
          return { cloud_scope, budget_id };
        }),
        active: true,
      });
      setOpen(false); setForm(blank); load();
    } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível salvar o contrato"); }
  };

  return <Box>
    {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 2 }}>
      <Box color="text.secondary">Contratos que optam pelo compromisso orçamentário por ID Orçamento.</Box>
      <Button variant="contained" startIcon={<Add />} onClick={() => setOpen(true)}>Novo contrato</Button>
    </Box>
    <Table size="small">
      <TableHead><TableRow><TableCell>Contrato</TableCell><TableCell>Vigência</TableCell><TableCell>Conexões</TableCell><TableCell>IDs Orçamento</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
      <TableBody>{contracts.map((contract) => <TableRow key={contract.id}>
        <TableCell><strong>{contract.name}</strong><br /><small>{contract.code}</small></TableCell>
        <TableCell>{contract.valid_from} — {contract.valid_until}</TableCell>
        <TableCell>{contract.connection_ids.length}</TableCell>
        <TableCell>{contract.budget_scopes.map((scope) => <Chip key={`${scope.cloud_scope}-${scope.budget_id}`} size="small" sx={{ mr: .5 }} label={`${scope.cloud_scope}/${scope.budget_id}`} />)}</TableCell>
        <TableCell><Chip size="small" color={contract.active ? "success" : "default"} label={contract.active ? "Ativo" : "Inativo"} /></TableCell>
      </TableRow>)}</TableBody>
    </Table>
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
      <DialogTitle>Novo contrato financeiro</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2, pt: "16px !important" }}>
        <TextField label="Código" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
        <TextField label="Nome" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Box sx={{ display: "flex", gap: 1 }}><TextField fullWidth label="Válido de" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} /><TextField fullWidth label="Válido até" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} /></Box>
        <FormControl><InputLabel>Conexões</InputLabel><Select multiple value={form.connection_ids} input={<OutlinedInput label="Conexões" />} onChange={(e) => setForm({ ...form, connection_ids: e.target.value as number[] })} renderValue={(selected) => (selected as number[]).map((id) => connections.find((c) => c.id === id)?.name || id).join(", ")}>{connections.map((connection) => <MenuItem key={connection.id} value={connection.id}><Checkbox checked={form.connection_ids.includes(connection.id)} /><ListItemText primary={connection.name} /></MenuItem>)}</Select></FormControl>
        <FormControl><InputLabel>IDs Orçamento</InputLabel><Select multiple value={form.scopes} input={<OutlinedInput label="IDs Orçamento" />} onChange={(e) => setForm({ ...form, scopes: e.target.value as string[] })} renderValue={(selected) => (selected as string[]).join(", ")}>{scopes.map((scope) => { const key = `${scope.cloud_scope}::${scope.budget_id}`; return <MenuItem key={key} value={key}><Checkbox checked={form.scopes.includes(key)} /><ListItemText primary={`${scope.cloud_scope}/${scope.budget_id}`} secondary={`${scope.project_count || 0} projetos no organograma`} /></MenuItem>; })}</Select></FormControl>
        <FormControl><InputLabel>Departamento sintético (opcional)</InputLabel><Select value={form.synthetic_department_id} label="Departamento sintético (opcional)" onChange={(e) => setForm({ ...form, synthetic_department_id: String(e.target.value) })}><MenuItem value="">Nenhum — não criar realizado sem consumo</MenuItem>{departments.map((department) => <MenuItem key={department.id} value={department.id}>{department.name}</MenuItem>)}</Select></FormControl>
      </DialogContent>
      <DialogActions><Button onClick={() => setOpen(false)}>Cancelar</Button><Button variant="contained" onClick={save} disabled={!form.code || !form.name || !form.connection_ids.length || !form.scopes.length}>Salvar</Button></DialogActions>
    </Dialog>
  </Box>;
}
