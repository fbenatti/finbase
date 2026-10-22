import { dataDisplayText } from "../utils/dataPresentation";
import { Alert, Box, CircularProgress, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import { api } from "../services/api";
import { FIELD_LABELS } from "../audit/fieldLabels";
import { colors, radius } from "../theme/tokens";
import VersionTimeline, { type TimelineItem } from "./VersionTimeline";

export interface HistoryEvent {
  id: number;
  created_at: string;
  user: { id: number | null; username: string | null; full_name: string | null };
  action: string;
  method: string;
  entity_label: string | null;
  changes: Array<{ field: string; old: unknown; new: unknown }> | null;
  has_full_snapshot: boolean;
}

const ACTION_META: Record<string, { label: string; tone: "success" | "primary" | "error" | "neutral" }> = {
  create: { label: "Criação", tone: "success" },
  update: { label: "Alteração", tone: "primary" },
  delete: { label: "Exclusão", tone: "error" },
  execute: { label: "Execução", tone: "neutral" },
  login: { label: "Acesso", tone: "neutral" },
  logout: { label: "Saída", tone: "neutral" },
  failed: { label: "Falha", tone: "error" },
};

const SUMMARY_BY_ACTION: Record<string, string> = {
  create: "Objeto criado",
  delete: "Objeto excluído",
};

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Snapshots({ eventId, module, entityType, entityId }: { eventId: number; module: string; entityType: string; entityId: number | string }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    api.get(`/administracao/auditoria/historico/${module}/${entityType}/${entityId}/${eventId}`).then((r) => setData(r.data)).catch(() => setError(true));
  }, [eventId, module, entityType, entityId]);
  if (error) return <Typography variant="caption" color="error">Não foi possível abrir o detalhe</Typography>;
  if (!data) return <CircularProgress size={16} sx={{ mt: 1 }} />;
  return (
    <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 1.25 }}>
      {[{ title: "Antes da alteração", value: data.before_json, bg: colors.raised }, { title: "Depois da alteração", value: data.after_json, bg: colors.brandTint }].map(side => (
        <Box key={side.title} flex={1} sx={{ bgcolor: side.bg, border: `1px solid ${colors.border}`, p: 1.25, borderRadius: radius.control, overflow: "auto", maxHeight: 220 }}>
          <Typography variant="caption" fontWeight={700} color={colors.textSecondary}>{side.title}</Typography>
          <Box component="pre" sx={{ fontSize: 11, m: 0, mt: 0.5, fontFamily: "monospace" }}>{JSON.stringify(side.value ?? null, null, 2)}</Box>
        </Box>
      ))}
    </Stack>
  );
}

export default function RecordHistory({ module, entityType, entityId }: { module: string; entityType: string; entityId: number | string }) {
  const [events, setEvents] = useState<HistoryEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEvents(null);
    setError(null);
    api.get(`/administracao/auditoria/historico/${module}/${entityType}/${entityId}`)
      .then((r) => setEvents(r.data))
      .catch((e) => setError(e?.response?.data?.detail || "Não foi possível carregar o histórico"));
  }, [module, entityType, entityId]);

  if (error) return <Alert severity="info" sx={{ mt: 1 }}>Histórico indisponível ({dataDisplayText(error)})</Alert>;
  if (!events) return <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}><CircularProgress size={22} /></Box>;

  const items: TimelineItem[] = events.map(ev => {
    const meta = ACTION_META[ev.action] ?? { label: ev.action, tone: "neutral" as const };
    const changes = (ev.changes ?? []).map(c => ({
      label: FIELD_LABELS[module]?.[c.field] || c.field,
      from: fmt(c.old),
      to: fmt(c.new),
    }));
    return {
      id: ev.id,
      title: meta.label,
      date: ev.created_at,
      author: ev.user?.full_name || ev.user?.username || null,
      tag: { label: meta.label, tone: meta.tone },
      summary: SUMMARY_BY_ACTION[ev.action],
      changes,
      details: ev.has_full_snapshot ? <Snapshots eventId={ev.id} module={module} entityType={entityType} entityId={entityId} /> : undefined,
    };
  });

  return <VersionTimeline items={items} emptyText="Nenhuma mudança registrada" />;
}
