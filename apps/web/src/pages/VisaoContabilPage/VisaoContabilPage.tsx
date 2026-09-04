import { Box, Typography } from "@mui/material";
import VisaoContabil, { EMPTY_FILTERS } from "../DimonDashboard/VisaoContabil";

/** Página dedicada (escopo visao-contabil): o mesmo painel da aba de Governança,
 *  mas em rota própria para permitir acesso granular sem abrir o módulo rules. */
export default function VisaoContabilPage() {
  return (
    <Box sx={{ p: 3, "& .MuiOutlinedInput-root:not(.MuiInputBase-multiline)": { height: 40 } }}>
      <Typography variant="h4" sx={{ mb: 2 }}>Visão Contábil</Typography>
      <VisaoContabil
        table="dest_dm_cost_visao_completa"
        filters={EMPTY_FILTERS}
        filterKey="visao-contabil-page"
      />
    </Box>
  );
}
