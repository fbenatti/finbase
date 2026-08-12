from pydantic import BaseModel
from datetime import datetime
from typing import Any


class SourceConnectionCreate(BaseModel):
    code: str
    name: str
    connector_type: str
    cloud_name: str | None = None
    config_json: dict[str, Any] = {}
    blob_prefixes: list[str] = []


class SourceConnectionUpdate(BaseModel):
    name: str | None = None
    config_json: dict[str, Any] | None = None
    blob_prefixes: list[str] | None = None


class SourceConnectionOut(BaseModel):
    id: int
    code: str
    name: str
    connector_type: str
    cloud_name: str | None
    config_json: dict[str, Any]
    blob_prefixes: list[str]
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
    ambiente: str | None = None
    cloud_filter: str | None = None
    custom_fields: dict[str, Any] | None = None
    active: bool = True


class DepartmentUpdate(BaseModel):
    parent_id: int | None = None
    name: str | None = None
    centro_custo: str | None = None
    ambiente: str | None = None
    cloud_filter: str | None = None
    custom_fields: dict[str, Any] | None = None
    active: bool | None = None


class DepartmentOut(BaseModel):
    id: int
    parent_id: int | None = None
    name: str
    centro_custo: str | None
    ambiente: str | None
    cloud_filter: str | None
    custom_fields: dict[str, Any] | None
    active: bool
    created_at: datetime
    updated_at: datetime
    children: list["DepartmentOut"] = []

    model_config = {"from_attributes": True}


# ---- Project ----

class ProjectCreate(BaseModel):
    department_id: int
    projeto_name: str
    cloud: str | None = None
    subscription_rg: str | None = None
    active: bool = True


class ProjectUpdate(BaseModel):
    department_id: int | None = None
    projeto_name: str | None = None
    cloud: str | None = None
    subscription_rg: str | None = None
    active: bool | None = None


class ProjectOut(BaseModel):
    id: int
    department_id: int
    projeto_name: str
    cloud: str | None
    subscription_rg: str | None
    active: bool
    created_at: datetime
    updated_at: datetime

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
    department_id: int | None = None
    project_id: int | None = None
    name: str
    source_field: str
    pattern: str
    pattern_type: str = "regex"
    match_value: str
    target_field: str
    conditions: list[dict[str, Any]] | None = None
    priority: int = 0
    active: bool = True


class ClassificationRuleUpdate(BaseModel):
    name: str | None = None
    source_field: str | None = None
    pattern: str | None = None
    pattern_type: str | None = None
    match_value: str | None = None
    target_field: str | None = None
    conditions: list[dict[str, Any]] | None = None
    department_id: int | None = None
    project_id: int | None = None
    destination_table_id: int | None = None
    priority: int | None = None
    active: bool | None = None


class ClassificationRuleOut(BaseModel):
    id: int
    connection_id: int | None = None
    destination_table_id: int | None = None
    department_id: int | None = None
    project_id: int | None = None
    name: str
    source_field: str
    pattern: str
    pattern_type: str
    match_value: str
    target_field: str
    conditions: list[dict[str, Any]] | None = None
    priority: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- CSV Import ----

class CsvImportRequest(BaseModel):
    diretorias_path: str = "/Users/fernandohenriquebenatti/Documents/FinOps Vivo/NovoReport/finopsNew/diretorias_download.csv"
    projetos_path: str = "/Users/fernandohenriquebenatti/Documents/FinOps Vivo/NovoReport/finopsNew/projetos_download.csv"
    business_rules_path: str = "/Users/fernandohenriquebenatti/Documents/FinOps Vivo/NovoReport/finopsNew/businessRules_download.csv"


class CsvImportResult(BaseModel):
    departments_created: int
    projects_created: int
    classification_rules_created: int
    errors: list[str] = []


# ---- ETL / Periods ----

class EtlRunRequest(BaseModel):
    connection_id: int
    selected_blobs: list[str] | None = None
    force_periods: list[str] | None = None


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

class DestinationTableCreate(BaseModel):
    name: str
    description: str | None = None
    physical_name: str | None = None  # auto-generated if not provided
    active: bool = True


class DestinationTableUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class DestinationTableOut(BaseModel):
    id: int
    name: str
    description: str | None
    physical_name: str
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
    active: bool
    created_at: datetime
    updated_at: datetime
    columns: list[DestinationColumnOut] = []

    model_config = {"from_attributes": True}


class ReprocessRequest(BaseModel):
    destination_table_id: int
    connection_ids: list[int]
    billing_periods: list[str] = []
    replace_existing: bool = False


class ScheduledTaskCreate(BaseModel):
    name: str
    connection_id: int
    cron_expression: str  # e.g. "0 3 * * *"
    timezone: str = "America/Sao_Paulo"
    destination_reprocess: bool = True
    active: bool = True
    config_json: dict[str, Any] = {}


class ScheduledTaskUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    timezone: str | None = None
    destination_reprocess: bool | None = None
    active: bool | None = None
    config_json: dict[str, Any] | None = None


class ScheduledTaskOut(BaseModel):
    id: int
    name: str
    connection_id: int
    cron_expression: str
    timezone: str
    destination_reprocess: bool
    active: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    config_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
