"""Metadados de tabela Iceberg (schema, versão, último período) sem consultar o Trino.

Por que existe: no cluster compartilhado um ``DESCRIBE`` custa 1,3–6,5 s e um
``SELECT MAX(period)`` 2–3 s, e o explorador fazia os dois em série em cada
request antes de ler qualquer dado. O catálogo SQL do PyIceberg no PostgreSQL
devolve o snapshot atual da tabela em ~6 ms, e o snapshot só muda em publicação.
Então ele é a chave do cache: nada expira por tempo, e nada fica velho depois de
um publish — a próxima leitura vê outro snapshot e recalcula.

Os nomes e tipos saem no formato que o Trino mostra em ``DESCRIBE`` (nomes em
minúsculas, ``varchar``/``bigint``...), para os chamadores não distinguirem a
origem. A invalidação explícita (``invalidate``) só libera memória; a correção
vem da chave.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Memória curta da versão da tabela: várias leituras do mesmo request (schema +
#: último período) compartilham um ``load_table``. Não é TTL de dado: a versão
#: lida tem no máximo este atraso em relação a uma publicação concorrente.
VERSION_MEMO_SECONDS = 1.0

_TRINO_TYPES = {
    "boolean": "boolean",
    "int": "integer",
    "long": "bigint",
    "float": "real",
    "double": "double",
    "date": "date",
    "time": "time(6)",
    "timestamp": "timestamp(6)",
    "timestamptz": "timestamp(6) with time zone",
    "string": "varchar",
    "uuid": "uuid",
    "binary": "varbinary",
}


@dataclass(frozen=True)
class TableVersion:
    namespace: str
    table: str
    snapshot_id: int | None
    schema_id: int

    @property
    def key(self) -> tuple[str, str, int | None, int]:
        return (self.namespace, self.table, self.snapshot_id, self.schema_id)


def trino_type_name(field_type: Any) -> str:
    """Nome do tipo como o Trino o descreve para uma coluna Iceberg."""
    text = str(field_type).lower()
    if text in _TRINO_TYPES:
        return _TRINO_TYPES[text]
    if text.startswith("decimal("):
        return text
    if text.startswith("fixed["):
        return "varbinary"
    element = getattr(field_type, "element_type", None)
    if element is not None and text.startswith("list"):
        return f"array({trino_type_name(element)})"
    key_type = getattr(field_type, "key_type", None)
    if key_type is not None and text.startswith("map"):
        return f"map({trino_type_name(key_type)}, {trino_type_name(field_type.value_type)})"
    fields = getattr(field_type, "fields", None)
    if fields is not None and text.startswith("struct"):
        inner = ", ".join(f"{f.name.lower()} {trino_type_name(f.field_type)}" for f in fields)
        return f"row({inner})"
    return text


def _load_table_sync(namespace: str, table: str):
    from app.services import iceberg_service as iceberg

    return iceberg.get_catalog().load_table(iceberg._table_id(namespace, table))


def _version_of(tbl: Any, namespace: str, table: str) -> TableVersion:
    metadata = tbl.metadata
    return TableVersion(namespace, table, metadata.current_snapshot_id, metadata.current_schema_id)


_lock = threading.Lock()
_version_memo: dict[tuple[str, str], tuple[float, TableVersion, Any]] = {}
_schemas: dict[tuple, dict[str, str]] = {}
_latest_periods: dict[tuple, str | None] = {}


async def load_versioned(namespace: str, table: str) -> tuple[TableVersion, Any]:
    """Tabela PyIceberg e sua versão, com memória de ``VERSION_MEMO_SECONDS``."""
    memo_key = (namespace, table)
    now = time.monotonic()
    with _lock:
        hit = _version_memo.get(memo_key)
        if hit and now - hit[0] <= VERSION_MEMO_SECONDS:
            return hit[1], hit[2]
    tbl = await asyncio.to_thread(_load_table_sync, namespace, table)
    version = _version_of(tbl, namespace, table)
    with _lock:
        _version_memo[memo_key] = (time.monotonic(), version, tbl)
    return version, tbl


async def table_version(namespace: str, table: str) -> TableVersion:
    version, _ = await load_versioned(namespace, table)
    return version


async def table_schema(namespace: str, table: str) -> dict[str, str]:
    """``{coluna_minuscula: tipo_trino}`` na ordem do schema, como ``DESCRIBE``."""
    version, tbl = await load_versioned(namespace, table)
    with _lock:
        cached = _schemas.get(version.key)
    if cached is not None:
        return dict(cached)
    schema = {field.name.lower(): trino_type_name(field.field_type) for field in tbl.schema().fields}
    with _lock:
        _drop_other_versions(_schemas, version)
        _schemas[version.key] = schema
    logger.info("Schema de %s.%s lido do catálogo (snapshot %s)", namespace, table, version.snapshot_id)
    return dict(schema)


def _partition_max_sync(tbl: Any, column: str) -> str | None:
    """Maior valor de uma coluna de partição, direto dos manifests (sem Trino)."""
    spec_names = {field.name.lower() for field in tbl.spec().fields}
    if column.lower() not in spec_names:
        return None
    frame = tbl.inspect.partitions().to_pandas()
    if frame.empty or "partition" not in frame.columns:
        return None
    values: list[str] = []
    for partition in frame["partition"]:
        if not isinstance(partition, dict):
            continue
        for name, value in partition.items():
            if name.lower() == column.lower() and value is not None:
                values.append(str(value))
    return max(values) if values else None


async def latest_period(
    namespace: str,
    table: str,
    period_column: str,
    *,
    fallback: Callable[[], Awaitable[str | None]] | None = None,
) -> str | None:
    """Último período da tabela, por snapshot.

    Quando a coluna é de partição, o valor sai dos manifests; senão, ``fallback``
    (um ``SELECT MAX`` no Trino) roda **uma vez por snapshot**, não por request.
    """
    version, tbl = await load_versioned(namespace, table)
    key = (*version.key, period_column.lower())
    with _lock:
        if key in _latest_periods:
            return _latest_periods[key]
    value: str | None = None
    try:
        value = await asyncio.to_thread(_partition_max_sync, tbl, period_column)
    except Exception:  # noqa: BLE001 — manifests ilegíveis caem no fallback
        logger.debug("Partições de %s.%s indisponíveis", namespace, table, exc_info=True)
    if value is None and fallback is not None:
        value = await fallback()
    with _lock:
        _drop_other_versions(_latest_periods, version)
        _latest_periods[key] = value
    logger.info("Último período de %s.%s = %s (snapshot %s)", namespace, table, value, version.snapshot_id)
    return value


def _drop_other_versions(store: dict[tuple, Any], version: TableVersion) -> None:
    stale = [
        key for key in store
        if key[0] == version.namespace and key[1] == version.table and key[2:4] != version.key[2:4]
    ]
    for key in stale:
        store.pop(key, None)


def invalidate(namespace: str | None = None, table: str | None = None) -> int:
    """Esquece o que foi lido de uma tabela (ou de todas).

    Libera memória e força o próximo request a reler o catálogo; a correção do
    dado já vem da chave por snapshot.
    """
    def matches(key: tuple) -> bool:
        return (namespace is None or key[0] == namespace) and (table is None or key[1] == table)

    with _lock:
        removed = 0
        for store in (_schemas, _latest_periods, _version_memo):
            keys = [key for key in store if matches(key)]
            removed += len(keys)
            for key in keys:
                store.pop(key, None)
    return removed
