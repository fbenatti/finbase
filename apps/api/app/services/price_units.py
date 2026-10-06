"""Conservative unit normalization for cloud prices and Azure Price Sheet."""
from __future__ import annotations

import re
import unicodedata


def normalize_price_unit(value: object) -> str:
    """Canonicalize formatting only; never infer a dimensional conversion."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("per ", "/")
    return text


def price_unit_factor(source_unit: object, target_unit: object) -> float | None:
    """Return a safe conversion factor, or None when units are not equivalent.

    Price Sheet ingestion currently has no approved dimensional conversion
    table. Exact normalized units are safe; silently treating (for example)
    hours and 10K operations as equivalent is not.
    """
    source = normalize_price_unit(source_unit)
    target = normalize_price_unit(target_unit)
    if source and source == target:
        return 1.0
    return None
