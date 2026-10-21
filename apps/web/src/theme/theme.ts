import { createTheme } from "@mui/material/styles";

const theme = createTheme({
  palette: {
    primary: {
      main: "#1B5878",
      light: "#3B7895",
      dark: "#123D59",
    },
    secondary: {
      main: "#168F83",
      light: "#42B5A6",
      dark: "#0D6B63",
    },
    background: {
      default: "#F4F7FA",
      paper: "#FFFFFF",
    },
    text: { primary: "#1D2D3F", secondary: "#617386" },
    divider: "#E4EAF0",
  },
  typography: {
    fontFamily: '"Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    h4: {
      fontWeight: 600,
    },
    h5: {
      fontWeight: 600,
    },
    h6: {
      fontWeight: 600,
    },
  },
  shape: {
    borderRadius: 10,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 500,
        },
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
          border: "1px solid #E4EAF0",
          backgroundImage: "none",
        },
      },
    },
    MuiPaper: { styleOverrides: { outlined: { borderColor: "#E4EAF0" } } },
    MuiTabs: { styleOverrides: { indicator: { height: 2, borderRadius: 2 } } },
    MuiTab: { styleOverrides: { root: { minHeight: 42, textTransform: "none", fontWeight: 600, color: "#617386" } } },
  },
});

export default theme;
