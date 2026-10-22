import type { MouseEvent, ReactNode } from "react";
import { IconButton, Stack } from "@mui/material";
import { CompareArrowsOutlined, ContentCopyOutlined, DeleteOutlined, EditOutlined, MoreVertOutlined, PlayArrowOutlined, StopOutlined, VisibilityOutlined } from "@mui/icons-material";
import { colors, radius } from "../theme/tokens";

/**
 * Conjunto único de ações de linha para datagrids. Ícone, tamanho e cor vêm
 * daqui, para que edição, clonagem, exclusão e execução tenham a mesma cara em
 * todas as telas. O tooltip continua no Tooltip de quem usa, com o texto da ação.
 */
export type RowActionKind = "edit" | "clone" | "delete" | "run" | "view" | "compare" | "cancel" | "more";

const ICON = {
  edit: EditOutlined,
  clone: ContentCopyOutlined,
  delete: DeleteOutlined,
  run: PlayArrowOutlined,
  view: VisibilityOutlined,
  compare: CompareArrowsOutlined,
  cancel: StopOutlined,
  more: MoreVertOutlined,
} as const;

const DEFAULT_LABEL: Record<RowActionKind, string> = {
  edit: "Editar",
  clone: "Clonar",
  delete: "Excluir",
  run: "Executar",
  view: "Ver detalhes",
  compare: "Comparar",
  cancel: "Cancelar",
  more: "Mais ações",
};

const NEUTRAL = { color: colors.textSecondary, hover: colors.brandSelected, hoverColor: colors.brandDark };

const TONE: Record<RowActionKind, { color: string; hover: string; hoverColor: string }> = {
  edit: NEUTRAL,
  clone: NEUTRAL,
  view: NEUTRAL,
  compare: NEUTRAL,
  more: NEUTRAL,
  run: { color: colors.brand, hover: colors.brandSelected, hoverColor: colors.brandDark },
  delete: { color: colors.textSecondary, hover: "rgba(185, 28, 28, 0.08)", hoverColor: colors.error },
  cancel: { color: colors.textSecondary, hover: "rgba(185, 28, 28, 0.08)", hoverColor: colors.error },
};

interface RowActionProps {
  kind: RowActionKind;
  label?: string;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
}

export function RowAction({ kind, label, onClick, disabled }: RowActionProps) {
  const Icon = ICON[kind];
  const tone = TONE[kind];
  return (
    <IconButton
      size="small"
      aria-label={label ?? DEFAULT_LABEL[kind]}
      disabled={disabled}
      onClick={(event) => { event.stopPropagation(); onClick(event); }}
      sx={{
        width: 32,
        height: 32,
        borderRadius: radius.control,
        color: tone.color,
        "&:hover": { bgcolor: tone.hover, color: tone.hoverColor },
        "&.Mui-disabled": { color: colors.borderStrong },
      }}
    >
      <Icon sx={{ fontSize: 18 }} />
    </IconButton>
  );
}

/** Agrupa as ações de uma linha, centralizadas na célula. */
export function RowActions({ children }: { children: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" justifyContent="center" gap={0.5} sx={{ height: "100%" }}>
      {children}
    </Stack>
  );
}
