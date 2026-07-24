#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

EXPECTED_RUNNERS = {
    "authority-activation.yml": (
        "VELLUM_AUTHORITY_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-authority-linux"]',
    ),
    "authority-release.yml": (
        "VELLUM_AUTHORITY_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-authority-linux"]',
    ),
    "gpu-macos.yml": (
        "VELLUM_MACOS_RUNS_ON_JSON",
        '["self-hosted","macOS","ARM64","vellum-build-macos"]',
    ),
    "product-quality.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-build-linux"]',
    ),
    "provenance.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["self-hosted","Linux","ARM64","vellum-build-linux"]',
    ),
    "readme-quick-start.yml": (
        "VELLUM_MACOS_RUNS_ON_JSON",
        '["self-hosted","macOS","ARM64","vellum-build-macos"]',
    ),
}

HOSTED_LABELS = (
    "ubuntu-latest",
    "windows-latest",
    "macos-latest",
    "macos-13",
    "macos-14",
    "macos-15",
    "macos-26",
)


class RunnerPolicyTests(unittest.TestCase):
    def test_every_workflow_uses_an_explicit_self_hosted_fallback(self) -> None:
        workflow_paths = sorted(WORKFLOWS.glob("*.yml"))
        self.assertEqual(
            [path.name for path in workflow_paths],
            sorted(EXPECTED_RUNNERS),
        )

        for path in workflow_paths:
            variable, fallback = EXPECTED_RUNNERS[path.name]
            runs_on_lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("runs-on:")
            ]
            self.assertTrue(runs_on_lines, path.name)
            for line in runs_on_lines:
                with self.subTest(workflow=path.name, selector=line):
                    self.assertIn(f"vars.{variable}", line)
                    self.assertIn(fallback, line)
                    self.assertIn("self-hosted", line)
                    for hosted_label in HOSTED_LABELS:
                        self.assertNotIn(hosted_label, line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
