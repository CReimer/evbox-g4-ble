# Quality Scale assessment

Reviewed 2026-10-08 against all 54 rules in the [official rule list](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/) and their individual explanations, including the [checklist](https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/).

This is a maintained self-assessment of a custom integration, not an official Bronze/Silver/Gold/Platinum award. `done` means implemented with source/test or documentation evidence; `todo` identifies an unmet or insufficiently verified requirement; `exempt` explains a rule that does not apply to the device model. Machine-readable status is in `custom_components/evbox_g4_ble/quality_scale.yaml`.

| Rule | Status | Evidence or remaining work |
|---|---|---|
| `action-exceptions` | todo | Rejected commands and readback failures raise; raw transport errors need consistent translated service errors. |
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
| `entity-unavailable` | todo | Outages mark controls unavailable and reachability off; missing count/text values fixed, remaining legacy select/switch missing-value cases need review. |
| `entity-unique-id` | done | Stable address plus control key; existing registry entities restored on partial startup. |
| `exception-translations` | todo | Validation keys translated; raw BLE transport exceptions still need complete UI normalization. |
| `has-entity-name` | done | Base entity sets has_entity_name. |
| `icon-translations` | done | icons.json defines control icons; device-class icons are retained. |
| `inject-websession` | done | Vendor metadata/downloads use Home Assistant shared aiohttp session; BLE/FTP do not create HTTP sessions. |
| `integration-owner` | done | Manifest codeowner is CReimer. |
| `log-when-unavailable` | todo | Coordinator suppresses repeated failure logs; core logging levels differ from the rule, transitions need dedicated verification. |
| `parallel-updates` | done | All platforms explicitly declare zero; BLE client transaction serializes I/O. |
| `reauthentication-flow` | done | Rejected code starts reauth; reauth and reconfigure validate before updating. |
| `reconfiguration-flow` | done | Existing security code can be replaced without changing identity. |
| `repair-issues` | done | Persistent restart-required repair includes actionable instructions and explicit confirmation limitations. |
| `runtime-data` | done | models.EVBoxConfigEntry = ConfigEntry[EVBoxCoordinator]; runtime_data used in setup, unload, all platforms, options and diagnostics. |
| `stale-devices` | exempt | Only the explicitly configured charger is registered; temporary outages must not remove it. Entry removal removes the charger. |
| `strict-typing` | todo | Not fully annotated or strict-mypy checked; protocol/library separation remains work. |
| `test-before-configure` | done | BLE authentication checked before creating or updating an entry. |
| `test-before-setup` | done | First coordinator refresh gates setup and triggers retry or reauthentication. |
| `test-coverage` | todo | Overall line and branch coverage exceeds 95%; some individual modules remain below 95%. |
| `unique-config-entry` | done | Unique BLE address prevents duplicate manual and discovered entries. |

## Verification and remaining work

All 20 Bronze rules are declared implemented or have the two applicable documentation exemptions (no custom triggers or conditions). The full test command enforces 100% flow statements and branches, rejects flow exclusions, checks the Bronze declarations and requires at least 95% aggregate line and branch coverage. The generated `coverage-report/bronze.json` is CI evidence, not an official grade. Metadata tests validate the local PNGs, typed runtime entry, field descriptions and action documentation.

Home Assistant continues to classify this HACS integration as **Custom**. An official scaled tier requires Home Assistant review/acceptance; changing a local manifest or this checklist cannot award it. Source: [Quality Scale and Custom tier](https://developers.home-assistant.io/docs/core/integration-quality-scale/).

Silver/Gold/Platinum work remains: >95% coverage in every module, consistent transport-exception translations, missing-value handling in remaining controls, log transition verification and strict typing. Those are not silently marked complete by the Bronze gate.

## Core inclusion prerequisites

The Bronze checklist above is not sufficient on its own for Core acceptance. The [Core contribution guide](https://developers.home-assistant.io/docs/core/integration/contributing_to_core/) and [development checklist](https://developers.home-assistant.io/docs/development_checklist/) additionally require product communication in a separate Python library published on PyPI, with a public source distribution and issue tracker. This integration currently includes its BLE client and protocol implementation in `client.py` and `protocol.py`; the client imports Home Assistant's Bluetooth helpers. `aioftp` provides FTP transport but is not a separate EVBox communication library. That separation and publication remain outstanding.

A first Core submission should contain one useful platform, a validated setup flow, the Bronze evidence and framework-native tests. The existing eight-platform HACS integration should continue to be supported; a smaller initial Core submission does not remove its current features. Core metadata, shared brand assets, a documentation PR to `home-assistant.io` and maintainer review are also required. The current HACS manifest version and locally bundled translations/branding are not a ready-to-submit Core change.

No Core submission, PyPI publication or official tier approval is claimed by this release.
