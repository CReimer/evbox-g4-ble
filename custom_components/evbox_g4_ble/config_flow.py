"""Config flow for EVBox Gen4 BLE."""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from .validation import vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import selector

from .models import EVBoxConfigEntry
from .coordinator import EVBoxCoordinator
from .client import EVBoxAuthError, EVBoxClient
from .const import (
    CONF_SECURITY_CODE,
    DOMAIN,
    KEY_APN_NAME,
    KEY_SERVER_URL,
    KEY_RF_MODULES,
    KEY_TRIGGER,
    KEY_USE_BACKEND,
    SERVICE_UUID,
    ESP32_SERVICE_UUID,
    APN_MAX_LENGTH,
    ASCII_NO_WHITESPACE_PATTERN,
    RFID_ID_PATTERN,
    SERVER_URL_MAX_LENGTH,
    SERVER_URL_PATTERN,
    SATELLITE_ID_PATTERN,
)
from .firmware_proxy import async_start_firmware_update
from .protocol import (
    valid_internet_connection,
    wifi_scan_networks,
    wifi_status,
)

_LOGGER = logging.getLogger(__name__)


def _enabled(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _valid_text(
    value: Any,
    pattern: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> bool:
    """Validate submitted text without exposing unserializable validators."""
    if not isinstance(value, str) or len(value) < minimum:
        return False
    if maximum is not None and len(value) > maximum:
        return False
    return re.fullmatch(pattern, value) is not None


def _text(*, password: bool = False) -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(
            type=(
                selector.TextSelectorType.PASSWORD
                if password
                else selector.TextSelectorType.TEXT
            )
        )
    )


class EVBoxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up an EVBox Gen4 station from discovery or a manual address."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name = "EVBox G4"

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: EVBoxConfigEntry,
    ) -> EVBoxOptionsFlow:
        """Expose app-like configuration dialogs from the integration page."""
        return EVBoxOptionsFlow()

    @callback
    def _existing_unique_id(self, address: str) -> str:
        """Match older mixed-case addresses without changing existing identity."""
        for entry in self._async_current_entries(include_ignore=True):
            if str(entry.data.get(CONF_ADDRESS, "")).strip().upper() == address:
                return entry.unique_id or address
        return address

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        supported_services = {SERVICE_UUID.lower(), ESP32_SERVICE_UUID.lower()}
        if not supported_services.intersection(
            uuid.lower() for uuid in discovery_info.service_uuids
        ):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address.upper()
        self._name = discovery_info.name or self._name
        await self.async_set_unique_id(self._existing_unique_id(self._address))
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_user()

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Ask for a new code after the charger rejects authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return await self._async_update_security_code("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Allow changing the code without replacing device or entity IDs."""
        return await self._async_update_security_code("reconfigure", user_input)

    async def _async_update_security_code(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> config_entries.ConfigFlowResult:
        entry = (
            self._get_reauth_entry()
            if step_id == "reauth_confirm"
            else self._get_reconfigure_entry()
        )
        errors = {}
        if user_input is not None:
            client = EVBoxClient(
                self.hass, entry.data[CONF_ADDRESS], user_input[CONF_SECURITY_CODE]
            )
            try:
                await client.evb("evbBTShow")
            except EVBoxAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_SECURITY_CODE: user_input[CONF_SECURITY_CODE]},
                    reason=(
                        "reauth_successful"
                        if step_id == "reauth_confirm"
                        else "reconfigure_successful"
                    ),
                )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {vol.Required(CONF_SECURITY_CODE): _text(password=True)}
            ),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input.get(CONF_ADDRESS, self._address)
            assert address is not None
            address = address.strip().upper()
            # Manual setup may race with Bluetooth discovery for the same
            # charger. Let this flow continue; configured entries are still
            # rejected by _abort_if_unique_id_configured below.
            await self.async_set_unique_id(
                self._existing_unique_id(address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            client = EVBoxClient(self.hass, address, user_input[CONF_SECURITY_CODE])
            try:
                await client.evb("evbBTShow")
            except EVBoxAuthError:
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.warning(
                    "EVBox Gen4 connection validation failed for %s: %s",
                    address,
                    type(err).__name__,
                    exc_info=True,
                )
                errors["base"] = "cannot_connect"
            else:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, address, connectable=True
                )
                if device is not None and device.name:
                    self._name = device.name
                return self.async_create_entry(
                    title=self._name,
                    data={
                        CONF_ADDRESS: address,
                        CONF_SECURITY_CODE: user_input[CONF_SECURITY_CODE],
                    },
                )
        schema_fields: dict[Any, Any] = {}
        if self._address is None:
            schema_fields[vol.Required(CONF_ADDRESS)] = _text()
        schema_fields[vol.Required(CONF_SECURITY_CODE)] = _text(password=True)
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema_fields), errors=errors
        )


class EVBoxOptionsFlow(config_entries.OptionsFlow):
    """App-like setup actions that need forms instead of simple entities."""

    config_entry: EVBoxConfigEntry

    def __init__(self) -> None:
        self._wifi_networks: list[dict[str, Any]] = []
        self._satellite_scan_results: list[dict[str, Any]] = []

    @property
    def coordinator(self) -> EVBoxCoordinator:
        return self.config_entry.runtime_data

    async def _finish(self, *, refresh: bool = True) -> config_entries.ConfigFlowResult:
        if refresh:
            await self.coordinator.async_request_refresh()
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    async def _finish_wifi(
        self, response: Any
    ) -> tuple[config_entries.ConfigFlowResult | None, str | None]:
        """Handle the connection result returned by evbWifiSet like the app."""
        result = wifi_status(response)
        status = result["status"]
        if status == "wrong_password":
            return None, "wrong_wifi_password"
        if status in ("disconnected", "unknown"):
            return None, "wifi_connection_failed"
        # Connecting is a valid result. Do not reconnect immediately: changing
        # the station's Wi-Fi can temporarily interrupt its radios. Preserve
        # the returned status until the normal coordinator refresh confirms it.
        self.coordinator.async_set_updated_data(
            {**self.coordinator.data, "wifi_status": response}
        )
        return await self._finish(refresh=False), None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        menu_options = []
        if "cards" in self.coordinator.data:
            menu_options.append("rfid")
        # EVBox Connect offers Wi-Fi whenever online/backend operation is
        # enabled. An empty status is normal before the first network is set
        # and must not hide the configuration itself.
        if _enabled(self.coordinator.data.get(KEY_USE_BACKEND)):
            menu_options.append("wifi")
        if KEY_USE_BACKEND in self.coordinator.data:
            menu_options.append("backend")
        if KEY_APN_NAME in self.coordinator.data:
            menu_options.append("apn")
        if KEY_RF_MODULES in self.coordinator.data:
            menu_options.append("satellites")
        # The app keeps UpdateFirmware available even when old firmware cannot
        # return BootInfo; it then shows an unknown current version. A manually
        # verified URL remains usable in that case as well.
        menu_options.append("firmware")
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="wifi",
            menu_options=["wifi_connect", "wifi_manual", "wifi_clear"],
        )

    async def async_step_wifi_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                selected = self._wifi_networks[int(user_input["network"])]
                password = user_input.get("password") or None
                secured = bool(selected.get("authentication"))
                if secured and not password:
                    errors["password"] = "password_required"
                else:
                    auth = {
                        "type": "WPA/WPA2 PSK" if secured else "Open",
                        "wepKeys": None,
                        "pskPassphrase": password,
                        "eapPassword": None,
                        "eapUser": None,
                        "epaDomain": None,
                    }
                    response = await self.coordinator.client.set_wifi(
                        (
                            selected["ssid"],
                            selected.get("mac_address"),
                            auth,
                            None,
                            None,
                        )
                    )
                    result, error = await self._finish_wifi(response)
                    if result is not None:
                        return result
                    errors["password" if error == "wrong_wifi_password" else "base"] = (
                        cast(str, error)
                    )
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not configure EVBox Wi-Fi")
                errors["base"] = "cannot_connect"
        if not self._wifi_networks:
            try:
                self._wifi_networks = wifi_scan_networks(
                    await self.coordinator.client.evb("evbWifiScan")
                )
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not scan Wi-Fi networks through EVBox")
                errors["base"] = "wifi_scan_failed"
        if not self._wifi_networks:
            return self.async_show_form(
                step_id="wifi_connect",
                errors=errors or {"base": "no_wifi_networks"},
            )
        choices = {
            str(index): f"{network['ssid']} ({network.get('signal_strength', '?')} dBm)"
            for index, network in enumerate(self._wifi_networks)
        }
        return self.async_show_form(
            step_id="wifi_connect",
            data_schema=vol.Schema(
                {
                    vol.Required("network"): vol.In(choices),
                    vol.Optional("password", default=""): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_wifi_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                password = user_input.get("password") or None
                secured = user_input.get("security") == "wpa"
                if secured and not password:
                    errors["password"] = "password_required"
                else:
                    auth = {
                        "type": "WPA/WPA2 PSK" if secured else "Open",
                        "wepKeys": None,
                        "pskPassphrase": password,
                        "eapPassword": None,
                        "eapUser": None,
                        "epaDomain": None,
                    }
                    response = await self.coordinator.client.set_wifi(
                        (user_input["ssid"].strip(), None, auth, None, None)
                    )
                    result, error = await self._finish_wifi(response)
                    if result is not None:
                        return result
                    errors["password" if error == "wrong_wifi_password" else "base"] = (
                        cast(str, error)
                    )
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not manually configure EVBox Wi-Fi")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="wifi_manual",
            data_schema=vol.Schema(
                {
                    vol.Required("ssid"): str,
                    vol.Required("security", default="wpa"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["wpa", "open"], translation_key="wifi_security"
                        )
                    ),
                    vol.Optional("password", default=""): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_wifi_clear(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input and user_input.get("confirm"):
            try:
                await self.coordinator.client.evb("evbWifiClear")
                return await self._finish()
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                return self.async_show_form(
                    step_id="wifi_clear", errors={"base": "invalid_auth"}
                )
            except Exception:
                _LOGGER.exception("Could not clear EVBox Wi-Fi configuration")
                return self.async_show_form(
                    step_id="wifi_clear", errors={"base": "cannot_connect"}
                )
        return self.async_show_form(
            step_id="wifi_clear",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
        )

    async def async_step_rfid(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="rfid", menu_options=["rfid_add", "rfid_remove", "rfid_clear"]
        )

    async def _send_card(self, id_tag: str, status: str) -> None:
        if status != "Accepted":
            raise ValueError("Only accepted cards can be stored")
        await self.coordinator.async_add_card(id_tag)

    async def _live_card_ids(self) -> list[str]:
        """Read the authoritative card list from the charger."""
        return await self.coordinator.async_card_ids()

    async def async_step_rfid_add(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            id_tag = user_input["id_tag"].strip().upper()
            if not _valid_text(id_tag, RFID_ID_PATTERN):
                errors["id_tag"] = "invalid_rfid_id"
            else:
                try:
                    if id_tag in await self._live_card_ids():
                        errors["id_tag"] = "rfid_card_exists"
                    else:
                        await self._send_card(id_tag, "Accepted")
                        return await self._finish()
                except (EVBoxAuthError, ConfigEntryAuthFailed):
                    self.config_entry.async_start_reauth_if_available(self.hass)
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("Could not add EVBox RFID card")
                    errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="rfid_add",
            data_schema=vol.Schema({vol.Required("id_tag"): _text()}),
            errors=errors,
        )

    async def async_step_rfid_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        cards = self.coordinator.data.get("cards", [])
        ids = [
            str(card.get("id_tag") or card.get("idTag"))
            for card in cards
            if card.get("id_tag") or card.get("idTag")
        ]
        if user_input is not None:
            try:
                await self.coordinator.async_remove_card(user_input["id_tag"])
                return await self._finish()
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not remove EVBox RFID card")
                errors["base"] = "cannot_connect"
        if not ids:
            return self.async_show_form(
                step_id="rfid_remove", errors={"base": "no_rfid_cards"}
            )
        return self.async_show_form(
            step_id="rfid_remove",
            data_schema=vol.Schema({vol.Required("id_tag"): vol.In(ids)}),
            errors=errors,
        )

    async def async_step_rfid_clear(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input and user_input.get("confirm"):
            try:
                await self.coordinator.async_clear_cards()
                return await self._finish()
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                return self.async_show_form(
                    step_id="rfid_clear", errors={"base": "invalid_auth"}
                )
            except Exception:
                _LOGGER.exception("Could not clear EVBox RFID cards")
                return self.async_show_form(
                    step_id="rfid_clear", errors={"base": "cannot_connect"}
                )
        return self.async_show_form(
            step_id="rfid_clear",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
        )

    async def async_step_apn(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            fields = {
                "apn": user_input["apn"],
                "username": user_input.get("username", ""),
                "password": user_input.get("password", ""),
            }
            for name, value in fields.items():
                if not _valid_text(
                    value,
                    ASCII_NO_WHITESPACE_PATTERN,
                    minimum=1 if name == "apn" else 0,
                    maximum=APN_MAX_LENGTH,
                ):
                    errors[name] = "invalid_apn_value"
            if not errors:
                try:
                    await self.coordinator.async_set_apn(
                        fields["apn"], fields["username"], fields["password"]
                    )
                    return await self._finish()
                except (EVBoxAuthError, ConfigEntryAuthFailed):
                    self.config_entry.async_start_reauth_if_available(self.hass)
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("Could not configure EVBox APN")
                    errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="apn",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "apn", default=str(self.coordinator.data.get("evb_APNName", ""))
                    ): _text(),
                    vol.Optional(
                        "username",
                        default=str(self.coordinator.data.get("evb_APNUser", "")),
                    ): _text(),
                    vol.Optional("password", default=""): _text(password=True),
                }
            ),
            errors=errors,
        )

    async def async_step_backend(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input.get("url")
            if url is not None and not _valid_text(
                url,
                SERVER_URL_PATTERN,
                minimum=1,
                maximum=SERVER_URL_MAX_LENGTH,
            ):
                errors["url"] = "invalid_backend_url"
            if not errors:
                try:
                    await self.coordinator.async_set_backend(user_input["online"], url)
                    return await self._finish()
                except (EVBoxAuthError, ConfigEntryAuthFailed):
                    self.config_entry.async_start_reauth_if_available(self.hass)
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("Could not configure EVBox backend")
                    errors["base"] = "cannot_connect"
        online = self.coordinator.data.get(KEY_USE_BACKEND)
        online = online is True or str(online).lower() == "true"
        fields: dict[Any, Any] = {
            vol.Required("online", default=online): bool,
        }
        if KEY_SERVER_URL in self.coordinator.data:
            fields[
                vol.Required(
                    "url", default=str(self.coordinator.data.get(KEY_SERVER_URL, ""))
                )
            ] = _text()
        return self.async_show_form(
            step_id="backend",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_satellites(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="satellites",
            menu_options=[
                "satellite_scan",
                "satellite_pair",
                "satellite_unpair",
                "satellite_identify",
            ],
        )

    async def async_step_satellite_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._satellite_scan_results = (
                    await self.coordinator.client.scan_satellites(user_input["timeout"])
                )
                return await self.async_step_satellite_scan_results()
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not scan for EVBox satellites")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="satellite_scan",
            data_schema=vol.Schema(
                {
                    vol.Required("timeout", default=40): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=120)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_satellite_scan_results(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        choices = {
            str(index): (
                f"{item['type']} {item['id']} ({item.get('signal_strength', '?')} dBm)"
            )
            for index, item in enumerate(self._satellite_scan_results)
        }
        if not choices:
            return self.async_show_form(
                step_id="satellite_scan_results",
                errors={"base": "no_satellites_found"},
            )
        if user_input is not None:
            try:
                item = self._satellite_scan_results[int(user_input["satellite"])]
                await self.coordinator.async_update_rf_modules(
                    add=(str(item["type"]), str(item["id"]))
                )
                return await self._finish()
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except HomeAssistantError as err:
                if err.translation_key == "max_satellites":
                    errors["base"] = "max_satellites"
                else:
                    raise
            except Exception:
                _LOGGER.exception("Could not pair scanned EVBox satellite")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="satellite_scan_results",
            data_schema=vol.Schema({vol.Required("satellite"): vol.In(choices)}),
            errors=errors,
        )

    async def async_step_satellite_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            satellite_id = user_input["satellite_id"].strip()
            if not _valid_text(satellite_id, SATELLITE_ID_PATTERN):
                errors["satellite_id"] = "invalid_satellite_id"
            else:
                try:
                    await self.coordinator.async_update_rf_modules(
                        add=("ChargeBox", satellite_id)
                    )
                    return await self._finish()
                except (EVBoxAuthError, ConfigEntryAuthFailed):
                    self.config_entry.async_start_reauth_if_available(self.hass)
                    errors["base"] = "invalid_auth"
                except HomeAssistantError as err:
                    if err.translation_key == "max_satellites":
                        errors["base"] = "max_satellites"
                    else:
                        raise
                except Exception:
                    _LOGGER.exception("Could not pair EVBox satellite")
                    errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="satellite_pair",
            data_schema=vol.Schema({vol.Required("satellite_id"): _text()}),
            errors=errors,
        )

    async def async_step_satellite_unpair(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        items = self.coordinator.data.get("rf_modules_parsed", [])
        choices = {
            str(item["id"]): f"{item.get('type', 'ChargeBox')} {item['id']}"
            for item in items
            if item.get("id")
        }
        if not choices:
            return self.async_show_form(
                step_id="satellite_unpair", errors={"base": "no_paired_satellites"}
            )
        if user_input is not None:
            try:
                await self.coordinator.async_update_rf_modules(
                    remove_id=user_input["satellite_id"]
                )
                return await self._finish()
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not unpair EVBox satellite")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="satellite_unpair",
            data_schema=vol.Schema({vol.Required("satellite_id"): vol.In(choices)}),
            errors=errors,
        )

    async def async_step_satellite_identify(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        items = self.coordinator.data.get("rf_modules_parsed", [])
        choices = {
            f"{item['type']}.{item['id']}": f"{item['type']} {item['id']}"
            for item in items
            if item.get("type") and item.get("id")
        }
        if not choices:
            return self.async_show_form(
                step_id="satellite_identify",
                errors={"base": "no_paired_satellites"},
            )
        if user_input is not None:
            try:
                satellite_type, satellite_id = user_input["satellite"].split(".", 1)
                value = f"RFShow,{satellite_type},{satellite_id}"
                await self.coordinator.client.set_configuration(KEY_TRIGGER, value)
                return await self._finish(refresh=False)
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Could not identify EVBox satellite")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="satellite_identify",
            data_schema=vol.Schema({vol.Required("satellite"): vol.In(choices)}),
            errors=errors,
        )

    async def async_step_firmware(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if not valid_internet_connection(
            self.coordinator.data.get("connection_info"),
            self.coordinator.data.get("wifi_status"),
        ):
            return self.async_show_form(
                step_id="firmware",
                data_schema=vol.Schema({}),
                errors={"base": "no_internet"},
            )
        if user_input is not None and user_input.get("confirm"):
            try:
                await async_start_firmware_update(
                    self.hass,
                    self.coordinator,
                    user_input["url"],
                )
                return await self._finish(refresh=False)
            except (EVBoxAuthError, ConfigEntryAuthFailed):
                self.config_entry.async_start_reauth_if_available(self.hass)
                errors["base"] = "invalid_auth"
            except HomeAssistantError as err:
                errors["base"] = err.translation_key or "cannot_connect"
            except Exception:
                _LOGGER.exception("Could not start EVBox firmware update")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="firmware",
            data_schema=vol.Schema(
                {
                    vol.Required("url"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                    ),
                    vol.Required("confirm", default=False): bool,
                }
            ),
            errors=errors,
        )
