import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect, useCallback, useRef } from "react";
import {
  Box, Typography, Button, Paper, IconButton, Chip,
  Select, MenuItem, FormControl, InputLabel, TextField,
  Alert, CircularProgress, Tooltip, Divider, Autocomplete,
} from "@mui/material";
import {
  Add, Delete, AutoFixHigh, SwapHoriz, Visibility,
  Save, Refresh, Download,
} from "@mui/icons-material";
import {
  getConnections, SourceConnection, discoverSchema, ColumnInfo,
  getMappings, createMapping, updateMapping, deleteMapping, EtlMapping,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";

const TRANSFORM_TYPES = ["RENAME", "CAST", "CONSTANT", "EXPRESSION"];

// Fuzzy similarity: how close two strings are (0..1)
function similarity(a: string, b: string): number {
  const aLower = a.toLowerCase().replace(/[^a-z0-9]/g, "");
  const bLower = b.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!aLower || !bLower) return 0;
  // Count matching chars in order
  let matches = 0; let lastMatch = -1;
  for (const ch of bLower) {
    const idx = aLower.indexOf(ch, lastMatch + 1);
    if (idx !== -1) { matches++; lastMatch = idx; }
  }
  const len = Math.max(aLower.length, bLower.length);
  return matches / len;
}

// Suggest target names for a source column
function suggestTargetName(src: string): string {
  return src
    .replace(/([A-Z])/g, "_$1")
    .toLowerCase()
    .replace(/^_/, "")
    .replace(/_+/g, "_");
}

interface MappingDraft {
  id: number; // 0 = new, >0 = existing
  source_column: string;
  target_column: string;
  transform_type: string;
  transform_config: string; // JSON string
  name: string;
  priority: number;
  active: boolean;
}

export default function Mappings() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [selectedConnId, setSelectedConnId] = useState<number | "">("");
  const [columns, setColumns] = useState<ColumnInfo[]>([]);
  const [mappings, setMappings] = useState<EtlMapping[]>([]);
  const [drafts, setDrafts] = useState<MappingDraft[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Drag state
  const [dragSrc, setDragSrc] = useState<string | null>(null);

  // Inline edit
  const [editDraftIdx, setEditDraftIdx] = useState<number | null>(null);

  // Save status per draft
  const [savingIdx, setSavingIdx] = useState<number | null>(null);

  useEffect(() => {
    getConnections(true).then(setConnections).catch(() => {});
  }, []);

  const loadAll = useCallback(async () => {
    if (!selectedConnId) return;
    setLoading(true); setError(""); setSuccess("");
    try {
      const connId = selectedConnId as number;
      const [schema, maps] = await Promise.all([
        discoverSchema(connId),
        getMappings(connId),
      ]);
      setColumns(schema.columns.filter(c => c.name && !c.name.startsWith("_")));
      setMappings(maps);

      // Build drafts: existing mappings + unmapped columns as suggestions
      const mappedSrcs = new Set(maps.map(m => m.source_column));
      const existingDrafts: MappingDraft[] = maps.map(m => ({
        id: m.id,
        source_column: m.source_column,
        target_column: m.target_column,
        transform_type: m.transform_type,
        transform_config: m.transform_config ? JSON.stringify(m.transform_config) : "",
        name: m.name,
        priority: m.priority,
        active: m.active,
      }));

      // Suggest mappings for unmapped columns
      const suggestedDrafts: MappingDraft[] = [];
      for (const col of schema.columns) {
        if (!mappedSrcs.has(col.name) && col.name && !col.name.startsWith("_")) {
          suggestedDrafts.push({
            id: 0,
            source_column: col.name,
            target_column: suggestTargetName(col.name),
            transform_type: "RENAME",
            transform_config: "",
            name: `${col.name} → ${suggestTargetName(col.name)}`,
            priority: 0,
            active: false, // suggested, not active by default
          });
        }
      }
      setDrafts([...existingDrafts, ...suggestedDrafts]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  }, [selectedConnId]);

  useEffect(() => { if (selectedConnId) loadAll(); }, [selectedConnId, loadAll]);

  // Auto-suggest: fuzzy match source columns to standard target names
  const handleAutoSuggest = () => {
    const standardTargets = [
      "date", "subscription_id", "subscription_name", "resource_group",
      "service_name", "service_category", "meter_category", "meter_name",
      "meter_subcategory", "resource_type", "resource_name", "resource_id",
      "region", "currency", "unit_of_measure", "quantity", "effective_price",
      "cost_usd", "payg_cost_usd", "cost_brl", "tax_base", "tax_marketplace",
      "final_cost_brl", "diretoria", "modelo_ai",
    ];

    const newDrafts = [...drafts];
    for (let i = 0; i < newDrafts.length; i++) {
      const d = newDrafts[i];
      if (d.id > 0) continue; // don't touch saved mappings
      let bestTarget = d.target_column;
      let bestScore = 0;
      for (const tgt of standardTargets) {
        const score = similarity(d.source_column, tgt);
        if (score > bestScore && score > 0.3) {
          bestScore = score;
          bestTarget = tgt;
        }
      }
      if (bestTarget !== d.target_column) {
        newDrafts[i] = { ...d, target_column: bestTarget, name: `${d.source_column} → ${bestTarget}` };
      }
    }
    setDrafts(newDrafts);
    setSuccess("Auto-suggest aplicado. Revise os mapeamentos e clique em Salvar Tudo.");
  };

  // Drag & drop
  const handleDragStart = (srcCol: string) => setDragSrc(srcCol);
  const handleDragOver = (e: React.DragEvent) => e.preventDefault();
  const handleDropOnTarget = (targetCol: string) => {
    if (!dragSrc) return;
    // Check if mapping already exists
    const exists = drafts.find(d => d.source_column === dragSrc);
    if (exists) {
      // Update existing target
      setDrafts(prev => prev.map(d =>
        d.source_column === dragSrc ? { ...d, target_column: targetCol, name: `${dragSrc} → ${targetCol}` } : d
      ));
    } else {
      setDrafts(prev => [...prev, {
        id: 0, source_column: dragSrc, target_column: targetCol,
        transform_type: "RENAME", transform_config: "", name: `${dragSrc} → ${targetCol}`,
        priority: 0, active: true,
      }]);
    }
    setDragSrc(null);
  };

  // Add a new target column
  const [newTargetCol, setNewTargetCol] = useState("");
  const allTargets = [...new Set(drafts.map(d => d.target_column))].filter(Boolean).sort();

  const addTargetColumn = () => {
    if (!newTargetCol.trim()) return;
    const tgt = newTargetCol.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
    setDrafts(prev => {
      const exists = prev.find(d => d.target_column === tgt || d.source_column === tgt);
      if (exists) return prev;
      return [...prev, {
        id: 0, source_column: "", target_column: tgt,
        transform_type: "CONSTANT", transform_config: '{"value": "TODO"}',
        name: `CONSTANT → ${tgt}`, priority: 999, active: true,
      }];
    });
    setNewTargetCol("");
  };

  // Delete a draft
  const removeDraft = async (idx: number) => {
    const d = drafts[idx];
    if (d.id > 0) {
      try { await deleteMapping(d.id); } catch { /* ignore */ }
    }
    setDrafts(prev => prev.filter((_, i) => i !== idx));
    if (d.id > 0) loadAll();
  };

  // Toggle active
  const toggleActive = (idx: number) => {
    setDrafts(prev => prev.map((d, i) => i === idx ? { ...d, active: !d.active } : d));
  };

  // Save single draft
  const saveDraft = async (idx: number) => {
    const d = drafts[idx];
    setSavingIdx(idx);
    const connId = selectedConnId as number;
    let config: Record<string, unknown> | null = null;
    try {
      if (d.transform_config.trim()) config = JSON.parse(d.transform_config);
    } catch { setError("JSON inválido na config"); setSavingIdx(null); return; }

    try {
      if (d.id > 0) {
        await updateMapping(d.id, {
          name: d.name, source_column: d.source_column, target_column: d.target_column,
          transform_type: d.transform_type, transform_config: config, priority: d.priority, active: d.active,
        });
      } else {
        const created = await createMapping({
          connection_id: connId, name: d.name, source_column: d.source_column,
          target_column: d.target_column, transform_type: d.transform_type,
          transform_config: config, priority: d.priority, active: d.active,
        });
        setDrafts(prev => prev.map((dd, i) => i === idx ? { ...dd, id: created.id } : dd));
      }
      setSuccess(`${d.name} salvo`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally { setSavingIdx(null); }
  };

  // Save all unsaved
  const saveAll = async () => {
    setLoading(true); setError(""); setSuccess("");
    let saved = 0;
    for (let i = 0; i < drafts.length; i++) {
      if (drafts[i].active) {
        try {
          await saveDraft(i);
          saved++;
        } catch { /* continue */ }
      }
    }
    setLoading(false);
    setSuccess(`${saved} mapeamentos salvos`);
    loadAll();
  };

  // Update draft field
  const updateDraft = (idx: number, field: string, value: string | number | boolean) => {
    setDrafts(prev => prev.map((d, i) => {
      if (i !== idx) return d;
      const updated = { ...d, [field]: value };
      if (field === "source_column" || field === "target_column") {
        updated.name = `${updated.source_column} → ${updated.target_column}`;
      }
      return updated;
    }));
  };

  const selectedConn = connections.find(c => c.id === selectedConnId);

  const sourceCols = columns.map(c => c.name);
  const mappedSourceCols = new Set(drafts.filter(d => d.active).map(d => d.source_column));
  const unmappedCols = sourceCols.filter(c => !mappedSourceCols.has(c));

  if (loading && drafts.length === 0) return <CircularProgress sx={{ display: "block", mx: "auto", mt: 10 }} />;

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: "flex", justifyContent: "space-between", mb: 2 }}>
        <Box>
          <Typography variant="h4">Mapeamento de Colunas</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Arraste colunas de origem para colunas de destino ou use Auto-Suggest
          </Typography>
        </Box>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button variant="outlined" startIcon={<AutoFixHigh />} onClick={handleAutoSuggest}
            disabled={!selectedConnId || columns.length === 0}>
            Auto-Suggest
          </Button>
          <Button variant="contained" startIcon={<Save />} onClick={saveAll}
            disabled={!selectedConnId || loading}>
            {loading ? "Salvando..." : "Salvar Tudo"}
          </Button>
        </Box>
      </Box>

      {/* Datasource selector */}
      <Paper sx={{ p: 2, mb: 2, display: "flex", gap: 2, alignItems: "center" }}>
        <FormControl sx={{ minWidth: 280 }} size="small">
          <InputLabel>Datasource</InputLabel>
          <Select value={selectedConnId} label="Datasource"
            onChange={(e) => setSelectedConnId(e.target.value as number)}>
            <MenuItem value=""><em>Selecione...</em></MenuItem>
            {connections.map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
            ))}
          </Select>
        </FormControl>
        {selectedConn && (
          <Chip label={`${columns.length} colunas descobertas`} size="small" color="primary" variant="outlined" />
        )}
        {unmappedCols.length > 0 && (
          <Chip label={`${unmappedCols.length} não mapeadas`} size="small" color="warning" variant="outlined" />
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button size="small" startIcon={<Refresh />} onClick={loadAll} disabled={!selectedConnId}>
          Recarregar
        </Button>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{dataDisplayText(success)}</Alert>}

      {!selectedConnId ? (
        <EmptyState message="Selecione um datasource para descobrir as colunas e criar mapeamentos." />
      ) : columns.length === 0 ? (
        <EmptyState message="Nenhuma coluna descoberta. Execute a coleta primeiro para preencher os dados e descobrir colunas." />
      ) : (
        <>
          {/* Legend */}
          <Box sx={{ display: "flex", gap: 2, mb: 1, alignItems: "center" }}>
            <Chip
              label="Arraste colunas da esquerda para as colunas destino na direita"
              icon={<SwapHoriz />}
              size="small"
              color="info"
              variant="outlined"
            />
          </Box>

          <Box sx={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 2 }}>
            {/* LEFT PANEL - Source Columns */}
            <Paper sx={{ p: 2, height: "fit-content", maxHeight: "calc(100vh - 230px)", overflow: "auto" }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 700, color: "#1565c0" }}>
                  Colunas de Origem
                </Typography>
                <Chip label={sourceCols.length} size="small" sx={{ ml: "auto" }} />
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
                {selectedConn?.name} — arraste para mapear
              </Typography>
              <Divider sx={{ mb: 1 }} />
              {sourceCols.map(col => {
                const colInfo = columns.find(c => c.name === col);
                const isMapped = mappedSourceCols.has(col);
                return (
                  <Box
                    key={col}
                    draggable
                    onDragStart={() => handleDragStart(col)}
                    sx={{
                      display: "flex", alignItems: "center", gap: 1,
                      p: 0.75, mb: 0.5, borderRadius: 1, cursor: "grab",
                      bgcolor: isMapped ? "grey.100" : "#e3f2fd",
                      border: isMapped ? "1px solid transparent" : "1px solid #90caf9",
                      opacity: isMapped ? 0.5 : 1,
                      "&:hover": { bgcolor: isMapped ? "grey.200" : "#bbdefb" },
                      transition: "all 0.15s",
                    }}
                  >
                    <Typography variant="body2" sx={{
                      fontWeight: isMapped ? 400 : 600,
                      fontFamily: "monospace", fontSize: "0.75rem",
                      textDecoration: isMapped ? "line-through" : "none",
                      flexGrow: 1,
                    }}>
                      {col}
                    </Typography>
                    {isMapped && <Chip label="mapeada" size="small" sx={{ height: 18, fontSize: "0.6rem" }} />}
                    {colInfo?.sample_values?.[0] && !isMapped && (
                      <Tooltip title={`Ex: ${colInfo.sample_values[0]}`} arrow>
                        <Chip label={colInfo.sample_values[0].slice(0, 20)} size="small"
                          variant="outlined" sx={{ height: 18, fontSize: "0.6rem", maxWidth: 100 }} />
                      </Tooltip>
                    )}
                  </Box>
                );
              })}
              {sourceCols.length === 0 && (
                <Typography variant="body2" color="text.secondary">Nenhuma coluna descoberta.</Typography>
              )}
            </Paper>

            {/* RIGHT PANEL - Target Columns & Mappings */}
            <Paper sx={{ p: 2, maxHeight: "calc(100vh - 230px)", overflow: "auto" }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, color: "#1a237e" }}>
                    Tabela de Destino
                  </Typography>
                  <Chip label={`${allTargets.length} colunas`} size="small" />
                </Box>
                <Box sx={{ display: "flex", gap: 1 }}>
                  <TextField
                    size="small"
                    placeholder="Nova coluna destino"
                    value={newTargetCol}
                    onChange={(e) => setNewTargetCol(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addTargetColumn()}
                    sx={{ width: 180 }}
                  />
                  <Button size="small" variant="outlined" startIcon={<Add />} onClick={addTargetColumn}>
                    Adicionar
                  </Button>
                </Box>
              </Box>
              <Divider sx={{ mb: 2 }} />

              {/* Group by target column */}
              {allTargets.map(tgt => {
                const tgtDrafts = drafts.filter(d => d.target_column === tgt);
                return (
                  <Box
                    key={tgt}
                    onDragOver={handleDragOver}
                    onDrop={() => handleDropOnTarget(tgt)}
                    sx={{
                      mb: 1.5, p: 1.5, borderRadius: 2,
                      border: dragSrc ? "2px dashed #1976d2" : "1px solid #e0e0e0",
                      bgcolor: dragSrc ? "#e3f2fd" : "#fafafa",
                      transition: "all 0.2s",
                    }}
                  >
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: tgtDrafts.length > 0 ? 1 : 0 }}>
                      <Typography variant="body2" sx={{
                        fontWeight: 700, fontFamily: "monospace", fontSize: "0.8rem",
                        color: "#1a237e",
                      }}>
                        {tgt}
                      </Typography>
                      <Chip label="destino" size="small" sx={{ height: 18, fontSize: "0.6rem", bgcolor: "#1a237e", color: "#fff" }} />
                      {dragSrc && (
                        <Chip label={`Solte "${dragSrc}" aqui`} size="small" color="info" sx={{ ml: "auto" }} />
                      )}
                    </Box>

                    {/* Mapping rows for this target */}
                    {tgtDrafts.map(d => {
                      const idx = drafts.indexOf(d);
                      return (
                        <Box key={`${d.source_column}-${tgt}-${idx}`} sx={{
                          display: "flex", alignItems: "center", gap: 0.5,
                          py: 0.5, px: 1, mb: 0.5, borderRadius: 1,
                          bgcolor: d.active ? "#fff" : "grey.100",
                          border: "1px solid #e0e0e0",
                        }}>
                          {/* Source */}
                          {d.source_column ? (
                            <Chip
                              label={d.source_column}
                              size="small"
                              sx={{ minWidth: 100, fontFamily: "monospace", fontSize: "0.7rem", bgcolor: "#e3f2fd" }}
                              onDelete={() => removeDraft(idx)}
                            />
                          ) : (
                            <Chip label="CONSTANT" size="small" color="secondary"
                              sx={{ minWidth: 80, fontSize: "0.7rem" }}
                              onDelete={() => removeDraft(idx)} />
                          )}

                          {/* Arrow */}
                          <SwapHoriz sx={{ fontSize: 16, color: "#888" }} />

                          {/* Transform type */}
                          <Select
                            size="small"
                            value={d.transform_type}
                            onChange={(e) => updateDraft(idx, "transform_type", e.target.value)}
                            sx={{ minWidth: 100, height: 28, fontSize: "0.7rem", "& .MuiSelect-select": { py: 0.5 } }}
                          >
                            {TRANSFORM_TYPES.map(t => <MenuItem key={t} value={t} dense sx={{ fontSize: "0.7rem" }}>{t}</MenuItem>)}
                          </Select>

                          {d.transform_type === "CAST" && (
                            <TextField
                              size="small"
                              placeholder='{"dtype":"float64"}'
                              value={d.transform_config}
                              onChange={(e) => updateDraft(idx, "transform_config", e.target.value)}
                              sx={{ width: 140, "& input": { fontSize: "0.7rem", py: 0.5 } }}
                            />
                          )}
                          {d.transform_type === "CONSTANT" && (
                            <TextField
                              size="small"
                              placeholder='{"value":"default"}'
                              value={d.transform_config}
                              onChange={(e) => updateDraft(idx, "transform_config", e.target.value)}
                              sx={{ width: 140, "& input": { fontSize: "0.7rem", py: 0.5 } }}
                            />
                          )}
                          {d.transform_type === "EXPRESSION" && (
                            <TextField
                              size="small"
                              placeholder='{"expression":"A*B"}'
                              value={d.transform_config}
                              onChange={(e) => updateDraft(idx, "transform_config", e.target.value)}
                              sx={{ width: 140, "& input": { fontSize: "0.7rem", py: 0.5 } }}
                            />
                          )}

                          {/* Priority */}
                          <TextField
                            size="small"
                            type="number"
                            value={d.priority}
                            onChange={(e) => updateDraft(idx, "priority", parseInt(e.target.value) || 0)}
                            sx={{ width: 50, "& input": { fontSize: "0.7rem", py: 0.5, textAlign: "center" } }}
                          />

                          {/* Active toggle */}
                          <Chip
                            label={d.active ? "ativo" : "inativo"}
                            size="small"
                            color={d.active ? "success" : "default"}
                            onClick={() => toggleActive(idx)}
                            sx={{ height: 22, fontSize: "0.6rem", cursor: "pointer" }}
                          />

                          {/* Save button */}
                          <Tooltip title="Salvar este mapeamento">
                            <IconButton size="small" onClick={() => saveDraft(idx)}
                              disabled={savingIdx === idx}
                              color={d.id > 0 ? "default" : "primary"}>
                              {savingIdx === idx ? <CircularProgress size={14} /> : <Save sx={{ fontSize: 16 }} />}
                            </IconButton>
                          </Tooltip>

                          {/* Status */}
                          {d.id > 0 && <Chip label="salvo" size="small" color="primary" variant="outlined" sx={{ height: 20, fontSize: "0.6rem" }} />}
                        </Box>
                      );
                    })}

                    {tgtDrafts.filter(d => d.active).length === 0 && (
                      <Typography variant="caption" color="text.secondary">
                        Arraste uma coluna de origem aqui ou clique em Auto-Suggest
                      </Typography>
                    )}
                  </Box>
                );
              })}

              {drafts.length === 0 && (
                <EmptyState message="Nenhum mapeamento. Arraste colunas da esquerda para criar mapeamentos ou use Auto-Suggest." />
              )}
            </Paper>
          </Box>
        </>
      )}
    </Box>
  );
}
