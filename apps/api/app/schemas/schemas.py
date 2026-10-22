from pydantic import BaseModel, Field
from datetime import datetime
from pathlib import Path
from typing import Any

# In local development this module lives at
# ``<repository>/apps/api/app/schemas``.  The Docker image, on the other hand,
# deliberately copies only the API to ``/app``.  Do not assume the fourth
# parent exists or the API cannot even import when running in Docker.
_MODULE_PATH = Path(__file__).resolve()
_REPOSITORY_ROOT = next(
    (
        parent
        for parent in _MODULE_PATH.parents
        if (parent / "apps" / "api").is_dir()
    ),
    _MODULE_PATH.parents[2],
)

class SourceConnectionCreate(BaseModel):
    code: str
    name: str
    connector_type: str
    cloud_name: str | None = None
    cloud_scope: str | None = None
    config_json: dict[str, Any] = {}
    # Lista (formato antigo) ou mapa `{tipo de relatório: caminho}` (novo).
    blob_prefixes: list[str] | dict[str, str] | dict[str, str] = []

    model_config = {"extra": "allow"}


class SourceConnectionUpdate(BaseModel):
    name: str | None = None
    cloud_name: str | None = None
    cloud_scope: str | None = None
    config_json: dict[str, Any] | None = None
    blob_prefixes: list[str] | dict[str, str] | dict[str, str] | None = None


class SourceConnectionOut(BaseModel):
    id: int
    code: str
    name: str
    connector_type: str
    cloud_name: str | None
    cloud_scope: str | None
    config_json: dict[str, Any]
    blob_prefixes: list[str] | dict[str, str]
    last_test_status: str | None = None
    last_test_message: str | None = None
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IngestedBlobOut(BaseModel):
    id: int
    connection_id: int
    blob_path: str
    blob_prefix: str
    billing_period: str | None
    last_modified: datetime
    etag: str | None
    content_length: int | None
    row_count: int | None
    status: str
    ingested_at: datetime

    model_config = {"from_attributes": True}


class BlobScanItem(BaseModel):
    blob_path: str
    blob_prefix: str
    last_modified: datetime
    content_length: int | None
    row_count: int | None = None
    status: str  # NEW | CHANGED | UNCHANGED
    billing_period: str | None = None


class BlobScanResult(BaseModel):
    connection_id: int
    total: int
    new: int
    changed: int
    unchanged: int
    items: list[BlobScanItem]


# ---- EtlMapping ----

class EtlMappingCreate(BaseModel):
    connection_id: int | None = None
    name: str
    source_column: str
    target_column: str
    transform_type: str = "RENAME"
    transform_config: dict[str, Any] | None = None
    priority: int = 0
    active: bool = True


class EtlMappingUpdate(BaseModel):
    name: str | None = None
    source_column: str | None = None
    target_column: str | None = None
    transform_type: str | None = None
    transform_config: dict[str, Any] | None = None
    priority: int | None = None
    active: bool | None = None


class EtlMappingOut(BaseModel):
    id: int
    connection_id: int | None = None
    name: str
    source_column: str
    target_column: str
    transform_type: str
    transform_config: dict[str, Any] | None
    priority: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- Department ----

class DepartmentCreate(BaseModel):
    parent_id: int | None = None
    name: str
    centro_custo: str | None = None
    id_orcamento: str | None = None
    ambiente: str | None = None
    custom_fields: dict[str, Any] | None = None
    active: bool = True


class DepartmentUpdate(BaseModel):
    parent_id: int | None = None
    name: str | None = None
    centro_custo: str | None = None
    id_orcamento: str | None = None
    ambiente: str | None = None
    custom_fields: dict[str, Any] | None = None
    active: bool | None = None


class DepartmentOut(BaseModel):
    id: int
    parent_id: int | None = None
    name: str
    centro_custo: str | None
    id_orcamento: str | None
    ambiente: str | None
    custom_fields: dict[str, Any] | None
    active: bool
    created_at: datetime
    updated_at: datetime
    children: list["DepartmentOut"] = []

    model_config = {"from_attributes": True}


class SharedCostTargetIn(BaseModel):
    department_id: int
    percent: float | None = None


class SharedCostTargetOut(BaseModel):
    department_id: int
    percent: float | None = None
    name: str | None = None
    path: str | None = None


class SharedCostUpsert(BaseModel):
    enabled: bool = False
    allocation_type: str = "shared_cost"
    mode: str = "consumption"
    targets: list[SharedCostTargetIn] = []


class SharedCostOut(BaseModel):
    department_id: int
    enabled: bool
    allocation_type: str = "shared_cost"
    mode: str
    name: str | None = None
    path: str | None = None
    targets: list[SharedCostTargetOut] = []

    model_config = {"from_attributes": True}



    model_config = {"from_attributes": True}


# ---- BusinessRule (enhanced) ----

class ConditionItem(BaseModel):
    field: str
    op: str  # ==, !=, contains, in
    value: str | list[str]
    logic: str = "AND"  # AND | OR — how to combine with previous condition
    then_field: str | None = None
    then_value: str | None = None


class BusinessRuleCreate(BaseModel):
    connection_id: int | None = None
    destination_table_id: int | None = None
    name: str
    description: str | None = None
    target_column: str
    rule_type: str
    config_json: dict[str, Any] = {}
    conditions: list[dict[str, Any]] | None = None
    expression: str | None = None
    priority: int = 0
    valid_from: str | None = None
    valid_until: str | None = None
    cloud_scope: str | None = None
    financial_effect: str = "NONE"
    financial_contract_id: int | None = None
    active: bool = True


class BusinessRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    connection_id: int | None = None
    destination_table_id: int | None = None
    target_column: str | None = None
    rule_type: str | None = None
    config_json: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = None
    expression: str | None = None
    priority: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    cloud_scope: str | None = None
    financial_effect: str | None = None
    financial_contract_id: int | None = None
    active: bool | None = None


class BusinessRuleOut(BaseModel):
    id: int
    connection_id: int | None = None
    destination_table_id: int | None = None
    name: str
    description: str | None
    target_column: str
    rule_type: str
    config_json: dict[str, Any]
    conditions: list[dict[str, Any]] | None = None
    expression: str | None = None
    priority: int
    valid_from: str | None
    valid_until: str | None
    cloud_scope: str | None = None
    financial_effect: str = "NONE"
    financial_contract_id: int | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- ClassificationRule (enhanced) ----

class ClassificationConditionItem(BaseModel):
    field: str
    operator: str  # =, ~, !=
    values: list[str]
    logic: str = "AND"


class ClassificationRuleCreate(BaseModel):
    connection_id: int | None = None
    destination_table_id: int | None = None
    # A classification is an allocation to an organizational object.  Rules
    # without a department cannot provide a reliable hierarchy in Gold, so
    # they are not a valid new rule.
    department_id: int
    name: str
    source_field: str
    pattern: str
    pattern_type: str = "regex"
    match_value: str
    target_field: str
    conditions: list[dict[str, Any]] | None = None
    priority: int = 0
    cloud_scope: str | None = None
    active: bool = True


class ClassificationRuleUpdate(BaseModel):
    connection_id: int | None = None
    name: str | None = None
    source_field: str | None = None
    pattern: str | None = None
    pattern_type: str | None = None
    match_value: str | None = None
    target_field: str | None = None
    conditions: list[dict[str, Any]] | None = None
    department_id: int | None = None
    destination_table_id: int | None = None
    priority: int | None = None
    cloud_scope: str | None = None
    active: bool | None = None


class ClassificationRuleOut(BaseModel):
    id: int
    connection_id: int | None = None
    destination_table_id: int | None = None
    department_id: int | None = None
    name: str
    source_field: str
    pattern: str
    pattern_type: str
    match_value: str
    target_field: str
    conditions: list[dict[str, Any]] | None = None
    priority: int
    cloud_scope: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- CSV Import ----

class CsvImportRequest(BaseModel):
    diretorias_path: str = str(_REPOSITORY_ROOT / "diretorias_download.csv")
    business_rules_path: str = str(_REPOSITORY_ROOT / "businessRules_download.csv")
    cloud_root_map: dict[str, int] | None = None


class CsvImportResult(BaseModel):
    departments_created: int
    classification_rules_created: int
    errors: list[str] = []


# ---- ETL / Periods ----

class EtlRunRequest(BaseModel):
    connection_id: int
    selected_blobs: list[str] | None = None
    force_periods: list[str] | None = None
    backfill: bool | None = None
    backfill_days: int | None = None
    lookback_hours: int | None = None


class EtlRunStatus(BaseModel):
    connection_id: int
    status: str
    blobs_processed: int
    rows_raw: int
    rows_normalized: int
    errors: list[str] = []


class PeriodStatusOut(BaseModel):
    billing_period: str
    connection_id: int
    status: str
    auto_close_days: int
    closed_at: datetime | None = None
    total_lines: int = 0
    classified_pct: float = 0.0

    model_config = {"from_attributes": True}


class PeriodCloseRequest(BaseModel):
    connection_id: int
    billing_period: str


class PeriodConfigUpdate(BaseModel):
    auto_close_days: int | None = None


# ---- Skipped Blobs ----

class SkippedBlobOut(BaseModel):
    id: int
    connection_id: int
    blob_path: str
    reason: str | None = None
    skipped_at: datetime

    model_config = {"from_attributes": True}


class SkipBlobRequest(BaseModel):
    connection_id: int
    blob_path: str
    reason: str | None = None


# ---- Destination Tables ----

class DestinationPartitionExtra(BaseModel):
    column: str
    transform: str = "identity"  # identity | bucket
    bucket_count: int = 16


class DestinationPartitionSpec(BaseModel):
    grain: str = "billing_period"  # billing_period | snapshot_date
    snapshot_column: str | None = None
    extra_partitions: list[DestinationPartitionExtra] = Field(default_factory=list)


class DestinationTableCreate(BaseModel):
    name: str
    description: str | None = None
    physical_name: str | None = None  # auto-generated if not provided
    partition_spec_json: DestinationPartitionSpec | dict | None = None
    active: bool = True


class DestinationTableUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    partition_spec_json: DestinationPartitionSpec | dict | None = None
    active: bool | None = None


class DestinationTableOut(BaseModel):
    id: int
    name: str
    description: str | None
    physical_name: str
    partition_spec_json: dict | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DestinationColumnOut(BaseModel):
    id: int
    destination_table_id: int
    column_name: str
    data_type: str
    is_required: bool
    position: int
    default_value: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DestinationColumnCreate(BaseModel):
    column_name: str
    data_type: str = "TEXT"
    is_required: bool = False
    position: int | None = None
    default_value: str | None = None


class SourceDestinationMappingOut(BaseModel):
    id: int
    source_connection_id: int
    destination_table_id: int
    name: str | None
    source_column: str
    destination_column: str
    transform_type: str
    transform_config: dict[str, Any] | None
    priority: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceDestinationMappingCreate(BaseModel):
    source_connection_id: int
    destination_table_id: int
    name: str | None = None
    source_column: str
    destination_column: str
    transform_type: str = "RENAME"
    transform_config: dict[str, Any] | None = None
    priority: int = 0
    active: bool = True


class SourceDestinationMappingUpdate(BaseModel):
    name: str | None = None
    source_column: str | None = None
    destination_column: str | None = None
    transform_type: str | None = None
    transform_config: dict[str, Any] | None = None
    priority: int | None = None
    active: bool | None = None


class DestinationTableDetailOut(BaseModel):
    """Destination table with its columns."""
    id: int
    name: str
    description: str | None
    physical_name: str
    partition_spec_json: dict | None = None
    active: bool
    created_at: datetime
    updated_at: datetime
    columns: list[DestinationColumnOut] = []

    model_config = {"from_attributes": True}


class GoldChainOption(BaseModel):
    """Optional Gold materialization enqueued after the reprocess completes."""
    enabled: bool = False
    rule_ids: list[int] = Field(default_factory=list)


class ReprocessRequest(BaseModel):
    destination_table_id: int
    connection_ids: list[int]
    billing_periods: list[str] = []
    replace_existing: bool = False
    chain_gold_cubes: GoldChainOption = Field(default_factory=GoldChainOption)


class ScheduledTaskCreate(BaseModel):
    name: str
    connection_id: int | None = None
    connection_ids: list[int] = []  # multi-connection scope; falls back to [connection_id]
    cron_expression: str  # e.g. "0 3 * * *"
    timezone: str = "America/Sao_Paulo"
    destination_reprocess: bool = True
    active: bool = True
    config_json: dict[str, Any] = {}
    schedule_type: str = "collection"


class ScheduledTaskUpdate(BaseModel):
    name: str | None = None
    connection_id: int | None = None
    connection_ids: list[int] | None = None
    cron_expression: str | None = None
    timezone: str | None = None
    destination_reprocess: bool | None = None
    active: bool | None = None
    config_json: dict[str, Any] | None = None
    schedule_type: str | None = None


class ScheduledTaskOut(BaseModel):
    id: int
    name: str
    connection_id: int | None
    connection_ids: list[int] = []
    cron_expression: str
    timezone: str
    destination_reprocess: bool
    active: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    config_json: dict[str, Any]
    schedule_type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- Azure Tag Policy ----

class AzureTenantOut(BaseModel):
    connection_id: int
    code: str
    name: str
    tenant_id: str


class AzurePolicyInheritance(BaseModel):
    name: str
    policyDefinitionId: str
    parameters: dict[str, Any]
    note: str = ""


class AzurePolicyResourceGroup(BaseModel):
    tags: dict[str, str]
    names: list[str]
    rule_names: list[str] = []


class AzurePolicyPattern(BaseModel):
    tags: dict[str, str]
    condition: dict[str, Any]
    rule_names: list[str] = []


class AzureTenantPolicyOut(BaseModel):
    tenant_id: str | None = None
    connection_code: str
    connection_name: str
    rule_count: int
    layers: dict[str, Any]


class AzureTagPolicyExport(BaseModel):
    tenants: list[AzureTenantPolicyOut]
    deployment_script: str = ""
