import React, { useEffect, useState } from "react";
import {
  Box, Button, Chip, Dialog, DialogContent, DialogTitle, Drawer, FormControl, IconButton,
  InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography, Tooltip,
} from "@mui/material";
import { Refresh as RefreshIcon, FilterAlt as FilterIcon } from "@mui/icons-material";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import { api } from "../../services/api";

interface AuditItem {
  id: number; created_at: string;
  user: { id: number | null; username: string | null };
  action: string; method: string; module: string;
  entity_label: string | null;
  changes: Array<{ field: string; old: unknown; new: unknown }> | null;
  has_full_snapshot: boolean;
}

const ACTION_COLOR: Record<string, "success" | "primary" | "error" | "default"> = {
  create: "success", update: "primary", delete: "error", execute: "default",
};

export default function AuditPage() {
  const [items, setItems] = useState<AuditItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [username, setUsername] = useState("");
  const [module, setModule] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const [detail, setDetail] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = (p = page) => {
    setLoading(true); setError(null);
    const params: Record<string, string | number> = { limit: 50, offset: p * 50 };
    if (from) params.from = from;
    if (to) params.to = to;
    if (username) params.user = username;
    if (module) params.module = module;
    if (action) params.action_label = action;
    api.get("/audit/logs", { params })
      .then((r) => { setItems(r.data.items); setTotal(r.data.total); })
      .catch((e) => setError(e?.response?.data?.detail || "Erro ao carregar auditoria"))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(0); }, []);

  const openDetail = async (id: number) => {
    try {
      setDetail((await api.get(`/audit/logs/${id}`)).data);
      setOpen(true);
    } catch (e) { setError(String(e)); }
  };

  const columns: GridColDef[] = [
    { field: "id", headerName: "ID", width: 70 },
    { field: "created_at", headerName: "Data/Hora", width: 170, valueGetter: (v) => v ? new Date(v as string).toLocaleString("pt-BR") : "" },
    { field: "user", headerName: "Usuário", width: 130, valueGetter: (_, row: any) => row.user?.username ?? "—" },
    { field: "module", headerName: "Módulo", width: 130 },
    { field: "action", headerName: "Ação", width: 120, renderCell: ({ row }: any) => (
      <Chip size="small" label={row.action} color={ACTION_COLOR[row.action] || "default"} variant="outlined" />
    ) },
    { field: "method", headerName: "Método", width: 90 },
    { field: "entity_label", headerName: "Registro", width: 220 },
    { field: "detail", headerName: "", width: 60, sortable: false, filterable: false,
      renderCell: ({ row }: any) => (
        <Tooltip title="Ver detalhe"><IconButton size="small" onClick={() => openDetail(row.id)}>▸</IconButton></Tooltip>
      ) },
  ];

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h5">Auditoria</Typography>
        <Box flex={1} />
        <Button size="small" variant="outlined" startIcon={<FilterIcon />} onClick={() => load(0)}>Aplicar filtros</Button>
        <IconButton size="small" onClick={() => load(0)}><RefreshIcon /></IconButton>
      </Stack>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={1.5} flexWrap="wrap">
          <TextField size="small" label="De" type="datetime-local" value={from}
            slotProps={{ inputLabel: { shrink: true } }} onChange={(e) => setFrom(e.target.value)} />
          <TextField size="small" label="Até" type="datetime-local" value={to}
            slotProps={{ inputLabel: { shrink: true } }} onChange={(e) => setTo(e.target.value)} />
          <TextField size="small" label="Usuário" value={username} onChange={(e) => setUsername(e.target.value)} />
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Módulo</InputLabel>
            <Select label="Módulo" value={module} onChange={(e) => setModule(e.target.value)}>
              <MenuItem value="">todos</MenuItem>
              {["connections","rules","etl","analysis","forecast","planning","reservations","savings","periods","destinations","schema","fact","csv_upload","clone_rules","dimon","ai_copilot","ai_dashboards","ai_provider_dashboards","azure_ptu","azure_resource_graph","azure_tag_policy","oci_operational","scheduled_shutdown","auth","users","groups","roles","audit"].map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Ação</InputLabel>
            <Select label="Ação" value={action} onChange={(e) => setAction(e.target.value)}>
              <MenuItem value="">todas</MenuItem>
              {["create","update","delete","execute","login","logout","failed"].map((a) => <MenuItem key={a} value={a}>{a}</MenuItem>)}
            </Select>
          </FormControl>
        </Stack>
      </Paper>
      {error && <Typography color="error" sx={{ mb: 1 }}>{error}</Typography>}
      <Paper sx={{ height: 520 }}>
        <DataGrid
          rows={items} columns={columns} loading={loading}
          rowCount={total} paginationMode="server" pageSizeOptions={[50]}
          paginationModel={{ page, pageSize: 50 }}
          onPaginationModelChange={(m) => { setPage(m.page); load(m.page); }}
          disableRowSelectionOnClick
          sx={{ border: 0 }}
        />
      </Paper>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          Evento #{detail?.id ?? ""}
          <Box flex={1} />
          <Chip size="small" label={detail?.module} />
          <Chip size="small" label={detail?.action} color={ACTION_COLOR[detail?.action] || "default"} />
        </DialogTitle>
        <DialogContent>
          {detail && (
            <>
              <Typography variant="body2" sx={{ mb: 1 }}>
                {detail.user?.username ?? "—"} · {detail.created_at ? new Date(detail.created_at).toLocaleString("pt-BR") : ""} · {detail.method} {detail.path}
                {detail.entity_type ? ` · ${detail.entity_type}#${detail.entity_id}` : ""}
              </Typography>
              {detail.changes && detail.changes.map((c: any, i: number) => (
                <Typography key={i} variant="body2"><b>{c.field}</b>: {JSON.stringify(c.old)} → {JSON.stringify(c.new)}</Typography>
              ))}
              {detail.changes?.length === 0 && <Typography variant="body2">Sem mudanças campo a campo.</Typography>}
              <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 2 }}>
                <Box flex={1} sx={{ bgcolor: "#fafafa", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 300 }}>
                  <Typography variant="caption" fontWeight={700}>Snapshot antes</Typography>
                  <pre style={{ fontSize: 11, margin: 0 }}>{JSON.stringify(detail.before_json ?? null, null, 2)}</pre>
                </Box>
                <Box flex={1} sx={{ bgcolor: "#f0f7ff", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 300 }}>
                  <Typography variant="caption" fontWeight={700}>Snapshot depois</Typography>
                  <pre style={{ fontSize: 11, margin: 0 }}>{JSON.stringify(detail.after_json ?? null, null, 2)}</pre>
                </Box>
              </Stack>
              <Typography variant="caption" color="text.secondary">
                IP: {detail.ip} · {detail.user_agent} · request {detail.request_id}
              </Typography>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
