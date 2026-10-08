"""Data coordinator for EVBox Gen4 BLE."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, TypedDict
from collections.abc import Callable, Coroutine
from typing import Concatenate, ParamSpec, TypeVar

if TYPE_CHECKING:
    from .models import EVBoxConfigEntry
from functools import wraps
from time import monotonic
from datetime import datetime, timezone

from homeassistant.exceptions import ConfigEntryAuthFailed
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import EVBoxAuthError, EVBoxClient, EVBoxConnectionError
from .errors import async_device_errors
from .const import (
    DOMAIN,
    KEY_BOOT_INFO,
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
from .protocol import (
    card_list,
    current_to_amperes,
    led_configuration,
    rf_modules,
    boot_information,
)

_LOGGER = logging.getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


class Health(TypedDict):
    last_success: str | None
    duration_seconds: float | None
    consecutive_failures: int
    last_error: str | None


def serialized(
    method: Callable[Concatenate[EVBoxCoordinator, P], Coroutine[Any, Any, R]],
) -> Callable[Concatenate[EVBoxCoordinator, P], Coroutine[Any, Any, R]]:
    """Keep read/modify/write operations together across BLE sessions."""

    @wraps(method)
    async def wrapped(
        self: EVBoxCoordinator, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        async with async_device_errors(self), self.client.transaction():
            return await method(self, *args, **kwargs)

    return wrapped


class EVBoxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll configuration exposed by the EVBox Connect app."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EVBoxClient,
        device_name: str = "EVBox G4",
        *,
        config_entry: EVBoxConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="EVBox G4",
            config_entry=config_entry,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.config_entry = config_entry
        self.client = client
        self.device_name = device_name
        self.health: Health = {
            "last_success": None,
            "duration_seconds": None,
            "consecutive_failures": 0,
            "last_error": None,
        }
        self.capabilities: set[str] = set()
        self._full_refresh_requested = True
        self._last_full_refresh: float | None = None
        self._reset_pending = False
        self._reset_disconnected = False
        self._restart_firmware = None
        if config_entry is not None and hasattr(config_entry, "data"):
            self._restart_firmware = config_entry.data.get("restart_firmware")
        self._restart_required = bool(
            config_entry is not None
            and getattr(config_entry, "data", {}).get("restart_required")
        )

    async def async_request_refresh(self) -> None:
        """Explicit refreshes include the less frequently polled configuration."""
        self._full_refresh_requested = True
        await super().async_request_refresh()

    def _sync_restart_issue(self) -> None:
        """Keep the repair and persisted pending marker in step with device state."""
        entry = self.config_entry
        if entry is None or not hasattr(self.hass, "config_entries"):
            return
        updates = {
            **entry.data,
            "restart_required": self._restart_required,
            "restart_firmware": self._restart_firmware,
        }
        if updates != entry.data:
            self.hass.config_entries.async_update_entry(entry, data=updates)
        issue_id = f"{entry.entry_id}_restart_required"
        if self._restart_required:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="restart_required",
                translation_placeholders={"name": self.device_name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _async_update_data(self) -> dict[str, Any]:
        started = monotonic()
        full = (
            self._full_refresh_requested
            or self._last_full_refresh is None
            or started - self._last_full_refresh >= 1800
        )
        # Consume this request now; a refresh requested while BLE is in flight
        # must remain pending for the next scheduled/debounced read.
        self._full_refresh_requested = False
        previous = getattr(self, "data", None)
        previous = previous if isinstance(previous, dict) else {}
        try:
            config, diagnostics = await self.client.get_snapshot(
                SCALAR_KEYS if full else ()
            )
            status, network, leds, cards, connection_info = diagnostics
            self.capabilities.update(config)
            data = (
                {**config}
                if full
                else {key: previous[key] for key in SCALAR_KEYS if key in previous}
            )
            data["rf_modules_parsed"] = rf_modules(data.get(KEY_RF_MODULES))
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
            firmware = boot_information(data.get(KEY_BOOT_INFO)).get("firmware_version")
            # Acknowledging Reset is not proof that the charger rebooted. Require
            # an observed outage followed by a successful authenticated read, or
            # a changed firmware version, which itself requires a restart.
            pending = self._restart_required or bool(previous.get("restart_required"))
            confirmed = (self._reset_pending and self._reset_disconnected) or (
                self._restart_firmware
                and firmware
                and firmware != self._restart_firmware
            )
            self._restart_required = bool(pending and not confirmed)
            if pending or "restart_required" in previous:
                data["restart_required"] = self._restart_required
            if confirmed:
                self._reset_pending = self._reset_disconnected = False
                self._restart_firmware = None
            self._sync_restart_issue()
            self.capabilities.update(key for key in data if key != "restart_required")
            self.health.update(
                {
                    "last_success": datetime.now(timezone.utc).isoformat(),
                    "consecutive_failures": 0,
                    "last_error": None,
                }
            )
            if full:
                self._last_full_refresh = monotonic()
            return data
        except Exception as err:
            if full:
                self._full_refresh_requested = True
            self.health["consecutive_failures"] += 1
            self.health["last_error"] = type(err).__name__
            if (
                self._reset_pending
                and isinstance(err, EVBoxConnectionError)
                and not isinstance(err, EVBoxAuthError)
            ):
                self._reset_disconnected = True
            if isinstance(err, EVBoxAuthError):
                raise ConfigEntryAuthFailed(
                    translation_domain="evbox_g4_ble", translation_key="invalid_auth"
                ) from err
            raise UpdateFailed(f"BLE update failed ({type(err).__name__})") from err
        finally:
            self.health["duration_seconds"] = round(monotonic() - started, 3)

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
            self._restart_required = True
            self._restart_firmware = boot_information(self.data.get(KEY_BOOT_INFO)).get(
                "firmware_version"
            )
            self._reset_pending = self._reset_disconnected = False
            self._sync_restart_issue()
            self.async_set_updated_data({**self.data, "restart_required": True})

    def note_restart_sent(self) -> None:
        """Wait for an observed outage and return before clearing the marker."""
        self._reset_pending = True
        self._reset_disconnected = False
        self._full_refresh_requested = True

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

    @serialized
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
    async def async_update_rf_modules(
        self, *, add: tuple[str, str] | None = None, remove_id: str | None = None
    ) -> None:
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
