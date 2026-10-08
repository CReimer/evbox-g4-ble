"""Diagnostics without device identifiers or raw compound protocol strings."""

from homeassistant.components.diagnostics import async_redact_data

from .models import EVBoxConfigEntry
from .const import SENSITIVE_FIELDS, KEY_BOOT_INFO, KEY_RF_MODULES
from .protocol import boot_information, rf_modules, wifi_network, wifi_status


async def async_get_config_entry_diagnostics(hass, entry: EVBoxConfigEntry):
    coordinator = entry.runtime_data
    data = dict(coordinator.data)
    for key, parser in (
        (KEY_BOOT_INFO, boot_information),
        (KEY_RF_MODULES, rf_modules),
        ("wifi_network", wifi_network),
        ("wifi_status", wifi_status),
    ):
        if key in data:
            data[key] = parser(data[key])
    return {
        "entry": async_redact_data(dict(entry.data), SENSITIVE_FIELDS),
        "last_update_success": coordinator.last_update_success,
        "health": dict(getattr(coordinator, "health", {})),
        "capabilities": sorted(getattr(coordinator, "capabilities", set())),
        "data": async_redact_data(data, SENSITIVE_FIELDS),
    }
