"""Firmware release metadata for supported legacy EVBox product families."""

from __future__ import annotations

import re
from urllib.parse import unquote

# Verified against the live catalog used by EVBox Connect on 2026-09-05.
# The vendor feed requires a client-side SDK credential embedded in the mobile
# app. Do not copy that credential into this integration. The static value is a
# fallback; Home Assistant checks the public EVBox document during operation.
CATALOG_CHECKED_AT = "2026-09-05"
FIRMWARE_ARTICLE_URL = (
    "https://help.evaftersales.com/s/article/"
    "What-is-the-latest-firmware-version-for-my-station?language=en_US"
)
# Salesforce ContentDocument URLs always serve the newest ContentVersion of a
# document. A one-byte range request is enough to read its current filename.
FIRMWARE_DOCUMENT_URL = (
    "https://help.evaftersales.com/sfc/servlet.shepherd/document/download/"
    "069bi00000SyLEWAA3"
)
LATEST_RELEASES = {
    "G4E-": "425v1",
    "G4X-": "425v1",
    "G4-": "425v1",
}


def release_from_filename(value: str | None) -> str | None:
    """Extract the EVBox app release from an official .evb filename."""
    if not value:
        return None
    decoded = unquote(value).rsplit("/", 1)[-1]
    match = re.search(r"P0*(\d+).*?[vV](\d+)\.evb$", decoded, re.IGNORECASE)
    if match is None:
        return None
    return f"{int(match.group(1))}v{int(match.group(2))}"


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
