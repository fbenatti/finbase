import { createTheme } from "@mui/material/styles";
import type {} from "@mui/x-data-grid/themeAugmentation";
import { dataGridIcons } from "../components/DataGridIcons";
import { ptBR } from "@mui/x-data-grid/locales";
import { colors, fontFamily, radius } from "./tokens";

export function createAppTheme() {
  const border = colors.border;
  return createTheme({
    palette: {
      primary: { main: colors.brand, light: colors.brandLight, dark: colors.brandDark },
      secondary: { main: colors.accent, dark: colors.accentDark },
      success: { main: colors.success },
      warning: { main: colors.warning },
      error: { main: colors.error },
      info: { main: colors.info },
      background: { default: colors.canvas, paper: colors.paper },
      text: { primary: colors.text, secondary: colors.textSecondary },
      divider: border,
    },
    typography: {
      fontFamily: fontFamily.body,
      fontSize: 14,
      button: { fontSize: "0.875rem", lineHeight: 1.4 },
      body2: { fontSize: "0.875rem", lineHeight: 1.5 },
      h4: { fontFamily: fontFamily.heading, fontSize: "1.75rem", lineHeight: 1.25, fontWeight: 700, letterSpacing: "-0.01em" },
      h5: { fontFamily: fontFamily.heading, fontSize: "1.25rem", fontWeight: 700 },
      h6: { fontFamily: fontFamily.heading, fontSize: "1rem", fontWeight: 700 },
    },
    shape: {
      borderRadius: radius.control,
    },
    components: {
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: {
            textTransform: "none",
            fontWeight: 600,
            minHeight: 40,
            padding: "8px 16px",
            borderRadius: radius.control,
            "& .MuiButton-startIcon > *:nth-of-type(1), & .MuiButton-endIcon > *:nth-of-type(1)": { fontSize: 20, width: 20, height: 20 },
            "&:focus-visible": { outline: "2px solid", outlineColor: colors.brandLight, outlineOffset: 2 },
          },
        },
      },
      MuiIconButton: {
        styleOverrides: {
          root: { width: 40, height: 40, borderRadius: radius.control, "& .MuiSvgIcon-root": { fontSize: 20 }, "&:focus-visible": { outline: "2px solid", outlineColor: colors.brandLight, outlineOffset: -2 } },
        },
      },
      MuiTextField: { defaultProps: { size: "small" } },
      MuiFormControl: { defaultProps: { size: "small" } },
      MuiSelect: { defaultProps: { size: "small" } },
      MuiInputBase: { styleOverrides: { root: { fontSize: "0.875rem" } } },
      MuiOutlinedInput: { styleOverrides: { root: { minHeight: 40, borderRadius: radius.control } } },
      MuiChip: { styleOverrides: { root: { fontSize: "0.75rem", fontWeight: 600, borderRadius: radius.pill } } },
      MuiDialogTitle: { styleOverrides: { root: { fontFamily: fontFamily.heading, fontSize: "1.25rem", fontWeight: 700 } } },
      MuiDialogActions: { styleOverrides: { root: { padding: "16px 24px", gap: 8, borderTop: `1px solid ${border}` } } },
      MuiTableCell: {
        styleOverrides: {
          root: { borderColor: border, fontSize: "0.875rem", padding: "12px 16px", fontVariantNumeric: "tabular-nums" },
          head: { backgroundColor: colors.tableHead, fontWeight: 700, color: colors.text },
        },
      },
      MuiDataGrid: {
        defaultProps: { rowHeight: 48, columnHeaderHeight: 48, disableRowSelectionOnClick: true, slots: dataGridIcons, localeText: ptBR.components.MuiDataGrid.defaultProps.localeText },
        styleOverrides: {
          root: { borderColor: border, backgroundColor: colors.paper, borderRadius: radius.surface, fontSize: "0.875rem", "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus, & .MuiDataGrid-cell:focus-within, & .MuiDataGrid-columnHeader:focus-within": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: -2 } },
          columnHeader: { backgroundColor: colors.tableHead, padding: "0 16px", "&:focus-within": { outlineOffset: -2 } },
          columnHeaderTitle: { fontWeight: 700 },
          cell: { borderColor: border, padding: "0 16px", fontVariantNumeric: "tabular-nums", display: "flex", alignItems: "center", "&.MuiDataGrid-cell--textRight": { justifyContent: "flex-end" }, "&:focus-within": { outlineOffset: -2 } },
          row: { "&:hover": { backgroundColor: colors.rowHover } },
          footerContainer: { minHeight: 48, borderColor: border, backgroundColor: colors.raised },
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
            borderRadius: radius.surface,
          },
        },
      },
      MuiPaper: { styleOverrides: { outlined: { borderColor: border } } },
      MuiTabs: { styleOverrides: { indicator: { height: 2, borderRadius: 2, backgroundColor: colors.brand } } },
      MuiTab: { styleOverrides: { root: { minHeight: 42, textTransform: "none", fontWeight: 600, color: colors.textSecondary, "&.Mui-selected": { color: colors.brand } } } },
    },
  });
}

export default createAppTheme();
