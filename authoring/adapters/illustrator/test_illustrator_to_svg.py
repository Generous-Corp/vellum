#!/usr/bin/env python3
"""Tests for the PDF-compatible Illustrator to SVG adapter.

A fake converter stands in for pdftocairo, so the tests need no Poppler. Its
behaviour is chosen per test through FAKE_MODE, and it records its argv.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import illustrator_to_svg as adapter  # noqa: E402
from illustrator_to_svg import CONVERTER_ENV, SCHEMA, ConversionError, convert, main  # noqa: E402
sys.path.pop(0)

FAKE_CONVERTER = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
if len(sys.argv) == 2 and sys.argv[1] == "-v":
    print("pdftocairo version 24.08.0")
    print("second line that must not reach the receipt")
    raise SystemExit(0)
log = os.environ.get("FAKE_ARGV_LOG")
if log:
    pathlib.Path(log).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
if "-svg" not in sys.argv:
    raise SystemExit(9)
out = pathlib.Path(sys.argv[-1])
probe = os.environ.get("FAKE_PROBE_LOG")
if probe:
    pathlib.Path(probe).write_text(json.dumps({
        "existed": out.exists(),
        "parent": str(out.parent),
        "parent_mode": out.parent.stat().st_mode & 0o777,
    }), encoding="utf-8")
if mode == "fail":
    print("Syntax Error: broken xref", file=sys.stderr)
    raise SystemExit(3)
if mode == "not-svg":
    out.write_text("this is not vector art\\n", encoding="utf-8")
    raise SystemExit(0)
if mode == "empty":
    out.write_bytes(b"")
    raise SystemExit(0)
if mode == "sleep":
    time.sleep(5)
out.write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8"><path d="M0 0"/></svg>\\n',
    encoding="utf-8",
)
"""


class IllustratorToSvgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "panel.ai"
        self.source.write_bytes(b"%PDF-1.7\nillustrator fixture\n")
        self.tool = self.root / "pdftocairo-fake"
        self.tool.write_text(FAKE_CONVERTER, encoding="utf-8")
        self.tool.chmod(self.tool.stat().st_mode | stat.S_IXUSR)
        self.argv_log = self.root / "argv.json"
        self.saved_env = {
            key: os.environ.get(key) for key in (CONVERTER_ENV, "FAKE_MODE", "FAKE_ARGV_LOG", "FAKE_PROBE_LOG")
        }
        os.environ[CONVERTER_ENV] = str(self.tool)
        os.environ["FAKE_ARGV_LOG"] = str(self.argv_log)
        os.environ.pop("FAKE_MODE", None)

    def tearDown(self) -> None:
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def stray_temporaries(self, directory: Path) -> list[str]:
        return sorted(p.name for p in directory.iterdir() if p.name.startswith("."))

    def test_conversion_writes_svg_and_hash_bound_receipt(self) -> None:
        output = self.root / "out" / "panel.svg"
        receipt_path = self.root / "out" / "panel.receipt.json"
        receipt = convert(self.source, output, receipt_path, page=2)

        self.assertTrue(output.is_file())
        self.assertEqual(receipt["schema"], SCHEMA)
        self.assertEqual(receipt["source"]["format"], "ai")
        self.assertEqual(receipt["source"]["bytes"], self.source.stat().st_size)
        self.assertEqual(receipt["converter"]["page"], 2)
        self.assertEqual(receipt["converter"]["version"], "pdftocairo version 24.08.0")
        self.assertEqual(
            receipt["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest()
        )
        self.assertEqual(receipt["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), receipt)
        self.assertEqual(self.stray_temporaries(output.parent), [])

    def test_converter_receives_one_page_range_and_argv(self) -> None:
        output = self.root / "panel.svg"
        convert(self.source, output, page=3)
        argv = json.loads(self.argv_log.read_text(encoding="utf-8"))
        self.assertEqual(argv[:5], ["-svg", "-f", "3", "-l", "3"])
        self.assertEqual(argv[5], str(self.source))
        # The converter writes a private temporary that is then moved into place.
        self.assertNotEqual(argv[6], str(output))
        self.assertEqual(Path(argv[6]).parent.parent, output.parent)

    def test_default_paths_are_beside_the_source(self) -> None:
        receipt = convert(self.source)
        output = self.root / "panel.svg"
        self.assertTrue(output.is_file())
        self.assertTrue((self.root / "panel.svg.provenance.json").is_file())
        self.assertEqual(receipt["output"]["path"], str(output))

    def test_pdf_source_is_accepted_case_insensitively(self) -> None:
        source = self.root / "ART.PDF"
        source.write_bytes(b"%PDF-1.4\n")
        self.assertEqual(convert(source)["source"]["format"], "pdf")

    def test_unsupported_source_fails_closed(self) -> None:
        source = self.root / "panel.svg"
        source.write_text("<svg></svg>", encoding="utf-8")
        with self.assertRaisesRegex(ConversionError, "unsupported source extension"):
            convert(source)

    def test_missing_source_fails_closed(self) -> None:
        with self.assertRaisesRegex(ConversionError, "source does not exist"):
            convert(self.root / "absent.ai")

    def test_missing_converter_fails_closed(self) -> None:
        os.environ[CONVERTER_ENV] = str(self.root / "missing")
        with self.assertRaisesRegex(ConversionError, "pdftocairo is not an executable file"):
            convert(self.source)
        self.assertFalse((self.root / "panel.svg").exists())

    def test_non_executable_converter_fails_closed(self) -> None:
        self.tool.chmod(stat.S_IRUSR | stat.S_IWUSR)
        with self.assertRaisesRegex(ConversionError, "pdftocairo is not an executable file"):
            convert(self.source)

    def test_symlinked_converter_is_resolved_and_recorded(self) -> None:
        link = self.root / "pdftocairo-link"
        link.symlink_to(self.tool)
        os.environ[CONVERTER_ENV] = str(link)
        receipt = convert(self.source)
        self.assertEqual(receipt["converter"]["path"], str(self.tool))

    def test_symlink_source_is_rejected(self) -> None:
        link = self.root / "alias.ai"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(ConversionError, "source must not be a symlink"):
            convert(link)

    def test_symlink_output_is_rejected(self) -> None:
        target = self.root / "elsewhere.svg"
        target.write_text("keep", encoding="utf-8")
        link = self.root / "panel.svg"
        link.symlink_to(target)
        with self.assertRaisesRegex(ConversionError, "output must not be a symlink"):
            convert(self.source, link)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_symlink_receipt_is_rejected_before_anything_is_written(self) -> None:
        target = self.root / "victim.json"
        target.write_text("keep", encoding="utf-8")
        link = self.root / "panel.receipt.json"
        link.symlink_to(target)
        with self.assertRaisesRegex(ConversionError, "receipt must not be a symlink"):
            convert(self.source, self.root / "panel.svg", link)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.root / "panel.svg").exists())

    def test_dangling_symlink_receipt_is_rejected(self) -> None:
        link = self.root / "panel.receipt.json"
        link.symlink_to(self.root / "nowhere" / "victim.json")
        with self.assertRaisesRegex(ConversionError, "receipt must not be a symlink"):
            convert(self.source, self.root / "panel.svg", link)

    def test_output_may_not_overwrite_the_source(self) -> None:
        source = self.root / "art.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        with self.assertRaisesRegex(ConversionError, "output must not overwrite the source"):
            convert(source, source)
        self.assertEqual(source.read_bytes(), b"%PDF-1.4\n")

    def test_receipt_may_not_overwrite_the_output(self) -> None:
        output = self.root / "panel.svg"
        with self.assertRaisesRegex(ConversionError, "receipt must not overwrite"):
            convert(self.source, output, output)

    def test_output_directory_is_rejected(self) -> None:
        directory = self.root / "panel.svg"
        directory.mkdir()
        with self.assertRaisesRegex(ConversionError, "output is not a regular file"):
            convert(self.source)

    def test_converter_failure_leaves_no_output_or_temporary(self) -> None:
        os.environ["FAKE_MODE"] = "fail"
        with self.assertRaisesRegex(ConversionError, "exit 3: Syntax Error: broken xref"):
            convert(self.source)
        self.assertFalse((self.root / "panel.svg").exists())
        self.assertEqual(self.stray_temporaries(self.root), [])

    def test_non_svg_output_keeps_the_previous_svg(self) -> None:
        output = self.root / "panel.svg"
        output.write_text("<svg>previous</svg>", encoding="utf-8")
        os.environ["FAKE_MODE"] = "not-svg"
        with self.assertRaisesRegex(ConversionError, "does not look like an SVG"):
            convert(self.source)
        self.assertEqual(output.read_text(encoding="utf-8"), "<svg>previous</svg>")
        self.assertFalse((self.root / "panel.svg.provenance.json").exists())
        self.assertEqual(self.stray_temporaries(self.root), [])

    def test_empty_output_fails_closed(self) -> None:
        os.environ["FAKE_MODE"] = "empty"
        with self.assertRaisesRegex(ConversionError, "produced no SVG output"):
            convert(self.source)

    def test_converter_timeout_fails_closed(self) -> None:
        os.environ["FAKE_MODE"] = "sleep"
        with self.assertRaisesRegex(ConversionError, "timed out after 1s"):
            convert(self.source, timeout=1)
        self.assertFalse((self.root / "panel.svg").exists())

    def test_invalid_page_and_timeout_are_rejected(self) -> None:
        for kwargs in ({"page": 0}, {"page": True}, {"timeout": 0}):
            with self.subTest(**kwargs):
                with self.assertRaises(ConversionError):
                    convert(self.source, **kwargs)

    def test_svg_probe_needs_both_open_and_close_tags(self) -> None:
        probe = self.root / "probe.svg"
        probe.write_text("<svg>", encoding="utf-8")
        self.assertFalse(adapter.looks_like_svg(probe))
        probe.write_text("<svg></svg>", encoding="utf-8")
        self.assertTrue(adapter.looks_like_svg(probe))
        probe.write_bytes(b"<svg>" + b" " * (2 * adapter.SVG_PROBE_BYTES) + b"</svg>")
        self.assertTrue(adapter.looks_like_svg(probe))

    def test_case_variant_output_does_not_overwrite_the_source(self) -> None:
        source = self.root / "art.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        with self.assertRaisesRegex(ConversionError, "output must not overwrite the source"):
            convert(source, self.root / "ART.PDF")
        self.assertEqual(source.read_bytes(), b"%PDF-1.4\n")

    def test_case_variant_receipt_does_not_overwrite_the_source(self) -> None:
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ConversionError, "receipt must not overwrite"):
            convert(self.source, self.root / "p.svg", self.root / "Panel.Ai")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.root / "p.svg").exists())

    def test_case_variant_receipt_does_not_overwrite_the_output(self) -> None:
        with self.assertRaisesRegex(ConversionError, "receipt must not overwrite"):
            convert(self.source, self.root / "panel.svg", self.root / "PANEL.svg")
        self.assertFalse((self.root / "panel.svg").exists())

    def test_case_variant_directory_is_compared_by_identity(self) -> None:
        (self.root / "out").mkdir()
        if not (self.root / "OUT").exists():
            self.skipTest("this volume is case-sensitive, so OUT is a different directory")
        with self.assertRaisesRegex(ConversionError, "receipt must not overwrite"):
            convert(self.source, self.root / "out" / "panel.svg", self.root / "OUT" / "Panel.SVG")

    def test_converter_writes_into_a_fresh_private_directory(self) -> None:
        probe = self.root / "probe.json"
        os.environ["FAKE_PROBE_LOG"] = str(probe)
        self.addCleanup(os.environ.pop, "FAKE_PROBE_LOG", None)
        output = self.root / "out" / "panel.svg"
        convert(self.source, output)
        seen = json.loads(probe.read_text(encoding="utf-8"))
        self.assertFalse(seen["existed"])
        self.assertEqual(seen["parent_mode"], 0o700)
        workdir = Path(seen["parent"])
        self.assertTrue(os.path.samefile(workdir.parent, output.parent))
        self.assertFalse(workdir.exists())

    def test_receipt_is_written_through_the_mkstemp_descriptor(self) -> None:
        with mock.patch.object(Path, "write_text", side_effect=AssertionError("path write")):
            receipt = convert(self.source)
        self.assertEqual(receipt["schema"], SCHEMA)

    def test_symlinked_default_receipt_is_rejected(self) -> None:
        victim = self.root / "victim.json"
        victim.write_text("keep", encoding="utf-8")
        (self.root / "panel.svg.provenance.json").symlink_to(victim)
        with self.assertRaisesRegex(ConversionError, "receipt must not be a symlink"):
            convert(self.source)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_preplanted_fixed_receipt_temporary_is_not_followed(self) -> None:
        victim = self.root / "victim.json"
        victim.write_text("keep", encoding="utf-8")
        receipt_path = self.root / "panel.receipt.json"
        (self.root / ".panel.receipt.json.tmp").symlink_to(victim)
        convert(self.source, self.root / "panel.svg", receipt_path)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["schema"], SCHEMA)

    def test_receipt_may_not_overwrite_the_source(self) -> None:
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ConversionError, "receipt must not overwrite"):
            convert(self.source, self.root / "panel.svg", self.source)
        self.assertEqual(self.source.read_bytes(), before)

    def test_cli_reports_errors_with_exit_2(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main([str(self.source), "--page", "0"]), 2)
        self.assertIn("page must be a positive integer", stderr.getvalue())

    def test_cli_prints_the_receipt(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main([str(self.source)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["schema"], SCHEMA)


if __name__ == "__main__":
    unittest.main()
