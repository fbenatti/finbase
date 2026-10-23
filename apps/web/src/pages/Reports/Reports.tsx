import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Box, Button, Paper, Typography } from "@mui/material";
import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import SavedReportsGrid from "../../components/SavedReportsGrid";
import { Add } from "../../components/FinbaseIcons";
import { DestinationAnalysis, PivotView, deletePivotView, formatApiError, getAnalysisDestinations, getPivotViews } from "../../services/api";

const PIVOT_PATH = "/dados/explorador/tabelas-dinamicas";

/** Relatórios salvos no pivot: abrir, excluir e criar novos. */
export default function Reports() {
  const navigate = useNavigate();
  const [reports, setReports] = useState<PivotView[]>([]);
  const [destinations, setDestinations] = useState<DestinationAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<PivotView | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [views, tables] = await Promise.all([getPivotViews(), getAnalysisDestinations()]);
      setReports(views);
      setDestinations(tables);
      setError("");
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível carregar os relatórios"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const openReport = useCallback((report: PivotView) => navigate(`${PIVOT_PATH}?relatorio=${report.id}`), [navigate]);
  const confirmDelete = async () => {
    if (!pendingDelete) return;
    try {
      await deletePivotView(pendingDelete.id);
      setPendingDelete(null);
      await load();
    } catch (e: unknown) {
      setError(formatApiError(e, "Não foi possível excluir o relatório"));
      setPendingDelete(null);
    }
  };

  return <Box>
    <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, mb: 2 }}>
      <Box>
        <Typography variant="h6">Relatórios salvos</Typography>
        <Typography variant="body2" color="text.secondary">Relatórios montados no pivot. Abra um relatório para ajustá-lo ou use-o como widget em um painel.</Typography>
      </Box>
      <Button size="small" variant="contained" startIcon={<Add />} onClick={() => navigate(PIVOT_PATH)} sx={{ flexShrink: 0 }}>Novo relatório</Button>
    </Box>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Paper variant="outlined">
      <SavedReportsGrid reports={reports} destinations={destinations} loading={loading} onOpen={openReport} onDelete={setPendingDelete} />
    </Paper>
    <Dialog open={!!pendingDelete} onClose={() => setPendingDelete(null)} maxWidth="xs" fullWidth>
      <DialogTitle>Excluir relatório</DialogTitle>
      <DialogContent>
        <Typography variant="body2">Excluir “{pendingDelete?.name}”? Os painéis que usam este relatório deixarão de mostrar esse widget.</Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setPendingDelete(null)}>Cancelar</Button>
        <Button color="error" variant="contained" onClick={() => { void confirmDelete(); }}>Excluir</Button>
      </DialogActions>
    </Dialog>
  </Box>;
}
