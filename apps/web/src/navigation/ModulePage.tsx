import { useEffect, useLayoutEffect, useState, type ComponentType } from "react";
import { Alert, Box, Button, CircularProgress, Paper, Stack, Typography } from "@mui/material";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, formatApiError } from "../services/api";
import { MenuViewContext, useNavigation } from "./NavigationContext";
import { resolveCatalogRoute, widgetSearch } from "./routes";
import { setActiveModule } from "./activeModule";
import type { NavigationModule } from "./types";

export function ModuleIndex({ item }: { item: NavigationModule }) {
  const { items } = useNavigation();
  const navigate = useNavigate();
  return <Stack spacing={2}><Typography variant="h4">{item.label}</Typography><Typography color="text.secondary">Escolha uma tela para continuar.</Typography>{items.filter(child => child.parent_code === item.code && child.allowed && child.visible !== false).map(child => <Paper variant="outlined" key={child.code} sx={{ p: 2, display: "flex", alignItems: "center", justifyContent: "space-between" }}><Typography>{child.label}</Typography><Button onClick={() => navigate(child.path)}>Abrir</Button></Paper>)}</Stack>;
}

export function ModulePage({ item, renderers }: { item: NavigationModule; renderers: Record<string, ComponentType> }) {
  const [access, setAccess] = useState<{ code: string; granted: boolean; error: string } | null>(null);
  const { items } = useNavigation();
  const current = items.find(entry => entry.code === item.code) || item;
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    let cancelled = false;
    setAccess(null);
    api.get(`/navegacao/acessos/${item.code}`).then(() => { if (!cancelled) setAccess({ code: item.code, granted: true, error: "" }); })
      .catch(e => { if (!cancelled) setAccess({ code: item.code, granted: false, error: formatApiError(e, "Acesso indisponível no momento.") }); });
    return () => { cancelled = true; };
  }, [item.code, current.allowed]);
  useLayoutEffect(() => {
    setActiveModule(item.code);
    return () => setActiveModule(null);
  }, [item.code]);
  useEffect(() => {
    const search = widgetSearch(new URLSearchParams(location.search)).toString();
    if ((search ? `?${search}` : "") !== location.search) navigate({ pathname: item.path, search }, { replace: true });
  }, [location.search, item.path, navigate]);
  if (!current.allowed) return <Alert severity="warning">Esta tela não está disponível para o seu cliente ou para o seu usuário.</Alert>;
  if (!access || access.code !== item.code) return <Box sx={{ p: 4, textAlign: "center" }}><CircularProgress /></Box>;
  if (!access.granted) return <Alert severity="error">{access.error}</Alert>;
  const Page = renderers[item.renderer];
  return <MenuViewContext.Provider value={item}>{item.renderer === "ModuleIndex" ? <ModuleIndex item={item} /> : Page ? <Page /> : <Alert severity="error">Esta tela ainda não foi configurada.</Alert>}</MenuViewContext.Provider>;
}

export function CatalogLegacyRedirect() {
  const { items } = useNavigation();
  const location = useLocation();
  const target = resolveCatalogRoute(items, location.pathname, new URLSearchParams(location.search));
  if (!target) return <Alert severity="warning">Esta página não existe ou foi movida.</Alert>;
  return <Navigate to={{ pathname: target.path, search: widgetSearch(new URLSearchParams(location.search)).toString(), hash: location.hash }} replace />;
}
export function DefaultModule() {
  const { items } = useNavigation();
  const target = items.find(item => item.allowed && item.visible !== false && item.renderer !== "ModuleIndex") || items.find(item => item.allowed);
  return target ? <Navigate to={target.path} replace /> : <Alert severity="warning">Nenhuma tela liberada para o seu usuário. Peça acesso ao administrador.</Alert>;
}
