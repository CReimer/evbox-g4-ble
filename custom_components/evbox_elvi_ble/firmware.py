"""Firmware release metadata for supported legacy EVBox product families."""

from __future__ import annotations

import re

# Verified against the live catalog used by EVBox Connect on 2026-09-01.
# The vendor feed requires a client-side SDK credential embedded in the mobile
# app. Do not copy that credential into this integration. Refresh this catalog
# with each integration release until EVBox publishes an unauthenticated feed.
CATALOG_CHECKED_AT = "2026-09-01"
LATEST_RELEASES = {
    "G4E-": "425v1",
    "G4X-": "425v1",
    "G4-": "425v1",
}


def installed_release(value: str | None) -> str | None:
    """Convert a full EVBox BootInfo firmware string to its app release name."""
    if not value:
        return None
    match = re.search(r"P0*(\d+).*?[vV](\d+)", value)
    if match is None:
        match = re.search(r"\b0*(\d+)[vV](\d+)\b", value)
    if match is None:
        return None
    return f"{int(match.group(1))}v{int(match.group(2))}"


def latest_release(model: str | None) -> str | None:
    """Return the verified EVBox Connect release for a BootInfo model."""
    if not model:
        return None
    normalized = model.upper()
    for prefix, version in LATEST_RELEASES.items():
        if normalized.startswith(prefix):
            return version
    return None
