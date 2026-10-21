import {
  DataGrid as MuiDataGrid, GridToolbarContainer, GridToolbarColumnsButton,
  GridToolbarFilterButton, GridToolbarDensitySelector, GridToolbarExport,
  useGridRootProps, useGridApiContext, useGridSelector, gridRowCountSelector,
  type DataGridProps, type GridValidRowModel, type GridToolbarProps,
} from "@mui/x-data-grid";
import { Box, Typography, type SxProps, type Theme } from "@mui/material";
import { dataGridIcons } from "./DataGridIcons";

export function FinbaseGridToolbar({ csvOptions, printOptions }: GridToolbarProps) {
  const root = useGridRootProps();
  const api = useGridApiContext();
  const count = useGridSelector(api, gridRowCountSelector);
  const button = { variant: "text" as const, size: "small" as const };
  return <GridToolbarContainer sx={{ gap: 0.5, px: 1.5, py: 1, minHeight: 56, flexWrap: "wrap" }}>
    {!root.disableColumnSelector && <GridToolbarColumnsButton slotProps={{ button }} />}
    {!root.disableColumnFilter && <GridToolbarFilterButton slotProps={{ button }} />}
    <GridToolbarDensitySelector slotProps={{ button }} />
    <GridToolbarExport
      slotProps={{ button }}
      csvOptions={{ fileName: "finbase-grade", utf8WithBom: true, ...csvOptions }}
      printOptions={{ disableToolbarButton: true, ...printOptions }}
    />
    <Box sx={{ flex: 1 }} />
    <Typography variant="caption" color="text.secondary" role="status" sx={{ px: 1 }}>
      {root.loading ? "Carregando…" : `${count.toLocaleString("pt-BR")} ${count === 1 ? "registro" : "registros"}`}
    </Typography>
  </GridToolbarContainer>;
}

const gridStyle: SxProps<Theme> = theme => ({
  fontFamily: theme.typography.fontFamily,
  fontSize: "0.875rem",
  backgroundColor: theme.palette.background.paper,
  borderColor: theme.palette.divider,
  "& .MuiDataGrid-columnHeader": {
    backgroundColor: theme.palette.primary.main === "#0f62fe" ? "#e0e0e0" : "#EDF2F6",
    color: theme.palette.text.primary, padding: "0 16px",
  },
  "& .MuiDataGrid-columnHeaderTitle, & .MuiDataGrid-columnHeader .MuiTypography-root": { fontWeight: 600, fontSize: "0.875rem" },
  "& .MuiDataGrid-cell": { padding: "0 16px", borderColor: theme.palette.divider, fontVariantNumeric: "tabular-nums" },
  "& .MuiDataGrid-cell--textRight": { justifyContent: "flex-end" },
  "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus, & .MuiDataGrid-cell:focus-within, & .MuiDataGrid-columnHeader:focus-within": { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: -2 },
  "& .MuiDataGrid-footerContainer": { minHeight: 48, borderColor: theme.palette.divider, backgroundColor: theme.palette.background.default },
  "& .MuiDataGrid-toolbarContainer": { backgroundColor: theme.palette.background.paper, borderBottom: `1px solid ${theme.palette.divider}` },
  "& .MuiDataGrid-toolbarContainer .MuiButton-root": { minHeight: 40, textTransform: "none", fontSize: "0.8125rem", padding: "8px 12px" },
  "& .MuiDataGrid-cell .MuiIconButton-root": { width: 36, height: 36 },
  "& .MuiDataGrid-cell .MuiSvgIcon-root": { fontSize: 20 },
});

/** All interactive grids share this structure; queries, edits and row semantics remain with the module. */
export default function FinbaseDataGrid<R extends GridValidRowModel = any>({ sx, slots, ...props }: DataGridProps<R>) {
  return <MuiDataGrid<R>
    rowHeight={48} columnHeaderHeight={48} disableRowSelectionOnClick
    {...props}
    slots={{ ...dataGridIcons, toolbar: FinbaseGridToolbar, ...slots }}
    sx={[...(Array.isArray(sx) ? sx : sx ? [sx] : []), gridStyle]}
  />;
}
