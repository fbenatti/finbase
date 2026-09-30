import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, FormControl, InputLabel, ListItemText, MenuItem, OutlinedInput,
  Select, Table, TableBody, TableCell, TableHead, TableRow, TextField,
  FormHelperText, Typography,
} from "@mui/material";
import { Add } from "@mui/icons-material";
import {
  createFinancialContract, FinancialContract, FinancialContractScope,
  getConnections, getDepartments, getFinancialContractScopes, getFinancialContracts,
  getFinancialContractSettlements, updateFinancialContract,
  getFinancialContractsOverview, recalculateFinancialContract,
} from "../../services/api";

const blank = { code: "", name: "", valid_from: "202601", valid_until: "202612", currency: "BRL", synthetic_department_id: "", connection_ids: [] as number[], scopes: [] as string[] };

export default function FinancialContracts() {
  const [contracts, setContracts] = useState<FinancialContract[]>([]);
  const [scopes, setScopes] = useState<FinancialContractScope[]>([]);
  const [connections, setConnections] = useState<{ id: number; name: string; code: string }[]>([]);
  const [departments, setDepartments] = useState<{ id: number; name: string; id_orcamento?: string | null }[]>([]);
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(blank);
  const [error, setError] = useState("");
  const [settlementMessage, setSettlementMessage] = useState("");
  const [selectedContract, setSelectedContract] = useState<number | null>(null);
  const [settlements, setSettlements] = useState<Record<string, unknown>[]>([]);
  const [overview, setOverview] = useState<Record<string, unknown>[]>([]);
  const [recalculating, setRecalculating] = useState(false);

  const load = () => { getFinancialContracts().then(setContracts).catch((e) => setError(String(e))); getFinancialContractsOverview().then(setOverview).catch(() => {}); };
  useEffect(() => {
    load();
    getFinancialContractScopes().then(setScopes).catch(() => {});
    getConnections().then((rows) => setConnections(rows as typeof connections)).catch(() => {});
    getDepartments(true).then((rows) => setDepartments(rows as typeof departments)).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedContract == null) { setSettlements([]); return; }
    getFinancialContractSettlements(selectedContract).then(setSettlements).catch((e) => setError(String(e)));
  }, [selectedContract]);

  const save = async () => {
    try {
      const payload = {
        code: form.code, name: form.name, currency: form.currency,
        valid_from: form.valid_from, valid_until: form.valid_until,
        synthetic_department_id: form.synthetic_department_id ? Number(form.synthetic_department_id) : null,
        connection_ids: form.connection_ids,
        budget_scopes: form.scopes.map((key) => {
          const [cloud_scope, budget_id] = key.split("::");
          return { cloud_scope, budget_id };
        }),
        active: true,
      };
      if (editingId == null) await createFinancialContract(payload);
      else await updateFinancialContract(editingId, payload);
      setOpen(false); setEditingId(null); setForm(blank); load();
    } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível salvar o contrato"); }
  };

  return <Box>
    {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 2 }}>
      <Box color="text.secondary">Contratos que optam pelo compromisso orçamentário por ID Orçamento.</Box>
      <Button variant="contained" startIcon={<Add />} onClick={() => { setEditingId(null); setForm(blank); setOpen(true); }}>Novo contrato</Button>
    </Box>
    {overview.length > 0 && <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 1.5, mb: 2 }}>{overview.map((item) => <Box key={String(item.contract_id)} sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, p: 1.5 }}><Typography variant="caption" color="text.secondary">{String(item.name)}</Typography><Typography variant="h6">Realizado: {String(item.realized || "0")}</Typography><Typography variant="body2">Crédito: {String(item.credit_balance || "0")}</Typography><Typography variant="body2">Overage: {String(item.uncovered_overage || "0")}</Typography></Box>)}</Box>}
    <Table size="small">
      <TableHead><TableRow><TableCell>Contrato</TableCell><TableCell>Vigência</TableCell><TableCell>Conexões</TableCell><TableCell>IDs Orçamento</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
      <TableBody>{contracts.map((contract) => <TableRow key={contract.id}>
        <TableCell><strong>{contract.name}</strong><br /><small>{contract.code}</small></TableCell>
        <TableCell>{contract.valid_from} — {contract.valid_until}</TableCell>
        <TableCell>{contract.connection_ids.length}</TableCell>
        <TableCell>{contract.budget_scopes.map((scope) => <Chip key={`${scope.cloud_scope}-${scope.budget_id}`} size="small" sx={{ mr: .5 }} label={`${scope.cloud_scope}/${scope.budget_id}`} />)}</TableCell>
        <TableCell><Chip size="small" color={contract.active ? "success" : "default"} label={contract.active ? "Ativo" : "Inativo"} /><Button size="small" sx={{ ml: 1 }} onClick={() => setSelectedContract(contract.id)}>Movimentações</Button><Button size="small" onClick={() => setSelectedContract(contract.id)}>Recalcular</Button><Button size="small" onClick={() => { setEditingId(contract.id); setForm({ code: contract.code, name: contract.name, valid_from: contract.valid_from, valid_until: contract.valid_until, currency: contract.currency, synthetic_department_id: contract.synthetic_department_id ? String(contract.synthetic_department_id) : "", connection_ids: contract.connection_ids, scopes: contract.budget_scopes.map((item) => `${item.cloud_scope}::${item.budget_id}`) }); setOpen(true); }}>Editar</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table>
    {selectedContract != null && <Box sx={{ mt: 3 }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h6">Movimentações da liquidação</Typography>
        <Button size="small" variant="outlined" disabled={recalculating} onClick={async () => {
          setRecalculating(true); setError(""); setSettlementMessage("");
          try {
            const result = await recalculateFinancialContract(selectedContract, { recalculate_all: true });
            const refreshed = await getFinancialContractSettlements(selectedContract);
            setSettlements(refreshed); load();
            setSettlementMessage(`Run ${String(result.run_id)} publicado para ${String(result.period_from)}–${String(result.period_until)}${result.idempotent_reuse ? " (sem alterações nos dados)" : ""}.`);
          } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível recalcular o contrato"); }
          finally { setRecalculating(false); }
        }}>{recalculating ? "Recalculando…" : "Recalcular contrato"}</Button>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Valores do razão por contrato, cloud, ID Orçamento e competência. O realizado só aparece quando existe consumo positivo ou overage descoberto.</Typography>
      {settlementMessage && <Alert severity="success" sx={{ my: 1 }}>{settlementMessage}</Alert>}
      <Table size="small"><TableHead><TableRow>{["Competência", "Cloud / ID", "Orçamento", "Consumo", "Realizado", "Ajuste", "Crédito gerado", "Crédito usado", "Overage descoberto", "Saldo final", "Run / origem / data", "Versões orçamento"].map((label) => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead><TableBody>{settlements.map((row, index) => <TableRow key={`${row.period}-${row.budget_id}-${index}`}><TableCell>{String(row.period || "—")}</TableCell><TableCell>{String(row.cloud_scope || "—")} / {String(row.budget_id || "—")}</TableCell><TableCell>{String(row.commitment || "0")}</TableCell><TableCell>{String(row.consumption || "0")}</TableCell><TableCell>{String(row.realized || "0")}</TableCell><TableCell>{String(row.contractual_adjustment || "0")}</TableCell><TableCell>{String(row.credit_generated || "0")}</TableCell><TableCell>{String(row.credit_used || "0")}</TableCell><TableCell>{String(row.uncovered_overage || "0")}</TableCell><TableCell>{String(row.closing_credit || "0")}</TableCell><TableCell>{`${String(row.run_id || "—")} / ${String(row.run_source || "—")} / ${String(row.run_finished_at || "—")}`}</TableCell><TableCell>{Array.isArray(row.budget_version_ids) ? row.budget_version_ids.map(String).join(", ") : "—"}</TableCell></TableRow>)}</TableBody></Table>
      {!settlements.length && <Typography color="text.secondary" sx={{ py: 2 }}>Nenhuma liquidação publicada para este contrato.</Typography>}
    </Box>}
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
      <DialogTitle>{editingId == null ? "Novo contrato financeiro" : "Editar contrato financeiro"}</DialogTitle>
      <DialogContent sx={{ display: "grid", gap: 2, pt: "16px !important" }}>
        <TextField label="Código" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
        <TextField label="Nome" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Box sx={{ display: "flex", gap: 1 }}><TextField fullWidth label="Válido de" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} /><TextField fullWidth label="Válido até" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} /></Box>
        <FormControl><InputLabel>Conexões</InputLabel><Select multiple value={form.connection_ids} input={<OutlinedInput label="Conexões" />} onChange={(e) => setForm({ ...form, connection_ids: e.target.value as number[] })} renderValue={(selected) => (selected as number[]).map((id) => connections.find((c) => c.id === id)?.name || id).join(", ")}>{connections.map((connection) => <MenuItem key={connection.id} value={connection.id}><Checkbox checked={form.connection_ids.includes(connection.id)} /><ListItemText primary={connection.name} /></MenuItem>)}</Select></FormControl>
        <FormControl><InputLabel>IDs Orçamento</InputLabel><Select multiple value={form.scopes} input={<OutlinedInput label="IDs Orçamento" />} onChange={(e) => setForm({ ...form, scopes: e.target.value as string[] })} renderValue={(selected) => (selected as string[]).join(", ")}>{scopes.map((scope) => { const key = `${scope.cloud_scope}::${scope.budget_id}`; return <MenuItem key={key} value={key}><Checkbox checked={form.scopes.includes(key)} /><ListItemText primary={`${scope.cloud_scope}/${scope.budget_id}`} secondary={`${scope.project_count || 0} projetos no organograma`} /></MenuItem>; })}</Select></FormControl>
        <FormControl><InputLabel>Departamento sintético (opcional)</InputLabel><Select value={form.synthetic_department_id} label="Departamento sintético (opcional)" onChange={(e) => setForm({ ...form, synthetic_department_id: String(e.target.value) })}><MenuItem value="">Nenhum — não criar realizado sem consumo</MenuItem>{departments.map((department) => <MenuItem key={department.id} value={department.id}>{department.name}</MenuItem>)}</Select><FormHelperText>Usado apenas para lançar o compromisso quando o pool tiver orçamento, mas nenhum consumo positivo. Não representa um projeto real e não deve ser informado quando o realizado deve seguir somente a Nota Fiscal.</FormHelperText></FormControl>
      </DialogContent>
      <DialogActions><Button onClick={() => setOpen(false)}>Cancelar</Button><Button variant="contained" onClick={save} disabled={!form.code || !form.name || !form.connection_ids.length || !form.scopes.length}>Salvar</Button></DialogActions>
    </Dialog>
  </Box>;
}
