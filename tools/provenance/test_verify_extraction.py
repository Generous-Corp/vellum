#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_extraction.py")
SPEC = importlib.util.spec_from_file_location("verify_extraction", MODULE_PATH)
assert SPEC and SPEC.loader
verify_extraction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_extraction)


class VerifyExtractionTests(unittest.TestCase):
    def test_debt_parser_reads_only_path_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "debt.yaml"
            path.write_text(
                "debts:\n  - id: x\n    paths:\n      - one/a.cpp\n      - two/b.hpp\n"
                "    owner: test\nnotes:\n  - not-a-path\n",
                encoding="utf-8",
            )
            self.assertEqual(
                verify_extraction.debt_paths(path), {"one/a.cpp", "two/b.hpp"}
            )

    def test_active_scan_finds_audio_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "foundation").mkdir()
            (root / "foundation/bad.cpp").write_text(
                "auto* x = pulp::audio::engine();\n", encoding="utf-8"
            )
            findings = verify_extraction.scan_active_surface(root)
            self.assertEqual(len(findings), 2)
            self.assertEqual(findings[0]["path"], "foundation/bad.cpp")

    def test_active_scan_ignores_quarantined_raw_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "core/audio").mkdir(parents=True)
            (root / "core/audio/legacy.cpp").write_text(
                "pulp::audio::engine();\n", encoding="utf-8"
            )
            self.assertEqual(verify_extraction.scan_active_surface(root), [])


if __name__ == "__main__":
    unittest.main()
