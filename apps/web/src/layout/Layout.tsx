import { useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import {
  Box,
  ToggleButton,
  ToggleButtonGroup,
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
  DeleteSweepOutlined,
  CurrencyExchangeOutlined,
  SettingsOutlined,
} from "@mui/icons-material";
import { cancelPendingAnalysisRequests } from "../services/api";
import { useAuth } from "../auth/AuthContext";
import { permissionTargetsForLocation } from "../auth/moduleMap";
import LogoutIcon from "@mui/icons-material/Logout";
import { NotificationBell, useNotifications } from "../context/NotificationContext";
import {
  Content as CarbonContent,
  Header as CarbonHeader,
  HeaderGlobalBar as CarbonHeaderGlobalBar,
  HeaderGlobalAction as CarbonHeaderGlobalAction,
  HeaderMenuButton as CarbonHeaderMenuButton,
  HeaderName as CarbonHeaderName,
  SideNav as CarbonSideNav,
  SideNavDivider as CarbonSideNavDivider,
  SideNavItems as CarbonSideNavItems,
  SideNavMenu as CarbonSideNavMenu,
  SideNavMenuItem as CarbonSideNavMenuItem,
  SideNavLink as CarbonSideNavLink,
  Theme as CarbonTheme,
} from "@carbon/react";
import {
  Dashboard as CarbonDashboard,
  Notification as CarbonNotification,
  Logout as CarbonLogout,
  Finance as CarbonFinance,
  Bot as CarbonBot,
  Warning as CarbonWarning,
  ChartLine as CarbonChartLine,
  DataBase as CarbonDataBase,
  Settings as CarbonSettings,
  Tag as CarbonTag,
  UserMultiple as CarbonUserMultiple,
  Cloud as CarbonCloud,
} from "@carbon/react/icons";

const DRAWER_WIDTH = 268;
const COLLAPSED_WIDTH = 72;
const LAYOUT_STORAGE_KEY = "finbase.layout";

type MenuEntry = { path: string; label: string; icon: React.ReactNode };
type MenuGroup = { label: string; icon: React.ReactNode; items: MenuEntry[]; keepVisibleWhenEmpty?: boolean };

const MENU_GROUPS: MenuGroup[] = [
  {
    label: "Panorama",
    icon: <DashboardOutlined fontSize="small" />,
    items: [
      { path: "/custos?tab=visao-executiva", label: "Resumo de custos", icon: <QueryStatsOutlined /> },
    ],
  },
  {
    label: "Operação de IA",
    icon: <SmartToyOutlined fontSize="small" />,
    items: [
      { path: "/ia/capacidade?view=0&tab=0", label: "IA · resumo de capacidade", icon: <SmartToyOutlined /> },
      { path: "/ia/capacidade?view=0&tab=1", label: "IA · consumo de tokens", icon: <QueryStatsOutlined /> },
      { path: "/ia/capacidade?view=0&tab=2", label: "IA · capacidade e operação", icon: <SettingsOutlined /> },
      { path: "/ia/capacidade?view=0&tab=3", label: "IA · recomendações", icon: <LightbulbOutlined /> },
    ],
  },
  {
    label: "Provedores de IA",
    icon: <SmartToyOutlined fontSize="small" />,
    items: [
      { path: "/ia/capacidade?view=1", label: "IA · GitHub Copilot", icon: <SmartToyOutlined /> },
      { path: "/ia/capacidade?view=2&providerTab=executive", label: "IA · Claude executivo", icon: <DashboardOutlined /> },
      { path: "/ia/capacidade?view=2&providerTab=technical", label: "IA · Claude técnico", icon: <ScienceOutlined /> },
      { path: "/ia/capacidade?view=3&providerTab=executive", label: "IA · NotebookLM executivo", icon: <DashboardOutlined /> },
      { path: "/ia/capacidade?view=3&providerTab=technical", label: "IA · NotebookLM técnico", icon: <ScienceOutlined /> },
      { path: "/ia/capacidade?view=4", label: "IA · Aura Brain", icon: <SmartToyOutlined /> },
      { path: "/ia/capacidade?view=5", label: "IA · Vivo Mais", icon: <SmartToyOutlined /> },
      { path: "/ia/capacidade?view=6", label: "IA · Sinfonia", icon: <SmartToyOutlined /> },
      { path: "/ia/capacidade?view=7", label: "IA · Datamesh", icon: <SmartToyOutlined /> },
      { path: "/ia/capacidade?view=8", label: "IA · Wisper", icon: <SmartToyOutlined /> },
    ],
  },
  {
    label: "Adoção de IA",
    icon: <AutoGraphOutlined fontSize="small" />,
    items: [
      { path: "/ia/adocao?tab=0", label: "IA · visão executiva", icon: <DashboardOutlined /> },
      { path: "/ia/adocao?tab=1", label: "IA · eficiência FinOps", icon: <AutoGraphOutlined /> },
      { path: "/ia/adocao?tab=2", label: "IA · uso por pessoa", icon: <ManageAccountsOutlined /> },
    ],
  },
  {
    label: "Alertas",
    icon: <WarningAmberOutlined fontSize="small" />,
    items: [
      { path: "/alertas?tab=0", label: "Visão geral de alertas", icon: <WarningAmberOutlined /> },
      { path: "/alertas?tab=1", label: "Impacto financeiro", icon: <PaidOutlined /> },
      { path: "/alertas?tab=2", label: "Casos em investigação", icon: <QueryStatsOutlined /> },
      { path: "/alertas?tab=3", label: "Eventos esperados", icon: <ScheduleOutlined /> },
      { path: "/alertas?tab=4", label: "Saúde do modelo", icon: <ScienceOutlined /> },
      { path: "/alertas?tab=5", label: "Retrospectivas", icon: <HistoryOutlined /> },
      { path: "/alertas?tab=6", label: "Anomalias confirmadas", icon: <FactCheckOutlined /> },
      { path: "/alertas?tab=7", label: "Configuração de alertas", icon: <SettingsOutlined /> },
    ],
  },
  {
    label: "Unit Economics",
    icon: <QueryStatsOutlined fontSize="small" />,
    items: [],
    keepVisibleWhenEmpty: true,
  },
  {
    label: "Planejamento",
    icon: <AccountBalanceWalletOutlined fontSize="small" />,
    items: [
      { path: "/planejamento?tab=dashboard&executive=orcamento", label: "Histórico do orçamento", icon: <AccountBalanceWalletOutlined /> },
      { path: "/planejamento?tab=dashboard&executive=historia-contrato", label: "História dos contratos", icon: <HistoryOutlined /> },
      { path: "/planejamento?tab=dashboard&executive=compromisso", label: "Consumo de compromissos", icon: <ReceiptLongOutlined /> },
      { path: "/planejamento?tab=orcamento", label: "Orçamentos", icon: <PaidOutlined /> },
      { path: "/planejamento?tab=acuracia-orcamento", label: "Acurácia do orçamento", icon: <AutoGraphOutlined /> },
      { path: "/planejamento?tab=forecast-manual", label: "Projeções manuais", icon: <TrendingUpOutlined /> },
      { path: "/planejamento?tab=visao-contabil&subview=evolucao-dolar", label: "Evolução do dólar", icon: <CurrencyExchangeOutlined /> },
      { path: "/projecoes/acuracia?aba=visao", label: "Acurácia · visão", icon: <AutoGraphOutlined /> },
      { path: "/projecoes/acuracia?aba=desvios", label: "Acurácia · desvios", icon: <WarningAmberOutlined /> },
      { path: "/projecoes/acuracia?aba=acks", label: "Acurácia · justificativas", icon: <FactCheckOutlined /> },
      { path: "/projecoes?tab=config", label: "Configuração e execução", icon: <SettingsOutlined /> },
      { path: "/projecoes?tab=rules", label: "Regras de projeção", icon: <RuleOutlined /> },
      { path: "/projecoes?tab=history", label: "Histórico de projeções", icon: <HistoryOutlined /> },
    ],
  },
  {
    label: "Otimização",
    icon: <SavingsOutlined fontSize="small" />,
    items: [
      { path: "/otimizacao", label: "Oportunidades", icon: <SavingsOutlined /> },
      { path: "/otimizacao?tab=compras-compromissos", label: "Compromissos e compras", icon: <EventAvailableOutlined /> },
      { path: "/otimizacao?tab=desligue&subview=dashboard", label: "Desligamento · painel", icon: <DashboardOutlined /> },
      { path: "/otimizacao?tab=desligue&subview=governance", label: "Desligamento · governança", icon: <RuleOutlined /> },
      { path: "/otimizacao?tab=desligue&subview=exceptions", label: "Desligamento · exceções", icon: <FactCheckOutlined /> },
      { path: "/otimizacao?tab=orfaos&subview=current", label: "Recursos · órfãos atuais", icon: <StorageOutlined /> },
      { path: "/otimizacao?tab=orfaos&subview=savings", label: "Recursos · economia gerada", icon: <SavingsOutlined /> },
    ],
  },
  {
    label: "Dados e operação",
    icon: <StorageOutlined fontSize="small" />,
    items: [
      { path: "/dados/fontes?view=connections", label: "Fontes de dados", icon: <CloudOutlined /> },
      { path: "/dados/fontes?view=integrations", label: "Serviços auxiliares", icon: <SmartToyOutlined /> },
      { path: "/dados/catalogo?tab=destinations", label: "Catálogo de dados", icon: <TableChartOutlined /> },
      { path: "/dados/catalogo?tab=relationship", label: "Relacionamentos", icon: <AccountTreeOutlined /> },
      { path: "/dados/fluxos", label: "Fluxos de dados", icon: <AccountTreeOutlined /> },
      { path: "/operacoes/execucoes?tab=data", label: "Execuções de dados", icon: <SyncOutlined /> },
      { path: "/operacoes/execucoes?tab=forecast&subtab=forecast", label: "Execuções de forecast", icon: <TrendingUpOutlined /> },
      { path: "/operacoes/execucoes?tab=forecast&subtab=accuracy", label: "Acurácia do forecast", icon: <AutoGraphOutlined /> },
      { path: "/operacoes/execucoes?tab=shared", label: "Custo compartilhado", icon: <PaidOutlined /> },
      { path: "/operacoes/execucoes?tab=gold&subtab=cubes", label: "Materialização de cubos", icon: <DataObjectOutlined /> },
      { path: "/operacoes/execucoes?tab=gold&subtab=ai-cost", label: "Relacionamentos de custo da IA", icon: <SmartToyOutlined /> },
      { path: "/operacoes/execucoes?tab=extraction", label: "Extrações", icon: <CloudOutlined /> },
      { path: "/operacoes/execucoes?tab=schedules", label: "Agendamentos", icon: <ScheduleOutlined /> },
      { path: "/operacoes/execucoes?tab=logs", label: "Histórico de execuções", icon: <HistoryOutlined /> },
      { path: "/dados/explorador?tab=destino", label: "Explorador · tabelas de destino", icon: <TableChartOutlined /> },
      { path: "/dados/explorador?tab=fontes", label: "Explorador · fontes de origem", icon: <CloudOutlined /> },
      { path: "/dados/explorador?tab=iceberg&layer=bronze", label: "Base de origem", icon: <StorageOutlined /> },
      { path: "/dados/explorador?tab=iceberg&layer=silver", label: "Base preparada", icon: <DataObjectOutlined /> },
      { path: "/dados/explorador?tab=iceberg&layer=gold", label: "Base consolidada", icon: <PaidOutlined /> },
      { path: "/dados/explorador?tab=staging", label: "Área temporária", icon: <DeleteSweepOutlined /> },
      { path: "/dados/explorador?tab=pivot", label: "Explorador · tabelas dinâmicas", icon: <AutoGraphOutlined /> },
      { path: "/dados/explorador?tab=busca", label: "Explorador · busca na fonte", icon: <ScienceOutlined /> },
    ],
  },
  {
    label: "Governança",
    icon: <RuleOutlined fontSize="small" />,
    items: [
      { path: "/governanca?tab=organization", label: "Organização", icon: <CorporateFareOutlined /> },
      { path: "/governanca?tab=classification", label: "Classificação de projetos", icon: <LocalOfferOutlined /> },
      { path: "/governanca?tab=rules&subtab=adjustments", label: "Regras de ajustes", icon: <SettingsOutlined /> },
      { path: "/governanca?tab=rules&subtab=clone-rules", label: "Reservas e Savings Plans", icon: <SavingsOutlined /> },
      { path: "/governanca?tab=financial-contracts", label: "Contratos e compromissos", icon: <ReceiptLongOutlined /> },
      { path: "/governanca?tab=cost-relationship", label: "Cubos analíticos", icon: <DataObjectOutlined /> },
    ],
  },
  {
    label: "Administração",
    icon: <ManageAccountsOutlined fontSize="small" />,
    items: [
      { path: "/administracao/acessos?tab=users", label: "Pessoas e acessos", icon: <ManageAccountsOutlined /> },
      { path: "/administracao/acessos?tab=groups", label: "Grupos", icon: <CorporateFareOutlined /> },
      { path: "/administracao/acessos?tab=roles", label: "Funções e permissões", icon: <VpnKeyOutlined /> },
      { path: "/administracao/auditoria", label: "Trilha de auditoria", icon: <FactCheckOutlined /> },
      { path: "/administracao/identidade", label: "Login corporativo", icon: <VpnKeyOutlined /> },
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
  const { unreadCount, openDialog: openNotifications } = useNotifications();
  const canAdmin = hasPerm("users", "read") || hasPerm("groups", "read") || hasPerm("roles", "read");
  const visibleMenuGroups = useMemo(() => MENU_GROUPS.map(group => ({
    ...group,
    items: group.items.filter(item => {
      if (item.path.startsWith("/alertas") && !showAnomaliesMenu) return false;
      if (item.path === "/administracao/auditoria") return hasPerm("audit", "read");
      if (item.path === "/administracao/identidade") return hasPerm("auth", "read");
      if (item.path.startsWith("/administracao/acessos?tab=groups")) return hasPerm("groups", "read");
      if (item.path.startsWith("/administracao/acessos?tab=roles")) return hasPerm("roles", "read");
      if (item.path === "/administracao/acessos?tab=users") return canAdmin;
      const { pathname, search } = menuPath(item.path);
      const targets = permissionTargetsForLocation(pathname, search);
      return !targets || targets.some(target => hasPerm(target.module, "read", target.scope));
    }),
  })).filter(group => group.items.length > 0 || group.keepVisibleWhenEmpty), [canAdmin, hasPerm]);
  const navigate = useNavigate();
  const location = useLocation();
  const [expanded, setExpanded] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openGroups, setOpenGroups] = useState<string[]>([]);
  const [layoutMode, setLayoutMode] = useState<"finbase" | "carbon">(() =>
    window.localStorage.getItem(LAYOUT_STORAGE_KEY) === "carbon" ? "carbon" : "finbase",
  );
  const drawerWidth = expanded ? DRAWER_WIDTH : COLLAPSED_WIDTH;
  const isCarbon = layoutMode === "carbon";
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
  })?.label ?? visibleMenuGroups.flatMap(group => group.items).find(item => menuPath(item.path).pathname === location.pathname)?.label ?? "Workspace";
  const activeGroupLabel = visibleMenuGroups.find(group => group.items.some(item => menuPath(item.path).pathname === location.pathname))?.label;

  useEffect(() => {
    if (activeGroupLabel) setOpenGroups([activeGroupLabel]);
  }, [activeGroupLabel]);

  useEffect(() => {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, layoutMode);
    document.documentElement.dataset.finbaseLayout = layoutMode;
  }, [layoutMode]);

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
              <ListItemButton disabled={group.items.length === 0} onClick={() => expanded ? toggleGroup(group.label) : (setExpanded(true), setOpenGroups(current => current.includes(group.label) ? current : [...current, group.label]))}
                aria-expanded={expanded ? isOpen : undefined}
                sx={{ minHeight: 42, px: 1.25, borderRadius: 1.5, color: groupActive ? "primary.main" : "text.secondary", "&:hover": { bgcolor: "#F3F7F8", color: "primary.dark" } }}>
                <ListItemIcon sx={{ minWidth: 34, color: "inherit" }}>{group.icon}</ListItemIcon>
                {expanded && <ListItemText primary={group.label} primaryTypographyProps={{ fontSize: 13, fontWeight: 700 }} />}
                {expanded && group.items.length > 0 && (isOpen ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />)}
              </ListItemButton>
            </Tooltip>
            <Collapse in={expanded && isOpen && group.items.length > 0} timeout="auto" unmountOnExit>
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

  const carbonNavigation = (
    <CarbonSideNav aria-label="Navegação principal" expanded={isMobile ? mobileOpen : expanded} isPersistent={!isMobile} isChildOfHeader>
      <CarbonSideNavItems isSideNavExpanded={isMobile ? mobileOpen : expanded}>
        {visibleMenuGroups.map(group => {
          const activeGroup = group.items.some(item => menuPath(item.path).pathname === location.pathname);
          const groupIcon = group.label === "Panorama" ? CarbonDashboard
            : group.label.includes("IA") ? CarbonBot
              : group.label === "Alertas" ? CarbonWarning
                : group.label === "Unit Economics" ? CarbonChartLine
                  : group.label === "Planejamento" ? CarbonFinance
                    : group.label === "Otimização" ? CarbonChartLine
                      : group.label === "Dados e operação" ? CarbonDataBase
                        : group.label === "Governança" ? CarbonSettings
                          : group.label === "Administração" ? CarbonUserMultiple
                            : CarbonCloud;
          return group.items.length === 1 ? (
            <CarbonSideNavLink key={group.label} href={group.items[0].path} renderIcon={groupIcon}
              isActive={matchesMenuPath(group.items[0].path, location.pathname, location.search)}
              onClick={() => { navigate(group.items[0].path); setMobileOpen(false); }}>
              {group.label}
            </CarbonSideNavLink>
          ) : (
            <CarbonSideNavMenu key={group.label} title={group.label} renderIcon={groupIcon} isActive={activeGroup}
              isSideNavExpanded={isMobile ? mobileOpen : expanded} defaultExpanded={openGroups.includes(group.label)}>
              {group.items.map(item => (
                <CarbonSideNavMenuItem key={item.path} href={item.path} as="button"
                  isActive={matchesMenuPath(item.path, location.pathname, location.search)}
                  onClick={() => { navigate(item.path); setMobileOpen(false); }}>
                  {item.label}
                </CarbonSideNavMenuItem>
              ))}
            </CarbonSideNavMenu>
          );
        })}
      </CarbonSideNavItems>
    </CarbonSideNav>
  );

  if (isCarbon) {
    return (
      <CarbonTheme theme="white" className="finbase-carbon-theme">
        <Box className="finbase-carbon-shell" sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
          <CarbonHeader aria-label="Finbase Cloud">
            <CarbonHeaderMenuButton aria-label={(isMobile ? mobileOpen : expanded) ? "Recolher menu" : "Abrir menu"} isActive={isMobile ? mobileOpen : expanded} onClick={() => isMobile ? setMobileOpen(value => !value) : setExpanded(value => !value)} />
            <CarbonHeaderName href="/" prefix="">finbase.cloud</CarbonHeaderName>
            <CarbonHeaderGlobalBar>
              <Box className="finbase-carbon-layout-switch" role="group" aria-label="Escolher layout">
                <button type="button" aria-pressed={!isCarbon} onClick={() => setLayoutMode("finbase")}>Finbase</button>
                <button type="button" aria-pressed={isCarbon} onClick={() => setLayoutMode("carbon")}>Carbon</button>
              </Box>
              <CarbonHeaderGlobalAction aria-label={`Notificações${unreadCount ? `, ${unreadCount} não lidas` : ""}`} onClick={openNotifications}>
                <CarbonNotification size={20} />
                {unreadCount > 0 && <span className="finbase-carbon-notification-count">{unreadCount > 99 ? "99+" : unreadCount}</span>}
              </CarbonHeaderGlobalAction>
              <CarbonHeaderGlobalAction aria-label={`Sair${user?.username ? ` de ${user.username}` : ""}`} onClick={() => logout()}><CarbonLogout size={20} /></CarbonHeaderGlobalAction>
            </CarbonHeaderGlobalBar>
          </CarbonHeader>
          {!isMobile && carbonNavigation}
          {isMobile && mobileOpen && carbonNavigation}
          <CarbonContent id="main-content" className="finbase-carbon-main">
            <Box className="finbase-carbon-page-title">{currentLabel}</Box>
            {(() => {
              const targets = permissionTargetsForLocation(location.pathname, location.search);
              if (targets && !targets.some(target => hasPerm(target.module, "read", target.scope))) {
                const modules = targets.map(target => target.scope ? `${target.module}:${target.scope}` : target.module).join(" / ");
                return <Box sx={{ p: 6, textAlign: "center" }}><Typography variant="h6" sx={{ mb: 1 }}>Acesso negado</Typography><Typography color="text.secondary">Você não tem permissão de leitura no módulo <b>{modules}</b>. Solicite acesso ao administrador se precisar utilizá-lo.</Typography></Box>;
              }
              return <Outlet />;
            })()}
          </CarbonContent>
          <Box component="footer" className="finbase-carbon-footer">
            <span>© {new Date().getFullYear()} finbase.cloud</span>
            <span>FinOps · Cloud cost management</span>
          </Box>
        </Box>
      </CarbonTheme>
    );
  }

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
            <ToggleButtonGroup size="small" exclusive value={layoutMode} onChange={(_, value: "finbase" | "carbon" | null) => value && setLayoutMode(value)} aria-label="Escolher layout" sx={{ mr: 1, "& .MuiToggleButton-root": { px: 1, py: 0.35, fontSize: 11 } }}>
              <ToggleButton value="finbase" aria-label="Layout Finbase">Finbase</ToggleButton>
              <ToggleButton value="carbon" aria-label="Layout Carbon">Carbon</ToggleButton>
            </ToggleButtonGroup>
            <NotificationBell />
            <Typography variant="body2" noWrap sx={{ ml: 1.5, mr: 0.5, maxWidth: 180, color: "text.secondary", display: { xs: "none", sm: "block" } }}>{user?.username}</Typography>
            <Tooltip title="Sair"><IconButton onClick={() => logout()} size="small" aria-label="Sair"><LogoutIcon fontSize="small" /></IconButton></Tooltip>
          </Toolbar>
        </AppBar>
        <Box component="main" sx={{ flex: 1, minHeight: 0, overflow: "auto", bgcolor: "background.default", p: { xs: 1.5, sm: 2.5, lg: 3 } }}>
          {(() => {
            const targets = permissionTargetsForLocation(location.pathname, location.search);
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
