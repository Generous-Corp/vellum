from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
from vellum_html_source import (
    HTMLSourceError,
    MAX_DEPENDENCY_BYTES,
    discover_dependencies,
    fingerprint_html,
    stage_html_source,
)
sys.path.pop(0)


class HTMLSourceTests(unittest.TestCase):
    def write_fixture(self, root: Path, *, claude: bool = False) -> Path:
        (root / "assets").mkdir(parents=True)
        (root / "assets" / "hero.svg").write_text("<svg></svg>\n", encoding="utf-8")
        (root / "assets" / "texture.svg").write_text("<svg id=texture></svg>\n", encoding="utf-8")
        (root / "app.js").write_text("document.body.dataset.ready = 'yes';\n", encoding="utf-8")
        marker = '<meta name="generator" content="Claude Design export">' if claude else ""
        body_marker = ' data-claude-component="board"' if claude else ""
        html = f'''<!doctype html><html><head>{marker}<link rel="stylesheet" href="styles.css"></head>
<body{body_marker}><img src="assets/hero.svg"><script src="app.js"></script></body></html>'''
        (root / "styles.css").write_text("body { color: #123456; background: url('assets/texture.svg'); }\n", encoding="utf-8")
        path = root / ("claude-board.html" if claude else "board.html")
        path.write_text(html, encoding="utf-8")
        return path

    def test_generic_and_claude_fingerprints_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generic_root = root / "generic"
            claude_root = root / "claude"
            generic = fingerprint_html(self.write_fixture(generic_root))
            claude = fingerprint_html(self.write_fixture(claude_root, claude=True))
            self.assertEqual(generic["fingerprint"], "generic-html-v1")
            self.assertEqual(claude["fingerprint"], "claude-design-v1")
            self.assertIn("attribute:data-claude-component", claude["markers"])
            self.assertEqual(len(discover_dependencies(generic_root / "board.html", generic)), 4)

    def test_staging_is_contained_and_records_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.write_fixture(root / "source")
            receipt = stage_html_source(source, root / "staged")
            self.assertEqual(receipt["entry"], "board.html")
            self.assertEqual(len(receipt["dependencies"]), 4)
            self.assertTrue((root / "staged" / "assets" / "hero.svg").is_file())
            self.assertTrue((root / "staged" / "assets" / "texture.svg").is_file())
            self.assertEqual((root / "staged" / "board.html").read_bytes(), source.read_bytes())

    def test_remote_and_data_references_are_not_local_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "remote.html"
            source.write_text(
                '<script src="https://example.invalid/app.js"></script>'
                '<img src="data:image/png;base64,AAAA">', encoding="utf-8",
            )
            fingerprint = fingerprint_html(source)
            self.assertEqual(discover_dependencies(source, fingerprint), [])

    def test_traversal_and_symlink_dependencies_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unsafe.html"
            source.write_text('<script src="../outside.js"></script>', encoding="utf-8")
            with self.assertRaises(HTMLSourceError):
                discover_dependencies(source, fingerprint_html(source))
            (root / "outside.js").write_text("bad", encoding="utf-8")
            safe = root / "safe"
            safe.mkdir()
            safe_source = safe / "unsafe.html"
            safe_source.write_text('<script src="link.js"></script>', encoding="utf-8")
            try:
                (safe / "link.js").symlink_to(root / "outside.js")
            except OSError:
                self.skipTest("symbolic links unavailable")
            with self.assertRaises(HTMLSourceError):
                discover_dependencies(safe_source, fingerprint_html(safe_source))

    def test_css_relative_inline_and_srcset_dependencies_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "css").mkdir()
            (root / "assets").mkdir()
            (root / "assets" / "css.svg").write_text("<svg css></svg>", encoding="utf-8")
            (root / "assets" / "inline.svg").write_text("<svg inline></svg>", encoding="utf-8")
            (root / "css" / "site.css").write_text(
                "body { background: url('../assets/css.svg'); }", encoding="utf-8",
            )
            source = root / "index.html"
            source.write_text(
                '<link rel="stylesheet" href="css/site.css">'
                '<style>.hero { background: url("assets/inline.svg"); }</style>'
                '<img srcset="data:image/png;base64,AAAA 1x, assets/inline.svg 2x">',
                encoding="utf-8",
            )
            paths = {item.path for item in discover_dependencies(source, fingerprint_html(source))}
            self.assertEqual(paths, {"css/site.css", "assets/css.svg", "assets/inline.svg"})

    def test_oversized_dependency_is_rejected_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dependency = root / "huge.bin"
            with dependency.open("wb") as handle:
                handle.truncate(MAX_DEPENDENCY_BYTES + 1)
            source = root / "index.html"
            source.write_text('<img src="huge.bin">', encoding="utf-8")
            with self.assertRaises(HTMLSourceError):
                discover_dependencies(source, fingerprint_html(source))

    def test_staging_rejects_a_symlink_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real.html"
            real.write_text("<p>real</p>", encoding="utf-8")
            link = root / "link.html"
            try:
                link.symlink_to(real)
            except OSError:
                self.skipTest("symbolic links unavailable")
            with self.assertRaises(HTMLSourceError):
                stage_html_source(link, root / "staged")
