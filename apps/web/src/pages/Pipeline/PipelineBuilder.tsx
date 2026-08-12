import { useCallback, useEffect, useState } from "react";
import {
  Box, Typography, Button, Paper, Select, MenuItem, FormControl,
  InputLabel, Chip, Alert, CircularProgress,
} from "@mui/material";
import { Refresh, AccountTree } from "@mui/icons-material";
import {
  ReactFlow, Controls, Background, MiniMap, useNodesState, useEdgesState,
  addEdge, Connection, Node, Edge, MarkerType, Position, Handle,
  NodeProps, BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  getConnections, discoverSchema,
  getDestinationTables, getDestinationSchema,
  getSourceDestMappings,
  getBusinessRules, getClassifications,
  SourceConnection, ColumnInfo, DestinationTable,
  BusinessRule, ClassificationRule, SourceDestinationMapping,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";

// ─── Custom Node Components ────────────────────────────────────────────

function SourceColNode({ data }: NodeProps) {
  const d = data as any;
  return (
    <Box sx={{ bgcolor: "#e3f2fd", border: "2px solid #1976d2", borderRadius: 2, p: 1, minWidth: 140 }}>
      <Handle type="source" position={Position.Right} />
      <Typography variant="caption" sx={{ color: "#888", display: "block", fontSize: "0.55rem" }}>{d.cloud}</Typography>
      <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.68rem", fontFamily: "monospace" }}>{d.label}</Typography>
    </Box>
  );
}

function FocusColNode({ data }: NodeProps) {
  const d = data as any;
  const isOutput = d.isOutput;
  const bg = isOutput ? "#1b5e20" : "#1a237e";
  const border = isOutput ? "#4caf50" : "#0d47a1";
  return (
    <Box sx={{ bgcolor: bg, border: `2px solid ${border}`, borderRadius: 2, p: 1, minWidth: 140 }}>
      <Handle type="target" position={Position.Left} />
      <Typography variant="body2" sx={{ color: "#fff", fontWeight: 600, fontSize: "0.65rem", fontFamily: "monospace" }}>{d.label}</Typography>
      {isOutput && <Typography variant="caption" sx={{ color: "#a5d6a7", fontSize: "0.5rem" }}>enriquecido</Typography>}
      <Handle type="source" position={Position.Right} />
    </Box>
  );
}

function ProcessNode({ data }: NodeProps) {
  const d = data as any;
  const isClassif = d.kind === "classification";
  const bg = isClassif ? "#e8f5e9" : "#fff3e0";
  const border = isClassif ? "#4caf50" : "#ff9800";
  const label = isClassif ? "CLASSIF" : "REGRA";
  const color = isClassif ? "#2e7d32" : "#e65100";
  return (
    <Box sx={{ bgcolor: bg, border: `2px solid ${border}`, borderRadius: 2, p: 1, minWidth: 180 }}>
      <Handle type="target" position={Position.Left} />
      <Typography variant="caption" sx={{ color, fontWeight: 600, fontSize: "0.55rem" }}>{label}</Typography>
      <Typography variant="body2" sx={{ fontSize: "0.65rem" }}>{d.label}</Typography>
      {d.detail && (
        <Typography variant="caption" sx={{ color: "#666", fontSize: "0.52rem", display: "block", mt: 0.3 }}>{d.detail}</Typography>
      )}
      <Handle type="source" position={Position.Right} />
    </Box>
  );
}

const nodeTypes = {
  sourceCol: SourceColNode,
  focusCol: FocusColNode,
  process: ProcessNode,
};

// ─── Layout constants ──────────────────────────────────────────────────
const X_SOURCE = 50;
const X_FOCUS = 380;
const X_PROCESS = 720;
const X_ENRICHED = 1060;
const ROW_H = 38;

// ─── Main Component ────────────────────────────────────────────────────
export default function PipelineBuilder() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [destId, setDestId] = useState<number | "">("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);

  useEffect(() => {
    getConnections().then(setConnections).catch(() => {});
    getDestinationTables(true).then(setDestTables).catch(() => {});
  }, []);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) =>
      addEdge({ ...params, markerEnd: { type: MarkerType.ArrowClosed }, style: { strokeWidth: 1.5 } }, eds),
    ),
    [setEdges],
  );

  const newEdge = (id: string, src: string, tgt: string, stroke = "#888", dash?: string) => ({
    id, source: src, target: tgt,
    markerEnd: { type: MarkerType.ArrowClosed as const },
    style: { strokeWidth: 1.2, stroke, ...(dash ? { strokeDasharray: dash } : {}) },
  });

  const loadPipeline = async () => {
    if (!destId) return;
    setLoading(true); setError("");
    try {
      const destIdNum = destId as number;
      const destSchema = await getDestinationSchema(destIdNum);
      const destCols: { name: string }[] = destSchema.columns;

      const [allMappings, allRules, allClassifs] = await Promise.all([
        getSourceDestMappings(undefined, destIdNum),
        getBusinessRules(undefined, destIdNum),
        getClassifications(undefined, undefined, destIdNum),
      ]);

      const newNodes: Node[] = [];
      const newEdges: Edge[] = [];

      // ──────────────────────────────────────────────────────────────────
      // Enriched output column set — these always live in zone 4 (rightmost)
      // ──────────────────────────────────────────────────────────────────
      const enrichedCols = new Set<string>([
        "projeto", "diretoria", "departamento", "centro_custo", "ambiente",
        "CostBRL", "Tax_Base", "Tax_Marketplace", "VL_CUSTO_FINAL_BRL",
        "Diretoria", "ModeloAI", "custom_fields",
      ]);

      // ──────────────────────────────────────────────────────────────────
      // Collect which FOCUS columns are actually used (mapped, classified, ruled)
      // ──────────────────────────────────────────────────────────────────
      const mappedDestCols = new Set<string>();
      for (const m of allMappings) mappedDestCols.add(m.destination_column);

      // Build reverse lookup: source column → destination column
      const srcToDest = new Map<string, string>();
      for (const m of allMappings) srcToDest.set(m.source_column, m.destination_column);

      const classifiedFields = new Set<string>();
      for (const c of allClassifs) {
        if (c.conditions) {
          for (const cond of c.conditions) classifiedFields.add((cond as any).field);
        }
        if (c.source_field) classifiedFields.add(c.source_field);
      }

      const ruledFields = new Set<string>();
      for (const r of allRules) {
        const cfgSrc = (r.config_json as any)?.source_column;
        if (cfgSrc) {
          ruledFields.add(srcToDest.get(cfgSrc) || cfgSrc);
        }
      }

      const usedFocusCols = new Set<string>([
        ...mappedDestCols, ...classifiedFields, ...ruledFields,
      ]);

      // projeto is always an enriched output, never a FOCUS input column.
      usedFocusCols.delete("projeto");

      // ──────────────────────────────────────────────────────────────────
      // ZONE 2: FOCUS — destination table columns that are referenced
      // ──────────────────────────────────────────────────────────────────
      const focusY = new Map<string, number>();
      let focusYIdx = 0;
      destCols
        .filter(c => usedFocusCols.has(c.name) && !enrichedCols.has(c.name))
        .forEach((c) => {
          const y = 50 + focusYIdx * ROW_H;
          focusY.set(c.name, y);
          newNodes.push({
            id: `foc-${c.name}`, type: "focusCol",
            position: { x: X_FOCUS, y },
            data: { label: c.name, isOutput: false },
          });
          focusYIdx += 1;
        });

      // ──────────────────────────────────────────────────────────────────
      // ZONE 4: FOCUS Enriquecida — final enriched output columns
      // ──────────────────────────────────────────────────────────────────
      const enrichedList = destCols.filter(c => enrichedCols.has(c.name));
      enrichedList.forEach((c, i) => {
        newNodes.push({
          id: `enr-${c.name}`, type: "focusCol",
          position: { x: X_ENRICHED, y: 50 + i * ROW_H },
          data: { label: c.name, isOutput: true },
        });
      });

      // ──────────────────────────────────────────────────────────────────
      // Build per-connection groups
      // ──────────────────────────────────────────────────────────────────
      const mappingsByConn = new Map<number, SourceDestinationMapping[]>();
      for (const m of allMappings) {
        const g = mappingsByConn.get(m.source_connection_id) || [];
        g.push(m); mappingsByConn.set(m.source_connection_id, g);
      }

      const rulesByConn = new Map<number, BusinessRule[]>();
      const globalRules: BusinessRule[] = [];
      for (const r of allRules) {
        if (r.connection_id) {
          const g = rulesByConn.get(r.connection_id) || [];
          g.push(r); rulesByConn.set(r.connection_id, g);
        } else { globalRules.push(r); }
      }

      const connIds = new Set<number>(mappingsByConn.keys());
      for (const k of rulesByConn.keys()) connIds.add(k);

      let srcY = 50;

      // ──────────────────────────────────────────────────────────────────
      // ZONE 1: Source columns — per connection, direct edges to FOCUS
      // ──────────────────────────────────────────────────────────────────
      for (const cid of connIds) {
        const conn = connections.find(c => c.id === cid);
        if (!conn) continue;

        // Load source schema
        let srcCols: string[] = [];
        try {
          const schema = await discoverSchema(cid);
          srcCols = schema.columns.filter((c: ColumnInfo) => c.name && !c.name.startsWith("_")).map(c => c.name);
        } catch { continue; }

        const connMappings = mappingsByConn.get(cid) || [];

        // Only show source cols that are actually mapped
        const mappedSet = new Set(connMappings.map(m => m.source_column));
        const shownSrc = srcCols.filter(c => mappedSet.has(c));
        if (shownSrc.length === 0) continue;

        // Source column nodes (Zone 1)
        const srcNodePos = new Map<string, number>();
        shownSrc.forEach((col, i) => {
          const y = srcY + i * ROW_H;
          srcNodePos.set(col, y);
          newNodes.push({
            id: `src-${cid}-${col}`, type: "sourceCol",
            position: { x: X_SOURCE, y },
            data: { label: col, cloud: conn.code },
          });
        });

        // Direct edges: source → FOCUS (the mapping IS the edge)
        connMappings.forEach((m) => {
          const srcId = `src-${cid}-${m.source_column}`;
          const focId = `foc-${m.destination_column}`;
          if (srcNodePos.has(m.source_column) && focusY.has(m.destination_column)) {
            newEdges.push(newEdge(`e-map-${m.id}`, srcId, focId, "#42a5f5"));
          }
        });

        srcY += shownSrc.length * ROW_H + 60;
      }

      // ──────────────────────────────────────────────────────────────────
      // ZONE 3: Business Rules + Classifications
      // ──────────────────────────────────────────────────────────────────
      let procY = 50;

      // ── Business rules (global) ──
      for (const r of globalRules) {
        const rId = `rule-${r.id}`;
        newNodes.push({
          id: rId, type: "process",
          position: { x: X_PROCESS, y: procY },
          data: { kind: "rule", label: r.name },
        });

        // Incoming: connect from enriched zone if destCol is enriched, otherwise from FOCUS
        const cfgSrcCol = (r.config_json as any)?.source_column;
        const destCol = cfgSrcCol ? srcToDest.get(cfgSrcCol) || cfgSrcCol : null;
        if (destCol) {
          if (enrichedCols.has(destCol))
            newEdges.push(newEdge(`e-${rId}-in`, `enr-${destCol}`, rId, "#ff9800"));
          else if (focusY.has(destCol))
            newEdges.push(newEdge(`e-${rId}-in`, `foc-${destCol}`, rId, "#ff9800"));
        }

        // Outgoing: only to enriched zone (rule output IS enriched data)
        if (r.target_column && enrichedCols.has(r.target_column))
          newEdges.push(newEdge(`e-${rId}-out`, rId, `enr-${r.target_column}`, "#ff9800"));

        procY += ROW_H + 8;
      }

      // ── Per-connection business rules ──
      for (const cid of connIds) {
        const list = rulesByConn.get(cid) || [];
        if (list.length === 0) continue;
        const connLabel = connections.find(c => c.id === cid)?.code || `conn${cid}`;

        const labelId = `rule-label-${cid}`;
        newNodes.push({
          id: labelId, type: "process",
          position: { x: X_PROCESS, y: procY },
          data: { kind: "rule", label: `Regras / ${connLabel}` },
        });
        procY += ROW_H + 4;

        for (const r of list) {
          const rId = `rule-${cid}-${r.id}`;
          newNodes.push({
            id: rId, type: "process",
            position: { x: X_PROCESS, y: procY },
            data: { kind: "rule", label: r.name },
          });

          const cfgSrcCol = (r.config_json as any)?.source_column;
          const destCol = cfgSrcCol ? srcToDest.get(cfgSrcCol) || cfgSrcCol : null;
          if (destCol) {
            if (enrichedCols.has(destCol))
              newEdges.push(newEdge(`e-${rId}-in`, `enr-${destCol}`, rId, "#ff9800"));
            else if (focusY.has(destCol))
              newEdges.push(newEdge(`e-${rId}-in`, `foc-${destCol}`, rId, "#ff9800"));
          }

          // Outgoing: only to enriched zone (rule output IS enriched data)
          if (r.target_column && enrichedCols.has(r.target_column))
            newEdges.push(newEdge(`e-${rId}-out`, rId, `enr-${r.target_column}`, "#ff9800"));

          procY += ROW_H + 4;
        }
        procY += 20;
      }

      // ── Classification rules — GROUPED by FOCUS column ──
      const classifGroups = new Map<string, { connIds: Set<number>; global: boolean; count: number; names: string[] }>();
      for (const c of allClassifs) {
        const fields = new Set<string>();
        if (c.conditions) {
          for (const cond of c.conditions) {
            if ((cond as any).field) fields.add((cond as any).field);
          }
        }
        if (c.source_field) fields.add(c.source_field);

        for (const f of fields) {
          if (!focusY.has(f)) continue;
          const grp = classifGroups.get(f) || { connIds: new Set<number>(), global: false, count: 0, names: [] };
          if (c.connection_id) grp.connIds.add(c.connection_id);
          else grp.global = true;
          grp.count += 1;
          grp.names.push(c.name);
          classifGroups.set(f, grp);
        }
      }

      // Render grouped classification summary nodes
      for (const [focusCol, grp] of classifGroups) {
        const gId = `cls-group-${focusCol}`;
        const scope = grp.global ? "Global" : [...grp.connIds].map(cid => connections.find(c => c.id === cid)?.code || `conn${cid}`).join(", ");

        newNodes.push({
          id: gId, type: "process",
          position: { x: X_PROCESS, y: procY },
          data: {
            kind: "classification",
            label: `${grp.count} classificações`,
            detail: `de ${scope} para ${focusCol}`,
          },
        });

        // Input edge: from FOCUS column
        newEdges.push(newEdge(`e-${gId}-in`, `foc-${focusCol}`, gId, "#4caf50"));

        // Output edges: to enriched columns
        for (const enrichedCol of ["projeto", "diretoria", "departamento", "centro_custo", "ambiente"]) {
          if (enrichedCols.has(enrichedCol)) {
            newEdges.push(newEdge(`e-${gId}-to-${enrichedCol}`, gId, `enr-${enrichedCol}`, "#4caf50", "4 2"));
          }
        }

        procY += ROW_H + 20;
      }

      setNodes(newNodes);
      setEdges(newEdges);

    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  };

  const destName = destId ? destTables.find(t => t.id === destId)?.name : null;

  return (
    <Box sx={{ height: "calc(100vh - 80px)", display: "flex", flexDirection: "column", mx: -3, mt: -3 }}>
      <Paper sx={{ p: 2, mb: 1, display: "flex", gap: 2, alignItems: "center", flexWrap: "wrap", mx: 3, mt: 3 }}>
        <Typography variant="h6" sx={{ mr: 2 }}>
          <AccountTree sx={{ verticalAlign: "middle", mr: 1 }} />Pipeline Builder
        </Typography>
        <FormControl sx={{ minWidth: 260 }} size="small">
          <InputLabel>Tabela Destino *</InputLabel>
          <Select value={destId} label="Tabela Destino *"
            onChange={(e) => { setDestId(e.target.value as number); setNodes([]); setEdges([]); }}>
            {destTables.map((t) => (<MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>))}
          </Select>
        </FormControl>
        <Button variant="outlined" startIcon={<Refresh />} onClick={loadPipeline} disabled={!destId}>
          Carregar Pipeline
        </Button>
        <Box sx={{ flexGrow: 1 }} />
        {destName && <Chip label={`Destino: ${destName}`} color="primary" />}
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 1, mx: 3 }} onClose={() => setError("")}>{error}</Alert>}

      {nodes.length > 0 && (
        <Box sx={{ display: "flex", gap: 2, mb: 1, px: 2, mx: 3, flexWrap: "wrap" }}>
          <Chip label="Origens" size="small" sx={{ bgcolor: "#e3f2fd", border: "1px solid #1976d2" }} />
          <Chip label="FOCUS" size="small" sx={{ bgcolor: "#1a237e", color: "#fff" }} />
          <Chip label="Regras de Negócio" size="small" sx={{ bgcolor: "#fff3e0", border: "1px solid #ff9800" }} />
          <Chip label="Classificações" size="small" sx={{ bgcolor: "#e8f5e9", border: "1px solid #4caf50" }} />
          <Chip label="FOCUS Enriquecida" size="small" sx={{ bgcolor: "#1b5e20", color: "#a5d6a7", border: "1px solid #4caf50" }} />
        </Box>
      )}

      <Box sx={{ flexGrow: 1, border: "1px solid #ddd", borderRadius: 1, overflow: "hidden" }}>
        {loading ? (
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}><CircularProgress /></Box>
        ) : !destId ? (
          <EmptyState message="Selecione uma Tabela Destino e clique em 'Carregar Pipeline'." />
        ) : nodes.length === 0 ? (
          <EmptyState message="Nenhuma relação encontrada." />
        ) : (
          <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect} nodeTypes={nodeTypes} fitView minZoom={0.1} maxZoom={2}>
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
            <Controls />
            <MiniMap nodeColor={(n) => {
              switch (n.type) {
                case "sourceCol": return "#1976d2";
                case "focusCol": return (n.data as any)?.isOutput ? "#4caf50" : "#1a237e";
                case "process": return (n.data as any)?.kind === "classification" ? "#4caf50" : "#ff9800";
                default: return "#888";
              }
            }} />
          </ReactFlow>
        )}
      </Box>
    </Box>
  );
}
