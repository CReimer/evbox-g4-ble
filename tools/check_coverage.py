"""Require more than 95% line and branch coverage in every integration module."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "custom_components/evbox_g4_ble"


def validate(files: dict, expected: set[str]) -> list[str]:
    errors = []
    if not expected:
        errors.append("No integration modules found")
    for name in sorted(expected):
        report = files.get(name, {})
        summary = report.get("summary", {})
        if not summary:
            errors.append(f"{name}: coverage missing")
            continue
        for kind, covered_key, total_key in (
            ("lines", "covered_lines", "num_statements"),
            ("branches", "covered_branches", "num_branches"),
        ):
            covered, total = summary.get(covered_key, 0), summary.get(total_key, 0)
            if total and covered * 100 <= total * 95:
                errors.append(f"{name}: {kind} {covered}/{total}; must exceed 95%")
    return errors


def main() -> int:
    report = json.loads((ROOT / "coverage-report/coverage.json").read_text())
    expected = {str(path.relative_to(ROOT)) for path in (ROOT / SOURCE).glob("*.py")}
    errors = validate(report.get("files", {}), expected)
    for error in errors:
        print(error, file=sys.stderr)
    print(
        f"Per-module coverage: {len(expected)} modules; {len(errors)} unmet requirements"
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
