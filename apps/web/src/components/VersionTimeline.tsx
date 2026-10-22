import { useState, type ReactNode } from "react";
import { Avatar, Box, Button, Chip, Stack, Typography } from "@mui/material";
import { colors, radius } from "../theme/tokens";

/**
 * Linha do tempo de versões. Compartilhada por todas as telas que mostram
 * histórico (planejamento, conexões, ideias). É somente leitura: não há ação
 * de restauração, por decisão do produto.
 */
export interface TimelineChange {
  label: string;
  from?: ReactNode;
  to?: ReactNode;
}

export interface TimelineItem {
  id: string | number;
  title: string;
  date: string;
  author?: string | null;
  tag?: { label: string; tone?: "success" | "primary" | "error" | "neutral" };
  summary?: string;
  changes?: TimelineChange[];
  metric?: ReactNode;
  /** Conteúdo extra (ex.: snapshots), carregado só ao expandir. */
  details?: ReactNode;
  /** Ações específicas da tela (ex.: publicar no orçamento). Nunca restaurar. */
  actions?: ReactNode;
}

interface Props {
  items: TimelineItem[];
  emptyText?: string;
}

const TONE_COLOR: Record<NonNullable<NonNullable<TimelineItem["tag"]>["tone"]>, string> = {
  success: colors.success,
  primary: colors.brand,
  error: colors.error,
  neutral: colors.textMuted,
};

function formatDate(iso: string): string {
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("pt-BR", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

function TimelineEntry({ item, isLast }: { item: TimelineItem; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  const tone = TONE_COLOR[item.tag?.tone ?? "neutral"];

  return (
    <Box sx={{ position: "relative", display: "grid", gridTemplateColumns: "20px 1fr", columnGap: 1.5, pb: isLast ? 0 : 2.5 }}>
      <Box aria-hidden sx={{ position: "relative", display: "flex", justifyContent: "center" }}>
        <Box sx={{ mt: 0.75, width: 12, height: 12, borderRadius: "50%", bgcolor: colors.paper, border: `3px solid ${tone}`, zIndex: 1 }} />
        {!isLast && <Box sx={{ position: "absolute", top: 20, bottom: -4, width: 2, bgcolor: colors.border, borderRadius: 1 }} />}
      </Box>

      <Box sx={{ minWidth: 0, bgcolor: colors.paper, border: `1px solid ${colors.border}`, borderRadius: radius.surface, p: 1.75 }}>
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap sx={{ rowGap: 0.75 }}>
          {item.author && (
            <Avatar sx={{ width: 24, height: 24, fontSize: 11, fontWeight: 700, bgcolor: colors.brandSelected, color: colors.brandDark }}>{initialsOf(item.author)}</Avatar>
          )}
          {item.author && <Typography variant="body2" fontWeight={600} color={colors.text}>{item.author}</Typography>}
          <Typography variant="caption" color="text.secondary">{formatDate(item.date)}</Typography>
          {item.tag && <Chip size="small" label={item.tag.label} variant="outlined" sx={{ height: 20, borderColor: tone, color: tone, fontWeight: 600 }} />}
        </Stack>

        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={2} sx={{ mt: 1 }}>
          <Box sx={{ minWidth: 0 }}>
            <Typography fontWeight={700} color={colors.text}>{item.title}</Typography>
            {item.summary && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>{item.summary}</Typography>}
          </Box>
          {item.metric && <Box sx={{ textAlign: "right", flexShrink: 0 }}>{item.metric}</Box>}
        </Stack>

        {item.changes && item.changes.length > 0 && (
          <Box sx={{ mt: 1.25, display: "grid", gap: 0.5 }}>
            {item.changes.map((change, index) => (
              <Stack key={`${change.label}-${index}`} direction="row" alignItems="baseline" gap={1} flexWrap="wrap" sx={{ fontSize: 13 }}>
                <Typography variant="body2" fontWeight={600} color={colors.textSecondary} sx={{ minWidth: 120 }}>{change.label}</Typography>
                <Typography variant="body2" sx={{ color: colors.textMuted, textDecoration: "line-through" }}>{change.from ?? "—"}</Typography>
                <Typography variant="body2" aria-hidden sx={{ color: colors.textMuted }}>→</Typography>
                <Typography variant="body2" fontWeight={700} color={colors.brandDark}>{change.to ?? "—"}</Typography>
              </Stack>
            ))}
          </Box>
        )}

        {(item.actions || item.details) && (
          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap" sx={{ mt: 1.25 }}>
            {item.actions}
            {item.details && (
              <Button size="small" variant="text" onClick={() => setOpen(value => !value)} aria-expanded={open} sx={{ ml: "auto", color: colors.textSecondary }}>
                {open ? "Ocultar detalhes" : "Ver detalhes"}
              </Button>
            )}
          </Stack>
        )}
        {open && item.details}
      </Box>
    </Box>
  );
}

export default function VersionTimeline({ items, emptyText = "Nenhuma versão registrada" }: Props) {
  if (items.length === 0) {
    return <Typography color="text.secondary" sx={{ py: 3, textAlign: "center" }}>{emptyText}</Typography>;
  }
  return (
    <Box component="ol" sx={{ listStyle: "none", m: 0, p: 0 }} aria-label="Histórico de versões">
      {items.map((item, index) => (
        <Box component="li" key={item.id}>
          <TimelineEntry item={item} isLast={index === items.length - 1} />
        </Box>
      ))}
    </Box>
  );
}
