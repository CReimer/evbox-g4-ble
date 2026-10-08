"""EVBox Gen4 BLE integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .client import EVBoxClient
from .const import (
    APN_MAX_LENGTH,
    ASCII_NO_WHITESPACE_PATTERN,
    CONF_ADDRESS,
    CONF_SECURITY_CODE,
    DOMAIN,
    KEY_APN_NAME,
    KEY_AUTO_START,
    KEY_CONNECTOR_LIST,
    KEY_METER_ADDRESS,
    KEY_PHASE_ROTATION,
    KEY_RF_MODULES,
    KEY_SERVER_URL,
    KEY_SERIAL_AS_CONNECTOR_ID,
    KEY_TRIGGER,
    KEY_USE_BACKEND,
    LED_LEVEL,
    LED_MODE,
    PLATFORMS,
    RFID_ID_PATTERN,
    SATELLITE_ID_PATTERN,
    SERVER_URL_MAX_LENGTH,
    SERVER_URL_PATTERN,
)
from .coordinator import EVBoxCoordinator
from .firmware_proxy import (
    async_cleanup_firmware_proxies,
    async_start_firmware_update,
)
from .protocol import (
    rf_modules,
    valid_internet_connection,
    wifi_scan_networks,
    wifi_status,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> EVBoxCoordinator:
    entry_id = call.data.get("entry_id")
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if len(entries) != 1 or entries[0].runtime_data is None:
        raise HomeAssistantError(
            "entry_required",
            translation_domain="evbox_g4_ble",
            translation_key="entry_required",
        )
    return entries[0].runtime_data


def _enabled(value: Any) -> bool:
    """Interpret the Boolean spelling used by GetConfiguration."""
    return value is True or str(value).lower() == "true"


def _require_capability(coordinator: EVBoxCoordinator, key: str, feature: str) -> None:
    """Reject an action that this charger's firmware did not advertise."""
    if key not in coordinator.data:
        raise HomeAssistantError(
            "unsupported_capability",
            translation_domain="evbox_g4_ble",
            translation_key="unsupported_capability",
        )


def _require_wifi(coordinator: EVBoxCoordinator) -> None:
    """Match the app: Wi-Fi is only usable in online/backend operation."""
    _require_capability(coordinator, KEY_USE_BACKEND, "WLAN-Konfiguration")
    if not _enabled(coordinator.data.get(KEY_USE_BACKEND)):
        raise HomeAssistantError(
            "wifi_backend_required",
            translation_domain="evbox_g4_ble",
            translation_key="wifi_backend_required",
        )


async def _handle_service(hass: HomeAssistant, call: ServiceCall) -> Any:
    coordinator = _coordinator(hass, call)
    client = coordinator.client
    data = call.data
    service = call.service
    if service == "refresh":
        await coordinator.async_request_refresh()
        return coordinator.data
    if service == "scan_wifi":
        _require_wifi(coordinator)
        return {"networks": wifi_scan_networks(await client.evb("evbWifiScan"))}
    if service == "set_wifi":
        _require_wifi(coordinator)
        password = data.get("password")
        auth = {
            "type": "WPA/WPA2 PSK" if password else "Open",
            "wepKeys": None,
            "pskPassphrase": password,
            "eapPassword": None,
            "eapUser": None,
            "epaDomain": None,
        }
        result = await client.set_wifi(
            (data["ssid"], data.get("mac_address"), auth, None, None)
        )
        details = wifi_status(result)
        status = details["status"]
        if status == "wrong_password":
            raise HomeAssistantError(
                "wrong_wifi_password",
                translation_domain="evbox_g4_ble",
                translation_key="wrong_wifi_password",
            )
        if status in ("disconnected", "unknown"):
            raise HomeAssistantError(
                "wifi_connection_failed",
                translation_domain="evbox_g4_ble",
                translation_key="wifi_connection_failed",
            )
        # Changing Wi-Fi can temporarily stop the Elvi's BLE and OCPP tasks.
        # Reconnecting immediately for a full refresh can leave legacy
        # firmware unresponsive until it is power-cycled.  The status
        # notification already confirms the write, so publish it directly and
        # let the normal update interval perform the next BLE connection.
        network = ",".join(
            (
                data["ssid"],
                data.get("mac_address") or details.get("mac_address", ""),
                "{" + auth["type"] + "}",
            )
        )
        coordinator.async_set_updated_data(
            {
                **coordinator.data,
                "wifi_status": result,
                "wifi_network": network,
                "connection_info": {},
                "restart_required": True,
            }
        )
        return {"result": result}
    elif service == "clear_wifi":
        _require_wifi(coordinator)
        result = await client.evb("evbWifiClear")
        updated = {
            **coordinator.data,
            "wifi_status": "0",
            "connection_info": {},
            "restart_required": True,
        }
        updated.pop("wifi_network", None)
        coordinator.async_set_updated_data(updated)
        return {"result": result}
    elif service == "set_apn":
        _require_capability(coordinator, KEY_APN_NAME, "Mobilfunk-APN")
        await coordinator.async_set_apn(
            data["apn"], data.get("username", ""), data.get("password", "")
        )
        result = True
    elif service == "set_server":
        _require_capability(coordinator, KEY_SERVER_URL, "Lade-Backend")
        await coordinator.async_set_server(data["url"])
        result = True
    elif service == "set_led_idle":
        _require_capability(coordinator, "led_idle", "LED-Ruhezustand")
        await coordinator.async_set_led(mode=data["mode"], level=data["level"])
        return {"result": True}
    elif service == "rfid_add":
        _require_capability(coordinator, "cards", "Ladekartenverwaltung")
        result = await coordinator.async_add_card(data["id_tag"])
    elif service == "rfid_remove":
        _require_capability(coordinator, "cards", "Ladekartenverwaltung")
        result = await coordinator.async_remove_card(data["id_tag"])
    elif service == "rfid_clear":
        _require_capability(coordinator, "cards", "Ladekartenverwaltung")
        result = await coordinator.async_clear_cards()
    elif service == "scan_satellites":
        _require_capability(coordinator, KEY_RF_MODULES, "Ladepunkt-Kopplung")
        return {"satellites": await client.scan_satellites(data.get("timeout", 40))}
    elif service == "pair_satellite":
        _require_capability(coordinator, KEY_RF_MODULES, "Ladepunkt-Kopplung")
        await coordinator.async_update_rf_modules(
            add=("ChargeBox", data["satellite_id"])
        )
        result = True
    elif service == "blink_satellite":
        _require_capability(coordinator, KEY_RF_MODULES, "Ladepunkt-Kopplung")
        satellite_id = data["satellite_id"]
        matches = [
            item
            for item in rf_modules(coordinator.data.get(KEY_RF_MODULES))
            if str(item.get("id")) == satellite_id and item.get("type")
        ]
        if len(matches) != 1:
            raise HomeAssistantError(
                "satellite_not_unique",
                translation_domain="evbox_g4_ble",
                translation_key="satellite_not_unique",
            )
        result = await client.set_configuration(
            KEY_TRIGGER, f"RFShow,{matches[0]['type']},{satellite_id}"
        )
    elif service == "connection_info":
        return {"connection_info": await client.connection_info()}
    elif service == "update_firmware":
        if not valid_internet_connection(
            coordinator.data.get("connection_info"),
            coordinator.data.get("wifi_status"),
        ):
            raise HomeAssistantError(
                "no_internet",
                translation_domain="evbox_g4_ble",
                translation_key="no_internet",
            )
        result, proxied = await async_start_firmware_update(
            hass, coordinator, data["url"]
        )
        await coordinator.async_request_refresh()
        return {"result": result, "proxied_via_ftp": proxied}
    elif service == "restart":
        result = await client.ocpp("Reset", {"type": "Hard"})
        coordinator.note_restart_sent()
        # The charger drops BLE while rebooting.  A normal refresh here would
        # race that shutdown and turn an accepted reset into a service error.
        return {"result": result}
    elif service == "identify":
        result = await client.evb("evbBTShow")
    else:
        raise HomeAssistantError(
            "unsupported_action",
            translation_domain="evbox_g4_ble",
            translation_key="unsupported_action",
            translation_placeholders={"service": service},
        )
    await coordinator.async_request_refresh()
    return {"result": result}


_APN_REQUIRED = vol.All(
    str,
    vol.Length(min=1, max=APN_MAX_LENGTH),
    vol.Match(ASCII_NO_WHITESPACE_PATTERN),
)
_APN_OPTIONAL = vol.All(
    str,
    vol.Length(max=APN_MAX_LENGTH),
    vol.Match(ASCII_NO_WHITESPACE_PATTERN),
)


SERVICE_SCHEMAS = {
    "refresh": vol.Schema({vol.Optional("entry_id"): str}),
    "scan_wifi": vol.Schema({vol.Optional("entry_id"): str}),
    "set_wifi": vol.Schema(
        {
            vol.Required("ssid"): str,
            vol.Optional("mac_address"): str,
            vol.Optional("password"): str,
            vol.Optional("entry_id"): str,
        }
    ),
    "clear_wifi": vol.Schema({vol.Optional("entry_id"): str}),
    "set_apn": vol.Schema(
        {
            vol.Required("apn"): _APN_REQUIRED,
            vol.Optional("username"): _APN_OPTIONAL,
            vol.Optional("password"): _APN_OPTIONAL,
            vol.Optional("entry_id"): str,
        }
    ),
    "set_server": vol.Schema(
        {
            vol.Required("url"): vol.All(
                str,
                vol.Length(min=1, max=SERVER_URL_MAX_LENGTH),
                vol.Match(SERVER_URL_PATTERN),
            ),
            vol.Optional("entry_id"): str,
        }
    ),
    "set_led_idle": vol.Schema(
        {
            vol.Required("mode"): vol.In(["Off", "On"]),
            vol.Required("level"): vol.All(vol.Coerce(int), vol.In([5, 25, 50, 75])),
            vol.Optional("entry_id"): str,
        }
    ),
    "rfid_add": vol.Schema(
        {
            vol.Required("id_tag"): vol.Match(RFID_ID_PATTERN),
            vol.Optional("entry_id"): str,
        }
    ),
    "rfid_remove": vol.Schema(
        {
            vol.Required("id_tag"): vol.Match(RFID_ID_PATTERN),
            vol.Optional("entry_id"): str,
        }
    ),
    "rfid_clear": vol.Schema({vol.Optional("entry_id"): str}),
    "scan_satellites": vol.Schema(
        {
            vol.Optional("timeout", default=40): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=120)
            ),
            vol.Optional("entry_id"): str,
        }
    ),
    "pair_satellite": vol.Schema(
        {
            vol.Required("satellite_id"): vol.Match(SATELLITE_ID_PATTERN),
            vol.Optional("entry_id"): str,
        }
    ),
    "blink_satellite": vol.Schema(
        {
            vol.Required("satellite_id"): vol.Match(SATELLITE_ID_PATTERN),
            vol.Optional("entry_id"): str,
        }
    ),
    "connection_info": vol.Schema({vol.Optional("entry_id"): str}),
    "update_firmware": vol.Schema(
        {vol.Required("url"): cv.url, vol.Optional("entry_id"): str}
    ),
    "restart": vol.Schema({vol.Optional("entry_id"): str}),
    "identify": vol.Schema({vol.Optional("entry_id"): str}),
}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def async_handle_service(call: ServiceCall) -> Any:
        return await _handle_service(hass, call)

    async def async_stop(_event) -> None:
        await async_cleanup_firmware_proxies(hass)

    for name, schema in SERVICE_SCHEMAS.items():
        hass.services.async_register(
            DOMAIN,
            name,
            async_handle_service,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    await async_cleanup_firmware_proxies(hass)
    hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP,
        async_stop,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = EVBoxClient(hass, entry.data[CONF_ADDRESS], entry.data[CONF_SECURITY_CODE])
    coordinator = EVBoxCoordinator(hass, client, entry.title, config_entry=entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # Version 0.2.0 exposed ConnectorPhaseRotation as a raw text entity. It is
    # now a constrained select for physical connector 1; remove the obsolete
    # registry entry before its replacement is set up.
    registry = er.async_get(hass)
    for old_domain, old_key in (
        ("text", KEY_PHASE_ROTATION),
        ("text", KEY_METER_ADDRESS),
        ("text", KEY_AUTO_START),
        ("switch", LED_MODE),
        ("number", LED_LEVEL),
        ("number", "evb_ServerPortOnChargeStation"),
        ("select", "evb_ServerOCPP"),
        ("switch", KEY_CONNECTOR_LIST),
        ("switch", KEY_SERIAL_AS_CONNECTOR_ID),
        # Values already represented by a configuration entity or used only
        # internally for capability detection must not remain as duplicates.
        ("sensor", "auto_start"),
        ("sensor", "server_url"),
        ("sensor", "server_ocpp"),
        ("sensor", "server_port"),
        ("sensor", "led_idle"),
        ("sensor", "meter_address"),
        ("sensor", "phase_rotation"),
        ("sensor", "ccid"),
        ("sensor", "ccid_ac"),
        ("sensor", "apn_name"),
        ("sensor", "apn_user"),
    ):
        old_entity_id = registry.async_get_entity_id(
            old_domain,
            DOMAIN,
            f"{entry.data[CONF_ADDRESS]}_{old_key}",
        )
        if old_entity_id is not None:
            registry.async_remove(old_entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
