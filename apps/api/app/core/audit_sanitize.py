"""Sanitização, serialização de snapshots e cálculo de diff para auditoria."""
import re
from datetime import date, datetime
from decimal import Decimal

# Estende o padrão _SECRET_CONFIG_KEYS de routers/connections.py
SECRET_KEYS = frozenset({
    "client_secret", "secret", "private_key", "oci_private_key", "pat_token",
    "gcp_token", "connection_string", "eventhub_connection_string",
    "access_token", "token", "password", "password_hash", "refresh_token",
    "authorization", "current_password", "new_password", "clientSecret",
})

# Chaves fora da lista acima que ainda carregam credenciais (certificado privado
# SAML, api_key, SAS). Casamento por padrão, não por lista exata.
_SECRET_KEY_PATTERN = re.compile(
    r"(secret|password|passwd|token|private_key|api_key|apikey|credential|cert_private|sas_)",
    re.IGNORECASE,
)

TRUNCATE_LIMIT = 10_000
DIFF_MAX_FIELDS = 200
# Campos técnicos ignorados no diff
DIFF_IGNORE_FIELDS = frozenset({"updated_at", "last_login_at", "created_at", "_sa_instance_state"})


def sanitize_dict(data):
    """Remove segredos (recursivo) e trunca strings longas."""
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if key in SECRET_KEYS or (isinstance(key, str) and _SECRET_KEY_PATTERN.search(key)):
                out[key] = "***"
                continue
            out[key] = sanitize_dict(value)
        return out
    if isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    if isinstance(data, str) and len(data) > TRUNCATE_LIMIT:
        return data[:TRUNCATE_LIMIT] + "...[truncated]"
    return data


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def to_snapshot(instance) -> dict:
    """Model SQLAlchemy → dict de colunas, datas em ISO, sanitizado."""
    from sqlalchemy import inspect as sa_inspect
    mapper = sa_inspect(type(instance))
    data = {}
    for column in mapper.columns:
        data[column.name] = _jsonable(getattr(instance, column.name))
    return sanitize_dict(data)


def snapshot_from_dict(data: dict | None) -> dict | None:
    if data is None:
        return None
    return sanitize_dict({k: _jsonable(v) for k, v in dict(data).items()})


def compute_diff(before: dict | None, after: dict | None) -> list[dict] | None:
    """Diff raso de primeiro nível: lista de {field, old, new}; None se ambos ausentes."""
    before = before or {}
    after = after or {}
    changes = []
    for field in sorted(set(before) | set(after)):
        if field in DIFF_IGNORE_FIELDS:
            continue
        old = before.get(field)
        new = after.get(field)
        if old == new:
            continue
        changes.append({"field": field, "old": old, "new": new})
        if len(changes) >= DIFF_MAX_FIELDS:
            changes.append({"field": "...", "old": "diff truncado", "new": f"máx. {DIFF_MAX_FIELDS} campos"})
            break
    return changes or None
