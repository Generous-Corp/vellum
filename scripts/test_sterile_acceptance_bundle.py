#!/usr/bin/env python3
"""Negative controls and reproducibility checks for the sterile job bundle."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle = load("build_sterile_acceptance_bundle", SCRIPT_DIR / "build_sterile_acceptance_bundle.py")
validator = load("validate_installed_sdk", SCRIPT_DIR / "validate_installed_sdk.py")


class SterileAcceptanceBundleTest(unittest.TestCase):
    def test_bundle_is_reproducible_and_source_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            one = bundle.build(first)
            two = bundle.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(one["sha256"], two["sha256"])
            with tarfile.open(first) as archive:
                names = set(archive.getnames())
            self.assertIn(
                "vellum-sterile-acceptance/validate_installed_sdk.py", names
            )
            self.assertIn(
                "vellum-sterile-acceptance/sterile-support/"
                "fixtures/authoring-phase3/scenarios/phase3.json",
                names,
            )
            self.assertNotIn(
                "vellum-sterile-acceptance/scripts/build_sdk_artifact.py", names
            )
            self.assertFalse(any("/.git/" in name for name in names))

    def test_checkout_negative_control_is_observed_red(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "vellum"
            (checkout / ".git").mkdir(parents=True)
            (checkout / "provenance").mkdir()
            (checkout / "provenance/pulp-extraction.json").write_text("{}\n")
            findings = validator.checkout_contamination_findings([root])
            self.assertEqual(findings, [str(checkout.resolve())])

    def test_empty_workspace_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                validator.checkout_contamination_findings([Path(temporary)]),
                [],
            )


if __name__ == "__main__":
    unittest.main()
