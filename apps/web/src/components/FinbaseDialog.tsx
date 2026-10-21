import { Children, createContext, forwardRef, isValidElement, useContext, type ReactNode } from "react";
import {
  Dialog as MuiDialog, DialogTitle as MuiDialogTitle, DialogContent as MuiDialogContent,
  DialogActions as MuiDialogActions, IconButton, Tooltip, ThemeProvider,
  type DialogProps, type DialogTitleProps, type DialogContentProps, type DialogActionsProps,
} from "@mui/material";
import { Close } from "./FinbaseIcons";
import { useInterfaceTheme } from "../theme/useInterfaceTheme";
const CloseContext = createContext<Pick<DialogProps, "onClose" | "disableEscapeKeyDown">>({});

const hasCloseControl = (children: ReactNode): boolean => Children.toArray(children).some(child => {
  if (!isValidElement<{ children?: ReactNode; "aria-label"?: string }>(child)) return false;
  return child.type === IconButton || /fechar|close/i.test(child.props["aria-label"] || "")
    || child.props.children === "Fechar" || hasCloseControl(child.props.children);
});

export const Dialog = forwardRef<HTMLDivElement, DialogProps>(function FinbaseDialog({ sx, children, ...props }, ref) {
  const theme = useInterfaceTheme();
  return <ThemeProvider theme={theme}><CloseContext.Provider value={{ onClose: props.onClose, disableEscapeKeyDown: props.disableEscapeKeyDown }}>
    <MuiDialog fullWidth maxWidth="sm" {...props} ref={ref} sx={[
      ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      {
        "& .MuiBackdrop-root": { backgroundColor: "rgba(22,22,22,0.46)" },
        "& .MuiDialog-paper": {
          backgroundImage: "none", border: "1px solid", borderColor: "divider",
          borderRadius: theme.shape.borderRadius === 0 ? 0 : "12px",
          boxShadow: "0 16px 56px rgba(22,22,22,0.2)", minWidth: 0,
          maxHeight: { xs: "calc(100dvh - 32px)", sm: "calc(100dvh - 64px)" },
          margin: { xs: 2, sm: 4 },
        },
        "& .MuiDialog-paperFullScreen": { borderRadius: 0, border: 0, margin: 0, maxHeight: "100dvh" },
        "& .MuiDialogTitle-root": {
          position: "relative", flexShrink: 0, px: { xs: 2, sm: 3 }, py: 2.5,
          background: theme.palette.background.paper, borderBottom: "1px solid", borderColor: "divider",
          fontFamily: theme.typography.fontFamily, fontSize: "1.25rem", fontWeight: 600, lineHeight: 1.4,
          "& .MuiTypography-h6": { fontSize: "1.25rem", fontWeight: 600 },
          "& .MuiTypography-overline": { textTransform: "none", letterSpacing: 0 },
        },
        "& .MuiDialogContent-root": {
          minWidth: 0, overflowX: "auto", backgroundColor: "background.paper",
          px: { xs: 2, sm: 3 }, py: 3,
          "& .MuiTextField-root, & .MuiFormControl-root": { maxWidth: "100%" },
        },
        "& .MuiDialogActions-root": {
          flexShrink: 0, flexWrap: "wrap", gap: 1, px: { xs: 2, sm: 3 }, py: 2,
          backgroundColor: "background.default", borderTop: "1px solid", borderColor: "divider",
          "& > :not(style) ~ :not(style)": { ml: 0 },
          "& .MuiButton-root": { minHeight: 40 },
        },
      },
    ]}>{children}</MuiDialog>
  </CloseContext.Provider></ThemeProvider>;
});

export const DialogTitle = forwardRef<HTMLDivElement, DialogTitleProps>(function FinbaseDialogTitle({ children, sx, ...props }, ref) {
  const { onClose, disableEscapeKeyDown } = useContext(CloseContext);
  const showClose = Boolean(onClose) && !hasCloseControl(children);
  return <MuiDialogTitle {...props} ref={ref} sx={[...(Array.isArray(sx) ? sx : sx ? [sx] : []), showClose ? { paddingRight: "64px !important" } : {}]}>
    {children}
    {showClose && <Tooltip title="Fechar"><IconButton aria-label="Fechar diálogo" disabled={disableEscapeKeyDown} onClick={event => onClose?.(event, "escapeKeyDown")} sx={{ position: "absolute", top: 16, right: 16, width: 32, height: 32 }}><Close /></IconButton></Tooltip>}
  </MuiDialogTitle>;
});
export const DialogContent = forwardRef<HTMLDivElement, DialogContentProps>(function FinbaseDialogContent(props, ref) { return <MuiDialogContent {...props} ref={ref} />; });
export const DialogActions = forwardRef<HTMLDivElement, DialogActionsProps>(function FinbaseDialogActions(props, ref) { return <MuiDialogActions {...props} ref={ref} />; });
