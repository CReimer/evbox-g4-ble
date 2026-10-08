"""Verify the declared Bronze checklist and its automated coverage evidence."""

from pathlib import Path
import json
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
BRONZE_RULES = frozenset(
    {
        "action-setup",
        "appropriate-polling",
        "brands",
        "common-modules",
        "config-flow-test-coverage",
        "config-flow",
        "dependency-transparency",
        "docs-actions",
        "docs-triggers",
        "docs-conditions",
        "docs-high-level-description",
        "docs-installation-instructions",
        "docs-removal-instructions",
        "entity-event-setup",
        "entity-unique-id",
        "has-entity-name",
        "runtime-data",
        "test-before-configure",
        "test-before-setup",
        "unique-config-entry",
    }
)


def validate(rules, coverage):
    """Return unmet requirements, without accepting missing or excluded paths."""
    errors = []
    for name in sorted(BRONZE_RULES):
        item = rules.get(name, {})
        if not isinstance(item, dict) or item.get("status") not in ("done", "exempt"):
            errors.append(f"{name}: incomplete")
        elif item["status"] == "exempt" and name not in (
            "docs-triggers",
            "docs-conditions",
        ):
            errors.append(f"{name}: exemption not applicable to this integration")
        if not isinstance(item, dict) or not item.get("comment"):
            errors.append(f"{name}: evidence or explanation required")
    report = coverage.get("files", {}).get(
        "custom_components/evbox_g4_ble/config_flow.py", {}
    )
    summary = report.get("summary", {})
    if not summary.get("num_statements") or summary.get("covered_lines") != summary.get(
        "num_statements"
    ):
        errors.append("config-flow-test-coverage: all flow statements must be covered")
    if not summary.get("num_branches") or summary.get(
        "covered_branches"
    ) != summary.get("num_branches"):
        errors.append("config-flow-test-coverage: all flow branches must be covered")
    if report.get("excluded_lines") or summary.get("excluded_lines"):
        errors.append("config-flow-test-coverage: exclusions are not permitted")
    return errors


def main():
    rules = yaml.safe_load(
        (ROOT / "custom_components/evbox_g4_ble/quality_scale.yaml").read_text()
    )["rules"]
    coverage = json.loads((ROOT / "coverage-report/coverage.json").read_text())
    errors = validate(rules, coverage)
    result = {
        "bronze_rule_count": len(BRONZE_RULES),
        "unmet_requirements": errors,
        "scope": "Custom integration self-assessment, not an official HA award",
    }
    (ROOT / "coverage-report/bronze.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(
            f"Bronze checklist: {len(BRONZE_RULES)} rules; flow lines and branches: 100%"
        )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
