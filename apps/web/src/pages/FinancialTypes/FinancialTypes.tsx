import { useEffect, useState } from "react";
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Stack, Switch, TextField, Typography } from "@mui/material";
import { Add, Edit } from "@mui/icons-material";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import { createFinancialType, FinancialType, getFinancialTypes, updateFinancialType } from "../../services/api";

const blank = { code: "", name: "", active: true };

export default function FinancialTypes() {
  const [rows, setRows] = useState<FinancialType[]>([]); const [error, setError] = useState("");
  const [open, setOpen] = useState(false); const [editing, setEditing] = useState<FinancialType | null>(null); const [form, setForm] = useState(blank);
  const load = () => getFinancialTypes().then(setRows).catch(e => setError(String(e)));
  useEffect(() => { void load(); }, []);
  const edit = (row?: FinancialType) => { setEditing(row || null); setForm(row ? { code: row.code, name: row.name, active: row.active } : blank); setOpen(true); };
  const save = async () => { try { if (editing) await updateFinancialType(editing.id, { name: form.name, active: form.active }); else await createFinancialType(form); setOpen(false); load(); } catch (e) { setError(String(e)); } };
  const columns: GridColDef[] = [
    { field: "code", headerName: "Código", flex: 1 }, { field: "name", headerName: "Nome", flex: 1.4 },
    { field: "active", headerName: "Ativa", width: 100, renderCell: p => p.value ? "Sim" : "Não" },
    { field: "actions", headerName: "", width: 80, sortable: false, renderCell: p => <IconButton onClick={() => edit(p.row)}><Edit /></IconButton> },
  ];
  return <Box><Stack direction="row" justifyContent="space-between" mb={2}><Box><Typography variant="h4">Tipificações financeiras</Typography><Typography color="text.secondary">Tipos usados nas séries realizadas, projetadas e cargas manuais.</Typography></Box><Button variant="contained" startIcon={<Add />} onClick={() => edit()}>Nova tipificação</Button></Stack>
    {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    <Box height={520}><DataGrid rows={rows} columns={columns} disableRowSelectionOnClick /></Box>
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm"><DialogTitle>{editing ? "Editar tipificação" : "Nova tipificação"}</DialogTitle><DialogContent><Stack gap={2} mt={1}><TextField label="Código técnico" value={form.code} disabled={!!editing} helperText="Minúsculas, números e hífen; não pode ser alterado depois." onChange={e => setForm({ ...form, code: e.target.value })}/><TextField label="Nome" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}/><Stack direction="row" alignItems="center">Ativa <Switch checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })}/></Stack></Stack></DialogContent><DialogActions><Button onClick={() => setOpen(false)}>Cancelar</Button><Button variant="contained" disabled={!form.name || !form.code} onClick={() => void save()}>Salvar</Button></DialogActions></Dialog>
  </Box>;
}
