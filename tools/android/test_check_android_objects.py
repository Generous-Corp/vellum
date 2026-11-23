#!/usr/bin/env python3
"""Tests for check_android_objects.py, including the controls that must fail."""

import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_android_objects as check  # noqa: E402


def elf_header(machine: int, klass: int = 2, data: int = 1) -> bytes:
    ident = b"\x7fELF" + bytes([klass, data, 1, 0]) + bytes(8)
    return ident + struct.pack("<HH", 1, machine) + bytes(8)


class ClassifyTests(unittest.TestCase):
    def test_aarch64_elf64_passes(self) -> None:
        self.assertEqual(check.classify(elf_header(check.EM_AARCH64)), "ok")

    def test_host_architecture_object_is_refused(self) -> None:
        self.assertIn("x86_64", check.classify(elf_header(check.EM_X86_64)))

    def test_mach_o_object_is_refused(self) -> None:
        macho = bytes.fromhex("cffaedfe0c000001") + bytes(12)
        self.assertIn("not ELF", check.classify(macho))

    def test_32_bit_elf_is_refused(self) -> None:
        self.assertEqual(check.classify(elf_header(check.EM_AARCH64, klass=1)), "not ELF64")

    def test_truncated_header_is_refused(self) -> None:
        self.assertIn("shorter", check.classify(b"\x7fELF"))


class BuildDirTests(unittest.TestCase):
    def build(self, headers: list[bytes]) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for index, header in enumerate(headers):
            target = root / "CMakeFiles" / "unit.dir"
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{index}.o").write_bytes(header)
        return root

    def test_all_aarch64_with_expected_count_passes(self) -> None:
        root = self.build([elf_header(check.EM_AARCH64)] * 3)
        self.assertEqual(check.check_objects(root, 3), [])

    def test_one_host_object_fails_the_lane(self) -> None:
        root = self.build([elf_header(check.EM_AARCH64), elf_header(check.EM_X86_64)])
        problems = check.check_objects(root, 2)
        self.assertEqual(len(problems), 1)
        self.assertIn("wrong architecture", problems[0])

    def test_missing_object_fails_the_lane(self) -> None:
        root = self.build([elf_header(check.EM_AARCH64)] * 2)
        self.assertIn("expected 3 objects", check.check_objects(root, 3)[0])

    def test_compiler_probe_objects_are_not_counted(self) -> None:
        root = self.build([elf_header(check.EM_AARCH64)])
        probe = root / "CMakeFiles" / "4.0" / "CompilerIdCXX"
        probe.mkdir(parents=True)
        (probe / "CMakeCXXCompilerId.o").write_bytes(elf_header(check.EM_X86_64))
        self.assertEqual(check.check_objects(root, 1), [])

    def test_empty_build_directory_fails_the_lane(self) -> None:
        self.assertTrue(check.check_objects(self.build([]), 1))


if __name__ == "__main__":
    unittest.main()
