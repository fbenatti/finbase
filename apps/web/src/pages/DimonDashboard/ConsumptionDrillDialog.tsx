import { useMemo } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from "@mui/material";
import { DimonQueryFilter } from "../../services/api";
import HierarchicalDecompTree, {
  buildExecutiveDecompChain,
  executiveChainStartIndex,
  normalizeDirectorPath,
  pathSegmentFilters,
} from "./HierarchicalDecompTree";

const DEFAULT_TABLE = "dest_dm_cost_visao_completa";

function monthLabel(period: string): string {
  const match = String(period ?? "").match(/^(\d{4})(\d{2})$/);
  if (!match) return period || "todos os períodos do escopo";
  const months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
  return `${months[Number(match[2]) - 1]}/${match[1].slice(2)}`;
}

export default function ConsumptionDrillDialog({
  open,
  onClose,
  table = DEFAULT_TABLE,
  directorPath,
  directorView,
  period,
}: {
  open: boolean;
  onClose: () => void;
  table?: string;
  directorPath: string;
  directorView: boolean;
  period?: string;
}) {
  const path = useMemo(
    () => normalizeDirectorPath(
      directorPath.split(" > ").map(s => s.trim()).filter(Boolean),
      directorView,
    ),
    [directorPath, directorView],
  );
  const chain = useMemo(
    () => buildExecutiveDecompChain(directorView, executiveChainStartIndex(path, directorView)),
    [directorView, path],
  );
  const baseFilters = useMemo<DimonQueryFilter[]>(() => {
    const filters: DimonQueryFilter[] = [
      { column: "tipo", operator: "IN", values: ["realizado"] },
      ...pathSegmentFilters(path, directorView),
    ];
    if (period) filters.push({ column: "_billing_period", operator: "IN", values: [period] });
    return filters;
  }, [path, directorView, period]);
  const queryKey = `${table}|${directorView}|${path.join(">")}|${period || ""}`;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth>
      <DialogTitle>Decomposição hierárquica de consumo</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          Escopo do departamento selecionado{path.length ? ` (${path.join(" > ")})` : ""} · {monthLabel(period || "")}.
          O drill começa no próximo nível do organograma já filtrado (sem cloud). Cadeia: {chain.map(c => c.label).join(" → ")}.
        </Typography>
        {open && (
          <HierarchicalDecompTree
            table={table}
            baseFilters={baseFilters}
            chain={chain}
            queryKey={queryKey}
            shareCaption="participação no contexto do diálogo"
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Fechar</Button>
      </DialogActions>
    </Dialog>
  );
}
