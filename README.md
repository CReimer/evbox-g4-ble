# EVBox Elvi BLE for Home Assistant

An unofficial Home Assistant custom integration for configuring and monitoring
legacy EVBox Elvi charging stations over Bluetooth Low Energy, including through
an active ESPHome Bluetooth proxy.

This project is independent and is not affiliated with, endorsed by, or
supported by EVBox. EVBox and Elvi are trademarks of their respective owners.

## Requirements

- Home Assistant 2026.8.0 or newer
- A supported EVBox Elvi charger
- The charger's Bluetooth security code
- Direct Bluetooth access or an active, connectable ESPHome Bluetooth proxy

## Installation with HACS

Until the integration is included in HACS by default, add it as a custom
repository:

1. Open HACS in Home Assistant.
2. Select **Integrations**.
3. Open the menu and select **Custom repositories**.
4. Add `https://github.com/CReimer/evbox-elvi-ble` with the category
   **Integration**.
5. Install **EVBox Elvi BLE** and restart Home Assistant.
6. Go to **Settings > Devices & services > Add integration** and search for
   **EVBox Elvi BLE**.

## Manual installation

Copy `custom_components/evbox_elvi_ble` into the `custom_components` directory
of your Home Assistant configuration, restart Home Assistant, and add the
integration from **Settings > Devices & services**.

## Features

The available controls depend on the capabilities reported by the charger.
They can include:

- local BLE authentication and automatic discovery;
- firmware, connectivity, Wi-Fi and charger diagnostics;
- minimum and maximum charging current;
- card-required or automatic charging;
- LED idle behavior and phase rotation;
- charging-backend and cellular APN configuration;
- Wi-Fi scanning and configuration;
- RFID allow-list management;
- discovery and pairing of linked charge points;
- restart, identify and firmware-update actions.

Configuration writes are read back where the charger protocol permits it.
Rejected or mismatching values are reported as errors instead of being treated
as successful changes.

## Safety

This is an independent community project for legacy hardware. Charging
equipment can switch substantial electrical loads. Verify current limits and
other safety-relevant settings locally, and do not perform firmware updates
without a recovery plan. The authors provide no warranty and accept no
responsibility for damage or loss.

Do not include Bluetooth security codes, APN passwords, charger diagnostics or
other credentials in bug reports. When reporting a problem, redact serial
numbers, network names and backend URLs.

## Development

Run the repository's dependency-free unit tests with:

```bash
python -m unittest discover -s tests
```

The integration was developed with substantial assistance from generative AI.
All changes are reviewed, tested, and released under the responsibility of the
maintainer.

## License

The independently written source code in this repository is licensed under the
[Apache License 2.0](LICENSE).

No EVBox application, firmware, decompiled source code, credentials or vendor
artwork is distributed with this repository. The license does not grant rights
to EVBox trademarks, firmware or other third-party material.
