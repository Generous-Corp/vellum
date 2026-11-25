#!/usr/bin/env python3
"""Unit tests for the web-dist import-graph check."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_web_dist_imports import missing_modules, specifiers  # noqa: E402


PAGE = '<script type="module" src="./demo.js"></script>'


def payload(files: dict[str, str]) -> tempfile.TemporaryDirectory[str]:
    directory = tempfile.TemporaryDirectory()
    for name, body in files.items():
        path = Path(directory.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return directory


class ImportGraph(unittest.TestCase):
    def test_complete_payload_resolves(self) -> None:
        with payload({
            "index.html": PAGE,
            "demo.js": "import { a } from './ui/runtime.js';\n",
            "ui/runtime.js": "import {\n  LIMIT,\n} from './limits.generated.js';\nexport const a = LIMIT;\n",
            "ui/limits.generated.js": "export const LIMIT = 1;\n",
        }) as root:
            self.assertEqual(missing_modules(Path(root)), [])

    def test_module_missing_two_hops_from_the_page_is_reported(self) -> None:
        with payload({
            "index.html": PAGE,
            "demo.js": "import { a } from './ui/runtime.js';\n",
            "ui/runtime.js": "import { LIMIT } from './limits.generated.js';\nexport const a = LIMIT;\n",
        }) as root:
            problems = missing_modules(Path(root))
            self.assertEqual(len(problems), 1)
            self.assertIn("missing ui/limits.generated.js", problems[0])

    def test_shipped_module_unreached_by_the_page_is_checked(self) -> None:
        with payload({
            "index.html": PAGE,
            "demo.js": "export const nothing = 0;\n",
            "ui/index.js": "export { Design } from './design.js';\n",
        }) as root:
            self.assertEqual(len(missing_modules(Path(root))), 1)

    def test_missing_page_script_is_reported(self) -> None:
        with payload({"index.html": PAGE}) as root:
            self.assertIn("missing demo.js", missing_modules(Path(root))[0])

    def test_static_bare_specifier_is_reported_and_guarded_dynamic_one_is_not(self) -> None:
        with payload({
            "index.html": PAGE,
            "demo.js": "import x from 'lodash';\nconst m = await import('node:module');\n",
        }) as root:
            problems = missing_modules(Path(root))
            self.assertEqual(len(problems), 1)
            self.assertIn("bare specifier", problems[0])

    def test_commented_import_is_ignored(self) -> None:
        static, dynamic = specifiers("// import a from './gone.js';\n/* import './gone2.js' */\n")
        self.assertEqual((static, dynamic), ([], []))

    def test_page_without_html_is_an_error_not_a_pass(self) -> None:
        with payload({"demo.js": "export {};\n"}) as root:
            self.assertTrue(missing_modules(Path(root)))


if __name__ == "__main__":
    unittest.main()
