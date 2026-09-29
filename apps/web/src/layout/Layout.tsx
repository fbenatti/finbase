import { useState, useEffect, useRef } from "react";
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
  useTheme,
} from "@mui/material";
import {
  Menu as MenuIcon,
  ChevronLeft as ChevronLeftIcon,
  Cloud as CloudIcon,
  FolderOpen as FolderOpenIcon,
  SwapHoriz as SwapHorizIcon,
  Rule as RuleIcon,
  Category as CategoryIcon,
  PlayArrow as PlayArrowIcon,
  BarChart as BarChartIcon,
  AccountTree as AccountTreeIcon,
  Business as BusinessIcon,
  TableChart as TableChartIcon,
  Refresh as RefreshIcon,
  ShowChart as ForecastIcon,
  Timeline as TimelineIcon,
  AccountBalance as AccountBalanceIcon,
  Savings as SavingsIcon,
  CloudUpload as CloudUploadIcon,
  SmartToy as SmartToyIcon,
  CalendarMonth as CalendarMonthIcon,
} from "@mui/icons-material";
import { cancelPendingAnalysisRequests } from "../services/api";
import { useAuth } from "../auth/AuthContext";
import { permissionTargetsForPath } from "../auth/moduleMap";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import LightbulbIcon from "@mui/icons-material/Lightbulb";
import LogoutIcon from "@mui/icons-material/Logout";
import { NotificationBell } from "../context/NotificationContext";

const DRAWER_WIDTH = 280;
const COLLAPSED_WIDTH = 56;

const menuGroups = [
  {
    label: "Importação de dados",
    icon: <FolderOpenIcon fontSize="small" />,
    color: "#2563eb",
    background: "#eff6ff",
    items: [
      { path: "/connections", label: "Fontes de dados", icon: <CloudIcon /> },
      { path: "/tables", label: "Tabelas", icon: <TableChartIcon /> },
      { path: "/reprocess", label: "Reprocessar", icon: <RefreshIcon /> },
      { path: "/pipeline", label: "Pipeline do dado", icon: <AccountTreeIcon /> },
    ],
  },
  {
    label: "Regras de negócios",
    icon: <RuleIcon fontSize="small" />,
    color: "#7c3aed",
    background: "#f5f3ff",
    items: [
      { path: "/forecast", label: "Forecast", icon: <ForecastIcon /> },
      { path: "/savings", label: "Savings", icon: <SavingsIcon /> },
      { path: "/departments", label: "Modelo Organizacional", icon: <BusinessIcon /> },
      { path: "/financial-planning", label: "Planejamento Financeiro", icon: <AccountBalanceIcon /> },
      { path: "/governance", label: "Governança", icon: <RuleIcon /> },
      { path: "/financial-contracts", label: "Dashboard contratual", icon: <AccountBalanceIcon /> },
      { path: "/governance?tab=financial-contracts", label: "Configurar contratos", icon: <RuleIcon /> },
      { path: "/financial-contracts-dashboard", label: "Dashboard contratual", icon: <BarChartIcon /> },
    ],
  },
  {
    label: "Visualizações",
    icon: <BarChartIcon fontSize="small" />,
    color: "#0f766e",
    background: "#f0fdfa",
    items: [
      { path: "/analysis", label: "Análise", icon: <BarChartIcon /> },
      { path: "/gold-dashboard", label: "FinOps Intelligence", icon: <BarChartIcon /> },
      { path: "/gold-dashboard-v2", label: "FinOps Intelligence v2 (BETA)", icon: <BarChartIcon /> },
      { path: "/visoes-consumo-ia", label: "Unit Economics", icon: <SmartToyIcon /> },
    ],
  },
  {
    label: "Administração",
    icon: <AdminPanelSettingsIcon fontSize="small" />,
    color: "#b45309",
    background: "#fffbeb",
    items: [
      { path: "/ideas", label: "Ideias", icon: <LightbulbIcon /> },
      { path: "/admin", label: "Usuários, grupos e roles", icon: <AdminPanelSettingsIcon /> },
      { path: "/admin/audit", label: "Auditoria", icon: <AdminPanelSettingsIcon /> },
      { path: "/admin/sso", label: "Provedores SSO", icon: <AdminPanelSettingsIcon /> },
    ],
  },
];

const AUDIT_MODULES = ["audit"];

export default function Layout() {
  const theme = useTheme();
  const { hasPerm, user, logout } = useAuth();
  const canAdmin = hasPerm("users", "read") || hasPerm("groups", "read") || hasPerm("roles", "read");
  const canIdeas = hasPerm("ideas", "read");
  const visibleMenuGroups = menuGroups
    .filter((g) => g.label !== "Administração" || canAdmin || canIdeas)
    .map((g) => {
      if (g.label !== "Administração") {
        return { ...g, items: g.items.filter((item) => {
          const targets = permissionTargetsForPath(item.path);
          return !targets || targets.some((t) => hasPerm(t.module, "read", t.scope));
        }) };
      }
      return {
        ...g,
        items: g.items.filter((item) => {
          if (item.path === "/ideas") return canIdeas;
          if (item.path === "/admin/audit") return hasPerm("audit", "read");
          if (item.path === "/admin/sso") return hasPerm("auth", "read");
          if (item.path === "/admin") return canAdmin;
          return true;
        }),
      };
    })
    .filter((g) => g.items.length > 0);
  const navigate = useNavigate();
  const location = useLocation();
  const [expanded, setExpanded] = useState(true);

  const drawerWidth = expanded ? DRAWER_WIDTH : COLLAPSED_WIDTH;

  const skipRouteCancelRef = useRef(true);

  useEffect(() => {
    if (skipRouteCancelRef.current) {
      skipRouteCancelRef.current = false;
      return;
    }
    cancelPendingAnalysisRequests();
  }, [location.pathname]);

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <AppBar
        position="fixed"
        sx={{
          zIndex: theme.zIndex.drawer + 1,
          background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${theme.palette.primary.dark})`,
        }}
      >
        <Toolbar>
          <IconButton
            color="inherit"
            edge="start"
            onClick={() => setExpanded(!expanded)}
            sx={{ mr: 1 }}
          >
            {expanded ? <ChevronLeftIcon /> : <MenuIcon />}
          </IconButton>
          <Typography variant="h6" noWrap sx={{ fontWeight: 600 }}>
            FinOps Intelligence Platform
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Typography variant="body2" sx={{ mr: 1, opacity: 0.9 }}>{user?.username}</Typography>
          <Tooltip title="Sair">
            <IconButton color="inherit" onClick={() => logout()} size="small" sx={{ mr: 1 }}>
              <LogoutIcon />
            </IconButton>
          </Tooltip>
          <NotificationBell />
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: drawerWidth,
          flexShrink: 0,
          transition: "width 0.2s",
          "& .MuiDrawer-paper": {
            width: drawerWidth,
            transition: "width 0.2s",
            overflowX: "hidden",
            boxSizing: "border-box",
            bgcolor: "#FFFFFF",
          },
        }}
      >
        <Toolbar />
        <Box sx={{ overflowY: "auto", overflowX: "hidden", flex: 1, py: 1 }}>
          <List sx={{ pb: 2 }}>
            {visibleMenuGroups.map((group, groupIndex) => (
              <Box key={group.label}>
                {groupIndex > 0 && <Divider sx={{ my: 1.25, mx: 1.5, borderColor: "#e5e7eb" }} />}
                {expanded ? (
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 1,
                      mx: 1.5,
                      mb: 0.75,
                      px: 1.25,
                      py: 0.75,
                      borderRadius: 1.5,
                      color: group.color,
                      bgcolor: group.background,
                    }}
                  >
                    {group.icon}
                    <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: 0.35 }}>
                      {group.label}
                    </Typography>
                  </Box>
                ) : (
                  <Tooltip title={group.label} placement="right">
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "center",
                        py: 0.75,
                        mx: 0.5,
                        borderRadius: 1.5,
                        color: group.color,
                        bgcolor: group.background,
                      }}
                    >
                      {group.icon}
                    </Box>
                  </Tooltip>
                )}
                {group.items.map((item) => {
                  const active = location.pathname === item.path
                    || (item.path === "/connections" && location.pathname.startsWith("/explorer"))
                    || (item.path === "/pipeline" && location.pathname.startsWith("/pipeline"));
                  return (
                    <Tooltip key={item.path} title={item.label} placement="right" disableHoverListener={expanded}>
                      <ListItemButton
                        onClick={() => navigate(item.path)}
                        sx={{
                          minHeight: 48,
                          justifyContent: expanded ? "initial" : "center",
                          px: 2.5,
                          mx: expanded ? 1 : 0.5,
                          borderRadius: 2,
                          mb: 0.5,
                          borderLeft: expanded ? "3px solid" : "none",
                          borderColor: active ? group.color : "transparent",
                          bgcolor: active ? group.background : "transparent",
                          color: active ? group.color : "text.secondary",
                          fontWeight: active ? 700 : 400,
                          "&:hover": {
                            bgcolor: active ? group.background : group.background,
                            color: group.color,
                          },
                        }}
                      >
                        <ListItemIcon
                          sx={{
                            minWidth: 0,
                            mr: expanded ? 2 : "auto",
                            justifyContent: "center",
                            color: "inherit",
                          }}
                        >
                          {item.icon}
                        </ListItemIcon>
                        {expanded && <ListItemText primary={item.label} slotProps={{ primary: { fontWeight: active ? 700 : 500 } }} />}
                      </ListItemButton>
                    </Tooltip>
                  );
                })}
              </Box>
            ))}
          </List>
        </Box>
      </Drawer>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          overflow: "auto",
          bgcolor: "background.default",
          p: 3,
          mt: 8,
          // A altura desconta o deslocamento do AppBar (mt: 8). Antes, com
          // minHeight: 100vh, o main ultrapassava o container e criava uma
          // segunda barra de rolagem na janela além da interna.
          height: "calc(100% - 64px)",
        }}
      >
        {(() => {
          const targets = permissionTargetsForPath(location.pathname);
          if (targets && !targets.some((t) => hasPerm(t.module, "read", t.scope))) {
            const mod = targets.map((t) => t.scope ? `${t.module}:${t.scope}` : t.module).join(" / ");
            return (
              <Box sx={{ p: 6, textAlign: "center" }}>
                <Typography variant="h6" sx={{ mb: 1 }}>Acesso negado</Typography>
                <Typography color="text.secondary">
                  Você não tem permissão de leitura no módulo <b>{mod}</b>.{" "}
                  Solicite acesso ao administrador se precisar utilizá-lo.
                </Typography>
              </Box>
            );
          }
          return <Outlet />;
        })()}
      </Box>
    </Box>
  );
}
