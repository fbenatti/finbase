import { useMemo, useSyncExternalStore } from "react";
import { createAppTheme } from "./theme";
const subscribe = (listener: () => void) => {
  window.addEventListener("finbase:layout-change", listener);
  return () => window.removeEventListener("finbase:layout-change", listener);
};
const snapshot = () => document.documentElement.dataset.finbaseLayout === "carbon" ? "carbon" : "finbase";
/** Also themes portals and reports mounted outside the page's ThemeProvider. */
export function useInterfaceTheme() {
  const mode = useSyncExternalStore(subscribe, snapshot, () => "finbase");
  return useMemo(() => createAppTheme(mode as "finbase" | "carbon"), [mode]);
}
