import { Box, Chip, CircularProgress, Stack, Typography, Alert, Divider, Avatar, IconButton, Tooltip } from "@mui/material";
import { useEffect, useState } from "react";
import { api } from "../services/api";
import { FIELD_LABELS } from "../audit/fieldLabels";

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

const ACTION_META: Record<string, { label: string; color: "success" | "primary" | "error" | "default" }> = {
  create: { label: "Criado", color: "success" },
  update: { label: "Alterado", color: "primary" },
  delete: { label: "Excluído", color: "error" },
  execute: { label: "Executado", color: "default" },
  login: { label: "Login", color: "default" },
  logout: { label: "Logout", color: "default" },
  failed: { label: "Falhou", color: "error" },
};

function fmt(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function Snapshots({ eventId, module, entityType, entityId }: { eventId: number; module: string; entityType: string; entityId: number | string }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    api.get(`/audit/history/${module}/${entityType}/${entityId}/${eventId}`).then((r) => setData(r.data)).catch(() => setError(true));
  }, [eventId, module, entityType, entityId]);
  if (error) return <Typography variant="caption" color="error">Falha ao carregar detalhe</Typography>;
  if (!data) return <CircularProgress size={16} />;
  return (
    <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 1 }}>
      <Box flex={1} sx={{ bgcolor: "#fafafa", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 220 }}>
        <Typography variant="caption" fontWeight={700}>Antes</Typography>
        <pre style={{ fontSize: 11, margin: 0 }}>{JSON.stringify(data.before_json ?? null, null, 2)}</pre>
      </Box>
      <Box flex={1} sx={{ bgcolor: "#f0f7ff", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 220 }}>
        <Typography variant="caption" fontWeight={700}>Depois</Typography>
        <pre style={{ fontSize: 11, margin: 0 }}>{JSON.stringify(data.after_json ?? null, null, 2)}</pre>
      </Box>
    </Stack>
  );
}

export default function RecordHistory({ module, entityType, entityId }: { module: string; entityType: string; entityId: number | string }) {
  const [events, setEvents] = useState<HistoryEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    setEvents(null);
    setError(null);
    api.get(`/audit/history/${module}/${entityType}/${entityId}`)
      .then((r) => setEvents(r.data))
      .catch((e) => setError(e?.response?.data?.detail || "Erro ao carregar histórico"));
  }, [module, entityType, entityId]);

  if (error) return <Alert severity="info" sx={{ mt: 1 }}>Histórico indisponível ({error})</Alert>;
  if (!events) return <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}><CircularProgress size={22} /></Box>;
  if (events.length === 0) return <Typography color="text.secondary" sx={{ py: 3, textAlign: "center" }}>Nenhuma alteração registrada</Typography>;

  return (
    <Box sx={{ mt: 1 }}>
      {events.map((ev) => {
        const meta = ACTION_META[ev.action] || { label: ev.action, color: "default" as const };
        const initials = (ev.user?.username || "?").slice(0, 2).toUpperCase();
        return (
          <Box key={ev.id} sx={{ display: "flex", gap: 1.5, py: 1 }}>
            <Avatar sx={{ width: 30, height: 30, fontSize: 12 }}>{initials}</Avatar>
            <Box flex={1} sx={{ borderBottom: "1px solid #eee", pb: 1 }}>
              <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
                <Typography variant="body2" fontWeight={600}>{ev.user?.username || "—"}</Typography>
                <Typography variant="caption" color="text.secondary">{fmtDate(ev.created_at)}</Typography>
                <Chip size="small" label={meta.label} color={meta.color} variant="outlined" sx={{ height: 20 }} />
                {ev.has_full_snapshot && (
                  <Tooltip title="Ver snapshots completos">
                    <IconButton size="small" onClick={() => setExpanded(expanded === ev.id ? null : ev.id)}>
                      <span style={{ fontSize: 14 }}>{expanded === ev.id ? "▾" : "▸"}</span>
                    </IconButton>
                  </Tooltip>
                )}
              </Stack>
              {ev.action === "create" && <Typography variant="body2" color="text.secondary">Registro criado</Typography>}
              {ev.action === "delete" && <Typography variant="body2" color="text.secondary">Registro excluído</Typography>}
              {ev.changes && ev.changes.map((c, i) => {
                const label = FIELD_LABELS[module]?.[c.field] || c.field;
                return (
                  <Typography key={i} variant="body2" sx={{ my: 0.25 }}>
                    <b>{label}</b>: {fmt(c.old)} → {fmt(c.new)}
                  </Typography>
                );
              })}
              {expanded === ev.id && <Snapshots eventId={ev.id} module={module} entityType={entityType} entityId={entityId} />}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}
