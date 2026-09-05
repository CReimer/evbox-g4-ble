"""EVBox Elvi legacy BLE framing and OCPP 1.6 message helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
import json
import re
import time
from typing import Any


class EVBoxProtocolError(Exception):
    """Raised for malformed or rejected EVBox messages."""


class EVBoxCallError(EVBoxProtocolError):
    """Raised for an OCPP CALLERROR response."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def new_message_id() -> str:
    """Return the millisecond identifier used by EVBox Connect."""
    return str(time.time_ns() // 1_000_000)


def build_ocpp_call(action: str, payload: Mapping[str, Any], message_id: str | None = None) -> tuple[str, str]:
    """Build an OCPP-J CALL frame."""
    request_id = message_id or new_message_id()
    return request_id, _json([2, request_id, action, payload])


def build_ocpp_call_result(message_id: str, payload: Mapping[str, Any]) -> str:
    """Acknowledge a charger-initiated OCPP CALL using its message ID."""
    return _json([3, message_id, payload])


def backend_companion_values(url: str) -> dict[str, bool]:
    """Return the hidden server flags derived by the current EVBox Connect app."""
    everon = "everon" in url.lower()
    return {
        "evb_SerialAsConnectorId": everon,
        "evb_ConnectorList": everon,
    }


def firmware_update_payload(
    url: str, now: datetime | None = None
) -> dict[str, str | int]:
    """Build the exact UpdateFirmware payload emitted by EVBox Connect."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # The app deliberately uses a fixed year in its SimpleDateFormat pattern.
    retrieve_date = (
        current.strftime("2000-%m-%dT%H:%M:%S.")
        + f"{current.microsecond // 1000:03d}Z"
    )
    return {
        "location": url,
        "retries": 1,
        "retrieveDate": retrieve_date,
        "retryInterval": 60,
    }


def _csv_value(value: Any) -> str:
    """Mirror the app's deliberately simple JSON-to-CSV converter."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        return "{" + ",".join(_csv_value(item) for item in value.values()) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ",".join(_csv_value(item) for item in value) + "]"
    return str(value)


def build_data_transfer(
    command: str,
    values: Iterable[Any] = (),
    message_id: str | None = None,
) -> tuple[str, str]:
    """Build an EVBox DataTransfer call using the app's CSV payload."""
    request_id = message_id or new_message_id()
    payload = {
        "messageId": command,
        "vendorId": "EV-BOX",
        "data": ",".join(_csv_value(value) for value in values),
    }
    return request_id, _json([2, request_id, "DataTransfer", payload])


def frame_message(message: str) -> bytes:
    """Prefix a message with its decimal UTF-8 byte length."""
    encoded = message.encode("utf-8")
    return str(len(encoded)).encode("ascii") + encoded


def chunks(message: str, size: int = 20) -> list[bytes]:
    """Split one framed message into legacy GATT-sized chunks."""
    framed = frame_message(message)
    return [framed[pos : pos + size] for pos in range(0, len(framed), size)]


class FrameDecoder:
    """Reassemble length-prefixed messages from arbitrary BLE notifications."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes | bytearray) -> list[str]:
        self._buffer.extend(data)
        messages: list[str] = []
        while self._buffer:
            first_json = min(
                (idx for idx in (self._buffer.find(b"["), self._buffer.find(b"{")) if idx >= 0),
                default=-1,
            )
            if first_json < 1:
                if first_json == 0:
                    raise EVBoxProtocolError("Missing message length prefix")
                break
            prefix = bytes(self._buffer[:first_json])
            if not prefix.isdigit():
                raise EVBoxProtocolError("Invalid message length prefix")
            expected = int(prefix)
            end = first_json + expected
            if len(self._buffer) < end:
                break
            raw = bytes(self._buffer[first_json:end])
            del self._buffer[:end]
            try:
                messages.append(raw.decode("utf-8"))
            except UnicodeDecodeError as err:
                raise EVBoxProtocolError("Response is not valid UTF-8") from err
        return messages


@dataclass(frozen=True, slots=True)
class Response:
    """Parsed OCPP response."""

    message_id: str
    payload: Any


def parse_response(raw: str, expected_id: str | None = None) -> Response:
    """Parse OCPP CALLRESULT/CALLERROR and unwrap DataTransfer responses."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as err:
        raise EVBoxProtocolError("Response is not valid JSON") from err
    if not isinstance(value, list) or len(value) < 3:
        raise EVBoxProtocolError("Invalid OCPP response")
    message_type, message_id = value[0], str(value[1])
    if expected_id is not None and message_id != expected_id:
        raise EVBoxProtocolError("Response message id does not match request")
    if message_type == 4:
        raise EVBoxCallError(str(value[2]), str(value[3]) if len(value) > 3 else "")
    if message_type != 3:
        raise EVBoxProtocolError(f"Unexpected OCPP message type {message_type}")
    payload = value[2]
    if isinstance(payload, dict) and "status" in payload:
        status = payload.get("status")
        if status not in (
            None,
            True,
            "Accepted",
            "accepted",
            "RebootRequired",
            "rebootrequired",
        ):
            raise EVBoxProtocolError(f"EVBox command rejected: {status}")
    if isinstance(payload, dict) and "data" in payload:
        data = payload.get("data")
        if isinstance(data, str):
            stripped = data.strip()
            if stripped and stripped[0] in "[{\"" or stripped in ("true", "false", "null"):
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    pass
        payload = data
    return Response(message_id, payload)


def data_transfer_event_details(raw: str) -> tuple[str, Any, str] | None:
    """Return event name, payload and OCPP ID for an asynchronous call."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as err:
        raise EVBoxProtocolError("Event is not valid JSON") from err
    if (
        isinstance(value, list)
        and len(value) >= 4
        and value[0] == 2
        and value[2] == "DataTransfer"
        and isinstance(value[3], Mapping)
    ):
        marker = value[3].get("messageId")
        if not marker:
            return None
        data = value[3].get("data")
        if isinstance(data, str):
            stripped = data.strip()
            if stripped and stripped[0] in "[{\"" or stripped in ("true", "false", "null"):
                try:
                    return str(marker), json.loads(stripped), str(value[1])
                except json.JSONDecodeError:
                    pass
        return str(marker), data, str(value[1])
    return None


def data_transfer_event(raw: str) -> tuple[str, Any] | None:
    """Return an asynchronous EVBox DataTransfer event name and payload."""
    event = data_transfer_event_details(raw)
    return (event[0], event[1]) if event is not None else None


def parse_event_payload(raw: str, marker: str) -> Any:
    """Unwrap an asynchronous EVBox event or a marked CALLRESULT."""
    event = data_transfer_event(raw)
    if event is not None:
        if event[0] != marker:
            raise EVBoxProtocolError("DataTransfer event marker does not match")
        return event[1]
    return parse_response(raw).payload


def configuration_values(payload: Any) -> dict[str, Any]:
    """Extract OCPP GetConfiguration values by key."""
    if not isinstance(payload, Mapping):
        raise EVBoxProtocolError("GetConfiguration response is not an object")
    result: dict[str, Any] = {}
    for item in payload.get("configurationKey", []):
        if isinstance(item, Mapping) and "key" in item:
            result[str(item["key"])] = item.get("value")
    return result


def configuration_boolean(payload: Any, key: str) -> bool | None:
    """Read a Boolean GetConfiguration value without guessing missing keys."""
    values = configuration_values(payload)
    if key not in values:
        return None
    value = values[key]
    if value is True or str(value).lower() == "true":
        return True
    if value is False or str(value).lower() == "false":
        return False
    return None


def current_to_amperes(value: Any) -> float | None:
    """Convert the deciampere values used by EVBox Connect to amperes."""
    try:
        return float(value) / 10.0
    except (TypeError, ValueError):
        return None


def amperes_to_current(value: float) -> int:
    """Convert amperes to the integer deciampere wire value."""
    return round(value * 10)


def phase_rotation_value(value: Any, connector_id: str = "1") -> str | None:
    """Return one connector's phase rotation from the OCPP CSV value."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    for part in parts:
        prefix, separator, rotation = part.partition(".")
        if separator and prefix == connector_id:
            return rotation or None
    # Some firmware versions expose only the rotation without a connector id.
    if len(parts) == 1 and "." not in parts[0]:
        return parts[0]
    return None


def auto_start_configuration(value: Any) -> dict[str, Any]:
    """Interpret new card-id and legacy boolean AutoStart firmware values."""
    raw = "" if value is None else str(value)
    normalized = raw.strip().lower()
    if normalized in ("true", "false"):
        return {"mode": "automatic_start" if normalized == "true" else "rfid", "legacy": True}
    result: dict[str, Any] = {"mode": "automatic_start" if raw.strip() else "rfid", "legacy": False}
    if raw.strip() and raw.strip() != "999999":
        result["card_id"] = raw.strip()
    return result


def auto_start_value(value: Any, mode: str) -> str:
    """Build the app-compatible AutoStart value for the detected firmware."""
    current = auto_start_configuration(value)
    if current["legacy"]:
        return "true" if mode == "automatic_start" else "false"
    if mode == "rfid":
        return ""
    return str(current.get("card_id", "999999"))


def phase_rotation_configuration(value: Any, rotation: str, connector_id: str = "1") -> str:
    """Update one connector while preserving the complete OCPP CSV value."""
    if not isinstance(value, str) or not value.strip():
        return f"{connector_id}.{rotation}"
    parts = [part.strip() for part in value.split(",") if part.strip()]
    updated = False
    for index, part in enumerate(parts):
        prefix, separator, _ = part.partition(".")
        if separator and prefix == connector_id:
            parts[index] = f"{connector_id}.{rotation}"
            updated = True
            break
    if not updated:
        if len(parts) == 1 and "." not in parts[0]:
            return rotation
        parts.append(f"{connector_id}.{rotation}")
    return ",".join(parts)


def split_evb_csv(value: Any) -> list[str]:
    """Split EVBox CSV while keeping nested brace/list values together."""
    if not isinstance(value, str):
        return []
    result: list[str] = []
    current: list[str] = []
    depth = 0
    for character in value.strip():
        if character in "{[":
            depth += 1
        elif character in "}]" and depth:
            depth -= 1
        if character == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    result.append("".join(current).strip())
    return result


def boot_information(value: Any) -> dict[str, str]:
    """Parse the six fields used by EVBox Connect for boot information."""
    names = ("vendor", "model", "serial_number", "firmware_version", "iccid", "imsi")
    parts = split_evb_csv(value)
    return {name: parts[index] for index, name in enumerate(names) if index < len(parts) and parts[index]}


def wifi_status(value: Any) -> dict[str, Any]:
    """Parse the EVBox Connect Wi-Fi status response."""
    names = (
        "status_code",
        "ssid",
        "mac_address",
        "channel",
        "signal_strength",
        "ip_address",
        "subnet_mask",
        "gateway",
        "primary_dns",
        "secondary_dns",
        "ipv6_address",
    )
    parts = split_evb_csv(value)
    result: dict[str, Any] = {
        name: parts[index] for index, name in enumerate(names) if index < len(parts) and parts[index]
    }
    for name in ("channel", "signal_strength"):
        try:
            result[name] = int(result[name])
        except (KeyError, ValueError):
            pass
    result["status"] = {
        "7": "connected",
        "6": "connecting",
        "4": "wrong_password",
        "0": "disconnected",
    }.get(str(result.get("status_code", "")), "unknown")
    return result


def wifi_network(value: Any) -> dict[str, Any]:
    """Parse the configured network returned by evbWifiGet."""
    parts = split_evb_csv(value)
    result: dict[str, Any] = {}
    names = ("ssid", "mac_address", "authorization", "static_ipv4", "static_ipv6")
    for index, name in enumerate(names):
        if index >= len(parts) or not parts[index]:
            continue
        item = parts[index]
        if name == "authorization" and item.startswith("{") and item.endswith("}"):
            authorization = split_evb_csv(item[1:-1])
            result[name] = authorization[0] if authorization else None
        else:
            result[name] = item
    return result


def valid_internet_connection(
    connection_info_value: Any, wifi_status_value: Any
) -> bool:
    """Mirror the app's precondition before starting a firmware update."""
    if isinstance(connection_info_value, Mapping) and connection_info_value:
        current = str(connection_info_value.get("current_connection", "")).lower()
        if current in ("wi-fi", "wifi"):
            wifi = connection_info_value.get("wifi", {})
            return isinstance(wifi, Mapping) and wifi.get("still_online") is True
        if current in ("cellular", "cell"):
            cellular = connection_info_value.get("cellular", {})
            return (
                isinstance(cellular, Mapping)
                and cellular.get("still_online") is True
            )
    # EVBox Connect falls back to a non-empty SSID when extended connection
    # information is unavailable or reports no active connection.
    return bool(wifi_status(wifi_status_value).get("ssid"))


def wifi_scan_networks(value: Any) -> list[dict[str, Any]]:
    """Normalize the network list returned by evbWifiScan."""
    if isinstance(value, str):
        raw = value.strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        if isinstance(value, str):
            raw = value
            # Legacy Elvi firmware returns the same wire format consumed by
            # EVBox Connect: one or more brace-delimited, eight-field records.
            # Lists inside a record use square brackets and may contain commas.
            records: list[dict[str, Any]] = []
            for match in re.finditer(r"\{(.*?)\}", raw):
                parts = split_evb_csv(match.group(1))
                if len(parts) < 8 or not parts[0]:
                    continue

                def integer(index: int) -> int | None:
                    try:
                        return int(parts[index])
                    except (ValueError, TypeError):
                        return None

                def values(index: int) -> list[str]:
                    item = parts[index].strip()
                    if item.startswith("[") and item.endswith("]"):
                        item = item[1:-1]
                    return [
                        entry.strip()
                        for entry in re.split(r"[;,]", item)
                        if entry.strip()
                    ]

                records.append(
                    {
                        "ssid": parts[0],
                        "macAddress": parts[1],
                        "channel": integer(2),
                        "mode": parts[3] or None,
                        "rssi": integer(4),
                        "authentication": values(5),
                        "unicast_ciphers": values(6),
                        "group_ciphers": values(7),
                    }
                )
            value = records
    if isinstance(value, Mapping):
        value = value.get("networks", [value])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        ssid = item.get("ssid")
        if not isinstance(ssid, str) or not ssid:
            continue
        network: dict[str, Any] = {"ssid": ssid}
        aliases = {
            "mac_address": ("macAddress", "mac_address"),
            "signal_strength": ("rssi", "signal_strength"),
            "channel": ("channel",),
            "authentication": ("authentication",),
        }
        for name, keys in aliases.items():
            for key in keys:
                if key in item and item[key] is not None:
                    network[name] = item[key]
                    break
        result.append(network)
    return sorted(
        result,
        key=lambda network: (
            -(network.get("signal_strength") if isinstance(network.get("signal_strength"), int) else -999),
            network["ssid"],
        ),
    )


def meter_configuration(value: Any) -> dict[str, Any]:
    """Parse the app's meter serial number and use-connector flag."""
    if not isinstance(value, str) or not value.strip():
        return {}
    raw = value.strip()
    if "." in raw:
        serial_number, flag = raw.rsplit(".", 1)
        return {
            "serial_number": serial_number,
            "uses_connector": flag == "1",
            "uses_comma_format": False,
        }
    parts = split_evb_csv(raw)
    if len(parts) >= 2:
        return {
            "serial_number": parts[1],
            "uses_connector": parts[0] == "1",
            "uses_comma_format": True,
        }
    return {"serial_number": raw, "uses_connector": False, "uses_comma_format": False}


def meter_configuration_value(value: Any, uses_connector: bool) -> str:
    """Change the use-connector flag while retaining firmware CSV style."""
    parsed = meter_configuration(value)
    serial_number = str(parsed.get("serial_number", "1"))
    flag = "1" if uses_connector else "0"
    if parsed.get("uses_comma_format"):
        return f"{flag},{serial_number}"
    return f"{serial_number}.{flag}"


def connector_value(value: Any, connector_id: str = "1") -> str | None:
    """Read a dot-prefixed value for one physical OCPP connector."""
    if not isinstance(value, str):
        return None
    for item in value.split(","):
        prefix, separator, item_value = item.partition(".")
        if separator and prefix.strip() == connector_id:
            return item_value.strip() or None
    return None


def ccid_ac_configuration(
    value: Any, connector_id: str | None = None
) -> dict[str, Any]:
    """Parse the first AC-leakage entry used by the current app SDK."""
    selected_id = connector_id
    status = None
    if selected_id is None and isinstance(value, str):
        first = value.replace(" ", "").split(",", 1)[0]
        selected_id, separator, status = first.partition(".")
        if not separator:
            selected_id, status = "1", None
    elif selected_id is not None:
        status = connector_value(value, selected_id)
    return {
        "connector_id": int(selected_id or "1"),
        "status": "enabled" if status == "100" else "disabled",
    }


def rf_modules(value: Any) -> list[dict[str, Any]]:
    """Parse evb_RFModules entries as EVBox Connect does."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str) or not value.strip():
        return []
    result: list[dict[str, Any]] = []
    for entry in value.split(","):
        parts = [part.strip() for part in entry.split(".", 2)]
        if len(parts) < 2:
            continue
        # EVBSatellite constructor order in the current SDK is
        # type, serialnumber, rssi. Pairing uses the same type.serial form.
        item: dict[str, Any] = {"type": parts[0], "id": parts[1]}
        if len(parts) > 2:
            try:
                item["signal_strength"] = int(parts[2])
            except ValueError:
                pass
        result.append(item)
    return result


def satellite_scan_results(value: Any) -> list[dict[str, Any]]:
    """Parse the asynchronous evbRFScan response used by EVBox Connect."""
    if not isinstance(value, str) or not value.strip():
        return []
    result: list[dict[str, Any]] = []
    for content in re.findall(r"\{(.*?)\}", value):
        parts = split_evb_csv(content)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        item: dict[str, Any] = {"type": parts[0], "id": parts[1]}
        if len(parts) > 2 and parts[2]:
            try:
                item["signal_strength"] = int(parts[2])
            except ValueError:
                pass
        result.append(item)
    return result


def connection_information(value: Any) -> dict[str, Any]:
    """Parse the asynchronous evbConnectionInfo response from the charger."""
    if not isinstance(value, str) or not value.strip():
        return {}
    raw = value.strip().strip('"')
    match = re.fullmatch(r"(.*),(\{.*?\}),(\{.*?\})", re.sub(r"\s+", "", raw))
    if match is None:
        return {}
    wifi = split_evb_csv(match.group(2)[1:-1])
    cell = split_evb_csv(match.group(3)[1:-1])
    if len(wifi) < 5 or len(cell) < 6:
        return {}

    def _integer(item: str) -> int | None:
        try:
            return int(item)
        except (TypeError, ValueError):
            return None

    wifi_last_online = _integer(wifi[4])
    cell_last_online = _integer(cell[5])
    return {
        "current_connection": match.group(1),
        "wifi": {
            "available": wifi[0] == "1",
            "configured": wifi[1] == "1",
            "network": wifi[2] == "1",
            "signal_strength": _integer(wifi[3]),
            "last_online_seconds": wifi_last_online,
            "still_online": wifi_last_online is not None and wifi_last_online < 300,
        },
        "cellular": {
            "available": cell[0] == "1",
            "sim_card": cell[1] == "1",
            "configured": cell[2] == "1",
            "network": cell[3] == "1",
            "signal_strength": _integer(cell[4]),
            "last_online_seconds": cell_last_online,
            "still_online": cell_last_online is not None and cell_last_online < 300,
        },
    }


def card_list(value: Any) -> list[dict[str, Any]]:
    """Parse the braced idTag/maxCurrent list returned by evbWhiteListGet."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str) or not value.strip():
        return []
    result: list[dict[str, Any]] = []
    for content in re.findall(r"\{(.*?)\}", value):
        parts = split_evb_csv(content)
        if not parts or not parts[0]:
            continue
        # EVBox Connect uses the second SDK field internally but neither shows
        # nor edits it in card management. Only expose the card ID it presents.
        result.append({"id_tag": parts[0]})
    return result




def evbox_time(value: Any) -> dt_time | None:
    """Parse the app's HH:MM:SSZ LED schedule format."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt_time.fromisoformat(value.removesuffix("Z"))
    except ValueError:
        return None


def evbox_time_value(value: dt_time) -> str:
    """Format a Home Assistant time for EVBox Connect."""
    return value.replace(microsecond=0).isoformat() + "Z"


def led_configuration(value: Any) -> dict[str, Any]:
    """Parse the EVBox idle LED CSV response."""
    parts = split_evb_csv(value)
    if len(parts) != 4:
        return {}
    try:
        level = int(parts[3])
    except ValueError:
        return {}
    return {
        "led_mode": parts[0],
        "led_start_time": parts[1],
        "led_end_time": parts[2],
        "led_level": level,
    }
