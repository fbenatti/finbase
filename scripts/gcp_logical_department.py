"""Converte project id/name GCP em departamento lógico (não usa slug nem label landing-zone)."""

from __future__ import annotations

import re

ENV_SUFFIXES = {"dev", "hml", "prd", "shd", "dpt"}
STRIP_PREFIXES = ("vivo-ti-", "vivo-", "ti-")
ACRONYMS = {
    "iac": "IaC",
    "rpa": "RPA",
    "mse": "MSE",
    "b2b": "B2B",
    "b2c": "B2C",
    "rcs": "RCS",
    "poc": "POC",
    "nspic": "NSPIC",
    "ai": "AI",
}

ALIASES = {
    "networkspoke": "Networking",
    "network-hub": "Networking",
    "networking-hub": "Networking",
    "networking": "Networking",
    "network": "Networking",
    "logging-network": "Logging Network",
    "logging": "Logging",
    "monitoring": "Monitoring",
    "monitoramento": "Monitoring",
    "billing": "Billing",
    "golden-images": "Golden Images",
    "audit-logs": "Audit Logs",
    "tecnologia-iac": "Tecnologia IaC",
    "terraform": "IaC",
    "mse-digital": "MSE Digital",
    "b2b-b2c-rcs": "B2B B2C RCS",
    "activity": "Activity",
    "rpa": "RPA",
}

TRILHA_MAP = {
    "billing": "Billing",
    "network": "Networking",
    "monitoramento": "Monitoring",
    "iac": "IaC",
}


def _strip_to_slug(raw: str) -> str:
    slug = (raw or "").strip().lower()
    slug = re.sub(r"-brtl$", "", slug)
    slug = re.sub(r"-[0-9a-f]{4}$", "", slug)
    for prefix in STRIP_PREFIXES:
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    parts = [p for p in slug.split("-") if p]
    if parts and parts[-1] in ENV_SUFFIXES:
        parts = parts[:-1]
    return "-".join(parts)


def _title_slug(slug: str) -> str:
    words = []
    for w in slug.replace("_", "-").split("-"):
        if not w:
            continue
        if w in ACRONYMS:
            words.append(ACRONYMS[w])
        else:
            words.append(w.capitalize())
    return " ".join(words)


def logical_department_name(
    project_id: str,
    display_name: str = "",
    labels: dict | None = None,
) -> str:
    """Nome de departamento amigável; agrupa ambientes (dev/hml/prd/shd)."""
    labels = labels or {}
    raw = display_name or project_id or ""
    slug = _strip_to_slug(raw) or _strip_to_slug(project_id)

    if slug in ALIASES:
        return ALIASES[slug]

    trilha = str(labels.get("trilha") or "").lower()
    projeto_label = str(labels.get("projeto") or "").lower()
    if projeto_label in ("landing-zone-gcp", "landing-zone") and trilha in TRILHA_MAP:
        return TRILHA_MAP[trilha]
    if trilha in TRILHA_MAP and slug in ("", "landing-zone-gcp", "landing-zone"):
        return TRILHA_MAP[trilha]

    titled = _title_slug(slug)
    return titled or raw or project_id
