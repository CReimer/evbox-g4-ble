# Using EVBox G4 BLE

## Setup and configuration

The **Bluetooth address** identifies the charger, not the ESPHome proxy. Copy it from Bluetooth discovery if available. The **security code** is the charger's Bluetooth code supplied with the station or set in EVBox Connect; it is not the Wi-Fi password. Home Assistant stores it for local authentication. Use **Reconfigure** to replace it. One charger can be configured only once.

Entities depend on capabilities exposed by the charger. Previously registered entities retain their IDs on partial startup; newly reported functions appear automatically. Missing sensor readings are unknown, not zero. The reachability sensor reports off when a read fails, while charger controls become unavailable. Signal-strength diagnostics start disabled; enable them in the entity settings if needed.

The options menu changes settings on the charger, rather than maintaining a separate copy of those settings in Home Assistant:

| Menu | Fields and effect |
|---|---|
| Wi-Fi | Scan and select a network or enter SSID, security and password manually. Optional BSSID selects a specific access point. Clearing Wi-Fi requires confirmation. |
| Charging cards | Add or remove an alphanumeric RFID ID (1–20 characters); clearing all cards requires confirmation. |
| Backend | Enable/disable online mode and set a `ws://` or `wss://` server URL ending in `/`. |
| Mobile APN | APN, username and password for cellular connectivity. Empty credentials are allowed. |
| Paired charge points | Scan (1–120 seconds), pair by serial number, remove or identify a paired point. RF-capable hardware required; maximum ten records. |
| Firmware | Supply a firmware URL for installation through the local FTP bridge. The firmware entity also offers vendor-discovered updates. |

The device contains configuration controls for current limits, charging mode, automatic-session card, phase order, LEDs, backend, APN and supported installer toggles. Diagnostic entities show firmware, Wi-Fi/network information, RFID and RF counts, connectivity and restart need. Buttons refresh, identify or restart the charger. No live charging power, delivered energy or session start/stop control is provided by this BLE interface.

Automatic-session card assignment requires compatible firmware, enabled backend mode and a stored card. The entity's availability attribute explains which prerequisite is missing.

## Updates and restart confirmation

Diagnostics are polled every five minutes in one authenticated BLE connection. Scalar configuration is read at startup and every 30 minutes; the refresh button/action forces a complete read. Configuration writes read back the changed values directly where supported. Unchanged data does not trigger redundant coordinator callbacks. External settings changes can therefore take up to 30 minutes to appear; use refresh when needed. Keep polling modest on legacy hardware and allow the vendor app to release its BLE connection.

Some settings take effect only after a restart. A persistent repair notification and problem sensor explain this. An accepted reset command alone does not clear the notice: the integration must observe a connection failure after its reset request followed by a successful authenticated read, or a firmware version change. Bluetooth outages are indirect evidence. A quick restart between polling cycles or an external restart on unchanged firmware cannot be reliably detected; the notice may remain. Pending restart state survives a Home Assistant reload. The integration never resets the charger automatically.

## Service actions

All action names below have the `evbox_g4_ble.` prefix. An optional `entry_id` selects a charger; it is required if more than one is configured. Use Developer Tools > Actions to inspect responses. Write actions change the physical charger's configuration.

| Action | Parameters beyond `entry_id` | Purpose |
|---|---|---|
| `refresh` | none | Read all configuration and diagnostics now. |
| `scan_wifi` | none | Return networks discovered by the charger. |
| `set_wifi` | `ssid`, optional `mac_address`, `password` | Configure a network; no password means open Wi-Fi. |
| `clear_wifi` | none | Remove stored Wi-Fi configuration. |
| `set_apn` | `apn`, optional `username`, `password` | Configure cellular credentials. |
| `set_server` | `url` | Set backend URL and derived compatibility flags. |
| `set_led_idle` | `mode` (`On`/`Off`), `level` (5/25/50/75) | Set idle LED mode and brightness. |
| `rfid_add` | `id_tag` | Add and verify one local charging card. |
| `rfid_remove` | `id_tag` | Remove and verify one local charging card. |
| `rfid_clear` | none | Remove all locally stored cards. |
| `scan_satellites` | optional `timeout` (default 40 seconds) | Return scanned RF charge points. |
| `pair_satellite` | `satellite_id` | Pair a supported charge point by serial number. |
| `blink_satellite` | `satellite_id` | Identify a currently paired charge point. |
| `connection_info` | none | Return Wi-Fi/cellular connectivity details. |
| `update_firmware` | `url` | Start installation via the firmware bridge. |
| `restart` | none | Request a hard charger reset. |
| `identify` | none | Ask the charger to identify itself. |

Example read-only action, for an installation with one configured charger:

```yaml
action: evbox_g4_ble.refresh
data: {}
```

The integration supplies no custom automation triggers or conditions. Use standard entity state triggers and conditions. For example, create a persistent notification after the charger has been unreachable for ten minutes (replace the example entity ID with yours):

```yaml
alias: EVBox connection lost
triggers:
  - trigger: state
    entity_id: binary_sensor.evbox_elvi_erreichbar
    to: "off"
    for: "00:10:00"
actions:
  - action: persistent_notification.create
    data:
      title: EVBox connection
      message: Check the Bluetooth proxy and whether EVBox Connect is still connected.
```

Useful applications include spotting Bluetooth/proxy outages, checking the charger's reported firmware, managing charging cards locally and checking configuration after changes in the vendor app. Energy dashboards and load management need a separate source of real-time measurements.

## Troubleshooting and diagnostics

| Symptom | What to check |
|---|---|
| Setup retries or reachability off | Charger power, Bluetooth range, an active connectable proxy and whether another app holds the BLE connection. |
| Reauthentication requested | Enter the current charger security code; reconfiguration preserves entity IDs. |
| One sensor is unknown | The latest read omitted that field; try a full refresh. New supported fields are added automatically. |
| Card assignment unavailable | Inspect its availability reason; enable backend mode, store a card and check firmware capability as indicated. |
| Firmware transfer cannot start | IPv4 routing and FTP control/passive ports between charger and Home Assistant; see README. |
| Restart notification remains | The integration has not observed sufficient restart evidence; see the confirmation limits above. |

Download diagnostics from the integration's menu under Settings > Devices & services. The file includes last successful poll time (UTC), duration in seconds, consecutive failures, last error class and observed capability names. Compound protocol strings are normalized before redaction. Credentials, card IDs, Bluetooth/network addresses and mobile subscriber identifiers are redacted. Inspect the file before sharing it, especially if your firmware returns additional fields. Debug logs can be enabled through the same integration menu; turn them off after reproduction.

## Removal

Delete the EVBox configuration entry under Settings > Devices & services. Remove automations referencing its entities or actions. To uninstall the code, remove EVBox G4 BLE in HACS and restart Home Assistant. For a manual install, delete `custom_components/evbox_g4_ble` and restart. Removing the integration does not reset charger settings, remove its cards or alter the backend connection.
