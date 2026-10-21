import { createTheme } from "@mui/material/styles";
import type {} from "@mui/x-data-grid/themeAugmentation";
import { dataGridIcons } from "../components/DataGridIcons";
import { ptBR } from "@mui/x-data-grid/locales";

export function createAppTheme(layout: "finbase" | "carbon" = "finbase") {
  const carbon = layout === "carbon";
  const border = carbon ? "#e0e0e0" : "#E4EAF0";
  const surface = carbon ? "#f4f4f4" : "#F4F7FA";
  return createTheme({
  palette: {
    primary: {
      main: carbon ? "#0f62fe" : "#1B5878",
      light: carbon ? "#4589ff" : "#3B7895",
      dark: carbon ? "#0043ce" : "#123D59",
    },
    secondary: {
      main: "#168F83",
      light: "#42B5A6",
      dark: "#0D6B63",
    },
    background: {
      default: surface,
      paper: "#FFFFFF",
    },
    text: { primary: carbon ? "#161616" : "#1D2D3F", secondary: carbon ? "#525252" : "#617386" },
    divider: border,
  },
  typography: {
    fontFamily: carbon ? '"IBM Plex Sans", "Helvetica Neue", Arial, sans-serif' : '"Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    fontSize: 14,
    button: { fontSize: "0.875rem", lineHeight: 1.4 },
    body2: { fontSize: "0.875rem", lineHeight: 1.5 },
    h4: {
      fontSize: "1.75rem",
      lineHeight: 1.3,
      fontWeight: carbon ? 400 : 600,
    },
    h5: {
      fontSize: "1.25rem",
      fontWeight: 600,
    },
    h6: {
      fontSize: "1rem",
      fontWeight: 600,
    },
  },
  shape: {
    borderRadius: carbon ? 0 : 8,
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 500,
          minHeight: 40,
          padding: "8px 16px",
          borderRadius: carbon ? 0 : 6,
          "& .MuiButton-startIcon > *:nth-of-type(1), & .MuiButton-endIcon > *:nth-of-type(1)": { fontSize: 20, width: 20, height: 20 },
          "&:focus-visible": { outline: "2px solid", outlineOffset: 2 },
        },
      },
    },
    MuiIconButton: {
      styleOverrides: { root: { width: 40, height: 40, borderRadius: carbon ? 0 : 6, "& .MuiSvgIcon-root": { fontSize: 20 }, "&:focus-visible": { outline: "2px solid", outlineOffset: -2 } } },
    },
    MuiTextField: { defaultProps: { size: "small" } },
    MuiFormControl: { defaultProps: { size: "small" } },
    MuiSelect: { defaultProps: { size: "small" } },
    MuiInputBase: { styleOverrides: { root: { fontSize: "0.875rem" } } },
    MuiOutlinedInput: { styleOverrides: { root: { minHeight: 40, borderRadius: carbon ? 0 : 6, ...(carbon ? { backgroundColor: surface } : {}) } } },
    MuiChip: { styleOverrides: { root: { fontSize: "0.75rem", fontWeight: 500, borderRadius: carbon ? 0 : 4 } } },
    MuiDialogTitle: { styleOverrides: { root: { fontSize: "1.25rem", fontWeight: 600 } } },
    MuiDialogActions: { styleOverrides: { root: { padding: "16px 24px", gap: 8, borderTop: `1px solid ${border}` } } },
    MuiTableCell: { styleOverrides: {
      root: { borderColor: border, fontSize: "0.875rem", padding: "12px 16px", fontVariantNumeric: "tabular-nums" },
      head: { backgroundColor: carbon ? "#e0e0e0" : "#EDF2F6", fontWeight: 600, color: carbon ? "#161616" : "#1D2D3F" },
    } },
    MuiDataGrid: {
      defaultProps: { rowHeight: 48, columnHeaderHeight: 48, disableRowSelectionOnClick: true, slots: dataGridIcons, localeText: ptBR.components.MuiDataGrid.defaultProps.localeText },
      styleOverrides: {
        root: { borderColor: border, backgroundColor: "#fff", borderRadius: carbon ? 0 : 6, fontSize: "0.875rem", "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus, & .MuiDataGrid-cell:focus-within, & .MuiDataGrid-columnHeader:focus-within": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: -2 } },
        columnHeader: { backgroundColor: carbon ? "#e0e0e0" : "#EDF2F6", padding: "0 16px", "&:focus-within": { outlineOffset: -2 } },
        columnHeaderTitle: { fontWeight: 600 },
        cell: { borderColor: border, padding: "0 16px", fontVariantNumeric: "tabular-nums", display: "flex", alignItems: "center", "&.MuiDataGrid-cell--textRight": { justifyContent: "flex-end" }, "&:focus-within": { outlineOffset: -2 } },

        row: { "&:hover": { backgroundColor: carbon ? "#e8e8e8" : "#F0F5F8" } },
        footerContainer: { minHeight: 48, borderColor: border, backgroundColor: surface },
        toolbarContainer: { padding: 16, gap: 8, borderBottom: `1px solid ${border}` },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          borderRight: "none",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          boxShadow: "none",
          border: `1px solid ${border}`,
          backgroundImage: "none",
        },
      },
    },
    MuiPaper: { styleOverrides: { outlined: { borderColor: border } } },
    MuiTabs: { styleOverrides: { indicator: { height: 2, borderRadius: 2 } } },
    MuiTab: { styleOverrides: { root: { minHeight: 42, textTransform: "none", fontWeight: 600, color: "#617386" } } },
  },
});
}

export default createAppTheme();
