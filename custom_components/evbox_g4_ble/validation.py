"""Validation engine types matching Home Assistant's runtime compatibility alias.

Home Assistant 2026.10 aliases voluptuous to probatio during package import.
Earlier supported releases use voluptuous. Keep that runtime import so both
work, and check the engine types against the stable HA version used by CI.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

__all__ = ["vol"]
