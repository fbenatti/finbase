#!/usr/bin/env python3
"""One-shot: rename legacy departamento_* columns to organograma_* in Silver/Gold Iceberg tables."""

from __future__ import annotations

import json
import sys

from app.services import iceberg_service as iceberg


def main() -> int:
    namespaces = sys.argv[1:] or ["silver", "gold"]
    summary: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for namespace in namespaces:
        summary[namespace] = iceberg.rename_legacy_organograma_columns(namespace)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
