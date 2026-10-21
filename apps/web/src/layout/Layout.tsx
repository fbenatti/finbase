import { useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import {
  Box,
  Drawer,
  AppBar,
  Toolbar,
  IconButton,
  Typography,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  Tooltip,
  Collapse,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import {
  Menu as MenuIcon,
  ChevronLeft as ChevronLeftIcon,
  ExpandLess,
  ExpandMore,
  DashboardOutlined,
  QueryStatsOutlined,
  SavingsOutlined,
  WarningAmberOutlined,
  AccountBalanceWalletOutlined,
  TrendingUpOutlined,
  PaidOutlined,
  ReceiptLongOutlined,
  CloudOutlined,
  TableChartOutlined,
  AccountTreeOutlined,
  SyncOutlined,
  RuleOutlined,
  CorporateFareOutlined,
  LocalOfferOutlined,
  EventAvailableOutlined,
  LightbulbOutlined,
  ManageAccountsOutlined,
  FactCheckOutlined,
  VpnKeyOutlined,
  ScienceOutlined,
  SmartToyOutlined,
  StorageOutlined,
  ShowChartOutlined,
  ScheduleOutlined,
  HistoryOutlined,
  AutoGraphOutlined,
  DataObjectOutlined,
  CurrencyExchangeOutlined,
  SettingsOutlined,
} from "@mui/icons-material";
import { cancelPendingAnalysisRequests } from "../services/api";
import { useAuth } from "../auth/AuthContext";
import { permissionTargetsForPath } from "../auth/moduleMap";
import LogoutIcon from "@mui/icons-material/Logout";
import { NotificationBell } from "../context/NotificationContext";

const DRAWER_WIDTH = 268;
const COLLAPSED_WIDTH = 72;

type MenuEntry = { path: string; label: string; icon: React.ReactNode };
type MenuGroup = { label: string; icon: React.ReactNode; items: MenuEntry[] };

const MENU_GROUPS: MenuGroup[] = [
  {
    label: "Panorama",
    icon: <DashboardOutlined fontSize="small" />,
    items: [
      { path: "/gold-dashboard?tab=visao-executiva", label: "Resumo de custos", icon: <QueryStatsOutlined /> },
      { path: "/gold-dashboard?tab=geral", label: "Custos gerais", icon: <PaidOutlined /> },
      { path: "/gold-dashboard?tab=evolucao", label: "Evolução de custos", icon: <TrendingUpOutlined /> },
      { path: "/gold-dashboard?tab=organizacao", label: "Custos por organização", icon: <CorporateFareOutlined /> },
      { path: "/gold-dashboard?tab=servicos", label: "Custos por serviço", icon: <CloudOutlined /> },
      { path: "/gold-dashboard?tab=investigacao", label: "Investigar variações", icon: <QueryStatsOutlined /> },
      { path: "/gold-dashboard?tab=decisao", label: "Decisões", icon: <FactCheckOutlined /> },
      { path: "/gold-dashboard?tab=drilldown", label: "Detalhamento", icon: <DataObjectOutlined /> },
      { path: "/gold-dashboard?tab=data-quality", label: "Qualidade dos dados", icon: <ScienceOutlined /> },
      { path: "/visoes-consumo-ia", label: "Economia da IA", icon: <SmartToyOutlined /> },
      { path: "/savings", label: "Oportunidades", icon: <SavingsOutlined /> },
      { path: "/anomalies", label: "Alertas de custo", icon: <WarningAmberOutlined /> },
    ],
  },
  {
    label: "Planejamento",
    icon: <AccountBalanceWalletOutlined fontSize="small" />,
    items: [
      { path: "/financial-planning?tab=dashboard", label: "Resumo financeiro", icon: <AccountBalanceWalletOutlined /> },
      { path: "/financial-planning?tab=dashboard&executive=historia-contrato", label: "História dos contratos", icon: <HistoryOutlined /> },
      { path: "/financial-planning?tab=dashboard&executive=compromisso", label: "Consumo de compromissos", icon: <ReceiptLongOutlined /> },
      { path: "/financial-planning?tab=orcamento", label: "Orçamentos", icon: <PaidOutlined /> },
      { path: "/financial-planning?tab=acuracia-orcamento", label: "Acurácia do orçamento", icon: <AutoGraphOutlined /> },
      { path: "/financial-planning?tab=forecast-manual", label: "Projeções manuais", icon: <TrendingUpOutlined /> },
      { path: "/financial-planning?tab=carta-credito", label: "Créditos", icon: <ReceiptLongOutlined /> },
      { path: "/financial-planning?tab=defesa-orcamentaria", label: "Defesa orçamentária", icon: <FactCheckOutlined /> },
      { path: "/financial-planning?tab=visao-contabil", label: "Pedidos SAP e câmbio", icon: <CurrencyExchangeOutlined /> },
      { path: "/forecast?tab=accuracy", label: "Acurácia de projeções", icon: <AutoGraphOutlined /> },
      { path: "/forecast?tab=config", label: "Configuração e execução", icon: <SettingsOutlined /> },
      { path: "/forecast?tab=rules", label: "Regras de projeção", icon: <RuleOutlined /> },
      { path: "/forecast?tab=history", label: "Histórico de projeções", icon: <HistoryOutlined /> },
    ],
  },
  {
    label: "Otimização",
    icon: <SavingsOutlined fontSize="small" />,
    items: [
      { path: "/savings?tab=calcular", label: "Ações de economia", icon: <SavingsOutlined /> },
      { path: "/savings?tab=compras-compromissos", label: "Compromissos e compras", icon: <EventAvailableOutlined /> },
      { path: "/savings?tab=desligue", label: "Desligamento programado", icon: <SettingsOutlined /> },
      { path: "/savings?tab=orfaos", label: "Recursos sem uso", icon: <StorageOutlined /> },
      { path: "/governance?tab=reservas", label: "Reservas Azure", icon: <EventAvailableOutlined /> },
    ],
  },
  {
    label: "Dados e operação",
    icon: <StorageOutlined fontSize="small" />,
    items: [
      { path: "/connections", label: "Integrações", icon: <CloudOutlined /> },
      { path: "/tables?tab=destinations", label: "Catálogo de dados", icon: <TableChartOutlined /> },
      { path: "/tables?tab=relationship", label: "Relacionamentos", icon: <AccountTreeOutlined /> },
      { path: "/pipeline", label: "Fluxos de dados", icon: <AccountTreeOutlined /> },
      { path: "/reprocess?tab=data", label: "Execuções de dados", icon: <SyncOutlined /> },
      { path: "/reprocess?tab=forecast", label: "Execuções de forecast", icon: <TrendingUpOutlined /> },
      { path: "/reprocess?tab=forecast&subtab=accuracy", label: "Acurácia do forecast", icon: <AutoGraphOutlined /> },
      { path: "/reprocess?tab=shared", label: "Custo compartilhado", icon: <PaidOutlined /> },
      { path: "/reprocess?tab=gold", label: "Modelos analíticos", icon: <DataObjectOutlined /> },
      { path: "/reprocess?tab=gold&subtab=ai-cost", label: "Relacionamentos de custo da IA", icon: <SmartToyOutlined /> },
      { path: "/reprocess?tab=extraction", label: "Extrações", icon: <CloudOutlined /> },
      { path: "/reprocess?tab=schedules", label: "Agendamentos", icon: <ScheduleOutlined /> },
      { path: "/reprocess?tab=logs", label: "Histórico de execuções", icon: <HistoryOutlined /> },
      { path: "/analysis?tab=fontes", label: "Explorador de dados", icon: <ScienceOutlined /> },
    ],
  },
  {
    label: "Governança",
    icon: <RuleOutlined fontSize="small" />,
    items: [
      { path: "/governance?tab=organization", label: "Organização", icon: <CorporateFareOutlined /> },
      { path: "/governance?tab=classification", label: "Classificação de projetos", icon: <LocalOfferOutlined /> },
      { path: "/governance?tab=rules", label: "Regras financeiras", icon: <RuleOutlined /> },
      { path: "/governance?tab=rules&subtab=adjustments", label: "Regras de ajustes", icon: <SettingsOutlined /> },
      { path: "/governance?tab=rules&subtab=clone-rules", label: "Reservas e Savings Plans", icon: <SavingsOutlined /> },
      { path: "/governance?tab=financial-contracts", label: "Contratos e compromissos", icon: <ReceiptLongOutlined /> },
      { path: "/governance?tab=cost-relationship", label: "Cubos analíticos", icon: <DataObjectOutlined /> },
      { path: "/governance?tab=tags", label: "Políticas de tags", icon: <LocalOfferOutlined /> },
    ],
  },
  {
    label: "Administração",
    icon: <ManageAccountsOutlined fontSize="small" />,
    items: [
      { path: "/ideas?tab=portal", label: "Portal de iniciativas", icon: <LightbulbOutlined /> },
      { path: "/ideas?tab=backlog", label: "Fila priorizada", icon: <ScheduleOutlined /> },
      { path: "/ideas?tab=canvas", label: "Canvas de entrega", icon: <AccountTreeOutlined /> },
      { path: "/ideas?tab=dashboard", label: "Resultados das iniciativas", icon: <DashboardOutlined /> },
      { path: "/admin", label: "Pessoas e acessos", icon: <ManageAccountsOutlined /> },
      { path: "/admin?tab=groups", label: "Grupos", icon: <CorporateFareOutlined /> },
      { path: "/admin?tab=roles", label: "Funções e permissões", icon: <VpnKeyOutlined /> },
      { path: "/admin/audit", label: "Trilha de auditoria", icon: <FactCheckOutlined /> },
      { path: "/admin/sso", label: "Login corporativo", icon: <VpnKeyOutlined /> },
    ],
  },
];

const showAnomaliesMenu = import.meta.env.VITE_APP_ENV !== "production";
const menuPath = (target: string) => {
  const url = new URL(target, window.location.origin);
  return { pathname: url.pathname, search: url.search };
};
const matchesMenuPath = (target: string, pathname: string, search: string) => {
  const route = menuPath(target);
  if (pathname !== route.pathname) return false;
  const expected = new URLSearchParams(route.search);
  const actual = new URLSearchParams(search);
  if (expected.size === 0) return actual.size === 0;
  return Array.from(expected.entries()).every(([key, value]) => actual.get(key) === value);
};

export default function Layout() {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));
  const { hasPerm, user, logout } = useAuth();
  const canAdmin = hasPerm("users", "read") || hasPerm("groups", "read") || hasPerm("roles", "read");
  const canIdeas = hasPerm("ideas", "read");
  const visibleMenuGroups = useMemo(() => MENU_GROUPS.map(group => ({
    ...group,
    items: group.items.filter(item => {
      if (item.path.startsWith("/anomalies") && !showAnomaliesMenu) return false;
      if (item.path.startsWith("/ideas")) return canIdeas;
      if (item.path === "/admin/audit") return hasPerm("audit", "read");
      if (item.path === "/admin/sso") return hasPerm("auth", "read");
      if (item.path.startsWith("/admin?tab=groups")) return hasPerm("groups", "read");
      if (item.path.startsWith("/admin?tab=roles")) return hasPerm("roles", "read");
      if (item.path === "/admin") return canAdmin;
      const { pathname } = menuPath(item.path);
      const targets = permissionTargetsForPath(pathname);
      return !targets || targets.some(target => hasPerm(target.module, "read", target.scope));
    }),
  })).filter(group => group.items.length > 0), [canAdmin, canIdeas, hasPerm]);
  const navigate = useNavigate();
  const location = useLocation();
  const [expanded, setExpanded] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openGroups, setOpenGroups] = useState<string[]>(["Panorama"]);
  const drawerWidth = expanded ? DRAWER_WIDTH : COLLAPSED_WIDTH;
  const skipRouteCancelRef = useRef(true);

  useEffect(() => {
    if (skipRouteCancelRef.current) {
      skipRouteCancelRef.current = false;
      return;
    }
    cancelPendingAnalysisRequests();
  }, [location.pathname]);

  const currentLabel = visibleMenuGroups.flatMap(group => group.items).find(item => {
    return matchesMenuPath(item.path, location.pathname, location.search);
  })?.label ?? "Workspace";

  const toggleGroup = (label: string) => setOpenGroups(current =>
    current.includes(label) ? current.filter(item => item !== label) : [...current, label],
  );
  const drawerContent = (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", bgcolor: "#fff" }}>
      <Box sx={{ height: 72, display: "flex", alignItems: "center", justifyContent: expanded ? "space-between" : "center", px: expanded ? 2 : 1 }}>
        {expanded && <Box component="img" src="/brand/finbase-logo.svg" alt="finbase.cloud" sx={{ display: "block", width: 196, height: "auto" }} />}
        {!expanded && <Box component="img" src="/brand/finbase-logo.svg" alt="finbase.cloud" sx={{ width: 42, height: 42, objectFit: "cover", objectPosition: "left", borderRadius: 2 }} />}
        {!isMobile && <Tooltip title={expanded ? "Recolher navegação" : "Expandir navegação"}><IconButton size="small" onClick={() => setExpanded(value => !value)} aria-label={expanded ? "Recolher navegação" : "Expandir navegação"}>{expanded ? <ChevronLeftIcon /> : <MenuIcon />}</IconButton></Tooltip>}
      </Box>
      <Divider />
      <Typography variant="overline" sx={{ px: 2.5, pt: 2, pb: 0.75, color: "text.secondary", fontWeight: 700, letterSpacing: 1.1, display: expanded ? "block" : "none" }}>Workspace</Typography>
      <Box sx={{ flex: 1, overflowY: "auto", px: 1, pb: 1 }}>
        {visibleMenuGroups.map(group => {
          const isOpen = openGroups.includes(group.label);
          const groupActive = group.items.some(item => location.pathname === menuPath(item.path).pathname);
          return <Box key={group.label} sx={{ mb: 0.5 }}>
            <Tooltip title={expanded ? "" : group.label} placement="right">
              <ListItemButton onClick={() => expanded ? toggleGroup(group.label) : (setExpanded(true), setOpenGroups(current => current.includes(group.label) ? current : [...current, group.label]))}
                aria-expanded={expanded ? isOpen : undefined}
                sx={{ minHeight: 42, px: 1.25, borderRadius: 1.5, color: groupActive ? "primary.main" : "text.secondary", "&:hover": { bgcolor: "#F3F7F8", color: "primary.dark" } }}>
                <ListItemIcon sx={{ minWidth: 34, color: "inherit" }}>{group.icon}</ListItemIcon>
                {expanded && <ListItemText primary={group.label} primaryTypographyProps={{ fontSize: 13, fontWeight: 700 }} />}
                {expanded && (isOpen ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />)}
              </ListItemButton>
            </Tooltip>
            <Collapse in={expanded && isOpen} timeout="auto" unmountOnExit>
              <List disablePadding sx={{ mt: 0.25, mb: 1 }}>
                {group.items.map(item => {
                  const active = matchesMenuPath(item.path, location.pathname, location.search);
                  return <ListItemButton key={item.path} selected={active} onClick={() => { navigate(item.path); setMobileOpen(false); }}
                    sx={{ minHeight: 38, pl: 2, pr: 1.25, ml: 2.1, borderRadius: 1.4, borderLeft: "2px solid", borderColor: active ? "secondary.main" : "transparent", color: active ? "primary.dark" : "text.secondary", "&.Mui-selected": { bgcolor: "#EAF5F3", color: "#0D6B63" }, "&.Mui-selected:hover": { bgcolor: "#E1F0ED" }, "&:hover": { bgcolor: "#F3F7F8" } }}>
                    <ListItemIcon sx={{ minWidth: 30, color: "inherit" }}>{item.icon}</ListItemIcon>
                    <ListItemText primary={item.label} primaryTypographyProps={{ fontSize: 12.5, fontWeight: active ? 700 : 500, noWrap: true }} />
                  </ListItemButton>;
                })}
              </List>
            </Collapse>
          </Box>;
        })}
      </Box>
      <Divider />
      <Box sx={{ px: 2, py: 1.5, display: expanded ? "block" : "none" }}>
        <Typography variant="caption" color="text.secondary">FinOps com clareza</Typography>
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: "flex", height: "100%", bgcolor: "background.default" }}>
      {isMobile ? <Drawer variant="temporary" open={mobileOpen} onClose={() => setMobileOpen(false)} ModalProps={{ keepMounted: true }} sx={{ "& .MuiDrawer-paper": { width: DRAWER_WIDTH, border: 0 } }}>{drawerContent}</Drawer>
        : <Drawer variant="permanent" sx={{ width: drawerWidth, flexShrink: 0, transition: "width 0.2s", "& .MuiDrawer-paper": { width: drawerWidth, transition: "width 0.2s", overflowX: "hidden", boxSizing: "border-box", border: 0, borderRight: "1px solid #E4EAF0" } }}>{drawerContent}</Drawer>}
      <Box sx={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1, height: "100%" }}>
        <AppBar position="static" elevation={0} color="inherit" sx={{ borderBottom: "1px solid", borderColor: "divider", bgcolor: "rgba(255,255,255,0.94)" }}>
          <Toolbar sx={{ minHeight: "64px !important", px: { xs: 1.5, md: 3 } }}>
            {isMobile && <IconButton edge="start" onClick={() => setMobileOpen(true)} aria-label="Abrir menu" sx={{ mr: 1 }}><MenuIcon /></IconButton>}
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="h6" noWrap sx={{ fontSize: 16, fontWeight: 700 }}>{currentLabel}</Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: { xs: "none", sm: "block" } }}>finbase.cloud</Typography>
            </Box>
            <Box sx={{ flexGrow: 1 }} />
            <NotificationBell />
            <Typography variant="body2" noWrap sx={{ ml: 1.5, mr: 0.5, maxWidth: 180, color: "text.secondary", display: { xs: "none", sm: "block" } }}>{user?.username}</Typography>
            <Tooltip title="Sair"><IconButton onClick={() => logout()} size="small" aria-label="Sair"><LogoutIcon fontSize="small" /></IconButton></Tooltip>
          </Toolbar>
        </AppBar>
        <Box component="main" sx={{ flex: 1, minHeight: 0, overflow: "auto", bgcolor: "background.default", p: { xs: 1.5, sm: 2.5, lg: 3 } }}>
          {(() => {
            const targets = permissionTargetsForPath(location.pathname);
            if (targets && !targets.some(target => hasPerm(target.module, "read", target.scope))) {
              const modules = targets.map(target => target.scope ? `${target.module}:${target.scope}` : target.module).join(" / ");
              return <Box sx={{ p: 6, textAlign: "center" }}><Typography variant="h6" sx={{ mb: 1 }}>Acesso negado</Typography><Typography color="text.secondary">Você não tem permissão de leitura no módulo <b>{modules}</b>. Solicite acesso ao administrador se precisar utilizá-lo.</Typography></Box>;
            }
            return <Outlet />;
          })()}
        </Box>
        <Box component="footer" sx={{ minHeight: 44, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1, px: { xs: 2, md: 3 }, borderTop: "1px solid", borderColor: "divider", bgcolor: "#fff" }}>
          <Typography variant="caption" color="text.secondary">© {new Date().getFullYear()} finbase.cloud</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: { xs: "none", sm: "block" } }}>FinOps · Cloud cost management</Typography>
        </Box>
      </Box>
    </Box>
  );
}
