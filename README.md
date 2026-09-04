# EVBox G4 BLE for Home Assistant

An unofficial Home Assistant custom integration for configuring and monitoring
EVBox Gen4 charging stations over Bluetooth Low Energy, including through an
active ESPHome Bluetooth proxy.

This project is independent and is not affiliated with, endorsed by, or
supported by EVBox. EVBox and the product names are trademarks of their
respective owners.

## Requirements

- Home Assistant 2026.8.0 or newer
- A compatible EVBox Gen4 charging station
- The charger's Bluetooth security code
- Direct Bluetooth access or an active, connectable ESPHome Bluetooth proxy

## Installation with HACS

Until the integration is included in HACS by default, add it as a custom
repository:

1. Open HACS in Home Assistant.
2. Select **Integrations**.
3. Open the menu and select **Custom repositories**.
4. Add `https://github.com/CReimer/evbox-g4-ble` with the category
   **Integration**.
5. Install **EVBox G4 BLE** and restart Home Assistant.
6. Go to **Settings > Devices & services > Add integration** and search for
   **EVBox G4 BLE**.

## Manual installation

Copy `custom_components/evbox_g4_ble` into the `custom_components` directory
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

## Compatible charging stations

The BLE services used by this integration are the same two transports accepted
by EVBox Connect. EVBox lists these models as compatible with that app:

- EVBox Elvi;
- EVBox BusinessLine Gen4;
- EVBox Iqon;
- WALLBOX and SMART WALLBOX branded variants.

Development and live acceptance have so far been performed with an EVBox Elvi.
The other listed Gen4 models are expected to use the same protocol but should be
treated as community-tested until owners confirm their exact capabilities.
Older BusinessLine Gen3 and HomeLine stations are not supported: they are
configured with the wired EVBox service tool rather than EVBox Connect BLE.
Newer Livo, Livo 2 and Liviqo models use EVBox Install and a different protocol.

## Safety

This is an independent community project for EVBox Gen4 hardware. Charging
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

No EVBox application, firmware, decompiled source code or credentials are
distributed with this repository. The Apache License does not grant rights to
EVBox trademarks, firmware, brand artwork or other third-party material.

## Trademark legal notice

All product names, trademarks and registered trademarks referenced by this
project or depicted in its images belong to their respective owners. Names,
marks and product imagery are used only to identify compatible products. Their
use does not imply endorsement of or affiliation with this project.

This notice follows the convention used by
[Home Assistant Brands](https://github.com/home-assistant/brands#trademark-legal-notices).
