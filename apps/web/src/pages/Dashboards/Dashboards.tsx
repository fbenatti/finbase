import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Box, Button, FormControl, InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material";
import { DndContext, DragEndEvent, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import EmptyState from "../../components/EmptyState";
import SavedReportsGrid, { reportVisualization } from "../../components/SavedReportsGrid";
import { Add, DashboardIcon, Delete, Edit, Save } from "../../components/FinbaseIcons";
import { useMenuSearchParams as useSearchParams } from "../../navigation/useMenuSearchParams";
import {
  Dashboard, DestinationAnalysis, PivotView, deleteDashboard, deleteDashboardWidget, formatApiError,
  getAnalysisDestinations, getDashboards, getPivotViews, saveDashboard, saveDashboardWidget,
} from "../../services/api";
import DashboardWidgetCard, { LaidOutWidget } from "./DashboardWidgetCard";
import { GRID_COLUMNS, GRID_GAP, ROW_HEIGHT, chartTypeFor, layoutWidgets, newWidgetPosition } from "./dashboardLayout";

const PIVOT_PATH = "/dados/explorador/tabelas-dinamicas";

/** Painéis montados com relatórios salvos do pivot. */
export default function Dashboards() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [reports, setReports] = useState<PivotView[]>([]);
  const [destinations, setDestinations] = useState<DestinationAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<LaidOutWidget[]>([]);
  const [removed, setRemoved] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [nameDialog, setNameDialog] = useState<{ id?: number; value: string } | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const requestedId = Number(searchParams.get("painel")) || undefined;
  const selected = dashboards.find(item => item.id === requestedId) || dashboards[0];
  const reportsById = useMemo(() => new Map(reports.map(report => [report.id, report])), [reports]);
  const widgets = editing ? draft : layoutWidgets(selected?.widgets || []);

  const load = useCallback(async () => {
    try {
      const [nextDashboards, nextReports, nextDestinations] = await Promise.all([getDashboards(), getPivotViews(), getAnalysisDestinations()]);
      setDashboards(nextDashboards);
      setReports(nextReports);
      setDestinations(nextDestinations);
      setError("");
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível carregar os painéis"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const selectDashboard = (id: number) => setSearchParams(current => { const next = new URLSearchParams(current); next.set("painel", String(id)); return next; }, { replace: true });
  const startEditing = () => { setDraft(layoutWidgets(selected?.widgets || [])); setRemoved([]); setEditing(true); };
  const cancelEditing = () => { setEditing(false); setDraft([]); setRemoved([]); };
  const updateDraft = (id: number, change: (widget: LaidOutWidget) => LaidOutWidget) => setDraft(current => current.map(widget => widget.id === id ? change(widget) : widget));

  const addWidget = (report: PivotView) => {
    setDraft(current => [...current, {
      id: -Date.now(), dashboard_id: selected?.id || 0, pivot_view_id: report.id, title: report.name,
      chart_type: chartTypeFor(reportVisualization(report)), config: {}, position: newWidgetPosition(current.length),
    }]);
    setPickerOpen(false);
  };
  const removeWidget = (widget: LaidOutWidget) => {
    setDraft(current => current.filter(item => item.id !== widget.id));
    if (widget.id > 0) setRemoved(current => [...current, widget.id]);
  };
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    setDraft(current => arrayMove(current, current.findIndex(item => item.id === active.id), current.findIndex(item => item.id === over.id)));
  };

  const saveLayout = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await Promise.all(removed.map(id => deleteDashboardWidget(selected.id, id)));
      // Sequential so a failure leaves a predictable prefix saved; dashboards are small.
      for (const [order, widget] of draft.entries()) {
        await saveDashboardWidget(selected.id, {
          pivot_view_id: widget.pivot_view_id, title: widget.title, chart_type: widget.chart_type,
          position: { ...widget.position, order },
        }, widget.id > 0 ? widget.id : undefined);
      }
      await load();
      cancelEditing();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível salvar o painel"));
    } finally {
      setSaving(false);
    }
  };

  const submitName = async () => {
    if (!nameDialog?.value.trim()) return;
    try {
      const saved = await saveDashboard(nameDialog.value.trim(), nameDialog.id);
      setNameDialog(null);
      await load();
      selectDashboard(saved.id);
      if (!nameDialog.id) { setDraft([]); setRemoved([]); setEditing(true); }
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível gravar o painel. Verifique se já existe um painel com esse nome."));
    }
  };
  const removeDashboard = async () => {
    if (!selected) return;
    try {
      await deleteDashboard(selected.id);
      setConfirmDelete(false);
      cancelEditing();
      setSearchParams(current => { const next = new URLSearchParams(current); next.delete("painel"); return next; }, { replace: true });
      await load();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível excluir o painel"));
      setConfirmDelete(false);
    }
  };

  return <Box>
    <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, mb: 2, flexWrap: "wrap" }}>
      <Box>
        <Typography variant="h6">Painéis</Typography>
        <Typography variant="body2" color="text.secondary">Junte relatórios salvos em um painel. Em modo de edição, arraste os widgets para reordenar e ajuste o tamanho de cada um.</Typography>
      </Box>
      <Button size="small" variant="outlined" startIcon={<Add />} onClick={() => setNameDialog({ value: "" })} disabled={editing} sx={{ flexShrink: 0 }}>Novo painel</Button>
    </Box>
    {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

    {!loading && !dashboards.length && <Paper variant="outlined">
      <EmptyState message="Nenhum painel criado ainda." icon={<DashboardIcon sx={{ fontSize: 64, mb: 2, opacity: 0.4 }} />} />
      <Box sx={{ textAlign: "center", pb: 4 }}><Button variant="contained" startIcon={<Add />} onClick={() => setNameDialog({ value: "" })}>Criar painel</Button></Box>
    </Paper>}

    {selected && <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 260 }}>
          <InputLabel>Painel</InputLabel>
          <Select label="Painel" value={selected.id} disabled={editing} onChange={e => selectDashboard(Number(e.target.value))}>
            {dashboards.map(item => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
          </Select>
        </FormControl>
        <Box sx={{ flex: 1 }} />
        {!editing && <>
          <Button size="small" startIcon={<Edit />} onClick={startEditing}>Editar painel</Button>
          <Button size="small" onClick={() => setNameDialog({ id: selected.id, value: selected.name })}>Renomear</Button>
          <Button size="small" color="error" startIcon={<Delete />} onClick={() => setConfirmDelete(true)}>Excluir</Button>
        </>}
        {editing && <>
          <Button size="small" startIcon={<Add />} onClick={() => setPickerOpen(true)}>Adicionar widget</Button>
          <Button size="small" onClick={cancelEditing} disabled={saving}>Cancelar</Button>
          <Button size="small" variant="contained" startIcon={<Save />} onClick={() => { void saveLayout(); }} disabled={saving}>{saving ? "Salvando..." : "Salvar painel"}</Button>
        </>}
      </Stack>
    </Paper>}

    {selected && !widgets.length && <Paper variant="outlined" sx={{ p: 4, textAlign: "center" }}>
      <Typography color="text.secondary" mb={1.5}>Este painel ainda não tem widgets.</Typography>
      <Button variant="contained" startIcon={<Add />} onClick={() => { if (!editing) startEditing(); setPickerOpen(true); }}>Adicionar widget</Button>
    </Paper>}

    {selected && widgets.length > 0 && <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={widgets.map(widget => widget.id)} strategy={rectSortingStrategy}>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: `repeat(${GRID_COLUMNS}, minmax(0, 1fr))` }, gridAutoRows: `${ROW_HEIGHT}px`, gap: `${GRID_GAP}px` }}>
          {widgets.map(widget => <DashboardWidgetCard
            key={widget.id}
            widget={widget}
            report={widget.pivot_view_id ? reportsById.get(widget.pivot_view_id) : undefined}
            editing={editing}
            onResize={position => updateDraft(widget.id, item => ({ ...item, position: { ...item.position, ...position } }))}
            onVisualization={visualization => updateDraft(widget.id, item => ({ ...item, chart_type: chartTypeFor(visualization) }))}
            onRemove={() => removeWidget(widget)}
            onOpenReport={report => navigate(`${PIVOT_PATH}?relatorio=${report.id}`)}
          />)}
        </Box>
      </SortableContext>
    </DndContext>}

    <Dialog open={pickerOpen} onClose={() => setPickerOpen(false)} fullWidth maxWidth="lg">
      <DialogTitle>Adicionar widget</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" mb={1.5}>Escolha um relatório salvo. O widget começa com a visualização do relatório; você pode trocá-la depois.</Typography>
        {reports.length
          ? <SavedReportsGrid reports={reports} destinations={destinations} onOpen={addWidget} openLabel="Adicionar ao painel" />
          : <Alert severity="info" action={<Button size="small" onClick={() => navigate(PIVOT_PATH)}>Abrir o pivot</Button>}>Nenhum relatório salvo. Monte um relatório no pivot e salve-o para usá-lo aqui.</Alert>}
      </DialogContent>
      <DialogActions><Button onClick={() => setPickerOpen(false)}>Fechar</Button></DialogActions>
    </Dialog>

    <Dialog open={!!nameDialog} onClose={() => setNameDialog(null)} fullWidth maxWidth="xs">
      <DialogTitle>{nameDialog?.id ? "Renomear painel" : "Novo painel"}</DialogTitle>
      <DialogContent>
        <TextField autoFocus fullWidth size="small" label="Nome do painel" sx={{ mt: 1 }} value={nameDialog?.value || ""}
          onChange={e => setNameDialog(current => current && { ...current, value: e.target.value })}
          onKeyDown={e => { if (e.key === "Enter") void submitName(); }} />
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setNameDialog(null)}>Cancelar</Button>
        <Button variant="contained" disabled={!nameDialog?.value.trim()} onClick={() => { void submitName(); }}>{nameDialog?.id ? "Salvar" : "Criar"}</Button>
      </DialogActions>
    </Dialog>

    <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} fullWidth maxWidth="xs">
      <DialogTitle>Excluir painel</DialogTitle>
      <DialogContent><Typography variant="body2">Excluir o painel “{selected?.name}” e seus widgets? Os relatórios continuam salvos.</Typography></DialogContent>
      <DialogActions>
        <Button onClick={() => setConfirmDelete(false)}>Cancelar</Button>
        <Button color="error" variant="contained" onClick={() => { void removeDashboard(); }}>Excluir</Button>
      </DialogActions>
    </Dialog>
  </Box>;
}
