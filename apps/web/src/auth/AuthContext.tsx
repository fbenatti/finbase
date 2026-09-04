import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiLogin, apiLogout, apiMe, onSessionExpired } from "../services/api";
import { setAccessToken } from "./tokenStore";
import { AuthUser, userHasPerm, Permission } from "./types";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasPerm: (module: string, action: string) => boolean;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
  hasPerm: () => false,
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    onSessionExpired(() => {
      setUser(null);
      navigate("/login", { replace: true });
    });
    // Boot: tenta /auth/me. Com bypass de DEV ativo responde 200 como dev-admin
    // (pula a tela de login); com auth ligada e sem token retorna 401.
    apiMe()
      .then((me) => setUser(me))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [navigate]);

  const login = useCallback(async (username: string, password: string) => {
    await apiLogin(username, password);
    const me = await apiMe();
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
    navigate("/login", { replace: true });
  }, [navigate]);

  const hasPerm = useCallback(
    (module: string, action: string) => userHasPerm(user, module, action),
    [user]
  );

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, hasPerm }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
