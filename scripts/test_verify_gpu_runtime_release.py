#!/usr/bin/env python3
"""Tests for verify_gpu_runtime_release.py: the finalizer's gate on the gpu-runtime archive."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_gpu_runtime_release as release  # noqa: E402

VERSION = "0.1.11"
COMMIT = "a" * 40
NAME = f"vellum-gpu-runtime-{VERSION}-darwin-arm64.tar.gz"
BYTES = b"gpu-runtime archive bytes"
DIGEST = hashlib.sha256(BYTES).hexdigest()


def source_record(**overrides):
    record = {"archive": NAME, "sha256": DIGEST, "version": VERSION, "target": "darwin-arm64",
              "source_commit": COMMIT, "checked_against_source": True, "consumer": "adapter ok"}
    record.update(overrides)
    return record


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gpu-runtime-release-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.gpu = self.root / "gpu"
        self.gpu.mkdir()
        (self.gpu / NAME).write_bytes(BYTES)
        (self.gpu / release.CHECKSUM_NAME).write_text(f"{DIGEST}  {NAME}\n")
        (self.gpu / release.SOURCE_RECORD).write_text(json.dumps(source_record()))
        self.source = self.root / "source"
        (self.source / "gpu-runtime/include").mkdir(parents=True)
        self.calls = []

    def verify(self, **overrides):
        def fn(archive, source_root, renderer):
            self.calls.append(("verify", archive.name, source_root, renderer.name))
            return {**source_record(), "renderer_lock_sha256": "f" * 64, "files_verified": 40, **overrides}
        return fn

    def run_validate(self, verify=None, closure=None, head=COMMIT, version=VERSION, commit=COMMIT):
        def fetch(lock, directory):
            self.calls.append(("fetch", lock))
            path = directory / "renderer.zip"
            path.write_bytes(b"renderer")
            return path
        return release.validate(self.gpu, self.source, version, commit, verify_fn=verify or self.verify(),
                                closure_fn=closure or (lambda include: []), fetch_fn=fetch,
                                head_fn=lambda root: head)


class Validate(Fixture):
    def test_a_release_archive_held_to_its_source_validates(self):
        record = self.run_validate()
        self.assertEqual((record["schema"], record["ok"], record["sha256"], record["archive"]),
                         (release.SCHEMA, True, DIGEST, NAME))
        self.assertTrue(record["gpu_adapter_required"])
        self.assertEqual(record["device_api_closure"], "ok")
        self.assertIn(("fetch", self.source / release.LOCK), self.calls)
        self.assertEqual([c[0] for c in self.calls], ["fetch", "verify"])

    def test_the_source_checkout_must_be_the_release_commit(self):
        with self.assertRaisesRegex(release.ReleaseError, "release source commit"):
            self.run_validate(head="b" * 40)

    def test_a_missing_or_linked_archive_is_refused(self):
        (self.gpu / NAME).unlink()
        with self.assertRaisesRegex(release.ReleaseError, "did not upload"):
            self.run_validate()
        target = self.root / "elsewhere.tar.gz"
        target.write_bytes(BYTES)
        (self.gpu / NAME).symlink_to(target)
        with self.assertRaisesRegex(release.ReleaseError, "did not upload"):
            self.run_validate()

    def test_a_checksum_file_that_disagrees_or_holds_more_is_refused(self):
        for text in (f"{'0' * 64}  {NAME}\n", f"{DIGEST}  other.tar.gz\n", f"{DIGEST}  {NAME}\n{DIGEST}  x\n", "junk\n"):
            with self.subTest(text=text):
                (self.gpu / release.CHECKSUM_NAME).write_text(text)
                with self.assertRaises(release.ReleaseError):
                    self.run_validate()

    def test_the_source_runs_own_record_must_agree(self):
        for key, value in (("sha256", "0" * 64), ("version", "0.1.10"), ("source_commit", "b" * 40),
                           ("checked_against_source", False), ("consumer", "")):
            with self.subTest(key=key):
                (self.gpu / release.SOURCE_RECORD).write_text(json.dumps(source_record(**{key: value})))
                with self.assertRaises(release.ReleaseError):
                    self.run_validate()

    def test_the_trusted_verification_must_agree_and_run_the_consumer(self):
        for key, value in (("sha256", "0" * 64), ("source_commit", "b" * 40), ("checked_against_source", False),
                           ("consumer", ""), ("target", "linux-x64")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(release.ReleaseError, "trusted gpu-runtime verification"):
                    self.run_validate(verify=self.verify(**{key: value}))

    def test_a_verifier_failure_is_never_a_pass(self):
        def broken(archive, source_root, renderer):
            raise RuntimeError("consumer did not find an adapter")
        with self.assertRaises(RuntimeError):
            self.run_validate(verify=broken)

    def test_a_device_api_closure_problem_refuses(self):
        with self.assertRaisesRegex(release.ReleaseError, "closure"):
            self.run_validate(closure=lambda include: ["gpu_runtime/x.hpp:3: names 'pulp'"])

    def test_bad_versions_and_commits_are_refused(self):
        for version, commit in (("v0.1.11", COMMIT), ("0.1", COMMIT), (VERSION, "abc")):
            with self.subTest(version=version, commit=commit), self.assertRaises(release.ReleaseError):
                self.run_validate(version=version, commit=commit)


class Renderer(unittest.TestCase):
    def lock(self, **overrides):
        lock = {"builder": {"repository": release.BUILDER}, "milestone": "chrome/m147",
                "platforms": {"macos-arm64": {"asset": "skia.zip", "sha256": hashlib.sha256(b"zip").hexdigest()}}}
        for key, value in overrides.items():
            if key in ("asset", "sha256"):
                lock["platforms"]["macos-arm64"][key] = value
            elif key == "repository":
                lock["builder"]["repository"] = value
            else:
                lock[key] = value
        path = Path(tempfile.mkdtemp(prefix="lock-")) / "renderer-lock.json"
        path.write_text(json.dumps(lock))
        return path

    def test_only_the_pinned_builder_asset_is_fetched(self):
        url, asset, _ = release.renderer_asset(self.lock())
        self.assertEqual(url, f"{release.BUILDER}/releases/download/chrome/m147/skia.zip")
        for bad in ({"repository": "https://evil.example/skia-builder"}, {"milestone": "chrome/m147/../x"},
                    {"asset": "--output=/etc/x"}, {"sha256": "nope"}):
            with self.subTest(bad=bad), self.assertRaises(release.ReleaseError):
                release.renderer_asset(self.lock(**bad))

    def test_a_download_that_does_not_match_the_lock_is_refused(self):
        directory = Path(tempfile.mkdtemp(prefix="fetch-"))
        opener = lambda url: io.BytesIO(b"zip")  # noqa: E731
        self.assertEqual(release.fetch_renderer(self.lock(), directory, opener).read_bytes(), b"zip")
        with self.assertRaisesRegex(release.ReleaseError, "locked digest"):
            release.fetch_renderer(self.lock(), directory, lambda url: io.BytesIO(b"other"))

    def test_the_committed_lock_reads(self):
        url, asset, sha256 = release.renderer_asset(ROOT / release.LOCK)
        self.assertTrue(url.startswith(release.BUILDER + "/releases/download/chrome/m"))
        self.assertRegex(sha256, r"^[0-9a-f]{64}$")


class TrustedControls(unittest.TestCase):
    """The default verifiers are main's own, with the signatures the gate calls."""

    def test_the_closure_check_is_mains_and_passes_on_mains_headers(self):
        self.assertEqual(release.trusted_closure(ROOT / release.HEADERS), [])

    def test_the_artifact_verifier_exposes_what_the_gate_calls(self):
        verifier = release.load_sibling("verify_gpu_runtime_artifact",
                                        release.HERE / "verify_gpu_runtime_artifact.py")
        for name in ("regenerate_package_files", "verify", "run_consumer"):
            self.assertTrue(callable(getattr(verifier, name)), name)


class Finalize(Fixture):
    def record(self, **overrides):
        record = self.run_validate()
        record.update(overrides)
        path = self.root / "record.json"
        path.write_text(json.dumps(record))
        return path

    def test_the_published_checksum_comes_from_the_published_bytes(self):
        out = self.root / release.CHECKSUM_NAME
        self.assertEqual(release.finalize(self.gpu, self.record(), VERSION, COMMIT, out), DIGEST)
        self.assertEqual(out.read_text(), f"{DIGEST}  {NAME}\n")

    def test_an_archive_other_than_the_validated_one_is_refused(self):
        record = self.record()
        (self.gpu / NAME).write_bytes(b"swapped after validation")
        with self.assertRaisesRegex(release.ReleaseError, "not the archive the validation job verified"):
            release.finalize(self.gpu, record, VERSION, COMMIT, self.root / "out")

    def test_a_record_for_another_release_or_without_its_checks_is_refused(self):
        for key, value in (("version", "0.1.10"), ("source_commit", "b" * 40), ("ok", False),
                           ("gpu_adapter_required", False), ("device_api_closure", "skipped"), ("schema", "x")):
            with self.subTest(key=key), self.assertRaises(release.ReleaseError):
                release.finalize(self.gpu, self.record(**{key: value}), VERSION, COMMIT, self.root / "out")


class Workflow(unittest.TestCase):
    """The finalizer publishes the gpu-runtime archive, its checksum and its record, and gates both jobs."""

    text = (ROOT / ".github/workflows/sdk-release.yml").read_text(encoding="utf-8")

    def test_the_validation_job_holds_the_archive_to_the_release_source(self):
        self.assertIn('git worktree add --detach "$source_root" "$SOURCE_COMMIT"', self.text)
        self.assertIn("python3 scripts/verify_gpu_runtime_release.py validate", self.text)
        self.assertIn("${{ runner.temp }}/trusted-gpu-runtime-verification.json", self.text)

    def test_the_finalizer_binds_and_publishes_all_three(self):
        self.assertIn("python3 -I scripts/verify_gpu_runtime_release.py finalize", self.text)
        for asset in ('"$GPU_RUNTIME_ARCHIVE=$RUNNER_TEMP/gpu/$GPU_RUNTIME_ARCHIVE"',
                      '"vellum-gpu-runtime.sha256=$RUNNER_TEMP/trusted/vellum-gpu-runtime.sha256"',
                      '"gpu-runtime-verification.json=$RUNNER_TEMP/trusted/trusted-gpu-runtime-verification.json"'):
            self.assertIn(asset, self.text)


if __name__ == "__main__":
    unittest.main()
