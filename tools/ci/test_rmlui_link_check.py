#!/usr/bin/env python3
"""rmlui_link_check.py on recorded otool output, and the pin on the files
copied from Pulp."""

from __future__ import annotations

import contextlib
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rmlui_link_check as rlc  # noqa: E402

CLEAN = """{binary}:
\t/System/Library/Frameworks/Metal.framework/Versions/A/Metal (compatibility version 1.0.0, current version 370.64.2)
\t/System/Library/Frameworks/QuartzCore.framework/Versions/A/QuartzCore (compatibility version 1.2.0, current version 1.11.0)
\t/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation (compatibility version 300.0.0, current version 3423.0.0)
\t/usr/lib/libobjc.A.dylib (compatibility version 1.0.0, current version 228.0.0)
\t/usr/lib/libc++.1.dylib (compatibility version 1.0.0, current version 1900.180.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)
"""


def run(output: str, required: list[str]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as directory:
        binary = Path(directory) / "test-binary"
        binary.write_bytes(b"")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            code = rlc.check([binary], required, otool=lambda path: output.format(binary=path))
        return code, captured.getvalue()


class LinkCheckTests(unittest.TestCase):
    def test_system_libraries_only_pass(self):
        code, _ = run(CLEAN, ["Metal.framework"])
        self.assertEqual(code, 0)

    def test_a_vellum_library_fails(self):
        code, out = run(CLEAN + "\t@rpath/libVellumGpu.dylib (compatibility version 0.0.0)\n", [])
        self.assertEqual(code, 1)
        self.assertIn("libVellumGpu.dylib", out)

    def test_a_homebrew_library_fails(self):
        code, out = run(CLEAN + "\t/opt/homebrew/opt/freetype/lib/libfreetype.6.dylib "
                                "(compatibility version 27.0.0)\n", [])
        self.assertEqual(code, 1)
        self.assertIn("outside", out)

    def test_a_system_path_named_like_skia_still_fails(self):
        code, _ = run(CLEAN + "\t/usr/lib/libskia.dylib (compatibility version 1.0.0)\n", [])
        self.assertEqual(code, 1)

    def test_a_missing_required_library_fails(self):
        code, out = run(CLEAN, ["CoreML.framework"])
        self.assertEqual(code, 1)
        self.assertIn("does not load CoreML.framework", out)

    def test_otool_listing_nothing_is_an_instrument_failure(self):
        code, out = run("{binary}:\n", ["Metal.framework"])
        self.assertEqual(code, 2)
        self.assertIn("read nothing", out)

    def test_fat_binary_headers_are_not_libraries(self):
        fat = CLEAN.replace("{binary}:\n", "{binary} (architecture arm64):\n")
        code, _ = run(fat, ["Metal.framework"])
        self.assertEqual(code, 0)

    def test_a_missing_binary_is_an_instrument_failure(self):
        code = rlc.check([Path("/nonexistent/binary")], [], otool=lambda path: CLEAN)
        self.assertEqual(code, 2)


# Pulp and Vellum carry byte-identical copies of the dependency request
# recorder and its checker. These are the blobs at Pulp's merge of the
# recorder (Generous-Corp/pulp 927c34a3dda7); change them only together with
# Pulp's.
PULP_COPIES = {
    "dependency_request_recorder.cmake": "1509e0820bc328500dddc230d1c1ae517876ed2e",
    "dependency_request_check.py": "9da5c0cb32dabb3a2c186df95cda9ffc82b59e3e",
    "test_dependency_request_check.py": "ed7187a42671d549da083d15f68d65d58a7b2f9c",
}


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class PulpCopyTests(unittest.TestCase):
    def test_copies_match_pulp(self):
        for name, expected in PULP_COPIES.items():
            with self.subTest(file=name):
                self.assertEqual(git_blob_sha(HERE / name), expected,
                                 f"tools/ci/{name} differs from Pulp's copy")


if __name__ == "__main__":
    unittest.main()
