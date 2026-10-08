"""Enforce cumulative technical quality tiers for this HACS integration."""

import argparse
import json
from pathlib import Path
import sys

import yaml

from check_bronze import BRONZE_RULES, validate as validate_bronze
from check_coverage import validate as validate_coverage

ROOT = Path(__file__).resolve().parents[1]
TIERS = {
    "bronze": BRONZE_RULES,
    "silver": frozenset(
        "action-exceptions config-entry-unloading docs-configuration-parameters docs-installation-parameters entity-unavailable integration-owner log-when-unavailable parallel-updates reauthentication-flow test-coverage".split()
    ),
    "gold": frozenset(
        "devices diagnostics discovery-update-info discovery docs-data-update docs-examples docs-known-limitations docs-supported-devices docs-supported-functions docs-troubleshooting docs-use-cases dynamic-devices entity-category entity-device-class entity-disabled-by-default entity-translations exception-translations icon-translations reconfiguration-flow repair-issues stale-devices".split()
    ),
    "platinum": frozenset("async-dependency inject-websession strict-typing".split()),
}
EXEMPTIONS = frozenset(
    "docs-triggers docs-conditions discovery-update-info dynamic-devices stale-devices".split()
)


def validate(rules: dict, coverage: dict, target: str, expected: set[str]) -> list[str]:
    errors = validate_bronze(rules, coverage)
    all_rules = set().union(*TIERS.values())
    if set(rules) != all_rules:
        errors.append(
            "Quality rule inventory must contain exactly all 54 official rules"
        )
    required = set()
    for tier, names in TIERS.items():
        required.update(names)
        if tier == target:
            break
    for name in sorted(required):
        rule = rules.get(name, {})
        if not isinstance(rule, dict) or rule.get("status") not in ("done", "exempt"):
            errors.append(f"{name}: incomplete")
            continue
        if not rule.get("comment"):
            errors.append(f"{name}: evidence required")
        if rule["status"] == "exempt" and name not in EXEMPTIONS:
            errors.append(f"{name}: exemption not applicable to this integration")
    if target != "bronze":
        errors.extend(validate_coverage(coverage.get("files", {}), expected))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=TIERS, default="platinum")
    args = parser.parse_args()
    rules = yaml.safe_load(
        (ROOT / "custom_components/evbox_g4_ble/quality_scale.yaml").read_text()
    )["rules"]
    coverage = json.loads((ROOT / "coverage-report/coverage.json").read_text())
    expected = {
        str(p.relative_to(ROOT))
        for p in (ROOT / "custom_components/evbox_g4_ble").glob("*.py")
    }
    errors = validate(rules, coverage, args.tier, expected)
    (ROOT / "coverage-report/quality.json").write_text(
        json.dumps(
            {
                "target": args.tier,
                "tiers": {name: len(names) for name, names in TIERS.items()},
                "unmet_requirements": errors,
                "scope": "Technical custom integration assessment; no official award claimed",
            },
            indent=2,
        )
        + "\n"
    )
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Technical {args.tier}: {len(errors)} unmet requirements")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
