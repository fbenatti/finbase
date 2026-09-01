import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton,
  InputLabel, MenuItem, Select, Stack, TextField, Tooltip, Typography,
} from "@mui/material";
import { Add, Block, Check, Delete, Edit } from "@mui/icons-material";
import { DataGrid, GridColDef, GridRenderEditCellParams, GridRowModel } from "@mui/x-data-grid";
import {
  ForecastAck, activateForecastAck, deactivateForecastAck, deleteForecastAck, getForecastAcks, updateForecastAck,
} from "../../services/api";
import AckRegisterDialog from "./AckRegisterDialog";

const money = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const ACK_CATEGORIES = ["Pico sazonal", "Migração", "Projeto novo", "Erro de precificação", "Outro"];
const ACK_SEVERITIES = ["Informação", "Perigo", "Crítico", "Sucesso"];

const severityColor = (severity?: string): "info" | "warning" | "error" | "success" => {
  switch ((severity || "").toLowerCase()) {
    case "perigo": return "warning";
    case "crítico":
    case "critico": return "error";
    case "sucesso": return "success";
    default: return "info";
  }
};
const SEVERITY_ORDER: Record<string, number> = {
  "crítico": 0, "critico": 0, "perigo": 1, "informação": 2, "informacao": 2, "sucesso": 3,
};

const prettyMonthLabel = (p: string) => p && p.length === 6
  ? `${["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"][Number(p.slice(4, 6)) - 1]}/${p.slice(2, 4)}`
  : p || "—";

function SeverityEditCell(props: GridRenderEditCellParams) {
  const { id, field, value, api } = props;
  return (
    <Select
      size="small" autoFocus fullWidth
      value={String(value ?? "Informação")}
      onChange={(e) => { api.setEditCellValue({ id, field, value: String(e.target.value) }); api.stopCellEditMode({ id, field }); }}
      onKeyDown={(e) => { if (e.key === "Escape") api.stopCellEditMode({ id, field }); }}
    >
      {ACK_SEVERITIES.map(sev => <MenuItem key={sev} value={sev}>{sev}</MenuItem>)}
    </Select>
  );
}

function CategoryEditCell(props: GridRenderEditCellParams) {
  const { id, field, value, api } = props;
  return (
    <Select
      size="small" autoFocus fullWidth
      value={String(value ?? ACK_CATEGORIES[0])}
      onChange={(e) => { api.setEditCellValue({ id, field, value: String(e.target.value) }); api.stopCellEditMode({ id, field }); }}
      onKeyDown={(e) => { if (e.key === "Escape") api.stopCellEditMode({ id, field }); }}
    >
      {ACK_CATEGORIES.map(cat => <MenuItem key={cat} value={cat}>{cat}</MenuItem>)}
    </Select>
  );
}

export default function AcksRegisteredTab({ defaultMonth, department }: { defaultMonth: string; department: string }) {
  const [month, setMonth] = useState(defaultMonth);
  const [acks, setAcks] = useState<ForecastAck[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [registerOpen, setRegisterOpen] = useState(false);
  const [message, setMessage] = useState("");
  // Edição de um Ack existente.
  const [editTarget, setEditTarget] = useState<ForecastAck | null>(null);
  const [editCategory, setEditCategory] = useState(ACK_CATEGORIES[0]);
  const [editSeverity, setEditSeverity] = useState(ACK_SEVERITIES[0]);
  const [editOrdem, setEditOrdem] = useState(0);
  const [editDate, setEditDate] = useState("");
  const [editReason, setEditReason] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  // Confirmação de desativação.
  const [deactivateTarget, setDeactivateTarget] = useState<ForecastAck | null>(null);
  const [togglingActive, setTogglingActive] = useState(false);
  // Exclusão do registro (departamento).
  const [deleteTarget, setDeleteTarget] = useState<ForecastAck | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async (monthKey: string, dept: string) => {
    setLoading(true);
    setError("");
    try {
      const result = await getForecastAcks(monthKey || undefined, dept || undefined);
      setAcks(result.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Recarrega quando muda o mês ou o departamento selecionado.
  useEffect(() => {
    const monthKey = month ? month.slice(0, 7).replace("-", "") : "";
    void load(monthKey, department);
  }, [month, department, load]);

  // O mês é derivado da data selecionada (YYYYMM) — mesma regra do mês realizado.
  const monthKey = month ? month.slice(0, 7).replace("-", "") : "";

  const openEdit = (ack: ForecastAck) => {
    setEditTarget(ack);
    setEditCategory(ack.category || ACK_CATEGORIES[0]);
    setEditSeverity(ack.severity || ACK_SEVERITIES[0]);
    setEditOrdem(Number(ack.ordem ?? SEVERITY_ORDER[(ack.severity || "").toLowerCase()] ?? 0));
    setEditDate(ack.registered_at ? ack.registered_at.slice(0, 10) : "");
    setEditReason(ack.reason || "");
  };

  const saveEdit = async () => {
    if (!editTarget || !editReason.trim()) return;
    setSavingEdit(true);
    setError("");
    try {
      await updateForecastAck(editTarget.id, {
        category: editCategory, reason: editReason.trim(),
        severity: editSeverity, ordem: editOrdem, registered_at: editDate || undefined,
      });
      setMessage(`Ack de ${editTarget.department_name} editado — versão anterior preservada no histórico.`);
      setEditTarget(null);
      await load(monthKey, department);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao salvar Ack");
    } finally {
      setSavingEdit(false);
    }
  };

  const toggleActive = async () => {
    if (!deactivateTarget) return;
    setTogglingActive(true);
    setError("");
    try {
      const target = deactivateTarget;
      if (target.active === false) {
        await activateForecastAck(target.id);
        setMessage(`Ack de ${target.department_name} reativado.`);
      } else {
        await deactivateForecastAck(target.id);
        setMessage(`Ack de ${target.department_name} desativado — não aparece mais nos dashboards.`);
      }
      setDeactivateTarget(null);
      await load(monthKey, department);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao alterar o Ack");
    } finally {
      setTogglingActive(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setError("");
    try {
      await deleteForecastAck(deleteTarget.id);
      setMessage(`Registro de ${deleteTarget.department_name} excluído.`);
      setDeleteTarget(null);
      await load(monthKey, department);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao excluir o Ack");
    } finally {
      setDeleting(false);
    }
  };

  // Merge: acks vinculados a desvios identificados (run_id preenchido, criados
  // pelo botão "Ack" nas abas Desvios/Top 5) não se repetem aqui. Esta lista
  // exibe somente itens manuais (cadastro livre sem run_id).
  const manualAcks = acks.filter(ack => ack.run_id == null);
  // Estrutura de ordem: campo `ordem` (menor primeiro), depois severidade e data.
  const orderedAcks = [...manualAcks].sort((a, b) => {
    const oa = Number(a.ordem ?? SEVERITY_ORDER[(a.severity || "").toLowerCase()] ?? 2);
    const ob = Number(b.ordem ?? SEVERITY_ORDER[(b.severity || "").toLowerCase()] ?? 2);
    if (oa !== ob) return oa - ob;
    const sa = SEVERITY_ORDER[(a.severity || "").toLowerCase()] ?? 2;
    const sb = SEVERITY_ORDER[(b.severity || "").toLowerCase()] ?? 2;
    if (sa !== sb) return sa - sb;
    const da = a.registered_at || a.created_at || "";
    const db = b.registered_at || b.created_at || "";
    return da < db ? 1 : da > db ? -1 : 0;
  });

  // Persistência da edição de célula (onCellEdit) via API.
  const processRowUpdate = async (newRow: GridRowModel, oldRow: GridRowModel) => {
    const next = newRow as ForecastAck;
    const prev = oldRow as ForecastAck;
    const changed: { category?: string; reason?: string; severity?: string; ordem?: number } = {};
    if ((next.reason ?? "") !== (prev.reason ?? "")) changed.reason = next.reason ?? "";
    if ((next.category ?? "") !== (prev.category ?? "")) changed.category = next.category || ACK_CATEGORIES[0];
    if ((next.severity ?? "") !== (prev.severity ?? "")) changed.severity = next.severity || ACK_SEVERITIES[0];
    if (Number(next.ordem ?? 0) !== Number(prev.ordem ?? 0)) changed.ordem = Number(next.ordem ?? 0);
    if (Object.keys(changed).length === 0) return next;
    try {
      // category/reason são obrigatórios no contrato; enviamos os valores atuais
      // (inalterados) e os campos realmente editados.
      const body: { category: string; reason: string; severity?: string; ordem?: number } = {
        category: next.category || ACK_CATEGORIES[0],
        reason: next.reason ?? "",
      };
      if (changed.severity) body.severity = changed.severity;
      if (changed.ordem !== undefined) body.ordem = changed.ordem;
      const updated = await updateForecastAck(next.id, body);
      setMessage(`Ack de ${next.department_name} atualizado.`);
      return updated;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao atualizar o Ack");
      return prev;
    }
  };
  const onProcessRowUpdateError = (e: unknown) => setError(e instanceof Error ? e.message : "Falha ao atualizar o Ack");

  const columns = useMemo<GridColDef[]>(() => [
    {
      field: "ordem", headerName: "Ordem", width: 90, type: "number", editable: true,
      align: "center", headerAlign: "center",
    },
    {
      field: "department_name", headerName: "Departamento", minWidth: 210, flex: 1.1,
      renderCell: ({ row }) => (
        <Stack justifyContent="center" sx={{ minWidth: 0, height: "100%" }}>
          <Typography variant="body2" fontWeight={600} noWrap>{row.department_name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>{row.department_path.join(" > ")}</Typography>
        </Stack>
      ),
    },
    { field: "reason", headerName: "Motivo", minWidth: 230, flex: 1.4, editable: true },
    {
      field: "severity", headerName: "Severidade", width: 140, editable: true,
      renderCell: ({ row }) => <Chip size="small" color={severityColor(row.severity)} label={row.severity || "Informação"} />,
      renderEditCell: (params) => <SeverityEditCell {...params} />,
    },
    {
      field: "category", headerName: "Categoria", width: 165, editable: true,
      renderEditCell: (params) => <CategoryEditCell {...params} />,
    },
    {
      field: "target_month", headerName: "Mês de referência", width: 150, align: "right", headerAlign: "right",
      valueGetter: (_v, row) => row.target_month,
      valueFormatter: (value) => prettyMonthLabel(String(value ?? "")),
    },
    {
      field: "active", headerName: "Status", width: 115,
      renderCell: ({ row }) => row.active === false
        ? <Chip size="small" label="Desativado" color="error" variant="outlined" />
        : <Chip size="small" label="Ativo" color="success" variant="outlined" />,
    },
    {
      field: "actions", headerName: "Ações", width: 140, sortable: false, filterable: false,
      align: "center", headerAlign: "center",
      renderCell: ({ row }) => (
        <Stack direction="row" alignItems="center" justifyContent="center" sx={{ height: "100%" }}>
          <Tooltip title="Editar Ack">
            <IconButton size="small" aria-label={`Editar Ack de ${row.department_name}`} onClick={() => openEdit(row)}>
              <Edit fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={row.active === false ? "Reativar Ack" : "Desativar Ack"}>
            <IconButton
              size="small"
              color={row.active === false ? "success" : "error"}
              aria-label={row.active === false ? `Reativar Ack de ${row.department_name}` : `Desativar Ack de ${row.department_name}`}
              onClick={() => setDeactivateTarget(row)}
            >
              {row.active === false ? <Check fontSize="small" /> : <Block fontSize="small" />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Excluir registro (departamento)">
            <IconButton size="small" color="error" aria-label={`Excluir registro de ${row.department_name}`} onClick={() => setDeleteTarget(row)}>
              <Delete fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ], []);

  return (
    <Box>
      {message && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage("")}>{message}</Alert>}
      <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }} flexWrap="wrap">
        <TextField
          label="Data (mês)"
          type="date"
          value={month}
          onChange={e => setMonth(e.target.value)}
          InputLabelProps={{ shrink: true }}
          sx={{ width: 200 }}
          helperText="Pode ser futura — o mês segue a regra do mês realizado (YYYYMM)."
        />
        <Button size="small" variant="outlined" onClick={() => void load(monthKey, department)}>Atualizar</Button>
        <Chip size="small" variant="outlined" label={`Mês: ${monthKey || "—"}`} />
        <Box sx={{ flexGrow: 1 }} />
        <Button variant="outlined" size="small" startIcon={<Add fontSize="small" />} onClick={() => setRegisterOpen(true)}>
          Cadastrar item
        </Button>
      </Stack>
      {department && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Filtrado pelo departamento selecionado: <b>{department}</b>
        </Typography>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      <Alert severity="info" sx={{ mb: 2 }}>
        Esta lista mostra somente <strong>itens manuais</strong> (cadastrados via "Cadastrar item"), para explicar
        algo que não foi identificado como desvio. Acks vinculados a desvios identificados são justificados
        diretamente nas abas <strong>Desvios</strong> e no <strong>Top 5</strong> — não se repetem aqui.
      </Alert>
      {!loading && acks.length > manualAcks.length && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {acks.length - manualAcks.length} ack(s) vinculado(s) a desvios identificados ocultos desta lista (aparecem nas abas Desvios / Top 5).
        </Typography>
      )}
      {loading ? (
        <Typography variant="body2" color="text.secondary">Carregando acks registrados…</Typography>
      ) : manualAcks.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Nenhum item manual registrado para o mês {monthKey || "selecionado"}
          {department ? ` e departamento ${department}` : ""}. Use "Cadastrar item" para explicar um desvio
          que não foi identificado automaticamente.
        </Typography>
      ) : (
        <DataGrid
          rows={orderedAcks}
          columns={columns}
          getRowId={(row) => row.id}
          autoHeight
          disableColumnMenu
          hideFooter
          density="compact"
          editMode="cell"
          processRowUpdate={processRowUpdate}
          onProcessRowUpdateError={onProcessRowUpdateError}
          getRowClassName={(params) => params.row.active === false ? "ack-row-inactive" : ""}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 2,
            bgcolor: "background.paper",
            "& .MuiDataGrid-columnHeaders": { bgcolor: "#f8fafc", borderBottomColor: "divider" },
            "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus": { outline: "none" },
            "& .ack-row-inactive": { opacity: 0.55 },
          }}
        />
      )}

      <AckRegisterDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        defaultDepartment={department}
        onSaved={(monthKeySaved, departmentName) => {
          setMessage(`Item cadastrado para ${departmentName} (mês ${monthKeySaved}).`);
          setMonth(`${monthKeySaved.slice(0, 4)}-${monthKeySaved.slice(4, 6)}-01`);
          void load(monthKeySaved, department);
        }}
      />

      {/* Edição de Ack */}
      <Dialog open={editTarget !== null} onClose={() => setEditTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Editar Ack — {editTarget?.department_name}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {editTarget?.department_path.join(" > ")}
          </Typography>
          <TextField
            fullWidth label="Data do registro" type="date" sx={{ mb: 2 }}
            value={editDate}
            onChange={e => setEditDate(e.target.value)}
            InputLabelProps={{ shrink: true }}
            helperText="O mês do item é derivado desta data (YYYYMM)."
          />
          <FormControl fullWidth sx={{ mb: 2 }}>
            <InputLabel>Categoria</InputLabel>
            <Select label="Categoria" value={editCategory} onChange={e => setEditCategory(e.target.value)}>
              {ACK_CATEGORIES.map(cat => <MenuItem key={cat} value={cat}>{cat}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl fullWidth sx={{ mb: 2 }}>
            <InputLabel>Severidade</InputLabel>
            <Select label="Severidade" value={editSeverity} onChange={e => setEditSeverity(e.target.value)}>
              {ACK_SEVERITIES.map(sev => <MenuItem key={sev} value={sev}>{sev}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            fullWidth label="Ordem (menor = aparece primeiro)" type="number" size="small" sx={{ mb: 2 }}
            value={editOrdem}
            onChange={e => setEditOrdem(Number(e.target.value) || 0)}
            helperText="Usada para ordenar os highlights."
          />
          <TextField
            fullWidth multiline minRows={3} label="Motivo do desvio"
            value={editReason} onChange={e => setEditReason(e.target.value)}
            placeholder="Ex.: migração de workloads anunciada após o fechamento do forecast..."
          />
          {editTarget?.history && editTarget.history.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="caption" color="text.secondary" fontWeight={700}>Histórico de edições:</Typography>
              {editTarget.history.map((h, index) => (
                <Typography key={index} variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  • {h.category}: {h.reason} <Box component="span" sx={{ opacity: 0.7 }}>(em {new Date(h.changed_at).toLocaleString("pt-BR")})</Box>
                </Typography>
              ))}
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditTarget(null)}>Cancelar</Button>
          <Button variant="contained" disabled={savingEdit || !editReason.trim()} onClick={() => void saveEdit()}>
            {savingEdit ? "Salvando..." : "Salvar edição"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirmação desativar/reativar */}
      <Dialog open={deactivateTarget !== null} onClose={() => { if (!togglingActive) setDeactivateTarget(null); }} maxWidth="xs" fullWidth>
        <DialogTitle>{deactivateTarget?.active === false ? "Reativar Ack?" : "Desativar Ack?"}</DialogTitle>
        <DialogContent>
          {deactivateTarget?.active === false ? (
            <Typography>
              Reativar o Ack de <strong>{deactivateTarget.department_name}</strong>? Ele voltará a aparecer nos dashboards.
            </Typography>
          ) : (
            <Typography>
              Desativar o Ack de <strong>{deactivateTarget?.department_name}</strong>? Ele deixará de aparecer nos
              dashboards (Ex.: Visão Esperada) sem perder o registro nem o histórico.
            </Typography>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={togglingActive} onClick={() => setDeactivateTarget(null)}>Cancelar</Button>
          <Button
            variant="contained"
            color={deactivateTarget?.active === false ? "success" : "error"}
            disabled={togglingActive}
            onClick={() => void toggleActive()}
          >
            {togglingActive ? "Alterando..." : deactivateTarget?.active === false ? "Reativar" : "Desativar"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirmação de exclusão do registro (departamento) */}
      <Dialog open={deleteTarget !== null} onClose={() => { if (!deleting) setDeleteTarget(null); }} maxWidth="xs" fullWidth>
        <DialogTitle>Excluir registro?</DialogTitle>
        <DialogContent>
          <Typography>
            Excluir o registro de <strong>{deleteTarget?.department_name}</strong>
            {deleteTarget?.department_path ? ` (${deleteTarget.department_path.join(" > ")})` : ""}? Essa ação é
            definitiva e remove também o histórico de edições.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button disabled={deleting} onClick={() => setDeleteTarget(null)}>Cancelar</Button>
          <Button variant="contained" color="error" disabled={deleting} onClick={() => void confirmDelete()}>
            {deleting ? "Excluindo..." : "Excluir"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
