import WorkspaceSearch from "../navigation/WorkspaceSearch";
import { searchWorkspaceMenus } from "../navigation/menuSearchIndex";
import { useNavigation } from "../navigation/NavigationContext";
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
import { ThemeProvider, useTheme } from "@mui/material/styles";
import { createAppTheme } from "../theme/theme";
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
  ManageAccountsOutlined,
  FactCheckOutlined,
  VpnKeyOutlined,
  ScienceOutlined,
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
import { dataDisplayText, moduleDisplayName } from "../utils/dataPresentation";
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
  Warning as CarbonWarning,
  ChartLine as CarbonChartLine,
  DataBase as CarbonDataBase,
  Settings as CarbonSettings,
  Tag as CarbonTag,
  UserMultiple as CarbonUserMultiple,
  Cloud as CarbonCloud,
  Search as CarbonSearch,
} from "@carbon/react/icons";

const DRAWER_WIDTH = 268;
const COLLAPSED_WIDTH = 72;
const LAYOUT_STORAGE_KEY = "finbase.layout";

type MenuEntry = { path: string; label: string; icon: React.ReactNode };
type MenuGroup = { label: string; icon: React.ReactNode; items: MenuEntry[] };

const MENU_ICONS = { AccountBalanceWalletOutlined, AccountTreeOutlined, AutoGraphOutlined, CloudOutlined, CorporateFareOutlined, CurrencyExchangeOutlined, DashboardOutlined, DataObjectOutlined, DeleteSweepOutlined, EventAvailableOutlined, FactCheckOutlined, HistoryOutlined, LocalOfferOutlined, ManageAccountsOutlined, PaidOutlined, QueryStatsOutlined, ReceiptLongOutlined, RuleOutlined, SavingsOutlined, ScheduleOutlined, ScienceOutlined, SettingsOutlined, StorageOutlined, SyncOutlined, TableChartOutlined, TrendingUpOutlined, VpnKeyOutlined, WarningAmberOutlined };

const menuPath = (target: string) => {
  const url = new URL(target, window.location.origin);
  return { pathname: url.pathname, search: url.search };
};
const matchesMenuPath = (target: string, pathname: string, search: string) => {
  return menuPath(target).pathname === pathname;
};

export default function Layout() {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));
  const { hasPerm, user, logout } = useAuth();
  const { unreadCount, openDialog: openNotifications } = useNotifications();
  const { items: catalogItems } = useNavigation();
  const visibleMenuGroups = useMemo(() => {
    const iconFor = (name: string) => {
      const Icon = MENU_ICONS[name as keyof typeof MENU_ICONS] || SettingsOutlined;
      return <Icon fontSize="small" />;
    };
    return catalogItems.filter(item => !item.parent_code && item.allowed && item.visible !== false).map(group => ({
      label: group.label,
      icon: iconFor(group.icon),
      items: [
        { path: group.path, label: "Visão geral", icon: iconFor(group.icon) },
        ...catalogItems.filter(item => item.parent_code === group.code && item.allowed && item.visible !== false).map(item => ({ path: item.path, label: item.label, icon: iconFor(item.icon) })),
      ],
    }));
  }, [catalogItems]);
  const navigate = useNavigate();
  const location = useLocation();
  const currentModule = catalogItems.find(item => item.path === location.pathname);
  const [menuSearch, setMenuSearch] = useState("");
  const [searchFocusToken, setSearchFocusToken] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openGroups, setOpenGroups] = useState<string[]>([]);
  const [layoutMode, setLayoutMode] = useState<"finbase" | "carbon">(() =>
    window.localStorage.getItem(LAYOUT_STORAGE_KEY) === "carbon" ? "carbon" : "finbase",
  );
  const drawerWidth = expanded ? DRAWER_WIDTH : COLLAPSED_WIDTH;
  const isCarbon = layoutMode === "carbon";
  const pageTheme = useMemo(() => createAppTheme(layoutMode), [layoutMode]);
  const searchMatches = useMemo(() => searchWorkspaceMenus(catalogItems, menuSearch), [catalogItems, menuSearch]);
  const matchedPaths = useMemo(() => new Set(searchMatches.map(item => item.path)), [searchMatches]);
  const displayedMenuGroups = useMemo(() => visibleMenuGroups.map(group => ({ ...group, items: group.items.filter(item => matchedPaths.has(item.path)) })).filter(group => group.items.length > 0), [visibleMenuGroups, matchedPaths]);
  const searching = Boolean(menuSearch.trim());
  const openMenuSearch = () => { setExpanded(true); if (isMobile) setMobileOpen(true); setSearchFocusToken(value => value + 1); };
  const openMenu = (path: string) => { navigate(path); setMenuSearch(""); setMobileOpen(false); };
  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setExpanded(true); if (isMobile) setMobileOpen(true); setSearchFocusToken(value => value + 1);
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [isMobile]);
  const workspaceSearch = <ThemeProvider theme={pageTheme}><WorkspaceSearch value={menuSearch} onChange={setMenuSearch} expanded={isMobile ? mobileOpen : expanded} focusToken={searchFocusToken} count={searchMatches.length} onOpen={openMenuSearch} onEnter={() => { const first = searchMatches.find(item => item.parent_code) || searchMatches[0]; if (first) openMenu(first.path); }} /></ThemeProvider>;
  const skipRouteCancelRef = useRef(true);

  useEffect(() => {
    if (skipRouteCancelRef.current) {
      skipRouteCancelRef.current = false;
      return;
    }
    cancelPendingAnalysisRequests();
  }, [location.pathname]);

  const currentLabel = currentModule?.label ?? visibleMenuGroups.flatMap(group => group.items).find(item => {
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
        {expanded && <Box component="img" src="/brand/finbase-logo.svg" alt="finbase.cloud" sx={{ display: "block", width: 194, height: "auto" }} />}
        {!expanded && <Tooltip title="Expandir navegação"><IconButton onClick={() => setExpanded(true)} aria-label="Expandir navegação"><Box component="img" src="/brand/finbase-symbol.svg" alt="finbase.cloud" sx={{ width: 32, height: 32, objectFit: "contain", flexShrink: 0 }} /></IconButton></Tooltip>}
        {!isMobile && expanded && <Tooltip title={expanded ? "Recolher navegação" : "Expandir navegação"}><IconButton size="small" onClick={() => setExpanded(value => !value)} aria-label={expanded ? "Recolher navegação" : "Expandir navegação"}>{expanded ? <ChevronLeftIcon /> : <MenuIcon />}</IconButton></Tooltip>}
      </Box>
      <Divider />
      <Typography variant="overline" sx={{ px: 2.5, pt: 2, pb: 0.75, color: "text.secondary", fontWeight: 700, letterSpacing: 1.1, display: expanded ? "block" : "none" }}>Workspace</Typography>
      {workspaceSearch}
      <Box sx={{ flex: 1, overflowY: "auto", px: 1, pb: 1 }}>
        {displayedMenuGroups.map(group => {
          const isOpen = searching || openGroups.includes(group.label);
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
                  return <ListItemButton key={item.path} selected={active} onClick={() => { openMenu(item.path); }}
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
        <li className="finbase-workspace-search">{workspaceSearch}</li>
        {displayedMenuGroups.map(group => {
          const activeGroup = group.items.some(item => menuPath(item.path).pathname === location.pathname);
          const groupIcon = group.label === "Panorama" ? CarbonDashboard
            : group.label === "Alertas" ? CarbonWarning
              : group.label === "Planejamento" ? CarbonFinance
                : group.label === "Otimização" ? CarbonChartLine
                  : group.label === "Dados e operação" ? CarbonDataBase
                    : group.label === "Governança" ? CarbonSettings
                      : group.label === "Administração" ? CarbonUserMultiple
                        : CarbonCloud;
          return group.items.length === 1 && !searching ? (
            <CarbonSideNavLink key={group.label} href={group.items[0].path} renderIcon={groupIcon}
              isActive={matchesMenuPath(group.items[0].path, location.pathname, location.search)}
              onClick={() => { openMenu(group.items[0].path); }}>
              {group.label}
            </CarbonSideNavLink>
          ) : (
            <CarbonSideNavMenu key={`${group.label}:${menuSearch}`} title={group.label} renderIcon={groupIcon} isActive={activeGroup}
              isSideNavExpanded={isMobile ? mobileOpen : expanded} defaultExpanded={searching || openGroups.includes(group.label)}>
              {group.items.map(item => (
                <CarbonSideNavMenuItem key={item.path} href={item.path} as="button"
                  isActive={matchesMenuPath(item.path, location.pathname, location.search)}
                  onClick={() => { openMenu(item.path); }}>
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
            <CarbonHeaderName href="/" prefix="" aria-label="finbase.cloud — início"><Box component="img" src="/brand/finbase-logo.svg" alt="finbase.cloud" sx={{ display: "block", width: { xs: 148, sm: 180 }, height: "auto" }} /></CarbonHeaderName>
            <CarbonHeaderGlobalBar>
              <CarbonHeaderGlobalAction aria-label="Buscar menus e submenus (Ctrl/Cmd + K)" onClick={openMenuSearch}><CarbonSearch size={20} /></CarbonHeaderGlobalAction>
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
            <Box className="finbase-carbon-page-title" sx={{ fontSize: "0.875rem", color: "#525252", mb: 2 }}>{currentLabel}</Box>
            {(() => {
              const targets = currentModule ? null : permissionTargetsForLocation(location.pathname, location.search);
              if (targets && !targets.some(target => hasPerm(target.module, "read", target.scope))) {
                const modules = targets.map(target => target.scope ? `${moduleDisplayName(target.module)}: ${dataDisplayText(target.scope)}` : moduleDisplayName(target.module)).join(" / ");
                return <Box sx={{ p: 6, textAlign: "center" }}><Typography variant="h6" sx={{ mb: 1 }}>Acesso negado</Typography><Typography color="text.secondary">Você não tem permissão de leitura no módulo <b>{modules}</b>. Solicite acesso ao administrador se precisar utilizá-lo.</Typography></Box>;
              }
              return <ThemeProvider theme={pageTheme}><Outlet /></ThemeProvider>;
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
            const targets = currentModule ? null : permissionTargetsForLocation(location.pathname, location.search);
            if (targets && !targets.some(target => hasPerm(target.module, "read", target.scope))) {
              const modules = targets.map(target => target.scope ? `${moduleDisplayName(target.module)}: ${dataDisplayText(target.scope)}` : moduleDisplayName(target.module)).join(" / ");
              return <Box sx={{ p: 6, textAlign: "center" }}><Typography variant="h6" sx={{ mb: 1 }}>Acesso negado</Typography><Typography color="text.secondary">Você não tem permissão de leitura no módulo <b>{modules}</b>. Solicite acesso ao administrador se precisar utilizá-lo.</Typography></Box>;
            }
            return <ThemeProvider theme={pageTheme}><Outlet /></ThemeProvider>;
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
