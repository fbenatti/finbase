import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, formatApiError } from "../services/api";
import type { NavigationModule, NavigationSnapshot } from "./types";

interface NavigationState {
  items: NavigationModule[];
  customerId: number | null;
  customerName: string;
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}
const NavigationContext = createContext<NavigationState>({ items: [], customerId: null, customerName: "", loading: true, error: "", reload: async () => {} });
export const MenuViewContext = createContext<NavigationModule | null>(null);

export function NavigationProvider({ children }: { children: React.ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [snapshot, setSnapshot] = useState<{ userId: number; data: NavigationSnapshot } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const userId = user?.id;
  const activeUser = useRef(userId);
  activeUser.current = userId;
  const reload = useCallback(async () => {
    if (userId == null) { setSnapshot(null); setLoading(false); return; }
    setError("");
    try {
      const data = (await api.get<NavigationSnapshot>("/navigation")).data;
      if (activeUser.current === userId) setSnapshot({ userId, data });
    }
    catch (e) { if (activeUser.current === userId) { setSnapshot(null); setError(formatApiError(e, "Não foi possível carregar os módulos.")); } }
    finally { if (activeUser.current === userId) setLoading(false); }
  }, [userId]);
  useEffect(() => {
    if (authLoading) return;
    setLoading(true);
    void reload();
  }, [authLoading, reload]);
  useEffect(() => {
    const refresh = () => { if (userId != null) void reload(); };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [reload, userId]);
  const current = snapshot?.userId === userId ? snapshot?.data : null;
  return <NavigationContext.Provider value={{ items: current?.items || [], customerId: current?.customer_id ?? null, customerName: current?.customer_name || "", loading: authLoading || loading || (userId != null && !current && !error), error, reload }}>{children}</NavigationContext.Provider>;
}
export function useNavigation() { return useContext(NavigationContext); }
