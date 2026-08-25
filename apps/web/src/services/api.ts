import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 120000,
});

export async function getAzurePtuOverview(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.post("/azure-ptu/dashboard/overview", params, { signal });
  return data;
}
export async function getAzurePtuOpsOverview(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.post("/azure-ptu/dashboard/ops-overview", params, { signal });
  return data;
}

export async function getAzurePtuExecutiveOverview(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.post("/azure-ptu/dashboard/executive-overview", params, { signal });
  return data;
}
export async function getAzurePtuView(view: "projects"|"deployments"|"subprojects"|"trends"|"reconciliation"|"recommendations", params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.post(`/azure-ptu/dashboard/${view}`, params, { signal }); return data;
}

// ── Reservas Azure (RI + Savings Plans) ────────────────────────────────
export async function getReservationSources(signal?: AbortSignal) {
  const { data } = await api.get("/reservations/sources", { signal });
  return data;
}
export async function testReservationConnection(connectionId: number) {
  const { data } = await api.post(`/reservations/connections/${connectionId}/test`);
  return data;
}
export async function collectReservations(connectionIds: number[] = []) {
  const { data } = await api.post("/reservations/collect", { connection_ids: connectionIds });
  return data;
}
export async function getReservationsDashboard(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.get("/reservations/dashboard", { params, signal });
  return data;
}
export async function getReservationsCatalog(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.get("/reservations/catalog", { params, signal });
  return data;
}
export async function getReservationsUtilization(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.get("/reservations/utilization", { params, signal });
  return data;
}
export async function getReservationsAdvisor(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.get("/reservations/advisor", { params, signal });
  return data;
}
export async function getReservationsRecommendations(params: Record<string, unknown> = {}, signal?: AbortSignal) {
  const { data } = await api.get("/reservations/recommendations", { params, signal });
  return data;
}

const CANCELLABLE_ANALYSIS = /^\/(analysis\/(expected-view|pivot|query)|ai-dashboards\/|azure-ptu\/dashboard|reservations\/dashboard)/;
const pendingAnalysis = new Set<AbortController>();
type ExpectedViewInflight = { count: number; controller: AbortController; promise: Promise<unknown>; abortTimer?: number };
const expectedViewInflight = new Map<string, ExpectedViewInflight>();

export function trackAnalysisAbort(controller: AbortController) {
  pendingAnalysis.add(controller);
  controller.signal.addEventListener("abort", () => pendingAnalysis.delete(controller), { once: true });
}

/** Abort in-flight Trino/analysis HTTP requests (e.g. when leaving a page). */
export function cancelPendingAnalysisRequests() {
  for (const controller of [...pendingAnalysis]) {
    controller.abort();
  }
  pendingAnalysis.clear();
  for (const entry of expectedViewInflight.values()) {
    if (entry.abortTimer) window.clearTimeout(entry.abortTimer);
    if (!entry.controller.signal.aborted) entry.controller.abort();
  }
  expectedViewInflight.clear();
}

api.interceptors.request.use((config) => {
  const url = config.url || "";
  if (config.method?.toLowerCase() !== "post" || !CANCELLABLE_ANALYSIS.test(url) || config.signal) {
    return config;
  }
  const controller = new AbortController();
  trackAnalysisAbort(controller);
  config.signal = controller.signal;
  return config;
});

/** Connector types that support blob/month scan in Extracao & Monitoramento. */
export const SCANNABLE_CONNECTOR_TYPES = new Set(["azure_blob", "azure_openai_ptu", "gcp_bigquery", "oci", "mongodb_atlas", "github_license", "github_token", "notebooklm_license", "notebooklm_token", "claude_license", "claude_token"]);

export function formatApiError(error: unknown, fallback = "Request failed"): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : JSON.stringify(item))).join("; ");
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return fallback;
}

export interface SourceConnection {
  id: number;
  code: string;
  name: string;
  connector_type: string;
  cloud_name: string | null;
  cloud_scope: string | null;
  config_json: Record<string, unknown>;
  blob_prefixes: string[];
  last_test_status: string | null;
  last_test_message: string | null;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BlobScanItem {
  blob_path: string;
  blob_prefix: string;
  last_modified: string;
  content_length: number | null;
  row_count?: number | null;
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
  cloud_scope: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClassificationRule {
  id: number;
  connection_id: number | null;
  destination_table_id: number | null;
  department_id: number | null;
  department_name?: string | null;
  name: string;
  source_field: string;
  pattern: string;
  pattern_type: string;
  match_value: string;
  target_field: string;
  conditions: Record<string, unknown>[] | null;
  priority: number;
  cloud_scope: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Department {
  id: number;
  parent_id: number | null;
  name: string;
  parent_name?: string | null;
  shared_cost?: boolean;
  centro_custo: string | null;
  ambiente: string | null;
  cloud_filter: string | null;
  custom_fields: Record<string, unknown> | null;
  active: boolean;
  children: Department[];
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

export interface ConnectionsGridResponse {
  items: SourceConnection[];
  total: number;
}

export async function getConnectionsGrid(params: {
  limit?: number;
  offset?: number;
  search?: string;
  provider?: string;
  category?: "provedor" | "ferramenta" | "marketplace";
  sort_field?: string;
  sort_direction?: "asc" | "desc";
  filter_field?: string;
  filter_operator?: "equals" | "notEquals";
  filter_value?: string;
  filters?: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
}): Promise<ConnectionsGridResponse> {
  const { filters, ...query } = params;
  const { data } = await api.get("/connections/grid", { params: { ...query, ...(filters?.length ? { filters: JSON.stringify(filters) } : {}) } });
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

export async function downloadIngestedBlob(blobId: number, format: "csv" | "xlsx" = "csv"): Promise<void> {
  const response = await api.get(`/etl/blobs/${blobId}/download`, {
    params: { format },
    responseType: "blob",
  });
  const disposition = response.headers["content-disposition"] || "";
  let filename = `origem-${blobId}.${format}`;
  const match = disposition.match(/filename=["']?([^"';]+)["']?/i);
  if (match && match[1]) {
    filename = match[1];
  }
  const blob = new Blob([response.data], {
    type: format === "xlsx"
      ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      : "text/csv;charset=utf-8;",
  });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}


export async function runEtl(body: {
  connection_id: number;
  selected_blobs?: string[];
  force_periods?: string[];
  backfill?: boolean;
  backfill_days?: number;
  lookback_hours?: number;
}): Promise<{ task_id: number; status: string; message?: string }> {
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
  task_type?: string;
  connection_id?: number;
  connector_type?: string;
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

export interface WorkerHealth { status: "ok" | "stale" | "offline"; heartbeat_age_seconds: number | null }
export async function getWorkerHealth(): Promise<WorkerHealth> {
  const { data } = await api.get("/etl/worker-health");
  return data;
}

export async function cancelEtl(taskId: number): Promise<void> {
  await api.post(`/etl/cancel/${taskId}`);
}

export async function testConnection(connectionId: number): Promise<{ status: string; message: string }> {
  const { data } = await api.post(`/etl/connections/${connectionId}/test`);
  return data;
}

export async function testConnectionConfig(body: Partial<SourceConnection>): Promise<{ status: string; message: string }> {
  const { data } = await api.post("/etl/connections/test-config", body);
  return data;
}

export async function processDestinationTables(connectionId: number): Promise<{ success: boolean; rows_written: number; error?: string }> {
  const { data } = await api.post(`/etl/connections/${connectionId}/process-destination`);
  return data;
}

export interface AvailablePeriod {
  period: string;
  blob_count: number;
  label?: string;
}

export async function getAvailablePeriods(connectionId: number): Promise<AvailablePeriod[]> {
  const { data } = await api.get("/etl/periods", { params: { connection_id: connectionId } });
  return data;
}

export async function reprocessSharedCost(): Promise<{ task_id: number; status: string; message: string }> {
  const { data } = await api.post("/etl/reprocess-shared-cost");
  return data;
}

export async function reprocessAICostRelationships(): Promise<{ task_id: number; status: string; message: string }> {
  const { data } = await api.post("/etl/reprocess-ai-cost-relationships");
  return data;
}

export async function getAICostRelationshipHistory(): Promise<Array<{ id: number; status: string; phase?: string; started_at?: string; finished_at?: string; rows_destination?: number }>> {
  const { data } = await api.get("/etl/ai-cost-relationship-history");
  return data;
}

export interface SharedCostJob {
  id: number;
  status: string;
  phase: string;
  rows_written: number;
  total_blobs?: number;
  processed_blobs?: number;
  table?: string;
  error?: string;
  errors?: string[];
  event_log?: Array<{ at: string; level: string; message: string }>;
  started_at?: string;
  finished_at?: string;
  snapshot?: Record<string, unknown> | null;
}

export async function getSharedCostHistory(): Promise<SharedCostJob[]> {
  const { data } = await api.get("/etl/shared-cost-history");
  return data;
}

export async function reprocessDest(body: {
  destination_table_id: number;
  connection_ids: number[];
  billing_periods?: string[];
  replace_existing?: boolean;
}): Promise<{ task_id: number; task_ids?: number[]; queue_group?: string; message: string }> {
  const { data } = await api.post("/etl/reprocess", body);
  return data;
}

export interface ReprocessStatus {
  id: number;
  status: string;
  phase: string;
  current_blob?: string;
  total_blobs?: number;
  processed_blobs?: number;
  total_batches: number;
  completed_batches: number;
  progress: number;
  rows_written: number;
  table: string;
  destination_table_id?: number;
  connection_ids: number[];
  error: string;
  errors?: string[];
  event_log?: Array<{ at: string; level: string; message: string; details?: Record<string, unknown> }>;
  billing_periods?: string[];
  started_at?: string;
  finished_at?: string;
}

export async function getReprocessStatus(taskId: number): Promise<ReprocessStatus> {
  const { data } = await api.get(`/etl/reprocess-status/${taskId}`);
  return data;
}
export async function getReprocessHistory(params: { limit?: number; offset?: number; search?: string; sort_field?: string; sort_direction?: "asc" | "desc"; filters?: Array<{ field: string; operator: "equals" | "notEquals"; value: string }> } = {}): Promise<{ items: ReprocessStatus[]; total: number }> {
  const { data } = await api.get("/etl/reprocess-history", { params: { ...params, filters: params.filters ? JSON.stringify(params.filters) : undefined } });
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

export interface ClassificationsGridResponse { items: ClassificationRule[]; total: number; }
export async function getClassificationsGrid(params: {
  limit?: number;
  offset?: number;
  search?: string;
  sort_field?: string;
  sort_direction?: "asc" | "desc";
  filters?: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
  destination_table_id?: number;
  cloud?: string;
} = {},
  signal?: AbortSignal,
): Promise<ClassificationsGridResponse> {
  const { data } = await api.get("/classifications/grid", {
    params: { ...params, filters: params.filters?.length ? JSON.stringify(params.filters) : undefined },
    signal,
  });
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

export interface ClassificationFieldValue { value: string; count: number }

export async function getClassificationFieldValues(params: {
  destination_table_id: number;
  field: string;
  cloud?: string;
  search?: string;
  limit?: number;
}): Promise<ClassificationFieldValue[]> {
  const { data } = await api.get("/classifications/field-values", { params });
  return data;
}

export async function refreshClassificationFieldValues(destination_table_id?: number): Promise<{ status: string; destination_table_id?: number | null }> {
  const { data } = await api.post("/classifications/refresh-field-values", null, {
    params: destination_table_id ? { destination_table_id } : {},
  });
  return data;
}

// Azure Tag Policy
export interface AzureTenant {
  connection_id: number;
  code: string;
  name: string;
  tenant_id: string;
}

export interface AzureTenantPolicy {
  tenant_id: string | null;
  connection_code: string;
  connection_name: string;
  rule_count: number;
  layers: Record<string, unknown>;
}

export interface AzureTagPolicyExport {
  tenants: AzureTenantPolicy[];
  deployment_script: string;
}

export async function getAzureTenants(): Promise<AzureTenant[]> {
  const { data } = await api.get("/azure-tag-policy/tenants");
  return data;
}

export async function exportAzureTagPolicy(
  connectionId?: number,
  fmt: "json" | "script" | "both" = "both",
): Promise<AzureTagPolicyExport> {
  const params: Record<string, string | number> = { fmt };
  if (connectionId !== undefined) params.connection_id = connectionId;
  const { data } = await api.get("/azure-tag-policy/export", { params });
  return data;
}

export function downloadText(filename: string, content: string, mime = "application/json"): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
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

export type SharedCostMode = "consumption" | "equal" | "custom";
export interface SharedCostTarget {
  department_id: number;
  percent: number | null;
  name?: string | null;
  path?: string | null;
}
export interface SharedCostConfig {
  department_id: number;
  enabled: boolean;
  mode: SharedCostMode;
  name?: string | null;
  path?: string | null;
  targets: SharedCostTarget[];
}
export async function getSharedCosts(): Promise<SharedCostConfig[]> {
  const { data } = await api.get("/departments/shared-costs");
  return data;
}
export async function getDepartmentSharedCost(id: number): Promise<SharedCostConfig> {
  const { data } = await api.get(`/departments/${id}/shared-cost`);
  return data;
}
export async function upsertDepartmentSharedCost(id: number, body: { enabled: boolean; mode: SharedCostMode; targets: Array<{ department_id: number; percent?: number | null }> }): Promise<SharedCostConfig> {
  const { data } = await api.put(`/departments/${id}/shared-cost`, body);
  return data;
}

export interface CloudOrganizationBootstrapResult {
  message: string;
  moved_directories: Array<{ id: number; name: string; old_path: string[]; new_path: string[] }>;
  planning_entries_updated: number;
  reprocess_required: boolean;
}
export async function bootstrapCloudOrganization(): Promise<CloudOrganizationBootstrapResult> {
  const { data } = await api.post("/departments/bootstrap-cloud-organization");
  return data;
}

export interface GridParams {
  limit?: number;
  offset?: number;
  search?: string;
  sort_field?: string;
  sort_direction?: "asc" | "desc";
  filters?: Array<{ field: string; operator: "equals" | "notEquals"; value: string }>;
}
export async function getDepartmentsGrid(params: GridParams = {}): Promise<{ items: Department[]; total: number }> {
  const { data } = await api.get("/departments/grid", { params: { ...params, filters: params.filters?.length ? JSON.stringify(params.filters) : undefined } });
  return data;
}
// CSV Import
export interface CsvImportResult {
  departments_created: number;
  classification_rules_created: number;
  errors: string[];
}

export async function importCsvData(body?: {
  diretorias_path?: string;
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

// ── Savings actions ──
export type SavingStatus = "EM_FILA" | "EM_ANALISE" | "FINALIZADA" | "ERRO" | "CANCELADA";
export interface SavingFilter { column: string; operator: "IN" | "NOT_IN" | "CONTAINS" | "NOT_CONTAINS"; values: Array<string | number> }
export interface SavingAction { id: number; name: string; description?: string | null; category?: string | null; owner?: string | null; destination_table_id: number; date_column: string; cost_column: string; currency_column?: string | null; filters: SavingFilter[]; start_date: string; duration_days: number; baseline_monthly: number; projected_monthly: number; status: SavingStatus; outcome?: string | null; final_realized?: number | null; final_projected?: number | null; error_message?: string | null; completed_at?: string | null; }
export interface SavingMeasurement { id: number; action_id: number; measured_date: string; actual_cost: number; baseline_value: number; projected_saving: number; realized_saving: number }
export interface SavingsDashboard { historic: Record<string, number>; year: Record<string, number>; monthly: Array<{month:string;projected:number;realized:number}>; status: Record<string,number>; top_savings: SavingAction[]; top_growth: SavingAction[] }
export async function getSavingActions(): Promise<SavingAction[]> { return (await api.get("/savings/actions")).data; }
export async function createSavingAction(body: Omit<SavingAction, "id"|"status"|"outcome"|"final_realized"|"final_projected"|"error_message"|"completed_at">): Promise<SavingAction> { return (await api.post("/savings/actions", body)).data; }
export async function updateSavingAction(id:number, body: Record<string, unknown>): Promise<SavingAction> { return (await api.put(`/savings/actions/${id}`, body)).data; }
export async function cancelSavingAction(id:number): Promise<SavingAction> { return (await api.post(`/savings/actions/${id}/cancel`)).data; }
export async function refreshSavingAction(id:number): Promise<SavingAction> { return (await api.post(`/savings/actions/${id}/refresh`)).data; }
export async function getSavingMeasurements(id:number): Promise<SavingMeasurement[]> { return (await api.get(`/savings/actions/${id}/measurements`)).data; }
export async function getSavingsDashboard(year?:number): Promise<SavingsDashboard> { return (await api.get("/savings/dashboard", {params:{year}})).data; }
export async function getSavingsMetadata(tableId:number): Promise<{columns:Array<{name:string;type:string}>}> { return (await api.get(`/savings/metadata/${tableId}`)).data; }

export interface ForecastMetadata { destination_table_id: number; destination_table: string; suggestion_profile?: string | null; columns: { name: string; type: string; numeric: boolean; temporal: boolean }[]; measures: string[]; date_columns: string[]; period_columns: string[]; dimensions: string[]; recommended_dimensions: string[]; default_measure: string | null; default_date: string | null; default_period: string | null; default_period_allocation?: string | null; default_charge_end_column?: string | null; }
export interface ConnectionAllocationRule { connection_id?: number | null; connection_code?: string | null; period_allocation?: "charge_interval"; charge_end_column?: string | null; }
export interface ForecastConfig { id: number; name: string; table_name?: string | null; destination_table_id: number; measure_column: string; currency_column: string | null; date_column: string; period_column: string | null; type_column: string | null; dimensions: string[]; cutoff_days: number; horizon_months: number; growth_rate: number; model_mode: string; enabled_windows: number[]; period_allocation?: string; charge_end_column?: string | null; connection_allocation_rules?: ConnectionAllocationRule[]; version: number; active: boolean; updated_at?: string; }
export interface ForecastRun { id: number; forecast_config_id: number; config_name?: string | null; status: string; phase: string | null; rows_written: number; total_series?: number; processed_series?: number; result_table: string | null; error: string | null; started_at?: string; finished_at?: string | null; }
export interface ForecastHistoryMetadata { run_id: number; result_table: string; cutoff_date: string; snapshot: Record<string, unknown>; columns: { name: string; type: string }[]; dimensions: string[]; default_rows: string[]; default_columns: string[]; types: string[]; }
export interface ForecastHistoryPivot { run_id: number; comparison: boolean; rows: string[]; columns: string[]; records: Record<string, unknown>[]; wape?: number | null; compared_at?: string; }
export interface ForecastIdentificationClause { column: string; values: string[]; }
export interface ForecastBusinessRule { id: number; forecast_config_id: number; name: string; identification_column: string; identification_values: string[]; identification_clauses?: ForecastIdentificationClause[]; charge_type: string; behavior: string; reference_column?: string | null; reference_values: string[]; value_column?: string | null; start_period: string; end_period?: string | null; growth_rate: number; replacement_value?: number | null; cancel_on_refund: boolean; scope: Record<string, string>; priority: number; active: boolean; version: number; }
export async function getForecastMetadata(tableId: number): Promise<ForecastMetadata> { const { data } = await api.get(`/forecast/metadata/${tableId}`); return data; }
export async function getForecastConfigs(): Promise<ForecastConfig[]> { const { data } = await api.get("/forecast/configs"); return data; }
export async function createForecastConfig(body: Record<string, unknown>): Promise<ForecastConfig> { const { data } = await api.post("/forecast/configs", body); return data; }
export async function updateForecastConfig(id: number, body: Record<string, unknown>): Promise<ForecastConfig> { const { data } = await api.put(`/forecast/configs/${id}`, body); return data; }
export async function deleteForecastConfig(id: number): Promise<void> { await api.delete(`/forecast/configs/${id}`); }
export async function validateForecastConfig(id: number): Promise<{ valid: boolean; errors: string[]; metadata: ForecastMetadata }> { const { data } = await api.post(`/forecast/configs/${id}/validate`); return data; }
export async function createForecastRun(configId: number): Promise<{ run_id: number; task_id: number; status: string }> { const { data } = await api.post(`/forecast/runs?config_id=${configId}`); return data; }
export async function getForecastRuns(): Promise<ForecastRun[]> { const { data } = await api.get("/forecast/runs"); return data; }
export interface ForecastRunsGridResponse { items: ForecastRun[]; total: number; }
export async function getForecastRunsGrid(params: { limit?: number; offset?: number; search?: string; sort_field?: string; sort_direction?: "asc" | "desc"; filters?: Array<{ field: string; operator: "equals" | "notEquals"; value: string }> } = {}): Promise<ForecastRunsGridResponse> {
  const { data } = await api.get("/forecast/runs/grid", { params: { ...params, filters: params.filters?.length ? JSON.stringify(params.filters) : undefined } });
  return data;
}
export async function cancelForecastRun(runId: number): Promise<{ run_id: number; status: string }> {
  const { data } = await api.post(`/forecast/runs/${runId}/cancel`);
  return data;
}
export async function getForecastHistoryMetadata(runId: number): Promise<ForecastHistoryMetadata> { const { data } = await api.get(`/forecast/runs/${runId}/history/metadata`); return data; }
export type HistoryFilter = string[] | { operator: string; value: string | string[] };
export type HistorySort = { field: string; direction: "asc" | "desc" };
export async function runForecastHistoryPivot(runId: number, body: { rows: string[]; columns: string[]; filters?: Record<string, HistoryFilter>; row_limit?: number; offset?: number; search?: string; sort_field?: string; sort_direction?: "asc" | "desc" }): Promise<ForecastHistoryPivot> { const { data } = await api.post(`/forecast/runs/${runId}/history/pivot`, body); return data; }
export async function compareForecastHistory(runId: number, body: { rows: string[]; columns: string[]; filters?: Record<string, HistoryFilter>; row_limit?: number; offset?: number; search?: string; sort_field?: string; sort_direction?: "asc" | "desc" }): Promise<ForecastHistoryPivot> { const { data } = await api.post(`/forecast/runs/${runId}/history/comparison`, body); return data; }
export async function getForecastBusinessRules(configId: number): Promise<ForecastBusinessRule[]> { const { data } = await api.get(`/forecast/configs/${configId}/business-rules`); return data; }

// ── Acurácia de Forecast ──────────────────────────────────────────────────
export interface ForecastAccuracyMonth {
  run_id: number; config_id: number; simulated_as_of: string; target_month: string;
  cutoff_date: string; result_table: string; started_at?: string; closed: boolean;
  forecast_value?: number | null; actual_value?: number | null; wape?: number | null;
  accuracy?: number | null; variance?: number | null; variance_pct?: number | null; bias?: number | null;
}
export interface ForecastAccuracySummary {
  run_id: number; config_id: number; simulated_as_of: string; target_month: string;
  cutoff_date: string; result_table: string; closed: boolean; compared_at?: string;
  rows_forecast?: number; forecast_value?: number | null; actual_value?: number | null;
  variance?: number | null; variance_pct?: number | null; wape?: number | null;
  accuracy?: number | null; bias?: number | null;
}
export interface ForecastAccuracyDimensionRow {
  dimension_value: string; forecast_value?: number | null; actual_value?: number | null;
  variance?: number | null; variance_pct?: number | null; wape?: number | null; accuracy?: number | null; bias?: number | null;
}
export interface ForecastAccuracyDimensions { run_id: number; target_month: string; dimension: string; simulated_as_of: string; records: ForecastAccuracyDimensionRow[]; }
export interface ForecastAccuracyHistory { items: ForecastAccuracySummary[]; }
export async function getForecastAccuracyMonths(configId?: number, department?: string, mode?: string): Promise<ForecastAccuracyMonth[]> { const { data } = await api.get("/forecast/accuracy/months", { params: { config_id: configId, department, mode } }); return data; }
export async function getForecastAccuracySummary(runId: number, targetMonth?: string, department?: string, mode?: string): Promise<ForecastAccuracySummary> { const { data } = await api.get("/forecast/accuracy/summary", { params: { run_id: runId, target_month: targetMonth, department, mode } }); return data; }
export async function getForecastAccuracyDimensions(runId: number, targetMonth?: string, dimension?: string, department?: string, mode?: string): Promise<ForecastAccuracyDimensions> { const { data } = await api.get("/forecast/accuracy/dimensions", { params: { run_id: runId, target_month: targetMonth, dimension, department, mode } }); return data; }
export async function getForecastAccuracyHistory(configId?: number): Promise<ForecastAccuracyHistory> { const { data } = await api.get("/forecast/accuracy/history", { params: configId ? { config_id: configId } : {} }); return data; }
export interface ForecastAccuracyDeltaRow {
  name: string; subcategory?: string; path?: string[]; forecast_value?: number | null; actual_value?: number | null;
  variance?: number | null; variance_pct?: number | null; wape?: number | null;
  accuracy?: number | null; bias?: number | null;
}
export interface ForecastAccuracyDelta {
  run_id: number; target_month: string; mode: string; department: string;
  level: number; level_label: string; dimension: string; leaf?: boolean; records: ForecastAccuracyDeltaRow[];
}
export async function getForecastAccuracyDelta(runId: number, targetMonth?: string, mode?: string, department?: string): Promise<ForecastAccuracyDelta> {
  const { data } = await api.get("/forecast/accuracy/delta", { params: { run_id: runId, target_month: targetMonth, mode, department } });
  return data;
}
export interface ForecastAccuracyDeltaSeriesPoint {
  day: number; actual: number; actual_cum: number; forecast_rate: number; forecast_cum: number;
}
export interface ForecastAccuracyDeltaSeries {
  run_id: number; target_month: string; department: string; subcategory: string;
  days_in_month: number; forecast_total: number; actual_total: number;
  variance: number; variance_pct?: number | null; accuracy?: number | null;
  deviation_day: number | null; daily_deviation_day?: number | null; points: ForecastAccuracyDeltaSeriesPoint[];
}
export async function getForecastAccuracyDeltaSeries(runId: number, targetMonth?: string, department?: string, subcategory?: string): Promise<ForecastAccuracyDeltaSeries> {
  const { data } = await api.get("/forecast/accuracy/delta/series", { params: { run_id: runId, target_month: targetMonth, department, subcategory }, timeout: 60000 });
  return data;
}
export interface ForecastDesvioItem {
  path: string[]; name: string; forecast_value: number; actual_value: number;
  variance: number; variance_pct?: number | null; accuracy?: number | null; wape?: number | null; bias?: number | null;
}
export interface ForecastDesvios { run_id: number; target_month: string; department: string; positive: ForecastDesvioItem[]; negative: ForecastDesvioItem[]; }
export interface ForecastDesvioSubcat { subcategory: string; forecast_value: number; actual_value: number; variance: number; variance_pct?: number | null; accuracy?: number | null; }
export interface ForecastAck {
  id: number; run_id: number | null; target_month: string; department_path: string[]; department_name: string;
  forecast_value: number; actual_value: number; variance: number; accuracy?: number | null;
  category: string; reason: string; created_at: string;
  history?: Array<{ id: number; category: string; reason: string; changed_at: string }>;
}
export async function getForecastAccuracyDesvios(runId: number, targetMonth?: string, department?: string): Promise<ForecastDesvios> {
  const { data } = await api.get("/forecast/accuracy/desvios", { params: { run_id: runId, target_month: targetMonth, department }, timeout: 60000 });
  return data;
}
export async function getForecastAccuracyDesviosSubcategories(runId: number, targetMonth?: string, department?: string, scope?: string): Promise<{ records: ForecastDesvioSubcat[] }> {
  const { data } = await api.get("/forecast/accuracy/desvios/subcategories", { params: { run_id: runId, target_month: targetMonth, department, scope }, timeout: 45000 });
  return data;
}
export async function createForecastAck(body: { run_id: number; target_month: string; department: string; category: string; reason: string }): Promise<ForecastAck> {
  const { data } = await api.post("/forecast/accuracy/acks", body);
  return data;
}
export async function updateForecastAck(ackId: number, body: { category: string; reason: string }): Promise<ForecastAck> {
  const { data } = await api.put(`/forecast/accuracy/acks/${ackId}`, body);
  return data;
}
export async function getForecastAcks(targetMonth?: string, department?: string): Promise<{ items: ForecastAck[] }> {
  const { data } = await api.get("/forecast/accuracy/acks", { params: { target_month: targetMonth, department } });
  return data;
}
export async function deleteForecastAck(ackId: number): Promise<void> { await api.delete(`/forecast/accuracy/acks/${ackId}`); }
export async function createSimulatedForecastRun(configId: number, simulatedAsOf: string): Promise<{ run_id: number; task_id: number; status: string }> { const { data } = await api.post(`/forecast/runs?config_id=${configId}&simulated_as_of=${simulatedAsOf}`); return data; }
export interface ForecastAccuracyReprocessResult { task_id: number; status: string; months: string[]; }
export async function getForecastAccuracyAvailableMonths(configId?: number): Promise<string[]> {
  const { data } = await api.get("/forecast/accuracy/available-months", { params: configId ? { config_id: configId } : {} });
  return data.months;
}
export async function reprocessForecastAccuracy(configId: number, months: string[]): Promise<ForecastAccuracyReprocessResult> {
  const { data } = await api.post("/forecast/accuracy/reprocess", { config_id: configId, months });
  return data;
}
export type ForecastGridParams = { limit?: number; offset?: number; search?: string; sort_field?: string; sort_direction?: "asc" | "desc"; filters?: Array<{ field: string; operator: "equals" | "notEquals"; value: string }> };
export interface ForecastConfigsGridResponse { items: ForecastConfig[]; total: number; }
export async function getForecastConfigsGrid(params: ForecastGridParams = {}): Promise<ForecastConfigsGridResponse> {
  const { data } = await api.get("/forecast/configs/grid", { params: { ...params, filters: params.filters?.length ? JSON.stringify(params.filters) : undefined } });
  return data;
}
export interface ForecastRulesGridResponse { items: ForecastBusinessRule[]; total: number; }
export async function getForecastBusinessRulesGrid(configId: number, params: ForecastGridParams = {}): Promise<ForecastRulesGridResponse> {
  const { data } = await api.get(`/forecast/configs/${configId}/business-rules/grid`, { params: { ...params, filters: params.filters?.length ? JSON.stringify(params.filters) : undefined } });
  return data;
}
export async function createForecastBusinessRule(configId: number, body: Record<string, unknown>): Promise<ForecastBusinessRule> { const { data } = await api.post(`/forecast/configs/${configId}/business-rules`, body); return data; }
export async function updateForecastBusinessRule(id: number, body: Record<string, unknown>): Promise<ForecastBusinessRule> { const { data } = await api.put(`/forecast/business-rules/${id}`, body); return data; }
export async function deleteForecastBusinessRule(id: number): Promise<void> { await api.delete(`/forecast/business-rules/${id}`); }

// ── Extraction History & Bronze DataGrid ──
export interface ExtractionHistoryItem {
  id: number;
  status: string;
  connection_id: number;
  connection_code: string;
  connection_name: string;
  connector_type: string;
  total_blobs: number;
  processed_blobs: number;
  rows_raw: number;
  rows_normalized: number;
  rows_destination: number;
  phase: string | null;
  errors: string[];
  started_at: string;
  finished_at: string | null;
  billing_periods: string[];
  result_json?: Record<string, unknown>;
}

export interface ExtractionHistoryMetadata {
  task_id: number;
  connection_id: number;
  connection_code: string;
  connection_name: string;
  connector_type: string;
  table_name: string;
  columns: string[];
  total_rows: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  phase: string | null;
}

export interface ExtractionHistoryQueryResponse {
  task_id: number;
  connection_code: string;
  connection_name: string;
  table_name: string;
  columns: string[];
  records: Record<string, unknown>[];
  total_records: number;
  offset: number;
  limit: number;
}

export async function getExtractionHistory(connectionId?: number, limit = 50, offset = 0): Promise<ExtractionHistoryItem[]> {
  const params: Record<string, unknown> = { limit, offset };
  if (connectionId !== undefined) params.connection_id = connectionId;
  const { data } = await api.get("/etl/history", { params });
  return data;
}

export async function getExtractionHistoryMetadata(taskId: number): Promise<ExtractionHistoryMetadata> {
  const { data } = await api.get(`/etl/history/${taskId}/metadata`);
  return data;
}

export async function queryExtractionHistoryData(
  taskId: number,
  body: {
    offset?: number;
    limit?: number;
    search?: string;
    filters?: Record<string, HistoryFilter>;
    sort_field?: string;
    sort_direction?: "asc" | "desc";
  }
): Promise<ExtractionHistoryQueryResponse> {
  const { data } = await api.post(`/etl/history/${taskId}/query`, body);
  return data;
}

// Financial planning / type catalog
export interface FinancialType { id: number; code: string; name: string; active: boolean; created_at: string; updated_at: string; }
export interface PlanningVersion { id: number; financial_type_id: number; year: number; version: number; is_current: boolean; source: string; source_filename?: string | null; created_at: string; }
export interface PlanningRow { entity_type: "department"; department_id: number; department_levels: string[]; values: number[]; }
export interface PlanningGrid { type: FinancialType; version: PlanningVersion | null; rows: PlanningRow[]; }
export async function getFinancialTypes(): Promise<FinancialType[]> { return (await api.get("/planning/types")).data; }
export async function getPlanningGrid(type: string, year: number, versionId?: number): Promise<PlanningGrid> { return (await api.get(`/planning/${type}/${year}`, { params: versionId ? { version_id: versionId } : {} })).data; }
export async function savePlanningGrid(type: string, year: number, rows: PlanningRow[]): Promise<{ version: PlanningVersion; rows: number }> { return (await api.post(`/planning/${type}/${year}`, { rows })).data; }
export async function getPlanningVersions(type: string, year: number): Promise<PlanningVersion[]> { return (await api.get(`/planning/${type}/${year}/versions`)).data; }
export async function restorePlanningVersion(type: string, year: number, id: number): Promise<{ version: PlanningVersion }> { return (await api.post(`/planning/${type}/${year}/versions/${id}/restore`)).data; }
export async function importPlanningExcel(type: string, year: number, file: File): Promise<{ version: PlanningVersion; rows: number }> { const data = new FormData(); data.append("file", file); return (await api.post(`/planning/${type}/${year}/import`, data)).data; }
export async function downloadPlanningTemplate(year: number): Promise<Blob> { return (await api.get("/planning/template", { params: { year }, responseType: "blob" })).data; }
export async function downloadSimulatedBudget(year: number, basePeriod = `${year}01`, detailed = false, detailColumn?: string, cloud?: string): Promise<Blob> { return (await api.get(`/planning/orcamento/${year}/simulado`, { params: { base_period: basePeriod, detailed, detail_column: detailColumn, cloud: cloud || undefined }, responseType: "blob" })).data; }

export interface CloneRule { id: number; destination_table_id: number; connection_id?: number | null; name: string; description?: string | null; provider?: string | null; conditions: { field: string; op: string; value?: string | number | string[] }[]; roles: Record<string, string>; original_changes: Record<string, unknown>; clone_changes: Record<string, unknown>; valid_from?: string | null; valid_until?: string | null; priority: number; active: boolean; version: number; }
export async function getCloneRules(destinationTableId?: number): Promise<CloneRule[]> { return (await api.get("/clone-rules", { params: destinationTableId ? { destination_table_id: destinationTableId } : {} })).data; }
export async function createCloneRule(body: Record<string, unknown>): Promise<CloneRule> { return (await api.post("/clone-rules", body)).data; }
export async function updateCloneRule(id: number, body: Record<string, unknown>): Promise<CloneRule> { return (await api.put(`/clone-rules/${id}`, body)).data; }
export async function deleteCloneRule(id: number): Promise<void> { await api.delete(`/clone-rules/${id}`); }
export async function getClonePendencies(destinationTableId?: number): Promise<Record<string, unknown>[]> { return (await api.get("/clone-rules/price-pendencies", { params: destinationTableId ? { destination_table_id: destinationTableId } : {} })).data; }

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
  nested?: boolean;
  parent?: string | null;
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
export interface PivotSort { column: string; direction: "asc" | "desc" }
export interface PivotRequest {
  destination_table_id: number; rows: string[]; columns: string[];
  measures: PivotMeasure[]; filters: PivotFilter[];
  row_limit?: number; column_limit?: number; include_totals?: boolean;
  sort_rows?: PivotSort[]; sort_columns?: PivotSort[];
}
export interface PivotMetadata {
  destination_table_id: number;
  columns: { name: string; type: string; numeric: boolean }[];
  dimensions: string[]; measures: string[]; recommended_dimensions: string[];
  default_measure: string | null;
}
export interface PivotUnionTable { id: number; physical_name: string; origin: string }
export interface PivotResult {
  rows: string[]; columns: string[]; measures: PivotMeasure[];
  row_keys: unknown[][]; column_keys: unknown[][];
  cells: { row: unknown[]; column: unknown[]; values: Array<number | null> }[];
  totals: { rows: Record<string, Array<number | null>>; columns: Record<string, Array<number | null>>; grand: Array<number | null> } | null;
  record_count: number; currency_added: boolean;
  period_auto_applied?: string | null;
  origin_column?: string;
  origin_values?: string[];
  tables?: PivotUnionTable[];
}
export interface PivotView { id: number; name: string; destination_table_id: number; config: PivotRequest & { perspective_config?: unknown }; created_at?: string; updated_at?: string }
export async function getPivotMetadata(tableId: number): Promise<PivotMetadata> {
  return (await api.get(`/analysis/pivot/metadata/${tableId}`)).data;
}
export async function runPivot(body: PivotRequest): Promise<PivotResult> {
  return (await api.post("/analysis/pivot", body)).data;
}

// ── Server-side Pivot — union of two (or N) tables (realizado + forecast) ──
export interface PivotUnionRequest {
  destination_table_ids: number[]; rows: string[]; columns: string[];
  measures: PivotMeasure[]; filters: PivotFilter[];
  row_limit?: number; column_limit?: number; include_totals?: boolean;
  origin_column?: string;
  sort_rows?: PivotSort[]; sort_columns?: PivotSort[];
}
export interface PivotUnionResult extends PivotResult {
  origin_column: string;
  origin_values: string[];
  tables: PivotUnionTable[];
}
export async function runPivotUnion(body: PivotUnionRequest): Promise<PivotUnionResult> {
  return (await api.post("/analysis/pivot/union", body)).data;
}
export async function drillDownPivot(body: PivotRequest & { cell_filters: Record<string, unknown>; limit?: number; offset?: number }): Promise<{ columns: string[]; rows: Record<string, unknown>[]; total: number; limit: number; offset: number }> {
  return (await api.post("/analysis/pivot/drill-down", body)).data;
}
export interface PivotRecordRequest {
  destination_table_id: number;
  columns?: string[];
  filters?: PivotFilter[];
  limit?: number;
  offset?: number;
}
export interface PivotRecordsResult {
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}
export async function getPivotRecords(body: PivotRecordRequest): Promise<PivotRecordsResult> {
  return (await api.post("/analysis/pivot/records", body)).data;
}
export async function getPivotViews(destinationTableId?: number): Promise<PivotView[]> {
  return (await api.get("/analysis/pivot/views", { params: { destination_table_id: destinationTableId } })).data;
}
export async function savePivotView(name: string, config: PivotRequest & { perspective_config?: unknown }): Promise<PivotView> {
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


export interface ExpectedViewRow { period: string; actual_value: number | null; forecast_value: number | null; forecast_manual_value?: number | null; shared_value?: number | null; shared_received?: number | null; shared_given?: number | null; budget_value: number; expected_value: number; cumulative_value: number; }
export interface ExpectedViewDriver { name: string; budget_value: number; forecast_value: number; variance: number; actual_value?: number; forecast_manual_value?: number; shared_value?: number; }
// Pseudo-destination id for the operational Forecast manual (Planning) exposed in the pivot.
export const FORECAST_MANUAL_PIVOT_ID = -1000000;

type ExpectedViewResult = { director: string; department_id?: number; rows: ExpectedViewRow[]; drivers: ExpectedViewDriver[] };

export async function getExpectedView(director = "DIR DADOS E INTELIGENCIA ARTIFICIAL", year = new Date().getFullYear(), departmentId?: number, signal?: AbortSignal, includeSharedCost = false): Promise<ExpectedViewResult> {
  const key = director + "|" + year + "|" + (departmentId ?? "") + "|" + (includeSharedCost ? 1 : 0);
  let entry = expectedViewInflight.get(key);
  if (!entry || entry.controller.signal.aborted) {
    const controller = new AbortController();
    trackAnalysisAbort(controller);
    const promise = api.post("/analysis/expected-view", { director, year, department_id: departmentId, include_shared_cost: includeSharedCost }, { timeout: 120000, signal: controller.signal }).then(result => result.data as ExpectedViewResult).finally(() => {
      const current = expectedViewInflight.get(key);
      if (current?.controller === controller) expectedViewInflight.delete(key);
    });
    entry = { count: 0, controller, promise };
    expectedViewInflight.set(key, entry);
  }
  if (entry.abortTimer) window.clearTimeout(entry.abortTimer);
  entry.count += 1;
  const release = () => {
    entry.count -= 1;
    if (entry.count > 0) return;
    if (entry.abortTimer) window.clearTimeout(entry.abortTimer);
    const abortNow = () => {
      if (entry.count <= 0 && !entry.controller.signal.aborted) entry.controller.abort();
    };
    if (signal?.aborted) abortNow();
    else entry.abortTimer = window.setTimeout(abortNow, 50);
  };
  if (signal?.aborted) {
    release();
    throw Object.assign(new Error("canceled"), { code: "ERR_CANCELED" });
  }
  signal?.addEventListener("abort", release, { once: true });
  try {
    return await entry.promise as Promise<ExpectedViewResult>;
  } finally {
    signal?.removeEventListener("abort", release);
  }
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
  config_json?: Record<string, unknown>;
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
  connector_type?: string;
  mapping_count: number;
}

export async function getConnectionsForDestination(destTableId: number): Promise<MappedConnection[]> {
  const { data } = await api.get(`/etl/connections-for-dest/${destTableId}`);
  return data;
}

// ── Manual CSV Upload (Bronze) ──
export interface CsvUploadConnection { id: number; code: string; name: string; connector_type: string; cloud_name: string | null; }
export interface CsvColumnPreview { name: string; inferred_type: string; sample_values: unknown[]; null_count: number; total_count: number; }
export interface CsvPreview {
  row_count: number;
  column_count: number;
  columns: CsvColumnPreview[];
  sample_rows: Record<string, unknown>[];
  suggested_billing_period: string | null;
  monthly_grain?: boolean;
  current_month?: boolean;
  warning_message?: string | null;
  skip_gold_realizado?: boolean;
}
export interface CsvIngestResult {
  row_count: number;
  billing_period: string;
  blob_path: string;
  table_name: string;
  column_types: Record<string, string>;
  monthly_grain?: boolean;
  current_month?: boolean;
  warning_message?: string | null;
  skip_gold_realizado?: boolean;
}

export async function getCsvUploadConnections(): Promise<CsvUploadConnection[]> {
  const { data } = await api.get("/csv-upload/connections");
  return data;
}

export async function previewCsv(file: File): Promise<CsvPreview> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post("/csv-upload/preview", formData, {
    headers: { "Content-Type": undefined },
  });
  return data;
}

export async function ingestCsv(file: File, connectionId: number, billingPeriod: string, columnTypes: Array<{ name: string; data_type: string }>): Promise<CsvIngestResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("connection_id", String(connectionId));
  formData.append("billing_period", billingPeriod);
  formData.append("column_types_json", JSON.stringify(columnTypes));
  const { data } = await api.post("/csv-upload/ingest", formData, {
    headers: { "Content-Type": undefined },
  });
  return data;
}

export async function getCsvUploadHistory(connectionId: number): Promise<IngestedBlob[]> {
  const { data } = await api.get(`/csv-upload/history/${connectionId}`);
  return data;
}

export async function ingestCsvBatch(files: File[], connectionId: number): Promise<CsvIngestResult[]> {
  const formData = new FormData();
  files.forEach((f) => formData.append("files", f));
  formData.append("connection_id", String(connectionId));
  const { data } = await api.post("/csv-upload/ingest-batch", formData, {
    headers: { "Content-Type": undefined },
  });
  return data;
}

// ── AI Dashboards ──
export type AIMetricKind = "tokens" | "cost";

export interface AICostCoveragePct {
  token: number;
  license: number;
  overall: number;
}

export interface AIDashboardFilter {
  period?: string;
  year?: number;
  department_id?: number;
  department_path?: string;
  provider?: string;
  metric?: AIMetricKind;
}

export interface AIOverviewResult {
  metric: AIMetricKind;
  cost_coverage_pct: AICostCoveragePct;
  department_path?: string | null;
  kpis: {
    total_usage: number;
    active_licenses: number;
    utilization_rate: number;
    usage_per_active_user: number;
    estimated_waste: number;
    mom_change_pct: number;
  };
  monthly_series: Array<{ period: string; provider: string; value: number }>;
  license_utilization_by_dept: Array<{ department: string; licenses: number; active_users: number; utilization_rate: number }>;
  adoption_funnel: { assigned: number; logged_in: number; used: number; healthy: number };
  provider_bubble: Array<{ provider: string; licenses: number; usage_value: number }>;
}

export interface AILicensesResult {
  metric: AIMetricKind;
  cost_coverage_pct: AICostCoveragePct;
  summary: {
    total_licenses: number;
    never_used: number;
    inactive_30d?: number;
    inactive_60d?: number;
    inactive_90d?: number;
    total_allocated_cost: number;
    reclaim_potential: number;
    [key: string]: number | undefined;
  };
  items: Array<{
    user_email: string;
    provider: string;
    product_name: string;
    license_sku: string;
    activity_status: string;
    days_since_activity: number;
    allocated_cost: number;
    last_activity_at?: string | null;
  }>;
}

export interface AIConsumptionResult {
  metric: AIMetricKind;
  cost_coverage_pct: AICostCoveragePct;
  percentiles: { p10: number; p95: number };
  pareto: Array<{ provider: string; value: number }>;
  cost_per_million_tokens: Array<{ provider: string; model_name: string; cost_per_million: number }>;
  outliers: Array<{ user_email: string; provider: string; metric_value: number; usage_index: number; percent_rank: number }>;
  zombies: Array<{ user_email: string; provider: string; metric_value: number; usage_index: number; percent_rank: number }>;
  ranked_users: Array<{ user_email: string; provider: string; metric_value: number; total_tokens: number; usage_index: number; percent_rank: number }>;
  heatmap: Array<{ usage_date: string; user_email: string; value: number }>;
}

export interface AIUserResult {
  metric: AIMetricKind;
  cost_coverage_pct: AICostCoveragePct;
  user_email: string;
  summary: {
    total_usage: number;
    total_cost: number;
    total_tokens: number;
    dept_percent_rank: number;
    license_count: number;
  };
  licenses: Array<{
    provider: string;
    product_name: string;
    license_status: string;
    activity_status: string;
    last_activity_at?: string | null;
    days_since_activity: number;
    allocated_cost: number;
  }>;
  consumption_by_model: Array<{
    provider: string;
    model_name: string;
    usage_date: string;
    total_tokens: number;
    cost: number;
    metric_value: number;
  }>;
}

async function postAIDashboard<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const { data } = await api.post(`/ai-dashboards/${path}`, body, { signal, timeout: 120000 });
  return data;
}

export async function getAIDashboardOverview(body: AIDashboardFilter, signal?: AbortSignal): Promise<AIOverviewResult> {
  return postAIDashboard("overview", body, signal);
}

export async function getAIDashboardLicenses(
  body: AIDashboardFilter & { limit?: number; offset?: number; reclaim_only?: boolean },
  signal?: AbortSignal,
): Promise<AILicensesResult> {
  return postAIDashboard("licenses", body, signal);
}

export async function getAIDashboardConsumption(
  body: AIDashboardFilter & { limit?: number; outliers_only?: boolean; zombie_only?: boolean },
  signal?: AbortSignal,
): Promise<AIConsumptionResult> {
  return postAIDashboard("consumption", body, signal);
}

export async function getAIDashboardUser(
  body: AIDashboardFilter & { user_email: string },
  signal?: AbortSignal,
): Promise<AIUserResult> {
  return postAIDashboard("user", body, signal);
}

// ── AI Cost Origin ──
export interface AICostLinkSource {
  id?: number;
  connection_id?: number | null;
  filter_json: Array<{ column: string; operator: string; values: string[] }>;
  value_column: string;
  percent: number;
}

export interface AICostLink {
  id?: number;
  name: string;
  provider: string;
  tool_connection_ids?: number[];
  product_name: string;
  target_type?: "license_pool" | "ptu_pool" | "payg_ai";
  operational_connection_id?: number | null;
  project_key?: string | null;
  purchase_mode?: string | null;
  deployment_type?: string | null;
  cost_component?: string | null;
  allocation_level?: string | null;
  license_sku?: string | null;
  valid_from?: string | null;
  valid_until?: string | null;
  active?: boolean;
  distribution_enabled?: boolean;
  distribution_basis?: string | null;
  distribution_user_key?: "email" | "login" | null;
  unit_cost?: number | null;  // R$/licença/mês — custo unitário manual (ex.: Claude)
  materialization_table?: string | null;
  materialization_status?: "PENDING" | "UPDATED" | "EMPTY" | "FAILED" | string | null;
  materialization_rows?: number | null;
  materialized_at?: string | null;
  materialization_error?: string | null;
  sources?: AICostLinkSource[];
}

export interface AIUnlinkedLicense {
  provider: string;
  product_name: string;
  license_sku?: string | null;
  billing_period?: string;
  license_count: number;
  has_link: boolean;
}

function mapLinkSources(sources: AICostLinkSource[] = []) {
  return sources.map(source => ({
    connection_id: source.connection_id ?? null,
    filter_json: source.filter_json,
    value_column: source.value_column,
    percent: source.percent,
  }));
}

/** Normaliza o filter_json que vem do backend como {conditions:[...]} para um array plano. */
function normalizeFilterJson(raw: unknown): Array<{ column: string; operator: string; values: string[] }> {
  if (Array.isArray(raw)) return raw as Array<{ column: string; operator: string; values: string[] }>;
  if (raw && typeof raw === "object" && "conditions" in raw) {
    const conds = (raw as { conditions: unknown }).conditions;
    if (Array.isArray(conds)) return conds as Array<{ column: string; operator: string; values: string[] }>;
  }
  return [{ column: "ServiceName", operator: "CONTAINS", values: [""] }];
}

/** Normaliza os sources recebidos do backend para o formato esperado pelo frontend. */
function normalizeSources(sources: AICostLinkSource[] = []): AICostLinkSource[] {
  return sources.map(s => ({
    ...s,
    filter_json: normalizeFilterJson(s.filter_json),
  }));
}

export async function getAICostLinks(): Promise<AICostLink[]> {
  const { data } = await api.get("/ai-cost-origin/links");
  return (data as AICostLink[]).map(link => ({
    ...link,
    sources: normalizeSources(link.sources),
  }));
}

export async function saveAICostLink(link: Partial<AICostLink>): Promise<AICostLink> {
  const body = {
    name: link.name,
    provider: link.provider,
    tool_connection_ids: link.tool_connection_ids ?? [],
    product_name: link.product_name,
    target_type: link.target_type ?? "license_pool",
    operational_connection_id: link.operational_connection_id ?? null,
    project_key: link.project_key ?? null,
    purchase_mode: link.purchase_mode ?? null,
    deployment_type: link.deployment_type ?? null,
    cost_component: link.cost_component ?? null,
    allocation_level: link.allocation_level ?? null,
    license_sku: link.license_sku,
    valid_from: link.valid_from,
    valid_until: link.valid_until,
    active: link.active ?? true,
    distribution_enabled: link.distribution_enabled ?? false,
    distribution_basis: link.distribution_basis,
    distribution_user_key: link.distribution_user_key,
    unit_cost: link.unit_cost ?? null,
    sources: mapLinkSources(link.sources),
  };
  if (link.id) {
    const { data } = await api.put(`/ai-cost-origin/links/${link.id}`, body);
    return { ...data, sources: normalizeSources(data.sources) };
  }
  const { data } = await api.post("/ai-cost-origin/links", body);
  return { ...data, sources: normalizeSources(data.sources) };
}

export async function getAIUnlinkedLicenses(period?: string): Promise<AIUnlinkedLicense[]> {
  const { data } = await api.get("/ai-cost-origin/unlinked", { params: period ? { billing_period: period } : undefined });
  return [...(data.unlinked || []), ...(data.linked || [])].map((item: AIUnlinkedLicense & { has_link?: boolean }) => ({
    ...item,
    has_link: item.has_link ?? false,
  }));
}

export async function previewAICostFilter(body: {
  period: string;
  sources: AICostLinkSource[];
}): Promise<{ matched_rows: number; total_value: number }> {
  let matched_rows = 0;
  let total_value = 0;
  for (const source of body.sources) {
    const filter_json = source.filter_json?.length ? { conditions: source.filter_json } : {};
    const { data } = await api.post("/ai-cost-origin/preview", {
      billing_period: body.period,
      filter_json,
      value_column: source.value_column,
      connection_id: source.connection_id,
      percent: source.percent,
    });
    matched_rows += Number(data.matched_rows || 0);
    total_value += Number(data.total_value || 0);
  }
  return { matched_rows, total_value };
}

export async function previewAIManualEntry(body: {
  provider: string;
  product_name: string;
  period: string;
  unit_cost: number;
  license_sku?: string;
}): Promise<{ active_license_count: number; total_cost: number }> {
  const { data } = await api.post("/ai-cost-origin/manual-entry/preview", {
    provider: body.provider,
    product_name: body.product_name,
    billing_period: body.period,
    unit_price: body.unit_cost,
    license_sku: body.license_sku,
  });
  return {
    active_license_count: data.active_license_count,
    total_cost: data.total_cost,
  };
}

export interface AICostPtuPreview {
  focus: { matched_rows: number; realized_cost: number; meters: string[] };
  operational: { deployments: number | null; ptus: number | null; tokens: number | null; utilization_avg: number | null };
  reconciliation: {
    status: string;
    financial_cost: number;
    operational_ptus: number | null;
    operational_utilization_avg: number | null;
    difference_ptus: number | null;
    conflicts: string[];
  };
  pool_allocations: Array<{ project_key?: string; deployment_key?: string; allocated_amount: number; allocation_basis: string }>;
}

export async function previewAICostPtu(body: {
  period: string;
  sources: AICostLinkSource[];
  project_key?: string;
  purchase_mode?: string;
  deployment_type?: string;
  operational_ptus?: number;
}): Promise<AICostPtuPreview> {
  const { data } = await api.post("/ai-cost-origin/preview-ptu", {
    billing_period: body.period,
    sources: mapLinkSources(body.sources),
    project_key: body.project_key,
    purchase_mode: body.purchase_mode,
    deployment_type: body.deployment_type,
    operational_ptus: body.operational_ptus,
  });
  return data;
}

export async function getAICostPtuConflicts(): Promise<{ conflicts: Array<{ link_id: number; name: string; project_key?: string; conflicts: string[]; meters: string[] }>; count: number }> {
  const { data } = await api.get("/ai-cost-origin/ptu-conflicts");
  return data;
}
