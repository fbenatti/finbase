import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import DataGrid from "../../components/FinbaseDataGrid";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Divider, FormControl, IconButton, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack, Switch, TextField, Tooltip, Typography } from "@mui/material";
import { Add, Delete, Edit, Refresh, Search } from "../../components/FinbaseIcons";
import { GridColDef } from "@mui/x-data-grid";
import {
  CloneRule, DestinationTable, SourceConnection, createCloneRule, deleteCloneRule, getClonePendencies,
  getCloneRules, getConnections, getDestinationSchema, getDestinationTables, updateCloneRule,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import { RowAction } from "../../components/RowAction";

type Condition = { field: string; op: string; value: string };
type Change = { field: string; operation: string; value: string };

const operations = ["=", "!=", "IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "EMPTY", "NOT_EMPTY", ">", "<"];
const transformations = ["SET", "ZERO", "NEGATE", "ABS", "COPY_COLUMN", "HISTORICAL_ON_DEMAND_COST"];
const roleLabels: Record<string, string> = {
  meter_id: "ID do medidor",
  unit_price: "Preço por unidade",
  quantity: "Quantidade",
  exchange_rate: "Câmbio",
  pricing_model: "Tipo de preço",
  billed_cost: "Custo faturado (BilledCost)",
  effective_cost: "Custo efetivo (EffectiveCost)",
};
const roleDefaults: Record<string, string> = {
  meter_id: "source_meter_id",
  unit_price: "source_unit_price",
  quantity: "ConsumedQuantity",
  exchange_rate: "source_exchange_rate",
  pricing_model: "source_pricing_model",
  billed_cost: "BilledCost",
  effective_cost: "EffectiveCost",
};

export default function CloneRules({ embedded }: { embedded?: boolean } = {}) {
  const [tables, setTables] = useState<DestinationTable[]>([]);
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [tableId, setTableId] = useState<number | "">("");
  const [rules, setRules] = useState<CloneRule[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [pending, setPending] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deletePending, setDeletePending] = useState<CloneRule | null>(null);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("Nova regra de clonagem");
  const [description, setDescription] = useState("");
  const [connectionId, setConnectionId] = useState<number | "">("");
  const [provider, setProvider] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [priority, setPriority] = useState(10);
  const [validFrom, setValidFrom] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>(roleDefaults);
  const [originalChanges, setOriginalChanges] = useState<Change[]>([]);
  const [cloneChanges, setCloneChanges] = useState<Change[]>([{ field: "ChargeCategory", operation: "SET", value: "Return" }]);

  const available = useMemo(() => [...new Set([...columns, ...Object.values(roles).filter(Boolean)])], [columns, roles]);

  const load = async (id = tableId) => {
    if (!id) return;
    setLoading(true);
    try {
      const [r, p, s] = await Promise.all([getCloneRules(Number(id)), getClonePendencies(Number(id)), getDestinationSchema(Number(id))]);
      setRules(r);
      setPending(p);
      setColumns(s.columns.map((x) => x.name));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    Promise.all([getDestinationTables(true), getConnections(), getCloneRules()])
      .then(([t, c, existingRules]) => {
        setTables(t);
        setConnections(c);
        const tableWithRules = t.find((table) => existingRules.some((rule) => rule.destination_table_id === table.id));
        if (tableWithRules) setTableId(tableWithRules.id);
        else if (t.length) setTableId(t[0].id);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    void load();
  }, [tableId]);

  const reset = () => {
    setEditingId(null);
    setName("Nova regra de clonagem");
    setDescription("");
    setConnectionId("");
    setProvider("");
    setEnabled(true);
    setPriority(10);
    setValidFrom("");
    setValidUntil("");
    setConditions([]);
    setRoles(roleDefaults);
    setOriginalChanges([]);
    setCloneChanges([{ field: "ChargeCategory", operation: "SET", value: "Return" }]);
  };

  const addCondition = () => setConditions((v) => [...v, { field: "", op: "=", value: "" }]);
  const setCondition = (i: number, k: keyof Condition, v: string) => setConditions((all) => all.map((x, n) => (n === i ? { ...x, [k]: v } : x)));
  const addChange = (setter: React.Dispatch<React.SetStateAction<Change[]>>) => setter((v) => [...v, { field: "", operation: "SET", value: "" }]);
  const setChange = (setter: React.Dispatch<React.SetStateAction<Change[]>>, i: number, k: keyof Change, v: string) => setter((all) => all.map((x, n) => (n === i ? { ...x, [k]: v } : x)));
  const serializeChanges = (items: Change[]) => Object.fromEntries(items.filter((x) => x.field).map((x) => [x.field, { operation: x.operation, value: x.value }]));

  const save = async () => {
    if (!tableId) {
      setError("Selecione a tabela de destino.");
      return;
    }
    const missing = Object.entries(roles).filter(([, v]) => !v).map(([k]) => roleLabels[k]);
    if (missing.length) {
      setError("Preencha os papéis obrigatórios: " + missing.join(", "));
      return;
    }
    const body: Record<string, unknown> = {
      destination_table_id: Number(tableId),
      connection_id: connectionId === "" ? null : Number(connectionId),
      name,
      description: description || null,
      provider: provider || null,
      active: enabled,
      priority,
      valid_from: validFrom || null,
      valid_until: validUntil || null,
      conditions: conditions
        .filter((x) => x.field && (x.value || ["EMPTY", "NOT_EMPTY"].includes(x.op)))
        .map((x) => ({ ...x, value: ["IN", "NOT_IN"].includes(x.op) ? x.value.split(",").map((v) => v.trim()).filter(Boolean) : x.value })),
      roles: { ...roles, on_demand_value: "OnDemand" },
      original_changes: serializeChanges(originalChanges),
      clone_changes: serializeChanges(cloneChanges),
    };
    try {
      if (editingId) await updateCloneRule(editingId, body);
      else await createCloneRule(body);
      reset();
      setDialogOpen(false);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const editRule = (r: CloneRule) => {
    setEditingId(r.id);
    setName(r.name);
    setDescription(r.description || "");
    setConnectionId(r.connection_id || "");
    setProvider(r.provider || "");
    setEnabled(r.active);
    setPriority(r.priority);
    setValidFrom(r.valid_from || "");
    setValidUntil(r.valid_until || "");
    setConditions(
      (r.conditions || []).map((c) => ({
        field: String(c.field || ""),
        op: String(c.op || "="),
        value: Array.isArray(c.value) ? c.value.join(", ") : String(c.value ?? ""),
      })),
    );
    setRoles({ ...roleDefaults, ...r.roles });
    const parse = (obj: Record<string, unknown>) =>
      Object.entries(obj || {}).map(([field, v]) =>
        typeof v === "object" && v
          ? { field, operation: String((v as Record<string, unknown>).operation || "SET"), value: String((v as Record<string, unknown>).value ?? "") }
          : { field, operation: "SET", value: String(v ?? "") },
      );
    setOriginalChanges(parse(r.original_changes || {}));
    setCloneChanges(parse(r.clone_changes || {}));
  };

  const openNew = () => {
    reset();
    setDialogOpen(true);
  };
  const openEdit = (r: CloneRule) => {
    editRule(r);
    setDialogOpen(true);
  };
  const remove = async () => {
    if (!deletePending) return;
    try {
      await deleteCloneRule(deletePending.id);
      setDeletePending(null);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const tableName = (id?: number | null) => tables.find((t) => t.id === id)?.name || ("Tabela nº" + String(id ?? "?"));

  const filtered = rules.filter((r) =>
    [r.name, r.description || "", r.provider || ""].join(" ").toLowerCase().includes(search.trim().toLowerCase()),
  );

  const gridColumns: GridColDef[] = [
    { field: "name", headerName: "Nome", flex: 1, minWidth: 220 },
    { field: "table", headerName: "Tabela de destino", width: 210, valueGetter: (_v, row) => tableName((row as CloneRule).destination_table_id) },
    {
      field: "scope",
      headerName: "Abrangência",
      width: 160,
      valueGetter: (_v, row) => {
        const r = row as CloneRule;
        return r.provider || (r.connection_id ? "conexão " + r.connection_id : "Global");
      },
    },
    { field: "priority", headerName: "Prioridade", width: 110, type: "number" },
    { field: "version", headerName: "Revisão", width: 90, type: "number" },
    {
      field: "active",
      headerName: "Ativa",
      width: 90,
      renderCell: ({ value }) => <Chip size="small" color={value ? "success" : "default"} label={value ? "Sim" : "Não"} />,
    },
    {
      field: "actions",
      headerName: "Opções",
      width: 120,
      sortable: false,
      filterable: false,
      renderCell: ({ row }) => (
        <Stack direction="row">
          <Tooltip title="Editar">
            <RowAction kind="edit" onClick={() => openEdit(row as CloneRule)} />
          </Tooltip>
          <Tooltip title="Remover regra">
            <RowAction kind="delete" onClick={() => setDeletePending(row as CloneRule)} />
          </Tooltip>
        </Stack>
      ),
    },
  ];

  const ChangeEditor = ({ title, items, setter }: { title: string; items: Change[]; setter: React.Dispatch<React.SetStateAction<Change[]>> }) => (
    <Box>
      <Typography fontWeight={600}>{title}</Typography>
      <Typography variant="caption" color="text.secondary">Transformações declarativas: sem SQL, sem Python e sem fórmula livre.</Typography>
      {items.map((x, i) => (
        <Stack key={i} direction="row" gap={1} mt={1} flexWrap="wrap">
          <Select size="small" value={x.field} displayEmpty onChange={(e) => setChange(setter, i, "field", String(e.target.value))} sx={{ minWidth: 210 }}>
            <MenuItem value="">Coluna</MenuItem>
            {available.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </Select>
          <Select size="small" value={x.operation} onChange={(e) => setChange(setter, i, "operation", String(e.target.value))} sx={{ minWidth: 200 }}>
            {transformations.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
          </Select>
          {!["ZERO", "NEGATE", "ABS", "HISTORICAL_ON_DEMAND_COST"].includes(x.operation) && (
            <TextField
              size="small"
              label={x.operation === "COPY_COLUMN" ? "Coluna da fonte" : "Valor"}
              value={x.value}
              onChange={(e) => setChange(setter, i, "value", e.target.value)}
              select={x.operation === "COPY_COLUMN"}
              sx={{ minWidth: 210 }}
            >
              {x.operation === "COPY_COLUMN" && available.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
            </TextField>
          )}
          <Button color="error" onClick={() => setter((all) => all.filter((_, n) => n !== i))}>Remover</Button>
        </Stack>
      ))}
      <Button size="small" startIcon={<Add />} onClick={() => addChange(setter)} sx={{ mt: 1 }}>Incluir alteração</Button>
    </Box>
  );

  return (
    <Box sx={{
      "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 },
      "& .MuiInputLabel-root:not(.MuiInputLabel-shrink)": { transform: "translate(14px, 11px) scale(1)" },
    }}>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      <Alert severity="info" sx={{ mb: 2 }}>
        <Typography variant="subtitle2" fontWeight={700} gutterBottom>Para que servem estas regras?</Typography>
        <Typography variant="body2" component="div">
          Na fatura Azure, linhas de <strong>uso (Usage)</strong> cobertas por <strong>Reserva</strong> ou <strong>Savings Plan</strong> frequentemente chegam com custo zerado — o compromisso já foi pago em outro lançamento. Sem esse tratamento, dashboards e pivots subestimam o consumo real da cloud.
          <br /><br />
          No reprocessamento da base consolidada, cada regra elegível reconstrói o <strong>preço sob demanda (OnDemand) de referência</strong> na linha original e cria uma linha sintética de <strong>estorno (Return)</strong> com valor negativo. O total financeiro se mantém; o que muda é a visibilidade do uso real para análise FinOps.
          <br /><br />
          Templates editáveis para tabelas de destino são instalados automaticamente (ex.: <em>Uso de reserva Azure → devolução</em>). Se não houver preço On-Demand histórico para o medidor, a linha vai para <strong>pendências</strong> — nada é alterado em silêncio.
        </Typography>
      </Alert>

      <Paper variant="outlined" sx={{ p: { xs: 1.5, md: 2.5 }, bgcolor: "background.paper" }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1.5} sx={{ mb: 2 }}>
          <Box>
            <Typography variant="h6">Compromissos de tarifa (RI e Savings Plan)</Typography>
            <Typography variant="body2" color="text.secondary">
              Ajustes de estorno sob demanda (OnDemand) para uso coberto por compromisso — por tabela de destino.
            </Typography>
          </Box>
          <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
            <FormControl size="small" sx={{ minWidth: 250 }}>
              <InputLabel>Tabela de destino</InputLabel>
              <Select value={tableId} label="Tabela de destino" onChange={(e) => { setTableId(Number(e.target.value)); reset(); }}>
                {tables.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField
              size="small"
              placeholder="Buscar por nome ou tabela..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{ startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment> }}
              sx={{ minWidth: 240 }}
            />
            <Button variant="contained" startIcon={<Add />} onClick={openNew}>Nova regra de clonagem</Button>
            <Button variant="text" startIcon={<Refresh />} onClick={() => void load()}>Recarregar dados</Button>
          </Stack>
        </Stack>

        {loading ? (
          <Stack alignItems="center" py={6}><CircularProgress /></Stack>
        ) : filtered.length === 0 ? (
          <Box sx={{ py: 7 }}>
            <EmptyState
              message={search
                ? "Nenhuma regra de clonagem encontrada para a busca."
                : "Nenhuma regra cadastrada para esta tabela. Clique em 'Nova regra de clonagem' para começar."}
            />
          </Box>
        ) : (
          <DataGrid
            rows={filtered}
            columns={gridColumns}
            autoHeight
            disableColumnMenu
            getRowId={(row) => (row as CloneRule).id}
            sx={{ border: 0 }}
          />
        )}

        <Divider sx={{ my: 2 }} />
        <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
          <Typography variant="subtitle2">Pendências de preço sob demanda</Typography>
          <Chip label={pending.length + " pendências"} size="small" color={pending.length ? "warning" : "default"} />
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
          Uma linha sem preço histórico compatível não é alterada nem gera clone.
        </Typography>
      </Paper>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editingId ? "Editar regra de clonagem" : "Nova regra de clonagem"}</DialogTitle>
        <DialogContent dividers>
          <Stack gap={2} sx={{ mt: 1 }}>
            <Stack direction="row" gap={2} flexWrap="wrap">
              <FormControl sx={{ minWidth: 250 }}>
                <InputLabel>Tabela de destino</InputLabel>
                <Select value={tableId} label="Tabela de destino" onChange={(e) => setTableId(Number(e.target.value))}>
                  {tables.map((t) => <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField label="Nome da regra" value={name} onChange={(e) => setName(e.target.value)} sx={{ minWidth: 230 }} />
              <TextField label="Finalidade" value={description} onChange={(e) => setDescription(e.target.value)} sx={{ minWidth: 260 }} />
              <FormControl sx={{ minWidth: 210 }}>
                <InputLabel>Conexão (abrangência)</InputLabel>
                <Select value={connectionId} label="Conexão (abrangência)" onChange={(e) => setConnectionId(e.target.value === "" ? "" : Number(e.target.value))}>
                  <MenuItem value="">Todas as fontes</MenuItem>
                  {connections.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField label="Provedor (abrangência)" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="azure" />
              <TextField type="number" label="Prioridade" value={priority} onChange={(e) => setPriority(Number(e.target.value))} sx={{ width: 120 }} />
              <TextField label="Início da vigência" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} placeholder="AAAAMM" sx={{ width: 140 }} />
              <TextField label="Fim da vigência" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} placeholder="AAAAMM" sx={{ width: 140 }} />
              <Stack direction="row" alignItems="center">
                <Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                <Typography>Em uso</Typography>
              </Stack>
            </Stack>

            <Divider />
            <Typography fontWeight={600}>Condições de seleção (todas devem valer)</Typography>
            {conditions.map((x, i) => (
              <Stack key={i} direction="row" gap={1} mt={1} flexWrap="wrap">
                <Select size="small" value={x.field} displayEmpty onChange={(e) => setCondition(i, "field", String(e.target.value))} sx={{ minWidth: 230 }}>
                  <MenuItem value="">Coluna</MenuItem>
                  {columns.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                </Select>
                <Select size="small" value={x.op} onChange={(e) => setCondition(i, "op", String(e.target.value))}>
                  {operations.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
                </Select>
                {!["EMPTY", "NOT_EMPTY"].includes(x.op) && (
                  <TextField size="small" label={x.op.includes("IN") ? "Valores (vírgula)" : "Valor"} value={x.value} onChange={(e) => setCondition(i, "value", e.target.value)} sx={{ minWidth: 250 }} />
                )}
                <Button color="error" onClick={() => setConditions((all) => all.filter((_, n) => n !== i))}>Remover</Button>
              </Stack>
            ))}
            <Button size="small" startIcon={<Add />} onClick={addCondition} sx={{ alignSelf: "flex-start" }}>Incluir condição</Button>

            <Divider />
            <Typography fontWeight={600}>Papéis de negócio</Typography>
            <Stack direction="row" gap={1} flexWrap="wrap" mt={1}>
              {Object.entries(roleLabels).map(([key, label]) => (
                <FormControl key={key} size="small" sx={{ minWidth: 220 }}>
                  <InputLabel>{label}</InputLabel>
                  <Select value={roles[key] || ""} label={label} onChange={(e) => setRoles((v) => ({ ...v, [key]: String(e.target.value) }))}>
                    {available.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                  </Select>
                </FormControl>
              ))}
            </Stack>

            <Divider />
            <Stack direction={{ xs: "column", md: "row" }} gap={3}>
              <Box flex={1}><ChangeEditor title="Alterações na linha de origem" items={originalChanges} setter={setOriginalChanges} /></Box>
              <Box flex={1}><ChangeEditor title="Alterações na linha clonada" items={cloneChanges} setter={setCloneChanges} /></Box>
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDialogOpen(false)}>Cancelar</Button>
          <Button variant="contained" startIcon={editingId ? <Edit /> : <Add />} onClick={() => void save()}>
            {editingId ? "Salvar nova versão" : "Criar regra de clonagem"}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(deletePending)} onClose={() => setDeletePending(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Excluir esta regra de clonagem?</DialogTitle>
        <DialogContent>
          <Typography>
            Você está prestes a excluir <strong>{deletePending?.name}</strong>. Esta ação não pode ser desfeita.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setDeletePending(null)}>Cancelar</Button>
          <Button variant="contained" color="error" onClick={() => void remove()}>Remover regra de clonagem</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
