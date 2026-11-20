from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from cli.vellum_illustrator import ConversionError, SCHEMA, convert


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
FAKE_CONVERTER = f"""#!{sys.executable}
import os
import pathlib
import sys

if len(sys.argv) == 2 and sys.argv[1] == '-v':
    print('pdftocairo version 24.08.0')
    raise SystemExit(0)
output = pathlib.Path(sys.argv[-1])
if os.environ.get('FAKE_MODE') == 'empty':
    output.write_bytes(b'')
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
        self.previous = os.environ.get("VELLUM_PDF_TO_SVG")
        os.environ["VELLUM_PDF_TO_SVG"] = str(self.converter)

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("VELLUM_PDF_TO_SVG", None)
        else:
            os.environ["VELLUM_PDF_TO_SVG"] = self.previous
        os.environ.pop("FAKE_MODE", None)
        self.temp.cleanup()

    def test_positive_conversion_records_every_provenance_field(self) -> None:
        output = self.root / "out" / "panel.svg"
        provenance = self.root / "out" / "panel.json"
        receipt = convert(self.source, output, provenance, page=2)

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
