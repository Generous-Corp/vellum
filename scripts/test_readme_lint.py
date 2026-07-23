#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).with_name("readme_lint.py")
SPEC = importlib.util.spec_from_file_location("readme_lint", PATH)
assert SPEC and SPEC.loader
readme_lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readme_lint)


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = readme_lint.README.read_text(encoding="utf-8")

    def test_repository_readme(self) -> None:
        readme_lint.validate(self.readme)

    def test_banner_negative_control(self) -> None:
        changed = self.readme.replace("private, experimental", "experimental", 1)
        with self.assertRaisesRegex(readme_lint.Error, "banner"):
            readme_lint.validate(changed)

    def test_section_order_negative_control(self) -> None:
        changed = self.readme.replace(
            "## What this is\n",
            "## __swap__\n",
            1,
        ).replace(
            "## Requirements\n",
            "## What this is\n",
            1,
        ).replace(
            "## __swap__\n",
            "## Requirements\n",
            1,
        )
        with self.assertRaisesRegex(readme_lint.Error, "section order"):
            readme_lint.validate(changed)

    def test_boundary_negative_control(self) -> None:
        changed = self.readme.replace("audio DesignIR extensions", "format extensions", 1)
        with self.assertRaisesRegex(readme_lint.Error, "boundary"):
            readme_lint.validate(changed)

    def test_forbidden_claim_negative_control(self) -> None:
        changed = self.readme.replace(
            "Vellum is an experimental",
            "Vellum imports arbitrary web apps. Vellum is an experimental",
            1,
        )
        with self.assertRaisesRegex(readme_lint.Error, "forbidden claim"):
            readme_lint.validate(changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
