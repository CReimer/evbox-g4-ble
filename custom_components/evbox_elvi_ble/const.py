"""Constants for the EVBox Gen4 BLE integration."""

from datetime import timedelta

DOMAIN = "evbox_elvi_ble"
PLATFORMS = ["binary_sensor", "button", "number", "select", "sensor", "switch", "text", "update"]

CONF_ADDRESS = "address"
CONF_SECURITY_CODE = "security_code"

SERVICE_UUID = "2456e1b9-26e2-8f83-e744-f34f01e9d701"
CHARACTERISTIC_UUID = "2456e1b9-26e2-8f83-e744-f34f01e9d703"
ESP32_SERVICE_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
ESP32_WRITE_UUID = "0000c304-0000-1000-8000-00805f9b34fb"
ESP32_NOTIFY_UUID = "0000c305-0000-1000-8000-00805f9b34fb"
CHUNK_SIZE = 20
ESP32_CHUNK_SIZE = 128
COMMAND_TIMEOUT = 30.0
WIFI_CONNECT_TIMEOUT = 60.0
UPDATE_INTERVAL = timedelta(minutes=5)
MAX_SATELLITES = 10
APN_MAX_LENGTH = 126
SERVER_URL_MAX_LENGTH = 254
ASCII_NO_WHITESPACE_PATTERN = r"^[\x21-\x7f]*$"
SERVER_URL_PATTERN = r"^[wW][sS]{1,2}://[\x21-\x7f]*/$"
SATELLITE_ID_PATTERN = r"^[0-9]{1,20}$"
RFID_ID_PATTERN = r"^[A-Za-z0-9]{1,20}$"

KEY_MAX_CURRENT = "evb_MaximumStationCurrent"
KEY_MIN_CURRENT = "evb_MinimumChargeCurrent"
KEY_USE_BACKEND = "evb_UseBackend"
KEY_SERVER_URL = "evb_ServerURL"
# EVBox Connect derives these internal compatibility flags from the server URL.
# They are deliberately not polled or exposed as Home Assistant entities.
KEY_CONNECTOR_LIST = "evb_ConnectorList"
KEY_SERIAL_AS_CONNECTOR_ID = "evb_SerialAsConnectorId"
KEY_AUTO_START = "evb_AutoStart"
KEY_LOCAL_AUTH_LIST_ENABLED = "LocalAuthListEnabled"
KEY_METER_ADDRESS = "evb_ConnectorKWhMeterAddress"
KEY_PHASE_ROTATION = "ConnectorPhaseRotation"
KEY_CCID_AC = "evb_ConnectorCcidAC"
KEY_CCID = "evb_ConnectorCcid"
KEY_APN_NAME = "evb_APNName"
KEY_APN_USER = "evb_APNUser"
KEY_APN_PASS = "evb_APNPass"
KEY_RF_MODULES = "evb_RFModules"
KEY_TRIGGER = "evb_Trigger"
KEY_BOOT_INFO = "evb_BootInfo"

LED_MODE = "led_mode"
LED_START_TIME = "led_start_time"
LED_END_TIME = "led_end_time"
LED_LEVEL = "led_level"

SCALAR_KEYS = (
    KEY_MAX_CURRENT,
    KEY_MIN_CURRENT,
    KEY_USE_BACKEND,
    KEY_SERVER_URL,
    KEY_AUTO_START,
    KEY_METER_ADDRESS,
    KEY_PHASE_ROTATION,
    KEY_CCID_AC,
    KEY_CCID,
    KEY_APN_NAME,
    KEY_APN_USER,
    KEY_RF_MODULES,
    KEY_BOOT_INFO,
)

SENSITIVE_FIELDS = {
    CONF_SECURITY_CODE,
    "password",
    "authorization",
    "apn_password",
    KEY_APN_PASS,
}
