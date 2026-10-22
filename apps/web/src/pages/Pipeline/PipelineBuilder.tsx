import { dataDisplayText } from "../../utils/dataPresentation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box, Typography, Button, Paper, Select, MenuItem, FormControl,
  InputLabel, Chip, Alert, CircularProgress, Stack,
} from "@mui/material";
import { RefreshOutlined, AccountTreeOutlined } from "@mui/icons-material";
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
    <Box sx={{ bgcolor: "#EEF4FF", border: "2px solid #0EA5C6", borderRadius: 2, p: 1, minWidth: 140 }}>
      <Handle type="source" position={Position.Right} />
      <Typography variant="caption" sx={{ color: "#888", display: "block", fontSize: "0.55rem" }}>{d.cloud}</Typography>
      <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.68rem", fontFamily: "monospace" }}>{d.label}</Typography>
    </Box>
  );
}

function FocusColNode({ data }: NodeProps) {
  const d = data as any;
  return (
    <Box sx={{ bgcolor: "#0B3FA8", border: "2px solid #0B3FA8", borderRadius: 2, p: 1, minWidth: 140 }}>
      <Handle type="target" position={Position.Left} />
      <Typography variant="body2" sx={{ color: "#fff", fontWeight: 600, fontSize: "0.65rem", fontFamily: "monospace" }}>{d.label}</Typography>
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
    <Box sx={{ bgcolor: bg, border: "2px solid " + border, borderRadius: 2, p: 1, minWidth: 180 }}>
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
const X_DEST = 380;
const X_PROCESS = 720;
const ROW_H = 38;

// ─── Main Component ────────────────────────────────────────────────────
export default function PipelineBuilder() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [destId, setDestId] = useState<number | "">("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

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
    setSelectedId(null);
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

      // Collect which destination columns are actually used (mapped, classified, ruled)
      const mappedDestCols = new Set<string>();
      for (const m of allMappings) mappedDestCols.add(m.destination_column);

      // Build reverse lookup: source column → destination column
      const srcToDest = new Map<string, string>();
      for (const m of allMappings) srcToDest.set(m.source_column, m.destination_column);

      const referencedCols = new Set<string>();
      for (const c of allClassifs) {
        if (c.target_field) referencedCols.add(c.target_field);
        if (c.conditions) {
          for (const cond of c.conditions) {
            if ((cond as any).field) referencedCols.add((cond as any).field);
          }
        }
        if (c.source_field) referencedCols.add(c.source_field);
      }
      for (const r of allRules) {
        const cfgSrc = (r.config_json as any)?.source_column;
        if (cfgSrc) referencedCols.add(srcToDest.get(cfgSrc) || cfgSrc);
        if (r.target_column) referencedCols.add(r.target_column);
      }

      const usedFocusCols = new Set<string>([
        ...mappedDestCols, ...referencedCols,
      ]);

      // ── ZONE: Destination — destination table columns that are referenced ──
      const focusY = new Map<string, number>();
      let focusYIdx = 0;
      destCols
        .filter(c => usedFocusCols.has(c.name))
        .forEach((c) => {
          const y = 50 + focusYIdx * ROW_H;
          focusY.set(c.name, y);
          newNodes.push({
            id: "foc-" + c.name, type: "focusCol",
            position: { x: X_DEST, y },
            data: { label: c.name },
          });
          focusYIdx += 1;
        });

      // ── Build per-connection groups ──
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

      // ── ZONE: Source columns — per connection, direct edges to destination ──
      for (const cid of connIds) {
        const conn = connections.find(c => c.id === cid);
        if (!conn) continue;

        let srcCols: string[] = [];
        try {
          const schema = await discoverSchema(cid);
          srcCols = schema.columns.filter((c: ColumnInfo) => c.name && !c.name.startsWith("_")).map(c => c.name);
        } catch { continue; }

        const connMappings = mappingsByConn.get(cid) || [];
        const mappedSet = new Set(connMappings.map(m => m.source_column));
        const shownSrc = srcCols.filter(c => mappedSet.has(c));
        if (shownSrc.length === 0) continue;

        const srcNodePos = new Map<string, number>();
        shownSrc.forEach((col, i) => {
          const y = srcY + i * ROW_H;
          srcNodePos.set(col, y);
          newNodes.push({
            id: "src-" + cid + "-" + col, type: "sourceCol",
            position: { x: X_SOURCE, y },
            data: { label: col, cloud: conn.code },
          });
        });

        connMappings.forEach((m) => {
          const srcId = "src-" + cid + "-" + m.source_column;
          const focId = "foc-" + m.destination_column;
          if (srcNodePos.has(m.source_column) && focusY.has(m.destination_column)) {
            newEdges.push(newEdge("e-map-" + m.id, srcId, focId, "#6EA8FE"));
          }
        });

        srcY += shownSrc.length * ROW_H + 60;
      }

      // ── ZONE: Business Rules + Classifications ──
      let procY = 50;

      // ── Business rules (global) ──
      for (const r of globalRules) {
        const rId = "rule-" + r.id;
        newNodes.push({
          id: rId, type: "process",
          position: { x: X_PROCESS, y: procY },
          data: { kind: "rule", label: r.name },
        });

        const cfgSrcCol = (r.config_json as any)?.source_column;
        const destCol = cfgSrcCol ? srcToDest.get(cfgSrcCol) || cfgSrcCol : null;
        if (destCol && focusY.has(destCol))
          newEdges.push(newEdge("e-" + rId + "-in", "foc-" + destCol, rId, "#ff9800"));

        if (r.target_column && focusY.has(r.target_column))
          newEdges.push(newEdge("e-" + rId + "-out", rId, "foc-" + r.target_column, "#ff9800"));

        procY += ROW_H + 8;
      }

      // ── Per-connection business rules ──
      for (const cid of connIds) {
        const list = rulesByConn.get(cid) || [];
        if (list.length === 0) continue;
        const connLabel = connections.find(c => c.id === cid)?.code || "conn" + cid;

        const labelId = "rule-label-" + cid;
        newNodes.push({
          id: labelId, type: "process",
          position: { x: X_PROCESS, y: procY },
          data: { kind: "rule", label: "Regras / " + connLabel },
        });
        procY += ROW_H + 4;

        for (const r of list) {
          const rId = "rule-" + cid + "-" + r.id;
          newNodes.push({
            id: rId, type: "process",
            position: { x: X_PROCESS, y: procY },
            data: { kind: "rule", label: r.name },
          });

          const cfgSrcCol = (r.config_json as any)?.source_column;
          const destCol = cfgSrcCol ? srcToDest.get(cfgSrcCol) || cfgSrcCol : null;
          if (destCol && focusY.has(destCol))
            newEdges.push(newEdge("e-" + rId + "-in", "foc-" + destCol, rId, "#ff9800"));

          if (r.target_column && focusY.has(r.target_column))
            newEdges.push(newEdge("e-" + rId + "-out", rId, "foc-" + r.target_column, "#ff9800"));

          procY += ROW_H + 4;
        }
        procY += 20;
      }

      // ── Classification rules — GROUPED by destination column ──
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

      for (const [focusCol, grp] of classifGroups) {
        const gId = "cls-group-" + focusCol;
        const scope = grp.global ? "Global" : [...grp.connIds].map(cid => connections.find(c => c.id === cid)?.code || "conn" + cid).join(", ");

        newNodes.push({
          id: gId, type: "process",
          position: { x: X_PROCESS, y: procY },
          data: {
            kind: "classification",
            label: grp.count + " classificações",
            detail: "de " + scope + " para " + focusCol,
          },
        });

        newEdges.push(newEdge("e-" + gId + "-in", "foc-" + focusCol, gId, "#4caf50"));

        // Saida para colunas de destino (quando existem no schema)
        for (const outCol of ["diretoria", "organograma", "centro_custo", "ambiente"]) {
          if (focusY.has(outCol)) {
            newEdges.push(newEdge("e-" + gId + "-to-" + outCol, gId, "foc-" + outCol, "#4caf50", "4 2"));
          }
        }

        procY += ROW_H + 20;
      }

      setNodes(newNodes);
      setEdges(newEdges);

    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar o pipeline");
    } finally { setLoading(false); }
  };

  const destName = destId ? destTables.find(t => t.id === destId)?.name : null;

  // ── Clique: realcar o objeto e seus relacionamentos ──
  const relatedIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const set = new Set<string>([selectedId]);
    for (const e of edges) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [selectedId, edges]);

  const displayNodes = useMemo(
    () => nodes.map(n => ({ ...n, style: { ...(n.style || {}), opacity: selectedId && !relatedIds.has(n.id) ? 0.15 : 1 } })),
    [nodes, selectedId, relatedIds],
  );
  const displayEdges = useMemo(
    () => edges.map(e => ({
      ...e,
      style: { ...(e.style || {}), opacity: selectedId && e.source !== selectedId && e.target !== selectedId ? 0.1 : 1 },
    })),
    [edges, selectedId],
  );

  const selectedNode = selectedId ? nodes.find(n => n.id === selectedId) : null;
  const relatedList = useMemo(() => {
    if (!selectedId) return [];
    return edges
      .filter(e => e.source === selectedId || e.target === selectedId)
      .map(e => {
        const otherId = e.source === selectedId ? e.target : e.source;
        const other = nodes.find(n => n.id === otherId);
        return other ? { id: other.id, label: String((other.data as any)?.label || other.id), outgoing: e.source === selectedId } : null;
      })
      .filter(Boolean) as Array<{ id: string; label: string; outgoing: boolean }>;
  }, [selectedId, edges, nodes]);

  return (
    <Stack spacing={2} sx={{ height: "calc(100vh - 170px)", minHeight: 520 }}>
      <Box>
        <Typography variant="h6">Esteira de dados</Typography>
        <Typography variant="body2" color="text.secondary">
          Fluxo de transformação: origens, colunas de destino, regras de negócio e classificações. Clique em um objeto para ver com quem ele se relaciona.
        </Typography>
      </Box>

      {error && <Alert severity="error" onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction={{ xs: "column", md: "row" }} alignItems={{ md: "center" }} gap={1.5} flexWrap="wrap" useFlexGap>
          <FormControl sx={{ minWidth: 260 }} size="small">
            <InputLabel>Tabela de destino</InputLabel>
            <Select value={destId} label="Tabela de destino"
              onChange={(e) => { setDestId(e.target.value as number); setNodes([]); setEdges([]); setSelectedId(null); }}>
              {destTables.map((t) => (<MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>))}
            </Select>
          </FormControl>
          <Button variant="contained" startIcon={<RefreshOutlined />} onClick={loadPipeline} disabled={!destId}>
            Carregar pipeline
          </Button>
          {destName && <Chip label={"Saída: " + destName} variant="outlined" />}
          {nodes.length > 0 && (
            <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap sx={{ ml: { md: "auto" } }}>
              <Chip label="Fontes" size="small" variant="outlined" sx={{ borderColor: "#0EA5C6" }} />
              <Chip label="Destino" size="small" variant="outlined" sx={{ borderColor: "#0B3FA8" }} />
              <Chip label="Ajustes de custo" size="small" variant="outlined" sx={{ borderColor: "#ff9800" }} />
              <Chip label="Classificação por projeto" size="small" variant="outlined" sx={{ borderColor: "#4caf50" }} />
            </Stack>
          )}
        </Stack>
      </Paper>

      <Box sx={{ flexGrow: 1, minHeight: 0, display: "flex" }}>
        <Paper variant="outlined" sx={{ position: "relative", flexGrow: 1, overflow: "hidden" }}>
          {loading ? (
            <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}><CircularProgress /></Box>
          ) : !destId ? (
            <EmptyState message="Escolha uma tabela de destino e clique em 'Carregar esteira'." />
          ) : nodes.length === 0 ? (
            <EmptyState message="Nenhuma relação de origem encontrada para esta tabela." />
          ) : (
            <ReactFlow
              nodes={displayNodes}
              edges={displayEdges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.1}
              maxZoom={2}
              onNodeClick={(_, node) => setSelectedId(node.id)}
              onPaneClick={() => setSelectedId(null)}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
              <Controls />
              <MiniMap nodeColor={(n) => {
                switch (n.type) {
                  case "sourceCol": return "#0EA5C6";
                  case "focusCol": return "#0B3FA8";
                  case "process": return (n.data as any)?.kind === "classification" ? "#4caf50" : "#ff9800";
                  default: return "#888";
                }
              }} />
            </ReactFlow>
          )}

          {/* Painel de relacionamentos do objeto selecionado */}
          {selectedNode && (
            <Paper elevation={4} sx={{
              position: "absolute", top: 12, right: 12, zIndex: 10,
              p: 1.5, borderRadius: 2, maxWidth: 280, bgcolor: "background.paper",
              border: "1px solid", borderColor: "divider",
            }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                {(selectedNode.data as any)?.label || selectedNode.id}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
                {relatedList.length} relacionamento(s)
              </Typography>
              {relatedList.length === 0 ? (
                <Typography variant="body2" color="text.secondary">Nenhuma relação.</Typography>
              ) : (
                <Stack spacing={0.5} sx={{ maxHeight: 220, overflowY: "auto" }}>
                  {relatedList.map(r => (
                    <Box key={r.id} sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <Typography component="span" sx={{ fontSize: "0.8rem", color: r.outgoing ? "#2e7d32" : "#0EA5C6", flexShrink: 0 }}>
                        {r.outgoing ? "→" : "←"}
                      </Typography>
                      <Typography variant="body2" noWrap sx={{ fontSize: "0.78rem" }}>{r.label}</Typography>
                    </Box>
                  ))}
                </Stack>
              )}
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                Clique fora para limpar a seleção.
              </Typography>
            </Paper>
          )}
        </Paper>
      </Box>
    </Stack>
  );
}
