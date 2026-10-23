/**
 * Central de avisos (painel lateral do sino) e toast de alerta.
 *
 * Só apresentação: o polling, a fila e as ações vivem em NotificationContext,
 * que entrega aqui cada tarefa já resumida em `NotificationView`.
 */
import React, { useEffect, useState } from "react";
import {
  AlertColor, Box, Button, ButtonBase, Drawer, IconButton, LinearProgress, Paper, Slide, Snackbar, Stack, Tooltip, Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import {
  AutorenewOutlined, BackupOutlined, CallSplitOutlined, CheckCircleOutlined, CloseOutlined, CloudDownloadOutlined,
  DoneAllOutlined, ErrorOutlineOutlined, HourglassEmptyOutlined, InfoOutlined, InsightsOutlined,
  StopCircleOutlined, TaskAltOutlined, WarningAmberOutlined,
} from "@mui/icons-material";
import { colors, fontFamily, radius } from "../theme/tokens";

export type NotificationKind = "extraction" | "reprocess" | "forecast" | "shared_cost" | "backup" | "other";

export interface NotificationView {
  id: number;
  kind: NotificationKind;
  title: string;
  status: string;
  progress: number;
  indeterminate: boolean;
  stage: string;
  caption: string;
  scope: string;
  startedAt: number;
  timestamp: number;
}

type Tone = "running" | "queued" | "success" | "error" | "neutral";

const KIND: Record<NotificationKind, { label: string; Icon: typeof InfoOutlined }> = {
  extraction: { label: "Coleta", Icon: CloudDownloadOutlined },
  reprocess: { label: "Reprocessamento", Icon: AutorenewOutlined },
  forecast: { label: "Forecast", Icon: InsightsOutlined },
  shared_cost: { label: "Rateio", Icon: CallSplitOutlined },
  backup: { label: "Backup", Icon: BackupOutlined },
  other: { label: "Tarefa", Icon: InfoOutlined },
};

const TONE: Record<Tone, string> = {
  running: colors.brand,
  queued: colors.textSecondary,
  success: colors.success,
  error: colors.error,
  neutral: colors.textMuted,
};

function toneOf(status: string): Tone {
  if (status === "RUNNING") return "running";
  if (status === "QUEUED") return "queued";
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "error";
  return "neutral";
}

function statusLabel(item: NotificationView): string {
  switch (item.status) {
    case "RUNNING": return item.indeterminate ? "Em execução" : `Em execução · ${item.progress}%`;
    case "QUEUED": return "Na fila";
    case "COMPLETED": return "Concluído";
    case "FAILED": return "Falhou";
    case "CANCELLED": return "Cancelado";
    default: return item.status;
  }
}

function relativeTime(timestamp: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000));
  if (seconds < 45) return "agora";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  return new Date(timestamp).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
}

function elapsed(startedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(s).padStart(2, "0")}s`;
}

/** "Origem: X · Mês: Y · Destino: Z" vira pares rótulo/valor; texto livre fica como está. */
function scopeEntries(scope: string): { label: string; value: string }[] {
  return scope.split(" · ").map((part) => {
    const index = part.indexOf(": ");
    return index > 0 ? { label: part.slice(0, index), value: part.slice(index + 2) } : { label: "", value: part };
  });
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function StatusPill({ item }: { item: NotificationView }) {
  const color = TONE[toneOf(item.status)];
  return (
    <Box component="span" sx={{
      display: "inline-flex", alignItems: "center", gap: 0.75, px: 1, py: 0.25, borderRadius: radius.pill,
      bgcolor: alpha(color, 0.1), color, fontSize: "0.75rem", fontWeight: 600, lineHeight: 1.5, whiteSpace: "nowrap",
    }}>
      <Box component="span" sx={{
        width: 6, height: 6, borderRadius: "50%", bgcolor: color,
        ...(item.status === "RUNNING" && {
          animation: "fbPulse 1.6s ease-in-out infinite",
          "@keyframes fbPulse": { "0%, 100%": { opacity: 1 }, "50%": { opacity: 0.3 } },
        }),
      }} />
      {statusLabel(item)}
    </Box>
  );
}

function NotificationCard({ item, now, onCancel, onRemove, cancelling }: {
  item: NotificationView;
  now: number;
  /** Ausente quando o usuário não pode interromper tarefas. */
  onCancel?: (id: number) => void;
  onRemove: (id: number) => void;
  cancelling: boolean;
}) {
  const tone = toneOf(item.status);
  const color = TONE[tone];
  const isActive = tone === "running" || tone === "queued";
  const { Icon, label } = KIND[item.kind];
  const entries = item.scope ? scopeEntries(item.scope) : [];
  // Espaço reservado à direita para os botões de ação, que ficam sobrepostos ao topo do card.
  const actionsGutter = isActive ? 8.5 : 4.5;
  const time = isActive ? elapsed(item.startedAt, now) : relativeTime(item.timestamp, now);

  return (
    <Box sx={{
      position: "relative", display: "flex", gap: 1.5, p: 2, borderRadius: `${radius.surface}px`,
      bgcolor: "background.paper", border: "1px solid", borderColor: tone === "error" ? alpha(colors.error, 0.35) : "divider",
      transition: "border-color 120ms, box-shadow 120ms",
      "&:hover": { borderColor: tone === "error" ? alpha(colors.error, 0.55) : colors.borderStrong, boxShadow: "0 2px 8px rgba(22,43,58,0.06)" },
      "&:hover .fb-actions, &:focus-within .fb-actions": { opacity: 1 },
    }}>
      <Box sx={{
        flexShrink: 0, width: 36, height: 36, borderRadius: `${radius.control}px`, display: "grid", placeItems: "center",
        bgcolor: alpha(color, 0.1), color,
      }}>
        <Icon sx={{ fontSize: 20 }} />
      </Box>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.25, pr: actionsGutter }}>
          <Typography noWrap sx={{ minWidth: 0, fontSize: "0.6875rem", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", color: "text.secondary" }}>
            {label}
          </Typography>
          <Typography sx={{ fontSize: "0.6875rem", color: colors.textMuted }}>·</Typography>
          <Typography sx={{ flexShrink: 0, whiteSpace: "nowrap", fontSize: "0.6875rem", color: colors.textMuted, fontVariantNumeric: "tabular-nums" }}>
            {time}
          </Typography>
        </Box>

        <Typography sx={{
          fontSize: "0.875rem", fontWeight: 600, color: "text.primary", lineHeight: 1.4, pr: actionsGutter,
          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden", wordBreak: "break-word",
        }}>
          {item.title}
        </Typography>

        {entries.length > 0 && (
          <Box component="dl" sx={{
            m: 0, mt: 1, display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 1.5, rowGap: 0.25,
            fontSize: "0.75rem", lineHeight: 1.5,
          }}>
            {entries.map((entry, index) => (
              <React.Fragment key={index}>
                {entry.label && <Box component="dt" sx={{ color: colors.textMuted }}>{entry.label}</Box>}
                <Box component="dd" sx={{ m: 0, color: "text.secondary", minWidth: 0, wordBreak: "break-word", gridColumn: entry.label ? undefined : "1 / -1" }}>
                  {entry.value}
                </Box>
              </React.Fragment>
            ))}
          </Box>
        )}

        {tone === "running" && (
          <Box sx={{ mt: 1.5 }}>
            <LinearProgress
              variant={item.indeterminate ? "indeterminate" : "determinate"}
              value={item.progress}
              sx={{
                height: 6, borderRadius: radius.pill, bgcolor: alpha(color, 0.12),
                "& .MuiLinearProgress-bar": { borderRadius: radius.pill, bgcolor: color },
              }}
            />
          </Box>
        )}

        <Box sx={{ mt: 1.25, display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <StatusPill item={item} />
          {item.stage && item.stage !== item.status && tone !== "error" && (
            <Typography sx={{ fontSize: "0.75rem", color: "text.secondary", minWidth: 0, flex: 1 }} noWrap title={item.stage}>
              {item.stage}
            </Typography>
          )}
        </Box>
        {item.stage && tone === "error" && (
          <Typography sx={{
            mt: 1, px: 1.25, py: 0.75, borderRadius: `${radius.control}px`, bgcolor: alpha(colors.error, 0.06),
            fontSize: "0.75rem", lineHeight: 1.5, color: colors.error, wordBreak: "break-word",
          }}>
            {item.stage}
          </Typography>
        )}
        {item.caption && tone !== "queued" && (
          <Typography sx={{ mt: 0.5, fontSize: "0.75rem", color: colors.textMuted, fontVariantNumeric: "tabular-nums" }}>
            {item.caption}
          </Typography>
        )}
      </Box>

      <Box className="fb-actions" sx={{
        position: "absolute", top: 10, right: 10, display: "flex", gap: 0.25,
        opacity: { xs: 1, md: isActive ? 1 : 0 }, transition: "opacity 120ms",
      }}>
        {isActive && onCancel && (
          <Tooltip title="Interromper tarefa">
            <span>
              <IconButton aria-label="Interromper tarefa" size="small" disabled={cancelling} onClick={() => onCancel(item.id)}
                sx={{ width: 30, height: 30, color: "text.secondary", "&:hover": { color: colors.error, bgcolor: alpha(colors.error, 0.08) } }}>
                <StopCircleOutlined sx={{ fontSize: "18px !important" }} />
              </IconButton>
            </span>
          </Tooltip>
        )}
        <Tooltip title="Dispensar aviso">
          <IconButton aria-label="Dispensar aviso" size="small" onClick={() => onRemove(item.id)}
            sx={{ width: 30, height: 30, color: "text.secondary" }}>
            <CloseOutlined sx={{ fontSize: "18px !important" }} />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 0.5, mb: 1 }}>
      <Typography sx={{ fontSize: "0.75rem", fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "text.secondary" }}>
        {title}
      </Typography>
      <Box sx={{
        minWidth: 20, height: 20, px: 0.75, borderRadius: radius.pill, bgcolor: colors.tableHead, color: "text.secondary",
        fontSize: "0.6875rem", fontWeight: 700, display: "grid", placeItems: "center",
      }}>{count}</Box>
    </Box>
  );
}

function EmptyState() {
  return (
    <Box sx={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", px: 4, py: 8 }}>
      <Box sx={{
        width: 64, height: 64, borderRadius: "50%", display: "grid", placeItems: "center", mb: 2,
        bgcolor: colors.brandTint, color: colors.brand, boxShadow: `0 0 0 8px ${alpha(colors.brandTint, 0.5)}`,
      }}>
        <TaskAltOutlined sx={{ fontSize: 30 }} />
      </Box>
      <Typography sx={{ fontFamily: fontFamily.heading, fontWeight: 700, fontSize: "1rem", color: "text.primary" }}>
        Tudo em dia
      </Typography>
      <Typography sx={{ mt: 0.5, fontSize: "0.875rem", color: "text.secondary", maxWidth: 280 }}>
        Coletas, reprocessamentos e execuções de Forecast aparecem aqui enquanto rodam e quando terminam.
      </Typography>
    </Box>
  );
}

export function NotificationPanel({ open, items, onClose, onCancel, onRemove, onClearAll }: {
  open: boolean;
  items: NotificationView[];
  onClose: () => void;
  onCancel?: (id: number) => Promise<void> | void;
  onRemove: (id: number) => void;
  onClearAll: () => void;
}) {
  const now = useNow(open);
  const [cancelling, setCancelling] = useState<Set<number>>(new Set());
  const active = items.filter((item) => item.status === "RUNNING" || item.status === "QUEUED");
  const finished = items.filter((item) => item.status !== "RUNNING" && item.status !== "QUEUED");
  const countOf = (status: string) => finished.filter((item) => item.status === status).length;
  const completed = countOf("COMPLETED");
  const failures = countOf("FAILED");
  const cancelled = countOf("CANCELLED");

  const cancel = async (id: number) => {
    setCancelling((prev) => new Set(prev).add(id));
    try { await onCancel?.(id); } finally {
      setCancelling((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  };

  const summary = [
    active.length ? `${active.length} em andamento` : "",
    completed ? `${completed} ${completed === 1 ? "concluído" : "concluídos"}` : "",
    failures ? `${failures} com falha` : "",
    cancelled ? `${cancelled} ${cancelled === 1 ? "cancelado" : "cancelados"}` : "",
  ].filter(Boolean).join(" · ") || "Nenhuma tarefa recente";

  return (
    <Drawer anchor="right" open={open} onClose={onClose}
      slotProps={{ backdrop: { sx: { bgcolor: "rgba(22,43,58,0.18)" } } }}
      PaperProps={{
        sx: {
          width: { xs: "100%", sm: 440 }, bgcolor: colors.canvas, display: "flex", flexDirection: "column",
          borderLeft: "1px solid", borderColor: "divider", boxShadow: "-12px 0 40px rgba(22,43,58,0.12)",
        },
      }}>
      <Box sx={{ px: 3, pt: 2.5, pb: 2, bgcolor: "background.paper", borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1.5 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography component="h2" sx={{ fontFamily: fontFamily.heading, fontSize: "1.25rem", fontWeight: 700, color: "text.primary" }}>
              Avisos
            </Typography>
            <Typography sx={{ mt: 0.25, fontSize: "0.8125rem", color: "text.secondary" }}>{summary}</Typography>
          </Box>
          <Tooltip title="Fechar">
            <IconButton aria-label="Fechar avisos" onClick={onClose} sx={{ mt: -0.5, mr: -1, color: "text.secondary" }}>
              <CloseOutlined />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      <Box sx={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", px: 2, py: 2.5, gap: 3 }}>
        {items.length === 0 && <EmptyState />}
        {active.length > 0 && (
          <Box component="section">
            <SectionHeader title="Em andamento" count={active.length} />
            <Stack spacing={1}>
              {active.map((item) => (
                <NotificationCard key={item.id} item={item} now={now} onCancel={onCancel ? (id) => void cancel(id) : undefined} onRemove={onRemove} cancelling={cancelling.has(item.id)} />
              ))}
            </Stack>
          </Box>
        )}
        {finished.length > 0 && (
          <Box component="section">
            <SectionHeader title="Recentes" count={finished.length} />
            <Stack spacing={1}>
              {finished.map((item) => (
                <NotificationCard key={item.id} item={item} now={now} onCancel={onCancel ? (id) => void cancel(id) : undefined} onRemove={onRemove} cancelling={false} />
              ))}
            </Stack>
          </Box>
        )}
      </Box>

      {items.length > 0 && (
        <Box sx={{
          px: 3, py: 1.5, bgcolor: "background.paper", borderTop: "1px solid", borderColor: "divider",
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1,
        }}>
          <Typography sx={{ fontSize: "0.75rem", color: colors.textMuted }}>
            Atualizado a cada 3 s
          </Typography>
          <Button size="small" variant="text" color="inherit" onClick={onClearAll}
            startIcon={<DoneAllOutlined />} sx={{ color: "text.secondary", minHeight: 32 }}>
            Limpar tudo
          </Button>
        </Box>
      )}
    </Drawer>
  );
}

const TOAST: Record<AlertColor, { color: string; title: string; Icon: typeof InfoOutlined }> = {
  success: { color: colors.success, title: "Concluído", Icon: CheckCircleOutlined },
  error: { color: colors.error, title: "Atenção", Icon: ErrorOutlineOutlined },
  warning: { color: colors.warning, title: "Aviso", Icon: WarningAmberOutlined },
  info: { color: colors.brand, title: "Em andamento", Icon: HourglassEmptyOutlined },
};

export function NotificationToast({ open, message, severity, onClose, onOpenPanel }: {
  open: boolean;
  message: string;
  severity: AlertColor;
  onClose: () => void;
  /** Ausente para quem não pode ver os avisos: o toast fica sem o atalho. */
  onOpenPanel?: () => void;
}) {
  const theme = useTheme();
  const { color, title, Icon } = TOAST[severity];
  return (
    <Snackbar open={open} autoHideDuration={6000} onClose={(_, reason) => { if (reason !== "clickaway") onClose(); }}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }} TransitionComponent={Slide}
      sx={{ [theme.breakpoints.up("sm")]: { bottom: 32, right: 32 } }}>
      <Paper role="status" elevation={0} sx={{
        position: "relative", overflow: "hidden", width: { xs: "100%", sm: 400 }, display: "flex", gap: 1.5, alignItems: "flex-start",
        p: 1.75, pl: 2.25, borderRadius: `${radius.surface}px`, border: "1px solid", borderColor: "divider",
        boxShadow: "0 12px 32px rgba(22,43,58,0.16), 0 2px 6px rgba(22,43,58,0.06)",
        "&::before": { content: '""', position: "absolute", left: 0, top: 0, bottom: 0, width: 4, bgcolor: color },
      }}>
        <Box sx={{ flexShrink: 0, width: 32, height: 32, borderRadius: "50%", display: "grid", placeItems: "center", bgcolor: alpha(color, 0.1), color }}>
          <Icon sx={{ fontSize: 18 }} />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0, pt: 0.25 }}>
          <Typography sx={{ fontSize: "0.8125rem", fontWeight: 700, color: "text.primary", lineHeight: 1.4 }}>{title}</Typography>
          <Typography sx={{ fontSize: "0.8125rem", color: "text.secondary", lineHeight: 1.45, wordBreak: "break-word" }}>{message}</Typography>
          {onOpenPanel && (
            <ButtonBase onClick={() => { onClose(); onOpenPanel(); }} sx={{
              mt: 0.75, fontSize: "0.75rem", fontWeight: 600, color: colors.brand, borderRadius: 0.5,
              "&:hover": { textDecoration: "underline" }, "&:focus-visible": { outline: `2px solid ${colors.brandLight}`, outlineOffset: 2 },
            }}>
              Ver avisos
            </ButtonBase>
          )}
        </Box>
        <IconButton aria-label="Fechar alerta" size="small" onClick={onClose} sx={{ width: 28, height: 28, mt: -0.25, mr: -0.5, color: colors.textMuted }}>
          <CloseOutlined sx={{ fontSize: "16px !important" }} />
        </IconButton>
      </Paper>
    </Snackbar>
  );
}
