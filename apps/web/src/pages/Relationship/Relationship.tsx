import { dataDisplayText } from "../../utils/dataPresentation";
import { useState, useEffect, useCallback } from "react";
import {
  Box, Typography, Button, Paper, Chip, IconButton,
  Select, MenuItem, FormControl, InputLabel, Alert, CircularProgress,
  Tooltip, Collapse,
} from "@mui/material";
import { DeleteOutlined, SaveOutlined, RefreshOutlined, ArrowForwardOutlined, UndoOutlined, DragIndicatorOutlined, ExpandMoreOutlined, ChevronRightOutlined, EditOutlined } from "@mui/icons-material";
import {
  getConnections, SourceConnection, discoverSchema, ColumnInfo,
  getDestinationTables, getDestinationSchema, DestinationTable, DestinationPartitionSpec,
  getSourceDestMappings, createSourceDestMapping, deleteSourceDestMapping,
  SourceDestinationMapping,
} from "../../services/api";
import EmptyState from "../../components/EmptyState";
import SortableTable, { ColumnDef } from "../../components/SortableTable";
import DestinationPartitionSettings from "../../components/DestinationPartitionSettings";

interface MappingState {
  savedId: number;
  sourceColumn: string;
  rawSourceColumn: string;
  destColumn: string;
  transformType?: string;
  transformConfig?: Record<string, unknown> | null;
}

function describeTransform(
  mapping: {
    source_column: string;
    destination_column: string;
    transform_type?: string;
    transform_config?: Record<string, unknown> | null;
  },
): string {
  const cfg = mapping.transform_config || {};
  const type = (mapping.transform_type || "RENAME").toUpperCase();
  const src = mapping.source_column;
  const dest = mapping.destination_column;

  if (type === "RENAME") {
    return `${src} → ${dest} (cópia direta)`;
  }
  if (type === "JSON_PATH") {
    const path = cfg.path ? `.${String(cfg.path)}` : "";
    return `${src}${path} → ${dest}`;
  }
  if (type === "CONST") {
    return `${dest} = ${String(cfg.value ?? "")}`;
  }
  if (type === "VALUE_MAP") {
    const map = cfg.map as Record<string, string> | undefined;
    const entries = map ? Object.entries(map).map(([k, v]) => `${k}→${v}`).join(", ") : "";
    return `${src} → ${dest} (mapa: ${entries || "—"}; default: ${String(cfg.default ?? "—")})`;
  }
  if (type === "CONDITIONAL") {
    const whenCol = String(cfg.when_column || src);
    const whenPath = cfg.when_path ? `.${String(cfg.when_path)}` : "";
    const whenOp = String(cfg.when_op || "in");
    const whenValues = Array.isArray(cfg.when_values)
      ? cfg.when_values.join(", ")
      : whenOp === "not_empty"
        ? "preenchido"
        : "—";
    const thenType = String(cfg.then_type || "RENAME").toUpperCase();
    const thenSource = String(cfg.then_source || src);
    const thenCfg = (cfg.then_config || {}) as Record<string, unknown>;
    let thenDetail = thenType;
    if (thenType === "JSON_PATH") {
      thenDetail = `JSON_PATH ${thenSource}.${String(thenCfg.path || "")}`;
    } else if (thenType === "VALUE_MAP") {
      const map = thenCfg.map as Record<string, string> | undefined;
      const entries = map ? Object.entries(map).map(([k, v]) => `${k}→${v}`).join(", ") : "";
      thenDetail = `VALUE_MAP {${entries}} default=${String(thenCfg.default ?? "—")}`;
    } else if (thenType === "CONST") {
      thenDetail = `CONST ${String(thenCfg.value ?? "")}`;
    } else {
      thenDetail = `${thenType} ${thenSource}`;
    }
    const predicate = whenOp === "not_empty"
      ? `${whenCol}${whenPath} preenchido`
      : `${whenCol}${whenPath} ∈ [${whenValues}]`;
    return `${src} → ${dest}\nQuando ${predicate}\nEntão ${thenDetail}`;
  }
  return `${src} → ${dest} (${type})`;
}

function transformChipLabel(mapping: MappingState): string {
  const base = mapping.sourceColumn;
  const type = mapping.transformType?.toUpperCase();
  if (!type || type === "RENAME") return base;
  return `${base} [${type}]`;
}

function displaySourceColumn(mapping: Pick<SourceDestinationMapping, "source_column" | "transform_type" | "transform_config">): string {
  if (mapping.transform_type === "JSON_PATH" && mapping.transform_config?.path) {
    const path = String(mapping.transform_config.path);
    if (path.startsWith("[")) {
      return `${mapping.source_column}${path}`;
    }
    return `${mapping.source_column}.${path}`;
  }
  return mapping.source_column;
}

function jsonGroupRoot(columnName: string): string {
  const bracket = columnName.indexOf("[");
  if (bracket > 0) return columnName.slice(0, bracket);
  const dot = columnName.indexOf(".");
  if (dot > 0) return columnName.slice(0, dot);
  return columnName;
}

function splitVirtualSourcePath(sourceColumn: string): { root: string; path: string | null } {
  const bracket = sourceColumn.indexOf("[");
  const dot = sourceColumn.indexOf(".");
  let rootEnd = -1;
  if (bracket >= 0 && dot >= 0) rootEnd = Math.min(bracket, dot);
  else if (bracket >= 0) rootEnd = bracket;
  else if (dot > 0) rootEnd = dot;

  if (rootEnd <= 0) {
    return { root: sourceColumn, path: null };
  }

  const root = sourceColumn.slice(0, rootEnd);
  const pathStart = sourceColumn[rootEnd] === "." ? rootEnd + 1 : rootEnd;
  return { root, path: sourceColumn.slice(pathStart) || null };
}

function buildMappingPayload(sourceColumn: string, destColumn: string): Partial<SourceDestinationMapping> {
  const { root, path } = splitVirtualSourcePath(sourceColumn);
  if (path) {
    return {
      source_column: root,
      destination_column: destColumn,
      transform_type: "JSON_PATH",
      transform_config: { path },
      priority: 0,
      active: true,
    };
  }
  return {
    source_column: sourceColumn,
    destination_column: destColumn,
    transform_type: "RENAME",
    priority: 0,
    active: true,
  };
}

function nestedPathLabel(fullName: string, parent: string): string {
  if (fullName === parent) return parent;
  if (fullName.startsWith(`${parent}.`)) return fullName.slice(parent.length + 1);
  if (fullName.startsWith(`${parent}[`)) return fullName.slice(parent.length);
  return fullName;
}

function nestedParentName(col: ColumnInfo): string {
  if (col.parent) return col.parent;
  return jsonGroupRoot(col.name);
}

function groupSourceColumns(cols: ColumnInfo[]) {
  const topLevel = cols.filter((c) => !c.nested);
  const nested = cols.filter((c) => c.nested);
  const childrenByParent = new Map<string, ColumnInfo[]>();

  for (const col of nested) {
    const parent = nestedParentName(col);
    const list = childrenByParent.get(parent) || [];
    list.push(col);
    childrenByParent.set(parent, list);
  }

  const flatCols: ColumnInfo[] = [];
  const jsonGroups: { parent: ColumnInfo; children: ColumnInfo[] }[] = [];

  for (const col of topLevel) {
    const children = childrenByParent.get(col.name) || [];
    if (children.length > 0) {
      jsonGroups.push({
        parent: col,
        children: [...children].sort((a, b) => a.name.localeCompare(b.name)),
      });
    } else {
      flatCols.push(col);
    }
  }

  flatCols.sort((a, b) => a.name.localeCompare(b.name));
  jsonGroups.sort((a, b) => a.parent.name.localeCompare(b.parent.name));
  return { flatCols, jsonGroups };
}

export default function Relationship({ embedded }: { embedded?: boolean } = {}) {
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
  const [collapsedJsonGroups, setCollapsedJsonGroups] = useState<Set<string>>(new Set());

  useEffect(() => {
    getConnections(true).then(setConnections).catch(() => {});
    getDestinationTables(true).then(setDestTables).catch(() => {});
    loadAllMappings();
  }, []);

  useEffect(() => {
    if (!selectedDestId) {
      setDestCols([]);
      return;
    }
    getDestinationSchema(selectedDestId as number)
      .then((schema) => setDestCols(schema.columns))
      .catch(() => setDestCols([]));
  }, [selectedDestId]);

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

      const restoredMappings = saved.map((m: SourceDestinationMapping) => ({
        savedId: m.id,
        sourceColumn: displaySourceColumn(m),
        rawSourceColumn: m.source_column,
        destColumn: m.destination_column,
        transformType: m.transform_type,
        transformConfig: m.transform_config,
      }));
      setMappings(restoredMappings);

      // Keep JSON groups expanded by default so nested origin paths stay visible
      // after saving mappings (e.g. credits[0].type must not collapse project/service).
      setCollapsedJsonGroups(new Set());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível carregar os relacionamentos");
    } finally { setLoading(false); }
  }, [selectedConnId, selectedDestId]);

  useEffect(() => { if (selectedConnId && selectedDestId) loadAll(); }, [selectedConnId, selectedDestId, loadAll]);

  const expandJsonGroup = (columnName: string) => {
    const parent = jsonGroupRoot(columnName);
    setCollapsedJsonGroups((prev) => {
      if (!prev.has(parent)) return prev;
      const next = new Set(prev);
      next.delete(parent);
      return next;
    });
  };

  // Build lookups: destColumn → mapping; sourceColumn → all mappings (dual-map allowed)
  const mappingByDest = new Map<string, MappingState>();
  const mappingBySource = new Map<string, MappingState[]>();
  const mappedSources = new Set<string>();
  for (const m of mappings) {
    mappingByDest.set(m.destColumn, m);
    mappedSources.add(m.sourceColumn);
    const list = mappingBySource.get(m.sourceColumn) || [];
    list.push(m);
    mappingBySource.set(m.sourceColumn, list);
  }

  const destinationsForSource = (sourceColumn: string) =>
    (mappingBySource.get(sourceColumn) || []).map((m) => m.destColumn);

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

    // No-op: exact same source → dest pair already exists
    const existingSame = mappings.find(m => m.sourceColumn === dragging && m.destColumn === destColumn);
    if (existingSame) return;

    const { path } = splitVirtualSourcePath(dragging);

    // One source may map to multiple destinations; only replace the target dest slot
    const existing = mappingByDest.get(destColumn);
    let updated: MappingState[];
    const nextMapping: MappingState = {
      savedId: 0,
      sourceColumn: dragging,
      rawSourceColumn: dragging,
      destColumn,
      transformType: path ? "JSON_PATH" : "RENAME",
      transformConfig: path ? { path } : null,
    };
    if (existing) {
      updated = mappings.filter(m => m.destColumn !== destColumn);
      updated.push(nextMapping);
    } else {
      updated = [...mappings, nextMapping];
    }

    if (existing?.transformType === "CONDITIONAL") {
      setSuccess(`"${dragging}" → "${destColumn}" substituirá a regra CONDITIONAL existente. Salve para persistir (será um RENAME simples).`);
    } else {
      setSuccess(`"${dragging}" → "${destColumn}"`);
    }

    setMappings(updated);
    expandJsonGroup(dragging);
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
      if (!sc.name) continue;
      const sourceKey = sc.name.replace(/^\ufeff/, "");
      const candidates = aliases[sc.name] || aliases[sourceKey] || aliases[sourceKey.toLowerCase()] || [];
      let addedAlias = false;
      for (const target of candidates) {
        if (!destCols.some(d => d.name === target) || alreadyMappedDests.has(target)) continue;
        const conflicting = newMappings.find(m => m.destColumn === target && m.savedId === 0);
        if (conflicting && conflicting.sourceColumn !== sc.name) {
          const index = newMappings.indexOf(conflicting);
          newMappings.splice(index, 1);
          alreadyMappedSrcs.delete(conflicting.sourceColumn);
        }
        const savedConflict = newMappings.find(m => m.destColumn === target && m.savedId !== 0);
        if (savedConflict) continue;
        newMappings.push({ savedId: 0, sourceColumn: sc.name, rawSourceColumn: sc.name, destColumn: target });
        alreadyMappedDests.add(target);
        addedAlias = true;
      }
      if (addedAlias) alreadyMappedSrcs.add(sc.name);
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
        newMappings.push({ savedId: 0, sourceColumn: sc.name, rawSourceColumn: sc.name, destColumn: best });
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
        // Replace only this source→dest pair; keep other destinations for the same source.
        next = next.filter(m => !(m.sourceColumn === actual && m.destColumn === dest));
        next.push({ savedId: 0, sourceColumn: actual, rawSourceColumn: actual, destColumn: dest });
      }
      return next;
    });
    setSuccess("Mapeamento Azure → FOCUS aplicado aos campos críticos. Revise e clique em Salvar.");
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
          ...buildMappingPayload(m.rawSourceColumn, m.destColumn),
        } as Partial<SourceDestinationMapping>);
        created++;
      }

      setSuccess(`Salvo: ${created} mapeamentos criados, ${deleted} removidos.`);
      loadAll();
      loadAllMappings();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível salvar os relacionamentos");
    } finally { setSaving(false); }
  };

  const hasChanges = mappings.some(m => m.savedId === 0);

  const connName = selectedConnId ? connections.find(c => c.id === selectedConnId)?.name : "";
  const destTableName = selectedDestId ? destTables.find(t => t.id === selectedDestId)?.name : "";

  const scrollToPartitionSettings = () => {
    document.getElementById("destination-partition-settings")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const formatPartitionSummary = (spec: DestinationPartitionSpec | null | undefined): string => {
    if (!spec) return "Padrão (competência + conexão)";
    const grain = spec.grain === "snapshot_date" ? "dia + fonte" : "mês + fonte";
    const extras = (spec.extra_partitions || []).map((e) => {
      const col = e.column;
      if (e.transform === "bucket") return `${col} bucket(${e.bucket_count ?? 16})`;
      return col;
    });
    return extras.length ? `${grain} · ${extras.join(", ")}` : grain;
  };

  const selectedConnection = selectedConnId
    ? connections.find((c) => c.id === selectedConnId)
    : undefined;

  // ── Datagrid: aggregated table-level relationships ──
  const connById = new Map(connections.map(c => [c.id, c]));
  const destById = new Map(destTables.map(t => [t.id, t]));

  interface TableRelationship {
    _key: string;
    source_connection_id: number;
    destination_table_id: number;
    column_count: number;
    columns: string;
    columnDetails: string;
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
        columns: maps.map(m => {
          const src = displaySourceColumn(m);
          const suffix = m.transform_type && m.transform_type !== "RENAME" ? ` [${m.transform_type}]` : "";
          return `${src} → ${m.destination_column}${suffix}`;
        }).join(", "),
        columnDetails: maps.map(m => describeTransform(m)).join("\n\n"),
        mappingIds: maps.map(m => m.id),
      };
    });
  })();

  const handleDeleteTableRel = async (rel: TableRelationship) => {
    if (!confirm(`Excluir a relação entre "${connById.get(rel.source_connection_id)?.name}" e "${destById.get(rel.destination_table_id)?.name}"? Todos os ${rel.column_count} mapeamentos de campos serão removidos.`)) return;
    setDeletingId(rel.source_connection_id);
    try {
      for (const id of rel.mappingIds) {
        await deleteSourceDestMapping(id);
      }
      setAllMappings(prev => prev.filter(m => !rel.mappingIds.includes(m.id)));
      setSuccess(`Relação excluída (${rel.column_count} mapeamentos).`);
      loadAll();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Não foi possível excluir a relação.");
    } finally { setDeletingId(null); }
  };

  const relationshipColumns: ColumnDef<TableRelationship>[] = [
    {
      key: "source_connection_id", label: "Fonte de origem", width: 240,
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
      key: "destination_table_id", label: "Tabela de destino", width: 200,
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
      key: "partition_spec_json" as keyof TableRelationship, label: "Partição base preparada", width: 220,
      sortable: false,
      render: (r) => {
        const dest = destById.get(r.destination_table_id);
        const summary = formatPartitionSummary(dest?.partition_spec_json);
        const conn = connById.get(r.source_connection_id);
        return (
          <Box>
            <Typography variant="caption" sx={{ display: "block", fontSize: "0.68rem" }}>
              {summary}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.62rem" }}>
              replace: período + source_connection_id ({conn?.code || r.source_connection_id})
            </Typography>
          </Box>
        );
      },
    },
    {
      key: "column_count", label: "Campos", width: 80,
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
        <Tooltip
          title={
            <Box component="pre" sx={{ m: 0, fontFamily: "monospace", fontSize: "0.68rem", whiteSpace: "pre-wrap" }}>
              {r.columnDetails || r.columns}
            </Box>
          }
          arrow
        >
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
      key: "_edit", label: "", width: 50, sortable: false, filterable: false,
      render: (r) => (
        <Tooltip title="Editar mapeamentos de campos">
          <IconButton size="small" color="primary"
            onClick={() => {
              setSelectedConnId(r.source_connection_id);
              setSelectedDestId(r.destination_table_id);
              void loadAll();
            }}>
            <EditOutlined sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
      ),
    },
    {
      key: "_delete", label: "", width: 50, sortable: false, filterable: false,
      render: (r) => (
        <IconButton size="small" color="error"
          onClick={() => handleDeleteTableRel(r)}
          disabled={deletingId === r.source_connection_id}>
          {deletingId === r.source_connection_id ? <CircularProgress size={16} /> : <DeleteOutlined sx={{ fontSize: 16 }} />}
        </IconButton>
      ),
    },
  ];

  const sortedSourceCols = [...sourceCols].sort((a, b) => {
    if (!!a.nested !== !!b.nested) return a.nested ? 1 : -1;
    return a.name.localeCompare(b.name);
  });
  const { flatCols, jsonGroups } = groupSourceColumns(sourceCols);

  const toggleJsonGroup = (parentName: string) => {
    setCollapsedJsonGroups((prev) => {
      const next = new Set(prev);
      if (next.has(parentName)) next.delete(parentName);
      else next.add(parentName);
      return next;
    });
  };

  const renderSourceColumn = (
    col: ColumnInfo,
    opts?: { nested?: boolean; parent?: string; subtitle?: string },
  ) => {
    const isMapped = mappedSources.has(col.name);
    const nested = opts?.nested ?? false;
    const parent = opts?.parent;
    const pathLabel = parent ? nestedPathLabel(col.name, parent) : col.name;

    return (
      <Box
        key={col.name}
        draggable
        onDragStart={() => handleDragStart(col.name)}
        onDragEnd={handleDragEnd}
        sx={{
          display: "flex", alignItems: "center", gap: 0.5,
          px: 1, py: 0.7, mb: 0.4, borderRadius: 1,
          pl: nested ? 2.5 : 1,
          cursor: "grab",
          bgcolor: isMapped ? "#e8f5e9" : dragging === col.name ? "#EEF4FF" : nested ? "#fafafa" : "#fff",
          border: isMapped ? "1px solid #a5d6a7" : "1px solid #E1E8F0",
          opacity: dragging === col.name ? 0.7 : 1,
          userSelect: "none",
          transition: "all 0.15s",
          "&:hover": { borderColor: "#A9C8FF", bgcolor: "#f5f9ff" },
        }}
      >
        {isMapped ? (
          <Box sx={{ width: 20, display: "flex", justifyContent: "center" }}>
            <Typography variant="caption" color="success.main">✓</Typography>
          </Box>
        ) : (
          <DragIndicatorOutlined sx={{ fontSize: 16, color: "#bbb" }} />
        )}
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography
            variant="body2"
            sx={{
              fontFamily: "monospace", fontSize: "0.78rem",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
          >
            {nested && parent ? (
              <>
                <Box component="span" sx={{ color: "text.secondary" }}>{parent}.</Box>
                <Box component="span" sx={{ color: "text.primary", fontWeight: 500 }}>{pathLabel}</Box>
              </>
            ) : col.name}
          </Typography>
          {opts?.subtitle && (
            <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.62rem" }}>
              {opts.subtitle}
            </Typography>
          )}
        </Box>
        {nested && (
          <Chip label="path" size="small" variant="outlined" sx={{ height: 18, fontSize: "0.58rem" }} />
        )}
        {isMapped && (
          <Tooltip
            title={
              <Box component="pre" sx={{ m: 0, fontFamily: "monospace", fontSize: "0.68rem", whiteSpace: "pre-wrap" }}>
                {(mappingBySource.get(col.name) || [])
                  .map((m) => describeTransform({
                    source_column: m.rawSourceColumn,
                    destination_column: m.destColumn,
                    transform_type: m.transformType || "RENAME",
                    transform_config: m.transformConfig,
                  }))
                  .join("\n\n")}
              </Box>
            }
          >
            <Typography variant="caption" sx={{ fontSize: "0.6rem", color: "#4caf50", flexShrink: 0, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis" }}>
              →{destinationsForSource(col.name).join(", ")}
            </Typography>
          </Tooltip>
        )}
      </Box>
    );
  };

  return (
    <Box>
      {!embedded && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6">Relações entre fontes e tabelas</Typography>
          <Typography variant="body2" color="text.secondary">Ligue os campos de cada fonte aos campos da tabela de destino.</Typography>
        </Box>
      )}

      {/* ── Barra de seleção ── */}
      <Paper variant="outlined" sx={{ p: 2, mb: 2, display: "flex", gap: 2, flexWrap: "wrap", alignItems: "center" }}>
        <FormControl sx={{ minWidth: 220 }} size="small">
          <InputLabel>Fonte de dados</InputLabel>
          <Select
            value={selectedConnId}
            label="Fonte de dados"
            onChange={(e) => { setSelectedConnId(e.target.value as number | ""); setMappings([]); }}
          >
            {connections.map((c) => (
              <MenuItem key={c.id} value={c.id}>{c.name} ({c.code})</MenuItem>
            ))}
          </Select>
        </FormControl>

        <ArrowForwardOutlined sx={{ color: "#888", mt: 1 }} />

        <FormControl sx={{ minWidth: 200 }} size="small">
          <InputLabel>Tabela de destino</InputLabel>
          <Select
            value={selectedDestId}
            label="Tabela de destino"
            onChange={(e) => {
              const id = e.target.value as number | "";
              setSelectedDestId(id);
              setMappings([]);
              if (id) scrollToPartitionSettings();
            }}
          >
            {destTables.map((t) => (
              <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <Button variant="outlined" startIcon={<RefreshOutlined />}
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
            <Button variant="contained" startIcon={<SaveOutlined />} onClick={saveAll}
              disabled={saving || !hasChanges}>
              {saving ? "Salvando..." : "Salvar"}
            </Button>
          </>
        )}
      </Paper>

      <Box id="destination-partition-settings" sx={{ mb: 2 }}>
        {!selectedDestId ? (
          <Paper variant="outlined" sx={{ p: 2, bgcolor: "#fafbff" }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              Partição base preparada (destino)
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Escolha <strong>Fonte</strong> (ex. azure-4p) e <strong>Tabela de destino</strong> (ex. MODELO FOCUS).
              A fatia de replace usa <code>_billing_period</code> + <code>source_connection_id</code>; extras como
              <code>SubAccountName</code> aparecem abaixo.
            </Typography>
          </Paper>
        ) : (
          <DestinationPartitionSettings
            destinationTableId={selectedDestId as number}
            columnNames={destCols.map((c) => c.name)}
            sourceConnectionId={selectedConnId}
            sourceConnectionLabel={selectedConnection
              ? `${selectedConnection.name} (${selectedConnection.code})`
              : undefined}
            onSaved={() => { getDestinationTables(true).then(setDestTables).catch(() => {}); }}
          />
        )}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{dataDisplayText(error)}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess("")}>{dataDisplayText(success)}</Alert>}

      {loading && <CircularProgress sx={{ display: "block", mx: "auto", mt: 4 }} />}

      {!loading && !selectedConnId && !selectedDestId && (
        <EmptyState message="Escolha uma fonte e uma tabela de destino para criar as relações." />
      )}

      {!loading && selectedConnId && selectedDestId && sourceCols.length === 0 && (
        <EmptyState message="Nenhum campo encontrado. Execute a coleta primeiro para descobrir a estrutura da fonte." />
      )}

      {/* ── Drag & Drop area ── */}
      {!loading && sourceCols.length > 0 && (
        <Box sx={{ display: "flex", gap: 2, mt: 1 }}>
          {/* Left: Source columns */}
          <Paper variant="outlined" sx={{ width: 340, maxHeight: "calc(100vh - 280px)", overflowY: "auto", p: 1 }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5, px: 1, color: "text.secondary" }}>
              Campos da fonte ({connName})
            </Typography>
            <Typography variant="caption" sx={{ display: "block", mb: 1, px: 1, color: "text.secondary" }}>
              Um campo de origem pode mapear para vários destinos (ex.: quantity → ConsumedQuantity e PricingQuantity).
            </Typography>

            {flatCols.map((col) => renderSourceColumn(col))}

            {jsonGroups.map(({ parent, children }) => {
              const collapsed = collapsedJsonGroups.has(parent.name);
              const mappedCount = [
                parent.name,
                ...children.map((c) => c.name),
              ].filter((name) => mappedSources.has(name)).length;

              return (
                <Box key={parent.name} sx={{ mb: 0.6 }}>
                  <Box
                    onClick={() => toggleJsonGroup(parent.name)}
                    sx={{
                      display: "flex", alignItems: "center", gap: 0.5,
                      px: 1, py: 0.5, borderRadius: 1,
                      cursor: "pointer", bgcolor: "#eef2f7", border: "1px solid #cfd8dc",
                      "&:hover": { bgcolor: "#e3eaf2" },
                    }}
                  >
                    <IconButton size="small" sx={{ p: 0.25 }} aria-label={collapsed ? "Expandir" : "Recolher"}>
                      {collapsed ? <ChevronRightOutlined sx={{ fontSize: 18 }} /> : <ExpandMoreOutlined sx={{ fontSize: 18 }} />}
                    </IconButton>
                    <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.8rem", fontWeight: 600, flexGrow: 1 }}>
                      {parent.name}
                    </Typography>
                    <Chip label="JSON" size="small" color="info" variant="outlined" sx={{ height: 20, fontSize: "0.62rem" }} />
                    <Chip
                      label={`${children.length} campos`}
                      size="small"
                      variant="outlined"
                      sx={{ height: 20, fontSize: "0.62rem" }}
                    />
                    {mappedCount > 0 && (
                      <Chip label={`${mappedCount} mapeados`} size="small" color="success" sx={{ height: 20, fontSize: "0.62rem" }} />
                    )}
                  </Box>

                  <Collapse in={!collapsed}>
                    <Box sx={{ mt: 0.4, pl: 0.5, borderLeft: "2px solid #cfd8dc", ml: 1.2 }}>
                      {renderSourceColumn(parent, {
                        subtitle: "Objeto JSON completo (campo base de origem)",
                      })}
                      {children.map((col) => renderSourceColumn(col, {
                        nested: true,
                        parent: parent.name,
                        subtitle: `Caminho: ${col.name}`,
                      }))}
                    </Box>
                  </Collapse>
                </Box>
              );
            })}

            {flatCols.length === 0 && jsonGroups.length === 0 && sortedSourceCols.map((col) => renderSourceColumn(col))}
          </Paper>

          {/* Divider arrow */}
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", width: 60 }}>
            <Typography variant="h5" sx={{ color: "#bbb" }}>→</Typography>
          </Box>

          {/* Right: Destination columns as drop targets */}
          <Paper variant="outlined" sx={{ flexGrow: 1, maxHeight: "calc(100vh - 280px)", overflowY: "auto", p: 1 }}>
            <Typography variant="subtitle2" sx={{ mb: 1, px: 1, color: "text.secondary" }}>
              Campos do destino ({destTableName})
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
                        ? "#EEF4FF"
                        : isDragging
                          ? "#fafafa"
                          : "#fff",
                    border: mapping
                      ? "1px solid #a5d6a7"
                      : isDropTarget
                        ? "2px dashed #6EA8FE"
                        : "1px solid #DCE8FF",
                    transition: "all 0.15s",
                    cursor: isDragging ? "copy" : "default",
                    "&:hover": isDragging ? { borderColor: "#A9C8FF", bgcolor: "#f5f9ff" } : {},
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
                      <Tooltip
                        title={
                          <Box component="pre" sx={{ m: 0, fontFamily: "monospace", fontSize: "0.68rem", whiteSpace: "pre-wrap" }}>
                            {describeTransform({
                              source_column: mapping.rawSourceColumn,
                              destination_column: mapping.destColumn,
                              transform_type: mapping.transformType || "RENAME",
                              transform_config: mapping.transformConfig,
                            })}
                            {mapping.transformType === "CONDITIONAL" && (
                              <>
                                {"\n\n"}
                                Regra criada via seed/config (ex.: Marketplace). Arrastar substitui por RENAME simples ao salvar.
                              </>
                            )}
                          </Box>
                        }
                        arrow
                      >
                        <Chip
                          label={
                            <Box component="span" sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                              <DragIndicatorOutlined sx={{ fontSize: 12 }} />
                              <span>{transformChipLabel(mapping)}</span>
                            </Box>
                          }
                          size="small"
                          color="success"
                          variant="outlined"
                          onDelete={() => handleUnmap(dc.name)}
                          deleteIcon={<DeleteOutlined sx={{ fontSize: 14 }} />}
                          sx={{ fontSize: "0.68rem", height: 22, maxWidth: 280 }}
                        />
                      </Tooltip>
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
                Nenhum campo no destino.
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
            label={`${sourceCols.filter(c => !mappedSources.has(c.name)).length} campos fonte não mapeados`}
            size="small" variant="outlined"
          />
          {hasChanges && (
            <Chip
              icon={<UndoOutlined sx={{ fontSize: 14 }} />}
              label="Alterações não gravadas"
              color="warning" size="small"
            />
          )}
        </Box>
      )}

      {/* ── Datagrid: relações por tabela ── */}
      <Box sx={{ mt: 4 }}>
        <Typography variant="h6" sx={{ mb: 1 }}>
          Relações criadas
        </Typography>
        {mappingsLoading ? (
          <CircularProgress sx={{ display: "block", mx: "auto", mt: 2 }} />
        ) : tableRels.length === 0 ? (
          <Paper sx={{ p: 3, textAlign: "center", color: "text.secondary" }}>
            Nenhuma relação criada ainda.
          </Paper>
        ) : (
          <SortableTable
            columns={relationshipColumns}
            data={tableRels}
            getRowKey={(r) => (r as unknown as TableRelationship)._key}
            size="small"
            placeholder="Filtrar por fonte ou tabela de destino..."
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
              bgcolor: "#EEF4FF", border: "2px solid #6EA8FE",
              boxShadow: 4,
            }}
          >
            <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
              <DragIndicatorOutlined sx={{ verticalAlign: "middle", mr: 0.5, fontSize: 16 }} />
              {dragging}
            </Typography>
          </Paper>
        </Box>
      )}
    </Box>
  );
}
