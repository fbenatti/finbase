"""Fase 1 do RBAC: modelo de ACL, escopos e regras de negócio (sem banco).

Garante que o modelo ORM e a migração SQL descrevem as mesmas tabelas,
colunas e valores permitidos.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models import access as acc  # noqa: E402
from app.database import Base  # noqa: E402

MIGRATION = (ROOT / "migrations" / "add_access_control.sql").read_text(encoding="utf-8")

NEW_TABLES = {
    "access_acl",
    "access_scope",
    "access_acl_condition",
    "user_scope",
    "group_scope",
    "access_business_rule",
    "access_policy_version",
}


def _check_values(sql_column: str) -> set[str]:
    """Lê os valores de um CHECK (coluna IN ('a', 'b')) da migração."""
    m = re.search(rf"{sql_column}.*?CHECK \({sql_column} IN \(([^)]*)\)\)", MIGRATION)
    assert m, f"CHECK de {sql_column} ausente na migração"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_new_tables_registered_in_metadata():
    assert NEW_TABLES <= set(Base.metadata.tables)


def test_new_tables_present_in_migration():
    for table in NEW_TABLES:
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(", MIGRATION), table


def test_operations_match_migration():
    assert _check_values("operation") == set(acc.ACL_OPERATIONS)


def test_effects_match_migration():
    assert _check_values("effect") == set(acc.ACL_EFFECTS)


def test_resource_types_match_migration():
    assert _check_values("resource_type") == set(acc.ACL_RESOURCE_TYPES)


def test_business_rule_events_match_migration():
    assert _check_values("event") == set(acc.BUSINESS_RULE_EVENTS)


def test_match_modes_match_migration():
    assert _check_values("match_mode") == set(acc.MATCH_MODES)


def test_acl_unique_key_matches_migration():
    table = Base.metadata.tables["access_acl"]
    uq = {c.name for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uq_access_acl" in uq
    cols = next(c for c in table.constraints if getattr(c, "name", None) == "uq_access_acl").columns.keys()
    assert set(cols) == {"role_id", "resource_type", "resource", "operation", "effect"}


def test_backfill_maps_update_to_write_and_skips_scoped_rows():
    backfill = MIGRATION.split("-- Backfill", 1)[1]
    assert "WHEN 'update' THEN 'write'" in backfill
    assert "rp.scope IS NULL" in backfill
