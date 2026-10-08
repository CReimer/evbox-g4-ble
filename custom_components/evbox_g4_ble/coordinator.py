"""Data coordinator for EVBox Gen4 BLE."""

from __future__ import annotations

from typing import Any
from functools import wraps

from homeassistant.exceptions import ConfigEntryAuthFailed
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import EVBoxAuthError, EVBoxClient
from .const import (
    KEY_APN_NAME,
    KEY_APN_PASS,
    KEY_APN_USER,
    KEY_AUTO_START,
    KEY_MAX_CURRENT,
    KEY_MIN_CURRENT,
    KEY_RF_MODULES,
    KEY_SERVER_URL,
    LED_END_TIME,
    LED_LEVEL,
    LED_MODE,
    LED_START_TIME,
    SCALAR_KEYS,
    MAX_SATELLITES,
    UPDATE_INTERVAL,
)
from .protocol import card_list, current_to_amperes, led_configuration, rf_modules

_LOGGER = logging.getLogger(__name__)


def serialized(method):
    """Keep read/modify/write operations together across BLE sessions."""

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self.client.transaction():
            try:
                return await method(self, *args, **kwargs)
            except EVBoxAuthError as err:
                if self.config_entry is not None:
                    self.config_entry.async_start_reauth_if_available(self.hass)
                raise ConfigEntryAuthFailed(
                    "invalid_auth",
                    translation_domain="evbox_g4_ble",
                    translation_key="invalid_auth",
                ) from err

    return wrapped


class EVBoxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll configuration exposed by the EVBox Connect app."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EVBoxClient,
        device_name: str = "EVBox G4",
        *,
        config_entry=None,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="EVBox G4",
            config_entry=config_entry,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.device_name = device_name

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            config, diagnostics = await self.client.get_snapshot(SCALAR_KEYS)
            status, network, leds, cards, connection_info = diagnostics
            data = {
                **config,
                "rf_modules_parsed": rf_modules(config.get("evb_RFModules")),
            }
            if isinstance(self.data, dict) and self.data.get("restart_required"):
                data["restart_required"] = True
            if status is not None:
                data["wifi_status"] = status
            if network is not None:
                data["wifi_network"] = network
            if leds is not None:
                data["led_idle"] = leds
                data.update(led_configuration(leds))
            if cards is not None:
                data["cards"] = card_list(cards)
            if connection_info:
                data["connection_info"] = connection_info
            return data
        except EVBoxAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain="evbox_g4_ble", translation_key="invalid_auth"
            ) from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err

    @serialized
    async def async_set_configuration(self, key: str, value: Any) -> None:
        if key in (KEY_MIN_CURRENT, KEY_MAX_CURRENT):
            other_key = KEY_MAX_CURRENT if key == KEY_MIN_CURRENT else KEY_MIN_CURRENT
            stored = await self.client.get_configuration((other_key,))
            other = current_to_amperes(stored.get(other_key))
            requested = current_to_amperes(value)
            if other is not None and requested is not None:
                error_key = None
                if key == KEY_MIN_CURRENT and requested > other:
                    error_key = "minimum_above_maximum"
                elif key == KEY_MAX_CURRENT and requested < other:
                    error_key = "maximum_below_minimum"
                if error_key:
                    raise HomeAssistantError(
                        error_key,
                        translation_domain="evbox_g4_ble",
                        translation_key=error_key,
                    )
        result = await self.client.set_configuration(key, value)
        self.note_response(result)
        await self._async_verify_configuration(key, value)

    def note_response(self, response: Any) -> None:
        """Remember that an accepted command still needs a charger restart."""
        values = response if isinstance(response, list) else [response]
        if any(
            isinstance(value, dict)
            and str(value.get("status", "")).lower() == "rebootrequired"
            for value in values
        ):
            self.async_set_updated_data({**self.data, "restart_required": True})

    def note_restart_sent(self) -> None:
        """Clear the pending marker after the charger accepted a hard reset."""
        self.async_set_updated_data({**self.data, "restart_required": False})

    async def _async_verify_configuration(self, key: str, expected: Any) -> Any:
        """Read a written value back before exposing it as stored state."""
        values = await self._async_verify_configurations({key: expected})
        return values[key]

    async def _async_verify_configurations(
        self,
        expected: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify several stored values in one authenticated read session."""
        values = await self.client.get_configuration(expected)

        def normalized(item: Any) -> str:
            if item is True or str(item).strip().lower() == "true":
                return "true"
            if item is False or str(item).strip().lower() == "false":
                return "false"
            return str(item).strip()

        for key, requested in expected.items():
            if key not in values:
                raise HomeAssistantError(
                    "readback_missing",
                    translation_domain="evbox_g4_ble",
                    translation_key="readback_missing",
                    translation_placeholders={"key": key},
                )
            if normalized(values[key]) != normalized(requested):
                raise HomeAssistantError(
                    "readback_mismatch",
                    translation_domain="evbox_g4_ble",
                    translation_key="readback_mismatch",
                    translation_placeholders={"key": key},
                )
        self.async_set_updated_data(
            {**self.data, **{key: values[key] for key in expected}}
        )
        return values

    async def async_command(self, command: str, values: tuple[Any, ...] = ()) -> Any:
        result = await self.client.evb(command, values)
        await self.async_request_refresh()
        return result

    @serialized
    async def async_set_led(
        self,
        *,
        mode: str | None = None,
        level: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> None:
        """Set the app's idle LED controls while preserving schedule fields."""
        selected_mode = mode or str(self.data.get(LED_MODE, "On"))
        selected_level = (
            level if level is not None else int(self.data.get(LED_LEVEL, 25))
        )
        start = start_time or str(self.data.get(LED_START_TIME, "00:00:00Z"))
        end = end_time or str(self.data.get(LED_END_TIME, "23:59:59Z"))
        value = f"{selected_mode},{start},{end},{selected_level}"
        await self.client.evb("evbLEDsIdleSet", (value,))
        stored = await self.client.evb("evbLEDsIdleGet")
        parsed = led_configuration(stored)
        expected = {
            LED_MODE: selected_mode,
            LED_START_TIME: start,
            LED_END_TIME: end,
            LED_LEVEL: selected_level,
        }
        if not parsed or any(parsed.get(key) != item for key, item in expected.items()):
            raise HomeAssistantError(
                "led_mismatch",
                translation_domain="evbox_g4_ble",
                translation_key="led_mismatch",
            )
        self.async_set_updated_data({**self.data, "led_idle": stored, **parsed})

    @serialized
    async def async_set_rf_modules(self, value: str) -> None:
        """Store paired charge points and verify the semantic list."""
        await self.client.set_configuration(KEY_RF_MODULES, value)
        values = await self.client.get_configuration((KEY_RF_MODULES,))
        if KEY_RF_MODULES not in values:
            raise HomeAssistantError(
                "satellites_missing",
                translation_domain="evbox_g4_ble",
                translation_key="satellites_missing",
            )
        stored = values[KEY_RF_MODULES]

        def identities(raw: Any) -> list[tuple[str, str]]:
            return sorted(
                (str(item.get("type", "")), str(item.get("id", "")))
                for item in rf_modules(raw)
            )

        if identities(stored) != identities(value):
            raise HomeAssistantError(
                "satellites_mismatch",
                translation_domain="evbox_g4_ble",
                translation_key="satellites_mismatch",
            )
        self.async_set_updated_data(
            {
                **self.data,
                KEY_RF_MODULES: stored,
                "rf_modules_parsed": rf_modules(stored),
            }
        )

    @serialized
    async def async_update_rf_modules(self, *, add=None, remove_id=None) -> None:
        """Modify the authoritative list without losing another action's changes."""
        values = await self.client.get_configuration((KEY_RF_MODULES,))
        if KEY_RF_MODULES not in values:
            raise HomeAssistantError(
                "satellites_missing",
                translation_domain="evbox_g4_ble",
                translation_key="satellites_missing",
            )
        items = [
            (str(item["type"]), str(item["id"]))
            for item in rf_modules(values[KEY_RF_MODULES])
            if item.get("type") and item.get("id") and str(item["id"]) != remove_id
        ]
        if add is not None and add not in items:
            if len(items) >= MAX_SATELLITES:
                raise HomeAssistantError(
                    "max_satellites",
                    translation_domain="evbox_g4_ble",
                    translation_key="max_satellites",
                )
            items.append(add)
        await self.async_set_rf_modules(
            ",".join(f"{kind}.{identifier}" for kind, identifier in items)
        )

    async def async_card_ids(self) -> list[str]:
        """Read the authoritative card IDs stored in the charger."""
        value = await self.client.evb("evbWhiteListGet")
        return [
            str(card.get("id_tag") or card.get("idTag")).strip().upper()
            for card in card_list(value)
            if card.get("id_tag") or card.get("idTag")
        ]

    async def _async_replace_cards(self, id_tags: list[str]) -> Any:
        """Replace and read back the complete local authorization list."""
        expected = [str(id_tag).strip().upper() for id_tag in id_tags]
        version_payload = await self.client.ocpp("GetLocalListVersion", {})
        version = (
            int(version_payload.get("listVersion", 0))
            if isinstance(version_payload, dict)
            else 0
        )
        cards = [
            {"idTag": id_tag, "idTagInfo": {"status": "Accepted"}}
            for id_tag in expected
        ]
        result = await self.client.ocpp(
            "SendLocalList",
            {
                "listVersion": version + 1,
                "localAuthorizationList": cards,
                "updateType": "Full",
            },
        )
        stored = await self.async_card_ids()
        if sorted(stored) != sorted(expected):
            raise HomeAssistantError(
                "cards_mismatch",
                translation_domain="evbox_g4_ble",
                translation_key="cards_mismatch",
            )
        self.async_set_updated_data(
            {**self.data, "cards": [{"id_tag": item} for item in stored]}
        )
        return result

    @serialized
    async def async_add_card(self, id_tag: str) -> Any:
        """Add one card and verify that the charger stored it."""
        normalized = id_tag.strip().upper()
        existing = await self.async_card_ids()
        if normalized in existing:
            raise HomeAssistantError(
                "card_exists",
                translation_domain="evbox_g4_ble",
                translation_key="card_exists",
            )
        await self.client.set_configuration("LocalAuthListEnabled", True)
        version_payload = await self.client.ocpp("GetLocalListVersion", {})
        version = (
            int(version_payload.get("listVersion", 0))
            if isinstance(version_payload, dict)
            else 0
        )
        result = await self.client.ocpp(
            "SendLocalList",
            {
                "listVersion": version + 1,
                "localAuthorizationList": [
                    {"idTag": normalized, "idTagInfo": {"status": "Accepted"}}
                ],
                "updateType": "Differential",
            },
        )
        stored = await self.async_card_ids()
        if normalized not in stored:
            raise HomeAssistantError(
                "card_missing_readback",
                translation_domain="evbox_g4_ble",
                translation_key="card_missing_readback",
            )
        self.async_set_updated_data(
            {**self.data, "cards": [{"id_tag": item} for item in stored]}
        )
        return result

    @serialized
    async def async_remove_card(self, id_tag: str) -> Any:
        """Remove one existing card and verify the resulting complete list."""
        normalized = id_tag.strip().upper()
        existing = await self.async_card_ids()
        if normalized not in existing:
            raise HomeAssistantError(
                "card_not_found",
                translation_domain="evbox_g4_ble",
                translation_key="card_not_found",
            )
        return await self._async_replace_cards(
            [item for item in existing if item != normalized]
        )

    @serialized
    async def async_clear_cards(self) -> Any:
        """Clear and verify the complete local authorization list."""
        return await self._async_replace_cards([])

    @serialized
    async def async_set_server(self, url: str) -> None:
        """Write the backend URL and the app-derived hidden compatibility flags."""
        result = await self.client.set_server(url)
        self.note_response(result)
        await self._async_verify_configuration(KEY_SERVER_URL, url)

    @serialized
    async def async_set_backend(self, online: bool, url: str | None = None) -> None:
        """Keep the backend address and online switch in one operation."""
        if url is not None:
            await self.async_set_server(url)
        await self.async_set_configuration("evb_UseBackend", online)

    @serialized
    async def async_set_apn(
        self, apn: str, username: str = "", password: str = ""
    ) -> None:
        """Write and verify the complete app APN model without exposing its password."""
        expected = {
            KEY_APN_NAME: apn,
            KEY_APN_USER: username,
            KEY_APN_PASS: password,
        }
        result = await self.client.session(
            [
                ("ocpp", "ChangeConfiguration", {"key": key, "value": value})
                for key, value in expected.items()
            ]
        )
        self.note_response(result)
        await self._async_verify_configurations(
            {
                KEY_APN_NAME: apn,
                KEY_APN_USER: username,
            }
        )

    @serialized
    async def async_set_auto_start(self, value: str) -> None:
        """Set AutoStart with the app's hidden local-authorization prerequisite."""
        result = await self.client.set_auto_start(value)
        self.note_response(result)
        await self._async_verify_configuration(KEY_AUTO_START, value)
