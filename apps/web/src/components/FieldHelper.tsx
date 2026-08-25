import { ReactNode } from "react";
import { Box, IconButton, Stack, Tooltip, TooltipProps } from "@mui/material";
import { InfoOutlined } from "@mui/icons-material";
import type { SxProps, Theme } from "@mui/material/styles";

type FieldHelperProps = {
  title: ReactNode;
  placement?: TooltipProps["placement"];
  sx?: SxProps<Theme>;
};

export function FieldHelper({ title, placement = "top", sx }: FieldHelperProps) {
  return (
    <Tooltip
      title={title}
      arrow
      placement={placement}
      slotProps={{
        tooltip: {
          sx: { maxWidth: 360, typography: "body2", whiteSpace: "pre-line" },
        },
      }}
    >
      <IconButton
        size="small"
        aria-label="Ajuda sobre o campo"
        tabIndex={-1}
        sx={{ color: "text.secondary", p: 0.25, flexShrink: 0, ...sx }}
      >
        <InfoOutlined sx={{ fontSize: 16 }} />
      </IconButton>
    </Tooltip>
  );
}

type FieldWithHelpProps = {
  help: ReactNode;
  children: ReactNode;
  sx?: SxProps<Theme>;
  helperSx?: SxProps<Theme>;
};

/** Agrupa um campo MUI com o ícone (i) de ajuda alinhado à direita. */
export function FieldWithHelp({ help, children, sx, helperSx }: FieldWithHelpProps) {
  return (
    <Stack direction="row" alignItems="flex-start" gap={0.25} sx={sx}>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
      <FieldHelper title={help} sx={{ mt: 1.1, ...helperSx }} />
    </Stack>
  );
}
