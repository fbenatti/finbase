import { useState, useEffect, useCallback } from "react";
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
  Collapse,
  Button,
  Chip,
  LinearProgress,
  Paper,
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
  Cancel,
  ExpandLess,
  ExpandMore,
  TableChart as TableChartIcon,
  Refresh as RefreshIcon,
} from "@mui/icons-material";
import {
  getRunningTasks, cancelEtl, getConnections,
  TaskStatus, SourceConnection, cancelReprocess,
} from "../services/api";

const DRAWER_WIDTH = 240;
const COLLAPSED_WIDTH = 56;

const menuItems = [
  { path: "/connections", label: "Fontes", icon: <CloudIcon /> },
  { path: "/extraction", label: "Extração", icon: <PlayArrowIcon /> },
  { path: "/destinations", label: "Tabelas Destino", icon: <TableChartIcon /> },
  { path: "/relationship", label: "Relacionamento", icon: <SwapHorizIcon /> },
  { path: "/reprocess", label: "Reprocessar", icon: <RefreshIcon /> },
  { path: "/departments", label: "Departamentos", icon: <BusinessIcon /> },
  { path: "/rules", label: "Regras", icon: <RuleIcon /> },
  { path: "/classifications", label: "Classificação", icon: <CategoryIcon /> },
  { path: "/pipeline", label: "Pipeline", icon: <AccountTreeIcon /> },
  { path: "/analysis", label: "Análise", icon: <BarChartIcon /> },
];

export default function Layout() {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [expanded, setExpanded] = useState(true);

  // Global running tasks
  const [runningTasks, setRunningTasks] = useState<TaskStatus[]>([]);
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [tasksExpanded, setTasksExpanded] = useState(true);

  useEffect(() => {
    getConnections().then(setConnections).catch(() => {});
  }, []);

  const pollTasks = useCallback(async () => {
    try {
      setRunningTasks(await getRunningTasks());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    pollTasks();
    const interval = setInterval(pollTasks, 3000);
    return () => clearInterval(interval);
  }, [pollTasks]);

  const doCancelTask = async (taskId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await cancelEtl(taskId);
      pollTasks();
    } catch { /* ignore */ }
  };

  const doCancelReprocess = async (taskId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await cancelReprocess(taskId);
      pollTasks();
    } catch { /* ignore */ }
  };

  const getConnName = (connectionId: number) => {
    const c = connections.find(c => c.id === connectionId);
    return c ? `${c.name} (${c.code})` : `Conexao #${connectionId}`;
  };

  const drawerWidth = expanded ? DRAWER_WIDTH : COLLAPSED_WIDTH;

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
        <Box sx={{ overflow: "hidden", flex: 1, py: 1 }}>
          <List>
            {menuItems.map((item) => {
              const active = location.pathname === item.path
                || (item.path === "/connections" && location.pathname.startsWith("/explorer"))
                || (item.path === "/pipeline" && location.pathname.startsWith("/pipeline"));
              return (
                <ListItemButton
                  key={item.path}
                  onClick={() => navigate(item.path)}
                  sx={{
                    minHeight: 48,
                    justifyContent: expanded ? "initial" : "center",
                    px: 2.5,
                    mx: expanded ? 1 : 0.5,
                    borderRadius: 2,
                    mb: 0.5,
                    bgcolor: active ? theme.palette.primary.main + "14" : "transparent",
                    color: active ? theme.palette.primary.main : "text.secondary",
                    "&:hover": {
                      bgcolor: active
                        ? theme.palette.primary.main + "22"
                        : "action.hover",
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
                  {expanded && <ListItemText primary={item.label} />}
                </ListItemButton>
              );
            })}
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
        {/* Global Running Tasks Banner — visible on all pages */}
        {runningTasks.length > 0 && (
          <Paper sx={{ p: 1.5, mb: 2, borderLeft: 4, borderColor: "info.main", bgcolor: "#f0f7ff" }}>
            <Box
              sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setTasksExpanded(!tasksExpanded)}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                {tasksExpanded ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
                <Typography variant="body2" fontWeight={600}>
                  Processos ativos ({runningTasks.length})
                </Typography>
                <Chip label={runningTasks.some(t => t.status === "RUNNING") ? "RUNNING" : "QUEUED"} size="small" color="info" />
              </Box>
            </Box>

            <Collapse in={tasksExpanded}>
              <Box sx={{ mt: 1, display: "flex", flexDirection: "column", gap: 1 }}>
                {runningTasks.map((t) => {
                  const isReprocess = t.type === "reprocess" || (!t.connection_id && t.destination_table_id);
                  const pct = t.total_blobs > 0 ? (t.processed_blobs / t.total_blobs) * 100 : 0;
                  const cancelFn = isReprocess ? doCancelReprocess : doCancelTask;
                  return (
                    <Box key={t.id} sx={{ p: 1, bgcolor: "#fff", borderRadius: 1, border: "1px solid #e0e0e0" }}>
                      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <Box>
                          <Typography variant="caption" fontWeight={600}>
                            {isReprocess ? `Reprocessar: ${t.table || "?"}` : getConnName(t.connection_id!)}
                          </Typography>
                          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                            {isReprocess
                              ? `${t.processed_blobs}/${t.total_blobs || "?"} lotes · ${(t.rows_destination || 0).toLocaleString()} linhas`
                              : `${t.processed_blobs}/${t.total_blobs || "?"} blobs · ${(t.rows_raw || 0).toLocaleString()} raw / ${(t.rows_normalized || 0).toLocaleString()} norm`
                            }
                          </Typography>
                          {t.phase && (
                            <Typography variant="caption" color="info.main" sx={{ display: "block" }}>
                              Fase: {t.phase}
                            </Typography>
                          )}
                        </Box>
                        <Button
                          size="small" variant="outlined" color="error"
                          startIcon={<Cancel />}
                          onClick={(e) => cancelFn(t.id, e)}
                          sx={{ fontSize: "0.7rem", py: 0.25, px: 1 }}
                        >
                          Cancelar
                        </Button>
                      </Box>
                      <LinearProgress
                        variant={t.total_blobs ? "determinate" : "indeterminate"}
                        value={pct}
                        sx={{ height: 4, borderRadius: 2, mt: 0.5 }}
                      />
                      {t.current_blob && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.25 }}>
                          {t.current_blob}
                        </Typography>
                      )}
                    </Box>
                  );
                })}
              </Box>
            </Collapse>
          </Paper>
        )}

        <Outlet />
      </Box>
    </Box>
  );
}
