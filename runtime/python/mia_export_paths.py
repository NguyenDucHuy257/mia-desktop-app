"""Canonical user-facing directories for every desktop export."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


_DIRECTION_LABELS = {
    "purchase": "Mua vào",
    "sold": "Bán ra",
}


def safe_tax_code(value: object) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "")).strip("._-")
    return cleaned or "MIA"


def export_direction_label(directions: str | Iterable[str] | None) -> str:
    if isinstance(directions, str):
        values = {directions}
    else:
        values = {str(value) for value in (directions or ())}
    if values == {"purchase"}:
        return _DIRECTION_LABELS["purchase"]
    if values == {"sold"}:
        return _DIRECTION_LABELS["sold"]
    return "Mua vào & Bán ra"


def direction_export_directory(
    destination: str | Path,
    tax_code: object,
    directions: str | Iterable[str] | None,
) -> Path:
    return Path(destination) / safe_tax_code(tax_code) / export_direction_label(directions)


def artifact_export_directory(
    destination: str | Path,
    tax_code: object,
    directions: str | Iterable[str] | None,
    kind: str,
    date_from: str,
    date_to: str,
) -> Path:
    normalized_kind = str(kind).lower()
    if normalized_kind not in {"xml", "html", "pdf"}:
        raise ValueError("invalid_artifact_kind")
    return direction_export_directory(destination, tax_code, directions) / (
        f"{normalized_kind.upper()} {date_from}_{date_to}"
    )
