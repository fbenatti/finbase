import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../components/FinbaseDialog";
import { useEffect, useMemo, useState } from "react";
import { Button, Checkbox, FormControlLabel, FormGroup, Stack, Typography } from "@mui/material";
import { GoldQueryFilter } from "../../services/api";
import HierarchicalDecompTree, {
  buildExecutiveDecompChain,
  executiveChainStartIndex,
  normalizeDirectorPath,
  pathSegmentFilters,
} from "./HierarchicalDecompTree";

const DEFAULT_TABLE = "dest_dm_cost_visao_completa";
type ConsumptionType = "realizado" | "forecast" | "ambos";

const TYPE_LABELS: Record<ConsumptionType, string> = {
  realizado: "Efetivado",
  forecast: "Forecast",
  ambos: "Efetivado + Forecast",
};

function selectionFromType(tipo: ConsumptionType) {
  return {
    realizado: tipo !== "forecast",
    forecast: tipo !== "realizado",
  };
}

function periodLabel(period: string): string {
  if (/^\d{4}$/.test(period)) return `ano de ${period}`;
  const match = String(period ?? "").match(/^(\d{4})(\d{2})$/);
  if (!match) return period || "todas as competências do escopo";
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
  tipo = "realizado",
  globalFilters = [],
  measureColumn = "realizado",
  labelManualForecast = false,
}: {
  open: boolean;
  onClose: () => void;
  table?: string;
  directorPath: string;
  directorView: boolean;
  period?: string;
  tipo?: ConsumptionType;
  globalFilters?: GoldQueryFilter[];
  measureColumn?: "realizado" | "realizado_cloud";
  labelManualForecast?: boolean;
}) {
  const [selectedTypes, setSelectedTypes] = useState(() => selectionFromType(tipo));
  useEffect(() => {
    if (open) setSelectedTypes(selectionFromType(tipo));
  }, [open, tipo]);
  const selectedTipo: ConsumptionType = selectedTypes.realizado && selectedTypes.forecast
    ? "ambos"
    : selectedTypes.forecast
      ? "forecast"
      : "realizado";
  const toggleType = (type: "realizado" | "forecast", checked: boolean) => {
    setSelectedTypes((current) => {
      if (!checked && !current[type === "realizado" ? "forecast" : "realizado"]) {
        return current;
      }
      return { ...current, [type]: checked };
    });
  };
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
  const baseFilters = useMemo<GoldQueryFilter[]>(() => {
    const filters: GoldQueryFilter[] = [
      // O modo combinado usa grupos OR explícitos abaixo. Excluir apenas
      // rateio também incluía orçamento na soma do consumo projetado.
      ...(selectedTipo === "forecast"
        ? [{ column: "tipo", operator: "IN" as const, values: ["forecast", "forecast-manual"] }]
        : selectedTipo === "realizado"
          ? [{ column: "tipo", operator: "IS_REALIZED" as const, values: [] }]
          : []),
      ...pathSegmentFilters(path, directorView),
      ...globalFilters,
    ];
    if (/^\d{4}$/.test(period || "")) {
      filters.push({
        column: "_billing_period",
        operator: "IN",
        values: Array.from({ length: 12 }, (_, index) =>
          `${period}${String(index + 1).padStart(2, "0")}`,
        ),
      });
    } else if (period) {
      filters.push({ column: "_billing_period", operator: "IN", values: [period] });
    }
    return filters;
  }, [path, directorView, period, selectedTipo, globalFilters]);
  const filterGroups = useMemo<GoldQueryFilter[][]>(() => selectedTipo === "ambos" ? [
    [{ column: "tipo", operator: "IS_REALIZED", values: [] }],
    [{ column: "tipo", operator: "IN", values: ["forecast", "forecast-manual"] }],
  ] : [], [selectedTipo]);
  const queryKey = JSON.stringify([table, directorView, baseFilters, filterGroups, measureColumn]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth>
      <DialogTitle>Decomposição de consumo pela estrutura</DialogTitle>
      <DialogContent dividers>
        <Stack direction={{ xs: "column", md: "row" }} gap={2} alignItems={{ md: "center" }} sx={{ mb: 1.5 }}>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
            Escopo do departamento selecionado{path.length ? ` (${path.join(" > ")})` : ""} · {periodLabel(period || "")} · {TYPE_LABELS[selectedTipo]}.
            O drill começa no próximo nível da hierarquia já filtrado (sem cloud). Cadeia: {chain.map(c => c.label).join(" → ")}.
          </Typography>
          <FormGroup row sx={{ flexWrap: "nowrap" }}>
            <FormControlLabel
              control={(
                <Checkbox
                  checked={selectedTypes.realizado}
                  onChange={(_, checked) => toggleType("realizado", checked)}
                />
              )}
              label="Efetivado"
            />
            <FormControlLabel
              control={(
                <Checkbox
                  checked={selectedTypes.forecast}
                  onChange={(_, checked) => toggleType("forecast", checked)}
                />
              )}
              label="Forecast do período"
            />
          </FormGroup>
        </Stack>
        {open && (
          <HierarchicalDecompTree
            table={table}
            baseFilters={baseFilters}
            filterGroups={filterGroups}
            chain={chain}
            queryKey={queryKey}
            shareCaption="participação no contexto da janela"
            filterRowTypes={false}
            measureColumn={measureColumn}
            labelManualForecast={labelManualForecast}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Fechar</Button>
      </DialogActions>
    </Dialog>
  );
}
