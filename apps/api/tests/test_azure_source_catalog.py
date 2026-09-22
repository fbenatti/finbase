"""Testes do catálogo de fontes Azure e do agrupamento por ambiente (P2).

O contrato que estes testes travam:

- o operador escolhe fontes por **ambiente/credencial compartilhada**, não fonte
  por fonte, e a configuração não exige repetir segredo;
- o agrupamento sai de metadata (`environment_slug`) ou do rótulo de tenant
  (`cloud_name`), nunca de adivinhação por prefixo de `code`;
- toda fonte que pode ser ligada tem uma entrada de catálogo, e o catálogo diz
  quais já têm coletor (`implemented`) e quais ainda não (`planned`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.azure_source_registry import (
    AZURE_SOURCE_CATALOG,
    CONNECTOR_TO_SOURCE_KEY,
    catalog_entry,
    environment_slug,
    is_telemetry,
    sanitized_bronze,
    shared_credential_key,
    source_key_for,
)


def conn(**kwargs):
    base = {"code": "", "connector_type": "", "cloud_name": None, "config_json": {}}
    base.update(kwargs)
    return SimpleNamespace(**base)


# --- agrupamento por ambiente ---------------------------------------------

def test_cloud_name_groups_sources_of_the_same_tenant():
    """`Azure-4P` reúne todas as fontes 4P no mesmo grupo."""
    assert environment_slug(conn(code="azure-4p", cloud_name="Azure-4P")) == "4p"
    assert environment_slug(conn(code="azure-4p-ptu", cloud_name="Azure-4P")) == "4p"
    assert environment_slug(conn(code="azure-automation-4p", cloud_name="Azure-4P")) == "4p"
    assert environment_slug(conn(code="azure-resource-graph-4p", cloud_name="Azure-4P")) == "4p"


def test_ti_is_a_different_environment_from_4p():
    assert environment_slug(conn(code="azure-ti", cloud_name="Azure-TI")) == "ti"
    assert environment_slug(conn(code="azure-automation-ti", cloud_name="Azure-TI")) == "ti"


def test_marketplace_uses_its_own_label():
    assert environment_slug(conn(code="azure-marketplace", cloud_name="Azure Marketplace")) == "marketplace"


def test_explicit_environment_slug_wins_over_label():
    """Metadata manda: se o operador fixou o slug, é ele que vale."""
    explicit = conn(code="azure-qualquer", cloud_name="Azure-4P",
                    config_json={"environment_slug": "producao"})
    assert environment_slug(explicit) == "producao"


def test_grouping_never_renames_the_code():
    """`code` é identidade do pipeline e não muda com o agrupamento."""
    c = conn(code="azure-resource-graph-4p", cloud_name="Azure-4P")
    assert environment_slug(c) == "4p"
    assert c.code == "azure-resource-graph-4p"


def test_falls_back_to_code_when_no_label():
    assert environment_slug(conn(code="azure-automation-4p")) == "4p"
    assert environment_slug(conn(code="azure-resource-graph-ti")) == "ti"


def test_shared_credential_key_groups_by_tenant_and_client():
    a = conn(config_json={"tenant_id": "t1", "client_id": "c1"})
    b = conn(config_json={"tenant_id": "t1", "client_id": "c1"})
    c = conn(config_json={"tenant_id": "t2", "client_id": "c2"})
    assert shared_credential_key(a) == shared_credential_key(b)
    assert shared_credential_key(a) != shared_credential_key(c)


def test_shared_credential_key_is_empty_without_credentials():
    """Fonte de upload manual não tem credencial e não deve agrupar por ela."""
    assert shared_credential_key(conn(config_json={})) == ""


# --- identidade da fonte ---------------------------------------------------

def test_source_key_prefers_explicit_metadata():
    c = conn(connector_type="azure_blob", config_json={"source_key": "resource_graph"})
    assert source_key_for(c) == "resource_graph"


def test_source_key_falls_back_to_connector_type():
    assert source_key_for(conn(connector_type="azure_monitor_vm_metrics")) == "monitor_vm_metrics"
    assert source_key_for(conn(connector_type="azure_resource_graph")) == "resource_graph"
    assert source_key_for(conn(connector_type="azure_automation_jobs")) == "automation_jobs"


def test_azure_blob_is_a_cost_source_by_connector_type():
    assert source_key_for(conn(connector_type="azure_blob")) == "cost_exports"


def test_unknown_connector_has_no_source_key():
    assert source_key_for(conn(connector_type="algo_novo")) is None


# --- catálogo --------------------------------------------------------------

def test_every_connector_type_maps_to_a_catalogued_source():
    keys = {entry["source_key"] for entry in AZURE_SOURCE_CATALOG}
    for connector_type, source_key in CONNECTOR_TO_SOURCE_KEY.items():
        assert source_key in keys, f"{connector_type} aponta para fonte fora do catálogo"


def test_catalog_keys_are_unique():
    keys = [entry["source_key"] for entry in AZURE_SOURCE_CATALOG]
    assert len(keys) == len(set(keys))


def test_catalog_declares_implementation_status_for_every_source():
    for entry in AZURE_SOURCE_CATALOG:
        assert entry["implementation_status"] in {"implemented", "planned"}, entry["source_key"]


def test_only_cost_exports_is_a_cost_source_in_the_catalog():
    """Nenhuma fonte operacional ou de telemetria pode alimentar o FOCUS."""
    cost = {e["source_key"] for e in AZURE_SOURCE_CATALOG if e.get("is_cost_source")}
    assert cost == {"cost_exports"}


def test_monitor_source_is_catalogued_as_implemented_telemetry():
    entry = catalog_entry("monitor_vm_metrics")
    assert entry is not None
    assert entry["implementation_status"] == "implemented"
    assert entry["connector_type"] == "azure_monitor_vm_metrics"
    assert is_telemetry("monitor_vm_metrics") is True
    assert entry["default_window_days"] == 15


def test_cost_exports_is_not_telemetry():
    assert is_telemetry("cost_exports") is False


def test_catalog_entry_returns_none_for_unknown():
    assert catalog_entry("nao_existe") is None


def test_sanitized_bronze_matches_the_pipeline_convention():
    """`_bronze_table_id` troca hífen e ponto por underscore."""
    assert sanitized_bronze(conn(code="azure-4p-monitor-vm")) == "raw_azure_4p_monitor_vm"
    assert sanitized_bronze(conn(code="azure.resource.graph")) == "raw_azure_resource_graph"


# --- estado da fonte na API ------------------------------------------------

def test_state_is_not_configured_when_source_is_disabled():
    from app.routers.azure_sources import STATE_NOT_CONFIGURED, _source_state

    assert _source_state(conn(config_json={"collection_enabled": False}), enabled=False, last_blob=None, running=0) == STATE_NOT_CONFIGURED


def test_state_reports_error_on_failed_test():
    from app.routers.azure_sources import STATE_ERROR, _source_state

    assert _source_state(conn(last_test_status="FAILED"), enabled=True, last_blob=None, running=0) == STATE_ERROR


def test_state_is_available_when_it_has_ingested():
    from app.routers.azure_sources import STATE_AVAILABLE, _source_state

    blob = SimpleNamespace(ingested_at=None, billing_period="202609", row_count=10)
    assert _source_state(conn(last_test_status="OK"), enabled=True, last_blob=blob, running=0) == STATE_AVAILABLE


def test_state_is_not_configured_when_never_tested_and_never_ingested():
    from app.routers.azure_sources import STATE_NOT_CONFIGURED, _source_state

    assert _source_state(conn(last_test_status=None), enabled=True, last_blob=None, running=0) == STATE_NOT_CONFIGURED


def test_enabled_defaults_to_true_for_existing_rows():
    """Conexão legada sem o marcador não pode aparecer desligada."""
    from app.routers.azure_sources import _enabled

    assert _enabled(conn(config_json={})) is True
    assert _enabled(conn(config_json={"collection_enabled": "true"})) is True
    assert _enabled(conn(config_json={"collection_enabled": False})) is False
