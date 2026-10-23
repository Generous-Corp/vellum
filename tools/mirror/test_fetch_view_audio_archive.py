#!/usr/bin/env python3
"""Negative controls for the pinned Pulp view-audio archive fetch."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fetch_view_audio_archive", HERE / "fetch_view_audio_archive.py")
assert spec and spec.loader
fva = importlib.util.module_from_spec(spec)
sys.modules["fetch_view_audio_archive"] = fva
spec.loader.exec_module(fva)

REPO = "Generous-Corp/pulp"
PULP_SHA = "a" * 40
WORKFLOW_SHA = PULP_SHA
RECEIPT = "provenance/mirror/receipts/20990101-aaaaaaaaaaaa.json"
TAG = f"artifact/pulp-view-audio/{PULP_SHA}"
ARCHIVE = b"!<arch>\npublished pulp-view-audio\n"
BUNDLE = b'{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}\n'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "vellum"
        receipts = self.root / "provenance/mirror/receipts"
        receipts.mkdir(parents=True)
        (receipts / "index.json").write_text(json.dumps(
            {"schema": fva.INDEX_SCHEMA, "receipts": [RECEIPT]}), encoding="utf-8")
        (self.root / RECEIPT).write_text(json.dumps(
            {"schema": fva.RECEIPT_SCHEMA, "pulp_repository": REPO, "pulp_sha": PULP_SHA}),
            encoding="utf-8")
        self.manifest = {
            "schema": fva.MANIFEST_SCHEMA,
            "source": {"repository": REPO, "sha": PULP_SHA},
            "target": "pulp-view-audio",
            "archive": {"name": "libpulp-view-audio.a", "sha256": sha(ARCHIVE), "size": len(ARCHIVE)},
            "workflow": {"run_id": "4242", "workflow_sha": WORKFLOW_SHA},
            "symbol_floor_gate": {"exit_code": 0},
        }
        self.assets: dict[str, bytes] = {}
        self.publish()
        self.out = Path(self.tmp.name) / "out"
        self.gh_log = Path(self.tmp.name) / "gh.log"
        self.gh_status = Path(self.tmp.name) / "gh.status"
        self.gh_status.write_text("0", encoding="utf-8")
        self.gh = Path(self.tmp.name) / "gh"
        self.gh.write_text(
            f"#!{sys.executable}\nimport pathlib, sys\n"
            f"pathlib.Path({str(self.gh_log)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"raise SystemExit(int(pathlib.Path({str(self.gh_status)!r}).read_text()))\n",
            encoding="utf-8")
        self.gh.chmod(self.gh.stat().st_mode | stat.S_IXUSR)
        self.urls: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def publish(self, **overrides: object) -> None:
        """Serve the release assets and pin them in the descriptor."""
        manifest_bytes = (json.dumps(self.manifest, sort_keys=True) + "\n").encode()
        self.assets = {"libpulp-view-audio.a": ARCHIVE,
                       "pulp-view-audio-archive.manifest.json": manifest_bytes,
                       "pulp-view-audio-archive.sigstore.json": BUNDLE}
        descriptor = {
            "schema": fva.DESCRIPTOR_SCHEMA,
            "pulp_repository": REPO,
            "source_sha": PULP_SHA,
            "export_receipt": RECEIPT,
            "release_tag": TAG,
            "workflow_run_id": 4242,
            "workflow_sha": WORKFLOW_SHA,
            "archive": {"name": "libpulp-view-audio.a", "sha256": sha(ARCHIVE), "size": len(ARCHIVE)},
            "manifest": {"name": "pulp-view-audio-archive.manifest.json", "sha256": sha(manifest_bytes)},
            "attestation_bundle": {"name": "pulp-view-audio-archive.sigstore.json", "sha256": sha(BUNDLE)},
        }
        descriptor.update(overrides)
        self.write_descriptor(descriptor)

    def write_descriptor(self, descriptor: dict) -> None:
        (self.root / fva.DESCRIPTOR_PATH).write_text(json.dumps(descriptor), encoding="utf-8")

    def descriptor(self) -> dict:
        return json.loads((self.root / fva.DESCRIPTOR_PATH).read_text(encoding="utf-8"))

    def fetcher(self, url: str, destination: Path) -> None:
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name not in self.assets:
            raise OSError(f"404 {url}")
        destination.write_bytes(self.assets[name])

    def fetch(self) -> dict:
        return fva.fetch(self.root, REPO, self.out, str(self.gh), self.fetcher)

    def test_a_pinned_published_archive_is_verified(self) -> None:
        result = self.fetch()
        self.assertEqual(Path(result["archive"]).read_bytes(), ARCHIVE)
        self.assertTrue(all(url.startswith(f"https://github.com/{REPO}/releases/download/{TAG}/")
                            for url in self.urls))
        calls = self.gh_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn(f"--signer-workflow {REPO}/.github/workflows/view-audio-archive.yml", call)
            self.assertIn("--source-ref refs/heads/main", call)
            self.assertIn(f"--source-digest {WORKFLOW_SHA}", call)
            self.assertIn("--deny-self-hosted-runners", call)

    def test_the_committed_descriptor_matches_the_landed_receipt(self) -> None:
        # The real descriptor must name the newest receipt's commit even before
        # it is pinned; until then it is refused as unpinned, never accepted.
        real = HERE.parents[1]
        try:
            fva.check_descriptor(real, REPO)
        except fva.Unusable as error:
            self.assertIn("not pinned yet", str(error))

    def test_an_unpinned_descriptor_fails_closed(self) -> None:
        descriptor = self.descriptor()
        descriptor["archive"]["sha256"] = None
        self.write_descriptor(descriptor)
        with self.assertRaisesRegex(fva.Unusable, "not pinned yet"):
            self.fetch()
        self.assertEqual(self.urls, [])

    def test_a_missing_descriptor_fails_closed(self) -> None:
        (self.root / fva.DESCRIPTOR_PATH).unlink()
        with self.assertRaises(fva.Unusable):
            self.fetch()

    def test_another_pulp_commit_is_refused(self) -> None:
        self.publish(source_sha="c" * 40, release_tag=f"artifact/pulp-view-audio/{'c' * 40}")
        with self.assertRaisesRegex(fva.Mismatch, "receipt pulp_sha"):
            self.fetch()
        self.assertEqual(self.urls, [])

    def test_a_stale_receipt_reference_is_refused(self) -> None:
        self.publish(export_receipt="provenance/mirror/receipts/old.json")
        with self.assertRaisesRegex(fva.Mismatch, "newest is"):
            self.fetch()

    def test_another_release_tag_is_refused(self) -> None:
        self.publish(release_tag="v0.1.0")
        with self.assertRaisesRegex(fva.Mismatch, "release_tag"):
            self.fetch()

    def test_another_repository_is_refused(self) -> None:
        with self.assertRaisesRegex(fva.Mismatch, "repository"):
            fva.fetch(self.root, "someone/pulp", self.out, str(self.gh), self.fetcher)

    def test_a_substituted_archive_is_refused(self) -> None:
        self.assets["libpulp-view-audio.a"] = b"!<arch>\nlocally built\n"
        with self.assertRaisesRegex(fva.Mismatch, "libpulp-view-audio.a sha256"):
            self.fetch()
        self.assertFalse((self.out / "libpulp-view-audio.a").exists())

    def test_a_missing_asset_is_refused(self) -> None:
        del self.assets["pulp-view-audio-archive.sigstore.json"]
        with self.assertRaisesRegex(fva.Mismatch, "cannot download"):
            self.fetch()

    def test_a_manifest_for_another_commit_is_refused(self) -> None:
        self.manifest["source"]["sha"] = "d" * 40
        self.publish()
        with self.assertRaisesRegex(fva.Mismatch, "manifest source.sha"):
            self.fetch()

    def test_a_manifest_from_another_run_is_refused(self) -> None:
        self.manifest["workflow"]["run_id"] = "1"
        self.publish()
        with self.assertRaisesRegex(fva.Mismatch, "workflow.run_id"):
            self.fetch()

    def test_a_failing_floor_gate_is_refused(self) -> None:
        self.manifest["symbol_floor_gate"]["exit_code"] = 1
        self.publish()
        with self.assertRaisesRegex(fva.Mismatch, "symbol_floor_gate.exit_code"):
            self.fetch()

    def test_a_run_at_another_commit_is_refused(self) -> None:
        self.manifest["workflow"]["workflow_sha"] = "b" * 40
        self.publish(workflow_sha="b" * 40)
        with self.assertRaisesRegex(fva.Mismatch, "workflow_sha"):
            self.fetch()
        self.assertEqual(self.urls, [])

    def test_a_rejected_attestation_is_refused(self) -> None:
        self.gh_status.write_text("1", encoding="utf-8")
        with self.assertRaisesRegex(fva.Mismatch, "attestation rejected"):
            self.fetch()

    def test_cli_exit_codes(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fva.main(["--root", str(self.root), "--repository", REPO, "--check"]), 0)
            self.publish(source_sha="c" * 40, release_tag=f"artifact/pulp-view-audio/{'c' * 40}")
            self.assertEqual(fva.main(["--root", str(self.root), "--repository", REPO, "--check"]), 1)
            self.write_descriptor({"schema": "something-else"})
            self.assertEqual(fva.main(["--root", str(self.root), "--repository", REPO, "--check"]), 2)


if __name__ == "__main__":
    unittest.main()
