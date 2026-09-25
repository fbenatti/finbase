"""Consistent full-project backup used by the independent backup schedule."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

BackupAbort = Callable[[], bool]


class BackupCancelledError(RuntimeError):
    """Raised when a full backup is cancelled while holding the write lock."""


def _abort_if(should_abort: BackupAbort | None) -> None:
    if should_abort and should_abort():
        raise BackupCancelledError("Backup cancelado pelo usuário")


def _run_subprocess_abortable(
    cmd: list[str],
    *,
    env: dict[str, str],
    should_abort: BackupAbort | None,
) -> None:
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while proc.poll() is None:
        if should_abort and should_abort():
            proc.kill()
            proc.wait()
            raise BackupCancelledError("Backup cancelado pelo usuário")
        time.sleep(0.5)
    stdout, stderr = proc.communicate(timeout=1)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout, stderr=stderr)

def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _database_parts() -> tuple[str, str, str, str, str]:
    raw = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL", "")
    if not raw:
        # The worker loads the project settings via pydantic-settings, while
        # the URL is not necessarily exported in its process environment.
        # Reuse that resolved value instead of falling back to localhost:5432.
        try:
            from app.config import settings
            raw = settings.database_url_sync
        except Exception:
            raw = ""
    raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1)
    parsed = urlparse(raw)
    return (
        parsed.hostname or "localhost",
        str(parsed.port or 5432),
        unquote(parsed.username or ""),
        unquote(parsed.password or ""),
        (parsed.path or "/").lstrip("/") or "postgres",
    )


def _database_server_major() -> int:
    """Return the connected PostgreSQL server major version.

    ``pg_dump`` must be the same major as, or newer than, the server. Relying
    on PATH alone is unsafe on developer machines where several libpq/Homebrew
    versions may be installed.
    """
    from sqlalchemy import text
    from app.database import SyncSession

    session = SyncSession()
    try:
        version_num = int(session.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        return version_num // 10000
    finally:
        session.close()


def _pg_tool(name: str, server_major: int) -> str:
    """Find a pg_dump/pg_restore that can read/write this server's format."""
    override = os.getenv(f"OPENFINOPS_{name.upper()}_BIN")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    for candidate in (
        shutil.which(name),
        shutil.which(f"{name}-{server_major}"),
        f"/opt/homebrew/opt/postgresql@{server_major}/bin/{name}",
        f"/usr/local/opt/postgresql@{server_major}/bin/{name}",
        f"/usr/lib/postgresql/{server_major}/bin/{name}",
        f"/usr/pgsql-{server_major}/bin/{name}",
    ):
        if candidate:
            candidates.append(Path(candidate))
    for cellar in (Path("/opt/homebrew/Cellar"), Path("/usr/local/Cellar")):
        if cellar.exists():
            candidates.extend(cellar.glob(f"postgresql@{server_major}/*/bin/{name}"))
            candidates.extend(cellar.glob(f"libpq/*/bin/{name}"))

    versions: list[tuple[int, Path]] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = str(candidate)
        if path in seen or not Path(path).is_file():
            continue
        seen.add(path)
        try:
            output = subprocess.run([path, "--version"], check=True, capture_output=True, text=True, timeout=5).stdout
            match = re.search(r"\b(\d+)\.", output)
            if match:
                versions.append((int(match.group(1)), Path(path)))
        except (OSError, subprocess.SubprocessError):
            continue
    compatible = [item for item in versions if item[0] >= server_major]
    if not compatible:
        found = ", ".join(f"{version} ({path})" for version, path in versions) or "nenhum"
        raise RuntimeError(
            f"Não há {name} compatível com PostgreSQL {server_major}; versões encontradas: {found}. "
            f"Instale o cliente PostgreSQL {server_major} ou defina OPENFINOPS_{name.upper()}_BIN."
        )
    # Prefer the exact server major; otherwise use the oldest compatible
    # version to minimize dump-format surprises.
    compatible.sort(key=lambda item: (item[0] != server_major, item[0]))
    return str(compatible[0][1])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_full_backup(
    *,
    retention_days: int = 30,
    output_dir: str | None = None,
    should_abort: BackupAbort | None = None,
) -> dict:
    """Create one compressed archive containing DB, Iceberg and the project.

    backup directory itself is excluded to avoid recursively archiving prior
    backups and the archive currently being written.
    """
    root = _project_root().resolve()
    destination = Path(output_dir or os.getenv("OPENFINOPS_BACKUP_DIR", str(root / "backups" / "scheduled"))).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = destination / f"openfinops-full-{stamp}.tar.gz"

    _abort_if(should_abort)

    with tempfile.TemporaryDirectory(prefix="openfinops-full-backup-") as temp_name:
        staging = Path(temp_name)
        warehouse = Path(os.getenv("ICEBERG_LOCAL_WAREHOUSE", str(root / "apps" / "data" / "iceberg"))).expanduser().resolve()
        db_dir = staging / "postgres"
        db_dir.mkdir()
        host, port, user, password, database = _database_parts()
        server_major = _database_server_major()
        pg_dump = _pg_tool("pg_dump", server_major)
        pg_restore = _pg_tool("pg_restore", server_major)
        dump = db_dir / "finops.dump"
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        _run_subprocess_abortable(
            [pg_dump, "--format=custom", "--no-owner", "--file", str(dump), "--host", host, "--port", port, "--username", user, database],
            env=env,
            should_abort=should_abort,
        )
        _abort_if(should_abort)
        _run_subprocess_abortable(
            [pg_restore, "--list", str(dump)],
            env=env,
            should_abort=should_abort,
        )

        manifest = {
            "format": "openfinops-full-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": database,
            "warehouse": str(warehouse),
            "includes_env": True,
            "project_root": str(root),
            "archive_excludes": ["backups/scheduled", "temporary staging"],
            "postgres_dump_sha256": _sha256(dump),
        }
        manifest_path = staging / "BACKUP_MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        backups_root = (root / "backups" / "scheduled").resolve()
        project_warehouse = (root / "apps" / "data" / "iceberg").resolve()
        def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            name = info.name.rstrip("/")
            if name == "openfinops/backups/scheduled" or name.startswith("openfinops/backups/scheduled/"):
                return None
            if project_warehouse == (root / "apps" / "data" / "iceberg").resolve() and (name == "openfinops/apps/data/iceberg" or name.startswith("openfinops/apps/data/iceberg/")):
                return None
            return info
        _abort_if(should_abort)
        # Stream directly into the compressed archive. The previous
        # copytree-based implementation duplicated the 15GB warehouse before
        # compression and made a full backup appear hung.
        with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
            tar.add(root, arcname="openfinops", recursive=True, filter=_filter)
            tar.add(db_dir, arcname="openfinops/postgres", recursive=True)
            tar.add(manifest_path, arcname="openfinops/BACKUP_MANIFEST.json")
            # When the warehouse lives inside the project, it was skipped by
            # the project filter above and must be added explicitly.  For an
            # external warehouse this is the sole copy as well.
            if warehouse.exists():
                tar.add(warehouse, arcname="openfinops/apps/data/iceberg", recursive=True)

    archive.chmod(0o600)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n", encoding="utf-8")
    checksum.chmod(0o600)
    with tarfile.open(archive, "r:gz") as tar:
        if "openfinops/BACKUP_MANIFEST.json" not in tar.getnames():
            raise RuntimeError("Manifesto ausente no backup compactado")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
    removed = 0
    for old in destination.glob("openfinops-full-*.tar.gz"):
        if old == archive:
            continue
        if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
            old.unlink()
            sidecar = old.with_suffix(old.suffix + ".sha256")
            if sidecar.exists():
                sidecar.unlink()
            removed += 1
    return {"archive": str(archive), "sha256": _sha256(archive), "retention_removed": removed}
