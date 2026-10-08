"""Prove missing evidence cannot pass the cumulative quality gates."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).parents[1]
with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
    from check_quality import TIERS, validate
from tools.check_coverage import validate as validate_coverage


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.rules = yaml.safe_load(
            (ROOT / "custom_components/evbox_g4_ble/quality_scale.yaml").read_text()
        )["rules"]
        self.coverage = {
            "files": {
                "custom_components/evbox_g4_ble/config_flow.py": {
                    "summary": {
                        "covered_lines": 100,
                        "num_statements": 100,
                        "covered_branches": 100,
                        "num_branches": 100,
                    }
                }
            }
        }
        self.expected = set(self.coverage["files"])

    def test_every_cumulative_tier_accepts_complete_evidence(self):
        self.assertEqual(sum(len(items) for items in TIERS.values()), 54)
        for tier in TIERS:
            self.assertEqual(
                validate(self.rules, self.coverage, tier, self.expected), []
            )

    def test_every_required_rule_is_enforced_at_its_tier(self):
        for tier, names in TIERS.items():
            for name in names:
                rules = deepcopy(self.rules)
                rules[name]["status"] = "todo"
                self.assertTrue(
                    validate(rules, self.coverage, tier, self.expected), name
                )

    def test_missing_or_invented_rules_and_unjustified_exemptions_fail(self):
        for change in ("missing", "invented", "exempt", "evidence"):
            rules = deepcopy(self.rules)
            if change == "missing":
                rules.pop("strict-typing")
            elif change == "invented":
                rules["invented-rule"] = {"status": "done", "comment": "none"}
            elif change == "exempt":
                rules["strict-typing"]["status"] = "exempt"
            else:
                rules["strict-typing"]["comment"] = ""
            self.assertTrue(validate(rules, self.coverage, "platinum", self.expected))

    def test_per_module_gate_rejects_missing_report_and_threshold_rounding(self):
        self.assertTrue(validate_coverage({}, self.expected))
        self.assertTrue(validate_coverage({}, set()))
        for kind in ("lines", "branches"):
            for covered in (94, 95):
                report = deepcopy(self.coverage["files"])
                next(iter(report.values()))["summary"]["covered_" + kind] = covered
                self.assertTrue(validate_coverage(report, self.expected))
        report = deepcopy(self.coverage["files"])
        next(iter(report.values()))["summary"]["covered_lines"] = 96
        self.assertFalse(validate_coverage(report, self.expected))
