from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
sys.path.insert(0, str(CLI.parent))
try:
    import vellum_illustrator
    import vellum_cli
    from vellum_illustrator import ConversionError, SCHEMA, convert
finally:
    sys.path.pop(0)
FAKE_CONVERTER = f"""#!{sys.executable}
import os
import json
import pathlib
import sys

if len(sys.argv) == 2 and sys.argv[1] == '-v':
    print('pdftocairo version 24.08.0')
    raise SystemExit(0)
if os.environ.get('FAKE_ARGV'):
    pathlib.Path(os.environ['FAKE_ARGV']).write_text(json.dumps(sys.argv[1:]))
if os.environ.get('FAKE_MODE') == 'failed':
    print('conversion refused', file=sys.stderr)
    raise SystemExit(7)
output = pathlib.Path(sys.argv[-1])
if os.environ.get('FAKE_MODE') == 'empty':
    output.write_bytes(b'')
elif os.environ.get('FAKE_MODE') == 'malformed':
    output.write_text('not svg')
else:
    output.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>\\n', encoding='utf-8')
"""


class IllustratorCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "panel.ai"
        self.source.write_bytes(b"%PDF-1.7\\nillustrator fixture\\n")
        self.converter = self.root / "pdftocairo-fake"
        self.converter.write_text(FAKE_CONVERTER, encoding="utf-8")
        self.converter.chmod(self.converter.stat().st_mode | stat.S_IXUSR)
        self.argv_path = self.root / "argv.json"
        self.argv_env = mock.patch.dict(os.environ, {"FAKE_ARGV": str(self.argv_path)})
        self.argv_env.start()
        self.previous = os.environ.get("VELLUM_PDF_TO_SVG")
        os.environ["VELLUM_PDF_TO_SVG"] = str(self.converter)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("VELLUM_PDF_TO_SVG", None)
        else:
            os.environ["VELLUM_PDF_TO_SVG"] = self.previous
        os.environ.pop("FAKE_MODE", None)
        self.argv_env.stop()
        self.temp.cleanup()

    def test_positive_conversion_records_every_provenance_field(self) -> None:
        output = self.root / "out" / "panel.svg"
        provenance = self.root / "out" / "panel.json"
        receipt = convert(self.source, output, provenance, page=2)

        argv = json.loads(self.argv_path.read_text())
        self.assertEqual(argv[:-1], ["-svg", "-f", "2", "-l", "2", str(self.source.resolve())])
        self.assertEqual(Path(argv[-1]).name, "converted.svg")
        self.assertEqual(receipt["schema"], SCHEMA)
        self.assertEqual(receipt["source"], {
            "path": str(self.source.resolve()),
            "format": "ai",
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "bytes": self.source.stat().st_size,
        })
        self.assertEqual(receipt["output"]["path"], str(output.resolve()))
        self.assertEqual(receipt["output"]["format"], "svg")
        self.assertEqual(receipt["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(receipt["output"]["bytes"], output.stat().st_size)
        self.assertEqual(receipt["converter"], {
            "name": "pdftocairo",
            "path": str(self.converter.resolve()),
            "version": "pdftocairo version 24.08.0",
            "page": 2,
        })
        self.assertTrue(receipt["generated_at_utc"])
        self.assertEqual(json.loads(provenance.read_text()), receipt)

    def test_wrong_suffix_is_refused(self) -> None:
        source = self.root / "panel.svg"
        source.write_text("<svg/>", encoding="utf-8")
        with self.assertRaisesRegex(ConversionError, "unsupported source extension"):
            convert(source)

    def test_symlinked_input_is_refused(self) -> None:
        link = self.root / "alias.ai"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(ConversionError, "source must not be a symlink"):
            convert(link)

    def test_symlinked_output_is_refused(self) -> None:
        victim = self.root / "victim.svg"
        victim.write_text("keep", encoding="utf-8")
        link = self.root / "panel.svg"
        link.symlink_to(victim)
        with self.assertRaisesRegex(ConversionError, "output must not be a symlink"):
            convert(self.source, link)
        self.assertEqual(victim.read_text(), "keep")

    def test_missing_converter_is_refused(self) -> None:
        os.environ["VELLUM_PDF_TO_SVG"] = str(self.root / "missing")
        with self.assertRaisesRegex(ConversionError, "pdftocairo is not an executable file"):
            convert(self.source)

    def test_empty_converter_output_is_refused(self) -> None:
        os.environ["FAKE_MODE"] = "empty"
        with self.assertRaisesRegex(ConversionError, "produced no SVG output"):
            convert(self.source)
        self.assertFalse((self.root / "panel.svg").exists())

    def test_cli_forwards_page_and_timeout(self) -> None:
        with mock.patch.object(vellum_illustrator, "convert", side_effect=ConversionError("probe")) as call:
            self.assertNotEqual(vellum_cli.main([
                "--json", "import", "illustrator", str(self.source), "--page", "3", "--timeout", "17",
            ]), 0)
        call.assert_called_once_with(self.source, None, None, page=3, timeout=17)

    def test_collisions_preserve_source_and_output(self) -> None:
        output = self.root / "out.svg"
        output.write_bytes(b"old svg")
        alias = self.root / "alias.pdf"
        os.link(self.source, alias)
        for target, receipt in ((self.source, None), (alias, None),
                                (output, self.source), (output, output)):
            with self.subTest(target=target, receipt=receipt):
                before = self.source.read_bytes()
                with self.assertRaisesRegex(ConversionError, "must not overwrite"):
                    convert(self.source, target, receipt)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(output.read_bytes(), b"old svg")

    def test_symlinked_receipt_is_refused(self) -> None:
        victim = self.root / "victim.json"
        victim.write_bytes(b"keep receipt")
        receipt = self.root / "receipt.json"
        receipt.symlink_to(victim)
        with self.assertRaisesRegex(ConversionError, "receipt must not be a symlink"):
            convert(self.source, receipt_path=receipt)
        self.assertEqual(victim.read_bytes(), b"keep receipt")
        self.assertFalse(self.source.with_suffix(".svg").exists())

    def test_non_executable_converter_is_refused(self) -> None:
        self.converter.chmod(0o600)
        with self.assertRaisesRegex(ConversionError, "not an executable file"):
            convert(self.source)

    def test_bad_converter_preserves_existing_artifacts(self) -> None:
        output = self.root / "out.svg"
        receipt = self.root / "out.json"
        for mode, message in (("malformed", "does not look like an SVG"),
                              ("failed", "failed with exit 7"), ("empty", "no SVG output")):
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {"FAKE_MODE": mode}):
                output.write_bytes(b"old svg")
                receipt.write_bytes(b"old receipt")
                with self.assertRaisesRegex(ConversionError, message):
                    convert(self.source, output, receipt)
                self.assertEqual(output.read_bytes(), b"old svg")
                self.assertEqual(receipt.read_bytes(), b"old receipt")

    def test_timeout_is_forwarded_and_preserves_output(self) -> None:
        output = self.root / "out.svg"
        output.write_bytes(b"old svg")
        real_run = subprocess.run
        def run(argv, **kwargs):
            if argv[1:] == ["-v"]:
                return real_run(argv, **kwargs)
            self.assertEqual(kwargs["timeout"], 17)
            raise subprocess.TimeoutExpired(argv, 17)
        with mock.patch.object(vellum_illustrator.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ConversionError, "timed out after 17s"):
                convert(self.source, output, timeout=17)
        self.assertEqual(output.read_bytes(), b"old svg")

    def test_missing_cli_input_is_structured(self) -> None:
        completed = subprocess.run([sys.executable, str(CLI), "--json", "import", "illustrator"],
                                   capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["status"], "invalid_arguments")
        self.assertNotIn("Traceback", completed.stderr)

    def test_receipt_staging_failure_preserves_artifacts(self) -> None:
        output = self.root / "out.svg"
        receipt = self.root / "out.json"
        output.write_bytes(b"old svg")
        receipt.write_bytes(b"old receipt")
        with mock.patch.object(vellum_illustrator.tempfile, "mkstemp", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ConversionError, "disk full"):
                convert(self.source, output, receipt)
        self.assertEqual(output.read_bytes(), b"old svg")
        self.assertEqual(receipt.read_bytes(), b"old receipt")

    def test_receipt_publication_failure_invalidates_old_receipt(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.root.resolve(), target_is_directory=True)
        for parent in (self.root.resolve(), alias):
            with self.subTest(parent=parent):
                output = parent / "out.svg"
                receipt = parent / "out.json"
                output.write_bytes(b"old svg")
                receipt.write_bytes(b"old receipt")
                resolved_receipt = receipt.resolve()
                real_replace = os.replace
                def replace(src, dst):
                    if Path(dst).resolve() == resolved_receipt:
                        raise OSError("receipt publication failed")
                    return real_replace(src, dst)
                with mock.patch.object(vellum_illustrator.os, "replace", side_effect=replace):
                    with self.assertRaisesRegex(ConversionError, "receipt publication failed"):
                        convert(self.source, output, receipt)
                self.assertIn(b"<svg", output.read_bytes())
                self.assertFalse(receipt.exists())

    def test_filesystem_errors_are_structured(self) -> None:
        with mock.patch.object(vellum_illustrator, "sha256_file", side_effect=OSError("read denied")):
            with self.assertRaisesRegex(ConversionError, "read denied"):
                convert(self.source)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), mock.patch.object(
            vellum_illustrator, "sha256_file", side_effect=OSError("read denied")
        ):
            self.assertNotEqual(vellum_cli.main([
                "--json", "import", "illustrator", str(self.source),
            ]), 0)
        self.assertEqual(json.loads(stream.getvalue())["status"], "illustrator_import_refused")
        with mock.patch.object(vellum_illustrator.Path, "mkdir", side_effect=OSError("write denied")):
            with self.assertRaisesRegex(ConversionError, "write denied"):
                convert(self.source)

    def test_cli_prints_svg_and_provenance_paths(self) -> None:
        output = self.root / "panel.svg"
        completed = subprocess.run(
            [sys.executable, str(CLI), "import", "illustrator", str(self.source), "--out", str(output)],
            text=True, capture_output=True, check=False,
            env={**os.environ, "VELLUM_PDF_TO_SVG": str(self.converter)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(f"SVG: {output.resolve()}", completed.stdout)
        self.assertIn(f"Provenance: {Path(str(output.resolve()) + '.provenance.json')}", completed.stdout)


if __name__ == "__main__":
    unittest.main()
