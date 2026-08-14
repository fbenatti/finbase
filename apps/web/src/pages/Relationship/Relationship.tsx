import { useState, useEffect, useCallback } from "react";
import {
  Box, Typography, Button, Paper, Chip, IconButton,
  Select, MenuItem, FormControl, InputLabel, Alert, CircularProgress,
  Tooltip,
} from "@mui/material";
import {
  Delete, Save, Refresh, ArrowForward,
  Undo, DragIndicator,
} from "@mui/icons-material";
import {
  getConnections, SourceConnection, discoverSchema, ColumnInfo,
  getDestinationTables, getDestinationSchema, DestinationTable,
  getSourceDestMappings, createSourceDestMapping, deleteSourceDestMapping,
  SourceDestinationMapping,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable, { ColumnDef } from "../../components/SortableTable";

interface MappingState {
  savedId: number;
  sourceColumn: string;
  destColumn: string;
}

export default function Relationship() {
  const [connections, setConnections] = useState<SourceConnection[]>([]);
  const [destTables, setDestTables] = useState<DestinationTable[]>([]);
  const [selectedConnId, setSelectedConnId] = useState<number | "">("");
  const [selectedDestId, setSelectedDestId] = useState<number | "">("");
  const [sourceCols, setSourceCols] = useState<ColumnInfo[]>([]);
  const [destCols, setDestCols] = useState<{ name: string; type: string; required: boolean }[]>([]);
  const [mappings, setMappings] = useState<MappingState[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // All mappings datagrid
  const [allMappings, setAllMappings] = useState<SourceDestinationMapping[]>([]);
  const [mappingsLoading, setMappingsLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // Drag state
  const [dragging, setDragging] = useState<string | null>(null);
  const [dragOverDest, setDragOverDest] = useState<string | null>(null);

  useEffect(() => {
    getConnections().then(setConnections).catch(() => {});
    getDestinationTables(true).then(setDestTables).catch(() => {});
    loadAllMappings();
  }, []);

  const loadAllMappings = async () => {
    setMappingsLoading(true);
    try {
      const maps = await getSourceDestMappings();
      setAllMappings(maps);
    } catch { /* ignore */ }
    setMappingsLoading(false);
  };

  const loadAll = useCallback(async () => {
    if (!selectedConnId || !selectedDestId) return;
    setLoading(true); setError(""); setSuccess("");
    try {
      const [schema, destSchema, saved] = await Promise.all([
        discoverSchema(selectedConnId as number),
        getDestinationSchema(selectedDestId as number),
        getSourceDestMappings(selectedConnId as number, selectedDestId as number),
      ]);

      const srcCols = schema.columns.filter((c: ColumnInfo) => c.name && !c.name.startsWith("_"));
      setSourceCols(srcCols);
      setDestCols(destSchema.columns);

      setMappings(saved.map((m: SourceDestinationMapping) => ({
        savedId: m.id,
        sourceColumn: m.source_column,
        destColumn: m.destination_column,
      })));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally { setLoading(false); }
  }, [selectedConnId, selectedDestId]);

  useEffect(() => { if (selectedConnId && selectedDestId) loadAll(); }, [selectedConnId, selectedDestId, loadAll]);

  // Build lookup: destColumn → { sourceColumn, savedId }
  const mappingByDest = new Map<string, MappingState>();
  const mappedSources = new Set<string>();
  for (const m of mappings) {
    mappingByDest.set(m.destColumn, m);
    mappedSources.add(m.sourceColumn);
  }

  // ─── Drag & Drop handlers ───
  const handleDragStart = (sourceColumn: string) => {
    setDragging(sourceColumn);
  };

  const handleDragEnd = () => {
    setDragging(null);
    setDragOverDest(null);
  };

  const handleDragOverDest = (e: React.DragEvent, destColumn: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "link";
    setDragOverDest(destColumn);
  };

  const handleDragLeaveDest = () => {
    setDragOverDest(null);
  };

  const handleDrop = (destColumn: string) => {
    if (!dragging) return;
    setDragOverDest(null);
    setDragging(null);

    // No-op: same source → same dest
    const existingSame = mappings.find(m => m.sourceColumn === dragging && m.destColumn === destColumn);
    if (existingSame) return;

    // Remove existing mapping for this source column (so we don't duplicate)
    const cleaned = mappings.filter(m => m.sourceColumn !== dragging);

    // If another source was mapped to this dest, swap: the old source gets orphaned
    const existing = mappingByDest.get(destColumn);
    let updated: MappingState[];
    if (existing) {
      // User is re-mapping this dest: old mapping removed, new one added
      updated = cleaned.filter(m => m.destColumn !== destColumn);
      updated.push({ savedId: 0, sourceColumn: dragging, destColumn });
    } else {
      updated = [...cleaned, { savedId: 0, sourceColumn: dragging, destColumn }];
    }

    setMappings(updated);
    setSuccess(`"${dragging}" → "${destColumn}"`);
  };

  const handleUnmap = (destColumn: string) => {
    setMappings(prev => prev.filter(m => m.destColumn !== destColumn));
    setSuccess(`Mapeamento "${mappingByDest.get(destColumn)?.sourceColumn} → ${destColumn}" removido.`);
  };

  // ─── Auto-suggest ───
  const autoSuggest = () => {
    // Provider names rarely match FOCUS names literally. Keep these aliases
    // explicit and reviewable; suggestions are still only saved after the
    // user clicks "Salvar".
    const aliases: Record<string, string[]> = {
      date: ["ChargePeriodStart"],
      Date: ["ChargePeriodStart"],
      invoiceId: ["InvoiceId"],
      previousInvoiceId: ["ReferenceInvoiceId"],
      billingAccountId: ["BillingAccountId"],
      billingAccountName: ["BillingAccountName"],
      invoiceSectionId: ["InvoiceDetailId"],
      invoiceSectionName: ["InvoiceDetailDescription"],
      resellerMpnId: ["ServiceProviderName"],
      resellerName: ["ServiceProviderName"],
      costCenter: ["ContractId"],
      billingPeriodEndDate: ["BillingPeriodEnd"],
      billingPeriodStartDate: ["BillingPeriodStart"],
      servicePeriodStartDate: ["ChargePeriodStart"],
      servicePeriodEndDate: ["ChargePeriodEnd"],
      servicePeriodStart: ["ChargePeriodStart"],
      servicePeriodEnd: ["ChargePeriodEnd"],
      productOrderId: ["ContractId"],
      productOrderName: ["ResourceName"],
      meterName: ["SkuMeter"],
      meterId: ["SkuId"],
      meterCategory: ["ServiceSubcategory"],
      meterSubCategory: ["ServiceSubcategory"],
      MeterSubCategory: ["ServiceSubcategory"],
      serviceFamily: ["ServiceCategory"],
      consumedService: ["ServiceName"],
      resourceLocation: ["RegionName"],
      ResourceLocation: ["RegionName"],
      location: ["RegionName"],
      Location: ["RegionName"],
      meterRegion: ["RegionName"],
      resourceGroupName: ["ResourceName"],
      ResourceId: ["ResourceId"],
      resourceId: ["ResourceId"],
      serviceInfo1: ["ChargeDescription"],
      serviceInfo2: ["ChargeDescription"],
      subscriptionName: ["SubAccountName"],
      SubscriptionId: ["SubAccountId"],
      quantity: ["ConsumedQuantity", "PricingQuantity"],
      unitOfMeasure: ["ConsumedUnit", "PricingUnit"],
      costInBillingCurrency: ["BilledCost", "PaymentCurrencyBilledCost"],
      costInUsd: ["EffectiveCost"],
      billingCurrency: ["BillingCurrency"],
      pricingCurrency: ["PricingCurrency"],
      ProductId: ["SkuId"],
      productId: ["SkuId"],
      publisherName: ["ServiceProviderName"],
      PublisherName: ["ServiceProviderName"],
      publisherId: ["ServiceProviderName"],
      publisherType: ["ServiceProviderName"],
      frequency: ["ChargeFrequency"],
      term: ["CommitmentDiscountDurationType"],
      tags: ["Tags", "AllocatedTags"],
      provider: ["HostProviderName", "ServiceProviderName"],
      chargeType: ["ChargeCategory"],
      unitPrice: ["PricingCurrencyContractedUnitPrice"],
      PayGPrice: ["PricingCurrencyListUnitPrice"],
      paygCostInBillingCurrency: ["ListCost"],
      payCostInBillingCurrency: ["ListCost"],
      paygCostInUsd: ["ListCost"],
      effectivePrice: ["PricingCurrencyEffectiveCost"],
      exchangeRatePricingToBilling: ["PricingCurrencyContractedUnitPrice"],
      exchangeRateToBilling: ["PricingCurrencyContractedUnitPrice"],
      exchangeRateToBillingCurrency: ["PricingCurrencyContractedUnitPrice"],
      exchangeRateDate: ["BillingPeriodCreated"],
      isAzureCreditEligible: ["CommitmentProgramEligibilityDetails"],
      additionalInfo: ["SkuPriceDetails"],
      pricingModel: ["CommitmentDiscountType"],
      reservationId: ["CommitmentDiscountId"],
      reservationName: ["CommitmentDiscountName"],
      ProductName: ["ResourceName"],
      productName: ["ResourceName"],
      benefitId: ["CommitmentDiscountId"],
      benefitName: ["CommitmentDiscountName"],
      reservationStatus: ["CommitmentDiscountStatus"],
      reservationQuantity: ["CommitmentDiscountQuantity"],
      billingProfileId: ["BillingAccountId"],
      billingProfileName: ["BillingAccountName"],
    };
    const clean = (value: string) => value.replace(/^\ufeff/, "").toLowerCase().replace(/[^a-z0-9]/g, "");
    const tokens = (value: string) => value.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    const newMappings: MappingState[] = [...mappings];
    const alreadyMappedDests = new Set(newMappings.map(m => m.destColumn));
    const alreadyMappedSrcs = new Set(newMappings.map(m => m.sourceColumn));

    // Explicit provider aliases always win over fuzzy matching, independent
    // of the order in which Azure returned the source columns.
    for (const sc of sourceCols) {
      if (!sc.name || alreadyMappedSrcs.has(sc.name)) continue;
      const sourceKey = sc.name.replace(/^\ufeff/, "");
      const candidates = aliases[sc.name] || aliases[sourceKey] || aliases[sourceKey.toLowerCase()] || [];
      const target = candidates.find(name => destCols.some(d => d.name === name));
      if (target) {
        // An automatic fuzzy suggestion may have occupied this destination.
        // Replace only that suggestion; never overwrite a saved/manual map.
        const conflicting = newMappings.find(m => m.destColumn === target && m.savedId === 0);
        if (conflicting && conflicting.sourceColumn !== sc.name) {
          const index = newMappings.indexOf(conflicting);
          newMappings.splice(index, 1);
          alreadyMappedSrcs.delete(conflicting.sourceColumn);
        }
        const savedConflict = newMappings.find(m => m.destColumn === target && m.savedId !== 0);
        if (savedConflict) continue;
        newMappings.push({ savedId: 0, sourceColumn: sc.name, destColumn: target });
        alreadyMappedDests.add(target);
        alreadyMappedSrcs.add(sc.name);
      }
    }

    for (const sc of sourceCols) {
      if (alreadyMappedSrcs.has(sc.name)) continue;
      if (!sc.name) continue;

      const lower = clean(sc.name);
      let best = ""; let bestScore = 0;
      for (const dc of destCols) {
        if (alreadyMappedDests.has(dc.name)) continue;
        const dl = clean(dc.name);
        const srcTokens = tokens(sc.name);
        const dstTokens = tokens(dc.name);
        const tokenScore = srcTokens.filter(t => dstTokens.some(d => d === t || d.includes(t) || t.includes(d))).length / Math.max(srcTokens.length, dstTokens.length);
        let matches = 0; let lastMatch = -1;
        for (const ch of dl) { const idx = lower.indexOf(ch, lastMatch + 1); if (idx !== -1) { matches++; lastMatch = idx; } }
        const score = Math.max(tokenScore, matches / Math.max(lower.length, dl.length));
        if (score > bestScore && score > 0.4) { bestScore = score; best = dc.name; }
      }
      if (best) {
        alreadyMappedDests.add(best);
        alreadyMappedSrcs.add(sc.name);
        newMappings.push({ savedId: 0, sourceColumn: sc.name, destColumn: best });
      }
    }
    setMappings(newMappings);
    setSuccess(`${newMappings.length - mappings.length} sugestões aplicadas. Revise e clique em Salvar para persistir.`);
  };

  const autoSuggestAzureFocus = () => {
    autoSuggest();
      const requiredAzureMappings: Record<string, string> = {
      reservationName: "CommitmentDiscountName",
      paygCostInBillingCurrency: "ListCost",
      payCostInBillingCurrency: "ListCost",
      exchangeRatePricingToBilling: "PricingCurrencyContractedUnitPrice",
      resourceLocation: "RegionName",
      ProductName: "ResourceName",
      productName: "ResourceName",
      productOrderName: "ResourceName",
      meterRegion: "RegionName",
      resourceGroupName: "ResourceName",
      location: "RegionName",
      unitPrice: "PricingCurrencyContractedUnitPrice",
    };
    setMappings(prev => {
      let next = [...prev];
      for (const [source, dest] of Object.entries(requiredAzureMappings)) {
        const actual = sourceCols.find(c => c.name.replace(/^\ufeff/, "").toLowerCase() === source.toLowerCase())?.name;
        if (!actual || !destCols.some(c => c.name === dest)) continue;
        // A profile mapping is intentional: replace stale automatic mappings
        // that currently occupy this destination, while retaining the source
        // column's existing saved id only when it already points to this dest.
        // Keep other valid source alternatives for the same FOCUS field;
        // the source column must not disappear from the relationship editor.
        next = next.filter(m => m.sourceColumn !== actual);
        next.push({ savedId: 0, sourceColumn: actual, destColumn: dest });
      }
      return next;
    });
    setSuccess("Perfil Azure → FOCUS aplicado aos campos críticos. Revise e clique em Salvar.");
  };

  // ─── Save ───
  const saveAll = async () => {
    if (!selectedConnId || !selectedDestId) return;
    setSaving(true); setError(""); setSuccess("");

    try {
      const connId = selectedConnId as number;
      const destId = selectedDestId as number;

      // Get current saved mappings to diff
      const current = await getSourceDestMappings(connId, destId);
      const savedIds = new Set(current.map((m: SourceDestinationMapping) => m.id));

      let created = 0; let deleted = 0;

      // Delete mappings that no longer exist
      for (const saved of current) {
        const stillExists = mappings.some(m => m.savedId === saved.id);
        if (!stillExists) {
          await deleteSourceDestMapping(saved.id);
          deleted++;
        }
      }

      // Create new mappings (savedId === 0)
      for (const m of mappings) {
        if (m.savedId !== 0) continue;
        await createSourceDestMapping({
          source_connection_id: connId,
          destination_table_id: destId,
          source_column: m.sourceColumn,
          destination_column: m.destColumn,
          transform_type: "RENAME",
          priority: 0,
          active: true,
        } as Partial<SourceDestinationMapping>);
        created++;
      }

      setSuccess(`Salvo: ${created} mapeamentos criados, ${deleted} removidos.`);
      loadAll();
      loadAllMappings();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally { setSaving(false); }
  };

  const hasChanges = mappings.some(m => m.savedId === 0);

  const connName = selectedConnId ? connections.find(c => c.id === selectedConnId)?.name : "";
  const destTableName = selectedDestId ? destTables.find(t => t.id === selectedDestId)?.name : "";

  // ── Datagrid: aggregated table-level relationships ──
  const connById = new Map(connections.map(c => [c.id, c]));
  const destById = new Map(destTables.map(t => [t.id, t]));

  interface TableRelationship {
    _key: string;
    source_connection_id: number;
    destination_table_id: number;
    column_count: number;
    columns: string;
    mappingIds: number[];
  }

  const tableRels: TableRelationship[] = (() => {
    const groups = new Map<string, SourceDestinationMapping[]>();
    for (const m of allMappings) {
      const key = `${m.source_connection_id}:${m.destination_table_id}`;
      const list = groups.get(key) || [];
      list.push(m);
      groups.set(key, list);
    }
    return Array.from(groups.entries()).map(([key, maps]) => {
      const [srcId, dstId] = key.split(":").map(Number);
      return {
        _key: key,
        source_connection_id: srcId,
        destination_table_id: dstId,
        column_count: maps.length,
        columns: maps.map(m => m.source_column + " → " + m.destination_column).join(", "),
        mappingIds: maps.map(m => m.id),
      };
    });
  })();

  const handleDeleteTableRel = async (rel: TableRelationship) => {
    if (!confirm(`Remover o relacionamento entre "${connById.get(rel.source_connection_id)?.name}" e "${destById.get(rel.destination_table_id)?.name}"? Todos os ${rel.column_count} mapeamentos de colunas serão removidos.`)) return;
    setDeletingId(rel.source_connection_id);
    try {
      for (const id of rel.mappingIds) {
        await deleteSourceDestMapping(id);
      }
      setAllMappings(prev => prev.filter(m => !rel.mappingIds.includes(m.id)));
      setSuccess(`Relacionamento removido (${rel.column_count} mapeamentos).`);
      loadAll();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erro ao remover.");
    } finally { setDeletingId(null); }
  };

  const relationshipColumns: ColumnDef<TableRelationship>[] = [
    {
      key: "source_connection_id", label: "Datasource Origem", width: 240,
      render: (r) => {
        const conn = connById.get(r.source_connection_id);
        return (
          <Box>
            <Typography variant="body2" sx={{ fontSize: "0.78rem", fontWeight: 500 }}>
              {conn?.name || `ID ${r.source_connection_id}`}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem", fontFamily: "monospace" }}>
              {conn?.code || ""}
            </Typography>
          </Box>
        );
      },
    },
    {
      key: "destination_table_id", label: "Tabela Destino", width: 200,
      render: (r) => {
        const dest = destById.get(r.destination_table_id);
        return (
          <Typography variant="body2" sx={{ fontSize: "0.78rem", fontWeight: 500 }}>
            {dest?.name || `ID ${r.destination_table_id}`}
          </Typography>
        );
      },
    },
    {
      key: "column_count", label: "Colunas", width: 80,
      render: (r) => (
        <Chip
          label={r.column_count}
          size="small"
          color="primary"
          variant="outlined"
          sx={{ height: 22, fontSize: "0.7rem" }}
        />
      ),
    },
    {
      key: "columns", label: "Mapeamentos", render: (r) => (
        <Tooltip title={r.columns} arrow>
          <Typography
            variant="body2"
            sx={{
              fontSize: "0.72rem", fontFamily: "monospace", color: "text.secondary",
              maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {r.columns}
          </Typography>
        </Tooltip>
      ),
    },
    {
      key: "_edit" as keyof TableRelationship, label: "", width: 50, sortable: false, filterable: false,
      render: (r) => (
        <Tooltip title="Editar mapeamentos de colunas">
          <IconButton size="small" color="primary"
            onClick={() => {
              setSelectedConnId(r.source_connection_id);
              setSelectedDestId(r.destination_table_id);
            }}>
            <Refresh sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
      ),
    },
    {
      key: "_delete" as keyof TableRelationship, label: "", width: 50, sortable: false, filterable: false,
      render: (r) => (
        <IconButton size="small" color="error"
          onClick={() => handleDeleteTableRel(r)}
          disabled={deletingId === r.source_connection_id}>
          {deletingId === r.source_connection_id ? <CircularProgress size={16} /> : <Delete sx={{ fontSize: 16 }} />}
        </IconButton>
      ),
    },
  ];

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 3 }}>
        <Typography variant="h4">Relacionamento</Typography>
      </Box>

      {/* ── Toolbar ── */}
      <Box sx={{ display: "flex", gap: 2, mb: 2, flexWrap: "wrap", alignItems: "center" }}>
        <FormControl sx={{ minWidth: 220 }} size="small">
          <InputLabel>Fonte (Datasource)</InputLabel>
          <Select
            value={selectedConnId}
            label="Fonte (Datasource)"
            onChange={(e) => { setSelectedConnId(e.target.value as number | ""); setMappings([]); }}
          >
            {connections.map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
            ))}
          </Select>
        </FormControl>

        <ArrowForward sx={{ color: "#888", mt: 1 }} />

        <FormControl sx={{ minWidth: 200 }} size="small">
          <InputLabel>Tabela Destino</InputLabel>
          <Select
            value={selectedDestId}
            label="Tabela Destino"
            onChange={(e) => { setSelectedDestId(e.target.value as number | ""); setMappings([]); }}
          >
            {destTables.map((t) => (
              <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <Button variant="outlined" startIcon={<Refresh />}
          onClick={loadAll} disabled={!selectedConnId || !selectedDestId}>
          Carregar
        </Button>

        <Box sx={{ flexGrow: 1 }} />

        {sourceCols.length > 0 && (
          <>
            <Button size="small" startIcon={<span>✨</span>} onClick={autoSuggest}>
              Similaridade
            </Button>
            {String(connName).toLowerCase().includes("azure") && (
              <Button size="small" variant="outlined" color="secondary" startIcon={<span>☁️</span>} onClick={autoSuggestAzureFocus}>
                Perfil Azure → FOCUS
              </Button>
            )}
            <Button variant="contained" startIcon={<Save />} onClick={saveAll}
              disabled={saving || !hasChanges}>
              {saving ? "Salvando..." : "Salvar"}
            </Button>
          </>
        )}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{success}</Alert>}

      {loading && <CircularProgress sx={{ display: "block", mx: "auto", mt: 4 }} />}

      {!loading && !selectedConnId && !selectedDestId && (
        <EmptyState message="Selecione uma fonte e uma tabela destino para criar os relacionamentos." />
      )}

      {!loading && selectedConnId && selectedDestId && sourceCols.length === 0 && (
        <EmptyState message="Nenhuma coluna encontrada. Execute o ETL primeiro para descobrir o schema da fonte." />
      )}

      {/* ── Drag & Drop area ── */}
      {!loading && sourceCols.length > 0 && (
        <Box sx={{ display: "flex", gap: 2, mt: 1 }}>
          {/* Left: Source columns */}
          <Paper
            sx={{
              width: 280, maxHeight: "calc(100vh - 280px)", overflowY: "auto",
              p: 1, bgcolor: "#f8f9fa", border: "2px dashed #dee2e6",
              borderRadius: 2,
            }}
          >
            <Typography variant="subtitle2" sx={{ mb: 1, px: 1, color: "text.secondary" }}>
              Colunas da Fonte ({connName})
            </Typography>
            {sourceCols.map((col) => {
              const isMapped = mappedSources.has(col.name);
              return (
                <Box
                  key={col.name}
                  draggable
                  onDragStart={() => handleDragStart(col.name)}
                  onDragEnd={handleDragEnd}
                  sx={{
                    display: "flex", alignItems: "center", gap: 0.5,
                    px: 1, py: 0.7, mb: 0.4, borderRadius: 1,
                    cursor: "grab",
                    bgcolor: isMapped ? "#e8f5e9" : dragging === col.name ? "#e3f2fd" : "#fff",
                    border: isMapped ? "1px solid #a5d6a7" : "1px solid #e0e0e0",
                    opacity: dragging === col.name ? 0.7 : 1,
                    userSelect: "none",
                    transition: "all 0.15s",
                    "&:hover": { borderColor: "#90caf9", bgcolor: "#f5f9ff" },
                  }}
                >
                  {isMapped ? (
                    <Box sx={{ width: 20, display: "flex", justifyContent: "center" }}>
                      <Typography variant="caption" color="success.main">✓</Typography>
                    </Box>
                  ) : (
                    <DragIndicator sx={{ fontSize: 16, color: "#bbb" }} />
                  )}
                  <Typography
                    variant="body2"
                    sx={{
                      fontFamily: "monospace", fontSize: "0.78rem",
                      textDecoration: isMapped ? "none" : undefined,
                      flexGrow: 1,
                    }}
                  >
                    {col.name}
                  </Typography>
                  {isMapped && (
                    <Tooltip title="Arraste para re-mapear para outra coluna">
                      <Typography variant="caption" sx={{ fontSize: "0.6rem", color: "#4caf50" }}>
                        →{mappings.find(m => m.sourceColumn === col.name)?.destColumn || ""}
                      </Typography>
                    </Tooltip>
                  )}
                </Box>
              );
            })}
          </Paper>

          {/* Divider arrow */}
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", width: 60 }}>
            <Typography variant="h5" sx={{ color: "#bbb" }}>→</Typography>
          </Box>

          {/* Right: Destination columns as drop targets */}
          <Paper
            sx={{
              flexGrow: 1, maxHeight: "calc(100vh - 280px)", overflowY: "auto",
              p: 1, border: "2px solid #e0e0e0", borderRadius: 2,
            }}
          >
            <Typography variant="subtitle2" sx={{ mb: 1, px: 1, color: "text.secondary" }}>
              Colunas do Destino ({destTableName})
            </Typography>
            {destCols.map((dc) => {
              const mapping = mappingByDest.get(dc.name);
              const isDropTarget = dragOverDest === dc.name;
              const isDragging = !!dragging;
              return (
                <Box
                  key={dc.name}
                  onDragOver={(e) => handleDragOverDest(e, dc.name)}
                  onDragLeave={handleDragLeaveDest}
                  onDrop={() => handleDrop(dc.name)}
                  sx={{
                    display: "flex", alignItems: "center", gap: 1,
                    px: 1.5, py: 0.7, mb: 0.4, borderRadius: 1,
                    bgcolor: mapping
                      ? "#e8f5e9"
                      : isDropTarget
                        ? "#e3f2fd"
                        : isDragging
                          ? "#fafafa"
                          : "#fff",
                    border: mapping
                      ? "1px solid #a5d6a7"
                      : isDropTarget
                        ? "2px dashed #42a5f5"
                        : "1px solid #e8e8e8",
                    transition: "all 0.15s",
                    cursor: isDragging ? "copy" : "default",
                    "&:hover": isDragging ? { borderColor: "#90caf9", bgcolor: "#f5f9ff" } : {},
                  }}
                >
                  <Box sx={{ flexGrow: 1, display: "flex", alignItems: "center", gap: 1 }}>
                    <Typography
                      variant="body2"
                      sx={{ fontFamily: "monospace", fontSize: "0.78rem", fontWeight: 500 }}
                    >
                      {dc.name}
                    </Typography>
                    <Typography variant="caption" sx={{ color: "#aaa", fontFamily: "monospace" }}>
                      {dc.type} {dc.required && "*"}
                    </Typography>
                  </Box>

                  {mapping ? (
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <Chip
                        label={
                          <Box component="span" sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                            <DragIndicator sx={{ fontSize: 12 }} />
                            <span>{mapping.sourceColumn}</span>
                          </Box>
                        }
                        size="small"
                        color="success"
                        variant="outlined"
                        onDelete={() => handleUnmap(dc.name)}
                        deleteIcon={<Delete sx={{ fontSize: 14 }} />}
                        sx={{ fontSize: "0.68rem", height: 22 }}
                      />
                    </Box>
                  ) : dragging ? (
                    <Typography variant="caption" sx={{ color: "#bbb", fontStyle: "italic" }}>
                      solte aqui
                    </Typography>
                  ) : null}
                </Box>
              );
            })}

            {destCols.length === 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: "center" }}>
                Nenhuma coluna no destino.
              </Typography>
            )}
          </Paper>
        </Box>
      )}

      {/* ── Summary footer ── */}
      {mappings.length > 0 && (
        <Box sx={{ mt: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Chip
            label={`${mappings.length} mapeamentos`}
            color="success" size="small"
          />
          <Chip
            label={`${sourceCols.filter(c => !mappedSources.has(c.name)).length} colunas fonte não mapeadas`}
            size="small" variant="outlined"
          />
          {hasChanges && (
            <Chip
              icon={<Undo sx={{ fontSize: 14 }} />}
              label="Alterações não salvas"
              color="warning" size="small"
            />
          )}
        </Box>
      )}

      {/* ── Datagrid: Relacionamentos por Tabela ── */}
      <Box sx={{ mt: 4 }}>
        <Typography variant="h6" sx={{ mb: 1 }}>
          Relacionamentos Criados
        </Typography>
        {mappingsLoading ? (
          <CircularProgress sx={{ display: "block", mx: "auto", mt: 2 }} />
        ) : tableRels.length === 0 ? (
          <Paper sx={{ p: 3, textAlign: "center", color: "text.secondary" }}>
            Nenhum relacionamento criado ainda.
          </Paper>
        ) : (
          <SortableTable
            columns={relationshipColumns}
            data={tableRels}
            getRowKey={(r) => (r as unknown as TableRelationship)._key}
            size="small"
            placeholder="Filtrar por datasource ou tabela destino..."
            defaultSortKey="destination_table_id"
          />
        )}
      </Box>

      {/* Drag cursor overlay */}
      {dragging && (
        <Box
          sx={{
            position: "fixed", inset: 0, zIndex: 9999, pointerEvents: "none",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <Paper
            sx={{
              px: 2, py: 1, borderRadius: 2,
              bgcolor: "#e3f2fd", border: "2px solid #42a5f5",
              boxShadow: 4,
            }}
          >
            <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
              <DragIndicator sx={{ verticalAlign: "middle", mr: 0.5, fontSize: 16 }} />
              {dragging}
            </Typography>
          </Paper>
        </Box>
      )}
    </Box>
  );
}
