import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useState } from "react";
import {
  DndContext, DragEndEvent, DragOverlay, DragStartEvent,
  PointerSensor, useSensor, useSensors, closestCorners, useDroppable, useDraggable,
} from "@dnd-kit/core";
import { Box, Card, CardContent, Chip, Stack, Typography, Alert } from "@mui/material";
import { useAuth } from "../../auth/AuthContext";
import { getIdeasBoard, transitionIdea, type Idea, type IdeaBoard, type IdeaStatus } from "../../services/ideasApi";
import { STATUS_LABELS, BOARD_STATUSES } from "./constants";
import { colors } from "../../theme/tokens";

interface Props {
  onOpen: (id: number) => void;
  refreshKey: number;
}

function DraggableCard({ idea, onOpen, disabled }: { idea: Idea; onOpen: (id: number) => void; disabled: boolean }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: idea.id, disabled });
  const style = transform ? { transform: `translate(${transform.x}px, ${transform.y}px)`, opacity: isDragging ? 0.5 : 1 } : undefined;
  return (
    <Card ref={setNodeRef} style={style} {...listeners} {...attributes}
      sx={{ mb: 1, cursor: disabled ? "pointer" : "grab", "&:hover": { boxShadow: 3 } }}
      onClick={() => onOpen(idea.id)}>
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>{idea.title}</Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          <Chip size="small" label={idea.idea_type} />
          <Chip size="small" label={`${idea.votes_count} votos`} />
        </Stack>
      </CardContent>
    </Card>
  );
}

function DroppableColumn({ status, ideas, onOpen, canDrag }: { status: IdeaStatus; ideas: Idea[]; onOpen: (id: number) => void; canDrag: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id: status });
  return (
    <Box ref={setNodeRef} sx={{
      minWidth: 240, maxWidth: 280, flex: "0 0 240px",
      bgcolor: isOver ? colors.brandTint : colors.tableHead,
      borderRadius: 2, p: 1, maxHeight: "calc(100vh - 280px)", overflowY: "auto",
      border: isOver ? "2px dashed #0F62FE" : "2px solid transparent",
    }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1, px: 0.5 }}>
        <Typography variant="subtitle2">{STATUS_LABELS[status]}</Typography>
        <Chip size="small" label={ideas.length} />
      </Stack>
      {ideas.map((idea) => <DraggableCard key={idea.id} idea={idea} onOpen={onOpen} disabled={!canDrag} />)}
    </Box>
  );
}

export default function CanvasTab({ onOpen, refreshKey }: Props) {
  const { hasPerm } = useAuth();
  const canUpdate = hasPerm("ideas", "update");
  const [board, setBoard] = useState<IdeaBoard | null>(null);
  const [active, setActive] = useState<Idea | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setBoard(await getIdeasBoard());
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));

  const findIdea = (id: number): Idea | undefined => {
    if (!board) return undefined;
    for (const col of board.columns) {
      const found = col.ideas.find((i) => i.id === id);
      if (found) return found;
    }
    return undefined;
  };

  const onDragStart = (e: DragStartEvent) => {
    setActive(findIdea(Number(e.active.id)) || null);
  };

  const onDragEnd = async (e: DragEndEvent) => {
    setActive(null);
    if (!canUpdate || !e.over) return;
    const ideaId = Number(e.active.id);
    const targetStatus = String(e.over.id) as IdeaStatus;
    const idea = findIdea(ideaId);
    if (!idea || idea.status === targetStatus) return;
    try {
      await transitionIdea(ideaId, targetStatus);
      await load();
    } catch (err) {
      setError(String(err));
      await load();
    }
  };

  if (!board) return <Typography>Carregando quadro...</Typography>;

  const columns = BOARD_STATUSES.map((status) => ({
    status,
    ideas: board.columns.find((c) => c.status === status)?.ideas || [],
  }));

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {!canUpdate && <Alert severity="info" sx={{ mb: 2 }}>Modo de visualização do quadro. Mover cards exige permissão de edição.</Alert>}
      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onDragStart} onDragEnd={(e) => void onDragEnd(e)}>
        <Stack direction="row" spacing={1.5} sx={{ overflowX: "auto", pb: 2 }}>
          {columns.map((col) => (
            <DroppableColumn key={col.status} status={col.status} ideas={col.ideas} onOpen={onOpen} canDrag={canUpdate} />
          ))}
        </Stack>
        <DragOverlay>
          {active ? (
            <Card sx={{ width: 220, boxShadow: 6 }}>
              <CardContent sx={{ py: 1.5 }}><Typography variant="subtitle2">{active.title}</Typography></CardContent>
            </Card>
          ) : null}
        </DragOverlay>
      </DndContext>
    </Box>
  );
}
