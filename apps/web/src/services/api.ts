import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 120000,
});

export interface SourceConnection {
  id: number;
  code: string;
  name: string;
  connector_type: string;
  cloud_name: string | null;
  config_json: Record<string, unknown>;
  blob_prefixes: string[];
  created_at: string;
  updated_at: string;
}

export interface BlobScanItem {
  blob_path: string;
  blob_prefix: string;
  last_modified: string;
  content_length: number | null;
  status: string;
  billing_period: string | null;
}

export interface BlobScanResult {
  connection_id: number;
  total: number;
  new: number;
  changed: number;
  unchanged: number;
  items: BlobScanItem[];
}

export interface IngestedBlob {
  id: number;
  connection_id: number;
  blob_path: string;
  blob_prefix: string;
  billing_period: string | null;
  last_modified: string;
  etag: string | null;
  content_length: number | null;
  row_count: number | null;
  status: string;
  ingested_at: string;
}

export interface SkippedBlob {
  id: number;
  connection_id: number;
  blob_path: string;
  reason: string | null;
  skipped_at: string;
}

export interface EtlMapping {
  id: number;
  connection_id: number | null;
  name: string;
  source_column: string;
  target_column: string;
  transform_type: string;
  transform_config: Record<string, unknown> | null;
  priority: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface BusinessRule {
  id: number;
  connection_id: number | null;
  destination_table_id: number | null;
  name: string;
  description: string | null;
  target_column: string;
  rule_type: string;
  config_json: Record<string, unknown>;
  conditions: Record<string, unknown>[] | null;
  expression: string | null;
  priority: number;
  valid_from: string | null;
  valid_until: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClassificationRule {
  id: number;
  connection_id: number | null;
  destination_table_id: number | null;
  department_id: number | null;
  project_id: number | null;
  name: string;
  source_field: string;
  pattern: string;
  pattern_type: string;
  match_value: string;
  target_field: string;
  conditions: Record<string, unknown>[] | null;
  priority: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Department {
  id: number;
  parent_id: number | null;
  name: string;
  centro_custo: string | null;
  ambiente: string | null;
  cloud_filter: string | null;
  custom_fields: Record<string, unknown> | null;
  active: boolean;
  children: Department[];
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: number;
  department_id: number;
  projeto_name: string;
  cloud: string | null;
  subscription_rg: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface EtlRunStatus {
  connection_id: number;
  status: string;
  blobs_processed: number;
  rows_raw: number;
  rows_normalized: number;
  errors: string[];
}

// Connections
export async function getConnections(withData?: boolean): Promise<SourceConnection[]> {
  const params = withData ? { with_data: true } : {};
  const { data } = await api.get("/connections/", { params });
  return data;
}

export async function getConnection(id: number): Promise<SourceConnection> {
  const { data } = await api.get(`/connections/${id}`);
  return data;
}

export async function createConnection(body: Partial<SourceConnection>): Promise<SourceConnection> {
  const { data } = await api.post("/connections/", body);
  return data;
}

export async function updateConnection(id: number, body: Partial<SourceConnection>): Promise<SourceConnection> {
  const { data } = await api.put(`/connections/${id}`, body);
  return data;
}

export async function deleteConnection(id: number): Promise<void> {
  await api.delete(`/connections/${id}`);
}

// Scan & ETL
export async function scanBlobs(connectionId: number): Promise<BlobScanResult> {
  const { data } = await api.post(`/etl/connections/${connectionId}/scan`);
  return data;
}

export async function getIngestedBlobs(connectionId?: number): Promise<IngestedBlob[]> {
  const params = connectionId ? { connection_id: connectionId } : {};
  const { data } = await api.get("/etl/blobs/", { params });
  return data;
}

export async function runEtl(body: { connection_id: number; selected_blobs?: string[]; force_periods?: string[] }): Promise<{ task_id: number; status: string; message?: string }> {
  const { data } = await api.post("/etl/run", body);
  return data;
}

export async function resetSourceForReimport(connectionId: number): Promise<{ status: string; message: string }> {
  const { data } = await api.post(`/etl/connections/${connectionId}/reset-source`);
  return data;
}

export interface TaskStatus {
  id: number;
  type?: string; // "etl" or "reprocess"
  connection_id?: number;
  destination_table_id?: number;
  connection_ids?: number[];
  status: string;
  total_blobs: number;
  processed_blobs: number;
  rows_raw?: number;
  rows_normalized?: number;
  rows_destination: number;
  current_blob: string | null;
  current_blob_size?: number;
  download_bytes?: number;
  downloaded_bytes_total?: number;
  total_download_bytes?: number;
  active_downloads?: number;
  download_blobs?: Array<{ blob_path: string; size: number; downloaded: number; status: string }>;
  phase?: string;
  table?: string;
  error?: string;
  errors?: string[];
}

export async function getTaskStatus(taskId: number): Promise<TaskStatus> {
  const { data } = await api.get(`/etl/status/${taskId}`);
  return data;
}

export async function getRunningTasks(): Promise<TaskStatus[]> {
  const { data } = await api.get("/etl/tasks/");
  return data;
}

export async function cancelEtl(taskId: number): Promise<void> {
  await api.post(`/etl/cancel/${taskId}`);
}

export async function testConnection(connectionId: number): Promise<{ status: string; message: string }> {
  const { data } = await api.post(`/etl/connections/${connectionId}/test`);
  return data;
}

export async function processDestinationTables(connectionId: number): Promise<{ success: boolean; rows_written: number; error?: string }> {
  const { data } = await api.post(`/etl/connections/${connectionId}/process-destination`);
  return data;
}

export interface AvailablePeriod {
  period: string;
  blob_count: number;
}

export async function getAvailablePeriods(connectionId: number): Promise<AvailablePeriod[]> {
  const { data } = await api.get("/etl/periods", { params: { connection_id: connectionId } });
  return data;
}

export async function reprocessDest(body: {
  destination_table_id: number;
  connection_ids: number[];
  billing_periods?: string[];
  replace_existing?: boolean;
}): Promise<{ task_id: number; message: string }> {
  const { data } = await api.post("/etl/reprocess", body);
  return data;
}

export interface ReprocessStatus {
  id: number;
  status: string;
  phase: string;
  total_batches: number;
  completed_batches: number;
  progress: number;
  rows_written: number;
  table: string;
  connection_ids: number[];
  error: string;
}

export async function getReprocessStatus(taskId: number): Promise<ReprocessStatus> {
  const { data } = await api.get(`/etl/reprocess-status/${taskId}`);
  return data;
}

export async function cancelReprocess(taskId: number): Promise<{ message: string }> {
  const { data } = await api.post(`/etl/reprocess/cancel/${taskId}`);
  return data;
}

// Mappings
export async function getMappings(connectionId?: number): Promise<EtlMapping[]> {
  const params = connectionId !== undefined ? { connection_id: connectionId } : {};
  const { data } = await api.get("/mappings/", { params });
  return data;
}

export async function createMapping(body: Partial<EtlMapping>): Promise<EtlMapping> {
  const { data } = await api.post("/mappings/", body);
  return data;
}

export async function updateMapping(id: number, body: Partial<EtlMapping>): Promise<EtlMapping> {
  const { data } = await api.put(`/mappings/${id}`, body);
  return data;
}

export async function deleteMapping(id: number): Promise<void> {
  await api.delete(`/mappings/${id}`);
}

// Business Rules
export async function getBusinessRules(connectionId?: number, destinationTableId?: number): Promise<BusinessRule[]> {
  const params: Record<string, string | number> = {};
  if (connectionId !== undefined) params.connection_id = connectionId;
  if (destinationTableId !== undefined) params.destination_table_id = destinationTableId;
  const { data } = await api.get("/business-rules/", { params });
  return data;
}

export async function createBusinessRule(body: Partial<BusinessRule>): Promise<BusinessRule> {
  const { data } = await api.post("/business-rules/", body);
  return data;
}

export async function updateBusinessRule(id: number, body: Partial<BusinessRule>): Promise<BusinessRule> {
  const { data } = await api.put(`/business-rules/${id}`, body);
  return data;
}

export async function deleteBusinessRule(id: number): Promise<void> {
  await api.delete(`/business-rules/${id}`);
}

// Classifications
export async function getClassifications(
  connectionId?: number,
  departmentId?: number,
  destinationTableId?: number,
): Promise<ClassificationRule[]> {
  const params: Record<string, string | number> = {};
  if (connectionId !== undefined) params.connection_id = connectionId;
  if (departmentId !== undefined) params.department_id = departmentId;
  if (destinationTableId !== undefined) params.destination_table_id = destinationTableId;
  const { data } = await api.get("/classifications/", { params });
  return data;
}

export async function createClassification(body: Partial<ClassificationRule>): Promise<ClassificationRule> {
  const { data } = await api.post("/classifications/", body);
  return data;
}

export async function updateClassification(id: number, body: Partial<ClassificationRule>): Promise<ClassificationRule> {
  const { data } = await api.put(`/classifications/${id}`, body);
  return data;
}

export async function deleteClassification(id: number): Promise<void> {
  await api.delete(`/classifications/${id}`);
}

// Departments
export async function getDepartments(flat?: boolean, parentId?: number): Promise<Department[]> {
  const params: Record<string, string | number | boolean> = {};
  if (flat !== undefined) params.flat = flat;
  if (parentId !== undefined) params.parent_id = parentId;
  const { data } = await api.get("/departments/", { params });
  return data;
}

export async function createDepartment(body: Partial<Department>): Promise<Department> {
  const { data } = await api.post("/departments/", body);
  return data;
}

export async function updateDepartment(id: number, body: Partial<Department>): Promise<Department> {
  const { data } = await api.put(`/departments/${id}`, body);
  return data;
}

export async function deleteDepartment(id: number): Promise<void> {
  await api.delete(`/departments/${id}`);
}

// Projects
export async function getProjects(departmentId?: number, cloud?: string): Promise<Project[]> {
  const params: Record<string, string | number> = {};
  if (departmentId !== undefined) params.department_id = departmentId;
  if (cloud) params.cloud = cloud;
  const { data } = await api.get("/projects/", { params });
  return data;
}

export async function createProject(body: Partial<Project>): Promise<Project> {
  const { data } = await api.post("/projects/", body);
  return data;
}

export async function updateProject(id: number, body: Partial<Project>): Promise<Project> {
  const { data } = await api.put(`/projects/${id}`, body);
  return data;
}

export async function deleteProject(id: number): Promise<void> {
  await api.delete(`/projects/${id}`);
}

// CSV Import
export interface CsvImportResult {
  departments_created: number;
  projects_created: number;
  classification_rules_created: number;
  errors: string[];
}

export async function importCsvData(body?: {
  diretorias_path?: string;
  projetos_path?: string;
  business_rules_path?: string;
}): Promise<CsvImportResult> {
  const { data } = await api.post("/departments/import", body || {});
  return data;
}

export interface PeriodStatusOut {
  billing_period: string;
  connection_id: number;
  status: string;
  auto_close_days: number;
  closed_at: string | null;
  total_lines: number;
  classified_pct: number;
}

export async function getPeriods(connectionId: number): Promise<PeriodStatusOut[]> {
  const { data } = await api.get("/etl/periods/", { params: { connection_id: connectionId } });
  return data;
}

export async function closePeriod(connectionId: number, billingPeriod: string): Promise<PeriodStatusOut> {
  const { data } = await api.post("/etl/periods/close", { connection_id: connectionId, billing_period: billingPeriod });
  return data;
}

export async function reopenPeriod(connectionId: number, billingPeriod: string): Promise<PeriodStatusOut> {
  const { data } = await api.post("/etl/periods/reopen", { connection_id: connectionId, billing_period: billingPeriod });
  return data;
}

export async function autoClosePeriods(connectionId: number): Promise<string[]> {
  const { data } = await api.post("/etl/periods/auto-close", null, { params: { connection_id: connectionId } });
  return data;
}

// Delete ingested datasets
export async function deleteIngestedBlob(blobId: number): Promise<void> {
  await api.delete(`/etl/blobs/${blobId}`);
}

export async function deleteAllIngestedBlobs(connectionId: number, billingPeriod?: string): Promise<void> {
  await api.delete("/etl/blobs/", { params: { connection_id: connectionId, billing_period: billingPeriod } });
}

// Skipped Blobs (ack/ignore)
export async function getSkippedBlobs(connectionId: number): Promise<SkippedBlob[]> {
  return (await api.get("/etl/blobs/skipped", { params: { connection_id: connectionId } })).data;
}
export async function skipBlob(connectionId: number, blobPath: string, reason?: string): Promise<SkippedBlob> {
  return (await api.post("/etl/blobs/skip", { connection_id: connectionId, blob_path: blobPath, reason })).data;
}
export async function clearSkippedBlobs(connectionId: number): Promise<void> {
  await api.delete("/etl/blobs/skipped", { params: { connection_id: connectionId } });
}
export async function unskipBlob(skipId: number): Promise<void> {
  await api.delete(`/etl/blobs/skip/${skipId}`);
}

// Destination Tables
export interface DestinationTable {
  id: number;
  name: string;
  description: string | null;
  physical_name: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface DestinationColumn {
  id: number;
  destination_table_id: number;
  column_name: string;
  data_type: string;
  is_required: boolean;
  position: number;
  default_value: string | null;
  created_at: string;
}

export interface DestinationTableDetail extends DestinationTable {
  columns: DestinationColumn[];
}

export interface SourceDestinationMapping {
  id: number;
  source_connection_id: number;
  destination_table_id: number;
  name: string | null;
  source_column: string;
  destination_column: string;
  transform_type: string;
  transform_config: Record<string, unknown> | null;
  priority: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export async function getDestinationTables(activeOnly = true): Promise<DestinationTable[]> {
  const { data } = await api.get("/destinations/tables/", { params: { active_only: activeOnly } });
  return data;
}

export async function getDestinationTable(tableId: number): Promise<DestinationTableDetail> {
  const { data } = await api.get(`/destinations/tables/${tableId}`);
  return data;
}

export async function createDestinationTable(body: { name: string; description?: string }): Promise<DestinationTable> {
  const { data } = await api.post("/destinations/tables/", body);
  return data;
}

export async function getDestinationColumns(tableId: number): Promise<DestinationColumn[]> {
  const { data } = await api.get(`/destinations/tables/${tableId}/columns/`);
  return data;
}

export async function addDestinationColumn(
  tableId: number,
  body: { column_name: string; data_type?: string; is_required?: boolean }
): Promise<DestinationColumn> {
  const { data } = await api.post(`/destinations/tables/${tableId}/columns/`, body);
  return data;
}

export async function deleteDestinationColumn(columnId: number): Promise<void> {
  await api.delete(`/destinations/columns/${columnId}`);
}

export async function getSourceDestMappings(
  sourceConnectionId?: number,
  destinationTableId?: number,
): Promise<SourceDestinationMapping[]> {
  const params: Record<string, number> = {};
  if (sourceConnectionId !== undefined) params.source_connection_id = sourceConnectionId;
  if (destinationTableId !== undefined) params.destination_table_id = destinationTableId;
  const { data } = await api.get("/destinations/mappings/", { params });
  return data;
}

export async function createSourceDestMapping(
  body: Partial<SourceDestinationMapping>,
): Promise<SourceDestinationMapping> {
  const { data } = await api.post("/destinations/mappings/", body);
  return data;
}

export async function updateSourceDestMapping(
  id: number,
  body: Partial<SourceDestinationMapping>,
): Promise<SourceDestinationMapping> {
  const { data } = await api.put(`/destinations/mappings/${id}`, body);
  return data;
}

export async function deleteSourceDestMapping(id: number): Promise<void> {
  await api.delete(`/destinations/mappings/${id}`);
}

export async function getDestinationSchema(tableId: number): Promise<{
  table_id: number; table_name: string; physical_name: string;
  columns: { name: string; type: string; required: boolean }[];
}> {
  const { data } = await api.get(`/destinations/tables/${tableId}/schema`);
  return data;
}

// Fact Table — Query & Summary
export interface FactRow {
  id: number;
  connection_id: number;
  connection_name: string;
  billing_period: string;
  cost_type: string;
  date: string | null;
  subscription_name: string | null;
  resource_group: string | null;
  service_name: string | null;
  meter_category: string | null;
  resource_type: string | null;
  region: string | null;
  currency: string | null;
  quantity: number | null;
  effective_price: number | null;
  cost_usd: number | null;
  cost_brl: number | null;
  tax_base: number | null;
  tax_marketplace: number | null;
  final_cost_brl: number | null;
  diretoria: string | null;
  modelo_ai: string | null;
  blob_path: string | null;
  created_at: string | null;
}

export interface FactQueryResult {
  total: number;
  limit: number;
  offset: number;
  rows: FactRow[];
}

export interface FactSummary {
  total_rows: number;
  total_cost_usd: number;
  total_cost_brl: number;
  total_final_cost_brl: number;
  total_tax_base: number;
  available_periods: string[];
  by_diretoria: { diretoria: string; count: number; cost_brl: number }[];
  by_connection: { connection_id: number; connection_name: string; count: number; cost_brl: number }[];
}

export async function queryFact(params: {
  connection_ids?: string;
  billing_period?: string;
  cost_type?: string;
  limit?: number;
  offset?: number;
}): Promise<FactQueryResult> {
  const { data } = await api.get("/fact/query", { params });
  return data;
}

export async function summaryFact(params: { connection_ids?: string; billing_period?: string }): Promise<FactSummary> {
  const { data } = await api.get("/fact/summary", { params });
  return data;
}
export async function populateFact(connectionId: number, billingPeriod?: string): Promise<{ status: string; rows_inserted: number; connection_id: number; billing_period: string }> {
  const params = billingPeriod ? { billing_period: billingPeriod } : {};
  const { data } = await api.post(`/fact/populate/${connectionId}`, null, { params });
  return data;
}

export function getExportUrl(connectionId?: number, billingPeriod?: string): string {
  const params = new URLSearchParams();
  if (connectionId) params.set("connection_id", String(connectionId));
  if (billingPeriod) params.set("billing_period", billingPeriod);
  return `/api/fact/export?${params.toString()}`;
}

// Schema Discovery
export interface ColumnInfo {
  name: string;
  sample_values: string[];
  null_count: number;
  total_count: number;
}

export interface SchemaDiscovery {
  connection_id: number;
  connector_type: string;
  columns: ColumnInfo[];
  sample_count: number;
  message?: string;
}

export async function discoverSchema(connectionId: number): Promise<SchemaDiscovery> {
  const { data } = await api.get(`/schema/${connectionId}`);
  return data;
}

// --- Trino / Data Lakehouse Analysis ---
export interface TrinoTableList {
  bronze: string[];
  silver: string[];
  gold: string[];
}

export interface TrinoQueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

export interface TrinoSummaryRow {
  connection_code: string;
  connection_name: string;
  billing_period: string;
  total_rows: number;
  total_usd: number;
  total_brl: number;
  total_final_brl: number;
  num_diretorias: number;
  num_services: number;
}

export interface TrinoHealth {
  trino: string;
  nessie: string;
  minio: string;
}

export async function getAnalysisTables(): Promise<TrinoTableList> {
  const { data } = await api.get("/analysis/tables");
  return data;
}

export async function runAnalysisQuery(sql: string): Promise<TrinoQueryResult> {
  const { data } = await api.post("/analysis/query", { sql });
  return data;
}

export async function getAnalysisSummary(
  connectionCode?: string,
  billingPeriod?: string,
  layer: "bronze" | "silver" | "gold" = "gold",
): Promise<{ summary: TrinoSummaryRow[] }> {
  const { data } = await api.get("/analysis/summary", {
    params: { connection_code: connectionCode, billing_period: billingPeriod, layer },
  });
  return data;
}

export async function getAnalysisHealth(): Promise<TrinoHealth> {
  const { data } = await api.get("/analysis/health");
  return data;
}

// ── Source ingested data ──
export interface SourceAnalysis {
  id: number;
  name: string;
  code: string;
  connector_type: string;
  cloud_name: string | null;
  row_count: number;
}

export interface SourceDataResult {
  connection_id: number;
  connection_name: string;
  connection_code: string;
  total: number;
  limit: number;
  offset: number;
  rows: Record<string, unknown>[];
  periods: { period: string; count: number }[];
}

export async function getAnalysisSources(): Promise<SourceAnalysis[]> {
  const { data } = await api.get("/analysis/sources");
  return data;
}

export async function queryAnalysisSource(
  connectionId: number,
  options?: { limit?: number; offset?: number; billing_period?: string },
): Promise<SourceDataResult> {
  const { data } = await api.get(`/analysis/sources/${connectionId}/data`, { params: options });
  return data;
}

// ── Destination table data ──
export interface DestinationAnalysis {
  id: number;
  name: string;
  physical_name: string;
  description: string | null;
  row_count: number;
  periods: { period: string; count: number }[];
}

export interface DestinationDataResult {
  table_id: number;
  physical_name: string;
  total: number;
  limit: number;
  offset: number;
  columns: string[];
  rows: Record<string, unknown>[];
}

export async function getAnalysisDestinations(): Promise<DestinationAnalysis[]> {
  const { data } = await api.get("/analysis/destinations");
  return data;
}

export async function queryAnalysisDestination(
  tableId: number,
  options?: { limit?: number; offset?: number },
): Promise<DestinationDataResult> {
  const { data } = await api.get(`/analysis/destinations/${tableId}/data`, { params: options });
  return data;
}

// ── Server-side Pivot ──
export type PivotAggregation = "SUM" | "COUNT" | "COUNT_DISTINCT" | "AVG" | "MIN" | "MAX";
export interface PivotMeasure { column: string; aggregation: PivotAggregation; label?: string | null }
export type PivotFilterOperator = "IN" | "NOT_IN" | "CONTAINS" | "NOT_CONTAINS";
export interface PivotFilter { column: string; values: Array<string | number | boolean>; operator?: PivotFilterOperator }
export interface PivotRequest {
  destination_table_id: number; rows: string[]; columns: string[];
  measures: PivotMeasure[]; filters: PivotFilter[];
  row_limit?: number; column_limit?: number; include_totals?: boolean;
}
export interface PivotMetadata {
  destination_table_id: number;
  columns: { name: string; type: string; numeric: boolean }[];
  dimensions: string[]; measures: string[]; recommended_dimensions: string[];
  default_measure: string | null;
}
export interface PivotResult {
  rows: string[]; columns: string[]; measures: PivotMeasure[];
  row_keys: unknown[][]; column_keys: unknown[][];
  cells: { row: unknown[]; column: unknown[]; values: Array<number | null> }[];
  totals: { rows: Record<string, Array<number | null>>; columns: Record<string, Array<number | null>>; grand: Array<number | null> } | null;
  record_count: number; currency_added: boolean;
}
export interface PivotView { id: number; name: string; destination_table_id: number; config: PivotRequest; created_at?: string; updated_at?: string }
export async function getPivotMetadata(tableId: number): Promise<PivotMetadata> {
  return (await api.get(`/analysis/pivot/metadata/${tableId}`)).data;
}
export async function runPivot(body: PivotRequest): Promise<PivotResult> {
  return (await api.post("/analysis/pivot", body)).data;
}
export async function drillDownPivot(body: PivotRequest & { cell_filters: Record<string, unknown>; limit?: number; offset?: number }): Promise<{ columns: string[]; rows: Record<string, unknown>[]; total: number; limit: number; offset: number }> {
  return (await api.post("/analysis/pivot/drill-down", body)).data;
}
export async function getPivotViews(destinationTableId?: number): Promise<PivotView[]> {
  return (await api.get("/analysis/pivot/views", { params: { destination_table_id: destinationTableId } })).data;
}
export async function savePivotView(name: string, config: PivotRequest): Promise<PivotView> {
  return (await api.post("/analysis/pivot/views", { name, destination_table_id: config.destination_table_id, config })).data;
}
export async function deletePivotView(viewId: number): Promise<void> { await api.delete(`/analysis/pivot/views/${viewId}`); }
export interface LakehouseSearchResult {
  columns: string[]; rows: Record<string, unknown>[]; total: number; limit: number; offset: number;
  total_is_estimate?: boolean;
  matched_columns: string[][]; searched_columns?: string[];
}
export async function searchPivotTable(destinationTableId: number, term: string, limit = 100, offset = 0, filters: PivotFilter[] = []): Promise<LakehouseSearchResult> {
  return (await api.post("/analysis/pivot/search", { destination_table_id: destinationTableId, term, limit, offset, filters })).data;
}

// ── Scheduled Tasks ──

export interface ScheduledTask {
  id: number;
  name: string;
  connection_id: number;
  cron_expression: string;
  timezone: string;
  destination_reprocess: boolean;
  active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  config_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export async function getSchedules(): Promise<ScheduledTask[]> {
  const { data } = await api.get("/etl/schedules");
  return data;
}

export async function createSchedule(body: {
  name: string;
  connection_id: number;
  cron_expression: string;
  timezone?: string;
  destination_reprocess?: boolean;
  active?: boolean;
}): Promise<ScheduledTask> {
  const { data } = await api.post("/etl/schedules", body);
  return data;
}

export async function updateSchedule(id: number, body: Partial<ScheduledTask>): Promise<ScheduledTask> {
  const { data } = await api.put(`/etl/schedules/${id}`, body);
  return data;
}

export async function deleteSchedule(id: number): Promise<void> {
  await api.delete(`/etl/schedules/${id}`);
}

export async function runScheduleNow(id: number): Promise<{ task_id: number; connection_id: number; schedule_name: string }> {
  const { data } = await api.post(`/etl/schedules/${id}/run`);
  return data;
}

// ── Reprocess helpers ──

export interface MappedConnection {
  id: number;
  code: string;
  name: string;
  mapping_count: number;
}

export async function getConnectionsForDestination(destTableId: number): Promise<MappedConnection[]> {
  const { data } = await api.get(`/etl/connections-for-dest/${destTableId}`);
  return data;
}
