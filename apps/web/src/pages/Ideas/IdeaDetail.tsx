import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { dataDisplayText } from "../../utils/dataPresentation";
import { useEffect, useState } from "react";
import { Alert, Autocomplete, Box, Button, Chip, Divider, FormControl, InputLabel, MenuItem, Select, Stack, TextField, Typography } from "@mui/material";
import { ThumbDown, ThumbUp } from "@mui/icons-material";
import RecordHistory from "../../components/RecordHistory";
import { useAuth } from "../../auth/AuthContext";
import { listUsers, type AdminUser } from "../../services/adminApi";
import {
  addIdeaComment, getIdea, getIdeaTimeline, listIdeaComments,
  setIdeaAssignees, transitionIdea, unvoteIdea, voteIdea,
  type Idea, type IdeaComment, type IdeaStatus, type IdeaTimeline,
} from "../../services/ideasApi";
import { IMPACT_LABELS, STATUS_COLOR, STATUS_LABELS, TRANSITIONS, TYPE_LABELS, VOTABLE_TYPES } from "./constants";

interface Props {
  ideaId: number | null;
  open: boolean;
  onClose: () => void;
  onUpdated: () => void;
}

export default function IdeaDetail({ ideaId, open, onClose, onUpdated }: Props) {
  const { hasPerm } = useAuth();
  const canUpdate = hasPerm("ideas", "update");
  const canCreate = hasPerm("ideas", "create");
  const [idea, setIdea] = useState<Idea | null>(null);
  const [timeline, setTimeline] = useState<IdeaTimeline | null>(null);
  const [comments, setComments] = useState<IdeaComment[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [voted, setVoted] = useState(false);

  const load = async () => {
    if (!ideaId) return;
    try {
      const [i, tl, cm] = await Promise.all([
        getIdea(ideaId),
        getIdeaTimeline(ideaId),
        listIdeaComments(ideaId),
      ]);
      setIdea(i);
      setTimeline(tl);
      setComments(cm);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    if (open && ideaId) void load();
  }, [open, ideaId]);

  useEffect(() => {
    if (open && canUpdate) {
      listUsers().then(setUsers).catch(() => setUsers([]));
    }
  }, [open, canUpdate]);

  const saveAssignees = async (selected: AdminUser[]) => {
    if (!idea) return;
    await setIdeaAssignees(idea.id, selected.map((u) => u.id));
    await load();
    onUpdated();
  };

  const doTransition = async (status: IdeaStatus) => {
    if (!idea) return;
    try {
      await transitionIdea(idea.id, status);
      await load();
      onUpdated();
    } catch (e) {
      setError(String(e));
    }
  };

  const submitComment = async () => {
    if (!idea || !comment.trim()) return;
    await addIdeaComment(idea.id, comment.trim());
    setComment("");
    await load();
  };

  const toggleVote = async () => {
    if (!idea) return;
    try {
      if (voted) await unvoteIdea(idea.id);
      else await voteIdea(idea.id);
      setVoted(!voted);
      await load();
      onUpdated();
    } catch (e) {
      setError(String(e));
    }
  };

  if (!idea) {
    return (
      <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
        <DialogContent><Typography color="text.secondary">Carregando...</Typography></DialogContent>
      </Dialog>
    );
  }

  const nextStatuses = TRANSITIONS[idea.status] || [];
  const canVote = canCreate && VOTABLE_TYPES.includes(idea.idea_type);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>{idea.title}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{dataDisplayText(error)}</Alert>}
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} flexWrap="wrap">
            <Chip label={TYPE_LABELS[idea.idea_type]} size="small" />
            <Chip label={STATUS_LABELS[idea.status]} color={STATUS_COLOR[idea.status]} size="small" />
            <Chip label={`Impacto: ${IMPACT_LABELS[idea.impact]}`} size="small" />
            <Chip label={`Dificuldade: ${idea.difficulty}`} size="small" />
            <Chip label={`${idea.votes_count} votos · score ${idea.priority_score}`} size="small" />
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Autor: {idea.author.full_name || idea.author.username}
            {idea.due_date ? ` · Prazo: ${idea.due_date}` : ""}
          </Typography>
          <Typography variant="body1">{idea.description || "Sem descrição."}</Typography>

          {canVote && (
            <Button startIcon={voted ? <ThumbDown /> : <ThumbUp />} onClick={() => void toggleVote()}>
              {voted ? "Remover voto" : "Votar"}
            </Button>
          )}

          {canUpdate && (
            <Stack direction="row" spacing={1} flexWrap="wrap">
              {nextStatuses.map((s) => (
                <Button key={s} size="small" variant="outlined" onClick={() => void doTransition(s)}>
                  → {STATUS_LABELS[s]}
                </Button>
              ))}
            </Stack>
          )}

          {canUpdate && (
            <Autocomplete
              multiple
              options={users}
              getOptionLabel={(u) => u.full_name || u.username}
              value={users.filter((u) => idea.assignees.some((a) => a.id === u.id))}
              onChange={(_, v) => void saveAssignees(v)}
              renderInput={(params) => <TextField {...params} label="Pessoas na ideia" />}
            />
          )}

          {timeline && (
            <Box>
              <Typography variant="subtitle1" gutterBottom>Linha do tempo (dwell)</Typography>
              <Stack spacing={1}>
                {timeline.intervals.map((iv) => (
                  <Box key={iv.id} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <Box sx={{ minWidth: 140 }}><Chip size="small" label={STATUS_LABELS[iv.status as IdeaStatus] || iv.status} /></Box>
                    <Box sx={{ flex: 1, height: 8, bgcolor: "#E2E5EE", borderRadius: 1, overflow: "hidden" }}>
                      <Box sx={{ width: `${Math.min(100, iv.duration_days * 8)}%`, height: "100%", bgcolor: "#4338CA" }} />
                    </Box>
                    <Typography variant="caption" sx={{ minWidth: 48 }}>{iv.duration_days}d</Typography>
                  </Box>
                ))}
              </Stack>
              <Stack direction="row" spacing={2} sx={{ mt: 1 }} flexWrap="wrap">
                <Typography variant="caption">Até iniciar: {timeline.metrics.time_to_start_days ?? "—"}d</Typography>
                <Typography variant="caption">Em implementação: {timeline.metrics.time_in_impl_days ?? "—"}d</Typography>
                <Typography variant="caption">Até PROD: {timeline.metrics.time_to_prod_days ?? "—"}d</Typography>
                <Typography variant="caption">No status atual: {timeline.metrics.days_in_current_status}d</Typography>
              </Stack>
            </Box>
          )}

          <Divider />
          <Typography variant="subtitle1">Comentários</Typography>
          {comments.map((c) => (
            <Box key={c.id} sx={{ p: 1, bgcolor: "#FAFBFD", borderRadius: 1 }}>
              <Typography variant="caption" color="text.secondary">
                {c.author.full_name || c.author.username} · {c.created_at ? new Date(c.created_at).toLocaleString("pt-BR") : ""}
              </Typography>
              <Typography variant="body2">{c.body}</Typography>
            </Box>
          ))}
          {canCreate && (
            <Stack direction="row" spacing={1}>
              <TextField fullWidth multiline minRows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Adicionar comentário..." />
              <Button variant="contained" onClick={() => void submitComment()} disabled={!comment.trim()}>Enviar</Button>
            </Stack>
          )}

          <RecordHistory module="ideas" entityType="idea" entityId={idea.id} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Fechar</Button>
      </DialogActions>
    </Dialog>
  );
}
