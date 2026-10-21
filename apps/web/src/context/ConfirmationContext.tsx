import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Button, Typography } from "@mui/material";
import { Dialog, DialogTitle, DialogContent, DialogActions } from "../components/FinbaseDialog";
interface Options { title?: string; confirmLabel?: string; danger?: boolean }
type Confirm = (description: string, options?: Options) => Promise<boolean>;
interface Request extends Options { id: number; description: string; resolve: (accepted: boolean) => void }
const Context = createContext<Confirm | null>(null);
export function useConfirmation() {
  const confirm = useContext(Context);
  if (!confirm) throw new Error("ConfirmationProvider ausente");
  return confirm;
}
export function ConfirmationProvider({ children }: { children: ReactNode }) {
  const requests = useRef<Request[]>([]);
  const nextId = useRef(0);
  const [active, setActive] = useState<Request | null>(null);
  const confirm = useCallback<Confirm>((description, options = {}) => new Promise(resolve => {
    const request = { ...options, description, id: ++nextId.current, resolve };
    requests.current.push(request);
    setActive(requests.current[0]);
  }), []);
  const finish = (accepted: boolean) => {
    if (!active || requests.current[0]?.id !== active.id) return;
    requests.current.shift()?.resolve(accepted);
    setActive(requests.current[0] || null);
  };
  useEffect(() => () => { requests.current.splice(0).forEach(request => request.resolve(false)); }, []);
  return <Context.Provider value={confirm}>
    {children}
    <Dialog open={Boolean(active)} onClose={() => finish(false)} maxWidth="xs">
      <DialogTitle>{active?.title || "Confirmar ação"}</DialogTitle>
      <DialogContent><Typography sx={{ whiteSpace: "pre-line", overflowWrap: "anywhere" }}>{active?.description}</Typography></DialogContent>
      <DialogActions><Button onClick={() => finish(false)}>Cancelar</Button><Button variant="contained" color={active?.danger ? "error" : "primary"} onClick={() => finish(true)}>{active?.confirmLabel || "Confirmar"}</Button></DialogActions>
    </Dialog>
  </Context.Provider>;
}
