import { useMemo } from "react";
import { Stack } from "@mui/material";
import type { GridColDef } from "@mui/x-data-grid";
import DataGrid from "./FinbaseDataGrid";
import { RowAction } from "./RowAction";
import type { DestinationAnalysis, PivotView } from "../services/api";
import type { PivotVisualization } from "./PivotChart";
import { fieldLabel, friendlyMeasureLabel } from "../utils/pivotLabels";

// Saved pivot configurations ("relatórios salvos"). Kept outside PivotAnalysis
// so the same grid can back a dedicated reports screen and the widget picker
// of the future dashboards.
const VISUALIZATION_LABELS: Record<PivotVisualization, string> = { table: "Tabela", bar: "Barras", line: "Linha", pie: "Pizza", indicator: "Indicador" };

export const reportVisualization = (view: PivotView): PivotVisualization =>
  ((view.config as { visualization?: PivotVisualization }).visualization) || "table";

interface Props {
  reports: PivotView[];
  destinations: DestinationAnalysis[];
  loading?: boolean;
  onOpen: (report: PivotView) => void;
  onDelete: (report: PivotView) => void;
}

export default function SavedReportsGrid({ reports, destinations, loading, onOpen, onDelete }: Props) {
  const columns = useMemo<GridColDef<PivotView>[]>(() => [
    { field: "name", headerName: "Relatório", flex: 1.4, minWidth: 200 },
    { field: "visualization", headerName: "Visualização", width: 130, valueGetter: (_, row) => VISUALIZATION_LABELS[reportVisualization(row)] },
    { field: "base", headerName: "Base", flex: 1, minWidth: 160, valueGetter: (_, row) => destinations.find(item => item.id === row.destination_table_id)?.name || "Base não disponível" },
    { field: "measure", headerName: "Cálculo", flex: 1, minWidth: 170, valueGetter: (_, row) => row.config.measures?.map(friendlyMeasureLabel).join(", ") || "—" },
    { field: "groups", headerName: "Agrupado por", flex: 1, minWidth: 160, valueGetter: (_, row) => [...(row.config.rows || []), ...(row.config.columns || [])].map(fieldLabel).join(" › ") || "—" },
    { field: "updated_at", headerName: "Atualizado em", width: 180, type: "dateTime", valueGetter: (_, row) => row.updated_at || row.created_at ? new Date((row.updated_at || row.created_at) as string) : null },
    {
      field: "actions", headerName: "Ações", width: 110, sortable: false, filterable: false, disableExport: true,
      renderCell: params => <Stack direction="row" spacing={0.5} alignItems="center" height="100%">
        <RowAction kind="run" label={`Abrir relatório ${params.row.name}`} onClick={event => { event.stopPropagation(); onOpen(params.row); }} />
        <RowAction kind="delete" label={`Excluir relatório ${params.row.name}`} onClick={event => { event.stopPropagation(); onDelete(params.row); }} />
      </Stack>,
    },
  ], [destinations, onOpen, onDelete]);

  return <DataGrid
    rows={reports}
    columns={columns}
    loading={loading}
    autoHeight
    disableRowSelectionOnClick
    onRowDoubleClick={params => onOpen(params.row)}
    initialState={{ sorting: { sortModel: [{ field: "updated_at", sort: "desc" }] } }}
  />;
}
