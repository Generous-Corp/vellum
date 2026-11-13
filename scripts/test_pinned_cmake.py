#!/usr/bin/env python3
"""Unit tests for pinned_cmake.py: the lock it accepts, the bytes it trusts and
the CMake it hands back. Nothing here reaches the network; the archive is a
stand-in whose cmake is a shell script."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pinned_cmake  # noqa: E402

VERSION = "9.8.7"
ASSET = f"cmake-{VERSION}-macos-universal.tar.gz"


def stand_in_archive(reports: str = VERSION, member: str = "CMake.app/Contents/bin/cmake") -> bytes:
    script = f"#!/bin/sh\necho 'cmake version {reports}'\n".encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(f"{ASSET.removesuffix('.tar.gz')}/{member}")
        info.size, info.mode = len(script), 0o755
        archive.addfile(info, io.BytesIO(script))
    return stream.getvalue()


class Opener:
    """Serves `responses` in order; an exception in the list is raised."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)


class PinnedCMakeTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        (self.repo / "gpu-runtime").mkdir(parents=True)
        self.cache = self.root / "cache"
        self.archive = stand_in_archive()
        self.write_lock()
        saved = os.environ.get(pinned_cmake.CACHE_ENV)
        os.environ[pinned_cmake.CACHE_ENV] = str(self.cache)
        self.addCleanup(lambda: os.environ.pop(pinned_cmake.CACHE_ENV) if saved is None
                        else os.environ.__setitem__(pinned_cmake.CACHE_ENV, saved))
        self.sleeps: list[float] = []
        real_sleep = pinned_cmake.time.sleep
        pinned_cmake.time.sleep = self.sleeps.append
        self.addCleanup(setattr, pinned_cmake.time, "sleep", real_sleep)

    def lock(self) -> dict:
        return {
            "schema": pinned_cmake.SCHEMA,
            "publisher": pinned_cmake.PUBLISHER,
            "version": VERSION,
            "platforms": {pinned_cmake.PLATFORM: {
                "asset": ASSET, "sha256": hashlib.sha256(self.archive).hexdigest()}},
        }

    def write_lock(self, edit=None) -> None:
        lock = self.lock()
        if edit is not None:
            edit(lock)
        (self.repo / pinned_cmake.LOCK).write_text(json.dumps(lock), encoding="utf-8")

    def assert_lock_refused(self, edit, fragment: str) -> None:
        self.write_lock(edit)
        with self.assertRaises(pinned_cmake.PinnedCMakeError) as caught:
            pinned_cmake.read_lock(self.repo)
        self.assertIn(fragment, str(caught.exception))

    def test_the_repository_lock_names_a_kitware_release(self) -> None:
        lock = pinned_cmake.read_lock(ROOT)
        self.assertEqual(lock["url"], "https://github.com/Kitware/CMake/releases/download/"
                                      f"v{lock['version']}/cmake-{lock['version']}-macos-universal.tar.gz")

    def test_the_url_is_built_from_the_version_and_never_read_from_the_lock(self) -> None:
        self.assertEqual(pinned_cmake.read_lock(self.repo)["url"],
                         f"https://github.com/Kitware/CMake/releases/download/v{VERSION}/{ASSET}")
        self.assert_lock_refused(lambda lock: lock.update(url="https://example.com/cmake.tar.gz"),
                                 "fields differ")

    def test_a_malformed_lock_is_refused(self) -> None:
        platform = pinned_cmake.PLATFORM
        cases = {
            "schema is unsupported": lambda lock: lock.update(schema="other"),
            "names another publisher": lambda lock: lock.update(publisher="https://github.com/evil/CMake"),
            "version is not": lambda lock: lock.update(version="4.3"),
            "version is not ": lambda lock: lock.update(version="4.3.3/../../x"),
            "asset is not Kitware's": lambda lock: lock["platforms"][platform].update(
                asset="cmake-9.8.7-linux-x86_64.tar.gz"),
            "does not hold a sha256": lambda lock: lock["platforms"][platform].update(sha256="abc"),
            "must pin exactly": lambda lock: lock["platforms"].update(linux={}),
            "must pin exactly ": lambda lock: lock["platforms"][platform].update(url="https://x"),
        }
        for fragment, edit in cases.items():
            with self.subTest(fragment=fragment):
                self.assert_lock_refused(edit, fragment.strip())

    def test_a_source_without_a_lock_is_refused(self) -> None:
        (self.repo / pinned_cmake.LOCK).unlink()
        with self.assertRaisesRegex(pinned_cmake.PinnedCMakeError, "pins no CMake"):
            pinned_cmake.read_lock(self.repo)

    def test_the_pinned_cmake_is_fetched_checked_and_unpacked(self) -> None:
        opener = Opener(self.archive)
        cmake, version = pinned_cmake.provide(self.repo, self.root / "unpacked", opener)
        self.assertEqual(version, VERSION)
        self.assertEqual(opener.urls, [pinned_cmake.read_lock(self.repo)["url"]])
        self.assertEqual(pinned_cmake.reported_version(cmake), VERSION)
        self.assertTrue(cmake.is_relative_to(self.root / "unpacked"))

    def test_an_archive_that_misses_the_locked_digest_is_refused_and_not_cached(self) -> None:
        with self.assertRaisesRegex(pinned_cmake.PinnedCMakeError, "does not match the locked digest"):
            pinned_cmake.provide(self.repo, self.root / "unpacked", Opener(self.archive + b"x"))
        self.assertEqual([path for path in self.cache.rglob("*") if path.is_file()], [])

    def test_a_cached_archive_is_reused_only_while_its_bytes_match(self) -> None:
        lock = pinned_cmake.read_lock(self.repo)
        cached = pinned_cmake.fetch(lock, self.cache, Opener(self.archive))
        self.assertEqual(pinned_cmake.fetch(lock, self.cache, Opener()), cached)
        cached.write_bytes(b"planted")
        opener = Opener(self.archive)
        self.assertEqual(pinned_cmake.fetch(lock, self.cache, opener), cached)
        self.assertEqual(len(opener.urls), 1)
        self.assertEqual(cached.read_bytes(), self.archive)

    def test_a_cmake_reporting_another_version_is_refused(self) -> None:
        self.archive = stand_in_archive(reports="9.8.6")
        self.write_lock()
        with self.assertRaisesRegex(pinned_cmake.PinnedCMakeError,
                                    f"reports version 9.8.6, not the locked {VERSION}"):
            pinned_cmake.provide(self.repo, self.root / "unpacked", Opener(self.archive))

    def test_an_archive_without_a_cmake_is_refused(self) -> None:
        self.archive = stand_in_archive(member="bin/cmake")
        self.write_lock()
        with self.assertRaisesRegex(pinned_cmake.PinnedCMakeError, "holds no CMake.app"):
            pinned_cmake.provide(self.repo, self.root / "unpacked", Opener(self.archive))

    def test_a_server_error_is_retried_and_a_client_error_is_final(self) -> None:
        def http(code: int) -> urllib.error.HTTPError:
            return urllib.error.HTTPError("https://x", code, "status", None, None)

        lock = pinned_cmake.read_lock(self.repo)
        opener = Opener(http(503), ConnectionResetError("reset"), self.archive)
        pinned_cmake.fetch(lock, self.cache, opener)
        self.assertEqual(len(opener.urls), 3)
        self.assertEqual(len(self.sleeps), 2)
        opener = Opener(http(404), self.archive)
        with self.assertRaisesRegex(pinned_cmake.PinnedCMakeError, "HTTP 404"):
            pinned_cmake.fetch(lock, self.root / "other-cache", opener)
        self.assertEqual(len(opener.urls), 1)

    def test_only_a_cmake_command_is_rerouted(self) -> None:
        self.assertEqual(pinned_cmake.use(["cmake", "-S", "."], Path("/pinned/cmake")),
                         ["/pinned/cmake", "-S", "."])
        for command in (["/usr/bin/cmake", "-S", "."], ["ninja"], []):
            with self.subTest(command=command):
                with self.assertRaisesRegex(pinned_cmake.PinnedCMakeError, "does not invoke cmake"):
                    pinned_cmake.use(command, Path("/pinned/cmake"))


if __name__ == "__main__":
    unittest.main()
