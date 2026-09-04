import os
import re
from pathlib import Path
from pydantic_settings import BaseSettings

# Load all .env-* files from project root and map legacy names → standard names
_project_root = Path(__file__).resolve().parent.parent.parent.parent


def _load_env_file(filepath: Path) -> dict[str, str]:
    """Load a .env file, handling KEY=VALUE, KEY = VALUE, KEY="VALUE" formats."""
    result: dict[str, str] = {}
    if not filepath.exists():
        return result
    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        val = val.strip('"').strip("'")
        result[key] = val
    return result


def _parse_gcp_table(table_spec: str) -> tuple[str, str, str]:
    """Parse 'project.dataset.table' into (project_id, dataset_id, table_name)."""
    parts = table_spec.split(".")
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    return "", "", ""


# Load and map legacy env files
for env_basename, mapping in [
    (".env-4p", {
        "CLIENT_TENANT_ID": "AZURE_4P_TENANT_ID",
        "CLIENT_CLIENT_ID": "AZURE_4P_CLIENT_ID",
        "CLIENT_CLIENT_SECRET": "AZURE_4P_CLIENT_SECRET",
        "CLIENT_STORAGE_ACCOUNT": "AZURE_4P_STORAGE_ACCOUNT",
        "CLIENT_CONTAINER_NAME": "AZURE_4P_CONTAINER",
    }),
    (".env-ti", {
        "CLIENT_TENANT_ID": "AZURE_TI_TENANT_ID",
        "CLIENT_CLIENT_ID": "AZURE_TI_CLIENT_ID",
        "CLIENT_CLIENT_SECRET": "AZURE_TI_CLIENT_SECRET",
        "CLIENT_STORAGE_ACCOUNT": "AZURE_TI_STORAGE_ACCOUNT",
        "CLIENT_CONTAINER_NAME": "AZURE_TI_CONTAINER",
    }),
    (".env-atlas", {
        "base_url": "ATLAS_BASE_URL",
        "contrato_pro": "ATLAS_PRO_CONTRACT_ID",
        "contrato_enterprise": "ATLAS_ENTERPRISE_CONTRACT_ID",
        "pro_dolar": "ATLAS_PRO_USD_TO_BRL",
        "enterprise_dolar": "ATLAS_ENTERPRISE_USD_TO_BRL",
        "taxa_pro": "ATLAS_PRO_TAX_RATE",
        "taxa_enterprise": "ATLAS_ENTERPRISE_TAX_RATE",
    }),
    (".env-databriks", {
        "token": "DATABRICKS_TOKEN",
    }),
]:
    env_data = _load_env_file(_project_root / env_basename)
    for legacy_key, std_key in mapping.items():
        if legacy_key in env_data and std_key not in os.environ:
            os.environ[std_key] = env_data[legacy_key]
    # Parse GCP table references
    if env_basename == ".env-4p" and "GCP_TABELA" in env_data:
        proj, ds, tbl = _parse_gcp_table(env_data["GCP_TABELA"])
        if proj:
            os.environ.setdefault("GCP_4P_PROJECT_ID", proj)
            os.environ.setdefault("GCP_4P_DATASET_ID", ds)
            os.environ.setdefault("GCP_4P_TABLE_NAME", tbl)
        if "GCP_TABELA_OLD" in env_data:
            _, _, tbl_old = _parse_gcp_table(env_data["GCP_TABELA_OLD"])
            if tbl_old:
                os.environ.setdefault("GCP_4P_TABLE_NAME_OLD", tbl_old)
    if env_basename == ".env-ti" and "GCP_TABELA" in env_data:
        proj, ds, tbl = _parse_gcp_table(env_data["GCP_TABELA"])
        if proj:
            os.environ.setdefault("GCP_TI_PROJECT_ID", proj)
            os.environ.setdefault("GCP_TI_DATASET_ID", ds)
            os.environ.setdefault("GCP_TI_TABLE_NAME", tbl)
        if "GCP_TABELA_OLD" in env_data:
            _, _, tbl_old = _parse_gcp_table(env_data["GCP_TABELA_OLD"])
            if tbl_old:
                os.environ.setdefault("GCP_TI_TABLE_NAME_OLD", tbl_old)

# Parse Atlas contract strings into individual keys (group_id:public_key:private_key)
def _parse_atlas_contract(contract_str: str) -> tuple[str, str, str]:
    """Parse 'group_id:public_key:private_key' into three parts."""
    parts = contract_str.split(":")
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), ":".join(parts[2:]).strip()
    return contract_str, "", ""

_pro_contract = os.environ.get("ATLAS_PRO_CONTRACT_ID", "")
if _pro_contract and not os.environ.get("ATLAS_PRO_GROUP_ID"):
    gid, pub, priv = _parse_atlas_contract(_pro_contract)
    os.environ.setdefault("ATLAS_PRO_GROUP_ID", gid)
    os.environ.setdefault("ATLAS_PRO_PUBLIC_KEY", pub)
    os.environ.setdefault("ATLAS_PRO_PRIVATE_KEY", priv)
    # Fallback global keys
    os.environ.setdefault("ATLAS_PUBLIC_KEY", pub)
    os.environ.setdefault("ATLAS_PRIVATE_KEY", priv)

_ent_contract = os.environ.get("ATLAS_ENTERPRISE_CONTRACT_ID", "")
if _ent_contract and not os.environ.get("ATLAS_ENTERPRISE_GROUP_ID"):
    gid, pub, priv = _parse_atlas_contract(_ent_contract)
    os.environ.setdefault("ATLAS_ENTERPRISE_GROUP_ID", gid)
    os.environ.setdefault("ATLAS_ENTERPRISE_PUBLIC_KEY", pub)
    os.environ.setdefault("ATLAS_ENTERPRISE_PRIVATE_KEY", priv)


class Settings(BaseSettings):
    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://finops:finops@localhost:5432/finops",
    )
    database_url_sync: str = os.getenv(
        "DATABASE_URL_SYNC",
        "postgresql://finops:finops@localhost:5432/finops",
    )
    cors_origins: str = os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
    )

    # Environment control
    env: str = os.getenv("ENV", "development")
    skip_seed: bool = os.getenv("SKIP_SEED", "false").lower() == "true"

    # --- Auth / RBAC ---
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "dev-insecure-secret-change-me")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    admin_initial_password: str = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    auth_enabled: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    dev_master_token: str = os.getenv("DEV_MASTER_TOKEN", "")

    # --- Azure 4P (loaded from .env-4p via mapping above) ---
    azure_4p_tenant_id: str = os.getenv("AZURE_4P_TENANT_ID", "")
    azure_4p_client_id: str = os.getenv("AZURE_4P_CLIENT_ID", "")
    azure_4p_client_secret: str = os.getenv("AZURE_4P_CLIENT_SECRET", "")
    azure_4p_storage_account: str = os.getenv("AZURE_4P_STORAGE_ACCOUNT", "")
    azure_4p_container: str = os.getenv("AZURE_4P_CONTAINER", "4pfinops")

    # --- Azure TI (loaded from .env-ti via mapping above) ---
    azure_ti_tenant_id: str = os.getenv("AZURE_TI_TENANT_ID", "")
    azure_ti_client_id: str = os.getenv("AZURE_TI_CLIENT_ID", "")
    azure_ti_client_secret: str = os.getenv("AZURE_TI_CLIENT_SECRET", "")
    azure_ti_storage_account: str = os.getenv("AZURE_TI_STORAGE_ACCOUNT", "")
    azure_ti_container: str = os.getenv("AZURE_TI_CONTAINER", "tifinops")

    # --- GCP 4P (parsed from .env-4p GCP_TABELA) ---
    gcp_4p_project_id: str = os.getenv("GCP_4P_PROJECT_ID", "")
    gcp_4p_dataset_id: str = os.getenv("GCP_4P_DATASET_ID", "")
    gcp_4p_table_name: str = os.getenv("GCP_4P_TABLE_NAME", "")
    gcp_4p_table_name_old: str = os.getenv("GCP_4P_TABLE_NAME_OLD", "")

    # --- GCP TI (parsed from .env-ti GCP_TABELA) ---
    gcp_ti_project_id: str = os.getenv("GCP_TI_PROJECT_ID", "")
    gcp_ti_dataset_id: str = os.getenv("GCP_TI_DATASET_ID", "")
    gcp_ti_table_name: str = os.getenv("GCP_TI_TABLE_NAME", "")
    gcp_ti_table_name_old: str = os.getenv("GCP_TI_TABLE_NAME_OLD", "")

    # --- MongoDB Atlas (loaded from .env-atlas via mapping above) ---
    atlas_base_url: str = os.getenv("ATLAS_BASE_URL", "https://cloud.mongodb.com/api/atlas/v1.0")
    atlas_pro_contract_id: str = os.getenv("ATLAS_PRO_CONTRACT_ID", "")
    atlas_enterprise_contract_id: str = os.getenv("ATLAS_ENTERPRISE_CONTRACT_ID", "")

    # Per-contract keys (parsed from contrato_pro / contrato_enterprise)
    atlas_pro_group_id: str = os.getenv("ATLAS_PRO_GROUP_ID", "")
    atlas_pro_public_key: str = os.getenv("ATLAS_PRO_PUBLIC_KEY", "")
    atlas_pro_private_key: str = os.getenv("ATLAS_PRO_PRIVATE_KEY", "")
    atlas_enterprise_group_id: str = os.getenv("ATLAS_ENTERPRISE_GROUP_ID", "")
    atlas_enterprise_public_key: str = os.getenv("ATLAS_ENTERPRISE_PUBLIC_KEY", "")
    atlas_enterprise_private_key: str = os.getenv("ATLAS_ENTERPRISE_PRIVATE_KEY", "")

    # Fallback global keys (set from pro contract if no explicit keys in .env-atlas)
    atlas_public_key: str = os.getenv("ATLAS_PUBLIC_KEY", "")
    atlas_private_key: str = os.getenv("ATLAS_PRIVATE_KEY", "")

    # Tax/PTAX rates for MongoDB (loaded from .env-atlas, used by seed to create BusinessRules)
    atlas_pro_usd_to_brl: float = float(os.getenv("ATLAS_PRO_USD_TO_BRL", "5.52"))
    atlas_enterprise_usd_to_brl: float = float(os.getenv("ATLAS_ENTERPRISE_USD_TO_BRL", "6.20"))
    atlas_pro_tax_rate: float = float(os.getenv("ATLAS_PRO_TAX_RATE", "0.0"))
    atlas_enterprise_tax_rate: float = float(os.getenv("ATLAS_ENTERPRISE_TAX_RATE", "0.0"))

    # --- Databricks (loaded from .env-databriks via mapping above) ---
    databricks_token: str = os.getenv("DATABRICKS_TOKEN", "")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
