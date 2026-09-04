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
  Dashboard as DashboardIcon,
  Timeline as TimelineIcon,
  AccountBalance as AccountBalanceIcon,
  Savings as SavingsIcon,
  CloudUpload as CloudUploadIcon,
  SmartToy as SmartToyIcon,
  CalendarMonth as CalendarMonthIcon,
} from "@mui/icons-material";
import { cancelPendingAnalysisRequests } from "../services/api";
import { useAuth } from "../auth/AuthContext";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
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
      { path: "/departments", label: "Departamentos", icon: <BusinessIcon /> },
      { path: "/budgets", label: "Orçamento", icon: <AccountBalanceIcon /> },
      { path: "/governance", label: "Governança", icon: <RuleIcon /> },
    ],
  },
  {
    label: "Visualizações",
    icon: <BarChartIcon fontSize="small" />,
    color: "#0f766e",
    background: "#f0fdfa",
    items: [
      { path: "/analysis", label: "Análise", icon: <BarChartIcon /> },
      { path: "/dimon-dashboard", label: "FinOps Intelligence", icon: <BarChartIcon /> },
      { path: "/visoes-consumo-ia", label: "Dados e IA", icon: <SmartToyIcon /> },
    ],
  },
  {
    label: "Administração",
    icon: <AdminPanelSettingsIcon fontSize="small" />,
    color: "#b45309",
    background: "#fffbeb",
    items: [{ path: "/admin", label: "Usuários, grupos e roles", icon: <AdminPanelSettingsIcon /> }],
  },
];

export default function Layout() {
  const theme = useTheme();
  const { hasPerm, user, logout } = useAuth();
  const canAdmin = hasPerm("users", "read") || hasPerm("groups", "read") || hasPerm("roles", "read");
  const visibleMenuGroups = menuGroups.filter((g) => g.label !== "Administração" || canAdmin);
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
            FinOps Platform
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
          minHeight: "100vh",
        }}
      >
        <Outlet />
      </Box>
    </Box>
  );
}
