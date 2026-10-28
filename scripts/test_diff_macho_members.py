#!/usr/bin/env python3
"""diff_macho_members names the section where two builds of a library differ."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff_macho_members as diff  # noqa: E402


def archive(path: Path, data: bytes) -> None:
    with tarfile.open(path, "w:gz") as handle:
        info = tarfile.TarInfo("sdk/lib/libx.dylib")
        info.size = len(data)
        handle.addfile(info, io.BytesIO(data))


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("otool") and shutil.which("nm"), "needs otool and nm")
class DiffMachoMembers(unittest.TestCase):
    def test_a_flipped_text_byte_is_named_and_identical_builds_report_nothing(self) -> None:
        image = Path("/usr/lib/dyld")  # any Mach-O image the host ships
        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            data = image.read_bytes()
            text = diff.sections(subprocess.run(["otool", "-l", str(image)], capture_output=True,
                                                text=True).stdout).get(("__TEXT", "__text"))
            if text is None:
                self.skipTest("no thin __TEXT,__text section in the host image")
            flipped = bytearray(data)
            flipped[text[0] + 64] ^= 0xFF
            archive(tmp / "a.tgz", data)
            archive(tmp / "b.tgz", bytes(flipped))
            out = subprocess.run([sys.executable, diff.__file__, str(tmp / "a.tgz"), str(tmp / "b.tgz"),
                                  str(tmp / "out")], capture_output=True, text=True, check=True).stdout
            self.assertIn("__TEXT,__text", out)
            self.assertTrue((tmp / "out" / "libx.dylib" / "second").is_file())
            same = subprocess.run([sys.executable, diff.__file__, str(tmp / "a.tgz"), str(tmp / "a.tgz"),
                                   str(tmp / "same")], capture_output=True, text=True, check=True).stdout
            self.assertEqual(same, "")

    def test_load_command_blocks_group_by_command(self) -> None:
        text = "x\nLoad command 0\n      cmd LC_UUID\n    uuid A\nLoad command 1\n      cmd LC_UUID\n    uuid B\n"
        self.assertEqual(list(diff.load_command_blocks(text)), ["LC_UUID"])
        self.assertEqual(len(diff.load_command_blocks(text)["LC_UUID"]), 2)


if __name__ == "__main__":
    unittest.main()
