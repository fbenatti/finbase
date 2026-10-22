import WorkspaceSearch from "../navigation/WorkspaceSearch";
import { searchWorkspaceMenus } from "../navigation/menuSearchIndex";
import { useNavigation } from "../navigation/NavigationContext";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { Box, ButtonBase, Drawer, IconButton, Typography, List, ListItemButton, ListItemText, Tooltip, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { MenuOutlined as MenuIcon, DashboardOutlined, QueryStatsOutlined, SavingsOutlined, WarningAmberOutlined, AccountBalanceWalletOutlined, TrendingUpOutlined, PaidOutlined, ReceiptLongOutlined, CloudOutlined, TableChartOutlined, AccountTreeOutlined, SyncOutlined, RuleOutlined, CorporateFareOutlined, LocalOfferOutlined, EventAvailableOutlined, ManageAccountsOutlined, FactCheckOutlined, VpnKeyOutlined, ScienceOutlined, StorageOutlined, ShowChartOutlined, ScheduleOutlined, HistoryOutlined, AutoGraphOutlined, DataObjectOutlined, DeleteSweepOutlined, CurrencyExchangeOutlined, SettingsOutlined, LogoutOutlined } from "@mui/icons-material";
import { cancelPendingAnalysisRequests } from "../services/api";
import { useAuth } from "../auth/AuthContext";
import { permissionTargetsForLocation } from "../auth/moduleMap";
import { NotificationBell } from "../context/NotificationContext";
import { dataDisplayText, moduleDisplayName } from "../utils/dataPresentation";
import { colors, fontFamily, radius } from "../theme/tokens";

const RAIL_WIDTH = 88;
const PANEL_WIDTH = 264;

type Entry = { path: string; label: string; code: string; parent_code: string | null };
type Domain = { code: string; label: string; path: string; icon: ReactNode; menus: Entry[] };

const MENU_ICONS = { AccountBalanceWalletOutlined, AccountTreeOutlined, AutoGraphOutlined, CloudOutlined, CorporateFareOutlined, CurrencyExchangeOutlined, DashboardOutlined, DataObjectOutlined, DeleteSweepOutlined, EventAvailableOutlined, FactCheckOutlined, HistoryOutlined, LocalOfferOutlined, ManageAccountsOutlined, PaidOutlined, QueryStatsOutlined, ReceiptLongOutlined, RuleOutlined, SavingsOutlined, ScheduleOutlined, ScienceOutlined, SettingsOutlined, StorageOutlined, SyncOutlined, TableChartOutlined, TrendingUpOutlined, VpnKeyOutlined, WarningAmberOutlined };

const menuPath = (target: string) => new URL(target, window.location.origin).pathname;

export default function Layout() {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));
  const { hasPerm, user, logout } = useAuth();
  const { items: catalogItems } = useNavigation();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuSearch, setMenuSearch] = useState("");
  const [searchFocusToken, setSearchFocusToken] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const skipRouteCancelRef = useRef(true);

  const visible = useMemo(() => catalogItems.filter(item => item.allowed && item.visible !== false), [catalogItems]);
  const domains = useMemo<Domain[]>(() => {
    const iconFor = (name: string) => {
      const Icon = MENU_ICONS[name as keyof typeof MENU_ICONS] || SettingsOutlined;
      return <Icon fontSize="small" />;
    };
    return visible.filter(item => !item.parent_code).flatMap(group => {
      // Abas e submenus viram itens do menu. O próprio domínio só vira item quando não tem filhos.
      const children = visible.filter(item => item.parent_code === group.code);
      if (!children.length && group.renderer === "ModuleIndex") return [];
      const menus = children.length ? children : [group];
      return [{ code: group.code, label: group.label, path: group.path, icon: iconFor(group.icon), menus: menus.map(item => ({ path: item.path, label: item.label, code: item.code, parent_code: item.parent_code })) }];
    });
  }, [visible]);

  const currentEntry = visible.find(item => menuPath(item.path) === location.pathname);
  const activeDomain = domains.find(domain => domain.menus.some(menu => menuPath(menu.path) === location.pathname))
    ?? domains.find(domain => domain.code === selectedDomain)
    ?? domains[0];

  const searchMatches = useMemo(() => searchWorkspaceMenus(catalogItems, menuSearch), [catalogItems, menuSearch]);
  const searching = Boolean(menuSearch.trim());
  // Na busca, só entram menus com rota (folhas), com o nome do domínio como legenda.
  const searchResults = useMemo(() => searchMatches
    .filter(item => item.parent_code || !visible.some(child => child.parent_code === item.code))
    .map(item => ({ path: item.path, label: item.label, code: item.code, domain: domains.find(domain => domain.code === (item.parent_code || item.code))?.label ?? "" })),
  [searchMatches, visible, domains]);

  const openMenu = (path: string) => {
    navigate(path);
    setMenuSearch("");
    setMobileOpen(false);
  };
  const openDomain = (domain: Domain) => {
    setSelectedDomain(domain.code);
    if (domain.menus[0]) openMenu(domain.menus[0].path);
  };
  const openMenuSearch = () => { if (isMobile) setMobileOpen(true); setSearchFocusToken(value => value + 1); };

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (isMobile) setMobileOpen(true);
        setSearchFocusToken(value => value + 1);
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [isMobile]);

  useEffect(() => {
    if (skipRouteCancelRef.current) {
      skipRouteCancelRef.current = false;
      return;
    }
    cancelPendingAnalysisRequests();
  }, [location.pathname]);

  const currentLabel = currentEntry?.label ?? activeDomain?.label ?? "finbase.cloud";

  const workspaceSearch = (
    <WorkspaceSearch value={menuSearch} onChange={setMenuSearch} expanded focusToken={searchFocusToken} count={searchResults.length}
      onOpen={openMenuSearch} onEnter={() => { const first = searchResults[0]; if (first) openMenu(first.path); }} />
  );

  const rail = (
    <Box component="nav" aria-label="Domínios" sx={{ width: RAIL_WIDTH, flexShrink: 0, bgcolor: colors.rail, color: colors.railText, borderRight: `1px solid ${colors.border}`, display: "flex", flexDirection: "column", alignItems: "center", py: 2, gap: 0.75, overflowY: "auto" }}>
      <Box component="img" src="/brand/finbase-symbol.svg" alt="finbase.cloud" sx={{ width: 36, height: 36, mb: 1.5, objectFit: "contain" }} />
      {domains.map(domain => {
        const active = domain.code === activeDomain?.code;
        return (
          <ButtonBase key={domain.code} onClick={() => openDomain(domain)} aria-current={active ? "page" : undefined} aria-label={domain.label}
            sx={{ position: "relative", width: 72, py: 1, px: 0.5, borderRadius: radius.surface, display: "flex", flexDirection: "column", gap: 0.5, color: active ? colors.brandDark : "inherit", bgcolor: active ? colors.railActive : "transparent", "&::before": { content: '""', position: "absolute", left: -8, top: 14, bottom: 14, width: 3, borderRadius: 2, bgcolor: active ? colors.brand : "transparent" }, "&:hover": { bgcolor: active ? colors.railActive : colors.rowHover, color: colors.text }, "&:focus-visible": { outline: `2px solid ${colors.brandLight}` } }}>
            {domain.icon}
            <Typography component="span" sx={{ fontSize: 11, lineHeight: 1.2, fontWeight: active ? 700 : 500, textAlign: "center", color: "inherit" }}>{domain.label}</Typography>
          </ButtonBase>
        );
      })}
    </Box>
  );

  const panel = (
    <Box sx={{ width: PANEL_WIDTH, maxWidth: "100%", height: "100%", display: "flex", flexDirection: "column", bgcolor: colors.raised, borderRight: `1px solid ${colors.border}` }}>
      <Box sx={{ px: 2.5, pt: 2.5, pb: 1.5 }}>
        <Typography variant="overline" sx={{ color: colors.textMuted, fontWeight: 700, letterSpacing: 1, lineHeight: 1.6 }}>
          {searching ? "Resultados da busca" : "Domínio"}
        </Typography>
        <Typography sx={{ fontFamily: fontFamily.heading, fontSize: 18, fontWeight: 700, color: colors.text, lineHeight: 1.3 }}>
          {searching ? `${searchResults.length} ${searchResults.length === 1 ? "tela" : "telas"}` : activeDomain?.label}
        </Typography>
      </Box>
      {workspaceSearch}
      <List component="nav" aria-label={searching ? "Resultados da busca" : `Telas do domínio ${activeDomain?.label ?? ""}`} disablePadding sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 1.25, pb: 2 }}>
        {searching
          ? searchResults.map(item => (
            <ListItemButton key={item.path} selected={menuPath(item.path) === location.pathname} onClick={event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); openMenu(item.path); }} sx={{ borderRadius: radius.control, minHeight: 44, mb: 0.25 }}>
              <ListItemText primary={item.label} secondary={item.domain} primaryTypographyProps={{ fontSize: 13.5, fontWeight: 600, noWrap: true }} secondaryTypographyProps={{ fontSize: 11.5, noWrap: true }} />
            </ListItemButton>
          ))
          : activeDomain?.menus.map(menu => {
            const active = menuPath(menu.path) === location.pathname;
            return (
              <ListItemButton key={menu.path} selected={active} onClick={event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); openMenu(menu.path); }}
                sx={{ borderRadius: radius.control, minHeight: 40, mb: 0.25, color: active ? colors.brandDark : colors.textSecondary, "&.Mui-selected": { bgcolor: colors.brandSelected, color: colors.brandDark, "&:hover": { bgcolor: colors.brandHover } }, "&:hover": { bgcolor: colors.rowHover } }}>
                <ListItemText primary={menu.label} primaryTypographyProps={{ fontSize: 13.5, fontWeight: active ? 700 : 500, noWrap: true }} />
              </ListItemButton>
            );
          })}
        {searching && !searchResults.length && <Typography variant="body2" color="text.secondary" sx={{ px: 1.5, py: 1 }}>Nenhuma tela corresponde à busca.</Typography>}
      </List>
    </Box>
  );

  const shellNavigation = (
    <Box sx={{ display: "flex", height: "100%" }}>
      {rail}
      {panel}
    </Box>
  );

  const pageContent = (() => {
    const targets = currentEntry ? null : permissionTargetsForLocation(location.pathname, location.search);
    if (targets && !targets.some(target => hasPerm(target.module, "read", target.scope))) {
      const modules = targets.map(target => target.scope ? `${moduleDisplayName(target.module)}: ${dataDisplayText(target.scope)}` : moduleDisplayName(target.module)).join(" / ");
      return <Box sx={{ p: 6, textAlign: "center" }}><Typography variant="h6" sx={{ mb: 1 }}>Acesso restrito</Typography><Typography color="text.secondary">Seu perfil não pode ler <b>{modules}</b>. Peça acesso ao administrador se precisar desta tela.</Typography></Box>;
    }
    return <Outlet />;
  })();

  return (
    <Box sx={{ display: "flex", height: "100%", bgcolor: "background.default" }}>
      {isMobile
        ? <Drawer variant="temporary" open={mobileOpen} onClose={() => setMobileOpen(false)} ModalProps={{ keepMounted: true }} PaperProps={{ sx: { width: RAIL_WIDTH + PANEL_WIDTH, maxWidth: "100%", border: 0, flexDirection: "row" } }}>
          {shellNavigation}
        </Drawer>
        : <Box sx={{ width: RAIL_WIDTH + PANEL_WIDTH, flexShrink: 0, height: "100%", display: "flex" }}>{shellNavigation}</Box>}
      <Box sx={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1, height: "100%" }}>
        <Box component="header" sx={{ position: "relative", minHeight: 64, px: { xs: 1.5, md: 3 }, display: "flex", alignItems: "center", gap: 1, bgcolor: colors.paper, borderBottom: `1px solid ${colors.border}`, "&::after": { content: '""', position: "absolute", left: 0, right: 0, bottom: -1, height: 2, background: `linear-gradient(90deg, ${colors.brand} 0%, ${colors.brandLight} 55%, ${colors.accent} 100%)` } }}>
          {isMobile && <IconButton edge="start" onClick={() => setMobileOpen(true)} aria-label="Abrir menu de navegação" sx={{ mr: 0.5 }}><MenuIcon /></IconButton>}
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="caption" noWrap sx={{ display: "block", color: colors.textMuted, fontWeight: 600, lineHeight: 1.3 }}>{activeDomain?.label}</Typography>
            <Typography noWrap sx={{ fontFamily: fontFamily.heading, fontSize: 17, fontWeight: 700, color: colors.text, lineHeight: 1.3 }}>{currentLabel}</Typography>
          </Box>
          <Box sx={{ flexGrow: 1 }} />
          <NotificationBell />
          <Typography variant="body2" noWrap sx={{ mx: 1, maxWidth: 180, color: colors.textSecondary, display: { xs: "none", sm: "block" } }}>{user?.username}</Typography>
          <Tooltip title="Encerrar sessão"><IconButton onClick={() => logout()} size="small" aria-label="Encerrar sessão"><LogoutOutlined fontSize="small" /></IconButton></Tooltip>
        </Box>
        <Box component="main" id="main-content" sx={{ flex: 1, minHeight: 0, overflow: "auto", p: { xs: 1.5, sm: 2.5, lg: 3 } }}>
          {pageContent}
        </Box>
        <Box component="footer" sx={{ minHeight: 40, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1, px: { xs: 2, md: 3 }, borderTop: `1px solid ${colors.border}`, bgcolor: colors.paper }}>
          <Typography variant="caption" color="text.secondary">© {new Date().getFullYear()} finbase.cloud</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: { xs: "none", sm: "block" } }}>FinBase · Plataforma de FinOps para custos de nuvem</Typography>
        </Box>
      </Box>
    </Box>
  );
}
