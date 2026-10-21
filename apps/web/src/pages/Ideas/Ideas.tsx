import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert, Box, Button, Chip, Stack, Typography,
} from "@mui/material";
import { Add, ThumbUp } from "@mui/icons-material";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import { useAuth } from "../../auth/AuthContext";
import { listIdeas, voteIdea, type Idea } from "../../services/ideasApi";
import { IMPACT_LABELS, STATUS_COLOR, STATUS_LABELS, TYPE_LABELS, VOTABLE_TYPES } from "./constants";
import IdeaDetail from "./IdeaDetail";
import IdeaFormDialog from "./IdeaFormDialog";
import CanvasTab from "./CanvasTab";
import DashboardTab from "./DashboardTab";

const TABS = [
  { id: "portal", label: "Portal" },
  { id: "backlog", label: "Backlog" },
  { id: "canvas", label: "Canvas" },
  { id: "dashboard", label: "Dashboard" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function Ideas() {
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("ideas", "create");
  const [searchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  const tab: TabId = TABS.some((t) => t.id === requested) ? (requested as TabId) : "portal";

  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(async () => {
    try {
      setIdeas(await listIdeas());
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);

  const portalCols: GridColDef[] = [
    { field: "title", headerName: "Ideia", flex: 1, minWidth: 200 },
    { field: "idea_type", headerName: "Tipo", width: 120, renderCell: (p) => <Chip size="small" label={TYPE_LABELS[p.value as keyof typeof TYPE_LABELS] || p.value} /> },
    { field: "status", headerName: "Status", width: 140, renderCell: (p) => <Chip size="small" color={STATUS_COLOR[p.value as keyof typeof STATUS_COLOR]} label={STATUS_LABELS[p.value as keyof typeof STATUS_LABELS] || p.value} /> },
    { field: "impact", headerName: "Impacto", width: 100, valueGetter: (_, row: Idea) => IMPACT_LABELS[row.impact] || row.impact },
    { field: "votes_count", headerName: "Votos", width: 80 },
    { field: "priority_score", headerName: "Score", width: 80 },
    {
      field: "vote", headerName: "", width: 90, sortable: false,
      renderCell: (p) => VOTABLE_TYPES.includes(p.row.idea_type) && canCreate ? (
        <Button size="small" startIcon={<ThumbUp />} onClick={(e) => { e.stopPropagation(); void voteIdea(p.row.id).then(refresh); }}>
          Votar
        </Button>
      ) : null,
    },
  ];

  const backlogIdeas = [...ideas]
    .filter((i) => i.status === "BACKLOG" || (i.status === "EM_VOTACAO" && VOTABLE_TYPES.includes(i.idea_type)))
    .sort((a, b) => b.priority_score - a.priority_score || (a.due_date || "").localeCompare(b.due_date || ""));

  const backlogCols: GridColDef<Idea>[] = [
    ...portalCols.filter((c) => c.field !== "vote"),
    { field: "due_date", headerName: "Prazo", width: 110 },
    { field: "difficulty", headerName: "Dific.", width: 80 },
  ];

  return (
    <Box sx={{ mx: -3, mt: -3, pb: 3, minHeight: "calc(100vh - 64px)", bgcolor: "#f8fafc" }}>
      <Box sx={{ px: 3, pt: 3, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Stack direction="row" justifyContent="space-between" alignItems="start" sx={{ mb: 2 }}>
          <Box>
            <Typography variant="h4">Ideias</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Portal de discovery, backlog priorizado, canvas de entrega e dashboard de ciclo até produção.
            </Typography>
          </Box>
          {canCreate && (
            <Button variant="contained" startIcon={<Add />} onClick={() => setDialog(true)}>Nova ideia</Button>
          )}
        </Stack>
      </Box>

      <Box sx={{ p: 3 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

        {tab === "portal" && (
          <Box sx={{ height: 520 }}>
            <DataGrid rows={ideas} columns={portalCols} getRowId={(r) => r.id}
              onRowClick={(p) => setDetailId(p.row.id)} disableRowSelectionOnClick />
          </Box>
        )}

        {tab === "backlog" && (
          <Box sx={{ height: 520 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              Ordenado por score de prioridade (votos × impacto) e prazo.
            </Typography>
            <DataGrid rows={backlogIdeas} columns={backlogCols} getRowId={(r) => r.id}
              onRowClick={(p) => setDetailId(p.row.id)} disableRowSelectionOnClick />
          </Box>
        )}

        {tab === "canvas" && <CanvasTab onOpen={setDetailId} refreshKey={refreshKey} />}
        {tab === "dashboard" && <DashboardTab onOpen={setDetailId} refreshKey={refreshKey} />}
      </Box>

      <IdeaFormDialog open={dialog} onClose={() => setDialog(false)} onCreated={refresh} />
      <IdeaDetail ideaId={detailId} open={detailId !== null} onClose={() => setDetailId(null)} onUpdated={refresh} />
    </Box>
  );
}
