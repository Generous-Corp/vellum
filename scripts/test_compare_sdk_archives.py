#!/usr/bin/env python3
"""compare_sdk_archives.py names each kind of archive difference."""

from __future__ import annotations

import gzip
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_sdk_archives", HERE / "compare_sdk_archives.py")
assert spec and spec.loader
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def write_archive(path: Path, members: list[dict], gzip_mtime: int = 0) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as handle:
        for spec_ in members:
            info = tarfile.TarInfo(spec_["name"])
            data = spec_.get("data", b"")
            info.size = len(data)
            info.mode = spec_.get("mode", 0o644)
            info.mtime = spec_.get("mtime", 0)
            info.uid = spec_.get("uid", 0)
            info.gid = spec_.get("gid", 0)
            handle.addfile(info, io.BytesIO(data))
    with open(path, "wb") as out, gzip.GzipFile(fileobj=out, mode="wb", mtime=gzip_mtime, filename="") as zipped:
        zipped.write(raw.getvalue())


BASE = [{"name": "a.txt", "data": b"one"}, {"name": "b.txt", "data": b"two"}]


class CompareSdkArchivesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.first = self.root / "first.tar.gz"
        self.second = self.root / "second.tar.gz"
        write_archive(self.first, BASE)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def diff(self, members: list[dict], gzip_mtime: int = 0) -> list[str]:
        write_archive(self.second, members, gzip_mtime)
        return compare.differences(self.first, self.second)

    def test_identical_archives_pass(self) -> None:
        write_archive(self.second, BASE)
        self.assertEqual(compare.main(["x", str(self.first), str(self.second)]), 0)

    def test_member_mtime_is_named(self) -> None:
        found = self.diff([{**BASE[0], "mtime": 5}, BASE[1]])
        self.assertEqual(found, ["a.txt: mtime 0 != 5"])

    def test_owner_ids_are_named(self) -> None:
        found = self.diff([BASE[0], {**BASE[1], "uid": 501, "gid": 20}])
        self.assertEqual(found, ["b.txt: uid 0 != 501, gid 0 != 20"])

    def test_content_is_named(self) -> None:
        found = self.diff([{**BASE[0], "data": b"uno"}, BASE[1]])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("a.txt: sha256 "))

    def test_member_order_is_named(self) -> None:
        self.assertEqual(self.diff([BASE[1], BASE[0]]), ["member order differs"])

    def test_gzip_header_is_named(self) -> None:
        found = self.diff(BASE, gzip_mtime=1234)
        self.assertEqual(len(found), 1)
        self.assertIn("gzip header", found[0])
        self.assertIn("'mtime': 1234", found[0])

    def test_added_member_is_named(self) -> None:
        self.assertEqual(self.diff([*BASE, {"name": "c.txt"}]), ["c.txt: only in second"])

    def test_a_difference_it_cannot_attribute_still_fails(self) -> None:
        write_archive(self.second, BASE)
        raw = gzip.decompress(self.second.read_bytes())
        # Same tar stream, recompressed at another level: only compressed bytes differ.
        with open(self.second, "wb") as out, gzip.GzipFile(
            fileobj=out, mode="wb", mtime=0, filename="", compresslevel=1
        ) as zipped:
            zipped.write(raw)
        self.assertNotEqual(self.first.read_bytes(), self.second.read_bytes())
        found = compare.differences(self.first, self.second)
        self.assertTrue(any("compressed bytes" in line or "gzip header" in line for line in found), found)
        self.assertEqual(compare.main(["x", str(self.first), str(self.second)]), 1)


if __name__ == "__main__":
    unittest.main()
