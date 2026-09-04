"""Redacted diagnostics for EVBox Gen4 BLE."""

from homeassistant.components.diagnostics import async_redact_data

from .const import SENSITIVE_FIELDS


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), SENSITIVE_FIELDS),
        "last_update_success": coordinator.last_update_success,
        "data": async_redact_data(dict(coordinator.data), SENSITIVE_FIELDS),
    }
