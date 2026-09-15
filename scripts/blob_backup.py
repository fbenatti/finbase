#!/usr/bin/env python3
"""Upload/download a full OpenFinops backup to Azure Blob Storage.

Examples:
  python scripts/blob_backup.py upload backups/scheduled/openfinops-full-20260914T212639Z.tar.gz
  python scripts/blob_backup.py download openfinops-full-20260914T212639Z.tar.gz /tmp/openfinops-backup.tar.gz

The default settings are read from ``.env-4p``.  The value of
DEFAULT_UPLOAD_CONTAINER is treated as a blob prefix (Azure container names
cannot contain ``/``); DEFAULT_CONTAINER_NAME is the actual container.
"""
import argparse
import os
import socket
import sys
import time
from pathlib import Path

from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from dotenv import dotenv_values


def _settings(env_file):
    values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    # Allow explicit environment variables to override the selected file.
    values.update({k: v for k, v in os.environ.items() if k.startswith("DEFAULT_")})
    required = (
        "DEFAULT_TENANT_ID", "DEFAULT_CLIENT_ID", "DEFAULT_CLIENT_SECRET",
        "DEFAULT_STORAGE_ACCOUNT", "DEFAULT_CONTAINER_NAME",
    )
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(f"Configuração ausente em {env_file}: {', '.join(missing)}")
    return values


def _blob_client(env_file, storage_ip=None):
    cfg = _settings(env_file)
    credential = ClientSecretCredential(
        tenant_id=cfg["DEFAULT_TENANT_ID"],
        client_id=cfg["DEFAULT_CLIENT_ID"],
        client_secret=cfg["DEFAULT_CLIENT_SECRET"],
    )
    storage_host = f"{cfg['DEFAULT_STORAGE_ACCOUNT']}.blob.core.windows.net"
    if storage_ip:
        # Some private VM networks lack a DNS forwarder for blob.core.windows.net.
        # Preserve the HTTPS hostname/SNI while resolving only this host to the
        # supplied address for the lifetime of this process.
        original_getaddrinfo = socket.getaddrinfo

        def getaddrinfo(host, *args, **kwargs):
            if host == storage_host:
                host = storage_ip
            return original_getaddrinfo(host, *args, **kwargs)

        socket.getaddrinfo = getaddrinfo
    service = BlobServiceClient(
        account_url=f"https://{storage_host}",
        credential=credential,
        connection_timeout=60,
        read_timeout=300,
    )
    prefix = cfg.get("DEFAULT_UPLOAD_CONTAINER", "").strip("/")
    return service, cfg["DEFAULT_CONTAINER_NAME"], prefix


def _blob_name(filename, prefix):
    name = Path(filename).name
    return f"{prefix}/{name}" if prefix else name


def _progress(label: str):
    started = time.monotonic()
    last_print = 0.0

    def report(current, total):
        nonlocal last_print
        now = time.monotonic()
        if total and current < total and now - last_print < 1.0:
            return
        last_print = now
        elapsed = max(now - started, 0.001)
        speed = current / elapsed
        percent = (current / total * 100) if total else 0.0
        message = (
            f"\r{label}: {percent:6.2f}% | {current / 1024**3:.2f} GiB"
            f"/{(total / 1024**3):.2f} GiB | {speed / 1024**2:.2f} MiB/s"
        )
        sys.stdout.write(message)
        sys.stdout.flush()
        if total and current >= total:
            sys.stdout.write("\n")

    return report


def upload(local_file, env_file=".env-4p", blob_name=None, storage_ip=None):
    """Upload *local_file* and return its full blob path."""
    source = Path(local_file).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    service, container, prefix = _blob_client(env_file, storage_ip)
    target = blob_name or _blob_name(source.name, prefix)
    blob = service.get_blob_client(container=container, blob=target)
    with source.open("rb") as stream:
        blob.upload_blob(
            stream,
            overwrite=True,
            max_concurrency=2,
            timeout=300,
            progress_hook=_progress("Upload"),
        )
    return target


def download(blob_name, local_file, env_file=".env-4p", storage_ip=None):
    """Download *blob_name* (relative to DEFAULT_UPLOAD_CONTAINER) locally."""
    service, container, prefix = _blob_client(env_file, storage_ip)
    target = blob_name.strip("/")
    if "/" not in target and prefix:
        target = _blob_name(target, prefix)
    destination = Path(local_file).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    blob = service.get_blob_client(container=container, blob=target)
    stream = blob.download_blob(max_concurrency=2, timeout=300)
    report = _progress("Download")
    total = stream.size
    with destination.open("wb") as output:
        current = 0
        for chunk in stream.chunks():
            output.write(chunk)
            current += len(chunk)
            report(current, total)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env-4p", help="arquivo de configuração Azure")
    parser.add_argument("--storage-ip", help="IP temporário do Blob quando a VM não resolve DNS")
    # ``required`` was added to argparse subparsers after Python 3.6.
    sub = parser.add_subparsers(dest="command")
    up = sub.add_parser("upload", help="enviar arquivo para o Blob")
    up.add_argument("local_file")
    up.add_argument("--blob-name", help="nome completo opcional do blob")
    down = sub.add_parser("download", help="baixar arquivo do Blob")
    down.add_argument("blob_name", help="nome do arquivo ou caminho completo do blob")
    down.add_argument("local_file")
    args = parser.parse_args()
    if not args.command:
        parser.error("informe upload ou download")
    if args.command == "upload":
        print(f"Upload concluído: {upload(args.local_file, env_file=args.env_file, blob_name=args.blob_name, storage_ip=args.storage_ip)}")
    else:
        print(f"Download concluído: {download(args.blob_name, args.local_file, env_file=args.env_file, storage_ip=args.storage_ip)}")


if __name__ == "__main__":
    main()
