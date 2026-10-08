# Quality Scale assessment

Reviewed 2026-10-08 against all 54 rules in the [official rule list](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/) and their individual explanations, including the [checklist](https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/).

This is a maintained self-assessment of a custom integration, not an official Bronze/Silver/Gold/Platinum award. `done` means implemented with source/test or documentation evidence; `todo` identifies an unmet or insufficiently verified requirement; `exempt` explains a rule that does not apply to the device model. Machine-readable status is in `custom_components/evbox_g4_ble/quality_scale.yaml`.

| Rule | Status | Evidence or remaining work |
|---|---|---|
| `action-exceptions` | done | Device errors normalized by errors.async_device_errors for services, entity writes, buttons and firmware installation; explicit failed refresh raises. Readback and rejected commands remain errors. tests/test_quality_silver.py. |
| `action-setup` | done | Actions registered in async_setup; unloaded entries rejected before device access. |
| `appropriate-polling` | done | Five-minute diagnostics; configuration cached for 30 minutes; explicit refresh forces full read. |
| `async-dependency` | done | BLE and aioftp I/O are asynchronous; blocking filesystem work uses executor jobs. |
| `brands` | done | Bundled brand/icon.png (256x256) and icon@2x.png (512x512), supported by HA >=2026.3; see documentation/dependencies.md. |
| `common-modules` | done | Coordinator and base entity have their standard modules. |
| `config-entry-unloading` | done | Platform unload and entry-scoped listener cleanup supported. |
| `config-flow` | done | UI setup, password selector, field descriptions; hardware settings are written to the charger. |
| `config-flow-test-coverage` | done | All flow statements and branches covered; tools/check_bronze.py enforces 100% without exclusions; tests/test_bronze_flows.py verifies retry and real framework identity/duplicates. |
| `dependency-transparency` | done | aioftp: Apache-2.0, PyPI releases, matching source tags and public build/publication workflows; provenance links in documentation/dependencies.md. |
| `devices` | done | DeviceInfo groups charger entities and exposes available identity/firmware metadata. |
| `diagnostics` | done | Health, capability list and normalized/redacted protocol data; nested identifiers covered by tests. |
| `discovery` | done | Manifest Bluetooth matchers and validated discovery flow. |
| `discovery-update-info` | exempt | BLE device lookup resolves the current adapter/proxy for the configured fixed identity on every connection; no stored IP endpoint. |
| `docs-actions` | done | documentation/usage.md lists action purposes and parameters. |
| `docs-conditions` | exempt | No integration-specific conditions; standard state conditions are used. |
| `docs-configuration-parameters` | done | documentation/usage.md describes charger options and their effects. |
| `docs-data-update` | done | Polling intervals, manual refresh, readback and BLE limitations documented. |
| `docs-examples` | done | State notification automation and read-only refresh action example. |
| `docs-high-level-description` | done | README describes EVBox Gen4 BLE and links the product website. |
| `docs-installation-instructions` | done | README contains HACS and manual installation and prerequisites. |
| `docs-installation-parameters` | done | documentation/usage.md explains BLE address and security code. |
| `docs-known-limitations` | done | No energy/session telemetry; firmware/network and reboot observation limitations documented. |
| `docs-removal-instructions` | done | documentation/usage.md describes entry removal and HACS/manual uninstall. |
| `docs-supported-devices` | done | README separates live-tested Elvi from expected Gen4 variants and unsupported generations. |
| `docs-supported-functions` | done | README feature list and documentation/usage.md entity groups and actions. |
| `docs-triggers` | exempt | No integration-specific triggers; standard entity state triggers are used. |
| `docs-troubleshooting` | done | README plus symptom/action table and diagnostic download instructions. |
| `docs-use-cases` | done | Monitor connectivity and inspect or configure local charger settings without cloud access. |
| `dynamic-devices` | exempt | One physical BLE charger per entry; RF pairing records are not separately controllable HA devices. Late charger capabilities are dynamically added. |
| `entity-category` | done | Controls are configuration; status information diagnostic. |
| `entity-device-class` | done | Connectivity/problem/signal-strength/enum/update classes where applicable. |
| `entity-disabled-by-default` | done | Optional Wi-Fi and cellular signal sensors disabled by default; existing registry preferences preserved. |
| `entity-event-setup` | done | CoordinatorEntity manages entity subscriptions; discovery listener is unloaded with its entry. |
| `entity-translations` | done | EN and DE entity names and enum/availability explanations. |
| `entity-unavailable` | done | CoordinatorEntity marks controls unavailable during outages; missing values return None, including charging mode, AC detection and meter switch. Recovery preserves entity identity; tests/test_quality_silver.py. |
| `entity-unique-id` | done | Stable address plus control key; existing registry entities restored on partial startup. |
| `exception-translations` | done | Action boundaries translate authentication, transport and protocol failures using EN/DE exception messages, without exposing private transport details. Validation keys and placeholders checked by tests/test_reliability.py. |
| `has-entity-name` | done | Base entity sets has_entity_name. |
| `icon-translations` | done | icons.json defines control icons; device-class icons are retained. |
| `inject-websession` | done | Vendor metadata/downloads use Home Assistant shared aiohttp session; BLE/FTP do not create HTTP sessions. |
| `integration-owner` | done | Manifest codeowner is CReimer. |
| `log-when-unavailable` | done | Uses documented DataUpdateCoordinator UpdateFailed mechanism; real-framework regression verifies exactly one outage and recovery record, no repeated failures or private transport details. tests/test_quality_silver.py. |
| `parallel-updates` | done | All platforms explicitly declare zero; BLE client transaction serializes I/O. |
| `reauthentication-flow` | done | Rejected code starts reauth; reauth and reconfigure validate before updating. |
| `reconfiguration-flow` | done | Existing security code can be replaced without changing identity. |
| `repair-issues` | done | Persistent restart-required repair includes actionable instructions and explicit confirmation limitations. |
| `runtime-data` | done | models.EVBoxConfigEntry = ConfigEntry[EVBoxCoordinator]; runtime_data used in setup, unload, all platforms, options and diagnostics. |
| `stale-devices` | exempt | Only the explicitly configured charger is registered; temporary outages must not remove it. Entry removal removes the charger. |
| `strict-typing` | done | All integration modules pass strict mypy in CI against stable HA; typed config entries throughout. Consumed untyped aioftp API has local stubs checked by real FTP tests; validation engine alias follows HA compatibility. |
| `test-before-configure` | done | BLE authentication checked before creating or updating an entry. |
| `test-before-setup` | done | First coordinator refresh gates setup and triggers retry or reauthentication. |
| `test-coverage` | done | tools/check_coverage.py enforces more than 95% line and branch coverage separately for every integration module; config/reauth/options flows retain 100%. Firmware edge cases exercise real FTP servers. |
| `unique-config-entry` | done | Unique BLE address prevents duplicate manual and discovered entries. |

## Cumulative verification

All 54 rules are implemented or have one of the five justified exemptions listed above. The cumulative gate contains 20 Bronze, 10 Silver, 21 Gold and 3 Platinum rules. `python tools/run_tests.py` requires 100% flow statements and branches without flow exclusions, and more than 95% line and branch coverage separately in **every** integration module. `python tools/check_quality.py --tier silver` (or `gold` / `platinum`) checks the cumulative declarations and coverage. `python -m mypy` checks all 23 integration modules in strict mode against stable HA; it is a required CI job. Local aioftp stubs describe the consumed upstream API; real FTP tests exercise those contracts. The validation adapter follows HA 2026.10's documented-in-source runtime alias from voluptuous to probatio, while retaining runtime compatibility with the minimum supported HA version.

The offline suite currently passes 196 tests with 2536/2547 statements (99.57%) and 719/732 branches (98.22%). Every individual module exceeds 95% for both metrics, and configuration flows remain at 100%. Coverage's default exclusions for type-checking-only imports are not device-runtime exclusions. Generated `coverage-report/quality.json` and `bronze.json` are CI evidence, not official grades. Tests deliberately reject missing rules, unjustified exemptions and coverage at or below 95%.

Home Assistant continues to classify this HACS integration as **Custom**. An official scaled tier requires Home Assistant review/acceptance; changing a local manifest or this checklist cannot award it. Source: [Quality Scale and Custom tier](https://developers.home-assistant.io/docs/core/integration-quality-scale/).

Silver adds verified action errors, availability, lifecycle, reauthentication and per-module coverage. Gold adds translated failures alongside the existing discovery, diagnostics, repairs and documentation. Platinum adds strict typing alongside asynchronous I/O and reuse of the shared HTTP session. A release still requires CI checks; hardware behavior is verified separately on Beliar.

## Current project scope and standalone library

The current goal is to meet the applicable technical Bronze, Silver, Gold and Platinum requirements for the HACS integration and maintain corresponding engineering standards for its standalone library. Submission to Home Assistant Core is outside the current scope. The supported HACS release retains its eight platforms and current functionality.

The separate [evbox-ble library](https://github.com/CReimer/evbox-ble) is now [published on PyPI as 0.1.2](https://pypi.org/project/evbox-ble/0.1.2/), with Apache-2.0 source, source and wheel distributions, an issue tracker, and Trusted Publishing provenance. It has no Home Assistant dependency. Its [quality requirements](https://github.com/CReimer/evbox-ble/blob/v0.1.2/QUALITY.md) assess all 54 integration rules for SDK applicability and enforce 100% line and branch coverage, strict mypy, Ruff, and isolated wheel/source-distribution tests on Python 3.11 and 3.14. The [release workflow](https://github.com/CReimer/evbox-ble/actions/runs/37753934145) passed all checks before publishing; the installed PyPI package also passed all 75 tests with 698/698 statements and 268/268 branches covered.

The released HACS integration still uses its bundled `client.py` and `protocol.py`; it does not yet depend on `evbox-ble`. Library publication does not change installed HACS behavior or make a Core submission. The library has no Home Assistant quality tier of its own. Tests simulate Bluetooth peers and do not replace physical-device verification for new hardware or firmware.
