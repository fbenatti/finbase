import type { ReactNode } from "react";
import { Accordion, AccordionDetails, AccordionSummary, Chip, Stack, Typography } from "@mui/material";
import { ExpandMoreOutlined } from "@mui/icons-material";
import { colors, radius } from "../theme/tokens";

/**
 * Seção recolhível de "Histórico de versões". Fechada por padrão, em todas as
 * telas que mostram histórico. O conteúdo (timeline) só é montado ao abrir.
 */
interface Props {
  count?: number;
  subtitle?: string;
  children: ReactNode;
}

export default function VersionHistorySection({ count, subtitle, children }: Props) {
  return (
    <Accordion
      defaultExpanded={false}
      disableGutters
      elevation={0}
      TransitionProps={{ unmountOnExit: true }}
      sx={{ mt: 2, border: `1px solid ${colors.border}`, borderRadius: `${radius.surface}px !important`, "&::before": { display: "none" }, "&.Mui-expanded": { margin: "16px 0 0" } }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreOutlined />}
        sx={{ px: 2.5, minHeight: 56, "&.Mui-expanded": { minHeight: 56 }, "& .MuiAccordionSummary-content": { alignItems: "center", gap: 1.5, my: 1.25 } }}
      >
        <Typography fontWeight={700} color={colors.text}>Histórico de versões</Typography>
        {count !== undefined && <Chip size="small" label={count} variant="outlined" sx={{ height: 20, fontWeight: 600, color: colors.textSecondary }} />}
        {subtitle && <Typography variant="body2" color="text.secondary" sx={{ display: { xs: "none", md: "block" } }}>{subtitle}</Typography>}
      </AccordionSummary>
      <AccordionDetails sx={{ px: 2.5, pt: 0, pb: 2.5 }}>
        <Stack>{children}</Stack>
      </AccordionDetails>
    </Accordion>
  );
}
