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
        static, dynamic, unsupported = specifiers("// import a from './gone.js';\n/* import './gone2.js' */\n")
        self.assertEqual((static, dynamic, unsupported), ([], [], []))

    def test_whitespace_free_and_comment_separated_static_forms(self) -> None:
        for source in (
            'import{X}from"./missing.js";',
            "import/* c */{X}/* d */from/* e */'./missing.js';",
            'import X,{Y as Z}from"./missing.js"',
            'import*as ns from"./missing.js"',
            'export{X}from"./missing.js"',
            'export*from"./missing.js"',
            'export * as ns from // note\n"./missing.js"',
            'import"./missing.js"',
            'import {\n a, // from "./decoy.js"\n b } from "./missing.js"',
        ):
            with self.subTest(source=source):
                static, dynamic, unsupported = specifiers(source)
                self.assertEqual((static, dynamic, unsupported), (["./missing.js"], [], []))

    def test_whitespace_free_import_is_reported_as_missing(self) -> None:
        with payload({"index.html": PAGE, "demo.js": 'import{X}from"./missing.js";'}) as root:
            self.assertIn("missing missing.js", missing_modules(Path(root))[0])

    def test_comment_separated_dynamic_forms(self) -> None:
        for source in (
            'import /* c */("./missing.js")',
            'import // c\n("./missing.js")',
            'import(/* c */ "./missing.js" /* d */)',
            'await import ( "./missing.js" )',
        ):
            with self.subTest(source=source):
                self.assertEqual(specifiers(source), ([], ["./missing.js"], []))

    def test_comment_separated_dynamic_import_is_reported_as_missing(self) -> None:
        with payload({"index.html": PAGE, "demo.js": 'import /* c */("./missing.js");'}) as root:
            self.assertIn("missing missing.js", missing_modules(Path(root))[0])

    def test_imports_inside_strings_templates_regexes_and_comments_are_ignored(self) -> None:
        source = (
            "const a = 'import x from \"./s.js\"';\n"
            "const b = `import y from './t.js' ${ import('./u.js') } tail`;\n"
            "const c = /import\\(\"x\"\\)\\/[/]/g.test(a) / 2;\n"
            "// import z from './c.js'\n/* import('./d.js') */\n"
            "obj.import('./e.js'); const m = import.meta.url;\n"
        )
        self.assertEqual(specifiers(source), ([], ["./u.js"], []))

    def test_dynamic_bare_specifier_is_reported(self) -> None:
        with payload({"index.html": PAGE, "demo.js": "await import('missing-package');\n"}) as root:
            problems = missing_modules(Path(root))
            self.assertEqual(len(problems), 1)
            self.assertIn("bare specifier", problems[0])

    def test_dynamic_bare_allowlist_is_exact(self) -> None:
        with payload({"index.html": PAGE, "demo.js": "await import('node:evil');\n"}) as root:
            problems = missing_modules(Path(root))
            self.assertEqual(len(problems), 1)
            self.assertIn("node:evil", problems[0])

    def test_node_module_dynamic_import_is_allowed(self) -> None:
        with payload({"index.html": PAGE, "demo.js": "await import('node:module');\n"}) as root:
            self.assertEqual(missing_modules(Path(root)), [])

    def test_dynamic_non_string_import_is_reported(self) -> None:
        for source in (
            "await import('./' + name);\n",
            "await import(`./${name}.js`);\n",
            "await import();\n",
        ):
            with self.subTest(source=source), payload({"index.html": PAGE, "demo.js": source}) as root:
                problems = missing_modules(Path(root))
                self.assertEqual(len(problems), 1)
                self.assertIn("requires one string literal", problems[0])

    def test_escaped_import_specifier_is_rejected(self) -> None:
        for source in (
            "import './\\u0066oo.js';\n",
            "await import('./\\u0066oo.js');\n",
        ):
            with self.subTest(source=source), payload({
                "index.html": PAGE,
                "demo.js": source,
                "foo.js": "export {};\n",
            }) as root:
                problems = missing_modules(Path(root))
                self.assertEqual(len(problems), 1)
                self.assertIn("escaped import specifier is unsupported", problems[0])

    def test_malformed_source_fails_closed(self) -> None:
        for name, source, message in (
            ("block comment", "/* never closed\nexport {};\n", "unterminated block comment"),
            ("string", "const a = 'oops;\n", "unterminated string literal"),
            ("string at end of file", "const a = 'oops", "unterminated string literal"),
            ("template", "const a = `oops ${1}\n", "unterminated template literal"),
            ("template substitution", "const a = `x${1 + ", "unterminated template literal"),
            ("regex", "const a = /oops\n", "unterminated regular expression literal"),
        ):
            with self.subTest(shape=name), payload({"index.html": PAGE, "demo.js": source}) as root:
                problems = missing_modules(Path(root))
                self.assertEqual(len(problems), 1)
                self.assertIn(message, problems[0])

    def test_invalid_utf8_fails_closed(self) -> None:
        with payload({"index.html": PAGE, "demo.js": "export {};\n"}) as root:
            (Path(root) / "demo.js").write_bytes(b"export const a = '\xff';\n")
            problems = missing_modules(Path(root))
            self.assertEqual(len(problems), 1)
            self.assertIn("demo.js: not valid UTF-8", problems[0])
        with payload({"index.html": PAGE, "demo.js": "export {};\n"}) as root:
            (Path(root) / "index.html").write_bytes(b'<script type="module" src="./demo.js"></script>\xff')
            self.assertIn("index.html: not valid UTF-8", missing_modules(Path(root))[0])

    def test_well_formed_literals_still_pass(self) -> None:
        source = (
            "/* ok */ const a = 'it\\'s';\n"
            "const b = `x${ {k: 1}.k }y`;\n"
            "const c = /[/]\\//g.test(a) ? 1 : 2 / 1;\n"
            "const d = 'line \\\ncontinued';\n"
        )
        with payload({"index.html": PAGE, "demo.js": source}) as root:
            self.assertEqual(missing_modules(Path(root)), [])

    def test_page_without_html_is_an_error_not_a_pass(self) -> None:
        with payload({"demo.js": "export {};\n"}) as root:
            self.assertTrue(missing_modules(Path(root)))


if __name__ == "__main__":
    unittest.main()
