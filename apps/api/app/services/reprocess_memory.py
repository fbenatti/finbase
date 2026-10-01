"""Memory pressure checks for long-running reprocess workers.

Use the effective cgroup budget when one is configured, otherwise host
available memory. Process RSS is deliberately not used as a quota: Python and
Arrow retain allocator arenas between blobs even after memory is reusable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class MemorySnapshot:
    total_bytes: int
    available_bytes: int
    source: str

    @property
    def available_ratio(self) -> float:
        return self.available_bytes / self.total_bytes if self.total_bytes > 0 else 1.0


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text().strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def memory_snapshot(cgroup_root: str | Path = "/sys/fs/cgroup") -> MemorySnapshot:
    """Return usable memory from cgroup v2/v1, falling back to host memory."""
    root = Path(cgroup_root)
    # cgroup v2 (Docker and modern Linux).
    limit = _read_int(root / "memory.max")
    current = _read_int(root / "memory.current")
    if limit and current is not None and limit < (1 << 60):
        stat: dict[str, int] = {}
        try:
            for line in (root / "memory.stat").read_text().splitlines():
                key, value = line.split(maxsplit=1)
                stat[key] = int(value)
        except (OSError, ValueError):
            pass
        reclaimable = stat.get("inactive_file", 0)
        return MemorySnapshot(limit, max(0, limit - current + reclaimable), "cgroup-v2")

    # cgroup v1. The root mount is common; nested cgroup mounts can be supplied
    # through the standard paths discovered in /proc/self/cgroup.
    for base in (root / "memory", root):
        limit = _read_int(base / "memory.limit_in_bytes")
        current = _read_int(base / "memory.usage_in_bytes")
        if limit and current is not None and limit < (1 << 60):
            stat: dict[str, int] = {}
            try:
                for line in (base / "memory.stat").read_text().splitlines():
                    key, value = line.split(maxsplit=1)
                    stat[key] = int(value)
            except (OSError, ValueError):
                pass
            reclaimable = stat.get("total_inactive_file", stat.get("inactive_file", 0))
            return MemorySnapshot(limit, max(0, limit - current + reclaimable), "cgroup-v1")

    vm = psutil.virtual_memory()
    return MemorySnapshot(int(vm.total), int(vm.available), "host")


def memory_pressure_reason(
    *,
    snapshot: MemorySnapshot | None = None,
    reserve_ratio: float | None = None,
    hard_limit_mb: int | None = None,
) -> str | None:
    """Describe real resource pressure, or return None when safe to proceed.

    ``hard_limit_mb`` is an optional emergency operator override. The default
    protection scales with the actual host/container memory budget.
    """
    snapshot = snapshot or memory_snapshot()
    reserve_ratio = reserve_ratio if reserve_ratio is not None else float(
        os.getenv("REPROCESS_MEMORY_MIN_AVAILABLE_RATIO", "0.10")
    )
    reserve_ratio = min(max(reserve_ratio, 0.01), 0.90)
    reserve_bytes = int(snapshot.total_bytes * reserve_ratio)
    if hard_limit_mb is None:
        raw = os.getenv("REPROCESS_MEMORY_LIMIT_MB", "").strip()
        hard_limit_mb = int(raw) if raw else None
    if hard_limit_mb and snapshot.available_bytes < hard_limit_mb * 1024 * 1024:
        return (f"Memória disponível abaixo do limite emergencial de {hard_limit_mb} MB "
                f"({snapshot.available_bytes // (1024 * 1024)} MB; {snapshot.source})")
    if snapshot.available_bytes < reserve_bytes:
        return (f"Pressão de memória: {snapshot.available_bytes // (1024 * 1024)} MB disponíveis "
                f"de {snapshot.total_bytes // (1024 * 1024)} MB ({snapshot.source}); "
                f"reserva configurada de {reserve_ratio:.0%}")
    return None
