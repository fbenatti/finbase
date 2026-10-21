import React from "react";
import ReactDOM from "react-dom/client";
import { ThemeProvider, CssBaseline } from "@mui/material";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ConfirmationProvider } from "./context/ConfirmationContext";
import theme from "./theme/theme";
import "./index.css";
import { NotificationProvider } from "./context/NotificationContext";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <ConfirmationProvider><NotificationProvider>
          <App />
        </NotificationProvider></ConfirmationProvider>
      </ThemeProvider>
    </BrowserRouter>
  </React.StrictMode>
);
