#!/usr/bin/env python3
"""Keep native and browser retained-tree layout defaults in lockstep."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads(
    (ROOT / "web/tests/fixtures/layout-defaults-v1.json").read_text(encoding="utf-8")
)


def extract(source: str, name: str, suffix: str = "") -> float:
    match = re.search(
        rf"\b{name}\s*=\s*([0-9]+(?:\.[0-9]+)?){re.escape(suffix)}\s*;",
        source,
    )
    if match is None:
        raise AssertionError(f"missing layout contract constant {name}")
    return float(match.group(1))


class LayoutContractTest(unittest.TestCase):
    def test_native_and_browser_defaults_match_canonical_evidence(self) -> None:
        native = (ROOT / "authoring/src/rendered_tree_materializer.mm").read_text(
            encoding="utf-8"
        )
        expected = {
            "kDefaultButtonHeight": CONTRACT["buttonHeight"],
            "kDefaultGenericHeight": CONTRACT["genericHeight"],
            "kDefaultTextInputHeight": CONTRACT["textInputHeight"],
            "kTextLineHeightMultiplier": CONTRACT["textLineHeightMultiplier"],
        }
        self.assertEqual(CONTRACT["schema"], "vellum.layout-defaults.v1")
        for name, value in expected.items():
            self.assertEqual(extract(native, name, "F"), value)
        self.assertIn("default_height(child_type, child_style)", native)

        browser_expected = {
            "DEFAULT_BUTTON_HEIGHT": CONTRACT["buttonHeight"],
            "DEFAULT_GENERIC_HEIGHT": CONTRACT["genericHeight"],
            "DEFAULT_TEXT_INPUT_HEIGHT": CONTRACT["textInputHeight"],
            "TEXT_LINE_HEIGHT_MULTIPLIER": CONTRACT["textLineHeightMultiplier"],
        }
        for path in (
            ROOT / "web/consumer/vellum_host.js",
            ROOT / "web/shell/demo.js",
        ):
            browser = path.read_text(encoding="utf-8")
            for name, value in browser_expected.items():
                self.assertEqual(extract(browser, name), value, path)
            self.assertIn("defaultHeight(child.type, childStyle)", browser)


if __name__ == "__main__":
    unittest.main()
